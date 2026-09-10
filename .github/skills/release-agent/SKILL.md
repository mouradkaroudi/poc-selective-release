---
name: release-agent
description: >
  Prepare an independent production release from original feature commits
  using GitHub Pull Requests as the only UI. Use when a PR is labeled
  release-request, when promoting one feature onto main without merging
  test, or when analyzing whether Feature B can ship while Feature A stays
  in the test integration branch.
---

# Release Agent

PoC skill for an AI-assisted GitHub release workflow. GitHub Pull Requests,
PR comments, labels, checks, and GitHub Actions are the UI. Do not create
GitHub Issues. Do not build a web UI.

## Purpose

Promote one requested feature onto a branch created from production `main`
(in this PoC, `poc/demo/main`) even when `test` currently contains additional
unreleased features.

Prove this invariant:

```text
test = A + B
production release = B
```

without reverting A from `test` and without merging `test` into `main`.

## Trigger

A developer opens a pull request that is a **release request**, not the final
merge:

1. Head branch is the **original feature branch** (example: `poc/demo/feature-b`).
2. Base branch is the production stand-in: `poc/demo/main`.
3. Label: `release-request`.
4. Optional body line: `Feature-Ref: poc/demo/feature-b`.

The GitHub Action `.github/workflows/poc-release-agent.yml` runs on:

- `pull_request` to `poc/demo/main` when the label is present
- `workflow_dispatch` with the request PR number

Do not use `pull_request_target`. Do not use Issues.

The request PR may contain extra develop history. **Do not merge it.**

The agent creates a second PR:

```text
poc/demo/release/feature-b -> poc/demo/main
```

That candidate PR is the only PR a human should merge.

## Never merge

The agent MUST NEVER:

- merge a pull request
- approve a pull request
- merge `release/*` into `main`
- merge `test` into `main`
- bypass branch protection
- force push
- modify branch protection
- delete protected branches
- create GitHub Issues
- revert unreleased work from `test` to make another feature shippable

It may create the candidate PR, comment, and label. Then it MUST STOP.
Human review and merge remain the final step.

## Branch analysis procedure

1. Resolve refs: `poc/demo/main`, `poc/demo/develop`, `poc/demo/test`.
2. Refuse real `main` / `develop` / `test`. This PoC is namespaced.
3. Identify the requested feature from the request PR head or `Feature-Ref`.
4. Confirm the original feature branch still exists.
5. List sibling `poc/demo/feature-*` branches.
6. Compute this feature's **unique original commits**:
   - if the feature is not yet on develop: `develop..feature`
   - if it was branched after another feature, use that ancestor feature tip
     as the fork point so B does not include A
7. List features present on develop and on test.
8. If the feature cannot be identified confidently, stop and report.

## Release decision procedure

Ready only if all of the following hold:

- Unique original commits were identified.
- The request is not pointing at `test`.
- Declared `Depends-On` features are already on `poc/demo/main`.
- Unique files do not overlap unreleased sibling features still in test.
- Those commits cherry-pick onto `poc/demo/main` cleanly.
- Validation passes.

Otherwise BLOCKED. Create no release branch. Change nothing in production.

## Dependency detection

Treat B as blocked when:

- A commit or PR body contains `Depends-On: <ref>` and that ref is not on main
- B's unique patch does not apply onto main (cherry-pick conflict)
- B changes files also changed by an unreleased sibling currently in test

Ancestry alone is not a dependency. B may have been branched from develop
after A and still be independently releasable if its unique patch applies.

## Release branch creation

```text
main -> release/B -> requested feature -> PR -> main
```

Not:

```text
test -> main
```

Steps:

1. Create `poc/demo/release/<slug>` from `poc/demo/main` only.
2. Cherry-pick the original feature commits (`git cherry-pick -x`).
3. Do not cherry-pick from `test`.
4. Do not merge the feature branch if it contains sibling history.
5. Do not overwrite an existing release branch.

Run:

```bash
python scripts/poc_release_agent/agent.py --feature-ref poc/demo/feature-b prepare
```

## Validation

After apply, verify:

- HEAD is a descendant of `poc/demo/main`
- HEAD is not a descendant of `poc/demo/test`
- requested feature files are present
- excluded feature markers are absent
- record Tests and Build as PASS or FAIL

Do not run the production ACI_SRV deploy workflow. This is a PoC.

## PR creation

If ready:

1. Push only `poc/demo/release/<slug>`.
2. Create `poc/demo/release/<slug>` -> `poc/demo/main`.
3. Label the candidate `poc-release-candidate`.
4. Comment the full dashboard on the request PR.
5. Label the request `poc-release-ready`.
6. Tell the human not to merge the request PR.
7. STOP.

If blocked:

1. Comment the blocked dashboard.
2. Label `poc-release-blocked`.
3. STOP.

## PR dashboard

Ready:

```markdown
# Release: Feature B

## Status

🟢 Ready for human review

## Requested feature

Feature B

## Branch analysis

main
develop = A + B
test = A + B

## Release strategy

This release was created from `main`.

Feature B was applied independently.

Feature A is currently present in `test` but is NOT included
in this release.

## Included

- Feature B

## Excluded

- Feature A

## Dependencies

None

## Validation

- Tests: PASS
- Build: PASS

## Safety

This PR was prepared by the release agent.

The agent does not merge Pull Requests.

Human review and merge are required.
```

Blocked:

```markdown
# Release: Feature B

## Status

🔴 BLOCKED

## Reason

Feature B depends on Feature A.

Feature A is not approved for production.

Releasing B independently would produce an incomplete release.

No release branch was created.
No production changes were made.
```

## Failure handling

- Unknown feature: block and report.
- Missing original branch: block and report.
- Cherry-pick conflict: abort, delete the local release branch, block.
- Validation failure: delete the local release branch, block.
- Existing release branch: refuse to overwrite.
- Any attempt to merge, approve, force-push, or touch production refs: raise
  `SafetyError` and stop.

## Executable procedure

The reusable implementation is `scripts/poc_release_agent/`. The skill is the
contract. The script is the deterministic procedure. Prefer running the
script over re-implementing git analysis in prose.
