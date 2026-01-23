# Production Worker Not Running - Root Cause Analysis

## 🚨 Issue Summary

**Problem**: Grading jobs stuck at "pending 0/30" in production deployment (app.gradesense.in)

**Status**: Jobs created successfully, but never processed by worker

## 🔍 Root Cause

### The Issue: Worker Service Not Running in Production

**Environment Differences**:

| Component | Preview/Local | Production (Kubernetes) |
|-----------|---------------|-------------------------|
| **Backend** | ✅ Running | ✅ Running |
| **Frontend** | ✅ Running | ✅ Running |
| **MongoDB** | ✅ Local (localhost:27017) | ✅ Managed MongoDB |
| **Task Worker** | ✅ Running (via supervisor) | ❌ **NOT RUNNING** |

### Why Worker Isn't Running in Production

The task_worker is defined in **supervisor configuration** (lines 16-26 of `/etc/supervisor/conf.d/supervisord.conf`):

```ini
[program:task_worker]
command=/root/.venv/bin/python task_worker.py
directory=/app/backend
autostart=true
autorestart=true
```

**However**: 
- Supervisor configuration is for **preview/local development only**
- Production Kubernetes deployment **does NOT use supervisor**
- Production uses Kubernetes pods with different process management
- The task_worker service **was never configured for production deployment**

## 📊 How This Happened

### Original Architecture (Working in Preview)
```
┌─────────────────────────────────────┐
│   Preview Environment (Supervisor)   │
├─────────────────────────────────────┤
│  ┌──────────┐  ┌──────────────┐    │
│  │ Backend  │  │ Task Worker  │    │
│  │ (8001)   │  │ (Background) │    │
│  └──────────┘  └──────────────┘    │
│  ┌──────────┐  ┌──────────────┐    │
│  │ Frontend │  │   MongoDB    │    │
│  │  (3000)  │  │ (localhost)  │    │
│  └──────────┘  └──────────────┘    │
└─────────────────────────────────────┘
```

### Production Deployment (Current - Broken)
```
┌──────────────────────────────────────┐
│ Production Kubernetes (Emergent)     │
├──────────────────────────────────────┤
│  ┌──────────┐                        │
│  │ Backend  │  ❌ Task Worker        │
│  │  Pod     │     (MISSING!)         │
│  └──────────┘                        │
│  ┌──────────┐  ┌─────────────────┐  │
│  │ Frontend │  │ Managed MongoDB │  │
│  │  Pod     │  │   (External)    │  │
│  └──────────┘  └─────────────────┘  │
└──────────────────────────────────────┘
```

## 💡 The Solution

### Option 1: Add Worker to Supervisor Config (Quick Fix)

The supervisor config exists but might not be used in production. We need to ensure the deployment actually runs supervisor.

### Option 2: Move Worker Logic to Main Backend (Recommended)

Instead of a separate worker process, start the worker as a background thread/task in the main FastAPI app.

**Advantages**:
- ✅ Single process = single pod in Kubernetes
- ✅ Simpler deployment (no need for separate worker service)
- ✅ Worker automatically deployed with backend
- ✅ Shares same environment and configuration

### Option 3: Deploy Worker as Separate Kubernetes Service

Configure a dedicated worker pod in Kubernetes (requires Emergent platform support).

## 🛠️ Recommended Fix: Integrate Worker into Backend

### Implementation

**Modify `/app/backend/server.py`** to start the worker on startup:

```python
import asyncio
from contextlib import asynccontextmanager

# Global worker task
worker_task = None

async def start_background_worker():
    """Start the task worker as a background task"""
    from task_worker import main as worker_main
    try:
        await worker_main()
    except Exception as e:
        logger.error(f"Worker error: {e}")

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Manage application lifecycle"""
    global worker_task
    
    # Startup: Start worker
    logger.info("Starting background task worker...")
    worker_task = asyncio.create_task(start_background_worker())
    
    yield
    
    # Shutdown: Cancel worker
    logger.info("Stopping background task worker...")
    if worker_task:
        worker_task.cancel()
        try:
            await worker_task
        except asyncio.CancelledError:
            pass

# Apply lifespan to app
app = FastAPI(lifespan=lifespan)
```

This ensures the worker:
- ✅ Starts automatically when backend starts
- ✅ Runs in the same pod/process
- ✅ Deployed together with backend
- ✅ Works in both preview and production

## 🔄 Alternative Quick Fix: Use Synchronous Processing

If integrating the worker is complex, we can fall back to processing tasks synchronously until the worker is properly deployed:

```python
@api_router.post("/exams/{exam_id}/grade-papers-sync")
async def grade_papers_sync(exam_id: str, files: List[UploadFile]):
    """Process grading synchronously (no worker needed)"""
    # Read files from GridFS
    # Call process_grading_job_in_background directly
    # Wait for completion
    # Return results
```

**Tradeoffs**:
- ❌ UI will block during processing
- ✅ No worker configuration needed
- ✅ Works immediately in production

## 📝 Testing Checklist

After implementing the fix:

1. ☐ Deploy to production
2. ☐ Verify backend logs show "Starting background task worker..."
3. ☐ Upload and grade 3-5 papers
4. ☐ Verify status changes from "pending" → "processing" → "completed"
5. ☐ Check backend logs for worker activity
6. ☐ Test with 30 papers (the original failing case)

## 🎯 Summary

**Root Cause**: The task_worker is a separate process managed by supervisor in preview, but supervisor is NOT used in production Kubernetes deployment. The worker never starts, so tasks remain in "pending" forever.

**Solution**: Integrate the worker into the main backend application as a background asyncio task, ensuring it's deployed and runs automatically in all environments.

**Priority**: HIGH - This is a critical production bug blocking core functionality.
