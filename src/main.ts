import * as core from '@actions/core'
import * as service from './service'

let api: string = 'sgu_Efza8ZO5NLe8kT1PfU7KK'

let endpoint: string =
  'https://testapi.qa.stackguardian.io/api/v1/orgs/policies/wfgrps/testing/wfs/demo-website-aws-s3/wfruns/'

const context = 'wfruns/'

let auth: service.Authorization = {api_key: api}

var sgService = new service.WebService(endpoint, context, auth)

export async function run() {
  console.log('in')
}

run()
