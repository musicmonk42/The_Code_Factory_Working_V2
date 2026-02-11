// Copyright © 2025 Novatrax Labs LLC. All Rights Reserved.

/**
 * K6 Load Testing Script for The Code Factory Platform
 * 
 * This script tests the scalability of the platform by simulating multiple
 * concurrent users making API requests. It uses staged ramp-up to gradually
 * increase load and tests key endpoints.
 * 
 * Usage:
 *   k6 run loadtest.js
 * 
 * With custom API URL:
 *   k6 run -e API_URL=http://myserver:8000 loadtest.js
 * 
 * With custom max VUs:
 *   k6 run --vus 100 loadtest.js
 */

import http from 'k6/http';
import { check, sleep } from 'k6';
import { Rate, Trend } from 'k6/metrics';

// Custom metrics
const healthCheckFailureRate = new Rate('health_check_failures');
const generateFailureRate = new Rate('generate_failures');
const listGenerationsFailureRate = new Rate('list_generations_failures');
const generateDuration = new Trend('generate_duration');

// Configuration
const API_URL = __ENV.API_URL || 'http://localhost:8000';
const P95_THRESHOLD_MS = 500;  // 95th percentile response time threshold
const ERROR_RATE_THRESHOLD = 0.01;  // 1% error rate threshold

// Test options with staged ramp-up
export const options = {
    stages: [
        // Warm-up phase: Ramp up to 10 users over 30 seconds
        { duration: '30s', target: 10 },
        // Maintain 10 users for 1 minute
        { duration: '1m', target: 10 },
        // Scale up to 50 users over 1 minute
        { duration: '1m', target: 50 },
        // Maintain 50 users for 2 minutes
        { duration: '2m', target: 50 },
        // Scale up to 100 users over 1 minute
        { duration: '1m', target: 100 },
        // Maintain 100 users for 2 minutes
        { duration: '2m', target: 100 },
        // Ramp down to 0 users over 30 seconds
        { duration: '30s', target: 0 },
    ],
    thresholds: {
        // 95th percentile response time should be under threshold
        'http_req_duration{type:health}': [`p(95)<${P95_THRESHOLD_MS}`],
        'http_req_duration{type:generate}': [`p(95)<${P95_THRESHOLD_MS}`],
        'http_req_duration{type:list}': [`p(95)<${P95_THRESHOLD_MS}`],
        // Overall p95 should be under threshold
        'http_req_duration': [`p(95)<${P95_THRESHOLD_MS}`],
        // Less than 1% request failure rate
        'http_req_failed': [`rate<${ERROR_RATE_THRESHOLD}`],
        'health_check_failures': [`rate<${ERROR_RATE_THRESHOLD}`],
        'generate_failures': [`rate<${ERROR_RATE_THRESHOLD}`],
        'list_generations_failures': [`rate<${ERROR_RATE_THRESHOLD}`],
    },
};

/**
 * Main test scenario - runs for each virtual user in each iteration
 */
export default function () {
    // 1. Health check
    testHealthEndpoint();
    
    // 2. Test code generation endpoint (main workload)
    testGenerateEndpoint();
    
    // 3. Test list generations endpoint
    testListGenerationsEndpoint();
    
    // Think time: simulate realistic user behavior
    sleep(1);
}

/**
 * Test the health check endpoint
 */
function testHealthEndpoint() {
    const response = http.get(`${API_URL}/health`, {
        tags: { type: 'health' },
    });
    
    const success = check(response, {
        'health check status is 200': (r) => r.status === 200,
        'health check has status field': (r) => {
            try {
                const body = JSON.parse(r.body);
                return body.status !== undefined;
            } catch (e) {
                return false;
            }
        },
    });
    
    healthCheckFailureRate.add(!success);
}

/**
 * Test the code generation endpoint (POST /api/v1/generate)
 * This is the main workload endpoint as seen in run_integration_tests.py
 */
function testGenerateEndpoint() {
    const payload = JSON.stringify({
        requirements: 'Create a simple Hello World function',
        language: 'python',
        framework: 'flask',
    });
    
    const params = {
        headers: {
            'Content-Type': 'application/json',
        },
        tags: { type: 'generate' },
    };
    
    const response = http.post(`${API_URL}/api/v1/generate`, payload, params);
    
    const success = check(response, {
        'generate status is 200 or 202': (r) => r.status === 200 || r.status === 202,
        'generate response has id': (r) => {
            try {
                const body = JSON.parse(r.body);
                return body.id !== undefined;
            } catch (e) {
                return false;
            }
        },
    });
    
    generateFailureRate.add(!success);
    if (response.timings.duration) {
        generateDuration.add(response.timings.duration);
    }
}

/**
 * Test the list generations endpoint (GET /api/v1/generations)
 */
function testListGenerationsEndpoint() {
    const response = http.get(`${API_URL}/api/v1/generations`, {
        tags: { type: 'list' },
    });
    
    const success = check(response, {
        'list generations status is 200': (r) => r.status === 200,
        'list generations returns array': (r) => {
            try {
                const body = JSON.parse(r.body);
                return Array.isArray(body);
            } catch (e) {
                return false;
            }
        },
    });
    
    listGenerationsFailureRate.add(!success);
}

/**
 * Setup function - runs once at the beginning of the test
 */
export function setup() {
    console.log(`Starting load test against ${API_URL}`);
    console.log('Testing endpoints:');
    console.log(`  - GET ${API_URL}/health`);
    console.log(`  - POST ${API_URL}/api/v1/generate`);
    console.log(`  - GET ${API_URL}/api/v1/generations`);
    
    // Verify the API is reachable
    const response = http.get(`${API_URL}/health`);
    if (response.status !== 200) {
        throw new Error(`API health check failed with status ${response.status}. Is the server running?`);
    }
    
    console.log('API is reachable. Starting load test...');
}

/**
 * Teardown function - runs once at the end of the test
 */
export function teardown(data) {
    console.log('Load test completed');
}
