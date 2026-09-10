"""Bounded git helpers for the PoC release agent."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

_PACKAGE_DIR = Path(__file__).resolve().parent
if str(_PACKAGE_DIR) not in sys.path:
    sys.path.insert(0, str(_PACKAGE_DIR))

from safety import (
    SafetyError,
    assert_allowed_lineage_ref,
    assert_release_branch,
    guard_git_args,
    normalize_ref,
)


class GitRepo:
    """Run inspected git commands in a single repository."""

    def __init__(self, path: str | Path) -> None:
        """Create a helper bound to *path*."""
        self.path = Path(path).resolve()

    def run(self, args: list[str], *, check: bool = True) -> subprocess.CompletedProcess[str]:
        """Run a git subcommand after safety checks.

        Args:
            args: Git arguments without the leading ``git`` token.
            check: If True, raise on non-zero exit.

        Returns:
            The completed process.
        """
        guard_git_args(args)
        return subprocess.run(
            ["git", *args],
            cwd=self.path,
            check=check,
            text=True,
            capture_output=True,
        )

    def capture(self, args: list[str]) -> str:
        """Return stripped stdout for a read-only git command."""
        return self.run(args).stdout.strip()

    def try_capture(self, args: list[str]) -> str | None:
        """Return stdout, or None when the command fails."""
        result = self.run(args, check=False)
        if result.returncode != 0:
            return None
        return result.stdout.strip()

    def rev_parse(self, ref: str) -> str:
        """Resolve *ref* to a commit SHA."""
        return self.capture(["rev-parse", "--verify", f"{ref}^{{commit}}"])

    def ref_exists(self, ref: str) -> bool:
        """Return True if *ref* resolves to a commit."""
        result = self.run(["rev-parse", "--verify", f"{ref}^{{commit}}"], check=False)
        return result.returncode == 0

    def resolve(self, ref: str) -> str:
        """Resolve a short name, ``origin/`` name, or SHA to a usable ref."""
        candidates = [
            ref,
            f"origin/{normalize_ref(ref)}",
            f"refs/heads/{normalize_ref(ref)}",
            f"refs/remotes/origin/{normalize_ref(ref)}",
        ]
        seen: set[str] = set()
        for candidate in candidates:
            if candidate in seen:
                continue
            seen.add(candidate)
            if self.ref_exists(candidate):
                return candidate
        raise SafetyError(f"Cannot resolve git ref {ref!r}.")

    def is_ancestor(self, maybe_ancestor: str, ref: str) -> bool:
        """Return True if *maybe_ancestor* is an ancestor of *ref*."""
        result = self.run(
            ["merge-base", "--is-ancestor", maybe_ancestor, ref],
            check=False,
        )
        return result.returncode == 0

    def merge_base(self, left: str, right: str) -> str:
        """Return the merge-base SHA of two refs."""
        return self.capture(["merge-base", left, right])

    def rev_list(self, rev_range: str, *, no_merges: bool = True) -> list[str]:
        """Return oldest-first commit SHAs in *rev_range*."""
        args = ["rev-list", "--reverse"]
        if no_merges:
            args.append("--no-merges")
        args.append(rev_range)
        output = self.capture(args)
        if not output:
            return []
        return output.splitlines()

    def commit_subject(self, sha: str) -> str:
        """Return the first line of a commit message."""
        return self.capture(["log", "-1", "--format=%s", sha])

    def commit_body(self, sha: str) -> str:
        """Return the commit message body (excluding the subject)."""
        return self.capture(["log", "-1", "--format=%b", sha])

    def commit_message(self, sha: str) -> str:
        """Return the full commit message."""
        return self.capture(["log", "-1", "--format=%B", sha])

    def files_changed(self, sha: str) -> list[str]:
        """Return paths changed by a single commit."""
        output = self.capture(
            ["diff-tree", "--no-commit-id", "--name-only", "-r", sha]
        )
        if not output:
            return []
        return output.splitlines()

    def diff_names(self, left: str, right: str) -> list[str]:
        """Return names changed between two commits."""
        output = self.capture(["diff", "--name-only", f"{left}...{right}"])
        if not output:
            return []
        return output.splitlines()

    def for_each_ref(self, pattern: str) -> list[str]:
        """Return short ref names matching *pattern*."""
        output = self.capture(
            ["for-each-ref", "--format=%(refname:short)", pattern]
        )
        if not output:
            return []
        return output.splitlines()

    def current_ref(self) -> str:
        """Return the current branch name, or HEAD."""
        symbolic = self.try_capture(["symbolic-ref", "--short", "HEAD"])
        if symbolic:
            return symbolic
        return "HEAD"

    def checkout(self, ref: str) -> None:
        """Check out an existing ref without creating a branch."""
        if normalize_ref(ref) in {"main", "develop", "test"}:
            raise SafetyError(f"Refusing to check out production branch {ref!r}.")
        self.run(["checkout", "--detach", ref] if ref == "HEAD" else ["checkout", ref])

    def checkout_release_from_main(self, release_ref: str, main_ref: str) -> bool:
        """Create *release_ref* from *main_ref*, or reuse it if already pushed.

        Returns:
            True if a new branch was created from main and still needs cherry-pick.
            False if an existing main-based release branch was checked out.
        """
        short_release = assert_release_branch(release_ref)
        short_main = assert_allowed_lineage_ref(main_ref, "main")
        main_resolved = self.resolve(main_ref)
        main_sha = self.rev_parse(main_resolved)
        remote = f"origin/{short_release}"
        if self.ref_exists(short_release) or self.ref_exists(remote):
            if self.ref_exists(short_release):
                self.run(["checkout", short_release])
            else:
                self.run(["checkout", "-b", short_release, remote])
            head = self.rev_parse("HEAD")
            if not self.is_ancestor(main_sha, head):
                raise SafetyError(
                    f"Existing release branch {short_release!r} is not based on "
                    f"{short_main}. The agent will not use it."
                )
            if self._head_is_test_tip():
                raise SafetyError("Release branch must not start at test.")
            return False
        self.run(["checkout", "-b", short_release, main_resolved])
        head = self.rev_parse("HEAD")
        if head != main_sha:
            raise SafetyError(
                f"Release branch {short_release} was not created from {short_main}."
            )
        if self._head_is_test_tip():
            raise SafetyError("Release branch must not start at test.")
        return True

    def _head_is_test_tip(self) -> bool:
        """Return True if HEAD is the test branch tip."""
        for candidate in ("poc/demo/test", "origin/poc/demo/test"):
            if self.ref_exists(candidate) and self.rev_parse("HEAD") == self.rev_parse(
                candidate
            ):
                return True
        return False

    def cherry_pick(self, shas: list[str]) -> None:
        """Apply original feature commits onto the current release branch."""
        if not shas:
            raise SafetyError("No commits to cherry-pick.")
        result = self.run(["cherry-pick", "-x", *shas], check=False)
        if result.returncode != 0:
            self.run(["cherry-pick", "--abort"], check=False)
            raise SafetyError(
                "Cherry-pick onto main failed. The requested feature is not "
                "independently releasable from the original commits.\n"
                f"{result.stderr}"
            )

    def delete_local_branch(self, ref: str) -> None:
        """Delete an agent-owned local release branch."""
        short = assert_release_branch(ref)
        current = self.current_ref()
        if normalize_ref(current) == short:
            self.run(["checkout", "poc/demo/main"], check=False)
            if normalize_ref(self.current_ref()) == short:
                self.run(["checkout", "--detach", "HEAD"])
        self.run(["branch", "-D", short], check=False)

    def push_release(self, ref: str, remote: str = "origin") -> None:
        """Push an agent-owned release branch to *remote*."""
        short = assert_release_branch(ref)
        self.run(["push", remote, short])
