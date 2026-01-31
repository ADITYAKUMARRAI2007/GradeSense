from fastapi import FastAPI, APIRouter, HTTPException, Request, Response, UploadFile, File, Form, Depends
from fastapi.responses import JSONResponse
from dotenv import load_dotenv
from starlette.middleware.cors import CORSMiddleware
from motor.motor_asyncio import AsyncIOMotorClient
from motor import motor_asyncio
import motor.motor_asyncio
import gridfs
from gridfs import GridFS
import os
import logging
from pathlib import Path
from pydantic import BaseModel, Field, ConfigDict
from typing import List, Optional, Dict, Any
import uuid
from datetime import datetime, timezone, timedelta
import base64
import httpx
import fitz
import io
from PIL import Image
import asyncio
import hashlib
import json
import pickle
from contextlib import asynccontextmanager
import time
import traceback
from bson import ObjectId
from file_utils import (
    convert_to_images, 
    extract_zip_files, 
    parse_student_from_filename,
    download_from_google_drive,
    extract_file_id_from_url,
    get_files_from_drive_folder
)
from annotation_utils import (
    Annotation,
    AnnotationType,
    apply_annotations_to_image,
    auto_position_annotations_for_question
)
from vision_ocr_service import get_vision_service, VisionOCRService

ROOT_DIR = Path(__file__).parent
load_dotenv(ROOT_DIR / '.env')

# Helper function for MongoDB serialization
def serialize_doc(doc):
    """Convert MongoDB document to JSON-safe dict"""
    if doc is None:
        return None
    if isinstance(doc, ObjectId):
        return str(doc)
    if isinstance(doc, list):
        return [serialize_doc(d) for d in doc]
    if isinstance(doc, dict):
        result = {}
        for key, value in doc.items():
            if key == "_id":
                continue  # Skip _id entirely
            elif isinstance(value, ObjectId):
                result[key] = str(value)
            elif isinstance(value, dict):
                result[key] = serialize_doc(value)
            elif isinstance(value, list):
                result[key] = serialize_doc(value)
            else:
                result[key] = value
        return result
    return doc

# MongoDB connection
mongo_url = os.environ['MONGO_URL']
client = AsyncIOMotorClient(mongo_url)
db = client[os.environ['DB_NAME']]

# GridFS for storing large files (model answers, question papers)
# Using synchronous GridFS since Motor doesn't have async GridFS yet
from pymongo import MongoClient
sync_client = MongoClient(mongo_url)
sync_db = sync_client[os.environ['DB_NAME']]
fs = GridFS(sync_db)

# Helper function: AI call with timeout protection
async def ai_call_with_timeout(chat, message, timeout_seconds=60, operation_name="AI call"):
    """
    Wrapper for AI API calls with timeout protection
    Prevents indefinite hanging on API timeouts
    """
    try:
        result = await asyncio.wait_for(
            chat.send_message(message),
            timeout=timeout_seconds
        )
        return result
    except asyncio.TimeoutError:
        logger.error(f"⏱️ TIMEOUT after {timeout_seconds}s: {operation_name}")
        raise TimeoutError(f"{operation_name} exceeded {timeout_seconds}s timeout")

# Global variable to hold worker task
_worker_task = None

# ============== DATA RETENTION & CLEANUP ==============

async def cleanup_old_metrics():
    """Delete metrics data older than 1 year, keep aggregated summaries"""
    try:
        one_year_ago = (datetime.now(timezone.utc) - timedelta(days=365)).isoformat()
        
        # Delete old metrics logs
        result1 = await db.metrics_logs.delete_many({"timestamp": {"$lt": one_year_ago}})
        logger.info(f"Deleted {result1.deleted_count} old metrics_logs records")
        
        # Delete old API metrics
        result2 = await db.api_metrics.delete_many({"timestamp": {"$lt": one_year_ago}})
        logger.info(f"Deleted {result2.deleted_count} old api_metrics records")
        
        # Keep grading_analytics forever as it's valuable for long-term insights
        # but delete associated metadata that's less critical
        
        logger.info("✅ Data cleanup completed successfully")
    except Exception as e:
        logger.error(f"Error during data cleanup: {e}", exc_info=True)

async def run_background_worker():
    """Integrated background worker - processes tasks and runs cleanup"""
    logger.info("🔄 Background worker started")
    logger.info("=" * 60)
    
    # Run cleanup on startup (once)
    await cleanup_old_metrics()
    
    # Schedule cleanup to run daily
    last_cleanup = datetime.now(timezone.utc)
    
    # Import worker main loop
    try:
        from task_worker import main as worker_main
        
        # Run worker in a loop with periodic cleanup
        while True:
            # Check if it's time for daily cleanup (every 24 hours)
            if (datetime.now(timezone.utc) - last_cleanup).total_seconds() > 86400:  # 24 hours
                logger.info("🗑️  Running scheduled data cleanup...")
                await cleanup_old_metrics()
                last_cleanup = datetime.now(timezone.utc)
            
            # Run the worker main loop
            await worker_main()
            
            # Small delay to prevent tight loop
            await asyncio.sleep(1)
            
    except Exception as e:
        logger.error(f"Background worker error: {e}", exc_info=True)

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan manager - starts/stops background worker"""
    global _worker_task
    
    # Startup: Check system dependencies
    logger.info("🚀 FastAPI app starting up...")
    logger.info("🔍 Checking system dependencies...")
    
    # Check if poppler-utils is installed
    import subprocess
    import shutil
    if not shutil.which("pdftoppm"):
        logger.warning("⚠️  poppler-utils not found. Attempting to install...")
        try:
            subprocess.run(
                ["sudo", "apt-get", "update", "-qq"],
                check=True,
                capture_output=True
            )
            subprocess.run(
                ["sudo", "apt-get", "install", "-y", "poppler-utils"],
                check=True,
                capture_output=True
            )
            logger.info("✅ poppler-utils installed successfully")
        except Exception as e:
            logger.error(f"❌ Failed to install poppler-utils: {e}")
            logger.error("⚠️  PDF processing may not work correctly!")
    else:
        logger.info("✅ poppler-utils is already installed")
    
    logger.info("🔄 Starting integrated background task worker...")
    _worker_task = asyncio.create_task(run_background_worker())
    logger.info("🔄 Background worker started")
    logger.info("=" * 60)
    
    yield
    
    # Shutdown: Cancel the background worker
    logger.info("🛑 FastAPI app shutting down...")
    if _worker_task and not _worker_task.done():
        logger.info("⏹️  Stopping background task worker...")
        _worker_task.cancel()
        try:
            await _worker_task
        except asyncio.CancelledError:
            logger.info("✅ Background task worker stopped cleanly")

# Create the main app with lifespan
app = FastAPI(title="GradeSense API", lifespan=lifespan)

# Create a router with the /api prefix
api_router = APIRouter(prefix="/api")

# ============== METRICS TRACKING MIDDLEWARE ==============

@app.middleware("http")
async def metrics_tracking_middleware(request: Request, call_next):
    """Track API metrics for all requests"""
    start_time = time.time()
    
    # Get user info if available
    user_id = None
    try:
        if request.url.path != "/api/auth/me":  # Avoid recursion
            auth_header = request.headers.get("cookie", "")
            if "session" in auth_header:
                # User is authenticated, we'll track this
                pass
    except:
        pass
    
    # Process request
    response = None
    error_type = None
    status_code = 500
    
    try:
        response = await call_next(request)
        status_code = response.status_code
    except Exception as e:
        error_type = type(e).__name__
        status_code = 500
        logger.error(f"Request failed: {str(e)}")
        raise
    finally:
        # Calculate response time
        response_time_ms = int((time.time() - start_time) * 1000)
        
        # Log API metrics asynchronously (don't block response)
        asyncio.create_task(log_api_metric(
            endpoint=request.url.path,
            method=request.method,
            response_time_ms=response_time_ms,
            status_code=status_code,
            error_type=error_type,
            user_id=user_id,
            ip_address=request.client.host if request.client else None
        ))
    
    return response

async def log_api_metric(endpoint: str, method: str, response_time_ms: int, 
                         status_code: int, error_type: Optional[str], 
                         user_id: Optional[str], ip_address: Optional[str]):
    """Log API metrics to database"""
    try:
        await db.api_metrics.insert_one({
            "endpoint": endpoint,
            "method": method,
            "response_time_ms": response_time_ms,
            "status_code": status_code,
            "error_type": error_type,
            "user_id": user_id,
            "ip_address": ip_address,
            "timestamp": datetime.now(timezone.utc).isoformat()
        })
    except Exception as e:
        logger.error(f"Failed to log API metric: {e}")

async def log_user_event(event_type: str, user_id: str, role: str, 
                         ip_address: str, metadata: Dict = None):
    """Log user events for analytics"""
    try:
        # Get geo location from IP (simplified - you can use a proper geo IP service)
        country = "Unknown"
        region = "Unknown"
        
        await db.metrics_logs.insert_one({
            "event_id": f"evt_{uuid.uuid4().hex[:12]}",
            "event_type": event_type,
            "user_id": user_id,
            "role": role,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "metadata": metadata or {},
            "ip_address": ip_address,
            "country": country,
            "region": region
        })
    except Exception as e:
        logger.error(f"Failed to log user event: {e}")

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Cache structure (memory-based cache)
grading_cache = {}
model_answer_cache = {}

# ============== MODELS ==============

class User(BaseModel):
    model_config = ConfigDict(extra="ignore")
    user_id: str
    email: str
    name: str
    picture: Optional[str] = None
    role: str = "teacher"  # teacher or student
    batches: List[str] = []
    contact: Optional[str] = None  # Phone number
    teacher_type: Optional[str] = None  # school, college, competitive, others
    exam_category: Optional[str] = None  # For competitive: UPSC, CA, CLAT, JEE, NEET, others
    profile_completed: bool = False  # Track if initial profile setup is done
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

class UserCreate(BaseModel):
    email: str
    name: str
    role: str = "student"
    student_id: Optional[str] = None
    batches: List[str] = []

class ProfileUpdate(BaseModel):
    name: str
    contact: str
    email: str
    teacher_type: str  # school, college, competitive, others
    exam_category: Optional[str] = None  # Only for competitive exams

class Batch(BaseModel):
    model_config = ConfigDict(extra="ignore")
    batch_id: str
    name: str
    teacher_id: str
    students: List[str] = []
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

class BatchCreate(BaseModel):
    name: str

class Subject(BaseModel):
    model_config = ConfigDict(extra="ignore")
    subject_id: str
    name: str
    teacher_id: str
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

class SubjectCreate(BaseModel):
    name: str

# Sub-question support
class SubQuestion(BaseModel):
    sub_id: str  # e.g., "a", "b", "c"
    max_marks: float
    rubric: Optional[str] = None

class ExamQuestion(BaseModel):
    question_number: int
    max_marks: float
    rubric: Optional[str] = None
    sub_questions: List[SubQuestion] = []  # For questions like 1a, 1b, 1c

class Exam(BaseModel):
    model_config = ConfigDict(extra="ignore")
    exam_id: str
    batch_id: str
    subject_id: str
    exam_type: str
    exam_name: str
    total_marks: float
    exam_date: str
    grading_mode: str
    questions: List[ExamQuestion] = []
    model_answer_file: Optional[str] = None
    teacher_id: str
    status: str = "draft"  # draft, processing, completed
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

class ExamCreate(BaseModel):
    batch_id: str
    subject_id: str
    exam_type: str
    exam_name: str
    total_marks: float = 100  # Default to 100, will be updated after extraction
    exam_date: str
    grading_mode: str
    questions: List[dict] = []  # Now optional, will be populated by auto-extraction
    exam_mode: str = "teacher_upload"  # ⭐ NEW: "teacher_upload" or "student_upload"
    show_question_paper: bool = False  # ⭐ NEW: For student mode, whether to show question paper

class StudentExamCreate(BaseModel):
    """Model for creating exam in student-upload mode"""
    batch_id: str
    exam_name: str
    total_marks: float
    grading_mode: str = "balanced"
    student_ids: List[str]  # Selected students
    show_question_paper: bool = False
    questions: List[ExamQuestion]

class StudentSubmission(BaseModel):
    """Model for student answer submission"""
    submission_id: str
    exam_id: str
    student_id: str
    student_name: str
    student_email: str
    answer_file_ref: str  # GridFS reference
    submitted_at: str
    status: str  # "submitted", "graded"

class AnnotationData(BaseModel):
    """Represents a single annotation on an answer paper"""
    type: str  # checkmark, score_circle, flag_circle, step_label, point_number, cross_mark, error_underline
    x: int = 0  # X coordinate (pixel), auto-calculated from box_2d if not provided
    y: int = 0  # Y coordinate (pixel), auto-calculated from box_2d if not provided
    text: str = ""
    color: str = "green"
    size: int = 30
    page_index: int = 0  # Which page/image this annotation belongs to
    box_2d: Optional[List[int]] = None  # [ymin, xmin, ymax, xmax] normalized 0-1000

class SubQuestionScore(BaseModel):
    sub_id: str
    max_marks: float
    obtained_marks: float
    ai_feedback: str
    annotations: List[AnnotationData] = []  # Annotations for this sub-question

class QuestionScore(BaseModel):
    question_number: int
    max_marks: float
    obtained_marks: float
    ai_feedback: str
    teacher_comment: Optional[str] = None
    is_reviewed: bool = False
    sub_scores: List[SubQuestionScore] = []  # For sub-question scores
    question_text: Optional[str] = None  # The question text
    status: str = "graded"  # graded, not_attempted, not_found, error
    annotations: List[AnnotationData] = []  # Annotations for this question
    page_number: Optional[int] = None  # Which page (1-indexed) the answer is on
    y_position: Optional[int] = None  # Vertical position (0-1000) on the page

class Submission(BaseModel):
    model_config = ConfigDict(extra="ignore")
    submission_id: str
    exam_id: str
    student_id: str
    student_name: str
    file_data: Optional[str] = None
    file_images: Optional[List[str]] = None  # Original student answer images
    annotated_images: Optional[List[str]] = None  # Annotated images with grading marks
    total_score: float = 0
    percentage: float = 0
    question_scores: List[QuestionScore] = []
    status: str = "pending"  # pending, ai_graded, teacher_reviewed
    graded_at: Optional[datetime] = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

class ReEvaluationRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")
    request_id: str
    submission_id: str
    student_id: str
    student_name: str
    exam_id: str
    questions: List[int]
    reason: str
    status: str = "pending"  # pending, in_review, resolved
    response: Optional[str] = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

class ReEvaluationCreate(BaseModel):
    submission_id: str
    questions: List[int]
    reason: str

# Feedback system schemas
class GradingFeedback(BaseModel):
    feedback_id: str
    teacher_id: str
    submission_id: Optional[str] = None
    question_number: Optional[int] = None
    feedback_type: str  # "question_grading", "general_suggestion", "correction"
    
    # Context for grading feedback
    question_text: Optional[str] = None
    student_answer_summary: Optional[str] = None
    ai_grade: Optional[float] = None
    ai_feedback: Optional[str] = None
    teacher_expected_grade: Optional[float] = None
    teacher_correction: str  # The actual feedback/correction
    
    # Metadata
    grading_mode: Optional[str] = None
    exam_id: Optional[str] = None
    is_common: bool = False  # Marked if pattern appears across multiple teachers
    upvote_count: int = 0
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

class FeedbackSubmit(BaseModel):
    submission_id: Optional[str] = None
    exam_id: Optional[str] = None
    question_number: Optional[int] = None
    sub_question_id: Optional[str] = None  # New: For sub-question specific feedback
    feedback_type: str
    teacher_correction: str
    question_text: Optional[str] = None
    ai_grade: Optional[float] = None
    ai_feedback: Optional[str] = None
    teacher_expected_grade: Optional[float] = None
    apply_to_all_papers: Optional[bool] = False  # New: Apply to all students
    question_topic: Optional[str] = None  # NEW: For pattern matching across exams

# ============== FILE HELPER FUNCTIONS ==============

def get_paper_hash(student_images, model_answer_images, questions, grading_mode):
    # Use sha256 for stable hashing of images instead of hash() which is randomized per process
    content = {
        "student": [hashlib.sha256(img.encode()).hexdigest() for img in student_images],
        "model": [hashlib.sha256(img.encode()).hexdigest() for img in model_answer_images],
        "questions": str(questions),
        "mode": grading_mode
    }
    return hashlib.sha256(json.dumps(content, sort_keys=True, default=str).encode()).hexdigest()

def get_model_answer_hash(images):
    # Use sha256 for stable hashing
    image_hashes = [hashlib.sha256(img.encode()).hexdigest() for img in images]
    return hashlib.sha256(json.dumps(image_hashes).encode()).hexdigest()

async def get_exam_model_answer_images(exam_id: str) -> List[str]:
    """Get model answer images from GridFS or fallback to old storage"""
    # First try GridFS storage (new method)
    file_doc = await db.exam_files.find_one(
        {"exam_id": exam_id, "file_type": "model_answer"},
        {"_id": 0, "gridfs_id": 1, "images": 1}
    )
    
    if file_doc:
        # Try GridFS first (new storage)
        if file_doc.get("gridfs_id"):
            try:
                from bson import ObjectId
                gridfs_file = fs.get(ObjectId(file_doc["gridfs_id"]))
                images = pickle.loads(gridfs_file.read())
                return images
            except Exception as e:
                logger.error(f"Error retrieving from GridFS: {e}")
        
        # Fallback to direct images storage (old method, still supported)
        if file_doc.get("images"):
            return file_doc["images"]
    
    # Fallback to very old storage in exam document
    exam = await db.exams.find_one({"exam_id": exam_id}, {"_id": 0, "model_answer_images": 1})
    if exam and exam.get("model_answer_images"):
        return exam["model_answer_images"]
    
    return []

async def get_exam_question_paper_images(exam_id: str) -> List[str]:
    """Get question paper images from GridFS or fallback to old storage"""
    # First try GridFS storage (new method)
    file_doc = await db.exam_files.find_one(
        {"exam_id": exam_id, "file_type": "question_paper"},
        {"_id": 0, "gridfs_id": 1, "images": 1}
    )
    
    if file_doc:
        # Try GridFS first (new storage)
        if file_doc.get("gridfs_id"):
            try:
                from bson import ObjectId
                gridfs_file = fs.get(ObjectId(file_doc["gridfs_id"]))
                images = pickle.loads(gridfs_file.read())
                return images
            except Exception as e:
                logger.error(f"Error retrieving from GridFS: {e}")
        
        # Fallback to direct images storage (old method, still supported)
        if file_doc.get("images"):
            return file_doc["images"]
    
    # Fallback to very old storage in exam document
    exam = await db.exams.find_one({"exam_id": exam_id}, {"_id": 0, "question_paper_images": 1})
    if exam and exam.get("question_paper_images"):
        return exam["question_paper_images"]
    
    return []

def validate_question_structure(questions: List[dict]) -> Dict[str, Any]:
    """
    Validate question structure for consistency.
    Returns validation result with warnings/errors.
    """
    warnings = []
    errors = []
    
    if not questions:
        errors.append("No questions found")
        return {"valid": False, "errors": errors, "warnings": warnings}
    
    total_marks = 0
    question_numbers = set()
    
    for idx, q in enumerate(questions):
        q_num = q.get("question_number")
        
        # Check for missing question number
        if not q_num:
            errors.append(f"Question at index {idx} is missing question_number")
            continue
        
        # Check for duplicate question numbers
        if q_num in question_numbers:
            errors.append(f"Duplicate question number: Q{q_num}")
        question_numbers.add(q_num)
        
        # Check for missing marks
        q_marks = q.get("max_marks", 0)
        if q_marks <= 0:
            errors.append(f"Q{q_num}: Missing or invalid max_marks")
        
        total_marks += q_marks
        
        # Validate sub-questions
        sub_questions = q.get("sub_questions", [])
        if sub_questions:
            sub_total = 0
            for sub in sub_questions:
                sub_marks = sub.get("max_marks", 0)
                sub_total += sub_marks
                
                # Check nested sub-questions
                if "sub_questions" in sub and sub["sub_questions"]:
                    nested_total = sum(ssub.get("max_marks", 0) for ssub in sub["sub_questions"])
                    if abs(nested_total - sub_marks) > 0.1:
                        warnings.append(f"Q{q_num}({sub.get('sub_id')}): Sub-question marks ({nested_total}) don't match parent ({sub_marks})")
            
            # Check if sub-question marks match parent
            if abs(sub_total - q_marks) > 0.1:
                warnings.append(f"Q{q_num}: Sub-question total ({sub_total}) doesn't match question total ({q_marks})")
    
    # Check for missing question numbers in sequence
    if question_numbers:
        max_num = max(question_numbers)
        expected = set(range(1, max_num + 1))
        missing = expected - question_numbers
        if missing:
            warnings.append(f"Missing question numbers: {sorted(missing)}")
    
    return {
        "valid": len(errors) == 0,
        "errors": errors,
        "warnings": warnings,
        "total_marks": total_marks,
        "question_count": len(questions)
    }

async def exam_has_model_answer(exam_id: str) -> bool:
    """Check if exam has model answer uploaded"""
    # Check new collection first
    file_doc = await db.exam_files.find_one(
        {"exam_id": exam_id, "file_type": "model_answer"},
        {"_id": 0}
    )
    if file_doc:
        return True
    
    # Fallback check
    exam = await db.exams.find_one({"exam_id": exam_id}, {"_id": 0, "model_answer_images": 1, "has_model_answer": 1})
    return bool(exam and (exam.get("has_model_answer") or exam.get("model_answer_images")))

# ============== AUTH HELPERS ==============

async def get_current_user(request: Request) -> User:
    """Get current user from session token (supports both OAuth sessions and JWT tokens)"""
    session_token = request.cookies.get("session_token")
    
    if not session_token:
        auth_header = request.headers.get("Authorization")
        if auth_header and auth_header.startswith("Bearer "):
            session_token = auth_header.split(" ")[1]
    
    if not session_token:
        raise HTTPException(status_code=401, detail="Not authenticated")
    
    # Try to decode as JWT first
    jwt_payload = decode_token(session_token)
    if jwt_payload:
        # JWT token - extract user_id and fetch from database
        user_id = jwt_payload.get("user_id")
        if not user_id:
            raise HTTPException(status_code=401, detail="Invalid token")
        
        user = await db.users.find_one({"user_id": user_id}, {"_id": 0})
        if not user:
            raise HTTPException(status_code=401, detail="User not found")
        
        # Check account status
        account_status = user.get("account_status", "active")
        if account_status == "banned":
            raise HTTPException(status_code=403, detail="Account banned. Contact support.")
        elif account_status == "disabled":
            raise HTTPException(status_code=403, detail="Account disabled. Contact support.")
        
        # Return user object
        return User(
            user_id=user["user_id"],
            email=user["email"],
            name=user["name"],
            role=user["role"],
            picture=user.get("picture")
        )
    
    # Fallback to session-based auth (OAuth)
    session = await db.user_sessions.find_one(
        {"session_token": session_token},
        {"_id": 0}
    )
    
    if not session:
        raise HTTPException(status_code=401, detail="Invalid session")
    
    expires_at = session.get("expires_at")
    if isinstance(expires_at, str):
        expires_at = datetime.fromisoformat(expires_at)
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    if expires_at < datetime.now(timezone.utc):
        raise HTTPException(status_code=401, detail="Session expired")
    
    user = await db.users.find_one(
        {"user_id": session["user_id"]},
        {"_id": 0}
    )
    
    if not user:
        raise HTTPException(status_code=401, detail="User not found")
    
    # Check account status - ENFORCE DISABLED/BANNED STATUS
    account_status = user.get("account_status", "active")
    if account_status == "banned":
        raise HTTPException(
            status_code=403, 
            detail="Your account has been banned. Contact support for assistance."
        )
    elif account_status == "disabled":
        raise HTTPException(
            status_code=403, 
            detail="Your account has been temporarily disabled. Contact support for assistance."
        )
    
    # Update last_login timestamp (throttled - only update if more than 5 minutes since last update)
    last_login = user.get("last_login")
    should_update = False
    
    if not last_login:
        should_update = True
    else:
        try:
            if isinstance(last_login, str):
                last_login_dt = datetime.fromisoformat(last_login.replace('Z', '+00:00'))
            else:
                last_login_dt = last_login
            
            if last_login_dt.tzinfo is None:
                last_login_dt = last_login_dt.replace(tzinfo=timezone.utc)
            
            # Update if more than 5 minutes have passed
            time_since_last_update = datetime.now(timezone.utc) - last_login_dt
            if time_since_last_update.total_seconds() > 300:  # 5 minutes
                should_update = True
        except:
            should_update = True
    
    if should_update:
        await db.users.update_one(
            {"user_id": user["user_id"]},
            {"$set": {"last_login": datetime.now(timezone.utc).isoformat()}}
        )
        user["last_login"] = datetime.now(timezone.utc).isoformat()
    
    return User(**user)

# ============== AUTH ROUTES ==============

@api_router.post("/auth/session")
async def create_session(request: Request, response: Response):
    """Exchange session_id for session_token"""
    data = await request.json()
    session_id = data.get("session_id")
    preferred_role = data.get("preferred_role", "teacher")
    
    logger.info(f"=== AUTH SESSION REQUEST === session_id: {session_id[:20] if session_id else 'None'}..., role: {preferred_role}")
    
    if not session_id:
        raise HTTPException(status_code=400, detail="session_id required")
    
    # Call Emergent auth service
    logger.info("Calling Emergent auth service...")
    async with httpx.AsyncClient(timeout=10.0) as client:  # 10 second timeout
        try:
            auth_response = await client.get(
                "https://demobackend.emergentagent.com/auth/v1/env/oauth/session-data",
                headers={"X-Session-ID": session_id}
            )
            logger.info(f"Emergent auth response status: {auth_response.status_code}")
            
            if auth_response.status_code != 200:
                logger.error(f"Auth service returned {auth_response.status_code}: {auth_response.text}")
                
                # Parse the error response to provide better error messages
                try:
                    error_data = auth_response.json()
                    error_detail = error_data.get("detail", {})
                    if isinstance(error_detail, dict):
                        error_msg = error_detail.get("error_description", "Invalid or expired session")
                    else:
                        error_msg = str(error_detail)
                except:
                    error_msg = "Invalid or expired session"
                
                raise HTTPException(
                    status_code=401, 
                    detail=f"Authentication failed: {error_msg}. Please try logging in again."
                )
            
            auth_data = auth_response.json()
            logger.info(f"Auth data received for email: {auth_data.get('email')}")
        except httpx.TimeoutException:
            logger.error("Auth service timeout after 10 seconds")
            raise HTTPException(status_code=504, detail="Auth service timeout - please try again")
        except httpx.HTTPError as e:
            logger.error(f"Auth service HTTP error: {str(e)}")
            raise HTTPException(status_code=500, detail="Auth service connection error")
        except Exception as e:
            logger.error(f"Auth service error: {str(e)}", exc_info=True)
            raise HTTPException(status_code=500, detail=f"Auth service error: {str(e)}")
    
    # Extract user data from auth response
    user_email = auth_data.get("email")
    user_name = auth_data.get("name")
    user_picture = auth_data.get("picture")
    
    if not user_email:
        raise HTTPException(status_code=400, detail="Email not found in auth data")
    
    # IMPORTANT: Check if this email was already created by a teacher as a student
    existing_student = await db.users.find_one({
        "email": user_email,
        "role": "student"
    }, {"_id": 0})
    
    if existing_student:
        # Student record exists (teacher created it)
        # Just update login info and return
        user_id = existing_student["user_id"]
        await db.users.update_one(
            {"user_id": user_id},
            {"$set": {
                "name": user_name,  # Update with Google name
                "picture": user_picture,
                "profile_completed": True,  # Existing students are considered complete
                "last_login": datetime.now(timezone.utc).isoformat()
            }}
        )
        user_role = "student"
    else:
        # Check if user exists with different role
        existing_user = await db.users.find_one({"email": user_email}, {"_id": 0})
        
        if existing_user:
            user_id = existing_user["user_id"]
            # Update user data and mark profile as completed for existing users
            await db.users.update_one(
                {"user_id": user_id},
                {"$set": {
                    "name": user_name,
                    "picture": user_picture,
                    "profile_completed": True,  # Existing users are considered complete
                    "last_login": datetime.now(timezone.utc).isoformat()
                }}
            )
            user_role = existing_user.get("role", "teacher")
        else:
            # Create new user with preferred role
            user_id = f"user_{uuid.uuid4().hex[:12]}"
            user_role = preferred_role if preferred_role in ["teacher", "student"] else "teacher"
            new_user = {
                "user_id": user_id,
                "email": user_email,
                "name": user_name,
                "picture": user_picture,
                "role": user_role,
                "batches": [],
                "profile_completed": False,  # New users need to complete profile
                "created_at": datetime.now(timezone.utc).isoformat(),
                "last_login": datetime.now(timezone.utc).isoformat()
            }
            await db.users.insert_one(new_user)
    
    # Check account status before creating session
    final_user = await db.users.find_one({"user_id": user_id}, {"_id": 0})
    account_status = final_user.get("account_status", "active")
    
    if account_status == "banned":
        raise HTTPException(
            status_code=403,
            detail="Your account has been banned. Contact support at gradingtoolaibased@gmail.com for assistance."
        )
    elif account_status == "disabled":
        raise HTTPException(
            status_code=403,
            detail="Your account has been temporarily disabled. Contact support at gradingtoolaibased@gmail.com for assistance."
        )
    
    # Create session token
    session_token = f"session_{uuid.uuid4().hex}"
    expires_at = (datetime.now(timezone.utc) + timedelta(days=7)).isoformat()
    
    # Store session in database
    await db.user_sessions.insert_one({
        "session_token": session_token,
        "user_id": user_id,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "expires_at": expires_at
    })
    
    # Set cookie
    response.set_cookie(
        key="session_token",
        value=session_token,
        httponly=True,
        max_age=7 * 24 * 60 * 60,  # 7 days
        samesite="lax",
        secure=True,
        path="/"
    )
    
    # Return user data
    return {
        "user_id": user_id,
        "email": user_email,
        "name": user_name,
        "picture": user_picture,
        "role": user_role,
        "session_token": session_token
    }



# ============== JWT AUTH ENDPOINTS (Email/Password Alternative) ==============
from auth_utils import verify_password, get_password_hash, create_access_token, decode_token
from pydantic import EmailStr, Field

class RegisterRequest(BaseModel):
    email: EmailStr
    password: str = Field(..., min_length=8, description="Password must be at least 8 characters")
    name: str
    role: str = Field(..., pattern="^(teacher|student)$")

class LoginRequest(BaseModel):
    email: EmailStr
    password: str

@api_router.post("/auth/register")
async def register_user(request: RegisterRequest, response: Response):
    """Register a new user with email and password (JWT-based auth)"""
    # Check if user already exists
    existing_user = await db.users.find_one({"email": request.email}, {"_id": 0})
    if existing_user:
        raise HTTPException(status_code=400, detail="Email already registered")
    
    # Create new user
    user_id = str(uuid.uuid4())
    hashed_password = get_password_hash(request.password)
    
    new_user = {
        "user_id": user_id,
        "email": request.email,
        "name": request.name,
        "role": request.role,
        "password_hash": hashed_password,
        "auth_type": "jwt",  # Mark as JWT auth vs OAuth
        "picture": None,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "last_login": datetime.now(timezone.utc).isoformat(),
        "account_status": "active"
    }
    
    await db.users.insert_one(new_user)
    logger.info(f"New user registered via JWT: {request.email} as {request.role}")
    
    # Create JWT token
    token_data = {
        "user_id": user_id,
        "email": request.email,
        "role": request.role
    }
    access_token = create_access_token(token_data)
    
    # Set cookie
    response.set_cookie(
        key="session_token",
        value=access_token,
        httponly=True,
        max_age=7 * 24 * 60 * 60,  # 7 days
        samesite="lax",
        secure=True,
        path="/"
    )
    
    return {
        "user_id": user_id,
        "email": request.email,
        "name": request.name,
        "role": request.role,
        "token": access_token
    }


class SetPasswordRequest(BaseModel):
    email: EmailStr
    new_password: str = Field(..., min_length=6, description="Password must be at least 6 characters")

@api_router.post("/auth/set-password")
async def set_password_for_google_account(request: SetPasswordRequest):
    """Allow Google OAuth users to set a password for email/password login"""
    # Find user
    user = await db.users.find_one({"email": request.email}, {"_id": 0})
    if not user:
        raise HTTPException(status_code=404, detail="No account found with this email")
    
    # Check if this is a Google account without a password
    if "password_hash" in user:
        raise HTTPException(status_code=400, detail="This account already has a password. Use the login page or reset password if you forgot it.")
    
    # Set password hash and mark profile as completed for existing Google users
    password_hash = get_password_hash(request.new_password)
    await db.users.update_one(
        {"email": request.email},
        {"$set": {
            "password_hash": password_hash,
            "password_set_at": datetime.now(timezone.utc).isoformat(),
            "profile_completed": True  # Mark existing Google users as having completed profile
        }}
    )
    
    return {
        "message": "Password set successfully! You can now login with your email and password."
    }

@api_router.post("/auth/login")
async def login_user(request: LoginRequest, response: Response):
    """Login with email and password (JWT-based auth)"""
    # Find user
    user = await db.users.find_one({"email": request.email}, {"_id": 0})
    if not user:
        raise HTTPException(status_code=401, detail="Invalid email or password")
    
    # Verify password
    if "password_hash" not in user:
        raise HTTPException(
            status_code=400, 
            detail="This account uses Google sign-in. Please use the 'Sign in with Google' button."
        )
    
    if not verify_password(request.password, user["password_hash"]):
        raise HTTPException(status_code=401, detail="Invalid email or password")
    
    # Check account status
    account_status = user.get("account_status", "active")
    if account_status == "banned":
        raise HTTPException(status_code=403, detail="Account banned. Contact support.")
    elif account_status == "disabled":
        raise HTTPException(status_code=403, detail="Account disabled. Contact support.")
    
    # Update last login
    await db.users.update_one(
        {"user_id": user["user_id"]},
        {"$set": {"last_login": datetime.now(timezone.utc).isoformat()}}
    )
    
    # Create JWT token
    token_data = {
        "user_id": user["user_id"],
        "email": user["email"],
        "role": user["role"]
    }
    access_token = create_access_token(token_data)
    
    # Set cookie
    response.set_cookie(
        key="session_token",
        value=access_token,
        httponly=True,
        max_age=7 * 24 * 60 * 60,  # 7 days
        samesite="lax",
        secure=True,
        path="/"
    )
    
    logger.info(f"User logged in via JWT: {user['email']}")
    
    return {
        "user_id": user["user_id"],
        "email": user["email"],
        "name": user.get("name"),
        "picture": user.get("picture"),
        "role": user["role"],
        "token": access_token,
        "profile_completed": user.get("profile_completed", True)  # Default to True for existing users
    }

# ============== ADMIN ROUTES (PROTECTED) ==============

@api_router.get("/admin/export-users")
async def admin_export_users(
    api_key: str,
    format: str = "json",
    role: Optional[str] = None,
    created_after: Optional[str] = None,
    last_login_after: Optional[str] = None,
    fields: Optional[str] = None
):
    """
    Protected admin endpoint to export user data
    Only accessible with valid ADMIN_API_KEY
    
    Parameters:
    - api_key: Admin API key (required)
    - format: json, csv (default: json)
    - role: Filter by role (teacher/student)
    - created_after: Filter users created after date (YYYY-MM-DD)
    - last_login_after: Filter users who logged in after date (YYYY-MM-DD)
    - fields: Comma-separated list of fields to include
    """
    import csv
    from io import StringIO
    from fastapi.responses import StreamingResponse
    
    # Verify admin API key
    ADMIN_API_KEY = os.environ.get('ADMIN_API_KEY')
    if not ADMIN_API_KEY or api_key != ADMIN_API_KEY:
        raise HTTPException(status_code=403, detail="Unauthorized - Invalid API key")
    
    # Build query filter
    query = {}
    if role:
        query["role"] = role
    if created_after:
        query["created_at"] = {"$gte": created_after}
    if last_login_after:
        query["last_login"] = {"$gte": last_login_after}
    
    # Fetch users
    users = await db.users.find(query, {"_id": 0}).to_list(10000)
    
    # Enrich with session data
    for user in users:
        # Get active sessions
        sessions = await db.user_sessions.find(
            {"user_id": user["user_id"]},
            {"_id": 0}
        ).to_list(100)
        
        user["active_sessions_count"] = len(sessions)
        user["sessions"] = sessions
        
        # Count submissions (for teachers)
        if user.get("role") == "teacher":
            exam_count = await db.exams.count_documents({"teacher_id": user["user_id"]})
            user["exams_created"] = exam_count
        
        # Count submissions (for students)
        if user.get("role") == "student":
            submission_count = await db.submissions.count_documents({"student_id": user["user_id"]})
            user["submissions_count"] = submission_count
    
    # Filter fields if specified
    if fields:
        field_list = [f.strip() for f in fields.split(",")]
        users = [
            {k: v for k, v in user.items() if k in field_list}
            for user in users
        ]
    
    # Return in requested format
    if format.lower() == "csv":
        # Convert to CSV
        if not users:
            return StreamingResponse(
                iter(["No data found"]),
                media_type="text/csv",
                headers={"Content-Disposition": "attachment; filename=users_export.csv"}
            )
        
        output = StringIO()
        
        # Get all unique field names from all users
        all_fields = set()
        for user in users:
            all_fields.update(user.keys())
        
        # Sort fields for consistent ordering
        fieldnames = sorted(list(all_fields))
        
        writer = csv.DictWriter(output, fieldnames=fieldnames, extrasaction='ignore')
        writer.writeheader()
        
        # Write each user, filling missing fields with empty string
        for user in users:
            row = {field: user.get(field, '') for field in fieldnames}
            writer.writerow(row)
        
        return StreamingResponse(
            iter([output.getvalue()]),
            media_type="text/csv",
            headers={"Content-Disposition": "attachment; filename=users_export.csv"}
        )
    
    # Default: JSON format
    return {
        "total_users": len(users),
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "filters_applied": {
            "role": role,
            "created_after": created_after,
            "last_login_after": last_login_after,
            "fields": fields
        },
        "users": users
    }


# ============== NOTIFICATIONS ROUTES ==============

@api_router.get("/notifications")
async def get_notifications(user: User = Depends(get_current_user)):
    """Get user's notifications"""
    notifications = await db.notifications.find(
        {"user_id": user.user_id},
        {"_id": 0}
    ).sort("created_at", -1).limit(50).to_list(50)
    
    unread_count = await db.notifications.count_documents({
        "user_id": user.user_id,
        "is_read": False
    })
    
    return {
        "notifications": notifications,
        "unread_count": unread_count
    }

@api_router.put("/notifications/{notification_id}/read")
async def mark_notification_read(notification_id: str, user: User = Depends(get_current_user)):
    """Mark notification as read"""
    result = await db.notifications.update_one(
        {"notification_id": notification_id, "user_id": user.user_id},
        {"$set": {"is_read": True, "read_at": datetime.now(timezone.utc).isoformat()}}
    )
    
    if result.modified_count == 0:
        raise HTTPException(status_code=404, detail="Notification not found")
    
    return {"message": "Notification marked as read"}


@api_router.put("/notifications/mark-all-read")
async def mark_all_notifications_read(user: User = Depends(get_current_user)):
    """Mark all notifications as read"""
    result = await db.notifications.update_many(
        {"user_id": user.user_id, "is_read": False},
        {"$set": {"is_read": True, "read_at": datetime.now(timezone.utc).isoformat()}}
    )
    
    return {
        "message": "All notifications marked as read",
        "count": result.modified_count
    }

@api_router.delete("/notifications/clear-all")
async def clear_all_notifications(user: User = Depends(get_current_user)):
    """Clear (delete) all notifications"""
    result = await db.notifications.delete_many({"user_id": user.user_id})
    
    return {
        "message": "All notifications cleared",
        "count": result.deleted_count
    }

@api_router.delete("/notifications/{notification_id}")
async def delete_notification(notification_id: str, user: User = Depends(get_current_user)):
    """Delete a specific notification"""
    result = await db.notifications.delete_one(
        {"notification_id": notification_id, "user_id": user.user_id}
    )
    
    if result.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Notification not found")
    
    return {"message": "Notification deleted"}



@api_router.get("/dashboard/class-snapshot")
async def get_class_snapshot(
    batch_id: Optional[str] = None,
    user: User = Depends(get_current_user)
):
    """
    Get overall class performance snapshot for dashboard
    Returns: average score, pass rate, total exams, trends
    """
    if user.role != "teacher":
        raise HTTPException(status_code=403, detail="Teacher only")
    
    # Build query
    exam_query = {"teacher_id": user.user_id}
    if batch_id:
        exam_query["batch_id"] = batch_id
    
    # Get exams
    exams = await db.exams.find(exam_query, {"_id": 0, "exam_id": 1, "exam_name": 1, "created_at": 1, "batch_id": 1}).to_list(100)
    
    if not exams:
        return {
            "batch_name": "No Batch Selected",
            "total_students": 0,
            "class_average": 0,
            "pass_rate": 0,
            "total_exams": 0,
            "recent_exam": None,
            "trend": 0,
            "top_performers": [],
            "struggling_students": []
        }
    
    exam_ids = [e["exam_id"] for e in exams]
    
    # Get all submissions
    submissions = await db.submissions.find(
        {"exam_id": {"$in": exam_ids}},
        {"_id": 0, "student_id": 1, "student_name": 1, "percentage": 1, "created_at": 1, "exam_id": 1}
    ).to_list(10000)
    
    if not submissions:
        return {
            "batch_name": "No Data",
            "total_students": 0,
            "class_average": 0,
            "pass_rate": 0,
            "total_exams": len(exams),
            "recent_exam": exams[0].get("exam_name") if exams else None,
            "trend": 0,
            "top_performers": [],
            "struggling_students": []
        }
    
    # Calculate metrics
    total_students = len(set(s["student_id"] for s in submissions))
    class_average = sum(s["percentage"] for s in submissions) / len(submissions)
    pass_count = len([s for s in submissions if s["percentage"] >= 50])
    pass_rate = (pass_count / len(submissions)) * 100 if submissions else 0
    
    # Get batch name
    batch_name = "All Batches"
    if batch_id:
        batch = await db.batches.find_one({"batch_id": batch_id}, {"_id": 0, "name": 1})
        batch_name = batch.get("name") if batch else "Unknown Batch"
    
    # Get recent exam
    recent_exam = max(exams, key=lambda x: x.get("created_at", ""))
    
    # Calculate trend (compare last 3 exams vs previous 3)
    sorted_exams = sorted(exams, key=lambda x: x.get("created_at", ""), reverse=True)
    trend = 0
    
    if len(sorted_exams) >= 6:
        recent_exam_ids = [e["exam_id"] for e in sorted_exams[:3]]
        older_exam_ids = [e["exam_id"] for e in sorted_exams[3:6]]
        
        recent_subs = [s for s in submissions if s["exam_id"] in recent_exam_ids]
        older_subs = [s for s in submissions if s["exam_id"] in older_exam_ids]
        
        if recent_subs and older_subs:
            recent_avg = sum(s["percentage"] for s in recent_subs) / len(recent_subs)
            older_avg = sum(s["percentage"] for s in older_subs) / len(older_subs)
            trend = round(recent_avg - older_avg, 1)
    
    # Get top performers (by average)
    student_averages = {}
    for sub in submissions:
        sid = sub["student_id"]
        if sid not in student_averages:
            student_averages[sid] = {"name": sub["student_name"], "scores": []}
        student_averages[sid]["scores"].append(sub["percentage"])
    
    student_stats = []
    for sid, data in student_averages.items():
        avg = sum(data["scores"]) / len(data["scores"])
        student_stats.append({
            "student_id": sid,
            "student_name": data["name"],
            "average": round(avg, 1)
        })
    
    student_stats.sort(key=lambda x: x["average"], reverse=True)
    
    top_performers = student_stats[:3]
    struggling_students = [s for s in student_stats if s["average"] < 50][:3]
    
    return {
        "batch_name": batch_name,
        "total_students": total_students,
        "class_average": round(class_average, 1),
        "pass_rate": round(pass_rate, 1),
        "total_exams": len(exams),
        "recent_exam": recent_exam.get("exam_name", "Unknown"),
        "recent_exam_date": recent_exam.get("created_at", ""),
        "trend": trend,
        "top_performers": top_performers,
        "struggling_students": struggling_students
    }



@api_router.get("/dashboard/actionable-stats")
async def get_actionable_stats(
    batch_id: Optional[str] = None,
    user: User = Depends(get_current_user)
):
    """
    Get actionable insights for dashboard heads-up display
    Returns: pending reviews, quality concerns, performance trends, at-risk students, hardest concepts
    """
    if user.role != "teacher":
        raise HTTPException(status_code=403, detail="Teacher only")
    
    # Build query
    exam_query = {"teacher_id": user.user_id}
    if batch_id:
        exam_query["batch_id"] = batch_id
    
    # Get exams
    exams = await db.exams.find(exam_query, {"_id": 0}).to_list(100)
    
    if not exams:
        return {
            "action_required": {
                "pending_reviews": 0,
                "quality_concerns": 0,
                "total": 0,
                "papers": []
            },
            "performance": {
                "current_avg": 0,
                "previous_avg": 0,
                "trend": 0,
                "trend_direction": "stable"
            },
            "at_risk": {
                "count": 0,
                "students": [],
                "threshold": 40
            },
            "hardest_concept": None
        }
    
    exam_ids = [e["exam_id"] for e in exams]
    
    # Get all submissions
    submissions = await db.submissions.find(
        {"exam_id": {"$in": exam_ids}},
        {"_id": 0, "submission_id": 1, "exam_id": 1, "student_id": 1, "student_name": 1, "percentage": 1, "total_score": 1, "created_at": 1, "status": 1, "question_scores": 1}
    ).to_list(10000)
    
    # 1. ACTION REQUIRED: Pending Reviews + Quality Concerns
    pending_reviews = len([s for s in submissions if s.get("status") == "pending"])
    
    # Quality concerns: Students with long answers but low scores (bluff candidates)
    quality_concerns = []
    for sub in submissions:
        if sub.get("percentage", 0) < 50:  # Failed submissions
            for qs in sub.get("question_scores", []):
                answer_text = qs.get("answer_text", "")
                obtained = qs.get("obtained_marks", 0)
                max_marks = qs.get("max_marks", 1)
                percentage = (obtained / max_marks) * 100 if max_marks > 0 else 0
                
                # Long answer but very low score = quality concern
                if len(answer_text) > 100 and percentage < 30:
                    quality_concerns.append({
                        "submission_id": sub["submission_id"],
                        "student_name": sub["student_name"],
                        "exam_id": sub["exam_id"]
                    })
                    break  # One flag per submission
    
    quality_concerns = quality_concerns[:10]  # Limit to 10
    
    # 2. PERFORMANCE: Current vs Previous Avg
    sorted_exams = sorted(exams, key=lambda x: x.get("created_at", ""), reverse=True)
    
    current_avg = 0
    previous_avg = 0
    trend = 0
    
    if len(sorted_exams) >= 2:
        # Recent exams (last 2)
        recent_exam_ids = [e["exam_id"] for e in sorted_exams[:2]]
        recent_subs = [s for s in submissions if s["exam_id"] in recent_exam_ids]
        
        # Previous exams (2-4)
        if len(sorted_exams) >= 4:
            prev_exam_ids = [e["exam_id"] for e in sorted_exams[2:4]]
            prev_subs = [s for s in submissions if s["exam_id"] in prev_exam_ids]
            
            if recent_subs and prev_subs:
                current_avg = sum(s["percentage"] for s in recent_subs) / len(recent_subs)
                previous_avg = sum(s["percentage"] for s in prev_subs) / len(prev_subs)
                trend = current_avg - previous_avg
    elif submissions:
        # If not enough exams, just use overall average
        current_avg = sum(s["percentage"] for s in submissions) / len(submissions)
    
    trend_direction = "up" if trend > 2 else "down" if trend < -2 else "stable"
    
    # 3. AT RISK: Students scoring <40% in recent exams
    at_risk_students = {}
    
    # Get recent submissions (last 2 exams)
    if len(sorted_exams) >= 2:
        recent_exam_ids = [e["exam_id"] for e in sorted_exams[:2]]
        recent_subs = [s for s in submissions if s["exam_id"] in recent_exam_ids]
        
        for sub in recent_subs:
            if sub["percentage"] < 40:
                sid = sub["student_id"]
                if sid not in at_risk_students:
                    at_risk_students[sid] = {
                        "student_id": sid,
                        "student_name": sub["student_name"],
                        "avg_score": sub["percentage"],
                        "exams_failed": 1
                    }
                else:
                    at_risk_students[sid]["exams_failed"] += 1
    
    at_risk_list = list(at_risk_students.values())
    at_risk_list.sort(key=lambda x: x["avg_score"])  # Worst first
    
    # 4. HARDEST CONCEPT: Question with lowest correct rate
    question_performance = {}
    
    for sub in submissions:
        for qs in sub.get("question_scores", []):
            q_key = f"{sub['exam_id']}_{qs.get('question_number')}"
            
            if q_key not in question_performance:
                question_performance[q_key] = {
                    "exam_id": sub["exam_id"],
                    "question_number": qs.get("question_number"),
                    "total_attempts": 0,
                    "total_score": 0,
                    "max_marks": qs.get("max_marks", 0)
                }
            
            question_performance[q_key]["total_attempts"] += 1
            question_performance[q_key]["total_score"] += qs.get("obtained_marks", 0)
    
    # Calculate success rates
    question_stats = []
    for q_key, data in question_performance.items():
        if data["total_attempts"] > 0:
            avg_obtained = data["total_score"] / data["total_attempts"]
            success_rate = (avg_obtained / data["max_marks"]) * 100 if data["max_marks"] > 0 else 0
            
            # Get question details
            exam = await db.exams.find_one({"exam_id": data["exam_id"]}, {"_id": 0, "exam_name": 1, "questions": 1})
            if exam:
                for q in exam.get("questions", []):
                    if q.get("question_number") == data["question_number"]:
                        question_stats.append({
                            "exam_id": data["exam_id"],
                            "exam_name": exam.get("exam_name", "Unknown"),
                            "question_number": data["question_number"],
                            "topic": q.get("rubric", "")[:50] + "..." if len(q.get("rubric", "")) > 50 else q.get("rubric", "Unknown"),
                            "success_rate": round(success_rate, 1),
                            "attempts": data["total_attempts"]
                        })
                        break
    
    # Find hardest (minimum 5 attempts to be statistically relevant)
    valid_questions = [q for q in question_stats if q["attempts"] >= 5]
    hardest = min(valid_questions, key=lambda x: x["success_rate"]) if valid_questions else None
    
    return {
        "action_required": {
            "pending_reviews": pending_reviews,
            "quality_concerns": len(quality_concerns),
            "total": pending_reviews + len(quality_concerns),
            "papers": quality_concerns[:5]  # Top 5 for display
        },
        "performance": {
            "current_avg": round(current_avg, 1),
            "previous_avg": round(previous_avg, 1),
            "trend": round(trend, 1),
            "trend_direction": trend_direction
        },
        "at_risk": {
            "count": len(at_risk_list),
            "students": at_risk_list[:5],  # Top 5 worst
            "threshold": 40
        },
        "hardest_concept": hardest
    }




async def create_notification(user_id: str, notification_type: str, title: str, message: str, link: str = None):
    """Helper function to create notifications"""
    notification_id = f"notif_{uuid.uuid4().hex[:12]}"
    notification = {
        "notification_id": notification_id,
        "user_id": user_id,
        "type": notification_type,
        "title": title,
        "message": message,
        "link": link,
        "is_read": False,
        "created_at": datetime.now(timezone.utc).isoformat()
    }
    await db.notifications.insert_one(notification)
    return notification_id

# ============== SEARCH ROUTE ==============

@api_router.post("/search")
async def global_search(query: str, user: User = Depends(get_current_user)):
    """Global search across exams, students, batches, submissions"""
    results = {
        "exams": [],
        "students": [],
        "batches": [],
        "submissions": []
    }
    
    if not query or len(query) < 2:
        return results
    
    search_regex = {"$regex": query, "$options": "i"}
    
    # Search exams
    if user.role == "teacher":
        exams = await db.exams.find(
            {"teacher_id": user.user_id, "exam_name": search_regex},
            {"_id": 0, "exam_id": 1, "exam_name": 1, "exam_date": 1, "status": 1}
        ).limit(10).to_list(10)
        results["exams"] = exams
        
        # Search students
        students = await db.users.find(
            {
                "teacher_id": user.user_id,
                "role": "student",
                "$or": [
                    {"name": search_regex},
                    {"student_id": search_regex},
                    {"email": search_regex}
                ]
            },
            {"_id": 0, "user_id": 1, "name": 1, "student_id": 1, "email": 1}
        ).limit(10).to_list(10)
        results["students"] = students
        
        # Search batches
        batches = await db.batches.find(
            {"teacher_id": user.user_id, "name": search_regex},
            {"_id": 0, "batch_id": 1, "name": 1}
        ).limit(10).to_list(10)
        results["batches"] = batches
        
        # Search submissions by student name
        submissions = await db.submissions.find(
            {"student_name": search_regex},
            {"_id": 0, "submission_id": 1, "student_name": 1, "exam_id": 1, "percentage": 1}
        ).limit(10).to_list(10)
        results["submissions"] = submissions
    
    elif user.role == "student":
        # Students can only search their own data
        exams = await db.submissions.find(
            {"student_id": user.user_id},
            {"_id": 0, "exam_id": 1, "submission_id": 1}
        ).limit(10).to_list(10)
        
        # Get exam details for matched submissions
        if exams:
            exam_ids = [e["exam_id"] for e in exams]
            exam_details = await db.exams.find(
                {"exam_id": {"$in": exam_ids}, "exam_name": search_regex},
                {"_id": 0, "exam_id": 1, "exam_name": 1, "exam_date": 1}
            ).to_list(10)
            results["exams"] = exam_details
    
    return results

    
    # Check if user exists
    user = await db.users.find_one({"email": auth_data["email"]}, {"_id": 0})
    
    if user:
        # Update existing user
        await db.users.update_one(
            {"email": auth_data["email"]},
            {"$set": {
                "name": auth_data["name"],
                "picture": auth_data.get("picture")
            }}
        )
        user_id = user["user_id"]
        role = user.get("role", "teacher")
    else:
        # Create new user (default to teacher)
        user_id = f"user_{uuid.uuid4().hex[:12]}"
        new_user = {
            "user_id": user_id,
            "email": auth_data["email"],
            "name": auth_data["name"],
            "picture": auth_data.get("picture"),
            "role": "teacher",
            "batches": [],
            "created_at": datetime.now(timezone.utc).isoformat()
        }
        await db.users.insert_one(new_user)
        role = "teacher"
    
    # Create session
    session_token = auth_data.get("session_token", f"session_{uuid.uuid4().hex}")
    expires_at = datetime.now(timezone.utc) + timedelta(days=7)
    
    await db.user_sessions.insert_one({
        "user_id": user_id,
        "session_token": session_token,
        "expires_at": expires_at.isoformat(),
        "created_at": datetime.now(timezone.utc).isoformat()
    })
    
    # Set cookie
    response.set_cookie(
        key="session_token",
        value=session_token,
        httponly=True,
        samesite="lax",
        path="/",
        max_age=7 * 24 * 60 * 60
    )
    
    return {
        "user_id": user_id,
        "email": auth_data["email"],
        "name": auth_data["name"],
        "picture": auth_data.get("picture"),
        "role": role
    }

@api_router.get("/auth/me")
async def get_me(user: User = Depends(get_current_user)):
    """Get current user info"""
    return {
        "user_id": user.user_id,
        "email": user.email,
        "name": user.name,
        "picture": user.picture,
        "role": user.role,
        "batches": user.batches
    }

@api_router.post("/auth/logout")
async def logout(request: Request, response: Response):
    """Logout and clear session"""
    session_token = request.cookies.get("session_token")
    if session_token:
        await db.user_sessions.delete_one({"session_token": session_token})
    
    response.delete_cookie(key="session_token", path="/")
    return {"message": "Logged out"}

# ============== PROFILE MANAGEMENT ==============

@api_router.put("/profile/complete")
async def complete_profile(
    profile: ProfileUpdate,
    user: User = Depends(get_current_user)
):
    """Complete user profile on first login"""
    try:
        # Validate teacher_type
        valid_teacher_types = ["school", "college", "competitive", "others"]
        if profile.teacher_type not in valid_teacher_types:
            raise HTTPException(status_code=400, detail="Invalid teacher type")
        
        # Validate exam_category if competitive
        if profile.teacher_type == "competitive":
            valid_exam_categories = ["UPSC", "CA", "CLAT", "JEE", "NEET", "others"]
            if not profile.exam_category or profile.exam_category not in valid_exam_categories:
                raise HTTPException(status_code=400, detail="Exam category required for competitive exams")
        
        # Update user profile
        update_data = {
            "name": profile.name,
            "contact": profile.contact,
            "email": profile.email,
            "teacher_type": profile.teacher_type,
            "exam_category": profile.exam_category if profile.teacher_type == "competitive" else None,
            "profile_completed": True,
            "updated_at": datetime.now(timezone.utc).isoformat()
        }
        
        await db.users.update_one(
            {"user_id": user.user_id},
            {"$set": update_data}
        )
        
        # Return updated user
        updated_user = await db.users.find_one({"user_id": user.user_id}, {"_id": 0})
        return updated_user
    
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error completing profile: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Failed to complete profile: {str(e)}")

@api_router.get("/profile/check")
async def check_profile_completion(user: User = Depends(get_current_user)):
    """Check if user has completed profile setup"""
    # For existing users (profile_completed is None/null), assume profile is complete
    # Only new users will have profile_completed explicitly set to False
    profile_completed = user.profile_completed if hasattr(user, 'profile_completed') else None
    
    # If profile_completed is None (existing user), treat as completed
    # If it's explicitly False (new user), they need to complete setup
    # SPECIAL CASE: If user has name, email, and role, they're an existing user - always return True
    if profile_completed is None or (user.name and user.email):
        profile_completed = True
    
    return {
        "profile_completed": profile_completed,
        "user_id": user.user_id,
        "email": user.email,
        "name": user.name,
        "teacher_type": user.teacher_type if hasattr(user, 'teacher_type') else None,
        "exam_category": user.exam_category if hasattr(user, 'exam_category') else None
    }

# ============== STUDENT-UPLOAD EXAM WORKFLOW ==============

@api_router.post("/exams/student-mode")
async def create_student_upload_exam(
    exam_data: StudentExamCreate,
    question_paper: UploadFile = File(...),
    model_answer: UploadFile = File(...),
    user: User = Depends(get_current_user)
):
    """Create exam where students upload their answer papers"""
    if user.role != "teacher":
        raise HTTPException(status_code=403, detail="Only teachers can create exams")
    
    exam_id = f"exam_{uuid.uuid4().hex[:12]}"
    
    # Store question paper in GridFS
    qp_bytes = await question_paper.read()
    qp_file_ref = f"qp_{exam_id}"
    await fs.upload_from_stream(qp_file_ref, qp_bytes)
    
    # Store model answer in GridFS
    ma_bytes = await model_answer.read()
    ma_file_ref = f"ma_{exam_id}"
    await fs.upload_from_stream(ma_file_ref, ma_bytes)
    
    # Create exam document
    exam_doc = {
        "exam_id": exam_id,
        "batch_id": exam_data.batch_id,
        "exam_name": exam_data.exam_name,
        "total_marks": exam_data.total_marks,
        "grading_mode": exam_data.grading_mode,
        "exam_mode": "student_upload",  # Mark as student-upload mode
        "show_question_paper": exam_data.show_question_paper,
        "question_paper_ref": qp_file_ref,
        "model_answer_ref": ma_file_ref,
        "questions": [q.dict() for q in exam_data.questions],
        "teacher_id": user.user_id,
        "selected_students": exam_data.student_ids,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "status": "awaiting_submissions",
        "total_students": len(exam_data.student_ids),
        "submitted_count": 0
    }
    
    await db.exams.insert_one(exam_doc)
    
    logger.info(f"Created student-upload exam {exam_id} with {len(exam_data.student_ids)} students")
    
    return {"exam_id": exam_id, "message": "Exam created. Students can now submit their answers."}

@api_router.get("/exams/{exam_id}/submissions-status")
async def get_submission_status(exam_id: str, user: User = Depends(get_current_user)):
    """Get submission status for a student-upload exam"""
    exam = await db.exams.find_one({"exam_id": exam_id}, {"_id": 0})
    if not exam:
        raise HTTPException(status_code=404, detail="Exam not found")
    
    if exam.get("exam_mode") != "student_upload":
        raise HTTPException(status_code=400, detail="This is not a student-upload exam")
    
    # Get all submissions
    submissions = await db.student_submissions.find(
        {"exam_id": exam_id}, 
        {"_id": 0}
    ).to_list(1000)
    
    selected_students = exam.get("selected_students", [])
    submitted_ids = {sub["student_id"] for sub in submissions}
    
    # Get student details
    students_info = []
    for student_id in selected_students:
        student = await db.users.find_one({"user_id": student_id}, {"_id": 0})
        if student:
            has_submitted = student_id in submitted_ids
            submission = next((s for s in submissions if s["student_id"] == student_id), None)
            students_info.append({
                "student_id": student_id,
                "name": student["name"],
                "email": student["email"],
                "submitted": has_submitted,
                "submitted_at": submission["submitted_at"] if submission else None
            })
    
    return {
        "exam_id": exam_id,
        "exam_name": exam["exam_name"],
        "total_students": len(selected_students),
        "submitted_count": len(submitted_ids),
        "students": students_info,
        "all_submitted": len(submitted_ids) == len(selected_students)
    }

@api_router.post("/exams/{exam_id}/submit")
async def submit_student_answer(
    exam_id: str,
    answer_paper: UploadFile = File(...),
    user: User = Depends(get_current_user)
):
    """Student submits their answer paper"""
    if user.role != "student":
        raise HTTPException(status_code=403, detail="Only students can submit answers")
    
    # Check exam exists and is in student-upload mode
    exam = await db.exams.find_one({"exam_id": exam_id}, {"_id": 0})
    if not exam:
        raise HTTPException(status_code=404, detail="Exam not found")
    
    if exam.get("exam_mode") != "student_upload":
        raise HTTPException(status_code=400, detail="This exam does not accept student submissions")
    
    # Check if student is in the selected list
    if user.user_id not in exam.get("selected_students", []):
        raise HTTPException(status_code=403, detail="You are not enrolled in this exam")
    
    # Check if already submitted
    existing = await db.student_submissions.find_one({
        "exam_id": exam_id,
        "student_id": user.user_id
    })
    if existing:
        raise HTTPException(status_code=400, detail="You have already submitted. Re-submission is not allowed.")
    
    # Store answer paper in GridFS with file type metadata
    file_bytes = await answer_paper.read()
    file_ref = f"ans_{exam_id}_{user.user_id}"
    
    # Store content type for proper file handling during grading
    gridfs_id = fs.put(
        file_bytes,
        filename=file_ref,
        contentType=answer_paper.content_type or 'application/pdf',
        exam_id=exam_id,
        student_id=user.user_id
    )
    
    # Create submission record
    submission_id = f"sub_{uuid.uuid4().hex[:12]}"
    submission_doc = {
        "submission_id": submission_id,
        "exam_id": exam_id,
        "student_id": user.user_id,
        "student_name": user.name,
        "student_email": user.email,
        "answer_file_ref": file_ref,
        "submitted_at": datetime.now(timezone.utc).isoformat(),
        "status": "submitted"
    }
    
    await db.student_submissions.insert_one(submission_doc)
    
    # Update exam submitted count
    await db.exams.update_one(
        {"exam_id": exam_id},
        {"$inc": {"submitted_count": 1}}
    )
    
    logger.info(f"Student {user.user_id} submitted answer for exam {exam_id}")
    
    return {"message": "Answer submitted successfully", "submission_id": submission_id}

@api_router.delete("/exams/{exam_id}/remove-student/{student_id}")
async def remove_student_from_exam(
    exam_id: str,
    student_id: str,
    user: User = Depends(get_current_user)
):
    """Teacher removes a student from exam (for non-submitters)"""
    if user.role != "teacher":
        raise HTTPException(status_code=403, detail="Only teachers can remove students")
    
    exam = await db.exams.find_one({"exam_id": exam_id}, {"_id": 0})
    if not exam:
        raise HTTPException(status_code=404, detail="Exam not found")
    
    if exam["teacher_id"] != user.user_id:
        raise HTTPException(status_code=403, detail="Not your exam")
    
    # Remove student from selected list
    await db.exams.update_one(
        {"exam_id": exam_id},
        {
            "$pull": {"selected_students": student_id},
            "$inc": {"total_students": -1}
        }
    )
    
    logger.info(f"Teacher {user.user_id} removed student {student_id} from exam {exam_id}")
    
    return {"message": "Student removed from exam"}

@api_router.post("/exams/{exam_id}/grade-student-submissions")
async def grade_student_submissions(exam_id: str, user: User = Depends(get_current_user)):
    """Trigger grading for all submitted student answers"""
    if user.role != "teacher":
        raise HTTPException(status_code=403, detail="Only teachers can grade")
    
    exam = await db.exams.find_one({"exam_id": exam_id}, {"_id": 0})
    if not exam:
        raise HTTPException(status_code=404, detail="Exam not found")
    
    if exam["teacher_id"] != user.user_id:
        raise HTTPException(status_code=403, detail="Not your exam")
    
    if exam.get("exam_mode") != "student_upload":
        raise HTTPException(status_code=400, detail="Not a student-upload exam")
    
    # Get all submissions
    submissions = await db.student_submissions.find(
        {"exam_id": exam_id, "status": "submitted"},
        {"_id": 0}
    ).to_list(1000)
    
    if not submissions:
        raise HTTPException(status_code=400, detail="No submissions to grade")
    
    # Create grading job
    job_id = f"job_{uuid.uuid4().hex[:12]}"
    
    # Get model answer from GridFS
    ma_file_ref = exam.get("model_answer_ref")
    ma_stream = await fs.open_download_stream_by_name(ma_file_ref)
    ma_bytes = await ma_stream.read()
    
    # Convert model answer to images (supports multiple formats)
    # Detect file type from GridFS metadata
    ma_file_info = await fs.find_one({"filename": ma_file_ref})
    ma_file_type = ma_file_info.get("contentType", "application/pdf").split('/')[-1] if ma_file_info else "pdf"
    ma_images = convert_to_images(ma_bytes, ma_file_type)
    
    # Store model answer in GridFS with images
    await db.exam_files.update_one(
        {"exam_id": exam_id, "file_type": "model_answer"},
        {"$set": {
            "exam_id": exam_id,
            "file_type": "model_answer",
            "file_refs": [ma_file_ref],
            "created_at": datetime.now(timezone.utc).isoformat()
        }},
        upsert=True
    )
    
    # Create task for each submission
    tasks_created = []
    for submission in submissions:
        task_id = f"task_{uuid.uuid4().hex[:12]}"
        
        # Get answer paper from GridFS
        ans_stream = await fs.open_download_stream_by_name(submission["answer_file_ref"])
        ans_bytes = await ans_stream.read()
        
        # Convert to images (supports multiple formats)
        # Detect file type from GridFS metadata
        ans_file_info = await fs.find_one({"filename": submission["answer_file_ref"]})
        ans_file_type = ans_file_info.get("contentType", "application/pdf").split('/')[-1] if ans_file_info else "pdf"
        ans_images = convert_to_images(ans_bytes, ans_file_type)
        
        # Store answer paper with GridFS reference
        await db.exam_files.update_one(
            {"exam_id": exam_id, "student_id": submission["student_id"], "file_type": "answer_paper"},
            {"$set": {
                "exam_id": exam_id,
                "student_id": submission["student_id"],
                "file_type": "answer_paper",
                "file_refs": [submission["answer_file_ref"]],
                "created_at": datetime.now(timezone.utc).isoformat()
            }},
            upsert=True
        )
        
        # Create task
        task_doc = {
            "task_id": task_id,
            "type": "grade_paper",
            "status": "pending",
            "created_at": datetime.now(timezone.utc).isoformat(),
            "payload": {
                "exam_id": exam_id,
                "student_id": submission["student_id"],
                "student_name": submission["student_name"],
                "grading_mode": exam["grading_mode"],
                "questions": exam["questions"],
                "answer_file_ref": submission["answer_file_ref"],
                "model_answer_ref": ma_file_ref
            },
            "result": None
        }
        
        await db.tasks.insert_one(task_doc)
        tasks_created.append(task_id)
    
    # Create grading job
    job_doc = {
        "job_id": job_id,
        "exam_id": exam_id,
        "teacher_id": user.user_id,
        "status": "processing",
        "progress": 0,
        "total_papers": len(submissions),
        "processed_papers": 0,
        "successful": 0,
        "failed": 0,
        "submissions": [],
        "errors": [],
        "created_at": datetime.now(timezone.utc).isoformat(),
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "task_ids": tasks_created
    }
    
    await db.grading_jobs.insert_one(job_doc)
    
    # Update exam status
    await db.exams.update_one(
        {"exam_id": exam_id},
        {"$set": {"status": "grading", "grading_job_id": job_id}}
    )
    
    logger.info(f"Created grading job {job_id} for {len(submissions)} student submissions")
    
    return {
        "job_id": job_id,
        "message": f"Grading started for {len(submissions)} submissions",
        "total_papers": len(submissions)
    }

# ============== BATCH ROUTES ==============

@api_router.get("/batches")
async def get_batches(user: User = Depends(get_current_user)):
    """Get all batches for current teacher"""
    if user.role == "teacher":
        batches = await db.batches.find(
            {"teacher_id": user.user_id},
            {"_id": 0}
        ).to_list(100)
        
        # Enrich with student count
        for batch in batches:
            student_count = await db.users.count_documents({
                "batches": batch["batch_id"],
                "role": "student"
            })
            batch["student_count"] = student_count
    else:
        batches = await db.batches.find(
            {"students": user.user_id},
            {"_id": 0}
        ).to_list(100)
    return serialize_doc(batches)

@api_router.get("/batches/{batch_id}")
async def get_batch(batch_id: str, user: User = Depends(get_current_user)):
    """Get batch details with students"""
    batch = await db.batches.find_one(
        {"batch_id": batch_id},
        {"_id": 0}
    )
    if not batch:
        raise HTTPException(status_code=404, detail="Batch not found")
    
    # Get students in this batch
    students = await db.users.find(
        {"batches": batch_id, "role": "student"},
        {"_id": 0, "user_id": 1, "name": 1, "email": 1, "student_id": 1}
    ).to_list(500)
    
    batch["students_list"] = students
    batch["student_count"] = len(students)
    
    # Get exams for this batch
    exams = await db.exams.find(
        {"batch_id": batch_id},
        {"_id": 0, "exam_id": 1, "exam_name": 1, "status": 1}
    ).to_list(100)
    batch["exams"] = exams
    
    return batch

@api_router.post("/batches")
async def create_batch(batch: BatchCreate, user: User = Depends(get_current_user)):
    """Create a new batch"""
    if user.role != "teacher":
        raise HTTPException(status_code=403, detail="Only teachers can create batches")
    
    # Check for duplicate name
    existing = await db.batches.find_one({
        "name": batch.name,
        "teacher_id": user.user_id
    })
    if existing:
        raise HTTPException(status_code=400, detail="A batch with this name already exists")
    
    batch_id = f"batch_{uuid.uuid4().hex[:8]}"
    new_batch = {
        "batch_id": batch_id,
        "name": batch.name,
        "teacher_id": user.user_id,
        "students": [],
        "created_at": datetime.now(timezone.utc).isoformat()
    }
    await db.batches.insert_one(new_batch)
    new_batch.pop("_id", None)
    return new_batch

@api_router.put("/batches/{batch_id}")
async def update_batch(batch_id: str, batch: BatchCreate, user: User = Depends(get_current_user)):
    """Update batch name"""
    if user.role != "teacher":
        raise HTTPException(status_code=403, detail="Only teachers can update batches")
    
    # Check for duplicate name (excluding current batch)
    existing = await db.batches.find_one({
        "name": batch.name,
        "teacher_id": user.user_id,
        "batch_id": {"$ne": batch_id}
    })
    if existing:
        raise HTTPException(status_code=400, detail="A batch with this name already exists")
    
    result = await db.batches.update_one(
        {"batch_id": batch_id, "teacher_id": user.user_id},
        {"$set": {"name": batch.name}}
    )
    if result.matched_count == 0:
        raise HTTPException(status_code=404, detail="Batch not found")
    return {"message": "Batch updated"}

@api_router.delete("/batches/{batch_id}")
async def delete_batch(batch_id: str, user: User = Depends(get_current_user)):
    """Delete a batch (only if empty)"""
    if user.role != "teacher":
        raise HTTPException(status_code=403, detail="Only teachers can delete batches")
    
    # Check if batch has students
    student_count = await db.users.count_documents({
        "batches": batch_id,
        "role": "student"
    })
    if student_count > 0:
        raise HTTPException(status_code=400, detail=f"Cannot delete batch with {student_count} students. Remove students first.")
    
    # Check if batch has exams
    exam_count = await db.exams.count_documents({"batch_id": batch_id})
    if exam_count > 0:
        raise HTTPException(status_code=400, detail=f"Cannot delete batch with {exam_count} exams. Delete exams first.")
    
    result = await db.batches.delete_one({
        "batch_id": batch_id,
        "teacher_id": user.user_id
    })
    if result.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Batch not found")
    return {"message": "Batch deleted"}

# ============== SUBJECT ROUTES ==============

@api_router.get("/subjects")
async def get_subjects(user: User = Depends(get_current_user)):
    """Get all subjects"""
    if user.role == "teacher":
        subjects = await db.subjects.find(
            {"teacher_id": user.user_id},
            {"_id": 0}
        ).to_list(100)
    else:
        # Students see subjects from their batches
        exams = await db.exams.find(
            {"batch_id": {"$in": user.batches}},
            {"subject_id": 1, "_id": 0}
        ).to_list(100)
        subject_ids = list(set(e["subject_id"] for e in exams))
        subjects = await db.subjects.find(
            {"subject_id": {"$in": subject_ids}},
            {"_id": 0}
        ).to_list(100)
    return subjects

@api_router.post("/subjects")
async def create_subject(subject: SubjectCreate, user: User = Depends(get_current_user)):
    """Create a new subject"""
    if user.role != "teacher":
        raise HTTPException(status_code=403, detail="Only teachers can create subjects")
    
    # Check for duplicate
    existing = await db.subjects.find_one({
        "name": subject.name,
        "teacher_id": user.user_id
    })
    if existing:
        raise HTTPException(status_code=400, detail="Subject already exists")
    
    subject_id = f"subj_{uuid.uuid4().hex[:8]}"
    new_subject = {
        "subject_id": subject_id,
        "name": subject.name,
        "teacher_id": user.user_id,
        "created_at": datetime.now(timezone.utc).isoformat()
    }
    await db.subjects.insert_one(new_subject)
    return {"subject_id": subject_id, "name": subject.name}

# ============== STUDENT MANAGEMENT ROUTES ==============

@api_router.get("/students")
async def get_students(batch_id: Optional[str] = None, user: User = Depends(get_current_user)):
    """Get students managed by this teacher"""
    if user.role != "teacher":
        raise HTTPException(status_code=403, detail="Only teachers can view students")
    
    query = {"role": "student", "teacher_id": user.user_id}
    if batch_id:
        query["batches"] = batch_id
    
    students = await db.users.find(query, {"_id": 0}).to_list(500)
    return serialize_doc(students)

@api_router.get("/students/my-exams")
async def get_my_exams(user: User = Depends(get_current_user)):
    """Get exams assigned to the current student for submission"""
    if user.role != "student":
        raise HTTPException(status_code=403, detail="Only students can access this")
    
    # Find exams where this student is assigned and exam is in student-upload mode
    exams = await db.exams.find(
        {
            "students": user.user_id,  # Student is in the students array
            "is_student_upload": True   # This is a student-upload exam
        },
        {"_id": 0}
    ).to_list(100)
    
    # Enrich with submission status
    for exam in exams:
        # Check if student has already submitted
        submission = await db.submissions.find_one(
            {
                "exam_id": exam["exam_id"],
                "student_id": user.user_id
            },
            {"_id": 0, "submission_id": 1, "status": 1, "percentage": 1, "obtained_marks": 1, "total_marks": 1}
        )
        
        if submission:
            exam["submitted"] = True
            exam["submission_status"] = submission.get("status", "submitted")
            exam["score"] = submission.get("percentage")
            exam["submission_id"] = submission.get("submission_id")
        else:
            exam["submitted"] = False
            exam["submission_status"] = "pending"
    
    return serialize_doc(exams)

@api_router.get("/students/{student_user_id}")
async def get_student_detail(student_user_id: str, user: User = Depends(get_current_user)):
    """Get detailed student information with performance analytics"""
    try:
        student = await db.users.find_one(
            {"user_id": student_user_id},
            {"_id": 0}
        )
        if not student:
            raise HTTPException(status_code=404, detail="Student not found")

        # Get all submissions for this student
        submissions = await db.submissions.find(
            {"student_id": student_user_id},
            {"_id": 0, "file_data": 0, "file_images": 0}
        ).to_list(100)
        
        # Calculate overall stats
        if submissions:
            percentages = [s.get("percentage", 0) for s in submissions]
            avg_percentage = sum(percentages) / len(percentages)
            highest = max(percentages)
            lowest = min(percentages)

            # Trend calculation (last 5 vs previous 5)
            sorted_subs = sorted(submissions, key=lambda x: x.get("created_at", ""))
            if len(sorted_subs) >= 2:
                recent = sorted_subs[-min(5, len(sorted_subs)):]
                recent_avg = sum(s.get("percentage", 0) for s in recent) / len(recent)
                if len(sorted_subs) > 5:
                    older = sorted_subs[-min(10, len(sorted_subs)):-5]
                    older_avg = sum(s.get("percentage", 0) for s in older) / len(older) if older else recent_avg
                    trend = recent_avg - older_avg
                else:
                    trend = 0
            else:
                trend = 0
        else:
            avg_percentage = 0
            highest = 0
            lowest = 0
            trend = 0
        
        # Subject-wise performance
        subject_performance = {}
        for sub in submissions:
            exam = await db.exams.find_one({"exam_id": sub["exam_id"]}, {"_id": 0, "subject_id": 1})
            if exam:
                subj = await db.subjects.find_one({"subject_id": exam["subject_id"]}, {"_id": 0, "name": 1})
                subj_name = subj.get("name", "Unknown") if subj else "Unknown"
                if subj_name not in subject_performance:
                    subject_performance[subj_name] = {"scores": [], "total_exams": 0}
                subject_performance[subj_name]["scores"].append(sub.get("percentage", 0))
                subject_performance[subj_name]["total_exams"] += 1
        
        for subj_name, data in subject_performance.items():
            data["average"] = sum(data["scores"]) / len(data["scores"]) if data["scores"] else 0
            data["highest"] = max(data["scores"]) if data["scores"] else 0
            data["lowest"] = min(data["scores"]) if data["scores"] else 0
        
        # ====== TOPIC-BASED PERFORMANCE ANALYSIS ======
        # Collect topic-wise performance across all exams with timestamps
        topic_performance = {}  # {topic: [{"score": pct, "exam_date": date, "exam_name": name}]}

        for sub in submissions:
            exam = await db.exams.find_one({"exam_id": sub["exam_id"]}, {"_id": 0})
            if not exam:
                continue
            
            exam_name = exam.get("exam_name", "Unknown Exam")
            exam_date = sub.get("created_at", "")
            exam_questions = exam.get("questions", [])
            
            # Create a map of question_number -> topics
            question_topics = {}
            for q in exam_questions:
                q_num = q.get("question_number")
                topics = q.get("topic_tags", [])
                if not topics:
                    # If no topic tags, use subject name as fallback
                    subj = await db.subjects.find_one({"subject_id": exam.get("subject_id")}, {"_id": 0, "name": 1})
                    topics = [subj.get("name", "General")] if subj else ["General"]
                question_topics[q_num] = topics

            # Analyze each question score
            for qs in sub.get("question_scores", []):
                q_num = qs.get("question_number")
                pct = (qs["obtained_marks"] / qs["max_marks"]) * 100 if qs.get("max_marks", 0) > 0 else 0
                
                # Get topics for this question
                topics = question_topics.get(q_num, ["General"])

                for topic in topics:
                    if topic not in topic_performance:
                        topic_performance[topic] = []

                    topic_performance[topic].append({
                        "score": pct,
                        "exam_date": exam_date,
                        "exam_name": exam_name,
                        "question_number": q_num
                    })
        
        # Analyze topics: identify weak areas, strengths, and improvement trends
        weak_topics = []
        strong_topics = []
        
        for topic, performances in topic_performance.items():
            if len(performances) == 0:
                continue
            
            # Sort by exam date to calculate trend
            sorted_perfs = sorted(performances, key=lambda x: x.get("exam_date", ""))

            # Calculate overall average
            avg_score = sum(p["score"] for p in sorted_perfs) / len(sorted_perfs)

            # Calculate trend (improvement/decline)
            trend = 0
            trend_text = "stable"
            if len(sorted_perfs) >= 2:
                # Compare first half vs second half
                mid = len(sorted_perfs) // 2
                first_half_avg = sum(p["score"] for p in sorted_perfs[:mid]) / mid if mid > 0 else 0
                second_half_avg = sum(p["score"] for p in sorted_perfs[mid:]) / (len(sorted_perfs) - mid)
                trend = second_half_avg - first_half_avg

                if trend > 10:
                    trend_text = "improving"
                elif trend < -10:
                    trend_text = "declining"
                else:
                    trend_text = "stable"

            topic_data = {
                "topic": topic,
                "avg_score": round(avg_score, 1),
                "total_attempts": len(sorted_perfs),
                "trend": round(trend, 1),
                "trend_text": trend_text,
                "recent_score": round(sorted_perfs[-1]["score"], 1) if sorted_perfs else 0,
                "first_score": round(sorted_perfs[0]["score"], 1) if sorted_perfs else 0
            }

            # Classify as weak or strong
            if avg_score < 50:
                weak_topics.append(topic_data)
            elif avg_score >= 75:
                strong_topics.append(topic_data)
        
        # Sort weak topics by score (lowest first) and strong by score (highest first)
        weak_topics = sorted(weak_topics, key=lambda x: x["avg_score"])[:5]
        strong_topics = sorted(strong_topics, key=lambda x: -x["avg_score"])[:5]
        
        # Generate smart recommendations based on topic analysis
        recommendations = []

        # Check for declining topics that need attention
        declining_topics = [t for t in weak_topics if t["trend_text"] == "declining"]
        if declining_topics:
            recommendations.append(f"⚠️ {declining_topics[0]['topic']} needs urgent attention - performance is declining")

        # Highlight improving weak topics
        improving_weak = [t for t in weak_topics if t["trend_text"] == "improving"]
        if improving_weak:
            recommendations.append(f"📈 Great progress in {improving_weak[0]['topic']}! Keep practicing to master it")

        # Stable weak topics need more focus
        stable_weak = [t for t in weak_topics if t["trend_text"] == "stable" and t["total_attempts"] >= 2]
        if stable_weak:
            recommendations.append(f"💡 Focus more on {stable_weak[0]['topic']} - needs consistent practice")

        # Celebrate consistent strengths
        if strong_topics:
            recommendations.append(f"⭐ Excellent in {strong_topics[0]['topic']}! Consider helping peers")

        # Default recommendations if no specific insights
        if not recommendations:
            recommendations = [
                "Complete more exams to get detailed topic insights",
                "Focus on understanding concepts deeply",
                "Practice regularly across all topics"
            ]

        return serialize_doc({
            "student": student,
            "stats": {
                "total_exams": len(submissions),
                "avg_percentage": round(avg_percentage, 1),
                "highest_score": highest,
                "lowest_score": lowest,
                "trend": round(trend, 1)
            },
            "subject_performance": subject_performance,
            "recent_submissions": submissions[-10:],
            "weak_topics": weak_topics,
            "strong_topics": strong_topics,
            "topic_performance": topic_performance,  # Full topic data for detailed view
            "recommendations": recommendations
        })
    except Exception as e:
        logger.error(f"Error fetching student detail {student_user_id}: {e}")
        if isinstance(e, HTTPException):
            raise e
        raise HTTPException(status_code=500, detail=str(e))

@api_router.post("/students")
async def create_student(student: UserCreate, user: User = Depends(get_current_user)):
    """Create a new student"""
    if user.role != "teacher":
        raise HTTPException(status_code=403, detail="Only teachers can create students")
    
    # Validate student ID format if provided
    if student.student_id:
        student_id = student.student_id.strip()
        if not (3 <= len(student_id) <= 20 and student_id.replace("-", "").isalnum()):
            raise HTTPException(
                status_code=400, 
                detail="Student ID must be 3-20 alphanumeric characters (letters, numbers, hyphens allowed)"
            )
        
        # Check if student ID already exists
        existing_id = await db.users.find_one({"student_id": student_id, "role": "student"})
        if existing_id:
            raise HTTPException(
                status_code=400, 
                detail=f"Student ID {student_id} already exists"
            )
    else:
        # Auto-generate student ID
        student_id = f"STU{uuid.uuid4().hex[:6].upper()}"
    
    # Check if email already exists
    existing = await db.users.find_one({"email": student.email})
    if existing:
        raise HTTPException(status_code=400, detail="Student with this email already exists")
    
    user_id = f"user_{uuid.uuid4().hex[:12]}"
    
    new_student = {
        "user_id": user_id,
        "email": student.email,
        "name": student.name,
        "role": "student",
        "student_id": student_id,
        "batches": student.batches,
        "teacher_id": user.user_id,
        "created_at": datetime.now(timezone.utc).isoformat()
    }
    await db.users.insert_one(new_student)
    
    # Add student to batches
    for batch_id in student.batches:
        await db.batches.update_one(
            {"batch_id": batch_id},
            {"$addToSet": {"students": user_id}}
        )
    
    return {
        "user_id": user_id,
        "student_id": student_id,
        "email": student.email,
        "name": student.name,
        "batches": student.batches
    }

@api_router.put("/students/{student_user_id}")
async def update_student(student_user_id: str, student: UserCreate, user: User = Depends(get_current_user)):
    """Update student details"""
    if user.role != "teacher":
        raise HTTPException(status_code=403, detail="Only teachers can update students")
    
    result = await db.users.update_one(
        {"user_id": student_user_id, "role": "student"},
        {"$set": {
            "name": student.name,
            "email": student.email,
            "student_id": student.student_id,
            "batches": student.batches
        }}
    )
    
    if result.matched_count == 0:
        raise HTTPException(status_code=404, detail="Student not found")
    
    return {"message": "Student updated"}

@api_router.post("/exams/{exam_id}/upload-more-papers")
async def upload_more_papers(
    exam_id: str,
    files: List[UploadFile] = File(...),
    user: User = Depends(get_current_user)
):
    """Upload additional student papers to an existing exam"""
    if user.role != "teacher":
        raise HTTPException(status_code=403, detail="Only teachers can upload papers")
    
    exam = await db.exams.find_one({"exam_id": exam_id, "teacher_id": user.user_id}, {"_id": 0})
    if not exam:
        raise HTTPException(status_code=404, detail="Exam not found")
    
    if exam.get("status") == "closed":
        raise HTTPException(status_code=400, detail="Cannot upload papers to closed exam")
    
    # Model answer is now optional - AI can grade without it
    
    submissions = []
    errors = []
    
    # Log the number of files received
    logger.info(f"=== BATCH GRADING START === Received {len(files)} files for exam {exam_id}")
    for idx, file in enumerate(files):
        filename = file.filename  # FIX: Define filename from file object
        file_start_time = datetime.now(timezone.utc)
        logger.info(f"[File {idx + 1}/{len(files)}] START processing: {filename}")
        try:
            # Process the PDF first to get images
            pdf_bytes = await file.read()
            logger.info(f"[File {idx + 1}/{len(files)}] Read {len(pdf_bytes)} bytes from {filename}")
            
            # Check file size - limit to 30MB for safety
            file_size_mb = len(pdf_bytes) / (1024 * 1024)
            if len(pdf_bytes) > 30 * 1024 * 1024:
                logger.warning(f"[File {idx + 1}/{len(files)}] File too large: {file_size_mb:.1f}MB")
                errors.append({
                    "filename": filename,
                    "error": f"File too large ({file_size_mb:.1f}MB). Maximum size is 30MB."
                })
                continue
            
            images = pdf_to_images(pdf_bytes)
            logger.info(f"[File {idx + 1}/{len(files)}] Extracted {len(images) if images else 0} images from PDF")
            
            if not images:
                logger.error(f"[File {idx + 1}/{len(files)}] Failed to extract images")
                errors.append({
                    "filename": filename,
                    "error": "Failed to extract images from PDF"
                })
                continue
            
            # Extract student ID and name from the paper using AI
            student_id, student_name = await extract_student_info_from_paper(images, filename)
            
            # Fallback to filename if AI extraction fails
            if not student_id or not student_name:
                filename_id, filename_name = parse_student_from_filename(filename)
                
                # Use filename ID if AI didn't find it
                if not student_id and filename_id:
                    student_id = filename_id
                
                # Use filename name if AI didn't find it
                if not student_name and filename_name:
                    student_name = filename_name
                
                # If still no ID or name, report error
                if not student_id and not student_name:
                    errors.append({
                        "filename": filename,
                        "error": "Could not extract student ID/name from paper or filename. Please ensure student writes their roll number and name clearly on the answer sheet."
                    })
                    continue
                
                # If we have one but not the other, use what we have
                if not student_id:
                    student_id = f"AUTO_{uuid.uuid4().hex[:6]}"
                if not student_name:
                    student_name = f"Student {student_id}"
            
            # Get or create student (FIXED: moved outside the if block)
            user_id, error = await get_or_create_student(
                student_id=student_id,
                student_name=student_name,
                batch_id=exam["batch_id"],
                teacher_id=user.user_id
            )
            
            if error:
                errors.append({
                    "filename": filename,
                    "student_id": student_id,
                    "error": error
                })
                continue
            
            # Grade with AI
            # Get model answer images from separate collection
            model_answer_imgs = await get_exam_model_answer_images(exam_id)
            
            # Get pre-extracted model answer text for efficient grading
            model_answer_text = await get_exam_model_answer_text(exam_id)
            
            scores = await grade_with_ai(
                images=images,
                model_answer_images=model_answer_imgs,
                questions=exam.get("questions", []),
                grading_mode=exam.get("grading_mode", "balanced"),
                total_marks=exam.get("total_marks", 100),
                model_answer_text=model_answer_text
            )
            
            total_score = sum(s.obtained_marks for s in scores)
            percentage = (total_score / exam["total_marks"]) * 100 if exam["total_marks"] > 0 else 0
            
            submission_id = f"sub_{uuid.uuid4().hex[:8]}"
            submission = {
                "submission_id": submission_id,
                "exam_id": exam_id,
                "student_id": user_id,
                "student_name": student_name,
                "file_data": base64.b64encode(pdf_bytes).decode(),
                "file_images": images,
                "total_score": total_score,
                "percentage": round(percentage, 2),
                "question_scores": [s.model_dump() for s in scores],
                "status": "ai_graded",
                "graded_at": datetime.now(timezone.utc).isoformat(),
                "created_at": datetime.now(timezone.utc).isoformat()
            }
            
            await db.submissions.insert_one(submission)
            submissions.append({
                "submission_id": submission_id,
                "student_id": student_id,
                "student_name": student_name,
                "total_score": total_score,
                "percentage": percentage
            })
            logger.info(f"✓ Successfully graded {filename} - Student: {student_name}, Score: {total_score}/{exam['total_marks']}")
            
        except Exception as e:
            logger.error(f"✗ Error processing {filename}: {e}", exc_info=True)
            errors.append({
                "filename": filename,
                "error": str(e)
            })
    
    result = {
        "processed": len(submissions),
        "submissions": submissions
    }
    
    if errors:
        result["errors"] = errors
    
    return result

@api_router.post("/exams/{exam_id}/bulk-approve")
async def bulk_approve_submissions(exam_id: str, user: User = Depends(get_current_user)):
    """Mark all submissions in an exam as reviewed"""
    if user.role != "teacher":
        raise HTTPException(status_code=403, detail="Only teachers can approve submissions")
    
    exam = await db.exams.find_one({"exam_id": exam_id, "teacher_id": user.user_id})
    if not exam:
        raise HTTPException(status_code=404, detail="Exam not found")
    
    result = await db.submissions.update_many(
        {"exam_id": exam_id, "status": {"$ne": "teacher_reviewed"}},
        {"$set": {"status": "teacher_reviewed", "is_reviewed": True}}
    )
    
    return {"message": f"Approved {result.modified_count} submissions"}

@api_router.put("/submissions/{submission_id}/unapprove")
async def unapprove_submission(submission_id: str, user: User = Depends(get_current_user)):
    """Revert a submission back to pending review status"""
    if user.role != "teacher":
        raise HTTPException(status_code=403, detail="Only teachers can unapprove submissions")
    
    submission = await db.submissions.find_one({"submission_id": submission_id}, {"_id": 0})
    if not submission:
        raise HTTPException(status_code=404, detail="Submission not found")
    
    # Update to pending review
    await db.submissions.update_one(
        {"submission_id": submission_id},
        {"$set": {"status": "pending_review", "is_reviewed": False}}
    )
    
    return {"message": "Submission reverted to pending review"}

@api_router.get("/exams/{exam_id}/submissions")
async def get_exam_submissions(exam_id: str, user: User = Depends(get_current_user)):
    """Get all submissions for a specific exam"""
    try:
        if user.role != "teacher":
            raise HTTPException(status_code=403, detail="Only teachers can view submissions")

        # Verify exam belongs to teacher
        exam = await db.exams.find_one({"exam_id": exam_id, "teacher_id": user.user_id}, {"_id": 0})
        if not exam:
            raise HTTPException(status_code=404, detail="Exam not found")

        # Get all submissions for this exam
        submissions = await db.submissions.find(
            {"exam_id": exam_id},
            {"_id": 0, "file_data": 0, "file_images": 0}  # Exclude large binary data
        ).to_list(1000)

        return serialize_doc(submissions)
    except Exception as e:
        logger.error(f"Error fetching submissions for exam {exam_id}: {e}")
        if isinstance(e, HTTPException):
            raise e
        raise HTTPException(status_code=500, detail=str(e))

@api_router.delete("/submissions/{submission_id}")
async def delete_submission(submission_id: str, user: User = Depends(get_current_user)):
    """Delete a specific submission (student paper)"""
    if user.role != "teacher":
        raise HTTPException(status_code=403, detail="Only teachers can delete submissions")
    
    # Find the submission
    submission = await db.submissions.find_one({"submission_id": submission_id}, {"_id": 0})
    if not submission:
        raise HTTPException(status_code=404, detail="Submission not found")
    
    # Verify the exam belongs to the teacher
    exam = await db.exams.find_one({
        "exam_id": submission["exam_id"],
        "teacher_id": user.user_id
    }, {"_id": 0})
    if not exam:
        raise HTTPException(status_code=403, detail="You don't have permission to delete this submission")
    
    # Delete the submission
    await db.submissions.delete_one({"submission_id": submission_id})
    
    # Also delete any re-evaluation requests for this submission
    await db.re_evaluations.delete_many({"submission_id": submission_id})
    
    return {"message": "Submission deleted successfully"}

@api_router.put("/exams/{exam_id}")
async def update_exam(exam_id: str, update_data: dict, user: User = Depends(get_current_user)):
    """Update exam details including name, subject, total marks, grading mode, etc."""
    if user.role != "teacher":
        raise HTTPException(status_code=403, detail="Only teachers can update exams")
    
    # Verify exam belongs to teacher
    exam = await db.exams.find_one({"exam_id": exam_id, "teacher_id": user.user_id}, {"_id": 0})
    if not exam:
        raise HTTPException(status_code=404, detail="Exam not found")
    
    # Prepare update fields - allow more fields now
    update_fields = {}
    
    # Questions array
    if "questions" in update_data:
        update_fields["questions"] = update_data["questions"]
        logger.info(f"Updating {len(update_data['questions'])} questions for exam {exam_id}")
    
    # Basic exam details
    if "exam_name" in update_data:
        update_fields["exam_name"] = update_data["exam_name"]
    
    if "subject_id" in update_data:
        update_fields["subject_id"] = update_data["subject_id"]
    
    if "total_marks" in update_data:
        update_fields["total_marks"] = float(update_data["total_marks"])
    
    if "grading_mode" in update_data:
        update_fields["grading_mode"] = update_data["grading_mode"]
    
    if "exam_type" in update_data:
        update_fields["exam_type"] = update_data["exam_type"]
    
    if "exam_date" in update_data:
        update_fields["exam_date"] = update_data["exam_date"]
    
    if update_fields:
        update_fields["updated_at"] = datetime.now(timezone.utc).isoformat()
        await db.exams.update_one(
            {"exam_id": exam_id},
            {"$set": update_fields}
        )
        logger.info(f"Updated exam {exam_id}: {list(update_fields.keys())}")
    
    return {"message": "Exam updated successfully", "updated_fields": list(update_fields.keys())}

@api_router.put("/exams/{exam_id}/close")
async def close_exam(exam_id: str, user: User = Depends(get_current_user)):
    """Close an exam (prevent further uploads/edits)"""
    if user.role != "teacher":
        raise HTTPException(status_code=403, detail="Only teachers can close exams")
    
    exam = await db.exams.find_one({"exam_id": exam_id, "teacher_id": user.user_id}, {"_id": 0})
    if not exam:
        raise HTTPException(status_code=404, detail="Exam not found")
    
    await db.exams.update_one(
        {"exam_id": exam_id},
        {"$set": {
            "status": "closed",
            "closed_at": datetime.now(timezone.utc).isoformat()
        }}
    )
    
    return {"message": "Exam closed successfully"}

@api_router.put("/exams/{exam_id}/reopen")
async def reopen_exam(exam_id: str, user: User = Depends(get_current_user)):
    """Reopen a closed exam"""
    if user.role != "teacher":
        raise HTTPException(status_code=403, detail="Only teachers can reopen exams")
    
    exam = await db.exams.find_one({"exam_id": exam_id, "teacher_id": user.user_id}, {"_id": 0})
    if not exam:
        raise HTTPException(status_code=404, detail="Exam not found")
    
    await db.exams.update_one(
        {"exam_id": exam_id},
        {"$set": {
            "status": "completed",
            "reopened_at": datetime.now(timezone.utc).isoformat()
        }}
    )
    
    return {"message": "Exam reopened successfully"}

@api_router.post("/exams/{exam_id}/regrade-all")
async def regrade_all_submissions(exam_id: str, user: User = Depends(get_current_user)):
    """Regrade all submissions for an exam with current settings"""
    if user.role != "teacher":
        raise HTTPException(status_code=403, detail="Only teachers can regrade exams")
    
    # Verify exam belongs to teacher
    exam = await db.exams.find_one({"exam_id": exam_id, "teacher_id": user.user_id}, {"_id": 0})
    if not exam:
        raise HTTPException(status_code=404, detail="Exam not found")
    
    # Get all submissions for this exam
    submissions = await db.submissions.find({"exam_id": exam_id}, {"_id": 0}).to_list(1000)
    
    if not submissions:
        return {"message": "No submissions to regrade", "regraded_count": 0}
    
    # Get model answer images from separate collection
    model_answer_imgs = await get_exam_model_answer_images(exam_id)
    
    # Get pre-extracted model answer text for efficient grading
    model_answer_text = await get_exam_model_answer_text(exam_id)
    
    regraded_count = 0
    errors = []
    
    for submission in submissions:
        try:
            # Get the student's answer images
            answer_images = submission.get("answer_images") or submission.get("file_images")
            if not answer_images:
                logger.warning(f"Submission {submission['submission_id']} has no answer images, skipping")
                continue
            
            # Re-grade using the current exam settings
            scores = await grade_with_ai(
                images=answer_images,
                model_answer_images=model_answer_imgs,
                questions=exam.get("questions", []),
                grading_mode=exam.get("grading_mode", "balanced"),
                total_marks=exam.get("total_marks", 100),
                model_answer_text=model_answer_text
            )
            
            # Calculate total score using exam's total_marks
            total_score = sum(s.obtained_marks for s in scores)
            exam_total_marks = exam.get("total_marks", 100)
            percentage = round((total_score / exam_total_marks) * 100, 2) if exam_total_marks > 0 else 0
            
            # Update submission with new scores
            await db.submissions.update_one(
                {"submission_id": submission["submission_id"]},
                {"$set": {
                    "question_scores": [s.model_dump() for s in scores],
                    "total_score": total_score,
                    "percentage": percentage,
                    "graded_at": datetime.now(timezone.utc).isoformat(),
                    "regraded_at": datetime.now(timezone.utc).isoformat(),
                    "grading_mode_used": exam.get("grading_mode", "balanced")
                }}
            )
            
            regraded_count += 1
            logger.info(f"Regraded submission {submission['submission_id']}: {total_score}/{exam_total_marks}")
            
        except Exception as e:
            logger.error(f"Error regrading submission {submission['submission_id']}: {str(e)}")
            errors.append({"submission_id": submission["submission_id"], "error": str(e)})
    
    return {
        "message": f"Regraded {regraded_count} submissions",
        "regraded_count": regraded_count,
        "total_submissions": len(submissions),
        "errors": errors[:5] if errors else []  # Return first 5 errors only
    }

@api_router.put("/batches/{batch_id}/close")
async def close_batch(batch_id: str, user: User = Depends(get_current_user)):
    """Close/archive a batch"""
    if user.role != "teacher":
        raise HTTPException(status_code=403, detail="Only teachers can close batches")
    
    batch = await db.batches.find_one({"batch_id": batch_id, "teacher_id": user.user_id}, {"_id": 0})
    if not batch:
        raise HTTPException(status_code=404, detail="Batch not found")
    
    await db.batches.update_one(
        {"batch_id": batch_id},
        {"$set": {
            "status": "closed",
            "closed_at": datetime.now(timezone.utc).isoformat()
        }}
    )
    
    return {"message": "Batch closed successfully"}

@api_router.put("/batches/{batch_id}/reopen")
async def reopen_batch(batch_id: str, user: User = Depends(get_current_user)):
    """Reopen a closed batch"""
    if user.role != "teacher":
        raise HTTPException(status_code=403, detail="Only teachers can reopen batches")
    
    batch = await db.batches.find_one({"batch_id": batch_id, "teacher_id": user.user_id}, {"_id": 0})
    if not batch:
        raise HTTPException(status_code=404, detail="Batch not found")
    
    await db.batches.update_one(
        {"batch_id": batch_id},
        {"$set": {
            "status": "active",
            "reopened_at": datetime.now(timezone.utc).isoformat()
        }}
    )
    
    return {"message": "Batch reopened successfully"}

@api_router.post("/batches/{batch_id}/students")
async def add_student_to_batch(batch_id: str, data: dict, user: User = Depends(get_current_user)):
    """Add an existing student to a batch"""
    if user.role != "teacher":
        raise HTTPException(status_code=403, detail="Only teachers can manage batch students")
    
    batch = await db.batches.find_one({"batch_id": batch_id, "teacher_id": user.user_id}, {"_id": 0})
    if not batch:
        raise HTTPException(status_code=404, detail="Batch not found")
    
    if batch.get("status") == "closed":
        raise HTTPException(status_code=400, detail="Cannot add students to a closed batch")
    
    student_id = data.get("student_id")
    if not student_id:
        raise HTTPException(status_code=400, detail="Student ID is required")
    
    # Verify student exists and belongs to teacher
    student = await db.users.find_one({"user_id": student_id, "teacher_id": user.user_id, "role": "student"}, {"_id": 0})
    if not student:
        raise HTTPException(status_code=404, detail="Student not found")
    
    # Check if student is already in batch
    student_batches = student.get("batches", [])
    if batch_id in student_batches:
        raise HTTPException(status_code=400, detail="Student is already in this batch")
    
    # Add batch to student's batches
    await db.users.update_one(
        {"user_id": student_id},
        {"$addToSet": {"batches": batch_id}}
    )
    
    return {"message": "Student added to batch successfully"}

@api_router.delete("/batches/{batch_id}/students/{student_id}")
async def remove_student_from_batch(batch_id: str, student_id: str, user: User = Depends(get_current_user)):
    """Remove a student from a batch"""
    if user.role != "teacher":
        raise HTTPException(status_code=403, detail="Only teachers can manage batch students")
    
    batch = await db.batches.find_one({"batch_id": batch_id, "teacher_id": user.user_id}, {"_id": 0})
    if not batch:
        raise HTTPException(status_code=404, detail="Batch not found")
    
    if batch.get("status") == "closed":
        raise HTTPException(status_code=400, detail="Cannot remove students from a closed batch")
    
    # Verify student exists
    student = await db.users.find_one({"user_id": student_id, "teacher_id": user.user_id, "role": "student"}, {"_id": 0})
    if not student:
        raise HTTPException(status_code=404, detail="Student not found")
    
    # Check if student is in the batch
    student_batches = student.get("batches", [])
    if batch_id not in student_batches:
        raise HTTPException(status_code=400, detail="Student is not in this batch")
    
    # Remove batch from student's batches
    await db.users.update_one(
        {"user_id": student_id},
        {"$pull": {"batches": batch_id}}
    )
    
    return {"message": "Student removed from batch successfully"}

# ============== STUDENT ROUTES ==============

@api_router.delete("/students/{student_user_id}")
async def delete_student(student_user_id: str, user: User = Depends(get_current_user)):
    """Delete a student"""
    result = await db.users.delete_one({
        "user_id": student_user_id,
        "teacher_id": user.user_id
    })
    if result.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Student not found")
    return {"message": "Student deleted"}

# ============== EXAM ROUTES ==============

@api_router.get("/exams")
async def get_exams(
    batch_id: Optional[str] = None,
    subject_id: Optional[str] = None,
    status: Optional[str] = None,
    user: User = Depends(get_current_user)
):
    """Get all exams"""
    if user.role == "teacher":
        query = {"teacher_id": user.user_id}
    else:
        # Students see exams for their batches
        query = {"batch_id": {"$in": user.batches}}
    
    if batch_id:
        query["batch_id"] = batch_id
    if subject_id:
        query["subject_id"] = subject_id
    if status:
        query["status"] = status
    
    exams = await db.exams.find(query, {"_id": 0}).to_list(100)
    
    # Enrich with batch and subject names
    for exam in exams:
        batch = await db.batches.find_one({"batch_id": exam["batch_id"]}, {"_id": 0, "name": 1})
        subject = await db.subjects.find_one({"subject_id": exam["subject_id"]}, {"_id": 0, "name": 1})
        exam["batch_name"] = batch["name"] if batch else "Unknown"
        exam["subject_name"] = subject["name"] if subject else "Unknown"
        
        # Get submission count
        sub_count = await db.submissions.count_documents({"exam_id": exam["exam_id"]})
        exam["submission_count"] = sub_count
    
    return serialize_doc(exams)

@api_router.post("/exams")
async def create_exam(exam: ExamCreate, user: User = Depends(get_current_user)):
    """Create a new exam"""
    if user.role != "teacher":
        raise HTTPException(status_code=403, detail="Only teachers can create exams")
    
    # Check for duplicate exam name within the same batch (case-insensitive, trimmed)
    exam_name_normalized = exam.exam_name.strip().lower()
    
    # Get all exams for this batch and teacher
    existing_exams = await db.exams.find({
        "batch_id": exam.batch_id,
        "teacher_id": user.user_id
    }, {"_id": 0, "exam_name": 1, "exam_id": 1}).to_list(1000)
    
    # Check if any existing exam has the same name (case-insensitive)
    for existing in existing_exams:
        existing_name_normalized = existing.get("exam_name", "").strip().lower()
        if existing_name_normalized == exam_name_normalized:
            logger.warning(f"Duplicate exam found: '{exam.exam_name}' matches existing '{existing.get('exam_name')}' (ID: {existing.get('exam_id')}) in batch {exam.batch_id}")
            raise HTTPException(status_code=400, detail=f"An exam named '{exam.exam_name}' already exists in this batch")
    
    exam_id = f"exam_{uuid.uuid4().hex[:8]}"
    new_exam = {
        "exam_id": exam_id,
        "batch_id": exam.batch_id,
        "subject_id": exam.subject_id,
        "exam_type": exam.exam_type,
        "exam_name": exam.exam_name,
        "total_marks": exam.total_marks,
        "exam_date": exam.exam_date,
        "grading_mode": exam.grading_mode,
        "questions": exam.questions,
        "teacher_id": user.user_id,
        "status": "draft",
        "created_at": datetime.now(timezone.utc).isoformat()
    }
    await db.exams.insert_one(new_exam)
    logger.info(f"Created new exam: {exam_id} - '{exam.exam_name}' in batch {exam.batch_id}")
    return {"exam_id": exam_id, "status": "draft"}

@api_router.get("/exams/{exam_id}")
async def get_exam(exam_id: str, user: User = Depends(get_current_user)):
    """Get exam details including files from separate collection"""
    try:
        exam = await db.exams.find_one({"exam_id": exam_id}, {"_id": 0})
        if not exam:
            raise HTTPException(status_code=404, detail="Exam not found")

        # Fetch model answer images from separate collection
        model_answer_imgs = await get_exam_model_answer_images(exam_id)
        if model_answer_imgs:
            exam["model_answer_images"] = model_answer_imgs

        # Fetch question paper images from separate collection
        question_paper_imgs = await get_exam_question_paper_images(exam_id)
        if question_paper_imgs:
            exam["question_paper_images"] = question_paper_imgs

        return serialize_doc(exam)
    except Exception as e:
        logger.error(f"Error fetching exam {exam_id}: {e}")
        if isinstance(e, HTTPException):
            raise e
        raise HTTPException(status_code=500, detail=str(e))

@api_router.delete("/exams/{exam_id}")
async def delete_exam(exam_id: str, user: User = Depends(get_current_user)):
    """Delete an exam and all its submissions, and cancel any active grading jobs"""
    if user.role != "teacher":
        raise HTTPException(status_code=403, detail="Only teachers can delete exams")
    
    # Check if exam exists and belongs to teacher
    exam = await db.exams.find_one({"exam_id": exam_id, "teacher_id": user.user_id})
    if not exam:
        raise HTTPException(status_code=404, detail="Exam not found")
    
    # IMPORTANT: Cancel any active grading jobs for this exam
    logger.info(f"Cancelling active grading jobs for exam {exam_id}")
    cancelled_jobs = await db.grading_jobs.update_many(
        {"exam_id": exam_id, "status": {"$in": ["pending", "processing"]}},
        {"$set": {
            "status": "cancelled",
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "cancellation_reason": "Exam deleted by teacher"
        }}
    )
    
    # Cancel associated tasks in the task queue
    cancelled_tasks = await db.tasks.update_many(
        {"data.exam_id": exam_id, "status": {"$in": ["pending", "processing"]}},
        {"$set": {"status": "cancelled"}}
    )
    
    if cancelled_jobs.modified_count > 0 or cancelled_tasks.modified_count > 0:
        logger.info(f"Cancelled {cancelled_jobs.modified_count} jobs and {cancelled_tasks.modified_count} tasks for exam {exam_id}")
    
    # Delete all submissions associated with this exam
    await db.submissions.delete_many({"exam_id": exam_id})
    
    # Delete all re-evaluation requests associated with this exam
    await db.re_evaluations.delete_many({"exam_id": exam_id})
    
    # Delete exam files from separate collection
    await db.exam_files.delete_many({"exam_id": exam_id})
    
    # Delete GridFS files for this exam (model answers, question papers, student papers)
    try:
        for grid_file in fs.find({"exam_id": exam_id}):
            fs.delete(grid_file._id)
            logger.info(f"Deleted GridFS file: {grid_file.filename}")
    except Exception as e:
        logger.warning(f"Error cleaning up GridFS files for exam {exam_id}: {e}")
    
    # Delete the exam
    result = await db.exams.delete_one({"exam_id": exam_id, "teacher_id": user.user_id})
    
    if result.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Exam not found")
    
    return {
        "message": "Exam deleted successfully",
        "cancelled_jobs": cancelled_jobs.modified_count,
        "cancelled_tasks": cancelled_tasks.modified_count
    }

@api_router.post("/exams/{exam_id}/extract-questions")
async def extract_and_update_questions(exam_id: str, user: User = Depends(get_current_user)):
    """Extract question text from question paper OR model answer and update exam"""
    if user.role != "teacher":
        raise HTTPException(status_code=403, detail="Only teachers can update exams")
    
    # Get exam
    exam = await db.exams.find_one({"exam_id": exam_id, "teacher_id": user.user_id}, {"_id": 0})
    if not exam:
        raise HTTPException(status_code=404, detail="Exam not found")
    
    # Get images from separate collection
    question_paper_imgs = await get_exam_question_paper_images(exam_id)
    model_answer_imgs = await get_exam_model_answer_images(exam_id)
    
    # Prioritize question paper over model answer
    extracted_questions = []
    source = ""
    
    if question_paper_imgs:
        # Extract from question paper (preferred)
        source = "question paper"
        extracted_questions = await extract_questions_from_question_paper(
            question_paper_imgs,
            len(exam.get("questions", []))
        )
    elif model_answer_imgs:
        # Fallback to model answer
        source = "model answer"
        extracted_questions = await extract_questions_from_model_answer(
            model_answer_imgs,
            len(exam.get("questions", []))
        )
    else:
        raise HTTPException(status_code=400, detail="No question paper or model answer found. Please upload one first.")
    
    if not extracted_questions:
        raise HTTPException(status_code=500, detail=f"Failed to extract questions from {source}")
    
    # Update question rubrics
    questions = exam.get("questions", [])
    updated_count = 0
    
    for i, q in enumerate(questions):
        if i < len(extracted_questions):
            extracted_q = extracted_questions[i]
            
            # CRITICAL FIX: extracted_questions returns objects, not strings
            # Extract the actual text from the object
            if isinstance(extracted_q, dict):
                rubric_text = extracted_q.get("rubric", "")
                question_text = extracted_q.get("question_text", "") or extracted_q.get("rubric", "")
                
                # CRITICAL: Also update sub_questions with their full text
                if "sub_questions" in extracted_q and extracted_q["sub_questions"]:
                    q["sub_questions"] = extracted_q["sub_questions"]
                    logger.info(f"Updated Q{q.get('question_number')} with {len(extracted_q['sub_questions'])} sub-questions")
            else:
                # Fallback if it's already a string
                rubric_text = str(extracted_q)
                question_text = str(extracted_q)
            
            q["rubric"] = rubric_text
            q["question_text"] = question_text
            updated_count += 1
    
    # Update exam in database
    await db.exams.update_one(
        {"exam_id": exam_id},
        {"$set": {"questions": questions}}
    )
    
    # CRITICAL: Also update the questions collection for consistency
    for q in questions:
        await db.questions.update_one(
            {"exam_id": exam_id, "question_number": q.get("question_number")},
            {"$set": {
                "rubric": q.get("rubric", ""),
                "question_text": q.get("question_text", ""),
                "sub_questions": q.get("sub_questions", [])
            }},
            upsert=True
        )
    
    return {
        "message": f"Successfully extracted {updated_count} questions from {source}",
        "updated_count": updated_count,
        "source": source
    }

@api_router.post("/exams/{exam_id}/re-extract-questions")
async def re_extract_question_structure(exam_id: str, user: User = Depends(get_current_user)):
    """
    Re-extract COMPLETE question structure (with force=True).
    Use this when initial extraction was incorrect or incomplete.
    """
    if user.role != "teacher":
        raise HTTPException(status_code=403, detail="Only teachers can re-extract questions")
    
    # Verify exam belongs to teacher
    exam = await db.exams.find_one({"exam_id": exam_id, "teacher_id": user.user_id}, {"_id": 0})
    if not exam:
        raise HTTPException(status_code=404, detail="Exam not found")
    
    # Force re-extraction
    result = await auto_extract_questions(exam_id, force=True)
    
    if not result.get("success"):
        raise HTTPException(
            status_code=500, 
            detail=result.get("message", "Failed to re-extract questions")
        )
    
    return {
        "message": result.get("message"),
        "count": result.get("count", 0),
        "total_marks": result.get("total_marks", 0),
        "source": result.get("source", ""),
        "questions": exam.get("questions", [])
    }
    result = await db.exams.update_one(
        {"exam_id": exam_id},
        {"$set": {"questions": questions}}
    )
    
    return {
        "message": f"Successfully extracted and updated {updated_count} questions",
        "questions_updated": updated_count
    }

# ============== FILE UPLOAD & GRADING ==============

async def extract_student_info_from_paper(file_images: List[str], filename: str) -> tuple:
    """
    Extract student ID/roll number and name from the answer paper using AI
    Returns: (student_id, student_name) or (None, None) if extraction fails
    """
    from emergentintegrations.llm.chat import LlmChat, UserMessage
    
    api_key = os.environ.get('EMERGENT_LLM_KEY')
    if not api_key:
        return (None, None)
    
    try:
        chat = LlmChat(
            api_key=api_key,
            session_id=f"extract_{uuid.uuid4().hex[:8]}",
            system_message="""You are an expert at reading handwritten and printed student information from exam papers.

Extract the student's Roll Number/ID and Name from the answer sheet.

Return ONLY a JSON object in this exact format:
{
  "student_id": "the roll number or student ID (can be numbers or alphanumeric)",
  "student_name": "the student's full name"
}

Important:
- Student ID can be just numbers (e.g., "123", "2024001") or alphanumeric (e.g., "STU001", "CS-2024-001")
- Look for labels like "Roll No", "Roll Number", "Student ID", "ID No", "Reg No", "ID", etc.
- Student name is usually written at the top of the page near ID
- If you cannot find either field, use null
- Do NOT include any explanation, ONLY return the JSON"""
        ).with_model("gemini", "gemini-2.5-flash").with_params(temperature=0)
        
        # Use first page only (usually has student info)
        from emergentintegrations.llm.chat import ImageContent
        first_page_image = ImageContent(image_base64=file_images[0])
        
        user_message = UserMessage(
            text="Extract the student ID/roll number and name from this answer sheet.",
            file_contents=[first_page_image]
        )
        
        response = await chat.send_message(user_message)
        response_text = response.strip()
        
        # Parse JSON response
        if "```json" in response_text:
            response_text = response_text.split("```json")[1].split("```")[0].strip()
        elif "```" in response_text:
            response_text = response_text.split("```")[1].split("```")[0].strip()
        
        import json
        result = json.loads(response_text)
        
        student_id = result.get("student_id")
        student_name = result.get("student_name")
        
        # Basic validation
        if student_id and student_name:
            # Clean up
            student_id = str(student_id).strip()
            student_name = str(student_name).strip().title()
            
            # Validate student ID is not too short or too long
            if 1 <= len(student_id) <= 30 and len(student_name) >= 2:
                return (student_id, student_name)
        
        return (None, None)
        
    except Exception as e:
        logger.error(f"Error extracting student info from paper: {e}")
        return (None, None)

def parse_student_from_filename(filename: str) -> tuple:
    """
    Parse student ID and name from filename
    Expected formats: 
    - STU003_Sagar_Maths.pdf -> (STU003, Sagar)
    - 123_John_Doe.pdf -> (123, John Doe)
    - StudentName.pdf -> (None, StudentName)
    Returns: (student_id, student_name)
    """
    try:
        # Remove .pdf extension
        name_part = filename.replace(".pdf", "").replace(".PDF", "")
        
        # Common subject names to filter out
        subject_names = [
            'maths', 'math', 'mathematics', 'english', 'science', 'physics', 
            'chemistry', 'biology', 'history', 'geography', 'hindi', 'sanskrit',
            'social', 'economics', 'commerce', 'accounts', 'computer', 'it',
            'arts', 'music', 'pe', 'physical', 'education', 'exam', 'test'
        ]
        
        # Split by underscore or hyphen
        parts = name_part.replace("-", "_").split("_")
        
        if len(parts) >= 2:
            # First part is likely student ID
            potential_id = parts[0].strip()
            
            # Remaining parts form the name, excluding subject names
            name_parts = []
            for part in parts[1:]:
                if part.lower() not in subject_names:
                    name_parts.append(part)
            
            potential_name = " ".join(name_parts).strip().title()
            
            # Validate ID (should be alphanumeric, not too long)
            if potential_id and len(potential_id) <= 20:
                return (potential_id, potential_name if potential_name else None)
        
        # Fallback: try to clean up the filename as a name
        student_name = name_part.replace("_", " ").replace("-", " ").strip().title()
        
        if student_name and len(student_name) >= 2:
            return (None, student_name)
        
        return (None, None)
    except Exception as e:
        logger.error(f"Error parsing filename {filename}: {e}")
        return (None, None)
        return (None, None)

async def get_or_create_student(
    student_id: str,
    student_name: str,
    batch_id: str,
    teacher_id: str
) -> tuple:
    """
    Get existing student or create new one
    Returns: (user_id, error_message)
    """
    # Check if student ID already exists
    existing = await db.users.find_one({"student_id": student_id, "role": "student"}, {"_id": 0})
    
    if existing:
        # Student exists - use existing student (allow re-grading)
        user_id = existing["user_id"]
        
        # Optionally update name if different (use the new one)
        if existing["name"].lower() != student_name.lower():
            # Log the name difference but don't treat as error - just use existing student
            logger.info(f"Student ID {student_id}: name '{student_name}' differs from existing '{existing['name']}', using existing student")
        
        # Add to batch if not already there
        if batch_id not in existing.get("batches", []):
            await db.users.update_one(
                {"user_id": user_id},
                {"$addToSet": {"batches": batch_id}}
            )
            # Also add student to batch document
            await db.batches.update_one(
                {"batch_id": batch_id},
                {"$addToSet": {"students": user_id}}
            )
        
        return (user_id, None)
    
    # Create new student
    user_id = f"user_{uuid.uuid4().hex[:12]}"
    new_student = {
        "user_id": user_id,
        "email": f"{student_id.lower()}@school.temp",  # Temporary email
        "name": student_name,
        "role": "student",
        "student_id": student_id,
        "batches": [batch_id],
        "teacher_id": teacher_id,
        "created_at": datetime.now(timezone.utc).isoformat()
    }
    
    await db.users.insert_one(new_student)
    
    # Add student to batch document
    await db.batches.update_one(
        {"batch_id": batch_id},
        {"$addToSet": {"students": user_id}}
    )
    
    return (user_id, None)

def pdf_to_images(pdf_bytes: bytes) -> List[str]:
    """Convert PDF pages to base64 images with compression - NO PAGE LIMIT"""
    images = []
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    
    # Process ALL pages - no limit
    for page_num in range(len(doc)):
        page = doc[page_num]
        # Use 1.5x zoom for balance between quality and token efficiency
        pix = page.get_pixmap(matrix=fitz.Matrix(1.5, 1.5))
        img_bytes = pix.tobytes("jpeg")
        
        # Compress the image to save storage (40-60% reduction)
        img = Image.open(io.BytesIO(img_bytes))
        
        # Compress with quality=60 (good balance of quality vs size)
        compressed_buffer = io.BytesIO()
        img.save(compressed_buffer, format="JPEG", quality=60, optimize=True)
        compressed_bytes = compressed_buffer.getvalue()
        
        # Convert to base64
        img_base64 = base64.b64encode(compressed_bytes).decode()
        images.append(img_base64)
    
    doc.close()
    logger.info(f"Converted PDF with {len(images)} pages to compressed images")
    return images

def detect_and_correct_rotation(image_base64: str) -> str:
    """
    Detect if an image is rotated and correct it.
    Uses PIL to analyze image orientation and rotate if needed.
    """
    from PIL import Image
    import io
    import base64
    
    try:
        # Decode base64 to image
        img_bytes = base64.b64decode(image_base64)
        img = Image.open(io.BytesIO(img_bytes))
        
        # Check EXIF orientation tag if available
        try:
            from PIL import ExifTags
            for orientation in ExifTags.TAGS.keys():
                if ExifTags.TAGS[orientation] == 'Orientation':
                    break
            exif = img._getexif()
            if exif is not None:
                orientation_value = exif.get(orientation)
                if orientation_value == 3:
                    img = img.rotate(180, expand=True)
                elif orientation_value == 6:
                    img = img.rotate(270, expand=True)
                elif orientation_value == 8:
                    img = img.rotate(90, expand=True)
        except (AttributeError, KeyError, IndexError):
            pass
        
        # Heuristic: Check if image is landscape but contains portrait text
        # Most answer sheets are portrait, so if width > height significantly, it might be rotated
        width, height = img.size
        if width > height * 1.3:  # Landscape orientation
            # Rotate 90 degrees counter-clockwise to make it portrait
            img = img.rotate(90, expand=True)
            logger.info(f"Rotated landscape image to portrait")
        
        # Convert back to base64
        buffer = io.BytesIO()
        img.save(buffer, format="JPEG", quality=85)
        return base64.b64encode(buffer.getvalue()).decode()
        
    except Exception as e:
        logger.error(f"Error in rotation detection: {e}")
        return image_base64  # Return original if detection fails

def correct_all_images_rotation(images: List[str]) -> List[str]:
    """Apply rotation correction to all images in a list."""
    corrected = []
    for idx, img in enumerate(images):
        corrected_img = detect_and_correct_rotation(img)
        corrected.append(corrected_img)
    return corrected

async def get_exam_model_answer_text(exam_id: str) -> str:
    """Get pre-extracted model answer text content for faster grading."""
    try:
        file_doc = await db.exam_files.find_one(
            {"exam_id": exam_id, "file_type": "model_answer"},
            {"_id": 0, "model_answer_text": 1}
        )
        if file_doc and file_doc.get("model_answer_text"):
            return file_doc["model_answer_text"]
    except Exception as e:
        logger.error(f"Error getting model answer text: {e}")
    return ""

async def extract_model_answer_content(
    model_answer_images: List[str],
    questions: List[dict]
) -> str:
    """
    Extract detailed answer content from model answer images as structured text.
    This is done ONCE during upload and stored for use during grading.
    """
    from emergentintegrations.llm.chat import LlmChat, UserMessage, ImageContent
    
    api_key = os.environ.get('EMERGENT_LLM_KEY')
    if not api_key:
        logger.error("No API key for model answer content extraction")
        return ""
    
    if not model_answer_images:
        return ""
    
    try:
        # Build questions context
        questions_context = ""
        for q in questions:
            q_num = q.get("question_number", "?")
            q_marks = q.get("total_marks", 0)
            questions_context += f"- Question {q_num} ({q_marks} marks)\n"
            for sq in q.get("sub_questions", []):
                sq_id = sq.get("sub_id", "?")
                sq_marks = sq.get("marks", 0)
                questions_context += f"  - Part {sq_id} ({sq_marks} marks)\n"
        
        # Process in chunks for large model answers
        CHUNK_SIZE = 8  # Process 8 pages at a time for stability
        all_extracted_content = []
        
        for chunk_start in range(0, len(model_answer_images), CHUNK_SIZE):
            chunk_end = min(chunk_start + CHUNK_SIZE, len(model_answer_images))
            chunk_images = model_answer_images[chunk_start:chunk_end]
            
            chat = LlmChat(
                api_key=api_key,
                session_id=f"extract_content_{uuid.uuid4().hex[:8]}",
                system_message="""You are an expert at extracting model answer content from exam papers.

Your task is to extract ALL answer content and structure it clearly.

For each question/sub-question:
1. Identify the question number
2. Extract the COMPLETE model answer text
3. Note any marking points or criteria

Output Format:
---
QUESTION [number]:
[Complete model answer text]

KEY POINTS:
- [Key point 1]
- [Key point 2]
---

Be thorough - extract EVERY detail useful for grading."""
            ).with_model("gemini", "gemini-2.5-flash").with_params(temperature=0)
            
            image_contents = [ImageContent(image_base64=img) for img in chunk_images]
            
            prompt = f"""Extract ALL model answer content from pages {chunk_start + 1} to {chunk_end}.

Questions in this exam:
{questions_context}

Extract complete answers for ALL questions visible on these pages."""

            user_message = UserMessage(text=prompt, file_contents=image_contents)
            
            max_retries = 3
            for attempt in range(max_retries):
                try:
                    logger.info(f"Extracting model answer content: pages {chunk_start + 1}-{chunk_end} (attempt {attempt + 1})")
                    response = await ai_call_with_timeout(
                        chat, 
                        user_message, 
                        timeout_seconds=90,
                        operation_name=f"Model answer extraction attempt {attempt+1}"
                    )
                    if response:
                        all_extracted_content.append(f"=== PAGES {chunk_start + 1}-{chunk_end} ===\n{response}")
                        break
                except Exception as e:
                    logger.error(f"Error extracting content chunk: {e}")
                    if attempt < max_retries - 1:
                        await asyncio.sleep(5 * (attempt + 1))
        
        full_content = "\n\n".join(all_extracted_content)
        logger.info(f"Extracted model answer content: {len(full_content)} chars from {len(model_answer_images)} pages")
        return full_content
        
    except Exception as e:
        logger.error(f"Error in extract_model_answer_content: {e}")
        return ""

async def extract_questions_from_question_paper(
    question_paper_images: List[str],
    num_questions: int
) -> List[str]:
    """Extract question text from question paper images using AI with improved sub-question handling"""
    from emergentintegrations.llm.chat import LlmChat, UserMessage, ImageContent
    
    api_key = os.environ.get('EMERGENT_LLM_KEY')
    if not api_key:
        return []
    
    try:
        chat = LlmChat(
            api_key=api_key,
            session_id=f"extract_qp_{uuid.uuid4().hex[:8]}",
            system_message="""You are an expert at extracting question text from exam question papers.

Extract ALL question text from the provided question paper images.
Return a JSON array with structured objects for each question.

CRITICAL INSTRUCTIONS FOR SUB-QUESTIONS:

1. For questions WITH sub-parts (a, b, c or i, ii, iii):
   - The parent question's rubric should be EMPTY or contain only a brief intro
   - Each sub-question's rubric MUST contain its FULL text
   - DO NOT put all text in the parent question

2. Text distribution example:
   Original: "Q5: (a) Explain photosynthesis. (b) Draw a diagram of a leaf."

   WRONG:
   {
     question_text: "Q5: (a) Explain photosynthesis. (b) Draw a diagram of a leaf.",
     rubric: "Explain photosynthesis. Draw a diagram of a leaf.",
     sub_questions: [
       { sub_id: "a", rubric: "" },
       { sub_id: "b", rubric: "" }
     ]
   }

   CORRECT:
   {
     question_text: "Q5:",
     rubric: "",
     sub_questions: [
       { sub_id: "a", rubric: "Explain photosynthesis." },
       { sub_id: "b", rubric: "Draw a diagram of a leaf." }
     ]
   }

3. For questions WITHOUT sub-parts:
   - Put the full text in the parent question's rubric field
   - sub_questions array should be empty []

4. Parsing rules:
   - (a), (b), (c) or (i), (ii), (iii) indicate sub-parts
   - Split text at each sub-part marker
   - Include the marker with its text: "(a) Explain..." not just "Explain..."

5. Nested sub-parts like (a)(i), (a)(ii):
   - These belong to sub-question "a"
   - Include them in sub_id "a"'s rubric: "(a) (i) First part (ii) Second part"

Required JSON structure for each question:
{
  "questions": [
    {
      "question_number": "string",
      "question_text": "Brief identifier only, e.g. 'Q5:' - NOT full text",
      "rubric": "Empty if has sub-parts, full text if no sub-parts",
      "max_marks": number,
      "sub_questions": [
        {
          "sub_id": "a",
          "rubric": "FULL TEXT of sub-part (a) goes here",
          "max_marks": number
        }
      ]
    }
  ]
}
"""
        ).with_model("gemini", "gemini-2.5-flash").with_params(temperature=0)
        
        # Create image contents - process ALL pages, no limit
        image_contents = [ImageContent(image_base64=img) for img in question_paper_images]
        logger.info(f"Extracting questions from {len(image_contents)} question paper pages")
        
        prompt = f"""Extract the questions from this question paper.
        
Expected number of questions: {num_questions}

Return ONLY the JSON, no other text."""
        
        user_message = UserMessage(text=prompt, file_contents=image_contents)
        
        # Retry logic for question extraction
        import asyncio
        max_retries = 3
        retry_delay = 5
        
        for attempt in range(max_retries):
            try:
                logger.info(f"Question extraction attempt {attempt + 1}/{max_retries}")
                ai_response = await ai_call_with_timeout(
                    chat,
                    user_message,
                    timeout_seconds=90,
                    operation_name=f"Question extraction attempt {attempt+1}"
                )
                
                # Parse response with robust JSON extraction
                import json
                import re
                response_text = ai_response.strip()
                
                # Strategy 1: Direct parse
                try:
                    result = json.loads(response_text)
                    logger.info(f"Successfully extracted {len(result.get('questions', []))} questions")
                    return result.get("questions", [])
                except json.JSONDecodeError:
                    pass
                
                # Strategy 2: Remove code blocks
                if response_text.startswith("```"):
                    response_text = response_text.split("```")[1]
                    if response_text.startswith("json"):
                        response_text = response_text[4:]
                    response_text = response_text.strip()
                    try:
                        result = json.loads(response_text)
                        logger.info(f"Successfully extracted {len(result.get('questions', []))} questions")
                        return result.get("questions", [])
                    except json.JSONDecodeError:
                        pass
                
                # Strategy 3: Find JSON object in text
                json_match = re.search(r'\{[^{}]*"questions"[^{}]*\[[^\]]*\][^{}]*\}', response_text, re.DOTALL)
                if json_match:
                    try:
                        result = json.loads(json_match.group())
                        logger.info(f"Successfully extracted {len(result.get('questions', []))} questions (pattern match)")
                        return result.get("questions", [])
                    except json.JSONDecodeError:
                        pass
                
                # If all strategies fail, log and retry
                logger.warning(f"Failed to parse JSON from response (attempt {attempt + 1}). Response preview: {response_text[:200]}")
                if attempt < max_retries - 1:
                    wait_time = retry_delay * (2 ** attempt)
                    logger.info(f"Retrying question extraction in {wait_time} seconds...")
                    await asyncio.sleep(wait_time)
                    continue
                else:
                    logger.error(f"All JSON parsing strategies failed after {max_retries} attempts")
                    return []
                
            except Exception as e:
                error_str = str(e).lower()
                logger.error(f"Error during question extraction attempt {attempt + 1}: {e}")
                if attempt < max_retries - 1 and ("502" in error_str or "503" in error_str or "timeout" in error_str or "gateway" in error_str or "rate limit" in error_str):
                    wait_time = retry_delay * (2 ** attempt)
                    logger.info(f"Retrying question extraction in {wait_time} seconds...")
                    await asyncio.sleep(wait_time)
                else:
                    if attempt >= max_retries - 1:
                        logger.error(f"Question extraction failed after {max_retries} attempts")
                    raise e
        
        return []
        
    except Exception as e:
        logger.error(f"Error extracting questions from question paper: {e}")
        return []

async def extract_questions_from_model_answer(
    model_answer_images: List[str],
    num_questions: int
) -> List[str]:
    """Extract question text from model answer images using AI with improved sub-question handling"""
    from emergentintegrations.llm.chat import LlmChat, UserMessage, ImageContent
    
    # Check cache
    cache_key = get_model_answer_hash(model_answer_images)
    if cache_key in model_answer_cache:
        logger.info(f"Cache hit (memory) for model answer extraction")
        return model_answer_cache[cache_key]

    api_key = os.environ.get('EMERGENT_LLM_KEY')
    if not api_key:
        return []
    
    try:
        chat = LlmChat(
            api_key=api_key,
            session_id=f"extract_{uuid.uuid4().hex[:8]}",
            system_message="""You are an expert at extracting question text from exam papers.
            
Extract ONLY the question text (not answers) from the provided model answer images.
Return a JSON array with structured objects for each question.

CRITICAL INSTRUCTIONS FOR SUB-QUESTIONS:

1. For questions WITH sub-parts (a, b, c or i, ii, iii):
   - The parent question's rubric should be EMPTY or contain only a brief intro
   - Each sub-question's rubric MUST contain its FULL text
   - DO NOT put all text in the parent question

2. Text distribution example:
   Original: "Q5: (a) Explain photosynthesis. (b) Draw a diagram of a leaf."

   WRONG:
   {
     question_text: "Q5: (a) Explain photosynthesis. (b) Draw a diagram of a leaf.",
     rubric: "Explain photosynthesis. Draw a diagram of a leaf.",
     sub_questions: [
       { sub_id: "a", rubric: "" },
       { sub_id: "b", rubric: "" }
     ]
   }

   CORRECT:
   {
     question_text: "Q5:",
     rubric: "",
     sub_questions: [
       { sub_id: "a", rubric: "Explain photosynthesis." },
       { sub_id: "b", rubric: "Draw a diagram of a leaf." }
     ]
   }

3. For questions WITHOUT sub-parts:
   - Put the full text in the parent question's rubric field
   - sub_questions array should be empty []

4. DO NOT include answer content, only question text
5. Look through ALL pages carefully
6. Return questions in order (Q1, Q2, Q3, etc.)

Required JSON structure:
{
  "questions": [
    {
      "question_number": "string",
      "question_text": "Brief identifier only",
      "rubric": "Empty if has sub-parts, full text if no sub-parts",
      "max_marks": number,
      "sub_questions": [
        {
          "sub_id": "a",
          "rubric": "FULL TEXT of sub-part (a)",
          "max_marks": number
        }
      ]
    }
  ]
}
"""
        ).with_model("gemini", "gemini-2.5-flash").with_params(temperature=0)
        
        # Create image contents - process ALL pages, no limit
        image_contents = [ImageContent(image_base64=img) for img in model_answer_images]
        logger.info(f"Extracting questions from {len(image_contents)} model answer pages")
        
        prompt = f"""Extract the question text from these model answer images.
        
CRITICAL: There are {num_questions} questions in this exam. You MUST extract ALL {num_questions} questions!

Look carefully through ALL images. Questions might be on different pages.

Extract each question's complete text. Do NOT include answers, only the question text.

Return ONLY the JSON with ALL {num_questions} questions, no other text."""
        
        user_message = UserMessage(
            text=prompt,
            file_contents=image_contents
        )
        
        # Retry logic for model answer extraction
        import asyncio
        max_retries = 3
        retry_delay = 5
        
        for attempt in range(max_retries):
            try:
                logger.info(f"Model answer extraction attempt {attempt + 1}/{max_retries}")
                ai_response = await chat.send_message(user_message)
                
                # Parse JSON response
                response_text = ai_response.strip()
                if response_text.startswith("```"):
                    response_text = response_text.split("```")[1]
                    if response_text.startswith("json"):
                        response_text = response_text[4:]
                
                import json
                result = json.loads(response_text)
                questions = result.get("questions", [])

                logger.info(f"Successfully extracted {len(questions)} questions from model answer")

                # Cache result
                model_answer_cache[cache_key] = questions
                return questions
                
            except Exception as e:
                error_str = str(e).lower()
                if attempt < max_retries - 1 and ("502" in error_str or "503" in error_str or "timeout" in error_str or "gateway" in error_str):
                    wait_time = retry_delay * (2 ** attempt)
                    logger.info(f"Retrying model answer extraction in {wait_time} seconds...")
                    await asyncio.sleep(wait_time)
                else:
                    raise e
        
        return []
        
    except Exception as e:
        logger.error(f"Error extracting questions: {e}")
        return []

async def extract_question_structure_from_paper(
    paper_images: List[str],
    paper_type: str = "question_paper"
) -> List[dict]:
    """
    Extract COMPLETE question structure including:
    - Question numbers
    - Sub-questions with IDs (a, b, c OR i, ii, iii)
    - Sub-sub-questions if any
    - Marks for each question/sub-question
    - Question text
    
    Returns a list of question dictionaries matching the exam structure.
    """
    from emergentintegrations.llm.chat import LlmChat, UserMessage, ImageContent
    
    api_key = os.environ.get('EMERGENT_LLM_KEY')
    if not api_key:
        logger.error("EMERGENT_LLM_KEY not configured")
        return []
    
    try:
        chat = LlmChat(
            api_key=api_key,
            session_id=f"extract_struct_{uuid.uuid4().hex[:8]}",
            system_message=f"""You are an expert at analyzing exam {paper_type.replace('_', ' ')}s and extracting complete question structure.

Your task is to extract:
1. ALL questions with their numbers (Q1, Q2, Q3, etc.)
2. Sub-questions with their IDs (a, b, c OR i, ii, iii)
3. Sub-sub-questions if they exist (like Q1(a)(i), Q1(a)(ii))
4. Marks allocated to each question and sub-question
5. Brief question text for each
6. **CRITICAL: Detect optional questions** - Look for instructions like "Attempt any X out of Y", "Answer any 4 questions", etc.

Return a JSON array where each question has this structure:
{{
  "question_number": 1,
  "max_marks": 12,
  "rubric": "Brief question text here",
  "question_text": "Brief question text here",
  "is_optional": false,
  "optional_group": null,
  "required_count": null,
  "sub_questions": [
    {{
      "sub_id": "a",
      "max_marks": 2,
      "rubric": "Part (a) text here"
    }},
    {{
      "sub_id": "b", 
      "max_marks": 10,
      "rubric": "Part (b) text here",
      "sub_questions": [
        {{
          "sub_id": "i",
          "max_marks": 5,
          "rubric": "Part (b)(i) text here"
        }},
        {{
          "sub_id": "ii",
          "max_marks": 5,
          "rubric": "Part (b)(ii) text here"
        }}
      ]
    }}
  ]
}}

CRITICAL RULES:
1. Extract EVERY question you see
2. Detect if sub-questions use letters (a,b,c) or roman numerals (i,ii,iii)
3. If a question has no sub-parts, leave sub_questions as empty array []
4. Sum of sub-question marks MUST equal parent question marks
5. Extract marks carefully - look for [10 marks], (5 marks), etc.
6. Keep question text brief but meaningful
7. **OPTIONAL QUESTIONS DETECTION:**
   - Look for phrases like "Attempt any X out of Y", "Answer any 4 questions", "Choose any 3"
   - If found, mark those questions with: is_optional=true, optional_group="group1", required_count=X
   - Calculate effective_total_marks by considering only the required questions from optional groups
   - Example: "Answer any 4 out of 6 questions (each 10 marks)" → 6 questions marked as optional, required_count=4, effective_marks=40

Return ONLY a JSON array of questions, nothing else."""
        ).with_model("gemini", "gemini-2.5-flash").with_params(temperature=0)
        
        # Create image contents - CHUNK if too many pages
        CHUNK_SIZE = 10  # Process 10 pages at a time to avoid timeouts
        all_images = paper_images
        
        if len(all_images) > CHUNK_SIZE:
            logger.info(f"Large document ({len(all_images)} pages) - processing in chunks of {CHUNK_SIZE}")
            all_extracted_questions = []
            
            for chunk_start in range(0, len(all_images), CHUNK_SIZE):
                chunk_end = min(chunk_start + CHUNK_SIZE, len(all_images))
                chunk_images = all_images[chunk_start:chunk_end]
                
                logger.info(f"Processing pages {chunk_start+1}-{chunk_end} ({len(chunk_images)} pages)")
                
                image_contents = [ImageContent(image_base64=img) for img in chunk_images]
                
                prompt = f"""Analyze this {paper_type.replace('_', ' ')} (Pages {chunk_start+1}-{chunk_end}) and extract the question structure.

Instructions:
- Identify ALL questions visible in these pages
- For each question, identify ALL sub-parts with their marks
- Detect the numbering style (a,b,c vs i,ii,iii)
- Extract marks for each part
- If a question spans multiple chunks, extract what's visible here

Return ONLY the JSON array of questions."""
        
                chunk_message = UserMessage(text=prompt, file_contents=image_contents)
                
                # Try to extract this chunk
                max_retries = 2  # Fewer retries per chunk
                retry_delay = 5
                chunk_questions = []
                
                for attempt in range(max_retries):
                    try:
                        logger.info(f"Chunk {chunk_start+1}-{chunk_end} extraction attempt {attempt + 1}/{max_retries}")
                        ai_response = await chat.send_message(chunk_message)
                        
                        import json
                        import re
                        response_text = ai_response.strip()
                        
                        # Try parsing
                        try:
                            result = json.loads(response_text)
                            if isinstance(result, list):
                                chunk_questions = result
                                break
                            elif isinstance(result, dict) and "questions" in result:
                                chunk_questions = result["questions"]
                                break
                        except:
                            pass
                        
                        # Remove code blocks
                        if response_text.startswith("```"):
                            response_text = response_text.split("```")[1]
                            if response_text.startswith("json"):
                                response_text = response_text[4:]
                            response_text = response_text.strip()
                            try:
                                result = json.loads(response_text)
                                if isinstance(result, list):
                                    chunk_questions = result
                                    break
                                elif isinstance(result, dict) and "questions" in result:
                                    chunk_questions = result["questions"]
                                    break
                            except:
                                pass
                        
                        logger.warning(f"Failed to parse chunk {chunk_start+1}-{chunk_end} (attempt {attempt + 1})")
                        if attempt < max_retries - 1:
                            await asyncio.sleep(retry_delay)
                            
                    except Exception as e:
                        logger.error(f"Error extracting chunk {chunk_start+1}-{chunk_end} (attempt {attempt + 1}): {e}")
                        if attempt < max_retries - 1:
                            await asyncio.sleep(retry_delay)
                
                if chunk_questions:
                    logger.info(f"✅ Extracted {len(chunk_questions)} questions from pages {chunk_start+1}-{chunk_end}")
                    all_extracted_questions.extend(chunk_questions)
                else:
                    logger.warning(f"⚠️ Failed to extract questions from pages {chunk_start+1}-{chunk_end}")
            
            logger.info(f"✅ Total: Extracted {len(all_extracted_questions)} questions from {len(all_images)} pages (chunked)")
            return all_extracted_questions
        else:
            # Small document - process all at once
            image_contents = [ImageContent(image_base64=img) for img in paper_images]
            logger.info(f"Extracting complete question structure from {len(image_contents)} pages ({paper_type})")
        
            prompt = f"""Analyze this {paper_type.replace('_', ' ')} and extract the COMPLETE question structure.

Instructions:
- Identify ALL questions (Q1, Q2, Q3...)
- For each question, identify ALL sub-parts with their marks
- Detect the numbering style (a,b,c vs i,ii,iii)
- Extract marks for each part
- Be thorough - don't miss any questions or sub-questions

Return ONLY the JSON array of questions."""
        
        user_message = UserMessage(text=prompt, file_contents=image_contents)
        
        # Retry logic
        max_retries = 3
        retry_delay = 5
        
        for attempt in range(max_retries):
            try:
                logger.info(f"Structure extraction attempt {attempt + 1}/{max_retries}")
                ai_response = await ai_call_with_timeout(
                    chat,
                    user_message,
                    timeout_seconds=90,
                    operation_name=f"Structure extraction attempt {attempt+1}"
                )
                
                # Robust JSON parsing
                import json
                import re
                response_text = ai_response.strip()
                
                # Strategy 1: Direct parse
                try:
                    result = json.loads(response_text)
                    if isinstance(result, list):
                        logger.info(f"✅ Extracted structure for {len(result)} questions")
                        return result
                    elif isinstance(result, dict) and "questions" in result:
                        logger.info(f"✅ Extracted structure for {len(result['questions'])} questions")
                        return result["questions"]
                except json.JSONDecodeError:
                    pass
                
                # Strategy 2: Remove code blocks
                if response_text.startswith("```"):
                    response_text = response_text.split("```")[1]
                    if response_text.startswith("json"):
                        response_text = response_text[4:]
                    response_text = response_text.strip()
                    try:
                        result = json.loads(response_text)
                        if isinstance(result, list):
                            logger.info(f"✅ Extracted structure for {len(result)} questions (code block)")
                            return result
                        elif isinstance(result, dict) and "questions" in result:
                            return result["questions"]
                    except json.JSONDecodeError:
                        pass
                
                # Strategy 3: Find JSON array in text
                json_match = re.search(r'\[\s*\{.*?\}\s*\]', response_text, re.DOTALL)
                if json_match:
                    try:
                        result = json.loads(json_match.group())
                        logger.info(f"✅ Extracted structure for {len(result)} questions (pattern match)")
                        return result
                    except json.JSONDecodeError:
                        pass
                
                # All strategies failed
                logger.warning(f"Failed to parse structure JSON (attempt {attempt + 1}). Response preview: {response_text[:200]}")
                if attempt < max_retries - 1:
                    wait_time = retry_delay * (2 ** attempt)
                    logger.info(f"Retrying structure extraction in {wait_time} seconds...")
                    await asyncio.sleep(wait_time)
                    continue
                else:
                    logger.error(f"All JSON parsing strategies failed after {max_retries} attempts")
                    return []
                
            except Exception as e:
                error_str = str(e).lower()
                logger.error(f"Error during structure extraction attempt {attempt + 1}: {e}")
                if attempt < max_retries - 1 and ("502" in error_str or "503" in error_str or "timeout" in error_str or "rate limit" in error_str):
                    wait_time = retry_delay * (2 ** attempt)
                    logger.info(f"Retrying structure extraction in {wait_time} seconds...")
                    await asyncio.sleep(wait_time)
                else:
                    if attempt >= max_retries - 1:
                        logger.error(f"Structure extraction failed after {max_retries} attempts")
                    raise e
        
        return []
        
    except Exception as e:
        logger.error(f"Error extracting question structure: {e}")
        return []

async def auto_extract_questions(exam_id: str, force: bool = False) -> Dict[str, Any]:
    """
    Auto-extract COMPLETE question structure from question paper (priority) or model answer.
    
    Extracts:
    - Question numbers
    - Sub-questions with proper IDs
    - Marks for each part
    - Question text
    
    Priority:
    1. Question Paper (if exists)
    2. Model Answer (if exists)

    If 'force' is True, re-extraction is performed even if already extracted from the target source.
    """
    try:
        # Get exam
        exam = await db.exams.find_one({"exam_id": exam_id}, {"_id": 0})
        if not exam:
            logger.error(f"Auto-extraction failed: Exam {exam_id} not found")
            return {"success": False, "message": "Exam not found"}

        # Check available sources
        qp_imgs = await get_exam_question_paper_images(exam_id)
        ma_imgs = await get_exam_model_answer_images(exam_id)

        target_source = None
        images_to_use = []

        if qp_imgs:
            target_source = "question_paper"
            images_to_use = qp_imgs
        elif ma_imgs:
            target_source = "model_answer"
            images_to_use = ma_imgs
        else:
            return {"success": False, "message": "No documents available for extraction"}

        # Check if already extracted
        current_source = exam.get("extraction_source")
        questions_exist = len(exam.get("questions", [])) > 0 and any(q.get("rubric") for q in exam.get("questions", []))

        if not force and questions_exist and current_source == target_source:
            logger.info(f"Skipping extraction for {exam_id}: Already extracted from {current_source}")
            return {
                "success": True,
                "message": f"Questions already extracted from {target_source.replace('_', ' ')}",
                "count": len(exam.get("questions", [])),
                "source": target_source,
                "skipped": True
            }

        logger.info(f"Auto-extracting COMPLETE question structure for {exam_id} from {target_source} (Force={force})")

        # Perform NEW structure extraction
        extracted_questions = await extract_question_structure_from_paper(
            images_to_use,
            paper_type=target_source
        )

        if not extracted_questions:
            logger.warning(f"Structure extraction returned no questions for {exam_id} from {target_source}")
            return {"success": False, "message": f"Failed to extract question structure from {target_source.replace('_', ' ')}"}

        # Calculate total marks from extracted structure, handling optional questions
        total_marks = 0
        optional_groups = {}
        
        for q in extracted_questions:
            is_optional = q.get("is_optional", False)
            
            if is_optional:
                # Group optional questions together
                group_id = q.get("optional_group", "default_optional")
                if group_id not in optional_groups:
                    optional_groups[group_id] = {
                        "questions": [],
                        "required_count": q.get("required_count", 0)
                    }
                optional_groups[group_id]["questions"].append(q)
            else:
                # Non-optional question - add full marks
                total_marks += q.get("max_marks", 0)
        
        # Calculate marks for optional groups
        for group_id, group_data in optional_groups.items():
            questions = group_data["questions"]
            required_count = group_data["required_count"]
            
            if required_count > 0 and len(questions) > 0:
                # Take marks from the required number of questions (typically all have same marks)
                # Use the first question's marks as representative
                marks_per_question = questions[0].get("max_marks", 0)
                group_effective_marks = marks_per_question * min(required_count, len(questions))
                total_marks += group_effective_marks
                logger.info(f"Optional group '{group_id}': {len(questions)} questions, need {required_count}, contributing {group_effective_marks} marks")
            else:
                # Fallback: add all marks if no required_count specified
                for q in questions:
                    total_marks += q.get("max_marks", 0)

        logger.info(f"Calculated total marks from extraction: {total_marks} (including optional question handling)")

        # Preserve user's original total_marks if it was explicitly set during exam creation
        # Only overwrite if user kept the default 100 or didn't set it
        user_total_marks = exam.get("total_marks", 100)
        
        # Use user's value if they changed it from default, otherwise use extracted value
        if user_total_marks and user_total_marks != 100:
            final_total_marks = user_total_marks
            logger.info(f"✓ Preserving user's total marks: {final_total_marks} (extracted: {total_marks})")
        else:
            final_total_marks = total_marks
            logger.info(f"✓ Using extracted total marks: {final_total_marks}")

        # STEP 1: Delete old questions for this exam to prevent duplicates
        delete_result = await db.questions.delete_many({"exam_id": exam_id})
        logger.info(f"Deleted {delete_result.deleted_count} old questions for exam {exam_id}")

        # STEP 2: Prepare questions for insertion with exam_id and unique question_id
        questions_to_insert = []
        for q in extracted_questions:
            question_doc = {
                "question_id": f"q_{uuid.uuid4().hex[:12]}",
                "exam_id": exam_id,
                **q
            }
            questions_to_insert.append(question_doc)

        # STEP 3: Insert questions into the questions collection
        if questions_to_insert:
            await db.questions.insert_many(questions_to_insert)
            logger.info(f"Inserted {len(questions_to_insert)} questions into database")

        # STEP 4: Update exam document with questions array, metadata, and correct counts
        await db.exams.update_one(
            {"exam_id": exam_id},
            {"$set": {
                "questions": extracted_questions,
                "questions_count": len(extracted_questions),
                "extraction_source": target_source,
                "total_marks": final_total_marks  # Preserve user's total marks if they set it
            }}
        )

        logger.info(f"✅ Successfully extracted and saved {len(extracted_questions)} questions with complete structure from {target_source}")
        return {
            "success": True,
            "message": f"Successfully extracted {len(extracted_questions)} questions with structure from {target_source.replace('_', ' ')}",
            "count": len(extracted_questions),
            "total_marks": final_total_marks,
            "extracted_total_marks": total_marks,  # Include calculated marks for reference
            "source": target_source,
            "skipped": False
        }

    except Exception as e:
        logger.error(f"Auto-extraction error for {exam_id}: {e}")
        return {"success": False, "message": f"Error during extraction: {str(e)}"}

async def fetch_teacher_learning_patterns(teacher_id: str, subject_id: str, exam_id: str = None):
    """
    Fetch past teacher corrections to apply as learned patterns
    Returns list of relevant corrections for this teacher + subject
    """
    try:
        query = {
            "teacher_id": teacher_id,
            "subject_id": subject_id,
            "$or": [
                {"apply_to_all": True},  # Patterns meant to be applied broadly
                {"exam_id": exam_id} if exam_id else {}
            ]
        }
        
        # Fetch recent corrections (last 100)
        corrections = await db.grading_feedback.find(
            query,
            {"_id": 0, "question_number": 1, "question_topic": 1, "teacher_correction": 1, 
             "teacher_expected_grade": 1, "ai_grade": 1, "created_at": 1, "exam_id": 1}
        ).sort("created_at", -1).limit(100).to_list(100)
        
        logger.info(f"Found {len(corrections)} learned patterns for teacher {teacher_id}, subject {subject_id}")
        return corrections
    except Exception as e:
        logger.error(f"Error fetching learning patterns: {e}")
        return []


async def grade_with_ai(
    images: List[str],
    model_answer_images: List[str],
    questions: List[dict],
    grading_mode: str,
    total_marks: float,
    model_answer_text: str = ""  # NEW: Pre-extracted model answer content
) -> List[QuestionScore]:
    """Grade answer paper using GPT-4o-mini with the GradeSense Master Instruction Set.
    
    If model_answer_text is provided, uses text-based grading (faster, more reliable).
    Falls back to image-based grading if text is not available.
    """
    from emergentintegrations.llm.chat import LlmChat, UserMessage, ImageContent
    import hashlib
    
    api_key = os.environ.get('EMERGENT_LLM_KEY')
    if not api_key:
        raise HTTPException(status_code=500, detail="AI service not configured")
    
    # Apply rotation correction to student images
    logger.info("Applying rotation correction to student images...")
    corrected_images = correct_all_images_rotation(images)
    
    # Determine grading mode: text-based (preferred) or image-based (fallback)
    use_text_based_grading = bool(model_answer_text and len(model_answer_text) > 100)
    
    if use_text_based_grading:
        logger.info(f"Using TEXT-BASED grading (model answer: {len(model_answer_text)} chars)")
    else:
        logger.info(f"Using IMAGE-BASED grading (model answer: {len(model_answer_images)} images)")
    
    # Create content hash for deterministic grading (same paper = same grade)
    hash_content = "".join(corrected_images).encode() + str(questions).encode() + grading_mode.encode()
    if use_text_based_grading:
        hash_content += model_answer_text.encode()
    else:
        hash_content += "".join(model_answer_images).encode()
    paper_hash = hashlib.sha256(hash_content).hexdigest()
    content_hash = paper_hash[:16]

    # Check cache (Memory)
    if paper_hash in grading_cache:
        logger.info(f"Cache hit (memory) for paper {paper_hash}")
        return grading_cache[paper_hash]

    # Check cache (Database)
    try:
        cached_result = await db.grading_results.find_one({"paper_hash": paper_hash})
        if cached_result and "results" in cached_result:
            logger.info(f"Cache hit (db) for paper {paper_hash}")
            import json
            results_data = json.loads(cached_result["results"])
            return [QuestionScore(**s) for s in results_data]
    except Exception as e:
        logger.error(f"Error checking grading cache: {e}")
    
    # ============== GRADESENSE MASTER GRADING MODE SPECIFICATIONS ==============
    mode_instructions = {
        "strict": """🔴 STRICT MODE - Academic rigor at its highest. Every step matters. Precision is paramount.

STEP-BY-STEP VERIFICATION:
- Every step in the model answer must be present
- Steps must be in logical order
- Skipping steps = mark deduction even if final answer is correct
- Each step carries independent weightage
- Working must be clearly shown

KEYWORD PRECISION:
- Technical terms must be exact
- Spelling of scientific/technical terms matters
- Definitions must be precise
- No credit for vague or approximately correct terminology

FORMAT STRICTNESS:
- Answer format must match expectations
- Diagrams must be properly labeled with all parts
- Units MUST be present for all numerical answers
- Proper mathematical notation required

ERROR PENALTIES:
- Calculation errors: Lose marks for all subsequent dependent steps
- Conceptual errors: Significant penalty
- Unit errors: Dedicated mark deduction
- Missing steps: Full step marks deducted
- Wrong method, right answer: Minimal credit (method matters)

PARTIAL MARKING:
- Only award marks for completely correct components
- Half-right steps = 0 marks for that step
- Ambiguous answers = no benefit of doubt
- Minimum threshold for any marks = 70% correctness of that component""",

        "balanced": """⚖️ BALANCED MODE (DEFAULT) - Fair and reasonable evaluation. Academic standards maintained while acknowledging genuine understanding.

DUAL ASSESSMENT:
- Evaluate both PROCESS and OUTCOME
- Both method and answer contribute to marks
- Approximate weight: 60% process, 40% outcome

REASONABLE EXPECTATIONS:
- Key steps must be present (not all steps)
- Important keywords must appear (not all keywords)
- Format should be appropriate (not perfect)
- Understanding should be evident
- Apply "would a reasonable teacher accept this?" test

STANDARD PARTIAL MARKING:
- Correct method, wrong answer: 60-70% marks
- Wrong method, correct answer: 30-40% marks
- Partially correct: Proportional to correctness
- Missing minor elements: Minor deductions
- Missing major elements: Significant deductions

PRACTICAL TOLERANCE:
- Minor calculation errors: Small penalty if method is correct
- Unit errors: Dedicated small penalty (typically 0.5-1 mark)
- Rounding differences: Accept within reasonable range
- Minor spelling errors in non-technical terms: Ignore
- Presentation issues: No penalty unless significantly affecting readability""",

        "conceptual": """🔵 CONCEPTUAL MODE - Understanding over procedure. The destination matters more than the exact path.

UNDERSTANDING VERIFICATION:
- Focus on whether the student understands the core concept
- The "essence" of the answer must be correct
- Can the student explain WHY, not just WHAT
- Look for evidence of genuine understanding

METHOD FLEXIBILITY:
- Accept alternative valid methods
- Different approach reaching correct answer = full marks
- Steps can be skipped IF logic is evident
- Personal explanation style accepted

KEYWORD FLEXIBILITY:
- Accept synonyms for technical terms
- Accept explanatory phrases instead of exact terminology
- Spelling errors in technical terms: Accept if phonetically similar and context clear
- Understanding demonstrated through explanation = keyword credit

WHAT MATTERS:
- Core concept correctly identified and applied
- Logical reasoning evident
- Final understanding/answer correct
- Ability to connect concepts

WHAT IS OVERLOOKED:
- Minor procedural variations
- Non-essential steps skipped (if logic is clear)
- Minor calculation errors that don't affect conceptual understanding
- Formatting preferences
- Order of presentation (if all components present)

PARTIAL MARKING:
- Award marks for demonstrated understanding even if execution is flawed
- Give credit for correct approach even if final answer is wrong
- Minimum threshold for any marks = 50% correctness of concept""",

        "lenient": """🟢 LENIENT MODE - Encourage and reward effort. Recognize attempts and guide toward correctness.

ATTEMPT RECOGNITION:
- Any genuine attempt at answering earns consideration
- Starting the problem correctly = minimum marks
- Showing relevant formula/concept = partial credit
- Being "on the right track" = proportional credit
- Effort and engagement valued

FLOOR MARKS SYSTEM:
- Every genuine attempt has a floor (minimum marks)
- Writing relevant formula = 10-20% of question marks
- Showing understanding of what's being asked = 10-15% of question marks
- Floor = MAX(attempt_value, 10% of question marks)

GENEROUS PARTIAL MARKING:
- Each correct element independently credited
- Correct parts not penalized for incorrect parts
- 2 out of 5 points mentioned = 40% marks (or more if substantial)
- Minimum threshold for any marks = 25% correctness

ERROR TOLERANCE:
- Calculation errors: Still credit the method and correct steps
- Conceptual confusion: Credit any correct portions
- Missing units: Minor penalty, not full marks lost
- Wrong method, right answer: Partial credit for answer
- Right method, wrong answer: Significant credit for method
- Carry-forward errors: Credit subsequent correct logic

INTERPRETATION GENEROSITY:
- Give benefit of doubt on ambiguous answers
- Interpret unclear handwriting favorably when possible
- Accept reasonable interpretations of questions"""
    }
    
    grading_instruction = mode_instructions.get(grading_mode, mode_instructions["balanced"])
    
    # ============== GRADESENSE MASTER INSTRUCTION SET ==============
    master_system_prompt = f"""# GRADESENSE AI GRADING ENGINE

You are the GradeSense Grading Engine - an advanced AI system designed to evaluate handwritten student answer papers with the precision, consistency, and pedagogical understanding of an expert educator.

## FUNDAMENTAL PRINCIPLES (SACRED - NEVER VIOLATE)

### 1. CONSISTENCY IS SACRED
- If the same paper is graded twice, the marks MUST be identical
- If two students write identical answers, they MUST receive identical marks
- Your grading decisions must be reproducible and explainable
- Never let randomness or uncertainty affect final scores
- When in doubt, flag for human review rather than guess

### 2. THE MODEL ANSWER IS YOUR HOLY GRAIL
- When a model answer is provided, treat it as the definitive reference
- Study it thoroughly before grading any paper
- Understand not just what is written, but the underlying logic and expectations
- Never contradict what the model answer establishes as correct

### 3. FAIRNESS ABOVE ALL
- Every student deserves unbiased evaluation
- Grade the answer, not the handwriting aesthetics
- Apply the same standards consistently across all papers
- Be the impartial evaluator every student deserves

## CURRENT GRADING MODE: {grading_mode.upper()}

{grading_instruction}

## ANSWER TYPE HANDLING

### Mathematical Problems
- Identify all logical steps in the solution
- Each step has independent value
- Carry-forward principle: If step 1 is wrong, credit correct logic in steps 2-n based on wrong value
- Formula stated correctly = marks (even if not applied correctly)
- Units MUST be present in final answers
- Alternative valid methods = full marks

### Diagrams and Labeled Drawings
- Component presence: Check all parts drawn
- Labels: Correct and complete
- Proportions: Reasonably accurate
- Partial credit based on percentage of components correct

### Short/Long Answers
- Key points coverage check
- Each key point = proportional marks
- Extra correct info doesn't compensate missing key points
- Introduction-Body-Conclusion structure for long answers

### MCQ/Objective
- Binary evaluation: Correct = full marks, Wrong = 0
- Multiple selections when single expected = 0

## HANDWRITING INTERPRETATION

- Use question context to aid recognition
- Use subject vocabulary to resolve ambiguous characters
- If character is ambiguous, consider most likely interpretation in context
- Honor final visible answer (ignore crossed-out content)
- If largely illegible, flag for teacher review

## EDGE CASE HANDLING

- BLANK ANSWERS: Award 0 marks, feedback: "No answer provided."
- IRRELEVANT CONTENT: Award 0 marks, flag for teacher awareness
- QUESTION REWRITTEN ONLY: Award 0 marks
- **CRITICAL**: If a question IS ANSWERED (even poorly), NEVER return -1.0 for obtained_marks
  - -1.0 means "question not found on these pages"
  - 0.0 means "question found but answer is wrong/blank"
  - Always check EVERY page before marking as "not found"
- **MULTI-PAGE ANSWERS**: Questions may span multiple pages - read ALL pages carefully
- MULTIPLE ANSWERS PROVIDED: Consider what appears to be final/emphasized answer
- ANSWER CONTRADICTS ITSELF: Grade based on predominant correct content, note contradiction
- BORDERLINE SCORES: Flag for teacher review with note

## OUTPUT FORMAT

Return your response in this exact JSON format:
{{
  "scores": [
    {{
      "question_number": 1,
      "obtained_marks": 8.5,
      "ai_feedback": "Detailed feedback with: 1) What was done well, 2) What was missing/incorrect, 3) How to improve",
      "sub_scores": [
        {{"sub_id": "a", "obtained_marks": 3, "ai_feedback": "Feedback for part a"}},
        {{"sub_id": "b", "obtained_marks": 2.5, "ai_feedback": "Feedback for part b"}}
      ],
      "confidence": 0.95
    }}
  ],
  "grading_notes": "Any overall observations about the paper"
}}

**CRITICAL - SUB-QUESTION HANDLING:**
- If a question has sub-parts (like Q32 has parts a and b), you MUST populate the sub_scores array
- Each sub-part gets its own: sub_id, obtained_marks, and ai_feedback
- The question's main obtained_marks should be the SUM of all sub-part marks
- The question's main ai_feedback should be brief overview, NOT detailed grading (details go in sub_scores)
- Example: Q32 has parts (a) and (b). If student answered (a) correctly and didn't attempt (b):
  - sub_scores: [{{"sub_id": "a", "obtained_marks": 3, "ai_feedback": "Correct answer"}}, {{"sub_id": "b", "obtained_marks": 0, "ai_feedback": "Not attempted/found"}}]
  - obtained_marks: 3 (sum of sub-scores)
  - ai_feedback: "Part (a) correct. Part (b) not attempted."

### Flag Types (use when needed):
- "BORDERLINE_SCORE": Score is borderline pass/fail
- "ALTERNATIVE_METHOD": Valid but unusual approach used
- "EXCEPTIONAL_ANSWER": Unusually brilliant answer
- "NEEDS_REVIEW": Uncertain grading, needs teacher check
- "ILLEGIBLE_PORTIONS": Some parts hard to read

If a question has no sub-questions, leave sub_scores as an empty array.

## QUALITY ASSURANCE

Before finalizing:
1. ARITHMETIC CHECK: Sum of marks = Total, no question exceeds max
2. CONSISTENCY CHECK: Same answer type = same treatment
3. COMPLETENESS CHECK: All questions evaluated
4. REASONABLENESS CHECK: Marks correlate with answer quality

## FINAL DIRECTIVE

Grade with integrity. Grade with insight. Grade with care.
Your measure of success: When the same paper graded by you and by an expert teacher receives the same marks.
"""

    # Prepare question details with sub-questions
    questions_text = ""
    for q in questions:
        q_text = f"Q{q['question_number']}: Max marks = {q['max_marks']}"
        if q.get('rubric'):
            q_text += f", Rubric: {q['rubric']}"
        
        # Add sub-questions if present
        if q.get('sub_questions'):
            for sq in q['sub_questions']:
                q_text += f"\n  - Part {sq['sub_id']}: Max marks = {sq['max_marks']}"
                if sq.get('rubric'):
                    q_text += f", Rubric: {sq['rubric']}"
        
        questions_text += q_text + "\n"

    # Define helper for grading a chunk of images
    async def process_chunk(chunk_imgs, chunk_idx, total_chunks, start_page_num):
        chunk_chat = LlmChat(
            api_key=api_key,
            session_id=f"grading_{content_hash}_{chunk_idx}",
            system_message=master_system_prompt
        ).with_model("gemini", "gemini-2.5-flash").with_params(temperature=0)

        # Prepare images based on grading mode
        chunk_all_images = []
        
        if use_text_based_grading:
            # TEXT-BASED: Only send student images, model answer is in prompt text
            for img in chunk_imgs:
                chunk_all_images.append(ImageContent(image_base64=img))
            model_images_included = 0
            logger.info(f"Chunk {chunk_idx+1}: TEXT-BASED grading with {len(chunk_imgs)} student images")
        else:
            # IMAGE-BASED: Include model answer images
            if model_answer_images:
                for img in model_answer_images:
                    chunk_all_images.append(ImageContent(image_base64=img))
            for img in chunk_imgs:
                chunk_all_images.append(ImageContent(image_base64=img))
            model_images_included = len(model_answer_images) if model_answer_images else 0
            logger.info(f"Chunk {chunk_idx+1}: IMAGE-BASED grading with {model_images_included} model + {len(chunk_imgs)} student images")
        
        # Build prompt
        partial_instruction = ""
        if total_chunks > 1:
            partial_instruction = f"""
**PARTIAL SUBMISSION NOTICE**:
This is PART {chunk_idx+1} of {total_chunks} of the student's answer (Pages {start_page_num+1} to {start_page_num+len(chunk_imgs)}).

**INSTRUCTIONS FOR PARTIAL GRADING**:
- Grade ONLY the questions visible in this part.
- If a question (or sub-question) is completely missing from these pages, return -1.0 for 'obtained_marks' to indicate 'Not Seen'.
- If a question is present but incorrect/blank, return 0.0 or appropriate marks.
- Do NOT guess marks for questions you cannot see.
"""

        # TEXT-BASED GRADING PROMPT (preferred - faster, more reliable)
        if use_text_based_grading:
            prompt_text = f"""# GRADING TASK {f'(Part {chunk_idx+1}/{total_chunks})' if total_chunks > 1 else ''}

## MODEL ANSWER REFERENCE (Pre-Extracted Text)
Below is the complete model answer content. Use this as your grading reference:

--- MODEL ANSWER START ---
{model_answer_text}
--- MODEL ANSWER END ---

## STUDENT PAPER EVALUATION

**Questions to Grade:**
{questions_text}

**Images Provided:** {len(chunk_imgs)} pages of STUDENT'S ANSWER PAPER (Pages {start_page_num+1}-{start_page_num+len(chunk_imgs)})
{partial_instruction}

**IMPORTANT**: 
- The images may contain rotated or sideways text - read carefully in all orientations
- Examine EVERY page and grade ALL questions found
- Compare student answers against the model answer text above

## GRADING MODE: {grading_mode.upper()}

{grading_instruction}

## CRITICAL REQUIREMENTS:
1. **CONSISTENCY IS SACRED**: Same answer = Same score ALWAYS
2. **MODEL ANSWER IS REFERENCE**: Compare against the model answer text provided above
3. **PRECISE SCORING**: Use decimals (e.g., 8.5, 7.25) not ranges
4. **CARRY-FORWARD**: Credit correct logic even on wrong base values
5. **PARTIAL CREDIT**: Apply according to {grading_mode} mode rules
6. **FEEDBACK QUALITY**: Provide constructive, specific feedback
7. **COMPLETE EVALUATION**: Grade ALL {len(questions)} questions - check EVERY page
8. **HANDLE ROTATION**: If text appears sideways, still read and grade it
9. **SUB-QUESTION GRADING (CRITICAL)**: 
   - If a question has sub-parts (a, b, c, i, ii, iii, etc.), you MUST grade EACH sub-part INDIVIDUALLY
   - Provide separate obtained_marks and ai_feedback for each sub-part in the sub_scores array
   - Do NOT give overall feedback for questions with sub-parts - grade each part separately
   - If a sub-part is not attempted, mark it as 0 with feedback "Not attempted/found"

## OUTPUT
Grade each question providing:
- Exact marks with breakdown
- What was done well
- What needs improvement

Return valid JSON only."""

        elif model_answer_images:
            prompt_text = f"""# GRADING TASK {f'(Part {chunk_idx+1}/{total_chunks})' if total_chunks > 1 else ''}

## PHASE 1: PRE-GRADING ANALYSIS
First, analyze the MODEL ANSWER thoroughly:
- Identify key points for each question
- Extract marking scheme from the model
- Note expected keywords and structure
- Understand acceptable variations

## PHASE 2: STUDENT PAPER EVALUATION

**Questions to Grade:**
{questions_text}

**Image Layout:**
- First {model_images_included} image(s): MODEL ANSWER (your holy grail reference)
- Next {len(chunk_imgs)} images: STUDENT'S ANSWER PAPER (evaluate ALL pages carefully)
{partial_instruction}

**IMPORTANT**: The student paper part has {len(chunk_imgs)} pages. You MUST examine EVERY page and grade ALL questions found. Do not skip any page.

## GRADING MODE: {grading_mode.upper()}

{grading_instruction}

## CRITICAL REQUIREMENTS:
1. **CONSISTENCY IS SACRED**: Same answer = Same score ALWAYS
2. **MODEL ANSWER IS REFERENCE**: Compare against the model answer
3. **PRECISE SCORING**: Use decimals (e.g., 8.5, 7.25) not ranges
4. **CARRY-FORWARD**: Credit correct logic even on wrong base values
5. **PARTIAL CREDIT**: Apply according to {grading_mode} mode rules
6. **FEEDBACK QUALITY**: Provide constructive, specific feedback that helps learning
7. **COMPLETE EVALUATION**: Grade ALL {len(questions)} questions - check EVERY page
8. **SUB-QUESTION GRADING (CRITICAL)**: 
   - If a question has sub-parts (a, b, c, i, ii, iii, etc.), you MUST grade EACH sub-part INDIVIDUALLY
   - Provide separate obtained_marks and ai_feedback for each sub-part in the sub_scores array
   - Do NOT give overall feedback for questions with sub-parts - grade each part separately
   - If a sub-part is not attempted, mark it as 0 with feedback "Not attempted/found"

## PHASE 3: OUTPUT
Grade each question providing:
- Exact marks with breakdown
- What was done well
- What needs improvement
- Error annotations if applicable

Return valid JSON only."""
        else:
            prompt_text = f"""# GRADING TASK (WITHOUT MODEL ANSWER) {f'(Part {chunk_idx+1}/{total_chunks})' if total_chunks > 1 else ''}

## IMPORTANT: No Model Answer Provided
You must grade based on:
- Question rubrics provided
- Your subject knowledge
- Standard academic expectations
- Be more conservative - flag uncertain gradings

**Questions to Grade:**
{questions_text}

**Images:** STUDENT'S ANSWER PAPER (Pages {start_page_num+1}-{start_page_num+len(chunk_imgs)})
{partial_instruction}

## GRADING MODE: {grading_mode.upper()}

{grading_instruction}

## CRITICAL REQUIREMENTS:
1. **CONSISTENCY IS SACRED**: Same answer = Same score ALWAYS
2. **RUBRIC-BASED**: Use provided rubrics as primary reference
3. **PRECISE SCORING**: Use decimals (e.g., 8.5, 7.25) not ranges
4. **CONSERVATIVE FLAGGING**: Flag uncertain gradings for teacher review
5. **PARTIAL CREDIT**: Apply according to {grading_mode} mode rules
6. **SUBJECT KNOWLEDGE**: Use your expertise to assess correctness
7. **CONSTRUCTIVE FEEDBACK**: Help the student understand and improve
8. **SUB-QUESTION GRADING (CRITICAL)**: 
   - If a question has sub-parts (a, b, c, i, ii, iii, etc.), you MUST grade EACH sub-part INDIVIDUALLY
   - Provide separate obtained_marks and ai_feedback for each sub-part in the sub_scores array
   - Do NOT give overall feedback for questions with sub-parts - grade each part separately
   - If a sub-part is not attempted, mark it as 0 with feedback "Not attempted/found"

Return valid JSON only."""

        user_msg = UserMessage(text=prompt_text, file_contents=chunk_all_images)

        # Retry logic with exponential backoff
        import asyncio
        max_retries = 3  # Reduced from 5 to 3 for faster failure
        base_retry_delay = 5  # Reduced base delay
        
        for attempt in range(max_retries):
            try:
                # Exponential backoff: 5s, 10s, 20s
                if attempt > 0:
                    wait_time = base_retry_delay * (2 ** attempt)
                    logger.info(f"Waiting {wait_time}s before retry {attempt+1}")
                    await asyncio.sleep(wait_time)
                
                logger.info(f"AI grading chunk {chunk_idx+1}/{total_chunks} attempt {attempt+1}")
                
                # Add timeout to prevent indefinite hanging (60 seconds)
                try:
                    ai_resp = await asyncio.wait_for(
                        chunk_chat.send_message(user_msg),
                        timeout=60.0  # 60 second timeout per attempt
                    )
                except asyncio.TimeoutError:
                    logger.error(f"Timeout after 60s grading chunk {chunk_idx+1}/{total_chunks} attempt {attempt+1}")
                    if attempt < max_retries - 1:
                        continue  # Retry
                    else:
                        logger.error(f"Failed chunk {chunk_idx+1} after {max_retries} timeout attempts")
                        return []  # Skip this chunk

                # Robust JSON parsing with multiple strategies
                import json
                import re
                resp_text = ai_resp.strip()
                
                # Strategy 1: Direct parse
                try:
                    res = json.loads(resp_text)
                    return res.get("scores", [])
                except json.JSONDecodeError:
                    pass
                
                # Strategy 2: Remove code blocks
                if resp_text.startswith("```"):
                    resp_text = resp_text.split("```")[1]
                    if resp_text.startswith("json"):
                        resp_text = resp_text[4:]
                    resp_text = resp_text.strip()
                    try:
                        res = json.loads(resp_text)
                        return res.get("scores", [])
                    except json.JSONDecodeError:
                        pass
                
                # Strategy 3: Find JSON in response
                json_match = re.search(r'\{[^{}]*"scores"[^{}]*\[[^\]]*\][^{}]*\}', resp_text, re.DOTALL)
                if json_match:
                    try:
                        res = json.loads(json_match.group())
                        return res.get("scores", [])
                    except json.JSONDecodeError:
                        pass
                
                # All strategies failed
                logger.warning(f"Failed to parse grading JSON (attempt {attempt + 1}). Response preview: {resp_text[:200]}")
                if attempt < max_retries - 1:
                    continue  # Will retry with exponential backoff at loop start
                else:
                    logger.error(f"All JSON parsing strategies failed for chunk {chunk_idx+1}")
                    return []

            except Exception as e:
                error_msg = str(e).lower()
                
                # Check for API errors (502, 503, timeouts)
                if "502" in str(e) or "503" in str(e) or "badgateway" in error_msg or "timeout" in error_msg:
                    logger.warning(f"API error on chunk {chunk_idx+1}: {e}")
                    if attempt < max_retries - 1:
                        continue  # Will retry with exponential backoff at loop start
                    else:
                        logger.error(f"Failed to grade chunk {chunk_idx+1} after {max_retries} retries due to API errors.")
                        return []
                
                # Check for rate limiting
                if "429" in str(e) or "rate limit" in error_msg or "quota" in error_msg:
                    wait_time = 60 * (attempt + 1)  # 60s, 120s, 180s for rate limits
                    logger.warning(f"Rate limit hit on chunk {chunk_idx+1}. Waiting {wait_time}s before retry...")
                    await asyncio.sleep(wait_time)
                    if attempt < max_retries - 1:
                        continue
                    else:
                        raise HTTPException(
                            status_code=429,
                            detail="API rate limit exceeded. Please try again in a few minutes or upgrade your plan."
                        )
                
                logger.error(f"Error grading chunk {chunk_idx+1}: {e}")
                if attempt < max_retries - 1:
                    continue  # Will retry with exponential backoff at loop start
                else:
                    # Return empty on final failure - aggregation will handle as "not found"
                    logger.error(f"Failed to grade chunk {chunk_idx+1} after retries. Using fallback.")
                    return []

        return []

    # CHUNKED PROCESSING LOGIC - REDUCED chunk size for better timeout handling
    CHUNK_SIZE = 8  # Process 8 pages at a time for stability
    OVERLAP = 0
    total_student_pages = len(images)
    
    # Create chunks
    chunks = []
    if total_student_pages <= 10:  # Process up to 10 pages in one go
        chunks.append((0, images))
    else:
        for i in range(0, total_student_pages, CHUNK_SIZE):
            chunk = images[i : i + CHUNK_SIZE]
            if chunk:
                chunks.append((i, chunk))
            if i + CHUNK_SIZE >= total_student_pages:
                break
    
    logger.info(f"Processing student paper in {len(chunks)} chunk(s)")
    
    # Add detailed logging for debugging
    logger.info(f"Questions to grade: {[q['question_number'] for q in questions]}")
    logger.info(f"Total marks possible: {sum(q['max_marks'] for q in questions)}")

    # Store aggregated results
    # Use deterministic aggregation: Use the FIRST valid score (>=0) encountered
    # This prevents aggregation jitter when multiple chunks see the same question

    question_scores = {} # q_id -> Score data

    all_chunk_results = []
    for idx, (start_idx, chunk_imgs) in enumerate(chunks):
        chunk_scores_data = await process_chunk(chunk_imgs, idx, len(chunks), start_idx)
        logger.info(f"Chunk {idx+1}/{len(chunks)} returned {len(chunk_scores_data)} scores")
        all_chunk_results.append(chunk_scores_data)
    
    logger.info(f"Total chunk results collected: {len(all_chunk_results)} chunks with {sum(len(cr) for cr in all_chunk_results)} total score entries")

    # Deterministic Aggregation - Use HIGHEST valid score from any chunk
    # CRITICAL FIX: Previous logic used FIRST score, which failed when question appeared in later chunks
    final_scores = []

    for q in questions:
        q_num = q["question_number"]
        best_score_data = None
        best_score_value = -1.0

        # Look for HIGHEST valid score across ALL chunks (not just first)
        for chunk_result in all_chunk_results:
            score_data = next((s for s in chunk_result if s["question_number"] == q_num), None)
            
            if score_data:
                obtained = score_data.get("obtained_marks", -1.0)
                
                # Take this score if:
                # 1. We have no valid score yet (best_score_value < 0) AND this one is valid (>= 0)
                # 2. OR this score is HIGHER than our current best
                if (best_score_value < 0 and obtained >= 0) or (obtained > best_score_value):
                    best_score_data = score_data
                    best_score_value = obtained

        # If no valid score found in any chunk, mark as not found
        if not best_score_data or best_score_value < 0:
             best_score_data = {
                 "question_number": q_num,
                 "obtained_marks": -1.0,
                 "ai_feedback": "Question not found in any page (or grading failed)",
                 "sub_scores": []
             }

        # Process status and normalize
        status = "graded"
        if best_score_data.get("obtained_marks", -1.0) < 0:
            status = "not_found"
            best_score_data["obtained_marks"] = 0.0
        elif best_score_data.get("obtained_marks") == 0 and "blank" in best_score_data.get("ai_feedback", "").lower():
            status = "not_attempted"
            
        # Handle sub-scores - Use HIGHEST valid score from any chunk
        final_sub_scores = []
        if q.get("sub_questions"):
            current_subs = best_score_data.get("sub_scores", [])
            current_sub_map = {s["sub_id"]: s for s in current_subs}

            for sq in q["sub_questions"]:
                sq_id = sq["sub_id"]
                best_sq_data = current_sub_map.get(sq_id)
                best_sq_marks = best_sq_data.get("obtained_marks", -1.0) if best_sq_data else -1.0
                
                # Look across ALL chunks for this sub-question's highest score
                for chunk_result in all_chunk_results:
                    q_score_in_chunk = next((s for s in chunk_result if s["question_number"] == q_num), None)
                    if q_score_in_chunk:
                        chunk_subs = q_score_in_chunk.get("sub_scores", [])
                        sq_in_chunk = next((s for s in chunk_subs if s["sub_id"] == sq_id), None)
                        if sq_in_chunk:
                            sq_marks_in_chunk = sq_in_chunk.get("obtained_marks", -1.0)
                            if sq_marks_in_chunk > best_sq_marks:
                                best_sq_data = sq_in_chunk
                                best_sq_marks = sq_marks_in_chunk
                
                if best_sq_data and best_sq_marks >= 0:
                    # Extract annotations for sub-question
                    sq_annotations = best_sq_data.get("annotations", [])
                    annotations_list = [AnnotationData(**ann) for ann in sq_annotations] if sq_annotations else []

                    final_sub_scores.append(SubQuestionScore(
                        sub_id=sq["sub_id"],
                        max_marks=sq["max_marks"],
                        obtained_marks=min(best_sq_marks, sq["max_marks"]),
                        ai_feedback=best_sq_data.get("ai_feedback", ""),
                        annotations=annotations_list
                    ))
                else:
                    final_sub_scores.append(SubQuestionScore(
                        sub_id=sq["sub_id"],
                        max_marks=sq["max_marks"],
                        obtained_marks=0.0,
                        ai_feedback="Not attempted/found"
                    ))
        
        # CRITICAL FIX: For questions with sub-questions, obtained_marks = sum of sub-scores
        # This prevents double-counting when AI returns both question-level AND sub-question scores
        if final_sub_scores:
            # Calculate from sub-scores to avoid double-counting
            total_sub_marks = sum(s.obtained_marks for s in final_sub_scores)
            question_obtained_marks = total_sub_marks
            logger.debug(f"Q{q_num}: Using sum of sub-scores = {total_sub_marks}")
        else:
            # No sub-questions, use the question-level score directly
            question_obtained_marks = best_score_data["obtained_marks"]
        
        # Extract question-level annotations
        q_annotations = best_score_data.get("annotations", [])
        annotations_list = [AnnotationData(**ann) for ann in q_annotations] if q_annotations else []

        qs_obj = QuestionScore(
            question_number=q_num,
            max_marks=q["max_marks"],
            obtained_marks=min(question_obtained_marks, q["max_marks"]),
            ai_feedback=best_score_data["ai_feedback"],
            sub_scores=[s.model_dump() for s in final_sub_scores],
            question_text=q.get("question_text") or q.get("rubric"),
            status=status,
            annotations=annotations_list
        )
        final_scores.append(qs_obj)
    
    # CRITICAL DEBUG: Verify final scores
    logger.info(f"Final aggregation produced {len(final_scores)} question scores")
    logger.info(f"Question numbers in final_scores: {[qs.question_number for qs in final_scores]}")
    q_nums_final = [qs.question_number for qs in final_scores]
    if len(q_nums_final) != len(set(q_nums_final)):
        logger.error(f"CRITICAL BUG: Duplicate questions in final_scores!")
        logger.error(f"Duplicates: {[q for q in q_nums_final if q_nums_final.count(q) > 1]}")
        
        # EMERGENCY FIX: Deduplicate final_scores before returning
        seen_q_nums = set()
        deduplicated_final_scores = []
        for qs in final_scores:
            if qs.question_number not in seen_q_nums:
                seen_q_nums.add(qs.question_number)
                deduplicated_final_scores.append(qs)
        
        logger.info(f"Deduplication: {len(final_scores)} -> {len(deduplicated_final_scores)} scores")
        final_scores = deduplicated_final_scores

    # Store in Cache and DB
    try:
        # Update memory cache
        grading_cache[paper_hash] = final_scores

        # Update DB cache
        results_json = json.dumps([s.model_dump() for s in final_scores])
        await db.grading_results.update_one(
            {"paper_hash": paper_hash},
            {"$set": {
                "paper_hash": paper_hash,
                "results": results_json,
                "created_at": datetime.now(timezone.utc).isoformat()
            }},
            upsert=True
        )
    except Exception as e:
        logger.error(f"Error saving grading cache: {e}")

    return final_scores


async def generate_annotated_images_with_vision_ocr(
    original_images: List[str],
    question_scores: List[QuestionScore],
    use_vision_ocr: bool = False  # DISABLED - set to True to enable Vision OCR annotations
) -> List[str]:
    """
    Generate annotated images using AI-provided page positions.
    
    CURRENTLY DISABLED - Returns original images without annotations.
    Set use_vision_ocr=True to enable annotation generation.
    
    Vision OCR credentials are saved at /app/backend/credentials/gcp-vision-key.json
    """
    # Return original images without annotations (disabled for now)
    logger.info(f"Annotation generation disabled - returning {len(original_images)} original images")
    return original_images


def _generate_margin_annotations(
    page_idx: int,
    page_questions: List[QuestionScore],
    img_height: int
) -> List[Annotation]:
    """Generate simple margin-based annotations when OCR fails"""
    annotations = []
    margin_x = 30
    section_height = img_height // max(1, len(page_questions))
    
    for q_idx, q_score in enumerate(page_questions):
        y_pos = q_idx * section_height + 40
        score_pct = (q_score.obtained_marks / q_score.max_marks * 100) if q_score.max_marks > 0 else 0
        
        annotations.append(Annotation(
            annotation_type=AnnotationType.POINT_NUMBER,
            x=margin_x,
            y=y_pos,
            text=str(q_score.question_number),
            color="black",
            size=22
        ))
        
        score_text = str(int(q_score.obtained_marks)) if q_score.obtained_marks == int(q_score.obtained_marks) else f"{q_score.obtained_marks:.1f}"
        annotations.append(Annotation(
            annotation_type=AnnotationType.SCORE_CIRCLE,
            x=margin_x + 50,
            y=y_pos,
            text=f"{score_text}/{int(q_score.max_marks)}",
            color="green" if score_pct >= 50 else "red",
            size=28
        ))
    
    return annotations


def generate_annotated_images(
    original_images: List[str],
    question_scores: List[QuestionScore]
) -> List[str]:
    """
    Generate annotated images by overlaying grading annotations on original student answer images
    
    Args:
        original_images: List of base64 encoded original student answer images
        question_scores: List of QuestionScore objects with annotation data
        
    Returns:
        List of base64 encoded annotated images
    """
    try:
        logger.info(f"Generating annotated images for {len(original_images)} pages")
        
        # Auto-generate annotations from scores if AI didn't provide them
        # This ensures annotations are always created
        annotations_by_page = {}
        for page_idx in range(len(original_images)):
            annotations_by_page[page_idx] = []
        
        # Auto-generate annotations based on question scores
        current_y = 120  # Starting Y position
        y_spacing = 100  # Space between question annotations
        margin_left = 30
        
        for q_score in question_scores:
            # Determine which page this question likely appears on
            # Simple heuristic: distribute questions evenly across pages
            page_idx = min(
                int((q_score.question_number - 1) / max(1, len(question_scores) / len(original_images))),
                len(original_images) - 1
            )
            
            # Check if AI provided annotations
            has_ai_annotations = len(q_score.annotations) > 0 or any(
                len(sub.annotations) > 0 for sub in q_score.sub_scores
            )
            
            if not has_ai_annotations:
                # Auto-generate basic annotations
                # Add question number circle
                annotations_by_page[page_idx].append(AnnotationData(
                    type=AnnotationType.POINT_NUMBER,
                    x=margin_left,
                    y=current_y,
                    text=str(q_score.question_number),
                    color="black",
                    size=25,
                    page_index=page_idx
                ))
                
                # Add score based on performance
                score_percentage = (q_score.obtained_marks / q_score.max_marks * 100) if q_score.max_marks > 0 else 0
                
                # Add checkmark or flag based on score
                if score_percentage >= 60:
                    # Good score - add checkmark
                    annotations_by_page[page_idx].append(AnnotationData(
                        type=AnnotationType.CHECKMARK,
                        x=margin_left + 60,
                        y=current_y,
                        text="",
                        color="green",
                        size=25,
                        page_index=page_idx
                    ))
                elif score_percentage < 30:
                    # Low score - add flag
                    annotations_by_page[page_idx].append(AnnotationData(
                        type=AnnotationType.FLAG_CIRCLE,
                        x=margin_left + 60,
                        y=current_y + 5,
                        text="R",
                        color="red",
                        size=30,
                        page_index=page_idx
                    ))
                
                # Add score circle
                annotations_by_page[page_idx].append(AnnotationData(
                    type=AnnotationType.SCORE_CIRCLE,
                    x=margin_left + 120,
                    y=current_y + 5,
                    text=str(int(q_score.obtained_marks)) if q_score.obtained_marks == int(q_score.obtained_marks) else f"{q_score.obtained_marks:.1f}",
                    color="green" if score_percentage >= 60 else "red",
                    size=32,
                    page_index=page_idx
                ))
                
                current_y += y_spacing
            else:
                # Use AI-provided annotations
                for ann_data in q_score.annotations:
                    if ann_data.page_index < len(original_images):
                        annotations_by_page[ann_data.page_index].append(ann_data)
                
                # Add sub-question annotations
                for sub_score in q_score.sub_scores:
                    for ann_data in sub_score.annotations:
                        if ann_data.page_index < len(original_images):
                            annotations_by_page[ann_data.page_index].append(ann_data)
        
        # Now apply annotations to each page
        annotated_images = []
        for page_idx, original_image in enumerate(original_images):
            page_annotations = annotations_by_page.get(page_idx, [])
            
            if not page_annotations:
                # No annotations for this page, keep original
                annotated_images.append(original_image)
                continue
            
            # Get image dimensions for coordinate conversion
            try:
                image_data = base64.b64decode(original_image)
                with Image.open(io.BytesIO(image_data)) as img:
                    img_width, img_height = img.size
            except Exception as e:
                logger.warning(f"Could not get image dimensions: {e}, using defaults")
                img_width, img_height = 1000, 1400  # Default A4-ish dimensions
            
            # Convert AnnotationData to Annotation objects with positioning
            positioned_annotations = []
            current_y_pos = 120
            
            for ann_data in page_annotations:
                # Check if AI provided box_2d coordinates (normalized 0-1000)
                if ann_data.box_2d and len(ann_data.box_2d) == 4:
                    # Convert normalized coordinates to pixel coordinates
                    # box_2d format: [ymin, xmin, ymax, xmax]
                    ymin, xmin, ymax, xmax = ann_data.box_2d
                    x_pos = int(xmin / 1000 * img_width)
                    y_pos = int(ymin / 1000 * img_height)
                    # Optionally use center of box instead of top-left
                    # x_pos = int((xmin + xmax) / 2 / 1000 * img_width)
                    # y_pos = int((ymin + ymax) / 2 / 1000 * img_height)
                    logger.debug(f"Converted box_2d {ann_data.box_2d} to pixels ({x_pos}, {y_pos})")
                elif ann_data.x > 0 or ann_data.y > 0:
                    # Use provided pixel coordinates
                    x_pos = ann_data.x if ann_data.x > 0 else 30
                    y_pos = ann_data.y if ann_data.y > 0 else current_y_pos
                else:
                    # Fallback: auto-position in margin
                    x_pos = 30 if ann_data.type in [AnnotationType.CHECKMARK, AnnotationType.POINT_NUMBER] else 90
                    y_pos = current_y_pos
                    current_y_pos += 80
                
                ann = Annotation(
                    annotation_type=ann_data.type,
                    x=x_pos,
                    y=y_pos,
                    text=ann_data.text,
                    color=ann_data.color,
                    size=ann_data.size
                )
                positioned_annotations.append(ann)
            
            # Apply annotations to this page
            annotated_image = apply_annotations_to_image(original_image, positioned_annotations)
            annotated_images.append(annotated_image)
            
            logger.info(f"Page {page_idx + 1}: Applied {len(positioned_annotations)} annotations")
        
        logger.info(f"Successfully generated {len(annotated_images)} annotated images")
        return annotated_images
        
    except Exception as e:
        logger.error(f"Error generating annotated images: {e}", exc_info=True)
        # Return original images on error
        return original_images


@api_router.post("/exams/{exam_id}/upload-model-answer")
async def upload_model_answer(
    exam_id: str,
    file: Optional[UploadFile] = File(None),
    link: Optional[str] = None,
    user: User = Depends(get_current_user)
):
    """Upload model answer (PDF/Word/Image/ZIP) or provide Google Drive link"""
    # Set processing flag immediately
    await db.exams.update_one(
        {"exam_id": exam_id},
        {"$set": {"model_answer_processing": True}}
    )
    
    if user.role != "teacher":
        raise HTTPException(status_code=403, detail="Only teachers can upload model answers")
    
    exam = await db.exams.find_one({"exam_id": exam_id, "teacher_id": user.user_id}, {"_id": 0})
    if not exam:
        raise HTTPException(status_code=404, detail="Exam not found")
    
    # Get file bytes - either from upload or link
    file_bytes = None
    file_type = None
    
    if link:
        # Download from Google Drive link
        file_id = extract_file_id_from_url(link)
        if not file_id:
            raise HTTPException(status_code=400, detail="Invalid Google Drive link")
        
        try:
            file_bytes, mime_type = download_from_google_drive(file_id)
            # Map mime type to file extension
            file_type = mime_type.split('/')[-1] if '/' in mime_type else 'pdf'
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"Failed to download from link: {str(e)}")
    elif file:
        # Read uploaded file
        file_bytes = await file.read()
        # Get file type from extension or content type
        file_ext = os.path.splitext(file.filename)[1].lower().replace('.', '')
        file_type = file_ext or file.content_type
    else:
        raise HTTPException(status_code=400, detail="Either file or link must be provided")
    
    # Check file size - limit to 30MB for safety
    file_size_mb = len(file_bytes) / (1024 * 1024)
    if len(file_bytes) > 30 * 1024 * 1024:
        raise HTTPException(
            status_code=400, 
            detail=f"File too large ({file_size_mb:.1f}MB). Maximum size is 30MB."
        )
    
    # Handle ZIP files - extract and process all files inside
    all_images = []
    if file_type in ['zip', 'application/zip', 'application/x-zip-compressed']:
        try:
            extracted_files = extract_zip_files(file_bytes)
            logger.info(f"Extracted {len(extracted_files)} files from ZIP")
            
            # Process each extracted file
            for filename, extracted_bytes, extracted_type in extracted_files:
                try:
                    file_images = convert_to_images(extracted_bytes, extracted_type)
                    all_images.extend(file_images)
                    logger.info(f"Processed {filename}: {len(file_images)} images")
                except Exception as e:
                    logger.warning(f"Failed to process {filename}: {e}")
                    # Continue with other files
            
            if not all_images:
                raise HTTPException(status_code=400, detail="No valid files found in ZIP")
                
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"Failed to process ZIP file: {str(e)}")
    else:
        # Convert single file to images
        try:
            all_images = convert_to_images(file_bytes, file_type)
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"Failed to process file: {str(e)}")
    
    images = all_images
    
    # Store images in GridFS to avoid MongoDB 16MB document limit
    file_id = str(uuid.uuid4())
    
    # Serialize images and store in GridFS
    images_data = pickle.dumps(images)
    gridfs_id = fs.put(
        images_data,
        filename=f"model_answer_{exam_id}_{file_id}",
        content_type="application/python-pickle",
        exam_id=exam_id,
        file_type="model_answer"
    )
    
    # Store only metadata and GridFS reference in MongoDB
    await db.exam_files.update_one(
        {"exam_id": exam_id, "file_type": "model_answer"},
        {"$set": {
            "exam_id": exam_id,
            "file_type": "model_answer",
            "file_id": file_id,
            "gridfs_id": str(gridfs_id),
            "page_count": len(images),
            "uploaded_at": datetime.now(timezone.utc).isoformat()
        }},
        upsert=True
    )
    
    # Store only reference in exam document (not the actual images to avoid size limit)
    await db.exams.update_one(
        {"exam_id": exam_id},
        {"$set": {
            "model_answer_file_id": file_id,
            "model_answer_pages": len(images),
            "has_model_answer": True
        }}
    )
    
    # Extract model answer content as text for efficient grading
    logger.info(f"Extracting model answer content as text for exam {exam_id}")
    model_answer_text = await extract_model_answer_content(
        model_answer_images=images,
        questions=exam.get("questions", [])
    )
    
    # Store the extracted text in the exam_files collection
    if model_answer_text:
        await db.exam_files.update_one(
            {"exam_id": exam_id, "file_type": "model_answer"},
            {"$set": {
                "model_answer_text": model_answer_text,
                "text_extracted_at": datetime.now(timezone.utc).isoformat()
            }}
        )
        logger.info(f"Stored model answer text ({len(model_answer_text)} chars) for exam {exam_id}")
    
    # Check if question paper exists
    qp_imgs = await get_exam_question_paper_images(exam_id)
    
    # Logic:
    # 1. If Question Paper exists -> Skip extraction (already done, unless we force, but user said skip)
    # 2. If NO Question Paper -> Force extraction from Model Answer
    
    force_extraction = False
    if not qp_imgs:
        force_extraction = True
        
    result = await auto_extract_questions(exam_id, force=force_extraction)
    
    # RE-EXTRACT model answer text if questions were just populated
    if result.get("success") and result.get("count", 0) > 0:
        logger.info(f"Questions populated ({result.get('count')}). Re-extracting model answer text with question context...")
        
        # Fetch updated exam with questions
        exam_updated = await db.exams.find_one({"exam_id": exam_id}, {"_id": 0})
        
        # Re-extract with proper question context
        model_answer_text_updated = await extract_model_answer_content(
            model_answer_images=images,
            questions=exam_updated.get("questions", [])
        )
        
        # Update with better extraction
        if model_answer_text_updated and len(model_answer_text_updated) > len(model_answer_text):
            await db.exam_files.update_one(
                {"exam_id": exam_id, "file_type": "model_answer"},
                {"$set": {
                    "model_answer_text": model_answer_text_updated,
                    "text_extracted_at": datetime.now(timezone.utc).isoformat()
                }}
            )
            logger.info(f"Updated model answer text ({len(model_answer_text_updated)} chars) with question context")
            # Use the updated text for response
            model_answer_text = model_answer_text_updated

    # Determine extraction success
    text_chars = len(model_answer_text) if model_answer_text else 0
    extraction_success = text_chars > 500  # Consider successful if >500 chars
    
    message = "Model answer uploaded successfully."
    if result.get("success"):
        if result.get("skipped"):
            message = f"✨ Model answer uploaded! Questions kept from {result.get('source').replace('_', ' ')}."
        else:
            message = f"✨ Model answer uploaded & {result.get('count')} questions auto-extracted from {result.get('source').replace('_', ' ')}!"
    
    # Add extraction status to message
    if extraction_success:
        message += f" ✅ Model answer content extracted successfully ({text_chars} characters)."
    elif text_chars > 0:
        message += f" ⚠️ Model answer extraction returned minimal content ({text_chars} characters). Grading may use image-based mode."
    else:
        message += " ❌ Model answer content extraction failed. Grading will use image-based mode (slower, less accurate)."

    return {
        "message": message,
        "pages": len(images),
        "auto_extracted": result.get("success", False),
        "extracted_count": result.get("count", 0),
        "source": result.get("source", ""),
        "text_extraction": {
            "success": extraction_success,
            "characters": text_chars,
            "status": "success" if extraction_success else ("partial" if text_chars > 0 else "failed")
        }
    }

@api_router.post("/exams/{exam_id}/upload-question-paper")
async def upload_question_paper(
    exam_id: str,
    file: UploadFile = File(...),
    user: User = Depends(get_current_user)
):
    """Upload question paper (PDF/Word/Image/ZIP) and AUTO-EXTRACT questions"""
    if user.role != "teacher":
        raise HTTPException(status_code=403, detail="Only teachers can upload question papers")
    
    exam = await db.exams.find_one({"exam_id": exam_id, "teacher_id": user.user_id}, {"_id": 0})
    if not exam:
        raise HTTPException(status_code=404, detail="Exam not found")
    
    # Read file and get file type
    file_bytes = await file.read()
    file_ext = os.path.splitext(file.filename)[1].lower().replace('.', '')
    file_type = file_ext or file.content_type
    
    # Check file size - limit to 30MB for safety
    file_size_mb = len(file_bytes) / (1024 * 1024)
    if len(file_bytes) > 30 * 1024 * 1024:
        raise HTTPException(
            status_code=400, 
            detail=f"File too large ({file_size_mb:.1f}MB). Maximum size is 30MB. Try compressing the file or reducing quality."
        )
    
    # Handle ZIP files - extract and process all files inside
    all_images = []
    if file_type in ['zip', 'application/zip', 'application/x-zip-compressed']:
        try:
            extracted_files = extract_zip_files(file_bytes)
            logger.info(f"Extracted {len(extracted_files)} files from ZIP")
            
            # Process each extracted file
            for filename, extracted_bytes, extracted_type in extracted_files:
                try:
                    file_images = convert_to_images(extracted_bytes, extracted_type)
                    all_images.extend(file_images)
                    logger.info(f"Processed {filename}: {len(file_images)} images")
                except Exception as e:
                    logger.warning(f"Failed to process {filename}: {e}")
                    # Continue with other files
            
            if not all_images:
                raise HTTPException(status_code=400, detail="No valid files found in ZIP")
                
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"Failed to process ZIP file: {str(e)}")
    else:
        # Convert single file to images
        try:
            all_images = convert_to_images(file_bytes, file_type)
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"Failed to process file: {str(e)}")
    
    images = all_images
    
    # Store images in GridFS to avoid MongoDB 16MB document limit
    file_id = str(uuid.uuid4())
    
    # Serialize images and store in GridFS
    images_data = pickle.dumps(images)
    gridfs_id = fs.put(
        images_data,
        filename=f"question_paper_{exam_id}_{file_id}",
        content_type="application/python-pickle",
        exam_id=exam_id,
        file_type="question_paper"
    )
    
    # Store only metadata and GridFS reference in MongoDB
    await db.exam_files.update_one(
        {"exam_id": exam_id, "file_type": "question_paper"},
        {"$set": {
            "exam_id": exam_id,
            "file_type": "question_paper",
            "file_id": file_id,
            "gridfs_id": str(gridfs_id),
            "page_count": len(images),
            "uploaded_at": datetime.now(timezone.utc).isoformat()
        }},
        upsert=True
    )
    
    # Store only reference in exam document
    await db.exams.update_one(
        {"exam_id": exam_id},
        {"$set": {
            "question_paper_file_id": file_id,
            "question_paper_pages": len(images),
            "has_question_paper": True
        }}
    )
    
    # AUTO-EXTRACT questions from question paper (Priority: Always extract when uploading QP)
    logger.info(f"Auto-extracting questions from question paper for exam {exam_id}")

    result = await auto_extract_questions(exam_id, force=True)

    message = "Question paper uploaded successfully."
    if result.get("success"):
        message = f"✨ Question paper uploaded & {result.get('count')} questions auto-extracted!"
    else:
        message = "Question paper uploaded, but auto-extraction failed."
        
    return {
        "message": message,
        "pages": len(images),
        "auto_extracted": result.get("success", False),
        "extracted_count": result.get("count", 0)
    }

@api_router.post("/exams/{exam_id}/upload-papers")
async def upload_student_papers(
    exam_id: str,
    files: List[UploadFile] = File(...),
    user: User = Depends(get_current_user)
):
    """Upload and grade student papers with background job processing - supports 30+ papers"""
    if user.role != "teacher":
        raise HTTPException(status_code=403, detail="Only teachers can upload papers")
    
    exam = await db.exams.find_one({"exam_id": exam_id, "teacher_id": user.user_id}, {"_id": 0})
    if not exam:
        raise HTTPException(status_code=404, detail="Exam not found")
    
    # Create a grading job
    job_id = f"job_{uuid.uuid4().hex[:12]}"
    
    # Read all files into memory first (before returning response)
    files_data = []
    for file in files:
        file_bytes = await file.read()
        files_data.append({
            "filename": filename,
            "content": file_bytes
        })
    
    # Create job record
    job_record = {
        "job_id": job_id,
        "exam_id": exam_id,
        "teacher_id": user.user_id,
        "status": "pending",  # pending, processing, completed, failed
        "total_papers": len(files_data),
        "processed_papers": 0,
        "successful": 0,
        "failed": 0,
        "submissions": [],
        "errors": [],
        "created_at": datetime.now(timezone.utc).isoformat(),
        "updated_at": datetime.now(timezone.utc).isoformat()
    }
    
    await db.grading_jobs.insert_one(job_record)
    
    # Update exam status
    await db.exams.update_one(
        {"exam_id": exam_id},
        {"$set": {"status": "processing"}}
    )
    
    # Start background processing
    asyncio.create_task(process_grading_job_in_background(job_id, exam_id, files_data, exam, user.user_id))
    
    # Return immediately with job_id
    return {
        "job_id": job_id,
        "status": "pending",
        "total_papers": len(files_data),
        "message": f"Grading job started for {len(files_data)} papers. Use job_id to check progress."
    }


async def process_grading_job_in_background(job_id: str, exam_id: str, files_data: List[dict], exam: dict, teacher_id: str):
    """Background task to process papers one by one"""
    try:
        # Update job status to processing
        await db.grading_jobs.update_one(
            {"job_id": job_id},
            {"$set": {
                "status": "processing",
                "updated_at": datetime.now(timezone.utc).isoformat()
            }}
        )
        
        submissions = []
        errors = []
        
        # Log the number of files received
        logger.info(f"=== BATCH GRADING START === Processing {len(files_data)} files for exam {exam_id} (Job: {job_id})")
        
        for idx, file_data in enumerate(files_data):
            file_start_time = datetime.now(timezone.utc)
            filename = file_data["filename"]
            pdf_bytes = file_data["content"]
            
            logger.info(f"[File {idx + 1}/{len(files_data)}] START processing: {filename}")
            try:
                # Check file size - limit to 30MB for safety
                file_size_mb = len(pdf_bytes) / (1024 * 1024)
                if len(pdf_bytes) > 30 * 1024 * 1024:
                    logger.warning(f"[File {idx + 1}/{len(files_data)}] File too large: {file_size_mb:.1f}MB")
                    errors.append({
                        "filename": filename,
                        "error": f"File too large ({file_size_mb:.1f}MB). Maximum size is 30MB."
                    })
                    # Update job progress
                    await db.grading_jobs.update_one(
                        {"job_id": job_id},
                        {
                            "$set": {
                                "processed_papers": idx + 1,
                                "failed": len(errors),
                                "errors": errors,
                                "updated_at": datetime.now(timezone.utc).isoformat()
                            }
                        }
                    )
                    continue
                
                images = pdf_to_images(pdf_bytes)
                logger.info(f"[File {idx + 1}/{len(files_data)}] Extracted {len(images) if images else 0} images from PDF")
                
                if not images:
                    logger.error(f"[File {idx + 1}/{len(files_data)}] Failed to extract images")
                    errors.append({
                        "filename": filename,
                        "error": "Failed to extract images from PDF"
                    })
                    # Update job progress
                    await db.grading_jobs.update_one(
                        {"job_id": job_id},
                        {
                            "$set": {
                                "processed_papers": idx + 1,
                                "failed": len(errors),
                                "errors": errors,
                                "updated_at": datetime.now(timezone.utc).isoformat()
                            }
                        }
                    )
                    continue
                
                # Extract student ID and name from the paper using AI
                student_id, student_name = await extract_student_info_from_paper(images, filename)
                
                # Fallback to filename if AI extraction fails
                if not student_id or not student_name:
                    filename_id, filename_name = parse_student_from_filename(filename)
                    
                    # Use filename ID if AI didn't find it
                    if not student_id and filename_id:
                        student_id = filename_id
                    
                    # Use filename name if AI didn't find it
                    if not student_name and filename_name:
                        student_name = filename_name
                
                # If still no ID or name, report error
                if not student_id and not student_name:
                    errors.append({
                        "filename": filename,
                        "error": "Could not extract student ID/name from paper or filename. Please ensure student writes their roll number and name clearly on the answer sheet."
                    })
                    continue
                
                # If we have one but not the other, use what we have
                if not student_id:
                    student_id = f"AUTO_{uuid.uuid4().hex[:6]}"
                if not student_name:
                    student_name = f"Student {student_id}"
            
                # Get or create student (FIXED: moved outside the if block)
                user_id, error = await get_or_create_student(
                    student_id=student_id,
                    student_name=student_name,
                    batch_id=exam["batch_id"],
                    teacher_id=user.user_id
                )
                
                if error:
                    errors.append({
                        "filename": filename,
                        "student_id": student_id,
                        "error": error
                    })
                    continue
                
                # Grade with AI using the grading mode from exam
                # Get model answer images from separate collection
                model_answer_imgs = await get_exam_model_answer_images(exam_id)
                
                # Get questions from the separate questions collection (where auto-extraction saves them)
                # Fallback to exam.questions if questions collection is empty (for backward compatibility)
                questions_from_collection = await db.questions.find(
                    {"exam_id": exam_id},
                    {"_id": 0}
                ).to_list(1000)
                
                if questions_from_collection:
                    questions_to_grade = questions_from_collection
                    logger.info(f"Using {len(questions_to_grade)} questions from questions collection")
                else:
                    questions_to_grade = exam.get("questions", [])
                    logger.info(f"Using {len(questions_to_grade)} questions from exam document (fallback)")
                
                if not questions_to_grade:
                    errors.append({
                        "student": student_name,
                        "error": "No questions found for this exam. Please ensure questions are extracted or manually added."
                    })
                    continue
                
                # Get pre-extracted model answer text for efficient grading
                model_answer_text = await get_exam_model_answer_text(exam_id)
                
                scores = await grade_with_ai(
                    images=images,
                    model_answer_images=model_answer_imgs,
                    questions=questions_to_grade,
                    grading_mode=exam.get("grading_mode", "balanced"),
                    total_marks=exam.get("total_marks", 100),
                    model_answer_text=model_answer_text
                )
                
                # Generate annotated images with grading marks using Vision OCR
                logger.info(f"Generating annotated images for {student_name} using Vision OCR")
                try:
                    annotated_images = await generate_annotated_images_with_vision_ocr(images, scores, use_vision_ocr=True)
                except Exception as ann_error:
                    logger.warning(f"Vision OCR annotation failed, falling back to basic: {ann_error}")
                    annotated_images = generate_annotated_images(images, scores)
                
                total_score = sum(s.obtained_marks for s in scores)
                percentage = (total_score / exam["total_marks"]) * 100 if exam["total_marks"] > 0 else 0
                
                submission_id = f"sub_{uuid.uuid4().hex[:8]}"
                submission = {
                    "submission_id": submission_id,
                    "exam_id": exam_id,
                    "student_id": user_id,
                    "student_name": student_name,
                    "file_data": base64.b64encode(pdf_bytes).decode(),
                    "file_images": images,  # Original images
                    "annotated_images": annotated_images,  # NEW: Annotated images with grading marks
                    "total_score": total_score,
                    "percentage": round(percentage, 2),
                    "question_scores": [s.model_dump() for s in scores],
                    "status": "ai_graded",
                    "graded_at": datetime.now(timezone.utc).isoformat(),
                    "created_at": datetime.now(timezone.utc).isoformat()
                }
                
                await db.submissions.insert_one(submission)
                submissions.append({
                    "submission_id": submission_id,
                    "student_id": student_id,
                    "student_name": student_name,
                    "total_score": total_score,
                    "percentage": percentage
                })
            
            except Exception as e:
                logger.error(f"Error processing {filename}: {e}")
                errors.append({
                    "filename": filename,
                    "error": str(e)
                })
        
        # Log final summary
        logger.info(f"Batch grading complete (Job {job_id}): {len(submissions)} successful, {len(errors)} errors out of {len(files_data)} total files")
        
        # Update exam status
        await db.exams.update_one(
            {"exam_id": exam_id},
            {"$set": {"status": "completed"}}
        )
        
        # Mark job as completed
        await db.grading_jobs.update_one(
            {"job_id": job_id},
            {"$set": {
                "status": "completed",
                "processed_papers": len(files_data),
                "successful": len(submissions),
                "failed": len(errors),
                "submissions": submissions,
                "errors": errors,
                "updated_at": datetime.now(timezone.utc).isoformat(),
                "completed_at": datetime.now(timezone.utc).isoformat()
            }}
        )
        
        # Create notification for teacher
        await create_notification(
            user_id=teacher_id,
            notification_type="grading_complete",
            title="Grading Complete",
            message=f"Successfully graded {len(submissions)} papers for {exam['exam_name']}",
            link=f"/teacher/review?exam={exam_id}"
        )

    except Exception as e:
        logger.error(f"Critical error in background job {job_id}: {e}")
        await db.grading_jobs.update_one(
            {"job_id": job_id},
            {"$set": {
                "status": "failed",
                "error": str(e),
                "updated_at": datetime.now(timezone.utc).isoformat()
            }}
        )


# ============== BACKGROUND GRADING (30+ Papers Support) ==============

@api_router.post("/exams/{exam_id}/grade-papers-bg")
async def grade_papers_background(
    exam_id: str,
    files: List[UploadFile] = File(...),
    user: User = Depends(get_current_user)
):
    """
    Start background grading job - creates a task in MongoDB for worker to process
    Returns immediately with job_id for progress polling
    Stores large files in GridFS to avoid 16MB BSON limit
    """
    try:
        logger.info(f"=== GRADE PAPERS BG START === User: {user.user_id}, Exam: {exam_id}, Files: {len(files)}")
        
        if user.role != "teacher":
            logger.error(f"Non-teacher attempted to grade: {user.role}")
            raise HTTPException(status_code=403, detail="Only teachers can upload papers")
        
        exam = await db.exams.find_one({"exam_id": exam_id, "teacher_id": user.user_id}, {"_id": 0})
        if not exam:
            logger.error(f"Exam not found: {exam_id}")
            raise HTTPException(status_code=404, detail="Exam not found")
        
        job_id = f"job_{uuid.uuid4().hex[:12]}"
        logger.info(f"Generated job_id: {job_id}")
        
        # Read all files into memory CONCURRENTLY
        logger.info(f"Reading {len(files)} files for job {job_id}...")
        read_tasks = [file.read() for file in files]
        file_contents = await asyncio.gather(*read_tasks)
        logger.info(f"All files read successfully")
        
        # Store files in GridFS to avoid 16MB BSON document limit
        # Only store file IDs in the task document
        logger.info(f"Storing {len(files)} files in GridFS...")
        file_refs = []
        for file, content in zip(files, file_contents):
            file_size_mb = len(content) / (1024 * 1024)
            logger.info(f"  Storing {file.filename}: {file_size_mb:.2f} MB")
            
            # Store in GridFS
            file_id = fs.put(
                content,
                filename=file.filename,
                job_id=job_id,
                content_type="application/pdf"
            )
            
            file_refs.append({
                "filename": file.filename,
                "gridfs_id": str(file_id),  # Store GridFS ID instead of file content
                "size_bytes": len(content)
            })
        
        logger.info(f"All files stored in GridFS. Creating job record and task...")
        
        # Create job record in database
        job_record = {
            "job_id": job_id,
            "exam_id": exam_id,
            "teacher_id": user.user_id,
            "status": "pending",
            "total_papers": len(file_refs),
            "processed_papers": 0,
            "successful": 0,
            "failed": 0,
            "submissions": [],
            "errors": [],
            "created_at": datetime.now(timezone.utc).isoformat(),
            "updated_at": datetime.now(timezone.utc).isoformat()
        }
        
        await db.grading_jobs.insert_one(job_record)
        logger.info(f"Job record created in DB")
        
        # Create task for worker to process
        # Store only file references (GridFS IDs), not the actual file data
        task_id = f"task_{uuid.uuid4().hex[:12]}"
        task_record = {
            "task_id": task_id,
            "type": "grade_papers",
            "status": "pending",
            "data": {
                "job_id": job_id,
                "exam_id": exam_id,
                "file_refs": file_refs,  # Only store GridFS IDs, not file content
                "teacher_id": user.user_id
            },
            "created_at": datetime.now(timezone.utc).isoformat()
        }
        
        await db.tasks.insert_one(task_record)
        logger.info(f"Task {task_id} created for worker. Job will be processed in background.")
        
        await db.exams.update_one({"exam_id": exam_id}, {"$set": {"status": "processing"}})
        
        logger.info(f"=== GRADE PAPERS BG SUCCESS === Job {job_id} queued for {len(file_refs)} papers")
        
        return {
            "job_id": job_id,
            "task_id": task_id,
            "status": "pending",
            "total_papers": len(file_refs),
            "message": f"Grading job queued for {len(file_refs)} papers. Worker will process in background."
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"=== GRADE PAPERS BG ERROR === {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to start grading job: {str(e)}")


@api_router.get("/grading-jobs/{job_id}")
async def get_grading_job_status(job_id: str, user: User = Depends(get_current_user)):
    """Poll grading job status - called every 2 seconds by frontend"""
    job = await db.grading_jobs.find_one({"job_id": job_id}, {"_id": 0})
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    if user.role == "teacher" and job["teacher_id"] != user.user_id:
        raise HTTPException(status_code=403, detail="Access denied")
    return serialize_doc(job)

@api_router.post("/grading-jobs/{job_id}/cancel")
async def cancel_grading_job(job_id: str, user: User = Depends(get_current_user)):
    """Cancel an ongoing grading job"""
    # Find the job
    job = await db.grading_jobs.find_one({"job_id": job_id}, {"_id": 0})
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    
    # Verify ownership
    if user.role == "teacher" and job["teacher_id"] != user.user_id:
        raise HTTPException(status_code=403, detail="Access denied")
    
    # Cancel the job if it's still running
    if job["status"] in ["queued", "processing"]:
        await db.grading_jobs.update_one(
            {"job_id": job_id},
            {"$set": {"status": "cancelled", "error": "Cancelled by user"}}
        )
        logger.info(f"Grading job {job_id} cancelled by user {user.user_id}")
        return {"message": "Job cancelled successfully", "job_id": job_id}
    else:
        return {"message": f"Job already {job['status']}", "job_id": job_id}


# ============== SUBMISSION ROUTES ==============

@api_router.get("/submissions")
async def get_submissions(
    exam_id: Optional[str] = None,
    batch_id: Optional[str] = None,
    status: Optional[str] = None,
    user: User = Depends(get_current_user)
):
    """Get submissions"""
    if user.role == "teacher":
        # Get teacher's exams first - only completed ones
        exam_query = {"teacher_id": user.user_id, "status": "completed"}
        if batch_id:
            exam_query["batch_id"] = batch_id
        if exam_id:
            exam_query["exam_id"] = exam_id
        
        exams = await db.exams.find(exam_query, {"exam_id": 1, "_id": 0}).to_list(100)
        exam_ids = [e["exam_id"] for e in exams]
        
        query = {"exam_id": {"$in": exam_ids}}
        if status:
            query["status"] = status
        
        submissions = await db.submissions.find(
            query,
            {"_id": 0, "file_data": 0, "file_images": 0}
        ).to_list(500)
    else:
        # Students see their own submissions ONLY if results are published
        # First, get exams where results are published
        published_exams = await db.exams.find(
            {"results_published": True},
            {"_id": 0, "exam_id": 1}
        ).to_list(1000)
        
        published_exam_ids = [e["exam_id"] for e in published_exams]
        
        # Get student's submissions for published exams only
        submissions = await db.submissions.find(
            {
                "student_id": user.user_id,
                "exam_id": {"$in": published_exam_ids}
            },
            {"_id": 0, "file_data": 0, "file_images": 0}
        ).to_list(100)
    
    # Enrich with exam details
    for sub in submissions:
        exam = await db.exams.find_one({"exam_id": sub["exam_id"]}, {"_id": 0, "exam_name": 1, "subject_id": 1, "batch_id": 1})
        if exam:
            sub["exam_name"] = exam.get("exam_name", "Unknown")
            subject = await db.subjects.find_one({"subject_id": exam.get("subject_id")}, {"_id": 0, "name": 1})
            sub["subject_name"] = subject.get("name", "Unknown") if subject else "Unknown"
            batch = await db.batches.find_one({"batch_id": exam.get("batch_id")}, {"_id": 0, "name": 1})
            sub["batch_name"] = batch.get("name", "Unknown") if batch else "Unknown"
    
    return serialize_doc(submissions)

@api_router.get("/submissions/{submission_id}")
async def get_submission(
    submission_id: str,
    include_images: bool = True,
    user: User = Depends(get_current_user)
):
    """Get submission details with PDF data and full question text"""
    try:
        projection = {"_id": 0}
        if not include_images:
            projection["file_images"] = 0
            projection["file_data"] = 0  # Also exclude raw file data if not needed

        submission = await db.submissions.find_one(
            {"submission_id": submission_id},
            projection
        )
        
        if not submission:
            raise HTTPException(status_code=404, detail="Submission not found")

        # Get exam to check visibility settings for students
        exam = await db.exams.find_one(
            {"exam_id": submission["exam_id"]},
            {"_id": 0, "questions": 1, "results_published": 1, "student_visibility": 1}
        )
        
        # For students, enforce visibility settings
        if user.role == "student":
            # Check if results are published
            if not exam or not exam.get("results_published"):
                raise HTTPException(status_code=403, detail="Results not yet published")
            
            # Apply visibility settings
            visibility = exam.get("student_visibility", {})
            
            # Remove answer sheet images if not allowed
            if not visibility.get("show_answer_sheet", True):
                submission["file_images"] = []
                submission.pop("file_data", None)
            
            # Feedback is always shown (question_scores contains feedback)
        
        # Enrich with full question text from exam
        if exam and exam.get("questions"):
            # Create a map of question_number to question data
            question_map = {q["question_number"]: q for q in exam["questions"]}
            
            # Enrich each question score with the full question text
            for qs in submission.get("question_scores", []):
                q_num = qs.get("question_number")
                if q_num in question_map:
                    question_data = question_map[q_num]
                    qs["question_text"] = question_data.get("rubric", "")
                    qs["sub_questions"] = question_data.get("sub_questions", [])

        # For students, handle question paper and model answer visibility
        if user.role == "student" and include_images and exam:
            visibility = exam.get("student_visibility", {})
            
            # Add question paper if allowed
            if visibility.get("show_question_paper", True):
                # Get question paper from exam_files
                exam_file = await db.exam_files.find_one(
                    {"exam_id": submission["exam_id"]},
                    {"_id": 0, "question_paper_gridfs_id": 1, "gridfs_id": 1}
                )

                if exam_file:
                    # Try question_paper_gridfs_id first, then fallback to gridfs_id
                    file_id_str = exam_file.get("question_paper_gridfs_id") or exam_file.get("gridfs_id")

                    if file_id_str:
                        try:
                            # Use global fs object which is safe
                            from bson import ObjectId
                            file_oid = ObjectId(file_id_str)
                            if fs.exists(file_oid):
                                grid_out = fs.get(file_oid)
                                import pickle
                                images_list = pickle.loads(grid_out.read())
                                submission["question_paper_images"] = images_list
                        except Exception as e:
                            logger.error(f"Error retrieving question paper for student: {e}")
                            submission["question_paper_images"] = []
            
            # Add model answer if allowed
            if visibility.get("show_model_answer", False):
                exam_file = await db.exam_files.find_one(
                    {"exam_id": submission["exam_id"]},
                    {"_id": 0, "model_answer_gridfs_id": 1}
                )
                
                if exam_file and exam_file.get("model_answer_gridfs_id"):
                    try:
                        from bson import ObjectId
                        file_oid = ObjectId(exam_file["model_answer_gridfs_id"])
                        if fs.exists(file_oid):
                            grid_out = fs.get(file_oid)
                            import pickle
                            images_list = pickle.loads(grid_out.read())
                            submission["model_answer_images"] = images_list
                    except Exception as e:
                        logger.error(f"Error retrieving model answer for student: {e}")
                        submission["model_answer_images"] = []
        
        # For teachers, always include everything
        elif user.role == "teacher" and include_images:
            # Get question paper from exam_files
            exam_file = await db.exam_files.find_one(
                {"exam_id": submission["exam_id"]},
                {"_id": 0, "question_paper_gridfs_id": 1, "gridfs_id": 1}
            )

            if exam_file:
                # Try question_paper_gridfs_id first, then fallback to gridfs_id
                file_id_str = exam_file.get("question_paper_gridfs_id") or exam_file.get("gridfs_id")

                if file_id_str:
                    try:
                        # Use global fs object which is safe
                        from bson import ObjectId
                        file_oid = ObjectId(file_id_str)
                        if fs.exists(file_oid):
                            grid_out = fs.get(file_oid)
                            import pickle
                            images_list = pickle.loads(grid_out.read())
                            submission["question_paper_images"] = images_list
                    except Exception as e:
                        logger.error(f"Error retrieving question paper: {e}")
                        submission["question_paper_images"] = []

        # CRITICAL: Fetch images from separate collection if they exist
        # This prevents MongoDB 16MB document limit errors with large papers
        if include_images and submission.get("has_images"):
            submission_images = await db.submission_images.find_one(
                {"submission_id": submission_id},
                {"_id": 0, "file_images": 1, "annotated_images": 1}
            )
            if submission_images:
                submission["file_images"] = submission_images.get("file_images", [])
                submission["annotated_images"] = submission_images.get("annotated_images", [])
            else:
                # Fallback if images not found
                submission["file_images"] = []
                submission["annotated_images"] = []
        elif not include_images:
            # Ensure these fields don't exist if not requested
            submission.pop("file_images", None)
            submission.pop("annotated_images", None)
        
        return serialize_doc(submission)

    except Exception as e:
        logger.error(f"Error fetching submission {submission_id}: {e}")
        if isinstance(e, HTTPException):
            raise e
        raise HTTPException(status_code=500, detail=str(e))

@api_router.put("/submissions/{submission_id}")
async def update_submission(
    submission_id: str,
    updates: dict,
    user: User = Depends(get_current_user)
):
    """Update submission scores and feedback"""
    if user.role != "teacher":
        raise HTTPException(status_code=403, detail="Only teachers can update submissions")
    
    # Get original submission for comparison
    original_submission = await db.submissions.find_one({"submission_id": submission_id}, {"_id": 0})
    if not original_submission:
        raise HTTPException(status_code=404, detail="Submission not found")
    
    # Calculate new total
    question_scores = updates.get("question_scores", [])
    total_score = sum(qs.get("obtained_marks", 0) for qs in question_scores)
    
    exam = await db.exams.find_one(
        {"exam_id": original_submission["exam_id"]},
        {"_id": 0, "total_marks": 1, "teacher_id": 1}
    )
    total_marks = exam.get("total_marks", 100) if exam else 100
    percentage = (total_score / total_marks) * 100 if total_marks > 0 else 0
    
    # Track edits in grading_analytics
    asyncio.create_task(track_teacher_edits(
        submission_id=submission_id,
        exam_id=original_submission["exam_id"],
        teacher_id=exam.get("teacher_id", user.user_id) if exam else user.user_id,
        original_scores=original_submission.get("question_scores", []),
        new_scores=question_scores
    ))
    
    await db.submissions.update_one(
        {"submission_id": submission_id},
        {"$set": {
            "question_scores": question_scores,
            "total_score": total_score,
            "percentage": round(percentage, 2),
            "status": "teacher_reviewed"
        }}
    )
    
    return {"message": "Submission updated", "total_score": total_score, "percentage": percentage}

async def track_teacher_edits(
    submission_id: str,
    exam_id: str,
    teacher_id: str,
    original_scores: List[dict],
    new_scores: List[dict]
):
    """Track when teachers edit AI-generated grades"""
    try:
        for new_qs in new_scores:
            q_num = new_qs.get("question_number")
            orig_qs = next((q for q in original_scores if q.get("question_number") == q_num), None)
            
            if orig_qs:
                # Check if edited
                grade_changed = orig_qs.get("obtained_marks") != new_qs.get("obtained_marks")
                feedback_changed = orig_qs.get("ai_feedback") != new_qs.get("ai_feedback")
                
                if grade_changed or feedback_changed:
                    grade_delta = new_qs.get("obtained_marks", 0) - orig_qs.get("obtained_marks", 0)
                    edit_dist = calculate_edit_distance(
                        orig_qs.get("ai_feedback", ""),
                        new_qs.get("ai_feedback", "")
                    )
                    
                    # Update or create analytics record
                    await db.grading_analytics.update_one(
                        {
                            "submission_id": submission_id,
                            "question_number": q_num
                        },
                        {
                            "$set": {
                                "final_grade": new_qs.get("obtained_marks", 0),
                                "grade_delta": grade_delta,
                                "final_feedback": new_qs.get("ai_feedback", ""),
                                "edit_distance": edit_dist,
                                "edited_by_teacher": True,
                                "edited_at": datetime.now(timezone.utc).isoformat()
                            }
                        },
                        upsert=True
                    )
    except Exception as e:
        logger.error(f"Failed to track teacher edits: {e}")

# ============== RE-EVALUATION ROUTES ==============

@api_router.get("/re-evaluations")
async def get_re_evaluations(user: User = Depends(get_current_user)):
    """Get re-evaluation requests"""
    if user.role == "teacher":
        # Get all requests for teacher's exams
        exams = await db.exams.find({"teacher_id": user.user_id}, {"exam_id": 1, "_id": 0}).to_list(100)
        exam_ids = [e["exam_id"] for e in exams]
        requests = await db.re_evaluations.find(
            {"exam_id": {"$in": exam_ids}},
            {"_id": 0}
        ).to_list(100)
    else:
        requests = await db.re_evaluations.find(
            {"student_id": user.user_id},
            {"_id": 0}
        ).to_list(50)
    
    # Enrich with exam details
    for req in requests:
        exam = await db.exams.find_one({"exam_id": req["exam_id"]}, {"_id": 0, "exam_name": 1})
        req["exam_name"] = exam.get("exam_name", "Unknown") if exam else "Unknown"
    
    return requests

@api_router.post("/re-evaluations")
async def create_re_evaluation(
    request: ReEvaluationCreate,
    user: User = Depends(get_current_user)
):
    """Create re-evaluation request"""
    submission = await db.submissions.find_one({"submission_id": request.submission_id}, {"_id": 0})
    if not submission:
        raise HTTPException(status_code=404, detail="Submission not found")
    
    # Get exam and teacher info
    exam = await db.exams.find_one({"exam_id": submission["exam_id"]}, {"_id": 0})
    
    request_id = f"reeval_{uuid.uuid4().hex[:8]}"
    new_request = {
        "request_id": request_id,
        "submission_id": request.submission_id,
        "student_id": user.user_id,
        "student_name": user.name,
        "exam_id": submission["exam_id"],
        "questions": request.questions,
        "reason": request.reason,
        "status": "pending",
        "created_at": datetime.now(timezone.utc).isoformat()
    }
    
    await db.re_evaluations.insert_one(new_request)
    
    # Create notification for teacher
    if exam:
        await create_notification(
            user_id=exam["teacher_id"],
            notification_type="re_evaluation_request",
            title="New Re-evaluation Request",
            message=f"{user.name} requested re-evaluation for {exam.get('exam_name', 'exam')}",
            link="/teacher/re-evaluations"
        )
    
    return {"request_id": request_id, "status": "pending"}

@api_router.put("/re-evaluations/{request_id}")
async def update_re_evaluation(
    request_id: str,
    updates: dict,
    user: User = Depends(get_current_user)
):
    """Update re-evaluation request (teacher response)"""
    if user.role != "teacher":
        raise HTTPException(status_code=403, detail="Only teachers can respond")
    
    # Get re-evaluation request
    re_eval = await db.re_evaluations.find_one({"request_id": request_id}, {"_id": 0})
    if not re_eval:
        raise HTTPException(status_code=404, detail="Re-evaluation request not found")
    
    await db.re_evaluations.update_one(
        {"request_id": request_id},
        {"$set": {
            "status": updates.get("status", "resolved"),
            "response": updates.get("response", ""),
            "responded_at": datetime.now(timezone.utc).isoformat()
        }}
    )
    
    # Create notification for student
    await create_notification(
        user_id=re_eval["student_id"],
        notification_type="re_evaluation_response",
        title="Re-evaluation Response",
        message=f"Teacher responded to your re-evaluation request",
        link="/student/re-evaluation"
    )
    
    return {"message": "Re-evaluation updated"}

# ============== ANALYTICS ROUTES ==============

@api_router.get("/analytics/dashboard")
async def get_dashboard_analytics(user: User = Depends(get_current_user)):
    """Get dashboard analytics for teacher"""
    if user.role != "teacher":
        raise HTTPException(status_code=403, detail="Teacher only")
    
    # Count stats
    total_exams = await db.exams.count_documents({"teacher_id": user.user_id})
    total_batches = await db.batches.count_documents({"teacher_id": user.user_id})
    total_students = await db.users.count_documents({"teacher_id": user.user_id, "role": "student"})
    
    # Get exams for submission counts
    exams = await db.exams.find({"teacher_id": user.user_id}, {"exam_id": 1, "_id": 0}).to_list(100)
    exam_ids = [e["exam_id"] for e in exams]
    
    total_submissions = await db.submissions.count_documents({"exam_id": {"$in": exam_ids}})
    pending_reviews = await db.submissions.count_documents({
        "exam_id": {"$in": exam_ids},
        "status": "ai_graded"
    })
    pending_reeval = await db.re_evaluations.count_documents({
        "exam_id": {"$in": exam_ids},
        "status": "pending"
    })
    
    # Calculate average score
    submissions = await db.submissions.find(
        {"exam_id": {"$in": exam_ids}},
        {"percentage": 1, "_id": 0}
    ).to_list(500)
    avg_score = sum(s.get("percentage", 0) for s in submissions) / len(submissions) if submissions else 0
    
    # Recent activity
    recent_submissions = await db.submissions.find(
        {"exam_id": {"$in": exam_ids}},
        {"_id": 0, "submission_id": 1, "student_name": 1, "exam_id": 1, "student_id": 1, "obtained_marks": 1, "total_marks": 1, "percentage": 1, "total_score": 1, "status": 1, "created_at": 1, "graded_at": 1}
    ).sort("graded_at", -1).limit(10).to_list(10)
    
    return {
        "stats": {
            "total_exams": total_exams,
            "total_batches": total_batches,
            "total_students": total_students,
            "total_submissions": total_submissions,
            "pending_reviews": pending_reviews,
            "pending_reeval": pending_reeval,
            "avg_score": round(avg_score, 1)
        },
        "recent_submissions": recent_submissions
    }

@api_router.get("/analytics/class-report")
async def get_class_report(
    batch_id: Optional[str] = None,
    subject_id: Optional[str] = None,
    exam_id: Optional[str] = None,
    user: User = Depends(get_current_user)
):
    """Get class report analytics"""
    if user.role != "teacher":
        raise HTTPException(status_code=403, detail="Teacher only")
    
    # Build exam query
    exam_query = {"teacher_id": user.user_id}
    if batch_id:
        exam_query["batch_id"] = batch_id
    if subject_id:
        exam_query["subject_id"] = subject_id
    if exam_id:
        exam_query["exam_id"] = exam_id
    
    exams = await db.exams.find(exam_query, {"_id": 0}).to_list(100)
    exam_ids = [e["exam_id"] for e in exams]
    
    # Get all submissions
    submissions = await db.submissions.find(
        {"exam_id": {"$in": exam_ids}},
        {"_id": 0}
    ).to_list(500)
    
    if not submissions:
        return {
            "overview": {
                "total_students": 0,
                "avg_score": 0,
                "highest_score": 0,
                "lowest_score": 0,
                "pass_percentage": 0
            },
            "score_distribution": [],
            "top_performers": [],
            "needs_attention": [],
            "question_analysis": []
        }
    
    percentages = [s["percentage"] for s in submissions]
    
    # Score distribution
    distribution = {
        "0-20": len([p for p in percentages if 0 <= p < 20]),
        "21-40": len([p for p in percentages if 20 <= p < 40]),
        "41-60": len([p for p in percentages if 40 <= p < 60]),
        "61-80": len([p for p in percentages if 60 <= p < 80]),
        "81-100": len([p for p in percentages if 80 <= p <= 100])
    }
    
    # Top performers
    sorted_subs = sorted(submissions, key=lambda x: x["percentage"], reverse=True)
    top_performers = [
        {
            "name": s["student_name"], 
            "student_id": s["student_id"], 
            "score": s.get("obtained_marks") or s.get("total_score", 0), 
            "percentage": s["percentage"]
        }
        for s in sorted_subs[:5]
    ]
    
    # Needs attention (below 40%)
    needs_attention = [
        {
            "name": s["student_name"], 
            "student_id": s["student_id"], 
            "score": s.get("obtained_marks") or s.get("total_score", 0), 
            "percentage": s["percentage"]
        }
        for s in submissions if s["percentage"] < 40
    ][:10]
    
    # Question analysis
    question_analysis = []
    if submissions and submissions[0].get("question_scores"):
        num_questions = len(submissions[0]["question_scores"])
        for q_idx in range(num_questions):
            q_scores = []
            max_marks = 0
            for sub in submissions:
                if len(sub.get("question_scores", [])) > q_idx:
                    qs = sub["question_scores"][q_idx]
                    q_scores.append(qs["obtained_marks"])
                    max_marks = qs["max_marks"]
            
            if q_scores:
                avg = sum(q_scores) / len(q_scores)
                question_analysis.append({
                    "question": q_idx + 1,
                    "max_marks": max_marks,
                    "avg_score": round(avg, 2),
                    "percentage": round((avg / max_marks) * 100, 1) if max_marks > 0 else 0
                })
    
    return {
        "overview": {
            "total_students": len(submissions),
            "avg_score": round(sum(percentages) / len(percentages), 1),
            "highest_score": max(percentages),
            "lowest_score": min(percentages),
            "pass_percentage": round(len([p for p in percentages if p >= 40]) / len(percentages) * 100, 1)
        },
        "score_distribution": [
            {"range": k, "count": v} for k, v in distribution.items()
        ],
        "top_performers": top_performers,
        "needs_attention": needs_attention,
        "question_analysis": question_analysis
    }

@api_router.get("/analytics/insights")
async def get_class_insights(
    exam_id: Optional[str] = None,
    user: User = Depends(get_current_user)
):
    """Get AI-generated class insights"""
    if user.role != "teacher":
        raise HTTPException(status_code=403, detail="Teacher only")
    
    # Get exam data
    exam_query = {"teacher_id": user.user_id}
    if exam_id:
        exam_query["exam_id"] = exam_id
    
    exams = await db.exams.find(exam_query, {"_id": 0}).to_list(10)
    exam_ids = [e["exam_id"] for e in exams]
    
    submissions = await db.submissions.find(
        {"exam_id": {"$in": exam_ids}},
        {"_id": 0, "question_scores": 1, "percentage": 1}
    ).to_list(200)
    
    if not submissions:
        return {
            "summary": "No submissions available for analysis.",
            "strengths": [],
            "weaknesses": [],
            "recommendations": []
        }
    
    # Analyze question performance
    question_stats = {}
    for sub in submissions:
        for qs in sub.get("question_scores", []):
            q_num = qs["question_number"]
            if q_num not in question_stats:
                question_stats[q_num] = {"scores": [], "max": qs["max_marks"]}
            question_stats[q_num]["scores"].append(qs["obtained_marks"])
    
    strengths = []
    weaknesses = []
    
    for q_num, stats in question_stats.items():
        avg = sum(stats["scores"]) / len(stats["scores"]) if stats["scores"] else 0
        pct = (avg / stats["max"]) * 100 if stats["max"] > 0 else 0
        
        if pct >= 70:
            strengths.append(f"Question {q_num}: {pct:.0f}% average")
        elif pct < 50:
            weaknesses.append(f"Question {q_num}: {pct:.0f}% average - needs attention")
    
    avg_class = sum(s["percentage"] for s in submissions) / len(submissions)
    
    recommendations = [
        "Review weak areas in upcoming classes",
        "Consider additional practice problems for struggling concepts",
        "Recognize top performers to encourage class participation"
    ]
    
    if avg_class < 50:
        recommendations.insert(0, "Class average is below 50% - consider remedial sessions")
    elif avg_class >= 75:
        recommendations.insert(0, "Excellent class performance! Consider advanced topics")
    
    return {
        "summary": f"Class average: {avg_class:.1f}%. Analyzed {len(submissions)} submissions across {len(exams)} exam(s).",
        "strengths": strengths,
        "weaknesses": weaknesses,
        "recommendations": recommendations
    }

# ============== ADVANCED ANALYTICS ==============

@api_router.get("/analytics/misconceptions")
async def get_misconceptions_analysis(
    exam_id: str,
    user: User = Depends(get_current_user)
):
    """AI-powered analysis of common misconceptions and why students fail"""
    if user.role != "teacher":
        raise HTTPException(status_code=403, detail="Teacher only")
    
    # Get exam and submissions
    exam = await db.exams.find_one({"exam_id": exam_id, "teacher_id": user.user_id}, {"_id": 0})
    if not exam:
        raise HTTPException(status_code=404, detail="Exam not found")
    
    submissions = await db.submissions.find(
        {"exam_id": exam_id},
        {"_id": 0, "submission_id": 1, "student_name": 1, "question_scores": 1, "file_images": 1}
    ).to_list(100)
    
    if not submissions:
        return {"misconceptions": [], "question_insights": []}
    
    # Analyze each question for misconceptions
    question_insights = []
    misconceptions = []
    
    for q_idx, question in enumerate(exam.get("questions", [])):
        q_num = question.get("question_number", q_idx + 1)
        q_scores = []
        wrong_answers = []
        
        for sub in submissions:
            for qs in sub.get("question_scores", []):
                if qs.get("question_number") == q_num:
                    pct = (qs["obtained_marks"] / qs["max_marks"]) * 100 if qs["max_marks"] > 0 else 0
                    q_scores.append(pct)
                    if pct < 60:
                        wrong_answers.append({
                            "student_name": sub["student_name"],
                            "submission_id": sub["submission_id"],
                            "obtained": qs["obtained_marks"],
                            "max": qs["max_marks"],
                            "feedback": qs.get("ai_feedback", ""),
                            "question_text": qs.get("question_text", "")
                        })
        
        if q_scores:
            avg_pct = sum(q_scores) / len(q_scores)
            fail_rate = len([s for s in q_scores if s < 60]) / len(q_scores) * 100
            
            question_insights.append({
                "question_number": q_num,
                "question_text": question.get("rubric", f"Question {q_num}"),
                "avg_percentage": round(avg_pct, 1),
                "fail_rate": round(fail_rate, 1),
                "total_students": len(q_scores),
                "failing_students": len(wrong_answers),
                "wrong_answers": wrong_answers[:5]  # Limit to 5 examples
            })
            
            # If significant failure rate, add to misconceptions
            if fail_rate >= 30 and wrong_answers:
                misconceptions.append({
                    "question_number": q_num,
                    "fail_percentage": round(fail_rate, 1),
                    "affected_students": len(wrong_answers),
                    "sample_feedbacks": [wa["feedback"][:200] for wa in wrong_answers[:3] if wa["feedback"]]
                })
    
    # Use AI to analyze misconceptions if we have significant data
    ai_analysis = None
    if misconceptions:
        try:
            from emergentintegrations.llm.chat import LlmChat, UserMessage
            llm_key = os.environ.get("EMERGENT_LLM_KEY", "")
            
            analysis_prompt = f"""Analyze these student misconceptions from exam "{exam.get('exam_name', 'Unknown')}":

{[{
    'question': m['question_number'],
    'fail_rate': f"{m['fail_percentage']}%",
    'sample_feedback': m['sample_feedbacks']
} for m in misconceptions[:5]]}

For each question with high failure rate, identify:
1. The likely conceptual confusion or mistake pattern
2. What concept students confused with another concept
3. A brief explanation of why this confusion happens

Return as JSON array with format:
[{{"question": 1, "confusion": "Students confused X with Y", "reason": "brief explanation", "recommendation": "teaching suggestion"}}]

Only return the JSON array, no other text."""

            chat = LlmChat(
                api_key=llm_key,
                session_id=f"misconceptions_{uuid.uuid4().hex[:8]}",
                system_message="You are an expert at analyzing student misconceptions and learning patterns."
            ).with_model("gemini", "gemini-2.5-flash").with_params(temperature=0)
            
            user_message = UserMessage(text=analysis_prompt)
            ai_response = await chat.send_message(user_message)
            
            import json
            try:
                # Clean response and parse JSON
                cleaned = ai_response.strip()
                if cleaned.startswith("```"):
                    cleaned = cleaned.split("```")[1]
                    if cleaned.startswith("json"):
                        cleaned = cleaned[4:]
                ai_analysis = json.loads(cleaned)
            except (json.JSONDecodeError, IndexError, ValueError):
                ai_analysis = None
        except Exception as e:
            logger.error(f"AI misconception analysis error: {e}")
    
    return {
        "exam_name": exam.get("exam_name"),
        "total_submissions": len(submissions),
        "misconceptions": misconceptions,
        "question_insights": sorted(question_insights, key=lambda x: x["fail_rate"], reverse=True),
        "ai_analysis": ai_analysis or []
    }




def extract_topic_from_rubric(rubric: str, subject_name: str = "General") -> str:
    """
    Extract topic from question rubric using keyword matching
    Returns specific topic name based on keywords found in rubric
    """
    if not rubric:
        return subject_name
    
    rubric_lower = rubric.lower()
    
    # Mathematics topics (most comprehensive)
    math_topics = {
        "Algebra": ["algebra", "algebraic", "equation", "equations", "variable", "variables", "expression", "expressions", 
                    "polynomial", "polynomials", "quadratic", "linear", "factorize", "factorization", "simplify", "solve for"],
        
        "Geometry": ["geometry", "geometric", "triangle", "triangles", "circle", "circles", "square", "rectangle", "polygon",
                    "angle", "angles", "perimeter", "area", "congruent", "similar", "parallel", "perpendicular", "diagonal"],
        
        "Trigonometry": ["trigonometry", "trigonometric", "sine", "cosine", "tangent", "sin", "cos", "tan", "sec", "cosec", "cot",
                        "radian", "degree", "angle", "hypotenuse", "opposite", "adjacent"],
        
        "Calculus": ["calculus", "derivative", "derivatives", "differentiation", "integration", "integral", "integrals", 
                    "limit", "limits", "maxima", "minima", "rate of change", "slope", "tangent line", "curve"],
        
        "Statistics & Probability": ["statistics", "statistical", "probability", "probable", "mean", "median", "mode", 
                                    "average", "data", "frequency", "distribution", "variance", "standard deviation",
                                    "random", "sample", "population", "histogram", "bar graph"],
        
        "Coordinate Geometry": ["coordinate", "coordinates", "cartesian", "graph", "line", "slope", "gradient", "intercept",
                               "axis", "axes", "origin", "plot", "distance formula", "section formula", "midpoint"],
        
        "Mensuration": ["volume", "volumes", "surface area", "cube", "cuboid", "cylinder", "cone", "sphere", "hemisphere",
                       "prism", "pyramid", "capacity", "curved surface", "total surface"],
        
        "Number Systems": ["number system", "number", "numbers", "integer", "integers", "fraction", "fractions", "decimal", 
                          "decimals", "rational", "irrational", "real number", "prime", "composite", "hcf", "lcm", "divisibility"],
        
        "Set Theory": ["set", "sets", "union", "intersection", "subset", "superset", "venn diagram", "element", "universal set",
                      "complement", "disjoint", "cardinality"],
        
        "Matrices": ["matrix", "matrices", "determinant", "inverse", "transpose", "row", "column", "order", "singular"],
        
        "Sequences & Series": ["sequence", "sequences", "series", "arithmetic progression", "geometric progression", "ap", "gp",
                              "term", "sum", "infinite series"],
    }
    
    # Science topics
    science_topics = {
        "Physics - Mechanics": ["force", "motion", "velocity", "acceleration", "momentum", "energy", "work", "power", "friction"],
        "Physics - Electricity": ["current", "voltage", "resistance", "circuit", "electricity", "ohm", "capacitor", "inductor"],
        "Physics - Optics": ["light", "reflection", "refraction", "lens", "mirror", "optics", "ray", "spectrum"],
        "Chemistry - Organic": ["organic", "hydrocarbon", "alcohol", "acid", "ester", "polymer", "isomer", "benzene"],
        "Chemistry - Inorganic": ["inorganic", "metal", "non-metal", "periodic", "salt", "oxide", "compound", "element"],
        "Biology - Botany": ["plant", "leaf", "root", "stem", "flower", "photosynthesis", "chlorophyll", "botany"],
        "Biology - Zoology": ["animal", "cell", "tissue", "organ", "digestion", "respiration", "circulation", "reproduction"],
    }
    
    # English/Language topics
    language_topics = {
        "Grammar": ["grammar", "tense", "verb", "noun", "adjective", "adverb", "pronoun", "preposition", "conjunction",
                   "sentence", "clause", "phrase", "subject", "predicate", "punctuation"],
        "Comprehension": ["comprehension", "passage", "read", "understand", "infer", "context", "meaning", "paragraph"],
        "Writing": ["essay", "letter", "write", "composition", "article", "story", "creative writing", "formal", "informal"],
        "Literature": ["poem", "poetry", "prose", "novel", "drama", "character", "plot", "theme", "author", "literary"],
    }
    
    # Combine all topic dictionaries based on subject
    all_topics = {**math_topics, **science_topics, **language_topics}
    
    # Check each topic for keyword matches
    topic_scores = {}
    for topic, keywords in all_topics.items():
        score = sum(1 for keyword in keywords if keyword in rubric_lower)
        if score > 0:
            topic_scores[topic] = score
    
    # Return topic with highest score
    if topic_scores:
        best_topic = max(topic_scores, key=topic_scores.get)
        return best_topic
    
    # Fallback to subject name
    return subject_name


@api_router.get("/analytics/topic-mastery")
async def get_topic_mastery(
    exam_id: Optional[str] = None,
    batch_id: Optional[str] = None,
    user: User = Depends(get_current_user)
):
    """Get topic-based mastery heatmap data"""
    if user.role != "teacher":
        raise HTTPException(status_code=403, detail="Teacher only")
    
    # Build query
    exam_query = {"teacher_id": user.user_id}
    if exam_id:
        exam_query["exam_id"] = exam_id
    if batch_id:
        exam_query["batch_id"] = batch_id
    
    exams = await db.exams.find(exam_query, {"_id": 0}).to_list(50)
    if not exams:
        return {"topics": [], "students_by_topic": {}, "questions_by_topic": {}}
    
    exam_ids = [e["exam_id"] for e in exams]
    
    # Get all submissions
    submissions = await db.submissions.find(
        {"exam_id": {"$in": exam_ids}},
        {"_id": 0, "student_id": 1, "student_name": 1, "exam_id": 1, "question_scores": 1}
    ).to_list(500)
    
    # Build topic performance data
    topic_data = {}
    questions_by_topic = {}
    
    for exam in exams:
        for question in exam.get("questions", []):
            q_num = question.get("question_number", 0)
            rubric = question.get("rubric", "")
            
            # Get topic tags - use AI-inferred or manually set topics
            topics = question.get("topic_tags", [])
            
            # If no topic tags, extract from rubric using keyword matching
            if not topics:
                subject = None
                if exam.get("subject_id"):
                    subject_doc = await db.subjects.find_one({"subject_id": exam["subject_id"]}, {"_id": 0, "name": 1})
                    subject = subject_doc.get("name") if subject_doc else None
                
                # Extract topic from rubric
                extracted_topic = extract_topic_from_rubric(rubric, subject or "General")
                topics = [extracted_topic]
            
            for topic in topics:
                if topic not in topic_data:
                    topic_data[topic] = {"scores": [], "max_marks": 0, "students": {}, "questions": []}
                    questions_by_topic[topic] = []
                
                # Add question info to topic
                q_info = {
                    "exam_id": exam["exam_id"],
                    "exam_name": exam.get("exam_name", "Unknown"),
                    "question_number": q_num,
                    "rubric": rubric[:100] if rubric else f"Question {q_num}",
                    "max_marks": question.get("max_marks", 0)
                }
                if q_info not in topic_data[topic]["questions"]:
                    topic_data[topic]["questions"].append(q_info)
                
                # Find scores for this question
                for sub in submissions:
                    if sub["exam_id"] != exam["exam_id"]:
                        continue
                    for qs in sub.get("question_scores", []):
                        if qs.get("question_number") == q_num:
                            pct = (qs["obtained_marks"] / qs["max_marks"]) * 100 if qs["max_marks"] > 0 else 0
                            topic_data[topic]["scores"].append(pct)
                            topic_data[topic]["max_marks"] = max(topic_data[topic]["max_marks"], qs["max_marks"])
                            
                            # Track per-student performance
                            student_id = sub["student_id"]
                            student_name = sub["student_name"]
                            if student_id not in topic_data[topic]["students"]:
                                topic_data[topic]["students"][student_id] = {
                                    "name": student_name,
                                    "scores": []
                                }
                            topic_data[topic]["students"][student_id]["scores"].append(pct)
    
    # Calculate topic mastery levels
    topics = []
    students_by_topic = {}
    
    for topic, data in topic_data.items():
        if not data["scores"]:
            continue
        
        avg = sum(data["scores"]) / len(data["scores"])
        
        # Determine mastery level
        if avg >= 70:
            level = "mastered"
            color = "green"
        elif avg >= 50:
            level = "developing"
            color = "amber"
        else:
            level = "critical"
            color = "red"
        
        # Find struggling students for this topic
        struggling_students = []
        for student_id, student_data in data["students"].items():
            student_avg = sum(student_data["scores"]) / len(student_data["scores"])
            if student_avg < 50:
                struggling_students.append({
                    "student_id": student_id,
                    "name": student_data["name"],
                    "avg_score": round(student_avg, 1)
                })
        
        topics.append({
            "topic": topic,
            "avg_percentage": round(avg, 1),
            "level": level,
            "color": color,
            "sample_count": len(data["scores"]),
            "struggling_count": len(struggling_students),
            "question_count": len(data["questions"])
        })
        
        students_by_topic[topic] = sorted(struggling_students, key=lambda x: x["avg_score"])[:10]
        questions_by_topic[topic] = data["questions"]
    
    return {
        "topics": sorted(topics, key=lambda x: x["avg_percentage"]),
        "students_by_topic": students_by_topic,
        "questions_by_topic": questions_by_topic
    }


@api_router.get("/analytics/student-deep-dive/{student_id}")
async def get_student_deep_dive(
    student_id: str,
    exam_id: Optional[str] = None,
    user: User = Depends(get_current_user)
):
    """Get detailed student analysis with AI-generated insights"""
    if user.role != "teacher":
        raise HTTPException(status_code=403, detail="Teacher only")
    
    # Get student info
    student = await db.users.find_one({"user_id": student_id}, {"_id": 0})
    if not student:
        raise HTTPException(status_code=404, detail="Student not found")
    
    # Get submissions
    sub_query = {"student_id": student_id}
    if exam_id:
        sub_query["exam_id"] = exam_id
    
    submissions = await db.submissions.find(sub_query, {"_id": 0}).to_list(20)
    
    if not submissions:
        return {
            "student": {"name": student.get("name", "Unknown"), "email": student.get("email", "")},
            "worst_questions": [],
            "performance_trend": [],
            "ai_analysis": None
        }
    
    # Find worst performing questions
    all_question_scores = []
    for sub in submissions:
        exam = await db.exams.find_one({"exam_id": sub["exam_id"]}, {"_id": 0, "exam_name": 1, "model_answer_images": 1})
        for qs in sub.get("question_scores", []):
            pct = (qs["obtained_marks"] / qs["max_marks"]) * 100 if qs["max_marks"] > 0 else 0
            all_question_scores.append({
                "exam_name": exam.get("exam_name", "Unknown") if exam else "Unknown",
                "exam_id": sub["exam_id"],
                "submission_id": sub["submission_id"],
                "question_number": qs["question_number"],
                "question_text": qs.get("question_text", ""),
                "obtained_marks": qs["obtained_marks"],
                "max_marks": qs["max_marks"],
                "percentage": round(pct, 1),
                "ai_feedback": qs.get("ai_feedback", ""),
                "has_model_answer": bool(exam.get("model_answer_images") if exam else False)
            })
    
    # Sort by percentage (worst first)
    worst_questions = sorted(all_question_scores, key=lambda x: x["percentage"])[:5]
    
    # Performance trend
    performance_trend = []
    for sub in sorted(submissions, key=lambda x: x.get("created_at", "")):
        exam = await db.exams.find_one({"exam_id": sub["exam_id"]}, {"_id": 0, "exam_name": 1})
        performance_trend.append({
            "exam_name": exam.get("exam_name", "Unknown") if exam else "Unknown",
            "percentage": sub["percentage"],
            "date": sub.get("created_at", "")
        })
    
    # Generate AI analysis
    ai_analysis = None
    if worst_questions:
        try:
            from emergentintegrations.llm.chat import LlmChat, UserMessage
            llm_key = os.environ.get("EMERGENT_LLM_KEY", "")
            
            analysis_prompt = f"""Analyze this student's performance and provide specific improvement guidance:

Student: {student.get('name', 'Unknown')}
Overall Average: {sum(s['percentage'] for s in submissions)/len(submissions):.1f}%

Worst Performing Questions:
{[{
    'exam': q['exam_name'],
    'question': q['question_number'],
    'score': f"{q['obtained_marks']}/{q['max_marks']} ({q['percentage']}%)",
    'feedback': q['ai_feedback'][:150]
} for q in worst_questions]}

Provide:
1. A brief summary of the student's main weaknesses
2. Specific study recommendations (2-3 points)
3. What concepts need review

Keep response concise (under 200 words). Format as JSON:
{{"summary": "...", "recommendations": ["...", "..."], "concepts_to_review": ["...", "..."]}}"""

            chat = LlmChat(
                api_key=llm_key,
                session_id=f"student_analysis_{uuid.uuid4().hex[:8]}",
                system_message="You are an expert educational analyst providing personalized student guidance."
            ).with_model("gemini", "gemini-2.5-flash").with_params(temperature=0)
            
            user_message = UserMessage(text=analysis_prompt)
            ai_response = await chat.send_message(user_message)
            
            import json
            try:
                cleaned = ai_response.strip()
                if cleaned.startswith("```"):
                    cleaned = cleaned.split("```")[1]
                    if cleaned.startswith("json"):
                        cleaned = cleaned[4:]
                ai_analysis = json.loads(cleaned)
            except (json.JSONDecodeError, IndexError, ValueError):
                ai_analysis = {"summary": ai_response[:300]}
        except Exception as e:
            logger.error(f"AI student analysis error: {e}")
    
    return {
        "student": {
            "name": student.get("name", "Unknown"),
            "email": student.get("email", ""),
            "student_id": student_id
        },
        "overall_average": round(sum(s["percentage"] for s in submissions)/len(submissions), 1) if submissions else 0,
        "total_exams": len(submissions),
        "worst_questions": worst_questions,
        "performance_trend": performance_trend,
        "ai_analysis": ai_analysis
    }


@api_router.post("/analytics/generate-review-packet")
async def generate_review_packet(
    exam_id: str,
    user: User = Depends(get_current_user)
):
    """Generate AI-powered practice questions based on weak topics"""
    if user.role != "teacher":
        raise HTTPException(status_code=403, detail="Teacher only")
    
    # Get exam info
    exam = await db.exams.find_one({"exam_id": exam_id, "teacher_id": user.user_id}, {"_id": 0})
    if not exam:
        raise HTTPException(status_code=404, detail="Exam not found")
    
    # Get submissions to identify weak areas
    submissions = await db.submissions.find(
        {"exam_id": exam_id},
        {"_id": 0, "question_scores": 1}
    ).to_list(100)
    
    if not submissions:
        raise HTTPException(status_code=400, detail="No submissions found for this exam")
    
    # Analyze weak questions
    question_performance = {}
    for sub in submissions:
        for qs in sub.get("question_scores", []):
            q_num = qs["question_number"]
            if q_num not in question_performance:
                question_performance[q_num] = {"scores": [], "max": qs["max_marks"], "text": qs.get("question_text", "")}
            pct = (qs["obtained_marks"] / qs["max_marks"]) * 100 if qs["max_marks"] > 0 else 0
            question_performance[q_num]["scores"].append(pct)
    
    # Find weak questions (avg < 60%)
    weak_questions = []
    for q_num, data in question_performance.items():
        avg = sum(data["scores"]) / len(data["scores"])
        if avg < 60:
            weak_questions.append({
                "question_number": q_num,
                "avg_percentage": round(avg, 1),
                "question_text": data["text"],
                "max_marks": data["max"]
            })
    
    if not weak_questions:
        return {
            "message": "No weak areas identified - all questions have good performance!",
            "practice_questions": []
        }
    
    # Generate practice questions using AI
    try:
        from emergentintegrations.llm.chat import LlmChat, UserMessage
        llm_key = os.environ.get("EMERGENT_LLM_KEY", "")
        
        subject = await db.subjects.find_one({"subject_id": exam.get("subject_id")}, {"_id": 0, "name": 1})
        subject_name = subject.get("name", "General") if subject else "General"
        
        generation_prompt = f"""Generate 5 practice questions for students based on these weak areas from a {subject_name} exam:

Exam: {exam.get('exam_name', 'Unknown')}
Weak Questions:
{[{
    'question': q['question_number'],
    'topic': q['question_text'][:100] if q['question_text'] else f"Question {q['question_number']}",
    'avg_score': f"{q['avg_percentage']}%",
    'max_marks': q['max_marks']
} for q in weak_questions[:5]]}

Generate 5 practice questions that:
1. Target the same concepts as the weak questions
2. Have varying difficulty levels
3. Include a mix of question types
4. Help students understand the underlying concepts

Return as JSON array:
[{{
    "question_number": 1,
    "question": "question text",
    "marks": 5,
    "topic": "topic being tested",
    "difficulty": "easy/medium/hard",
    "hint": "optional hint for students"
}}]

Only return the JSON array."""

        chat = LlmChat(
            api_key=llm_key,
            session_id=f"review_packet_{uuid.uuid4().hex[:8]}",
            system_message="You are an expert educator creating practice questions to help students improve."
        ).with_model("gemini", "gemini-2.5-flash").with_params(temperature=0)
        
        user_message = UserMessage(text=generation_prompt)
        ai_response = await chat.send_message(user_message)
        
        import json
        try:
            cleaned = ai_response.strip()
            if cleaned.startswith("```"):
                cleaned = cleaned.split("```")[1]
                if cleaned.startswith("json"):
                    cleaned = cleaned[4:]
            practice_questions = json.loads(cleaned)
        except (json.JSONDecodeError, IndexError, ValueError):
            practice_questions = []
        
        return {
            "exam_name": exam.get("exam_name"),
            "subject": subject_name,
            "weak_areas_identified": len(weak_questions),
            "practice_questions": practice_questions,
            "generated_at": datetime.now(timezone.utc).isoformat()
        }
        
    except Exception as e:
        logger.error(f"Error generating review packet: {e}")
        raise HTTPException(status_code=500, detail="Failed to generate practice questions")


@api_router.post("/exams/{exam_id}/infer-topics")
async def infer_question_topics(
    exam_id: str,
    user: User = Depends(get_current_user)
):
    """Use AI to automatically infer topic tags for exam questions"""
    if user.role != "teacher":
        raise HTTPException(status_code=403, detail="Teacher only")
    
    exam = await db.exams.find_one({"exam_id": exam_id, "teacher_id": user.user_id}, {"_id": 0})
    if not exam:
        raise HTTPException(status_code=404, detail="Exam not found")
    
    questions = exam.get("questions", [])
    if not questions:
        raise HTTPException(status_code=400, detail="No questions found in exam")
    
    # Get subject info for context
    subject = await db.subjects.find_one({"subject_id": exam.get("subject_id")}, {"_id": 0, "name": 1})
    subject_name = subject.get("name", "General") if subject else "General"
    
    try:
        from emergentintegrations.llm.chat import LlmChat, UserMessage
        llm_key = os.environ.get("EMERGENT_LLM_KEY", "")
        
        # Build question data
        question_data = []
        for q in questions:
            question_data.append({
                "number": q.get("question_number"),
                "rubric": q.get("rubric", ""),
                "marks": q.get("max_marks")
            })
        
        inference_prompt = f"""Analyze these exam questions from a {subject_name} exam and assign topic tags:

Exam: {exam.get('exam_name', 'Unknown')}
Subject: {subject_name}

Questions:
{question_data}

For each question, provide 1-3 topic tags that describe what concept/skill is being tested.
Topics should be specific but not too narrow (e.g., "Grammar - Tenses", "Algebra - Quadratic Equations", "Reading Comprehension").

Return as JSON:
{{
    "1": ["Topic A", "Topic B"],
    "2": ["Topic C"],
    ...
}}

Only return the JSON object."""

        chat = LlmChat(
            api_key=llm_key,
            session_id=f"infer_topics_{uuid.uuid4().hex[:8]}",
            system_message="You are an expert at analyzing exam questions and categorizing them by topic."
        ).with_model("gemini", "gemini-2.5-flash").with_params(temperature=0)
        
        user_message = UserMessage(text=inference_prompt)
        ai_response = await chat.send_message(user_message)
        
        import json
        try:
            cleaned = ai_response.strip()
            if cleaned.startswith("```"):
                cleaned = cleaned.split("```")[1]
                if cleaned.startswith("json"):
                    cleaned = cleaned[4:]
            topic_mapping = json.loads(cleaned)
        except (json.JSONDecodeError, IndexError, ValueError):
            raise HTTPException(status_code=500, detail="Failed to parse AI response")
        
        # Update questions with topic tags
        updated_questions = []
        for q in questions:
            q_num = str(q.get("question_number"))
            q["topic_tags"] = topic_mapping.get(q_num, [])
            updated_questions.append(q)
        
        # Save to database
        await db.exams.update_one(
            {"exam_id": exam_id},
            {"$set": {"questions": updated_questions}}
        )
        
        return {
            "message": "Topic tags inferred successfully",
            "topics": topic_mapping
        }
        
    except Exception as e:
        logger.error(f"Error inferring topics: {e}")
        raise HTTPException(status_code=500, detail="Failed to infer topic tags")


@api_router.put("/exams/{exam_id}/question-topics")
async def update_question_topics(
    exam_id: str,
    topics: Dict[str, List[str]],
    user: User = Depends(get_current_user)
):
    """Manually update topic tags for exam questions"""
    if user.role != "teacher":
        raise HTTPException(status_code=403, detail="Teacher only")
    
    exam = await db.exams.find_one({"exam_id": exam_id, "teacher_id": user.user_id}, {"_id": 0})
    if not exam:
        raise HTTPException(status_code=404, detail="Exam not found")
    
    # Update questions with new topic tags
    updated_questions = []
    for q in exam.get("questions", []):
        q_num = str(q.get("question_number"))
        if q_num in topics:
            q["topic_tags"] = topics[q_num]
        updated_questions.append(q)
    
    await db.exams.update_one(
        {"exam_id": exam_id},
        {"$set": {"questions": updated_questions}}
    )
    
    return {"message": "Topic tags updated successfully"}


@api_router.get("/analytics/student-dashboard")
async def get_student_dashboard(user: User = Depends(get_current_user)):
    """Get student's personal dashboard analytics"""
    if user.role != "student":
        raise HTTPException(status_code=403, detail="Only students can access this")
    
    # Get student's submissions for PUBLISHED exams only
    published_exam_ids = []
    published_exams = await db.exams.find(
        {"results_published": True},
        {"_id": 0, "exam_id": 1}
    ).to_list(1000)
    published_exam_ids = [e["exam_id"] for e in published_exams]
    
    # Get submissions only for published results
    submissions = await db.submissions.find(
        {
            "student_id": user.user_id,
            "exam_id": {"$in": published_exam_ids}  # Only published
        },
        {"_id": 0}
    ).to_list(100)
    
    if not submissions:
        return {
            "stats": {
                "total_exams": 0,
                "avg_percentage": 0,
                "rank": "N/A",
                "improvement": 0
            },
            "recent_results": [],
            "subject_performance": [],
            "recommendations": ["Complete your first exam to see analytics!"],
            "weak_areas": [],
            "strong_areas": []
        }
    
    percentages = [s.get("percentage", 0) for s in submissions]
    
    # Recent results
    recent = sorted(submissions, key=lambda x: x.get("graded_at", x.get("created_at", "")), reverse=True)[:5]
    recent_results = []
    for r in recent:
        exam = await db.exams.find_one({"exam_id": r["exam_id"]}, {"_id": 0, "exam_name": 1, "subject_id": 1})
        subject = await db.subjects.find_one({"subject_id": exam.get("subject_id")}, {"_id": 0, "name": 1}) if exam else None
        recent_results.append({
            "exam_name": exam.get("exam_name", "Unknown") if exam else "Unknown",
            "subject": subject.get("name", "Unknown") if subject else "Unknown",
            "score": f"{r.get('obtained_marks', 0)}/{r.get('total_marks', 100)}",
            "percentage": r.get("percentage", 0),
            "date": r.get("graded_at", r.get("created_at", ""))
        })
    
    # Subject-wise performance
    subject_perf = {}
    for sub in submissions:
        exam = await db.exams.find_one({"exam_id": sub["exam_id"]}, {"_id": 0, "subject_id": 1})
        if exam:
            subj = await db.subjects.find_one({"subject_id": exam.get("subject_id")}, {"_id": 0, "name": 1})
            subj_name = subj.get("name", "Unknown") if subj else "Unknown"
            if subj_name not in subject_perf:
                subject_perf[subj_name] = []
            subject_perf[subj_name].append(sub["percentage"])
    
    subject_performance = [
        {"subject": name, "average": round(sum(scores)/len(scores), 1), "exams": len(scores)}
        for name, scores in subject_perf.items()
    ]
    
    # ====== TOPIC-BASED PERFORMANCE ANALYSIS FOR STUDENTS ======
    topic_performance = {}  # {topic: [{"score": pct, "exam_date": date}]}
    
    for sub in submissions:
        exam = await db.exams.find_one({"exam_id": sub["exam_id"]}, {"_id": 0})
        if not exam:
            continue
        
        exam_date = sub.get("created_at", "")
        exam_questions = exam.get("questions", [])
        
        # Create a map of question_number -> topics
        question_topics = {}
        for q in exam_questions:
            q_num = q.get("question_number")
            topics = q.get("topic_tags", [])
            if not topics:
                subj = await db.subjects.find_one({"subject_id": exam.get("subject_id")}, {"_id": 0, "name": 1})
                topics = [subj.get("name", "General")] if subj else ["General"]
            question_topics[q_num] = topics
        
        for qs in sub.get("question_scores", []):
            q_num = qs.get("question_number")
            pct = (qs["obtained_marks"] / qs["max_marks"]) * 100 if qs.get("max_marks", 0) > 0 else 0
            topics = question_topics.get(q_num, ["General"])
            
            for topic in topics:
                if topic not in topic_performance:
                    topic_performance[topic] = []
                topic_performance[topic].append({
                    "score": pct,
                    "exam_date": exam_date,
                    "feedback": qs.get("ai_feedback", "")[:150]
                })
    
    # Analyze topics
    weak_topics = []
    strong_topics = []
    
    for topic, performances in topic_performance.items():
        if len(performances) == 0:
            continue
        
        sorted_perfs = sorted(performances, key=lambda x: x.get("exam_date", ""))
        avg_score = sum(p["score"] for p in sorted_perfs) / len(sorted_perfs)
        
        # Calculate trend
        trend = 0
        trend_text = "stable"
        if len(sorted_perfs) >= 2:
            mid = len(sorted_perfs) // 2
            first_half_avg = sum(p["score"] for p in sorted_perfs[:mid]) / mid if mid > 0 else 0
            second_half_avg = sum(p["score"] for p in sorted_perfs[mid:]) / (len(sorted_perfs) - mid)
            trend = second_half_avg - first_half_avg
            
            if trend > 10:
                trend_text = "improving"
            elif trend < -10:
                trend_text = "declining"
        
        topic_data = {
            "topic": topic,
            "avg_score": round(avg_score, 1),
            "total_attempts": len(sorted_perfs),
            "trend": round(trend, 1),
            "trend_text": trend_text,
            "recent_score": round(sorted_perfs[-1]["score"], 1) if sorted_perfs else 0,
            "feedback": sorted_perfs[-1].get("feedback", "") if sorted_perfs else ""
        }
        
        if avg_score < 50:
            weak_topics.append(topic_data)
        elif avg_score >= 75:
            strong_topics.append(topic_data)
    
    weak_topics = sorted(weak_topics, key=lambda x: x["avg_score"])[:5]
    strong_topics = sorted(strong_topics, key=lambda x: -x["avg_score"])[:5]
    
    # Smart recommendations
    recommendations = []
    
    declining_topics = [t for t in weak_topics if t["trend_text"] == "declining"]
    if declining_topics:
        recommendations.append(f"⚠️ Focus on {declining_topics[0]['topic']} - your performance is declining")
    
    improving_weak = [t for t in weak_topics if t["trend_text"] == "improving"]
    if improving_weak:
        recommendations.append(f"📈 Great improvement in {improving_weak[0]['topic']}! Keep practicing")
    
    stable_weak = [t for t in weak_topics if t["trend_text"] == "stable"]
    if stable_weak:
        recommendations.append(f"💡 Review concepts in {stable_weak[0]['topic']} - needs more attention")
    
    if strong_topics:
        recommendations.append(f"⭐ You're excelling in {strong_topics[0]['topic']}! Consider helping classmates")
    
    if not recommendations:
        recommendations = [
            "Complete more exams to get personalized insights",
            "Review feedback on each question to improve",
            "Practice regularly across all topics"
        ]
    
    # Calculate improvement trend
    avg_percentage = sum(percentages) / len(percentages) if percentages else 0
    
    if len(percentages) >= 2:
        recent_avg = sum(percentages[-3:]) / min(3, len(percentages))
        older_avg = sum(percentages[:-3]) / max(1, len(percentages) - 3) if len(percentages) > 3 else recent_avg
        improvement = round(recent_avg - older_avg, 1)
    else:
        improvement = 0
    
    return {
        "stats": {
            "total_exams": len(submissions),
            "avg_percentage": round(avg_percentage, 1),
            "rank": "Top 10",  # Simplified for MVP
            "improvement": improvement
        },
        "recent_results": recent_results,
        "subject_performance": subject_performance,
        "recommendations": recommendations,
        "weak_topics": weak_topics,
        "strong_topics": strong_topics,
        # Keep backward compatibility
        "weak_areas": [{"question": t["topic"], "score": f"{t['avg_score']}%", "feedback": t.get("feedback", "")} for t in weak_topics],
        "strong_areas": [{"question": t["topic"], "score": f"{t['avg_score']}%"} for t in strong_topics]
    }



# ============== NEW DRILL-DOWN ANALYTICS ==============

@api_router.get("/analytics/drill-down/topic/{topic_name}")
async def get_topic_drilldown(
    topic_name: str,
    exam_id: Optional[str] = None,
    batch_id: Optional[str] = None,
    user: User = Depends(get_current_user)
):
    """
    Level 2 Drill-Down: Get detailed breakdown of a topic into sub-skills
    Returns: Sub-skill performance, questions, and student groups
    """
    if user.role != "teacher":
        raise HTTPException(status_code=403, detail="Teacher only")
    
    # Build query
    exam_query = {"teacher_id": user.user_id}
    if exam_id:
        exam_query["exam_id"] = exam_id
    if batch_id:
        exam_query["batch_id"] = batch_id
    
    exams = await db.exams.find(exam_query, {"_id": 0}).to_list(50)
    if not exams:
        return {"sub_skills": [], "questions": [], "students": []}
    
    exam_ids = [e["exam_id"] for e in exams]
    
    # Get all questions related to this topic
    questions_in_topic = []
    for exam in exams:
        for question in exam.get("questions", []):
            topics = question.get("topic_tags", [])
            if not topics:
                # Fallback to subject name
                subject = None
                if exam.get("subject_id"):
                    subject_doc = await db.subjects.find_one({"subject_id": exam["subject_id"]}, {"_id": 0, "name": 1})
                    subject = subject_doc.get("name") if subject_doc else None
                topics = [subject or "General"]
            
            if topic_name in topics:
                questions_in_topic.append({
                    "exam_id": exam["exam_id"],
                    "exam_name": exam.get("exam_name", "Unknown"),
                    "question_number": question.get("question_number"),
                    "rubric": question.get("rubric", ""),
                    "max_marks": question.get("max_marks", 0),
                    "sub_questions": question.get("sub_questions", [])
                })
    
    # Get submissions for these exams
    submissions = await db.submissions.find(
        {"exam_id": {"$in": exam_ids}},
        {"_id": 0, "student_id": 1, "student_name": 1, "exam_id": 1, "question_scores": 1}
    ).to_list(500)
    
    # Analyze sub-skills using AI
    # Extract sub-skills from question rubrics
    sub_skill_performance = {}
    question_performance = {}
    
    for q in questions_in_topic:
        q_key = f"{q['exam_id']}_{q['question_number']}"
        question_performance[q_key] = {
            "exam_name": q["exam_name"],
            "question_number": q["question_number"],
            "rubric": q["rubric"],
            "max_marks": q["max_marks"],
            "scores": [],
            "avg_percentage": 0
        }
        
        # Identify sub-skill from rubric (simplified - can be enhanced with AI)
        rubric_lower = q["rubric"].lower()
        sub_skill = "Concept Understanding"  # Default
        
        if any(word in rubric_lower for word in ["calculate", "compute", "find the value"]):
            sub_skill = "Calculation"
        elif any(word in rubric_lower for word in ["prove", "derive", "show that"]):
            sub_skill = "Proof & Derivation"
        elif any(word in rubric_lower for word in ["apply", "solve", "use"]):
            sub_skill = "Application"
        elif any(word in rubric_lower for word in ["explain", "describe", "define"]):
            sub_skill = "Concept Understanding"
        
        if sub_skill not in sub_skill_performance:
            sub_skill_performance[sub_skill] = {"scores": [], "question_count": 0}
        
        sub_skill_performance[sub_skill]["question_count"] += 1
    
    # Collect scores
    for submission in submissions:
        for qs in submission.get("question_scores", []):
            q_key = f"{submission['exam_id']}_{qs.get('question_number')}"
            if q_key in question_performance:
                percentage = (qs["obtained_marks"] / qs["max_marks"]) * 100 if qs.get("max_marks", 0) > 0 else 0
                question_performance[q_key]["scores"].append({
                    "student_id": submission["student_id"],
                    "student_name": submission["student_name"],
                    "obtained": qs["obtained_marks"],
                    "max": qs["max_marks"],
                    "percentage": percentage,
                    "feedback": qs.get("ai_feedback", "")
                })
    
    # Calculate averages
    for q_key, q_data in question_performance.items():
        if q_data["scores"]:
            q_data["avg_percentage"] = round(sum(s["percentage"] for s in q_data["scores"]) / len(q_data["scores"]), 1)
    
    # Aggregate sub-skill scores
    for q in questions_in_topic:
        q_key = f"{q['exam_id']}_{q['question_number']}"
        if q_key in question_performance:
            rubric_lower = q["rubric"].lower()
            sub_skill = "Concept Understanding"
            
            if any(word in rubric_lower for word in ["calculate", "compute", "find the value"]):
                sub_skill = "Calculation"
            elif any(word in rubric_lower for word in ["prove", "derive", "show that"]):
                sub_skill = "Proof & Derivation"
            elif any(word in rubric_lower for word in ["apply", "solve", "use"]):
                sub_skill = "Application"
            elif any(word in rubric_lower for word in ["explain", "describe", "define"]):
                sub_skill = "Concept Understanding"
            
            for score in question_performance[q_key]["scores"]:
                sub_skill_performance[sub_skill]["scores"].append(score["percentage"])
    
    # Format sub-skills
    sub_skills = []
    for skill, data in sub_skill_performance.items():
        if data["scores"]:
            avg = round(sum(data["scores"]) / len(data["scores"]), 1)
            sub_skills.append({
                "name": skill,
                "avg_percentage": avg,
                "question_count": data["question_count"],
                "color": "green" if avg >= 70 else "amber" if avg >= 50 else "red"
            })
    
    # Get struggling students for this topic
    student_performance = {}
    for q_key, q_data in question_performance.items():
        for score in q_data["scores"]:
            sid = score["student_id"]
            if sid not in student_performance:
                student_performance[sid] = {
                    "student_id": sid,
                    "student_name": score["student_name"],
                    "scores": []
                }
            student_performance[sid]["scores"].append(score["percentage"])
    
    struggling_students = []
    for sid, data in student_performance.items():
        avg = sum(data["scores"]) / len(data["scores"]) if data["scores"] else 0
        if avg < 60:  # Threshold for struggling
            struggling_students.append({
                "student_id": data["student_id"],
                "student_name": data["student_name"],
                "avg_percentage": round(avg, 1),
                "attempts": len(data["scores"])
            })
    
    # AI-generated insight
    insight = f"Analysis shows {len(struggling_students)} students need attention in {topic_name}. "
    if sub_skills:
        weakest = min(sub_skills, key=lambda x: x["avg_percentage"])
        insight += f"Weakest sub-skill: {weakest['name']} ({weakest['avg_percentage']}%)."
    
    return {
        "topic": topic_name,
        "insight": insight,
        "sub_skills": sorted(sub_skills, key=lambda x: x["avg_percentage"]),
        "questions": [q for q in question_performance.values()],
        "struggling_students": struggling_students
    }


@api_router.get("/analytics/drill-down/question")
async def get_question_drilldown(
    exam_id: str,
    question_number: int,
    user: User = Depends(get_current_user)
):
    """
    Level 3 Drill-Down: Get error patterns for a specific question
    Returns: Student groups by error type
    """
    if user.role != "teacher":
        raise HTTPException(status_code=403, detail="Teacher only")
    
    # Get exam
    exam = await db.exams.find_one(
        {"exam_id": exam_id, "teacher_id": user.user_id},
        {"_id": 0}
    )
    if not exam:
        raise HTTPException(status_code=404, detail="Exam not found")
    
    # Get question details
    question = None
    for q in exam.get("questions", []):
        if q.get("question_number") == question_number:
            question = q
            break
    
    if not question:
        raise HTTPException(status_code=404, detail="Question not found")
    
    # Get all submissions for this exam
    submissions = await db.submissions.find(
        {"exam_id": exam_id},
        {"_id": 0, "student_id": 1, "student_name": 1, "question_scores": 1, "file_images": 1}
    ).to_list(1000)
    
    # Collect all answers for this question
    student_answers = []
    for submission in submissions:
        for qs in submission.get("question_scores", []):
            if qs.get("question_number") == question_number:
                student_answers.append({
                    "student_id": submission["student_id"],
                    "student_name": submission["student_name"],
                    "obtained_marks": qs["obtained_marks"],
                    "max_marks": qs["max_marks"],
                    "percentage": (qs["obtained_marks"] / qs["max_marks"]) * 100 if qs.get("max_marks", 0) > 0 else 0,
                    "feedback": qs.get("ai_feedback", ""),
                    "answer_text": qs.get("answer_text", ""),
                    "sub_scores": qs.get("sub_scores", [])
                })
    
    # Group students by error patterns using AI
    logger.info(f"Analyzing error patterns for Question {question_number} with {len(student_answers)} answers")
    
    # Separate into performance groups
    failed_answers = [a for a in student_answers if a["percentage"] < 50]
    passed_answers = [a for a in student_answers if a["percentage"] >= 50]
    blank_answers = [a for a in student_answers if a["obtained_marks"] == 0]
    
    # Use AI to categorize errors for failed students
    error_groups = {}
    
    if failed_answers:
        try:
            from emergentintegrations.llm.chat import LlmChat, UserMessage
            
            # Prepare feedback summary for AI
            feedback_samples = [f"Student {a['student_name']}: {a['feedback'][:200]}" for a in failed_answers[:10]]
            
            prompt = f"""
Analyze these student errors for Question {question_number}:

Question: {question.get('rubric', '')}
Max Marks: {question.get('max_marks', 0)}

Failed Student Feedbacks:
{chr(10).join(feedback_samples)}

Task: Identify 3-4 common error patterns/categories. For each category, provide:
1. Error type name (e.g., "Calculation Error", "Conceptual Misunderstanding", "Incomplete Answer")
2. Brief description
3. Which students fall into this category (by name)

Respond in JSON format:
{{
    "error_categories": [
        {{
            "type": "Calculation Error",
            "description": "Made arithmetic mistakes",
            "student_names": ["Alice", "Bob"]
        }}
    ]
}}
"""
            
            chat = LlmChat(
                api_key=os.environ.get("EMERGENT_LLM_KEY"),
                session_id=f"error_group_{uuid.uuid4().hex[:8]}",
                system_message="You are an educational data analyst. Categorize student errors precisely."
            ).with_model("gemini", "gemini-2.5-flash").with_params(temperature=0)
            
            user_message = UserMessage(text=prompt)
            response = await chat.send_message(user_message)
            import json
            import re
            
            # Extract JSON from response
            response_text = response.strip()
            json_match = re.search(r'\{.*\}', response_text, re.DOTALL)
            
            if json_match:
                error_analysis = json.loads(json_match.group())
                
                # Map students to categories
                for category in error_analysis.get("error_categories", []):
                    error_type = category["type"]
                    error_groups[error_type] = {
                        "description": category["description"],
                        "students": []
                    }
                    
                    # Find students matching this category
                    for answer in failed_answers:
                        if answer["student_name"] in category.get("student_names", []):
                            error_groups[error_type]["students"].append({
                                "student_id": answer["student_id"],
                                "student_name": answer["student_name"],
                                "score": answer["obtained_marks"],
                                "feedback": answer["feedback"]
                            })
            
        except Exception as e:
            logger.error(f"Error in AI error grouping: {e}")
            # Fallback grouping
            error_groups = {
                "Low Scorers": {
                    "description": "Students who scored below 50%",
                    "students": [{
                        "student_id": a["student_id"],
                        "student_name": a["student_name"],
                        "score": a["obtained_marks"],
                        "feedback": a["feedback"]
                    } for a in failed_answers]
                }
            }
    
    # Add blank/not attempted group
    if blank_answers:
        error_groups["Not Attempted / Blank"] = {
            "description": "Students who left the question blank or scored 0",
            "students": [{
                "student_id": a["student_id"],
                "student_name": a["student_name"],
                "score": 0,
                "feedback": "No answer provided"
            } for a in blank_answers]
        }
    
    # Calculate statistics
    total_students = len(student_answers)
    avg_score = sum(a["percentage"] for a in student_answers) / total_students if total_students > 0 else 0
    pass_count = len([a for a in student_answers if a["percentage"] >= 50])
    
    return {
        "question": {
            "number": question_number,
            "rubric": question.get("rubric", ""),
            "max_marks": question.get("max_marks", 0)
        },
        "statistics": {
            "total_students": total_students,
            "avg_percentage": round(avg_score, 1),
            "pass_count": pass_count,
            "fail_count": total_students - pass_count,
            "blank_count": len(blank_answers)
        },
        "error_groups": [
            {
                "type": error_type,
                "description": data["description"],
                "count": len(data["students"]),
                "students": data["students"]
            }
            for error_type, data in error_groups.items()
        ],
        "top_performers": sorted(
            [{
                "student_name": a["student_name"],
                "score": a["obtained_marks"],
                "max_marks": a["max_marks"]
            } for a in passed_answers],
            key=lambda x: x["score"],
            reverse=True
        )[:5]
    }


@api_router.get("/analytics/student-journey/{student_id}")
async def get_student_journey(
    student_id: str,
    user: User = Depends(get_current_user)
):
    """
    Student Journey View: Complete academic health record with comparisons
    """
    if user.role != "teacher":
        raise HTTPException(status_code=403, detail="Teacher only")
    
    # Get student info
    student = await db.users.find_one({"user_id": student_id}, {"_id": 0})
    if not student:
        raise HTTPException(status_code=404, detail="Student not found")
    
    # Get all submissions for this student
    submissions = await db.submissions.find(
        {"student_id": student_id},
        {"_id": 0}
    ).to_list(1000)
    
    if not submissions:
        return {
            "student": student,
            "performance_trend": [],
            "vs_class_avg": [],
            "blind_spots": [],
            "strengths": []
        }
    
    # Sort by date
    submissions.sort(key=lambda x: x.get("created_at", ""))
    
    # Build performance trend
    performance_trend = []
    for sub in submissions:
        exam = await db.exams.find_one({"exam_id": sub["exam_id"]}, {"_id": 0, "exam_name": 1})
        performance_trend.append({
            "exam_name": exam.get("exam_name", "Unknown") if exam else "Unknown",
            "date": sub.get("created_at", ""),
            "percentage": sub["percentage"],
            "score": sub["total_score"]
        })
    
    # Calculate class averages for comparison
    exam_ids = [s["exam_id"] for s in submissions]
    class_averages = {}
    
    for exam_id in exam_ids:
        all_submissions = await db.submissions.find(
            {"exam_id": exam_id},
            {"_id": 0, "percentage": 1}
        ).to_list(1000)
        
        if all_submissions:
            avg = sum(s["percentage"] for s in all_submissions) / len(all_submissions)
            class_averages[exam_id] = round(avg, 1)
    
    # Add class average to trend
    vs_class_avg = []
    for sub in submissions:
        exam = await db.exams.find_one({"exam_id": sub["exam_id"]}, {"_id": 0, "exam_name": 1})
        vs_class_avg.append({
            "exam_name": exam.get("exam_name", "Unknown") if exam else "Unknown",
            "student_score": sub["percentage"],
            "class_avg": class_averages.get(sub["exam_id"], 0),
            "difference": round(sub["percentage"] - class_averages.get(sub["exam_id"], 0), 1)
        })
    
    # Identify blind spots (topics with consistent low performance)
    topic_performance = {}
    
    for sub in submissions:
        exam = await db.exams.find_one({"exam_id": sub["exam_id"]}, {"_id": 0, "questions": 1})
        if not exam:
            continue
        
        question_topics = {}
        for q in exam.get("questions", []):
            topics = q.get("topic_tags", ["General"])
            question_topics[q.get("question_number")] = topics
        
        for qs in sub.get("question_scores", []):
            q_num = qs.get("question_number")
            topics = question_topics.get(q_num, ["General"])
            percentage = (qs["obtained_marks"] / qs["max_marks"]) * 100 if qs.get("max_marks", 0) > 0 else 0
            
            for topic in topics:
                if topic not in topic_performance:
                    topic_performance[topic] = []
                topic_performance[topic].append(percentage)
    
    # Calculate blind spots (avg < 50%)
    blind_spots = []
    strengths = []
    
    for topic, scores in topic_performance.items():
        avg = sum(scores) / len(scores)
        data = {
            "topic": topic,
            "avg_score": round(avg, 1),
            "attempts": len(scores)
        }
        
        if avg < 50:
            blind_spots.append(data)
        elif avg >= 70:
            strengths.append(data)
    
    return {
        "student": {
            "name": student.get("name", "Unknown"),
            "email": student.get("email", ""),
            "student_id": student.get("student_id", "")
        },
        "overall_stats": {
            "total_exams": len(submissions),
            "avg_percentage": round(sum(s["percentage"] for s in submissions) / len(submissions), 1),
            "highest": max(s["percentage"] for s in submissions),
            "lowest": min(s["percentage"] for s in submissions)
        },
        "performance_trend": performance_trend,
        "vs_class_avg": vs_class_avg,
        "blind_spots": sorted(blind_spots, key=lambda x: x["avg_score"]),
        "strengths": sorted(strengths, key=lambda x: x["avg_score"], reverse=True)
    }




# ============== COMPREHENSIVE AI ANALYTICS ==============

@api_router.post("/analytics/ask-ai")
async def ask_ai_comprehensive(
    request: dict,
    user: User = Depends(get_current_user)
):
    """
    Comprehensive AI Analytics Assistant
    Can answer ANY question about the data with text, numbers, charts, lists
    
    Examples:
    - "List students who scored below 50%"
    - "Show me a histogram of student performance"
    - "Which topics do students struggle with most?"
    - "How many students gave the exam?"
    - "Show performance trend over time"
    """
    if user.role != "teacher":
        raise HTTPException(status_code=403, detail="Teacher only")
    
    query = request.get("query", "").strip()
    exam_id = request.get("exam_id")
    batch_id = request.get("batch_id")
    
    if not query:
        raise HTTPException(status_code=400, detail="Query is required")
    
    logger.info(f"AI Analytics Query from {user.email}: {query}")
    
    try:
        # Gather all relevant data for the teacher
        exam_query = {"teacher_id": user.user_id}
        if exam_id:
            exam_query["exam_id"] = exam_id
        if batch_id:
            exam_query["batch_id"] = batch_id
        
        # Fetch data
        exams = await db.exams.find(exam_query, {"_id": 0}).to_list(100)
        exam_ids = [e["exam_id"] for e in exams]
        
        if not exam_ids:
            return {
                "type": "text",
                "response": "No exams found matching your criteria. Please create an exam first."
            }
        
        # Get submissions
        submissions = await db.submissions.find(
            {"exam_id": {"$in": exam_ids}},
            {"_id": 0}
        ).to_list(1000)
        
        # Get students
        students = await db.users.find(
            {"teacher_id": user.user_id, "role": "student"},
            {"_id": 0, "user_id": 1, "name": 1, "email": 1}
        ).to_list(500)
        
        # Get batches
        batches = await db.batches.find(
            {"teacher_id": user.user_id},
            {"_id": 0}
        ).to_list(100)
        
        # Prepare context data for AI
        context_data = {
            "total_exams": len(exams),
            "total_students": len(students),
            "total_submissions": len(submissions),
            "total_batches": len(batches),
            "exams_summary": [
                {
                    "exam_id": e.get("exam_id"),
                    "exam_name": e.get("exam_name"),
                    "total_marks": e.get("total_marks"),
                    "status": e.get("status"),
                    "batch_id": e.get("batch_id")
                } for e in exams
            ],
            "submissions_data": [
                {
                    "submission_id": s.get("submission_id"),
                    "student_name": s.get("student_name"),
                    "student_id": s.get("student_id"),
                    "exam_id": s.get("exam_id"),
                    "total_score": s.get("total_score"),
                    "percentage": s.get("percentage"),
                    "status": s.get("status"),
                    "question_scores": s.get("question_scores", [])
                } for s in submissions
            ],
            "students_list": [
                {
                    "user_id": st.get("user_id"),
                    "name": st.get("name"),
                    "email": st.get("email")
                } for st in students
            ]
        }
        
        # Build AI prompt
        prompt = f"""You are an AI analytics assistant for a teacher using GradeSense (an AI-powered grading platform).

The teacher asked: "{query}"

Here's the data context:
- Total Exams: {context_data['total_exams']}
- Total Students: {context_data['total_students']}
- Total Submissions: {context_data['total_submissions']}
- Total Batches: {context_data['total_batches']}

Submissions Data Summary:
{context_data['submissions_data'][:50] if len(submissions) > 0 else "No submissions yet"}

Analyze this data and provide a comprehensive answer to the teacher's question.

IMPORTANT RESPONSE FORMAT:
1. If the query asks for a LIST (e.g., "list students who...", "which students...", "show me students..."):
   Return JSON: {{"type": "list", "title": "descriptive title", "items": [list of items], "description": "brief explanation"}}

2. If the query asks for a CHART/GRAPH/HISTOGRAM/VISUALIZATION:
   Return JSON: {{"type": "chart", "chart_type": "bar|line|pie|histogram", "title": "chart title", "data": [chart data], "x_label": "x axis label", "y_label": "y axis label", "description": "brief explanation"}}
   
   For histogram: data should be [{{"range": "0-10", "count": 5}}, {{"range": "11-20", "count": 8}}, ...]
   For bar chart: data should be [{{"name": "label", "value": number}}, ...]
   For line chart: data should be [{{"x": "label", "y": number}}, ...]
   For pie chart: data should be [{{"name": "label", "value": number}}, ...]

3. If the query asks for a NUMBER/STATISTIC (e.g., "how many...", "what percentage..."):
   Return JSON: {{"type": "number", "value": number, "label": "what this number represents", "description": "additional context"}}

4. If the query asks for COMPARISON or ANALYSIS (general question):
   Return JSON: {{"type": "text", "response": "detailed text response", "key_points": ["point 1", "point 2", ...]}}

5. If you need to show multiple types of data:
   Return JSON: {{"type": "multi", "components": [component1, component2, ...]}}

IMPORTANT: 
- Always use actual data from the submissions_data
- Be specific with student names, scores, and details
- If data is insufficient, mention it clearly
- Format numbers properly (e.g., percentages with 1 decimal place)
- For student lists, include their scores/performance metrics
- Return ONLY valid JSON, no extra text

Now analyze and respond:"""

        # Call AI
        from emergentintegrations.llm.chat import LlmChat, UserMessage
        
        api_key = os.environ.get('EMERGENT_LLM_KEY')
        if not api_key:
            raise HTTPException(status_code=500, detail="AI service not configured")
        
        chat = LlmChat(
            api_key=api_key,
            session_id=f"analytics_{uuid.uuid4().hex[:8]}",
            system_message="""You are an AI analytics assistant for a teacher using GradeSense (an AI-powered grading platform).

Your job is to analyze student performance data and provide helpful insights to teachers.

CRITICAL: You MUST respond in valid JSON format matching one of these types:
- {"type": "text", "response": "...", "key_points": ["...", "..."]}
- {"type": "number", "value": 123, "label": "...", "description": "..."}
- {"type": "list", "title": "...", "items": [...], "description": "..."}
- {"type": "chart", "chart_type": "bar|line|pie|histogram", "title": "...", "data": [...], "x_label": "...", "y_label": "...", "description": "..."}

Always use actual data from the context provided. Be specific with student names and scores."""
        ).with_model("gemini", "gemini-2.5-flash").with_params(temperature=0.3)
        
        user_msg = UserMessage(text=prompt)
        
        response = await chat.send_message(user_msg)
        ai_response_text = response.strip() if response else "{}"
        
        # Parse AI response
        import json
        
        # Try to extract JSON if wrapped in markdown
        if "```json" in ai_response_text:
            ai_response_text = ai_response_text.split("```json")[1].split("```")[0].strip()
        elif "```" in ai_response_text:
            ai_response_text = ai_response_text.split("```")[1].split("```")[0].strip()
        
        try:
            result = json.loads(ai_response_text)
        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse AI response: {ai_response_text}")
            result = {
                "type": "text",
                "response": ai_response_text
            }
        
        return result
        
    except Exception as e:
        logger.error(f"Error in AI analytics: {str(e)}", exc_info=True)
        return {
            "type": "error",
            "message": f"Sorry, I couldn't process your question. Error: {str(e)}"
        }



# ============== PHASE 2: ADVANCED AI METRICS ==============

@api_router.get("/analytics/bluff-index")
async def get_bluff_index(
    exam_id: str,
    user: User = Depends(get_current_user)
):
    """
    Bluff Index: Detect answers that are long but contain low semantic relevance
    Uses AI to identify students who are 'guessing' rather than 'knowing'
    """
    if user.role != "teacher":
        raise HTTPException(status_code=403, detail="Teacher only")
    
    # Get exam
    exam = await db.exams.find_one(
        {"exam_id": exam_id, "teacher_id": user.user_id},
        {"_id": 0}
    )
    if not exam:
        raise HTTPException(status_code=404, detail="Exam not found")
    
    # Get all submissions
    submissions = await db.submissions.find(
        {"exam_id": exam_id},
        {"_id": 0, "student_id": 1, "student_name": 1, "question_scores": 1}
    ).to_list(1000)
    
    logger.info(f"Analyzing bluff index for {len(submissions)} submissions")
    
    # Analyze each answer for semantic relevance
    bluff_candidates = []
    
    for submission in submissions:
        student_bluff_score = 0
        suspicious_answers = []
        
        for qs in submission.get("question_scores", []):
            answer_text = qs.get("answer_text", "")
            feedback = qs.get("ai_feedback", "")
            obtained = qs.get("obtained_marks", 0)
            max_marks = qs.get("max_marks", 1)
            percentage = (obtained / max_marks) * 100 if max_marks > 0 else 0
            
            # Heuristic: Long answer (>100 chars) but low score (<40%)
            if len(answer_text) > 100 and percentage < 40:
                # Use AI feedback to determine if it's bluffing
                if any(keyword in feedback.lower() for keyword in [
                    "irrelevant", "off-topic", "does not answer", "incorrect approach",
                    "vague", "unclear", "lacks understanding", "superficial"
                ]):
                    student_bluff_score += 1
                    suspicious_answers.append({
                        "question_number": qs.get("question_number"),
                        "answer_length": len(answer_text),
                        "score_percentage": round(percentage, 1),
                        "feedback_snippet": feedback[:150]
                    })
        
        # If student has 2+ suspicious answers, add to bluff candidates
        if student_bluff_score >= 2:
            bluff_candidates.append({
                "student_id": submission["student_id"],
                "student_name": submission["student_name"],
                "bluff_score": student_bluff_score,
                "suspicious_answers": suspicious_answers
            })
    
    # Sort by bluff score
    bluff_candidates.sort(key=lambda x: x["bluff_score"], reverse=True)
    
    return {
        "exam_id": exam_id,
        "exam_name": exam.get("exam_name", "Unknown"),
        "total_students": len(submissions),
        "bluff_candidates": bluff_candidates,
        "summary": f"Found {len(bluff_candidates)} students with potential bluffing patterns (long answers with low relevance)"
    }


@api_router.get("/analytics/syllabus-coverage")
async def get_syllabus_coverage(
    batch_id: Optional[str] = None,
    subject_id: Optional[str] = None,
    user: User = Depends(get_current_user)
):
    """
    Syllabus Coverage Heatmap: Shows which topics have been tested and results
    Helps teachers identify gaps in assessment coverage
    """
    if user.role != "teacher":
        raise HTTPException(status_code=403, detail="Teacher only")
    
    # Get all exams for this teacher
    exam_query = {"teacher_id": user.user_id}
    if batch_id:
        exam_query["batch_id"] = batch_id
    if subject_id:
        exam_query["subject_id"] = subject_id
    
    exams = await db.exams.find(exam_query, {"_id": 0}).to_list(100)
    
    if not exams:
        return {
            "tested_topics": [],
            "untested_topics": [],
            "coverage_percentage": 0,
            "topic_heatmap": []
        }
    
    # Get subject to identify full syllabus
    subject = None
    if subject_id:
        subject = await db.subjects.find_one({"subject_id": subject_id}, {"_id": 0})
    
    # Collect all tested topics
    tested_topics = {}
    
    for exam in exams:
        exam_id = exam["exam_id"]
        
        # Get submissions for this exam
        submissions = await db.submissions.find(
            {"exam_id": exam_id},
            {"_id": 0, "question_scores": 1}
        ).to_list(1000)
        
        for question in exam.get("questions", []):
            topics = question.get("topic_tags", [])
            if not topics:
                topics = [subject.get("name", "General")] if subject else ["General"]
            
            q_num = question.get("question_number")
            
            for topic in topics:
                if topic not in tested_topics:
                    tested_topics[topic] = {
                        "exam_count": 0,
                        "question_count": 0,
                        "total_scores": [],
                        "last_tested": None
                    }
                
                tested_topics[topic]["exam_count"] += 1
                tested_topics[topic]["question_count"] += 1
                tested_topics[topic]["last_tested"] = exam.get("created_at", "")
                
                # Collect scores
                for sub in submissions:
                    for qs in sub.get("question_scores", []):
                        if qs.get("question_number") == q_num:
                            percentage = (qs["obtained_marks"] / qs["max_marks"]) * 100 if qs.get("max_marks", 0) > 0 else 0
                            tested_topics[topic]["total_scores"].append(percentage)
    
    # Calculate coverage heatmap
    topic_heatmap = []
    for topic, data in tested_topics.items():
        avg_score = sum(data["total_scores"]) / len(data["total_scores"]) if data["total_scores"] else 0
        
        color = "grey"  # Not tested
        if avg_score > 0:
            if avg_score >= 70:
                color = "green"
            elif avg_score >= 50:
                color = "amber"
            else:
                color = "red"
        
        topic_heatmap.append({
            "topic": topic,
            "status": "tested",
            "exam_count": data["exam_count"],
            "question_count": data["question_count"],
            "avg_score": round(avg_score, 1),
            "last_tested": data["last_tested"],
            "color": color
        })
    
    # TODO: Identify untested topics (would require a predefined syllabus structure)
    # For now, we show only tested topics
    
    coverage_percentage = 100  # Assuming all tested topics = 100% coverage
    
    return {
        "subject": subject.get("name") if subject else "All Subjects",
        "total_exams": len(exams),
        "tested_topics": sorted(topic_heatmap, key=lambda x: x["avg_score"]),
        "untested_topics": [],  # Placeholder for future enhancement
        "coverage_percentage": coverage_percentage,
        "summary": f"Assessed {len(tested_topics)} topics across {len(exams)} exams"
    }


@api_router.get("/analytics/peer-groups")
async def get_peer_group_suggestions(
    batch_id: str,
    user: User = Depends(get_current_user)
):
    """
    Peer Groups: Auto-suggest study pairs based on complementary strengths/weaknesses
    E.g., Student A (strong in Algebra, weak in Geometry) + Student B (strong in Geometry, weak in Algebra)
    """
    if user.role != "teacher":
        raise HTTPException(status_code=403, detail="Teacher only")
    
    # Get all students in batch
    batch = await db.batches.find_one({"batch_id": batch_id}, {"_id": 0})
    if not batch:
        raise HTTPException(status_code=404, detail="Batch not found")
    
    student_ids = batch.get("students", [])
    
    if len(student_ids) < 2:
        return {
            "suggestions": [],
            "summary": "Need at least 2 students to form peer groups"
        }
    
    # Get all students' performance data
    student_profiles = {}
    
    for student_id in student_ids:
        student = await db.users.find_one({"user_id": student_id}, {"_id": 0, "name": 1})
        if not student:
            continue
        
        # Get submissions
        submissions = await db.submissions.find(
            {"student_id": student_id},
            {"_id": 0, "exam_id": 1, "question_scores": 1}
        ).to_list(1000)
        
        if not submissions:
            continue
        
        # Build topic performance profile
        topic_performance = {}
        
        for sub in submissions:
            exam = await db.exams.find_one({"exam_id": sub["exam_id"]}, {"_id": 0, "questions": 1})
            if not exam:
                continue
            
            question_topics = {}
            for q in exam.get("questions", []):
                topics = q.get("topic_tags", ["General"])
                question_topics[q.get("question_number")] = topics
            
            for qs in sub.get("question_scores", []):
                q_num = qs.get("question_number")
                topics = question_topics.get(q_num, ["General"])
                percentage = (qs["obtained_marks"] / qs["max_marks"]) * 100 if qs.get("max_marks", 0) > 0 else 0
                
                for topic in topics:
                    if topic not in topic_performance:
                        topic_performance[topic] = []
                    topic_performance[topic].append(percentage)
        
        # Calculate averages
        strengths = []
        weaknesses = []
        
        for topic, scores in topic_performance.items():
            avg = sum(scores) / len(scores)
            if avg >= 70:
                strengths.append(topic)
            elif avg < 50:
                weaknesses.append(topic)
        
        student_profiles[student_id] = {
            "name": student.get("name", "Unknown"),
            "strengths": strengths,
            "weaknesses": weaknesses
        }
    
    # Find complementary pairs
    suggestions = []
    processed_pairs = set()
    
    for sid1, profile1 in student_profiles.items():
        for sid2, profile2 in student_profiles.items():
            if sid1 >= sid2:  # Avoid duplicates and self-pairing
                continue
            
            pair_key = tuple(sorted([sid1, sid2]))
            if pair_key in processed_pairs:
                continue
            
            # Check for complementary strengths/weaknesses
            # Student 1's strength matches Student 2's weakness
            complementary_topics = []
            
            for strength in profile1["strengths"]:
                if strength in profile2["weaknesses"]:
                    complementary_topics.append({
                        "topic": strength,
                        "helper": profile1["name"],
                        "learner": profile2["name"]
                    })
            
            for strength in profile2["strengths"]:
                if strength in profile1["weaknesses"]:
                    complementary_topics.append({
                        "topic": strength,
                        "helper": profile2["name"],
                        "learner": profile1["name"]
                    })
            
            # If they have 2+ complementary topics, suggest pairing
            if len(complementary_topics) >= 2:
                suggestions.append({
                    "student1": {
                        "id": sid1,
                        "name": profile1["name"],
                        "strengths": profile1["strengths"],
                        "weaknesses": profile1["weaknesses"]
                    },
                    "student2": {
                        "id": sid2,
                        "name": profile2["name"],
                        "strengths": profile2["strengths"],
                        "weaknesses": profile2["weaknesses"]
                    },
                    "complementary_topics": complementary_topics,
                    "synergy_score": len(complementary_topics)
                })
                processed_pairs.add(pair_key)
    
    # Sort by synergy score
    suggestions.sort(key=lambda x: x["synergy_score"], reverse=True)
    
    return {
        "batch_id": batch_id,
        "batch_name": batch.get("name", "Unknown"),
        "total_students": len(student_profiles),
        "suggestions": suggestions[:10],  # Top 10 pairs
        "summary": f"Found {len(suggestions)} potential study pairs with complementary skills"
    }


@api_router.post("/analytics/send-peer-group-email")
async def send_peer_group_email(
    student1_id: str,
    student2_id: str,
    message: str,
    user: User = Depends(get_current_user)
):
    """
    Send email notification to suggested peer group
    Placeholder for future email integration
    """
    if user.role != "teacher":
        raise HTTPException(status_code=403, detail="Teacher only")
    
    # Get student emails
    student1 = await db.users.find_one({"user_id": student1_id}, {"_id": 0, "email": 1, "name": 1})
    student2 = await db.users.find_one({"user_id": student2_id}, {"_id": 0, "email": 1, "name": 1})
    
    if not student1 or not student2:
        raise HTTPException(status_code=404, detail="Students not found")
    
    # TODO: Integrate with email service (SendGrid, Resend, etc.)
    # For now, create a notification
    
    await create_notification(
        user_id=student1_id,
        notification_type="peer_group_suggestion",
        title="Study Partner Suggestion",
        message=f"Your teacher suggests studying with {student2.get('name')}. {message}"
    )
    
    await create_notification(
        user_id=student2_id,
        notification_type="peer_group_suggestion",
        title="Study Partner Suggestion",
        message=f"Your teacher suggests studying with {student1.get('name')}. {message}"
    )
    
    return {
        "success": True,
        "message": "Notifications sent to both students"
    }


# ============== NATURAL LANGUAGE QUERY (Ask Your Data) ==============

class NaturalLanguageQuery(BaseModel):
    query: str
    batch_id: Optional[str] = None
    exam_id: Optional[str] = None
    subject_id: Optional[str] = None

@api_router.post("/analytics/ask")
async def ask_your_data(
    request: NaturalLanguageQuery,
    user: User = Depends(get_current_user)
):
    """
    Natural Language Query: Ask questions in plain English and get visualizations
    Examples:
    - "Show me top 5 students in Math"
    - "Compare performance of boys vs girls"
    - "Who failed in Question 3?"
    """
    if user.role != "teacher":
        raise HTTPException(status_code=403, detail="Teacher only")
    
    logger.info(f"NL Query: '{request.query}' from user {user.user_id}")
    
    # Step 1: Get context data for the teacher
    context_data = {}
    
    # Get teacher's batches
    batches = await db.batches.find({"teacher_id": user.user_id}, {"_id": 0, "batch_id": 1, "name": 1}).to_list(100)
    context_data["batches"] = [{"id": b["batch_id"], "name": b["name"]} for b in batches]
    
    # Get teacher's subjects
    subjects = await db.subjects.find({"teacher_id": user.user_id}, {"_id": 0, "subject_id": 1, "name": 1}).to_list(100)
    context_data["subjects"] = [{"id": s["subject_id"], "name": s["name"]} for s in subjects]
    
    # Get teacher's exams
    exam_query = {"teacher_id": user.user_id}
    if request.batch_id:
        exam_query["batch_id"] = request.batch_id
    if request.exam_id:
        exam_query["exam_id"] = request.exam_id
    if request.subject_id:
        exam_query["subject_id"] = request.subject_id
    
    exams = await db.exams.find(exam_query, {"_id": 0, "exam_id": 1, "exam_name": 1, "batch_id": 1, "subject_id": 1}).to_list(100)
    context_data["exams"] = [{"id": e["exam_id"], "name": e["exam_name"]} for e in exams]
    
    # Get submissions for context
    exam_ids = [e["exam_id"] for e in exams]
    if not exam_ids:
        return {
            "type": "error",
            "message": "No exams found. Please create and grade some exams first."
        }
    
    submissions = await db.submissions.find(
        {"exam_id": {"$in": exam_ids}},
        {"_id": 0, "submission_id": 1, "exam_id": 1, "student_id": 1, "student_name": 1, "total_score": 1, "percentage": 1, "question_scores": 1}
    ).to_list(1000)
    
    context_data["total_submissions"] = len(submissions)
    context_data["total_students"] = len(set(s["student_id"] for s in submissions))
    
    # Step 2: Use AI to parse the query and determine what data to fetch
    try:
        from emergentintegrations.llm.chat import LlmChat, UserMessage
        
        prompt = f"""
You are a data analyst for a teacher. Parse the natural language query and return a structured JSON response.

Teacher's Context:
- Batches: {', '.join([b['name'] for b in context_data['batches']])}
- Subjects: {', '.join([s['name'] for s in context_data['subjects']])}
- Total Students: {context_data['total_students']}
- Total Submissions: {context_data['total_submissions']}

Teacher's Query: "{request.query}"

Your task:
1. Understand the intent
2. Determine what data to show
3. Choose the best visualization type
4. Return ONLY valid JSON (no markdown, no explanations)

Available chart types: "bar", "table", "pie", "comparison"

Response Format (JSON only, no markdown):
{{
    "intent": "show_top_students | compare_groups | show_failures | show_distribution | other",
    "chart_type": "bar | table | pie | comparison",
    "data_query": {{
        "entity": "students | questions | topics",
        "filter": {{
            "subject": "Math" (if mentioned),
            "question_number": 3 (if mentioned),
            "performance": "failed | passed | top" (if mentioned)
        }},
        "group_by": "batch | gender | topic" (if comparison),
        "limit": 5 (if top N mentioned)
    }},
    "chart_config": {{
        "title": "Top 5 Students in Math",
        "xAxis": "student_name",
        "yAxis": "score",
        "description": "Brief explanation of what this shows"
    }}
}}

If the query is unclear or impossible to answer, return:
{{
    "intent": "error",
    "chart_type": "error",
    "message": "Explanation of why this cannot be answered"
}}
"""
        
        chat = LlmChat(
            api_key=os.environ.get("EMERGENT_LLM_KEY"),
            session_id=f"nl_query_{uuid.uuid4().hex[:8]}",
            system_message="You are a precise data analyst. Return ONLY valid JSON, no markdown formatting."
        ).with_model("gemini", "gemini-2.5-flash").with_params(temperature=0)
        
        user_message = UserMessage(text=prompt)
        response = await chat.send_message(user_message)
        response_text = response.strip()
        
        # Clean response
        import re
        response_text = re.sub(r'^```json\s*', '', response_text)
        response_text = re.sub(r'\s*```$', '', response_text)
        
        query_intent = json.loads(response_text)
        
        # Handle error from AI
        if query_intent.get("intent") == "error":
            return {
                "type": "error",
                "message": query_intent.get("message", "Could not understand the query")
            }
        
    except Exception as e:
        logger.error(f"Error parsing NL query with AI: {e}")
        return {
            "type": "error",
            "message": f"Failed to parse query: {str(e)}"
        }
    
    # Step 3: Fetch actual data based on AI's interpretation
    try:
        data_query = query_intent.get("data_query", {})
        entity = data_query.get("entity", "students")
        filters = data_query.get("filter", {})
        limit = data_query.get("limit", 10)
        
        result_data = []
        
        # Query: Top/Bottom Students
        if entity == "students":
            # Filter submissions
            filtered_submissions = submissions
            
            # Filter by subject if mentioned
            if "subject" in filters:
                subject_name = filters["subject"]
                subject_doc = await db.subjects.find_one(
                    {"name": {"$regex": subject_name, "$options": "i"}, "teacher_id": user.user_id},
                    {"_id": 0, "subject_id": 1}
                )
                if subject_doc:
                    subject_exams = [e["exam_id"] for e in exams if e.get("subject_id") == subject_doc["subject_id"]]
                    filtered_submissions = [s for s in filtered_submissions if s["exam_id"] in subject_exams]
            
            # Filter by performance
            if filters.get("performance") == "failed":
                filtered_submissions = [s for s in filtered_submissions if s["percentage"] < 50]
            elif filters.get("performance") == "passed":
                filtered_submissions = [s for s in filtered_submissions if s["percentage"] >= 50]
            elif filters.get("performance") == "top":
                filtered_submissions = sorted(filtered_submissions, key=lambda x: x["percentage"], reverse=True)[:limit]
            
            # Aggregate by student
            student_aggregates = {}
            for sub in filtered_submissions:
                sid = sub["student_id"]
                if sid not in student_aggregates:
                    student_aggregates[sid] = {
                        "student_name": sub["student_name"],
                        "total_score": 0,
                        "count": 0,
                        "percentages": []
                    }
                student_aggregates[sid]["total_score"] += sub["total_score"]
                student_aggregates[sid]["count"] += 1
                student_aggregates[sid]["percentages"].append(sub["percentage"])
            
            # Calculate averages
            for sid, data in student_aggregates.items():
                avg_percentage = sum(data["percentages"]) / len(data["percentages"]) if data["percentages"] else 0
                result_data.append({
                    "student_name": data["student_name"],
                    "avg_score": round(avg_percentage, 1),
                    "exams_taken": data["count"]
                })
            
            # Sort and limit
            result_data = sorted(result_data, key=lambda x: x["avg_score"], reverse=True)[:limit]
        
        # Query: Question Analysis
        elif entity == "questions":
            question_num = filters.get("question_number")
            
            if question_num:
                # Get all answers for this question
                question_data = []
                for sub in submissions:
                    for qs in sub.get("question_scores", []):
                        if qs.get("question_number") == question_num:
                            percentage = (qs["obtained_marks"] / qs["max_marks"]) * 100 if qs.get("max_marks", 0) > 0 else 0
                            
                            # Filter by performance
                            if filters.get("performance") == "failed" and percentage >= 50:
                                continue
                            
                            question_data.append({
                                "student_name": sub["student_name"],
                                "score": qs["obtained_marks"],
                                "max_marks": qs["max_marks"],
                                "percentage": round(percentage, 1)
                            })
                
                result_data = sorted(question_data, key=lambda x: x["percentage"])[:limit]
        
        # Query: Topic Performance
        elif entity == "topics":
            # Aggregate by topic
            topic_performance = {}
            
            for sub in submissions:
                exam = await db.exams.find_one({"exam_id": sub["exam_id"]}, {"_id": 0, "questions": 1})
                if not exam:
                    continue
                
                question_topics = {}
                for q in exam.get("questions", []):
                    topics = q.get("topic_tags", ["General"])
                    question_topics[q.get("question_number")] = topics
                
                for qs in sub.get("question_scores", []):
                    q_num = qs.get("question_number")
                    topics = question_topics.get(q_num, ["General"])
                    percentage = (qs["obtained_marks"] / qs["max_marks"]) * 100 if qs.get("max_marks", 0) > 0 else 0
                    
                    for topic in topics:
                        if topic not in topic_performance:
                            topic_performance[topic] = []
                        topic_performance[topic].append(percentage)
            
            # Calculate averages
            for topic, scores in topic_performance.items():
                avg = sum(scores) / len(scores) if scores else 0
                result_data.append({
                    "topic": topic,
                    "avg_score": round(avg, 1),
                    "sample_size": len(scores)
                })
            
            result_data = sorted(result_data, key=lambda x: x["avg_score"], reverse=True)[:limit]
        
        # Step 4: Return structured response
        chart_config = query_intent.get("chart_config", {})
        
        return {
            "type": query_intent.get("chart_type", "table"),
            "title": chart_config.get("title", "Query Results"),
            "description": chart_config.get("description", ""),
            "xAxis": chart_config.get("xAxis", "name"),
            "yAxis": chart_config.get("yAxis", "value"),
            "data": result_data,
            "query_intent": query_intent.get("intent", "unknown")
        }
        
    except Exception as e:
        logger.error(f"Error executing data query: {e}")
        return {
            "type": "error",
            "message": f"Failed to fetch data: {str(e)}"
        }




# ============== STUDY MATERIALS (STUDENT) ==============

@api_router.get("/study-materials")
async def get_study_materials(subject_id: Optional[str] = None, user: User = Depends(get_current_user)):
    """Get study material recommendations based on weak areas"""
    # Get recent weak areas
    submissions = await db.submissions.find(
        {"student_id": user.user_id},
        {"_id": 0, "question_scores": 1, "exam_id": 1}
    ).sort("created_at", -1).limit(5).to_list(5)
    
    weak_topics = []
    for sub in submissions:
        exam = await db.exams.find_one({"exam_id": sub["exam_id"]}, {"_id": 0, "subject_id": 1})
        subject = await db.subjects.find_one({"subject_id": exam.get("subject_id")}, {"_id": 0, "name": 1}) if exam else None
        subj_name = subject.get("name", "General") if subject else "General"
        
        for qs in sub.get("question_scores", []):
            pct = (qs["obtained_marks"] / qs["max_marks"]) * 100 if qs["max_marks"] > 0 else 0
            if pct < 50:
                weak_topics.append({
                    "subject": subj_name,
                    "question": f"Q{qs['question_number']}",
                    "score": f"{pct:.0f}%"
                })
    
    # Generate recommendations based on weak areas
    materials = [
        {
            "title": "Practice Problems",
            "description": "Work through similar problems to strengthen weak areas",
            "type": "practice"
        },
        {
            "title": "Concept Review",
            "description": "Review fundamental concepts related to questions you struggled with",
            "type": "theory"
        },
        {
            "title": "Video Tutorials",
            "description": "Watch explanatory videos for complex topics",
            "type": "video"
        }
    ]
    
    return {
        "weak_topics": weak_topics[:10],
        "recommended_materials": materials
    }

# ============== FEEDBACK ROUTES ==============

@api_router.post("/feedback/submit")
async def submit_grading_feedback(feedback: FeedbackSubmit, user: User = Depends(get_current_user)):
    """Submit feedback to improve AI grading"""
    if user.role != "teacher":
        raise HTTPException(status_code=403, detail="Only teachers can submit feedback")
    
    feedback_id = f"feedback_{uuid.uuid4().hex[:8]}"
    
    # Get additional context if submission_id provided
    exam_id = None
    grading_mode = None
    student_answer_summary = None
    
    if feedback.submission_id:
        submission = await db.submissions.find_one(
            {"submission_id": feedback.submission_id},
            {"_id": 0, "exam_id": 1, "question_scores": 1}
        )
        if submission:
            exam_id = submission.get("exam_id")
            # Get question context
            if feedback.question_number:
                qs = next((q for q in submission.get("question_scores", []) 
                          if q["question_number"] == feedback.question_number), None)
                if qs:
                    student_answer_summary = qs.get("ai_feedback", "")[:200]
            
            # Get grading mode and subject
            exam = await db.exams.find_one({"exam_id": exam_id}, {"_id": 0, "grading_mode": 1, "subject_id": 1})
            if exam:
                grading_mode = exam.get("grading_mode")
                subject_id = exam.get("subject_id", "unknown")
            else:
                subject_id = "unknown"
    
    feedback_doc = {
        "feedback_id": feedback_id,
        "teacher_id": user.user_id,
        "submission_id": feedback.submission_id,
        "exam_id": feedback.exam_id or exam_id,
        "subject_id": subject_id,  # NEW: For cross-exam learning
        "question_number": feedback.question_number,
        "sub_question_id": feedback.sub_question_id,  # New field
        "feedback_type": feedback.feedback_type,
        "question_text": feedback.question_text,
        "question_topic": feedback.question_topic,  # NEW: Pattern matching
        "student_answer_summary": student_answer_summary,
        "ai_grade": feedback.ai_grade,
        "ai_feedback": feedback.ai_feedback,
        "teacher_expected_grade": feedback.teacher_expected_grade,
        "teacher_correction": feedback.teacher_correction,
        "grading_mode": grading_mode,
        "is_common": False,
        "upvote_count": 0,
        "created_at": datetime.now(timezone.utc)
    }
    
    await db.grading_feedback.insert_one(feedback_doc)
    
    return {
        "message": "Feedback submitted successfully",
        "feedback_id": feedback_id,
        "exam_id": exam_id
    }

@api_router.post("/feedback/{feedback_id}/apply-to-batch")
async def apply_feedback_to_batch(
    feedback_id: str,
    user: User = Depends(get_current_user)
):
    """Re-grade a specific question across all submissions in the batch based on teacher feedback"""
    if user.role != "teacher":
        raise HTTPException(status_code=403, detail="Only teachers can apply feedback")
    
    # Get the feedback
    feedback = await db.grading_feedback.find_one({"feedback_id": feedback_id}, {"_id": 0})
    if not feedback:
        raise HTTPException(status_code=404, detail="Feedback not found")
    
    exam_id = feedback.get("exam_id")
    question_number = feedback.get("question_number")
    teacher_correction = feedback.get("teacher_correction")
    
    if not exam_id or not question_number:
        raise HTTPException(status_code=400, detail="Missing exam_id or question_number in feedback")
    
    # Find all AI-graded submissions for this exam
    submissions = await db.submissions.find(
        {"exam_id": exam_id, "status": "ai_graded"},
        {"_id": 0, "submission_id": 1, "question_scores": 1, "file_images": 1}
    ).to_list(1000)
    
    if not submissions:
        return {"message": "No submissions to re-grade", "updated_count": 0}
    
    # Get exam and question details
    exam = await db.exams.find_one({"exam_id": exam_id}, {"_id": 0})
    if not exam:
        raise HTTPException(status_code=404, detail="Exam not found")
    
    # Get question from questions collection
    question = await db.questions.find_one(
        {"exam_id": exam_id, "question_number": question_number},
        {"_id": 0}
    )
    
    if not question:
        # Fallback to exam questions array
        question = next((q for q in exam.get("questions", []) if q.get("question_number") == question_number), None)
    
    if not question:
        raise HTTPException(status_code=404, detail=f"Question {question_number} not found")
    
    # Get model answer text
    model_answer_text = await get_exam_model_answer_text(exam_id)
    
    updated_count = 0
    
    # Re-grade this question for each submission
    for submission in submissions:
        try:
            # Find the question score
            question_scores = submission.get("question_scores", [])
            q_score = next((qs for qs in question_scores if qs.get("question_number") == question_number), None)
            
            if not q_score:
                continue  # Question not found in this submission
            
            # Get student images
            student_images = submission.get("file_images", [])
            if not student_images:
                continue
            
            # Create enhanced prompt with teacher's correction
            enhanced_prompt = f"""# RE-GRADING TASK - Question {question_number}

## TEACHER'S CORRECTION GUIDANCE
{teacher_correction}

## QUESTION DETAILS
Question {question_number}: {question.get('rubric', '')}
Maximum Marks: {question.get('max_marks')}

## MODEL ANSWER REFERENCE
{model_answer_text[:5000] if model_answer_text else "No model answer available"}

## TASK
Re-grade ONLY Question {question_number} based on the teacher's correction guidance above.
Apply the same grading standard the teacher expects.

## OUTPUT FORMAT
Return JSON:
{{
  "question_number": {question_number},
  "obtained_marks": <marks>,
  "ai_feedback": "<detailed feedback>",
  "sub_scores": []
}}
"""
            
            # Call AI to re-grade just this question
            from emergentintegrations import LlmChat, UserMessage, ImageContent
            
            api_key = os.getenv("EMERGENT_LLM_KEY")
            chat = LlmChat(
                api_key=api_key,
                session_id=f"regrade_{submission['submission_id']}_{question_number}",
                system_message="You are an expert grader. Re-grade this specific question based on teacher's guidance."
            ).with_model("gemini", "gemini-2.5-flash").with_params(temperature=0)
            
            image_objs = [ImageContent(image_base64=img) for img in student_images[:10]]
            user_msg = UserMessage(text=enhanced_prompt, file_contents=image_objs)
            
            response = await chat.send_message(user_msg)
            
            # Parse response
            import json
            import re
            resp_text = response.strip()
            
            # Try to extract JSON
            new_score = None
            if resp_text.startswith("```"):
                resp_text = resp_text.split("```")[1]
                if resp_text.startswith("json"):
                    resp_text = resp_text[4:]
                resp_text = resp_text.strip()
            
            try:
                result = json.loads(resp_text)
                new_score = result
            except:
                # Try regex extraction
                json_match = re.search(r'\{[^{}]*"question_number"[^{}]*\}', resp_text, re.DOTALL)
                if json_match:
                    result = json.loads(json_match.group())
                    new_score = result
            
            if new_score and "obtained_marks" in new_score:
                # Update this question's score
                for qs in question_scores:
                    if qs.get("question_number") == question_number:
                        qs["obtained_marks"] = new_score["obtained_marks"]
                        qs["ai_feedback"] = new_score.get("ai_feedback", qs["ai_feedback"])
                        if "sub_scores" in new_score:
                            qs["sub_scores"] = new_score["sub_scores"]
                        break
                
                # Recalculate total score
                total_score = sum(qs.get("obtained_marks", 0) for qs in question_scores)
                
                # Update submission
                await db.submissions.update_one(
                    {"submission_id": submission["submission_id"]},
                    {"$set": {
                        "question_scores": question_scores,
                        "total_score": total_score,
                        "updated_at": datetime.now(timezone.utc).isoformat()
                    }}
                )
                
                updated_count += 1
                logger.info(f"Re-graded Q{question_number} for submission {submission['submission_id']}")
        
        except Exception as e:
            logger.error(f"Error re-grading submission {submission['submission_id']}: {e}")
            continue
    
    return {
        "message": f"Successfully re-graded question {question_number} for {updated_count} submissions",
        "updated_count": updated_count,
        "total_submissions": len(submissions)
    }





@api_router.post("/feedback/{feedback_id}/apply-to-all-papers")
async def apply_feedback_to_all_papers(
    feedback_id: str,
    user: User = Depends(get_current_user)
):
    """
    Intelligent re-grading: Uses teacher's feedback to re-analyze each student's answer via AI
    Applies grading criteria individually for fair, differentiated grading
    """
    if user.role != "teacher":
        raise HTTPException(status_code=403, detail="Only teachers can apply corrections")
    
    # Get the feedback
    feedback = await db.grading_feedback.find_one({"feedback_id": feedback_id}, {"_id": 0})
    if not feedback:
        raise HTTPException(status_code=404, detail="Feedback not found")
    
    exam_id = feedback.get("exam_id")
    question_number = feedback.get("question_number")
    sub_question_id = feedback.get("sub_question_id")
    teacher_expected_grade = feedback.get("teacher_expected_grade")
    teacher_correction = feedback.get("teacher_correction")
    
    if not exam_id or not question_number:
        raise HTTPException(status_code=400, detail="Missing exam_id or question_number")
    
    # Get exam details
    exam = await db.exams.find_one({"exam_id": exam_id}, {"_id": 0})
    if not exam:
        raise HTTPException(status_code=404, detail="Exam not found")
    
    # Get question details from questions collection
    question = await db.questions.find_one(
        {"exam_id": exam_id, "question_number": question_number},
        {"_id": 0}
    )
    
    if not question:
        # Fallback to exam questions array
        question = next((q for q in exam.get("questions", []) if q.get("question_number") == question_number), None)
    
    if not question:
        raise HTTPException(status_code=404, detail=f"Question {question_number} not found")
    
    # Get model answer text for reference
    model_answer_text = await get_exam_model_answer_text(exam_id)
    
    # Find all submissions for this exam
    submissions = await db.submissions.find(
        {"exam_id": exam_id},
        {"_id": 0, "submission_id": 1, "student_name": 1, "question_scores": 1, "file_images": 1, "total_score": 1}
    ).to_list(1000)
    
    if not submissions:
        return {"message": "No submissions found", "updated_count": 0}
    
    updated_count = 0
    failed_count = 0
    
    logger.info(f"Starting intelligent re-grading for {len(submissions)} papers - Question {question_number}" + 
                (f" Sub-question {sub_question_id}" if sub_question_id and sub_question_id != "all" else ""))
    
    # Process each submission with AI re-grading
    for idx, submission in enumerate(submissions):
        try:
            question_scores = submission.get("question_scores", [])
            
            # Find the question
            q_index = next((i for i, qs in enumerate(question_scores) 
                           if qs.get("question_number") == question_number), None)
            
            if q_index is None:
                continue
            
            question_score = question_scores[q_index]
            student_images = submission.get("file_images", [])
            
            if not student_images:
                logger.warning(f"No images for submission {submission['submission_id']}")
                continue
            
            # Determine what to re-grade: sub-question or whole question
            if sub_question_id and sub_question_id != "all":
                # Re-grade specific sub-question
                sub_scores = question_score.get("sub_scores", [])
                sub_index = next((i for i, ss in enumerate(sub_scores) 
                                 if ss.get("sub_id") == sub_question_id), None)
                
                if sub_index is None:
                    continue
                
                old_sub_score = sub_scores[sub_index]
                
                # Get sub-question details
                sub_question = next((sq for sq in question.get("sub_questions", []) 
                                    if sq.get("sub_id") == sub_question_id), None)
                
                if not sub_question:
                    continue
                
                # Create AI prompt for intelligent re-grading
                re_grade_prompt = f"""# INTELLIGENT RE-GRADING TASK

## TEACHER'S GRADING GUIDANCE
{teacher_correction}

## CONTEXT
- Question {question_number}, Part/Sub-question: {sub_question.get('sub_label', 'Part')}
- Maximum Marks: {sub_question.get('max_marks')}
- Sub-question: {sub_question.get('rubric', '')}

## MODEL ANSWER REFERENCE
{model_answer_text[:3000] if model_answer_text else "No model answer available"}

## YOUR TASK
Re-grade this student's answer for the sub-question based on the teacher's guidance above.
Analyze the student's response carefully and apply the grading criteria the teacher expects.
Award marks based on:
1. Understanding demonstrated
2. Key concepts mentioned
3. Correctness of approach
4. Completeness of answer

## IMPORTANT
- Apply the teacher's grading philosophy consistently
- Give partial credit where appropriate
- Be fair and objective

## OUTPUT FORMAT (JSON ONLY)
{{
  "obtained_marks": <marks between 0 and {sub_question.get('max_marks')}>,
  "ai_feedback": "<brief explanation of grading decision>"
}}
"""
                
                # Call AI to re-grade
                from emergentintegrations import LlmChat, UserMessage, ImageContent
                
                chat = LlmChat(
                    api_key=os.environ.get('EMERGENT_LLM_KEY'),
                    provider="gemini",
                    response_format="json_object"
                ).with_model("gemini-2.5-flash").with_params(temperature=0.3)
                
                # Prepare images for this question
                content = [re_grade_prompt]
                for img in student_images[:20]:  # Limit images for API
                    content.append(ImageContent(image=img, detail="low"))
                
                result = await chat.send_message(UserMessage(content=content))
                re_grade_result = json.loads(result.text)
                
                # Update sub-question score
                new_marks = float(re_grade_result.get("obtained_marks", old_sub_score["obtained_marks"]))
                new_feedback = f"[Teacher Re-graded] {re_grade_result.get('ai_feedback', '')}"
                
                sub_scores[sub_index]["obtained_marks"] = new_marks
                sub_scores[sub_index]["ai_feedback"] = new_feedback
                
                # Recalculate question total
                new_question_total = sum(ss.get("obtained_marks", 0) for ss in sub_scores)
                question_scores[q_index]["obtained_marks"] = new_question_total
                question_scores[q_index]["sub_scores"] = sub_scores
                
                # Recalculate submission total
                old_submission_total = submission.get("total_score", 0)
                old_question_total = question_score.get("obtained_marks", 0)
                new_submission_total = old_submission_total - old_question_total + new_question_total
                
                # Update in database
                await db.submissions.update_one(
                    {"submission_id": submission["submission_id"]},
                    {"$set": {
                        "question_scores": question_scores,
                        "total_score": new_submission_total
                    }}
                )
                
                updated_count += 1
                logger.info(f"[{idx+1}/{len(submissions)}] Re-graded {submission['student_name']} - Q{question_number} Part: {new_marks}/{sub_question.get('max_marks')}")
                
            else:
                # Re-grade whole question
                re_grade_prompt = f"""# INTELLIGENT RE-GRADING TASK

## TEACHER'S GRADING GUIDANCE
{teacher_correction}

## CONTEXT
- Question {question_number}
- Maximum Marks: {question.get('max_marks')}
- Question: {question.get('rubric', '')}

## MODEL ANSWER REFERENCE
{model_answer_text[:3000] if model_answer_text else "No model answer available"}

## YOUR TASK
Re-grade this student's entire answer for Question {question_number} based on the teacher's guidance above.
Analyze the student's response carefully and apply the grading criteria the teacher expects.

## IMPORTANT
- Apply the teacher's grading philosophy consistently
- Give partial credit where appropriate
- Be fair and objective
- Consider all aspects of the answer

## OUTPUT FORMAT (JSON ONLY)
{{
  "obtained_marks": <marks between 0 and {question.get('max_marks')}>,
  "ai_feedback": "<brief explanation of grading decision>"
}}
"""
                
                # Call AI to re-grade
                from emergentintegrations import LlmChat, UserMessage, ImageContent
                
                chat = LlmChat(
                    api_key=os.environ.get('EMERGENT_LLM_KEY'),
                    provider="gemini",
                    response_format="json_object"
                ).with_model("gemini-2.5-flash").with_params(temperature=0.3)
                
                # Prepare images
                content = [re_grade_prompt]
                for img in student_images[:20]:
                    content.append(ImageContent(image=img, detail="low"))
                
                result = await chat.send_message(UserMessage(content=content))
                re_grade_result = json.loads(result.text)
                
                # Update question score
                new_marks = float(re_grade_result.get("obtained_marks", question_score.get("obtained_marks", 0)))
                new_feedback = f"[Teacher Re-graded] {re_grade_result.get('ai_feedback', '')}"
                
                question_scores[q_index]["obtained_marks"] = new_marks
                question_scores[q_index]["ai_feedback"] = new_feedback
                
                # Recalculate submission total
                old_submission_total = submission.get("total_score", 0)
                old_question_total = question_score.get("obtained_marks", 0)
                new_submission_total = old_submission_total - old_question_total + new_marks
                
                # Update in database
                await db.submissions.update_one(
                    {"submission_id": submission["submission_id"]},
                    {"$set": {
                        "question_scores": question_scores,
                        "total_score": new_submission_total
                    }}
                )
                
                updated_count += 1
                logger.info(f"[{idx+1}/{len(submissions)}] Re-graded {submission['student_name']} - Q{question_number}: {new_marks}/{question.get('max_marks')}")
                
        except Exception as e:
            logger.error(f"Error re-grading submission {submission.get('submission_id')}: {e}")
            failed_count += 1
            continue
    
    logger.info(f"Intelligent re-grading complete: {updated_count} updated, {failed_count} failed")
    
    return {
        "message": f"Intelligently re-graded {updated_count} papers using your feedback",
        "updated_count": updated_count,
        "failed_count": failed_count
    }


@api_router.post("/feedback/apply-multiple-to-all-papers")
async def apply_multiple_feedback_to_all_papers(
    request: dict,
    user: User = Depends(get_current_user)
):
    """
    Apply multiple feedback corrections to all papers in ONE batch
    Efficient processing of multiple sub-question corrections
    """
    if user.role != "teacher":
        raise HTTPException(status_code=403, detail="Only teachers can apply corrections")
    
    feedback_ids = request.get("feedback_ids", [])
    if not feedback_ids:
        raise HTTPException(status_code=400, detail="No feedback IDs provided")
    
    # Get all feedback records
    feedbacks = []
    for fid in feedback_ids:
        feedback = await db.grading_feedback.find_one({"feedback_id": fid}, {"_id": 0})
        if feedback:
            feedbacks.append(feedback)
    
    if not feedbacks:
        raise HTTPException(status_code=404, detail="No feedback found")
    
    # All feedbacks should be for the same exam and question
    exam_id = feedbacks[0].get("exam_id")
    question_number = feedbacks[0].get("question_number")
    
    logger.info(f"Processing {len(feedbacks)} corrections for Q{question_number} in exam {exam_id}")
    
    # Get exam details
    exam = await db.exams.find_one({"exam_id": exam_id}, {"_id": 0})
    if not exam:
        raise HTTPException(status_code=404, detail="Exam not found")
    
    # Get question details
    question = await db.questions.find_one(
        {"exam_id": exam_id, "question_number": question_number},
        {"_id": 0}
    )
    
    if not question:
        question = next((q for q in exam.get("questions", []) if q.get("question_number") == question_number), None)
    
    if not question:
        raise HTTPException(status_code=404, detail=f"Question {question_number} not found")
    
    # Get model answer text for reference
    model_answer_text = await get_exam_model_answer_text(exam_id)
    
    # Find all submissions for this exam
    submissions = await db.submissions.find(
        {"exam_id": exam_id},
        {"_id": 0, "submission_id": 1, "student_name": 1, "question_scores": 1, "file_images": 1, "total_score": 1}
    ).to_list(1000)
    
    if not submissions:
        return {"message": "No submissions found", "updated_count": 0, "failed_count": 0}
    
    updated_count = 0
    failed_count = 0
    
    # Process each submission
    for idx, submission in enumerate(submissions):
        try:
            question_scores = submission.get("question_scores", [])
            
            # Find the question
            q_index = next((i for i, qs in enumerate(question_scores) 
                           if qs.get("question_number") == question_number), None)
            
            if q_index is None:
                continue
            
            question_score = question_scores[q_index]
            student_images = submission.get("file_images", [])
            
            if not student_images:
                logger.warning(f"No images for submission {submission['submission_id']}")
                continue
            
            old_question_total = question_score.get("obtained_marks", 0)
            submission_changed = False
            
            # Apply each feedback correction
            for feedback in feedbacks:
                sub_question_id = feedback.get("sub_question_id")
                teacher_correction = feedback.get("teacher_correction")
                
                if not teacher_correction:
                    continue
                
                # Determine what to re-grade
                if sub_question_id and sub_question_id != "all":
                    # Re-grade specific sub-question
                    sub_scores = question_score.get("sub_scores", [])
                    sub_index = next((i for i, ss in enumerate(sub_scores) 
                                     if ss.get("sub_id") == sub_question_id), None)
                    
                    if sub_index is None:
                        continue
                    
                    # Get sub-question details
                    sub_question = next((sq for sq in question.get("sub_questions", []) 
                                        if sq.get("sub_id") == sub_question_id), None)
                    
                    if not sub_question:
                        continue
                    
                    # Create AI prompt
                    re_grade_prompt = f"""# INTELLIGENT RE-GRADING TASK

## TEACHER'S GRADING GUIDANCE
{teacher_correction}

## CONTEXT
- Question {question_number}, Part: {sub_question.get('sub_label', 'Part')}
- Maximum Marks: {sub_question.get('max_marks')}
- Sub-question: {sub_question.get('rubric', '')}

## MODEL ANSWER REFERENCE
{model_answer_text[:2000] if model_answer_text else "No model answer available"}

## YOUR TASK
Re-grade this student's answer based on the teacher's guidance above.

## OUTPUT FORMAT (JSON ONLY)
{{
  "obtained_marks": <marks between 0 and {sub_question.get('max_marks')}>,
  "ai_feedback": "<brief explanation>"
}}
"""
                    
                    # Call AI to re-grade
                    from emergentintegrations import LlmChat, UserMessage, ImageContent
                    
                    chat = LlmChat(
                        api_key=os.environ.get('EMERGENT_LLM_KEY'),
                        provider="gemini",
                        response_format="json_object"
                    ).with_model("gemini-2.5-flash").with_params(temperature=0.3)
                    
                    content = [re_grade_prompt]
                    for img in student_images[:15]:
                        content.append(ImageContent(image=img, detail="low"))
                    
                    result = await chat.send_message(UserMessage(content=content))
                    re_grade_result = json.loads(result.text)
                    
                    # Update sub-question score
                    new_marks = float(re_grade_result.get("obtained_marks", sub_scores[sub_index]["obtained_marks"]))
                    new_feedback = f"[Teacher Re-graded] {re_grade_result.get('ai_feedback', '')}"
                    
                    sub_scores[sub_index]["obtained_marks"] = new_marks
                    sub_scores[sub_index]["ai_feedback"] = new_feedback
                    question_scores[q_index]["sub_scores"] = sub_scores
                    
                    submission_changed = True
                    logger.info(f"[{idx+1}/{len(submissions)}] {submission['student_name']} - Q{question_number} Part {sub_question_id}: {new_marks}/{sub_question.get('max_marks')}")
            
            # Recalculate question total from sub-questions
            if submission_changed:
                sub_scores = question_scores[q_index].get("sub_scores", [])
                new_question_total = sum(ss.get("obtained_marks", 0) for ss in sub_scores)
                question_scores[q_index]["obtained_marks"] = new_question_total
                
                # Recalculate submission total
                old_submission_total = submission.get("total_score", 0)
                new_submission_total = old_submission_total - old_question_total + new_question_total
                
                # Update in database
                await db.submissions.update_one(
                    {"submission_id": submission["submission_id"]},
                    {"$set": {
                        "question_scores": question_scores,
                        "total_score": new_submission_total
                    }}
                )
                
                updated_count += 1
                
        except Exception as e:
            logger.error(f"Error re-grading submission {submission.get('submission_id')}: {e}")
            failed_count += 1
            continue
    
    logger.info(f"Multi-correction complete: {updated_count} updated, {failed_count} failed")
    
    return {
        "message": f"Intelligently re-graded {updated_count} papers for {len(feedbacks)} corrections",
        "updated_count": updated_count,
        "failed_count": failed_count
    }


@api_router.post("/feedback/apply-multiple-to-all-papers")
async def apply_multiple_feedback_to_all_papers(
    request: dict,
    user: User = Depends(get_current_user)
):
    """
    Apply multiple feedback corrections to all papers in a batch.
    Handles multiple sub-question corrections for the same question.
    """
    if user.role != "teacher":
        raise HTTPException(status_code=403, detail="Only teachers can apply corrections")
    
    feedback_ids = request.get("feedback_ids", [])
    if not feedback_ids:
        raise HTTPException(status_code=400, detail="No feedback IDs provided")
    
    # Get all feedback records
    feedbacks = await db.grading_feedback.find(
        {"feedback_id": {"$in": feedback_ids}},
        {"_id": 0}
    ).to_list(100)
    
    if not feedbacks:
        raise HTTPException(status_code=404, detail="No feedback found")
    
    # Group feedbacks by exam_id and question_number
    exam_question_groups = {}
    for feedback in feedbacks:
        exam_id = feedback.get("exam_id")
        question_number = feedback.get("question_number")
        key = f"{exam_id}_{question_number}"
        
        if key not in exam_question_groups:
            exam_question_groups[key] = {
                "exam_id": exam_id,
                "question_number": question_number,
                "feedbacks": []
            }
        exam_question_groups[key]["feedbacks"].append(feedback)
    
    total_updated = 0
    total_failed = 0
    
    # Process each exam-question group
    for group_key, group in exam_question_groups.items():
        exam_id = group["exam_id"]
        question_number = group["question_number"]
        group_feedbacks = group["feedbacks"]
        
        logger.info(f"Processing {len(group_feedbacks)} corrections for Q{question_number} in exam {exam_id}")
        
        # Get exam details
        exam = await db.exams.find_one({"exam_id": exam_id}, {"_id": 0})
        if not exam:
            logger.error(f"Exam {exam_id} not found")
            continue
        
        # Get question details
        question = await db.questions.find_one(
            {"exam_id": exam_id, "question_number": question_number},
            {"_id": 0}
        )
        
        if not question:
            # Fallback to exam questions array
            question = next((q for q in exam.get("questions", []) if q.get("question_number") == question_number), None)
        
        if not question:
            logger.error(f"Question {question_number} not found for exam {exam_id}")
            continue
        
        # Get model answer text for reference
        model_answer_text = await get_exam_model_answer_text(exam_id)
        
        # Find all submissions for this exam
        submissions = await db.submissions.find(
            {"exam_id": exam_id},
            {"_id": 0, "submission_id": 1, "student_name": 1, "question_scores": 1, "file_images": 1, "total_score": 1}
        ).to_list(1000)
        
        if not submissions:
            logger.warning(f"No submissions found for exam {exam_id}")
            continue
        
        # Process each submission
        for idx, submission in enumerate(submissions):
            try:
                question_scores = submission.get("question_scores", [])
                
                # Find the question
                q_index = next((i for i, qs in enumerate(question_scores) 
                               if qs.get("question_number") == question_number), None)
                
                if q_index is None:
                    continue
                
                question_score = question_scores[q_index]
                student_images = submission.get("file_images", [])
                
                if not student_images:
                    logger.warning(f"No images for submission {submission['submission_id']}")
                    continue
                
                # Track if any changes were made to this submission
                submission_updated = False
                old_question_total = question_score.get("obtained_marks", 0)
                
                # Apply each feedback correction
                for feedback in group_feedbacks:
                    sub_question_id = feedback.get("sub_question_id")
                    teacher_expected_grade = feedback.get("teacher_expected_grade")
                    teacher_correction = feedback.get("teacher_correction")
                    
                    if not teacher_correction:
                        continue
                    
                    # Determine what to re-grade: sub-question or whole question
                    if sub_question_id and sub_question_id != "all":
                        # Re-grade specific sub-question
                        sub_scores = question_score.get("sub_scores", [])
                        sub_index = next((i for i, ss in enumerate(sub_scores) 
                                         if ss.get("sub_id") == sub_question_id), None)
                        
                        if sub_index is None:
                            continue
                        
                        old_sub_score = sub_scores[sub_index]
                        
                        # Get sub-question details
                        sub_question = next((sq for sq in question.get("sub_questions", []) 
                                            if sq.get("sub_id") == sub_question_id), None)
                        
                        if not sub_question:
                            continue
                        
                        # Create AI prompt for intelligent re-grading
                        re_grade_prompt = f"""# INTELLIGENT RE-GRADING TASK

## TEACHER'S GRADING GUIDANCE
{teacher_correction}

## CONTEXT
- Question {question_number}, Part/Sub-question: {sub_question.get('sub_label', 'Part')}
- Maximum Marks: {sub_question.get('max_marks')}
- Sub-question: {sub_question.get('rubric', '')}

## MODEL ANSWER REFERENCE
{model_answer_text[:3000] if model_answer_text else "No model answer available"}

## YOUR TASK
Re-grade this student's answer for the sub-question based on the teacher's guidance above.
Analyze the student's response carefully and apply the grading criteria the teacher expects.
Award marks based on:
1. Understanding demonstrated
2. Key concepts mentioned
3. Correctness of approach
4. Completeness of answer

## IMPORTANT
- Apply the teacher's grading philosophy consistently
- Give partial credit where appropriate
- Be fair and objective

## OUTPUT FORMAT (JSON ONLY)
{{
  "obtained_marks": <marks between 0 and {sub_question.get('max_marks')}>,
  "ai_feedback": "<brief explanation of grading decision>"
}}
"""
                        
                        # Call AI to re-grade
                        from emergentintegrations import LlmChat, UserMessage, ImageContent
                        
                        chat = LlmChat(
                            api_key=os.environ.get('EMERGENT_LLM_KEY'),
                            provider="gemini",
                            response_format="json_object"
                        ).with_model("gemini-2.5-flash").with_params(temperature=0.3)
                        
                        # Prepare images for this question
                        content = [re_grade_prompt]
                        for img in student_images[:20]:  # Limit images for API
                            content.append(ImageContent(image=img, detail="low"))
                        
                        result = await chat.send_message(UserMessage(content=content))
                        re_grade_result = json.loads(result.text)
                        
                        # Update sub-question score
                        new_marks = float(re_grade_result.get("obtained_marks", old_sub_score["obtained_marks"]))
                        new_feedback = f"[Teacher Re-graded] {re_grade_result.get('ai_feedback', '')}"
                        
                        sub_scores[sub_index]["obtained_marks"] = new_marks
                        sub_scores[sub_index]["ai_feedback"] = new_feedback
                        question_scores[q_index]["sub_scores"] = sub_scores
                        
                        submission_updated = True
                        logger.info(f"[{idx+1}/{len(submissions)}] Re-graded {submission['student_name']} - Q{question_number} Part {sub_question_id}: {new_marks}/{sub_question.get('max_marks')}")
                        
                    else:
                        # Re-grade whole question
                        re_grade_prompt = f"""# INTELLIGENT RE-GRADING TASK

## TEACHER'S GRADING GUIDANCE
{teacher_correction}

## CONTEXT
- Question {question_number}
- Maximum Marks: {question.get('max_marks')}
- Question: {question.get('rubric', '')}

## MODEL ANSWER REFERENCE
{model_answer_text[:3000] if model_answer_text else "No model answer available"}

## YOUR TASK
Re-grade this student's entire answer for Question {question_number} based on the teacher's guidance above.
Analyze the student's response carefully and apply the grading criteria the teacher expects.

## IMPORTANT
- Apply the teacher's grading philosophy consistently
- Give partial credit where appropriate
- Be fair and objective
- Consider all aspects of the answer

## OUTPUT FORMAT (JSON ONLY)
{{
  "obtained_marks": <marks between 0 and {question.get('max_marks')}>,
  "ai_feedback": "<brief explanation of grading decision>"
}}
"""
                        
                        # Call AI to re-grade
                        from emergentintegrations import LlmChat, UserMessage, ImageContent
                        
                        chat = LlmChat(
                            api_key=os.environ.get('EMERGENT_LLM_KEY'),
                            provider="gemini",
                            response_format="json_object"
                        ).with_model("gemini-2.5-flash").with_params(temperature=0.3)
                        
                        # Prepare images
                        content = [re_grade_prompt]
                        for img in student_images[:20]:
                            content.append(ImageContent(image=img, detail="low"))
                        
                        result = await chat.send_message(UserMessage(content=content))
                        re_grade_result = json.loads(result.text)
                        
                        # Update question score
                        new_marks = float(re_grade_result.get("obtained_marks", question_score.get("obtained_marks", 0)))
                        new_feedback = f"[Teacher Re-graded] {re_grade_result.get('ai_feedback', '')}"
                        
                        question_scores[q_index]["obtained_marks"] = new_marks
                        question_scores[q_index]["ai_feedback"] = new_feedback
                        
                        submission_updated = True
                        logger.info(f"[{idx+1}/{len(submissions)}] Re-graded {submission['student_name']} - Q{question_number}: {new_marks}/{question.get('max_marks')}")
                
                # If any changes were made, recalculate totals and update database
                if submission_updated:
                    # Recalculate question total from sub-scores if they exist
                    if question_score.get("sub_scores"):
                        new_question_total = sum(ss.get("obtained_marks", 0) for ss in question_score["sub_scores"])
                        question_scores[q_index]["obtained_marks"] = new_question_total
                    else:
                        new_question_total = question_scores[q_index]["obtained_marks"]
                    
                    # Recalculate submission total
                    old_submission_total = submission.get("total_score", 0)
                    new_submission_total = old_submission_total - old_question_total + new_question_total
                    
                    # Update in database
                    await db.submissions.update_one(
                        {"submission_id": submission["submission_id"]},
                        {"$set": {
                            "question_scores": question_scores,
                            "total_score": new_submission_total
                        }}
                    )
                    
                    total_updated += 1
                    
            except Exception as e:
                logger.error(f"Error re-grading submission {submission.get('submission_id')}: {e}")
                total_failed += 1
                continue
    
    logger.info(f"Multiple feedback re-grading complete: {total_updated} updated, {total_failed} failed")
    
    return {
        "message": f"Intelligently re-graded {total_updated} papers using {len(feedback_ids)} corrections",
        "updated_count": total_updated,
        "failed_count": total_failed
    }


class PublishResultsRequest(BaseModel):
    show_model_answer: bool = False
    show_answer_sheet: bool = True
    show_question_paper: bool = True
    # Feedback is always shown

@api_router.post("/exams/{exam_id}/publish-results")
async def publish_exam_results(
    exam_id: str, 
    settings: PublishResultsRequest,
    user: User = Depends(get_current_user)
):
    """Publish exam results to make them visible to students with visibility controls"""
    if user.role != "teacher":
        raise HTTPException(status_code=403, detail="Only teachers can publish results")
    
    # Verify exam belongs to teacher
    exam = await db.exams.find_one({"exam_id": exam_id, "teacher_id": user.user_id}, {"_id": 0})
    if not exam:
        raise HTTPException(status_code=404, detail="Exam not found or access denied")
    
    # Update exam to mark results as published with visibility settings
    await db.exams.update_one(
        {"exam_id": exam_id},
        {"$set": {
            "results_published": True,
            "published_at": datetime.now(timezone.utc).isoformat(),
            "student_visibility": {
                "show_model_answer": settings.show_model_answer,
                "show_answer_sheet": settings.show_answer_sheet,
                "show_question_paper": settings.show_question_paper,
                "show_feedback": True  # Always show feedback
            }
        }}
    )
    
    return {"message": "Results published successfully", "exam_id": exam_id, "visibility": settings.dict()}

@api_router.post("/exams/{exam_id}/unpublish-results")
async def unpublish_exam_results(exam_id: str, user: User = Depends(get_current_user)):
    """Unpublish exam results to hide them from students (for corrections)"""
    if user.role != "teacher":
        raise HTTPException(status_code=403, detail="Only teachers can unpublish results")
    
    # Verify exam belongs to teacher
    exam = await db.exams.find_one({"exam_id": exam_id, "teacher_id": user.user_id}, {"_id": 0})
    if not exam:
        raise HTTPException(status_code=404, detail="Exam not found or access denied")
    
    # Update exam to mark results as unpublished
    await db.exams.update_one(
        {"exam_id": exam_id},
        {"$set": {"results_published": False}}
    )
    
    return {"message": "Results unpublished successfully", "exam_id": exam_id}

@api_router.get("/feedback/my-feedback")
async def get_my_feedback(user: User = Depends(get_current_user)):
    """Get teacher's own feedback submissions"""
    if user.role != "teacher":
        raise HTTPException(status_code=403, detail="Only teachers can view feedback")
    
    feedback = await db.grading_feedback.find(
        {"teacher_id": user.user_id},
        {"_id": 0}
    ).sort("created_at", -1).to_list(100)
    
    return {"feedback": feedback, "count": len(feedback)}

@api_router.get("/feedback/teacher-patterns/{teacher_id}")
async def get_teacher_feedback_patterns(teacher_id: str):
    """Get feedback patterns for a specific teacher to personalize grading"""
    # Get recent feedback for this teacher
    feedback = await db.grading_feedback.find(
        {"teacher_id": teacher_id, "feedback_type": {"$in": ["question_grading", "correction"]}},
        {"_id": 0, "teacher_correction": 1, "grading_mode": 1, "question_text": 1, "ai_feedback": 1}
    ).sort("created_at", -1).to_list(10)
    
    return feedback

@api_router.get("/feedback/common-patterns")
async def get_common_feedback_patterns():
    """Get common feedback patterns across all teachers"""
    # Get feedback marked as common or with high upvotes
    common_feedback = await db.grading_feedback.find(
        {"$or": [{"is_common": True}, {"upvote_count": {"$gte": 3}}]},
        {"_id": 0, "teacher_correction": 1, "grading_mode": 1, "feedback_type": 1}
    ).to_list(20)
    
    return common_feedback

# ============== HEALTH CHECK ==============

@api_router.get("/health")
async def health_check():
    return {"status": "healthy", "service": "GradeSense API"}

# ============== BATCH STATS ==============

@api_router.get("/batches/{batch_id}/stats")
async def get_batch_stats(batch_id: str, user: User = Depends(get_current_user)):
    """Get statistics for a specific batch"""
    if user.role != "teacher":
        raise HTTPException(status_code=403, detail="Only teachers can view batch stats")
    
    # Verify batch belongs to teacher
    batch = await db.batches.find_one({"batch_id": batch_id, "teacher_id": user.user_id})
    if not batch:
        raise HTTPException(status_code=404, detail="Batch not found")
    
    # Get exams for this batch
    exams = await db.exams.find({"batch_id": batch_id, "teacher_id": user.user_id}, {"_id": 0}).to_list(100)
    
    # Get submissions for batch exams
    exam_ids = [exam["exam_id"] for exam in exams]
    submissions = await db.submissions.find({"exam_id": {"$in": exam_ids}}, {"_id": 0}).to_list(1000)
    
    # Calculate stats
    total_exams = len([e for e in exams if e.get("status") == "completed"])
    action_required = len([e for e in exams if e.get("status") == "processing"])
    
    # Class average
    if submissions:
        class_average = round(sum(s.get("percentage", 0) for s in submissions) / len(submissions), 1)
    else:
        class_average = 0
    
    # At-risk students (below 40%)
    at_risk_count = len(set(s["student_id"] for s in submissions if s.get("percentage", 0) < 40))
    
    # Trend (last 8 exams)
    trend = []
    for exam in sorted(exams, key=lambda x: x.get("created_at", ""))[-8:]:
        exam_submissions = [s for s in submissions if s["exam_id"] == exam["exam_id"]]
        if exam_submissions:
            avg = sum(s.get("percentage", 0) for s in exam_submissions) / len(exam_submissions)
            trend.append(int(avg))
    
    trend_direction = "up" if len(trend) >= 2 and trend[-1] > trend[0] else "down" if len(trend) >= 2 and trend[-1] < trend[0] else "neutral"
    
    return {
        "total_exams": total_exams,
        "action_required": action_required,
        "class_average": class_average,
        "at_risk_count": at_risk_count,
        "trend": trend,
        "trend_direction": trend_direction,
        "total_students": len(batch.get("students", []))
    }


@api_router.get("/batches/{batch_id}/students")
async def get_batch_students(batch_id: str, user: User = Depends(get_current_user)):
    """Get students in a batch with their performance data"""
    if user.role != "teacher":
        raise HTTPException(status_code=403, detail="Only teachers can view batch students")
    
    # Verify batch belongs to teacher
    batch = await db.batches.find_one({"batch_id": batch_id, "teacher_id": user.user_id})
    if not batch:
        raise HTTPException(status_code=404, detail="Batch not found")
    
    student_ids = batch.get("students", [])
    if not student_ids:
        return []
    
    # Get student details
    students = await db.users.find({"user_id": {"$in": student_ids}}, {"_id": 0, "password": 0}).to_list(100)
    
    # Get submissions for each student
    exams = await db.exams.find({"batch_id": batch_id}, {"_id": 0, "exam_id": 1}).to_list(100)
    exam_ids = [e["exam_id"] for e in exams]
    
    # Enrich with performance data
    enriched_students = []
    for student in students:
        student_submissions = await db.submissions.find({
            "student_id": student["user_id"],
            "exam_id": {"$in": exam_ids}
        }, {"_id": 0}).to_list(100)
        
        if student_submissions:
            average = round(sum(s.get("percentage", 0) for s in student_submissions) / len(student_submissions), 1)
            
            # Calculate trend
            recent = sorted(student_submissions, key=lambda x: x.get("graded_at", ""))[-2:]
            trend = "up" if len(recent) == 2 and recent[1].get("percentage", 0) > recent[0].get("percentage", 0) else \
                   "down" if len(recent) == 2 and recent[1].get("percentage", 0) < recent[0].get("percentage", 0) else "neutral"
        else:
            average = 0
            trend = "neutral"
        
        enriched_students.append({
            "student_id": student["user_id"],
            "name": student.get("name", "Unknown"),
            "email": student.get("email", ""),
            "roll_number": student.get("roll_number", ""),
            "average": average,
            "trend": trend
        })
    
    return enriched_students


@api_router.get("/students/{student_id}/analytics")
async def get_student_analytics(
    student_id: str,
    batch_id: str,
    user: User = Depends(get_current_user)
):
    """Get detailed analytics for a student"""
    if user.role != "teacher":
        raise HTTPException(status_code=403, detail="Only teachers can view student analytics")
    
    # Get exams for this batch
    exams = await db.exams.find({"batch_id": batch_id, "teacher_id": user.user_id}, {"_id": 0}).to_list(100)
    exam_ids = [e["exam_id"] for e in exams]
    
    # Get student submissions
    submissions = await db.submissions.find({
        "student_id": student_id,
        "exam_id": {"$in": exam_ids}
    }, {"_id": 0}).to_list(100)
    
    if not submissions:
        return {
            "overall_average": 0,
            "total_exams": 0,
            "exam_history": [],
            "strengths": ["No data yet"],
            "weaknesses": ["No data yet"]
        }
    
    # Calculate overall average
    overall_average = round(sum(s.get("percentage", 0) for s in submissions) / len(submissions), 1)
    
    # Build exam history
    exam_history = []
    for sub in sorted(submissions, key=lambda x: x.get("graded_at", ""), reverse=True):
        exam = next((e for e in exams if e["exam_id"] == sub["exam_id"]), None)
        if exam:
            # Get class average for comparison
            all_submissions = await db.submissions.find({"exam_id": sub["exam_id"]}, {"_id": 0, "percentage": 1}).to_list(100)
            class_avg = round(sum(s.get("percentage", 0) for s in all_submissions) / len(all_submissions), 1) if all_submissions else 0
            
            exam_history.append({
                "exam_name": exam.get("exam_name", "Untitled"),
                "percentage": sub.get("percentage", 0),
                "obtained_marks": sub.get("obtained_marks", 0),
                "total_marks": sub.get("total_marks", 100),
                "graded_at": sub.get("graded_at", ""),
                "class_average": class_avg
            })
    
    # Analyze strengths and weaknesses (based on scores)
    strengths = []
    weaknesses = []
    
    for sub in submissions:
        scores = sub.get("scores", [])
        for score in scores:
            if isinstance(score, dict):
                q_num = score.get("question_number", "")
                obtained = score.get("obtained_marks", 0)
                total = score.get("max_marks", 1)
                percentage = (obtained / total * 100) if total > 0 else 0
                
                if percentage >= 80:
                    strengths.append(f"Question {q_num} ({percentage:.0f}%)")
                elif percentage < 40:
                    weaknesses.append(f"Question {q_num} ({percentage:.0f}%)")
    
    # Deduplicate and limit
    strengths = list(set(strengths))[:5] or ["Consistent performance across topics"]
    weaknesses = list(set(weaknesses))[:5] or ["No major weaknesses identified"]
    
    return {
        "overall_average": overall_average,
        "total_exams": len(submissions),
        "exam_history": exam_history,
        "strengths": strengths,
        "weaknesses": weaknesses
    }


# ============== DEBUG ENDPOINT ==============

@api_router.post("/debug/cleanup")
async def debug_cleanup():
    """
    EMERGENCY CLEANUP: Cancel all stuck jobs and tasks
    Use this when the system is blocked by zombie jobs
    """
    from datetime import timedelta
    
    try:
        # AGGRESSIVE: Cancel ALL jobs in processing/pending state (no time limit)
        jobs_result = await db.grading_jobs.update_many(
            {"status": {"$in": ["processing", "pending"]}},
            {
                "$set": {
                    "status": "failed",
                    "error": "Emergency cleanup - manually cancelled",
                    "updated_at": datetime.now(timezone.utc).isoformat()
                }
            }
        )
        
        # Cancel all stuck tasks
        tasks_result = await db.tasks.update_many(
            {"status": {"$in": ["pending", "processing", "claimed"]}},
            {"$set": {"status": "cancelled"}}
        )
        
        return {
            "success": True,
            "jobs_cancelled": jobs_result.modified_count,
            "tasks_cancelled": tasks_result.modified_count,
            "message": f"Cleaned up {jobs_result.modified_count} jobs and {tasks_result.modified_count} tasks"
        }
        
    except Exception as e:
        logger.error(f"Cleanup error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@api_router.get("/debug/status")
async def debug_status():
    """
    Debug endpoint to check worker status, database connectivity, and job queue
    USE THIS TO DIAGNOSE PRODUCTION ISSUES
    """
    from datetime import datetime, timezone
    
    debug_info = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "environment": {
            "db_name": os.environ.get('DB_NAME', 'NOT_SET'),
            "mongo_url_configured": "MONGO_URL" in os.environ,
            "worker_integrated": True,  # We integrated the worker
            "worker_task_status": "Unknown"
        },
        "database": {
            "connection": "Unknown",
            "collections": []
        },
        "worker": {
            "status": "Unknown",
            "message": "Checking..."
        },
        "jobs": {
            "pending": 0,
            "processing": 0,
            "completed_last_hour": 0,
            "failed_last_hour": 0,
            "recent_jobs": []
        },
        "tasks": {
            "pending": 0,
            "processing": 0,
            "recent_tasks": []
        }
    }
    
    try:
        # Check database connectivity
        await db.command("ping")
        debug_info["database"]["connection"] = "Connected ✅"
        
        # List collections
        collections = await db.list_collection_names()
        debug_info["database"]["collections"] = collections[:10]  # First 10
        
        # Count jobs by status
        from datetime import timedelta
        one_hour_ago = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
        
        debug_info["jobs"]["pending"] = await db.grading_jobs.count_documents({"status": "pending"})
        debug_info["jobs"]["processing"] = await db.grading_jobs.count_documents({"status": "processing"})
        debug_info["jobs"]["completed_last_hour"] = await db.grading_jobs.count_documents({
            "status": "completed",
            "updated_at": {"$gte": one_hour_ago}
        })
        debug_info["jobs"]["failed_last_hour"] = await db.grading_jobs.count_documents({
            "status": "failed",
            "updated_at": {"$gte": one_hour_ago}
        })
        
        # Get recent jobs (last 5)
        recent_jobs = await db.grading_jobs.find(
            {},
            {"_id": 0, "job_id": 1, "exam_id": 1, "status": 1, "total_papers": 1, "processed_papers": 1, "created_at": 1}
        ).sort([("created_at", -1)]).limit(5).to_list(5)
        
        debug_info["jobs"]["recent_jobs"] = [
            {
                "job_id": job.get("job_id"),
                "status": job.get("status"),
                "progress": f"{job.get('processed_papers', 0)}/{job.get('total_papers', 0)}",
                "created": job.get("created_at", "")[:19]
            }
            for job in recent_jobs
        ]
        
        # Count tasks by status
        debug_info["tasks"]["pending"] = await db.tasks.count_documents({"status": "pending"})
        debug_info["tasks"]["processing"] = await db.tasks.count_documents({"status": "processing"})
        
        # Get recent tasks (last 5)
        recent_tasks = await db.tasks.find(
            {},
            {"_id": 0, "task_id": 1, "type": 1, "status": 1, "created_at": 1}
        ).sort([("created_at", -1)]).limit(5).to_list(5)
        
        debug_info["tasks"]["recent_tasks"] = [
            {
                "task_id": task.get("task_id"),
                "type": task.get("type"),
                "status": task.get("status"),
                "created": task.get("created_at", "")[:19]
            }
            for task in recent_tasks
        ]
        
        # Worker status check
        global _worker_task
        if _worker_task is not None:
            if _worker_task.done():
                debug_info["worker"]["status"] = "STOPPED ⚠️"
                try:
                    exception = _worker_task.exception()
                    debug_info["worker"]["message"] = f"Worker crashed: {str(exception)}"
                except:
                    debug_info["worker"]["message"] = "Worker completed or was cancelled"
            else:
                debug_info["worker"]["status"] = "RUNNING ✅"
                debug_info["worker"]["message"] = "Integrated worker is active and polling for tasks"
        else:
            debug_info["worker"]["status"] = "NOT STARTED ❌"
            debug_info["worker"]["message"] = "Worker task was never initialized. Check app startup logs."
        
    except Exception as e:
        debug_info["error"] = f"Error collecting debug info: {str(e)}"
        logger.error(f"Debug endpoint error: {e}", exc_info=True)
    
    return debug_info


# ============== ADMIN ROLE SYSTEM ==============

ADMIN_WHITELIST = [
    "gradingtoolaibased@gmail.com",
    # Add more admin emails here
]

# Default feature flags and quotas
DEFAULT_FEATURES = {
    "ai_suggestions": True,
    "sub_questions": True,
    "bulk_upload": True,
    "analytics": True,
    "peer_comparison": True,
    "export_data": True
}

DEFAULT_QUOTAS = {
    "max_exams_per_month": 100,
    "max_papers_per_month": 1000,
    "max_students": 500,
    "max_batches": 50
}

def is_admin(user: User) -> bool:
    """Check if user has admin privileges"""
    return user.email in ADMIN_WHITELIST or user.role == "admin"

async def get_admin_user(user: User = Depends(get_current_user)) -> User:
    """Dependency to ensure user has admin privileges"""
    if not is_admin(user):
        raise HTTPException(
            status_code=403, 
            detail="Admin access required. Contact support if you need admin privileges."
        )
    return user

@api_router.get("/auth/check-admin")
async def check_admin_status(user: User = Depends(get_current_user)):
    """Check if current user has admin privileges"""
    return {
        "is_admin": is_admin(user),
        "email": user.email,
        "role": user.role
    }

@api_router.get("/admin/dashboard-stats")
async def get_dashboard_stats(user: User = Depends(get_admin_user)):
    """Get real-time dashboard statistics for admin"""
    try:
        # Active Now - users with sessions active in last 30 minutes
        thirty_mins_ago = (datetime.now(timezone.utc) - timedelta(minutes=30)).isoformat()
        active_sessions = await db.user_sessions.distinct(
            "user_id",
            {"created_at": {"$gte": thirty_mins_ago}}
        )
        active_now = len(active_sessions)
        
        # Pending Feedback - unresolved feedback count
        pending_feedback = await db.user_feedback.count_documents({
            "status": {"$ne": "resolved"}
        })
        
        # API Health - calculate from recent API metrics
        recent_time = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
        api_metrics = await db.api_metrics.aggregate([
            {"$match": {"timestamp": {"$gte": recent_time}}},
            {"$group": {
                "_id": None,
                "total": {"$sum": 1},
                "successful": {"$sum": {"$cond": [{"$eq": ["$status_code", 200]}, 1, 0]}}
            }}
        ]).to_list(1)
        
        if api_metrics and api_metrics[0]["total"] > 0:
            api_health = round((api_metrics[0]["successful"] / api_metrics[0]["total"]) * 100, 1)
        else:
            api_health = 100.0  # Default if no recent metrics
        
        # System Status
        system_status = "Healthy" if api_health >= 95 else "Degraded" if api_health >= 80 else "Issues"
        
        return {
            "active_now": active_now,
            "pending_feedback": pending_feedback,
            "api_health": api_health,
            "system_status": system_status
        }
    except Exception as e:
        logger.error(f"Error fetching dashboard stats: {e}")
        # Return defaults on error
        return {
            "active_now": 0,
            "pending_feedback": 0,
            "api_health": 0.0,
            "system_status": "Unknown"
        }

# ============== ADVANCED USER CONTROLS ==============

class UserFeatureFlags(BaseModel):
    ai_suggestions: bool = True
    sub_questions: bool = True
    bulk_upload: bool = True
    analytics: bool = True
    peer_comparison: bool = True
    export_data: bool = True

class UserQuotas(BaseModel):
    max_exams_per_month: int = 100
    max_papers_per_month: int = 1000
    max_students: int = 500
    max_batches: int = 50

class UserStatusUpdate(BaseModel):
    status: str  # 'active', 'disabled', 'banned'
    reason: Optional[str] = None

@api_router.get("/admin/users/{user_id}/details")
async def get_user_details(user_id: str, admin: User = Depends(get_admin_user)):
    """Get detailed user information including features and quotas"""
    user = await db.users.find_one({"user_id": user_id}, {"_id": 0, "password": 0})
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    
    # Add default features/quotas if not present
    if "feature_flags" not in user:
        user["feature_flags"] = DEFAULT_FEATURES
    if "quotas" not in user:
        user["quotas"] = DEFAULT_QUOTAS
    if "account_status" not in user:
        user["account_status"] = "active"
    
    # Get usage statistics
    now = datetime.now(timezone.utc)
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    
    exams_this_month = await db.exams.count_documents({
        "teacher_id": user_id,
        "created_at": {"$gte": month_start.isoformat()}
    })
    
    papers_this_month = await db.submissions.aggregate([
        {"$lookup": {
            "from": "exams",
            "localField": "exam_id",
            "foreignField": "exam_id",
            "as": "exam"
        }},
        {"$unwind": "$exam"},
        {"$match": {
            "exam.teacher_id": user_id,
            "created_at": {"$gte": month_start.isoformat()}
        }},
        {"$count": "total"}
    ]).to_list(1)
    
    total_students = await db.students.count_documents({"teacher_id": user_id})
    total_batches = await db.batches.count_documents({"teacher_id": user_id})
    
    user["current_usage"] = {
        "exams_this_month": exams_this_month,
        "papers_this_month": papers_this_month[0]["total"] if papers_this_month else 0,
        "total_students": total_students,
        "total_batches": total_batches
    }
    
    return user

@api_router.put("/admin/users/{user_id}/features")
async def update_user_features(
    user_id: str,
    features: UserFeatureFlags,
    admin: User = Depends(get_admin_user)
):
    """Update user's feature flags"""
    result = await db.users.update_one(
        {"user_id": user_id},
        {"$set": {"feature_flags": features.model_dump()}}
    )
    
    if result.modified_count == 0:
        raise HTTPException(status_code=404, detail="User not found")
    
    logger.info(f"Admin {admin.email} updated features for user {user_id}")
    return {"success": True, "message": "Feature flags updated"}

@api_router.put("/admin/users/{user_id}/quotas")
async def update_user_quotas(
    user_id: str,
    quotas: UserQuotas,
    admin: User = Depends(get_admin_user)
):
    """Update user's usage quotas"""
    result = await db.users.update_one(
        {"user_id": user_id},
        {"$set": {"quotas": quotas.model_dump()}}
    )
    
    if result.modified_count == 0:
        raise HTTPException(status_code=404, detail="User not found")
    
    logger.info(f"Admin {admin.email} updated quotas for user {user_id}")
    return {"success": True, "message": "Quotas updated"}

@api_router.put("/admin/users/{user_id}/status")
async def update_user_status(
    user_id: str,
    status_update: UserStatusUpdate,
    admin: User = Depends(get_admin_user)
):
    """Update user account status (active/disabled/banned)"""
    if status_update.status not in ["active", "disabled", "banned"]:
        raise HTTPException(status_code=400, detail="Invalid status")
    
    update_data = {
        "account_status": status_update.status,
        "status_updated_at": datetime.now(timezone.utc).isoformat(),
        "status_updated_by": admin.email
    }
    
    if status_update.reason:
        update_data["status_reason"] = status_update.reason
    
    result = await db.users.update_one(
        {"user_id": user_id},
        {"$set": update_data}
    )
    
    if result.modified_count == 0:
        raise HTTPException(status_code=404, detail="User not found")
    
    logger.info(f"Admin {admin.email} changed user {user_id} status to {status_update.status}")
    return {"success": True, "message": f"User status updated to {status_update.status}"}

# ============== USER FEEDBACK SYSTEM ==============

class UserFeedback(BaseModel):
    type: str  # 'bug', 'suggestion', 'question'
    data: Dict[str, Any]
    metadata: Optional[Dict[str, Any]] = None

class FrontendEvent(BaseModel):
    event_type: str  # 'button_click', 'tab_switch', 'feature_use'
    element_id: Optional[str] = None
    page: str
    metadata: Optional[Dict[str, Any]] = None

class GradingAnalytics(BaseModel):
    """Model for tracking detailed grading analytics"""
    submission_id: str
    exam_id: str
    teacher_id: str
    original_ai_grade: float
    final_grade: float
    grade_delta: float
    original_ai_feedback: str
    final_feedback: str
    edit_distance: int  # Levenshtein distance or simple char diff
    ai_confidence_score: float  # 0-100
    tokens_input: int
    tokens_output: int
    estimated_cost: float  # in USD
    edited_by_teacher: bool
    edited_at: Optional[str] = None
    grading_duration_seconds: float
    timestamp: str

@api_router.post("/feedback")
async def submit_user_feedback(feedback: UserFeedback, user: User = Depends(get_current_user)):
    """Submit user feedback (bug report, suggestion, or question)"""
    feedback_id = f"ufb_{uuid.uuid4().hex[:12]}"
    
    feedback_doc = {
        "feedback_id": feedback_id,
        "type": feedback.type,
        "data": feedback.data,
        "metadata": feedback.metadata or {},
        "user": {
            "user_id": user.user_id,
            "name": user.name,
            "email": user.email,
            "role": user.role
        },
        "status": "pending",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "resolved_at": None
    }
    
    await db.user_feedback.insert_one(feedback_doc)
    
    logger.info(f"Feedback submitted: {feedback_id} ({feedback.type}) by {user.name}")
    
    return {
        "success": True,
        "feedback_id": feedback_id,
        "message": "Feedback submitted successfully"
    }


@api_router.get("/admin/feedback")
async def get_all_feedback(user: User = Depends(get_admin_user)):
    """Get all user feedback (admin only)"""
    feedbacks = await db.user_feedback.find({}, {"_id": 0}).sort([("created_at", -1)]).to_list(1000)
    
    return feedbacks


@api_router.put("/admin/feedback/{feedback_id}/resolve")
async def resolve_feedback(feedback_id: str, user: User = Depends(get_admin_user)):
    """Mark feedback as resolved (admin only)"""
    
    result = await db.user_feedback.update_one(
        {"feedback_id": feedback_id},
        {
            "$set": {
                "status": "resolved",
                "resolved_at": datetime.now(timezone.utc).isoformat(),
                "resolved_by": {
                    "user_id": user.user_id,
                    "name": user.name
                }
            }
        }
    )
    
    if result.modified_count == 0:
        raise HTTPException(status_code=404, detail="Feedback not found")
    
    logger.info(f"Feedback resolved: {feedback_id} by {user.name}")
    
    return {"success": True, "message": "Feedback marked as resolved"}


# ============== METRICS & ANALYTICS TRACKING ==============

# Gemini Flash pricing (per 1M tokens)
GEMINI_INPUT_PRICE = 0.075  # $0.075 per 1M input tokens
GEMINI_OUTPUT_PRICE = 0.30  # $0.30 per 1M output tokens

def calculate_grading_cost(tokens_input: int, tokens_output: int) -> float:
    """Calculate cost in USD for Gemini Flash API usage"""
    input_cost = (tokens_input / 1_000_000) * GEMINI_INPUT_PRICE
    output_cost = (tokens_output / 1_000_000) * GEMINI_OUTPUT_PRICE
    return round(input_cost + output_cost, 6)

def calculate_edit_distance(original: str, final: str) -> int:
    """Calculate simple edit distance (character difference)"""
    # Simple implementation - counts character differences
    # For production, consider using Levenshtein distance
    if original == final:
        return 0
    return len(set(original) ^ set(final))

async def log_grading_analytics(
    submission_id: str,
    exam_id: str, 
    teacher_id: str,
    question_scores: List[QuestionScore],
    grading_duration: float,
    ai_confidence: float = 0.0,
    tokens_input: int = 0,
    tokens_output: int = 0
):
    """Log detailed grading analytics to database"""
    try:
        for qs in question_scores:
            analytics_doc = {
                "analytics_id": f"ga_{uuid.uuid4().hex[:12]}",
                "submission_id": submission_id,
                "exam_id": exam_id,
                "teacher_id": teacher_id,
                "question_number": qs.question_number,
                "original_ai_grade": qs.obtained_marks,
                "final_grade": qs.obtained_marks,  # Initially same, updated when teacher edits
                "grade_delta": 0.0,
                "original_ai_feedback": qs.ai_feedback,
                "final_feedback": qs.ai_feedback,  # Initially same
                "edit_distance": 0,
                "ai_confidence_score": ai_confidence,
                "tokens_input": tokens_input,
                "tokens_output": tokens_output,
                "estimated_cost": calculate_grading_cost(tokens_input, tokens_output),
                "edited_by_teacher": False,
                "edited_at": None,
                "grading_duration_seconds": grading_duration,
                "timestamp": datetime.now(timezone.utc).isoformat()
            }
            await db.grading_analytics.insert_one(analytics_doc)
    except Exception as e:
        logger.error(f"Failed to log grading analytics: {e}")

@api_router.post("/metrics/track-event")
async def track_frontend_event(event: FrontendEvent, user: User = Depends(get_current_user)):
    """Track frontend user interactions for analytics"""
    try:
        await db.metrics_logs.insert_one({
            "event_id": f"evt_{uuid.uuid4().hex[:12]}",
            "event_type": event.event_type,
            "element_id": event.element_id,
            "page": event.page,
            "user_id": user.user_id,
            "role": user.role,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "metadata": event.metadata or {}
        })
        return {"success": True}
    except Exception as e:
        logger.error(f"Failed to track event: {e}")
        return {"success": False}

@api_router.get("/admin/users")
async def get_all_users(user: User = Depends(get_admin_user)):
    """Get all users for admin management"""
    users = await db.users.find({}, {"_id": 0, "password": 0}).sort([("created_at", -1)]).to_list(1000)
    return serialize_doc(users)

@api_router.get("/admin/metrics/overview")
async def get_metrics_overview(user: User = Depends(get_admin_user)):
    """Get comprehensive metrics overview for admin dashboard"""
    
    try:
        # Business & Growth Metrics
        total_users = await db.users.count_documents({})
        total_teachers = await db.users.count_documents({"role": "teacher"})
        total_students = await db.users.count_documents({"role": "student"})
        
        # Calculate DAU/WAU/MAU (users with activity in last 1/7/30 days)
        now = datetime.now(timezone.utc)
        day_ago = (now - timedelta(days=1)).isoformat()
        week_ago = (now - timedelta(days=7)).isoformat()
        month_ago = (now - timedelta(days=30)).isoformat()
        
        dau = await db.metrics_logs.distinct("user_id", {"timestamp": {"$gte": day_ago}})
        wau = await db.metrics_logs.distinct("user_id", {"timestamp": {"$gte": week_ago}})
        mau = await db.metrics_logs.distinct("user_id", {"timestamp": {"$gte": month_ago}})
        
        # New signups (last 30 days)
        new_signups = await db.users.count_documents({"created_at": {"$gte": month_ago}})
        
        # ⭐ NEW: Retention Rate - Users who graded 2nd exam within 30 days of first
        # Simplified approach: count teachers with 2+ exams
        teachers_with_multiple_exams = await db.exams.aggregate([
            {"$group": {
                "_id": "$teacher_id",
                "exam_count": {"$sum": 1},
                "exams": {"$push": {"exam_id": "$exam_id", "created_at": "$created_at"}}
            }},
            {"$match": {"exam_count": {"$gte": 2}}}
        ]).to_list(None)
        
        # Calculate retention: teachers whose 2nd exam is within 30 days of 1st
        retained_users = 0
        eligible_users = await db.users.count_documents({"role": "teacher"})
        
        for teacher in teachers_with_multiple_exams:
            exams = sorted(teacher["exams"], key=lambda x: x["created_at"])
            if len(exams) >= 2:
                first = datetime.fromisoformat(exams[0]["created_at"].replace('Z', '+00:00'))
                second = datetime.fromisoformat(exams[1]["created_at"].replace('Z', '+00:00'))
                days_diff = (second - first).days
                if days_diff <= 30:
                    retained_users += 1
        
        retention_rate = (retained_users / eligible_users * 100) if eligible_users > 0 else 0
        
        # Engagement Metrics
        total_exams = await db.exams.count_documents({})
        total_papers = await db.submissions.count_documents({})
        
        # Calculate average batch size
        exams_with_counts = await db.exams.aggregate([
            {"$lookup": {
                "from": "submissions",
                "localField": "exam_id",
                "foreignField": "exam_id",
                "as": "submissions"
            }},
            {"$project": {
                "submission_count": {"$size": "$submissions"}
            }}
        ]).to_list(None)
        
        avg_batch_size = sum(e["submission_count"] for e in exams_with_counts) / len(exams_with_counts) if exams_with_counts else 0
        
        # Power users (Top 10 teachers by papers graded)
        power_users = await db.submissions.aggregate([
            {"$lookup": {
                "from": "exams",
                "localField": "exam_id",
                "foreignField": "exam_id",
                "as": "exam"
            }},
            {"$unwind": "$exam"},
            {"$group": {
                "_id": "$exam.teacher_id",
                "papers_graded": {"$sum": 1}
            }},
            {"$sort": {"papers_graded": -1}},
            {"$limit": 10},
            {"$lookup": {
                "from": "users",
                "localField": "_id",
                "foreignField": "user_id",
                "as": "teacher"
            }},
            {"$unwind": "$teacher"},
            {"$project": {
                "teacher_id": "$_id",
                "teacher_name": "$teacher.name",
                "papers_graded": 1,
                "_id": 0
            }}
        ]).to_list(10)
        
        # Grading mode preference
        grading_modes = await db.exams.aggregate([
            {"$group": {
                "_id": "$grading_mode",
                "count": {"$sum": 1}
            }}
        ]).to_list(None)
        
        # ⭐ NEW: End-to-End Grading Time - Average duration from grading_analytics
        grading_time_stats = await db.grading_analytics.aggregate([
            {"$group": {
                "_id": None,
                "avg_grading_time": {"$avg": "$grading_duration_seconds"}
            }}
        ]).to_list(1)
        
        avg_grading_time = grading_time_stats[0]["avg_grading_time"] if grading_time_stats else 0
        
        # ⭐ NEW: Error Categorization - Breakdown of API errors from last 24 hours
        day_ago_errors = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()
        error_breakdown = await db.api_metrics.aggregate([
            {"$match": {
                "timestamp": {"$gte": day_ago_errors},
                "status_code": {"$ne": 200},
                "error_type": {"$ne": None}
            }},
            {"$group": {
                "_id": "$error_type",
                "count": {"$sum": 1}
            }},
            {"$sort": {"count": -1}},
            {"$limit": 10}
        ]).to_list(10)
        
        # ⭐ NEW: Geographic Distribution - Simple IP-based tracking (placeholder for now)
        # In production, you'd use a geo IP service like MaxMind
        geo_distribution = await db.metrics_logs.aggregate([
            {"$group": {
                "_id": "$country",
                "users": {"$addToSet": "$user_id"}
            }},
            {"$project": {
                "country": "$_id",
                "user_count": {"$size": "$users"},
                "_id": 0
            }},
            {"$sort": {"user_count": -1}},
            {"$limit": 10}
        ]).to_list(10)
        
        # If no geo data yet, create placeholder
        if not geo_distribution:
            geo_distribution = [{"country": "Unknown", "user_count": total_users}]
        
        # AI Trust Metrics (from grading_analytics if exists)
        ai_metrics = await db.grading_analytics.aggregate([
            {"$group": {
                "_id": None,
                "avg_confidence": {"$avg": "$ai_confidence_score"},
                "avg_grade_delta": {"$avg": "$grade_delta"},
                "total_graded": {"$sum": 1},
                "edited_count": {"$sum": {"$cond": ["$edited_by_teacher", 1, 0]}},
                "zero_touch_count": {"$sum": {"$cond": [{"$eq": ["$edited_by_teacher", False]}, 1, 0]}}
            }}
        ]).to_list(1)
        
        ai_stats = ai_metrics[0] if ai_metrics else {
            "avg_confidence": 0,
            "avg_grade_delta": 0,
            "total_graded": 0,
            "edited_count": 0,
            "zero_touch_count": 0
        }
        
        # Calculate rates
        human_intervention_rate = (ai_stats["edited_count"] / ai_stats["total_graded"] * 100) if ai_stats["total_graded"] > 0 else 0
        zero_touch_rate = (ai_stats["zero_touch_count"] / ai_stats["total_graded"] * 100) if ai_stats["total_graded"] > 0 else 0
        
        # System Performance Metrics
        avg_response_time = await db.api_metrics.aggregate([
            {"$group": {
                "_id": None,
                "avg_time": {"$avg": "$response_time_ms"}
            }}
        ]).to_list(1)
        
        success_rate_data = await db.api_metrics.aggregate([
            {"$group": {
                "_id": None,
                "total": {"$sum": 1},
                "successful": {"$sum": {"$cond": [{"$eq": ["$status_code", 200]}, 1, 0]}}
            }}
        ]).to_list(1)
        
        success_rate = (success_rate_data[0]["successful"] / success_rate_data[0]["total"] * 100) if success_rate_data and success_rate_data[0]["total"] > 0 else 0
        
        # Unit Economics
        cost_metrics = await db.grading_analytics.aggregate([
            {"$group": {
                "_id": None,
                "total_cost": {"$sum": "$estimated_cost"},
                "avg_cost_per_paper": {"$avg": "$estimated_cost"},
                "total_tokens_input": {"$sum": "$tokens_input"},
                "total_tokens_output": {"$sum": "$tokens_output"}
            }}
        ]).to_list(1)
        
        cost_stats = cost_metrics[0] if cost_metrics else {
            "total_cost": 0,
            "avg_cost_per_paper": 0,
            "total_tokens_input": 0,
            "total_tokens_output": 0
        }
        
        return {
            "business_metrics": {
                "total_users": total_users,
                "total_teachers": total_teachers,
                "total_students": total_students,
                "dau": len(dau),
                "wau": len(wau),
                "mau": len(mau),
                "new_signups_30d": new_signups,
                "retention_rate": round(retention_rate, 1)  # ⭐ NEW
            },
            "engagement_metrics": {
                "total_exams": total_exams,
                "total_papers": total_papers,
                "avg_batch_size": round(avg_batch_size, 1),
                "power_users": power_users,
                "grading_mode_distribution": grading_modes,
                "avg_grading_time_seconds": round(avg_grading_time, 1)  # ⭐ NEW
            },
            "ai_trust_metrics": {
                "avg_confidence": round(ai_stats["avg_confidence"], 1),
                "avg_grade_delta": round(ai_stats["avg_grade_delta"], 2),
                "human_intervention_rate": round(human_intervention_rate, 1),
                "zero_touch_rate": round(zero_touch_rate, 1),
                "total_graded": ai_stats["total_graded"]
            },
            "system_performance": {
                "avg_response_time_ms": round(avg_response_time[0]["avg_time"], 0) if avg_response_time else 0,
                "api_success_rate": round(success_rate, 1),
                "error_breakdown": error_breakdown  # ⭐ NEW
            },
            "unit_economics": {
                "total_cost_usd": round(cost_stats["total_cost"], 2),
                "avg_cost_per_paper_usd": round(cost_stats["avg_cost_per_paper"], 4),
                "total_tokens_input": cost_stats["total_tokens_input"],
                "total_tokens_output": cost_stats["total_tokens_output"]
            },
            "geographic_distribution": geo_distribution  # ⭐ NEW
        }
    except Exception as e:
        logger.error(f"Error fetching metrics: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


# Include router and add middleware
app.include_router(api_router)

# Root-level health check endpoint (for Kubernetes probes)
@app.get("/health")
async def root_health_check():
    """Health check for Kubernetes liveness/readiness probes"""
    return {"status": "healthy", "service": "GradeSense API"}

app.add_middleware(
    CORSMiddleware,
    allow_credentials=True,
    allow_origins=os.environ.get('CORS_ORIGINS', '*').split(','),
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.on_event("shutdown")
async def shutdown_db_client():
    client.close()
