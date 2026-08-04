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

        if "file_upload_url" in self.path:
            # The real shape: the URL as a bare string in msg, the key alongside it in data.
            return self._respond(
                200, {"msg": f"{base}/put-archive", "data": {"key": "orgs/acme/wf/a.tar.gz"}}
            )
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
            "GITHUB_WORKFLOW": "Policy Check",
            "GITHUB_WORKFLOW_REF": "acme/infra/.github/workflows/policy.yml@refs/heads/main",
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


def archive_members(body):
    """The member names in the uploaded tarball."""
    with tarfile.open(fileobj=io.BytesIO(body), mode="r:gz") as tar:
        return [m.name for m in tar.getmembers()]


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


def test_the_actions_own_scratch_files_are_not_uploaded(tmp_path, stub):
    """
    source-dir defaults to "." and the archive packs it, so a scratch file written next to the
    terraform lands in the upload. Verified in QA: tirith-trigger.json -- which carries the PR
    title, repo URL and actor -- shipped to the platform. They go to RUNNER_TEMP instead.
    """
    run_action(tmp_path, stub)

    with tarfile.open(fileobj=io.BytesIO(uploaded_archive()), mode="r:gz") as tar:
        names = tar.getnames()

    assert not [n for n in names if n.startswith("tirith-")], names


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
    completed, _ = run_action(tmp_path, stub, INPUT_SG_ORG="", SG_ORG="")

    assert completed.returncode == 1
    assert "sg-org" in completed.stdout


# --- credentials, identity and scoping ---------------------------------------------------------


def test_credentials_can_come_from_the_environment(tmp_path, stub):
    """
    What makes the no-`with:` one-liner possible. GitHub exposes neither secrets nor vars as
    environment automatically, so a job-level `env:` is the only route without inputs.
    """
    completed, _ = run_action(
        tmp_path, stub, INPUT_SG_API_KEY="", INPUT_SG_ORG="", SG_API_TOKEN="sgo_env", SG_ORG="acme"
    )

    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert any("/orgs/acme/" in r["path"] for r in Stub.requests)


def test_missing_credentials_name_both_routes(tmp_path, stub):
    completed, _ = run_action(
        tmp_path, stub, INPUT_SG_API_KEY="", INPUT_SG_ORG="", SG_API_TOKEN="", SG_ORG=""
    )

    assert completed.returncode == 1
    assert "sg-api-key" in completed.stdout and "SG_API_TOKEN" in completed.stdout


def test_identity_comes_from_the_workflow_filename(tmp_path, stub):
    """Not $GITHUB_WORKFLOW: that is the `name:` field, and renaming must not re-identify."""
    run_action(tmp_path, stub, INPUT_WORKFLOW_ID="")

    created = [r for r in Stub.requests if r["method"] == "POST" and r["path"].endswith("/wfs/")]
    assert created and json.loads(created[0]["body"])["Id"] == "github-com-acme-infra-policy"


def test_renaming_the_workflow_does_not_change_the_identity(tmp_path, stub):
    """
    The regression: the SG workflow identity used to follow the `name:` field, so a cosmetic rename
    silently started a fresh workflow and de-scoped every policy pointing at the old one.
    """
    run_action(tmp_path, stub, INPUT_WORKFLOW_ID="", GITHUB_WORKFLOW="Something Else Entirely")
    first = [r for r in Stub.requests if r["method"] == "POST" and r["path"].endswith("/wfs/")]
    Stub.requests.clear()

    run_action(tmp_path, stub, INPUT_WORKFLOW_ID="", GITHUB_WORKFLOW="Renamed Again")
    second = [r for r in Stub.requests if r["method"] == "POST" and r["path"].endswith("/wfs/")]

    assert json.loads(first[0]["body"])["Id"] == json.loads(second[0]["body"])["Id"]


def test_an_override_is_slugified(tmp_path, stub):
    """A terragrunt unit path is the documented use; it must not produce a malformed URL."""
    completed, _ = run_action(tmp_path, stub, INPUT_WORKFLOW_ID="live/prod/vpc")

    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert any("live-prod-vpc" in r["path"] for r in Stub.requests)
    assert not any("live/prod/vpc" in r["path"] or "live%2Fprod" in r["path"] for r in Stub.requests)


def test_workflow_group_is_used_everywhere(tmp_path, stub):
    """Policies are scoped per group, so the wrong group silently enforces nothing."""
    run_action(tmp_path, stub, INPUT_WORKFLOW_GROUP="production-infra")

    # The group is created by POST /wfgrps/ with the name in the body; everything afterwards
    # addresses it by path.
    created = [r for r in Stub.requests if r["method"] == "POST" and r["path"].endswith("/wfgrps/")]
    assert created and json.loads(created[0]["body"])["ResourceName"] == "production-infra"

    scoped = [r["path"] for r in Stub.requests if "/wfgrps/" in r["path"] and not r["path"].endswith("/wfgrps/")]
    assert scoped
    assert all("/wfgrps/production-infra/" in p for p in scoped), scoped


# --- region ------------------------------------------------------------------------------------


def test_region_and_explicit_url_are_not_both_sent(tmp_path, stub):
    """
    The CLI rejects the combination, so the wrapper must not manufacture it -- the stub URLs are
    always explicit, and a region set alongside them would break every other test here.
    """
    completed, _ = run_action(tmp_path, stub, INPUT_SG_REGION="us")

    assert completed.returncode == 0, completed.stdout + completed.stderr
    # The explicit URL won: traffic still reached the stub rather than the real US endpoint.
    assert any("/orgs/acme/" in r["path"] for r in Stub.requests)


# --- source upload -----------------------------------------------------------------------------


def test_source_is_not_uploaded_by_default(tmp_path, stub):
    """
    A one-line action must not ship the working directory to a third party by default. A secret
    hardcoded in a .tf file would go with it.
    """
    run_action(tmp_path, stub, INPUT_SOURCE_DIR="")

    names = archive_members(uploaded_archive())
    assert "main.tf" not in names
    assert "plan.json" in names, "the masked document must still be uploaded"


def test_source_is_uploaded_when_asked_for(tmp_path, stub):
    run_action(tmp_path, stub)

    assert "main.tf" in archive_members(uploaded_archive())


def test_the_archive_name_is_excluded_from_artifact_sync(tmp_path, stub):
    """
    `__sg.` keeps the archive out of the per-run artifact sync. Without it every later run of the
    workflow downloads it, forever -- the upload sync has no --delete.
    """
    run_action(tmp_path, stub)

    upload_urls = [r["path"] for r in Stub.requests if "file_upload_url" in r["path"]]
    assert upload_urls
    assert "__sg." in upload_urls[0]
