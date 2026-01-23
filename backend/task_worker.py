"""
MongoDB-based background task worker for GradeSense
Processes grading jobs asynchronously without blocking the main API
"""

import asyncio
import os
import sys
import logging
from datetime import datetime, timezone
from motor.motor_asyncio import AsyncIOMotorClient
from dotenv import load_dotenv
from pymongo import MongoClient
from gridfs import GridFS
from bson.objectid import ObjectId

# Add parent directory to path to import from server.py
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# Load environment variables
load_dotenv()

# Import functions from server.py
from server import (
    pdf_to_images,
    extract_student_info_from_paper,
    parse_student_from_filename,
    get_or_create_student,
    get_exam_model_answer_images,
    get_exam_model_answer_text,
    grade_with_ai,
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

# GridFS for reading uploaded files
sync_client = MongoClient(MONGO_URL)
sync_db = sync_client[DB_NAME]
fs = GridFS(sync_db)

# Import background grading function
from background_grading import process_grading_job_in_background


async def process_task(task):
    """Process a single grading task"""
    task_id = task['task_id']
    task_data = task['data']
    
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


async def process_grading_task(task_data):
    """Process a grading job"""
    job_id = task_data['job_id']
    exam_id = task_data['exam_id']
    files_data = task_data['files_data']
    teacher_id = task_data['teacher_id']
    
    logger.info(f"Processing grading job {job_id} with {len(files_data)} papers")
    
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
        create_notification=create_notification
    )


async def worker_loop():
    """Main worker loop - polls for pending tasks"""
    logger.info("Task worker started. Polling for tasks...")
    
    while True:
        try:
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
