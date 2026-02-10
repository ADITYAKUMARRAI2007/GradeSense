# GradeSense Backend Architecture v2.0

A clean, modular, production-ready architecture for AI-powered exam grading.

## 📁 Project Structure

```
backend/
├── app/                          # Main application package
│   ├── __init__.py              # App initialization
│   ├── config/                  # Configuration
│   │   ├── __init__.py
│   │   └── settings.py          # Environment settings & defaults
│   │
│   ├── cache/                   # Caching layer
│   │   └── __init__.py          # Cache service (questions, answers, results)
│   │
│   ├── services/                # Core business logic services
│   │   ├── __init__.py
│   │   ├── document_extraction.py    # PDF → Images conversion
│   │   ├── question_extraction.py    # Extract questions from PDF (Gemini)
│   │   ├── answer_extraction.py      # Extract answers from model sheet (Gemini OCR)
│   │   ├── grading.py               # Grade student answers (Gemini + system prompt)
│   │   └── orchestration.py         # Coordinate the complete workflow
│   │
│   ├── routes/                  # API endpoints
│   │   ├── __init__.py
│   │   ├── exam_routes.py           # Question paper & model answer upload
│   │   └── grading_routes.py        # Student paper grading & job tracking
│   │
│   ├── models/                  # Pydantic data models
│   │   └── __init__.py
│   │
│   └── utils/                   # Utility functions
│       └── __init__.py          # File validation, formatting, etc.
│
├── main.py                      # FastAPI application entry point
├── requirements.txt             # Python dependencies
└── .env                        # Environment variables
```

## 🔄 Complete Grading Workflow

### Phase 1: Question Paper Upload
```
1. Teacher uploads question paper PDF
   ↓
2. PDF validated & converted to images
   ↓
3. Gemini AI extracts structured questions:
   - Question number
   - Question text
   - Max marks
   - Rubric
   - Sub-questions (if any)
   ↓
4. Questions cached by exam_id + pdf_hash
   ↓
5. Questions stored in exams collection
```

### Phase 2: Model Answer Upload (Optional)
```
1. Teacher uploads model answer PDF
   ↓
2. PDF validated & converted to images
   ↓
3. For each question number:
   - Gemini Vision OCR extracts answer text
   - Detects diagrams/formulas
   - Caches by exam_id + question_number + pdf_hash
   ↓
4. Answers stored in model_answers collection
```

### Phase 3: Student Paper Grading
```
For each student paper submitted:

1. Paper converted to base64 JPEG images
   ↓
2. For each question:
   a. Get cached question details
   b. Get cached model answer (if available)
   c. Send student answer images to Gemini with:
      - System prompt (with grading mode rules)
      - Question text
      - Model answer (optional)
      - Images of student's answer
   ↓
   d. Gemini returns JSON:
      {
        "obtained_marks": 8,
        "feedback": "...",
        "sub_scores": [...],
        "confidence": 0.95
      }
   ↓
   e. Result cached by student_answer_hash + question_number
   ↓
3. All question scores compiled into submission record
   ↓
4. Submission saved to database
```

## 🗄️ Database Collections

### exams
Exam metadata, questions, and file hashes
```json
{
  "exam_id": "unique_exam_id",
  "teacher_id": "teacher_id",
  "exam_name": "Math Midterm",
  "grading_mode": "balanced",
  "total_marks": 100,
  "questions": [...],
  "question_paper_hash": "sha256_hash",
  "model_answer_hash": "sha256_hash",
  "status": "ready_to_grade"
}
```

### model_answers
Extracted answers for each question
```json
{
  "exam_id": "exam_id",
  "question_number": 1,
  "pdf_hash": "hash_of_model_answer_pdf",
  "answer_text": "OCR'd text of answer",
  "has_diagrams": true,
  "confidence": 0.92
}
```

### submissions
Student answer sheets and grades
```json
{
  "submission_id": "unique_id",
  "exam_id": "exam_id",
  "student_id": "student_id",
  "student_name": "John Doe",
  "obtained_marks": 75.5,
  "total_marks": 100,
  "percentage": 75.5,
  "scores": [...],  // Per-question scores
  "status": "graded"
}
```

### questions_cache
Cached extracted questions (TTL: 30 days)
```json
{
  "exam_id": "exam_id",
  "pdf_hash": "hash",
  "questions": [...],
  "cached_at": "2026-02-05T...",
  "expires_at": "2026-03-07T..."
}
```

### model_answer_cache
Cached extracted model answers (TTL: 30 days)
```json
{
  "exam_id": "exam_id",
  "question_number": 1,
  "pdf_hash": "hash",
  "answer_data": {...},
  "cached_at": "2026-02-05T...",
  "expires_at": "2026-03-07T..."
}
```

### grading_result_cache
Cached grading results (TTL: 30 days)
```json
{
  "cache_key": "exam_id_student_hash_q_num",
  "exam_id": "exam_id",
  "student_answer_hash": "hash",
  "question_number": 1,
  "result": {...},
  "cached_at": "2026-02-05T...",
  "expires_at": "2026-03-07T..."
}
```

### grading_jobs
Track grading job progress
```json
{
  "job_id": "unique_job_id",
  "exam_id": "exam_id",
  "teacher_id": "teacher_id",
  "total_papers": 30,
  "processed_papers": 15,
  "successful": 14,
  "failed": 1,
  "status": "processing",
  "started_at": "2026-02-05T...",
  "errors": [...]
}
```

## 🎯 Grading Modes

All modes use the same system prompt structure but with different rubrics:

### Strict Mode
- Every step required
- 70% threshold for passing
- Alternative methods not accepted
- High precision expected

### Balanced Mode (Default)
- Fair evaluation of method + answer
- 60-70% marks for correct approach
- 40-50% marks for concept with calculation error
- 50% threshold
- Accept reasonable alternatives

### Conceptual Mode
- Understanding over execution
- Minor arithmetic errors: -10% only
- Alternative methods accepted
- 50% threshold

### Lenient Mode
- Effort-based grading
- 25% floor marks for any attempt
- Partial credit for elements
- Creative approaches encouraged

## 🚀 Key Services

### DocumentExtractionService
- PDF → Base64 JPEG conversion
- Configurable zoom (2.0x for quality)
- Configurable JPEG quality (85%)
- Rate limiting via semaphore

### QuestionExtractionService
- Gemini AI analyzes question paper images
- Returns structured questions with max_marks, rubric, sub_questions
- Caches results automatically

### AnswerExtractionService
- Gemini Vision OCR on model answer sheet
- Extracts answer for each question separately
- Detects diagrams/formulas
- Caches per question

### GradingService
- Grades student answers using Gemini
- Passes system prompt with grading mode instructions
- Handles edge cases (-1.0 for not found vs 0.0 for wrong)
- Validates JSON responses
- Caches results

### GradeOrchestrationService
- Coordinates entire workflow
- Orchestrates document → questions → answers → grading
- Handles batch grading of multiple papers

## 🔐 Caching Strategy

**3-level caching for performance:**

1. **Questions Cache**
   - Key: exam_id + question_paper_hash
   - TTL: 30 days
   - Hit rate: High (same question paper used for all students)

2. **Model Answer Cache**
   - Key: exam_id + question_number + model_answer_hash
   - TTL: 30 days
   - Hit rate: Very High (single model answer for all students)

3. **Grading Result Cache**
   - Key: exam_id + student_answer_hash + question_number
   - TTL: 30 days
   - Hit rate: Medium (if students submit identical answers)

## 🔗 API Endpoints

### Exam Management

**POST** `/api/exams/{exam_id}/upload-question-paper`
- Upload question paper PDF
- Extracts & caches questions
- Returns: `{success, question_count, questions}`

**POST** `/api/exams/{exam_id}/upload-model-answer`
- Upload model answer PDF
- Extracts & caches answers per question
- Returns: `{success, answers_extracted, answers}`

**GET** `/api/exams/{exam_id}/status`
- Get exam metadata and status
- Returns: exam document

### Grading

**POST** `/api/grading/grade-papers`
- Submit student papers for grading
- Parameters: exam_id, grading_mode, files
- Returns: `{job_id, status, papers}`

**GET** `/api/grading/job/{job_id}/status`
- Track grading job progress
- Returns: job document with results

**POST** `/api/grading/job/{job_id}/cancel`
- Cancel active grading job
- Returns: `{job_id, status}`

## 🔧 Configuration

Edit `.env` or set environment variables:

```bash
# Database
MONGODB_URI=mongodb+srv://user:pass@cluster.mongodb.net/

# AI
GEMINI_API_KEY=your_key_here
EMERGENT_LLM_KEY=optional_fallback_key

# Server
PORT=8001
HOST=0.0.0.0
DEBUG=False

# Processing
PDF_ZOOM=2.0          # Image quality multiplier
JPEG_QUALITY=85       # Compression quality
CHUNK_SIZE=8          # Pages per AI call
MAX_WORKERS=5         # Concurrent grading tasks
```

## 📦 Dependencies

See `requirements.txt`:
- FastAPI: Web framework
- Motor: Async MongoDB driver
- PyMuPDF (fitz): PDF processing
- Pillow: Image processing
- google-generativeai: Gemini API client
- pydantic: Data validation
- uvicorn: ASGI server

## ⚡ Performance Optimizations

1. **Async/Await**: Non-blocking I/O throughout
2. **Semaphores**: Rate limiting on PDF conversion and AI calls
3. **Caching**: 3-level cache reduces redundant API calls
4. **Chunking**: Process large PDFs in 8-page chunks to Gemini
5. **Base64**: Inline images in AI requests (faster than file refs)
6. **Connection Pooling**: MongoDB with maxPoolSize=50

## 🧪 Testing Workflow

```bash
# 1. Start backend
cd backend
source venv/bin/activate
python -m uvicorn app.main:app --reload

# 2. Upload question paper
curl -X POST http://localhost:8001/api/exams/exam_1/upload-question-paper \
  -F "file=@question_paper.pdf"

# 3. Upload model answer
curl -X POST http://localhost:8001/api/exams/exam_1/upload-model-answer \
  -F "file=@model_answer.pdf"

# 4. Submit student papers for grading
curl -X POST http://localhost:8001/api/grading/grade-papers \
  -F "exam_id=exam_1" \
  -F "grading_mode=balanced" \
  -F "files=@student_1.pdf" \
  -F "files=@student_2.pdf"

# 5. Check grading progress
curl http://localhost:8001/api/grading/job/{job_id}/status
```

## 📝 Important Notes

- **No task queue needed**: Papers grade sequentially with asyncio semaphores
- **No GridFS delays**: Files loaded to memory as base64
- **Caching saves money**: Avoid re-extracting & re-grading
- **Gemini as orchestrator**: Single LLM handles questions, answers, and grading
- **System prompt is sacred**: All grading mode logic in master prompt
- **Edge cases matter**: -1.0 (not found) vs 0.0 (wrong) is explicitly handled
