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

class QuestionScore(BaseModel):
    question_number: int
    max_marks: float
    obtained_marks: float
    ai_feedback: str
    teacher_comment: Optional[str] = None
    is_reviewed: bool = False
    sub_scores: List[SubQuestionScore] = []  # For sub-question scores

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
        secure=True,
        samesite="none",
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
        {"user_id": student_user_id, "teacher_id": user.user_id},
        {"$set": {
            "name": student.name,
            "email": student.email,
            "batches": student.batches
        }}
    )
    if result.matched_count == 0:
        raise HTTPException(status_code=404, detail="Student not found")
    return {"message": "Student updated"}

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
    
    # Check for duplicate exam name
    existing = await db.exams.find_one({
        "exam_name": exam.exam_name,
        "teacher_id": user.user_id
    })
    if existing:
        raise HTTPException(status_code=400, detail="An exam with this name already exists")
    
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

# ============== FILE UPLOAD & GRADING ==============

def parse_student_from_filename(filename: str) -> tuple:
    """
    Parse student ID and name from filename
    Expected format: StudentID_StudentName.pdf (e.g., STU001_John_Doe.pdf)
    Returns: (student_id, student_name) or (None, None) if parsing fails
    """
    try:
        # Remove .pdf extension
        name_part = filename.replace(".pdf", "").replace(".PDF", "")
        
        # Split by underscore
        parts = name_part.split("_", 1)
        
        if len(parts) == 2:
            student_id = parts[0].strip()
            student_name = parts[1].replace("_", " ").strip().title()
            
            # Validate student ID: alphanumeric, 3-20 characters
            if student_id and 3 <= len(student_id) <= 20 and student_id.replace("-", "").isalnum():
                return (student_id, student_name)
        
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

You will receive the model answer and student answer as images.
Grade each question and provide detailed feedback.

Return your response in this exact JSON format:
{{
  "scores": [
    {{
      "question_number": 1,
      "obtained_marks": 8.5,
      "ai_feedback": "Detailed feedback explaining the grade",
      "sub_scores": [
        {{"sub_id": "a", "obtained_marks": 3, "ai_feedback": "Feedback for part a"}},
        {{"sub_id": "b", "obtained_marks": 2.5, "ai_feedback": "Feedback for part b"}}
      ]
    }}
  ]
}}

If a question has no sub-questions, leave sub_scores as an empty array.
"""
    ).with_model("gemini", "gemini-3-flash-preview")
    
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
    
    # Add model answer images
    for i, img in enumerate(model_answer_images[:3]):
        all_images.append(ImageContent(image_base64=img))
    
    # Add student answer images
    for i, img in enumerate(images[:5]):
        all_images.append(ImageContent(image_base64=img))
    
    user_message = UserMessage(
        text=f"""Grade this student's handwritten answer paper.

Questions to grade:
{questions_text}

The first {min(len(model_answer_images), 3)} image(s) show the MODEL ANSWER (reference).
The remaining images show the STUDENT'S ANSWER PAPER.

IMPORTANT: Apply {grading_mode.upper()} grading mode as instructed.
Please grade each question and provide constructive feedback.
Return valid JSON only.""",
        file_contents=all_images
    )
    
    try:
        response = await chat.send_message(user_message)
        
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
                    sub_scores=[s.model_dump() for s in sub_scores]
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
    
    if not exam.get("model_answer_images"):
        raise HTTPException(status_code=400, detail="Upload model answer first")
    
    # Update exam status
    await db.exams.update_one(
        {"exam_id": exam_id},
        {"$set": {"status": "processing"}}
    )
    
    submissions = []
    errors = []
    
    for file in files:
        try:
            # Parse student ID and name from filename
            student_id, student_name = parse_student_from_filename(file.filename)
            
            if not student_id or not student_name:
                errors.append({
                    "filename": file.filename,
                    "error": "Invalid filename format. Expected: StudentID_StudentName.pdf (e.g., STU001_John_Doe.pdf)"
                })
                continue
            
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
            
            # Process the PDF
            pdf_bytes = await file.read()
            images = pdf_to_images(pdf_bytes)
            
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
    """Get submission details with PDF data"""
    submission = await db.submissions.find_one(
        {"submission_id": submission_id},
        {"_id": 0}
    )
    if not submission:
        raise HTTPException(status_code=404, detail="Submission not found")
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
    
    await db.re_evaluations.update_one(
        {"request_id": request_id},
        {"$set": {
            "status": updates.get("status", "resolved"),
            "response": updates.get("response", "")
        }}
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
        {"name": s["student_name"], "score": s["total_score"], "percentage": s["percentage"]}
        for s in sorted_subs[:5]
    ]
    
    # Needs attention (below 40%)
    needs_attention = [
        {"name": s["student_name"], "score": s["total_score"], "percentage": s["percentage"]}
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
