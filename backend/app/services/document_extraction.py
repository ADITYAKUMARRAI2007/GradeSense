"""
Document extraction service - converts PDFs to images.
"""

import asyncio
import base64
import io
from typing import List, Tuple

import fitz  # PyMuPDF
from PIL import Image

from ..config.settings import settings
from ..cache import get_cache


class DocumentExtractionService:
    """Converts PDF documents to base64-encoded JPEG images."""
    
    def __init__(self):
        self.zoom = settings.PDF_ZOOM
        self.jpeg_quality = settings.JPEG_QUALITY
        self.semaphore = asyncio.Semaphore(3)  # Limit concurrent PDF processing
    
    async def pdf_to_base64_images(self, pdf_bytes: bytes) -> List[str]:
        """
        Convert PDF to list of base64-encoded JPEG images.
        
        Args:
            pdf_bytes: Raw PDF file bytes
        
        Returns:
            List of base64-encoded JPEG strings
        
        Raises:
            ValueError: If PDF is invalid or has no pages
        """
        async with self.semaphore:
            return await asyncio.to_thread(
                self._sync_pdf_to_base64_images,
                pdf_bytes
            )
    
    def _sync_pdf_to_base64_images(self, pdf_bytes: bytes) -> List[str]:
        """Synchronous PDF conversion (run in thread pool)."""
        try:
            pdf_document = fitz.open(stream=pdf_bytes, filetype="pdf")
            
            if pdf_document.page_count == 0:
                raise ValueError("PDF has no pages")
            
            base64_images = []
            
            for page_num in range(pdf_document.page_count):
                page = pdf_document[page_num]
                
                # Render page to image with zoom for better quality
                mat = fitz.Matrix(self.zoom, self.zoom)
                pix = page.get_pixmap(matrix=mat, alpha=False)
                
                # Convert to PIL Image for JPEG compression
                img_data = pix.tobytes("ppm")
                img = Image.open(io.BytesIO(pix.tobytes("ppm")))
                
                # Save as JPEG with quality setting to reduce size
                buffer = io.BytesIO()
                img.save(buffer, format="JPEG", quality=self.jpeg_quality, optimize=True)
                buffer.seek(0)
                
                # Encode to base64
                base64_str = base64.b64encode(buffer.getvalue()).decode('utf-8')
                base64_images.append(base64_str)
            
            pdf_document.close()
            
            print(f"✅ Converted PDF with {len(base64_images)} pages")
            return base64_images
            
        except Exception as e:
            raise ValueError(f"Failed to convert PDF: {str(e)}")
    
    async def validate_pdf(self, pdf_bytes: bytes) -> Tuple[bool, str]:
        """
        Validate PDF file.
        
        Returns:
            Tuple of (is_valid, error_message)
        """
        try:
            pdf_document = fitz.open(stream=pdf_bytes, filetype="pdf")
            page_count = pdf_document.page_count
            pdf_document.close()
            
            if page_count == 0:
                return False, "PDF has no pages"
            
            return True, f"Valid PDF with {page_count} pages"
        except Exception as e:
            return False, f"Invalid PDF: {str(e)}"
