import asyncio
import json
import traceback
from collections.abc import Callable
from pathlib import Path
from fastapi.encoders import jsonable_encoder
from fastapi import FastAPI, UploadFile, File, HTTPException, Body
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import StreamingResponse, FileResponse
from typing import Any

from backend.models import ChatRequest, PDFInfo, UploadResponse
from backend.services import config_service, pdf_service, embedding_service, chroma_service, llm_service

# ─── APP SETUP ────────────────────────────────────────────────────────

app = FastAPI(title="REI", description="PDF Chat with RAG")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Serve frontend static files
FRONTEND_DIR = Path(__file__).parent.parent / "frontend"

# In-memory PDF metadata registry (loaded from storage on startup)
pdf_registry: dict[str, dict] = {}


# ─── STARTUP ──────────────────────────────────────────────────────────

@app.on_event("startup")
async def startup():
    """Load existing PDF metadata on startup."""
    meta_file = pdf_service.STORAGE_DIR / "_registry.json"
    if meta_file.exists():
        try:
            with open(meta_file, "r") as f:
                pdf_registry.update(json.load(f))
        except Exception:
            pass


def _save_registry():
    """Persist PDF registry to disk."""
    meta_file = pdf_service.STORAGE_DIR / "_registry.json"
    pdf_service.STORAGE_DIR.mkdir(exist_ok=True)
    with open(meta_file, "w") as f:
        json.dump(pdf_registry, f, indent=2)


# ─── ROUTES: PDF MANAGEMENT ──────────────────────────────────────────


def _process_uploaded_pdf(
    file_bytes: bytes,
    filename: str,
    progress_callback: Callable[[str, int], None] | None = None,
) -> dict:
    """Shared logic to extract, embed, and store a PDF from a local upload."""
    config = config_service.load_config()
    report = progress_callback or (lambda _stage, _percent: None)

    # Step 1 & 2: Extract text and chunk
    report("Saving PDF...", 5)
    result = pdf_service.process_pdf(file_bytes, filename, report)

    # Step 3: Create embeddings
    report(f"Generating embeddings for {result['chunk_count']} chunks...", 52)

    def report_embedding_progress(completed: int, total: int) -> None:
        percent = 52 + round(28 * completed / max(total, 1))
        report(f"Generating embeddings ({completed}/{total} chunks)...", percent)

    embeddings = embedding_service.embed(
        result["chunks"],
        mode=config.get("embedding_mode", "local"),
        api_key=config.get("embedding_api_key", "") or config.get("openai_api_key", ""),
        progress_callback=report_embedding_progress,
    )

    # Step 4: Store in ChromaDB
    report("Storing document chunks...", 82)

    def report_storage_progress(completed: int, total: int) -> None:
        percent = 82 + round(16 * completed / max(total, 1))
        report(f"Storing chunks ({completed}/{total})...", percent)

    stored_count = chroma_service.store_document(
        pdf_id=result["pdf_id"],
        chunks=result["chunks"],
        embeddings=embeddings,
        metadata={
            "filename": result["filename"],
            "page_count": result["page_count"],
            "embedding_type": config.get("embedding_mode", "local"),
        },
        progress_callback=report_storage_progress,
    )

    # Update registry
    pdf_info = {
        "pdf_id": result["pdf_id"],
        "filename": result["filename"],
        "page_count": result["page_count"],
        "chunk_count": result["chunk_count"],
        "embedding_type": config.get("embedding_mode", "local"),
        "upload_date": result["upload_date"],
        "file_size": result["file_size"],
    }
    pdf_registry[result["pdf_id"]] = pdf_info
    _save_registry()
    report("Finished processing PDF.", 100)

    return {
        "success": True,
        "message": f"Processed '{filename}': {result['page_count']} pages, {stored_count} chunks stored.",
        "pdf_info": PDFInfo(**pdf_info),
    }


def _upload_event_stream(
    worker: Callable[
        [Callable[[str, int], None], Callable[[str, Any], None]], Any
    ],
):
    async def events():
        loop = asyncio.get_running_loop()
        queue: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue()

        def report(stage: str, percent: int) -> None:
            loop.call_soon_threadsafe(
                queue.put_nowait,
                {"type": "progress", "stage": stage, "progress": percent},
            )

        def emit(event_type: str, payload: Any) -> None:
            loop.call_soon_threadsafe(
                queue.put_nowait, {"type": event_type, event_type: payload}
            )

        def run_worker() -> None:
            try:
                result = worker(report, emit)
                loop.call_soon_threadsafe(
                    queue.put_nowait, {"type": "result", "result": result}
                )
            except ValueError as error:
                loop.call_soon_threadsafe(
                    queue.put_nowait, {"type": "error", "message": str(error)}
                )
            except Exception as error:
                traceback.print_exc()
                loop.call_soon_threadsafe(
                    queue.put_nowait,
                    {"type": "error", "message": f"Upload failed: {error}"},
                )
            finally:
                loop.call_soon_threadsafe(queue.put_nowait, None)

        loop.run_in_executor(None, run_worker)
        while True:
            event = await queue.get()
            if event is None:
                break
            yield f"data: {json.dumps(jsonable_encoder(event))}\n\n"
            await asyncio.sleep(0)
        yield "data: [DONE]\n\n"

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.post("/api/upload", response_model=UploadResponse)
async def upload_pdf(file: UploadFile = File(...)):
    """Upload a PDF from the local system: extract text, chunk, embed, store in ChromaDB."""
    if not file.filename or not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Only PDF files are accepted.")

    try:
        file_bytes = await file.read()
        result = _process_uploaded_pdf(file_bytes, file.filename)
        return UploadResponse(**result)

    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    except Exception as e:
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"Upload failed: {str(e)}")


@app.post("/api/upload-multiple")
async def upload_multiple_pdfs(files: list[UploadFile] = File(...)):
    """Upload multiple PDFs selected from the user's machine."""
    if not files:
        raise HTTPException(status_code=400, detail="No PDF files were provided.")

    results = []
    for file in files:
        if not file.filename or not file.filename.lower().endswith(".pdf"):
            raise HTTPException(status_code=400, detail="Only PDF files are accepted.")
        try:
            file_bytes = await file.read()
            results.append(_process_uploaded_pdf(file_bytes, file.filename))
        except ValueError as e:
            raise HTTPException(status_code=422, detail=str(e))
        except Exception as e:
            traceback.print_exc()
            raise HTTPException(status_code=500, detail=f"Upload failed: {str(e)}")

    return {"success": True, "uploaded": results}


@app.post("/api/upload-progress")
async def upload_pdf_with_progress(file: UploadFile = File(...)):
    """Upload one PDF and stream real processing-stage updates."""
    filename = file.filename
    if not filename or not filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Only PDF files are accepted.")

    file_bytes = await file.read()
    return _upload_event_stream(
        lambda report, _emit: _process_uploaded_pdf(file_bytes, filename, report)
    )


@app.post("/api/upload-multiple-progress")
async def upload_multiple_pdfs_with_progress(files: list[UploadFile] = File(...)):
    """Upload multiple PDFs and stream real processing-stage updates."""
    if not files:
        raise HTTPException(status_code=400, detail="No PDF files were provided.")

    upload_files = []
    for file in files:
        filename = file.filename
        if not filename or not filename.lower().endswith(".pdf"):
            raise HTTPException(status_code=400, detail="Only PDF files are accepted.")
        upload_files.append((filename, await file.read()))

    def process_all(
        report: Callable[[str, int], None],
        emit: Callable[[str, Any], None],
    ) -> dict:
        results = []
        total_files = len(upload_files)
        for index, (filename, file_bytes) in enumerate(upload_files, start=1):
            start_percent = round((index - 1) * 100 / total_files)
            file_progress = 100 / total_files
            report(f"Processing PDF {index}/{total_files}: {filename}", start_percent)

            def report_file_progress(stage: str, percent: int) -> None:
                overall_percent = round(
                    start_percent + file_progress * percent / 100
                )
                report(f"PDF {index}/{total_files}: {stage}", overall_percent)

            result = _process_uploaded_pdf(file_bytes, filename, report_file_progress)
            results.append(result)
            emit("uploaded", result)
        return {"success": True, "uploaded": results}

    return _upload_event_stream(process_all)


@app.get("/api/pdfs")
async def list_pdfs():
    """List all uploaded PDFs with metadata."""
    return list(pdf_registry.values())


@app.delete("/api/pdfs/{pdf_id}")
async def delete_pdf(pdf_id: str):
    """Delete a PDF from storage and ChromaDB."""
    if pdf_id not in pdf_registry:
        raise HTTPException(status_code=404, detail="PDF not found.")

    chroma_service.delete_collection(pdf_id)
    pdf_service.delete_pdf(pdf_id)

    del pdf_registry[pdf_id]
    _save_registry()

    return {"success": True, "message": f"Deleted PDF '{pdf_id}'."}


# ─── ROUTES: CHAT ────────────────────────────────────────────────────

@app.post("/api/chat")
async def chat(request: ChatRequest):
    """
    Chat with PDF(s) using RAG.
    Streams the response as Server-Sent Events (SSE).

    pdf_ids behavior:
    - [] (empty) → search ALL documents
    - ["id1"] → search single document
    - ["id1", "id2", ...] → search specific documents
    """
    config = config_service.load_config()

    try:
        # Step 1: Embed the question
        query_embedding = embedding_service.embed_query(
            request.question,
            mode=config.get("embedding_mode", "local"),
            api_key=config.get("embedding_api_key", "") or config.get("openai_api_key", ""),
        )

        # Step 2: Retrieve relevant chunks based on scope
        if not request.pdf_ids:
            # All docs mode
            chunks = chroma_service.query_all_collections(
                query_embedding=query_embedding,
                n_results=5,
            )
        elif len(request.pdf_ids) == 1:
            # Single PDF mode
            chunks = chroma_service.query_collection(
                pdf_id=request.pdf_ids[0],
                query_embedding=query_embedding,
                n_results=5,
            )
        else:
            # Multi-PDF mode
            chunks = chroma_service.query_selected_collections(
                pdf_ids=request.pdf_ids,
                query_embedding=query_embedding,
                n_results=5,
            )

        if not chunks:
            async def no_context():
                yield "data: No relevant context found in the documents.\n\n"
                yield "data: [DONE]\n\n"
            return StreamingResponse(no_context(), media_type="text/event-stream")

        context = "\n\n---\n\n".join(chunks)

        # Step 3: Stream LLM response
        async def event_stream():
            try:
                async for token in llm_service.generate(
                    question=request.question,
                    context=context,
                    history=request.history,
                    config=config,
                ):
                    # SSE format: escape newlines for SSE protocol
                    escaped = token.replace("\n", "\\n")
                    yield f"data: {escaped}\n\n"
                yield "data: [DONE]\n\n"
            except Exception as e:
                traceback.print_exc()
                yield f"data: Error: {str(e)}\n\n"
                yield "data: [DONE]\n\n"

        return StreamingResponse(event_stream(), media_type="text/event-stream")

    except Exception as e:
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"Chat failed: {str(e)}")


# ─── ROUTES: SETTINGS ────────────────────────────────────────────────

@app.get("/api/settings")
async def get_settings():
    """Get current configuration."""
    config = config_service.load_config()
    # Mask API keys for security (show last 4 chars only)
    masked = dict(config)
    for key in ["openai_api_key", "openrouter_api_key", "gemini_api_key", "anthropic_api_key", "huggingface_api_key", "embedding_api_key"]:
        val = masked.get(key, "")
        if val and len(val) > 4:
            masked[key] = "•" * (len(val) - 4) + val[-4:]
    return masked


@app.post("/api/settings")
async def update_settings(settings: dict[str, Any] = Body(...)):
    """Update configuration. Only non-empty values are updated."""
    # Filter out masked values (don't overwrite with dots)
    clean = {}
    for k, v in settings.items():
        if isinstance(v, str) and "•" in v:
            continue  # Skip masked values
        clean[k] = v

    config_service.update_config(clean)
    return {"success": True, "message": "Settings updated."}


# ─── SERVE FRONTEND ──────────────────────────────────────────────────

@app.get("/")
async def serve_index():
    """Serve the frontend index.html."""
    return FileResponse(FRONTEND_DIR / "index.html")


# Mount static files (CSS, JS) — must be AFTER explicit routes
app.mount("/", StaticFiles(directory=str(FRONTEND_DIR)), name="frontend")
