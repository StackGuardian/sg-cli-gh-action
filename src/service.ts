import * as core from '@actions/core'
import http = require('http')
import https = require('https')
import q = require('q')
import url = require('url')
import axios from 'axios'

// TODO: Use SG CoreServices for Workflow Runs

export interface Authorization {
  api_key: string
}

export class WebService {
  private baseURL: url.Url
  protected protocol: typeof https | typeof http
  protected protocolLabel: string
  protected authorization: Authorization

  constructor(
    endpoint: string,
    context: string,
    authorization?: Authorization
  ) {
    this.baseURL = url.parse(endpoint)
    if (this.baseURL.path === '/') {
      this.baseURL.path += context
    } else if (this.baseURL.path === `/${context}/`) {
      this.baseURL.path = `/${context}`
    }
    this.authorization = authorization
    this.protocol = this.baseURL.protocol === 'https:' ? https : http
    this.protocolLabel = this.baseURL.protocol || 'http:'
  }

  WorkflowRun = async () => {
    const config = {
      headers: {
        'Content-Type': 'application/json'
      }
    }
    axios.defaults.headers.common = {
      //   Authorization:
      //     'Bearer eyJraWQiOiI2YmNLXC9uRXpcL3Y2ZWFtbk55MDMra2E5VHlUNnlsYlwveEJYMUUrZUxtRGc0PSIsImFsZyI6IlJTMjU2In0.eyJhdF9oYXNoIjoiYmtodjNEeGoyM1J4NDBVWEh2NU5rQSIsInN1YiI6IjUzNTk4YzEzLWY3MDctNDUzNi05ZTUzLWJjZjJkMGFmZjg0OSIsImVtYWlsX3ZlcmlmaWVkIjp0cnVlLCJpc3MiOiJodHRwczpcL1wvY29nbml0by1pZHAuZXUtY2VudHJhbC0xLmFtYXpvbmF3cy5jb21cL2V1LWNlbnRyYWwtMV9DNmJ3dWdnTEkiLCJjb2duaXRvOnVzZXJuYW1lIjoiNTM1OThjMTMtZjcwNy00NTM2LTllNTMtYmNmMmQwYWZmODQ5IiwiZ2l2ZW5fbmFtZSI6Ik5hdmVlbiIsIm5vbmNlIjoiNDRzaXdMeVF4aFdpa1VEamQ2OTJ3bU91dmwtZENweGJ4OTlIOU56TTAwLTJFdHpSSldTb2oyZmJMLVRfUUV6Y1lYcHU0cDFWbGg5SVV6TWdjbW1lTjh5TlFmM1hzbmVzNUJSZWZESl85b1VXUW16bmVCdkNSVWZTRXJ1T2JWY1o2LUtQQTl5VDRzOGdtbDhCMkh5OE5FVG9qZWg2Uzlfa0FZWF9xUS1vbEJzIiwicGljdHVyZSI6Imh0dHBzOlwvXC9saDMuZ29vZ2xldXNlcmNvbnRlbnQuY29tXC9hLVwvQU9oMTRHaTBBN0tpS194NjFSaUR5c0Nma0UycEQ0WmlvTXpyakxQbDhHVVE9czk2LWMiLCJvcmlnaW5fanRpIjoiMmJhMjg5ZDgtYzg4ZS00NTY5LTg5YjQtODIxN2NlNjc0NWRiIiwiYXVkIjoiM2ZlY3A3cWs3dWtrbnMzcm0zbnNmbjJ0dXMiLCJpZGVudGl0aWVzIjpbeyJ1c2VySWQiOiIxMDQ5MDM5OTI0OTczNzY1ODgzMTYiLCJwcm92aWRlck5hbWUiOiJHb29nbGUiLCJwcm92aWRlclR5cGUiOiJHb29nbGUiLCJpc3N1ZXIiOm51bGwsInByaW1hcnkiOiJmYWxzZSIsImRhdGVDcmVhdGVkIjoiMTYzOTQ5OTEwMDU4OSJ9XSwidG9rZW5fdXNlIjoiaWQiLCJhdXRoX3RpbWUiOjE2NDU0NTY1ODksIm5hbWUiOiJOYXZlZW4gS3VtYXIiLCJjdXN0b206b3JncyI6InRlc3Rfb3JnXzIsdGFuZGxhYnMsc3RhY2tndWFyZGlhbixwb2xpY2llcyxodXNreS1hbWJlci13YWxsYWJ5LGdsb3Jpb3VzLWhhcmxlcXVpbi1yb29rLGRpc2NpcGxpbmFyeS1ibHVzaC1vcmFuZ3V0YW4scXVlcnVsb3VzLXdoaXRlLWZyb2csbnV0dHktZ29sZC1saW1wZXQsc3RhdGlzdGljYWwtYXF1YW1hcmluZS1sYXJrLHZpdGFsLWFtYmVyLXNtZWx0LGV2ZW50dWFsLWNvcHBlcix1bmhhcHB5LXRhbixzaHktb2xpdmUsZGVmZWF0ZWQteWVsbG93IiwiZXhwIjoxNjQ1NDY3Mzg5LCJpYXQiOjE2NDU0NTY1ODksImZhbWlseV9uYW1lIjoiS3VtYXIiLCJqdGkiOiJhMjczZWU5Ni1mMjU1LTRmNzEtYTVmYy05MTU4YTljNDc1MmEiLCJlbWFpbCI6Im5hdmVlbnNoYXJtYTEwZEBnbWFpbC5jb20ifQ.cWnepujmjX7cwbJ6KtY2RmnNFDDYqjVOrGHPg08dY6LhKzZfYMq3aw6aYVV36iKbYbQsb2rnxaWkWKr6v_Z6PHZv7o5r_6Wh83Mq1XQML-n9iDEjc2xsYJbq6nYNmcvNq6G5QYi_4w5e-3f4zTvtRSx070Xa_vpYjgvj-DugTTEsQkju8LLb-nv3v3YrkykF8LPKdjgc4whjIbBY3UHpkGk3fkQqrDmtNIJ_mucs5SO79SgDfGpAhd0gATEA_-D6NuCeL28NWSLL3VZbwrRAIeFR0s5ExwmNbcDrk38bDlSx9jbtzIDYkuiBvxr-Axk7nzVlHFx9rokkSDhS3F-L0g'
      Authorization: `apikey ${this.authorization.api_key}`
    }

    try {
      const res = await axios.post(this.baseURL.href, {}, config)
      console.log(res)
    } catch (error) {
      console.log(error)
    }
  }
}
