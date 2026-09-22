"""
brain/embeddings.py — embeddings para la memoria vectorial.

Estrategia (graceful fallback, como en todo el proyecto):
1. Si `sentence-transformers` está instalado  → all-MiniLM-L6-v2 (384-d, semántico real).
2. Si no → embedder local SIN dependencias (bolsa de tokens + bigramas hasheados,
   normalizado) para que la memoria funcione desde el primer segundo sin instalar nada.

El fallback es "keyword-semántico": suficiente para que el motor híbrido ya
recuerde; cuando se instale sentence-transformers, la calidad sube sin tocar nada.
"""
import math
import re
from typing import List, Optional

_WORD_RE = re.compile(r"[a-z0-9áéíóúñü]+", re.IGNORECASE)
_FALLBACK_DIM = 256

_sbert = None
_sbert_ok = False
_try_sbert = True


def _load_sbert():
    """Intenta cargar sentence-transformers (una sola vez)."""
    global _sbert, _sbert_ok, _try_sbert
    if not _try_sbert:
        return
    _try_sbert = False
    try:
        from sentence_transformers import SentenceTransformer
        _sbert = SentenceTransformer("all-MiniLM-L6-v2")
        _sbert_ok = True
    except Exception:
        _sbert = None
        _sbert_ok = False


def using_real_embeddings() -> bool:
    _load_sbert()
    return _sbert_ok


def _tokenize(text: str) -> List[str]:
    return _WORD_RE.findall(text.lower())


def _hash_token(tok: str) -> int:
    # djb2-like, estable entre procesos
    h = 5381
    for ch in tok:
        h = ((h << 5) + h) + ord(ch)
        h &= 0xFFFFFFFF
    return h


def _fallback_embed(text: str) -> List[float]:
    """Embedding local determinista: unigramas + bigramas hasheados con signo."""
    vec = [0.0] * _FALLBACK_DIM
    toks = _tokenize(text)
    if not toks:
        return vec
    grams = list(toks) + [toks[i] + " " + toks[i + 1] for i in range(len(toks) - 1)]
    counts: dict = {}
    for g in grams:
        counts[g] = counts.get(g, 0) + 1
    for g, c in counts.items():
        h = _hash_token(g)
        idx = h % _FALLBACK_DIM
        sign = 1.0 if (h & 1) else -1.0
        vec[idx] += sign * (1.0 + math.log(c))
    return _normalize(vec)


def embed(text: str) -> List[float]:
    """Devuelve el vector normalizado del texto (siempre, con o sin dependencias)."""
    if not text:
        text = " "
    _load_sbert()
    if _sbert_ok and _sbert is not None:
        try:
            v = _sbert.encode(text, normalize_embeddings=True)
            return [float(x) for x in v]
        except Exception:
            pass
    return _fallback_embed(text)


def embed_many(texts: List[str]) -> List[List[float]]:
    return [embed(t) for t in texts]


def _normalize(vec: List[float]) -> List[float]:
    n = math.sqrt(sum(x * x for x in vec))
    if n == 0:
        return vec
    return [x / n for x in vec]


def cosine(a: List[float], b: List[float]) -> float:
    """Coseno entre dos vectores (siempre listas de igual largo; a lo sumo distinto → 0)."""
    if not a or not b or len(a) != len(b):
        return 0.0
    # NumPy opcional; si no está, Python puro.
    try:
        import numpy as np
        xa = np.asarray(a, dtype="float64")
        xb = np.asarray(b, dtype="float64")
        na = float(np.linalg.norm(xa))
        nb = float(np.linalg.norm(xb))
        if na == 0 or nb == 0:
            return 0.0
        return float(np.dot(xa, xb) / (na * nb))
    except Exception:
        dot = sum(x * y for x, y in zip(a, b))
        na = math.sqrt(sum(x * x for x in a))
        nb = math.sqrt(sum(y * y for y in b))
        if na == 0 or nb == 0:
            return 0.0
        return dot / (na * nb)
