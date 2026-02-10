"""
Annotation utility for grading papers with visual feedback
Supports drawing circles, checkmarks, boxes, and text overlays on student answer images
"""
import io
import math
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
    CROSS_MARK = "cross_mark"  # Red X mark for incorrect answers
    ERROR_UNDERLINE = "error_underline"  # Underline marking an error
    SCORE_BOX = "score_box"  # Red box with score (e.g., 2/10)
    MARGIN_NOTE = "margin_note"  # Red handwritten-style margin note
    MARGIN_BRACKET = "margin_bracket"  # Red bracket in the margin
    HIGHLIGHT_BOX = "highlight_box"  # Box around a text region
    COMMENT = "comment"  # Margin comment near anchor
    CALLOUT_LINE = "callout_line"  # Line/arrow from margin note to text

class Annotation:
    """Represents a single annotation on an image"""
    def __init__(
        self,
        annotation_type: str,
        x: int,
        y: int,
        text: str = "",
        color: str = "green",
        size: int = 30,
        width: Optional[int] = None,
        height: Optional[int] = None
    ):
        self.annotation_type = annotation_type
        self.x = x
        self.y = y
        self.text = text
        self.color = color
        self.size = size
        self.width = width
        self.height = height
    
    def to_dict(self) -> Dict:
        """Convert annotation to dictionary for storage"""
        return {
            "type": self.annotation_type,
            "x": self.x,
            "y": self.y,
            "text": self.text,
            "color": self.color,
            "size": self.size,
            "width": self.width,
            "height": self.height
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
            size=data.get("size", 30),
            width=data.get("width"),
            height=data.get("height")
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


def draw_cross_mark(
    draw: ImageDraw,
    x: int,
    y: int,
    size: int = 30,
    text: str = "",
    font: Optional[ImageFont.FreeTypeFont] = None
):
    """Draw a red X mark for incorrect answers"""
    cross_color = get_color_rgb("red")
    line_width = 3
    
    # Draw X shape
    # Line 1: top-left to bottom-right
    draw.line([(x, y), (x + size, y + size)], fill=cross_color, width=line_width)
    # Line 2: top-right to bottom-left
    draw.line([(x + size, y), (x, y + size)], fill=cross_color, width=line_width)
    
    # Optionally draw text below the X
    if text:
        text_y = y + size + 5
        text_color = get_color_rgb("red")
        if font:
            draw.text((x, text_y), text, fill=text_color, font=font)
        else:
            draw.text((x, text_y), text, fill=text_color)


def draw_error_underline(
    draw: ImageDraw,
    x: int,
    y: int,
    width: int = 100,
    text: str = "",
    font: Optional[ImageFont.FreeTypeFont] = None
):
    """Draw a red wavy underline for errors"""
    underline_color = get_color_rgb("red")
    line_width = 2
    
    # Draw a wavy line (zigzag pattern)
    wave_height = 4
    wave_width = 8
    points = []
    current_x = x
    going_up = True
    
    while current_x < x + width:
        if going_up:
            points.append((current_x, y))
        else:
            points.append((current_x, y + wave_height))
        current_x += wave_width // 2
        going_up = not going_up
    
    # Draw the wavy line
    if len(points) > 1:
        draw.line(points, fill=underline_color, width=line_width)
    
    # Optionally draw text below
    if text:
        text_y = y + wave_height + 5
        text_color = get_color_rgb("red")
        if font:
            draw.text((x, text_y), text, fill=text_color, font=font)
        else:
            draw.text((x, text_y), text, fill=text_color)


def draw_score_box(
    draw: ImageDraw,
    x: int,
    y: int,
    text: str,
    color: str = "red",
    font: Optional[ImageFont.FreeTypeFont] = None
):
    """Draw a boxed score like a teacher's margin total"""
    box_color = get_color_rgb(color)
    text_color = box_color

    if font:
        bbox = draw.textbbox((0, 0), text, font=font)
        text_width = bbox[2] - bbox[0]
        text_height = bbox[3] - bbox[1]
    else:
        text_width = len(text) * 8
        text_height = 12

    padding_x = 6
    padding_y = 4
    rect = [
        x,
        y,
        x + text_width + padding_x * 2,
        y + text_height + padding_y * 2
    ]
    draw.rectangle(rect, outline=box_color, width=2)
    draw.text((x + padding_x, y + padding_y), text, fill=text_color, font=font)


def draw_margin_note(
    draw: ImageDraw,
    x: int,
    y: int,
    text: str,
    color: str = "red",
    font: Optional[ImageFont.FreeTypeFont] = None
):
    """Draw a red margin note (simple handwritten-style text)"""
    note_color = get_color_rgb(color)
    draw.text((x, y), text, fill=note_color, font=font)


def draw_margin_bracket(
    draw: ImageDraw,
    x: int,
    y: int,
    height: int = 60,
    color: str = "red"
):
    """Draw a teacher-style bracket in the margin"""
    bracket_color = get_color_rgb(color)
    line_width = 2
    top = y
    bottom = y + height
    draw.line([(x, top), (x, bottom)], fill=bracket_color, width=line_width)
    draw.line([(x, top), (x + 12, top)], fill=bracket_color, width=line_width)
    draw.line([(x, bottom), (x + 12, bottom)], fill=bracket_color, width=line_width)


def draw_highlight_box(
    draw: ImageDraw,
    x: int,
    y: int,
    width: int,
    height: int,
    color: str = "red"
):
    """Draw a rectangular highlight box around a region"""
    box_color = get_color_rgb(color)
    rect = [x, y, x + max(2, width), y + max(2, height)]
    draw.rectangle(rect, outline=box_color, width=2)


def draw_callout_line(
    draw: ImageDraw,
    x1: int,
    y1: int,
    x2: int,
    y2: int,
    color: str = "red"
):
    """Draw a callout line with a small arrow head"""
    line_color = get_color_rgb(color)
    draw.line([(x1, y1), (x2, y2)], fill=line_color, width=2)
    # Arrow head
    arrow_size = 6
    angle = math.atan2(y2 - y1, x2 - x1)
    left = (x2 - arrow_size * math.cos(angle - 0.4), y2 - arrow_size * math.sin(angle - 0.4))
    right = (x2 - arrow_size * math.cos(angle + 0.4), y2 - arrow_size * math.sin(angle + 0.4))
    draw.polygon([(x2, y2), left, right], fill=line_color)


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
        # Skip if no annotations
        if not annotations:
            return image_base64
            
        # Decode base64 image
        img_data = base64.b64decode(image_base64)
        img = Image.open(io.BytesIO(img_data))
        
        # Ensure RGB mode
        if img.mode != 'RGB':
            img = img.convert('RGB')
        
        # Create drawing context
        draw = ImageDraw.Draw(img)
        
        # Try to load a font (fallback to default if not available)
        font = None
        font_large = None
        font_paths = [
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
            "/Library/Fonts/Arial.ttf",
            "/System/Library/Fonts/Supplemental/Arial.ttf"
        ]
        for path in font_paths:
            try:
                font = ImageFont.truetype(path, 16)
                font_large = ImageFont.truetype(path, 20)
                break
            except Exception:
                continue
        if not font or not font_large:
            if not getattr(apply_annotations_to_image, "_font_warned", False):
                logger.warning("Could not load custom font, using default")
                apply_annotations_to_image._font_warned = True
        
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
            
            elif ann.annotation_type == AnnotationType.CROSS_MARK:
                # Draw a red X mark
                draw_cross_mark(draw, ann.x, ann.y, ann.size, ann.text, font)
            
            elif ann.annotation_type == AnnotationType.ERROR_UNDERLINE:
                # Draw a red underline
                draw_error_underline(draw, ann.x, ann.y, ann.size, ann.text, font)

            elif ann.annotation_type == AnnotationType.SCORE_BOX:
                draw_score_box(draw, ann.x, ann.y, ann.text, ann.color or "red", font)

            elif ann.annotation_type == AnnotationType.MARGIN_NOTE:
                draw_margin_note(draw, ann.x, ann.y, ann.text, ann.color or "red", font)

            elif ann.annotation_type == AnnotationType.MARGIN_BRACKET:
                draw_margin_bracket(draw, ann.x, ann.y, ann.size, ann.color or "red")

            elif ann.annotation_type == AnnotationType.COMMENT:
                draw_margin_note(draw, ann.x, ann.y, ann.text, ann.color or "red", font)

            elif ann.annotation_type == AnnotationType.HIGHLIGHT_BOX:
                if ann.width and ann.height:
                    draw_highlight_box(draw, ann.x, ann.y, ann.width, ann.height, ann.color or "red")

            elif ann.annotation_type == AnnotationType.CALLOUT_LINE:
                if ann.width is not None and ann.height is not None:
                    draw_callout_line(draw, ann.x, ann.y, ann.width, ann.height, ann.color or "red")
        
        # Convert back to base64 with optimized quality for faster processing
        buffered = io.BytesIO()
        img.save(buffered, format="JPEG", quality=85, optimize=False)  # Reduced quality, disabled optimize for speed
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
