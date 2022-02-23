import * as core from '@actions/core'
import * as service from './service'

// TODO: Get from action inputs. apiKey will be a secret.
let apiKey: string = 'sgu_Efza8ZO5NLe8kT1PfU7KK'
let orgId: string = 'policies'
let workflowGroupId: string = 'testing'
let workflowId: string = 's3'

let endpoint: string = `https://testapi.qa.stackguardian.io/api/v1/orgs/${orgId}/wfgrps/${workflowGroupId}/wfs/${workflowId}/wfruns/`

const context = ''

let auth: service.Authorization = {api_key: apiKey}

var sgService = new service.WebService(endpoint, context, auth)

export async function run() {
  await sgService.WorkflowRun();
}

run()
