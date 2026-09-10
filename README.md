# PoC: AI-assisted release workflow

Personal GitHub sandbox for testing independent feature promotion.

This repository is not ACI_SRV. It contains only the PoC agent, skill, and
GitHub Action. Demo branches are namespaced under `poc/demo/*`.

## What it proves

```text
test = Feature A + Feature B
production release = Feature B
```

The agent never merges `test` into main, never reverts A from test, and
never merges the pull request. A human reviews the candidate PR.

## Local test

```bash
python scripts/poc_release_agent/selftest.py
```

## GitHub test

1. Create a PR from `poc/demo/feature-b` to `poc/demo/main`.
2. Add the label `release-request`.
3. The Action prepares `poc/demo/release/feature-b` from `poc/demo/main`.
4. It opens a second PR: `poc/demo/release/feature-b` -> `poc/demo/main`.
5. Do not merge the request PR. Review the candidate PR. The agent stops there.

Repo setting required once:

Settings -> Actions -> General -> Workflow permissions -> enable
"Allow GitHub Actions to create and approve pull requests".

GitHub bundles create+approve in that checkbox. The agent still never
approves or merges.

See `docs/poc-ai-release-workflow.md` and `.github/skills/release-agent/SKILL.md`.
