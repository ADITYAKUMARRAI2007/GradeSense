"""
Grading routes.

Endpoints:
- POST /api/grading/grade-papers
- GET /api/grading/job/{job_id}/status
- POST /api/grading/job/{job_id}/cancel
"""

from fastapi import APIRouter, UploadFile, File, HTTPException, Form
from motor.motor_asyncio import AsyncIOMotorDatabase
from typing import List
import uuid
from datetime import datetime

from ..services import GradeOrchestrationService
from ..utils import validate_file_type, validate_file_size
from ..config.settings import settings

router = APIRouter(prefix="/api/grading", tags=["grading"])


def create_grading_routes(db: AsyncIOMotorDatabase) -> APIRouter:
    """Create grading routes with database connection."""
    
    orchestrator = GradeOrchestrationService(db)
    
    @router.post("/grade-papers")
    async def grade_student_papers(
        exam_id: str = Form(...),
        grading_mode: str = Form("balanced"),
        files: List[UploadFile] = File(...)
    ):
        """
        Grade student papers.
        
        Creates a grading job that processes all papers.
        Returns job_id for tracking progress.
        """
        try:
            # Validate exam exists
            exam = await db.exams.find_one({"exam_id": exam_id})
            if not exam:
                raise HTTPException(status_code=404, detail="Exam not found")
            
            # Validate grading mode
            if grading_mode not in ["strict", "balanced", "conceptual", "lenient"]:
                raise HTTPException(status_code=400, detail="Invalid grading mode")
            
            # Validate and process files
            student_papers = {}
            
            for file in files:
                # Validate
                is_valid, msg = validate_file_type(file.filename, settings.ALLOWED_EXTENSIONS)
                if not is_valid:
                    raise HTTPException(status_code=400, detail=f"{file.filename}: {msg}")
                
                file_bytes = await file.read()
                
                is_valid, msg = validate_file_size(file_bytes, settings.MAX_FILE_SIZE_MB)
                if not is_valid:
                    raise HTTPException(status_code=400, detail=f"{file.filename}: {msg}")
                
                # Use filename as student identifier
                student_id = file.filename.rsplit('.', 1)[0]
                student_papers[student_id] = file_bytes
            
            # Create grading job record
            job_id = str(uuid.uuid4())
            
            job_doc = {
                "job_id": job_id,
                "exam_id": exam_id,
                "total_papers": len(student_papers),
                "processed_papers": 0,
                "successful": 0,
                "failed": 0,
                "status": "processing",
                "started_at": datetime.utcnow(),
                "errors": [],
                "results": {}
            }
            
            await db.grading_jobs.insert_one(job_doc)
            
            # Start grading (in background via asyncio.create_task in main app)
            # For now, grade synchronously
            result = await orchestrator.grade_student_papers(
                exam_id=exam_id,
                student_papers=student_papers,
                grading_mode=grading_mode
            )
            
            # Update job status
            if result["success"]:
                successful = len([r for r in result["results"].values() if "error" not in r])
                
                await db.grading_jobs.update_one(
                    {"job_id": job_id},
                    {
                        "$set": {
                            "status": "completed",
                            "successful": successful,
                            "failed": len(result["results"]) - successful,
                            "processed_papers": len(result["results"]),
                            "results": result["results"],
                            "completed_at": datetime.utcnow()
                        }
                    }
                )
            else:
                await db.grading_jobs.update_one(
                    {"job_id": job_id},
                    {
                        "$set": {
                            "status": "failed",
                            "errors": [{"error": result.get("error", "Unknown error")}]
                        }
                    }
                )
            
            return {
                "job_id": job_id,
                "status": "started",
                "exam_id": exam_id,
                "papers": len(student_papers)
            }
            
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))
    
    @router.get("/job/{job_id}/status")
    async def get_job_status(job_id: str):
        """Get grading job status."""
        try:
            job = await db.grading_jobs.find_one(
                {"job_id": job_id},
                {"_id": 0}
            )
            
            if not job:
                raise HTTPException(status_code=404, detail="Job not found")
            
            return job
            
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))
    
    @router.post("/job/{job_id}/cancel")
    async def cancel_job(job_id: str):
        """Cancel grading job."""
        try:
            result = await db.grading_jobs.update_one(
                {"job_id": job_id},
                {"$set": {"status": "cancelled"}}
            )
            
            if result.matched_count == 0:
                raise HTTPException(status_code=404, detail="Job not found")
            
            return {"job_id": job_id, "status": "cancelled"}
            
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))
    
    return router
