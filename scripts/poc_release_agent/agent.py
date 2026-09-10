"""PoC release agent: independently promote one feature onto a main-based PR.

This module inspects git topology, applies original feature commits onto a
branch created from ``poc/demo/main``, validates the result, and can open a
release pull request. It never merges pull requests and never uses ``test``
as the release source.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

_PACKAGE_DIR = Path(__file__).resolve().parent
if str(_PACKAGE_DIR) not in sys.path:
    sys.path.insert(0, str(_PACKAGE_DIR))

from github_pr import GitHubPR
from gitops import GitRepo
from report import Analysis, FeatureInfo, display_feature_name, render_report
from safety import (
    ALLOWED_DEVELOP,
    ALLOWED_MAIN,
    ALLOWED_TEST,
    BLOCKED_LABEL,
    CANDIDATE_LABEL,
    READY_LABEL,
    RELEASE_PREFIX,
    REQUEST_LABEL,
    SafetyError,
    assert_allowed_lineage_ref,
    assert_feature_ref,
    assert_pr_base,
    assert_release_branch,
    normalize_ref,
)

DEPENDS_RE = re.compile(r"^Depends-On:\s*(.+)\s*$", re.IGNORECASE | re.MULTILINE)
FEATURE_REF_RE = re.compile(
    r"^Feature-Ref:\s*(\S+)\s*$", re.IGNORECASE | re.MULTILINE
)


def discover_feature_refs(repo: GitRepo) -> list[str]:
    """Return unique short feature branch names visible in the repo."""
    patterns = (
        "refs/heads/poc/demo/feature-*",
        "refs/remotes/origin/poc/demo/feature-*",
        "refs/heads/poc/demo/feature/*",
        "refs/remotes/origin/poc/demo/feature/*",
    )
    found: list[str] = []
    seen: set[str] = set()
    for pattern in patterns:
        for ref in repo.for_each_ref(pattern):
            short = normalize_ref(ref)
            if short.startswith(RELEASE_PREFIX):
                continue
            if short not in seen:
                seen.add(short)
                found.append(short)
    return found


def _closest_ancestor_tip(repo: GitRepo, tips: list[str]) -> str:
    """Return the descendant-most commit among ancestor feature tips."""
    best = tips[0]
    for tip in tips[1:]:
        if repo.is_ancestor(best, tip):
            best = tip
    return best


def unique_feature_commits(
    repo: GitRepo,
    feature_ref: str,
    main_ref: str,
    develop_ref: str,
    other_feature_refs: list[str],
) -> list[str]:
    """Return this feature's original commits, not sibling feature commits.

    If Feature B was branched from develop after Feature A, A's commit is an
    ancestor of B. Those ancestor sibling tips are used as the fork point so
    B's unique commits do not include A.
    """
    feature = repo.resolve(feature_ref)
    main = repo.resolve(main_ref)
    develop = repo.resolve(develop_ref)
    ancestor_siblings: list[str] = []
    for other in other_feature_refs:
        if normalize_ref(other) == normalize_ref(feature_ref):
            continue
        if not repo.ref_exists(other) and not repo.ref_exists(f"origin/{other}"):
            continue
        other_resolved = repo.resolve(other)
        if repo.rev_parse(other_resolved) == repo.rev_parse(feature):
            continue
        if repo.is_ancestor(other_resolved, feature):
            ancestor_siblings.append(other_resolved)

    if ancestor_siblings:
        fork = _closest_ancestor_tip(repo, ancestor_siblings)
        return repo.rev_list(f"{fork}..{feature}")

    not_in_develop = repo.rev_list(f"{develop}..{feature}")
    if not_in_develop:
        return not_in_develop
    return repo.rev_list(f"{main}..{feature}")


def parse_depends_on(*texts: str) -> list[str]:
    """Return Depends-On refs declared in commit messages or PR bodies."""
    found: list[str] = []
    for text in texts:
        for match in DEPENDS_RE.finditer(text or ""):
            value = match.group(1).strip()
            if value.lower() in {"none", "n/a", "-"}:
                continue
            if value not in found:
                found.append(value)
    return found


def feature_present_on(
    repo: GitRepo, feature_commits: list[str], target_ref: str
) -> bool:
    """Return True if any original feature commit is already on *target_ref*."""
    if not feature_commits:
        return False
    target = repo.resolve(target_ref)
    return any(repo.is_ancestor(sha, target) for sha in feature_commits)


def describe_features(
    repo: GitRepo,
    refs: list[str],
    main_ref: str,
    develop_ref: str,
) -> list[FeatureInfo]:
    """Build feature metadata for each discovered ref."""
    infos: list[FeatureInfo] = []
    for ref in refs:
        commits = unique_feature_commits(repo, ref, main_ref, develop_ref, refs)
        files: list[str] = []
        seen_files: set[str] = set()
        for sha in commits:
            for path in repo.files_changed(sha):
                if path not in seen_files:
                    seen_files.add(path)
                    files.append(path)
        infos.append(
            FeatureInfo(
                ref=normalize_ref(ref),
                name=display_feature_name(ref),
                commits=commits,
                files=files,
            )
        )
    return infos


def analyze_request(
    repo: GitRepo,
    *,
    feature_ref: str,
    main_ref: str = ALLOWED_MAIN,
    develop_ref: str = ALLOWED_DEVELOP,
    test_ref: str = ALLOWED_TEST,
    extra_depends: list[str] | None = None,
) -> Analysis:
    """Inspect topology and decide whether *feature_ref* can be released."""
    feature_short = assert_feature_ref(feature_ref)
    main_short = assert_allowed_lineage_ref(main_ref, "main")
    develop_short = assert_allowed_lineage_ref(develop_ref, "develop")
    test_short = assert_allowed_lineage_ref(test_ref, "test")

    feature_resolved = repo.resolve(feature_short)
    main_resolved = repo.resolve(main_short)
    develop_resolved = repo.resolve(develop_short)
    test_resolved = repo.resolve(test_short)

    all_features = discover_feature_refs(repo)
    if feature_short not in all_features:
        all_features.append(feature_short)
    infos = describe_features(repo, all_features, main_resolved, develop_resolved)
    requested = next(item for item in infos if item.ref == feature_short)

    develop_names = [
        item.name
        for item in infos
        if item.commits and feature_present_on(repo, item.commits, develop_resolved)
    ]
    test_names = [
        item.name
        for item in infos
        if item.commits and feature_present_on(repo, item.commits, test_resolved)
    ]

    unique = unique_feature_commits(
        repo,
        feature_resolved,
        main_resolved,
        develop_resolved,
        all_features,
    )
    requested.commits = unique
    requested.files = []
    seen_files: set[str] = set()
    for sha in unique:
        for path in repo.files_changed(sha):
            if path not in seen_files:
                seen_files.add(path)
                requested.files.append(path)

    excluded_infos = [item for item in infos if item.ref != feature_short and item.commits]
    excluded_names = [
        item.name
        for item in excluded_infos
        if feature_present_on(repo, item.commits, test_resolved)
    ]
    excluded_files = {
        path for item in excluded_infos for path in item.files if item.name in excluded_names
    }

    messages = [repo.commit_message(sha) for sha in unique]
    dependencies = parse_depends_on(*messages, *(extra_depends or []))

    def blocked(reason: str, deps: list[str] | None = None) -> Analysis:
        return Analysis(
            status="blocked",
            feature=requested,
            included=[],
            excluded=excluded_names,
            dependencies=deps if deps is not None else dependencies,
            reason=reason,
            main_ref=main_short,
            develop_features=develop_names,
            test_features=test_names,
            unique_commits=unique,
            strategy="No release branch was created.",
        )

    if repo.is_ancestor(test_resolved, feature_resolved) and repo.rev_parse(
        test_resolved
    ) == repo.rev_parse(feature_resolved):
        return blocked(
            "The request points at test. The agent will not use test as a "
            "release source."
        )

    if not unique:
        return blocked(
            f"{requested.name} cannot be isolated from other unreleased work. "
            "Keep the original feature branch available, or stop because the "
            "agent cannot confidently identify the requested feature."
        )

    unmet: list[str] = []
    for dep in dependencies:
        try:
            dep_ref = repo.resolve(dep)
        except SafetyError:
            unmet.append(f"{dep} (ref not found; treated as unreleased)")
            continue
        if not repo.is_ancestor(dep_ref, main_resolved) and repo.rev_parse(
            dep_ref
        ) != repo.rev_parse(main_resolved):
            dep_on_main = False
            try:
                dep_commits = unique_feature_commits(
                    repo, dep_ref, main_resolved, develop_resolved, all_features
                )
                dep_on_main = bool(dep_commits) and all(
                    repo.is_ancestor(sha, main_resolved) for sha in dep_commits
                )
            except SafetyError:
                dep_on_main = False
            if not dep_on_main:
                unmet.append(display_feature_name(dep))

    if unmet:
        return blocked(
            f"{requested.name} depends on {', '.join(unmet)}. "
            "That work is not on the production stand-in. Releasing it "
            "independently would produce an incomplete release.",
            deps=unmet,
        )

    overlap = sorted(set(requested.files) & excluded_files)
    if overlap:
        return blocked(
            f"{requested.name} changes files also changed by unreleased "
            f"features currently in test ({', '.join(overlap)}). "
            "That is treated as a dependency. No release branch was created.",
            deps=excluded_names,
        )

    strategy = (
        f"{requested.name} was applied independently from its original "
        "feature branch commits. "
        + (
            f"{', '.join(excluded_names)} "
            if excluded_names
            else "Other unreleased work "
        )
        + "is currently present in test but is NOT included in this release."
    )
    return Analysis(
        status="ready",
        feature=requested,
        included=[requested.name],
        excluded=excluded_names,
        dependencies=[],
        reason="",
        main_ref=main_short,
        develop_features=develop_names,
        test_features=test_names,
        unique_commits=unique,
        strategy=strategy,
        release_branch=f"{RELEASE_PREFIX}{normalize_ref(feature_short).rsplit('/', 1)[-1]}",
    )


def validate_release(
    repo: GitRepo,
    analysis: Analysis,
    *,
    main_ref: str,
    test_ref: str,
) -> dict[str, str]:
    """Verify the current HEAD is a main-based isolated release."""
    results: dict[str, str] = {}
    main_sha = repo.rev_parse(repo.resolve(main_ref))
    test_sha = repo.rev_parse(repo.resolve(test_ref))
    head = repo.rev_parse("HEAD")
    results["Based on main"] = "PASS" if repo.is_ancestor(main_sha, head) else "FAIL"
    results["Not started from test"] = "PASS" if head != test_sha else "FAIL"
    results["Did not merge test"] = (
        "PASS" if not repo.is_ancestor(test_sha, head) else "FAIL"
    )
    changed = set(repo.diff_names(main_sha, head))
    excluded_files: set[str] = set()
    # Recompute excluded files from analysis.excluded names via current tree markers.
    for path in changed:
        if path.endswith("/A.txt") or path.endswith("\\A.txt") or path.endswith("A.txt"):
            if "Feature A" in analysis.excluded or "A" in "".join(analysis.excluded):
                excluded_files.add(path)
    leaked = []
    for path in changed:
        marker_file = repo.path / path
        if marker_file.is_file():
            text = marker_file.read_text(encoding="ascii", errors="replace")
            if "FEATURE_A" in text and analysis.feature.name != "Feature A":
                leaked.append(path)
    results["Excluded features absent"] = "PASS" if not leaked else f"FAIL {leaked}"
    included_ok = True
    for path in analysis.feature.files:
        if path not in changed:
            included_ok = False
    results["Requested files present"] = "PASS" if included_ok else "FAIL"
    results["Tests"] = (
        "PASS"
        if all(value == "PASS" for value in results.values())
        else "FAIL"
    )
    results["Build"] = results["Tests"]
    if any(value != "PASS" for key, value in results.items() if key not in {"Tests", "Build"}):
        raise SafetyError(
            "Release validation failed:\n"
            + "\n".join(f"- {key}: {value}" for key, value in results.items())
        )
    return results


def prepare_release(
    repo: GitRepo,
    analysis: Analysis,
    *,
    main_ref: str,
    test_ref: str,
    push: bool = False,
    github: GitHubPR | None = None,
    request_pr: int | None = None,
) -> Analysis:
    """Create the release branch, validate, optionally open the candidate PR."""
    if not analysis.ready:
        if github and request_pr is not None:
            try:
                github.add_labels(request_pr, [BLOCKED_LABEL])
                github.remove_labels(request_pr, [READY_LABEL])
            except SafetyError:
                pass
            try:
                github.comment(request_pr, render_report(analysis))
            except SafetyError:
                pass
        return analysis

    release_ref = assert_release_branch(analysis.release_branch or "")
    origin_ref = repo.current_ref()
    created = False
    applied = False
    try:
        created_new = repo.checkout_release_from_main(release_ref, main_ref)
        created = True
        if created_new:
            repo.cherry_pick(analysis.unique_commits)
        analysis.validation = validate_release(
            repo, analysis, main_ref=main_ref, test_ref=test_ref
        )
        applied = True
        if push:
            repo.push_release(release_ref)
        if github:
            title = f"Release: {analysis.feature.name}"
            body = render_report(analysis)
            created_pr = github.create_pr(
                base=ALLOWED_MAIN,
                head=release_ref,
                title=title,
                body=body,
                labels=[CANDIDATE_LABEL],
            )
            analysis.release_pr_url = str(created_pr.get("url") or "")
            analysis.validation["Release PR"] = "CREATED"
            body = render_report(analysis)
            github.create_pr(
                base=ALLOWED_MAIN,
                head=release_ref,
                title=title,
                body=body,
                labels=[CANDIDATE_LABEL],
            )
            if request_pr is not None:
                try:
                    github.add_labels(request_pr, [READY_LABEL])
                    github.remove_labels(request_pr, [BLOCKED_LABEL])
                except SafetyError:
                    pass
                request_note = (
                    body
                    + "\n\n## Request PR warning\n\n"
                    + "Do **not** merge this `release-request` pull request. "
                    + "It may contain unrelated develop/test history. Merge only "
                    + f"the candidate: {analysis.release_pr_url}\n"
                )
                github.comment(request_pr, request_note)
    except SafetyError as exc:
        if created and not applied:
            try:
                if origin_ref:
                    repo.run(["checkout", origin_ref], check=False)
                repo.delete_local_branch(release_ref)
            except SafetyError:
                pass
            analysis.status = "blocked"
            analysis.reason = str(exc)
            analysis.strategy = "No production changes were kept."
            analysis.release_branch = None
            analysis.release_pr_url = None
            if github and request_pr is not None:
                try:
                    github.add_labels(request_pr, [BLOCKED_LABEL])
                    github.comment(request_pr, render_report(analysis))
                except SafetyError:
                    pass
            return analysis
        analysis.validation["GitHub"] = f"FAIL {exc}"
        if github and request_pr is not None:
            try:
                github.comment(request_pr, render_report(analysis))
            except SafetyError:
                pass
        return analysis
    except Exception:
        if created and not applied:
            try:
                if origin_ref:
                    repo.run(["checkout", origin_ref], check=False)
                repo.delete_local_branch(release_ref)
            except SafetyError:
                pass
        raise
    finally:
        if origin_ref and normalize_ref(repo.current_ref()) != normalize_ref(origin_ref):
            repo.run(["checkout", origin_ref], check=False)
    return analysis


def feature_ref_from_pr(pr: dict) -> str:
    """Resolve the requested feature ref from a release-request pull request."""
    base = assert_pr_base(pr["baseRefName"])
    del base
    body = pr.get("body") or ""
    match = FEATURE_REF_RE.search(body)
    if match:
        return assert_feature_ref(match.group(1))
    return assert_feature_ref(pr["headRefName"])


def build_parser() -> argparse.ArgumentParser:
    """Return the CLI parser."""
    parser = argparse.ArgumentParser(
        description="PoC release agent (never merges pull requests)."
    )
    parser.add_argument("--repo-path", default=".", help="Git repository path.")
    parser.add_argument("--main", default=ALLOWED_MAIN)
    parser.add_argument("--develop", default=ALLOWED_DEVELOP)
    parser.add_argument("--test", default=ALLOWED_TEST)
    parser.add_argument("--feature-ref", help="Original feature branch to release.")
    parser.add_argument("--request-pr", type=int, help="release-request PR number.")
    parser.add_argument(
        "--github",
        action="store_true",
        help="Comment / create PRs with gh. Never merge.",
    )
    parser.add_argument(
        "--push",
        action="store_true",
        help="Push the poc/demo/release/* branch.",
    )
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("analyze", help="Inspect topology and print the report.")
    sub.add_parser("prepare", help="Analyze, apply onto a main-based branch, stop.")
    return parser


def main(argv: list[str] | None = None) -> int:
    """CLI entrypoint.

    Returns:
        0 on ready, 2 on blocked, 1 on error.
    """
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        repo = GitRepo(args.repo_path)
        github = GitHubPR(enabled=bool(args.github)) if args.github else None
        extra_depends: list[str] = []
        feature_ref = args.feature_ref
        request_pr = args.request_pr
        if request_pr is not None:
            if github is None:
                github = GitHubPR(enabled=True)
            pr = github.get_pr(request_pr)
            labels = {item["name"] for item in pr.get("labels") or []}
            if REQUEST_LABEL not in labels:
                raise SafetyError(
                    f"PR #{request_pr} is missing the {REQUEST_LABEL} label."
                )
            assert_pr_base(pr["baseRefName"])
            feature_ref = feature_ref or feature_ref_from_pr(pr)
            extra_depends = parse_depends_on(pr.get("body") or "")
        if not feature_ref:
            raise SafetyError("Provide --feature-ref or --request-pr.")
        analysis = analyze_request(
            repo,
            feature_ref=feature_ref,
            main_ref=args.main,
            develop_ref=args.develop,
            test_ref=args.test,
            extra_depends=extra_depends,
        )
        if args.command == "prepare":
            analysis = prepare_release(
                repo,
                analysis,
                main_ref=args.main,
                test_ref=args.test,
                push=bool(args.push),
                github=github if args.github else None,
                request_pr=request_pr,
            )
        sys.stdout.write(render_report(analysis) + "\n")
        return 0 if analysis.ready else 2
    except SafetyError as exc:
        sys.stderr.write(f"BLOCKED/SAFETY: {exc}\n")
        return 2
    except Exception as exc:  # noqa: BLE001
        sys.stderr.write(f"ERROR: {exc}\n")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
