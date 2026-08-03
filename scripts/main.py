#!/usr/bin/env python3
"""
Tirith Policy Check -- GitHub Action entry point.

This is a wrapper. Everything that talks to StackGuardian -- masking, packing, uploading, running,
polling, rendering -- lives in the `tirith` CLI (`tirith platform check`). What is left here is
only the part that is genuinely GitHub-specific:

  * translating the workflow event into a workflow identity and trigger details
  * invoking the CLI
  * turning its JSON back into action outputs, a sticky pull-request comment and a check run

Keeping the split at that line is the point: a GitLab or Jenkins integration reuses the CLI
unchanged, and this file never has to be the place where platform behaviour is decided.
"""

import json
import os
import re
import subprocess
import tempfile
import sys
import uuid

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from tirith_action.gh_client import GitHubClient, GitHubError  # noqa: E402

CHECK_NAME = "Tirith Policy"

# Exit codes from `tirith platform check`. 3 means a policy said no; 1 means tirith could not
# reach the platform or the run produced no verdict. The distinction is the whole reason
# fail-on-error exists, so it must survive the round trip.
EXIT_OK = 0
EXIT_TOOL_FAILURE = 1
EXIT_POLICY_FAILED = 3


def log(message):
    print(message, flush=True)


def notice(message):
    print(f"::notice::{message}", flush=True)


def warn(message):
    print(f"::warning::{message}", flush=True)


def fail(message):
    print(f"::error::{message}", flush=True)


def env(name, default=""):
    return os.environ.get(name, default) or default


def env_bool(name, default=False):
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


def set_output(name, value):
    """
    Append to $GITHUB_OUTPUT with a random heredoc delimiter.

    A fixed delimiter is an injection vector: policy results carry resource names and messages
    that come from the evaluated infrastructure, and a crafted one containing the delimiter could
    end the block early and inject arbitrary outputs.
    """
    path = os.environ.get("GITHUB_OUTPUT")
    if not path:
        return
    delimiter = f"ghadelim_{uuid.uuid4().hex}"
    with open(path, "a") as f:
        f.write(f"{name}<<{delimiter}\n{value}\n{delimiter}\n")


def _slug(value):
    return re.sub(r"-+", "-", re.sub(r"[^a-zA-Z0-9_-]", "-", value or "")).strip("-")


def slugify_workflow_id(repository, action_name):
    """
    Derive the StackGuardian workflow identity from the repository and workflow name.

    `Id` is a DRF SlugField, so dots are rejected -- hence `github-com-` rather than `github.com-`.
    Verified: `github.com-...` is a 400, `github-com-...` is accepted.
    """
    owner, _, repo = (repository or "").partition("/")
    return _slug(f"github-com-{owner}-{repo}-{action_name}")[:100]


def _event_payload():
    path = os.environ.get("GITHUB_EVENT_PATH")
    if not path or not os.path.exists(path):
        return {}
    try:
        with open(path) as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return {}


def pull_request_number():
    payload = _event_payload()
    if payload.get("pull_request"):
        return payload["pull_request"].get("number")
    # `issue_comment` on a PR carries the number under `issue`.
    issue = payload.get("issue") or {}
    if issue.get("pull_request"):
        return issue.get("number")
    return None


def pull_request_title():
    return ((_event_payload().get("pull_request")) or {}).get("title")


def head_sha():
    """
    The SHA the check run must attach to.

    On `pull_request`, $GITHUB_SHA is the *merge* commit, which does not exist in the PR's branch
    -- a check posted against it is invisible on the PR. `pull_request.head.sha` is the real one.
    """
    payload = _event_payload()
    if payload.get("pull_request"):
        return payload["pull_request"]["head"]["sha"]
    return env("GITHUB_SHA")


def build_trigger_details(sha):
    """
    Describe what triggered this run, for the platform's UI and run history.

    Deliberately NOT `github_webhook`: the run controller gates its own PR comment and check run on
    that value (common/vcs.py), so claiming it would produce two comments and two checks on every
    pull request. `commentsUrl` and `checksApiUrl` are likewise omitted -- unused on this path, and
    leaving them out means the platform is never handed a token-shaped URL it has no business with.
    """
    repo = env("GITHUB_REPOSITORY")
    server = env("GITHUB_SERVER_URL", "https://github.com")
    pr = pull_request_number()

    details = {
        "type": "github_action",
        "ghEventType": env("GITHUB_EVENT_NAME"),
        "repoHttpUrl": f"{server}/{repo}",
        "headSha": sha,
        "ref": env("GITHUB_HEAD_REF") or env("GITHUB_REF_NAME"),
        "runUrl": f"{server}/{repo}/actions/runs/{env('GITHUB_RUN_ID')}",
        "eventInitializer": env("GITHUB_ACTOR"),
    }
    if pr:
        details["prId"] = str(pr)
        # The html_url, not the API url: this is rendered as a link in the dashboard.
        details["eventSource"] = f"{server}/{repo}/pull/{pr}"
        title = pull_request_title()
        if title:
            details["pullRequestTitle"] = title
    else:
        branch = env("GITHUB_REF_NAME")
        details["eventSource"] = f"{server}/{repo}/tree/{branch}" if branch else f"{server}/{repo}"
    return details


def comment_marker(tag):
    """
    A markdown link-reference definition, used to find this comment again on the next run.

    `[//]: <> (...)` renders as nothing and survives round-tripping through the API body, which
    `<!-- -->` does not reliably do.
    """
    return f"[//]: <> (tirith-comment, tag={tag})"


def check_conclusion(verdict):
    """
    Map a verdict to a GitHub check-run conclusion.

    `neutral` SATISFIES a required status check, so it is correct for warnings and wrong for
    anything unresolved. An errored or unreachable run must be `failure`, never `neutral` and
    never `success`.
    """
    return {
        "passed": "success",
        # Nothing in scope is not the same as a clean pass -- the likeliest cause is a policy
        # scoped to the wrong workflow group -- but it must not block either, and `neutral`
        # satisfies a required check just as `success` does.
        "no-policies": "neutral",
        "warned": "neutral",
        # A human has to act; `action_required` says exactly that and does not satisfy the check.
        "approval-required": "action_required",
        "failed": "failure",
        "errored": "failure",
    }.get(verdict, "failure")


def build_command(result_path, markdown_path, trigger_path, tag):
    """Assemble the `tirith platform check` invocation from the action inputs."""
    sha = head_sha()
    workflow_id = env("INPUT_WORKFLOW_ID") or slugify_workflow_id(
        env("GITHUB_REPOSITORY"), env("INPUT_ACTION_NAME") or env("GITHUB_WORKFLOW")
    )

    # Passed as a file rather than on argv: it carries a PR title, which is user-controlled text
    # that would otherwise need shell-safe quoting for no benefit.
    with open(trigger_path, "w") as f:
        json.dump(build_trigger_details(sha), f)

    cmd = [
        "tirith",
        "platform",
        "check",
        "--org", env("INPUT_SG_ORG"),
        "--api-url", env("INPUT_SG_API_URL"),
        "--dashboard-url", env("INPUT_SG_DASHBOARD_URL"),
        "--workflow-id", workflow_id,
        "--input-kind", env("INPUT_INPUT_KIND", "terraform_plan"),
        "--artifact-tag", tag,
        "--timeout", env("INPUT_TIMEOUT", "1800"),
        "--output-json", result_path,
        "--output-markdown", markdown_path,
        "--comment-marker", comment_marker(tag),
        "--trigger-details-file", trigger_path,
        # Read the key from stdin rather than argv: an argument is visible in `ps` to anything
        # else on the runner for the lifetime of the process.
        "--api-key", "-",
    ]

    if env("INPUT_INPUT_PATH"):
        cmd += ["--input-path", env("INPUT_INPUT_PATH")]
    if env("INPUT_STATE_PATH"):
        cmd += ["--state-path", env("INPUT_STATE_PATH")]
    if env("INPUT_INFRACOST_PATH"):
        cmd += ["--infracost-path", env("INPUT_INFRACOST_PATH")]
    if env("INPUT_SOURCE_DIR"):
        cmd += ["--source-dir", env("INPUT_SOURCE_DIR")]
    if env("INPUT_TERRAFORM_VERSION"):
        cmd += ["--terraform-version", env("INPUT_TERRAFORM_VERSION")]
    if env("INPUT_STEP_TEMPLATE_ID"):
        cmd += ["--step-template-id", env("INPUT_STEP_TEMPLATE_ID")]
    if sha:
        cmd += ["--sha", sha]
    if env_bool("INPUT_FAIL_ON_ERROR"):
        cmd += ["--fail-on-error"]

    return cmd, workflow_id, sha


def report(result, markdown_path, tag, sha, want_comment, want_check):
    """
    Post the sticky comment and the check run.

    Reporting failures are warnings, not errors. A pull request from a fork gets a read-only
    GITHUB_TOKEN, so commenting fails there through no fault of the user -- and the policy verdict
    is still carried by the job's exit code, which is what actually gates the merge.
    """
    token = env("INPUT_GITHUB_TOKEN")
    repository = env("GITHUB_REPOSITORY")
    if not token or not repository:
        return

    try:
        with open(markdown_path) as f:
            body = f.read()
    except OSError:
        body = result.get("headline", "Tirith policy check")

    gh = GitHubClient(token, repository, api_url=env("GITHUB_API_URL", "https://api.github.com"))
    verdict = result.get("verdict", "errored")

    pr = pull_request_number()
    if want_comment and pr:
        try:
            comment_id = gh.upsert_comment(pr, comment_marker(tag), body)
            set_output("comment-id", str(comment_id))
        except GitHubError as e:
            warn(f"Could not post the pull-request comment: {e}")

    if want_check and sha:
        try:
            gh.create_check_run(
                head_sha=sha,
                name=CHECK_NAME if tag == "default" else f"{CHECK_NAME} ({tag})",
                conclusion=check_conclusion(verdict),
                title=result.get("headline", "Tirith policy check"),
                # The marker is meaningless outside an issue comment.
                summary="\n".join(l for l in body.split("\n") if not l.startswith("[//]: <>")),
                details_url=result.get("wfrun_url"),
            )
        except GitHubError as e:
            warn(f"Could not create the check run: {e}")


def main():
    api_key = env("INPUT_SG_API_KEY")
    org = env("INPUT_SG_ORG")
    if not api_key or not org:
        fail("sg-api-key and sg-org are required")
        return EXIT_TOOL_FAILURE
    if not env("INPUT_INPUT_PATH") and not env("INPUT_STATE_PATH"):
        fail("one of input-path or state-path is required")
        return EXIT_TOOL_FAILURE

    tag = env("INPUT_COMMENT_TAG", "default")

    # Written to RUNNER_TEMP, never the working directory. source-dir defaults to "." and the
    # archive packs it, so a scratch file next to the terraform lands in the upload -- verified in
    # QA, where tirith-trigger.json shipped to the platform. RUNNER_TEMP is job-scoped, so
    # results-file stays readable by later steps.
    scratch = env("RUNNER_TEMP") or tempfile.gettempdir()
    result_path = os.path.join(scratch, "tirith-result.json")
    markdown_path = os.path.join(scratch, "tirith-comment.md")
    trigger_path = os.path.join(scratch, "tirith-trigger.json")

    cmd, workflow_id, sha = build_command(result_path, markdown_path, trigger_path, tag)
    log(f"Workflow: {workflow_id}")

    completed = subprocess.run(cmd, input=api_key + "\n", text=True)

    result = {}
    if os.path.exists(result_path):
        try:
            with open(result_path) as f:
                result = json.load(f)
        except (json.JSONDecodeError, OSError) as e:
            warn(f"Could not read the result document: {e}")

    verdict = result.get("verdict", "errored")
    counts = result.get("counts") or {}

    set_output("verdict", verdict)
    set_output("passed", str(counts.get("passed", 0)))
    set_output("failed", str(counts.get("failed", 0)))
    set_output("warned", str(counts.get("warned", 0)))
    set_output("results", json.dumps(result.get("policy_results") or {}))
    set_output("results-file", result_path)
    if result.get("wfrun_id"):
        set_output("wfrun-id", result["wfrun_id"])
    if result.get("wfrun_url"):
        set_output("wfrun-url", result["wfrun_url"])
        notice(f"StackGuardian run: {result['wfrun_url']}")

    report(result, markdown_path, tag, sha, env_bool("INPUT_COMMENT", True), env_bool("INPUT_CHECK", True))

    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary_path and os.path.exists(markdown_path):
        try:
            with open(markdown_path) as src, open(summary_path, "a") as dst:
                dst.write("\n".join(l for l in src.read().split("\n") if not l.startswith("[//]: <>")))
                dst.write("\n")
        except OSError:
            pass

    # The CLI already decided this; passing its code through keeps one source of truth for what
    # counts as a failure. A tool failure is red regardless of fail-on-error.
    if completed.returncode == EXIT_TOOL_FAILURE:
        fail("Tirith could not complete the check; failing closed regardless of fail-on-error")
    elif completed.returncode == EXIT_POLICY_FAILED:
        fail(result.get("headline", "Policy check failed"))
    return completed.returncode


if __name__ == "__main__":
    sys.exit(main())
