"""Offline, immutable local-model input identity. Does not load or download it."""

import hashlib
import json
from dataclasses import replace
from pathlib import Path
from typing import Any

from app.embeddings.config import EmbeddingConfiguration
from app.embeddings.local import SentenceTransformerModel, load_sentence_transformer
from app.embeddings.types import ResolvedEmbeddingDevice

ROOT = Path(__file__).resolve().parent


def model_identity(cache: str) -> dict[str, Any]:
    base = Path(cache)
    if not base.is_absolute() or base.resolve() != base or not base.is_dir():
        raise ValueError("explicit canonical model cache required")
    lock = json.loads((ROOT / "model-lock.json").read_text())
    snapshot = base / "models--sentence-transformers--all-MiniLM-L6-v2" / "snapshots"
    snapshot = snapshot / lock["revision"]
    actual_files = {p.relative_to(snapshot).as_posix() for p in snapshot.rglob("*") if p.is_file()}
    if actual_files != set(lock["sha256"]):
        raise ValueError("model snapshot inventory differs from locked artifacts")
    size = 0
    for name, expected in lock["sha256"].items():
        path = snapshot / name
        if not path.resolve().is_relative_to(base) or not path.is_file():
            raise ValueError("missing or escaped model artifact")
        size += path.stat().st_size
        if size > 2 * 1024**3:
            raise ValueError("model artifact budget exceeded")
        with path.open("rb") as artifact:
            actual = hashlib.file_digest(artifact, "sha256").hexdigest()
        if actual != expected:
            raise ValueError("model artifact fingerprint mismatch")
    return {
        "name": lock["name"],
        "revision": lock["revision"],
        "sha256": lock["sha256"],
        "bytes": size,
        "cache": str(base),
        "snapshot": str(snapshot),
        "downloads": False,
    }


def load_pinned_model(
    configuration: EmbeddingConfiguration, device: ResolvedEmbeddingDevice, snapshot: str
) -> SentenceTransformerModel:
    """Use the hashed snapshot, never a cache's mutable refs/main alias."""
    return load_sentence_transformer(replace(configuration, model=snapshot), device)
