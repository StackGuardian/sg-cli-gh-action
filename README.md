# Tirith IaC Governance

Evaluate Tirith policies against a terraform plan in CI, and report the outcome as a
pull-request comment and a check run.

> **`@v2` is not cut yet.** Until it is, pin the branch:
> `uses: StackGuardian/tirith-iac-governance-action@feat/tirith-policy-check`.
> Do **not** use `@main` or `@v1` — both still serve the unrelated sg-cli passthrough action,
> and `@main` fails by quietly running the wrong thing rather than by erroring.

```yaml
permissions:
  contents: read
  pull-requests: write   # sticky comment
  checks: write          # check run

env:
  SG_API_TOKEN: ${{ secrets.SG_API_TOKEN }}
  SG_ORG: ${{ vars.SG_ORG }}

steps:
  - run: |
      terraform plan -out=tfplan -input=false
      terraform show -json tfplan > plan.json

  - uses: StackGuardian/tirith-iac-governance-action@v2
```

That is the whole integration. With `plan.json` in the working directory the action needs no
`with:` block at all — it finds the document by convention, derives the workflow identity from the
repository and workflow filename, and defaults to the `eu` region.

Everything below is for when you want something other than the defaults:

```yaml
  - uses: StackGuardian/tirith-iac-governance-action@v2
    with:
      sg-region: us            # eu (default) or us
      input-path: out/plan.json
      fail-on-error: true      # fail the job when a policy fails
```

### Credentials

`SG_API_TOKEN` and `SG_ORG` may be supplied either as environment variables, as above, or as the
`sg-api-key` and `sg-org` inputs. The environment route exists because GitHub does not expose
`secrets` or `vars` to an action automatically, so it is the only way to keep the `with:` block
empty. The key should be an **organization** (`sgo_`) token: `sgu_` tokens are non-functional for
SSO-group-only users. A `sgu_` key is warned about rather than rejected, so the symptom is a 403
later rather than a clear failure up front.

## Running without a StackGuardian org

Omit the credentials and the action evaluates policy files from your repository instead, on the
runner, talking to nothing. Everything you see on the pull request is the same — the same sticky
comment, the same `Tirith IaC Governance` check run, the same outputs and exit codes:

```yaml
steps:
  - run: terraform show -json tfplan > plan.json
  - uses: StackGuardian/tirith-iac-governance-action@v2
```

with a policy committed at `.tirith/policies/no-public-ingress.tirith.json`. `policy-path` also
takes a single file or a glob.

Mode is chosen by whether credentials are present — there is no switch — and the log says which one
ran. **No credentials and no policies is a hard failure, not a skip**: a check that gated nothing
must not report green. So is **one credential without the other** — a set `sg-org` with an empty
`sg-api-key`, which is what a typo in `vars.SG_ORG` produces — because silently dropping to local
mode would evaluate the wrong policies and still report green.

| | with credentials | without |
|---|---|---|
| Where policies come from | StackGuardian, by `EnforcedOn` scope | files in your repository |
| Where evaluation happens | a StackGuardian workflow run | your GitHub runner |
| Run history, dashboard, `wfrun-url` | yes | no |
| Org-wide enforcement, drift checks, approvals | yes | no |
| Run history and audit trail | yes | no |
| Cost policies (`infracost-path`) | yes | ignored, with a warning |
| State published as the workflow's `tfstate.json` | yes | no |
| The code bundle other systems read | yes | nothing is uploaded |

A local run is still masked before anything is rendered — for `terraform_plan` and
`terraform_state`. `json` and `kubernetes` documents are passed through untouched in both modes,
because there is no schema to know which fields are secret. Nothing is uploaded, but evaluator messages
quote the values they compared and those messages go into the pull-request comment — so masking is
what keeps a `sensitive` value out of GitHub. It also means a local verdict matches the platform one
for the same plan, because the platform evaluates the masked document too.

Two limits worth knowing: one policy file is one rule, so `meta.id` and `meta.name` are what appear
in the comment; and a policy that cannot be evaluated — unparseable, or with unresolved variables —
fails the job regardless of `fail-on-error`, because "could not evaluate" is a tool failure rather
than a policy decision. Mark a policy advisory with `"enforcement": "soft_mandatory"` in its `meta`
to have a failure warn instead of block. Anything unrecognised there blocks.

## What it does

1. **Masks the plan on your runner**, before anything leaves it. Values terraform marked sensitive
   are replaced, root `variables` are dropped wholesale, and `prior_state` is removed entirely.
   `planned_values` is *rebuilt* from the already-masked `resource_changes` rather than passed
   through: terraform's own copy mirrors every value with no sensitivity markers at all, so shipping
   it would leak the secret masked a few lines earlier — but dropping it outright disarmed Infracost
   and Checkov, which read that section and nothing else.
2. **Packs** the masked documents together with your terraform source into a `tar.gz`, excluding
   `.git`, `.terraform`, `*.tfstate*` and anything in `.gitignore`.
3. **Uploads** it and creates a StackGuardian workflow run, which evaluates the policies your
   organization has scoped to that workflow.
4. **Reports** the verdict: a sticky pull-request comment, a `Tirith IaC Governance` check run, the job
   summary, and action outputs.

With credentials, policies live in StackGuardian and are selected server-side by their `EnforcedOn`
scope: there are no policy files in your repository and nothing is evaluated on the runner. Without
them, steps 2 and 3 are replaced by a local evaluation — see
[Running without a StackGuardian org](#running-without-a-stackguardian-org).

### Getting your policies to apply

`EnforcedOn` matches on the **workflow**, and this action derives a workflow identity of
`github-com-<owner>-<repo>-<workflow-filename>` — a workflow that almost certainly did not exist
when your policies were written. If the verdict comes back `no-policies`, that scope is why. Three
ways out, in increasing order of effort:

- scope the policy organization-wide (`*`);
- set `workflow-group` to a group your policies already cover — policies are scoped per group, and
  an unknown group is *created* rather than rejected, so a typo silently enforces nothing;
- add the derived workflow to the policy's `EnforcedOn`.

The identity is derived from the workflow **filename**, not its `name:`, so renaming a workflow
does not silently start a fresh workflow and de-scope every policy pointing at the old one.

## What actually gets uploaded

The masked documents — the plan, and the state if you pass one — **and your terraform source**. The
platform unpacks the source in place of a VCS checkout, so the code the findings refer to sits
alongside them; that is what makes automated fixes and run reproduction possible.

> ### ⚠️ Your committed source ships as written
>
> Masking applies to the plan and state **documents**, not to your repository. A secret hardcoded in
> a `.tf` file reaches StackGuardian in plaintext:
>
> ```hcl
> resource "local_sensitive_file" "creds" {
>   content = "hunter2"   # masked in the plan, and still verbatim in main.tf
> }
> ```
>
> Excluded automatically: `.git`, `.terraform`, `*.tfstate*`, and anything in `.gitignore`. If other
> files must not travel, add them to `.gitignore` or narrow `source-dir`.

`source-dir` defaults to the working directory. Point it at a subdirectory to send less:

```yaml
- uses: StackGuardian/tirith-iac-governance-action@v2
  with:
    source-dir: envs/prod
```

Or set it to an empty string to send **only** the masked documents:

```yaml
    source-dir: ""
```

That is the escape hatch if you cannot ship HCL to a third party. It is deliberately distinct from
leaving `source-dir` out, which gets you the default.

**If the source is too large**, the upload falls back to documents-only rather than failing the run —
the policy verdict is what gates your merge, and it should not be lost to a stray `vendor/` directory.
You get a warning annotation saying so, and the run records that its archive carries no code. The
limit is 100 MB compressed; scope `source-dir` or extend `.gitignore` rather than raising it.

## A note on masking

Terraform's `*_sensitive` markers are **not exhaustive**. A value that flows through `locals`, or
comes from a provider that did not mark its schema, arrives marked `false` and marker-driven
masking will not catch it. Dropping `planned_values`, `variables` and the literal values in
`configuration` limits the blast radius, but if a value must never leave your infrastructure, do
not let it into a plan — and do not commit it to the repository either.

Two related habits worth keeping:

- Write `terraform state pull > state.json`, never `> terraform.tfstate`. With a local backend the
  shell truncates the file terraform is about to read.
- Use `input-kind: terraform_state` for a state document. Plain `json` uploads it unmasked, and
  state holds every attribute in plaintext.

### What `state-path` publishes

The masked state is also written to the workflow's `tfstate.json` artifact, which is the name
StackGuardian already treats as a workflow's state document — so it shows up in the State and
artifacts views rather than only inside the run's archive. A post-apply check therefore updates both
the Resources view (via the `TfStateCleaned` run fact) and the state artifact.

**That copy is masked, so it cannot be used to run terraform.** It records what was evaluated, not a
restorable state file. And if the workflow manages its own terraform state, the upload is skipped
entirely with a warning: for such a workflow that object *is* the live state, and overwriting it would
be data loss. Policy evaluation is unaffected either way.

## Inputs

Every input is optional — the action runs with an empty `with:` block.

The version of `py-tirith` is **not** an input: it is fixed by the action and released with it, so a given action ref always runs one known CLI.

| Input | Default | |
|---|---|---|
| `sg-api-key` | `$SG_API_TOKEN` | Organization (`sgo_`) token. Omit for local mode |
| `sg-org` | `$SG_ORG` | Organization name. Omit for local mode |
| `policy-path` | `.tirith/policies` | Local mode only: a file, directory or glob of policy files |
| `sg-region` | `eu` | `eu` or `us`. Sets both URLs, so run links always match |
| `input-path` | `plan.json` / `tfplan.json` | Document to evaluate, found by convention. Required in local mode for `json` and `kubernetes`, which have no conventional filename |
| `plan-file` | | Binary plan, rendered with `show -json` in memory |
| `input-kind` | `terraform_plan` | `terraform_plan`, `terraform_state`, `kubernetes`, `json` |
| `state-path` | | Terraform state, masked before upload. Also published as the workflow's `tfstate.json` — see below |
| `infracost-path` | | `infracost breakdown --format json`. Platform mode only — ignored locally, with a warning |
| `source-dir` | `.` | Terraform source uploaded with the documents. Narrow it, or `""` to send documents only. See above |
| `fail-on-error` | `false` | Fail the job when a policy fails |
| `comment` / `check` | `true` | Post the comment / check run |
| `comment-tag` | `default` | Namespaces the comment, the archive **and the check-run name** — see [Matrix](#matrix-and-monorepo-usage) |
| `timeout` | `1800` | Seconds to wait for the run |
| `workflow-id` | derived | Overrides `github-com-<owner>-<repo>-<workflow-filename>` |
| `workflow-group` | `default` | Workflow group. Policies are scoped per group |
| `terraform-version` | | Recorded on the workflow at creation |
| `step-template-id` | platform default | Override the terraform step template |
| `terraform-bin` | auto | Binary for `plan-file`. Prefers the real one over the CI wrapper |
| `github-token` | `${{ github.token }}` | Used only to post the comment and check run. Never sent to StackGuardian. Set to `""` to skip reporting entirely |
| `sg-api-url` / `sg-dashboard-url` | | Deprecated. Self-hosted or dedicated hosts only |

## Outputs

`verdict` (`passed` \| `warned` \| `failed` \| `errored` \| `no-policies`),
`mode` (`platform` \| `local`), `passed`, `failed`, `warned`, `results`, `results-file`, `wfrun-id`,
`wfrun-url`, `comment-id`.

A policy that asks for approval (`onFail: APPROVAL_REQUIRED`, or `meta.enforcement:
approval_required` locally) reports as `warned` and does not block. The evaluation has already
finished by the time the intent is known, so there is nothing to approve; the comment still says how
many rules asked for one.

`wfrun-id` and `wfrun-url` are unset in local mode: no run was recorded, and a link to one that does
not exist would be worse than none.

## Exit codes

`fail-on-error` governs **policy verdicts**, not tool health.

| | `fail-on-error: false` | `fail-on-error: true` |
|---|---|---|
| Policies pass or warn | green | green |
| A policy fails | green | **red** |
| Run errored, platform unreachable, no verdict | **red** | **red** |
| Nothing to evaluate: no credentials and no policies | **red** | **red** |
| A local policy could not be evaluated | **red** | **red** |

The last three rows are deliberate: a run that produced no verdict must never look like a pass, and
neither must one that had nothing to check.

Reporting is separate from the verdict, and the exit code is unchanged either way. An empty
`github-token` skips the comment and the check run before attempting them, with a log line. A fork
pull request *attempts* them and fails with a `::warning::`, because the token GitHub gives a fork is
read-only — so if a comment is missing, which of the two you are looking at is visible in the log.
Set `comment: false` / `check: false` to turn either off deliberately.

## Matrix and monorepo usage

Give each leg its own `workflow-id` **and** `comment-tag`:

```yaml
strategy:
  fail-fast: false
  matrix:
    stack: [dev, prod]
steps:
  - uses: StackGuardian/tirith-iac-governance-action@v2
    with:
      sg-api-key: ${{ secrets.SG_API_TOKEN }}
      sg-org: ${{ vars.SG_ORG }}
      input-path: ${{ matrix.stack }}/plan.json
      source-dir: ${{ matrix.stack }}
      workflow-id: infra-${{ matrix.stack }}
      comment-tag: ${{ matrix.stack }}
```

Both are load-bearing. Runs on a single StackGuardian workflow serialize while one is pending, so
without a distinct `workflow-id` a 20-leg matrix becomes a 20-deep queue. And the sticky comment is
found by a marker containing the tag, so shared tags mean the legs overwrite each other's comment.

**`comment-tag` also renames the check run**, which matters if you gate on it. The name is
`Tirith IaC Governance` for the default tag and `Tirith IaC Governance (<tag>)` for any other, so the
matrix above produces `Tirith IaC Governance (dev)` and `Tirith IaC Governance (prod)`. A branch
protection rule requiring the unsuffixed name matches **neither**, and a required check that never
arrives leaves the pull request blocked while gating nothing. Either require one check per tag, or
leave `comment-tag` unset on the leg you gate on.

## Upgrading

> **The check run was renamed** to `Tirith IaC Governance` (it was `Tirith Policy`). If you made it a
> **required status check** in branch protection, update the rule — a rule still naming `Tirith Policy`
> waits for a check that no longer arrives, so those pull requests stay blocked and are never gated by
> the new one. Nothing else about the check changed — but note that a non-default `comment-tag`
> suffixes the name, so check what your runs actually produce before writing the rule.

## Migrating from the sg-cli action

The first version of this action was a thin `sg-cli` passthrough with a single `operation` input.
It has evolved into what this action does now. To keep the old behaviour pin the tag exactly —
`@v1.0.0-beta` — not `@v1`, which does not exist as a tag. There is no automatic migration.

## Where the code lives

The action is a wrapper of Tirith which is maintained in
[tirith](https://github.com/StackGuardian/tirith). You can also run this using the following from GitLab, a Makefile, local etc.:

```
tirith platform check --org acme --workflow-id infra --input-path plan.json --fail-on-error
```

What is left in this repository is only what is genuinely GitHub-specific: reading the event
payload, posting the comment and check run, and setting action outputs.

## Examples

Complete workflows, runnable as-is:

| | |
|---|---|
| [`examples/basic.yml`](examples/basic.yml) | The ordinary case: plan, evaluate, comment |
| [`examples/local-no-credentials.yml`](examples/local-no-credentials.yml) | Policies from the repository, nothing uploaded |
| [`examples/with-state.yml`](examples/with-state.yml) | A state document, and the two-phase plan-then-state pipeline |
| [`examples/monorepo-matrix.yml`](examples/monorepo-matrix.yml) | One leg per stack, with the `workflow-id` and `comment-tag` split above |

## Development

```
pip install pytest
pip install "py-tirith @ git+https://github.com/StackGuardian/tirith@feat/gate-capable-engine"
python -m pytest tests/ -q
```

CI additionally runs two fail-closed smoke jobs, which are the ones worth not breaking: a policy
violation must gate the pull request, and a run that produces no verdict must be red rather than
green. Both assert on the exit code, not on the log.
