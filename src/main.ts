import * as core from '@actions/core'
import * as service from './service'

export async function run() {
  // TODO: Get from action inputs. apiKey will be a secret.
  const apiKey: string = core.getInput('apiKey', {
    required: true
  })
  const orgId: string = core.getInput('orgId', {
    required: true
  })
  const workflowGroupId: string = core.getInput('workflowGroupId', {
    required: true
  })
  const workflowId: string = core.getInput('workflowId', {
    required: true
  })

  const endpoint = `https://testapi.qa.stackguardian.io/api/v1/orgs/${orgId}/wfgrps/${workflowGroupId}/wfs/${workflowId}/wfruns/`

  const context = ''

  const auth: service.Authorization = {api_key: apiKey}

  var sgService = new service.WebService(endpoint, context, auth)

  core.info(`endpoint: ${endpoint}, apiKey: ${apiKey}, orgId: ${orgId}, workflowGroupId: ${workflowGroupId}, workflowId: ${workflowId}`)

  core.info('Triggering Workflow Run')

  const {response, error} = await sgService.WorkflowRun()
  core.info('Workflow Scheduled')
  core.info(`response: ${response}`)
  core.info(`error: ${error}`)

  core.info('Finished')
}

run()
