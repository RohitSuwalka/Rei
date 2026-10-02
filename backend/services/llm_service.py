import asyncio
import time
from typing import AsyncGenerator
from openai.types.chat import ChatCompletionMessageParam


# ??? RAG PROMPT TEMPLATE ?????????????????????????????????????????????
RAG_SYSTEM_PROMPT = """You are REI, a helpful document assistant. Answer questions using ONLY the provided context from the user's documents. If the context doesn't contain enough information to answer, say so honestly. Be concise and precise."""

RAG_USER_TEMPLATE = """Context from documents:
{context}

---

Question: {question}"""


def _build_messages(
    question: str,
    context: str,
    history: list[dict],
) -> list[ChatCompletionMessageParam]:
    """Build the message list for the LLM with system prompt, history, and current question."""
    messages: list[ChatCompletionMessageParam] = [
        {"role": "system", "content": RAG_SYSTEM_PROMPT}
    ]

    for msg in history[-10:]:
        role = msg.get("role")
        if role in {"system", "user", "assistant"}:
            messages.append({"role": role, "content": msg["content"]})
        else:
            raise ValueError(f"Unsupported conversation role: {role}")

    user_content = RAG_USER_TEMPLATE.format(context=context, question=question)
    messages.append({"role": "user", "content": user_content})
    return messages


# ??? OLLAMA (LOCAL) ??????????????????????????????????????????????????

async def stream_ollama(
    question: str,
    context: str,
    history: list[dict],
    model: str = "qwen3:4b",
) -> AsyncGenerator[str, None]:
    """Stream response from local Ollama."""
    from ollama import chat as ollama_chat

    messages = _build_messages(question, context, history)

    loop = asyncio.get_running_loop()
    async_queue = asyncio.Queue()
    sentinel = object()

    def _run_stream():
        try:
            stream = ollama_chat(
                model=model,
                messages=messages,
                stream=True,
            )
            for chunk in stream:
                try:
                    content = chunk.message.content
                except AttributeError:
                    try:
                        content = chunk["message"]["content"]
                    except (KeyError, TypeError):
                        content = ""
                if content:
                    loop.call_soon_threadsafe(async_queue.put_nowait, content)
        except Exception as e:
            loop.call_soon_threadsafe(async_queue.put_nowait, e)
        finally:
            loop.call_soon_threadsafe(async_queue.put_nowait, sentinel)

    loop.run_in_executor(None, _run_stream)

    while True:
        item = await async_queue.get()
        if item is sentinel:
            break
        if isinstance(item, Exception):
            raise item
        yield item


# ??? OPENAI ??????????????????????????????????????????????????????????

async def stream_openai(
    question: str,
    context: str,
    history: list[dict],
    api_key: str,
    model: str = "gpt-4o-mini",
    base_url: str | None = None,
) -> AsyncGenerator[str, None]:
    """Stream response from OpenAI API."""
    from openai import OpenAI

    client = OpenAI(api_key=api_key, base_url=base_url)
    messages = _build_messages(question, context, history)

    loop = asyncio.get_running_loop()
    async_queue = asyncio.Queue()
    sentinel = object()

    def _run_stream():
        try:
            stream = client.chat.completions.create(
                model=model,
                messages=messages,
                stream=True,
            )
            for chunk in stream:
                if chunk.choices and chunk.choices[0].delta and chunk.choices[0].delta.content:
                    loop.call_soon_threadsafe(async_queue.put_nowait, chunk.choices[0].delta.content)
        except Exception as e:
            loop.call_soon_threadsafe(async_queue.put_nowait, e)
        finally:
            loop.call_soon_threadsafe(async_queue.put_nowait, sentinel)

    loop.run_in_executor(None, _run_stream)

    while True:
        item = await async_queue.get()
        if item is sentinel:
            break
        if isinstance(item, Exception):
            raise item
        yield item


# ??? GOOGLE GEMINI ???????????????????????????????????????????????????

async def stream_gemini(
    question: str,
    context: str,
    history: list[dict],
    api_key: str,
    model: str = "gemini-3.8-flash",
) -> AsyncGenerator[str, None]:
    """Stream response from Google Gemini API."""
    from google import genai

    client = genai.Client(api_key=api_key)

    full_prompt = RAG_SYSTEM_PROMPT + "\n\n"
    for msg in history[-10:]:
        role_label = "User" if msg["role"] == "user" else "Assistant"
        full_prompt += f"{role_label}: {msg['content']}\n\n"
    full_prompt += RAG_USER_TEMPLATE.format(context=context, question=question)

    loop = asyncio.get_running_loop()
    async_queue = asyncio.Queue()
    sentinel = object()

    def _run_stream():
        try:
            emitted_text = False
            for attempt in range(3):
                try:
                    for chunk in client.models.generate_content_stream(
                        model=model,
                        contents=full_prompt,
                    ):
                        if chunk.text:
                            emitted_text = True
                            loop.call_soon_threadsafe(async_queue.put_nowait, chunk.text)
                    break
                except Exception as e:
                    if (
                        not emitted_text
                        and attempt < 2
                        and getattr(e, "code", None) == 503
                    ):
                        time.sleep(1.5 * (attempt + 1))
                        continue
                    loop.call_soon_threadsafe(async_queue.put_nowait, e)
                    break
        finally:
            loop.call_soon_threadsafe(async_queue.put_nowait, sentinel)

    loop.run_in_executor(None, _run_stream)

    while True:
        item = await async_queue.get()
        if item is sentinel:
            break
        if isinstance(item, Exception):
            raise item
        yield item


# ??? ANTHROPIC CLAUDE ????????????????????????????????????????????????

async def stream_anthropic(
    question: str,
    context: str,
    history: list[dict],
    api_key: str,
    model: str = "claude-sonnet-4-20250514",
    workspace_id: str = "",
) -> AsyncGenerator[str, None]:
    """Stream response from Anthropic Claude API."""
    import anthropic

    client = anthropic.Anthropic(
        api_key=api_key,
        default_headers=(
            {"anthropic-workspace-id": workspace_id} if workspace_id else None
        ),
    )
    messages = []

    for msg in history[-10:]:
        messages.append({"role": msg["role"], "content": msg["content"]})

    user_content = RAG_USER_TEMPLATE.format(context=context, question=question)
    messages.append({"role": "user", "content": user_content})

    loop = asyncio.get_running_loop()
    async_queue = asyncio.Queue()
    sentinel = object()

    def _run_stream():
        try:
            with client.messages.stream(
                model=model,
                max_tokens=4096,
                system=RAG_SYSTEM_PROMPT,
                messages=messages,
            ) as stream:
                for text in stream.text_stream:
                    if text:
                        loop.call_soon_threadsafe(async_queue.put_nowait, text)
        except Exception as e:
            loop.call_soon_threadsafe(async_queue.put_nowait, e)
        finally:
            loop.call_soon_threadsafe(async_queue.put_nowait, sentinel)

    loop.run_in_executor(None, _run_stream)

    while True:
        item = await async_queue.get()
        if item is sentinel:
            break
        if isinstance(item, Exception):
            raise item
        yield item


# ??? HUGGING FACE ???????????????????????????????????????????????????

async def stream_huggingface(
    question: str,
    context: str,
    history: list[dict],
    api_key: str,
    model: str = "openai/gpt-oss-120b",
) -> AsyncGenerator[str, None]:
    """Stream response from Hugging Face Inference API."""
    import json
    import httpx

    messages = _build_messages(question, context, history)
    url = "https://router.huggingface.co/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    data = {
        "model": model,
        "messages": messages,
        "stream": True,
    }

    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            async with client.stream("POST", url, headers=headers, json=data) as response:
                if response.status_code != 200:
                    err_text = await response.aread()
                    if response.status_code == 400 and b"model_not_supported" in err_text:
                        yield (
                            "Error: This Hugging Face model is not available from any "
                            "Inference Provider enabled for your account. Choose an available "
                            "chat model in the Hugging Face Playground and enter its model ID "
                            "in REI Settings."
                        )
                        return
                    yield f"Error: Hugging Face API returned status {response.status_code} - {err_text.decode(errors='ignore')}"
                    return

                async for line in response.aiter_lines():
                    if not line:
                        continue
                    if line.startswith("data: "):
                        data_str = line[6:].strip()
                        if data_str == "[DONE]":
                            break
                        try:
                            chunk = json.loads(data_str)
                            token = chunk["choices"][0]["delta"].get("content", "")
                            if token:
                                yield token
                        except Exception:
                            pass
    except Exception as e:
        yield f"Error connecting to Hugging Face: {str(e)}"


# ??? UNIFIED INTERFACE ??????????????????????????????????????????????

async def generate(
    question: str,
    context: str,
    history: list[dict],
    config: dict,
) -> AsyncGenerator[str, None]:
    """
    Unified streaming generator. Routes to the correct provider
    based on config['llm_mode'] and config['api_provider'].
    """
    mode = config.get("llm_mode", "local")

    if mode == "local":
        model = config.get("ollama_model", "qwen3:4b")
        async for token in stream_ollama(question, context, history, model):
            yield token

    elif mode == "api":
        provider = config.get("api_provider", "openai")

        if provider == "openai":
            api_key = config.get("openai_api_key", "")
            model = config.get("openai_model", "gpt-4o-mini")
            if not api_key:
                yield "Error: OpenAI API key not configured. Please add it in Settings."
                return
            async for token in stream_openai(question, context, history, api_key, model):
                yield token

        elif provider == "openrouter":
            api_key = config.get("openrouter_api_key", "")
            model = config.get("openrouter_model", "openrouter/free")
            if not api_key:
                yield "Error: OpenRouter API key not configured. Please add it in Settings."
                return
            async for token in stream_openai(
                question,
                context,
                history,
                api_key,
                model,
                base_url="https://openrouter.ai/api/v1",
            ):
                yield token

        elif provider == "gemini":
            api_key = config.get("gemini_api_key", "")
            model = config.get("gemini_model", "gemini-3.8-flash")
            if not api_key:
                yield "Error: Gemini API key not configured. Please add it in Settings."
                return
            async for token in stream_gemini(question, context, history, api_key, model):
                yield token

        elif provider == "anthropic":
            api_key = config.get("anthropic_api_key", "")
            model = config.get("anthropic_model", "claude-sonnet-4-20250514")
            if not api_key:
                yield "Error: Anthropic API key not configured. Please add it in Settings."
                return
            workspace_id = config.get("anthropic_workspace_id", "").strip()
            async for token in stream_anthropic(
                question,
                context,
                history,
                api_key,
                model,
                workspace_id,
            ):
                yield token

        elif provider == "huggingface":
            api_key = config.get("huggingface_api_key", "")
            model = config.get("huggingface_model", "openai/gpt-oss-120b")
            if not api_key:
                yield "Error: Hugging Face API key not configured. Please add it in Settings."
                return
            async for token in stream_huggingface(question, context, history, api_key, model):
                yield token

        else:
            yield f"Error: Unknown provider '{provider}'"
    else:
        yield f"Error: Unknown LLM mode '{mode}'"
