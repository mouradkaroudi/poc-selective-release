"""Hard safety gates for the PoC release agent.

The agent may inspect git state, create ``poc/demo/release/*`` branches, and
open pull requests. It must never merge, approve, force-push, or touch
production refs (``main``, ``develop``, ``test``).
"""

from __future__ import annotations

PRODUCTION_BRANCHES = frozenset({"main", "develop", "test"})
POC_NAMESPACE = "poc/demo"
ALLOWED_MAIN = f"{POC_NAMESPACE}/main"
ALLOWED_DEVELOP = f"{POC_NAMESPACE}/develop"
ALLOWED_TEST = f"{POC_NAMESPACE}/test"
RELEASE_PREFIX = f"{POC_NAMESPACE}/release/"
FEATURE_PREFIX = f"{POC_NAMESPACE}/feature-"
REQUEST_LABEL = "release-request"
CANDIDATE_LABEL = "poc-release-candidate"
READY_LABEL = "poc-release-ready"
BLOCKED_LABEL = "poc-release-blocked"

_FORBIDDEN_GH_PAIRS = frozenset(
    {
        ("pr", "merge"),
        ("pr", "review"),
        ("pr", "ready"),
        ("issue", "create"),
        ("issue", "comment"),
    }
)


class SafetyError(RuntimeError):
    """Raised when an operation would violate the PoC safety contract."""


def normalize_ref(ref: str) -> str:
    """Return a short branch name from a ref, remote ref, or SHA-prefixed name."""
    value = ref.strip()
    for prefix in (
        "refs/heads/",
        "refs/remotes/origin/",
        "refs/remotes/",
        "origin/",
    ):
        if value.startswith(prefix):
            value = value[len(prefix) :]
    return value


def is_production_branch(ref: str) -> bool:
    """Return True if *ref* is a real production branch name."""
    return normalize_ref(ref) in PRODUCTION_BRANCHES


def assert_allowed_lineage_ref(ref: str, role: str) -> str:
    """Require *ref* to be the namespaced PoC stand-in for *role*.

    Args:
        ref: Git ref supplied by the caller.
        role: One of ``main``, ``develop``, or ``test``.

    Returns:
        The normalized short ref.

    Raises:
        SafetyError: If the ref is a production branch or the wrong PoC ref.
    """
    short = normalize_ref(ref)
    expected = {
        "main": ALLOWED_MAIN,
        "develop": ALLOWED_DEVELOP,
        "test": ALLOWED_TEST,
    }[role]
    if short in PRODUCTION_BRANCHES:
        raise SafetyError(
            f"Refusing to use production branch {short!r} as PoC {role}. "
            f"Use {expected!r} only."
        )
    if short != expected:
        raise SafetyError(
            f"PoC {role} ref must be {expected!r}, got {short!r} from {ref!r}."
        )
    return short


def assert_feature_ref(ref: str) -> str:
    """Require a PoC feature ref, never a release or production branch."""
    short = normalize_ref(ref)
    if short in PRODUCTION_BRANCHES:
        raise SafetyError(f"Feature ref must not be production branch {short!r}.")
    if short.startswith(RELEASE_PREFIX):
        raise SafetyError(
            f"Refusing to treat release branch {short!r} as a release request."
        )
    if short == ALLOWED_TEST:
        raise SafetyError("Feature ref must not be test. Never release from test.")
    if not short.startswith(FEATURE_PREFIX) and not short.startswith(
        f"{POC_NAMESPACE}/feature/"
    ):
        raise SafetyError(
            f"Feature ref must start with {FEATURE_PREFIX!r}, got {short!r}."
        )
    return short


def assert_release_branch(ref: str) -> str:
    """Require a namespaced agent-owned release branch."""
    short = normalize_ref(ref)
    if not short.startswith(RELEASE_PREFIX) or short == RELEASE_PREFIX:
        raise SafetyError(
            f"Release branch must start with {RELEASE_PREFIX!r}, got {short!r}."
        )
    if short in PRODUCTION_BRANCHES:
        raise SafetyError("Release branch must not be a production branch.")
    return short


def assert_pr_base(ref: str) -> str:
    """Require the pull request base to be the PoC main stand-in."""
    return assert_allowed_lineage_ref(ref, "main")


def guard_git_args(args: list[str]) -> None:
    """Reject git invocations that could merge, force-push, or alter production."""
    if not args:
        raise SafetyError("Empty git command.")
    verb = args[0]
    joined = " ".join(args)

    if verb == "push":
        if any(a == "--force" or a == "-f" or a.startswith("--force") for a in args):
            raise SafetyError("Force push is forbidden.")
        dests = [normalize_ref(a) for a in args[1:] if not a.startswith("-")]
        dests = [d for d in dests if d not in {"origin"}]
        for dest in dests:
            if dest in PRODUCTION_BRANCHES or dest.split(":")[-1] in PRODUCTION_BRANCHES:
                raise SafetyError(f"Refusing to push production ref {dest!r}.")
            short = dest.split(":")[-1]
            short = normalize_ref(short)
            if not short.startswith(RELEASE_PREFIX):
                raise SafetyError(
                    f"Agent may only push {RELEASE_PREFIX}* refs, not {dest!r}."
                )

    if verb == "merge":
        raise SafetyError(
            "git merge is forbidden in the release agent. Cherry-pick onto "
            "a main-based release branch instead. Never merge test into main."
        )

    if verb in {"rebase", "reset"}:
        raise SafetyError(f"git {verb} is forbidden in the release agent.")

    if verb == "branch" and any(a in {"-D", "-d", "--delete"} for a in args):
        for token in args:
            if token.startswith("-"):
                continue
            short = normalize_ref(token)
            if short in PRODUCTION_BRANCHES or short in {
                ALLOWED_MAIN,
                ALLOWED_DEVELOP,
                ALLOWED_TEST,
            }:
                raise SafetyError(f"Refusing to delete protected/demo lineage {short!r}.")
            if not short.startswith(RELEASE_PREFIX):
                raise SafetyError(
                    f"Agent may only delete its own {RELEASE_PREFIX}* branches."
                )

    if verb in {"checkout", "switch"}:
        for token in args:
            if token in PRODUCTION_BRANCHES:
                raise SafetyError(
                    f"Agent must not check out production branch {token!r}."
                )

    if "merge test" in joined or "merge origin/test" in joined:
        raise SafetyError("Never merge test into a release.")


def guard_gh_args(args: list[str]) -> None:
    """Reject GitHub CLI invocations that merge, approve, or create issues."""
    if not args or args[0] != "gh":
        raise SafetyError(f"Expected gh command, got {args!r}.")
    pair = tuple(args[1:3]) if len(args) >= 3 else tuple(args[1:])
    if pair in _FORBIDDEN_GH_PAIRS:
        raise SafetyError(f"Forbidden GitHub CLI command: {' '.join(args)}")
    joined = " ".join(args)
    if "pr merge" in joined or "/merge" in joined:
        raise SafetyError("Merging pull requests is forbidden.")
    if "review --approve" in joined or "pr review" in joined:
        raise SafetyError("Approving or reviewing pull requests is forbidden.")
    if "issue create" in joined:
        raise SafetyError("GitHub Issues are out of scope. Use pull requests only.")
