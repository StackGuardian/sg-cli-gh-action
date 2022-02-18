import * as core from '@actions/core'
import http = require('http');
import https = require('https');
import q = require('q');
import url = require('url');

// TODO: Use SG CoreServices for Workflow Runs

export interface Authorization {
    api_key: string
}

export class WebService {
    private baseURL: url.Url;
    protected protocol: typeof https | typeof http;
    protected protocolLabel: string;
    protected authorization: Authorization;

    constructor(endpoint: string, context: string, authorization?: Authorization) {
        this.baseURL = url.parse(endpoint);
        if (this.baseURL.path === '/') {
            this.baseURL.path += context;
        } else if (this.baseURL.path === `/${context}/`) {
            this.baseURL.path = `/${context}`;
        }
        this.authorization = authorization;
        this.protocol = this.baseURL.protocol === 'https:' ? https : http;
        this.protocolLabel = this.baseURL.protocol || 'http:';
    }

    // Get workflow run, post workflow run
}
