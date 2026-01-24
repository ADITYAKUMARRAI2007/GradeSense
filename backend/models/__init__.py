"""Pydantic models for GradeSense application"""

from .user import User, UserCreate, ProfileUpdate
from .batch import Batch, BatchCreate
from .subject import Subject, SubjectCreate
from .exam import (
    SubQuestion,
    ExamQuestion,
    Exam,
    ExamCreate,
    StudentExamCreate,
    StudentSubmission
)
from .submission import (
    SubQuestionScore,
    QuestionScore,
    Submission
)
from .reevaluation import ReEvaluationRequest, ReEvaluationCreate
from .feedback import GradingFeedback, FeedbackSubmit
from .analytics import NaturalLanguageQuery, GradingAnalytics, FrontendEvent
from .admin import UserFeatureFlags, UserQuotas, UserStatusUpdate, UserFeedback

__all__ = [
    # User models
    "User",
    "UserCreate",
    "ProfileUpdate",
    
    # Batch models
    "Batch",
    "BatchCreate",
    
    # Subject models
    "Subject",
    "SubjectCreate",
    
    # Exam models
    "SubQuestion",
    "ExamQuestion",
    "Exam",
    "ExamCreate",
    "StudentExamCreate",
    "StudentSubmission",
    
    # Submission models
    "SubQuestionScore",
    "QuestionScore",
    "Submission",
    
    # Re-evaluation models
    "ReEvaluationRequest",
    "ReEvaluationCreate",
    
    # Feedback models
    "GradingFeedback",
    "FeedbackSubmit",
    
    # Analytics models
    "NaturalLanguageQuery",
    "GradingAnalytics",
    "FrontendEvent",
    
    # Admin models
    "UserFeatureFlags",
    "UserQuotas",
    "UserStatusUpdate",
    "UserFeedback",
]
