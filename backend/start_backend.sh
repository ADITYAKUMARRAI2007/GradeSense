#!/bin/bash
# Backend startup wrapper - ensures dependencies are installed before starting

# Run dependency check
/app/backend/check_dependencies.sh

# Start the backend server
cd /app/backend
exec /root/.venv/bin/uvicorn server:app --host 0.0.0.0 --port 8001 --workers 1
