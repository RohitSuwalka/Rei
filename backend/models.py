from pydantic import BaseModel
from typing import Optional


class ChatRequest(BaseModel):
    """Request body for the chat endpoint."""
    question: str
    pdf_ids: list[str] = []  # empty list = all docs mode
    history: list[dict] = []  # conversation history [{role, content}]


class ChatMessage(BaseModel):
    """A single chat message."""
    role: str  # "user" or "assistant"
    content: str


class SettingsModel(BaseModel):
    """Application settings."""
    llm_mode: str = "local"  # "local" or "api"
    api_provider: str = "openai"  # "openai", "openrouter", "gemini", "anthropic", "huggingface"
    openai_api_key: str = ""
    openrouter_api_key: str = ""
    gemini_api_key: str = ""
    anthropic_api_key: str = ""
    anthropic_workspace_id: str = ""
    huggingface_api_key: str = ""
    openai_model: str = "gpt-4o-mini"
    openrouter_model: str = "openrouter/free"
    gemini_model: str = "gemini-3.8-flash"
    anthropic_model: str = "claude-sonnet-4-20250514"
    huggingface_model: str = "openai/gpt-oss-120b"
    ollama_model: str = "qwen3:4b"
    embedding_mode: str = "local"  # "local" or "api"
    embedding_api_key: str = ""  # separate key for embeddings if desired


class PDFInfo(BaseModel):
    """Metadata about an uploaded PDF."""
    pdf_id: str
    filename: str
    page_count: int
    chunk_count: int
    embedding_type: str  # "local" or "api"
    upload_date: str
    file_size: str  # human readable


class UploadResponse(BaseModel):
    """Response after uploading a PDF."""
    success: bool
    message: str
    pdf_info: Optional[PDFInfo] = None
