from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from openai import OpenAI
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


def _get_openai_client() -> OpenAI:
    api_key = _get_streamlit_secret("OPENAI_API_KEY") or settings.openai_api_key
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY is missing. Set it in your .env file.")
    return OpenAI(api_key=api_key)


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
    Embed a text string using OpenAI's `text-embedding-3-small` model.

    Returns the embedding vector as a list of floats.
    """
    text = (text or "").strip()
    if not text:
        return []

    client = _get_openai_client()
    resp = client.embeddings.create(model="text-embedding-3-small", input=text)
    return list(resp.data[0].embedding)


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

