import axios from 'axios'
import * as core from '@actions/core'
import http from 'http'
import https from 'https'
import url from 'url'

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
      Authorization: `apikey ${this.authorization.api_key}`
    }

    try {
      const res = await axios.post(this.baseURL.href, {}, config)

      return {
        data: res.data.data,
        msg: res.data.msg,
        error: null
      }
    } catch (error) {
      return {
        data: null,
        msg: null,
        error: this.getErrorMessage(error)
      }
    }
  }

  getErrorMessage(error) {
    let {status} = error?.response || {}
    let msg = ''
    if (status === 401) {
      msg = 'Unauthorized'
    } else if (status === 403) {
      msg = 'Unauthorized'
    } else if (status === 404) {
      msg = 'Not Found'
    } else if (status === 500) {
      msg = 'Internal Server Error'
    }

    if (!msg) msg = 'Connection Error'
    return msg
  }
}
