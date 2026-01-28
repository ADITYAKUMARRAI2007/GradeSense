# Poppler-Utils Permanent Fix Documentation

## Problem
The application was experiencing recurring "Unable to get page count. Is poppler installed and in PATH?" errors when processing PDF files. This happened because the `poppler-utils` system dependency was not persisting across container restarts.

## Root Cause
- `poppler-utils` is a system-level dependency required by the `pdf2image` Python library
- Manual `apt-get install` commands don't persist when containers restart in Kubernetes
- The dependency needs to be automatically installed on every backend startup

## Permanent Solution Implemented

### 1. Startup Dependency Check in Python (Primary Solution)
**File:** `/app/backend/server.py`
**Location:** `lifespan()` function (lines ~152-195)

The backend now checks for poppler-utils on every startup and automatically installs it if missing:

```python
@asynccontextmanager
async def lifespan(app: FastAPI):
    # Check if poppler-utils is installed
    import subprocess
    import shutil
    if not shutil.which("pdftoppm"):
        logger.warning("⚠️  poppler-utils not found. Attempting to install...")
        subprocess.run(["sudo", "apt-get", "update", "-qq"], check=True, capture_output=True)
        subprocess.run(["sudo", "apt-get", "install", "-y", "poppler-utils"], check=True, capture_output=True)
        logger.info("✅ poppler-utils installed successfully")
    else:
        logger.info("✅ poppler-utils is already installed")
```

### 2. Shell Scripts (Backup Solution)
**Files:** 
- `/app/backend/check_dependencies.sh` - Checks and installs poppler-utils
- `/app/backend/start_backend.sh` - Wrapper script that runs dependency check before starting backend

These scripts can also be used independently if needed.

## Verification
Check backend logs on startup to see:
```
🚀 FastAPI app starting up...
🔍 Checking system dependencies...
✅ poppler-utils is already installed
```

## Testing
The fix has been tested and verified:
- PDF to image conversion works correctly
- The check runs on every backend restart
- If poppler-utils is missing, it's automatically installed
- The installation only takes a few seconds

## Impact
- **Zero downtime:** The check happens during startup before accepting requests
- **Automatic recovery:** Even if poppler-utils is somehow removed, it will be reinstalled on next restart
- **No manual intervention needed:** The system is now self-healing for this dependency

## Future Improvements
If the application is containerized with a custom Dockerfile, add this line:
```dockerfile
RUN apt-get update && apt-get install -y poppler-utils && rm -rf /var/lib/apt/lists/*
```

This would make the dependency part of the base image, eliminating the need for runtime installation.

## Date Implemented
January 28, 2026

## Last Verified
January 28, 2026 - All tests passing ✅
