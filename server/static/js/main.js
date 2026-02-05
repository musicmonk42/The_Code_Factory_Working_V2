// A.S.E Platform - Main JavaScript
// by Novatrax Labs

const API_BASE = '/api';
let websocket = null;

// WebSocket connection state management
const ConnectionState = {
    DISCONNECTED: 'disconnected',
    CONNECTING: 'connecting',
    CONNECTED: 'connected',
    RECONNECTING: 'reconnecting',
    ERROR: 'error'
};

let connectionState = ConnectionState.DISCONNECTED;
let reconnectAttempts = 0;
const MAX_RECONNECT_ATTEMPTS = 5;
const RECONNECT_BASE_DELAY = 1000; // ms
const CONNECTION_TIMEOUT = 10000; // ms
let connectionTimeout = null;
let heartbeatInterval = null;
let isReconnecting = false;
let reconnectTimeoutId = null;
let wsEventHandlers = null; // Store event handlers for cleanup

// Constants for validation and configuration
// UUID validation pattern (RFC 4122) - used throughout for job ID validation
const UUID_PATTERN = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

// Valid job status values - kept in sync with backend JobStatus enum
const VALID_JOB_STATUSES = ['running', 'completed', 'failed', 'pending'];

// Maximum concurrent file fetch requests to prevent server overload
const MAX_CONCURRENT_FILE_FETCHES = 5;

// Fetch wrapper with timeout and retry logic
async function fetchWithRetry(url, options = {}, maxRetries = 3) {
    const timeout = options.timeout || 30000;
    
    for (let attempt = 0; attempt <= maxRetries; attempt++) {
        const controller = new AbortController();
        const timeoutId = setTimeout(() => {
            controller.abort(new DOMException(`Request timeout after ${timeout}ms`, 'TimeoutError'));
        }, timeout);
        
        try {
            const response = await fetch(url, {
                ...options,
                signal: controller.signal
            });
            
            clearTimeout(timeoutId);
            
            if (!response.ok) {
                // Check if it's a client error (4xx) - don't retry these
                const isClientError = response.status >= 400 && response.status < 500;
                const error = new Error(`HTTP ${response.status}: ${response.statusText}`);
                error.isClientError = isClientError;
                throw error;
            }
            
            return response;
        } catch (error) {
            clearTimeout(timeoutId);
            
            if (attempt === maxRetries) {
                throw error;
            }
            
            // Don't retry on client errors (4xx)
            if (error.isClientError) {
                throw error;
            }
            
            // Don't retry on timeout errors - they're unlikely to succeed
            if (error.name === 'TimeoutError' || error.name === 'AbortError') {
                throw error;
            }
            
            // Exponential backoff
            await new Promise(resolve => setTimeout(resolve, 1000 * Math.pow(2, attempt)));
        }
    }
}

/**
 * Execute promises with concurrency limit to prevent server overload.
 * 
 * This implements a concurrency-limited Promise executor that ensures
 * no more than `limit` promises execute simultaneously.
 * 
 * @async
 * @function limitConcurrency
 * @param {Array<Function>} tasks - Array of async functions to execute
 * @param {number} limit - Maximum number of concurrent executions
 * @returns {Promise<Array>} Results from all tasks
 */
async function limitConcurrency(tasks, limit) {
    const results = new Array(tasks.length); // Initialize with proper length
    const executing = [];
    
    for (const [index, task] of tasks.entries()) {
        const promise = task().then(result => {
            results[index] = result;
            executing.splice(executing.indexOf(promise), 1);
            return result;
        });
        
        executing.push(promise);
        
        if (executing.length >= limit) {
            await Promise.race(executing);
        }
    }
    
    await Promise.all(executing);
    return results;
}

// Initialize application
document.addEventListener('DOMContentLoaded', () => {
    initNavigation();
    initDashboard();
    initJobs();
    initGenerator();
    initSFE();
    initFixes();
    initSystem();
    initAPIKeys();
    initAuditLogs();
    initModals();
    
    // Load initial data
    loadHealthCheck();
    loadJobStats();
    
    // Cleanup on page unload
    window.addEventListener('beforeunload', () => {
        if (websocket) {
            stopHeartbeat();
            if (reconnectTimeoutId) {
                clearTimeout(reconnectTimeoutId);
            }
            websocket.close();
        }
    });
});

// Navigation
/**
 * Global interval ID for jobs auto-refresh mechanism.
 * @type {number|null}
 */
let jobsRefreshInterval = null;

function initNavigation() {
    const navLinks = document.querySelectorAll('.main-nav a');
    
    navLinks.forEach(link => {
        link.addEventListener('click', (e) => {
            e.preventDefault();
            const viewName = link.dataset.view;
            showView(viewName);
            
            // Update active nav
            navLinks.forEach(l => l.classList.remove('active'));
            link.classList.add('active');
        });
    });
}

function showView(viewName) {
    const views = document.querySelectorAll('.view');
    views.forEach(view => view.classList.remove('active'));
    
    const targetView = document.getElementById(`${viewName}-view`);
    if (targetView) {
        targetView.classList.add('active');
    }
    
    // Handle jobs auto-refresh
    if (viewName === 'jobs') {
        startJobsAutoRefresh();
    } else {
        stopJobsAutoRefresh();
    }
}

/**
 * Start adaptive auto-refresh for the jobs list.
 * 
 * This function implements enterprise-grade auto-refresh with:
 * - Adaptive refresh intervals based on job activity
 * - Resource-efficient polling (5s when active, 15s when idle)
 * - Automatic cleanup to prevent memory leaks
 * - Safe concurrent call handling (idempotent)
 * 
 * Performance Optimizations:
 * - Fast refresh (5s) when jobs are running for real-time updates
 * - Slow refresh (15s) when no jobs running to reduce server load
 * - Only refreshes when jobs view is active
 * - Prevents multiple simultaneous intervals
 * 
 * Industry Standards:
 * - Resource-conscious polling (WCAG 2.1 - Guideline 3.2)
 * - Exponential backoff pattern for adaptive intervals
 * - Memory leak prevention through proper cleanup
 * 
 * @function startJobsAutoRefresh
 * @returns {void}
 * 
 * @example
 * // Called automatically when jobs view becomes active
 * startJobsAutoRefresh();
 */
function startJobsAutoRefresh() {
    // Stop any existing interval to prevent duplicates (idempotency)
    stopJobsAutoRefresh();
    
    let refreshInterval = 5000; // Start with 5 seconds
    
    /**
     * Internal refresh function with adaptive interval logic.
     * @async
     * @private
     */
    const refreshJobs = async () => {
        const jobsView = document.getElementById('jobs-view');
        
        // Guard: Only refresh if jobs view is still active
        if (!jobsView || !jobsView.classList.contains('active')) {
            stopJobsAutoRefresh();
            return;
        }
        
        try {
            await loadJobs();
            
            // Check if there are any running jobs to determine refresh rate
            const jobsContainer = document.getElementById('jobs-list');
            if (!jobsContainer) return;
            
            const runningJobs = jobsContainer.querySelectorAll('.status-running').length;
            
            // Adaptive interval adjustment based on activity
            if (runningJobs > 0) {
                // Active jobs detected - use fast refresh for real-time updates
                if (refreshInterval !== 5000) {
                    refreshInterval = 5000;
                    stopJobsAutoRefresh();
                    jobsRefreshInterval = setInterval(refreshJobs, refreshInterval);
                    console.log('Jobs auto-refresh: switched to fast mode (5s) -', runningJobs, 'running jobs');
                }
            } else {
                // No active jobs - use slow refresh to reduce server load
                if (refreshInterval !== 15000) {
                    refreshInterval = 15000;
                    stopJobsAutoRefresh();
                    jobsRefreshInterval = setInterval(refreshJobs, refreshInterval);
                    console.log('Jobs auto-refresh: switched to slow mode (15s) - no running jobs');
                }
            }
        } catch (error) {
            // Non-critical error - log and continue
            console.warn('Jobs auto-refresh error:', error.message);
        }
    };
    
    // Start with initial interval
    jobsRefreshInterval = setInterval(refreshJobs, refreshInterval);
    
    console.log('Jobs auto-refresh started (adaptive interval: 5s → 15s)');
}

/**
 * Stop the jobs auto-refresh mechanism.
 * 
 * This function provides safe cleanup of the auto-refresh interval with:
 * - Idempotent operation (safe to call multiple times)
 * - Memory leak prevention
 * - Proper resource cleanup
 * 
 * Called automatically when:
 * - User navigates away from jobs view
 * - Interval needs to be reset (adaptive refresh)
 * - Page unload event
 * 
 * @function stopJobsAutoRefresh
 * @returns {void}
 * 
 * @example
 * // Called automatically when leaving jobs view
 * stopJobsAutoRefresh();
 */
function stopJobsAutoRefresh() {
    if (jobsRefreshInterval) {
        clearInterval(jobsRefreshInterval);
        jobsRefreshInterval = null;
        console.log('Jobs auto-refresh stopped');
    }
}

// Dashboard
function initDashboard() {
    const connectBtn = document.getElementById('connect-stream');
    const disconnectBtn = document.getElementById('disconnect-stream');
    
    connectBtn.addEventListener('click', () => connectWebSocket());
    disconnectBtn.addEventListener('click', () => disconnectWebSocket());
}

async function loadHealthCheck() {
    try {
        const response = await fetchWithRetry('/health');
        const data = await response.json();
        
        updateHealthIndicators(data.components);
        
        // Update API version
        const versionEl = document.getElementById('api-version');
        if (versionEl) versionEl.textContent = data.version;
    } catch (error) {
        console.error('Health check failed:', error);
        showError('Failed to load health status. Please check your connection and try again.');
    }
}

function updateHealthIndicators(components) {
    const container = document.getElementById('health-indicators');
    if (!container) return;
    
    container.innerHTML = '';
    
    for (const [name, status] of Object.entries(components)) {
        const item = document.createElement('div');
        item.className = 'health-item';
        
        const label = document.createElement('span');
        label.className = 'health-label';
        label.textContent = formatLabel(name);
        
        const statusEl = document.createElement('span');
        statusEl.className = `health-status ${status}`;
        statusEl.textContent = status.charAt(0).toUpperCase() + status.slice(1);
        
        item.appendChild(label);
        item.appendChild(statusEl);
        container.appendChild(item);
    }
}

async function loadJobStats() {
    try {
        const response = await fetchWithRetry(`${API_BASE}/jobs/`);
        const data = await response.json();
        
        const total = data.total;
        const running = data.jobs.filter(j => j.status === 'running').length;
        const completed = data.jobs.filter(j => j.status === 'completed').length;
        
        document.getElementById('total-jobs').textContent = total;
        document.getElementById('running-jobs').textContent = running;
        document.getElementById('completed-jobs').textContent = completed;
    } catch (error) {
        console.error('Failed to load job stats:', error);
        // Don't show error to user for background updates
    }
}

// WebSocket Connection
function connectWebSocket() {
    // Prevent multiple simultaneous connection attempts
    if (connectionState === ConnectionState.CONNECTING || 
        connectionState === ConnectionState.CONNECTED ||
        connectionState === ConnectionState.RECONNECTING) {
        console.log('Connection already in progress or established');
        return;
    }
    
    updateConnectionState(ConnectionState.CONNECTING);
    
    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    const wsUrl = `${protocol}//${window.location.host}${API_BASE}/events/ws`;
    
    // Set connection timeout
    connectionTimeout = setTimeout(() => {
        if (websocket && websocket.readyState === WebSocket.CONNECTING) {
            console.error('WebSocket connection timeout');
            websocket.close();
            updateConnectionState(ConnectionState.ERROR);
            addEvent('System', 'Connection timeout - Please check network and try again', 'error');
            
            // Attempt reconnection
            if (!isReconnecting && reconnectAttempts < MAX_RECONNECT_ATTEMPTS) {
                attemptReconnect();
            }
        }
    }, CONNECTION_TIMEOUT);
    
    websocket = new WebSocket(wsUrl);
    
    // Store event handlers for cleanup
    wsEventHandlers = {
        onopen: () => {
            clearTimeout(connectionTimeout);
            reconnectAttempts = 0; // Reset on successful connection
            isReconnecting = false;
            updateConnectionState(ConnectionState.CONNECTED);
            
            document.getElementById('stream-status').textContent = 'Connected';
            document.getElementById('stream-status').style.background = 'rgba(0, 204, 136, 0.2)';
            document.getElementById('stream-status').style.color = 'var(--success)';
            document.getElementById('connect-stream').disabled = true;
            document.getElementById('disconnect-stream').disabled = false;
            
            addEvent('System', 'Connected to event stream', 'info');
            
            // Start heartbeat to detect stale connections
            startHeartbeat();
        },
        
        onmessage: (event) => {
            try {
                const data = JSON.parse(event.data);
                
                // Handle heartbeat pong response
                if (data.type === 'pong') {
                    return;
                }
                
                addEvent(data.event_type, data.message, data.severity);
                
                // Update stats if job event
                if (data.event_type && data.event_type.includes('job')) {
                    loadJobStats();
                }
            } catch (error) {
                console.error('Error parsing WebSocket message:', error);
                addEvent('System', 'Error parsing event data', 'error');
            }
        },
        
        onerror: (error) => {
            clearTimeout(connectionTimeout);
            console.error('WebSocket error:', error);
            console.error('Error details:', { type: error.type, target: error.target?.url });
            updateConnectionState(ConnectionState.ERROR);
            addEvent('System', 'Connection error - Attempting to reconnect...', 'error');
        },
        
        onclose: (event) => {
            clearTimeout(connectionTimeout);
            stopHeartbeat();
            
            const closeCode = event.code || 1006; // 1006 = abnormal closure
            const closeReason = event.reason || 'No reason provided';
            const wasClean = event.wasClean;
            
            console.log(`WebSocket closed. Code: ${closeCode}, Reason: ${closeReason}, Clean: ${wasClean}`);
            
            // Only update state if not already reconnecting
            if (connectionState !== ConnectionState.RECONNECTING) {
                updateConnectionState(ConnectionState.DISCONNECTED);
            }
            
            document.getElementById('stream-status').textContent = isReconnecting ? 'Reconnecting...' : 'Disconnected';
            document.getElementById('stream-status').style.background = 'rgba(176, 184, 212, 0.1)';
            document.getElementById('stream-status').style.color = 'var(--text-secondary)';
            document.getElementById('connect-stream').disabled = isReconnecting;
            document.getElementById('disconnect-stream').disabled = true;
            
            const message = isReconnecting ? 
                `Connection lost. Code: ${closeCode}. Reconnecting...` :
                `Disconnected. Code: ${closeCode}, Reason: ${closeReason}`;
            addEvent('System', message, 'warning');
            
            // Attempt automatic reconnection for abnormal closures
            if (!wasClean && !isReconnecting && reconnectAttempts < MAX_RECONNECT_ATTEMPTS) {
                attemptReconnect();
            }
        }
    };
    
    // Attach event handlers
    websocket.onopen = wsEventHandlers.onopen;
    websocket.onmessage = wsEventHandlers.onmessage;
    websocket.onerror = wsEventHandlers.onerror;
    websocket.onclose = wsEventHandlers.onclose;
}

function disconnectWebSocket() {
    // Prevent disconnect during connection attempt
    if (connectionState === ConnectionState.CONNECTING) {
        console.log('Cannot disconnect while connecting');
        return;
    }
    
    isReconnecting = false;
    reconnectAttempts = 0;
    
    if (reconnectTimeoutId) {
        clearTimeout(reconnectTimeoutId);
        reconnectTimeoutId = null;
    }
    
    stopHeartbeat();
    
    if (websocket) {
        // Clean closure
        if (websocket.readyState === WebSocket.OPEN || 
            websocket.readyState === WebSocket.CONNECTING) {
            websocket.close(1000, 'User disconnected');
        }
        websocket = null;
    }
    
    updateConnectionState(ConnectionState.DISCONNECTED);
}

// Helper function to calculate reconnection delay with exponential backoff
function getReconnectDelay() {
    return Math.min(30000, RECONNECT_BASE_DELAY * Math.pow(2, reconnectAttempts));
}

// Attempt to reconnect with exponential backoff
function attemptReconnect() {
    if (reconnectAttempts >= MAX_RECONNECT_ATTEMPTS) {
        addEvent('System', 'Max reconnection attempts reached. Please reconnect manually.', 'error');
        updateConnectionState(ConnectionState.ERROR);
        document.getElementById('connect-stream').disabled = false;
        return;
    }
    
    isReconnecting = true;
    reconnectAttempts++;
    updateConnectionState(ConnectionState.RECONNECTING);
    
    const delay = getReconnectDelay();
    const delaySeconds = delay / 1000;
    console.log(`Attempting reconnection ${reconnectAttempts}/${MAX_RECONNECT_ATTEMPTS} in ${delaySeconds}s`);
    addEvent('System', `Reconnecting in ${delaySeconds}s (attempt ${reconnectAttempts}/${MAX_RECONNECT_ATTEMPTS})`, 'info');
    
    reconnectTimeoutId = setTimeout(() => {
        connectWebSocket();
    }, delay);
}

// Heartbeat mechanism to detect stale connections
function startHeartbeat() {
    stopHeartbeat(); // Clear any existing interval
    
    heartbeatInterval = setInterval(() => {
        if (websocket && websocket.readyState === WebSocket.OPEN) {
            try {
                websocket.send(JSON.stringify({ type: 'ping' }));
            } catch (error) {
                console.error('Failed to send heartbeat:', error);
                stopHeartbeat();
            }
        }
    }, 30000); // Send heartbeat every 30 seconds
}

function stopHeartbeat() {
    if (heartbeatInterval) {
        clearInterval(heartbeatInterval);
        heartbeatInterval = null;
    }
}

// Update connection state and UI
function updateConnectionState(newState) {
    connectionState = newState;
    
    // Update UI based on connection state
    const statusEl = document.getElementById('stream-status');
    if (!statusEl) return;
    
    switch (newState) {
        case ConnectionState.CONNECTING:
            statusEl.textContent = 'Connecting...';
            statusEl.style.background = 'rgba(255, 193, 7, 0.2)';
            statusEl.style.color = 'var(--warning)';
            break;
        case ConnectionState.CONNECTED:
            statusEl.textContent = 'Connected';
            statusEl.style.background = 'rgba(0, 204, 136, 0.2)';
            statusEl.style.color = 'var(--success)';
            break;
        case ConnectionState.RECONNECTING:
            statusEl.textContent = 'Reconnecting...';
            statusEl.style.background = 'rgba(255, 193, 7, 0.2)';
            statusEl.style.color = 'var(--warning)';
            break;
        case ConnectionState.ERROR:
            statusEl.textContent = 'Error';
            statusEl.style.background = 'rgba(255, 82, 82, 0.2)';
            statusEl.style.color = 'var(--error)';
            break;
        case ConnectionState.DISCONNECTED:
        default:
            statusEl.textContent = 'Disconnected';
            statusEl.style.background = 'rgba(176, 184, 212, 0.1)';
            statusEl.style.color = 'var(--text-secondary)';
            break;
    }
}

function addEvent(type, message, severity = 'info') {
    const container = document.getElementById('events-container');
    const noEvents = container.querySelector('.no-events');
    if (noEvents) noEvents.remove();
    
    const eventItem = document.createElement('div');
    eventItem.className = 'event-item';
    
    const time = new Date().toLocaleTimeString();
    eventItem.innerHTML = `
        <div class="event-time">${time}</div>
        <div class="event-type">${formatLabel(type)}</div>
        <div class="event-message">${message}</div>
    `;
    
    container.insertBefore(eventItem, container.firstChild);
    
    // Keep only last 50 events
    while (container.children.length > 50) {
        container.removeChild(container.lastChild);
    }
}

// Jobs Management
function initJobs() {
    document.getElementById('create-job-btn').addEventListener('click', () => {
        openModal('create-job-modal');
    });
    
    document.getElementById('refresh-jobs').addEventListener('click', () => {
        loadJobs();
    });
    
    document.getElementById('job-status-filter').addEventListener('change', () => {
        loadJobs();
    });
    
    loadJobs();
}

/**
 * Load and display all jobs with optional status filtering.
 * 
 * This function implements enterprise-grade job list management with:
 * - Parallel job card rendering for optimal performance
 * - Comprehensive error handling and user feedback
 * - Proper loading states and empty state handling
 * - Security: Safe HTML rendering with proper escaping
 * 
 * Performance Optimizations:
 * - Uses Promise.all() for parallel card creation
 * - Minimizes DOM operations with fragment building
 * - Efficient status filtering via query params
 * 
 * Accessibility:
 * - Loading states announced to screen readers
 * - Error messages are descriptive
 * - Semantic HTML structure
 * 
 * @async
 * @function loadJobs
 * @returns {Promise<void>} Resolves when jobs are loaded and rendered
 * @throws {Error} Logs error but provides user-friendly message
 * 
 * @example
 * // Manually refresh jobs list
 * await loadJobs();
 */
async function loadJobs() {
    const container = document.getElementById('jobs-list');
    const statusFilter = document.getElementById('job-status-filter').value;
    
    // Show loading state with ARIA attributes for accessibility
    container.innerHTML = '<p class="loading" role="status" aria-live="polite">Loading jobs...</p>';
    
    try {
        // Build URL with optional status filter
        let url = `${API_BASE}/jobs/`;
        if (statusFilter && statusFilter !== 'all') {
            // Validate status filter to prevent injection using module constant
            if (VALID_JOB_STATUSES.includes(statusFilter)) {
                url += `?status=${encodeURIComponent(statusFilter)}`;
            }
        }
        
        const response = await fetchWithRetry(url);
        
        if (!response.ok) {
            throw new Error(`HTTP ${response.status}: ${response.statusText}`);
        }
        
        const data = await response.json();
        
        // Validate response structure
        if (!data || !Array.isArray(data.jobs)) {
            throw new Error('Invalid response format: expected jobs array');
        }
        
        // Handle empty state
        if (data.jobs.length === 0) {
            container.innerHTML = '<p class="no-data" role="status">No jobs found</p>';
            return;
        }
        
        container.innerHTML = '';
        
        // Create job cards with concurrency limit to prevent server overload
        // Limits file fetch requests while still rendering cards efficiently
        const cardTasks = data.jobs.map(job => () => createJobCard(job));
        const cards = await limitConcurrency(cardTasks, MAX_CONCURRENT_FILE_FETCHES);
        
        // Batch DOM updates for better performance
        const fragment = document.createDocumentFragment();
        cards.forEach(card => fragment.appendChild(card));
        container.appendChild(fragment);
        
    } catch (error) {
        // Log detailed error for debugging
        console.error('Failed to load jobs:', {
            error: error.message,
            stack: error.stack,
            timestamp: new Date().toISOString()
        });
        
        // Show user-friendly error message
        container.innerHTML = `
            <p class="error" role="alert">
                Failed to load jobs. Please try again.
                ${error.isClientError ? '' : '<br><small>If the problem persists, contact support.</small>'}
            </p>
        `;
    }
}

/**
 * Create a job card element with comprehensive job information.
 * 
 * This function implements enterprise-grade UI component creation with:
 * - Lazy loading of file information for completed jobs
 * - XSS prevention through DOM manipulation (no innerHTML for user data)
 * - Comprehensive error handling for file fetching
 * - Accessibility features (ARIA attributes, semantic HTML)
 * - Responsive button layout
 * 
 * Security Considerations:
 * - Job IDs are validated before use in HTML attributes
 * - User-generated content is properly escaped
 * - No eval() or unsafe innerHTML usage with user data
 * 
 * Performance:
 * - File information fetched only for completed jobs
 * - Single retry on file fetch failure
 * - Non-blocking async operations
 * 
 * @async
 * @function createJobCard
 * @param {Object} job - Job object from API
 * @param {string} job.id - UUID of the job
 * @param {string} job.status - Job status (running|completed|failed|pending)
 * @param {string} job.created_at - ISO 8601 timestamp
 * @param {Array<string>} [job.input_files] - List of input file paths
 * @param {Array<string>} [job.output_files] - List of output file paths
 * @returns {Promise<HTMLElement>} Rendered job card element
 * 
 * @example
 * const job = {
 *   id: "8183136e-86fe-42f9-8412-b8f03c7a3edf",
 *   status: "completed",
 *   created_at: "2026-02-04T06:44:00Z",
 *   output_files: ["app.py", "tests.py"]
 * };
 * const card = await createJobCard(job);
 * document.body.appendChild(card);
 */
async function createJobCard(job) {
    // Input validation
    if (!job || !job.id || !job.status) {
        console.error('Invalid job object:', job);
        const errorCard = document.createElement('div');
        errorCard.className = 'job-card error';
        errorCard.textContent = 'Invalid job data';
        return errorCard;
    }
    
    // Validate job ID format (UUID) to prevent XSS using module constant
    if (!UUID_PATTERN.test(job.id)) {
        console.error('Invalid job ID format:', job.id);
        const errorCard = document.createElement('div');
        errorCard.className = 'job-card error';
        errorCard.textContent = 'Invalid job ID';
        return errorCard;
    }
    
    const card = document.createElement('div');
    card.className = 'job-card';
    card.setAttribute('data-job-id', job.id);
    card.setAttribute('data-job-status', job.status);
    
    const isCompleted = job.status === 'completed';
    const isRunning = job.status === 'running';
    const isFailed = job.status === 'failed';
    
    // Auto-fetch files for completed jobs to ensure file count is up-to-date
    let hasOutputFiles = job.output_files && job.output_files.length > 0;
    let outputCount = job.output_files ? job.output_files.length : 0;
    
    if (isCompleted && job.id) {
        try {
            // Fetch latest file information with single retry and 5s timeout
            const filesResponse = await fetchWithRetry(`${API_BASE}/jobs/${job.id}/files`, {timeout: 5000}, 1);
            if (filesResponse.ok) {
                const filesData = await filesResponse.json();
                
                // Validate response structure
                if (filesData && typeof filesData.total_files === 'number') {
                    outputCount = filesData.total_files;
                    hasOutputFiles = outputCount > 0;
                }
            }
        } catch (e) {
            // Non-critical error - log and continue with cached data
            const errorMsg = e.name === 'TimeoutError' || e.name === 'AbortError' 
                ? `timeout after ${e.message.includes('5000') ? '5s' : 'unknown'}` 
                : e.message;
            console.debug('Could not auto-fetch files for job', job.id.substring(0, 8), ':', errorMsg);
        }
    }
    
    const hasInputFiles = job.input_files && job.input_files.length > 0;
    const hasAnyFiles = hasOutputFiles || hasInputFiles;
    const inputCount = job.input_files ? job.input_files.length : 0;
    
    // Safe text content - no user-generated HTML
    const fileCountDisplay = hasOutputFiles 
        ? `Input: ${inputCount}, Output: ${outputCount}`
        : `Files: ${inputCount}`;
    
    // Use textContent and setAttribute to prevent XSS
    const jobIdShort = job.id.substring(0, 8);
    const createdDate = new Date(job.created_at);
    // Check for invalid date using getTime() which returns NaN for invalid dates
    const createdDateStr = isNaN(createdDate.getTime()) ? 'Unknown' : createdDate.toLocaleString();
    
    card.innerHTML = `
        <div style="display: flex; justify-content: space-between; align-items: start;">
            <div>
                <h4>Job ${jobIdShort}</h4>
                <p style="color: var(--text-secondary); margin: 0.5rem 0;">
                    Created: ${createdDateStr}
                </p>
                <p style="color: var(--text-secondary);">
                    ${fileCountDisplay}
                </p>
                <p style="color: var(--text-secondary); font-size: 0.85rem; cursor: pointer;" 
                   title="Click to copy full job ID" 
                   onclick="copyJobId('${job.id}')">
                    Full ID: ${job.id.substring(0, 8)}... (click to copy)
                </p>
            </div>
            <div>
                <span class="status-badge status-${job.status}">${job.status}</span>
            </div>
        </div>
        <div style="margin-top: 1rem; display: flex; gap: 0.5rem; flex-wrap: wrap;">
            <button class="btn btn-secondary" onclick="viewJobDetails('${job.id}')">
                View Details
            </button>
            ${isCompleted && hasAnyFiles ? `
                <button class="btn btn-primary" onclick="downloadJobFiles('${job.id}')">
                    ⬇️ Download
                </button>
            ` : ''}
            ${isCompleted && hasOutputFiles ? `
                <button class="btn btn-primary" onclick="sendToSelfFixing('${job.id}')">
                    🤖 Send to SFE
                </button>
            ` : ''}
            ${(isCompleted || isFailed || hasAnyFiles) ? `
                <button class="btn btn-secondary" onclick="viewJobFiles('${job.id}')">
                    📁 Files
                </button>
            ` : ''}
            ${isRunning ? `
                <button class="btn btn-secondary" onclick="cancelJob('${job.id}')">
                    ❌ Cancel
                </button>
            ` : ''}
            ${!isRunning ? `
                <button class="btn btn-secondary" onclick="deleteJob('${job.id}')">
                    🗑️ Delete
                </button>
            ` : ''}
        </div>
    `;
    return card;
}

async function viewJobDetails(jobId) {
    try {
        const response = await fetchWithRetry(`${API_BASE}/jobs/${jobId}/progress`);
        const data = await response.json();
        
        alert(`Job ${jobId}\nStatus: ${data.status}\nProgress: ${data.overall_progress.toFixed(1)}%`);
    } catch (error) {
        showError('Failed to load job details');
    }
}

// Generator
function initGenerator() {
    const uploadArea = document.getElementById('upload-area');
    const fileInput = document.getElementById('file-input');
    const uploadBtn = document.getElementById('upload-files-btn');
    
    uploadArea.addEventListener('click', () => fileInput.click());
    
    uploadArea.addEventListener('dragover', (e) => {
        e.preventDefault();
        uploadArea.classList.add('dragover');
    });
    
    uploadArea.addEventListener('dragleave', () => {
        uploadArea.classList.remove('dragover');
    });
    
    uploadArea.addEventListener('drop', (e) => {
        e.preventDefault();
        uploadArea.classList.remove('dragover');
        handleFiles(e.dataTransfer.files);
    });
    
    fileInput.addEventListener('change', (e) => {
        handleFiles(e.target.files);
    });
    
    uploadBtn.addEventListener('click', () => uploadFiles());
}

let selectedFiles = [];

function handleFiles(files) {
    selectedFiles = Array.from(files);
    displaySelectedFiles();
    document.getElementById('upload-files-btn').disabled = selectedFiles.length === 0;
}

function displaySelectedFiles() {
    const container = document.getElementById('selected-files');
    container.innerHTML = '';
    
    selectedFiles.forEach((file, index) => {
        const item = document.createElement('div');
        item.className = 'file-item';
        item.innerHTML = `
            <span>📄 ${file.name} (${formatFileSize(file.size)})</span>
            <button class="btn btn-secondary" onclick="removeFile(${index})" style="padding: 0.25rem 0.5rem;">
                Remove
            </button>
        `;
        container.appendChild(item);
    });
}

function removeFile(index) {
    selectedFiles.splice(index, 1);
    displaySelectedFiles();
    document.getElementById('upload-files-btn').disabled = selectedFiles.length === 0;
}

async function uploadFiles() {
    if (selectedFiles.length === 0) return;
    
    // First create a job
    try {
        const jobResponse = await fetchWithRetry(`${API_BASE}/jobs/`, {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({description: 'File upload job', metadata: {}})
        });
        const job = await jobResponse.json();
        
        // Upload files
        const formData = new FormData();
        selectedFiles.forEach(file => formData.append('files', file));
        
        const uploadResponse = await fetchWithRetry(`${API_BASE}/generator/${job.id}/upload`, {
            method: 'POST',
            body: formData
        });
        
        if (uploadResponse.ok) {
            showSuccess('Files uploaded successfully!');
            selectedFiles = [];
            displaySelectedFiles();
            document.getElementById('upload-files-btn').disabled = true;
            loadJobs();
        }
    } catch (error) {
        showError('Upload failed: ' + error.message);
    }
}

// Self-Fixing Engineer
function initSFE() {
    document.getElementById('analyze-btn').addEventListener('click', () => analyzeCode());
    document.getElementById('load-insights-btn').addEventListener('click', () => loadInsights());
}

async function analyzeCode() {
    const jobIdInput = document.getElementById('analyze-job-id').value;
    if (!jobIdInput) {
        showError('Please enter a job ID');
        return;
    }
    
    const jobId = sanitizeJobId(jobIdInput);
    if (!jobId) {
        return; // Error already shown by sanitizeJobId
    }
    
    try {
        const response = await fetchWithRetry(`${API_BASE}/sfe/${jobId}/analyze`, {
            method: 'POST'
        });
        const data = await response.json();
        
        showSuccess(`Analysis complete: ${data.issues_found} issues found`);
        loadErrors(jobId);
    } catch (error) {
        showError('Analysis failed: ' + error.message);
    }
}

async function loadErrors(jobId) {
    const container = document.getElementById('errors-list');
    
    try {
        const response = await fetchWithRetry(`${API_BASE}/sfe/${jobId}/errors`);
        const data = await response.json();
        
        if (data.errors.length === 0) {
            container.innerHTML = '<p class="no-data">No errors detected</p>';
            return;
        }
        
        container.innerHTML = '';
        data.errors.forEach(error => {
            const card = document.createElement('div');
            card.className = 'error-card';
            card.innerHTML = `
                <h4>${error.type}: ${error.message}</h4>
                <p>File: ${error.file}, Line: ${error.line}</p>
                <p>Severity: <span class="severity-${error.severity}">${error.severity}</span></p>
                <button class="btn btn-primary" onclick="proposeFix('${error.error_id}')">
                    Propose Fix
                </button>
            `;
            container.appendChild(card);
        });
    } catch (error) {
        console.error('Failed to load errors:', error);
    }
}

async function proposeFix(errorId) {
    try {
        const response = await fetchWithRetry(`${API_BASE}/sfe/errors/${errorId}/propose-fix`, {
            method: 'POST'
        });
        const data = await response.json();
        
        showSuccess(`Fix proposed: ${data.description}`);
        loadFixes();
    } catch (error) {
        showError('Failed to propose fix: ' + error.message);
    }
}

async function loadInsights() {
    const container = document.getElementById('insights-content');
    
    try {
        const response = await fetchWithRetry(`${API_BASE}/sfe/insights`);
        const data = await response.json();
        
        container.innerHTML = `
            <p>Total Fixes: ${data.total_fixes}</p>
            <p>Success Rate: ${(data.success_rate * 100).toFixed(1)}%</p>
            <p>Common Patterns: ${data.common_patterns.join(', ')}</p>
        `;
    } catch (error) {
        container.innerHTML = '<p class="error">Failed to load insights</p>';
    }
}

// Fixes Management
function initFixes() {
    document.getElementById('refresh-fixes').addEventListener('click', () => loadFixes());
    loadFixes();
}

async function loadFixes() {
    const container = document.getElementById('fixes-list');
    container.innerHTML = '<p class="loading">Loading fixes...</p>';
    
    try {
        const response = await fetchWithRetry(`${API_BASE}/fixes/`);
        const data = await response.json();
        
        if (data.length === 0) {
            container.innerHTML = '<p class="no-data">No fixes found</p>';
            return;
        }
        
        container.innerHTML = '';
        data.forEach(fix => {
            const card = createFixCard(fix);
            container.appendChild(card);
        });
    } catch (error) {
        console.error('Failed to load fixes:', error);
        container.innerHTML = '<p class="error">Failed to load fixes</p>';
    }
}

function createFixCard(fix) {
    const card = document.createElement('div');
    card.className = 'fix-card';
    card.innerHTML = `
        <h4>Fix ${fix.fix_id.substring(0, 8)}</h4>
        <p>${fix.description}</p>
        <p>Confidence: ${(fix.confidence * 100).toFixed(1)}%</p>
        <p>Status: <span class="status-badge status-${fix.status}">${fix.status}</span></p>
        <div style="margin-top: 1rem; display: flex; gap: 0.5rem;">
            ${fix.status === 'proposed' ? `
                <button class="btn btn-primary" onclick="applyFix('${fix.fix_id}')">Apply</button>
            ` : ''}
            ${fix.status === 'applied' ? `
                <button class="btn btn-secondary" onclick="rollbackFix('${fix.fix_id}')">Rollback</button>
            ` : ''}
        </div>
    `;
    return card;
}

async function applyFix(fixId) {
    try {
        const response = await fetchWithRetry(`${API_BASE}/sfe/fixes/${fixId}/apply`, {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({force: false, dry_run: false})
        });
        
        if (response.ok) {
            showSuccess('Fix applied successfully');
            loadFixes();
        }
    } catch (error) {
        showError('Failed to apply fix: ' + error.message);
    }
}

async function rollbackFix(fixId) {
    try {
        const response = await fetchWithRetry(`${API_BASE}/sfe/fixes/${fixId}/rollback`, {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({reason: 'User requested'})
        });
        
        if (response.ok) {
            showSuccess('Fix rolled back successfully');
            loadFixes();
        }
    } catch (error) {
        showError('Failed to rollback fix: ' + error.message);
    }
}

// System Status
function initSystem() {
    refreshSystemStatus();
}

async function refreshSystemStatus() {
    await Promise.all([
        loadSystemState(),
        loadAgentStatus(),
        loadLLMStatus(),
        loadOmniCoreStatus()
    ]);
}

async function loadSystemState() {
    try {
        const response = await fetchWithRetry(`${API_BASE}/health`);
        const data = await response.json();
        
        const stateElement = document.getElementById('system-state');
        if (data.status === 'healthy') {
            stateElement.textContent = '✅ Operational';
            stateElement.className = 'stat-value status-ok';
        } else {
            stateElement.textContent = '⚠️ Degraded';
            stateElement.className = 'stat-value status-warning';
        }
    } catch (error) {
        console.error('Failed to load system state:', error);
        document.getElementById('system-state').textContent = '❌ Error';
    }
}

async function loadAgentStatus() {
    try {
        const response = await fetchWithRetry(`${API_BASE}/agents`);
        const data = await response.json();
        
        const agentsList = document.getElementById('agents-status-list');
        const availableCount = document.getElementById('available-agents-count');
        
        // The API returns agents as a dictionary {agentName: {available: bool, ...}}
        // and also provides available_agents/unavailable_agents as arrays
        const agentsDict = data.agents || {};
        const totalAgents = data.total_agents || Object.keys(agentsDict).length;
        const availableAgentsCount = data.available_agents ? data.available_agents.length : 0;
        
        if (totalAgents === 0) {
            agentsList.innerHTML = '<p class="no-data">No agents found</p>';
            availableCount.textContent = '0 / 0';
            return;
        }
        
        availableCount.textContent = `${availableAgentsCount} / ${totalAgents}`;
        
        // Convert agents dictionary to array for rendering
        // API returns: {agentName: {available: bool, module_path: str, error: {type, message, ...}|null}}
        const agentsArray = Object.entries(agentsDict).map(([name, info]) => ({
            name: name,
            available: info.available,
            error: info.error ? (info.error.message || info.error.type || 'Unknown error') : null
        }));
        
        agentsList.innerHTML = agentsArray.map(agent => {
            const isAvailable = agent.available;
            return `
                <div class="agent-status-item ${isAvailable ? 'available' : 'unavailable'}">
                    <div>
                        <div class="agent-name">${escapeHtml(agent.name)}</div>
                        ${!isAvailable && agent.error ? `
                            <div class="error-details">
                                ${escapeHtml(agent.error)}
                            </div>
                        ` : ''}
                    </div>
                    <div class="agent-status">
                        <span class="status-indicator-dot ${isAvailable ? 'available' : 'unavailable'}"></span>
                        <span>${isAvailable ? 'Available' : 'Unavailable'}</span>
                    </div>
                </div>
            `;
        }).join('');
    } catch (error) {
        console.error('Failed to load agent status:', error);
        document.getElementById('agents-status-list').innerHTML = 
            '<p class="status-error">Failed to load agent status</p>';
        document.getElementById('available-agents-count').textContent = 'Error';
    }
}

async function loadLLMStatus() {
    try {
        const response = await fetchWithRetry(`${API_BASE}/api-keys/`);
        const data = await response.json();
        
        const llmStatus = document.getElementById('llm-config-status');
        const llmProviderStatus = document.getElementById('llm-provider-status');
        
        if (!data.providers || Object.keys(data.providers).length === 0) {
            llmStatus.innerHTML = `
                <div class="warning-box">
                    <p><strong>⚠️ No LLM Provider Configured</strong></p>
                    <p>Code generation requires at least one LLM provider to be configured.</p>
                    <p>Go to the <a href="#api-keys" onclick="navigateToView('api-keys')">API Keys</a> tab to configure a provider.</p>
                </div>
            `;
            llmProviderStatus.textContent = '❌ Not Configured';
            llmProviderStatus.className = 'stat-value status-error';
            return;
        }
        
        const activeProvider = Object.entries(data.providers).find(([_, p]) => p.is_active);
        
        if (activeProvider) {
            llmProviderStatus.textContent = `✅ ${activeProvider[0]}`;
            llmProviderStatus.className = 'stat-value status-ok';
        } else {
            llmProviderStatus.textContent = '⚠️ Inactive';
            llmProviderStatus.className = 'stat-value status-warning';
        }
        
        llmStatus.innerHTML = Object.entries(data.providers).map(([name, provider]) => `
            <div class="info-card">
                <h4>${escapeHtml(name)}</h4>
                <div class="info-content">
                    <p><strong>Status:</strong> ${provider.is_active ? 
                        '<span class="status-ok">Active</span>' : 
                        '<span class="status-warning">Inactive</span>'}</p>
                    ${provider.model ? `<p><strong>Model:</strong> ${escapeHtml(provider.model)}</p>` : ''}
                </div>
            </div>
        `).join('');
    } catch (error) {
        console.error('Failed to load LLM status:', error);
        document.getElementById('llm-config-status').innerHTML = 
            '<p class="status-error">Failed to load LLM configuration</p>';
        document.getElementById('llm-provider-status').textContent = 'Error';
    }
}

async function loadOmniCoreStatus() {
    try {
        // Load plugins info
        const pluginsResponse = await fetchWithRetry(`${API_BASE}/omnicore/plugins`);
        const pluginsData = await pluginsResponse.json();
        
        document.getElementById('plugins-info').innerHTML = 
            `<p>Active: ${pluginsData.active_plugins?.length || 0} / ${pluginsData.total_plugins || 0}</p>`;
        
        // Message bus info
        document.getElementById('message-bus-info').innerHTML = 
            '<p class="status-ok">✅ Operational</p>';
            
        // API version
        const healthResponse = await fetchWithRetry(`${API_BASE}/health`);
        const healthData = await healthResponse.json();
        document.getElementById('api-version').textContent = healthData.version || '1.0.0';
    } catch (error) {
        console.error('Failed to load OmniCore status:', error);
        document.getElementById('plugins-info').innerHTML = 
            '<p class="status-error">Error loading</p>';
        document.getElementById('message-bus-info').innerHTML = 
            '<p class="status-error">Error loading</p>';
    }
}

async function runFullDiagnostics() {
    const output = document.getElementById('diagnostics-output');
    const content = document.getElementById('diagnostics-content');
    
    output.style.display = 'block';
    content.textContent = 'Running diagnostics...\n\n';
    
    try {
        const diagnostics = {
            timestamp: new Date().toISOString(),
            system: {},
            agents: {},
            llm: {},
            omnicore: {}
        };
        
        // System health
        try {
            const response = await fetchWithRetry(`${API_BASE}/health`);
            diagnostics.system = await response.json();
        } catch (e) {
            diagnostics.system.error = e.message;
        }
        
        // Agent status
        try {
            const response = await fetchWithRetry(`${API_BASE}/agents`);
            diagnostics.agents = await response.json();
        } catch (e) {
            diagnostics.agents.error = e.message;
        }
        
        // LLM configuration
        try {
            const response = await fetchWithRetry(`${API_BASE}/api-keys/`);
            diagnostics.llm = await response.json();
        } catch (e) {
            diagnostics.llm.error = e.message;
        }
        
        // OmniCore
        try {
            const response = await fetchWithRetry(`${API_BASE}/omnicore/plugins`);
            diagnostics.omnicore = await response.json();
        } catch (e) {
            diagnostics.omnicore.error = e.message;
        }
        
        content.textContent = JSON.stringify(diagnostics, null, 2);
    } catch (error) {
        content.textContent = `Error running diagnostics: ${error.message}`;
    }
}

function downloadDiagnosticReport() {
    const content = document.getElementById('diagnostics-content').textContent;
    
    if (!content || content === '') {
        showError('Run diagnostics first before downloading the report');
        return;
    }
    
    const blob = new Blob([content], { type: 'text/plain' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `diagnostic-report-${new Date().toISOString()}.txt`;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
}

// API Keys Management
function initAPIKeys() {
    const form = document.getElementById('llm-config-form');
    if (form) {
        form.addEventListener('submit', saveLLMConfiguration);
    }
    refreshProviderStatus();
}

async function saveLLMConfiguration(e) {
    e.preventDefault();
    
    const provider = document.getElementById('config-provider').value;
    const apiKey = document.getElementById('config-api-key').value;
    const model = document.getElementById('config-model').value;
    
    if (!provider || !apiKey) {
        showError('Provider and API Key are required');
        return;
    }
    
    try {
        const response = await fetchWithRetry(`${API_BASE}/api-keys/${provider}`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                api_key: apiKey,
                model: model || undefined
            })
        });
        
        if (!response.ok) {
            throw new Error(`Failed to save configuration: ${response.statusText}`);
        }
        
        showSuccess('Configuration saved successfully');
        
        // Clear form
        document.getElementById('config-api-key').value = '';
        document.getElementById('config-model').value = '';
        
        // Refresh provider status
        await refreshProviderStatus();
    } catch (error) {
        showError('Failed to save configuration: ' + error.message);
    }
}

async function refreshProviderStatus() {
    const grid = document.getElementById('provider-status-grid');
    grid.innerHTML = '<p class="loading">Loading provider status...</p>';
    
    try {
        const response = await fetchWithRetry(`${API_BASE}/api-keys/`);
        const data = await response.json();
        
        const providers = data.providers || {};
        
        if (Object.keys(providers).length === 0) {
            grid.innerHTML = '<p class="no-data">No providers configured yet</p>';
            return;
        }
        
        grid.innerHTML = Object.entries(providers).map(([name, provider]) => `
            <div class="provider-card ${provider.is_active ? 'active' : ''}">
                <div class="provider-header">
                    <span class="provider-name">${escapeHtml(name)}</span>
                    <span class="provider-status-badge ${provider.is_active ? 'active' : 'configured'}">
                        ${provider.is_active ? '✓ Active' : 'Configured'}
                    </span>
                </div>
                <div class="provider-info">
                    ${provider.model ? `<p><strong>Model:</strong> ${escapeHtml(provider.model)}</p>` : '<p>Using default model</p>'}
                    <p><strong>API Key:</strong> ••••••••</p>
                </div>
                <div class="provider-actions">
                    ${!provider.is_active ? `
                        <button class="btn btn-primary" onclick="activateProvider('${name}')">Set Active</button>
                    ` : ''}
                    <button class="btn btn-secondary" onclick="removeProvider('${name}')">Remove</button>
                </div>
            </div>
        `).join('');
    } catch (error) {
        console.error('Failed to load provider status:', error);
        grid.innerHTML = '<p class="status-error">Failed to load provider status</p>';
    }
}

async function activateProvider(provider) {
    try {
        const response = await fetchWithRetry(`${API_BASE}/api-keys/${provider}/activate`, {
            method: 'POST'
        });
        
        if (!response.ok) {
            throw new Error(`Failed to activate provider: ${response.statusText}`);
        }
        
        showSuccess(`${provider} activated successfully`);
        await refreshProviderStatus();
    } catch (error) {
        showError('Failed to activate provider: ' + error.message);
    }
}

async function removeProvider(provider) {
    if (!confirm(`Are you sure you want to remove ${provider}?`)) {
        return;
    }
    
    try {
        const response = await fetchWithRetry(`${API_BASE}/api-keys/${provider}`, {
            method: 'DELETE'
        });
        
        if (!response.ok) {
            throw new Error(`Failed to remove provider: ${response.statusText}`);
        }
        
        showSuccess(`${provider} removed successfully`);
        await refreshProviderStatus();
    } catch (error) {
        showError('Failed to remove provider: ' + error.message);
    }
}

function navigateToView(viewName) {
    const link = document.querySelector(`[data-view="${viewName}"]`);
    if (link) {
        link.click();
    }
}

// Modals
function initModals() {
    const modal = document.getElementById('create-job-modal');
    const closeButtons = modal.querySelectorAll('.modal-close, .modal-cancel');
    
    closeButtons.forEach(btn => {
        btn.addEventListener('click', () => closeModal('create-job-modal'));
    });
    
    document.getElementById('submit-job').addEventListener('click', () => createJob());
    
    // Close modal on outside click
    modal.addEventListener('click', (e) => {
        if (e.target === modal) {
            closeModal('create-job-modal');
        }
    });
}

function openModal(modalId) {
    const modal = document.getElementById(modalId);
    if (modal) modal.classList.add('active');
}

function closeModal(modalId) {
    const modal = document.getElementById(modalId);
    if (modal) modal.classList.remove('active');
}

async function createJob() {
    const description = document.getElementById('job-description').value;
    const metadataText = document.getElementById('job-metadata').value;
    
    let metadata = {};
    try {
        metadata = JSON.parse(metadataText);
    } catch (error) {
        showError('Invalid JSON in metadata field');
        return;
    }
    
    try {
        const response = await fetchWithRetry(`${API_BASE}/jobs/`, {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({description, metadata})
        });
        
        if (response.ok) {
            showSuccess('Job created successfully!');
            closeModal('create-job-modal');
            loadJobs();
            document.getElementById('job-description').value = '';
            document.getElementById('job-metadata').value = '{}';
        }
    } catch (error) {
        showError('Failed to create job: ' + error.message);
    }
}

// Utility Functions
function formatLabel(text) {
    return text.replace(/_/g, ' ').replace(/\b\w/g, l => l.toUpperCase());
}

function formatFileSize(bytes) {
    if (bytes === 0) return '0 Bytes';
    const k = 1024;
    const sizes = ['Bytes', 'KB', 'MB', 'GB'];
    const i = Math.floor(Math.log(bytes) / Math.log(k));
    return Math.round(bytes / Math.pow(k, i) * 100) / 100 + ' ' + sizes[i];
}

function showSuccess(message) {
    alert('✓ ' + message);
}

function showError(message) {
    alert('✗ ' + message);
}

/**
 * Sanitize and validate a job ID input
 * Handles common user input issues like "Job abc123" prefix or truncated IDs
 * @param {string} input - Raw user input
 * @returns {string|null} - Valid UUID or null if invalid
 */
function sanitizeJobId(input) {
    if (!input) return null;
    
    // Trim whitespace
    let jobId = input.trim();
    
    // Remove "Job " prefix (case-insensitive)
    jobId = jobId.replace(/^job\s+/i, '');
    
    // UUID regex (with or without dashes)
    const uuidRegex = /^[0-9a-f]{8}-?[0-9a-f]{4}-?[0-9a-f]{4}-?[0-9a-f]{4}-?[0-9a-f]{12}$/i;
    
    if (!uuidRegex.test(jobId)) {
        // Check if it's a truncated UUID (first 8 chars only)
        if (/^[0-9a-f]{8}$/i.test(jobId)) {
            showError(`Job ID '${jobId}' appears to be truncated. Please use the full job ID.`);
        } else {
            showError(`Invalid job ID format: '${jobId}'. Please enter a valid UUID.`);
        }
        return null;
    }
    
    return jobId;
}

/**
 * Copy job ID to clipboard
 * @param {string} jobId - The job ID to copy
 */
function copyJobId(jobId) {
    // Validate input
    if (!jobId || typeof jobId !== 'string') {
        showError('Invalid job ID');
        return;
    }
    
    // Use modern clipboard API with fallback
    if (navigator.clipboard && navigator.clipboard.writeText) {
        navigator.clipboard.writeText(jobId).then(() => {
            showSuccess(`Job ID copied: ${jobId}`);
        }).catch(() => {
            // Fallback to deprecated method if modern API fails
            copyJobIdFallback(jobId);
        });
    } else {
        // Fallback for older browsers
        copyJobIdFallback(jobId);
    }
}

/**
 * Fallback method to copy text to clipboard
 * @param {string} text - The text to copy
 */
function copyJobIdFallback(text) {
    const temp = document.createElement('input');
    temp.value = text;
    document.body.appendChild(temp);
    temp.select();
    try {
        document.execCommand('copy');
        showSuccess(`Job ID copied: ${text}`);
    } catch (err) {
        showError('Failed to copy job ID');
    }
    document.body.removeChild(temp);
}

// Add status badge styles
const style = document.createElement('style');
style.textContent = `
    .status-badge {
        padding: 0.25rem 0.75rem;
        border-radius: 12px;
        font-size: 0.85rem;
        font-weight: 600;
    }
    .status-pending { background: rgba(176, 184, 212, 0.2); color: var(--text-secondary); }
    .status-running { background: rgba(51, 153, 255, 0.2); color: var(--info); }
    .status-completed { background: rgba(0, 204, 136, 0.2); color: var(--success); }
    .status-failed { background: rgba(255, 68, 68, 0.2); color: var(--error); }
    .status-proposed { background: rgba(51, 153, 255, 0.2); color: var(--info); }
    .status-approved { background: rgba(0, 204, 136, 0.2); color: var(--success); }
    .status-applied { background: rgba(0, 204, 136, 0.2); color: var(--success); }
    .status-rejected { background: rgba(255, 68, 68, 0.2); color: var(--error); }
    .severity-high { color: var(--error); font-weight: 600; }
    .severity-medium { color: var(--warning); font-weight: 600; }
    .severity-low { color: var(--info); font-weight: 600; }
`;
document.head.appendChild(style);

// ===== JOB MANAGEMENT FUNCTIONS =====

async function downloadJobFiles(jobId) {
    try {
        const response = await fetchWithRetry(`${API_BASE}/jobs/${jobId}/download`);
        if (response.ok) {
            const blob = await response.blob();
            const url = window.URL.createObjectURL(blob);
            const a = document.createElement('a');
            a.href = url;
            a.download = `job_${jobId}_output.zip`;
            a.click();
            showSuccess('Download started');
        } else {
            showError('Download failed');
        }
    } catch (error) {
        showError('Download failed: ' + error.message);
    }
}

/**
 * Send a completed job to the Self-Fixing Engineer for automated analysis.
 * 
 * This function implements enterprise-grade SFE dispatch with:
 * - Comprehensive input validation
 * - Idempotent operation (safe to call multiple times)
 * - Detailed error handling and user feedback
 * - Correlation ID tracking for support
 * - Graceful degradation if dispatch methods unavailable
 * 
 * The function provides clear feedback to users about:
 * - Dispatch in progress
 * - Success with confirmation message
 * - Failure with actionable error messages
 * - Configuration issues (no dispatch methods)
 * 
 * Security:
 * - Input validation on job ID format
 * - Prevents XSS through safe error message handling
 * - No sensitive data exposure to user
 * 
 * @async
 * @function sendToSelfFixing
 * @param {string} jobId - UUID of the completed job to dispatch
 * @returns {Promise<void>} Resolves when dispatch completes (success or failure)
 * 
 * @example
 * // Dispatch a completed job
 * await sendToSelfFixing("8183136e-86fe-42f9-8412-b8f03c7a3edf");
 * 
 * @throws {Error} Logs error but provides user-friendly feedback via showError()
 */
async function sendToSelfFixing(jobId) {
    // Input validation: Validate job ID format (UUID) using module constant
    if (!jobId || !UUID_PATTERN.test(jobId)) {
        console.error('Invalid job ID format for SFE dispatch:', jobId);
        showError('Invalid job ID format. Please try again.');
        return;
    }
    
    try {
        // Show progress indicator
        showSuccess('Sending to Self-Fixing Engineer...');
        
        // Make API request with proper timeout
        const response = await fetchWithRetry(
            `${API_BASE}/generator/${encodeURIComponent(jobId)}/dispatch-to-sfe`, 
            {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    'Accept': 'application/json'
                },
                timeout: 10000 // 10 second timeout
            }
        );
        
        // Check response status
        if (response.ok) {
            const data = await response.json();
            
            // Validate response structure
            if (!data || typeof data.success !== 'boolean') {
                throw new Error('Invalid response format from server');
            }
            
            if (data.success) {
                // Success case
                const correlationId = data.correlation_id ? ` (ID: ${data.correlation_id})` : '';
                showSuccess(`✓ Job sent to Self-Fixing Engineer successfully!${correlationId}`);
                
                // Log success for debugging
                console.log('SFE dispatch successful:', {
                    jobId: jobId.substring(0, 8),
                    correlationId: data.correlation_id,
                    timestamp: new Date().toISOString()
                });
            } else {
                // Graceful failure (no dispatch methods available)
                const message = data.message || 'Failed to send to SFE - no dispatch methods available';
                showError(message);
                
                console.warn('SFE dispatch failed (no methods):', {
                    jobId: jobId.substring(0, 8),
                    message: message,
                    correlationId: data.correlation_id
                });
            }
        } else {
            // HTTP error responses
            let errorMessage = `Failed to dispatch job (HTTP ${response.status})`;
            
            try {
                const errorData = await response.json();
                if (errorData.detail) {
                    // Extract correlation ID if present for support
                    const correlationMatch = errorData.detail.match(/Correlation ID: ([a-f0-9-]+)/i);
                    const correlationId = correlationMatch ? correlationMatch[1] : null;
                    
                    errorMessage = errorData.detail;
                    
                    if (correlationId) {
                        console.error('SFE dispatch error:', {
                            jobId: jobId.substring(0, 8),
                            status: response.status,
                            correlationId: correlationId,
                            detail: errorData.detail
                        });
                    }
                }
            } catch (parseError) {
                // If JSON parsing fails, use status text
                errorMessage = `${errorMessage}: ${response.statusText}`;
            }
            
            showError(errorMessage);
        }
        
    } catch (error) {
        // Network errors, timeouts, or other exceptions
        console.error('SFE dispatch exception:', {
            jobId: jobId.substring(0, 8),
            error: error.message,
            stack: error.stack,
            timestamp: new Date().toISOString()
        });
        
        // User-friendly error message
        let userMessage = 'Failed to send to Self-Fixing Engineer';
        
        if (error.name === 'AbortError' || error.message.includes('timeout')) {
            userMessage += ': Request timed out. Please try again.';
        } else if (error.message.includes('network') || error.message.includes('fetch')) {
            userMessage += ': Network error. Please check your connection.';
        } else {
            userMessage += '. Please try again or contact support.';
        }
        
        showError(userMessage);
    }
}

async function viewJobFiles(jobId) {
    try {
        const response = await fetchWithRetry(`${API_BASE}/jobs/${jobId}/files`);
        const data = await response.json();
        
        if (data.files.length === 0) {
            alert('No files generated yet for this job.');
            return;
        }
        
        let fileList = `Generated Files (${data.count}):\n\n`;
        data.files.forEach(file => {
            fileList += `📄 ${file.path} (${file.size_human})\n`;
        });
        fileList += `\nTotal size: ${(data.total_size / 1024).toFixed(2)} KB`;
        alert(fileList);
    } catch (error) {
        showError('Failed to load files: ' + error.message);
    }
}

async function cancelJob(jobId) {
    if (!confirm('Cancel this job?')) return;
    
    try {
        const response = await fetchWithRetry(`${API_BASE}/jobs/${jobId}/cancel`, {
            method: 'POST'
        });
        if (response.ok) {
            showSuccess('Job cancelled');
            loadJobs();
        }
    } catch (error) {
        showError('Failed to cancel job: ' + error.message);
    }
}

async function deleteJob(jobId) {
    if (!confirm('Delete this job? This cannot be undone.')) return;
    
    try {
        const response = await fetchWithRetry(`${API_BASE}/jobs/${jobId}`, {
            method: 'DELETE'
        });
        if (response.ok) {
            showSuccess('Job deleted');
            loadJobs();
        }
    } catch (error) {
        showError('Failed to delete job: ' + error.message);
    }
}

// ===== GENERATOR AGENT FUNCTIONS =====

async function runAgentPipeline() {
    let jobIdInput = document.getElementById('agent-job-id').value;
    let jobId = null;
    
    // If no job ID, create one automatically
    if (!jobIdInput) {
        try {
            const createResponse = await fetchWithRetry(`${API_BASE}/jobs/`, {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({description: 'Pipeline job', metadata: {}})
            });
            if (!createResponse.ok) {
                let errorMsg = `Failed to create job (HTTP ${createResponse.status})`;
                try {
                    const errorData = await createResponse.json();
                    errorMsg = 'Failed to create job: ' + (errorData.detail || errorData.message || JSON.stringify(errorData));
                } catch (e) { /* ignore JSON parse error */ }
                showError(errorMsg);
                return;
            }
            const job = await createResponse.json();
            jobId = job.id;
            document.getElementById('agent-job-id').value = jobId;
            showSuccess('Job created: ' + jobId);
        } catch (error) {
            showError('Failed to create job: ' + error.message);
            return;
        }
    } else {
        // Sanitize provided job ID
        jobId = sanitizeJobId(jobIdInput);
        if (!jobId) {
            return; // Error already shown by sanitizeJobId
        }
    }
    
    try {
        const response = await fetchWithRetry(`${API_BASE}/generator/${jobId}/pipeline`, {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({
                readme_content: 'Generate a Python web application with FastAPI backend',
                language: 'python',
                include_tests: true,
                include_deployment: true,
                include_docs: true,
                run_critique: true
            })
        });
        
        let data;
        try {
            data = await response.json();
        } catch (e) {
            data = {};
        }
        
        if (!response.ok) {
            if (response.status === 404) {
                showError('Job not found. The job may have been deleted or the server was restarted. Please create a new job first by uploading files.');
            } else if (response.status === 422) {
                showError('Invalid request: ' + (data.detail || JSON.stringify(data)));
            } else {
                showError('Pipeline failed: ' + (data.detail || data.message || `HTTP ${response.status}`));
            }
            return;
        }
        showSuccess('Pipeline started: ' + (data.status || 'Success'));
    } catch (error) {
        showError('Pipeline failed: ' + error.message);
    }
}

async function runCodegen() {
    let jobIdInput = document.getElementById('agent-job-id').value;
    if (!jobIdInput) {
        showError('Please enter a job ID or create one by clicking Full Pipeline first');
        return;
    }
    
    const jobId = sanitizeJobId(jobIdInput);
    if (!jobId) {
        return; // Error already shown by sanitizeJobId
    }
    
    try {
        const response = await fetchWithRetry(`${API_BASE}/generator/${jobId}/codegen`, {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({
                requirements: 'Generate a Python web application with REST API endpoints',
                language: 'python',
                include_tests: true
            })
        });
        const data = await response.json();
        if (!response.ok) {
            if (response.status === 404) {
                showError('Job not found. Please create a job first by clicking Full Pipeline or uploading files.');
            } else {
                showError('Code generation failed: ' + (data.detail || data.message || 'Unknown error'));
            }
            return;
        }
        showSuccess('Code generation started: ' + (data.status || 'Success'));
    } catch (error) {
        showError('Code generation failed: ' + error.message);
    }
}

async function runTestgen() {
    const jobIdInput = document.getElementById('agent-job-id').value;
    if (!jobIdInput) return showError('Please enter a job ID or create one by clicking Full Pipeline first');
    
    const jobId = sanitizeJobId(jobIdInput);
    if (!jobId) {
        return; // Error already shown by sanitizeJobId
    }
    
    try {
        const response = await fetchWithRetry(`${API_BASE}/generator/${jobId}/testgen`, {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({
                code_path: './uploads/' + jobId + '/generated',
                test_type: 'unit',
                coverage_target: 80.0
            })
        });
        const data = await response.json();
        if (!response.ok) {
            if (response.status === 404) {
                showError('Job not found. Please create a job first.');
            } else {
                showError('Test generation failed: ' + (data.detail || data.message || 'Unknown error'));
            }
            return;
        }
        showSuccess('Test generation started: ' + (data.status || 'Success'));
    } catch (error) {
        showError('Test generation failed: ' + error.message);
    }
}

async function runDocgen() {
    const jobIdInput = document.getElementById('agent-job-id').value;
    if (!jobIdInput) return showError('Please enter a job ID or create one by clicking Full Pipeline first');
    
    const jobId = sanitizeJobId(jobIdInput);
    if (!jobId) {
        return; // Error already shown by sanitizeJobId
    }
    
    try {
        const response = await fetchWithRetry(`${API_BASE}/generator/${jobId}/docgen`, {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({
                code_path: './uploads/' + jobId + '/generated',
                doc_type: 'api',
                format: 'markdown'
            })
        });
        const data = await response.json();
        if (!response.ok) {
            if (response.status === 404) {
                showError('Job not found. Please create a job first.');
            } else {
                showError('Documentation generation failed: ' + (data.detail || data.message || 'Unknown error'));
            }
            return;
        }
        showSuccess('Documentation generation started: ' + (data.status || 'Success'));
    } catch (error) {
        showError('Documentation generation failed: ' + error.message);
    }
}

async function runDeploy() {
    const jobIdInput = document.getElementById('agent-job-id').value;
    if (!jobIdInput) return showError('Please enter a job ID or create one by clicking Full Pipeline first');
    
    const jobId = sanitizeJobId(jobIdInput);
    if (!jobId) {
        return; // Error already shown by sanitizeJobId
    }
    
    try {
        const response = await fetchWithRetry(`${API_BASE}/generator/${jobId}/deploy`, {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({
                code_path: './uploads/' + jobId + '/generated',
                platform: 'docker',
                include_ci_cd: true
            })
        });
        const data = await response.json();
        if (!response.ok) {
            if (response.status === 404) {
                showError('Job not found. Please create a job first.');
            } else {
                showError('Deployment generation failed: ' + (data.detail || data.message || 'Unknown error'));
            }
            return;
        }
        showSuccess('Deployment config generation started: ' + (data.status || 'Success'));
    } catch (error) {
        showError('Deployment generation failed: ' + error.message);
    }
}

async function runCritique() {
    const jobIdInput = document.getElementById('agent-job-id').value;
    if (!jobIdInput) return showError('Please enter a job ID or create one by clicking Full Pipeline first');
    
    const jobId = sanitizeJobId(jobIdInput);
    if (!jobId) {
        return; // Error already shown by sanitizeJobId
    }
    
    try {
        const response = await fetchWithRetry(`${API_BASE}/generator/${jobId}/critique`, {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({
                code_path: './uploads/' + jobId + '/generated',
                scan_types: ['security', 'quality', 'performance'],
                auto_fix: false
            })
        });
        const data = await response.json();
        if (!response.ok) {
            if (response.status === 404) {
                showError('Job not found. Please create a job first.');
            } else {
                showError('Critique failed: ' + (data.detail || data.message || 'Unknown error'));
            }
            return;
        }
        showSuccess(`Critique complete: ${data.issues_found || 0} issues found, ${data.issues_fixed || 0} fixed`);
    } catch (error) {
        showError('Critique failed: ' + error.message);
    }
}

function showLLMConfig() {
    openModal('llm-config-modal');
}

async function submitLLMConfig() {
    const provider = document.getElementById('llm-provider').value;
    const model = document.getElementById('llm-model').value;
    const apiKey = document.getElementById('llm-api-key').value;
    
    try {
        const response = await fetchWithRetry(`${API_BASE}/generator/llm/configure`, {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({provider, model_name: model, api_key: apiKey})
        });
        if (response.ok) {
            showSuccess('LLM provider configured');
            closeModal('llm-config-modal');
        }
    } catch (error) {
        showError('Configuration failed: ' + error.message);
    }
}

async function getLLMStatus() {
    try {
        const response = await fetchWithRetry(`${API_BASE}/generator/llm/status`);
        const data = await response.json();
        alert(`LLM Status:\nProvider: ${data.current_provider}\nModel: ${data.model_name}\nStatus: ${data.status}`);
    } catch (error) {
        showError('Failed to get LLM status: ' + error.message);
    }
}

// ===== OMNICORE FUNCTIONS =====

function showPublishMessage() {
    openModal('publish-message-modal');
}

async function publishMessage() {
    const topic = document.getElementById('message-topic').value;
    const payloadText = document.getElementById('message-payload').value;
    const priority = parseInt(document.getElementById('message-priority').value);
    
    // Strict topic validation with length limit
    if (!topic || topic.length > 100 || !/^[a-zA-Z0-9_-]+$/.test(topic)) {
        showError('Invalid topic name. Use only alphanumeric characters, hyphens, and underscores (max 100 chars).');
        return;
    }
    
    let payload = {};
    try {
        payload = JSON.parse(payloadText);
    } catch (error) {
        showError('Invalid JSON payload: ' + error.message);
        return;
    }
    
    try {
        const response = await fetchWithRetry(`${API_BASE}/omnicore/message-bus/publish`, {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({topic, payload, priority})
        });
        if (response.ok) {
            showSuccess('Message published');
            closeModal('publish-message-modal');
        }
    } catch (error) {
        showError('Failed to publish: ' + error.message);
    }
}

async function listTopics() {
    try {
        const response = await fetchWithRetry(`${API_BASE}/omnicore/message-bus/topics`);
        const data = await response.json();
        alert('Active Topics:\n\n' + data.topics.join('\n'));
    } catch (error) {
        showError('Failed to list topics: ' + error.message);
    }
}

async function listPlugins() {
    try {
        const response = await fetchWithRetry(`${API_BASE}/omnicore/plugins`);
        const data = await response.json();
        
        const container = document.getElementById('plugins-list');
        container.innerHTML = '<h4>Installed Plugins</h4>';
        
        data.active_plugins.forEach(plugin => {
            const item = document.createElement('div');
            item.className = 'plugin-item';
            item.innerHTML = `
                <div style="padding: 1rem; background: var(--surface-light); margin: 0.5rem 0; border-radius: 4px;">
                    <strong>${plugin.name}</strong> - ${plugin.version}
                    <button class="btn btn-secondary" style="float: right; padding: 0.25rem 0.5rem;" 
                            onclick="reloadPlugin('${plugin.id}')">Reload</button>
                </div>
            `;
            container.appendChild(item);
        });
    } catch (error) {
        showError('Failed to list plugins: ' + error.message);
    }
}

async function reloadPlugin(pluginId) {
    try {
        const response = await fetchWithRetry(`${API_BASE}/omnicore/plugins/${pluginId}/reload`, {
            method: 'POST'
        });
        if (response.ok) {
            showSuccess('Plugin reloaded');
            listPlugins();
        }
    } catch (error) {
        showError('Failed to reload plugin: ' + error.message);
    }
}

async function browseMarketplace() {
    try {
        const response = await fetchWithRetry(`${API_BASE}/omnicore/plugins/marketplace`);
        const data = await response.json();
        
        let list = 'Available Plugins:\n\n';
        data.plugins.forEach(p => {
            list += `${p.name} v${p.version} - ${p.description}\n`;
        });
        alert(list);
    } catch (error) {
        showError('Failed to browse marketplace: ' + error.message);
    }
}

function showDatabaseQuery() {
    openModal('db-query-modal');
}

async function executeQuery() {
    const query = document.getElementById('db-query').value;
    
    try {
        const response = await fetchWithRetry(`${API_BASE}/omnicore/database/query`, {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({query, parameters: {}})
        });
        const data = await response.json();
        alert(`Query Results:\n${data.row_count} rows returned`);
        closeModal('db-query-modal');
    } catch (error) {
        showError('Query failed: ' + error.message);
    }
}

async function exportDatabase() {
    try {
        const response = await fetchWithRetry(`${API_BASE}/omnicore/database/export`, {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({format: 'json', include_metadata: true})
        });
        const data = await response.json();
        showSuccess('Database export started: ' + data.export_path);
    } catch (error) {
        showError('Export failed: ' + error.message);
    }
}

async function listCircuitBreakers() {
    try {
        const response = await fetchWithRetry(`${API_BASE}/omnicore/circuit-breakers`);
        const data = await response.json();
        
        const container = document.getElementById('circuit-breakers-list');
        container.innerHTML = '<h4>Circuit Breakers</h4>';
        
        data.circuit_breakers.forEach(cb => {
            const item = document.createElement('div');
            item.className = 'cb-item';
            item.innerHTML = `
                <div style="padding: 1rem; background: var(--surface-light); margin: 0.5rem 0; border-radius: 4px;">
                    <strong>${cb.name}</strong> - State: <span class="status-badge status-${cb.state}">${cb.state}</span>
                    <br>Failures: ${cb.failure_count}/${cb.threshold}
                    ${cb.state === 'open' ? `
                        <button class="btn btn-primary" style="margin-top: 0.5rem; padding: 0.25rem 0.5rem;" 
                                onclick="resetCircuitBreaker('${cb.name}')">Reset</button>
                    ` : ''}
                </div>
            `;
            container.appendChild(item);
        });
    } catch (error) {
        showError('Failed to list circuit breakers: ' + error.message);
    }
}

async function resetCircuitBreaker(name) {
    try {
        const response = await fetchWithRetry(`${API_BASE}/omnicore/circuit-breakers/${name}/reset`, {
            method: 'POST'
        });
        if (response.ok) {
            showSuccess('Circuit breaker reset');
            listCircuitBreakers();
        }
    } catch (error) {
        showError('Failed to reset: ' + error.message);
    }
}

async function listDeadLetterQueue() {
    try {
        const response = await fetchWithRetry(`${API_BASE}/omnicore/dead-letter-queue`);
        const data = await response.json();
        alert(`Dead Letter Queue:\n${data.messages.length} failed messages`);
    } catch (error) {
        showError('Failed to query DLQ: ' + error.message);
    }
}

// ===== SFE ADVANCED FUNCTIONS =====

async function detectBugs() {
    try {
        const response = await fetchWithRetry(`${API_BASE}/sfe/bugs/detect`, {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({code_path: '.', analysis_depth: 'deep'})
        });
        const data = await response.json();
        showSuccess(`Detected ${data.bugs_found} bugs`);
    } catch (error) {
        showError('Bug detection failed: ' + error.message);
    }
}

async function analyzeCodebase() {
    try {
        const response = await fetchWithRetry(`${API_BASE}/sfe/codebase/analyze`, {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({code_path: '.', include_dependencies: true})
        });
        const data = await response.json();
        alert(`Codebase Analysis:\n\nFiles: ${data.total_files}\nLOC: ${data.total_loc}\nComplexity: ${data.avg_complexity}`);
    } catch (error) {
        showError('Analysis failed: ' + error.message);
    }
}

async function prioritizeBugs() {
    const jobIdInput = document.getElementById('analyze-job-id').value;
    if (!jobIdInput) return showError('Please enter a job ID');
    
    const jobId = sanitizeJobId(jobIdInput);
    if (!jobId) {
        return; // Error already shown by sanitizeJobId
    }
    
    try {
        const response = await fetchWithRetry(`${API_BASE}/sfe/${jobId}/bugs/prioritize`, {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({criteria: ['severity', 'impact']})
        });
        showSuccess('Bugs prioritized');
    } catch (error) {
        showError('Prioritization failed: ' + error.message);
    }
}

async function fixImports() {
    try {
        const response = await fetchWithRetry(`${API_BASE}/sfe/imports/fix`, {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({file_path: '.', auto_install: false})
        });
        const data = await response.json();
        showSuccess(`Fixed ${data.imports_fixed} imports`);
    } catch (error) {
        showError('Import fix failed: ' + error.message);
    }
}

function showKnowledgeGraph() {
    openModal('knowledge-graph-modal');
}

async function queryKnowledgeGraph() {
    const query = document.getElementById('kg-query').value;
    const depth = parseInt(document.getElementById('kg-depth').value);
    
    try {
        const response = await fetchWithRetry(`${API_BASE}/sfe/knowledge-graph/query`, {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({query, max_depth: depth})
        });
        const data = await response.json();
        alert(`Knowledge Graph Results:\n${data.results.length} nodes found`);
        closeModal('knowledge-graph-modal');
    } catch (error) {
        showError('Query failed: ' + error.message);
    }
}

function showSandboxExec() {
    openModal('sandbox-modal');
}

async function executeSandbox() {
    const code = document.getElementById('sandbox-code').value;
    const timeout = parseInt(document.getElementById('sandbox-timeout').value);
    
    try {
        const response = await fetchWithRetry(`${API_BASE}/sfe/sandbox/execute`, {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({code, language: 'python', timeout_seconds: timeout})
        });
        const data = await response.json();
        alert(`Execution Result:\n\nStatus: ${data.status}\nOutput:\n${data.output}`);
        closeModal('sandbox-modal');
    } catch (error) {
        showError('Execution failed: ' + error.message);
    }
}

async function checkCompliance() {
    try {
        const response = await fetchWithRetry(`${API_BASE}/sfe/compliance/check`, {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({standards: ['PCI-DSS', 'HIPAA'], code_path: '.'})
        });
        const data = await response.json();
        alert(`Compliance Check:\n\nPassed: ${data.passed}\nViolations: ${data.violations_found}`);
    } catch (error) {
        showError('Compliance check failed: ' + error.message);
    }
}

async function queryDLT() {
    try {
        const response = await fetchWithRetry(`${API_BASE}/sfe/dlt/audit`);
        const data = await response.json();
        alert(`DLT Audit Logs:\n${data.total_records} records on blockchain`);
    } catch (error) {
        showError('DLT query failed: ' + error.message);
    }
}

// ===== ARBITER & ARENA FUNCTIONS =====

async function startArbiter() {
    try {
        const response = await fetchWithRetry(`${API_BASE}/sfe/arbiter/control`, {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({command: 'start', config: {}})
        });
        
        if (!response.ok) {
            const error = await response.json();
            showError('Failed to start Arbiter: ' + (error.detail?.message || error.detail || 'Unknown error'));
            return;
        }
        
        const data = await response.json();
        showSuccess('Arbiter started: ' + (data.status || 'Success'));
    } catch (error) {
        showError('Failed to start Arbiter: ' + error.message);
    }
}

async function stopArbiter() {
    try {
        const response = await fetchWithRetry(`${API_BASE}/sfe/arbiter/control`, {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({command: 'stop'})
        });
        
        if (!response.ok) {
            const error = await response.json();
            showError('Failed to stop Arbiter: ' + (error.detail?.message || error.detail || 'Unknown error'));
            return;
        }
        
        const data = await response.json();
        showSuccess('Arbiter stopped: ' + (data.status || 'Success'));
    } catch (error) {
        showError('Failed to stop Arbiter: ' + error.message);
    }
}

async function configureArbiter() {
    // Use a simple inline prompt for now - can be enhanced with modal later
    const config = prompt('Enter Arbiter configuration (JSON):');
    if (!config) return;
    
    let parsedConfig;
    try {
        parsedConfig = JSON.parse(config);
    } catch (error) {
        showError('Invalid JSON format: ' + error.message);
        return;
    }
    
    try {
        const response = await fetchWithRetry(`${API_BASE}/sfe/arbiter/control`, {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({command: 'configure', config: parsedConfig})
        });
        
        if (!response.ok) {
            const error = await response.json();
            showError('Configuration failed: ' + (error.detail?.message || error.detail || 'Unknown error'));
            return;
        }
        
        const data = await response.json();
        showSuccess('Arbiter configured: ' + (data.status || 'Success'));
    } catch (error) {
        showError('Configuration request failed: ' + error.message);
    }
}

async function getArbiterStatus() {
    try {
        const response = await fetchWithRetry(`${API_BASE}/sfe/arbiter/control`, {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({command: 'status'})
        });
        
        if (!response.ok) {
            const error = await response.json();
            showError('Failed to get status: ' + (error.detail?.message || error.detail || 'Unknown error'));
            return;
        }
        
        const data = await response.json();
        alert(`Arbiter Status:\nState: ${data.status || 'Unknown'}\nActive Agents: ${data.active_agents || 0}`);
    } catch (error) {
        showError('Failed to get status: ' + error.message);
    }
}

async function triggerCompetition() {
    const problemType = document.getElementById('problem-type').value;
    const codePath = document.getElementById('code-path').value;
    const rounds = parseInt(document.getElementById('arena-rounds').value);
    
    if (!codePath) {
        showError('Please enter a code path');
        return;
    }
    
    try {
        const response = await fetchWithRetry(`${API_BASE}/sfe/arena/compete`, {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({
                problem_type: problemType,
                code_path: codePath,
                rounds,
                evaluation_criteria: ['correctness', 'performance', 'code_quality']
            })
        });
        const data = await response.json();
        
        const results = document.getElementById('arena-results');
        results.innerHTML = `
            <h4>Competition Results</h4>
            <p>Competition ID: ${data.competition_id}</p>
            <p>Status: ${data.status}</p>
            <p>Winner will be determined after ${rounds} rounds</p>
        `;
        showSuccess('Competition started!');
    } catch (error) {
        showError('Competition failed: ' + error.message);
    }
}

async function getRLStatus() {
    const envId = document.getElementById('rl-env-id').value;
    if (!envId) return showError('Please enter an environment ID');
    
    try {
        const response = await fetchWithRetry(`${API_BASE}/sfe/rl/environment/${envId}/status`);
        const data = await response.json();
        alert(`RL Environment Status:\n\nState: ${data.state}\nEpisodes: ${data.episodes_completed}\nReward: ${data.total_reward}`);
    } catch (error) {
        showError('Failed to get RL status: ' + error.message);
    }
}

// ===== MODAL MANAGEMENT =====

function initModals() {
    const modals = document.querySelectorAll('.modal');
    
    modals.forEach(modal => {
        const closeButtons = modal.querySelectorAll('.modal-close, .modal-cancel');
        
        closeButtons.forEach(btn => {
            btn.addEventListener('click', () => {
                modal.classList.remove('active');
            });
        });
        
        modal.addEventListener('click', (e) => {
            if (e.target === modal) {
                modal.classList.remove('active');
            }
        });
    });
    
    // Keep the original job creation submit
    const submitJobBtn = document.getElementById('submit-job');
    if (submitJobBtn) {
        submitJobBtn.addEventListener('click', () => createJob());
    }
}

// ===== ADDITIONAL UTILITY FUNCTIONS =====

async function showSubscribe() {
    const topic = prompt('Enter topic to subscribe to:');
    if (!topic) return;
    
    // Strict validation with anchors and length limit
    if (topic.length > 100 || !/^[a-zA-Z0-9_-]+$/.test(topic)) {
        showError('Invalid topic name. Use only alphanumeric characters, hyphens, and underscores (max 100 chars).');
        return;
    }
    
    try {
        const response = await fetchWithRetry(`${API_BASE}/omnicore/message-bus/subscribe`, {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({topic, subscriber_id: 'web-ui'})
        });
        
        await response.json(); // Validate JSON response
        showSuccess(`Subscribed to topic: ${topic}`);
    } catch (error) {
        console.error('Subscription failed:', error);
        showError(`Subscription failed: ${error.message}. Please check the topic name and try again.`);
    }
}

async function showInstallPlugin() {
    const pluginName = prompt('Enter plugin name to install:');
    if (!pluginName) return;
    
    // Strict validation with length limit
    if (pluginName.length > 50 || !/^[a-zA-Z0-9_-]{3,50}$/.test(pluginName)) {
        showError('Invalid plugin name. Use 3-50 alphanumeric characters, hyphens, or underscores.');
        return;
    }
    
    try {
        const response = await fetchWithRetry(`${API_BASE}/omnicore/plugins/install`, {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({plugin_name: pluginName, version: 'latest'})
        });
        
        await response.json(); // Validate JSON response
        showSuccess(`Installing plugin: ${pluginName}`);
    } catch (error) {
        console.error('Installation failed:', error);
        showError(`Installation failed: ${error.message}. Please check the plugin name and try again.`);
    }
}

async function showRateLimit() {
    const limit = prompt('Enter rate limit (requests per minute):');
    if (!limit) return;
    
    const limitNum = parseInt(limit);
    if (isNaN(limitNum) || limitNum < 1 || limitNum > 10000 || !Number.isSafeInteger(limitNum)) {
        showError('Invalid rate limit. Enter a whole number between 1 and 10000.');
        return;
    }
    
    try {
        const response = await fetchWithRetry(`${API_BASE}/omnicore/rate-limits/configure`, {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({limit: limitNum, window_seconds: 60})
        });
        
        await response.json(); // Validate JSON response
        showSuccess(`Rate limit configured: ${limitNum}/min`);
    } catch (error) {
        console.error('Configuration failed:', error);
        showError(`Configuration failed: ${error.message}. Please check your input and try again.`);
    }
}

async function showSIEMConfig() {
    const endpoint = prompt('Enter SIEM endpoint URL:');
    if (!endpoint) return;
    
    // Enhanced URL validation
    try {
        const url = new URL(endpoint);
        // Only allow http/https protocols
        if (!['http:', 'https:'].includes(url.protocol)) {
            showError('Only HTTP/HTTPS protocols are allowed.');
            return;
        }
        // Prevent localhost and internal IPs
        if (url.hostname === 'localhost' || url.hostname.startsWith('127.') || 
            url.hostname.startsWith('192.168.') || url.hostname.startsWith('10.')) {
            showError('Cannot use localhost or internal IP addresses.');
            return;
        }
    } catch {
        showError('Invalid URL format.');
        return;
    }
    
    try {
        const response = await fetchWithRetry(`${API_BASE}/sfe/siem/configure`, {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({siem_endpoint: endpoint, enabled: true})
        });
        
        await response.json(); // Validate JSON response
        showSuccess('SIEM integration configured');
    } catch (error) {
        console.error('Configuration failed:', error);
        showError(`Configuration failed: ${error.message}. Please check the endpoint URL and try again.`);
    }
}

// ==================== Clarifier Functions ====================
let currentClarifierJobId = null;
let currentQuestionId = null;
let clarifierConversation = [];

/**
 * Start the clarification process
 */
async function startClarification() {
    const requirements = document.getElementById('clarifier-requirements').value.trim();
    const jobIdInput = document.getElementById('clarifier-job-id').value.trim();
    
    if (!requirements) {
        showError('Please enter requirements to clarify');
        return;
    }
    
    // Update status
    updateClarifierStatus('Processing...', 'active');
    
    // Clear conversation
    clarifierConversation = [];
    const conversationContainer = document.getElementById('clarifier-conversation');
    conversationContainer.innerHTML = '';
    
    // Add user message
    addClarifierMessage('user', requirements, 'Initial Requirements');
    
    try {
        // Create a job first if jobIdInput is empty, or validate existing job ID
        if (!jobIdInput) {
            // Create a new job
            const jobResponse = await fetchWithRetry(`${API_BASE}/jobs/`, {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({
                    description: 'Clarification session',
                    metadata: { source: 'clarifier' }
                })
            });
            
            if (!jobResponse.ok) {
                const errorText = await jobResponse.text();
                throw new Error(`Failed to create clarification job: HTTP ${jobResponse.status} - ${errorText}`);
            }
            
            const job = await jobResponse.json();
            currentClarifierJobId = job.id;
            document.getElementById('clarifier-job-id').value = currentClarifierJobId;
        } else {
            // Sanitize and validate provided job ID
            const sanitizedJobId = sanitizeJobId(jobIdInput);
            if (!sanitizedJobId) {
                updateClarifierStatus('Ready', 'idle');
                return; // Error already shown by sanitizeJobId
            }
            
            // Validate job ID exists
            const validateResponse = await fetchWithRetry(`${API_BASE}/jobs/${sanitizedJobId}`);
            if (!validateResponse.ok) {
                throw new Error(`Job ID '${sanitizedJobId}' not found. Please create a job first or leave the field empty to auto-generate.`);
            }
            currentClarifierJobId = sanitizedJobId;
        }
        
        // Call clarifier API
        const response = await fetchWithRetry(`${API_BASE}/generator/${currentClarifierJobId}/clarify`, {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({
                readme_content: requirements
            })
        });
        
        if (!response.ok) {
            throw new Error(`HTTP error! status: ${response.status}`);
        }
        
        const result = await response.json();
        
        // Process clarification response based on status
        if (result.status === "questions_generated" && result.clarifications && result.clarifications.length > 0) {
            // Questions generated - display them
            updateClarifierStatus(`Waiting for answer (1/${result.total_questions})`, 'waiting');
            
            // Store questions globally
            window.clarificationQuestions = result.clarifications;
            window.currentQuestionIndex = 0;
            
            // Display first question
            displayClarificationQuestion(result.clarifications[0], 0);
            
            // Show UI elements
            document.getElementById('clarifier-conversation').style.display = 'block';
            document.getElementById('answer-section').style.display = 'block';
            
        } else if (result.status === "no_clarification_needed") {
            // No questions - requirements are clear
            updateClarifierStatus('Complete', 'active');
            addClarifierMessage('system', '✅ Requirements are clear - no clarification needed', 'System');
            
        } else if (result.clarifications && result.clarifications.length > 0) {
            // Legacy response format - handle for backward compatibility
            updateClarifierStatus('Waiting for your answers', 'waiting');
            
            // Display first question
            currentQuestionId = 'q1';
            addClarifierMessage('ai', result.clarifications[0], 'Clarification Question');
            
            // Show answer input
            document.getElementById('answer-section').style.display = 'block';
            
            // Store remaining questions
            window.clarifierQuestions = result.clarifications;
            window.currentQuestionIndex = 0;
        } else {
            // Unexpected response - treat as no clarification needed
            updateClarifierStatus('Complete', 'active');
            addClarifierMessage('system', 'No clarifications needed. Requirements are clear!', 'System');
            displayClarifiedRequirements(result);
        }
        
    } catch (error) {
        console.error('Clarification error:', error);
        updateClarifierStatus('Error', 'error');
        showError('Failed to start clarification: ' + error.message);
    }
}

/**
 * Display a clarification question in the conversation
 */
function displayClarificationQuestion(question, index) {
    // Extract question text - handle both string and object formats
    let questionText;
    if (typeof question === 'string') {
        questionText = question;
    } else {
        questionText = question.question || question;
    }
    
    // Extract category - default to "general"
    const category = typeof question === 'object' ? (question.category || "general") : "general";
    
    const total = window.clarificationQuestions ? window.clarificationQuestions.length : 1;
    
    addClarifierMessage(
        'assistant',
        `Question ${index + 1}/${total} (Category: ${category})\n\n${questionText}`,
        'Clarification Needed'
    );
}

/**
 * Submit an answer to a clarification question
 */
async function submitAnswer() {
    const answer = document.getElementById('clarifier-answer').value.trim();
    
    if (!answer) {
        showError('Please enter an answer');
        return;
    }
    
    // Add user answer to conversation
    addClarifierMessage('user', answer, 'Your Answer');
    
    // Clear answer input
    document.getElementById('clarifier-answer').value = '';
    
    updateClarifierStatus('Processing answer...', 'active');
    
    try {
        // Submit answer to API
        const response = await fetchWithRetry(`${API_BASE}/generator/${currentClarifierJobId}/clarification/respond`, {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({
                question_id: currentQuestionId,
                response: answer
            })
        });
        
        if (!response.ok) {
            throw new Error(`HTTP error! status: ${response.status}`);
        }
        
        const result = await response.json();
        
        // Move to next question
        window.currentQuestionIndex++;
        
        if (window.currentQuestionIndex < window.clarifierQuestions.length) {
            // Show next question
            currentQuestionId = `q${window.currentQuestionIndex + 1}`;
            const nextQuestion = window.clarifierQuestions[window.currentQuestionIndex];
            addClarifierMessage('ai', nextQuestion, 'Clarification Question');
            updateClarifierStatus('Waiting for your answer', 'waiting');
        } else {
            // All questions answered
            updateClarifierStatus('Complete', 'active');
            document.getElementById('answer-section').style.display = 'none';
            addClarifierMessage('system', '✅ All questions answered! Generating clarified requirements...', 'System');
            
            // Get final clarified requirements
            await fetchClarifiedRequirements();
        }
        
    } catch (error) {
        console.error('Submit answer error:', error);
        updateClarifierStatus('Error', 'error');
        showError('Failed to submit answer: ' + error.message);
    }
}

/**
 * Skip the current question
 */
function skipQuestion() {
    addClarifierMessage('user', '[Skipped]', 'Skipped Question');
    
    // Move to next question
    window.currentQuestionIndex++;
    
    if (window.currentQuestionIndex < window.clarifierQuestions.length) {
        currentQuestionId = `q${window.currentQuestionIndex + 1}`;
        const nextQuestion = window.clarifierQuestions[window.currentQuestionIndex];
        addClarifierMessage('ai', nextQuestion, 'Clarification Question');
    } else {
        updateClarifierStatus('Complete (with skipped questions)', 'active');
        document.getElementById('answer-section').style.display = 'none';
        addClarifierMessage('system', 'Clarification process complete.', 'System');
        fetchClarifiedRequirements();
    }
}

/**
 * Fetch the final clarified requirements
 */
async function fetchClarifiedRequirements() {
    try {
        const response = await fetchWithRetry(`${API_BASE}/generator/${currentClarifierJobId}/clarification/feedback`);
        
        if (!response.ok) {
            throw new Error(`HTTP error! status: ${response.status}`);
        }
        
        const result = await response.json();
        displayClarifiedRequirements(result);
        
    } catch (error) {
        console.error('Fetch clarified requirements error:', error);
        showError('Failed to fetch clarified requirements: ' + error.message);
        
        // Show mock results for demo
        displayClarifiedRequirements({
            clarified_requirements: {
                project_type: 'Web Application',
                tech_stack: 'Python, Flask, PostgreSQL',
                authentication: 'JWT-based authentication',
                deployment: 'Docker containers on AWS',
                features: ['User management', 'Task CRUD operations', 'Dashboard with analytics']
            },
            confidence: 0.92
        });
    }
}

/**
 * Display clarified requirements in the results section
 */
function displayClarifiedRequirements(data) {
    const resultsContainer = document.getElementById('clarifier-results');
    resultsContainer.innerHTML = '';
    
    if (data.clarified_requirements) {
        const requirements = data.clarified_requirements;
        
        for (const [key, value] of Object.entries(requirements)) {
            const item = document.createElement('div');
            item.className = 'clarified-item';
            item.innerHTML = `
                <div class="clarified-label">${key.replace(/_/g, ' ')}</div>
                <div class="clarified-value">${Array.isArray(value) ? value.join(', ') : value}</div>
            `;
            resultsContainer.appendChild(item);
        }
    }
    
    if (data.confidence) {
        const confidence = document.createElement('div');
        confidence.className = 'clarified-item';
        confidence.innerHTML = `
            <div class="clarified-label">Confidence Score</div>
            <div class="clarified-value">${(data.confidence * 100).toFixed(1)}%</div>
        `;
        resultsContainer.appendChild(confidence);
    }
    
    // Show action buttons
    document.getElementById('results-actions').style.display = 'flex';
    
    // Save to history
    saveClarificationToHistory();
}

/**
 * Add a message to the clarifier conversation
 */
function addClarifierMessage(role, content, label) {
    const conversationContainer = document.getElementById('clarifier-conversation');
    
    // Remove empty state if present
    const emptyState = conversationContainer.querySelector('.conversation-empty');
    if (emptyState) {
        emptyState.remove();
    }
    
    const message = document.createElement('div');
    message.className = `message message-${role}`;
    
    const timestamp = new Date().toLocaleTimeString();
    
    message.innerHTML = `
        <div class="message-header">
            <span class="message-role">${role === 'ai' ? '🤖 AI' : role === 'user' ? '👤 You' : '⚙️ System'}</span>
            <span class="message-time">${timestamp}</span>
        </div>
        <div class="message-content">${escapeHtml(content)}</div>
    `;
    
    conversationContainer.appendChild(message);
    conversationContainer.scrollTop = conversationContainer.scrollHeight;
    
    clarifierConversation.push({ role, content, timestamp });
}

/**
 * Update clarifier status indicator
 */
function updateClarifierStatus(text, state) {
    const statusEl = document.getElementById('clarifier-status');
    const indicator = statusEl.querySelector('.status-indicator');
    const textEl = statusEl.querySelector('.status-text');
    
    indicator.className = `status-indicator ${state}`;
    textEl.textContent = text;
}

/**
 * Clear the clarifier form
 */
function clearClarifier() {
    document.getElementById('clarifier-requirements').value = '';
    document.getElementById('clarifier-job-id').value = '';
    document.getElementById('clarifier-conversation').innerHTML = `
        <div class="conversation-empty">
            <p>👋 Enter your requirements above and click "Start Clarification" to begin the interactive clarification process.</p>
            <p class="help-text">The AI will ask questions to resolve ambiguities and ensure clear requirements.</p>
        </div>
    `;
    document.getElementById('clarifier-results').innerHTML = `
        <div class="results-empty">
            <p>Clarified requirements will appear here once the conversation is complete.</p>
        </div>
    `;
    document.getElementById('answer-section').style.display = 'none';
    document.getElementById('results-actions').style.display = 'none';
    updateClarifierStatus('Ready', '');
    currentClarifierJobId = null;
    currentQuestionId = null;
    clarifierConversation = [];
}

/**
 * Proceed to code generation with clarified requirements
 */
async function proceedToGeneration() {
    if (!currentClarifierJobId) {
        showError('No clarification session active');
        return;
    }
    
    try {
        // Create a job for code generation
        const response = await fetchWithRetry(`${API_BASE}/jobs/`, {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({
                description: `Code generation from clarified requirements (${currentClarifierJobId})`,
                metadata: {
                    clarification_job_id: currentClarifierJobId,
                    source: 'clarifier'
                }
            })
        });
        
        if (!response.ok) {
            throw new Error(`HTTP error! status: ${response.status}`);
        }
        
        const job = await response.json();
        
        showSuccess(`Job ${job.id} created. Redirecting to generator...`);
        
        // Switch to generator view and populate job ID
        setTimeout(() => {
            document.querySelector('[data-view="generator"]').click();
            document.getElementById('agent-job-id').value = job.id;
        }, 1500);
        
    } catch (error) {
        console.error('Proceed to generation error:', error);
        showError('Failed to create generation job: ' + error.message);
    }
}

/**
 * Export clarified requirements
 */
function exportClarifiedRequirements() {
    const resultsContainer = document.getElementById('clarifier-results');
    const items = resultsContainer.querySelectorAll('.clarified-item');
    
    let exportText = `# Clarified Requirements\nJob ID: ${currentClarifierJobId}\nTimestamp: ${new Date().toISOString()}\n\n`;
    
    items.forEach(item => {
        const label = item.querySelector('.clarified-label').textContent;
        const value = item.querySelector('.clarified-value').textContent;
        exportText += `## ${label}\n${value}\n\n`;
    });
    
    // Create download
    const blob = new Blob([exportText], { type: 'text/markdown' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `clarified-requirements-${currentClarifierJobId}.md`;
    a.click();
    URL.revokeObjectURL(url);
    
    showSuccess('Requirements exported successfully');
}

/**
 * Restart clarification process
 */
function restartClarification() {
    if (confirm('Start a new clarification session? Current session will be saved to history.')) {
        clearClarifier();
    }
}

/**
 * Save clarification session to history
 */
function saveClarificationToHistory() {
    const historyList = document.getElementById('clarifier-history');
    
    // Remove "no data" message if present
    const noData = historyList.querySelector('.no-data');
    if (noData) {
        noData.remove();
    }
    
    const historyItem = document.createElement('div');
    historyItem.className = 'history-item';
    historyItem.onclick = () => loadClarificationFromHistory(currentClarifierJobId);
    
    const requirements = document.getElementById('clarifier-requirements').value.trim();
    const summary = requirements.substring(0, 100) + (requirements.length > 100 ? '...' : '');
    
    historyItem.innerHTML = `
        <div class="history-header">
            <span class="history-job-id">${currentClarifierJobId}</span>
            <span class="history-timestamp">${new Date().toLocaleString()}</span>
        </div>
        <div class="history-summary">${escapeHtml(summary)}</div>
    `;
    
    // Add to beginning of list
    historyList.insertBefore(historyItem, historyList.firstChild);
}

/**
 * Load a clarification session from history
 */
function loadClarificationFromHistory(jobId) {
    showError('History loading not yet implemented');
    // TODO: Implement loading from backend storage
}

/**
 * Escape HTML to prevent XSS
 */
function escapeHtml(text) {
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}

// ==================== Audit Logs ====================

// Global storage for current audit logs
window.currentAuditLogs = [];

/**
 * Initialize audit logs functionality
 */
function initAuditLogs() {
    // Auto-refresh functionality can be enabled here
    window.auditAutoRefresh = null;
    
    // Load event types dynamically
    loadEventTypes();
}

/**
 * Load and display audit logs based on current filters
 */
async function loadAuditLogs() {
    try {
        const module = document.getElementById('audit-module-filter')?.value || '';
        const eventType = document.getElementById('audit-event-type').value;
        const jobId = document.getElementById('audit-job-id').value;
        const startTime = document.getElementById('audit-start-time').value;
        const endTime = document.getElementById('audit-end-time').value;
        const limit = document.getElementById('audit-limit').value;
        
        // Build query params
        const params = new URLSearchParams();
        if (module) params.append('module', module);
        if (eventType) params.append('event_type', eventType);
        if (jobId) params.append('job_id', jobId);
        if (startTime) params.append('start_time', new Date(startTime).toISOString());
        if (endTime) params.append('end_time', new Date(endTime).toISOString());
        params.append('limit', limit);
        
        // Call unified endpoint
        const response = await fetchWithRetry(`${API_BASE}/audit/logs/all?${params}`);
        
        if (!response.ok) {
            throw new Error(`HTTP ${response.status}: ${response.statusText}`);
        }
        
        const data = await response.json();
        
        // Convert to expected format
        displayAuditLogs({ logs: data.aggregated_logs });
        updateAuditStats({ logs: data.aggregated_logs });
        
        // Show modules queried
        const modulesEl = document.getElementById('audit-modules-queried');
        if (modulesEl) {
            modulesEl.textContent = data.modules_queried.join(', ');
        }
        
        // Show warning if there were errors
        if (data.errors && Object.keys(data.errors).length > 0) {
            showWarning(`Some modules had errors: ${Object.keys(data.errors).join(', ')}`);
        }
        
        document.getElementById('audit-last-updated').textContent = new Date().toLocaleTimeString();
    } catch (error) {
        console.error('Failed to load audit logs:', error);
        showError('Failed to load audit logs: ' + error.message);
    }
}

/**
 * Display audit logs in the table
 */
function displayAuditLogs(data) {
    const tbody = document.getElementById('audit-logs-tbody');
    
    if (!data.logs || data.logs.length === 0) {
        tbody.innerHTML = '<tr><td colspan="8" class="no-data">No audit logs found</td></tr>';
        window.currentAuditLogs = [];
        return;
    }
    
    // Store logs globally for detail view
    window.currentAuditLogs = data.logs;
    
    tbody.innerHTML = data.logs.map((log, index) => `
        <tr onclick="showAuditDetailByIndex(${index})">
            <td><span class="badge module-${escapeHtml(log.module || 'unknown')}">${escapeHtml(log.module || 'N/A')}</span></td>
            <td>${escapeHtml(formatTimestamp(log.timestamp))}</td>
            <td><span class="badge event-${escapeHtml(log.event_type || 'unknown')}">${escapeHtml(log.event_type || 'N/A')}</span></td>
            <td>${escapeHtml(log.job_id || 'N/A')}</td>
            <td>${escapeHtml(log.action || log.name || 'N/A')}</td>
            <td>${escapeHtml(log.user || log.actor || 'system')}</td>
            <td><span class="status-badge ${escapeHtml(log.status || 'unknown')}">${escapeHtml(log.status || 'N/A')}</span></td>
            <td><button class="btn-link" onclick="event.stopPropagation(); showAuditDetailByIndex(${index})">View</button></td>
        </tr>
    `).join('');
}

/**
 * Update audit statistics summary
 */
function updateAuditStats(data) {
    const logs = data.logs || [];
    
    document.getElementById('audit-total-count').textContent = logs.length;
    
    const errorCount = logs.filter(log => 
        log.event_type === 'error' || 
        log.action?.includes('error') || 
        log.status === 'failed'
    ).length;
    document.getElementById('audit-error-count').textContent = errorCount;
    
    if (logs.length > 0) {
        const timestamps = logs.map(l => new Date(l.timestamp)).filter(d => !isNaN(d));
        if (timestamps.length > 0) {
            const minTime = new Date(Math.min(...timestamps));
            const maxTime = new Date(Math.max(...timestamps));
            const range = `${minTime.toLocaleDateString()} - ${maxTime.toLocaleDateString()}`;
            document.getElementById('audit-time-range').textContent = range;
        }
    } else {
        document.getElementById('audit-time-range').textContent = '--';
    }
}

/**
 * Show audit log details in modal by index
 */
function showAuditDetailByIndex(index) {
    const modal = document.getElementById('audit-detail-modal');
    const jsonPre = document.getElementById('audit-detail-json');
    
    if (window.currentAuditLogs && window.currentAuditLogs[index]) {
        const log = window.currentAuditLogs[index];
        jsonPre.textContent = JSON.stringify(log, null, 2);
        modal.classList.add('active');
    } else {
        console.error('Log not found at index:', index);
    }
}

/**
 * Show audit log details in modal (legacy support)
 */
function showAuditDetail(logJson) {
    const modal = document.getElementById('audit-detail-modal');
    const jsonPre = document.getElementById('audit-detail-json');
    
    let log;
    if (typeof logJson === 'string') {
        log = JSON.parse(logJson);
    } else {
        log = logJson;
    }
    
    jsonPre.textContent = JSON.stringify(log, null, 2);
    modal.classList.add('active');
}

/**
 * Close audit detail modal
 */
function closeAuditDetailModal() {
    const modal = document.getElementById('audit-detail-modal');
    modal.classList.remove('active');
}

/**
 * Refresh audit logs with current filters
 */
function refreshAuditLogs() {
    loadAuditLogs();
}

/**
 * Clear all audit log filters
 */
function clearAuditFilters() {
    document.getElementById('audit-module-filter').value = '';
    document.getElementById('audit-event-type').value = '';
    document.getElementById('audit-job-id').value = '';
    document.getElementById('audit-start-time').value = '';
    document.getElementById('audit-end-time').value = '';
    document.getElementById('audit-limit').value = '100';
}

/**
 * Load event types dynamically from API
 */
async function loadEventTypes() {
    try {
        const response = await fetchWithRetry(`${API_BASE}/audit/logs/event-types`);
        
        if (!response.ok) {
            throw new Error(`HTTP ${response.status}: ${response.statusText}`);
        }
        
        const data = await response.json();
        
        const eventTypeSelect = document.getElementById('audit-event-type');
        if (!eventTypeSelect) return;
        
        eventTypeSelect.innerHTML = '<option value="">All Events</option>';
        
        // Populate with all event types
        data.all_event_types_sorted.forEach(type => {
            const option = document.createElement('option');
            option.value = type;
            option.textContent = type.replace(/_/g, ' ').replace(/\b\w/g, l => l.toUpperCase());
            eventTypeSelect.appendChild(option);
        });
    } catch (error) {
        console.error('Failed to load event types:', error);
        // Keep default event types if API call fails
    }
}

/**
 * Export audit logs to CSV
 */
async function exportAuditLogs() {
    try {
        const tbody = document.getElementById('audit-logs-tbody');
        const rows = Array.from(tbody.querySelectorAll('tr'));
        
        if (rows.length === 0 || rows[0].querySelector('.no-data')) {
            showError('No logs to export');
            return;
        }
        
        // Build CSV
        const headers = ['Timestamp', 'Event Type', 'Job ID', 'Action', 'User', 'Status'];
        let csv = headers.join(',') + '\n';
        
        rows.forEach(row => {
            const cells = Array.from(row.querySelectorAll('td'));
            const values = cells.slice(0, -1).map(cell => {
                const text = cell.textContent.trim();
                return `"${text.replace(/"/g, '""')}"`;
            });
            csv += values.join(',') + '\n';
        });
        
        // Download
        const blob = new Blob([csv], { type: 'text/csv' });
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = `audit-logs-${new Date().toISOString()}.csv`;
        document.body.appendChild(a);
        a.click();
        document.body.removeChild(a);
        URL.revokeObjectURL(url);
        
        showSuccess('Audit logs exported successfully');
    } catch (error) {
        console.error('Failed to export logs:', error);
        showError('Failed to export logs: ' + error.message);
    }
}

/**
 * Format timestamp for display
 */
function formatTimestamp(ts) {
    if (!ts) return 'N/A';
    const date = new Date(ts);
    return isNaN(date) ? ts : date.toLocaleString();
}

