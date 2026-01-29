"""
Google Cloud Vision OCR Service for GradeSense
Provides precise bounding box detection for handwritten text
"""
import os
import io
import base64
import logging
from typing import Dict, List, Optional, Tuple
from google.cloud import vision
from google.oauth2 import service_account

logger = logging.getLogger(__name__)

# Path to service account credentials
CREDENTIALS_PATH = os.path.join(os.path.dirname(__file__), 'credentials', 'gcp-vision-key.json')

class TextRegion:
    """Represents a detected text region with bounding box"""
    def __init__(self, text: str, confidence: float, vertices: List[Dict], block_type: str = "word"):
        self.text = text
        self.confidence = confidence
        self.vertices = vertices  # [{"x": int, "y": int}, ...]
        self.block_type = block_type  # word, line, paragraph, block
    
    def get_center(self) -> Tuple[int, int]:
        """Get center point of bounding box"""
        if len(self.vertices) < 4:
            return (0, 0)
        avg_x = sum(v.get("x", 0) for v in self.vertices) // len(self.vertices)
        avg_y = sum(v.get("y", 0) for v in self.vertices) // len(self.vertices)
        return (avg_x, avg_y)
    
    def get_top_left(self) -> Tuple[int, int]:
        """Get top-left corner of bounding box"""
        if len(self.vertices) < 1:
            return (0, 0)
        return (self.vertices[0].get("x", 0), self.vertices[0].get("y", 0))
    
    def to_dict(self) -> Dict:
        return {
            "text": self.text,
            "confidence": self.confidence,
            "vertices": self.vertices,
            "block_type": self.block_type,
            "center": self.get_center(),
            "top_left": self.get_top_left()
        }


class VisionOCRService:
    """Service for detecting handwritten text with precise bounding boxes"""
    
    def __init__(self):
        """Initialize the Vision API client with service account credentials"""
        self.client = None
        self._initialize_client()
    
    def _initialize_client(self):
        """Initialize the Vision API client"""
        try:
            if os.path.exists(CREDENTIALS_PATH):
                credentials = service_account.Credentials.from_service_account_file(
                    CREDENTIALS_PATH
                )
                self.client = vision.ImageAnnotatorClient(credentials=credentials)
                logger.info("Google Cloud Vision client initialized successfully")
            else:
                logger.warning(f"Vision credentials not found at {CREDENTIALS_PATH}")
                self.client = None
        except Exception as e:
            logger.error(f"Failed to initialize Vision client: {e}")
            self.client = None
    
    def is_available(self) -> bool:
        """Check if Vision API is available"""
        return self.client is not None
    
    def detect_text_from_base64(
        self, 
        image_base64: str,
        language_hints: Optional[List[str]] = None
    ) -> Dict:
        """
        Detect text in an image from base64 string.
        
        Args:
            image_base64: Base64 encoded image
            language_hints: Optional language hints for better accuracy
            
        Returns:
            Dictionary with full text and bounding boxes
        """
        if not self.client:
            raise RuntimeError("Vision API client not initialized")
        
        try:
            # Decode base64 to bytes
            image_bytes = base64.b64decode(image_base64)
            return self.detect_text_from_bytes(image_bytes, language_hints)
        except Exception as e:
            logger.error(f"Error decoding base64 image: {e}")
            raise
    
    def detect_text_from_bytes(
        self, 
        image_bytes: bytes,
        language_hints: Optional[List[str]] = None
    ) -> Dict:
        """
        Detect text in an image from bytes using DOCUMENT_TEXT_DETECTION.
        This feature is optimized for handwritten text and dense documents.
        
        Args:
            image_bytes: Image as bytes
            language_hints: Optional language hints
            
        Returns:
            Dictionary with:
            - full_text: Complete extracted text
            - regions: List of TextRegion objects with bounding boxes
            - confidence: Average confidence score
            - page_count: Number of pages detected
        """
        if not self.client:
            raise RuntimeError("Vision API client not initialized")
        
        try:
            image = vision.Image(content=image_bytes)
            
            # Use DOCUMENT_TEXT_DETECTION for handwriting
            features = [
                vision.Feature(type_=vision.Feature.Type.DOCUMENT_TEXT_DETECTION)
            ]
            
            # Build request with optional language hints
            image_context = vision.ImageContext(
                language_hints=language_hints or ["en"]
            )
            
            request = vision.AnnotateImageRequest(
                image=image,
                features=features,
                image_context=image_context
            )
            
            response = self.client.annotate_image(request)
            
            if response.error.message:
                raise ValueError(f"Vision API error: {response.error.message}")
            
            return self._parse_document_response(response)
            
        except Exception as e:
            logger.error(f"Error processing image with Vision API: {e}")
            raise
    
    def _parse_document_response(self, response) -> Dict:
        """
        Parse DOCUMENT_TEXT_DETECTION response to extract text and bounding boxes.
        
        Returns structured data with word-level, line-level, and paragraph-level regions.
        """
        if not response.full_text_annotation:
            return {
                "full_text": "",
                "regions": [],
                "words": [],
                "lines": [],
                "paragraphs": [],
                "confidence": 0.0,
                "page_count": 0
            }
        
        annotation = response.full_text_annotation
        full_text = annotation.text
        
        words = []
        lines = []
        paragraphs = []
        word_confidences = []
        
        # Iterate through the hierarchical structure
        for page in annotation.pages:
            for block in page.blocks:
                block_text_parts = []
                
                for paragraph in block.paragraphs:
                    paragraph_text_parts = []
                    line_text_parts = []
                    line_start_y = None
                    
                    for word in paragraph.words:
                        # Extract word text
                        word_text = "".join([symbol.text for symbol in word.symbols])
                        confidence = word.confidence
                        
                        # Convert vertices to dict format
                        vertices = self._vertices_to_dict(word.bounding_box.vertices)
                        
                        word_region = TextRegion(
                            text=word_text,
                            confidence=float(confidence),
                            vertices=vertices,
                            block_type="word"
                        )
                        words.append(word_region)
                        
                        if confidence > 0:
                            word_confidences.append(confidence)
                        
                        paragraph_text_parts.append(word_text)
                        
                        # Track lines by Y position
                        word_y = vertices[0].get("y", 0) if vertices else 0
                        if line_start_y is None:
                            line_start_y = word_y
                            line_text_parts = [word_text]
                        elif abs(word_y - line_start_y) < 20:  # Same line threshold
                            line_text_parts.append(word_text)
                        else:
                            # New line detected
                            line_start_y = word_y
                            line_text_parts = [word_text]
                    
                    # Create paragraph region
                    paragraph_text = " ".join(paragraph_text_parts)
                    if paragraph.bounding_box:
                        para_vertices = self._vertices_to_dict(paragraph.bounding_box.vertices)
                        para_region = TextRegion(
                            text=paragraph_text,
                            confidence=float(paragraph.confidence) if paragraph.confidence else 0.0,
                            vertices=para_vertices,
                            block_type="paragraph"
                        )
                        paragraphs.append(para_region)
                    
                    block_text_parts.append(paragraph_text)
        
        # Calculate average confidence
        avg_confidence = (
            sum(word_confidences) / len(word_confidences) 
            if word_confidences else 0.0
        )
        
        return {
            "full_text": full_text,
            "regions": [w.to_dict() for w in words],  # All word regions
            "words": [w.to_dict() for w in words],
            "paragraphs": [p.to_dict() for p in paragraphs],
            "confidence": float(avg_confidence),
            "page_count": len(annotation.pages),
            "total_words": len(words),
            "total_paragraphs": len(paragraphs)
        }
    
    def _vertices_to_dict(self, vertices) -> List[Dict]:
        """Convert protobuf vertices to list of dicts"""
        return [
            {"x": int(v.x), "y": int(v.y)}
            for v in vertices
        ]
    
    def find_text_location(
        self, 
        ocr_result: Dict, 
        search_text: str,
        fuzzy_match: bool = True
    ) -> Optional[Dict]:
        """
        Find the location of specific text in OCR results.
        
        Args:
            ocr_result: Result from detect_text_* methods
            search_text: Text to search for
            fuzzy_match: If True, do partial/fuzzy matching
            
        Returns:
            Bounding box info for the text, or None if not found
        """
        search_lower = search_text.lower().strip()
        
        for word in ocr_result.get("words", []):
            word_text = word.get("text", "").lower().strip()
            
            if fuzzy_match:
                if search_lower in word_text or word_text in search_lower:
                    return word
            else:
                if word_text == search_lower:
                    return word
        
        # Also check paragraphs for longer text
        for para in ocr_result.get("paragraphs", []):
            para_text = para.get("text", "").lower()
            if search_lower in para_text:
                return para
        
        return None
    
    def get_regions_in_area(
        self,
        ocr_result: Dict,
        x_min: int,
        y_min: int,
        x_max: int,
        y_max: int
    ) -> List[Dict]:
        """
        Get all text regions within a specified bounding area.
        
        Useful for finding text in specific regions of an answer sheet
        (e.g., question 1 area, question 2 area).
        """
        regions_in_area = []
        
        for word in ocr_result.get("words", []):
            vertices = word.get("vertices", [])
            if not vertices:
                continue
            
            # Check if any vertex is within the area
            for v in vertices:
                if x_min <= v.get("x", 0) <= x_max and y_min <= v.get("y", 0) <= y_max:
                    regions_in_area.append(word)
                    break
        
        return regions_in_area


# Global service instance
_vision_service: Optional[VisionOCRService] = None

def get_vision_service() -> VisionOCRService:
    """Get or create the global Vision OCR service instance"""
    global _vision_service
    if _vision_service is None:
        _vision_service = VisionOCRService()
    return _vision_service
