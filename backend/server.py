from fastapi import FastAPI, APIRouter, HTTPException, Request, Response, UploadFile, File, Form, Depends
from fastapi.responses import JSONResponse
from dotenv import load_dotenv
from starlette.middleware.cors import CORSMiddleware
from motor.motor_asyncio import AsyncIOMotorClient
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

ROOT_DIR = Path(__file__).parent
load_dotenv(ROOT_DIR / '.env')

# MongoDB connection
mongo_url = os.environ['MONGO_URL']
client = AsyncIOMotorClient(mongo_url)
db = client[os.environ['DB_NAME']]

# Create the main app
app = FastAPI(title="GradeSense API")

# Create a router with the /api prefix
api_router = APIRouter(prefix="/api")

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
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

class UserCreate(BaseModel):
    email: str
    name: str
    role: str = "student"
    student_id: Optional[str] = None
    batches: List[str] = []

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
    total_marks: float
    exam_date: str
    grading_mode: str
    questions: List[dict] = []

class SubQuestionScore(BaseModel):
    sub_id: str
    max_marks: float
    obtained_marks: float
    ai_feedback: str

class ErrorAnnotation(BaseModel):
    error_type: str  # calculation_error, conceptual_error, incomplete, spelling, formatting
    description: str
    severity: str  # minor, moderate, major
    page: int = 1
    region: str = "middle"  # top, middle, bottom

class QuestionScore(BaseModel):
    question_number: int
    max_marks: float
    obtained_marks: float
    ai_feedback: str
    teacher_comment: Optional[str] = None
    is_reviewed: bool = False
    sub_scores: List[SubQuestionScore] = []  # For sub-question scores
    error_annotations: List[dict] = []  # Error locations for visual annotations
    question_text: Optional[str] = None  # The question text
    status: str = "graded"  # graded, not_attempted, not_found, error

class Submission(BaseModel):
    model_config = ConfigDict(extra="ignore")
    submission_id: str
    exam_id: str
    student_id: str
    student_name: str
    file_data: Optional[str] = None
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
    question_number: Optional[int] = None
    feedback_type: str
    teacher_correction: str
    question_text: Optional[str] = None
    ai_grade: Optional[float] = None
    ai_feedback: Optional[str] = None
    teacher_expected_grade: Optional[float] = None

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
    """Get model answer images from separate collection or fallback to exam document"""
    # First try the new separate collection
    file_doc = await db.exam_files.find_one(
        {"exam_id": exam_id, "file_type": "model_answer"},
        {"_id": 0, "images": 1}
    )
    if file_doc and file_doc.get("images"):
        return file_doc["images"]
    
    # Fallback to old storage in exam document
    exam = await db.exams.find_one({"exam_id": exam_id}, {"_id": 0, "model_answer_images": 1})
    if exam and exam.get("model_answer_images"):
        return exam["model_answer_images"]
    
    return []

async def get_exam_question_paper_images(exam_id: str) -> List[str]:
    """Get question paper images from separate collection or fallback to exam document"""
    # First try the new separate collection
    file_doc = await db.exam_files.find_one(
        {"exam_id": exam_id, "file_type": "question_paper"},
        {"_id": 0, "images": 1}
    )
    if file_doc and file_doc.get("images"):
        return file_doc["images"]
    
    # Fallback to old storage in exam document
    exam = await db.exams.find_one({"exam_id": exam_id}, {"_id": 0, "question_paper_images": 1})
    if exam and exam.get("question_paper_images"):
        return exam["question_paper_images"]
    
    return []

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
    """Get current user from session token"""
    session_token = request.cookies.get("session_token")
    
    if not session_token:
        auth_header = request.headers.get("Authorization")
        if auth_header and auth_header.startswith("Bearer "):
            session_token = auth_header.split(" ")[1]
    
    if not session_token:
        raise HTTPException(status_code=401, detail="Not authenticated")
    
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
    
    return User(**user)

# ============== AUTH ROUTES ==============

@api_router.post("/auth/session")
async def create_session(request: Request, response: Response):
    """Exchange session_id for session_token"""
    data = await request.json()
    session_id = data.get("session_id")
    preferred_role = data.get("preferred_role", "teacher")
    
    if not session_id:
        raise HTTPException(status_code=400, detail="session_id required")
    
    # Call Emergent auth service
    async with httpx.AsyncClient() as client:
        try:
            auth_response = await client.get(
                "https://demobackend.emergentagent.com/auth/v1/env/oauth/session-data",
                headers={"X-Session-ID": session_id}
            )
            if auth_response.status_code != 200:
                raise HTTPException(status_code=401, detail="Invalid session_id")
            
            auth_data = auth_response.json()
        except Exception as e:
            logger.error(f"Auth service error: {e}")
            raise HTTPException(status_code=500, detail="Auth service error")
    
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
                "last_login": datetime.now(timezone.utc).isoformat()
            }}
        )
        user_role = "student"
    else:
        # Check if user exists with different role
        existing_user = await db.users.find_one({"email": user_email}, {"_id": 0})
        
        if existing_user:
            user_id = existing_user["user_id"]
            # Update user data
            await db.users.update_one(
                {"user_id": user_id},
                {"$set": {
                    "name": user_name,
                    "picture": user_picture,
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
                "created_at": datetime.now(timezone.utc).isoformat(),
                "last_login": datetime.now(timezone.utc).isoformat()
            }
            await db.users.insert_one(new_user)
    
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
    return batches

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
    return students

@api_router.get("/students/{student_user_id}")
async def get_student_detail(student_user_id: str, user: User = Depends(get_current_user)):
    """Get detailed student information with performance analytics"""
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
    
    return {
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
    }

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
    
    for file in files:
        try:
            # Process the PDF first to get images
            pdf_bytes = await file.read()
            images = pdf_to_images(pdf_bytes)
            
            if not images:
                errors.append({
                    "filename": file.filename,
                    "error": "Failed to extract images from PDF"
                })
                continue
            
            # Extract student ID and name from the paper using AI
            student_id, student_name = await extract_student_info_from_paper(images, file.filename)
            
            # Fallback to filename if AI extraction fails
            if not student_id or not student_name:
                filename_id, filename_name = parse_student_from_filename(file.filename)
                
                # Use filename ID if AI didn't find it
                if not student_id and filename_id:
                    student_id = filename_id
                
                # Use filename name if AI didn't find it
                if not student_name and filename_name:
                    student_name = filename_name
                
                # If still no ID or name, report error
                if not student_id and not student_name:
                    errors.append({
                        "filename": file.filename,
                        "error": "Could not extract student ID/name from paper or filename. Please ensure student writes their roll number and name clearly on the answer sheet."
                    })
                    continue
                
                # If we have one but not the other, use what we have
                if not student_id:
                    student_id = f"AUTO_{uuid.uuid4().hex[:6]}"
                if not student_name:
                    student_name = f"Student {student_id}"
            
            # Get or create student
            user_id, error = await get_or_create_student(
                student_id=student_id,
                student_name=student_name,
                batch_id=exam["batch_id"],
                teacher_id=user.user_id
            )
            
            if error:
                errors.append({
                    "filename": file.filename,
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
            
        except Exception as e:
            logger.error(f"Error processing {file.filename}: {e}")
            errors.append({
                "filename": file.filename,
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
    
    return submissions

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
    
    return exams

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
    
    return exam

@api_router.delete("/exams/{exam_id}")
async def delete_exam(exam_id: str, user: User = Depends(get_current_user)):
    """Delete an exam and all its submissions"""
    if user.role != "teacher":
        raise HTTPException(status_code=403, detail="Only teachers can delete exams")
    
    # Check if exam exists and belongs to teacher
    exam = await db.exams.find_one({"exam_id": exam_id, "teacher_id": user.user_id})
    if not exam:
        raise HTTPException(status_code=404, detail="Exam not found")
    
    # Delete all submissions associated with this exam
    await db.submissions.delete_many({"exam_id": exam_id})
    
    # Delete all re-evaluation requests associated with this exam
    await db.re_evaluations.delete_many({"exam_id": exam_id})
    
    # Delete exam files from separate collection
    await db.exam_files.delete_many({"exam_id": exam_id})
    
    # Delete the exam
    result = await db.exams.delete_one({"exam_id": exam_id, "teacher_id": user.user_id})
    
    if result.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Exam not found")
    
    return {"message": "Exam deleted successfully"}

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
            q["rubric"] = extracted_questions[i]
            q["question_text"] = extracted_questions[i]  # Also set question_text field
            updated_count += 1
    
    # Update exam in database
    await db.exams.update_one(
        {"exam_id": exam_id},
        {"$set": {"questions": questions}}
    )
    
    return {
        "message": f"Successfully extracted {updated_count} questions from {source}",
        "updated_count": updated_count,
        "source": source
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
    """Convert PDF pages to base64 images - NO PAGE LIMIT"""
    images = []
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    
    # Process ALL pages - no limit
    for page_num in range(len(doc)):
        page = doc[page_num]
        # Use 1.5x zoom for balance between quality and token efficiency
        pix = page.get_pixmap(matrix=fitz.Matrix(1.5, 1.5))
        img_bytes = pix.tobytes("jpeg")
        img_base64 = base64.b64encode(img_bytes).decode()
        images.append(img_base64)
    
    doc.close()
    logger.info(f"Converted PDF with {len(images)} pages to images")
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
        CHUNK_SIZE = 8
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
                    response = await chat.send_message(user_message)
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
    """Extract question text from question paper images using AI"""
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
Return a JSON array with the complete text for each question, including any sub-parts.

Return this exact JSON format:
{
  "questions": [
    "Full text of question 1 here (include all sub-parts if any)",
    "Full text of question 2 here",
    ...
  ]
}

Important:
- Extract ONLY questions, not instructions or headers
- Include question numbers
- Include all sub-parts (a, b, c, etc.) in the same string
- Maintain the original formatting and numbering
- Extract exactly what's written, don't paraphrase
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
                ai_response = await chat.send_message(user_message)
                
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
    """Extract question text from model answer images using AI"""
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
            
Extract ONLY the question text (not answers) from the provided images.
Return a JSON array with the question text for each question.

CRITICAL: You MUST extract ALL questions present in the document. Count carefully!

Return this exact JSON format:
{
  "questions": [
    "Full text of question 1 here",
    "Full text of question 2 here",
    "Full text of question 3 here",
    ...
  ]
}

Important:
- Extract ALL questions - don't stop at just one!
- Extract the complete question text including any sub-parts (a, b, c)
- Do NOT include answer content, only question text
- Maintain original question numbering if visible
- Look through ALL pages carefully
- Return questions in order (Q1, Q2, Q3, etc.)
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

async def auto_extract_questions(exam_id: str, force: bool = False) -> Dict[str, Any]:
    """
    Auto-extract questions from question paper (priority) or model answer.

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
        extraction_func = None

        if qp_imgs:
            target_source = "question_paper"
            images_to_use = qp_imgs
            extraction_func = extract_questions_from_question_paper
        elif ma_imgs:
            target_source = "model_answer"
            images_to_use = ma_imgs
            extraction_func = extract_questions_from_model_answer
        else:
            return {"success": False, "message": "No documents available for extraction"}

        # Check if already extracted
        current_source = exam.get("extraction_source")
        questions_exist = any(q.get("rubric") for q in exam.get("questions", []))

        if not force and questions_exist and current_source == target_source:
            logger.info(f"Skipping extraction for {exam_id}: Already extracted from {current_source}")
            return {
                "success": True,
                "message": f"Questions already extracted from {target_source.replace('_', ' ')}",
                "count": len([q for q in exam.get("questions", []) if q.get("rubric")]),
                "source": target_source,
                "skipped": True
            }

        logger.info(f"Auto-extracting questions for {exam_id} from {target_source} (Force={force})")

        # Perform extraction
        extracted_questions = await extraction_func(
            images_to_use,
            len(exam.get("questions", []))
        )

        if not extracted_questions:
            logger.warning(f"Extraction returned no questions for {exam_id} from {target_source}")
            return {"success": False, "message": f"Failed to extract questions from {target_source.replace('_', ' ')}"}

        # Update questions in database
        questions = exam.get("questions", [])
        updated_count = 0

        for i, q in enumerate(questions):
            if i < len(extracted_questions):
                q["rubric"] = extracted_questions[i]
                q["question_text"] = extracted_questions[i]
                updated_count += 1

        await db.exams.update_one(
            {"exam_id": exam_id},
            {"$set": {
                "questions": questions,
                "extraction_source": target_source
            }}
        )

        logger.info(f"Successfully extracted {updated_count} questions from {target_source}")
        return {
            "success": True,
            "message": f"Successfully extracted {updated_count} questions from {target_source.replace('_', ' ')}",
            "count": updated_count,
            "source": target_source,
            "skipped": False
        }

    except Exception as e:
        logger.error(f"Auto-extraction error for {exam_id}: {e}")
        return {"success": False, "message": f"Error during extraction: {str(e)}"}

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
      "what_done_well": "Brief summary of correct elements",
      "areas_to_improve": "Specific improvement suggestions",
      "error_annotations": [
        {{
          "error_type": "calculation_error|conceptual_error|incomplete|spelling|formatting",
          "description": "Brief description of the error",
          "severity": "minor|moderate|major",
          "page": 1,
          "region": "top|middle|bottom"
        }}
      ],
      "sub_scores": [
        {{"sub_id": "a", "obtained_marks": 3, "ai_feedback": "Feedback for part a"}},
        {{"sub_id": "b", "obtained_marks": 2.5, "ai_feedback": "Feedback for part b"}}
      ],
      "confidence": 0.95,
      "flags": []
    }}
  ],
  "grading_notes": "Any overall observations about the paper"
}}

### Error Annotation Types:
- calculation_error: Mathematical/arithmetic mistakes
- conceptual_error: Fundamental misunderstanding of concept
- incomplete: Missing required elements
- spelling: Spelling mistakes in technical terms
- formatting: Presentation/format issues

### Severity Levels:
- minor: Small deduction (10-20% of component marks)
- moderate: Significant deduction (30-50% of component marks)
- major: Most marks lost (60%+ of component marks)

### Flag Types (use when needed):
- "BORDERLINE_SCORE": Score is borderline pass/fail
- "ALTERNATIVE_METHOD": Valid but unusual approach used
- "EXCEPTIONAL_ANSWER": Unusually brilliant answer
- "NEEDS_REVIEW": Uncertain grading, needs teacher check
- "ILLEGIBLE_PORTIONS": Some parts hard to read

If a question has no sub-questions, leave sub_scores as an empty array.
If there are no errors, leave error_annotations as an empty array.

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
Apply the {grading_mode} mode specifications strictly.

## CRITICAL REQUIREMENTS:
1. **CONSISTENCY IS SACRED**: Same answer = Same score ALWAYS
2. **MODEL ANSWER IS REFERENCE**: Compare against the model answer text provided above
3. **PRECISE SCORING**: Use decimals (e.g., 8.5, 7.25) not ranges
4. **CARRY-FORWARD**: Credit correct logic even on wrong base values
5. **PARTIAL CREDIT**: Apply according to {grading_mode} mode rules
6. **FEEDBACK QUALITY**: Provide constructive, specific feedback
7. **COMPLETE EVALUATION**: Grade ALL {len(questions)} questions - check EVERY page
8. **HANDLE ROTATION**: If text appears sideways, still read and grade it

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
Apply the {grading_mode} mode specifications strictly.

## CRITICAL REQUIREMENTS:
1. **CONSISTENCY IS SACRED**: Same answer = Same score ALWAYS
2. **MODEL ANSWER IS REFERENCE**: Compare against the model answer
3. **PRECISE SCORING**: Use decimals (e.g., 8.5, 7.25) not ranges
4. **CARRY-FORWARD**: Credit correct logic even on wrong base values
5. **PARTIAL CREDIT**: Apply according to {grading_mode} mode rules
6. **FEEDBACK QUALITY**: Provide constructive, specific feedback that helps learning
7. **COMPLETE EVALUATION**: Grade ALL {len(questions)} questions - check EVERY page

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
Apply the {grading_mode} mode specifications strictly.

## CRITICAL REQUIREMENTS:
1. **CONSISTENCY IS SACRED**: Same answer = Same score ALWAYS
2. **RUBRIC-BASED**: Use provided rubrics as primary reference
3. **PRECISE SCORING**: Use decimals (e.g., 8.5, 7.25) not ranges
4. **CONSERVATIVE FLAGGING**: Flag uncertain gradings for teacher review
5. **PARTIAL CREDIT**: Apply according to {grading_mode} mode rules
6. **SUBJECT KNOWLEDGE**: Use your expertise to assess correctness
7. **CONSTRUCTIVE FEEDBACK**: Help the student understand and improve

Return valid JSON only."""

        user_msg = UserMessage(text=prompt_text, file_contents=chunk_all_images)

        # Retry logic
        import asyncio
        max_retries = 3
        retry_delay = 5

        for attempt in range(max_retries):
            try:
                logger.info(f"AI grading chunk {chunk_idx+1}/{total_chunks} attempt {attempt+1}")
                ai_resp = await chunk_chat.send_message(user_msg)

                # Parse
                import json
                resp_text = ai_resp.strip()
                if resp_text.startswith("```"):
                    resp_text = resp_text.split("```")[1]
                    if resp_text.startswith("json"):
                        resp_text = resp_text[4:]

                res = json.loads(resp_text)
                return res.get("scores", [])

            except Exception as e:
                error_msg = str(e).lower()
                
                # Check for rate limiting
                if "429" in str(e) or "rate limit" in error_msg or "quota" in error_msg:
                    wait_time = 60 * (attempt + 1)  # Exponential: 60s, 120s, 180s
                    logger.warning(f"Rate limit hit on chunk {chunk_idx+1}. Waiting {wait_time}s before retry...")
                    await asyncio.sleep(wait_time)
                    if attempt < max_retries - 1:
                        continue  # Retry immediately after wait
                    else:
                        raise HTTPException(
                            status_code=429,
                            detail="API rate limit exceeded. Please try again in a few minutes or upgrade your plan."
                        )
                
                logger.error(f"Error grading chunk {chunk_idx+1}: {e}")
                if attempt < max_retries - 1:
                    await asyncio.sleep(retry_delay * (2**attempt))
                else:
                    # Return fallback on final failure
                    logger.error(f"Failed to grade chunk {chunk_idx+1} after retries. Using fallback.")
                    # Create consistent fallback for questions likely in this chunk
                    # Since we don't know exactly which questions are in this chunk, we return empty
                    # and let aggregation handle it as "not found"
                    return []

        return []

    # CHUNKED PROCESSING LOGIC
    CHUNK_SIZE = 10
    OVERLAP = 2
    total_student_pages = len(images)
    
    if total_student_pages > 20:
        logger.warning(f"Large document detected ({total_student_pages} pages). Using chunked processing.")
    
    # Create chunks
    chunks = []
    if total_student_pages <= 12: # Use simple processing for small docs (limit raised slightly)
        chunks.append((0, images))
    else:
        for i in range(0, total_student_pages, CHUNK_SIZE - OVERLAP):
            chunk = images[i : i + CHUNK_SIZE]
            if not chunk: break
            chunks.append((i, chunk))
            if i + CHUNK_SIZE >= total_student_pages:
                break
    
    logger.info(f"Processing student paper in {len(chunks)} chunk(s)")

    # Store aggregated results
    # Use deterministic aggregation: Use the FIRST valid score (>=0) encountered
    # This prevents aggregation jitter when multiple chunks see the same question

    question_scores = {} # q_id -> Score data

    all_chunk_results = []
    for idx, (start_idx, chunk_imgs) in enumerate(chunks):
        chunk_scores_data = await process_chunk(chunk_imgs, idx, len(chunks), start_idx)
        all_chunk_results.append(chunk_scores_data)

    # Deterministic Aggregation
    final_scores = []

    for q in questions:
        q_num = q["question_number"]
        best_score_data = None

        # Look for first valid score across chunks
        for chunk_result in all_chunk_results:
            score_data = next((s for s in chunk_result if s["question_number"] == q_num), None)
            
            if score_data and score_data.get("obtained_marks", -1.0) >= 0:
                best_score_data = score_data
                break # Deterministic: Use first valid score found

        # If no valid score found, use the last one (error state or not found) or default
        if not best_score_data:
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
            
        # Handle sub-scores
        final_sub_scores = []
        if q.get("sub_questions"):
            current_subs = best_score_data.get("sub_scores", [])
            current_sub_map = {s["sub_id"]: s for s in current_subs}

            for sq in q["sub_questions"]:
                sq_data = current_sub_map.get(sq["sub_id"])
                if sq_data:
                    marks = sq_data.get("obtained_marks", -1.0)
                    if marks < 0: marks = 0.0

                    final_sub_scores.append(SubQuestionScore(
                        sub_id=sq["sub_id"],
                        max_marks=sq["max_marks"],
                        obtained_marks=min(marks, sq["max_marks"]),
                        ai_feedback=sq_data.get("ai_feedback", "")
                    ))
                else:
                    final_sub_scores.append(SubQuestionScore(
                        sub_id=sq["sub_id"],
                        max_marks=sq["max_marks"],
                        obtained_marks=0.0,
                        ai_feedback="Not attempted/found"
                    ))

        qs_obj = QuestionScore(
            question_number=q_num,
            max_marks=q["max_marks"],
            obtained_marks=min(best_score_data["obtained_marks"], q["max_marks"]),
            ai_feedback=best_score_data["ai_feedback"],
            sub_scores=[s.model_dump() for s in final_sub_scores],
            error_annotations=best_score_data.get("error_annotations", []),
            question_text=q.get("question_text") or q.get("rubric"),
            status=status
        )
        final_scores.append(qs_obj)

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

@api_router.post("/exams/{exam_id}/upload-model-answer")
async def upload_model_answer(
    exam_id: str,
    file: UploadFile = File(...),
    user: User = Depends(get_current_user)
):
    """Upload model answer PDF and AUTO-EXTRACT questions"""
    if user.role != "teacher":
        raise HTTPException(status_code=403, detail="Only teachers can upload model answers")
    
    exam = await db.exams.find_one({"exam_id": exam_id, "teacher_id": user.user_id}, {"_id": 0})
    if not exam:
        raise HTTPException(status_code=404, detail="Exam not found")
    
    # Read and convert PDF to images
    pdf_bytes = await file.read()
    
    # Check file size - limit to 15MB for safety
    if len(pdf_bytes) > 15 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="File too large. Maximum size is 15MB.")
    
    images = pdf_to_images(pdf_bytes)
    
    # Store images in separate collection to avoid MongoDB 16MB document limit
    file_id = str(uuid.uuid4())
    model_answer_data = base64.b64encode(pdf_bytes).decode()
    
    # Store the file data separately
    await db.exam_files.update_one(
        {"exam_id": exam_id, "file_type": "model_answer"},
        {"$set": {
            "exam_id": exam_id,
            "file_type": "model_answer",
            "file_id": file_id,
            "file_data": model_answer_data,
            "images": images,
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

    message = "Model answer uploaded successfully."
    if result.get("success"):
        if result.get("skipped"):
            message = f"✨ Model answer uploaded! Questions kept from {result.get('source').replace('_', ' ')}."
        else:
            message = f"✨ Model answer uploaded & {result.get('count')} questions auto-extracted from {result.get('source').replace('_', ' ')}!"

    return {
        "message": message,
        "pages": len(images),
        "auto_extracted": result.get("success", False),
        "extracted_count": result.get("count", 0),
        "source": result.get("source", "")
    }

@api_router.post("/exams/{exam_id}/upload-question-paper")
async def upload_question_paper(
    exam_id: str,
    file: UploadFile = File(...),
    user: User = Depends(get_current_user)
):
    """Upload question paper PDF and AUTO-EXTRACT questions"""
    if user.role != "teacher":
        raise HTTPException(status_code=403, detail="Only teachers can upload question papers")
    
    exam = await db.exams.find_one({"exam_id": exam_id, "teacher_id": user.user_id}, {"_id": 0})
    if not exam:
        raise HTTPException(status_code=404, detail="Exam not found")
    
    # Read and convert PDF to images
    pdf_bytes = await file.read()
    
    # Check file size - limit to 15MB for safety
    if len(pdf_bytes) > 15 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="File too large. Maximum size is 15MB.")
    
    images = pdf_to_images(pdf_bytes)
    
    # Store images in separate collection to avoid MongoDB 16MB document limit
    file_id = str(uuid.uuid4())
    question_paper_data = base64.b64encode(pdf_bytes).decode()
    
    # Store the file data separately
    await db.exam_files.update_one(
        {"exam_id": exam_id, "file_type": "question_paper"},
        {"$set": {
            "exam_id": exam_id,
            "file_type": "question_paper",
            "file_id": file_id,
            "file_data": question_paper_data,
            "images": images,
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
    """Upload and grade student papers with auto-student creation"""
    if user.role != "teacher":
        raise HTTPException(status_code=403, detail="Only teachers can upload papers")
    
    exam = await db.exams.find_one({"exam_id": exam_id, "teacher_id": user.user_id}, {"_id": 0})
    if not exam:
        raise HTTPException(status_code=404, detail="Exam not found")
    
    # Model answer is now optional
    
    # Update exam status
    await db.exams.update_one(
        {"exam_id": exam_id},
        {"$set": {"status": "processing"}}
    )
    
    submissions = []
    errors = []
    
    for file in files:
        try:
            # Process the PDF first to get images
            pdf_bytes = await file.read()
            images = pdf_to_images(pdf_bytes)
            
            if not images:
                errors.append({
                    "filename": file.filename,
                    "error": "Failed to extract images from PDF"
                })
                continue
            
            # Extract student ID and name from the paper using AI
            student_id, student_name = await extract_student_info_from_paper(images, file.filename)
            
            # Fallback to filename if AI extraction fails
            if not student_id or not student_name:
                filename_id, filename_name = parse_student_from_filename(file.filename)
                
                # Use filename ID if AI didn't find it
                if not student_id and filename_id:
                    student_id = filename_id
                
                # Use filename name if AI didn't find it
                if not student_name and filename_name:
                    student_name = filename_name
                
                # If still no ID or name, report error
                if not student_id and not student_name:
                    errors.append({
                        "filename": file.filename,
                        "error": "Could not extract student ID/name from paper or filename. Please ensure student writes their roll number and name clearly on the answer sheet."
                    })
                    continue
                
                # If we have one but not the other, use what we have
                if not student_id:
                    student_id = f"AUTO_{uuid.uuid4().hex[:6]}"
                if not student_name:
                    student_name = f"Student {student_id}"
            
            # Get or create student
            user_id, error = await get_or_create_student(
                student_id=student_id,
                student_name=student_name,
                batch_id=exam["batch_id"],
                teacher_id=user.user_id
            )
            
            if error:
                errors.append({
                    "filename": file.filename,
                    "student_id": student_id,
                    "error": error
                })
                continue
            
            # Grade with AI using the grading mode from exam
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
            
        except Exception as e:
            logger.error(f"Error processing {file.filename}: {e}")
            errors.append({
                "filename": file.filename,
                "error": str(e)
            })
    
    # Update exam status
    await db.exams.update_one(
        {"exam_id": exam_id},
        {"$set": {"status": "completed"}}
    )
    
    # Create notification for teacher
    await create_notification(
        user_id=user.user_id,
        notification_type="grading_complete",
        title="Grading Complete",
        message=f"Successfully graded {len(submissions)} papers for {exam['exam_name']}",
        link=f"/teacher/review?exam={exam_id}"
    )
    
    result = {
        "processed": len(submissions),
        "submissions": submissions
    }
    
    if errors:
        result["errors"] = errors
    
    return result

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
        # Students see their own submissions
        submissions = await db.submissions.find(
            {"student_id": user.user_id},
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
    
    return submissions

@api_router.get("/submissions/{submission_id}")
async def get_submission(submission_id: str, user: User = Depends(get_current_user)):
    """Get submission details with PDF data and full question text"""
    submission = await db.submissions.find_one(
        {"submission_id": submission_id},
        {"_id": 0}
    )
    if not submission:
        raise HTTPException(status_code=404, detail="Submission not found")
    
    # Enrich with full question text from exam
    exam = await db.exams.find_one(
        {"exam_id": submission["exam_id"]},
        {"_id": 0, "questions": 1}
    )
    
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
    
    return submission

@api_router.put("/submissions/{submission_id}")
async def update_submission(
    submission_id: str,
    updates: dict,
    user: User = Depends(get_current_user)
):
    """Update submission scores and feedback"""
    if user.role != "teacher":
        raise HTTPException(status_code=403, detail="Only teachers can update submissions")
    
    # Calculate new total
    question_scores = updates.get("question_scores", [])
    total_score = sum(qs.get("obtained_marks", 0) for qs in question_scores)
    
    exam = await db.exams.find_one(
        {"exam_id": (await db.submissions.find_one({"submission_id": submission_id}))["exam_id"]},
        {"_id": 0, "total_marks": 1}
    )
    total_marks = exam.get("total_marks", 100) if exam else 100
    percentage = (total_score / total_marks) * 100 if total_marks > 0 else 0
    
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
        {"_id": 0, "submission_id": 1, "student_name": 1, "exam_id": 1, "total_score": 1, "status": 1, "created_at": 1}
    ).sort("created_at", -1).limit(10).to_list(10)
    
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
        {"name": s["student_name"], "student_id": s["student_id"], "score": s["total_score"], "percentage": s["percentage"]}
        for s in sorted_subs[:5]
    ]
    
    # Needs attention (below 40%)
    needs_attention = [
        {"name": s["student_name"], "student_id": s["student_id"], "score": s["total_score"], "percentage": s["percentage"]}
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
            
            # If no topic tags, create a generic topic based on exam subject
            if not topics:
                subject = None
                if exam.get("subject_id"):
                    subject_doc = await db.subjects.find_one({"subject_id": exam["subject_id"]}, {"_id": 0, "name": 1})
                    subject = subject_doc.get("name") if subject_doc else None
                topics = [subject or "General"]
            
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
    # Get student's submissions
    submissions = await db.submissions.find(
        {"student_id": user.user_id},
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
    
    percentages = [s["percentage"] for s in submissions]
    
    # Recent results
    recent = sorted(submissions, key=lambda x: x.get("created_at", ""), reverse=True)[:5]
    recent_results = []
    for r in recent:
        exam = await db.exams.find_one({"exam_id": r["exam_id"]}, {"_id": 0, "exam_name": 1, "subject_id": 1})
        subject = await db.subjects.find_one({"subject_id": exam.get("subject_id")}, {"_id": 0, "name": 1}) if exam else None
        recent_results.append({
            "exam_name": exam.get("exam_name", "Unknown") if exam else "Unknown",
            "subject": subject.get("name", "Unknown") if subject else "Unknown",
            "score": r["total_score"],
            "percentage": r["percentage"],
            "date": r.get("created_at", "")
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
    if len(percentages) >= 2:
        recent_avg = sum(percentages[-3:]) / min(3, len(percentages))
        older_avg = sum(percentages[:-3]) / max(1, len(percentages) - 3) if len(percentages) > 3 else recent_avg
        improvement = round(recent_avg - older_avg, 1)
    else:
        improvement = 0
    
    return {
        "stats": {
            "total_exams": len(submissions),
            "avg_percentage": round(avg_pct, 1),
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
            
            # Get grading mode
            exam = await db.exams.find_one({"exam_id": exam_id}, {"_id": 0, "grading_mode": 1})
            if exam:
                grading_mode = exam.get("grading_mode")
    
    feedback_doc = {
        "feedback_id": feedback_id,
        "teacher_id": user.user_id,
        "submission_id": feedback.submission_id,
        "question_number": feedback.question_number,
        "feedback_type": feedback.feedback_type,
        "question_text": feedback.question_text,
        "student_answer_summary": student_answer_summary,
        "ai_grade": feedback.ai_grade,
        "ai_feedback": feedback.ai_feedback,
        "teacher_expected_grade": feedback.teacher_expected_grade,
        "teacher_correction": feedback.teacher_correction,
        "grading_mode": grading_mode,
        "exam_id": exam_id,
        "is_common": False,
        "upvote_count": 0,
        "created_at": datetime.now(timezone.utc)
    }
    
    await db.grading_feedback.insert_one(feedback_doc)
    
    return {"message": "Feedback submitted successfully", "feedback_id": feedback_id}

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

# Include router and add middleware
app.include_router(api_router)

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
