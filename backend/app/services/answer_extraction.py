"""
Answer extraction service - extracts answers from model answer sheet using OCR.
"""

import asyncio
import json
from typing import Any, Dict, List

import google.generativeai as genai

from ..config.settings import settings
from ..cache import get_cache
from .document_extraction import DocumentExtractionService


class AnswerExtractionService:
    """Extracts answer text for each question from model answer sheet using Gemini Vision."""
    
    OCR_PROMPT = """You are reading a model answer sheet. Extract the answer to question {question_number}.

Return this JSON:
{{
  "question_number": {question_number},
  "answer_text": "The answer text for this question",
  "has_diagrams": false,
  "confidence": 0.95
}}

RULES:
- Extract ONLY the answer to question {question_number}
- Include all text, formulas, and descriptions
- If there are diagrams/sketches, set has_diagrams to true
- confidence: 0.0 (no answer found) to 1.0 (very clear)
- Return ONLY valid JSON"""
    
    def __init__(self):
        self.doc_extraction = DocumentExtractionService()
        self.semaphore = asyncio.Semaphore(settings.MAX_WORKERS)
        genai.configure(api_key=settings.LLM_API_KEY)
    
    async def extract_all_answers(
        self,
        exam_id: str,
        pdf_bytes: bytes,
        pdf_hash: str,
        question_numbers: List[int],
        force_refresh: bool = False
    ) -> Dict[int, Dict[str, Any]]:
        """
        Extract answers for all questions from model answer sheet.
        
        Args:
            exam_id: Unique exam identifier
            pdf_bytes: Raw PDF file bytes of model answer
            pdf_hash: SHA256 hash of PDF
            question_numbers: List of question numbers to extract (e.g., [1, 2, 3])
            force_refresh: Ignore cache and re-extract
        
        Returns:
            {
                1: {"question_number": 1, "answer_text": "...", "has_diagrams": false},
                2: {"question_number": 2, "answer_text": "...", "has_diagrams": true}
            }
        """
        print(f"🔍 Extracting answers for {len(question_numbers)} questions...")
        
        # Convert PDF to images once
        images = await self.doc_extraction.pdf_to_base64_images(pdf_bytes)
        
        # Extract each question's answer in parallel
        tasks = [
            self._extract_single_answer(
                exam_id, pdf_hash, q_num, images, force_refresh
            )
            for q_num in question_numbers
        ]
        
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        # Combine results
        answers = {}
        for result in results:
            if isinstance(result, Exception):
                print(f"⚠️  Error extracting answer: {result}")
            elif result:
                q_num = result["question_number"]
                answers[q_num] = result
        
        print(f"✅ Extracted {len(answers)}/{len(question_numbers)} answers")
        return answers
    
    async def _extract_single_answer(
        self,
        exam_id: str,
        pdf_hash: str,
        question_number: int,
        base64_images: List[str],
        force_refresh: bool
    ) -> Dict[str, Any]:
        """Extract answer for a single question."""
        # Check cache first
        if not force_refresh:
            cache = get_cache()
            cached = await cache.get_cached_model_answer(exam_id, question_number, pdf_hash)
            if cached:
                print(f"📦 Using cached answer for Q{question_number}")
                return cached
        
        async with self.semaphore:
            # Call Gemini
            result = await self._call_gemini_ocr(question_number, base64_images)
            
            # Cache the result
            cache = get_cache()
            await cache.cache_model_answer(exam_id, question_number, pdf_hash, result)
            
            return result
    
    async def _call_gemini_ocr(
        self, 
        question_number: int, 
        base64_images: List[str]
    ) -> Dict[str, Any]:
        """Call Gemini API to extract answer for a question."""
        try:
            model = genai.GenerativeModel("gemini-2.5-flash")
            
            prompt = self.OCR_PROMPT.format(question_number=question_number)
            
            # Build multimodal content
            content = [prompt]
            
            # Add images
            for b64_image in base64_images:
                content.append({
                    "mime_type": "image/jpeg",
                    "data": b64_image
                })
            
            # Call API
            response = await asyncio.wait_for(
                asyncio.to_thread(
                    lambda: model.generate_content(
                        content,
                        generation_config=genai.types.GenerationConfig(
                            temperature=0.0,
                            max_output_tokens=2000
                        )
                    )
                ),
                timeout=settings.LLM_TIMEOUT
            )
            
            # Parse JSON response
            response_text = response.text.strip()
            
            if "```json" in response_text:
                json_str = response_text.split("```json")[1].split("```")[0].strip()
            elif "```" in response_text:
                json_str = response_text.split("```")[1].split("```")[0].strip()
            else:
                json_str = response_text
            
            result = json.loads(json_str)
            
            # Validate
            if "answer_text" not in result:
                result["answer_text"] = ""
            if "has_diagrams" not in result:
                result["has_diagrams"] = False
            if "confidence" not in result:
                result["confidence"] = 0.5
            
            return result
            
        except json.JSONDecodeError as e:
            print(f"⚠️  Failed to parse response for Q{question_number}: {e}")
            return {
                "question_number": question_number,
                "answer_text": "",
                "has_diagrams": False,
                "confidence": 0.0
            }
        except asyncio.TimeoutError:
            print(f"⚠️  Extraction timed out for Q{question_number}")
            return {
                "question_number": question_number,
                "answer_text": "",
                "has_diagrams": False,
                "confidence": 0.0
            }
        except Exception as e:
            print(f"⚠️  Error extracting Q{question_number}: {e}")
            return {
                "question_number": question_number,
                "answer_text": "",
                "has_diagrams": False,
                "confidence": 0.0
            }
