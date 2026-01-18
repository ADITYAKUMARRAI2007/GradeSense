# Background grading job processing for handling 30+ papers simultaneously
# This module provides asynchronous background processing for paper grading

import asyncio
import logging
from datetime import datetime, timezone
from typing import List, Dict
import base64
import uuid

logger = logging.getLogger(__name__)


async def process_grading_job_in_background(
    job_id: str,
    exam_id: str,
    files_data: List[dict],  # Files with content already read
    exam: dict,
    teacher_id: str,
    db,  # MongoDB database instance
    pdf_to_images,  # Function reference
    extract_student_info_from_paper,  # Function reference
    parse_student_from_filename,  # Function reference
    get_or_create_student,  # Function reference
    get_exam_model_answer_images,  # Function reference
    get_exam_model_answer_text,  # Function reference
    grade_with_ai,  # Function reference
    create_notification  # Function reference
):
    """
    Background task to process papers one by one with progress tracking
    Supports 30+ papers without timeout issues
    Files are already read - content is in files_data
    """
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
        
        logger.info(f"=== BACKGROUND GRADING START === Job {job_id}: {len(files_data)} files for exam {exam_id}")
        
        # Get pre-loaded data (to avoid repeated DB queries)
        model_answer_imgs = await get_exam_model_answer_images(exam_id)
        model_answer_text = await get_exam_model_answer_text(exam_id)
        
        # Get questions
        questions_from_collection = await db.questions.find(
            {"exam_id": exam_id},
            {"_id": 0}
        ).to_list(1000)
        
        if questions_from_collection:
            questions_to_grade = questions_from_collection
        else:
            questions_to_grade = exam.get("questions", [])
        
        if not questions_to_grade:
            logger.error(f"Job {job_id}: No questions found for exam")
            await db.grading_jobs.update_one(
                {"job_id": job_id},
                {"$set": {
                    "status": "failed",
                    "error": "No questions found for this exam",
                    "updated_at": datetime.now(timezone.utc).isoformat()
                }}
            )
            return
        
        # Process each paper - content already read
        for idx, file_data in enumerate(files_data):
            filename = file_data["filename"]
            pdf_bytes = file_data["content"]
            
            logger.info(f"[Job {job_id}] [{idx + 1}/{len(files_data)}] Processing: {filename}")
                if file_size_mb > 30:
                    errors.append({
                        "filename": filename,
                        "error": f"File too large ({file_size_mb:.1f}MB). Maximum 30MB."
                    })
                    await _update_job_progress(db, job_id, idx + 1, len(submissions), len(errors), submissions, errors)
                    continue
                
                # Extract images from PDF
                images = pdf_to_images(pdf_bytes)
                if not images:
                    errors.append({
                        "filename": filename,
                        "error": "Failed to extract images from PDF"
                    })
                    await _update_job_progress(db, job_id, idx + 1, len(submissions), len(errors), submissions, errors)
                    continue
                
                # Extract student info
                student_id, student_name = await extract_student_info_from_paper(images, filename)
                
                # Fallback to filename parsing
                if not student_id or not student_name:
                    filename_id, filename_name = parse_student_from_filename(filename)
                    student_id = student_id or filename_id or f"AUTO_{uuid.uuid4().hex[:6]}"
                    student_name = student_name or filename_name or f"Student {student_id}"
                
                if not student_id and not student_name:
                    errors.append({
                        "filename": filename,
                        "error": "Could not extract student ID/name"
                    })
                    await _update_job_progress(db, job_id, idx + 1, len(submissions), len(errors), submissions, errors)
                    continue
                
                # Get or create student
                user_id, error = await get_or_create_student(
                    student_id=student_id,
                    student_name=student_name,
                    batch_id=exam["batch_id"],
                    teacher_id=teacher_id
                )
                
                if error:
                    errors.append({
                        "filename": filename,
                        "error": error
                    })
                    await _update_job_progress(db, job_id, idx + 1, len(submissions), len(errors), submissions, errors)
                    continue
                
                # Grade with AI
                scores = await grade_with_ai(
                    images=images,
                    model_answer_images=model_answer_imgs,
                    questions=questions_to_grade,
                    grading_mode=exam.get("grading_mode", "balanced"),
                    total_marks=exam.get("total_marks", 100),
                    model_answer_text=model_answer_text
                )
                
                total_score = sum(s.obtained_marks for s in scores)
                percentage = (total_score / exam["total_marks"]) * 100 if exam["total_marks"] > 0 else 0
                
                # Create submission
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
                
                logger.info(f"[Job {job_id}] ✓ {filename}: {student_name} - {total_score}/{exam['total_marks']}")
                
                # Update progress
                await _update_job_progress(db, job_id, idx + 1, len(submissions), len(errors), submissions, errors)
                
            except Exception as e:
                logger.error(f"[Job {job_id}] ✗ Error processing {filename}: {e}")
                errors.append({
                    "filename": filename,
                    "error": str(e)
                })
                await _update_job_progress(db, job_id, idx + 1, len(submissions), len(errors), submissions, errors)
        
        # Mark as completed
        await db.exams.update_one(
            {"exam_id": exam_id},
            {"$set": {"status": "completed"}}
        )
        
        await db.grading_jobs.update_one(
            {"job_id": job_id},
            {"$set": {
                "status": "completed",
                "processed_papers": len(files),
                "successful": len(submissions),
                "failed": len(errors),
                "submissions": submissions,
                "errors": errors,
                "updated_at": datetime.now(timezone.utc).isoformat(),
                "completed_at": datetime.now(timezone.utc).isoformat()
            }}
        )
        
        # Notify teacher
        await create_notification(
            user_id=teacher_id,
            notification_type="grading_complete",
            title="Grading Complete",
            message=f"Successfully graded {len(submissions)} of {len(files)} papers for {exam['exam_name']}",
            link=f"/teacher/review?exam={exam_id}"
        )
        
        logger.info(f"=== JOB COMPLETE === {job_id}: {len(submissions)} successful, {len(errors)} errors")
        
    except Exception as e:
        logger.error(f"[Job {job_id}] CRITICAL ERROR: {e}", exc_info=True)
        await db.grading_jobs.update_one(
            {"job_id": job_id},
            {"$set": {
                "status": "failed",
                "error": str(e),
                "updated_at": datetime.now(timezone.utc).isoformat()
            }}
        )


async def _update_job_progress(db, job_id: str, processed: int, successful: int, failed: int, submissions: List, errors: List):
    """Helper to update job progress"""
    await db.grading_jobs.update_one(
        {"job_id": job_id},
        {"$set": {
            "processed_papers": processed,
            "successful": successful,
            "failed": failed,
            "submissions": submissions,
            "errors": errors,
            "updated_at": datetime.now(timezone.utc).isoformat()
        }}
    )
