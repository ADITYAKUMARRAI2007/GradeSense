#!/bin/bash
# Dependency check script - ensures system dependencies are installed
# This script runs before the backend starts

echo "🔍 Checking system dependencies..."

# Check if poppler-utils is installed
if ! command -v pdftoppm &> /dev/null; then
    echo "⚠️  poppler-utils not found. Installing..."
    sudo apt-get update -qq && sudo apt-get install -y poppler-utils > /dev/null 2>&1
    if [ $? -eq 0 ]; then
        echo "✅ poppler-utils installed successfully"
    else
        echo "❌ Failed to install poppler-utils"
        exit 1
    fi
else
    echo "✅ poppler-utils is already installed"
fi

echo "✅ All system dependencies are ready"
