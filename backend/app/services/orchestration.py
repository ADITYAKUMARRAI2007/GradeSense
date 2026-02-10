"""
Orchestration service - coordinates the complete grading workflow.

FLOW:
1. Question Paper Uploaded → Extract questions (Gemini) → Cache questions
2. Model Answer Uploaded → Extract answers per question (Gemini OCR) → Cache answers
3. Student Papers Uploaded → For each paper:
   a. Convert to images
   b. For each question:
      - Get cached question
      - Get cached model answer
      - Get student's answer images for that question
      - Grade using Gemini
      - Cache result
   c. Compile all scores
"""

import asyncio
import hashlib
from typing import Any, Dict, List, Optional

from motor.motor_asyncio import AsyncIOMotorDatabase

from .document_extraction import DocumentExtractionService
from .question_extraction import QuestionExtractionService
from .answer_extraction import AnswerExtractionService
from .grading import GradingService
from ..cache import get_cache


class GradeOrchestrationService:
    """Orchestrates the complete question → answer → grading workflow."""
    
    def __init__(self, db: AsyncIOMotorDatabase):
        self.db = db
        self.doc_extractor = DocumentExtractionService()
        self.question_extractor = QuestionExtractionService()
        self.answer_extractor = AnswerExtractionService()
        self.grader = GradingService()
    
    # ============ PHASE 1: QUESTION PAPER UPLOAD ============
    
    async def process_question_paper(
        self,
        exam_id: str,
        pdf_bytes: bytes
    ) -> Dict[str, Any]:
        """
        Process uploaded question paper.
        
        1. Validate PDF
        2. Extract questions using Gemini
        3. Cache extracted questions
        4. Store in exams collection
        
        Returns:
            {
                "success": True,
                "exam_id": "...",
                "question_count": 5,
                "questions": [...]
            }
        """
        try:
            # Validate PDF
            is_valid, msg = await self.doc_extractor.validate_pdf(pdf_bytes)
            if not is_valid:
                return {"success": False, "error": msg}
            
            # Compute hash for caching
            pdf_hash = hashlib.sha256(pdf_bytes).hexdigest()
            
            # Extract questions
            questions_data = await self.question_extractor.extract_questions(
                exam_id=exam_id,
                pdf_bytes=pdf_bytes,
                pdf_hash=pdf_hash
            )
            
            # Store in database
            exam_doc = await self.db.exams.find_one({"exam_id": exam_id})
            if exam_doc:
                await self.db.exams.update_one(
                    {"exam_id": exam_id},
                    {
                        "$set": {
                            "question_paper_hash": pdf_hash,
                            "questions": questions_data.get("questions", []),
                            "total_questions": len(questions_data.get("questions", []))
                        }
                    }
                )
            
            return {
                "success": True,
                "exam_id": exam_id,
                "question_count": len(questions_data.get("questions", [])),
                "questions": questions_data.get("questions", [])
            }
            
        except Exception as e:
            print(f"❌ Error processing question paper: {e}")
            return {"success": False, "error": str(e)}
    
    # ============ PHASE 2: MODEL ANSWER UPLOAD ============
    
    async def process_model_answer(
        self,
        exam_id: str,
        pdf_bytes: bytes,
        question_numbers: List[int]
    ) -> Dict[str, Any]:
        """
        Process uploaded model answer sheet.
        
        1. Validate PDF
        2. Extract answer for each question using Gemini OCR
        3. Cache each answer
        4. Store in model_answers collection
        
        Returns:
            {
                "success": True,
                "exam_id": "...",
                "answers_extracted": 5,
                "answers": {1: {...}, 2: {...}}
            }
        """
        try:
            # Validate PDF
            is_valid, msg = await self.doc_extractor.validate_pdf(pdf_bytes)
            if not is_valid:
                return {"success": False, "error": msg}
            
            # Compute hash
            pdf_hash = hashlib.sha256(pdf_bytes).hexdigest()
            
            # Extract answers for all questions
            answers = await self.answer_extractor.extract_all_answers(
                exam_id=exam_id,
                pdf_bytes=pdf_bytes,
                pdf_hash=pdf_hash,
                question_numbers=question_numbers
            )
            
            # Store in database
            exam_update = {"model_answer_hash": pdf_hash}
            
            for q_num, answer_data in answers.items():
                # Also store individual answers for quick retrieval
                await self.db.model_answers.update_one(
                    {"exam_id": exam_id, "question_number": q_num},
                    {
                        "$set": {
                            "exam_id": exam_id,
                            "question_number": q_num,
                            "pdf_hash": pdf_hash,
                            "answer_text": answer_data.get("answer_text", ""),
                            "has_diagrams": answer_data.get("has_diagrams", False),
                            "confidence": answer_data.get("confidence", 0.5)
                        }
                    },
                    upsert=True
                )
            
            await self.db.exams.update_one(
                {"exam_id": exam_id},
                {"$set": exam_update}
            )
            
            return {
                "success": True,
                "exam_id": exam_id,
                "answers_extracted": len(answers),
                "answers": answers
            }
            
        except Exception as e:
            print(f"❌ Error processing model answer: {e}")
            return {"success": False, "error": str(e)}
    
    # ============ PHASE 3: STUDENT PAPERS GRADING ============
    
    async def grade_student_papers(
        self,
        exam_id: str,
        student_papers: Dict[str, bytes],  # {student_id: pdf_bytes}
        grading_mode: str = "balanced"
    ) -> Dict[str, Any]:
        """
        Grade multiple student papers.
        
        For each student paper:
        1. Convert to images
        2. For each question:
           a. Get cached question details
           b. Get cached model answer
           c. Get student's answer images for that question
           d. Grade using Gemini
           e. Cache result
        3. Compile all scores
        
        Returns:
            {
                "success": True,
                "exam_id": "...",
                "papers_graded": 30,
                "results": {
                    "student_id_1": {
                        "scores": [{question_number: 1, obtained_marks: 8, ...}],
                        "total_marks": 80
                    },
                    ...
                }
            }
        """
        try:
            results = {}
            
            # Get exam questions
            exam = await self.db.exams.find_one({"exam_id": exam_id})
            if not exam:
                return {"success": False, "error": "Exam not found"}
            
            questions = exam.get("questions", [])
            question_numbers = [q.get("question_number") for q in questions]
            
            # Grade each paper
            for student_id, pdf_bytes in student_papers.items():
                student_result = await self._grade_single_paper(
                    exam_id=exam_id,
                    student_id=student_id,
                    pdf_bytes=pdf_bytes,
                    questions=questions,
                    grading_mode=grading_mode
                )
                
                results[student_id] = student_result
            
            return {
                "success": True,
                "exam_id": exam_id,
                "papers_graded": len(results),
                "results": results
            }
            
        except Exception as e:
            print(f"❌ Error grading papers: {e}")
            return {"success": False, "error": str(e)}
    
    async def _grade_single_paper(
        self,
        exam_id: str,
        student_id: str,
        pdf_bytes: bytes,
        questions: List[Dict[str, Any]],
        grading_mode: str
    ) -> Dict[str, Any]:
        """Grade a single student paper."""
        try:
            # Convert to images
            student_images = await self.doc_extractor.pdf_to_base64_images(pdf_bytes)
            
            # Compute hash for caching
            paper_hash = hashlib.sha256(pdf_bytes).hexdigest()
            
            # Get model answer hash from exam
            exam = await self.db.exams.find_one({"exam_id": exam_id})
            model_answer_hash = exam.get("model_answer_hash", "")
            
            # Grade each question
            scores = []
            total_marks = 0
            
            for question in questions:
                q_num = question.get("question_number")
                q_text = question.get("question_text", "")
                max_marks = question.get("max_marks", 0)
                
                total_marks += max_marks
                
                # Get model answer (if available)
                model_answer = await self.db.model_answers.find_one({
                    "exam_id": exam_id,
                    "question_number": q_num
                })
                model_answer_text = model_answer.get("answer_text", "") if model_answer else None
                
                # Grade this question
                score = await self.grader.grade_question(
                    exam_id=exam_id,
                    question_number=q_num,
                    question_text=q_text,
                    max_marks=max_marks,
                    student_answer_images=student_images,  # All images, Gemini will find right one
                    model_answer_text=model_answer_text,
                    grading_mode=grading_mode,
                    student_answer_hash=paper_hash  # For caching
                )
                
                scores.append(score)
            
            # Compute total obtained marks
            obtained_marks = sum(s.get("obtained_marks", 0) for s in scores)
            
            return {
                "student_id": student_id,
                "scores": scores,
                "total_marks": total_marks,
                "obtained_marks": obtained_marks,
                "percentage": (obtained_marks / total_marks * 100) if total_marks > 0 else 0
            }
            
        except Exception as e:
            print(f"❌ Error grading paper for {student_id}: {e}")
            return {
                "student_id": student_id,
                "error": str(e),
                "scores": []
            }
