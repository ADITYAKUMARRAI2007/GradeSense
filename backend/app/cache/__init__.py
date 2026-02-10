"""Cache module for caching questions, answers, and grading results."""

import hashlib
import json
from datetime import datetime, timedelta
from typing import Any, Dict, Optional
from motor.motor_asyncio import AsyncIOMotorDatabase


class GradeSenseCache:
    """Handles caching for questions, model answers, and grading results."""
    
    QUESTIONS_CACHE = "questions_cache"
    MODEL_ANSWER_CACHE = "model_answer_cache"
    GRADING_RESULT_CACHE = "grading_result_cache"
    
    def __init__(self, db: AsyncIOMotorDatabase):
        """Initialize cache with MongoDB database."""
        self.db = db
        self.questions_col = db[self.QUESTIONS_CACHE]
        self.answer_col = db[self.MODEL_ANSWER_CACHE]
        self.grading_col = db[self.GRADING_RESULT_CACHE]
    
    @staticmethod
    def _compute_hash(data: bytes) -> str:
        """Compute SHA256 hash of file data."""
        return hashlib.sha256(data).hexdigest()
    
    # ============ QUESTIONS CACHE ============
    
    async def cache_questions(
        self, 
        exam_id: str, 
        pdf_hash: str, 
        questions: Dict[str, Any]
    ) -> bool:
        """
        Cache extracted questions for an exam's question paper.
        
        Args:
            exam_id: Unique exam identifier
            pdf_hash: SHA256 hash of the question paper PDF
            questions: Extracted questions data {"questions": [{...}]}
        
        Returns:
            True if cached successfully
        """
        try:
            cache_entry = {
                "exam_id": exam_id,
                "pdf_hash": pdf_hash,
                "questions": questions,
                "cached_at": datetime.utcnow(),
                "expires_at": datetime.utcnow() + timedelta(days=30)
            }
            
            # Update or insert
            result = await self.questions_col.update_one(
                {"exam_id": exam_id, "pdf_hash": pdf_hash},
                {"$set": cache_entry},
                upsert=True
            )
            return result.matched_count > 0 or result.upserted_id is not None
        except Exception as e:
            print(f"Error caching questions: {e}")
            return False
    
    async def get_cached_questions(
        self, 
        exam_id: str, 
        pdf_hash: str
    ) -> Optional[Dict[str, Any]]:
        """Get cached questions for exam."""
        try:
            doc = await self.questions_col.find_one({
                "exam_id": exam_id,
                "pdf_hash": pdf_hash,
                "expires_at": {"$gt": datetime.utcnow()}
            })
            return doc["questions"] if doc else None
        except Exception:
            return None
    
    # ============ MODEL ANSWER CACHE ============
    
    async def cache_model_answer(
        self, 
        exam_id: str, 
        question_number: int,
        pdf_hash: str,
        answer_data: Dict[str, Any]
    ) -> bool:
        """
        Cache extracted model answer for a specific question.
        
        Args:
            exam_id: Unique exam identifier
            question_number: Question number in exam
            pdf_hash: SHA256 hash of model answer PDF
            answer_data: Extracted answer {"text": "...", "images": [...]}
        
        Returns:
            True if cached successfully
        """
        try:
            cache_entry = {
                "exam_id": exam_id,
                "question_number": question_number,
                "pdf_hash": pdf_hash,
                "answer_data": answer_data,
                "cached_at": datetime.utcnow(),
                "expires_at": datetime.utcnow() + timedelta(days=30)
            }
            
            result = await self.answer_col.update_one(
                {
                    "exam_id": exam_id,
                    "question_number": question_number,
                    "pdf_hash": pdf_hash
                },
                {"$set": cache_entry},
                upsert=True
            )
            return result.matched_count > 0 or result.upserted_id is not None
        except Exception as e:
            print(f"Error caching model answer: {e}")
            return False
    
    async def get_cached_model_answer(
        self, 
        exam_id: str, 
        question_number: int,
        pdf_hash: str
    ) -> Optional[Dict[str, Any]]:
        """Get cached model answer for question."""
        try:
            doc = await self.answer_col.find_one({
                "exam_id": exam_id,
                "question_number": question_number,
                "pdf_hash": pdf_hash,
                "expires_at": {"$gt": datetime.utcnow()}
            })
            return doc["answer_data"] if doc else None
        except Exception:
            return None
    
    # ============ GRADING RESULT CACHE ============
    
    async def cache_grading_result(
        self, 
        exam_id: str,
        student_answer_hash: str,
        question_number: int,
        grading_result: Dict[str, Any]
    ) -> bool:
        """
        Cache grading result for a student's answer to a question.
        
        Args:
            exam_id: Unique exam identifier
            student_answer_hash: SHA256 hash of student's answer pages
            question_number: Question number
            grading_result: {"marks": 10, "feedback": "...", "confidence": 0.95}
        
        Returns:
            True if cached successfully
        """
        try:
            cache_key = f"{exam_id}_{student_answer_hash}_{question_number}"
            
            cache_entry = {
                "cache_key": cache_key,
                "exam_id": exam_id,
                "student_answer_hash": student_answer_hash,
                "question_number": question_number,
                "result": grading_result,
                "cached_at": datetime.utcnow(),
                "expires_at": datetime.utcnow() + timedelta(days=30)
            }
            
            result = await self.grading_col.update_one(
                {"cache_key": cache_key},
                {"$set": cache_entry},
                upsert=True
            )
            return result.matched_count > 0 or result.upserted_id is not None
        except Exception as e:
            print(f"Error caching grading result: {e}")
            return False
    
    async def get_cached_grading_result(
        self, 
        exam_id: str,
        student_answer_hash: str,
        question_number: int
    ) -> Optional[Dict[str, Any]]:
        """Get cached grading result for student's answer."""
        try:
            cache_key = f"{exam_id}_{student_answer_hash}_{question_number}"
            
            doc = await self.grading_col.find_one({
                "cache_key": cache_key,
                "expires_at": {"$gt": datetime.utcnow()}
            })
            return doc["result"] if doc else None
        except Exception:
            return None
    
    # ============ CLEANUP ============
    
    async def cleanup_expired_cache(self) -> int:
        """Remove expired cache entries. Returns number of deleted documents."""
        try:
            now = datetime.utcnow()
            
            # Clean all cache collections
            q_result = await self.questions_col.delete_many({"expires_at": {"$lt": now}})
            a_result = await self.answer_col.delete_many({"expires_at": {"$lt": now}})
            g_result = await self.grading_col.delete_many({"expires_at": {"$lt": now}})
            
            total = q_result.deleted_count + a_result.deleted_count + g_result.deleted_count
            print(f"✅ Cleaned up {total} expired cache entries")
            return total
        except Exception as e:
            print(f"Error cleaning cache: {e}")
            return 0


# Global cache instance (initialized in main app)
_cache_instance: Optional[GradeSenseCache] = None

def init_cache(db: AsyncIOMotorDatabase) -> GradeSenseCache:
    """Initialize global cache instance."""
    global _cache_instance
    _cache_instance = GradeSenseCache(db)
    return _cache_instance

def get_cache() -> GradeSenseCache:
    """Get global cache instance."""
    if _cache_instance is None:
        raise RuntimeError("Cache not initialized. Call init_cache() first.")
    return _cache_instance
