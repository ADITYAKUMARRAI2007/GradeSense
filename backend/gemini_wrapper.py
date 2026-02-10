"""
Google Gemini AI Wrapper - Replaces emergentintegrations with native Google AI
Provides compatibility classes and functions for seamless integration
"""

import google.generativeai as genai
import base64
import asyncio
import logging
from typing import List, Optional, Any
import uuid

logger = logging.getLogger(__name__)

class ImageContent:
    """Wrapper for image content in Gemini API format"""
    def __init__(self, image_base64: str):
        self.image_base64 = image_base64
    
    def get_data(self):
        """Get raw image data"""
        return base64.b64decode(self.image_base64)

class UserMessage:
    """Wrapper for user messages with optional file contents"""
    def __init__(self, text: str = "", file_contents: List[ImageContent] = None):
        self.text = text
        self.file_contents = file_contents or []
    
    def __str__(self):
        return self.text
    
    def __repr__(self):
        return f"UserMessage(text='{self.text[:50]}...', images={len(self.file_contents)})"

class LlmChat:
    """
    Compatibility wrapper for Gemini Chat API
    Provides a similar interface to emergentintegrations.llm.chat.LlmChat
    """
    def __init__(self, api_key: str = None, session_id: str = None, system_message: str = None):
        self.api_key = api_key
        self.session_id = session_id or str(uuid.uuid4())
        self.system_message = system_message
        self.model_name = "gemini-2.5-flash"
        self.temperature = 0.0
        self.model = None
        self.chat = None
        self._initialize()
    
    def _initialize(self):
        """Initialize the Gemini model and chat session"""
        try:
            if self.api_key:
                genai.configure(api_key=self.api_key)
            
            self.model = genai.GenerativeModel(
                model_name=self.model_name,
                system_instruction=self.system_message if self.system_message else None
            )
            self.chat = self.model.start_chat(history=[])
        except Exception as e:
            logger.error(f"Error initializing Gemini chat: {e}")
            raise
    
    def with_model(self, provider: str, model_name: str) -> 'LlmChat':
        """Set the model (provider and name)"""
        if provider.lower() == "gemini":
            self.model_name = model_name
            self._initialize()
        return self
    
    def with_params(self, temperature: float = None, **kwargs) -> 'LlmChat':
        """Set model parameters"""
        if temperature is not None:
            self.temperature = temperature
        return self
    
    async def send_message(self, message: Any) -> 'ResponseWrapper':
        """
        Send a message to the Gemini model
        
        Args:
            message: Can be:
                - UserMessage object with text and file_contents
                - String text
                - List of content items
        
        Returns:
            ResponseWrapper with .text attribute
        """
        try:
            # Prepare content based on message type
            content = []
            
            if isinstance(message, str):
                # Simple text message
                content = [message]
            elif isinstance(message, UserMessage):
                # UserMessage with optional images
                if message.text:
                    content.append(message.text)
                
                # Add images
                for img_content in message.file_contents:
                    try:
                        image_data = img_content.get_data()
                        # Create Gemini-compatible image part (new SDK format)
                        image_part = {
                            "mime_type": "image/jpeg",
                            "data": image_data
                        }
                        content.append(image_part)
                    except Exception as e:
                        logger.error(f"Error processing image content: {e}")
            elif isinstance(message, list):
                # Direct list of content items
                content = message
            else:
                content = [str(message)]
            
            # Make the API call in executor to avoid blocking
            loop = asyncio.get_event_loop()
            response = await loop.run_in_executor(
                None,
                lambda: self.chat.send_message(content)
            )
            
            # Wrap response
            return ResponseWrapper(response)
        
        except Exception as e:
            logger.error(f"Error sending message to Gemini: {e}", exc_info=True)
            raise

class ResponseWrapper:
    """Wrapper for Gemini API responses"""
    def __init__(self, response: Any):
        self.response = response
    
    @property
    def text(self) -> str:
        """Get response text"""
        try:
            if hasattr(self.response, 'text'):
                return self.response.text
            return str(self.response)
        except Exception as e:
            logger.error(f"Error extracting response text: {e}")
            return ""
    
    def strip(self) -> str:
        """Get stripped response text (for compatibility)"""
        return self.text.strip()
    
    def __str__(self) -> str:
        """String representation"""
        return self.text
