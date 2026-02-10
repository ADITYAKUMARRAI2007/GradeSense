# GradeSense Backend v2.0 - Clean Architecture

> Production-ready AI exam grading system with modular, scalable design

## 🎯 What is GradeSense?

GradeSense is an AI-powered exam grading system that:

- ✅ **Extracts questions** from question papers using Gemini Vision AI
- ✅ **Extracts answers** from model answer sheets using OCR
- ✅ **Grades student papers** question-by-question using AI
- ✅ **Caches everything** to minimize API costs
- ✅ **Handles 30+ papers** with 50+ pages efficiently
- ✅ **Supports 4 grading modes**: Strict, Balanced, Conceptual, Lenient
- ✅ **Provides detailed feedback** with confidence scores

## 🏗️ Architecture Overview

### Project Structure

```
backend/
├── app/                                      # Main application package
│   ├── cache/           → Caching layer (questions, answers, results)
│   ├── config/          → Settings & configuration
│   ├── models/          → Pydantic data models
│   ├── routes/          → API endpoints
│   │   ├── exam_routes.py      (question paper & model answer)
│   │   └── grading_routes.py   (student paper grading)
│   ├── services/        → Business logic (the real work!)
│   │   ├── document_extraction.py   (PDF → Images)
│   │   ├── question_extraction.py   (Extract questions)
│   │   ├── answer_extraction.py     (Extract answers via OCR)
│   │   ├── grading.py               (Grade answers)
│   │   └── orchestration.py         (Orchestrate the flow)
│   └── utils/           → Utility functions
│
├── main.py                                   # FastAPI app entry point
├── requirements_clean.txt                    # Clean dependencies
└── .env                                      # Environment variables
```

### Workflow: 4 Phases

```
PHASE 1: Upload Question Paper
↓
Question Paper PDF → Extract questions using Gemini → Cache → Done

PHASE 2 (Optional): Upload Model Answer
↓
Model Answer PDF → Extract each answer using Gemini Vision → Cache → Done

PHASE 3: Upload & Grade Student Papers
↓
For each of 30+ student papers:
  For each question:
    Get cached question + model answer
    Get student's answer images
    Send to Gemini with system prompt
    Get marks & feedback
    Cache result
  Compile all scores
  Save to database

PHASE 4: Review & Publish
↓
Teacher reviews scores → Publishes to students
```

## 🚀 Quick Start

### 1. Install Dependencies

```bash
cd backend

# Option A: Use clean requirements (recommended for new setup)
pip install -r requirements_clean.txt

# Option B: Use existing requirements
pip install -r requirements.txt
```

### 2. Setup Environment

```bash
# Create .env file
cat > .env << EOF
MONGODB_URI=mongodb+srv://user:pass@cluster.mongodb.net/
GEMINI_API_KEY=your_gemini_api_key_here
LOG_LEVEL=INFO
EOF
```

### 3. Start Backend

```bash
# Option A: Using startup script
bash start_backend_v2.sh

# Option B: Direct uvicorn
python -m uvicorn main:app --reload --port 8001

# Option C: Using bash script (requires venv)
source venv/bin/activate
python -m uvicorn main:app --reload
```

### 4. Verify Health

```bash
# Check API is running
curl http://localhost:8001/api/health

# Response:
# {"status": "healthy", "version": "2.0.0", "database": "connected"}
```

## 📚 API Endpoints

### Exam Management

**Upload Question Paper**
```bash
POST /api/exams/{exam_id}/upload-question-paper
Content-Type: multipart/form-data
- file: question_paper.pdf

Response:
{
  "success": true,
  "exam_id": "exam_001",
  "question_count": 5,
  "questions": [...]
}
```

**Upload Model Answer**
```bash
POST /api/exams/{exam_id}/upload-model-answer
Content-Type: multipart/form-data
- file: model_answer.pdf

Response:
{
  "success": true,
  "exam_id": "exam_001",
  "answers_extracted": 5,
  "answers": {...}
}
```

**Get Exam Status**
```bash
GET /api/exams/{exam_id}/status

Response:
{
  "exam_id": "exam_001",
  "exam_name": "Math Midterm",
  "questions": [...],
  "status": "ready_to_grade"
}
```

### Grading

**Submit Papers for Grading**
```bash
POST /api/grading/grade-papers
Content-Type: multipart/form-data
- exam_id: "exam_001"
- grading_mode: "balanced"  (strict|balanced|conceptual|lenient)
- files: [student_1.pdf, student_2.pdf, ...]

Response:
{
  "job_id": "job_xyz",
  "status": "started",
  "papers": 30
}
```

**Get Job Status**
```bash
GET /api/grading/job/{job_id}/status

Response:
{
  "job_id": "job_xyz",
  "status": "processing",
  "total_papers": 30,
  "processed_papers": 15,
  "successful": 15,
  "results": {...}
}
```

**Cancel Job**
```bash
POST /api/grading/job/{job_id}/cancel

Response:
{
  "job_id": "job_xyz",
  "status": "cancelled"
}
```

## 🔐 Caching Strategy

GradeSense uses **3-level caching** to minimize API costs:

### Level 1: Questions Cache
```
Key: exam_id + question_paper_hash
TTL: 30 days
Hit Rate: 100% (same questions for all students)
Savings: 1 Gemini call per exam
```

### Level 2: Model Answer Cache
```
Key: exam_id + question_number + model_answer_hash
TTL: 30 days
Hit Rate: 100% (same model answer for all students)
Savings: 5+ Gemini calls per exam
```

### Level 3: Grading Result Cache
```
Key: exam_id + student_answer_hash + question_number
TTL: 30 days
Hit Rate: High (if similar answers submitted)
Savings: Reduces re-grading cost
```

### Cost Example: 10 Exams × 30 Students

```
WITHOUT CACHING:
  - 10 exams × (1 question extraction + 5 answer extraction + 30×5 gradings)
  - = 10 × (1 + 5 + 150) = 1,560 Gemini API calls
  - Cost: ~$312 (at $0.20/call)

WITH CACHING:
  - First exam: 1 + 5 + 150 = 156 calls
  - Next 9 exams: 1 + 5 + 0 (cached) = 6 calls each
  - Total: 156 + (9×6) = 210 calls
  - Cost: ~$42 (at $0.20/call)
  - Savings: 86.5%
```

## 🎯 Grading Modes

Each mode uses the same system prompt but with different rubrics:

### Strict Mode
- Every step required
- 70% threshold for passing
- Alternative methods: 0 marks
- Best for: Technical exams with specific procedures

### Balanced Mode (Default)
- Fair evaluation of method + answer
- 60-70% for correct method, 40-50% for concept with error
- 50% threshold
- Accept reasonable alternatives
- Best for: Most educational contexts

### Conceptual Mode
- Understanding over perfect execution
- Minor arithmetic errors: -10% only
- Alternative correct methods: full marks
- 50% threshold
- Best for: Conceptual understanding assessment

### Lenient Mode
- Effort-based grading
- 25% floor marks for any attempt
- Partial credit for any correct element
- Best for: Formative assessment, lower grades

## 🔧 Configuration

### Environment Variables

```bash
# Database
MONGODB_URI=mongodb+srv://user:pass@cluster.mongodb.net/

# API Keys (Google Gemini)
GEMINI_API_KEY=your_api_key_here
EMERGENT_LLM_KEY=optional_fallback_key

# Server
PORT=8001                # Port to run on
HOST=0.0.0.0            # Host to bind to
DEBUG=False             # Enable debug mode

# Processing
PDF_ZOOM=2.0            # PDF → Image zoom (quality)
JPEG_QUALITY=85         # JPEG compression quality
CHUNK_SIZE=8            # Pages per AI call (limit)
MAX_WORKERS=5           # Concurrent grading tasks

# Logging
LOG_LEVEL=INFO          # DEBUG|INFO|WARNING|ERROR

# Cache
CACHE_TTL_DAYS=30       # Cache expiration time
```

## 📊 Database Collections

### exams
```json
{
  "exam_id": "unique_id",
  "teacher_id": "teacher_id",
  "exam_name": "Math Midterm",
  "grading_mode": "balanced",
  "total_marks": 100,
  "questions": [...],  // Extracted questions
  "question_paper_hash": "sha256_hash",
  "model_answer_hash": "sha256_hash",
  "status": "completed"
}
```

### submissions
```json
{
  "submission_id": "unique_id",
  "exam_id": "exam_id",
  "student_id": "student_id",
  "student_name": "John Doe",
  "obtained_marks": 87.5,
  "total_marks": 100,
  "percentage": 87.5,
  "scores": [
    {
      "question_number": 1,
      "obtained_marks": 8,
      "total_marks": 10,
      "feedback": "Clear explanation...",
      "sub_scores": [...]
    }
  ],
  "status": "graded"
}
```

### grading_jobs
```json
{
  "job_id": "unique_id",
  "exam_id": "exam_id",
  "status": "completed",
  "total_papers": 30,
  "processed_papers": 30,
  "successful": 30,
  "failed": 0,
  "results": {...}
}
```

## 🧪 Testing

### Test Question Paper Upload
```bash
curl -X POST http://localhost:8001/api/exams/test_exam/upload-question-paper \
  -F "file=@tests/samples/question_paper.pdf"
```

### Test Model Answer Upload
```bash
curl -X POST http://localhost:8001/api/exams/test_exam/upload-model-answer \
  -F "file=@tests/samples/model_answer.pdf"
```

### Test Grading
```bash
curl -X POST http://localhost:8001/api/grading/grade-papers \
  -F "exam_id=test_exam" \
  -F "grading_mode=balanced" \
  -F "files=@tests/samples/student_1.pdf" \
  -F "files=@tests/samples/student_2.pdf"
```

## 📚 Documentation Files

- **ARCHITECTURE_V2.md** - Complete architecture overview
- **COMPLETE_FLOW_DIAGRAM.md** - Visual workflow diagrams
- **MIGRATION_GUIDE.md** - How to migrate from v1 to v2

## 🐛 Troubleshooting

### Backend won't start
```bash
# Check .env file exists
ls -la .env

# Check MongoDB connection
python -c "from motor.motor_asyncio import AsyncIOMotorClient; import asyncio; asyncio.run(AsyncIOMotorClient('your_uri').server_info())"

# Check API key is set
echo $GEMINI_API_KEY
```

### Import errors
```bash
# Ensure all dependencies installed
pip install -r requirements_clean.txt

# Check Python version (needs 3.9+)
python --version
```

### Grading fails
```bash
# Check logs
tail -f server.log

# Verify Gemini API key is valid
# - Check quota at https://aistudio.google.com/
# - Verify API key format
```

## 🚀 Performance Tips

1. **Use modular services**: Each service is independent and testable
2. **Leverage caching**: 95%+ cost reduction on repeated exams
3. **Batch processing**: Use asyncio for parallel question grading
4. **Rate limiting**: Built-in semaphores prevent API throttling
5. **Database indexes**: Auto-created for optimal query performance

## 📞 Support & Contribution

- **Issues**: Check logs in `backend_output.log`
- **Questions**: See documentation files
- **Improvements**: Modular design makes adding features easy

## 📄 License

GradeSense - AI Exam Grading System

---

**v2.0** - Clean, modular, production-ready architecture
Built with ❤️ for educators and students
