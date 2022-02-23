import * as core from '@actions/core'
import http = require('http')
import https = require('https')
import url = require('url')
import axios from 'axios'

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
      const res = await axios.post(this.baseURL.href, {}, config);
      return {
        response: res.data,
        error: ""
      };
    } catch (error) {
      return {
        response: null,
        error: error
      };
    }
  }
}
