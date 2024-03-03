# StackGuardian CLI docker action

This action interacts with [sg-cli](https://github.com/StackGuardian/sg-cli/blob/main/README.md).

## Inputs

## `operation`

**Required** The sg-cli operation like `"workflow create ..."` or `"stack create ..."`.

## Environment variables

## `SG_API_TOKEN`

**Required** StackGuardian API Token. Retrieve on the platform at `https://app.stackguardian.io/orchestrator/orgs/<YOUR_SG_ORG>/settings?tab=api_key/`.

## Example usage

```yaml
jobs:
  execute-sg-cli:
    runs-on: ubuntu-latest
    env:
      SG_API_TOKEN: ${{ secrets.SG_API_TOKEN }}
    steps:
      # Checks-out your repository under $GITHUB_WORKSPACE, so your job can access it
      - uses: actions/checkout@v2
      - uses: stackguardian/sg-cli-gh-action@main
        with:
          operation: 'workflow create --org demo-org --workflow-group gh-actions --run -- payload.json'
```

See [sg-cli docs](https://github.com/StackGuardian/sg-cli/blob/main/README.md) for the explanation on `payload.json`.
