"""Utility functions for the GradeSense backend."""

import hashlib
import mimetypes
from typing import Tuple


def compute_file_hash(file_bytes: bytes) -> str:
    """Compute SHA256 hash of file."""
    return hashlib.sha256(file_bytes).hexdigest()


def validate_file_type(filename: str, allowed_extensions: list) -> Tuple[bool, str]:
    """Validate file type by extension."""
    file_ext = filename.split('.')[-1].lower()
    
    if file_ext not in allowed_extensions:
        return False, f"File type '{file_ext}' not allowed. Allowed: {allowed_extensions}"
    
    return True, "OK"


def validate_file_size(file_bytes: bytes, max_size_mb: int) -> Tuple[bool, str]:
    """Validate file size in MB."""
    file_size_mb = len(file_bytes) / (1024 * 1024)
    
    if file_size_mb > max_size_mb:
        return False, f"File size {file_size_mb:.1f} MB exceeds limit of {max_size_mb} MB"
    
    return True, "OK"


def extract_student_name_from_filename(filename: str) -> str:
    """
    Extract student name from filename.
    
    Assumes format like: "John_Doe_Paper.pdf" or "student_01.pdf"
    """
    base_name = filename.rsplit('.', 1)[0]  # Remove extension
    name = base_name.replace('_', ' ').replace('-', ' ')
    return name.strip()


def format_percentage(obtained: float, total: float) -> float:
    """Format percentage with 2 decimals."""
    if total == 0:
        return 0.0
    return round((obtained / total) * 100, 2)
