# workflow-run-action

![build-test](https://github.com/StackGuardian/workflow-run-action/actions/workflows/test.yml/badge.svg)
<!-- ![workflow-run-action Test](https://github.com/<OWNER>/<REPOSITORY>/actions/workflows/<WORKFLOW_FILE>/badge.svg) -->
<br><br>
[![tag badge](https://img.shields.io/github/v/tag/StackGuardian/workflow-run-action)](https://github.com/StackGuardian/workflow-run-action/tags)
[![license badge](https://img.shields.io/github/license/StackGuardian/workflow-run-action)](./LICENSE)
[![size badge](https://img.shields.io/github/size/StackGuardian/workflow-run-action/dist/index.js)](./dist)

`workflow-run-action` runs a executes a StackGuardian Workflow Run on your Github Action runner

# Run a workflow run

This action enables you to run a workflow on StackGuardian platform.

## Usage

Add the following entry to your Github workflow YAML file with the required inputs: 

```yaml
uses: stackguardian/workflow-run-action@v1
with:
  orgId: 'http://exampleUrl'
  workflowGroupId: 'username'
  workflowId: ${{ secrets.password }}
  apiKey: 'system'
```

### Required Inputs
The following inputs are required to use this action:

| Input | Description |
| --- | --- |
| `orgId` | Org Id |
| `workflowGroupId` | Workflow Group Id |
| `workflowId` | Workflow Id |
| `apiKey` | Specifies a Github encrypted secret for accessing the StackGuardian API. Refer to the [Encrypted Secrets Documentation](https://docs.github.com/en/actions/reference/encrypted-secrets) for details on how to create an encrypted secret. |

### Optional Inputs
None

## Build and Test this Action Locally

1. Install the dependencies:  
```bash
$ npm install
```

2. Build the typescript and package it for distribution:
```bash
$ npm run build && npm run package
```

3. Run the tests:
```bash
$ npm test

 PASS  ./index.test.js
