# GradeSense v2.0 - Migration & Cleanup Guide

## 📋 Files to Keep (New Architecture)

### Essential Files
```
backend/
├── app/                    ✅ NEW - Keep all (modular architecture)
├── main.py                ✅ NEW - Main FastAPI app
├── requirements_new.txt   ✅ NEW - Updated dependencies
├── .env                   ✅ KEEP - Environment config
├── credentials/           ✅ KEEP - API keys
└── venv/                  ✅ KEEP - Python environment
```

## 🗑️ Files to Delete (Cleanup)

### Old Monolithic Files
```
backend/
├── server.py                           ❌ DELETE - Replaced by app/
├── server.py.backup                    ❌ DELETE - Backup
├── server.py.before_applying_fix       ❌ DELETE - Backup
├── server.py.before_fix                ❌ DELETE - Backup
├── server.py.new                       ❌ DELETE - Backup
├── server_backup_20260114_170251.py   ❌ DELETE - Backup
├── server_no_gridfs_backup.py         ❌ DELETE - Backup
├── server_with_gridfs.py              ❌ DELETE - Backup
└── background_grading.py.broken       ❌ DELETE - Unused

### Old Utility Files (Replaced by modular services)
├── background_grading.py              ❌ DELETE - Replaced by orchestration.py
├── task_worker.py                     ❌ DELETE - No longer needed
├── gemini_wrapper.py                  ❌ DELETE - Integrated into grading.py
├── vision_ocr_service.py              ❌ DELETE - Integrated into answer_extraction.py
├── file_utils.py                      ❌ DELETE - Replaced by utils/__init__.py
├── annotation_utils.py                ❌ DELETE - Unused
├── auth_utils.py                      ❌ DELETE - Not in scope
├── concurrency.py                     ❌ DELETE - Replaced by semaphores in services

### Database Migration Scripts (Already executed)
├── migrate_large_files_to_gridfs.py           ❌ DELETE
├── migrate_submission_images_to_gridfs.py     ❌ DELETE
├── migrate_submissions_to_gridfs.py           ❌ DELETE

### Old Config Files
├── requirements.txt                   ❌ REPLACE - Use requirements_new.txt
├── requirements.txt.new              ❌ DELETE
├── check_dependencies.sh              ❌ DELETE - Not needed

### Server Startup Scripts
├── start_backend.sh                   ❌ DELETE - Use: python -m uvicorn main:app

### Logs
├── server.log                         ❌ DELETE - Can regenerate
```

## 📊 Comparison: Old vs New

### Old Architecture
```
Single 11,807-line file (server.py) with:
- API routes mixed with business logic
- Database models in same file
- Services not separated
- No clear responsibilities
- Hard to test individual components
- All functionality compiled at startup
```

### New Architecture
```
Modular structure with clear separation:
- app/routes/          → API endpoints only
- app/services/        → Business logic isolated
- app/models/          → Data models
- app/cache/           → Caching layer
- app/config/          → Configuration
- app/utils/           → Utilities

Benefits:
✅ Easy to test
✅ Easy to extend
✅ Clear responsibilities
✅ Reusable services
✅ Readable code organization
```

## 🔄 Migration Steps

### Step 1: Backup Old Database
```bash
# Your MongoDB data is safe in cloud
# No database migration needed - collections remain same
```

### Step 2: Create New Backend Structure
```bash
cd backend/

# Old files still present for reference
# New app/ directory is parallel structure
```

### Step 3: Update Environment
```bash
# Copy .env settings (no changes needed)
cp .env .env.backup

# Update if using new requirements
pip install -r requirements_new.txt
```

### Step 4: Test New Application
```bash
# Terminal 1: Start new app
cd backend
python -m uvicorn main:app --reload --port 8001

# Terminal 2: Test health endpoint
curl http://localhost:8001/api/health

# Terminal 3: Test workflow
curl -X POST http://localhost:8001/api/exams/test_exam/upload-question-paper \
  -F "file=@question_paper.pdf"
```

### Step 5: Cleanup Old Files
```bash
# After confirming new app works:
cd backend/

# Delete old monolithic files
rm server.py
rm server.py.backup
rm server.py.before_applying_fix
rm server.py.before_fix
rm server.py.new
rm server_backup_*.py
rm server_no_gridfs_backup.py
rm server_with_gridfs.py
rm background_grading.py.broken

# Delete old utilities (replaced by modular services)
rm background_grading.py
rm task_worker.py
rm gemini_wrapper.py
rm vision_ocr_service.py
rm file_utils.py
rm annotation_utils.py
rm auth_utils.py
rm concurrency.py

# Delete migration scripts (already executed)
rm migrate_*.py

# Delete old config/scripts
rm requirements.txt.new
rm check_dependencies.sh
rm start_backend.sh
rm server.log

# Replace with new requirements
mv requirements_new.txt requirements.txt
```

## 🚀 Quick Start with New Architecture

```bash
# 1. Navigate to backend
cd backend

# 2. Install dependencies
pip install -r requirements.txt

# 3. Create .env (if not exists)
# MONGODB_URI=...
# GEMINI_API_KEY=...

# 4. Run application
python -m uvicorn main:app --reload --port 8001

# 5. Access API
# - Health: http://localhost:8001/api/health
# - Docs: http://localhost:8001/docs
# - Upload question paper: POST /api/exams/{exam_id}/upload-question-paper
```

## 📚 New API Endpoints

### Exam Management
- `POST /api/exams/{exam_id}/upload-question-paper` - Upload & extract questions
- `POST /api/exams/{exam_id}/upload-model-answer` - Upload & extract answers
- `GET /api/exams/{exam_id}/status` - Get exam metadata

### Grading
- `POST /api/grading/grade-papers` - Submit papers for grading
- `GET /api/grading/job/{job_id}/status` - Track progress
- `POST /api/grading/job/{job_id}/cancel` - Cancel job

## ✅ Verification Checklist

After migration:

- [ ] Backend starts without errors: `python -m uvicorn main:app`
- [ ] Health check works: `curl http://localhost:8001/api/health`
- [ ] Database connection successful
- [ ] Cache layer initialized
- [ ] All routes registered (check startup logs)
- [ ] Question extraction works (test with PDF)
- [ ] Model answer extraction works
- [ ] Student paper grading works
- [ ] Results saved to database
- [ ] Caching is functional

## 🐛 Debugging

### Check startup logs
```bash
tail -f backend_output.log
```

### Verify database connection
```bash
python -c "from motor.motor_asyncio import AsyncIOMotorClient; import asyncio; asyncio.run(AsyncIOMotorClient('mongodb+srv://...').server_info())"
```

### Test individual services
```python
# In Python shell:
from app.services import DocumentExtractionService
from app.config.settings import settings

doc_svc = DocumentExtractionService()

# Test PDF validation
with open('test.pdf', 'rb') as f:
    is_valid, msg = await doc_svc.validate_pdf(f.read())
    print(f"Valid: {is_valid}, {msg}")
```

## 📞 Support

If you encounter issues:

1. Check `.env` variables are set
2. Verify MongoDB connection: `MONGODB_URI` should work
3. Verify API keys: `GEMINI_API_KEY` should be valid
4. Check logs for specific error messages
5. Ensure Python 3.9+ is used
6. Verify all dependencies installed: `pip list | grep fastapi`
