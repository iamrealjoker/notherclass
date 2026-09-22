"""
brain/store.py — MEMORIA del agente (su mente, persistente y portable).

Modelo de memoria infinita sobre SQLite + vectores, sin servidor de base de datos.

- Episódica : `chunks` (texto + vector + metadatos + ts). Cada cosa vivida.
- Híbrida   : recuperación = palabra clave (BM25-ish) + vector (coseno),
              fusionadas con RRF.
- Creencias : `beliefs` (clave→valor con confidence). Lo que el agente "cree".
- Lecciones : `lessons` (tipo win|fail). Aprende de fallos y victorias.
- Curar     : `recall()` con presupuesto de tokens → memoria infinita, contexto
              finito (el principio de tu ahorro de ~90% de tokens).

El esquema es SQLite puro: no se requiere ningún servicio externo para arrancar.
"""
import json
import os
import re
import sqlite3
import time
import uuid
from typing import Dict, List, Optional

from . import embeddings

_WORD_RE = re.compile(r"[a-z0-9áéíóúñü]+", re.IGNORECASE)
RRF_K = 60.0


def _now():
    return time.time()


def _token_count(text: str) -> int:
    return max(1, len(text) // 4)


# ── Higiene de creencias ────────────────────────────────────────────────────
# Claves equivalentes (sinónimos / idioma) → MISMA "clave canónica", para FUSIONAR
# duplicados (p. ej. project.root == proyecto.ubicacion == notherclass.workspace_path).
_TOKEN_SYN = {
    "project": "proyecto", "notherclass": "proyecto",
    "root": "ruta", "path": "ruta", "ubicacion": "ruta", "ubicación": "ruta",
    "workspace_path": "ruta", "workspace": "ruta", "ruta": "ruta",
    "name": "nombre",
}


def _canon_key(key: str) -> str:
    toks = [t for t in re.split(r"[.\s/_-]+", (key or "").strip().lower()) if t]
    toks = [_TOKEN_SYN.get(t, t) for t in toks]
    out = []
    for t in toks:                       # colapsar repetidos consecutivos
        if not out or out[-1] != t:
            out.append(t)
    if "ruta" in out:                    # todas las "ruta del proyecto" -> una sola
        return "workspace.ruta"
    return ".".join(out)


def _ruta_obsoleta(value) -> bool:
    """True si el valor ES una ruta absoluta (bare) que ya NO existe en disco."""
    v = str(value or "").strip()
    if not v.startswith("/"):
        return False
    p = re.split(r"[\s,;)]", v)[0]
    return not os.path.exists(p)


class Brain:
    """Memoria persistente de un agente, por defecto en MEMORY/brain.sqlite3."""

    def __init__(self, memory_dir: str, agent_id: str = "default"):
        self.memory_dir = memory_dir
        self.agent_id = agent_id
        os.makedirs(memory_dir, exist_ok=True)
        self.db_path = os.path.join(memory_dir, f"brain_{agent_id}.sqlite3")
        # check_same_thread=False: el MISMO agente atiende ahora DOS entradas
        # (Telegram y la API local de la web) y cada una en su hilo. Los turnos y
        # la consolidación van serializados con `_TURNO_LOCK` en run.py, así que
        # no hay acceso simultáneo; timeout=30 evita "database is locked".
        self._conn = sqlite3.connect(self.db_path, check_same_thread=False,
                                     timeout=30)
        self._conn.row_factory = sqlite3.Row
        self._init_schema()

    def _init_schema(self):
        c = self._conn.cursor()
        c.execute("CREATE TABLE IF NOT EXISTS chunks ("
                  "id TEXT PRIMARY KEY, agent TEXT, kind TEXT, text TEXT, "
                  "embedding TEXT, meta TEXT, ts REAL, access_ts REAL)")
        c.execute("CREATE TABLE IF NOT EXISTS beliefs ("
                  "agent TEXT, key TEXT, value TEXT, confidence REAL, "
                  "source TEXT, updated REAL, PRIMARY KEY (agent, key))")
        c.execute("CREATE TABLE IF NOT EXISTS lessons ("
                  "id TEXT PRIMARY KEY, agent TEXT, kind TEXT, title TEXT, "
                  "body TEXT, outcome TEXT, ts REAL)")
        c.execute("CREATE TABLE IF NOT EXISTS meta ("
                  "agent TEXT, key TEXT, value TEXT, updated REAL, "
                  "PRIMARY KEY (agent, key))")
        c.execute("CREATE INDEX IF NOT EXISTS ix_chunks_agent ON chunks(agent)")
        c.execute("CREATE INDEX IF NOT EXISTS ix_lessons_agent ON lessons(agent)")
        # migración: columna processed (bruto sin consolidar = 0, procesado = 1)
        try:
            c.execute("ALTER TABLE chunks ADD COLUMN processed INTEGER DEFAULT 0")
        except Exception:
            pass  # ya existe
        # migración: columna ckey (clave canónica) + FUSIÓN de creencias duplicadas
        try:
            c.execute("ALTER TABLE beliefs ADD COLUMN ckey TEXT")
        except Exception:
            pass  # ya existe
        self._conn.commit()
        self._fusionar_creencias()

    def _fusionar_creencias(self):
        """Rellena ckey y fusiona creencias equivalentes (una sola por concepto).
        Elige la mejor: la que NO es una ruta obsoleta y, a igualdad, la de mayor
        confianza. Idempotente: tras la 1ª vez no quedan filas sin ckey."""
        pend = self._conn.execute(
            "SELECT rowid AS rid, key, value, confidence FROM beliefs "
            "WHERE agent=? AND (ckey IS NULL OR ckey='')",
            (self.agent_id,)).fetchall()
        for r in pend:
            ck = _canon_key(r["key"])
            prev = self._conn.execute(
                "SELECT rowid AS rid, value, confidence FROM beliefs "
                "WHERE agent=? AND ckey=?", (self.agent_id, ck)).fetchall()
            cands = list(prev) + [r]

            def _score(x):
                return (0 if _ruta_obsoleta(x["value"]) else 1, x["confidence"] or 0.0)

            mejor = max(cands, key=_score)
            for x in cands:
                if x["rid"] != mejor["rid"]:
                    self._conn.execute("DELETE FROM beliefs WHERE rowid=?", (x["rid"],))
            self._conn.execute("UPDATE beliefs SET ckey=? WHERE rowid=?", (ck, mejor["rid"]))
        self._conn.commit()

    # ── meta (estado interno, p.ej. marca de consolidación) ─────────────────
    def meta_get(self, key: str, default: str = "") -> str:
        row = self._conn.execute(
            "SELECT value FROM meta WHERE agent=? AND key=?",
            (self.agent_id, key)).fetchone()
        return row["value"] if row else default

    def meta_set(self, key: str, value: str):
        self._conn.execute(
            "INSERT INTO meta (agent, key, value, updated) VALUES (?,?,?,?) "
            "ON CONFLICT(agent, key) DO UPDATE SET value=excluded.value, "
            "updated=excluded.updated",
            (self.agent_id, key, value, _now()))
        self._conn.commit()

    # ── procesado / consolidación (bruto → neto) ───────────────────────────
    def unprocessed_chunks(self, limit: int = 200, max_chars: int = 16000) -> List[dict]:
        """Chunks "brutos" sin consolidar, por si el LLM puede destilarlos."""
        rows = self._conn.execute(
            "SELECT * FROM chunks WHERE agent=? AND (processed IS NULL OR processed=0) "
            "ORDER BY ts ASC LIMIT ?", (self.agent_id, limit)).fetchall()
        out = []
        total = 0
        for r in rows:
            txt = r["text"] or ""
            if total + len(txt) > max_chars:
                break
            out.append(dict(r))
            total += len(txt)
        return out

    def mark_processed(self, ids):
        if not ids:
            return
        self._conn.executemany(
            "UPDATE chunks SET processed=1 WHERE agent=? AND id=?",
            [(self.agent_id, cid) for cid in ids])
        self._conn.commit()

    def add_summary_chunk(self, text: str, meta: Optional[dict] = None) -> str:
        """Resumen 'neto' de una consolidación: se guarda y queda procesado (no se repite)."""
        cid = str(uuid.uuid4())
        vec = embeddings.embed(text)
        ts = _now()
        self._conn.execute(
            "INSERT INTO chunks (id, agent, kind, text, embedding, meta, ts, access_ts, processed) "
            "VALUES (?,?,?,?,?,?,?,?,1)",
            (cid, self.agent_id, "summary", text, json.dumps(vec),
             json.dumps(meta or {}), ts, ts))
        self._conn.commit()
        return cid


    def _kw_tokens(self, text: str) -> set:
        return set(t.lower() for t in _WORD_RE.findall(text))

    # ── episódica ──────────────────────────────────────────────────────────
    def remember(self, text: str, kind: str = "event", meta: Optional[dict] = None) -> str:
        """Guarda un evento como chunk con vector (memoria episódica)."""
        cid = str(uuid.uuid4())
        vec = embeddings.embed(text)
        ts = _now()
        self._conn.execute(
            "INSERT INTO chunks (id, agent, kind, text, embedding, meta, ts, access_ts) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (cid, self.agent_id, kind, text, json.dumps(vec), json.dumps(meta or {}), ts, ts))
        self._conn.commit()
        return cid

    # ── declarativa (creencias) ────────────────────────────────────────────
    def set_belief(self, key: str, value: str, confidence: float = 0.7, source: str = ""):
        """Guarda/actualiza una creencia FUSIONANDO por clave canónica: escribir
        'project.root' cuando ya existe 'proyecto.ubicacion' ACTUALIZA la misma
        (una sola por concepto) en vez de crear un duplicado."""
        ck = _canon_key(key)
        row = self._conn.execute(
            "SELECT rowid AS rid FROM beliefs WHERE agent=? AND ckey=?",
            (self.agent_id, ck)).fetchone()
        if row:
            self._conn.execute(
                "UPDATE beliefs SET key=?, value=?, confidence=?, source=?, updated=? "
                "WHERE rowid=?",
                (key, value, confidence, source, _now(), row["rid"]))
        else:
            self._conn.execute(
                "INSERT INTO beliefs (agent, key, ckey, value, confidence, source, updated) "
                "VALUES (?,?,?,?,?,?,?)",
                (self.agent_id, key, ck, value, confidence, source, _now()))
        self._conn.commit()

    def get_belief(self, key: str) -> Optional[dict]:
        row = self._conn.execute(
            "SELECT * FROM beliefs WHERE agent=? AND key=?",
            (self.agent_id, key)).fetchone()
        return dict(row) if row else None

    def beliefs(self, limit: int = 50) -> List[dict]:
        rows = self._conn.execute(
            "SELECT * FROM beliefs WHERE agent=? ORDER BY confidence DESC LIMIT ?",
            (self.agent_id, limit * 3)).fetchall()
        out = []
        for r in rows:
            if _ruta_obsoleta(r["value"]):     # higiene: no inyectar rutas muertas
                continue
            out.append(dict(r))
            if len(out) >= limit:
                break
        return out

    # ── lecciones (fallos y victorias) ────────────────────────────────────
    def learn(self, kind: str, title: str, body: str, outcome: str = ""):
        """Registra una lección. kind ∈ {'win','fail'}."""
        assert kind in ("win", "fail")
        lid = str(uuid.uuid4())
        self._conn.execute(
            "INSERT INTO lessons (id, agent, kind, title, body, outcome, ts) "
            "VALUES (?,?,?,?,?,?,?)",
            (lid, self.agent_id, kind, title, body, outcome, _now()))
        self._conn.commit()
        return lid

    def lessons(self, kind: Optional[str] = None, limit: int = 40) -> List[dict]:
        q = "SELECT * FROM lessons WHERE agent=?"
        args: list = [self.agent_id]
        if kind:
            q += " AND kind=?"
            args.append(kind)
        q += " ORDER BY ts DESC LIMIT ?"
        args.append(limit)
        rows = self._conn.execute(q, args).fetchall()
        return [dict(r) for r in rows]

    # ── búsqueda híbrida (BM25-ish + vector + recencia, fusionado RRF) ────
    def _bm25_rank(self, query_tokens: set, rows) -> Dict[str, int]:
        scored = []
        for r in rows:
            toks = self._kw_tokens(r["text"] or "")
            overlap = len(query_tokens & toks)
            if overlap:
                scored.append((overlap, r["id"]))
        scored.sort(key=lambda x: -x[0])
        return {cid: i for i, (_, cid) in enumerate(scored)}

    def _vector_rank(self, qvec, rows) -> Dict[str, int]:
        scored = []
        for r in rows:
            try:
                v = json.loads(r["embedding"])
            except Exception:
                continue
            scored.append((embeddings.cosine(qvec, v), r["id"]))
        scored.sort(key=lambda x: -x[0])
        return {cid: i for i, (_, cid) in enumerate(scored)}

    def recall(self, query: str, budget_tokens: int = 2800,
               top_k: int = 20, include_recent: int = 6,
               max_items: int = None, min_ratio: float = None) -> Dict[str, list]:
        """Recuperación híbrida con RRF + SELECCIÓN ESTRICTA.

        La memoria guarda TODO (infinita); aquí elegimos SOLO lo relevante:
          - `max_items`: nº MÁXIMO de chunks episódicos a inyectar (los mejores).
          - `min_ratio`: descarta los que puntúan < ratio × mejor (evita ruido).
          - `budget_tokens`: tope duro adicional.
        Así no se inyectan 79 chunks por turno: se inyectan ~8 buenos.
        """
        if max_items is None:
            try:
                max_items = int(os.environ.get("TW_RECALL_MAX_ITEMS", "8"))
            except (TypeError, ValueError):
                max_items = 8
        if min_ratio is None:
            try:
                min_ratio = float(os.environ.get("TW_RECALL_MIN_RATIO", "0.25"))
            except (TypeError, ValueError):
                min_ratio = 0.25

        rows = self._conn.execute(
            "SELECT * FROM chunks WHERE agent=? ORDER BY ts DESC LIMIT 4000",
            (self.agent_id,)).fetchall() or []

        # working memory: lo más reciente SIEMPRE entra (prioridad de contexto)
        recent = [dict(r) for r in rows[:include_recent]]

        query_tokens = self._kw_tokens(query)
        qvec = embeddings.embed(query)

        bm25 = self._bm25_rank(query_tokens, rows)
        vrank = self._vector_rank(qvec, rows)

        acc: Dict[str, float] = {}
        for cid, pos in bm25.items():
            acc[cid] = acc.get(cid, 0.0) + 1.0 / (RRF_K + pos + 1)
        for cid, pos in vrank.items():
            acc[cid] = acc.get(cid, 0.0) + 1.0 / (RRF_K + pos + 1)

        recent_ids = {r["id"] for r in recent}
        orden = sorted(acc.keys(), key=lambda cid: acc[cid], reverse=True)
        best = acc[orden[0]] if orden else 0.0
        corte = best * min_ratio

        episodic: List[dict] = []
        used_tokens = 0
        vistos = set()
        for cid in orden:
            if cid in recent_ids:
                continue
            if max_items and len(episodic) >= max_items:
                break                      # tope de N items (lo más relevante)
            if best > 0 and acc[cid] < corte:
                break                      # por debajo del umbral → ruido, paramos
            row = self._conn.execute("SELECT * FROM chunks WHERE id=?", (cid,)).fetchone()
            if not row:
                continue
            clave = (row["text"] or "")[:80].strip().lower()
            if clave in vistos:            # dedupe (chunks casi idénticos)
                continue
            toks = _token_count(row["text"])
            if used_tokens + toks > budget_tokens:
                break
            vistos.add(clave)
            episodic.append(dict(row))
            used_tokens += toks
            self._conn.execute("UPDATE chunks SET access_ts=? WHERE id=?",
                               (_now(), cid))

        self._conn.commit()
        return {"working": recent, "episodic": episodic, "beliefs": self.beliefs(limit=30)}

    def close(self):
        try:
            self._conn.close()
        except Exception:
            pass

