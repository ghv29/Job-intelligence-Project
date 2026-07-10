from __future__ import annotations

from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from uuid import uuid4

from pinecone import Pinecone

from app.config import settings


def _get_streamlit_secret(key: str) -> str:
    """
    Return a secret from Streamlit (`st.secrets`) when available, else "".
    """
    try:
        import streamlit as st  # type: ignore

        return str(st.secrets.get(key, "")) or ""
    except Exception:
        return ""


@lru_cache(maxsize=1)
def _get_embedding_model():
    """
    Load the local embedding model once per process (cached).

    Uses fastembed (ONNX, CPU) so embeddings are free and offline — no OpenAI
    key or quota. The model downloads once (~220 MB) into a stable cache dir,
    then loads from disk on every later run.
    """
    import os

    # Quiet the HuggingFace "Fetching N files" progress bar and symlink warning.
    # After the one-time download these files are served from the local cache,
    # so the bar was misleading (it prints even on a pure cache hit).
    os.environ.setdefault("HF_HUB_DISABLE_PROGRESS_BARS", "1")
    os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")

    from fastembed import TextEmbedding

    cache_dir = Path.home() / ".cache" / "fastembed"
    cache_dir.mkdir(parents=True, exist_ok=True)
    return TextEmbedding(settings.embedding_model, cache_dir=str(cache_dir))


def _get_index():
    api_key = _get_streamlit_secret("PINECONE_API_KEY") or settings.pinecone_api_key
    index_name = _get_streamlit_secret("PINECONE_INDEX") or settings.pinecone_index
    if not api_key:
        raise RuntimeError("PINECONE_API_KEY is missing. Set it in your .env file.")
    if not index_name:
        raise RuntimeError("PINECONE_INDEX is missing. Set it in your .env file.")
    pc = Pinecone(api_key=api_key)
    return pc.Index(index_name)


def embed_text(text: str) -> list[float]:
    """
    Embed a text string using a local fastembed model (free, offline).

    Returns the embedding vector as a list of floats (empty list for empty text).
    The vector size must match the Pinecone index dimension (settings.embedding_dim).
    """
    text = (text or "").strip()
    if not text:
        return []

    model = _get_embedding_model()
    # model.embed yields one vector per input text; take the first.
    vector = next(iter(model.embed([text])))
    return [float(x) for x in vector]


def upsert_job(job_id: int, title: str, company: str, location: str, description: str) -> str:
    """
    Create/replace a Pinecone vector for a job posting and return its vector id.

    The vector id is `str(job_id)`. Metadata includes: job_id, title, company, location.
    """
    idx = _get_index()
    vector_id = str(job_id)

    doc = f"{title or ''} {(description or '')[:1000]}".strip()
    values = embed_text(doc)

    idx.upsert(
        vectors=[
            {
                "id": vector_id,
                "values": values,
                "metadata": {
                    "job_id": int(job_id),
                    "title": title or "",
                    "company": company or "",
                    "location": location or "",
                },
            }
        ]
    )
    return vector_id


def search_jobs_semantic(query: str, top_k: int = 10) -> list[dict]:
    """
    Semantic-search jobs in Pinecone using the user's query.

    Returns a list of dicts with: job_id, title, company, location, score.
    """
    idx = _get_index()
    qvec = embed_text(query or "")
    if not qvec:
        return []

    res = idx.query(vector=qvec, top_k=int(top_k), include_metadata=True)
    matches = getattr(res, "matches", None) or []

    out: list[dict] = []
    for m in matches:
        md = getattr(m, "metadata", None) or {}
        out.append(
            {
                "job_id": md.get("job_id"),
                "title": md.get("title", ""),
                "company": md.get("company", ""),
                "location": md.get("location", ""),
                "score": getattr(m, "score", None),
            }
        )
    return out


def save_memory(content: str, memory_type: str) -> str:
    """
    Store a memory vector in Pinecone under the `agent_memory` namespace.

    Metadata includes: content, memory_type, created_at (ISO timestamp).
    Returns the created Pinecone vector id (a UUID string).
    """
    idx = _get_index()
    vector_id = str(uuid4())

    content = (content or "").strip()
    values = embed_text(content)
    created_at = datetime.now(timezone.utc).isoformat()

    idx.upsert(
        vectors=[
            {
                "id": vector_id,
                "values": values,
                "metadata": {
                    "content": content,
                    "memory_type": memory_type or "",
                    "created_at": created_at,
                },
            }
        ],
        namespace="agent_memory",
    )
    return vector_id


def retrieve_memories(query: str, top_k: int = 5) -> list[str]:
    """
    Retrieve the most relevant memory `content` strings from Pinecone.

    Queries the `agent_memory` namespace and returns memory content strings.
    """
    idx = _get_index()
    qvec = embed_text(query or "")
    if not qvec:
        return []

    res = idx.query(
        vector=qvec,
        top_k=int(top_k),
        include_metadata=True,
        namespace="agent_memory",
    )
    matches = getattr(res, "matches", None) or []
    memories: list[str] = []
    for m in matches:
        md = getattr(m, "metadata", None) or {}
        content = md.get("content")
        if content:
            memories.append(str(content))
    return memories

