# PoC: AI-assisted release workflow (PR only)

This is a demonstration of independent feature promotion on GitHub.

It lives only on `poc/ai-assisted-release-workflow` plus namespaced
`poc/demo/*` branches. It does not change `deploy-aci-srv.yml`, real `main`,
real `develop`, real `test`, or branch protection.

## Problem

QA can have A and B on `test` while only B is approved for production.
Merging `test` into `main` would ship A. Reverting A from `test` would
break the integration environment.

## Convention

Two pull requests:

1. **Request PR** (do not merge): `poc/demo/feature-b` -> `poc/demo/main`
   with label `release-request`.
2. **Candidate PR** (human merges): `poc/demo/release/feature-b` ->
   `poc/demo/main`, created by the agent from `poc/demo/main` plus B only.

Optional request body line:

```text
Feature-Ref: poc/demo/feature-b
```

## Local proof (no GitHub required)

From this branch:

```bash
python scripts/poc_release_agent/selftest.py
```

That builds a temporary git repo where `test = A + B`, releases B onto a
main-based branch, and asserts A is still in test and absent from the
release. A second topology where B depends on A stays blocked.

Analyze a temp demo by hand:

```bash
python scripts/poc_release_agent/agent.py --repo-path <demo-repo> --feature-ref poc/demo/feature-b analyze
python scripts/poc_release_agent/agent.py --repo-path <demo-repo> --feature-ref poc/demo/feature-b prepare
```

## GitHub demonstration

Requires `gh` authenticated to `ACI-Code/ACI_SRV` and permission to push
`poc/*` branches. Do not push to `main`.

```bash
# 1. Push the implementation branch
git push -u origin poc/ai-assisted-release-workflow

# 2. Create namespaced demo branches from this HEAD (A and B only on demo refs)
python scripts/poc_release_agent/seed_demo.py --apply-demo-branches

# 3. Push demo refs only
git push origin poc/demo/main poc/demo/develop poc/demo/test poc/demo/feature-a poc/demo/feature-b

# 4. Open the request PR (this is the UI). Do not merge it.
gh pr create --base poc/demo/main --head poc/demo/feature-b --title "Release request: Feature B" --body "Feature-Ref: poc/demo/feature-b" --label release-request

# 5. The Action runs on the labeled PR targeting poc/demo/main.
#    If the workflow is not yet on poc/demo/main, run it manually:
gh workflow run "PoC Release Agent" --ref poc/ai-assisted-release-workflow -f request_pr=<PR_NUMBER>
```

Expected agent result:

- Detects test contains A + B
- Does not use test as the release source
- Creates `poc/demo/release/feature-b` from `poc/demo/main`
- Applies Feature B only
- Opens `poc/demo/release/feature-b` -> `poc/demo/main`
- Stops. A human reviews and merges the candidate PR.

## Demo script for the team

1. Feature A exists on `poc/demo/feature-a` and is in test.
2. Feature B exists on `poc/demo/feature-b` and is in test.
3. Show `git log --oneline poc/demo/main..poc/demo/test` contains A and B.
4. Open the `release-request` PR for B.
5. Agent comments the dashboard on that PR.
6. Agent creates the candidate PR whose diff is B only.
7. Confirm the candidate diff has `poc/demo/features/B.txt` and not `A.txt`.
8. Confirm `poc/demo/test` still contains A.
9. Human merges the candidate PR. The agent never merges.

## Security and permissions

Workflow permissions:

- `contents: write` (push `poc/demo/release/*` only; the agent refuses other refs)
- `pull-requests: write` (create/comment/label PRs)

The workflow does not grant administration, issues create, or a merge
exception. `GITHUB_TOKEN` technically can merge if branch protection is
off; the agent and workflow never call merge, never approve, and never
force-push.

The job runs on `ubuntu-latest`. Untrusted PR code is not executed: scripts
are checked out from the trusted base/`workflow_dispatch` ref. Fork PRs are
ignored.

GitHub default: Actions may not create pull requests. Enable
Settings -> Actions -> General -> Workflow permissions ->
"Allow GitHub Actions to create and approve pull requests".
The agent still never calls approve or merge.

## How merge is prevented

- `GitHubPR.merge_pr` always raises
- `guard_gh_args` rejects `gh pr merge` and `gh pr review`
- `guard_git_args` rejects `git merge`, force push, and production refs
- The workflow has no merge step
- The agent exits after creating the candidate PR

## Existing production protection (unchanged)

`.github/workflows/protect-main-promotion.yml` currently requires real PRs
into `main` to come from `test`, and requires the merge result to match the
`test` tree. That is the workflow this PoC is demonstrating an alternative
to.

This PoC does **not** modify that file. Demo PRs target `poc/demo/main`, so
the production protection job does not run. A later production rollout would
need an explicit decision to relax or replace that rule.

## Limitations

- PoC refs are `poc/demo/*` only. Real `main` / `develop` / `test` are refused.
- Original feature branches must still exist so unique commits can be isolated.
- Squash-merged features without the original branch cannot be reconstructed
  confidently; the agent blocks.
- Semantic dependencies without shared files or `Depends-On` need tests to catch.
- This does not replace the production deploy workflow.
- `.github/workflows/protect-main-promotion.yml` still requires real `main`
  PRs to come from `test`. The PoC does not change that. Demo PRs use
  `poc/demo/main`.
- No Jira key was attached; this branch must not be squash-merged to `develop`
  or `main` as product work.
