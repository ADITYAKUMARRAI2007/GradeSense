"""
Question extraction service - extracts questions from question paper.
"""

import asyncio
import json
from typing import Any, Dict, List

import google.generativeai as genai

from ..config.settings import settings
from ..cache import get_cache
from .document_extraction import DocumentExtractionService


class QuestionExtractionService:
    """Extracts structured question data from question paper using Gemini."""
    
    EXTRACTION_PROMPT = """You are analyzing a question paper. Extract all questions and return valid JSON.

RETURN EXACTLY THIS JSON STRUCTURE:
{
  "questions": [
    {
      "question_number": 1,
      "question_text": "The question text here",
      "max_marks": 5,
      "rubric": "Brief grading rubric",
      "sub_questions": [
        {"sub_num": "a", "text": "Sub-question text", "marks": 2},
        {"sub_num": "b", "text": "Sub-question text", "marks": 3}
      ]
    }
  ]
}

RULES:
- Extract ALL questions found
- For each question, determine max_marks from the paper
- If sub-questions exist (a, b, c), list them
- Return ONLY valid JSON, no other text"""
    
    def __init__(self):
        self.doc_extraction = DocumentExtractionService()
        self.semaphore = asyncio.Semaphore(settings.MAX_WORKERS)
        genai.configure(api_key=settings.LLM_API_KEY)
    
    async def extract_questions(
        self, 
        exam_id: str,
        pdf_bytes: bytes,
        pdf_hash: str,
        force_refresh: bool = False
    ) -> Dict[str, Any]:
        """
        Extract questions from question paper PDF.
        
        Args:
            exam_id: Unique exam identifier
            pdf_bytes: Raw PDF file bytes
            pdf_hash: SHA256 hash of PDF
            force_refresh: Ignore cache and re-extract
        
        Returns:
            {"questions": [{"question_number": 1, "question_text": "...", ...}]}
        
        Raises:
            ValueError: If extraction fails
        """
        # Check cache first (unless forcing refresh)
        if not force_refresh:
            cache = get_cache()
            cached = await cache.get_cached_questions(exam_id, pdf_hash)
            if cached:
                print(f"📦 Using cached questions for exam {exam_id}")
                return cached
        
        print(f"🔍 Extracting questions from question paper...")
        
        async with self.semaphore:
            # Convert PDF to images
            images = await self.doc_extraction.pdf_to_base64_images(pdf_bytes)
            
            # Send to Gemini for extraction
            result = await self._call_gemini_extraction(images)
            
            # Cache the result
            cache = get_cache()
            await cache.cache_questions(exam_id, pdf_hash, result)
            
            print(f"✅ Extracted {len(result['questions'])} questions")
            return result
    
    async def _call_gemini_extraction(self, base64_images: List[str]) -> Dict[str, Any]:
        """Call Gemini API to extract questions from images."""
        try:
            model = genai.GenerativeModel("gemini-2.5-flash")
            
            # Build multimodal content
            content = [self.EXTRACTION_PROMPT]
            
            # Add images
            for i, b64_image in enumerate(base64_images):
                content.append({
                    "mime_type": "image/jpeg",
                    "data": b64_image
                })
            
            # Call API with timeout
            response = await asyncio.wait_for(
                asyncio.to_thread(
                    lambda: model.generate_content(
                        content,
                        generation_config=genai.types.GenerationConfig(
                            temperature=0.0,
                            max_output_tokens=4000
                        )
                    )
                ),
                timeout=settings.LLM_TIMEOUT
            )
            
            # Parse response
            response_text = response.text.strip()
            
            # Try to extract JSON from response
            if "```json" in response_text:
                json_str = response_text.split("```json")[1].split("```")[0].strip()
            elif "```" in response_text:
                json_str = response_text.split("```")[1].split("```")[0].strip()
            else:
                json_str = response_text
            
            result = json.loads(json_str)
            
            # Validate structure
            if "questions" not in result or not isinstance(result["questions"], list):
                raise ValueError("Invalid response structure - missing 'questions' array")
            
            return result
            
        except json.JSONDecodeError as e:
            raise ValueError(f"Failed to parse Gemini response as JSON: {str(e)}")
        except asyncio.TimeoutError:
            raise ValueError(f"Question extraction timed out after {settings.LLM_TIMEOUT}s")
        except Exception as e:
            raise ValueError(f"Gemini extraction failed: {str(e)}")
