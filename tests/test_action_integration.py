"""
End-to-end tests for the action.

The action is run as a real subprocess against a stub serving both the StackGuardian API and
GitHub on one port, with a real `tirith` installed. That is deliberate: the highest-value
assertion here is that a secret in the plan never appears in any recorded request body, and only
an end-to-end run can prove that. Testing the masking function in isolation is what let a leak
through once already -- the secret lived in a part of the plan the function never looked at.

Requires `tirith` on PATH (`pip install -e ../tirith`).
"""

import gzip
import io
import json
import os
import shutil
import subprocess
import sys
import tarfile
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

ACTION = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts", "main.py")
SECRET = "hunter2-must-not-reach-the-platform"

pytestmark = pytest.mark.skipif(shutil.which("tirith") is None, reason="tirith is not installed")


class Stub(BaseHTTPRequestHandler):
    """Serves the SG API, the presigned upload target and the GitHub API on one port."""

    requests = []
    run_status = "COMPLETED"
    policy_results = {}

    def log_message(self, *args):
        pass

    def _read(self):
        length = int(self.headers.get("Content-Length") or 0)
        return self.rfile.read(length) if length else b""

    def _respond(self, status, payload):
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _record(self, method):
        body = self._read()
        Stub.requests.append({"method": method, "path": self.path, "body": body})
        return body

    def do_GET(self):
        self._record("GET")
        base = f"http://127.0.0.1:{self.server.server_port}"

        if "configuration_upload_url" in self.path:
            return self._respond(200, {"msg": {"signedUrl": f"{base}/put-archive", "key": "orgs/acme/wf/a.tar.gz"}})
        if "/wfruns/" in self.path and self.path.rstrip("/").endswith("wfrun-1"):
            return self._respond(200, {"msg": {"LatestStatus": Stub.run_status}})
        if "/artifacts/" in self.path:
            return self._respond(200, {"PolicyEvalResults": Stub.policy_results})
        if "/wfrunfacts/" in self.path:
            return self._respond(404, {"msg": "not found"})
        if "/issues/" in self.path and "/comments" in self.path:
            return self._respond(200, [])
        return self._respond(200, {"msg": "ok"})

    def do_PUT(self):
        self._record("PUT")
        self.send_response(200)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_PATCH(self):
        self._record("PATCH")
        return self._respond(200, {"id": 1})

    def do_POST(self):
        self._record("POST")
        if self.path.endswith("/wfruns/"):
            return self._respond(201, {"data": {"ResourceName": "wfrun-1"}})
        if "check-runs" in self.path:
            return self._respond(201, {"id": 2})
        if "/comments" in self.path:
            return self._respond(201, {"id": 1})
        return self._respond(201, {"msg": "created"})


@pytest.fixture
def stub():
    Stub.requests = []
    Stub.run_status = "COMPLETED"
    Stub.policy_results = {}
    server = HTTPServer(("127.0.0.1", 0), Stub)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield server
    server.shutdown()


def plan_with_a_secret():
    """A plan whose secret is masked in resource_changes and *also* present in planned_values."""
    return {
        "format_version": "1.2",
        "terraform_version": "1.5.7",
        "resource_changes": [
            {
                "address": "local_sensitive_file.secret",
                "type": "local_sensitive_file",
                "change": {
                    "actions": ["create"],
                    "after": {"content": SECRET, "filename": "out.txt"},
                    "after_sensitive": {"content": True},
                },
            }
        ],
        "planned_values": {
            "root_module": {"resources": [{"type": "local_sensitive_file", "values": {"content": SECRET}}]}
        },
    }


def run_action(tmp_path, stub, **overrides):
    base = f"http://127.0.0.1:{stub.server_port}"

    source = tmp_path / "src"
    source.mkdir(exist_ok=True)
    (source / "main.tf").write_text('resource "null_resource" "a" {}')
    (source / "plan.json").write_text(json.dumps(plan_with_a_secret()))

    event = tmp_path / "event.json"
    event.write_text(
        json.dumps({"pull_request": {"number": 7, "title": "Add a VPC", "head": {"sha": "9f2c1ab" + "0" * 33}}})
    )

    env = dict(os.environ)
    env.update(
        {
            "INPUT_SG_API_KEY": "sgo_test",
            "INPUT_SG_ORG": "acme",
            "INPUT_SG_API_URL": f"{base}/api/v1",
            "INPUT_SG_DASHBOARD_URL": base,
            "INPUT_INPUT_PATH": str(source / "plan.json"),
            "INPUT_INPUT_KIND": "terraform_plan",
            "INPUT_SOURCE_DIR": str(source),
            "INPUT_COMMENT_TAG": "default",
            "INPUT_COMMENT": "true",
            "INPUT_CHECK": "true",
            "INPUT_FAIL_ON_ERROR": "false",
            "INPUT_TIMEOUT": "60",
            "INPUT_GITHUB_TOKEN": "ghs_test",
            "GITHUB_REPOSITORY": "acme/infra",
            "GITHUB_SERVER_URL": "https://github.com",
            "GITHUB_API_URL": base,
            "GITHUB_EVENT_NAME": "pull_request",
            "GITHUB_EVENT_PATH": str(event),
            "GITHUB_WORKFLOW": "policy",
            "GITHUB_RUN_ID": "1",
            "GITHUB_ACTOR": "someone",
            "GITHUB_OUTPUT": str(tmp_path / "outputs.txt"),
            "GITHUB_STEP_SUMMARY": str(tmp_path / "summary.md"),
        }
    )
    env.update(overrides)

    completed = subprocess.run(
        [sys.executable, ACTION], env=env, cwd=str(tmp_path), capture_output=True, text=True
    )
    outputs = {}
    if (tmp_path / "outputs.txt").exists():
        raw = (tmp_path / "outputs.txt").read_text()
        for block in raw.split("\n"):
            if "<<" in block:
                outputs[block.split("<<")[0]] = None
    return completed, outputs


def uploaded_archive():
    for request in Stub.requests:
        if request["method"] == "PUT":
            return request["body"]
    return None


def archive_contents(body):
    """Every byte in the uploaded tarball, for leak assertions."""
    blob = b""
    with tarfile.open(fileobj=io.BytesIO(body), mode="r:gz") as tar:
        for member in tar.getmembers():
            blob += member.name.encode()
            if member.isfile():
                blob += tar.extractfile(member).read()
    return blob


# --- the assertion that matters ----------------------------------------------------------------


def test_the_secret_never_leaves_the_runner(tmp_path, stub):
    """
    Asserted against every recorded request body, not against the masking function's return value.

    This is the shape that leaked in QA: masked correctly in resource_changes, plaintext in
    planned_values, which mirrors every resource's values and carries no sensitivity markers.
    """
    run_action(tmp_path, stub)

    for request in Stub.requests:
        assert SECRET.encode() not in request["body"], f"secret leaked in {request['method']} {request['path']}"

    archive = uploaded_archive()
    assert archive is not None, "no archive was uploaded"
    assert SECRET.encode() not in archive_contents(archive)


def test_the_raw_plan_on_disk_is_not_packed(tmp_path, stub):
    """
    The source directory contains plan.json -- unmasked, since that is what the user generated.
    Only the masked copy may reach the archive.
    """
    run_action(tmp_path, stub)

    with tarfile.open(fileobj=io.BytesIO(uploaded_archive()), mode="r:gz") as tar:
        packed = json.loads(tar.extractfile("plan.json").read())

    assert packed["resource_changes"][0]["change"]["after"]["content"] == "__SG_REDACTED__"
    assert "planned_values" not in packed


def test_provider_cache_is_not_uploaded(tmp_path, stub):
    source = tmp_path / "src"
    source.mkdir(exist_ok=True)
    (source / ".terraform").mkdir(exist_ok=True)
    (source / ".terraform" / "provider").write_bytes(b"x" * 4096)

    run_action(tmp_path, stub)

    with tarfile.open(fileobj=io.BytesIO(uploaded_archive()), mode="r:gz") as tar:
        assert not any(name.startswith(".terraform/") for name in tar.getnames())


# --- run creation ------------------------------------------------------------------------------


def test_run_is_created_with_the_archive_and_no_step_config(tmp_path, stub):
    run_action(tmp_path, stub)

    created = [r for r in Stub.requests if r["method"] == "POST" and r["path"].endswith("/wfruns/")]
    assert len(created) == 1

    body = json.loads(created[0]["body"])
    assert body["TerraformAction"] == {"action": "policy-only"}
    assert body["terraformProjectZip"] == "orgs/acme/wf/a.tar.gz"
    assert "WfStepsConfig" not in body, "core ignores it for TERRAFORM workflows"


def test_trigger_details_do_not_claim_to_be_a_webhook(tmp_path, stub):
    """
    The run controller posts its own comment and check when type is github_webhook. Claiming it
    would double-post on every pull request.
    """
    run_action(tmp_path, stub)

    body = json.loads([r for r in Stub.requests if r["path"].endswith("/wfruns/")][0]["body"])

    assert body["TriggerDetails"]["type"] == "github_action"
    assert "commentsUrl" not in body["TriggerDetails"]
    assert "checksApiUrl" not in body["TriggerDetails"]
    assert body["TriggerDetails"]["prId"] == "7"


def test_workflow_is_created_as_terraform(tmp_path, stub):
    run_action(tmp_path, stub)

    created = [r for r in Stub.requests if r["method"] == "POST" and r["path"].endswith("/wfs/")]
    body = json.loads(created[0]["body"])

    assert body["WfType"] == "TERRAFORM"
    assert body["TerraformConfig"]["managedTerraformState"] is False
    # Id is a SlugField: dots are rejected, so github.com- would 400.
    assert "." not in body["Id"]


# --- reporting ---------------------------------------------------------------------------------


def test_posts_one_comment_and_one_check(tmp_path, stub):
    Stub.policy_results = {"p": [{"rule_name": "r", "result": "PASS", "evaluations": {"passes": []}}]}

    run_action(tmp_path, stub)

    comments = [r for r in Stub.requests if "/comments" in r["path"] and r["method"] in ("POST", "PATCH")]
    checks = [r for r in Stub.requests if "check-runs" in r["path"]]

    assert len(comments) == 1
    assert len(checks) == 1
    assert json.loads(checks[0]["body"])["conclusion"] == "success"


def test_failing_policy_maps_to_a_failure_conclusion(tmp_path, stub):
    Stub.policy_results = {"p": [{"rule_name": "r", "result": "FAIL", "evaluations": {"fails": []}}]}

    run_action(tmp_path, stub)

    checks = [r for r in Stub.requests if "check-runs" in r["path"]]
    assert json.loads(checks[0]["body"])["conclusion"] == "failure"


# --- exit codes --------------------------------------------------------------------------------


def test_failing_policy_is_green_without_fail_on_error(tmp_path, stub):
    Stub.policy_results = {"p": [{"rule_name": "r", "result": "FAIL", "evaluations": {"fails": []}}]}

    completed, _ = run_action(tmp_path, stub)

    assert completed.returncode == 0


def test_failing_policy_is_red_with_fail_on_error(tmp_path, stub):
    Stub.policy_results = {"p": [{"rule_name": "r", "result": "FAIL", "evaluations": {"fails": []}}]}

    completed, _ = run_action(tmp_path, stub, INPUT_FAIL_ON_ERROR="true")

    assert completed.returncode == 3, "3 distinguishes a policy failure from a tool failure"


def test_errored_run_is_red_even_without_fail_on_error(tmp_path, stub):
    """fail-on-error governs policy verdicts, not tool health."""
    Stub.run_status = "ERRORED"

    completed, _ = run_action(tmp_path, stub, INPUT_FAIL_ON_ERROR="false")

    assert completed.returncode == 1


def test_unreachable_platform_is_red(tmp_path, stub):
    completed, _ = run_action(tmp_path, stub, INPUT_SG_API_URL="http://127.0.0.1:1/api/v1")

    assert completed.returncode == 1


def test_missing_inputs_fail_fast(tmp_path, stub):
    completed, _ = run_action(tmp_path, stub, INPUT_SG_ORG="")

    assert completed.returncode == 1
    assert "sg-org" in completed.stdout
