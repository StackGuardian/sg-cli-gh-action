# Roadmap

Scope is this action. Items that depend on other repositories say so.

## Blocking the v2 release

Everything here is a loose end from the initial build, not new work.

- **Tag `py-tirith` and pin to it.** `tirith-version` defaults to a *branch*
  (`feat/gate-capable-engine`), so a green pipeline can turn red with nothing in the repository
  changing. Needs the `1.2.0` tag on StackGuardian/tirith, then set the default back to a version.
- **Revert the temporary refs** in the dependency chain: `api/platform_api/Pipfile.qa` → `ref = "main"`
  (after StackGuardian/core#1235 merges) and `workflow-step-templates/terraform/Pipfile` → the tirith
  tag.
- **Bump the `WORKFLOW_STEP` revision** so the dashboard's schema offers `policy-only` and
  `policyInputKind`. The API path works without it; only the UI is affected.
- **Cut `v2`, keep `@v1.0.0-beta` alive.** v1 was an unrelated `sg-cli` passthrough. Do not move
  `@main`; say so in the release notes.
- **Marketplace listing.** `branding` is already set.

## Next

- **`plan-file` input.** Take the binary plan (`terraform plan -out=tfplan`) and run
  `show -json` inside the CLI, so no unmasked `plan.json` is ever written to disk. Resolve
  `terraform-bin`/`tofu-bin` before `terraform`/`tofu` — calling the wrapper that
  `hashicorp/setup-terraform` installs would append the whole plan to `$GITHUB_OUTPUT`. Lands in
  the CLI, so non-GitHub callers get it too.
- **Verify the archive install.** `pip install` from a git ref has no integrity check.
  `opentofu/setup-opentofu` verifies a published SHA-256 by default; match that posture once tirith
  is on PyPI.
- **Terragrunt example** (`examples/terragrunt-matrix.yml`). Zero code: matrix over units with a
  distinct `workflow-id` and `comment-tag` each. See `docs/terragrunt.md`.
- **Fail loudly on a mis-scoped policy.** `EnforcedOn` is per-workflow and the workflow identity is
  derived from the *workflow filename*, so a mismatch silently evaluates nothing. `no-policies`
  already reports it; consider an opt-in `require-policies: true` that fails instead.

## Later

- **`comment/` sub-action** for one aggregated comment across many units. `results-file` already
  exists as its input, which was the prerequisite. Worth it at ~20 units, not at 3.
- **Cost policies without a pre-generated breakdown.** The step generates one lazily when a cost
  policy is enforced; confirm that path on a plan with real priced resources.
- **Private-runner storage.** The upload key layout is runner-aware (a runner's own S3 bucket or
  Azure container). Only the shared bucket is exercised today.

## Not planned

- **Approvals.** A policy with `onFail: APPROVAL_REQUIRED` is reported, maps to an
  `action_required` check, and blocks the merge — but the action implements no approve/reject flow.
  Use environment protection rules or the platform's own approval. Deliberate: the step never exits
  11, because `APPROVAL_REQUIRED` is a non-terminal run status and would wedge the workflow for
  every later run.
- **Inline annotations.** Terraform plan JSON carries no file or line information, so there is
  nothing to anchor them to. Fabricating `file:line` would be worse than the summary table.
- **Comment-driven commands** (`/tirith recheck`). Out of scope; users keep their existing pipelines.

## Known upstream issues that affect users here

Neither is caused by this action; both change what a user can see.

- **`TfStateCleaned` and `TfPlan` are unreachable.** The step writes them and the run controller
  forwards them to the report-aggregator, but `wfrunfacts` returns "does not exist" and the facts
  file is excluded from artifact sync. Only `PolicyEvalResults` survives, via its own artifact.
- **`clean_tf_state` masking is a no-op** on the terraform step's own plan/apply path: it reads
  top-level `outputs`/`resources` from `terraform show -json`, which has neither, and overwrites
  `resources` with `[]`. Confirmed against real terraform. Unrelated to `policy-only`, which masks
  client-side.
