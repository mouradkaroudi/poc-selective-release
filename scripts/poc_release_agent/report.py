"""Render the pull-request dashboard markdown for a PoC release."""

from __future__ import annotations

from dataclasses import dataclass, field

READY_STATUS = "\U0001f7e2 Ready for human review"
BLOCKED_STATUS = "\U0001f534 BLOCKED"
COMMENT_MARKER = "<!-- poc-release-agent-report -->"


@dataclass
class FeatureInfo:
    """One named feature discovered in the git graph."""

    ref: str
    name: str
    commits: list[str] = field(default_factory=list)
    files: list[str] = field(default_factory=list)


@dataclass
class Analysis:
    """Result of inspecting whether a feature can be released independently."""

    status: str
    feature: FeatureInfo
    included: list[str]
    excluded: list[str]
    dependencies: list[str]
    reason: str
    main_ref: str
    develop_features: list[str]
    test_features: list[str]
    unique_commits: list[str]
    strategy: str
    validation: dict[str, str] = field(default_factory=dict)
    release_branch: str | None = None
    release_pr_url: str | None = None

    @property
    def ready(self) -> bool:
        """Return True when the feature can be prepared for human review."""
        return self.status == "ready"


def display_feature_name(ref: str) -> str:
    """Convert a feature ref into a short human-readable name."""
    slug = ref.rstrip("/").rsplit("/", 1)[-1]
    if slug.startswith("feature-"):
        slug = slug[len("feature-") :]
    elif slug.startswith("feature"):
        slug = slug[len("feature") :].lstrip("-/")
    if len(slug) == 1:
        return f"Feature {slug.upper()}"
    titled = slug.replace("-", " ").replace("_", " ").strip().title()
    if titled.lower().startswith("feature "):
        return titled
    return f"Feature {titled}" if titled else ref


def render_report(analysis: Analysis) -> str:
    """Return the PR dashboard markdown for *analysis*."""
    feature_name = analysis.feature.name
    if analysis.status == "blocked":
        deps = "\n".join(f"- {item}" for item in analysis.dependencies) or "None recorded"
        return f"""{COMMENT_MARKER}
# Release: {feature_name}

## Status

{BLOCKED_STATUS}

## Reason

{analysis.reason}

## Requested feature

{feature_name} (`{analysis.feature.ref}`)

## Branch analysis

- `{analysis.main_ref}` is the production stand-in
- develop contains: {", ".join(analysis.develop_features) or "(none detected)"}
- test contains: {", ".join(analysis.test_features) or "(none detected)"}

## Dependencies

{deps}

## Safety

No release branch was created.
No production changes were made.
The agent does not merge pull requests.
Human review is required. Do not merge the request PR.
"""

    included = "\n".join(f"- {item}" for item in analysis.included) or "- (none)"
    excluded = "\n".join(f"- {item}" for item in analysis.excluded) or "- (none)"
    deps = "\n".join(f"- {item}" for item in analysis.dependencies) or "None"
    validation_lines = "\n".join(
        f"- {key}: {value}" for key, value in analysis.validation.items()
    ) or "- (not run)"
    release_branch = analysis.release_branch or "(not created)"
    release_pr = analysis.release_pr_url or "(not created)"
    return f"""{COMMENT_MARKER}
# Release: {feature_name}

## Status

{READY_STATUS}

## Requested feature

{feature_name} (`{analysis.feature.ref}`)

## Branch analysis

- `{analysis.main_ref}` is the production stand-in
- develop contains: {", ".join(analysis.develop_features) or "(none detected)"}
- test contains: {", ".join(analysis.test_features) or "(none detected)"}

## Release strategy

This release was created from `{analysis.main_ref}`.

{analysis.strategy}

Unreleased work currently present in `test` is NOT included in this release.

## Release branch

`{release_branch}`

## Release PR

{release_pr}

## Included

{included}

## Excluded

{excluded}

## Dependencies

{deps}

## Validation

{validation_lines}

## Safety

This PR was prepared by the release agent.

The agent does not merge pull requests.

Human review and merge are required.

Do not merge `test` into `main`.
Do not merge the `release-request` PR if it still contains unrelated history.
Merge only this main-based release PR after review.
"""
