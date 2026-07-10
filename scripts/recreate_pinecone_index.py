"""
(Re)create the Pinecone index so its dimension matches the local embedding model.

We switched embeddings from OpenAI's 1536-dim model to a local fastembed model
(default 384 dims). Pinecone index dimensions are fixed at creation, so moving
models means recreating the index. Job and memory vectors are rebuilt on the
next scrape / agent run, so dropping the old (now-mismatched) vectors is safe.

Usage:
  python -m scripts.recreate_pinecone_index          # recreate only if dim differs
  python -m scripts.recreate_pinecone_index --force   # always drop and recreate
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from pinecone import Pinecone, ServerlessSpec

from app.config import settings

# Fallbacks if we cannot read the existing index's placement.
DEFAULT_CLOUD = "aws"
DEFAULT_REGION = "us-east-1"
DEFAULT_METRIC = "cosine"


def _existing_index(pc: Pinecone, name: str):
    try:
        return pc.describe_index(name)
    except Exception:
        return None


def _placement(desc) -> tuple[str, str]:
    """Read cloud/region from an existing index so we recreate it in place."""
    try:
        serverless = desc.spec["serverless"]
        return serverless.get("cloud", DEFAULT_CLOUD), serverless.get("region", DEFAULT_REGION)
    except Exception:
        return DEFAULT_CLOUD, DEFAULT_REGION


def _wait_until_ready(pc: Pinecone, name: str, *, timeout_sec: int = 120) -> None:
    deadline = time.time() + timeout_sec
    while time.time() < deadline:
        try:
            if pc.describe_index(name).status["ready"]:
                return
        except Exception:
            pass
        time.sleep(2)
    print(f"Warning: index '{name}' not reported ready within {timeout_sec}s.")


def main(force: bool = False) -> None:
    if not settings.pinecone_api_key:
        raise RuntimeError("PINECONE_API_KEY is missing. Set it in your .env file.")
    name = settings.pinecone_index
    if not name:
        raise RuntimeError("PINECONE_INDEX is missing. Set it in your .env file.")

    target_dim = int(settings.embedding_dim)
    pc = Pinecone(api_key=settings.pinecone_api_key)

    desc = _existing_index(pc, name)
    if desc is not None:
        current_dim = int(desc.dimension)
        cloud, region = _placement(desc)
        if current_dim == target_dim and not force:
            print(f"Index '{name}' already at dimension {target_dim} — nothing to do.")
            return
        print(f"Deleting index '{name}' (dim {current_dim} -> {target_dim}) ...")
        pc.delete_index(name)
        # Deletion is async; wait for it to disappear before recreating.
        for _ in range(60):
            if _existing_index(pc, name) is None:
                break
            time.sleep(2)
    else:
        cloud, region = DEFAULT_CLOUD, DEFAULT_REGION
        print(f"Index '{name}' does not exist — creating it.")

    print(f"Creating index '{name}' (dim={target_dim}, metric={DEFAULT_METRIC}, {cloud}/{region}) ...")
    pc.create_index(
        name=name,
        dimension=target_dim,
        metric=DEFAULT_METRIC,
        spec=ServerlessSpec(cloud=cloud, region=region),
    )
    _wait_until_ready(pc, name)
    print(f"Done. Index '{name}' is ready at dimension {target_dim}.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Recreate the Pinecone index at the embedding dimension.")
    parser.add_argument("--force", action="store_true", help="Drop and recreate even if the dimension already matches.")
    args = parser.parse_args()
    main(force=bool(args.force))
