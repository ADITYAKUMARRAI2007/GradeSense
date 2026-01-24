"""Admin and system management Pydantic models"""

from pydantic import BaseModel
from typing import Optional, Dict, Any


class UserFeatureFlags(BaseModel):
    """Feature flags for user access control"""
    ai_suggestions: bool = True
    sub_questions: bool = True
    bulk_upload: bool = True
    analytics: bool = True
    peer_comparison: bool = True
    export_data: bool = True


class UserQuotas(BaseModel):
    """Usage quotas for users"""
    max_exams_per_month: int = 100
    max_papers_per_month: int = 1000
    max_students: int = 500
    max_batches: int = 50


class UserStatusUpdate(BaseModel):
    """Model for updating user status"""
    status: str  # 'active', 'disabled', 'banned'
    reason: Optional[str] = None


class UserFeedback(BaseModel):
    """Model for user-submitted feedback"""
    type: str  # 'bug', 'suggestion', 'question'
    data: Dict[str, Any]
    metadata: Optional[Dict[str, Any]] = None
