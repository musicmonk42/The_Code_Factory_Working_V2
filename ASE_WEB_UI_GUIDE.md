# A.S.E Web UI Quick Start Guide

## Overview

The **A.S.E (Automated Software Engineering) Platform** now includes a modern web interface with the Novatrax Labs branding prominently displayed in the top left corner.

## Features

### 🎨 Modern Dark Theme UI
- Professional gradient design
- Responsive layout for desktop and mobile
- Real-time updates via WebSocket

### 🏷️ Branding
- **A.S.E** logo in top left corner
- **"by Novatrax Labs"** displayed alongside
- Consistent branding throughout the interface

### 📊 Dashboard Views
1. **Dashboard**: System health, live stats, event stream
2. **Jobs**: Create, monitor, and manage generation jobs
3. **Generator**: File upload and generation status
4. **Self-Fixing Engineer**: Code analysis, error detection, fix management
5. **Fixes**: Review, apply, and rollback automated fixes
6. **System Status**: OmniCore engine and plugin information

## Running the Server

### Option 1: Using the run script (Recommended)

```bash
# Development mode with auto-reload
python server/run.py --reload

# Production mode
python server/run.py --host 0.0.0.0 --port 8000 --workers 4
```

### Option 2: Using uvicorn directly

```bash
# Development
uvicorn server.main:app --reload --host 0.0.0.0 --port 8000

# Production
uvicorn server.main:app --host 0.0.0.0 --port 8000 --workers 4
```

### Option 3: Using Docker

```bash
# Build image
docker build -t ase-platform .

# Run container
docker run -p 8000:8000 ase-platform
```

## Accessing the Interface

### Web UI (Browser)
Open your browser and navigate to:
```
http://localhost:8000/
```

You'll see the A.S.E web interface with:
- **Top Left**: "A.S.E by Novatrax Labs" branding
- **Navigation**: Access different platform features
- **Dashboard**: Real-time system status and event stream

### API Documentation
- **Swagger UI**: http://localhost:8000/api/docs
- **ReDoc**: http://localhost:8000/api/redoc
- **OpenAPI JSON**: http://localhost:8000/api/openapi.json

### API Endpoints (JSON)
If you access the root with an API client (curl, Postman, etc.):
```bash
curl http://localhost:8000/
```

Returns JSON with API information and endpoints.

## Using the Web UI

### 1. Dashboard
- View system health status for all components
- Monitor job statistics (total, running, completed)
- Connect to live event stream for real-time updates
- See platform-wide metrics

### 2. Create a Job
1. Click **"+ Create New Job"** button
2. Enter job description
3. Add metadata (optional, JSON format)
4. Click **"Create Job"**

### 3. Upload Files
1. Navigate to **Generator** tab
2. Drag and drop files or click to browse
3. Select README.md or other supported files
4. Click **"Upload Files"**
5. Files are automatically associated with a new job

### 4. Monitor Progress
1. Go to **Jobs** tab
2. Click **"View Details"** on any job
3. See real-time progress across all pipeline stages:
   - Upload
   - Generator Clarification
   - Generator Generation
   - OmniCore Processing
   - SFE Analysis
   - SFE Fixing

### 5. Handle Errors and Fixes
1. Navigate to **Self-Fixing Engineer** tab
2. Enter a job ID and click **"Analyze Code"**
3. View detected errors in the errors section
4. Click **"Propose Fix"** on any error
5. Go to **Fixes** tab to review proposed fixes
6. Click **"Apply"** to apply a fix
7. Click **"Rollback"** if needed

### 6. Real-Time Events
1. On the Dashboard, click **"Connect to Stream"**
2. Watch live events as they occur:
   - Job creations and completions
   - Stage transitions
   - Error detections
   - Fix applications
   - Platform status changes

## UI Components

### Header
```
┌─────────────────────────────────────────────────┐
│ A.S.E by Novatrax Labs                          │
│ Automated Software Engineering Platform         │
└─────────────────────────────────────────────────┘
```

### Navigation Bar
```
Dashboard | Jobs | Generator | Self-Fixing Engineer | Fixes | System Status
```

### Dashboard Stats
```
┌──────────┬──────────┬──────────┬──────────┐
│ 📊       │ ▶️       │ ✅       │ 🔧       │
│ Total    │ Running  │ Complete │ Fixes    │
│ Jobs: 10 │ Jobs: 2  │ Jobs: 8  │ Active:3 │
└──────────┴──────────┴──────────┴──────────┘
```

### Event Stream
```
┌─────────────────────────────────────────────┐
│ Live Event Stream                           │
│ [Connect to Stream] [Disconnect]            │
├─────────────────────────────────────────────┤
│ 04:15:30 - Job Created - New job started    │
│ 04:15:35 - Stage Started - Generator active │
│ 04:15:40 - Fix Proposed - Issue detected    │
└─────────────────────────────────────────────┘
```

## Color Scheme

The UI uses a professional dark theme with:
- **Primary**: Blue (#0066cc) - Links, buttons, highlights
- **Secondary**: Teal (#00cc99) - Success states, accents
- **Background**: Dark navy (#0a0e27) - Main background
- **Surface**: Lighter navy (#1a1f3a) - Cards, panels
- **Text**: White with secondary gray for labels
- **Success**: Green (#00cc88)
- **Warning**: Amber (#ffaa00)
- **Error**: Red (#ff4444)

## Branding Details

### Logo (Top Left)
- **A.S.E** in large gradient text (blue to teal)
- **"by Novatrax Labs"** in smaller text next to it
- Subtitle below: "Automated Software Engineering Platform"

### Typography
- Main font: System fonts (SF Pro, Segoe UI, Roboto)
- Monospace: Courier New (for code/technical content)
- Letter spacing on titles for professional appearance

## Browser Compatibility

Tested and supported on:
- ✅ Chrome 90+
- ✅ Firefox 88+
- ✅ Safari 14+
- ✅ Edge 90+

## Responsive Design

The UI automatically adapts to different screen sizes:
- **Desktop**: Full multi-column layout
- **Tablet**: Adjusted grid layout
- **Mobile**: Single-column stacked layout

## Keyboard Shortcuts

(Future feature - not yet implemented)
- `Ctrl+K`: Quick search
- `Ctrl+N`: New job
- `Ctrl+R`: Refresh current view

## Troubleshooting

### UI Not Loading
1. Check server is running: `curl http://localhost:8000/health`
2. Verify static files exist in `server/static/`
3. Check browser console for errors

### WebSocket Not Connecting
1. Ensure server supports WebSocket connections
2. Check firewall/proxy settings
3. Verify URL scheme (ws:// vs wss://)

### Styles Not Applying
1. Clear browser cache
2. Check browser developer tools for CSS errors
3. Verify static files are being served: `curl http://localhost:8000/static/css/main.css`

## Development

### File Structure
```
server/
├── static/
│   ├── css/
│   │   └── main.css          # All styles
│   └── js/
│       └── main.js           # All JavaScript
├── templates/
│   └── index.html            # Main HTML template
└── main.py                   # FastAPI app
```

### Customizing the UI

#### Change Colors
Edit `server/static/css/main.css`:
```css
:root {
    --primary-color: #0066cc;     /* Change primary color */
    --background: #0a0e27;        /* Change background */
    /* ... */
}
```

#### Add New Views
1. Add HTML section to `index.html`
2. Add navigation link
3. Add JavaScript handlers in `main.js`
4. Style in `main.css`

#### Modify Branding
Edit `server/templates/index.html`:
```html
<div class="branding-left">
    <h1>A.S.E</h1>
    <div class="by-line">
        <span class="by-text">by</span>
        <span class="company-name">Novatrax Labs</span>
    </div>
</div>
```

## Screenshots

### Dashboard View
The main dashboard shows:
- A.S.E branding in top left
- System health indicators
- Job statistics
- Live event stream

### Job Management
Create and monitor jobs:
- Visual job cards with status badges
- Filter by status
- View detailed progress

### Self-Fixing Engineer
Analyze code and manage fixes:
- Error detection results
- Fix proposals with confidence scores
- One-click apply/rollback

## Next Steps

1. **Install dependencies**: `pip install -r requirements.txt`
2. **Run the server**: `python server/run.py --reload`
3. **Open browser**: http://localhost:8000/
4. **Create your first job** and upload files
5. **Watch the live event stream** for real-time updates

## Support

For issues or questions:
- Check the server logs for errors
- Review API documentation at `/api/docs`
- See `server/README.md` for detailed API information
- Contact: support@novatraxlabs.com

---

**A.S.E - Automated Software Engineering Platform**  
*by Novatrax Labs*  
© 2026 Novatrax Labs LLC. All rights reserved.
