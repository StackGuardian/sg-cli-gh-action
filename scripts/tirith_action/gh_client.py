"""
GitHub API client for the sticky PR comment and the check run.

Both are posted from the runner with ${{ github.token }}, so no GitHub credential is ever sent to
StackGuardian.

Note the platform can also post a comment and a check of its own, from sg-run-controller. That
path is gated on `TriggerDetails.type == "github_webhook"`; our runs set `github_action`, so it
does not fire. That gate is load-bearing -- see the README. The check created here is named
`Tirith Policy`, distinct from the platform's `StackGuardian Workflow Run`, so the two can coexist
if a repo ever uses both.
"""

import json
import urllib.error
import urllib.parse
import urllib.request


class GitHubError(Exception):
    pass


class GitHubClient:
    def __init__(self, token, repository, api_url="https://api.github.com", timeout=30):
        self.token = token
        self.repository = repository
        self.api_url = api_url.rstrip("/")
        self.timeout = timeout

    def _request(self, method, path, body=None):
        url = f"{self.api_url}{path}"
        data = json.dumps(body).encode() if body is not None else None
        request = urllib.request.Request(url, data=data, method=method)
        request.add_header("Authorization", f"Bearer {self.token}")
        request.add_header("Accept", "application/vnd.github+json")
        request.add_header("X-GitHub-Api-Version", "2022-11-28")
        if data:
            request.add_header("Content-Type", "application/json")

        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                raw = response.read()
                return response.status, (json.loads(raw) if raw else {})
        except urllib.error.HTTPError as e:
            raw = e.read()
            raise GitHubError(f"{method} {path} -> HTTP {e.code}: {raw[:400].decode('utf-8', 'replace')}")
        except (urllib.error.URLError, TimeoutError) as e:
            raise GitHubError(f"{method} {path} failed: {e}")

    def find_sticky_comment(self, pr_number, marker):
        """
        Find this action's own previous comment by its hidden marker.

        A match needs the marker *and* one of two signals that the comment is ours, so a human
        quoting the marker in a reply cannot cause the action to overwrite what they wrote:

          * the author is a Bot -- true for the default `${{ github.token }}`; or
          * the body *begins* with the marker, which is where the renderer always puts it.

        The second signal is not redundant. With `github-token` overridden by a personal access
        token the comment is authored by a `User`, so the Bot test alone never matched and the
        action posted a fresh comment on every single run, forever.
        """
        page = 1
        while page <= 10:
            status, comments = self._request(
                "GET", f"/repos/{self.repository}/issues/{pr_number}/comments?per_page=100&page={page}"
            )
            if status != 200 or not comments:
                return None
            for comment in comments:
                body = comment.get("body") or ""
                if marker not in body:
                    continue
                if body.startswith(marker) or (comment.get("user") or {}).get("type") == "Bot":
                    return comment["id"]
            if len(comments) < 100:
                return None
            page += 1
        return None

    def upsert_comment(self, pr_number, marker, body):
        """Update the existing sticky comment if there is one, otherwise create it."""
        comment_id = self.find_sticky_comment(pr_number, marker)
        if comment_id:
            # Editing rather than reposting keeps the comment in place in the timeline and does
            # not re-notify everyone following the PR.
            self._request("PATCH", f"/repos/{self.repository}/issues/comments/{comment_id}", {"body": body})
            return comment_id

        _, created = self._request(
            "POST", f"/repos/{self.repository}/issues/{pr_number}/comments", {"body": body}
        )
        return created.get("id")

    def create_check_run(self, head_sha, name, conclusion, title, summary, details_url=None):
        """
        Create a completed check run.

        Annotations are deliberately omitted: they only render inline when anchored to a path and
        line inside the PR diff, and terraform plan JSON carries no source positions at all. A
        fabricated file:line would be worse than none.
        """
        body = {
            "name": name,
            "head_sha": head_sha,
            "status": "completed",
            "conclusion": conclusion,
            "output": {"title": title[:255], "summary": summary},
        }
        if details_url:
            body["details_url"] = details_url

        _, created = self._request("POST", f"/repos/{self.repository}/check-runs", body)
        return created.get("id")
