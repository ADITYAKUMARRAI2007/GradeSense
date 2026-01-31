# Background grading job processing for handling 2500+ pages simultaneously
# This module provides asynchronous background processing for paper grading
# with robust error handling, retry logic, and rate limit management

import asyncio
import logging
from datetime import datetime, timezone
from typing import List, Dict
import base64
import uuid
import time

logger = logging.getLogger(__name__)

# Rate limiting configuration - OPTIMIZED for dedicated Gemini API key
RATE_LIMIT_DELAY = 0.05  # 50ms delay (even faster for dedicated key)
MAX_RETRIES = 3  # Standard retry attempts
RETRY_BACKOFF = 2  # Standard exponential backoff


async def retry_with_exponential_backoff(func, *args, max_retries=MAX_RETRIES, **kwargs):
    """
    Retry function with exponential backoff for handling rate limits and transient failures
    Enhanced for large-scale grading (2500+ pages)
    """
    last_exception = None
    
    for attempt in range(max_retries):
        try:
            result = await func(*args, **kwargs)
            
            # Add rate limiting delay between successful calls (critical for shared API key)
            if attempt > 0:
                logger.info(f"✅ Retry attempt {attempt + 1} succeeded after {attempt} failures")
            
            await asyncio.sleep(RATE_LIMIT_DELAY)
            return result
            
        except Exception as e:
            last_exception = e
            error_msg = str(e).lower()
            
            # Check if it's a rate limit or quota error
            is_rate_limit = any(term in error_msg for term in ['429', 'rate', 'quota', 'limit', 'resource_exhausted', 'rate_limit_exceeded'])
            
            if attempt < max_retries - 1:  # Not the last attempt
                wait_time = (RETRY_BACKOFF ** attempt) * 3  # 3s, 9s, 27s, 81s, 243s
                
                if is_rate_limit:
                    logger.warning(f"⚠️ Rate limit hit! Attempt {attempt + 1}/{max_retries}. Waiting {wait_time}s before retry...")
                else:
                    logger.warning(f"⚠️ API error: {str(e)[:100]}. Attempt {attempt + 1}/{max_retries}. Retrying in {wait_time}s...")
                
                await asyncio.sleep(wait_time)
            else:
                logger.error(f"❌ All {max_retries} attempts failed. Last error: {str(e)}")
                raise last_exception
    
    raise last_exception


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
    create_notification,  # Function reference
    generate_annotated_images_with_vision_ocr=None  # Optional: Vision OCR annotation function
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
            
            logger.info(f"[Job {job_id}] ========================================")
            logger.info(f"[Job {job_id}] 📄 PAPER {idx + 1}/{len(files_data)}: {filename}")
            logger.info(f"[Job {job_id}] File size: {len(pdf_bytes) / (1024*1024):.2f}MB")
            logger.info(f"[Job {job_id}] Progress: {(idx/len(files_data)*100):.1f}% complete")
            logger.info(f"[Job {job_id}] ========================================")
            
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
                    
                    logger.info(f"[Job {job_id}] Grading with AI (with retry logic + learned patterns)...")
                    
                    # Grade the paper with retry logic for rate limit handling + teacher's learned patterns
                    scores = await retry_with_exponential_backoff(
                        grade_with_ai,
                        images=paper_images,
                        model_answer_images=model_answer_imgs,
                        questions=questions_to_grade,
                        grading_mode=exam.get("grading_mode", "balanced"),
                        total_marks=exam.get("total_marks", 100),
                        model_answer_text=model_answer_text,
                        teacher_id=teacher_id,  # NEW: For learning patterns
                        subject_id=exam.get("subject_id"),  # NEW: Cross-exam learning
                        exam_id=exam_id  # NEW: Pattern matching
                    )
                    
                    # CRITICAL DEBUG: Check for score duplication
                    logger.info(f"[Job {job_id}] grade_with_ai returned {len(scores)} scores")
                    logger.info(f"[Job {job_id}] Question numbers in scores: {[s.question_number for s in scores]}")
                    
                    # CRITICAL FIX: Deduplicate scores before calculating total
                    # Keep only the FIRST occurrence of each question number
                    seen_questions = set()
                    deduplicated_scores = []
                    for s in scores:
                        if s.question_number not in seen_questions:
                            seen_questions.add(s.question_number)
                            deduplicated_scores.append(s)
                        else:
                            logger.warning(f"[Job {job_id}] Duplicate Q{s.question_number} found and removed")
                    
                    # Log duplication if found
                    if len(scores) != len(deduplicated_scores):
                        logger.error(f"[Job {job_id}] DUPLICATE QUESTION NUMBERS DETECTED!")
                        logger.error(f"[Job {job_id}] Original: {len(scores)} scores, After dedup: {len(deduplicated_scores)} scores")
                        logger.error(f"[Job {job_id}] Duplicates removed: {len(scores) - len(deduplicated_scores)}")
                    
                    # Use deduplicated scores for everything going forward
                    scores = deduplicated_scores
                    
                    # Calculate total from deduplicated scores
                    obtained_marks = sum(s.obtained_marks for s in scores if s.obtained_marks >= 0)
                    percentage = (obtained_marks / exam.get("total_marks", 100)) * 100 if exam.get("total_marks") else 0
                    
                    logger.info(f"[Job {job_id}] Total: {obtained_marks}/{exam.get('total_marks', 100)} = {percentage:.1f}%")
                    
                    # Generate annotated images using Vision OCR if available
                    annotated_images = []
                    if generate_annotated_images_with_vision_ocr:
                        try:
                            logger.info(f"[Job {job_id}] Generating annotated images with Vision OCR...")
                            annotated_images = await generate_annotated_images_with_vision_ocr(
                                original_images=paper_images,
                                question_scores=scores,
                                use_vision_ocr=True
                            )
                            logger.info(f"[Job {job_id}] Generated {len(annotated_images)} annotated images")
                        except Exception as ann_error:
                            logger.error(f"[Job {job_id}] Annotation generation failed: {ann_error}")
                            annotated_images = []
                    
                    # CRITICAL FIX: Store images separately to avoid 16MB document limit
                    # For 50 papers × 50 pages, we need separate storage for images
                    
                    submission_id = f"sub_{uuid.uuid4().hex[:12]}"
                    
                    # Store images in separate collection (not embedded in submission)
                    if paper_images:
                        await db.submission_images.update_one(
                            {"submission_id": submission_id},
                            {"$set": {
                                "submission_id": submission_id,
                                "file_images": paper_images,
                                "annotated_images": annotated_images if annotated_images else [],
                                "created_at": datetime.now(timezone.utc).isoformat()
                            }},
                            upsert=True
                        )
                    
                    # Create submission document with metadata only (no large base64 data)
                    submission = {
                        "submission_id": submission_id,
                        "exam_id": exam_id,
                        "student_id": student_id,
                        "student_name": student_name,
                        "roll_number": student_id_from_paper,
                        "filename": filename,
                        "file_images_count": len(paper_images),
                        "annotated_images_count": len(annotated_images),
                        "has_images": True,  # Flag to indicate images exist in separate collection
                        "obtained_marks": obtained_marks,
                        "total_marks": exam.get("total_marks", 100),
                        "percentage": round(percentage, 2),
                        "scores": [s.dict() for s in scores],
                        "question_scores": [s.dict() for s in scores],
                        "status": "ai_graded",
                        "submitted_at": datetime.now(timezone.utc).isoformat(),
                        "graded_at": datetime.now(timezone.utc).isoformat()
                    }
                    
                    await db.submissions.insert_one(submission)
                    
                    # Log grading analytics for admin dashboard
                    try:
                        analytics_entry = {
                            "submission_id": submission_id,
                            "exam_id": exam_id,
                            "teacher_id": exam["teacher_id"],
                            "graded_at": datetime.now(timezone.utc).isoformat(),
                            "grading_mode": exam.get("grading_mode", "balanced"),
                            "ai_confidence_score": 0.85,  # Placeholder - could be calculated from AI response
                            "edited_by_teacher": False,  # Initially not edited
                            "grade_delta": 0,  # No change from AI grade initially
                            "grading_duration_seconds": 0,  # Could track actual duration if needed
                            "estimated_cost": 0.0015,  # Rough estimate: ~$0.0015 per paper (Gemini 2.5 Flash)
                            "tokens_input": len(str(paper_images)) // 4,  # Rough token estimate
                            "tokens_output": len(str(scores)) // 4,  # Rough token estimate
                        }
                        await db.grading_analytics.insert_one(analytics_entry)
                        logger.info(f"[Job {job_id}] Logged analytics for {filename}")
                    except Exception as analytics_error:
                        logger.error(f"[Job {job_id}] Failed to log analytics: {analytics_error}")
                        # Don't fail the grading if analytics logging fails
                    
                    logger.info(f"[Job {job_id}] ✓ Paper graded: {obtained_marks}/{exam.get('total_marks')} ({percentage:.1f}%)")
                    
                    return {"submission": submission, "filename": filename}
                
                # Process with 10-minute timeout (increased for annotation generation)
                try:
                    result = await asyncio.wait_for(process_single_paper(), timeout=600.0)
                    
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
                    logger.error(f"[Job {job_id}] ⏱️ TIMEOUT: Paper {filename} exceeded 10 minutes")
                    error_details = {
                        "filename": filename,
                        "error": "Processing timeout - exceeded 10 minutes (paper too complex or API slow)"
                    }
                    errors.append(error_details)
                    
                    # Log to file for production debugging
                    try:
                        import traceback
                        with open("/tmp/grading_errors.log", "a") as f:
                            f.write(f"\n{'='*80}\n")
                            f.write(f"TIMEOUT at paper {idx + 1}/{len(files_data)}: {filename}\n")
                            f.write(f"Job: {job_id}\n")
                            f.write(f"Time: {datetime.now(timezone.utc).isoformat()}\n")
                            f.write(f"{'='*80}\n")
                    except:
                        pass
                
                # Explicit memory cleanup after EACH paper (critical for large batches)
                import gc
                pdf_bytes = None
                paper_images = None
                model_answer_imgs = None
                scores = None
                gc.collect()
                
                # Update progress after each paper
                await _update_job_progress(db, job_id, idx + 1, len(submissions), len(errors), submissions, errors)
                
                # Log progress at milestones (every 10 papers, 50 papers, 100 papers)
                progress_milestones = [10, 25, 50, 100, 200, 500, 1000]
                if (idx + 1) in progress_milestones or (idx + 1) % 100 == 0:
                    logger.info(f"[Job {job_id}] 🎯 MILESTONE: {idx + 1}/{len(files_data)} papers processed ({len(submissions)} successful, {len(errors)} failed)")
                else:
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
        
        # Extract submission references (not full data)
        submission_refs = [
            {
                "submission_id": sub.get("submission_id"),
                "student_name": sub.get("student_name"),
                "obtained_marks": sub.get("obtained_marks"),
                "total_marks": sub.get("total_marks"),
                "percentage": sub.get("percentage")
            }
            for sub in submissions
        ]
        
        await db.grading_jobs.update_one(
            {"job_id": job_id},
            {"$set": {
                "status": "completed",
                "processed_papers": len(files_data),
                "successful": len(submissions),
                "failed": len(errors),
                "submission_refs": submission_refs,  # Only references, not full data
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
    """Helper to update job progress - stores REFERENCES only, not full data
    
    CRITICAL: To handle 50+ papers with many pages, we only store submission IDs and summary data
    Full submission data is in submissions collection
    """
    # Extract only submission_ids and basic info (no large image data)
    submission_refs = [
        {
            "submission_id": sub.get("submission_id"),
            "student_name": sub.get("student_name"),
            "obtained_marks": sub.get("obtained_marks"),
            "total_marks": sub.get("total_marks"),
            "percentage": sub.get("percentage")
        }
        for sub in submissions
    ]
    
    await db.grading_jobs.update_one(
        {"job_id": job_id},
        {"$set": {
            "processed_papers": processed,
            "successful": successful,
            "failed": failed,
            "submission_refs": submission_refs,  # Only references/summary, not full data
            "errors": errors,  # Error messages are typically small
            "updated_at": datetime.now(timezone.utc).isoformat()
        }}
    )
