import * as core from '@actions/core'
import * as service from './service'

export async function run() {
  // TODO: Get from action inputs. apiKey will be a secret.
  let apiKey: string = core.getInput('apiKey', { required: true });
  let orgId: string = core.getInput('orgId', { required: true });
  let workflowGroupId: string = core.getInput('workflowGroupId', { required: true });
  let workflowId: string = core.getInput('workflowId', { required: true });

  let endpoint: string = `https://testapi.qa.stackguardian.io/api/v1/orgs/${orgId}/wfgrps/${workflowGroupId}/wfs/${workflowId}/wfruns/`

  const context = ''

  let auth: service.Authorization = {api_key: apiKey}

  var sgService = new service.WebService(endpoint, context, auth)

  await sgService.WorkflowRun();
  core.setOutput('msg', 'Workflow Scheduled');
  
}

console.log('Triggering Workflow Run')

run()
