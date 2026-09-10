"""GitHub pull-request adapter. Never merges, approves, or opens issues."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

_PACKAGE_DIR = Path(__file__).resolve().parent
if str(_PACKAGE_DIR) not in sys.path:
    sys.path.insert(0, str(_PACKAGE_DIR))

from report import COMMENT_MARKER
from safety import (
    CANDIDATE_LABEL,
    REQUEST_LABEL,
    SafetyError,
    guard_gh_args,
)


class GitHubPR:
    """Subset of ``gh`` used by the PoC agent."""

    def __init__(self, repo: str | None = None, *, enabled: bool = True) -> None:
        """Create an adapter.

        Args:
            repo: Optional ``owner/name`` override for ``gh``.
            enabled: If False, mutating calls raise rather than talking to GitHub.
        """
        self.repo = repo
        self.enabled = enabled

    def _run(self, args: list[str], *, input_text: str | None = None) -> str:
        command = ["gh", *args]
        guard_gh_args(command)
        if not self.enabled:
            raise SafetyError("GitHub API is disabled in this run.")
        env = os.environ.copy()
        result = subprocess.run(
            command,
            check=False,
            text=True,
            capture_output=True,
            input=input_text,
            env=env,
        )
        if result.returncode != 0:
            raise SafetyError(
                f"gh {' '.join(args)} failed:\n{result.stderr or result.stdout}"
            )
        return result.stdout.strip()

    def _repo_args(self) -> list[str]:
        if self.repo:
            return ["--repo", self.repo]
        return []

    def get_pr(self, number: int) -> dict[str, Any]:
        """Return pull request metadata as a dict."""
        raw = self._run(
            [
                "pr",
                "view",
                str(number),
                *self._repo_args(),
                "--json",
                "number,title,body,headRefName,baseRefName,labels,url,isDraft,headRepositoryOwner",
            ]
        )
        return json.loads(raw)

    def ensure_labels(self, names: list[str]) -> None:
        """Create missing labels used by the PoC (not GitHub Issues).

        Label creation is best-effort. Missing permission must not block
        candidate PR creation.
        """
        try:
            existing_raw = self._run(
                ["label", "list", *self._repo_args(), "--json", "name"]
            )
        except SafetyError:
            return
        found = {item["name"] for item in json.loads(existing_raw or "[]")}
        colors = {
            REQUEST_LABEL: "1D76DB",
            CANDIDATE_LABEL: "0E8A16",
            "poc-release-ready": "0E8A16",
            "poc-release-blocked": "B60205",
        }
        for name in names:
            if name in found:
                continue
            color = colors.get(name, "C5DEF5")
            try:
                self._run(
                    [
                        "label",
                        "create",
                        name,
                        *self._repo_args(),
                        "--color",
                        color,
                        "--description",
                        "PoC AI-assisted release workflow",
                        "--force",
                    ]
                )
            except SafetyError:
                return

    def add_labels(self, number: int, labels: list[str]) -> None:
        """Add labels to an existing pull request."""
        if not labels:
            return
        self.ensure_labels(labels)
        args = ["pr", "edit", str(number), *self._repo_args()]
        for label in labels:
            args.extend(["--add-label", label])
        self._run(args)

    def remove_labels(self, number: int, labels: list[str]) -> None:
        """Remove labels from an existing pull request."""
        args = ["pr", "edit", str(number), *self._repo_args()]
        for label in labels:
            args.extend(["--remove-label", label])
        self._run(args)

    def _repo_slug(self) -> str:
        """Return owner/name for REST calls."""
        if self.repo:
            return self.repo
        slug = os.environ.get("GITHUB_REPOSITORY", "")
        if not slug:
            raise SafetyError("GITHUB_REPOSITORY is not set.")
        return slug

    def comment(self, number: int, body: str) -> None:
        """Post or update the sticky agent report on a pull request."""
        slug = self._repo_slug()
        raw = self._run(["api", f"repos/{slug}/issues/{number}/comments"])
        comments = json.loads(raw or "[]")
        sticky = next(
            (item for item in comments if COMMENT_MARKER in (item.get("body") or "")),
            None,
        )
        payload = json.dumps({"body": body})
        if sticky:
            self._run(
                [
                    "api",
                    "--method",
                    "PATCH",
                    f"repos/{slug}/issues/comments/{sticky['id']}",
                    "--input",
                    "-",
                ],
                input_text=payload,
            )
            return
        self._run(
            [
                "pr",
                "comment",
                str(number),
                *self._repo_args(),
                "--body-file",
                "-",
            ],
            input_text=body,
        )

    def find_pr(self, *, head: str, base: str) -> dict[str, Any] | None:
        """Return an open PR for *head* -> *base*, if any."""
        raw = self._run(
            [
                "pr",
                "list",
                *self._repo_args(),
                "--head",
                head,
                "--base",
                base,
                "--state",
                "open",
                "--json",
                "number,url,title,headRefName,baseRefName",
            ]
        )
        items = json.loads(raw or "[]")
        return items[0] if items else None

    def create_pr(
        self,
        *,
        base: str,
        head: str,
        title: str,
        body: str,
        labels: list[str],
    ) -> dict[str, Any]:
        """Create a release candidate pull request. Does not merge it."""
        existing = self.find_pr(head=head, base=base)
        if existing:
            self._run(
                [
                    "pr",
                    "edit",
                    str(existing["number"]),
                    *self._repo_args(),
                    "--title",
                    title,
                    "--body-file",
                    "-",
                ],
                input_text=body,
            )
            try:
                self.add_labels(int(existing["number"]), labels)
            except SafetyError:
                pass
            viewed = self.get_pr(int(existing["number"]))
            return viewed
        self.ensure_labels(labels)
        args = [
            "pr",
            "create",
            *self._repo_args(),
            "--base",
            base,
            "--head",
            head,
            "--title",
            title,
            "--body-file",
            "-",
        ]
        labeled_args = list(args)
        for label in labels:
            labeled_args.extend(["--label", label])
        try:
            url = self._run(labeled_args, input_text=body)
        except SafetyError:
            url = self._run(args, input_text=body)
        listed = self.find_pr(head=head, base=base)
        if listed:
            return {"url": url or listed.get("url"), **listed}
        return {"url": url, "headRefName": head, "baseRefName": base}

    def merge_pr(self, number: int) -> None:
        """Explicitly rejected merge entrypoint.

        The agent must never merge. This method exists so tests can prove the
        guard fires even if a caller tries.
        """
        raise SafetyError(
            f"Refusing to merge pull request #{number}. Human review is required."
        )
