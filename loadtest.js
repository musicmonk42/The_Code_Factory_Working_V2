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
import { textSummary } from 'https://jslib.k6.io/k6-summary/0.1.0/index.js';

// Custom metrics
const healthCheckFailureRate = new Rate('health_check_failures');
const generateFailureRate = new Rate('generate_failures');
const listGenerationsFailureRate = new Rate('list_generations_failures');
const generateDuration = new Trend('generate_duration');
const e2eGenerationDuration = new Trend('e2e_generation_duration');
// Note: k6 Rate.add(true) = "pass", Rate.add(false) = "fail". rate = passes/(passes+fails).
// For e2e_generation_failures, we add(true) on success and add(false) on failure,
// so the threshold 'rate>0.95' means "more than 95% of jobs should succeed".
const e2eGenerationFailures = new Rate('e2e_generation_failures');
const pollingIterations = new Trend('polling_iterations');

// Configuration
const API_URL = __ENV.API_URL || 'http://localhost:8000';
const MAX_VUS = parseInt(__ENV.MAX_VUS || '100', 10);  // Maximum virtual users
const P95_THRESHOLD_MS = 500;  // 95th percentile response time threshold
const POLL_P95_THRESHOLD_MS = 1000;  // 95th percentile for polling requests (more lenient)
const ERROR_RATE_THRESHOLD = 0.01;  // 1% error rate threshold
const POLL_TIMEOUT_S = parseInt(__ENV.POLL_TIMEOUT_S || '60', 10);  // Polling timeout in seconds
const POLL_INTERVAL_S = 2;  // Polling interval in seconds
const SKIP_POLLING = __ENV.SKIP_POLLING === 'true';  // Skip polling if true
const E2E_THRESHOLD_MS = parseInt(__ENV.E2E_THRESHOLD_MS || '30000', 10);  // E2E p95 threshold

// Calculate intermediate VU targets based on MAX_VUS
const VU_WARMUP = Math.floor(MAX_VUS * 0.1);  // 10% of max
const VU_MEDIUM = Math.floor(MAX_VUS * 0.5);  // 50% of max

// Test options with staged ramp-up
export const options = {
    stages: [
        // Warm-up phase: Ramp up to 10% of max users over 30 seconds
        { duration: '30s', target: VU_WARMUP },
        // Maintain warmup level for 1 minute
        { duration: '1m', target: VU_WARMUP },
        // Scale up to 50% of max users over 1 minute
        { duration: '1m', target: VU_MEDIUM },
        // Maintain medium level for 2 minutes
        { duration: '2m', target: VU_MEDIUM },
        // Scale up to max users over 1 minute
        { duration: '1m', target: MAX_VUS },
        // Maintain peak load for 2 minutes
        { duration: '2m', target: MAX_VUS },
        // Ramp down to 0 users over 30 seconds
        { duration: '30s', target: 0 },
    ],
    thresholds: {
        // 95th percentile response time should be under threshold
        'http_req_duration{type:health}': [`p(95)<${P95_THRESHOLD_MS}`],
        'http_req_duration{type:generate}': [`p(95)<${P95_THRESHOLD_MS}`],
        'http_req_duration{type:list}': [`p(95)<${P95_THRESHOLD_MS}`],
        'http_req_duration{type:poll}': [`p(95)<${POLL_P95_THRESHOLD_MS}`],  // More lenient for polling
        // Overall p95 should be under threshold
        'http_req_duration': [`p(95)<${P95_THRESHOLD_MS}`],
        // Less than 1% request failure rate
        'http_req_failed': [`rate<${ERROR_RATE_THRESHOLD}`],
        'health_check_failures': [`rate<${ERROR_RATE_THRESHOLD}`],
        'generate_failures': [`rate<${ERROR_RATE_THRESHOLD}`],
        'list_generations_failures': [`rate<${ERROR_RATE_THRESHOLD}`],
        // E2E thresholds
        'e2e_generation_duration': [`p(95)<${E2E_THRESHOLD_MS}`],
        'e2e_generation_failures': ['rate>0.95'],
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
    
    // Log failures with response details for debugging
    if (!success) {
        console.warn(`Health check failed: status=${response.status}, body=${response.body}`);
    }
    
    healthCheckFailureRate.add(!success);
}

/**
 * Poll for generation job completion
 * @param {string} jobId - The job ID to poll
 * @param {number} startTime - The timestamp when the job was submitted
 * @returns {boolean} - True if job completed successfully, false otherwise
 */
function pollForCompletion(jobId, startTime) {
    const maxAttempts = Math.ceil(POLL_TIMEOUT_S / POLL_INTERVAL_S);
    let iterations = 0;
    let completed = false;
    let failed = false;
    
    for (let attempt = 0; attempt < maxAttempts; attempt++) {
        sleep(POLL_INTERVAL_S);
        iterations++;
        
        const response = http.get(`${API_URL}/api/v1/generations/${jobId}`, {
            tags: { type: 'poll' },
        });
        
        if (response.status !== 200) {
            console.warn(`Poll request failed: status=${response.status}, jobId=${jobId}`);
            failed = true;
            break;
        }
        
        try {
            const body = JSON.parse(response.body);
            const jobStatus = body.status;
            
            if (jobStatus === 'completed' || jobStatus === 'success') {
                completed = true;
                break;
            } else if (jobStatus === 'failed' || jobStatus === 'cancelled') {
                console.warn(`Job failed: jobId=${jobId}, status=${jobStatus}, error=${body.error || 'unknown'}`);
                failed = true;
                break;
            }
            // Continue polling for 'pending', 'running', 'needs_clarification' statuses
        } catch (e) {
            console.warn(`Failed to parse poll response: jobId=${jobId}, error=${e.message}`);
            failed = true;
            break;
        }
    }
    
    // Record metrics
    // Note: elapsed time includes polling overhead (sleep intervals), which is intentional
    // to measure the total wall-clock time from submission to completion
    const elapsedTime = Date.now() - startTime;
    e2eGenerationDuration.add(elapsedTime);
    pollingIterations.add(iterations);
    
    // Record success/failure
    // k6 Rate.add(true) = "pass" (counted in passes), Rate.add(false) = "fail" (counted in fails)
    // threshold 'rate>0.95' means: passes/(passes+fails) > 0.95, i.e., >95% of jobs should complete
    if (!completed && !failed) {
        console.warn(`Job timed out after ${POLL_TIMEOUT_S}s: jobId=${jobId}`);
    }
    // Use boolean directly - consistent with other Rate metrics in this file
    // (e.g., healthCheckFailureRate.add(!success) also passes a boolean)
    e2eGenerationFailures.add(completed);
    
    return completed;
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
    
    const startTime = Date.now();
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
    
    // Log failures with response details for debugging
    if (!success) {
        console.warn(`Generate endpoint failed: status=${response.status}, body=${response.body}`);
    }
    
    generateFailureRate.add(!success);
    if (response.timings.duration) {
        generateDuration.add(response.timings.duration);
    }
    
    // Poll for completion if enabled and job was submitted successfully
    if (!SKIP_POLLING && success && (response.status === 200 || response.status === 202)) {
        try {
            const body = JSON.parse(response.body);
            const jobId = body.id;
            if (jobId) {
                pollForCompletion(jobId, startTime);
            }
        } catch (e) {
            console.warn(`Failed to parse generate response for polling: ${e.message}`);
        }
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
    
    // Log failures with response details for debugging
    if (!success) {
        console.warn(`List generations endpoint failed: status=${response.status}, body=${response.body}`);
    }
    
    listGenerationsFailureRate.add(!success);
}

/**
 * Setup function - runs once at the beginning of the test
 */
export function setup() {
    console.log(`Starting load test against ${API_URL}`);
    console.log(`Max virtual users: ${MAX_VUS} (warmup: ${VU_WARMUP}, medium: ${VU_MEDIUM})`);
    console.log(`Polling: ${SKIP_POLLING ? 'disabled' : 'enabled'} (timeout: ${POLL_TIMEOUT_S}s, interval: ${POLL_INTERVAL_S}s)`);
    console.log('Testing endpoints:');
    console.log(`  - GET ${API_URL}/health`);
    console.log(`  - POST ${API_URL}/api/v1/generate`);
    console.log(`  - GET ${API_URL}/api/v1/generations/{job_id} (polling)`);
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

/**
 * Custom summary handler - replaces deprecated --summary-export flag.
 * 
 * This function is called by k6 at the end of the test run with the
 * complete test results. It produces both console output and a JSON
 * summary file with correct threshold boolean values.
 * 
 * The deprecated --summary-export flag has a known bug where threshold
 * pass/fail booleans are inverted in the JSON output. Using handleSummary()
 * bypasses this bug entirely since we serialize the data ourselves.
 */
export function handleSummary(data) {
    return {
        'stdout': textSummary(data, { indent: ' ', enableColors: true }),
        'loadtest-summary.json': JSON.stringify(data, null, 4),
    };
}
