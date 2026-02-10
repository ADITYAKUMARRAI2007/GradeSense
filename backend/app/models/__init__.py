"""Database models using Pydantic for validation."""

from typing import List, Optional, Dict, Any
from pydantic import BaseModel
from datetime import datetime


# ============ QUESTION ============
class SubQuestion(BaseModel):
    sub_num: str  # "a", "b", "c"
    text: str
    marks: float


class Question(BaseModel):
    question_number: int
    question_text: str
    max_marks: float
    rubric: str
    sub_questions: Optional[List[SubQuestion]] = None


# ============ EXAM ============
class Exam(BaseModel):
    exam_id: str
    teacher_id: str
    batch_id: str
    exam_name: str
    subject_id: Optional[str]
    total_marks: float
    grading_mode: str = "balanced"  # strict, balanced, conceptual, lenient
    questions: List[Question] = []
    question_paper_hash: Optional[str]
    model_answer_hash: Optional[str]
    status: str = "pending"  # pending, ready_to_grade, grading, completed
    created_at: datetime = datetime.utcnow()
    updated_at: datetime = datetime.utcnow()


# ============ GRADING ============
class SubScore(BaseModel):
    sub_question: str  # "a", "b", etc.
    obtained_marks: float
    total_marks: float
    feedback: str


class QuestionScore(BaseModel):
    question_number: int
    obtained_marks: float
    total_marks: float
    status: str  # correct, partial, incorrect, not_found, blank
    confidence: float  # 0.0 to 1.0
    feedback: str
    sub_scores: Optional[List[SubScore]] = None


class Submission(BaseModel):
    submission_id: str
    exam_id: str
    student_id: str
    student_name: str
    obtained_marks: float = 0
    total_marks: float = 0
    percentage: float = 0
    scores: List[QuestionScore] = []
    status: str = "pending"  # pending, graded, reviewed, finalized
    created_at: datetime = datetime.utcnow()
    graded_at: Optional[datetime] = None


# ============ CACHING ============
class CacheEntry(BaseModel):
    cache_key: str
    data: Dict[str, Any]
    expires_at: datetime


# ============ GRADING JOB ============
class GradingJob(BaseModel):
    job_id: str
    exam_id: str
    teacher_id: str
    total_papers: int
    processed_papers: int = 0
    successful: int = 0
    failed: int = 0
    status: str = "pending"  # pending, processing, completed, failed, cancelled
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    errors: List[Dict[str, str]] = []  # [{paper_name, error_message}]
