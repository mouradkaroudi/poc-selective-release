"""Build the A/B demonstration git topology used by the PoC."""

from __future__ import annotations

import subprocess
from pathlib import Path

FEATURE_A_PATH = "poc/demo/features/A.txt"
FEATURE_B_PATH = "poc/demo/features/B.txt"
SHARED_PATH = "poc/demo/features/shared.txt"


class SeedError(RuntimeError):
    """Raised when the demo topology cannot be created."""


def _git(repo: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        ["git", *args],
        cwd=repo,
        check=False,
        text=True,
        capture_output=True,
    )
    if check and result.returncode != 0:
        raise SeedError(
            f"git {' '.join(args)} failed:\n{result.stderr or result.stdout}"
        )
    return result


def _configure(repo: Path) -> None:
    _git(repo, "config", "user.email", "poc-release-agent@example.com")
    _git(repo, "config", "user.name", "PoC Release Agent")
    _git(repo, "config", "commit.gpgsign", "false")
    _git(repo, "config", "advice.detachedHead", "false")


def _write(repo: Path, relative: str, content: str) -> None:
    path = repo / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="ascii")


def _commit(repo: Path, message: str) -> None:
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", message)


def init_demo_repo(repo: Path) -> None:
    """Initialize an empty git repo with PoC commit identity."""
    repo.mkdir(parents=True, exist_ok=True)
    if not (repo / ".git").exists():
        _git(repo, "init")
    _configure(repo)


def build_independent_ab_topology(repo: Path) -> dict[str, str]:
    """Create main/develop/test with independent features A and B.

    Layout::

        poc/demo/main
            \\
             poc/demo/develop
                  ├── Feature A
                  └── Feature B
                       \\
                        poc/demo/test  (A + B)

    Feature B does not modify Feature A files, so B is independently
    releasable onto main.

    Returns:
        Map of logical names to branch names.
    """
    init_demo_repo(repo)
    _write(repo, "poc/demo/README.md", "PoC production baseline\n")
    _commit(repo, "poc: demo production baseline")
    _git(repo, "branch", "-M", "poc/demo/main")
    _git(repo, "checkout", "-b", "poc/demo/develop")

    _git(repo, "checkout", "-b", "poc/demo/feature-a")
    _write(repo, FEATURE_A_PATH, "FEATURE_A\n")
    _commit(repo, "feat: Feature A")
    _git(repo, "checkout", "poc/demo/develop")
    _git(repo, "merge", "--no-ff", "poc/demo/feature-a", "-m", "merge Feature A into develop")

    _git(repo, "checkout", "-b", "poc/demo/feature-b")
    _write(repo, FEATURE_B_PATH, "FEATURE_B\n")
    _commit(repo, "feat: Feature B")
    _git(repo, "checkout", "poc/demo/develop")
    _git(repo, "merge", "--no-ff", "poc/demo/feature-b", "-m", "merge Feature B into develop")

    _git(repo, "checkout", "-b", "poc/demo/test", "poc/demo/main")
    _git(repo, "merge", "--no-ff", "poc/demo/develop", "-m", "merge develop into test")
    _git(repo, "checkout", "poc/demo/main")
    return {
        "main": "poc/demo/main",
        "develop": "poc/demo/develop",
        "test": "poc/demo/test",
        "feature_a": "poc/demo/feature-a",
        "feature_b": "poc/demo/feature-b",
    }


def build_dependent_ab_topology(repo: Path) -> dict[str, str]:
    """Create a topology where Feature B depends on Feature A.

    B modifies the same file A introduced and declares ``Depends-On``.
    """
    init_demo_repo(repo)
    _write(repo, "poc/demo/README.md", "PoC production baseline\n")
    _commit(repo, "poc: demo production baseline")
    _git(repo, "branch", "-M", "poc/demo/main")
    _git(repo, "checkout", "-b", "poc/demo/develop")

    _git(repo, "checkout", "-b", "poc/demo/feature-a")
    _write(repo, SHARED_PATH, "FEATURE_A_SHARED\n")
    _commit(repo, "feat: Feature A")
    _git(repo, "checkout", "poc/demo/develop")
    _git(repo, "merge", "--no-ff", "poc/demo/feature-a", "-m", "merge Feature A into develop")

    _git(repo, "checkout", "-b", "poc/demo/feature-b")
    _write(repo, SHARED_PATH, "FEATURE_A_SHARED\nFEATURE_B_NEEDS_A\n")
    _commit(
        repo,
        "feat: Feature B\n\nDepends-On: poc/demo/feature-a\n",
    )
    _git(repo, "checkout", "poc/demo/develop")
    _git(repo, "merge", "--no-ff", "poc/demo/feature-b", "-m", "merge Feature B into develop")

    _git(repo, "checkout", "-b", "poc/demo/test", "poc/demo/main")
    _git(repo, "merge", "--no-ff", "poc/demo/develop", "-m", "merge develop into test")
    _git(repo, "checkout", "poc/demo/main")
    return {
        "main": "poc/demo/main",
        "develop": "poc/demo/develop",
        "test": "poc/demo/test",
        "feature_a": "poc/demo/feature-a",
        "feature_b": "poc/demo/feature-b",
    }


def assert_not_production_repo(repo: Path) -> None:
    """Refuse to seed demo branches inside a checkout of production lineage."""
    head = _git(repo, "rev-parse", "--abbrev-ref", "HEAD", check=False).stdout.strip()
    if head in {"main", "develop", "test"}:
        raise SeedError(
            f"Refusing to seed demo topology while HEAD is production branch {head!r}."
        )


def seed_into_existing_repo(repo: Path) -> dict[str, str]:
    """Create namespaced demo branches from the current HEAD without rewriting it.

    ``poc/demo/main`` is pointed at the current commit so it carries the PoC
    workflow. Features A and B are added only on demo branches. The original
    branch is restored.

    Returns:
        Map of logical names to branch names.
    """
    assert_not_production_repo(repo)
    _configure(repo)
    original = _git(repo, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip()
    original_sha = _git(repo, "rev-parse", "HEAD").stdout.strip()
    for name in (
        "poc/demo/main",
        "poc/demo/develop",
        "poc/demo/test",
        "poc/demo/feature-a",
        "poc/demo/feature-b",
        "poc/demo/release/feature-b",
    ):
        exists = _git(repo, "rev-parse", "--verify", name, check=False)
        if exists.returncode == 0:
            raise SeedError(
                f"Refusing to overwrite existing branch {name!r}. "
                "Delete the poc/demo/* demo refs first if you want to reseed."
            )

    _git(repo, "branch", "poc/demo/main", original_sha)
    _git(repo, "branch", "poc/demo/develop", original_sha)

    _git(repo, "checkout", "-b", "poc/demo/feature-a", "poc/demo/main")
    _write(repo, FEATURE_A_PATH, "FEATURE_A\n")
    _commit(repo, "feat: Feature A")
    _git(repo, "checkout", "poc/demo/develop")
    _git(repo, "merge", "--no-ff", "poc/demo/feature-a", "-m", "merge Feature A into develop")

    _git(repo, "checkout", "-b", "poc/demo/feature-b")
    _write(repo, FEATURE_B_PATH, "FEATURE_B\n")
    _commit(repo, "feat: Feature B")
    _git(repo, "checkout", "poc/demo/develop")
    _git(repo, "merge", "--no-ff", "poc/demo/feature-b", "-m", "merge Feature B into develop")

    _git(repo, "checkout", "-b", "poc/demo/test", "poc/demo/main")
    _git(repo, "merge", "--no-ff", "poc/demo/develop", "-m", "merge develop into test")
    _git(repo, "checkout", original)
    return {
        "main": "poc/demo/main",
        "develop": "poc/demo/develop",
        "test": "poc/demo/test",
        "feature_a": "poc/demo/feature-a",
        "feature_b": "poc/demo/feature-b",
    }


def main(argv: list[str] | None = None) -> int:
    """CLI to seed namespaced demo branches into this checkout."""
    import argparse

    parser = argparse.ArgumentParser(
        description="Seed poc/demo/* branches. Never touches main/develop/test."
    )
    parser.add_argument(
        "--apply-demo-branches",
        action="store_true",
        help="Create poc/demo/* from current HEAD, then restore the current branch.",
    )
    parser.add_argument("--repo-path", default=".")
    args = parser.parse_args(argv)
    repo = Path(args.repo_path).resolve()
    if not args.apply_demo_branches:
        parser.error("Refusing to run without --apply-demo-branches.")
    refs = seed_into_existing_repo(repo)
    for key, value in refs.items():
        print(f"{key}={value}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
