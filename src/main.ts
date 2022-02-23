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

  core.info(`apiKey: ', ${apiKey}, ', orgId:', ${orgId}, ' workflowGroupId:', ${workflowGroupId}, ' workflowId:', ${workflowId}`)

  core.info('Triggering Workflow Run')

  const {response, error} = await sgService.WorkflowRun();
  core.setOutput('msg', 'Workflow Scheduled')
  core.info(`response: ${response}`)
  core.info(`error: ${error}`)

  core.info('Workflow Run Triggered')
  
}

run()
