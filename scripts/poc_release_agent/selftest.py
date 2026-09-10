#!/usr/bin/env python3
"""Prove the PoC invariant: test = A+B, production release = B only."""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

_PACKAGE_DIR = Path(__file__).resolve().parent
if str(_PACKAGE_DIR) not in sys.path:
    sys.path.insert(0, str(_PACKAGE_DIR))

from agent import analyze_request, prepare_release
from github_pr import GitHubPR
from gitops import GitRepo
from safety import SafetyError, guard_gh_args, guard_git_args
from seed_demo import (
    FEATURE_A_PATH,
    FEATURE_B_PATH,
    SHARED_PATH,
    build_dependent_ab_topology,
    build_independent_ab_topology,
)

REPO_ROOT = _PACKAGE_DIR.parents[1]
WORKFLOW_PATH = REPO_ROOT / ".github" / "workflows" / "poc-release-agent.yml"
AGENT_SOURCES = [
    _PACKAGE_DIR / "agent.py",
    _PACKAGE_DIR / "github_pr.py",
    _PACKAGE_DIR / "gitops.py",
    _PACKAGE_DIR / "safety.py",
]


def _git_show_file(repo: GitRepo, ref: str, relative: str) -> str | None:
    result = repo.run(["show", f"{ref}:{relative}"], check=False)
    if result.returncode != 0:
        return None
    return result.stdout


class IndependentReleaseTests(unittest.TestCase):
    """Feature B is releasable while Feature A remains in test."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.repo_path = Path(self._tmp.name)
        self.refs = build_independent_ab_topology(self.repo_path)
        self.repo = GitRepo(self.repo_path)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_test_contains_a_and_b_but_main_contains_neither(self) -> None:
        test = self.refs["test"]
        main = self.refs["main"]
        self.assertIn("FEATURE_A", _git_show_file(self.repo, test, FEATURE_A_PATH) or "")
        self.assertIn("FEATURE_B", _git_show_file(self.repo, test, FEATURE_B_PATH) or "")
        self.assertIsNone(_git_show_file(self.repo, main, FEATURE_A_PATH))
        self.assertIsNone(_git_show_file(self.repo, main, FEATURE_B_PATH))

    def test_prepare_releases_only_b_from_main(self) -> None:
        analysis = analyze_request(
            self.repo, feature_ref=self.refs["feature_b"]
        )
        self.assertTrue(analysis.ready, analysis.reason)
        self.assertEqual(analysis.included, ["Feature B"])
        self.assertIn("Feature A", analysis.excluded)
        self.assertIn("Feature A", analysis.test_features)
        self.assertIn("Feature B", analysis.test_features)

        prepared = prepare_release(
            self.repo,
            analysis,
            main_ref=self.refs["main"],
            test_ref=self.refs["test"],
        )
        self.assertTrue(prepared.ready, prepared.reason)
        release_ref = prepared.release_branch
        self.assertIsNotNone(release_ref)
        assert release_ref is not None
        self.assertTrue(self.repo.ref_exists(release_ref))
        self.assertTrue(
            self.repo.is_ancestor(
                self.repo.rev_parse(self.refs["main"]),
                self.repo.rev_parse(release_ref),
            )
        )
        self.assertFalse(
            self.repo.is_ancestor(
                self.repo.rev_parse(self.refs["test"]),
                self.repo.rev_parse(release_ref),
            )
        )
        self.assertIsNone(_git_show_file(self.repo, release_ref, FEATURE_A_PATH))
        self.assertIn(
            "FEATURE_B", _git_show_file(self.repo, release_ref, FEATURE_B_PATH) or ""
        )
        self.assertIn("FEATURE_A", _git_show_file(self.repo, self.refs["test"], FEATURE_A_PATH) or "")
        self.assertEqual("PASS", prepared.validation.get("Tests"))
        self.assertEqual("PASS", prepared.validation.get("Build"))
        self.assertEqual("PASS", prepared.validation.get("Not started from test"))
        self.assertEqual("PASS", prepared.validation.get("Did not merge test"))

    def test_requesting_test_as_feature_is_blocked(self) -> None:
        with self.assertRaises(SafetyError):
            analyze_request(self.repo, feature_ref=self.refs["test"])


class DependentReleaseTests(unittest.TestCase):
    """Feature B that depends on unreleased A must not be prepared."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.repo_path = Path(self._tmp.name)
        self.refs = build_dependent_ab_topology(self.repo_path)
        self.repo = GitRepo(self.repo_path)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_dependent_b_is_blocked_and_creates_no_release_branch(self) -> None:
        analysis = analyze_request(
            self.repo, feature_ref=self.refs["feature_b"]
        )
        self.assertFalse(analysis.ready)
        self.assertIn("depend", analysis.reason.lower())
        prepared = prepare_release(
            self.repo,
            analysis,
            main_ref=self.refs["main"],
            test_ref=self.refs["test"],
        )
        self.assertFalse(prepared.ready)
        self.assertFalse(self.repo.ref_exists("poc/demo/release/feature-b"))
        self.assertIn(
            "FEATURE_A_SHARED",
            _git_show_file(self.repo, self.refs["test"], SHARED_PATH) or "",
        )


class SafetyGuardTests(unittest.TestCase):
    """The agent must refuse merge, force-push, and production refs."""

    def test_git_merge_is_forbidden(self) -> None:
        with self.assertRaises(SafetyError):
            guard_git_args(["merge", "poc/demo/test"])

    def test_force_push_is_forbidden(self) -> None:
        with self.assertRaises(SafetyError):
            guard_git_args(["push", "--force", "origin", "poc/demo/release/feature-b"])

    def test_push_main_is_forbidden(self) -> None:
        with self.assertRaises(SafetyError):
            guard_git_args(["push", "origin", "main"])

    def test_gh_pr_merge_is_forbidden(self) -> None:
        with self.assertRaises(SafetyError):
            guard_gh_args(["gh", "pr", "merge", "12"])
        adapter = GitHubPR(enabled=False)
        with self.assertRaises(SafetyError):
            adapter.merge_pr(12)

    def test_sources_do_not_call_pr_merge(self) -> None:
        forbidden = ("gh pr merge", "['pr', 'merge']", '["pr", "merge"]')
        for path in AGENT_SOURCES:
            text = path.read_text(encoding="ascii")
            if path.name == "safety.py":
                continue
            for token in forbidden:
                self.assertNotIn(token, text, f"{path} contains {token}")
            self.assertNotIn("pr merge", text.replace("Merging pull requests", ""))

    def test_workflow_never_merges(self) -> None:
        text = WORKFLOW_PATH.read_text(encoding="ascii")
        self.assertNotIn("gh pr merge", text)
        self.assertNotIn("pull_request_target", text)
        self.assertIn("pull-requests: write", text)
        self.assertIn("contents: write", text)
        self.assertIn("permissions:", text)
        self.assertIn("poc/demo/main", text)


def main() -> int:
    """Run the PoC self-test suite."""
    suite = unittest.defaultTestLoader.loadTestsFromModule(sys.modules[__name__])
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    raise SystemExit(main())
