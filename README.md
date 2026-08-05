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

## Running without an account

Omit the credentials and the action evaluates policy files from your repository instead, on the
runner, talking to nothing. Everything you see on the pull request is the same — the same sticky
comment, the same `Tirith Policy` check run, the same outputs and exit codes:

```yaml
steps:
  - run: terraform show -json tfplan > plan.json
  - uses: StackGuardian/sg-cli-gh-action@v2
```

with a policy committed at `.tirith/policies/no-public-ingress.tirith.json`. `policy-path` also
takes a single file or a glob.

Mode is chosen by whether credentials are present — there is no switch — and the log says which one
ran. **No credentials and no policies is a hard failure, not a skip**: a check that gated nothing
must not report green.

| | with credentials | without |
|---|---|---|
| Where policies come from | StackGuardian, by `EnforcedOn` scope | files in your repository |
| Where evaluation happens | a StackGuardian workflow run | the runner |
| Run history, dashboard, `wfrun-url` | yes | no |
| Org-wide enforcement, drift, approvals | yes | no |
| Comment, check run, exit codes | identical | identical |

A local run is still masked before anything is rendered. Nothing is uploaded, but evaluator messages
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
4. **Reports** the verdict: a sticky pull-request comment, a `Tirith Policy` check run, the job
   summary, and action outputs.

With credentials, policies live in StackGuardian and are selected server-side by their `EnforcedOn`
scope: there are no policy files in your repository and nothing is evaluated on the runner. Without
them, steps 2 and 3 are replaced by a local evaluation — see
[Running without an account](#running-without-an-account).

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
| `sg-api-key` | | `$SG_API_TOKEN` | Organization (`sgo_`) token. Omit for local mode |
| `sg-org` | | `$SG_ORG` | Organization name. Omit for local mode |
| `policy-path` | | `.tirith/policies` | Local mode only: a file, directory or glob of policy files |
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
`mode` (`platform` \| `local`), `passed`, `failed`, `warned`, `results`, `results-file`, `wfrun-id`,
`wfrun-url`, `comment-id`.

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

Reporting is separate from the verdict. If `github-token` is empty the comment and the check run are
skipped with a log line and the exit code is unchanged — the same thing happens on a fork pull
request, where the token is read-only. Set `comment: false` / `check: false` to turn either off
deliberately.

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
