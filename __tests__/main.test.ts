import * as service from '../src/service';
import axios from 'axios';


// mock axios
jest.mock('axios');
const mockedAxios = axios as jest.Mocked<typeof axios>;

// const systemResponse_SUCCESS = {"systems": [{"id": 10, "name": "fake-system"}]};
// const environmentResponse_SUCCESS = {"environments": [ {"id": 29, "name": "fake-environment", "systemId": 10 }]}
// const instanceResponse_SUCCESS = {"instances": [{"id": 131, "name": "fake-instance"}]};
// const provision_SUCCESS = {
//   "environmentId": 29,
//   "instanceId": 131,
//   "abortOnFailure": true,
//   "steps": [
//     {
//       "percent": 0,
//       "stepId": 0,
//       "description": "string",
//       "name": "string",
//       "result": "SUCCESS"
//     }
//   ],
//   "status": "SUCCESS",
//   "eventId": 0
// }
// const getProvision_SUCCESS = {"eventId": 1, "environmentId": 29, "instanceId": 131, "abortOnFailure": true, "status": "success", "steps": []};


// sanity check
test("Sanity check", () => {
  expect(true).toBe(true);
});

describe("Test a single deployment", () => {
  // variables
  let apiKey: string = 'sgu_Efza8ZO5NLe8kT1PfU7KK';
  let endpoint: string = 'https://testapi.qa.stackguardian.io/api/v1/orgs/policies/wfgrps/testing/wfs/s3/wfruns/';
  const context = '';
  let auth: service.Authorization = {api_key: apiKey};

  // defined the service
  var sgService = new service.WebService(endpoint, context, auth);
  it("Successful API call", async () => {
    const mockResponse = {
      data: {
        msg: "Workflow Run scheduled"
      }
    };

    mockedAxios.post.mockResolvedValue(mockResponse);
    
    const {response, error} = await sgService.WorkflowRun();
    console.log("response: ", response)
    // expect(axios.post).toHaveBeenCalledWith(endpoint);
    expect(response.msg).toEqual(mockResponse.data.msg);
    expect(error).toEqual("");
  })
})

