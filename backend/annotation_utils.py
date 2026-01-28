"""
Annotation utility for grading papers with visual feedback
Supports drawing circles, checkmarks, boxes, and text overlays on student answer images
"""
import io
import base64
import logging
from PIL import Image, ImageDraw, ImageFont
from typing import List, Dict, Tuple, Optional

logger = logging.getLogger(__name__)

class AnnotationType:
    """Enum for annotation types"""
    CHECKMARK = "checkmark"  # Green checkmark in box
    SCORE_CIRCLE = "score_circle"  # Green circle with number
    FLAG_CIRCLE = "flag_circle"  # Red circle with 'R'
    STEP_LABEL = "step_label"  # Step label like "2aStep1"
    POINT_NUMBER = "point_number"  # Numbered circles (1, 2, 3)

class Annotation:
    """Represents a single annotation on an image"""
    def __init__(
        self,
        annotation_type: str,
        x: int,
        y: int,
        text: str = "",
        color: str = "green",
        size: int = 30
    ):
        self.annotation_type = annotation_type
        self.x = x
        self.y = y
        self.text = text
        self.color = color
        self.size = size
    
    def to_dict(self) -> Dict:
        """Convert annotation to dictionary for storage"""
        return {
            "type": self.annotation_type,
            "x": self.x,
            "y": self.y,
            "text": self.text,
            "color": self.color,
            "size": self.size
        }
    
    @classmethod
    def from_dict(cls, data: Dict) -> 'Annotation':
        """Create annotation from dictionary"""
        return cls(
            annotation_type=data["type"],
            x=data["x"],
            y=data["y"],
            text=data.get("text", ""),
            color=data.get("color", "green"),
            size=data.get("size", 30)
        )


def get_color_rgb(color_name: str) -> Tuple[int, int, int]:
    """Convert color name to RGB tuple"""
    colors = {
        "green": (34, 197, 94),  # Tailwind green-500
        "red": (239, 68, 68),  # Tailwind red-500
        "black": (0, 0, 0),
        "white": (255, 255, 255),
        "gray": (156, 163, 175),  # Tailwind gray-400
    }
    return colors.get(color_name, (0, 0, 0))


def draw_checkmark_in_box(draw: ImageDraw, x: int, y: int, size: int = 30):
    """Draw a green checkmark inside a green box"""
    # Draw green box
    box_color = get_color_rgb("green")
    box_padding = 3
    box_coords = [
        x - box_padding,
        y - box_padding,
        x + size + box_padding,
        y + size + box_padding
    ]
    draw.rectangle(box_coords, outline=box_color, width=2)
    
    # Draw checkmark
    check_color = box_color
    # Checkmark is drawn as two lines forming a check shape
    line_width = 3
    
    # Short line (bottom left to middle)
    check_x1 = x + size * 0.2
    check_y1 = y + size * 0.5
    check_x2 = x + size * 0.4
    check_y2 = y + size * 0.7
    draw.line([(check_x1, check_y1), (check_x2, check_y2)], fill=check_color, width=line_width)
    
    # Long line (middle to top right)
    check_x3 = x + size * 0.8
    check_y3 = y + size * 0.2
    draw.line([(check_x2, check_y2), (check_x3, check_y3)], fill=check_color, width=line_width)


def draw_circle_with_text(
    draw: ImageDraw,
    x: int,
    y: int,
    text: str,
    color: str = "green",
    size: int = 40,
    font: Optional[ImageFont.FreeTypeFont] = None
):
    """Draw a colored circle with text inside"""
    fill_color = get_color_rgb(color)
    text_color = get_color_rgb("white")
    
    # Draw circle
    circle_coords = [
        x - size,
        y - size,
        x + size,
        y + size
    ]
    draw.ellipse(circle_coords, fill=fill_color, outline=fill_color)
    
    # Draw text in center
    # Get text bounding box for centering
    if font:
        bbox = draw.textbbox((0, 0), text, font=font)
        text_width = bbox[2] - bbox[0]
        text_height = bbox[3] - bbox[1]
    else:
        # Fallback estimation if no font
        text_width = len(text) * 8
        text_height = 12
    
    text_x = x - text_width // 2
    text_y = y - text_height // 2
    
    if font:
        draw.text((text_x, text_y), text, fill=text_color, font=font)
    else:
        draw.text((text_x, text_y), text, fill=text_color)


def draw_step_label(
    draw: ImageDraw,
    x: int,
    y: int,
    text: str,
    font: Optional[ImageFont.FreeTypeFont] = None
):
    """Draw step label with green background (like '2aStep1')"""
    bg_color = get_color_rgb("green")
    text_color = get_color_rgb("white")
    
    # Measure text size
    if font:
        bbox = draw.textbbox((0, 0), text, font=font)
        text_width = bbox[2] - bbox[0]
        text_height = bbox[3] - bbox[1]
    else:
        text_width = len(text) * 8
        text_height = 14
    
    padding = 5
    
    # Draw rounded rectangle background
    bg_coords = [
        x,
        y,
        x + text_width + padding * 2,
        y + text_height + padding * 2
    ]
    draw.rounded_rectangle(bg_coords, radius=5, fill=bg_color)
    
    # Draw text
    text_x = x + padding
    text_y = y + padding
    
    if font:
        draw.text((text_x, text_y), text, fill=text_color, font=font)
    else:
        draw.text((text_x, text_y), text, fill=text_color)


def apply_annotations_to_image(
    image_base64: str,
    annotations: List[Annotation]
) -> str:
    """
    Apply annotations to a base64 encoded image
    
    Args:
        image_base64: Base64 encoded image string
        annotations: List of Annotation objects to apply
        
    Returns:
        Base64 encoded annotated image
    """
    try:
        # Decode base64 image
        img_data = base64.b64decode(image_base64)
        img = Image.open(io.BytesIO(img_data))
        
        # Ensure RGB mode
        if img.mode != 'RGB':
            img = img.convert('RGB')
        
        # Create drawing context
        draw = ImageDraw.Draw(img)
        
        # Try to load a font (fallback to default if not available)
        try:
            font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 16)
            font_large = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 20)
        except Exception:
            logger.warning("Could not load custom font, using default")
            font = None
            font_large = None
        
        # Apply each annotation
        for ann in annotations:
            if ann.annotation_type == AnnotationType.CHECKMARK:
                draw_checkmark_in_box(draw, ann.x, ann.y, ann.size)
            
            elif ann.annotation_type == AnnotationType.SCORE_CIRCLE:
                draw_circle_with_text(draw, ann.x, ann.y, ann.text, "green", ann.size, font_large)
            
            elif ann.annotation_type == AnnotationType.FLAG_CIRCLE:
                draw_circle_with_text(draw, ann.x, ann.y, "R", "red", ann.size, font_large)
            
            elif ann.annotation_type == AnnotationType.STEP_LABEL:
                draw_step_label(draw, ann.x, ann.y, ann.text, font)
            
            elif ann.annotation_type == AnnotationType.POINT_NUMBER:
                draw_circle_with_text(draw, ann.x, ann.y, ann.text, "black", ann.size // 2, font)
        
        # Convert back to base64
        buffered = io.BytesIO()
        img.save(buffered, format="JPEG", quality=95)
        annotated_base64 = base64.b64encode(buffered.getvalue()).decode()
        
        return annotated_base64
    
    except Exception as e:
        logger.error(f"Error applying annotations to image: {e}")
        # Return original image on error
        return image_base64


def create_sample_annotations(image_width: int, image_height: int) -> List[Annotation]:
    """
    Create sample annotations for testing purposes
    Positions are relative to image dimensions
    """
    annotations = []
    
    # Example: Add checkmarks on the left side
    for i in range(3):
        annotations.append(Annotation(
            annotation_type=AnnotationType.CHECKMARK,
            x=50,
            y=100 + i * 100,
            size=25
        ))
    
    # Example: Add step label
    annotations.append(Annotation(
        annotation_type=AnnotationType.STEP_LABEL,
        x=100,
        y=200,
        text="2aStep1"
    ))
    
    # Example: Add score circle
    annotations.append(Annotation(
        annotation_type=AnnotationType.SCORE_CIRCLE,
        x=150,
        y=250,
        text="3",
        size=35
    ))
    
    # Example: Add flag circle
    annotations.append(Annotation(
        annotation_type=AnnotationType.FLAG_CIRCLE,
        x=150,
        y=350,
        size=35
    ))
    
    return annotations


def auto_position_annotations_for_question(
    question_number: int,
    sub_questions: List[Dict],
    image_width: int,
    image_height: int,
    base_y_offset: int = 100
) -> List[Annotation]:
    """
    Automatically generate annotation positions for a question
    This is a helper function that can be called during grading
    
    Args:
        question_number: The question number being graded
        sub_questions: List of sub-question data with marks
        image_width: Width of the image
        image_height: Height of the image
        base_y_offset: Starting Y position for annotations
        
    Returns:
        List of Annotation objects
    """
    annotations = []
    margin_left = 50
    y_spacing = 80  # Space between annotations vertically
    current_y = base_y_offset
    
    # Add point number circle
    annotations.append(Annotation(
        annotation_type=AnnotationType.POINT_NUMBER,
        x=margin_left,
        y=current_y,
        text=str(question_number),
        size=25
    ))
    
    # Add step labels and scores for each sub-question
    for idx, sub_q in enumerate(sub_questions):
        current_y += y_spacing
        
        # Step label
        step_label = f"Q{question_number}Step{idx + 1}"
        annotations.append(Annotation(
            annotation_type=AnnotationType.STEP_LABEL,
            x=margin_left + 40,
            y=current_y,
            text=step_label
        ))
        
        # Score circle
        score = sub_q.get('obtained_marks', 0)
        annotations.append(Annotation(
            annotation_type=AnnotationType.SCORE_CIRCLE,
            x=margin_left + 200,
            y=current_y + 15,
            text=str(int(score)) if score == int(score) else str(score),
            size=30
        ))
        
        # Add checkmarks for correct points (example logic)
        if score > 0:
            annotations.append(Annotation(
                annotation_type=AnnotationType.CHECKMARK,
                x=margin_left + 100,
                y=current_y + 5,
                size=25
            ))
        else:
            # Add flag for incorrect or missing
            annotations.append(Annotation(
                annotation_type=AnnotationType.FLAG_CIRCLE,
                x=margin_left + 100,
                y=current_y + 15,
                size=25
            ))
    
    return annotations
