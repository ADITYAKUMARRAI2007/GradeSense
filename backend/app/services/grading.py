"""
Grading service - grades student answers using Gemini AI with system prompt.
"""

import asyncio
import hashlib
import json
from typing import Any, Dict, List, Optional

import google.generativeai as genai

from ..config.settings import settings
from ..cache import get_cache
from .document_extraction import DocumentExtractionService


class GradingService:
    """Grades student answers against model answers using Gemini AI."""
    
    # Grading modes with detailed rubrics
    MODE_INSTRUCTIONS = {
        "strict": {
            "name": "Strict Mode",
            "description": "Every step required. High precision.",
            "threshold": 0.70,
            "rules": [
                "Award marks ONLY for each correct step shown",
                "Deduct marks for any incorrect step",
                "Sub-steps must be shown for full marks",
                "Alternative methods: 0 marks if different from model",
                "Minimum threshold: 70% correctness"
            ]
        },
        "balanced": {
            "name": "Balanced Mode",
            "description": "Fair evaluation of understanding and method.",
            "threshold": 0.50,
            "rules": [
                "Award 60-70% marks for correct method/approach",
                "Award 40-50% for correct concept but calculation error",
                "Award 0% for completely wrong approach",
                "Accept reasonable alternative methods",
                "Minimum threshold: 50% correctness"
            ]
        },
        "conceptual": {
            "name": "Conceptual Mode",
            "description": "Understanding over perfect execution.",
            "threshold": 0.50,
            "rules": [
                "Focus on understanding of concept",
                "Minor arithmetic errors: -10% only",
                "Alternative correct methods: full marks",
                "Correct approach with missing steps: 70%",
                "Minimum threshold: 50% understanding"
            ]
        },
        "lenient": {
            "name": "Lenient Mode",
            "description": "Effort-based grading with floor marks.",
            "threshold": 0.25,
            "rules": [
                "Award marks for attempt and effort",
                "Partial credit for any correct element",
                "Floor marks: 25% of max for any attempt",
                "Creative approaches encouraged",
                "Minimum threshold: 25% effort"
            ]
        }
    }
    
    MASTER_SYSTEM_PROMPT = """═══════════════════════════════════════════════════════════════════════════════
                    GRADESENSE AI GRADING ENGINE - MASTER SYSTEM PROMPT
═══════════════════════════════════════════════════════════════════════════════

## FUNDAMENTAL PRINCIPLES (SACRED - NEVER VIOLATE)

1. **CONSISTENCY**: Apply the same rules to all students equally
2. **MODEL ANSWER**: Always prioritize model answer when determining correctness  
3. **FAIRNESS**: Award marks proportional to effort and understanding shown

═══════════════════════════════════════════════════════════════════════════════
## GRADING MODE: {mode_name}
{mode_description}

**Key Rules for this mode:**
{mode_rules}

═══════════════════════════════════════════════════════════════════════════════
## ANSWER TYPE HANDLING

### Mathematics/Calculations
- Check each step in the solution
- Award partial marks for correct method even if final answer wrong
- Carry-forward principle: If a step depends on previous work, check if logic is correct
- Formulas: Must be stated before use for full marks

### Diagrams/Sketches
- Verify diagram accurately represents the concept
- Label correctness is important
- Scale/proportions: Check if reasonable
- If diagram is only partial answer, weight it appropriately

### Short Answers (1-3 lines)
- Must answer the EXACT question asked
- No marks for related but off-topic answers
- -1.0 only if question not found on these pages
- 0.0 if question found but answer is wrong/blank

### Long Answers (1+ pages)
- Check for logical flow and completeness
- Partial marks for incomplete answers
- Award credit for correct sub-points even if overall structure poor

### Multiple Choice (MCQ)
- Only mark as correct if exact choice is correct
- No partial marks for MCQ
- If student shows work, check it but final mark = correct/incorrect only

═══════════════════════════════════════════════════════════════════════════════
## EDGE CASES (CRITICAL RULES)

**Question Not Found:**
- value: -1.0 (negative one point zero, NOT 0)
- Use ONLY if you checked ALL pages and question truly doesn't exist
- Must say: "Question not found on provided pages"
- ALWAYS check EVERY PAGE before marking as -1.0

**Question Found But Wrong/Blank:**
- value: 0.0 (zero point zero)
- Student made attempt but it's incorrect
- Or blank/no attempt
- This is DIFFERENT from "not found" (-1.0)
- ALWAYS award 0.0 if question found on page but answer is empty/wrong

**Illegible/Unclear Answer:**
- Award 0.0 (found but unreadable)
- Note in feedback: "Answer illegible - cannot grade fairly"
- Suggest human review for final grade

**Multi-page Answer:**
- Check if answer spans multiple pages
- Sum all content before determining correctness
- Award marks based on complete answer across pages

═══════════════════════════════════════════════════════════════════════════════
## EXACT OUTPUT FORMAT (MUST BE VALID JSON)

Return EXACTLY this JSON for each question:

{{
  "question_number": 1,
  "obtained_marks": 8,
  "total_marks": 10,
  "status": "correct",
  "confidence": 0.95,
  "feedback": "Clear explanation with all steps shown. Minor notation issue.",
  "sub_scores": [
    {{
      "sub_question": "a",
      "obtained_marks": 4,
      "total_marks": 4,
      "feedback": "Perfect solution"
    }},
    {{
      "sub_question": "b", 
      "obtained_marks": 4,
      "total_marks": 6,
      "feedback": "Correct method but calculation error in final step"
    }}
  ]
}}

**JSON Field Rules:**
- obtained_marks: 0 to total_marks (float allowed)
- status: "correct" | "partial" | "incorrect" | "not_found" | "blank"
- confidence: 0.0 to 1.0 (your confidence in the grade)
- sub_scores: [] if no sub-questions, OR array of sub-scores
- CRITICAL: If sub_scores exist, SUM(sub_scores.obtained_marks) MUST = obtained_marks

═══════════════════════════════════════════════════════════════════════════════
## QUALITY ASSURANCE CHECKLIST

Before finalizing each grade, verify:

- [ ] Question number correctly identified
- [ ] Answer found on provided pages (or correctly marked -1.0)
- [ ] Marks awarded match rubric for this mode
- [ ] Feedback is specific and actionable
- [ ] Confidence reflects certainty of grade
- [ ] If sub-questions: sum check passed
- [ ] No marks awarded above max_marks
- [ ] Negative marks only for "not found" (-1.0)
- [ ] Tone is professional and constructive
- [ ] Alternative correct methods accepted (if mode allows)

═══════════════════════════════════════════════════════════════════════════════

Grade with integrity, insight, and care. 
Every student deserves fair evaluation.

═══════════════════════════════════════════════════════════════════════════════"""
    
    def __init__(self):
        self.doc_extraction = DocumentExtractionService()
        self.semaphore = asyncio.Semaphore(settings.MAX_WORKERS)
        genai.configure(api_key=settings.LLM_API_KEY)
    
    async def grade_question(
        self,
        exam_id: str,
        question_number: int,
        question_text: str,
        max_marks: int,
        student_answer_images: List[str],  # Base64 images of student's answer
        model_answer_text: Optional[str] = None,  # OCR'd text from model answer
        grading_mode: str = "balanced",
        student_answer_hash: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Grade a student's answer to a single question.
        
        Args:
            exam_id: Unique exam ID
            question_number: Question number
            question_text: Full question text
            max_marks: Maximum marks for this question
            student_answer_images: List of base64 JPEG images of student's answer pages
            model_answer_text: Optional extracted text from model answer sheet
            grading_mode: "strict" | "balanced" | "conceptual" | "lenient"
            student_answer_hash: SHA256 hash of student answers (for caching)
        
        Returns:
            {
                "question_number": 1,
                "obtained_marks": 8.5,
                "total_marks": 10,
                "status": "correct",
                "confidence": 0.92,
                "feedback": "...",
                "sub_scores": [...]
            }
        """
        # Validate grading mode
        if grading_mode not in self.MODE_INSTRUCTIONS:
            grading_mode = "balanced"
        
        # Check cache if we have hash
        if student_answer_hash:
            cache = get_cache()
            cached = await cache.get_cached_grading_result(
                exam_id, 
                student_answer_hash, 
                question_number
            )
            if cached:
                print(f"📦 Using cached grade for Q{question_number}")
                return cached
        
        print(f"⏳ Grading Q{question_number}...")
        
        async with self.semaphore:
            result = await self._call_gemini_grading(
                question_number=question_number,
                question_text=question_text,
                max_marks=max_marks,
                student_answer_images=student_answer_images,
                model_answer_text=model_answer_text,
                grading_mode=grading_mode
            )
            
            # Cache if we have hash
            if student_answer_hash:
                cache = get_cache()
                await cache.cache_grading_result(
                    exam_id,
                    student_answer_hash,
                    question_number,
                    result
                )
            
            print(f"✅ Q{question_number}: {result['obtained_marks']}/{result['total_marks']}")
            return result
    
    async def _call_gemini_grading(
        self,
        question_number: int,
        question_text: str,
        max_marks: int,
        student_answer_images: List[str],
        model_answer_text: Optional[str],
        grading_mode: str
    ) -> Dict[str, Any]:
        """Call Gemini API to grade the question."""
        try:
            model = genai.GenerativeModel("gemini-2.5-flash")
            
            # Build system prompt with mode instructions
            mode_info = self.MODE_INSTRUCTIONS[grading_mode]
            mode_rules = "\n".join(f"- {rule}" for rule in mode_info["rules"])
            
            system_prompt = self.MASTER_SYSTEM_PROMPT.format(
                mode_name=mode_info["name"],
                mode_description=mode_info["description"],
                mode_rules=mode_rules
            )
            
            # Build grading request
            grading_request = f"""GRADE THIS ANSWER:

**Question {question_number}:**
{question_text}

**Max Marks:** {max_marks}

**Student's Answer:** (see images below)
"""
            
            if model_answer_text:
                grading_request += f"\n**Model Answer (from sheet):**\n{model_answer_text}\n"
            
            grading_request += """
**Your task:** Grade the student's answer and return JSON only."""
            
            # Build multimodal content
            content = [
                {
                    "text": system_prompt + "\n\n" + grading_request
                }
            ]
            
            # Add student answer images
            for b64_img in student_answer_images[:settings.CHUNK_SIZE]:  # Limit images per call
                content.append({
                    "mime_type": "image/jpeg",
                    "data": b64_img
                })
            
            # Call API
            response = await asyncio.wait_for(
                asyncio.to_thread(
                    lambda: model.generate_content(
                        content,
                        generation_config=genai.types.GenerationConfig(
                            temperature=settings.LLM_TEMPERATURE,
                            max_output_tokens=1000
                        )
                    )
                ),
                timeout=settings.LLM_TIMEOUT
            )
            
            # Parse response
            response_text = response.text.strip()
            
            # Extract JSON
            if "```json" in response_text:
                json_str = response_text.split("```json")[1].split("```")[0].strip()
            elif "```" in response_text:
                json_str = response_text.split("```")[1].split("```")[0].strip()
            else:
                json_str = response_text
            
            result = json.loads(json_str)
            
            # Validate and normalize response
            result = self._validate_grading_result(result, question_number, max_marks)
            
            return result
            
        except json.JSONDecodeError as e:
            print(f"⚠️  Failed to parse grading response: {e}")
            return self._default_grading_result(question_number, max_marks, "error")
        except asyncio.TimeoutError:
            print(f"⚠️  Grading timed out for Q{question_number}")
            return self._default_grading_result(question_number, max_marks, "timeout")
        except Exception as e:
            print(f"⚠️  Grading error for Q{question_number}: {e}")
            return self._default_grading_result(question_number, max_marks, "error")
    
    def _validate_grading_result(
        self, 
        result: Dict[str, Any], 
        question_number: int,
        max_marks: int
    ) -> Dict[str, Any]:
        """Validate and normalize grading result."""
        # Ensure required fields
        if "question_number" not in result:
            result["question_number"] = question_number
        
        if "obtained_marks" not in result:
            result["obtained_marks"] = 0
        
        if "total_marks" not in result:
            result["total_marks"] = max_marks
        
        # Validate marks ranges
        if result["obtained_marks"] < 0:
            if result["obtained_marks"] != -1.0:  # -1.0 is valid for "not found"
                result["obtained_marks"] = 0
        
        if result["obtained_marks"] > max_marks:
            result["obtained_marks"] = max_marks
        
        # Ensure confidence is 0-1
        if "confidence" not in result:
            result["confidence"] = 0.7
        else:
            result["confidence"] = max(0, min(1, result["confidence"]))
        
        # Ensure feedback exists
        if "feedback" not in result:
            result["feedback"] = "Graded by AI system"
        
        # Ensure status
        if "status" not in result:
            if result["obtained_marks"] == -1.0:
                result["status"] = "not_found"
            elif result["obtained_marks"] == 0:
                result["status"] = "blank"
            elif result["obtained_marks"] == max_marks:
                result["status"] = "correct"
            else:
                result["status"] = "partial"
        
        # Ensure sub_scores exists
        if "sub_scores" not in result:
            result["sub_scores"] = []
        
        # Validate sub_scores sum if present
        if result["sub_scores"]:
            sub_sum = sum(s.get("obtained_marks", 0) for s in result["sub_scores"])
            if abs(sub_sum - result["obtained_marks"]) > 0.01:
                print(f"⚠️  Sub-scores sum ({sub_sum}) != obtained_marks ({result['obtained_marks']})")
                # Normalize obtained_marks to match sub_scores sum
                result["obtained_marks"] = min(sub_sum, max_marks)
        
        return result
    
    def _default_grading_result(
        self, 
        question_number: int, 
        max_marks: int, 
        error_type: str
    ) -> Dict[str, Any]:
        """Return default grading when error occurs."""
        return {
            "question_number": question_number,
            "obtained_marks": 0,
            "total_marks": max_marks,
            "status": "error",
            "confidence": 0.0,
            "feedback": f"Grading failed ({error_type}). Please review manually.",
            "sub_scores": []
        }
