"""
MongoDB-based background task worker for GradeSense
Processes grading jobs asynchronously without blocking the main API
"""

import asyncio
import os
import sys
import logging
from datetime import datetime, timezone
from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorGridFSBucket
from dotenv import load_dotenv
from bson.objectid import ObjectId

# Add parent directory to path to import from server.py
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# Load environment variables
load_dotenv()

# Import functions from server.py
from concurrency import conversion_semaphore, llm_semaphore
from server import (
    pdf_to_images,
    extract_student_info_from_paper,
    parse_student_from_filename,
    get_or_create_student,
    get_exam_model_answer_images,
    get_exam_model_answer_text,
    grade_with_ai,
    generate_annotated_images,
    generate_annotated_images_with_vision_ocr,
    create_notification
)

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger('task_worker')

# MongoDB connection
MONGO_URL = os.environ.get('MONGO_URL')
DB_NAME = os.environ.get('DB_NAME', 'test_database')

client = AsyncIOMotorClient(MONGO_URL)
db = client[DB_NAME]

# Async GridFS bucket
fs_bucket = AsyncIOMotorGridFSBucket(db)

# Helper for async full-file read
async def read_gridfs_file_async(gridfs_id_or_filename, use_filename=False):
    """Async GridFS read with chunking for large files"""
    try:
        if use_filename:
            # For filename (legacy single-paper): get latest version ID first
            # Access fs.files collection explicitly
            file_doc = await db['fs.files'].find_one(
                {"filename": gridfs_id_or_filename},
                sort=[("uploadDate", -1)]
            )
            if not file_doc:
                raise FileNotFoundError(f"File {gridfs_id_or_filename} not found")
            gridfs_id = file_doc["_id"]
        else:
            gridfs_id = ObjectId(gridfs_id_or_filename)

        stream = await fs_bucket.open_download_stream(gridfs_id)
        chunks = []
        total_size = 0
        last_logged_size = 0
        chunk_size = 8192  # 8KB chunks

        while True:
            chunk = await stream.read(chunk_size)
            if not chunk:
                break
            chunks.append(chunk)
            total_size += len(chunk)

            # Optional: Yield progress for very large files (>10MB), log every 10MB
            if total_size - last_logged_size > 10 * 1024 * 1024:
                logger.info(f"Read {total_size/1024/1024:.1f}MB so far")
                last_logged_size = total_size

        await stream.close()
        return b"".join(chunks)
    except Exception as e:
        logger.error(f"Async GridFS read failed: {e}")
        raise

# Import background grading function
from background_grading import process_grading_job_in_background


async def process_task(task):
    """Process a single grading task"""
    task_id = task['task_id']
    # Support both 'data' (old format) and 'payload' (new format)
    task_data = task.get('data') or task.get('payload')
    
    if not task_data:
        raise ValueError(f"Task {task_id} has no data or payload field")
    
    try:
        logger.info(f"Starting task {task_id}: {task['type']}")
        
        # Update status to processing
        await db.tasks.update_one(
            {"task_id": task_id},
            {"$set": {
                "status": "processing",
                "started_at": datetime.now(timezone.utc).isoformat()
            }}
        )
        
        # Process based on task type
        if task['type'] == 'grade_papers':
            await process_grading_task(task_data)
        elif task['type'] == 'grade_paper':
            await process_single_paper_grading(task_data)
        else:
            logger.error(f"Unknown task type: {task['type']}")
            raise ValueError(f"Unknown task type: {task['type']}")
        
        # Mark as completed
        await db.tasks.update_one(
            {"task_id": task_id},
            {"$set": {
                "status": "completed",
                "completed_at": datetime.now(timezone.utc).isoformat()
            }}
        )
        
        logger.info(f"Task {task_id} completed successfully")
        
    except Exception as e:
        logger.error(f"Task {task_id} failed: {str(e)}", exc_info=True)
        
        # Mark as failed
        await db.tasks.update_one(
            {"task_id": task_id},
            {"$set": {
                "status": "failed",
                "error": str(e),
                "failed_at": datetime.now(timezone.utc).isoformat()
            }}
        )


async def process_single_paper_grading(task_data):
    """Process grading for a single student paper (student-upload mode)"""
    exam_id = task_data['exam_id']
    student_id = task_data['student_id']
    student_name = task_data['student_name']
    answer_file_ref = task_data['answer_file_ref']
    model_answer_ref = task_data.get('model_answer_ref')
    questions = task_data['questions']
    grading_mode = task_data.get('grading_mode', 'balanced')
    
    logger.info(f"Grading single paper for student {student_name} in exam {exam_id}")
    
    try:
        # Import required functions from server
        from server import (
            pdf_to_images, grade_with_ai, 
            get_exam_model_answer_images, get_exam_model_answer_text
        )
        
        # Get exam first
        exam = await db.exams.find_one({"exam_id": exam_id}, {"_id": 0})
        if not exam:
            raise ValueError(f"Exam {exam_id} not found")
        
        total_marks = exam['total_marks']
        
        # Get answer paper from GridFS
        ans_bytes = await read_gridfs_file_async(answer_file_ref, use_filename=True)
        async with conversion_semaphore:
            ans_images = await asyncio.to_thread(pdf_to_images, ans_bytes)
        
        # Get model answer from GridFS if available
        ma_images = []
        ma_text = ""
        if model_answer_ref:
            ma_bytes = await read_gridfs_file_async(model_answer_ref, use_filename=True)
            async with conversion_semaphore:
                ma_images = await asyncio.to_thread(pdf_to_images, ma_bytes)
            # Try to extract text from model answer
            try:
                ma_text = await get_exam_model_answer_text(exam_id)
            except:
                ma_text = ""
        
        # Use the grade_with_ai function with correct parameters
        logger.info(f"Grading paper for student {student_name} (ID: {student_id}) in exam {exam_id}")
        async with llm_semaphore:
            question_scores = await grade_with_ai(
                images=ans_images,
                model_answer_images=ma_images,
                questions=questions,
                grading_mode=grading_mode,
                total_marks=total_marks,
                model_answer_text=ma_text
            )
        
        # Calculate total obtained marks
        total_obtained = sum(q.obtained_marks for q in question_scores)
        percentage = (total_obtained / total_marks) * 100 if total_marks > 0 else 0
        
        # Create paper document
        paper_id = f"paper_{uuid.uuid4().hex[:12]}"
        paper_doc = {
            "paper_id": paper_id,
            "exam_id": exam_id,
            "student_id": student_id,
            "student_name": student_name,
            "question_scores": [q.dict() for q in question_scores],
            "total_marks": total_marks,
            "obtained_marks": total_obtained,
            "percentage": round(percentage, 2),
            "status": "graded",
            "graded_at": datetime.now(timezone.utc).isoformat()
        }
        
        await db.papers.insert_one(paper_doc)
        
        # Update submission status
        await db.student_submissions.update_one(
            {"exam_id": exam_id, "student_id": student_id},
            {"$set": {"status": "graded", "graded_at": datetime.now(timezone.utc).isoformat()}}
        )
        
        # Update exam progress
        total_students = exam.get('total_students', 0)
        graded_count = await db.papers.count_documents({"exam_id": exam_id})
        
        update_data = {
            "graded_count": graded_count,
            "progress": (graded_count / total_students) * 100 if total_students > 0 else 0
        }
        
        # If all graded, mark exam as completed
        if graded_count >= total_students:
            update_data["status"] = "completed"
        
        await db.exams.update_one(
            {"exam_id": exam_id},
            {"$set": update_data}
        )
        
        logger.info(f"✓ Successfully graded paper for {student_name}: {total_obtained}/{total_marks} ({percentage:.1f}%)")
        
    except Exception as e:
        logger.error(f"✗ Error grading paper for {student_name}: {e}", exc_info=True)
        raise


async def process_grading_task(task_data):
    """Process a grading job - reads files from GridFS"""
    job_id = task_data['job_id']
    exam_id = task_data['exam_id']
    file_refs = task_data.get('file_refs', task_data.get('files_data', []))  # Support both old and new format
    teacher_id = task_data['teacher_id']
    
    logger.info(f"Processing grading job {job_id} with {len(file_refs)} papers")
    
    # Check if we're using old format (files_data) or new format (file_refs with GridFS IDs)
    files_data = []
    if file_refs and isinstance(file_refs[0], dict):
        if 'gridfs_id' in file_refs[0]:
            # New format: Read files from GridFS
            logger.info(f"Reading {len(file_refs)} files from GridFS...")
            for ref in file_refs:
                try:
                    file_content = await read_gridfs_file_async(ref['gridfs_id'])
                    files_data.append({
                        "filename": ref['filename'],
                        "content": file_content
                    })
                    logger.info(f"  Read {ref['filename']} from GridFS: {len(file_content)} bytes")
                except Exception as e:
                    logger.error(f"Error reading file {ref['filename']} from GridFS: {e}")
                    raise
        else:
            # Old format: files already have content
            files_data = file_refs
    else:
        files_data = file_refs
    
    logger.info(f"Successfully loaded {len(files_data)} files for processing")
    
    # Get exam data
    exam = await db.exams.find_one({"exam_id": exam_id}, {"_id": 0})
    if not exam:
        raise ValueError(f"Exam {exam_id} not found")
    
    # Call the existing background grading function
    await process_grading_job_in_background(
        job_id=job_id,
        exam_id=exam_id,
        files_data=files_data,
        exam=exam,
        teacher_id=teacher_id,
        db=db,
        pdf_to_images=pdf_to_images,
        extract_student_info_from_paper=extract_student_info_from_paper,
        parse_student_from_filename=parse_student_from_filename,
        get_or_create_student=get_or_create_student,
        get_exam_model_answer_images=get_exam_model_answer_images,
        get_exam_model_answer_text=get_exam_model_answer_text,
        grade_with_ai=grade_with_ai,
        create_notification=create_notification,
        generate_annotated_images_with_vision_ocr=generate_annotated_images_with_vision_ocr
    )


async def cleanup_stuck_jobs():
    """Cleanup jobs that have been processing for too long"""
    try:
        from datetime import timedelta
        
        # Cancel jobs stuck for more than 1 hour
        one_hour_ago = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
        
        result = await db.grading_jobs.update_many(
            {
                "status": "processing",
                "updated_at": {"$lt": one_hour_ago}
            },
            {
                "$set": {
                    "status": "failed",
                    "error": "Job timeout - exceeded 1 hour processing time",
                    "updated_at": datetime.now(timezone.utc).isoformat()
                }
            }
        )
        
        if result.modified_count > 0:
            logger.warning(f"⚠️  Cleaned up {result.modified_count} stuck jobs (>1 hour)")
        
        # Cancel stuck tasks (both claimed and processing)
        task_result = await db.tasks.update_many(
            {
                "status": {"$in": ["claimed", "processing"]},
                "created_at": {"$lt": one_hour_ago}
            },
            {"$set": {"status": "failed", "error": "Task timeout - exceeded 1 hour"}}
        )
        
        if task_result.modified_count > 0:
            logger.warning(f"⚠️  Cleaned up {task_result.modified_count} stuck tasks (>1 hour)")
            
    except Exception as e:
        logger.error(f"Error in cleanup: {e}")


async def worker_loop():
    """Main worker loop - polls for pending tasks"""
    logger.info("Task worker started. Polling for tasks...")
    
    # Track when we last ran cleanup
    last_cleanup = datetime.now(timezone.utc)
    
    while True:
        try:
            # Run cleanup every 10 minutes
            if (datetime.now(timezone.utc) - last_cleanup).total_seconds() > 600:
                await cleanup_stuck_jobs()
                last_cleanup = datetime.now(timezone.utc)
            
            # Find next pending task
            task = await db.tasks.find_one_and_update(
                {"status": "pending"},
                {"$set": {"status": "claimed", "claimed_at": datetime.now(timezone.utc).isoformat()}},
                sort=[("created_at", 1)]  # FIFO
            )
            
            if task:
                logger.info(f"Picked up task: {task['task_id']}")
                await process_task(task)
            else:
                # No tasks available, wait before polling again
                await asyncio.sleep(3)  # Poll every 3 seconds
                
        except Exception as e:
            logger.error(f"Worker loop error: {str(e)}", exc_info=True)
            await asyncio.sleep(5)  # Wait longer on error


async def main():
    """Entry point"""
    logger.info("="*50)
    logger.info("GradeSense Background Task Worker")
    logger.info(f"MongoDB: {DB_NAME}")
    logger.info("="*50)
    
    try:
        await worker_loop()
    except KeyboardInterrupt:
        logger.info("Worker stopped by user")
    except Exception as e:
        logger.error(f"Worker crashed: {str(e)}", exc_info=True)
    finally:
        client.close()


if __name__ == "__main__":
    asyncio.run(main())
