# StackGuardian CLI docker action

This action interacts with [sg-cli](https://github.com/StackGuardian/sg-cli/blob/main/README.md).

## Inputs

## `operation`

**Required** The sg-cli operation like `"workflow create ..."` or `"stack create ..."`.

## Environment variables

## `SG_API_TOKEN`

**Required** StackGuardian API Token. Retrieve on the platform at `https://app.stackguardian.io/orchestrator/orgs/<YOUR_SG_ORG>/settings?tab=api_key/`.

## Example usage

uses: stackguardian/workflow-run-action
with:
  operation: 'workflow create --bulk --org demo-org --workflow-group demo-grp  -- payload.json'
