# StackGuardian CLI docker action

This action interacts with [sg-cli](https://github.com/StackGuardian/sg-cli/blob/main/README.md).

## Inputs

## `operation`

**Required** sg-cli operation like `"workflow create ..."` or `"stack create ..."`.

## Example usage

uses: stackguardian/workflow-run-action
with:
  operation: 'workflow create --bulk --org demo-org --workflow-group demo-grp  -- payload.json'
