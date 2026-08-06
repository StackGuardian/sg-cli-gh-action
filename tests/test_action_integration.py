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
    # What GET /issues/<n>/comments returns. Empty by default; a test that wants to exercise the
    # sticky-comment *reuse* path sets it, which nothing did before -- which is how a bug that
    # destroyed the comment on every failing run got out.
    existing_comments = []

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
            return self._respond(200, Stub.existing_comments)
        return self._respond(200, {"msg": "ok"})

    def do_PUT(self):
        self._record("PUT")
        self.send_response(200)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_DELETE(self):
        self._record("DELETE")
        return self._respond(200, {"msg": "Artifact deleted"})

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
    Stub.existing_comments = []
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


def run_action(tmp_path, stub, unset=(), **overrides):
    """
    Run the action against the stub.

    `unset` removes variables the harness would otherwise set, which is the only way to exercise a
    real action.yml default: the harness pins INPUT_SOURCE_DIR to a subdirectory, so without this a
    test cannot tell the default from the override.
    """
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
    for name in unset:
        env.pop(name, None)

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

    # planned_values is rebuilt from the already-masked resource_changes rather than dropped.
    # Dropping it disarmed Infracost and Checkov, which read that section and nothing else; keeping
    # terraform's own copy would have leaked the secret, because it mirrors every value with no
    # sensitivity markers at all.
    planned = packed["planned_values"]["root_module"]["resources"]
    assert planned, packed["planned_values"]
    assert all(SECRET not in json.dumps(r) for r in planned), planned


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
    assert body["TerraformAction"] == {"action": "tirith-iac-governance"}
    # A context tag, not a run field: `terraformProjectZip` belongs to the CLI-driven workflow.
    assert body["ContextTags"] == {"codeZipWfArtifactPath": "orgs/acme/wf/a.tar.gz"}
    assert "terraformProjectZip" not in body
    assert "WfStepsConfig" not in body, "core ignores it for TERRAFORM workflows"


def test_trigger_details_do_not_claim_to_be_a_webhook(tmp_path, stub):
    """
    The run controller posts its own comment and check when type is github_webhook. Claiming it
    would double-post on every pull request.
    """
    run_action(tmp_path, stub)

    body = json.loads([r for r in Stub.requests if r["path"].endswith("/wfruns/")][0]["body"])

    assert body["TriggerDetails"]["type"] == "tirith"
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


def test_source_is_uploaded_by_default(tmp_path, stub):
    """
    The findings are about code, so the archive carries the code. An archive of just plan.json gives
    an autofix consumer nothing to work from.

    Runs with INPUT_SOURCE_DIR *absent*, which is the only way to see the default rather than the
    harness's override.
    """
    run_action(tmp_path, stub, unset=("INPUT_SOURCE_DIR",))

    names = archive_members(uploaded_archive())
    assert "src/main.tf" in names, names
    assert "plan.json" in names


def test_the_declared_default_matches_the_script(tmp_path, stub):
    """
    In a real run the value comes from action.yml, not from the script's fallback. If the two ever
    disagree, the tests above would be exercising something users never hit.
    """
    import re

    action_yml = os.path.join(os.path.dirname(os.path.dirname(ACTION)), "action.yml")
    block = re.search(r"\n  source-dir:\n(?:    .*\n)+", open(action_yml).read()).group(0)

    assert 'default: "."' in block, block


def test_an_empty_source_dir_is_the_opt_out(tmp_path, stub):
    """
    Deliberately distinct from absent. Somebody who cannot ship HCL to a third party needs a way to
    say so, and `source-dir: ""` is it -- so an empty value must not be swallowed by the default.
    """
    run_action(tmp_path, stub, INPUT_SOURCE_DIR="")

    names = archive_members(uploaded_archive())
    assert "main.tf" not in names
    assert "src/main.tf" not in names
    assert "plan.json" in names, "the masked document must still be uploaded"


def test_source_is_uploaded_when_a_subdirectory_is_named(tmp_path, stub):
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


def test_workflow_records_the_source_repo(tmp_path, stub):
    """
    So the workflow links back to the code instead of showing a "configure" prompt. GIT_OTHER is
    the connector-less provider: with isPrivate false it needs no auth, and core pops iacVCSConfig
    for archive-based runs so nothing ever tries to clone it.
    """
    run_action(tmp_path, stub)

    created = [r for r in Stub.requests if r["method"] == "POST" and r["path"].endswith("/wfs/")]
    source = json.loads(created[0]["body"])["VCSConfig"]["iacVCSConfig"]["customSource"]

    assert source["sourceConfigDestKind"] == "GIT_OTHER"
    assert source["config"]["repo"] == "https://github.com/acme/infra"
    assert source["config"]["isPrivate"] is False


def test_the_project_archive_is_retained_for_autofix(tmp_path, stub):
    """
    The archive is the source that produced the findings, and the autofix system reads it back from
    the run record, so deleting it would remove the only copy of what was actually evaluated.

    Retaining it is safe for later runs -- the `__sg.` prefix keeps it out of the per-run artifact
    sync -- but it is not free: nothing prunes this prefix, so it is one object per commit and tag.
    """
    run_action(tmp_path, stub)

    deleted = [r for r in Stub.requests if r["method"] == "DELETE" and "/artifacts/" in r["path"]]
    assert deleted == [], [r["path"] for r in deleted]

    uploads = [r for r in Stub.requests if "file_upload_url" in r["path"]]
    assert uploads, [r["path"] for r in Stub.requests]
    name = uploads[0]["path"].split("filename=", 1)[1].split("&", 1)[0]
    # The `__sg.` prefix is what keeps it out of every later run's working directory, and the name
    # stays flat: a nested key is swallowed by the greedy <path:wfGrp> converter in the authorizer.
    assert name.startswith("__sg."), name
    assert "/" not in name, name


# --- local mode: no StackGuardian credentials --------------------------------------------------
#
# The action used to exit 1 the moment credentials were absent. It now evaluates policy files from
# the repository instead. The tests below are weighted towards the failure modes rather than the
# happy path, because the one outcome that would make this worse than the old hard-fail is a green
# check on a pull request that nothing was actually evaluated against.


PASSING_POLICY = {
    "meta": {
        "id": "instance-type-allowed",
        "name": "Instance types come from the approved list",
        "required_provider": "stackguardian/terraform_plan",
        "version": "v1",
    },
    "evaluators": [
        {
            "id": "ev",
            "description": "instance_type must be t3.medium",
            "condition": {"type": "Equals", "value": "t3.medium", "error_tolerance": 0},
            "provider_args": {
                "operation_type": "attribute",
                "terraform_resource_attribute": "instance_type",
                "terraform_resource_type": "aws_instance",
            },
        }
    ],
    "eval_expression": "ev",
}


def failing_policy(**meta):
    policy = json.loads(json.dumps(PASSING_POLICY))
    policy["meta"].update({"id": "instance-type-denied", "name": "Instance types are restricted"})
    policy["meta"].update(meta)
    policy["evaluators"][0]["condition"]["value"] = "t2.nano"
    return policy


def local_plan():
    """A plan with a priced resource and a sensitive attribute, so masking is observable."""
    return {
        "format_version": "1.2",
        "terraform_version": "1.5.7",
        "resource_changes": [
            {
                "address": "aws_instance.app",
                "mode": "managed",
                "type": "aws_instance",
                "name": "app",
                "change": {
                    "actions": ["create"],
                    "before": None,
                    "after": {"instance_type": "t3.medium"},
                    "after_sensitive": {},
                },
            },
            {
                "address": "local_sensitive_file.secret",
                "mode": "managed",
                "type": "local_sensitive_file",
                "name": "secret",
                "change": {
                    "actions": ["create"],
                    "before": None,
                    "after": {"content": SECRET, "filename": "out.txt"},
                    "after_sensitive": {"content": True},
                },
            },
        ],
    }


def read_outputs(path):
    """Parse $GITHUB_OUTPUT's heredoc blocks into a dict, values included."""
    values = {}
    if not os.path.exists(path):
        return values
    lines = open(path).read().split("\n")
    index = 0
    while index < len(lines):
        if "<<" in lines[index]:
            name, delimiter = lines[index].split("<<", 1)
            body = []
            index += 1
            while index < len(lines) and lines[index] != delimiter:
                body.append(lines[index])
                index += 1
            values[name] = "\n".join(body)
        index += 1
    return values


def run_local(tmp_path, policies=(("policy.tirith.json", PASSING_POLICY),), plan=None, **overrides):
    """
    Run the action with no credentials at all.

    No stub server: local mode must not talk to anything, and a test that provided one could not
    prove that. GITHUB_REPOSITORY and the token are omitted too, so the reporting path is skipped.
    """
    workdir = tmp_path / "repo"
    (workdir / ".tirith" / "policies").mkdir(parents=True)
    (workdir / "plan.json").write_text(json.dumps(plan if plan is not None else local_plan()))
    for name, policy in policies:
        target = workdir / ".tirith" / "policies" / name
        target.write_text(policy if isinstance(policy, str) else json.dumps(policy))

    scratch = tmp_path / "scratch"
    scratch.mkdir()

    env = {
        "PATH": os.environ.get("PATH", ""),
        "HOME": os.environ.get("HOME", ""),
        "RUNNER_TEMP": str(scratch),
        "GITHUB_OUTPUT": str(tmp_path / "outputs.txt"),
        "INPUT_INPUT_KIND": "terraform_plan",
    }
    env.update(overrides)

    completed = subprocess.run(
        [sys.executable, ACTION], env=env, cwd=str(workdir), capture_output=True, text=True
    )
    return completed, read_outputs(str(tmp_path / "outputs.txt")), scratch


def test_no_credentials_evaluates_local_policies(tmp_path):
    completed, outputs, scratch = run_local(tmp_path)

    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert outputs["mode"] == "local"
    assert outputs["verdict"] == "passed"
    assert outputs["passed"] == "1"
    # Inference is silent by design, so the log has to say which mode ran.
    assert "evaluated locally" in completed.stdout

    body = (scratch / "tirith-comment.md").read_text()
    assert "instance-type-allowed" in body
    # No run was created, so there is nothing to link to.
    assert "View run in StackGuardian" not in body


def test_local_mode_talks_to_nothing(tmp_path):
    """
    The whole point of local mode. Run with no API URL, no token and no network stub: if any code
    path tried to reach StackGuardian it would have to invent a host, and the run would not be green.
    """
    completed, outputs, _ = run_local(tmp_path)

    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert "stackguardian.io" not in completed.stdout.replace("sg-api-key", "")


def test_local_failing_policy_is_red_with_fail_on_error(tmp_path):
    completed, outputs, _ = run_local(
        tmp_path,
        policies=(("deny.tirith.json", failing_policy()),),
        INPUT_FAIL_ON_ERROR="true",
    )

    assert completed.returncode == 3, completed.stdout + completed.stderr
    assert outputs["verdict"] == "failed"
    assert outputs["failed"] == "1"


def test_local_failing_policy_is_green_without_fail_on_error(tmp_path):
    """Matches platform mode exactly: fail-on-error is what decides whether a verdict gates."""
    completed, outputs, _ = run_local(tmp_path, policies=(("deny.tirith.json", failing_policy()),))

    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert outputs["verdict"] == "failed"


def test_no_credentials_and_no_policies_is_never_green(tmp_path):
    """
    The failure this mode most needs to avoid. A user who supplies neither credentials nor policies
    has configured nothing, and reporting that as a pass would gate nothing while looking like it did.
    """
    completed, outputs, _ = run_local(tmp_path, policies=(), INPUT_FAIL_ON_ERROR="false")

    assert completed.returncode == 1, completed.stdout + completed.stderr
    assert outputs.get("verdict") == "errored"
    # One message naming both routes, because this is the first thing a new user hits.
    combined = completed.stdout + completed.stderr
    assert "sg-api-key" in combined and "policy-path" in combined


def test_an_unevaluable_policy_is_red_regardless_of_fail_on_error(tmp_path):
    """
    "Could not evaluate" is a tool failure, not a policy decision, so it ignores fail-on-error for
    the same reason an unreachable platform does in the other mode.
    """
    completed, outputs, scratch = run_local(
        tmp_path,
        policies=(("broken.tirith.json", "{ not valid json"),),
        INPUT_FAIL_ON_ERROR="false",
    )

    assert completed.returncode == 1, completed.stdout + completed.stderr
    body = (scratch / "tirith-comment.md").read_text()
    # Surfaced as an engine problem rather than a policy violation, so it cannot be mistaken for one.
    assert "engine:" in body


def test_a_non_policy_json_file_is_not_evaluated_as_a_policy(tmp_path):
    """
    A policy directory routinely also holds the document being evaluated. Without a shape filter the
    plan is evaluated as a policy, which reports a spurious failure and buries the real findings.
    """
    completed, outputs, _ = run_local(
        tmp_path,
        policies=(("policy.tirith.json", PASSING_POLICY), ("plan.json", local_plan())),
    )

    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert "Evaluating 1 policy file(s)" in completed.stdout


def test_soft_mandatory_failure_warns_instead_of_failing(tmp_path):
    completed, outputs, _ = run_local(
        tmp_path,
        policies=(("advisory.tirith.json", failing_policy(enforcement="soft_mandatory")),),
        INPUT_FAIL_ON_ERROR="true",
    )

    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert outputs["verdict"] == "warned"
    assert outputs["warned"] == "1"


def test_an_unrecognised_enforcement_still_gates(tmp_path):
    """An unlabelled or mislabelled policy must block, not slip through as advisory."""
    completed, outputs, _ = run_local(
        tmp_path,
        policies=(("odd.tirith.json", failing_policy(enforcement="whatever")),),
        INPUT_FAIL_ON_ERROR="true",
    )

    assert completed.returncode == 3, completed.stdout + completed.stderr
    assert outputs["verdict"] == "failed"
    assert "Unrecognised meta.enforcement" in completed.stdout


def test_local_mode_masks_before_rendering(tmp_path):
    """
    Nothing is uploaded, but evaluator messages embed the values they compared and those messages
    are copied into the pull-request comment -- so an unmasked local run publishes plan values to
    GitHub. Masking also keeps a local verdict identical to the platform one for the same plan.
    """
    policy = json.loads(json.dumps(PASSING_POLICY))
    policy["meta"]["id"] = "content-check"
    policy["evaluators"][0]["condition"]["value"] = "something-else"
    policy["evaluators"][0]["provider_args"] = {
        "operation_type": "attribute",
        "terraform_resource_attribute": "content",
        "terraform_resource_type": "local_sensitive_file",
    }

    completed, _, scratch = run_local(tmp_path, policies=(("content.tirith.json", policy),))

    body = (scratch / "tirith-comment.md").read_text()
    assert SECRET not in body, body
    assert "__SG_REDACTED__" in body
    assert SECRET not in (scratch / "tirith-result.json").read_text()


def test_reporting_is_skipped_and_said_out_loud_without_a_token(tmp_path):
    """
    A missing token is usually a deliberate `github-token: ""`, but it is also what a misconfigured
    job looks like, and a silent skip makes "no comment appeared" indistinguishable from "the action
    never ran". The verdict still rides on the exit code.
    """
    completed, outputs, _ = run_local(
        tmp_path,
        policies=(("deny.tirith.json", failing_policy()),),
        INPUT_FAIL_ON_ERROR="true",
        GITHUB_REPOSITORY="acme/infra",
    )

    assert completed.returncode == 3, completed.stdout + completed.stderr
    assert "skipping the pull-request comment" in completed.stdout


def test_credentials_still_select_platform_mode(tmp_path, stub):
    """The regression that matters: inference must not divert a configured platform run."""
    completed, _ = run_action(tmp_path, stub)

    assert completed.returncode == 0, completed.stdout + completed.stderr
    created = [r for r in Stub.requests if r["method"] == "POST" and r["path"].endswith("/wfruns/")]
    assert created, [r["path"] for r in Stub.requests]
    assert "mode<<" in (tmp_path / "outputs.txt").read_text()


# --- sticky-comment stickiness -----------------------------------------------------------------
#
# This block exists because of a live defect. On a run that produced no report, `report()` fell back
# to a bare string with no marker and PATCHed it over the good sticky comment. The marker was then
# gone, so the comment could never be found again and every later run posted a fresh one. Observed
# in the wild -- quoted verbatim, so it stays a record of what happened rather than being rebranded
# along with everything else:
#
#   id=5191457956  created 12:01:32  updated 12:03:56  body="Tirith policy check"  (19 chars)
#
# The suite could not have caught it: the stub always returned an empty comment list, so no PATCH
# was ever issued in a test.

MARKER = "[//]: <> (tirith-comment, tag=default)"


def existing_comment(comment_id=99, author_type="Bot", body=None):
    return {
        "id": comment_id,
        "user": {"login": "github-actions[bot]", "type": author_type},
        "body": body if body is not None else f"{MARKER}\n\n## 🛡️ Tirith — 1 passed\n",
    }


def comment_writes():
    """(method, parsed body) for every comment create/update the stub saw."""
    writes = []
    for request in Stub.requests:
        if "/comments" in request["path"] and request["method"] in ("POST", "PATCH"):
            writes.append((request["method"], json.loads(request["body"] or b"{}")))
    return writes


def test_an_existing_comment_is_edited_not_reposted(tmp_path, stub):
    Stub.existing_comments = [existing_comment(comment_id=4242)]

    run_action(tmp_path, stub)

    writes = comment_writes()
    assert [m for m, _ in writes] == ["PATCH"], writes
    patched = [r for r in Stub.requests if r["method"] == "PATCH" and "/comments/" in r["path"]]
    assert patched[0]["path"].endswith("/comments/4242"), patched[0]["path"]


def test_a_comment_from_a_pat_is_still_found(tmp_path, stub):
    """
    A `github-token` overridden with a personal access token authors the comment as a `User`, not a
    `Bot`. Matching on the author alone meant the action never found its own comment and posted a
    new one on every run, forever.
    """
    Stub.existing_comments = [existing_comment(comment_id=77, author_type="User")]

    run_action(tmp_path, stub)

    assert [m for m, _ in comment_writes()] == ["PATCH"]


def test_a_human_quoting_the_marker_is_not_overwritten(tmp_path, stub):
    """The reason the author check exists at all. A quote has the marker, but not as line 1."""
    Stub.existing_comments = [
        existing_comment(comment_id=5, author_type="User", body=f"I think this is wrong:\n\n> {MARKER}\n")
    ]

    run_action(tmp_path, stub)

    assert [m for m, _ in comment_writes()] == ["POST"], comment_writes()


def test_every_posted_body_starts_with_the_marker(tmp_path, stub):
    """The invariant the bug violated. Whatever the outcome, the comment must stay findable."""
    Stub.policy_results = {}
    Stub.run_status = "ERRORED"

    run_action(tmp_path, stub)

    writes = comment_writes()
    assert writes, "no comment was posted at all"
    for method, payload in writes:
        assert payload["body"].startswith(MARKER), (method, payload["body"][:120])


def run_local_reporting(tmp_path, stub, policies, **overrides):
    """Local mode, but with the GitHub stub wired up so the comment path is exercised."""
    workdir = tmp_path / "repo"
    (workdir / ".tirith" / "policies").mkdir(parents=True)
    (workdir / "plan.json").write_text(json.dumps(local_plan()))
    for name, policy in policies:
        (workdir / ".tirith" / "policies" / name).write_text(
            policy if isinstance(policy, str) else json.dumps(policy)
        )

    event = tmp_path / "event.json"
    event.write_text(json.dumps({"pull_request": {"number": 7, "head": {"sha": "9f2c1ab" + "0" * 33}}}))

    scratch = tmp_path / "scratch"
    scratch.mkdir()

    env = {
        "PATH": os.environ.get("PATH", ""),
        "HOME": os.environ.get("HOME", ""),
        "RUNNER_TEMP": str(scratch),
        "GITHUB_OUTPUT": str(tmp_path / "outputs.txt"),
        "INPUT_INPUT_KIND": "terraform_plan",
        "INPUT_GITHUB_TOKEN": "ghs_test",
        "GITHUB_REPOSITORY": "acme/infra",
        "GITHUB_API_URL": f"http://127.0.0.1:{stub.server_port}",
        "GITHUB_EVENT_NAME": "pull_request",
        "GITHUB_EVENT_PATH": str(event),
    }
    env.update(overrides)

    completed = subprocess.run(
        [sys.executable, ACTION], env=env, cwd=str(workdir), capture_output=True, text=True
    )
    return completed


def test_a_local_run_with_nothing_to_evaluate_keeps_the_comment_findable(tmp_path, stub):
    """
    The exact failure that was observed. Run 1 posts findings; run 2 finds no policies. Run 2 must
    edit the comment to say so -- with the marker intact -- not replace it with a bare string that
    orphans it.
    """
    Stub.existing_comments = [existing_comment(comment_id=1234)]

    completed = run_local_reporting(tmp_path, stub, policies=())

    assert completed.returncode == 1, completed.stdout + completed.stderr

    writes = comment_writes()
    assert [m for m, _ in writes] == ["PATCH"], writes
    body = writes[0][1]["body"]
    assert body.startswith(MARKER), body[:200]
    # And it says what to do about it, rather than just "Tirith IaC Governance".
    assert "policy-path" in body or "credentials" in body


def test_a_local_run_whose_input_is_missing_keeps_the_comment_findable(tmp_path, stub):
    """The other early return: a LocalError from prepare_input, e.g. no plan document."""
    Stub.existing_comments = [existing_comment(comment_id=555)]

    completed = run_local_reporting(
        tmp_path, stub, policies=(("policy.tirith.json", PASSING_POLICY),), INPUT_INPUT_PATH="no-such-plan.json"
    )

    assert completed.returncode == 1, completed.stdout + completed.stderr
    writes = comment_writes()
    assert [m for m, _ in writes] == ["PATCH"], writes
    assert writes[0][1]["body"].startswith(MARKER)


def test_a_dropped_source_tree_raises_a_warning_annotation(tmp_path, stub):
    """
    The CLI degrades to documents-only rather than failing when the tree is too large, and logs it.
    A log line in a green job is easy to miss, and the consequence -- an archive with no code in it --
    is exactly what somebody reading it later needs to know about.
    """
    source = tmp_path / "src"
    source.mkdir()
    (source / "main.tf").write_text('resource "null_resource" "a" {}')
    (source / "plan.json").write_text(json.dumps(plan_with_a_secret()))
    # Random, so gzip cannot compress it back under the limit.
    (source / "vendor.bin").write_bytes(os.urandom(300_000))

    completed, _ = run_action(tmp_path, stub, TIRITH_MAX_ARCHIVE_BYTES="51200")

    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert "::warning::" in completed.stdout
    assert "terraform source was not uploaded" in completed.stdout
    # The check still ran, and the documents still went.
    assert "plan.json" in archive_members(uploaded_archive())
    assert "src/main.tf" not in archive_members(uploaded_archive())


def test_the_comment_names_the_commit_it_scanned(tmp_path, stub):
    """
    The comment is edited in place, so it always shows the latest verdict and nothing else. Naming
    the commit is what lets a reader tell whether that verdict is about the head of the branch or
    about a push from an hour ago.
    """
    Stub.policy_results = {"p": [{"rule_name": "r", "result": "PASS", "evaluations": {"passes": []}}]}

    run_action(tmp_path, stub)

    writes = comment_writes()
    assert writes, "no comment was posted"
    body = writes[0][1]["body"]
    # The harness's event payload puts the PR head sha at 9f2c1ab...; short form is what git shows.
    assert "<sub>Scanned commit <code>9f2c1ab</code></sub>" in body, body[:400]
