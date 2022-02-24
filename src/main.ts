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

  if (!apiKey || !orgId || !workflowGroupId || !workflowId) {
    // setDailed sets the actions status to failed with status code 1
    core.setFailed('Required Input not provided correctly')
  }

  const endpoint = `https://testapi.qa.stackguardian.io/api/v1/orgs/${orgId}/wfgrps/${workflowGroupId}/wfs/${workflowId}/wfruns/`

  const context = ''

  const auth: service.Authorization = {api_key: apiKey}

  var sgService = new service.WebService(endpoint, context, auth)

  core.info(
    `endpoint: ${endpoint}, apiKey: ${apiKey}, orgId: ${orgId}, workflowGroupId: ${workflowGroupId}, workflowId: ${workflowId}`
  )

  core.info('Triggering Workflow Run')

  const {data, msg, error} = await sgService.WorkflowRun()
  if (error) {
    core.setFailed(`Action failed with error ${error}`)
  }

  // core.info('Workflow Scheduled')

  // Prining res msg
  core.info(`data: ${data}`)

  //Testing Debug
  core.debug(`data: ${JSON.stringify(data)}`)

  core.info('Finished')
}

run()
