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
        # CRITICAL: Check if exam still exists before starting
        exam_check = await db.exams.find_one({"exam_id": exam_id})
        if not exam_check:
            logger.warning(f"Job {job_id}: Exam {exam_id} was deleted. Cancelling job.")
            await db.grading_jobs.update_one(
                {"job_id": job_id},
                {"$set": {
                    "status": "cancelled",
                    "cancellation_reason": "Exam deleted",
                    "updated_at": datetime.now(timezone.utc).isoformat()
                }}
            )
            return
        
        # Check if job is already cancelled
        job_check = await db.grading_jobs.find_one({"job_id": job_id}, {"_id": 0, "status": 1})
        if job_check and job_check.get("status") == "cancelled":
            logger.info(f"Job {job_id} was cancelled before processing started")
            return
        
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
            # Check if job was cancelled during processing
            job_status_check = await db.grading_jobs.find_one({"job_id": job_id}, {"_id": 0, "status": 1})
            if job_status_check and job_status_check.get("status") == "cancelled":
                logger.info(f"Job {job_id} was cancelled during processing at paper {idx+1}/{len(files_data)}")
                return
            
            filename = file_data["filename"]
            pdf_bytes = file_data["content"]
            
            logger.info(f"[Job {job_id}] [{idx + 1}/{len(files_data)}] Processing: {filename}")
            logger.info(f"[Job {job_id}] File size: {len(pdf_bytes) / (1024*1024):.2f}MB")
            
            try:
                # Add timeout for entire paper processing (5 minutes max per paper)
                import asyncio
                
                async def process_single_paper():
                    # Ensure we have bytes
                    if not isinstance(pdf_bytes, bytes):
                        logger.error(f"[Job {job_id}] ERROR: pdf_bytes is not bytes type, it is {type(pdf_bytes)}")
                        return {
                            "error": f"Invalid file data type: {type(pdf_bytes)}",
                            "filename": filename
                        }
                    
                    # Check file size
                    file_size_mb = len(pdf_bytes) / (1024 * 1024)
                    if file_size_mb > 30:
                        return {
                            "error": f"File too large ({file_size_mb:.1f}MB). Maximum 30MB.",
                            "filename": filename
                        }
                    
                    logger.info(f"[Job {job_id}] Converting PDF to images...")
                    paper_images = pdf_to_images(pdf_bytes)
                    
                    if not paper_images:
                        return {
                            "error": "Failed to extract images from PDF",
                            "filename": filename
                        }
                    
                    logger.info(f"[Job {job_id}] Extracted {len(paper_images)} pages")
                    
                    # Extract student info (returns tuple: student_id, student_name)
                    student_info = await extract_student_info_from_paper(paper_images, filename)
                    
                    # Unpack tuple properly (2 values: id and name only)
                    if student_info and len(student_info) == 2 and student_info[0] and student_info[1]:
                        student_id_from_paper, student_name = student_info
                        logger.info(f"[Job {job_id}] Extracted student info from paper: {student_name} (ID: {student_id_from_paper})")
                    else:
                        # AI extraction failed, try filename parsing as fallback
                        logger.warning(f"[Job {job_id}] AI extraction failed, trying filename parsing for {filename}")
                        student_id_from_filename, student_name_from_filename = parse_student_from_filename(filename)
                        
                        if student_id_from_filename and student_name_from_filename:
                            student_id_from_paper = student_id_from_filename
                            student_name = student_name_from_filename
                            logger.info(f"[Job {job_id}] Extracted from filename: {student_name} (ID: {student_id_from_paper})")
                        else:
                            # Both methods failed, use filename as student name
                            logger.warning(f"[Job {job_id}] All extraction methods failed, using filename as student name")
                            student_name = filename.replace(".pdf", "").replace(".PDF", "").strip()
                            student_id_from_paper = f"UNKNOWN_{uuid.uuid4().hex[:8]}"
                            logger.info(f"[Job {job_id}] Using default: {student_name} (ID: {student_id_from_paper})")
                    
                    student_email = f"{student_id_from_paper}@school.edu"  # Generate placeholder email
                    
                    # Get or create student
                    result = await get_or_create_student(
                        student_id=student_id_from_paper,
                        student_name=student_name,
                        batch_id=exam["batch_id"],
                        teacher_id=exam["teacher_id"]
                    )
                    student_id, error = result
                    
                    if error:
                        logger.error(f"[Job {job_id}] Error creating student: {error}")
                        return {
                            "error": f"Failed to create student: {error}",
                            "filename": filename
                        }
                    
                    logger.info(f"[Job {job_id}] Grading with AI...")
                    
                    # Grade the paper
                    scores = await grade_with_ai(
                        images=paper_images,
                        model_answer_images=model_answer_imgs,
                        questions=questions_to_grade,
                        grading_mode=exam.get("grading_mode", "balanced"),
                        total_marks=exam.get("total_marks", 100),
                        model_answer_text=model_answer_text
                    )
                    
                    # Calculate total
                    obtained_marks = sum(s.obtained_marks for s in scores if s.obtained_marks >= 0)
                    percentage = (obtained_marks / exam.get("total_marks", 100)) * 100 if exam.get("total_marks") else 0
                    
                    # Create submission
                    submission_id = f"sub_{uuid.uuid4().hex[:12]}"
                    submission = {
                        "submission_id": submission_id,
                        "exam_id": exam_id,
                        "student_id": student_id,
                        "student_name": student_name,
                        "roll_number": student_id_from_paper,
                        "filename": filename,
                        "file_images": paper_images,  # Store the answer sheet images
                        "obtained_marks": obtained_marks,
                        "total_marks": exam.get("total_marks", 100),
                        "percentage": round(percentage, 2),
                        "scores": [s.dict() for s in scores],
                        "question_scores": [s.dict() for s in scores],  # Add for frontend compatibility
                        "status": "ai_graded",
                        "submitted_at": datetime.now(timezone.utc).isoformat(),
                        "graded_at": datetime.now(timezone.utc).isoformat()
                    }
                    
                    await db.submissions.insert_one(submission)
                    logger.info(f"[Job {job_id}] ✓ Paper graded: {obtained_marks}/{exam.get('total_marks')} ({percentage:.1f}%)")
                    
                    return {"submission": submission, "filename": filename}
                
                # Process with 5-minute timeout
                try:
                    result = await asyncio.wait_for(process_single_paper(), timeout=300.0)
                    
                    if "error" in result:
                        errors.append({"filename": result["filename"], "error": result["error"]})
                    else:
                        # Store the full submission but without MongoDB _id
                        submission_data = result["submission"]
                        # Remove _id if it exists
                        if "_id" in submission_data:
                            del submission_data["_id"]
                        submissions.append(submission_data)
                    
                except asyncio.TimeoutError:
                    logger.error(f"[Job {job_id}] ⏱️ TIMEOUT: Paper {filename} exceeded 5 minutes")
                    errors.append({
                        "filename": filename,
                        "error": "Processing timeout - exceeded 5 minutes (paper too complex or API slow)"
                    })
                
                # Explicit memory cleanup
                import gc
                pdf_bytes = None
                gc.collect()
                
                # Update progress after each paper
                await _update_job_progress(db, job_id, idx + 1, len(submissions), len(errors), submissions, errors)
                logger.info(f"[Job {job_id}] Progress: {idx + 1}/{len(files_data)} papers, {len(submissions)} successful, {len(errors)} errors")
                
            except Exception as e:
                logger.error(f"[Job {job_id}] ERROR processing {filename}: {str(e)}", exc_info=True)
                errors.append({
                    "filename": filename,
                    "error": f"Processing error: {str(e)[:200]}"
                })
                await _update_job_progress(db, job_id, idx + 1, len(submissions), len(errors), submissions, errors)
                continue
        
        # Mark as completed
        await db.exams.update_one(
            {"exam_id": exam_id},
            {"$set": {"status": "completed"}}
        )
        
        await db.grading_jobs.update_one(
            {"job_id": job_id},
            {"$set": {
                "status": "completed",
                "processed_papers": len(files_data),
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
            message=f"Successfully graded {len(submissions)} of {len(files_data)} papers for {exam['exam_name']}",
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
    """Helper to update job progress - submissions already have _id removed"""
    await db.grading_jobs.update_one(
        {"job_id": job_id},
        {"$set": {
            "processed_papers": processed,
            "successful": successful,
            "failed": failed,
            "submissions": submissions,  # Already cleaned of _id
            "errors": errors,
            "updated_at": datetime.now(timezone.utc).isoformat()
        }}
    )
