import chromadb
from chromadb.api import ClientAPI
from chromadb.api.types import Metadata, PyEmbedding
from collections.abc import Callable
from collections.abc import Sequence
from pathlib import Path

# ChromaDB persistent directory (same location as existing)
CHROMA_DIR = str(Path(__file__).parent.parent.parent / "chroma_db")

# Lazy singleton
_client = None


def _get_client() -> ClientAPI:
    """Get or create the ChromaDB persistent client."""
    global _client
    if _client is None:
        _client = chromadb.PersistentClient(path=CHROMA_DIR)
    return _client


def _collection_name(pdf_id: str) -> str:
    """Sanitize collection name for ChromaDB (3-63 chars, alphanumeric + underscores)."""
    name = f"rei_{pdf_id}"
    # ChromaDB requires 3-63 chars, start/end with alphanumeric
    name = name[:63]
    if not name[0].isalnum():
        name = "r" + name
    if not name[-1].isalnum():
        name = name + "0"
    return name


def store_document(
    pdf_id: str,
    chunks: list[str],
    embeddings: Sequence[Sequence[float]],
    metadata: dict,
    progress_callback: Callable[[int, int], None] | None = None,
) -> int:
    """
    Store document chunks and embeddings in ChromaDB.
    Creates a per-PDF collection.
    Returns the number of stored chunks.
    """
    client = _get_client()
    col_name = _collection_name(pdf_id)

    # Delete existing collection if re-uploading
    try:
        client.delete_collection(col_name)
    except Exception:
        pass

    collection = client.create_collection(
        name=col_name,
        metadata={"pdf_id": pdf_id, **metadata},
    )

    # Batch insert
    ids = [f"{pdf_id}_chunk_{i}" for i in range(len(chunks))]
    metadatas: list[Metadata] = [
        {"pdf_id": pdf_id, "chunk_index": i} for i in range(len(chunks))
    ]

    # ChromaDB has batch limits, insert in batches of 500
    batch_size = 500
    for start in range(0, len(chunks), batch_size):
        end = start + batch_size
        batch_embeddings: list[PyEmbedding] = [
            list(vector) for vector in embeddings[start:end]
        ]
        collection.add(
            ids=ids[start:end],
            documents=chunks[start:end],
            embeddings=batch_embeddings,
            metadatas=metadatas[start:end],
        )
        if progress_callback:
            progress_callback(min(end, len(chunks)), len(chunks))

    return collection.count()


def query_collection(
    pdf_id: str,
    query_embedding: list[float],
    n_results: int = 5,
) -> list[str]:
    """
    Query a specific PDF's collection.
    Returns the top matching document chunks.
    """
    client = _get_client()
    col_name = _collection_name(pdf_id)

    try:
        collection = client.get_collection(col_name)
    except Exception:
        return []

    chunk_count = collection.count()
    if chunk_count == 0:
        return []

    results = collection.query(
        query_embeddings=[query_embedding],
        n_results=min(n_results, chunk_count),
    )

    documents = results["documents"]
    retrieved_ids = results["ids"]
    if not documents or not documents[0] or not retrieved_ids or not retrieved_ids[0]:
        return []

    selected_documents = list(zip(retrieved_ids[0], documents[0]))
    selected_ids = {chunk_id for chunk_id, _ in selected_documents}

    # Semantic search can favor introductory text; include the ending where
    # conclusions and final sections commonly appear.
    tail_start = max(0, chunk_count - 5)
    tail_ids = [
        f"{pdf_id}_chunk_{chunk_index}"
        for chunk_index in range(tail_start, chunk_count)
        if f"{pdf_id}_chunk_{chunk_index}" not in selected_ids
    ]
    if tail_ids:
        tail = collection.get(ids=tail_ids)
        tail_documents = tail["documents"]
        tail_ids_found = tail["ids"]
        if tail_documents and tail_ids_found:
            tail_by_id = dict(zip(tail_ids_found, tail_documents))
            selected_documents.extend(
                (chunk_id, tail_by_id[chunk_id])
                for chunk_id in tail_ids
                if chunk_id in tail_by_id
            )

    return [document for _, document in selected_documents]


def query_all_collections(
    query_embedding: list[float],
    n_results: int = 5,
) -> list[str]:
    """
    Query ALL PDF collections (cross-document search).
    Returns combined results sorted by relevance.
    """
    client = _get_client()
    all_results: list[tuple[float, str]] = []

    for col_info in client.list_collections():
        col_name = col_info if isinstance(col_info, str) else col_info.name
        if not col_name.startswith("rei_"):
            continue

        try:
            collection = client.get_collection(col_name)
            count = collection.count()
            if count == 0:
                continue

            results = collection.query(
                query_embeddings=[query_embedding],
                n_results=min(n_results, count),
            )

            documents = results["documents"]
            distances = results["distances"]
            if documents and documents[0]:
                document_batch = documents[0]
                distance_batch = (
                    distances[0]
                    if distances and distances[0]
                    else [0.0] * len(document_batch)
                )
                for doc, dist in zip(
                    document_batch,
                    distance_batch,
                ):
                    all_results.append((dist, doc))
        except Exception:
            continue

    # Sort by distance (lower = more relevant for L2, depends on metric)
    all_results.sort(key=lambda result: result[0])
    return [document for _, document in all_results[:n_results]]


def query_selected_collections(
    pdf_ids: list[str],
    query_embedding: list[float],
    n_results: int = 5,
) -> list[str]:
    """
    Query specific PDF collections (multi-select mode).
    Returns combined results sorted by relevance.
    """
    client = _get_client()
    all_results = []

    for pdf_id in pdf_ids:
        col_name = _collection_name(pdf_id)
        try:
            collection = client.get_collection(col_name)
            count = collection.count()
            if count == 0:
                continue

            results = collection.query(
                query_embeddings=[query_embedding],
                n_results=min(n_results, count),
            )

            documents = results["documents"]
            distances = results["distances"]
            if documents and documents[0]:
                document_batch = documents[0]
                distance_batch = (
                    distances[0]
                    if distances and distances[0]
                    else [0.0] * len(document_batch)
                )
                for doc, dist in zip(
                    document_batch,
                    distance_batch,
                ):
                    all_results.append({
                        "document": doc,
                        "distance": dist,
                    })
        except Exception:
            continue

    all_results.sort(key=lambda x: x["distance"])
    return [r["document"] for r in all_results[:n_results]]


def delete_collection(pdf_id: str) -> bool:
    """Delete a PDF's collection from ChromaDB."""
    client = _get_client()
    col_name = _collection_name(pdf_id)
    try:
        client.delete_collection(col_name)
        return True
    except Exception:
        return False


def list_collections() -> list[str]:
    """List all REI collection names."""
    client = _get_client()
    collections = client.list_collections()
    names = []
    for col in collections:
        name = col if isinstance(col, str) else col.name
        if name.startswith("rei_"):
            names.append(name)
    return names


def get_collection_info(pdf_id: str) -> dict | None:
    """Get info about a specific collection."""
    client = _get_client()
    col_name = _collection_name(pdf_id)
    try:
        collection = client.get_collection(col_name)
        return {
            "name": col_name,
            "count": collection.count(),
            "metadata": collection.metadata,
        }
    except Exception:
        return None
