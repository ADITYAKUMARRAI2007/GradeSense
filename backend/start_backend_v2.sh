#!/bin/bash

# GradeSense Backend v2.0 - Startup Script

set -e

echo "🚀 Starting GradeSense Backend v2.0..."

# Check if venv exists
if [ ! -d "venv" ]; then
    echo "Creating Python virtual environment..."
    python3 -m venv venv
fi

# Activate venv
source venv/bin/activate

# Install/update dependencies
if [ -f "requirements.txt" ]; then
    echo "Installing dependencies..."
    pip install -q -r requirements.txt
fi

# Check .env exists
if [ ! -f ".env" ]; then
    echo "⚠️  .env file not found!"
    echo "Please create .env with:"
    echo "  MONGODB_URI=your_mongodb_uri"
    echo "  GEMINI_API_KEY=your_gemini_key"
    echo ""
    exit 1
fi

# Start server
echo "✅ Starting FastAPI server on port 8001..."
echo "📖 Docs available at: http://localhost:8001/docs"
echo "❌ To stop: Ctrl+C"
echo ""

python -m uvicorn main:app --reload --host 0.0.0.0 --port 8001
