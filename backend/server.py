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
    
    # Question-wise analysis (weak areas)
    weak_areas = []
    strong_areas = []
    for sub in submissions[-5:]:  # Last 5 exams
        for qs in sub.get("question_scores", []):
            pct = (qs["obtained_marks"] / qs["max_marks"]) * 100 if qs["max_marks"] > 0 else 0
            if pct < 50:
                weak_areas.append(f"Q{qs['question_number']}: {pct:.0f}%")
            elif pct >= 80:
                strong_areas.append(f"Q{qs['question_number']}: {pct:.0f}%")
    
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
        "weak_areas": weak_areas[:5],
        "strong_areas": strong_areas[:5],
        "recommendations": [
            "Focus on improving weak areas identified above",
            "Maintain consistency in strong subjects",
            "Practice more numerical problems" if any("Math" in s for s in subject_performance.keys()) else "Review conceptual topics"
        ]
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
                _, filename_name = parse_student_from_filename(file.filename)
                if not student_id and not student_name:
                    errors.append({
                        "filename": file.filename,
                        "error": "Could not extract student ID/name from paper or filename. Please ensure student writes their roll number and name clearly on the answer sheet."
                    })
                    continue
                # Use filename name if we have it
                if filename_name and not student_name:
                    student_name = filename_name
            
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
            scores = await grade_with_ai(
                images=images,
                model_answer_images=exam.get("model_answer_images", []),
                questions=exam.get("questions", []),
                grading_mode=exam.get("grading_mode", "balanced"),
                total_marks=exam.get("total_marks", 100)
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
    
    # Check for duplicate exam name within the same batch
    existing = await db.exams.find_one({
        "exam_name": exam.exam_name,
        "batch_id": exam.batch_id,
        "teacher_id": user.user_id
    })
    if existing:
        raise HTTPException(status_code=400, detail="An exam with this name already exists in this batch")
    
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
    return {"exam_id": exam_id, "status": "draft"}

@api_router.get("/exams/{exam_id}")
async def get_exam(exam_id: str, user: User = Depends(get_current_user)):
    """Get exam details"""
    exam = await db.exams.find_one({"exam_id": exam_id}, {"_id": 0})
    if not exam:
        raise HTTPException(status_code=404, detail="Exam not found")
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
    
    # Delete the exam
    result = await db.exams.delete_one({"exam_id": exam_id, "teacher_id": user.user_id})
    
    if result.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Exam not found")
    
    return {"message": "Exam deleted successfully"}

@api_router.post("/exams/{exam_id}/extract-questions")
async def extract_and_update_questions(exam_id: str, user: User = Depends(get_current_user)):
    """Extract question text from model answer and update exam rubrics"""
    if user.role != "teacher":
        raise HTTPException(status_code=403, detail="Only teachers can update exams")
    
    # Get exam
    exam = await db.exams.find_one({"exam_id": exam_id, "teacher_id": user.user_id}, {"_id": 0})
    if not exam:
        raise HTTPException(status_code=404, detail="Exam not found")
    
    # Check if model answer exists
    if not exam.get("model_answer_images"):
        raise HTTPException(status_code=400, detail="No model answer found. Please upload model answer first.")
    
    # Extract questions from model answer
    extracted_questions = await extract_questions_from_model_answer(
        exam["model_answer_images"],
        len(exam.get("questions", []))
    )
    
    if not extracted_questions:
        raise HTTPException(status_code=500, detail="Failed to extract questions from model answer")
    
    # Update question rubrics
    questions = exam.get("questions", [])
    updated_count = 0
    
    for i, q in enumerate(questions):
        if i < len(extracted_questions):
            q["rubric"] = extracted_questions[i]
            updated_count += 1
    
    # Update exam in database
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
    from emergentintegrations.llm.chat import LlmChat, UserMessage, ImageContent
    
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
- Look for labels like "Roll No", "Roll Number", "Student ID", "ID No", "Reg No", etc.
- Student name is usually written at the top of the page
- If you cannot find either field, use null
- Do NOT include any explanation, ONLY return the JSON"""
        ).with_model("openai", "gpt-4o")
        
        # Use first page (usually has student info)
        image_content = ImageContent(image_base64=file_images[0])
        
        user_message = UserMessage(
            text="Extract the student ID/roll number and name from this answer sheet.",
            images=[image_content]
        )
        
        response = await asyncio.to_thread(chat.send_message, user_message)
        response_text = response.text.strip()
        
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
    Parse student name from filename (student ID is now extracted from paper)
    Optional format: StudentName.pdf or any filename
    Returns: (None, student_name) - ID will be extracted from paper
    """
    try:
        # Remove .pdf extension
        name_part = filename.replace(".pdf", "").replace(".PDF", "")
        
        # Clean up the filename to get potential name
        student_name = name_part.replace("_", " ").replace("-", " ").strip().title()
        
        # Return None for ID (will be extracted from paper), and cleaned name
        if student_name and len(student_name) >= 2:
            return (None, student_name)
        
        return (None, None)
    except Exception as e:
        logger.error(f"Error parsing filename {filename}: {e}")
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
        # Student exists - verify name matches
        if existing["name"].lower() != student_name.lower():
            return (None, f"Student ID {student_id} already exists with different name: {existing['name']}")
        
        # Student exists with same name - add to batch if not already there
        user_id = existing["user_id"]
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
    """Convert PDF pages to base64 images"""
    images = []
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    
    for page_num in range(min(len(doc), 10)):  # Limit to 10 pages
        page = doc[page_num]
        pix = page.get_pixmap(matrix=fitz.Matrix(2, 2))  # 2x zoom for better quality
        img_bytes = pix.tobytes("jpeg")
        img_base64 = base64.b64encode(img_bytes).decode()
        images.append(img_base64)
    
    doc.close()
    return images

async def extract_questions_from_model_answer(
    model_answer_images: List[str],
    num_questions: int
) -> List[str]:
    """Extract question text from model answer images using AI"""
    from emergentintegrations.llm.chat import LlmChat, UserMessage, ImageContent
    
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

Return this exact JSON format:
{
  "questions": [
    "Full text of question 1 here",
    "Full text of question 2 here"
  ]
}

Important:
- Extract the complete question text including any sub-parts (a, b, c)
- Do NOT include answer content
- Maintain original question numbering if visible
- If you can't find all questions, return what you find
"""
        ).with_model("openai", "gpt-4o")
        
        # Create image contents
        image_contents = [ImageContent(image_base64=img) for img in model_answer_images[:5]]
        
        user_message = UserMessage(
            text=f"""Extract the question text from these model answer images.
            
Expected number of questions: {num_questions}

Extract each question's complete text.""",
            images=image_contents
        )
        
        response = await asyncio.to_thread(chat.send_message, user_message)
        response_text = response.text.strip()
        
        # Parse JSON response
        if "```json" in response_text:
            response_text = response_text.split("```json")[1].split("```")[0].strip()
        elif "```" in response_text:
            response_text = response_text.split("```")[1].split("```")[0].strip()
        
        import json
        result = json.loads(response_text)
        return result.get("questions", [])
        
    except Exception as e:
        logger.error(f"Error extracting questions: {e}")
        return []

async def grade_with_ai(
    images: List[str],
    model_answer_images: List[str],
    questions: List[dict],
    grading_mode: str,
    total_marks: float
) -> List[QuestionScore]:
    """Grade answer paper using Gemini Vision"""
    from emergentintegrations.llm.chat import LlmChat, UserMessage, ImageContent
    
    api_key = os.environ.get('EMERGENT_LLM_KEY')
    if not api_key:
        raise HTTPException(status_code=500, detail="AI service not configured")
    
    # Grading mode instructions
    mode_instructions = {
        "strict": "Grade STRICTLY. Require exact match with model answer. Give minimal partial credit. Deduct marks for any deviation from expected answer.",
        "balanced": "Grade FAIRLY. Consider both accuracy and conceptual understanding. Give reasonable partial credit for partially correct answers.",
        "conceptual": "Grade for UNDERSTANDING. Focus on whether the student understands the concept, even if wording differs from model answer. Be generous with partial credit.",
        "lenient": "Grade LENIENTLY. Reward any reasonable attempt. Give generous partial credit. Focus on what the student got right rather than wrong."
    }
    
    grading_instruction = mode_instructions.get(grading_mode, mode_instructions["balanced"])
    
    chat = LlmChat(
        api_key=api_key,
        session_id=f"grading_{uuid.uuid4().hex[:8]}",
        system_message=f"""You are an expert exam grader for handwritten answer papers.

GRADING MODE: {grading_mode.upper()}
{grading_instruction}

You will receive student answer images {'and model answer images for reference' if model_answer_images else ''}.
Grade each question based on the rubric and provide detailed feedback.

Return your response in this exact JSON format:
{{
  "scores": [
    {{
      "question_number": 1,
      "obtained_marks": 8.5,
      "ai_feedback": "Detailed feedback explaining the grade",
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
      ]
    }}
  ]
}}

For error_annotations:
- error_type: Type of mistake (calculation_error, conceptual_error, incomplete, spelling, formatting)
- description: Brief explanation of what went wrong
- severity: How serious the error is (minor=small deduction, moderate=significant, major=most marks lost)
- page: Which page of the answer sheet (1-indexed)
- region: Where on the page (top, middle, bottom third)

If a question has no sub-questions, leave sub_scores as an empty array.
If there are no errors, leave error_annotations as an empty array.
"""
    ).with_model("openai", "gpt-4o")
    
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
    
    # Create image contents list
    all_images = []
    
    # Add model answer images if provided (now optional)
    if model_answer_images:
        for i, img in enumerate(model_answer_images[:3]):
            all_images.append(ImageContent(image_base64=img))
    
    # Add student answer images
    for i, img in enumerate(images[:5]):
        all_images.append(ImageContent(image_base64=img))
    
    # Construct prompt based on whether model answer is available
    if model_answer_images:
        prompt_text = f"""Grade this student's handwritten answer paper.

Questions to grade:
{questions_text}

The first {min(len(model_answer_images), 3)} image(s) show the MODEL ANSWER (reference).
The remaining images show the STUDENT'S ANSWER PAPER.

IMPORTANT: Apply {grading_mode.upper()} grading mode as instructed.
Please grade each question and provide constructive feedback.
Return valid JSON only."""
    else:
        prompt_text = f"""Grade this student's handwritten answer paper WITHOUT a model answer.

Questions to grade:
{questions_text}

The images show the STUDENT'S ANSWER PAPER.

IMPORTANT: 
- Apply {grading_mode.upper()} grading mode as instructed.
- Use the provided rubrics and your knowledge to assess correctness.
- Focus on conceptual understanding, calculation accuracy, and completeness.
- Provide constructive feedback to help the student improve.

Return valid JSON only."""
    
    user_message = UserMessage(
        text=prompt_text,
        file_contents=all_images
    )
    
    try:
        ai_response = await chat.send_message(user_message)
        
        # Parse the response
        import json
        response_text = response.strip()
        if response_text.startswith("```"):
            response_text = response_text.split("```")[1]
            if response_text.startswith("json"):
                response_text = response_text[4:]
        
        result = json.loads(response_text)
        scores = []
        
        for q in questions:
            q_num = q["question_number"]
            score_data = next(
                (s for s in result.get("scores", []) if s["question_number"] == q_num),
                None
            )
            
            if score_data:
                # Handle sub-scores
                sub_scores = []
                if q.get("sub_questions") and score_data.get("sub_scores"):
                    for sq in q["sub_questions"]:
                        sq_data = next(
                            (ss for ss in score_data.get("sub_scores", []) if ss.get("sub_id") == sq["sub_id"]),
                            None
                        )
                        if sq_data:
                            sub_scores.append(SubQuestionScore(
                                sub_id=sq["sub_id"],
                                max_marks=sq["max_marks"],
                                obtained_marks=min(sq_data.get("obtained_marks", 0), sq["max_marks"]),
                                ai_feedback=sq_data.get("ai_feedback", "")
                            ))
                        else:
                            sub_scores.append(SubQuestionScore(
                                sub_id=sq["sub_id"],
                                max_marks=sq["max_marks"],
                                obtained_marks=sq["max_marks"] * 0.5,
                                ai_feedback="Could not grade this sub-question"
                            ))
                
                scores.append(QuestionScore(
                    question_number=q_num,
                    max_marks=q["max_marks"],
                    obtained_marks=min(score_data["obtained_marks"], q["max_marks"]),
                    ai_feedback=score_data["ai_feedback"],
                    sub_scores=[s.model_dump() for s in sub_scores],
                    error_annotations=score_data.get("error_annotations", [])
                ))
            else:
                scores.append(QuestionScore(
                    question_number=q_num,
                    max_marks=q["max_marks"],
                    obtained_marks=q["max_marks"] * 0.5,
                    ai_feedback="Could not grade this question"
                ))
        
        return scores
    except Exception as e:
        logger.error(f"AI grading error: {e}")
        # Return default scores on error
        return [
            QuestionScore(
                question_number=q["question_number"],
                max_marks=q["max_marks"],
                obtained_marks=q["max_marks"] * 0.5,
                ai_feedback=f"Auto-graded: Please review manually. Error: {str(e)}"
            )
            for q in questions
        ]

@api_router.post("/exams/{exam_id}/upload-model-answer")
async def upload_model_answer(
    exam_id: str,
    file: UploadFile = File(...),
    user: User = Depends(get_current_user)
):
    """Upload model answer PDF"""
    if user.role != "teacher":
        raise HTTPException(status_code=403, detail="Only teachers can upload model answers")
    
    exam = await db.exams.find_one({"exam_id": exam_id, "teacher_id": user.user_id})
    if not exam:
        raise HTTPException(status_code=404, detail="Exam not found")
    
    # Read and convert PDF to images
    pdf_bytes = await file.read()
    images = pdf_to_images(pdf_bytes)
    
    # Store as base64
    model_answer_data = base64.b64encode(pdf_bytes).decode()
    
    await db.exams.update_one(
        {"exam_id": exam_id},
        {"$set": {
            "model_answer_file": model_answer_data,
            "model_answer_images": images
        }}
    )
    
    return {"message": "Model answer uploaded", "pages": len(images)}

@api_router.post("/exams/{exam_id}/upload-question-paper")
async def upload_question_paper(
    exam_id: str,
    file: UploadFile = File(...),
    user: User = Depends(get_current_user)
):
    """Upload question paper PDF"""
    if user.role != "teacher":
        raise HTTPException(status_code=403, detail="Only teachers can upload question papers")
    
    exam = await db.exams.find_one({"exam_id": exam_id, "teacher_id": user.user_id})
    if not exam:
        raise HTTPException(status_code=404, detail="Exam not found")
    
    # Read and convert PDF to images
    pdf_bytes = await file.read()
    images = pdf_to_images(pdf_bytes)
    
    await db.exams.update_one(
        {"exam_id": exam_id},
        {"$set": {
            "question_paper_images": images
        }}
    )
    
    return {"message": "Question paper uploaded", "pages": len(images)}

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
                _, filename_name = parse_student_from_filename(file.filename)
                if not student_id and not student_name:
                    errors.append({
                        "filename": file.filename,
                        "error": "Could not extract student ID/name from paper. Please ensure student writes their roll number and name clearly."
                    })
                    continue
                # Use filename name if we have it
                if filename_name and not student_name:
                    student_name = filename_name
            
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
            scores = await grade_with_ai(
                images=images,
                model_answer_images=exam.get("model_answer_images", []),
                questions=exam.get("questions", []),
                grading_mode=exam.get("grading_mode", "balanced"),
                total_marks=exam.get("total_marks", 100)
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
            ).with_model("openai", "gpt-4o")
            
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
            ).with_model("openai", "gpt-4o")
            
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
        ).with_model("openai", "gpt-4o")
        
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
        ).with_model("openai", "gpt-4o")
        
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
    
    # Weak areas analysis
    weak_areas = []
    strong_areas = []
    for sub in submissions[-3:]:
        for qs in sub.get("question_scores", []):
            pct = (qs["obtained_marks"] / qs["max_marks"]) * 100 if qs["max_marks"] > 0 else 0
            if pct < 50:
                weak_areas.append({
                    "question": f"Q{qs['question_number']}",
                    "score": f"{qs['obtained_marks']}/{qs['max_marks']}",
                    "feedback": qs.get("ai_feedback", "")[:100]
                })
            elif pct >= 80:
                strong_areas.append({
                    "question": f"Q{qs['question_number']}",
                    "score": f"{qs['obtained_marks']}/{qs['max_marks']}"
                })
    
    # Recommendations
    recommendations = []
    if weak_areas:
        recommendations.append("Focus on improving accuracy in detailed answer questions")
    avg_pct = sum(percentages) / len(percentages)
    if avg_pct < 60:
        recommendations.append("Consider regular revision sessions")
        recommendations.append("Practice more problems from previous exam topics")
    else:
        recommendations.append("Great progress! Keep up the consistent effort")
    recommendations.append("Review feedback on weak areas to understand mistakes")
    
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
        "weak_areas": weak_areas[:5],
        "strong_areas": strong_areas[:5]
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
