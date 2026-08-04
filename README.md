# Tirith Policy Check

Evaluate your StackGuardian policies against a terraform plan in CI, and report the outcome as a
pull-request comment and a check run.

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

  - uses: StackGuardian/sg-cli-gh-action@v2
```

That is the whole integration. With `plan.json` in the working directory the action needs no
`with:` block at all — it finds the document by convention, derives the workflow identity from the
repository and workflow filename, and defaults to the `eu` region.

Everything below is for when you want something other than the defaults:

```yaml
  - uses: StackGuardian/sg-cli-gh-action@v2
    with:
      sg-region: us            # eu (default) or us
      input-path: out/plan.json
      fail-on-error: true      # fail the job when a policy fails
```

### Credentials

`SG_API_TOKEN` and `SG_ORG` may be supplied either as environment variables, as above, or as the
`sg-api-key` and `sg-org` inputs. The environment route exists because GitHub does not expose
`secrets` or `vars` to an action automatically, so it is the only way to keep the `with:` block
empty. The key must be an **organization** (`sgo_`) token: `sgu_` tokens are non-functional for
SSO-group-only users.

## What it does

1. **Masks the plan on your runner**, before anything leaves it. Values terraform marked sensitive
   are replaced, root `variables` are dropped wholesale, and `planned_values` and `prior_state` are
   removed entirely.
2. **Packs** the masked documents together with your terraform source into a `tar.gz`, excluding
   `.git`, `.terraform`, `*.tfstate*` and anything in `.gitignore`.
3. **Uploads** it and creates a StackGuardian workflow run, which evaluates the policies your
   organization has scoped to that workflow.
4. **Reports** the verdict: a sticky pull-request comment, a `Tirith Policy` check run, the job
   summary, and action outputs.

Policies live in StackGuardian and are selected server-side by their `EnforcedOn` scope. There are
no policy files in your repository and nothing is evaluated on the runner.

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

By default, **only the masked documents** — the plan, and the state if you pass one. Your terraform
source stays on the runner.

Set `source-dir` to upload the source tree alongside them. The platform unpacks it in place of a
VCS checkout, which is what policies over HCL will eventually read. Be aware of what that means:
masking applies to the plan and state *documents*, not to your `.tf` files, so a secret hardcoded
in HCL reaches StackGuardian in plaintext.

```yaml
- uses: StackGuardian/sg-cli-gh-action@v2
  with:
    source-dir: .
```

```hcl
resource "local_sensitive_file" "creds" {
  content = "hunter2"   # masked in the plan, and still verbatim in main.tf
}
```

When `source-dir` is set, these are excluded automatically: `.git`, `.terraform`, `*.tfstate*`, and
anything in `.gitignore`. If you have other files that must not travel, add them to `.gitignore`,
or point `source-dir` at a directory that does not contain them.

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

## Inputs

| Input | Required | Default | |
|---|---|---|---|
| `sg-api-key` | | `$SG_API_TOKEN` | Organization (`sgo_`) token |
| `sg-org` | | `$SG_ORG` | Organization name |
| `sg-region` | | `eu` | `eu` or `us`. Sets both URLs, so run links always match |
| `input-path` | | `plan.json` / `tfplan.json` | Document to evaluate, found by convention |
| `plan-file` | | | Binary plan, rendered with `show -json` in memory |
| `input-kind` | | `terraform_plan` | `terraform_plan`, `terraform_state`, `kubernetes`, `json` |
| `state-path` | | | Terraform state, masked before upload |
| `infracost-path` | | | `infracost breakdown --format json` |
| `source-dir` | | *(none)* | Upload the terraform source too. See above |
| `fail-on-error` | | `false` | Fail the job when a policy fails |
| `comment` / `check` | | `true` | Post the comment / check run |
| `comment-tag` | | `default` | Namespaces the comment and the archive |
| `timeout` | | `1800` | Seconds to wait for the run |
| `workflow-id` | | derived | Overrides `github-com-<owner>-<repo>-<workflow-filename>` |
| `workflow-group` | | `default` | Workflow group. Policies are scoped per group |
| `terraform-version` | | | Recorded on the workflow at creation |
| `step-template-id` | | platform default | Override the terraform step template |
| `tirith-version` | | `1.2.0` | Pin the CLI version |
| `terraform-bin` | | auto | Binary for `plan-file`. Prefers the real one over the CI wrapper |
| `sg-api-url` / `sg-dashboard-url` | | | Deprecated. Self-hosted or dedicated hosts only |

## Outputs

`verdict` (`passed` \| `warned` \| `failed` \| `errored` \| `no-policies` \| `approval-required`),
`passed`, `failed`, `warned`, `results`, `results-file`, `wfrun-id`, `wfrun-url`, `comment-id`.

## Exit codes

`fail-on-error` governs **policy verdicts**, not tool health.

| | `fail-on-error: false` | `fail-on-error: true` |
|---|---|---|
| Policies pass or warn | green | green |
| A policy fails | green | **red** |
| Run errored, platform unreachable, no verdict | **red** | **red** |

The last row is deliberate: a run that never produced a verdict must never look like a pass.

## Matrix and monorepo usage

Give each leg its own `workflow-id` **and** `comment-tag`:

```yaml
strategy:
  fail-fast: false
  matrix:
    stack: [dev, prod]
steps:
  - uses: StackGuardian/sg-cli-gh-action@v2
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

## Migrating from the sg-cli action

Version 1 of this action was a thin `sg-cli` passthrough with a single `operation` input. It is
unrelated to what this action does now. Pin `@v1.0.0-beta` to keep the old behaviour; there is no
automatic migration.

## Where the code lives

The action is a wrapper. Everything that talks to StackGuardian is `tirith platform check` in
[StackGuardian/tirith](https://github.com/StackGuardian/tirith) — so the same behaviour is
available from GitLab, a Makefile or a laptop:

```
tirith platform check --org acme --workflow-id infra --input-path plan.json --fail-on-error
```

What is left in this repository is only what is genuinely GitHub-specific: reading the event
payload, posting the comment and check run, and setting action outputs.
