"""
Exam management routes.

Endpoints:
- POST /api/exams/{exam_id}/upload-question-paper
- POST /api/exams/{exam_id}/upload-model-answer
- POST /api/exams/{exam_id}/upload-student-papers
- GET /api/exams/{exam_id}/status
"""

from fastapi import APIRouter, UploadFile, File, HTTPException
from motor.motor_asyncio import AsyncIOMotorDatabase

from ..services import GradeOrchestrationService
from ..utils import validate_file_type, validate_file_size
from ..config.settings import settings

router = APIRouter(prefix="/api/exams", tags=["exams"])


def create_exam_routes(db: AsyncIOMotorDatabase) -> APIRouter:
    """Create exam routes with database connection."""
    
    orchestrator = GradeOrchestrationService(db)
    
    @router.post("/{exam_id}/upload-question-paper")
    async def upload_question_paper(exam_id: str, file: UploadFile = File(...)):
        """Upload and process question paper."""
        try:
            # Validate file
            is_valid, msg = validate_file_type(file.filename, settings.ALLOWED_EXTENSIONS)
            if not is_valid:
                raise HTTPException(status_code=400, detail=msg)
            
            file_bytes = await file.read()
            
            is_valid, msg = validate_file_size(file_bytes, settings.MAX_FILE_SIZE_MB)
            if not is_valid:
                raise HTTPException(status_code=400, detail=msg)
            
            # Process
            result = await orchestrator.process_question_paper(exam_id, file_bytes)
            
            if not result["success"]:
                raise HTTPException(status_code=400, detail=result.get("error"))
            
            return result
            
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))
    
    @router.post("/{exam_id}/upload-model-answer")
    async def upload_model_answer(exam_id: str, file: UploadFile = File(...)):
        """Upload and process model answer sheet."""
        try:
            # Validate file
            is_valid, msg = validate_file_type(file.filename, settings.ALLOWED_EXTENSIONS)
            if not is_valid:
                raise HTTPException(status_code=400, detail=msg)
            
            file_bytes = await file.read()
            
            is_valid, msg = validate_file_size(file_bytes, settings.MAX_FILE_SIZE_MB)
            if not is_valid:
                raise HTTPException(status_code=400, detail=msg)
            
            # Get question numbers from exam
            exam = await db.exams.find_one({"exam_id": exam_id})
            if not exam:
                raise HTTPException(status_code=404, detail="Exam not found")
            
            questions = exam.get("questions", [])
            question_numbers = [q.get("question_number") for q in questions]
            
            # Process
            result = await orchestrator.process_model_answer(
                exam_id=exam_id,
                pdf_bytes=file_bytes,
                question_numbers=question_numbers
            )
            
            if not result["success"]:
                raise HTTPException(status_code=400, detail=result.get("error"))
            
            return result
            
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))
    
    @router.get("/{exam_id}/status")
    async def get_exam_status(exam_id: str):
        """Get exam status."""
        try:
            exam = await db.exams.find_one(
                {"exam_id": exam_id},
                {"_id": 0}
            )
            
            if not exam:
                raise HTTPException(status_code=404, detail="Exam not found")
            
            return exam
            
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))
    
    return router
