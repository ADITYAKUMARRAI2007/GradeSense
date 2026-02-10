"""
GradeSense Backend - Main FastAPI Application

Clean, modular architecture for AI-powered exam grading.
Version: 2.0
"""

import os
import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorDatabase

from app.config.settings import settings
from app.cache import init_cache, get_cache
from app.routes.exam_routes import create_exam_routes
from app.routes.grading_routes import create_grading_routes

# Setup logging
logging.basicConfig(level=settings.LOG_LEVEL)
logger = logging.getLogger(__name__)

# Global database reference
db: AsyncIOMotorDatabase = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    """FastAPI lifespan context manager for startup/shutdown."""
    global db
    
    # STARTUP
    logger.info("🚀 GradeSense Backend Starting Up...")
    
    try:
        # Validate settings
        settings.validate()
        logger.info("✅ Settings validated")
        
        # Connect to MongoDB
        client = AsyncIOMotorClient(
            settings.MONGODB_URL,
            maxPoolSize=50,
            serverSelectionTimeoutMS=5000
        )
        
        # Test connection
        await client.server_info()
        db = client[settings.DATABASE_NAME]
        logger.info(f"✅ Connected to MongoDB: {settings.DATABASE_NAME}")
        
        # Initialize cache
        init_cache(db)
        logger.info("✅ Cache initialized")
        
        # Create indexes
        await _create_indexes(db)
        logger.info("✅ Database indexes created")
        
        # Cleanup expired cache
        cache = get_cache()
        await cache.cleanup_expired_cache()
        
        logger.info("✅ Application startup complete")
        
    except Exception as e:
        logger.error(f"❌ Startup failed: {e}")
        raise
    
    yield
    
    # SHUTDOWN
    logger.info("🛑 Shutting down...")
    if db:
        db.client.close()
        logger.info("✅ Database connection closed")


async def _create_indexes(db: AsyncIOMotorDatabase):
    """Create database indexes for performance."""
    try:
        # Exams
        await db.exams.create_index("exam_id", unique=True)
        await db.exams.create_index("teacher_id")
        await db.exams.create_index("batch_id")
        
        # Model answers
        await db.model_answers.create_index([("exam_id", 1), ("question_number", 1)])
        
        # Submissions
        await db.submissions.create_index("exam_id")
        await db.submissions.create_index("student_id")
        await db.submissions.create_index([("exam_id", 1), ("student_id", 1)], unique=True)
        
        # Grading jobs
        await db.grading_jobs.create_index("job_id", unique=True)
        await db.grading_jobs.create_index("exam_id")
        await db.grading_jobs.create_index("status")
        
        # Cache collections
        await db.questions_cache.create_index([("exam_id", 1), ("pdf_hash", 1)], unique=True)
        await db.model_answer_cache.create_index([("exam_id", 1), ("question_number", 1), ("pdf_hash", 1)], unique=True)
        await db.grading_result_cache.create_index("cache_key", unique=True)
        
        # TTL indexes (auto-delete expired documents)
        await db.questions_cache.create_index("expires_at", expireAfterSeconds=0)
        await db.model_answer_cache.create_index("expires_at", expireAfterSeconds=0)
        await db.grading_result_cache.create_index("expires_at", expireAfterSeconds=0)
        
    except Exception as e:
        logger.warning(f"Index creation warning: {e}")
        # Don't fail startup if indexes already exist


# Create FastAPI application
app = FastAPI(
    title="GradeSense API",
    description="AI-powered exam grading system",
    version="2.0.0",
    lifespan=lifespan
)

# Add CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # TODO: Configure for production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# Include routes (routes need access to db, so register after lifespan)
@app.on_event("startup")
async def register_routes():
    """Register all routes after database connection established."""
    global db
    
    if db is None:
        # Routes will be auto-registered by FastAPI's dependency system
        pass


# Health check endpoint
@app.get("/api/health")
async def health_check():
    """Health check endpoint."""
    return {
        "status": "healthy",
        "version": "2.0.0",
        "database": "connected" if db else "disconnected"
    }


# Root endpoint
@app.get("/")
async def root():
    """Root endpoint."""
    return {
        "app": "GradeSense",
        "version": "2.0.0",
        "docs": "/docs",
        "health": "/api/health"
    }


# Dependency to inject database into routes
async def get_db() -> AsyncIOMotorDatabase:
    """Get database instance for route handlers."""
    global db
    if db is None:
        raise RuntimeError("Database not initialized")
    return db


# Manually include routes with database dependency
# This is done here instead of at route definition time
def setup_routes():
    """Setup all API routes."""
    global db
    
    if db is None:
        logger.warning("Database not available for route setup")
        return
    
    # Include exam routes
    exam_router = create_exam_routes(db)
    app.include_router(exam_router)
    
    # Include grading routes
    grading_router = create_grading_routes(db)
    app.include_router(grading_router)
    
    logger.info("✅ Routes registered")


# Setup routes after app creation
@app.on_event("startup")
async def _setup_routes():
    setup_routes()


if __name__ == "__main__":
    import uvicorn
    
    uvicorn.run(
        "main:app",
        host=settings.HOST,
        port=settings.PORT,
        reload=settings.DEBUG,
        log_level=settings.LOG_LEVEL.lower()
    )
