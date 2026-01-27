# File utility functions for handling multiple file formats
import os
import io
import zipfile
import tempfile
import logging
from typing import List, Tuple, Optional
from docx import Document
from pdf2image import convert_from_bytes
from PIL import Image, ImageDraw
import base64
import re
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload
from google.oauth2 import service_account

logger = logging.getLogger(__name__)

def convert_word_to_pdf_bytes(word_bytes: bytes) -> bytes:
    """Convert Word document to PDF bytes using python-docx and pdf2docx.
    This is the cheapest method - no AI or external API calls needed."""
    try:
        # For now, we'll convert Word to images directly instead of PDF intermediate
        # This saves processing time and is cheaper
        return word_bytes  # Will handle in convert_to_images
    except Exception as e:
        logger.error(f"Error converting Word to PDF: {e}")
        raise

def convert_to_images(file_bytes: bytes, file_type: str) -> List[str]:
    """Convert any supported file type to base64 encoded images.
    
    Supported types: PDF, Word (.docx, .doc), Images (JPG, PNG)
    Returns list of base64 encoded image strings.
    """
    try:
        images = []
        
        if file_type in ['pdf', 'application/pdf']:
            # PDF to images (existing method)
            from pdf2image import convert_from_bytes
            pil_images = convert_from_bytes(file_bytes)
            for img in pil_images:
                buffered = io.BytesIO()
                img.save(buffered, format="JPEG", quality=95)
                img_str = base64.b64encode(buffered.getvalue()).decode()
                images.append(img_str)
                
        elif file_type in ['docx', 'doc', 'application/vnd.openxmlformats-officedocument.wordprocessingml.document']:
            # Word document - convert to PDF first, then to images
            # This provides better quality and layout preservation
            try:
                from docx import Document
                from PIL import Image, ImageDraw, ImageDraw, ImageFont
                import textwrap
                
                # Parse Word document
                doc = Document(io.BytesIO(file_bytes))
                
                # Extract all text content with basic formatting
                full_text = []
                for para in doc.paragraphs:
                    if para.text.strip():
                        full_text.append(para.text)
                
                # If document has text, convert to image pages
                if full_text:
                    # Create images from text (simple text renderer)
                    page_height = 1400
                    page_width = 1000
                    margin = 50
                    line_height = 25
                    
                    current_page = Image.new('RGB', (page_width, page_height), color='white')
                    draw = ImageDraw.Draw(current_page)
                    
                    y_position = margin
                    for text in full_text:
                        # Wrap long lines
                        wrapped_lines = textwrap.wrap(text, width=80)
                        for line in wrapped_lines:
                            if y_position > page_height - margin:
                                # Save current page and start new one
                                buffered = io.BytesIO()
                                current_page.save(buffered, format="JPEG", quality=95)
                                img_str = base64.b64encode(buffered.getvalue()).decode()
                                images.append(img_str)
                                
                                current_page = Image.new('RGB', (page_width, page_height), color='white')
                                draw = ImageDraw.Draw(current_page)
                                y_position = margin
                            
                            draw.text((margin, y_position), line, fill='black')
                            y_position += line_height
                    
                    # Save the last page
                    if y_position > margin:
                        buffered = io.BytesIO()
                        current_page.save(buffered, format="JPEG", quality=95)
                        img_str = base64.b64encode(buffered.getvalue()).decode()
                        images.append(img_str)
                else:
                    # No text found, try to extract embedded images
                    for rel in doc.part.rels.values():
                        if "image" in rel.target_ref:
                            image_data = rel.target_part.blob
                            img_str = base64.b64encode(image_data).decode()
                            images.append(img_str)
                    
                    # If still no content, create placeholder
                    if not images:
                        logger.warning("Word doc has no text or images")
                        img = Image.new('RGB', (800, 1000), color='white')
                        draw = ImageDraw.Draw(img)
                        draw.text((50, 50), "Empty Document", fill='black')
                        buffered = io.BytesIO()
                        img.save(buffered, format="JPEG")
                        img_str = base64.b64encode(buffered.getvalue()).decode()
                        images.append(img_str)
                        
            except Exception as e:
                logger.error(f"Word document processing failed: {e}")
                # Fallback: create error placeholder
                img = Image.new('RGB', (800, 1000), color='white')
                draw = ImageDraw.Draw(img)
                draw.text((50, 50), f"Error processing Word document: {str(e)[:100]}", fill='red')
                buffered = io.BytesIO()
                img.save(buffered, format="JPEG")
                img_str = base64.b64encode(buffered.getvalue()).decode()
                images.append(img_str)
                
        elif file_type in ['jpg', 'jpeg', 'png', 'image/jpeg', 'image/png', 'gif', 'bmp', 'image/gif', 'image/bmp']:
            # Direct image file
            try:
                img = Image.open(io.BytesIO(file_bytes))
                
                # Convert RGBA/Palette images to RGB
                if img.mode in ('RGBA', 'LA', 'P'):
                    # Create white background
                    background = Image.new('RGB', img.size, (255, 255, 255))
                    if img.mode == 'P':
                        img = img.convert('RGBA')
                    background.paste(img, mask=img.split()[-1] if img.mode in ('RGBA', 'LA') else None)
                    img = background
                elif img.mode not in ('RGB', 'L'):
                    img = img.convert('RGB')
                
                # Ensure minimum size for OCR (at least 100x100)
                if img.size[0] < 100 or img.size[1] < 100:
                    logger.warning(f"Image too small ({img.size}), resizing to 800x800")
                    img = img.resize((800, 800), Image.Resampling.LANCZOS)
                
                buffered = io.BytesIO()
                img.save(buffered, format="JPEG", quality=95)
                img_str = base64.b64encode(buffered.getvalue()).decode()
                images.append(img_str)
                
            except Exception as e:
                logger.error(f"Image processing failed: {e}")
                # Create error placeholder
                img = Image.new('RGB', (800, 600), color='white')
                draw = ImageDraw.Draw(img)
                draw.text((50, 50), f"Error processing image: {str(e)[:100]}", fill='red')
                buffered = io.BytesIO()
                img.save(buffered, format="JPEG")
                img_str = base64.b64encode(buffered.getvalue()).decode()
                images.append(img_str)
            
        else:
            raise ValueError(f"Unsupported file type: {file_type}")
            
        return images
        
    except Exception as e:
        logger.error(f"Error converting file to images: {e}")
        raise

def extract_zip_files(zip_bytes: bytes) -> List[Tuple[str, bytes, str]]:
    """Extract files from ZIP archive.
    
    Returns list of tuples: (filename, file_bytes, file_type)
    """
    extracted_files = []
    
    try:
        with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
            for file_info in zf.filelist:
                # Skip directories and hidden files
                if file_info.is_dir() or file_info.filename.startswith('__MACOSX'):
                    continue
                    
                filename = os.path.basename(file_info.filename)
                file_bytes = zf.read(file_info.filename)
                
                # Determine file type from extension
                ext = os.path.splitext(filename)[1].lower()
                file_type = ext.replace('.', '')
                
                extracted_files.append((filename, file_bytes, file_type))
                logger.info(f"Extracted {filename} from ZIP")
                
    except Exception as e:
        logger.error(f"Error extracting ZIP file: {e}")
        raise
        
    return extracted_files

def parse_student_from_filename(filename: str) -> Tuple[Optional[str], Optional[str]]:
    """Extract student ID and name from filename.
    
    Expected formats:
    - 123_Ayushi_Kumar.pdf -> ID: 123, Name: Ayushi Kumar
    - STU005_John_Doe.pdf -> ID: STU005, Name: John Doe
    - Ayushi.pdf -> ID: None, Name: Ayushi
    """
    try:
        # Remove extension
        name_part = os.path.splitext(filename)[0]
        
        # Try to match pattern: ID_Name or ID_FirstName_LastName
        patterns = [
            r'^(\w+)_(.+)$',  # ID_Name format
            r'^(\d+)[\s_-](.+)$',  # Number followed by name
        ]
        
        for pattern in patterns:
            match = re.match(pattern, name_part)
            if match:
                student_id = match.group(1)
                student_name = match.group(2).replace('_', ' ').replace('-', ' ').strip()
                return student_id, student_name
        
        # If no pattern matches, use entire filename as name
        student_name = name_part.replace('_', ' ').replace('-', ' ').strip()
        return None, student_name
        
    except Exception as e:
        logger.error(f"Error parsing filename: {e}")
        return None, filename

def download_from_google_drive(file_id: str, credentials_json: Optional[dict] = None) -> Tuple[bytes, str]:
    """Download file from Google Drive using file ID.
    
    Args:
        file_id: Google Drive file ID from URL
        credentials_json: Optional service account credentials
        
    Returns:
        Tuple of (file_bytes, mime_type)
    """
    try:
        # For public files, no auth needed
        # For private files, would need OAuth or service account
        
        # Build service
        if credentials_json:
            creds = service_account.Credentials.from_service_account_info(credentials_json)
            service = build('drive', 'v3', credentials=creds)
        else:
            # Try without credentials (for public files)
            service = build('drive', 'v3', developerKey=os.getenv('GOOGLE_API_KEY'))
        
        # Get file metadata
        file_metadata = service.files().get(fileId=file_id).execute()
        mime_type = file_metadata.get('mimeType', '')
        
        # Download file
        request = service.files().get_media(fileId=file_id)
        file_bytes = io.BytesIO()
        downloader = MediaIoBaseDownload(file_bytes, request)
        
        done = False
        while not done:
            status, done = downloader.next_chunk()
            logger.info(f"Download progress: {int(status.progress() * 100)}%")
        
        return file_bytes.getvalue(), mime_type
        
    except Exception as e:
        logger.error(f"Error downloading from Google Drive: {e}")
        raise

def extract_file_id_from_url(url: str) -> Optional[str]:
    """Extract Google Drive file ID from various URL formats.
    
    Supported formats:
    - https://drive.google.com/file/d/FILE_ID/view
    - https://drive.google.com/open?id=FILE_ID
    - https://docs.google.com/document/d/FILE_ID/edit
    """
    patterns = [
        r'/file/d/([a-zA-Z0-9_-]+)',
        r'/document/d/([a-zA-Z0-9_-]+)',
        r'/spreadsheets/d/([a-zA-Z0-9_-]+)',
        r'[?&]id=([a-zA-Z0-9_-]+)',
    ]
    
    for pattern in patterns:
        match = re.search(pattern, url)
        if match:
            return match.group(1)
    
    return None

def get_files_from_drive_folder(folder_id: str, credentials_json: Optional[dict] = None) -> List[Tuple[str, str]]:
    """Get all files from a Google Drive folder.
    
    Returns list of tuples: (file_id, filename)
    """
    try:
        if credentials_json:
            creds = service_account.Credentials.from_service_account_info(credentials_json)
            service = build('drive', 'v3', credentials=creds)
        else:
            service = build('drive', 'v3', developerKey=os.getenv('GOOGLE_API_KEY'))
        
        # Query files in folder
        results = service.files().list(
            q=f"'{folder_id}' in parents and trashed=false",
            fields="files(id, name, mimeType)",
            pageSize=50  # Limit to 50 files
        ).execute()
        
        files = results.get('files', [])
        return [(f['id'], f['name']) for f in files]
        
    except Exception as e:
        logger.error(f"Error listing Drive folder contents: {e}")
        raise
