# REI — Chat with your PDFs

REI is a locally hosted PDF question-answering app. Upload one or more PDF files, let REI extract and index their text, then ask questions in a chat interface. Answers are generated from text retrieved from the selected documents.

The app uses a FastAPI backend, a static HTML/CSS/JavaScript frontend, ChromaDB for vector storage, and either local or hosted language models.

## Features

- Choose PDFs from your computer; upload one or multiple documents.
- See upload progress for text extraction, chunking, embedding generation, and vector storage.
- Ask questions about one selected PDF, several selected PDFs, or all indexed PDFs.
- Use local Ollama models or supported hosted providers.
- Choose local Sentence Transformers embeddings or OpenAI embeddings.
- Keep PDF files, the ChromaDB database, and API configuration on your machine.

## Requirements

- Windows, macOS, or Linux.
- Python 3.10 or newer.
- Ollama installed and running if you want local language-model chat.
- An API key for any hosted language or embedding provider you choose.

The first use of local embeddings downloads the `all-MiniLM-L6-v2` model. Ollama also needs to download the local chat model you select.

## Run locally on Windows

Open PowerShell in the project folder.

### 1. Create and activate a virtual environment

```powershell
Set-Location "C:\path\to\Rei"
py -m venv .venv
.\.venv\Scripts\Activate.ps1
```

If PowerShell prevents activation, you can run the commands below using the virtual environment's Python executable without activating it.

### 2. Install Python dependencies

```powershell
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

### 3. Start Ollama (for local chat)

Install Ollama from [ollama.com](https://ollama.com/) and make sure it is running. Download the default REI chat model:

```powershell
ollama pull qwen3:4b
```

You can select another model installed in Ollama from REI Settings.

### 4. Start the REI server

```powershell
python -m uvicorn backend.main:app --reload
```

Open [http://127.0.0.1:8000](http://127.0.0.1:8000) in your browser. Keep the terminal open while using REI. Stop the server with **Ctrl+C**.

If you use the project's existing virtual environment instead of activating it:

```powershell
.\.venv\Scripts\python.exe -m uvicorn backend.main:app --reload
```

If port 8000 is busy, choose a different port:

```powershell
python -m uvicorn backend.main:app --reload --port 8001
```

Then open [http://127.0.0.1:8001](http://127.0.0.1:8001).

## Configure models

Open the **Settings** button in REI.

### Language model (answer generation)

Choose **local** to use Ollama, or **api** to choose a hosted provider. Enter that provider's API key and choose one of the listed model IDs or use **Custom model ID...**.

The API providers currently wired into REI are:

| Settings provider | Connection |
| --- | --- |
| OpenAI | OpenAI chat completions API |
| Gemini | Google Gemini API |
| Claude | Anthropic Messages API |
| Hugging Face | Hugging Face Inference Providers chat-completions API |
| Other | OpenRouter API |

The **Other** tab uses OpenRouter internally. Its model IDs use OpenRouter's model naming convention, typically `provider/model`; the default is `openrouter/free`.

Model access, availability, rate limits, and pricing are determined by the provider and your account. A model appearing in the dropdown does not guarantee that your account can use it.

### Embeddings (document search)

Embedding mode is selected separately from the language model:

- **local** uses Sentence Transformers (`all-MiniLM-L6-v2`) on your computer.
- **openai api** uses OpenAI's `text-embedding-3-small` embedding model and requires a working OpenAI API key. API usage may incur charges.

Choose the embedding mode you intend to use before uploading PDFs. Switching embedding modes does not regenerate embeddings for PDFs already indexed. Re-upload documents after changing modes so their stored vectors match the query embeddings.

## Upload and chat workflow

1. Start REI and open it in your browser.
2. Select one or more PDF files using **choose pdf**.
3. Follow the progress indicator while REI extracts text, creates chunks and embeddings, and stores them in ChromaDB.
4. Select the document scope: the current document or all documents. Individual documents can also be selected from the list.
5. Ask a question in the chat box.

REI extracts selectable text from PDFs. Image-only or scanned PDFs may not contain extractable text and currently require OCR before they can be used.

## Configuration and local data

At runtime, REI creates or updates:

- `config.json` — provider, model, and API-key settings.
- `storage/` — uploaded PDF files and the PDF registry.
- `chroma_db/` — ChromaDB collections containing indexed text and embeddings.

These paths are excluded by `.gitignore`; they are local application data, not source files. Back them up separately if you need to preserve local settings or indexed PDFs.

Never commit API keys or other secrets. If a key is exposed, revoke it with the provider and create a replacement.

## API overview

The FastAPI application exposes these main routes:

| Method | Route | Purpose |
| --- | --- | --- |
| `GET` | `/` | Serve the REI web app |
| `POST` | `/api/upload` | Upload one PDF and return a JSON result |
| `POST` | `/api/upload-multiple` | Upload multiple PDFs and return JSON results |
| `POST` | `/api/upload-progress` | Upload one PDF and stream progress events |
| `POST` | `/api/upload-multiple-progress` | Upload multiple PDFs and stream progress events |
| `GET` | `/api/pdfs` | List indexed PDFs |
| `DELETE` | `/api/pdfs/{pdf_id}` | Delete a PDF and its vector collection |
| `POST` | `/api/chat` | Ask a question; response is streamed using Server-Sent Events |
| `GET` | `/api/settings` | Read settings (API keys are masked) |
| `POST` | `/api/settings` | Save settings |

Interactive API documentation is available at [http://127.0.0.1:8000/docs](http://127.0.0.1:8000/docs) while the server is running.

For `/api/chat`, send JSON containing `question`, `pdf_ids`, and optionally `history`. An empty `pdf_ids` list searches all indexed documents; one or more IDs limit the search to those PDFs.

## Troubleshooting

- **Port 8000 is already in use:** stop the other REI server with Ctrl+C, or start REI with `--port 8001`.
- **Ollama connection/model error:** make sure Ollama is running and the selected model has been downloaded with `ollama pull <model-name>`.
- **Provider authentication or model error:** verify the provider key, model ID, account access, and any provider-specific workspace requirements in Settings.
- **Embedding API error:** verify the OpenAI key and account billing/access. Local embeddings avoid OpenAI embedding API usage charges.
- **No text could be extracted:** the PDF may be scanned/image-only or otherwise have no selectable text. OCR is not currently built into REI.
- **Old PDFs fail after changing embedding mode:** re-upload them using the currently selected embedding mode.
- **Existing documents are missing from the list:** keep the `storage/` and `chroma_db/` directories together; the registry and vector data are stored separately.

## Development checks

There is no automated test suite configured in the repository at this time. From the project root, the following checks can be used:

```powershell
python -m compileall backend
node --check frontend/app.js
git diff --check
```

## Security note

REI is intended for local development and personal use. The current FastAPI configuration allows cross-origin requests from any origin and does not add user authentication. Do not expose the server directly to the public internet or an untrusted network without adding appropriate authentication, access controls, and a restricted CORS policy.
