// A.S.E Platform - Main JavaScript
// by Novatrax Labs

const API_BASE = '/api';
let websocket = null;

// Initialize application
document.addEventListener('DOMContentLoaded', () => {
    initNavigation();
    initDashboard();
    initJobs();
    initGenerator();
    initSFE();
    initFixes();
    initSystem();
    initModals();
    
    // Load initial data
    loadHealthCheck();
    loadJobStats();
});

// Navigation
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
        const response = await fetch('/health');
        const data = await response.json();
        
        updateHealthIndicators(data.components);
        
        // Update API version
        const versionEl = document.getElementById('api-version');
        if (versionEl) versionEl.textContent = data.version;
    } catch (error) {
        console.error('Health check failed:', error);
        showError('Failed to load health status');
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
        const response = await fetch(`${API_BASE}/jobs/`);
        const data = await response.json();
        
        const total = data.total;
        const running = data.jobs.filter(j => j.status === 'running').length;
        const completed = data.jobs.filter(j => j.status === 'completed').length;
        
        document.getElementById('total-jobs').textContent = total;
        document.getElementById('running-jobs').textContent = running;
        document.getElementById('completed-jobs').textContent = completed;
    } catch (error) {
        console.error('Failed to load job stats:', error);
    }
}

// WebSocket Connection
function connectWebSocket() {
    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    const wsUrl = `${protocol}//${window.location.host}${API_BASE}/events/ws`;
    
    websocket = new WebSocket(wsUrl);
    
    websocket.onopen = () => {
        document.getElementById('stream-status').textContent = 'Connected';
        document.getElementById('stream-status').style.background = 'rgba(0, 204, 136, 0.2)';
        document.getElementById('stream-status').style.color = 'var(--success)';
        document.getElementById('connect-stream').disabled = true;
        document.getElementById('disconnect-stream').disabled = false;
        
        addEvent('System', 'Connected to event stream', 'info');
    };
    
    websocket.onmessage = (event) => {
        const data = JSON.parse(event.data);
        addEvent(data.event_type, data.message, data.severity);
        
        // Update stats if job event
        if (data.event_type.includes('job')) {
            loadJobStats();
        }
    };
    
    websocket.onerror = (error) => {
        console.error('WebSocket error:', error);
        addEvent('System', 'Connection error', 'error');
    };
    
    websocket.onclose = () => {
        document.getElementById('stream-status').textContent = 'Disconnected';
        document.getElementById('stream-status').style.background = 'rgba(176, 184, 212, 0.1)';
        document.getElementById('stream-status').style.color = 'var(--text-secondary)';
        document.getElementById('connect-stream').disabled = false;
        document.getElementById('disconnect-stream').disabled = true;
        
        addEvent('System', 'Disconnected from event stream', 'warning');
    };
}

function disconnectWebSocket() {
    if (websocket) {
        websocket.close();
        websocket = null;
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

async function loadJobs() {
    const container = document.getElementById('jobs-list');
    const statusFilter = document.getElementById('job-status-filter').value;
    
    container.innerHTML = '<p class="loading">Loading jobs...</p>';
    
    try {
        let url = `${API_BASE}/jobs/`;
        if (statusFilter) {
            url += `?status=${statusFilter}`;
        }
        
        const response = await fetch(url);
        const data = await response.json();
        
        if (data.jobs.length === 0) {
            container.innerHTML = '<p class="no-data">No jobs found</p>';
            return;
        }
        
        container.innerHTML = '';
        data.jobs.forEach(job => {
            const card = createJobCard(job);
            container.appendChild(card);
        });
    } catch (error) {
        console.error('Failed to load jobs:', error);
        container.innerHTML = '<p class="error">Failed to load jobs</p>';
    }
}

function createJobCard(job) {
    const card = document.createElement('div');
    card.className = 'job-card';
    card.innerHTML = `
        <div style="display: flex; justify-content: space-between; align-items: start;">
            <div>
                <h4>Job ${job.id.substring(0, 8)}</h4>
                <p style="color: var(--text-secondary); margin: 0.5rem 0;">
                    Created: ${new Date(job.created_at).toLocaleString()}
                </p>
                <p style="color: var(--text-secondary);">
                    Files: ${job.input_files.length}
                </p>
            </div>
            <div>
                <span class="status-badge status-${job.status}">${job.status}</span>
            </div>
        </div>
        <div style="margin-top: 1rem;">
            <button class="btn btn-secondary" onclick="viewJobDetails('${job.id}')">
                View Details
            </button>
        </div>
    `;
    return card;
}

async function viewJobDetails(jobId) {
    try {
        const response = await fetch(`${API_BASE}/jobs/${jobId}/progress`);
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
        const jobResponse = await fetch(`${API_BASE}/jobs/`, {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({description: 'File upload job', metadata: {}})
        });
        const job = await jobResponse.json();
        
        // Upload files
        const formData = new FormData();
        selectedFiles.forEach(file => formData.append('files', file));
        
        const uploadResponse = await fetch(`${API_BASE}/generator/${job.id}/upload`, {
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
    const jobId = document.getElementById('analyze-job-id').value;
    if (!jobId) {
        showError('Please enter a job ID');
        return;
    }
    
    try {
        const response = await fetch(`${API_BASE}/sfe/${jobId}/analyze`, {
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
        const response = await fetch(`${API_BASE}/sfe/${jobId}/errors`);
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
        const response = await fetch(`${API_BASE}/sfe/errors/${errorId}/propose-fix`, {
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
        const response = await fetch(`${API_BASE}/sfe/insights`);
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
        const response = await fetch(`${API_BASE}/fixes/`);
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
        const response = await fetch(`${API_BASE}/sfe/fixes/${fixId}/apply`, {
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
        const response = await fetch(`${API_BASE}/sfe/fixes/${fixId}/rollback`, {
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
    loadSystemInfo();
}

async function loadSystemInfo() {
    try {
        const response = await fetch(`${API_BASE}/omnicore/plugins`);
        const data = await response.json();
        
        document.getElementById('plugins-info').textContent = 
            `Active: ${data.active_plugins.length} / ${data.total_plugins}`;
    } catch (error) {
        console.error('Failed to load system info:', error);
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
        const response = await fetch(`${API_BASE}/jobs/`, {
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
