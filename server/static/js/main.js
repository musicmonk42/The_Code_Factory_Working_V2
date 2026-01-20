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
    
    const hasFiles = job.output_files && job.output_files.length > 0;
    const isCompleted = job.status === 'completed';
    const isRunning = job.status === 'running';
    
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
        <div style="margin-top: 1rem; display: flex; gap: 0.5rem; flex-wrap: wrap;">
            <button class="btn btn-secondary" onclick="viewJobDetails('${job.id}')">
                View Details
            </button>
            ${isCompleted && hasFiles ? `
                <button class="btn btn-primary" onclick="downloadJobFiles('${job.id}')">
                    ⬇️ Download
                </button>
            ` : ''}
            ${isCompleted || hasFiles ? `
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

// ===== JOB MANAGEMENT FUNCTIONS =====

async function downloadJobFiles(jobId) {
    try {
        const response = await fetch(`${API_BASE}/jobs/${jobId}/download`);
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

async function viewJobFiles(jobId) {
    try {
        const response = await fetch(`${API_BASE}/jobs/${jobId}/files`);
        const data = await response.json();
        
        let fileList = 'Generated Files:\n\n';
        data.files.forEach(file => {
            fileList += `📄 ${file.name} (${formatFileSize(file.size)})\n`;
        });
        alert(fileList);
    } catch (error) {
        showError('Failed to load files: ' + error.message);
    }
}

async function cancelJob(jobId) {
    if (!confirm('Cancel this job?')) return;
    
    try {
        const response = await fetch(`${API_BASE}/jobs/${jobId}/cancel`, {
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
        const response = await fetch(`${API_BASE}/jobs/${jobId}`, {
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
    const jobId = document.getElementById('agent-job-id').value;
    if (!jobId) {
        showError('Please enter a job ID');
        return;
    }
    
    try {
        const response = await fetch(`${API_BASE}/generator/${jobId}/pipeline`, {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({
                readme_content: 'Full pipeline execution',
                language: 'python',
                include_tests: true,
                include_deployment: true,
                include_docs: true,
                run_critique: true
            })
        });
        const data = await response.json();
        showSuccess('Pipeline started: ' + data.status);
    } catch (error) {
        showError('Pipeline failed: ' + error.message);
    }
}

async function runCodegen() {
    const jobId = document.getElementById('agent-job-id').value;
    if (!jobId) {
        showError('Please enter a job ID');
        return;
    }
    
    try {
        const response = await fetch(`${API_BASE}/generator/${jobId}/codegen`, {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({
                readme_content: 'Generate code from requirements',
                language: 'python'
            })
        });
        const data = await response.json();
        showSuccess('Code generation started');
    } catch (error) {
        showError('Code generation failed: ' + error.message);
    }
}

async function runTestgen() {
    const jobId = document.getElementById('agent-job-id').value;
    if (!jobId) return showError('Please enter a job ID');
    
    try {
        const response = await fetch(`${API_BASE}/generator/${jobId}/testgen`, {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({target_files: [], framework: 'pytest'})
        });
        showSuccess('Test generation started');
    } catch (error) {
        showError('Test generation failed: ' + error.message);
    }
}

async function runDocgen() {
    const jobId = document.getElementById('agent-job-id').value;
    if (!jobId) return showError('Please enter a job ID');
    
    try {
        const response = await fetch(`${API_BASE}/generator/${jobId}/docgen`, {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({target_files: [], format: 'markdown'})
        });
        showSuccess('Documentation generation started');
    } catch (error) {
        showError('Documentation generation failed: ' + error.message);
    }
}

async function runDeploy() {
    const jobId = document.getElementById('agent-job-id').value;
    if (!jobId) return showError('Please enter a job ID');
    
    try {
        const response = await fetch(`${API_BASE}/generator/${jobId}/deploy`, {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({platform: 'docker', include_ci_cd: true})
        });
        showSuccess('Deployment config generation started');
    } catch (error) {
        showError('Deployment generation failed: ' + error.message);
    }
}

async function runCritique() {
    const jobId = document.getElementById('agent-job-id').value;
    if (!jobId) return showError('Please enter a job ID');
    
    try {
        const response = await fetch(`${API_BASE}/generator/${jobId}/critique`, {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({target_files: [], check_security: true, check_quality: true})
        });
        const data = await response.json();
        alert(`Critique complete:\nIssues found: ${data.issues_found}\nSecurity: ${data.security_score}/100\nQuality: ${data.quality_score}/100`);
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
        const response = await fetch(`${API_BASE}/generator/llm/configure`, {
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
        const response = await fetch(`${API_BASE}/generator/llm/status`);
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
        const response = await fetch(`${API_BASE}/omnicore/message-bus/publish`, {
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
        const response = await fetch(`${API_BASE}/omnicore/message-bus/topics`);
        const data = await response.json();
        alert('Active Topics:\n\n' + data.topics.join('\n'));
    } catch (error) {
        showError('Failed to list topics: ' + error.message);
    }
}

async function listPlugins() {
    try {
        const response = await fetch(`${API_BASE}/omnicore/plugins`);
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
        const response = await fetch(`${API_BASE}/omnicore/plugins/${pluginId}/reload`, {
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
        const response = await fetch(`${API_BASE}/omnicore/plugins/marketplace`);
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
        const response = await fetch(`${API_BASE}/omnicore/database/query`, {
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
        const response = await fetch(`${API_BASE}/omnicore/database/export`, {
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
        const response = await fetch(`${API_BASE}/omnicore/circuit-breakers`);
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
        const response = await fetch(`${API_BASE}/omnicore/circuit-breakers/${name}/reset`, {
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
        const response = await fetch(`${API_BASE}/omnicore/dead-letter-queue`);
        const data = await response.json();
        alert(`Dead Letter Queue:\n${data.messages.length} failed messages`);
    } catch (error) {
        showError('Failed to query DLQ: ' + error.message);
    }
}

// ===== SFE ADVANCED FUNCTIONS =====

async function detectBugs() {
    try {
        const response = await fetch(`${API_BASE}/sfe/bugs/detect`, {
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
        const response = await fetch(`${API_BASE}/sfe/codebase/analyze`, {
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
    const jobId = document.getElementById('analyze-job-id').value;
    if (!jobId) return showError('Please enter a job ID');
    
    try {
        const response = await fetch(`${API_BASE}/sfe/${jobId}/bugs/prioritize`, {
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
        const response = await fetch(`${API_BASE}/sfe/imports/fix`, {
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
        const response = await fetch(`${API_BASE}/sfe/knowledge-graph/query`, {
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
        const response = await fetch(`${API_BASE}/sfe/sandbox/execute`, {
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
        const response = await fetch(`${API_BASE}/sfe/compliance/check`, {
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
        const response = await fetch(`${API_BASE}/sfe/dlt/audit`);
        const data = await response.json();
        alert(`DLT Audit Logs:\n${data.total_records} records on blockchain`);
    } catch (error) {
        showError('DLT query failed: ' + error.message);
    }
}

// ===== ARBITER & ARENA FUNCTIONS =====

async function startArbiter() {
    try {
        const response = await fetch(`${API_BASE}/sfe/arbiter/control`, {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({action: 'start', config: {}})
        });
        showSuccess('Arbiter started');
    } catch (error) {
        showError('Failed to start Arbiter: ' + error.message);
    }
}

async function stopArbiter() {
    try {
        const response = await fetch(`${API_BASE}/sfe/arbiter/control`, {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({action: 'stop'})
        });
        showSuccess('Arbiter stopped');
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
        parsedConfig = JSON.parse(config); // Validate and store parsed JSON
        const response = await fetch(`${API_BASE}/sfe/arbiter/control`, {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({action: 'configure', config: parsedConfig})
        });
        showSuccess('Arbiter configured');
    } catch (error) {
        showError('Configuration failed: ' + error.message);
    }
}

async function getArbiterStatus() {
    try {
        const response = await fetch(`${API_BASE}/sfe/arbiter/control`, {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({action: 'status'})
        });
        const data = await response.json();
        alert(`Arbiter Status:\nState: ${data.status}\nActive Agents: ${data.active_agents}`);
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
        const response = await fetch(`${API_BASE}/sfe/arena/compete`, {
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
        const response = await fetch(`${API_BASE}/sfe/rl/environment/${envId}/status`);
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

function showSubscribe() {
    const topic = prompt('Enter topic to subscribe to:');
    if (!topic) return;
    
    // Strict validation with anchors and length limit
    if (topic.length > 100 || !/^[a-zA-Z0-9_-]+$/.test(topic)) {
        showError('Invalid topic name. Use only alphanumeric characters, hyphens, and underscores (max 100 chars).');
        return;
    }
    
    fetch(`${API_BASE}/omnicore/message-bus/subscribe`, {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({topic, subscriber_id: 'web-ui'})
    })
    .then(() => showSuccess(`Subscribed to topic: ${topic}`))
    .catch(err => showError('Subscription failed: ' + err.message));
}

function showInstallPlugin() {
    const pluginName = prompt('Enter plugin name to install:');
    if (!pluginName) return;
    
    // Strict validation with length limit
    if (pluginName.length > 50 || !/^[a-zA-Z0-9_-]{3,50}$/.test(pluginName)) {
        showError('Invalid plugin name. Use 3-50 alphanumeric characters, hyphens, or underscores.');
        return;
    }
    
    fetch(`${API_BASE}/omnicore/plugins/install`, {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({plugin_name: pluginName, version: 'latest'})
    })
    .then(() => showSuccess(`Installing plugin: ${pluginName}`))
    .catch(err => showError('Installation failed: ' + err.message));
}

function showRateLimit() {
    const limit = prompt('Enter rate limit (requests per minute):');
    if (!limit) return;
    
    const limitNum = parseInt(limit);
    if (isNaN(limitNum) || limitNum < 1 || limitNum > 10000 || !Number.isSafeInteger(limitNum)) {
        showError('Invalid rate limit. Enter a whole number between 1 and 10000.');
        return;
    }
    
    fetch(`${API_BASE}/omnicore/rate-limits/configure`, {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({limit: limitNum, window_seconds: 60})
    })
    .then(() => showSuccess(`Rate limit configured: ${limitNum}/min`))
    .catch(err => showError('Configuration failed: ' + err.message));
}

function showSIEMConfig() {
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
    
    fetch(`${API_BASE}/sfe/siem/configure`, {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({siem_endpoint: endpoint, enabled: true})
    })
    .then(() => showSuccess('SIEM integration configured'))
    .catch(err => showError('Configuration failed: ' + err.message));
}
