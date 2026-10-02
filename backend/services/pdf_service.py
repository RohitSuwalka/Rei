import hashlib
import shutil
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from pypdf import PdfReader
from langchain_text_splitters import RecursiveCharacterTextSplitter

# Storage directory for uploaded PDFs
STORAGE_DIR = Path(__file__).parent.parent.parent / "storage"
STORAGE_DIR.mkdir(exist_ok=True)

# Metadata file tracking all PDFs
META_FILE = STORAGE_DIR / "_metadata.json"

# Chunking parameters (same as existing scripts)
CHUNK_SIZE = 1000
CHUNK_OVERLAP = 200


def _generate_pdf_id(filename: str) -> str:
    """Create a stable, unique ID from filename."""
    name = Path(filename).stem.lower()
    # Sanitize: keep only alphanumeric and underscores
    clean = "".join(c if c.isalnum() or c == "_" else "_" for c in name)
    # Add short hash to handle duplicates
    h = hashlib.md5(filename.encode()).hexdigest()[:6]
    return f"{clean}_{h}"


def _human_size(size_bytes: int) -> str:
    """Convert bytes to human-readable string."""
    size = float(size_bytes)
    for unit in ["B", "KB", "MB", "GB"]:
        if size < 1024:
            return f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} TB"


def save_pdf(file_bytes: bytes, filename: str) -> Path:
    """Save uploaded PDF to storage directory."""
    pdf_id = _generate_pdf_id(filename)
    pdf_dir = STORAGE_DIR / pdf_id
    pdf_dir.mkdir(exist_ok=True)
    dest = pdf_dir / filename
    dest.write_bytes(file_bytes)
    return dest


def extract_text(
    pdf_path: Path,
    progress_callback: Callable[[int, int], None] | None = None,
) -> str:
    """Extract full text from PDF (same logic as read_pdf.py)."""
    reader = PdfReader(str(pdf_path))
    full_text = ""
    page_count = len(reader.pages)
    for page_number, page in enumerate(reader.pages, start=1):
        text = page.extract_text()
        if text:
            full_text += text + "\n"
        if progress_callback:
            progress_callback(page_number, page_count)
    return full_text


def get_page_count(pdf_path: Path) -> int:
    """Get number of pages in PDF."""
    reader = PdfReader(str(pdf_path))
    return len(reader.pages)


def chunk_text(text: str) -> list[str]:
    """Split text into chunks (same logic as chunk_pdf.py)."""
    splitter = RecursiveCharacterTextSplitter(
        chunk_size=CHUNK_SIZE,
        chunk_overlap=CHUNK_OVERLAP,
    )
    return splitter.split_text(text)


def process_pdf(
    file_bytes: bytes,
    filename: str,
    progress_callback: Callable[[str, int], None] | None = None,
) -> dict:
    """
    Full PDF processing pipeline:
    1. Save to storage
    2. Extract text
    3. Chunk text
    4. Return metadata + chunks for embedding

    Returns dict with: pdf_id, filename, pdf_path, page_count, chunks, file_size, upload_date
    """
    # Save
    pdf_path = save_pdf(file_bytes, filename)

    # Extract
    if progress_callback:
        progress_callback("Extracting text from PDF...", 10)

    def report_page(page_number: int, total_pages: int) -> None:
        if progress_callback:
            percent = 10 + round(30 * page_number / max(total_pages, 1))
            progress_callback(
                f"Extracting text (page {page_number}/{total_pages})...",
                percent,
            )

    reader = PdfReader(str(pdf_path))
    page_count = len(reader.pages)
    text = extract_text(pdf_path, report_page)

    if not text.strip():
        raise ValueError(f"No text could be extracted from '{filename}'. It may be a scanned/image PDF.")

    # Chunk
    if progress_callback:
        progress_callback("Chunking extracted text...", 45)
    chunks = chunk_text(text)
    if progress_callback:
        progress_callback(f"Created {len(chunks)} text chunks.", 50)

    return {
        "pdf_id": _generate_pdf_id(filename),
        "filename": filename,
        "pdf_path": str(pdf_path),
        "page_count": page_count,
        "chunk_count": len(chunks),
        "chunks": chunks,
        "file_size": _human_size(len(file_bytes)),
        "upload_date": datetime.now().isoformat(),
    }


def delete_pdf(pdf_id: str) -> bool:
    """Delete a PDF and its directory from storage."""
    pdf_dir = STORAGE_DIR / pdf_id
    if pdf_dir.exists():
        shutil.rmtree(pdf_dir)
        return True
    return False


def list_stored_pdfs() -> list[str]:
    """List all PDF IDs in storage."""
    if not STORAGE_DIR.exists():
        return []
    return [
        d.name for d in STORAGE_DIR.iterdir()
        if d.is_dir() and not d.name.startswith("_")
    ]
