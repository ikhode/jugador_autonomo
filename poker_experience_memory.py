"""
Módulo de Memoria de Experiencia y Aprendizaje de Errores Tácticos (RAG para Póker Bot).

Características de producción:
1. Migraciones idempotentes de esquema e índice sobre `similarity_key`.
2. Normalización semántica de claves (`hand_tier|board_texture|opp_archetype`) evitando colisiones.
3. Deduplicación con refuerzo (`occurrences`, `total_loss_bb`, `ev_lost_bb`, `severity_score`).
4. Guardia anti-varianza: distingue errores reales de EV (`ev_diff < -0.5` o mala calidad táctica)
   de pérdidas por pura varianza en jugadas `+EV` / `OPTIMA`.
5. Recuperación rankeada por severidad, decaimiento temporal y coincidencia de calle/posición.
"""
import atexit
from contextlib import closing
from datetime import datetime, timezone
import math
from pathlib import Path
import threading
from typing import Any, Dict, List, Optional

from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import NullPool

from db_models import Base, ErrorExperiencia

PROJECT_ROOT = Path(__file__).resolve().parent
DB_MEMORY_PATH = PROJECT_ROOT / "logs" / "poker_stats.db"

_EXTRA_COLUMNS = (
    ("street", "TEXT DEFAULT 'PREFLOP'"),
    ("position", "TEXT DEFAULT 'DESCONOCIDA'"),
    ("occurrences", "INTEGER NOT NULL DEFAULT 1"),
    ("total_loss_bb", "REAL NOT NULL DEFAULT 0.0"),
    ("ev_lost_bb", "REAL NOT NULL DEFAULT 0.0"),
    ("severity_score", "REAL NOT NULL DEFAULT 1.0"),
    ("confidence", "REAL NOT NULL DEFAULT 1.0"),
    ("decision_quality", "TEXT DEFAULT ''"),
    ("last_seen_at", "TEXT DEFAULT ''"),
)


def _utc_now_str() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def _configure_sqlite_memory(target_engine) -> None:
    @event.listens_for(target_engine, "connect")
    def _set_pragmas(dbapi_conn, _conn_record):
        cur = dbapi_conn.cursor()
        try:
            cur.execute("PRAGMA journal_mode=WAL")
            cur.execute("PRAGMA busy_timeout=10000")
            cur.execute("PRAGMA synchronous=NORMAL")
        finally:
            cur.close()


def _ensure_schema_migrations(target_engine) -> None:
    """Crea tablas faltantes y migra columnas/índices nuevos de forma idempotente."""
    Base.metadata.create_all(target_engine)
    with target_engine.begin() as conn:
        cols = {row[1] for row in conn.execute(text("PRAGMA table_info(errores_experiencia)")).fetchall()}
        for col_name, col_def in _EXTRA_COLUMNS:
            if col_name not in cols:
                conn.execute(text(f"ALTER TABLE errores_experiencia ADD COLUMN {col_name} {col_def}"))
        conn.execute(text("CREATE INDEX IF NOT EXISTS idx_err_sim_key ON errores_experiencia(similarity_key)"))
        conn.execute(text("CREATE INDEX IF NOT EXISTS idx_err_street ON errores_experiencia(street)"))


engine = create_engine(
    f"sqlite:///{DB_MEMORY_PATH}",
    connect_args={"check_same_thread": False, "timeout": 30.0},
    poolclass=NullPool,
)
atexit.register(engine.dispose)
_configure_sqlite_memory(engine)
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)


def inicializar_db_memoria(target_engine=None):
    eng = target_engine or engine
    DB_MEMORY_PATH.parent.mkdir(parents=True, exist_ok=True)
    _ensure_schema_migrations(eng)
    if ":memory:" not in str(getattr(eng, "url", "")):
        eng.dispose()


inicializar_db_memoria()


def _normalizar_textura_token(board_texture: str) -> str:
    raw = (board_texture or "DESCONOCIDA").strip().upper()
    if not raw or raw == "DESCONOCIDA":
        return "DESCONOCIDA"
    if any(k in raw for k in ("CONECTADA", "COORDINADA", "PELIGROSA", "MONOTONA", "MONÓTONA")):
        return "COORDINADA"
    if any(k in raw for k in ("EMPAREJADA", "DOBLADA", "PAIRED")):
        return "EMPAREJADA"
    if any(k in raw for k in ("SECA", "DISPERSA", "DRY")):
        return "SECA"
    tokens = [t for t in raw.replace("/", " ").split() if t and t != "MESA"]
    return tokens[0] if tokens else "DESCONOCIDA"


def _construir_similarity_key(hand_tier: str, board_texture: str, opp_archetype: str) -> str:
    tier_tok = (hand_tier or "DESCONOCIDO").strip().upper()
    if "(" in tier_tok:
        tier_tok = tier_tok.split("(")[0].strip() or tier_tok
    board_tok = _normalizar_textura_token(board_texture)
    opp_raw = (opp_archetype or "DESCONOCIDO").strip().upper()
    if not opp_raw or any(k in opp_raw for k in ("SIN DATOS", "SIN HISTORIAL", "DESCONOCIDO", "DESCONOCIDA", "SIN")):
        opp_tok = "DESCONOCIDO"
    else:
        opp_tok = opp_raw.split()[0] if opp_raw else "DESCONOCIDO"
    return f"{tier_tok}|{board_tok}|{opp_tok}"


def _normalizar_street_grupo(street_str: str) -> str:
    s = (street_str or "PREFLOP").strip().upper()
    return "POSTFLOP" if s in ("FLOP", "TURN", "RIVER", "POSTFLOP") else "PREFLOP"


def _calcular_recency_decay(last_seen_at: Optional[str], half_life_days: float = 14.0) -> float:
    if not last_seen_at:
        return 0.85
    try:
        dt = datetime.strptime(last_seen_at[:19], "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
        age_days = max(0.0, (datetime.now(timezone.utc) - dt).total_seconds() / 86400.0)
        return math.exp(-math.log(2.0) * age_days / max(1.0, half_life_days))
    except Exception:
        return 0.85


def _calcular_severity_score(occurrences: int, total_loss_bb: float, ev_lost_bb: float, confidence: float) -> float:
    base = max(0.5, ev_lost_bb * 0.8 + (total_loss_bb / max(1, occurrences)) * 0.25)
    freq_boost = 1.0 + 0.35 * math.log1p(max(0, occurrences - 1))
    return round(base * freq_boost * max(0.25, min(1.0, confidence)), 4)


class PokerExperienceMemory:
    def __init__(self, db_path: Optional[Path] = None):
        self._lock = threading.RLock()
        if db_path is not None:
            db_path = Path(db_path).resolve()
            db_path.parent.mkdir(parents=True, exist_ok=True)
            self._engine = create_engine(
                f"sqlite:///{db_path}",
                connect_args={"check_same_thread": False, "timeout": 30.0},
                poolclass=NullPool,
            )
            _configure_sqlite_memory(self._engine)
            _ensure_schema_migrations(self._engine)
            self._session_factory = sessionmaker(bind=self._engine, expire_on_commit=False)
        else:
            inicializar_db_memoria()
            self._engine = engine
            self._session_factory = SessionLocal

    def close(self):
        if hasattr(self, "_engine") and self._engine:
            try:
                from sqlalchemy.orm import close_all_sessions
                close_all_sessions()
                self._engine.dispose()
            except Exception:
                pass

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()

    def registrar_error_pasado(
        self,
        hand_tier: str,
        board_texture: str,
        opp_archetype: str,
        action_taken: str,
        loss_amount: int,
        lesson_learned: str,
        street: str = "PREFLOP",
        position: str = "DESCONOCIDA",
        big_blind: float = 20.0,
        ev_lost_bb: float = 0.0,
        confidence: float = 1.0,
        decision_quality: str = "",
    ) -> Dict[str, Any]:
        similarity_key = _construir_similarity_key(hand_tier, board_texture, opp_archetype)
        street_norm = (street or "PREFLOP").strip().upper()
        street_grp = _normalizar_street_grupo(street_norm)
        pos_norm = (position or "DESCONOCIDA").strip().upper()
        action_norm = (action_taken or "").strip().upper()
        bb = max(1.0, float(big_blind or 20.0))
        loss_chips = abs(int(loss_amount or 0))
        loss_bb = round(loss_chips / bb, 3)
        ev_lost = abs(float(ev_lost_bb or 0.0))
        now_str = _utc_now_str()

        with self._lock:
            with closing(self._session_factory()) as session:
                candidates = (
                    session.query(ErrorExperiencia)
                    .filter(ErrorExperiencia.similarity_key == similarity_key)
                    .all()
                )
                existente = None
                for c in candidates:
                    if _normalizar_street_grupo(c.street or "") == street_grp:
                        c_act = (c.action_taken or "").upper()
                        if c_act == action_norm or (action_norm in ("BET", "RAISE") and c_act in ("BET", "RAISE")):
                            existente = c
                            break

                if existente is not None:
                    existente.occurrences = int(existente.occurrences or 1) + 1
                    existente.total_loss_bb = round(float(existente.total_loss_bb or 0.0) + loss_bb, 3)
                    existente.ev_lost_bb = round(float(existente.ev_lost_bb or 0.0) + ev_lost, 3)
                    existente.loss_amount = max(int(existente.loss_amount or 0), loss_chips)
                    existente.confidence = max(float(existente.confidence or 0.5), float(confidence))
                    if decision_quality:
                        existente.decision_quality = decision_quality
                    if pos_norm != "DESCONOCIDA":
                        existente.position = pos_norm
                    existente.last_seen_at = now_str
                    existente.severity_score = _calcular_severity_score(
                        existente.occurrences,
                        existente.total_loss_bb,
                        existente.ev_lost_bb,
                        existente.confidence,
                    )
                    if lesson_learned:
                        existente.lesson_learned = lesson_learned
                    session.commit()
                    return {"status": "UPDATED", "similarity_key": similarity_key, "occurrences": existente.occurrences}

                sev = _calcular_severity_score(1, loss_bb, ev_lost, confidence)
                nuevo_error = ErrorExperiencia(
                    timestamp=now_str,
                    hand_tier=hand_tier,
                    board_texture=board_texture,
                    opp_archetype=opp_archetype,
                    street=street_norm,
                    position=pos_norm,
                    action_taken=action_norm,
                    loss_amount=loss_chips,
                    lesson_learned=lesson_learned,
                    similarity_key=similarity_key,
                    occurrences=1,
                    total_loss_bb=loss_bb,
                    ev_lost_bb=ev_lost,
                    severity_score=sev,
                    confidence=float(confidence),
                    decision_quality=decision_quality or "",
                    last_seen_at=now_str,
                )
                session.add(nuevo_error)
                session.commit()
                return {"status": "INSERTED", "similarity_key": similarity_key, "occurrences": 1}

    def auto_evaluar_y_registrar_mano_perdedora(
        self,
        hand_tier: str,
        board_texture: str,
        opp_archetype: str,
        accion_tomada: str,
        net_chips: float,
        ev_diff: float = 0.0,
        street: str = "PREFLOP",
        position: str = "DESCONOCIDA",
        big_blind: float = 20.0,
        decision_quality: str = "",
    ) -> Optional[Dict[str, Any]]:
        """
        Evalúa retrospectivamente la mano evitando contaminar la memoria con varianza:
        - Si hubo evaluación EV y fue positiva/óptima (ev_diff >= -0.5 y calidad OPTIMA/ACEPTABLE),
          NO se registra como error aunque se haya perdido el bote.
        - Si hubo error estratégico real (ev_diff < -0.5 o calidad INACCURACY/MISTAKE/BLUNDER_EV),
          o pérdida sin evaluación EV disponible (confianza reducida 0.6), registra/refuerza la lección.
        """
        qual = (decision_quality or "").strip().upper()
        tiene_error_ev = float(ev_diff or 0.0) < -0.5 or qual in ("INACCURACY", "MISTAKE", "BLUNDER_EV")
        es_buena_jugada_varianza = (
            (qual in ("OPTIMA", "ACEPTABLE") and float(ev_diff or 0.0) >= -0.5)
            or (float(ev_diff or 0.0) > 0.0 and qual not in ("INACCURACY", "MISTAKE", "BLUNDER_EV"))
        )
        if es_buena_jugada_varianza:
            return None
        if not tiene_error_ev and float(net_chips or 0.0) >= 0.0:
            return None

        confianza = 1.0 if tiene_error_ev else 0.6
        perdidaf = abs(int(round(float(net_chips or 0.0))))
        ev_lost = abs(min(0.0, float(ev_diff or 0.0)))
        leccion = (
            f"En [{hand_tier}] ({street}) con textura {board_texture} vs {opp_archetype}, "
            f"'{accion_tomada}' resultó en pérdida de {perdidaf} fichas (EV Δ: {float(ev_diff or 0.0):.1f} BB). "
            f"INSTRUCCIÓN ANTI-REPETICIÓN: Revisar/evitar '{accion_tomada}' en condiciones equivalentes."
        )

        return self.registrar_error_pasado(
            hand_tier=hand_tier,
            board_texture=board_texture,
            opp_archetype=opp_archetype,
            action_taken=accion_tomada,
            loss_amount=perdidaf,
            lesson_learned=leccion,
            street=street,
            position=position,
            big_blind=big_blind,
            ev_lost_bb=ev_lost,
            confidence=confianza,
            decision_quality=qual,
        )

    def buscar_errores_similares(
        self,
        hand_tier: str,
        board_texture: str,
        opp_archetype: str,
        limit: int = 2,
        street: str = "",
        position: str = "",
    ) -> List[Dict[str, Any]]:
        similarity_key = _construir_similarity_key(hand_tier, board_texture, opp_archetype)
        street_norm = (street or "").strip().upper()
        pos_norm = (position or "").strip().upper()

        with self._lock:
            with closing(self._session_factory()) as session:
                candidates = (
                    session.query(ErrorExperiencia)
                    .filter(
                        (ErrorExperiencia.similarity_key == similarity_key)
                        | (
                            (ErrorExperiencia.hand_tier == hand_tier)
                            & (ErrorExperiencia.board_texture == board_texture)
                            & (ErrorExperiencia.opp_archetype == opp_archetype)
                        )
                    )
                    .all()
                )

                scored = []
                for r in candidates:
                    sev = float(r.severity_score or 1.0)
                    decay = _calcular_recency_decay(r.last_seen_at or r.timestamp)
                    street_boost = 1.25 if (street_norm and (r.street or "").upper() == street_norm) else 1.0
                    pos_boost = 1.15 if (pos_norm and pos_norm != "DESCONOCIDA" and (r.position or "").upper() == pos_norm) else 1.0
                    rank = sev * decay * street_boost * pos_boost
                    scored.append((rank, r))

                scored.sort(key=lambda item: (item[0], int(item[1].loss_amount or 0), int(item[1].id or 0)), reverse=True)
                return [
                    {
                        "hand_tier": r.hand_tier,
                        "board_texture": r.board_texture,
                        "opp_archetype": r.opp_archetype,
                        "street": r.street or "PREFLOP",
                        "position": r.position or "DESCONOCIDA",
                        "action_taken": r.action_taken,
                        "loss_amount": r.loss_amount,
                        "occurrences": int(r.occurrences or 1),
                        "total_loss_bb": float(r.total_loss_bb or 0.0),
                        "ev_lost_bb": float(r.ev_lost_bb or 0.0),
                        "severity_score": float(r.severity_score or 1.0),
                        "confidence": float(r.confidence or 1.0),
                        "decision_quality": r.decision_quality or "",
                        "lesson_learned": r.lesson_learned,
                        "similarity_key": r.similarity_key,
                    }
                    for _, r in scored[: max(1, int(limit))]
                ]

    def obtener_estadisticas_memoria(self) -> Dict[str, Any]:
        """Devuelve métricas agregadas de salud y cobertura de la memoria de experiencia."""
        with self._lock:
            with closing(self._session_factory()) as session:
                rows = session.query(ErrorExperiencia).all()
                if not rows:
                    return {
                        "total_lessons": 0,
                        "total_occurrences": 0,
                        "total_ev_lost_bb": 0.0,
                        "avg_severity": 0.0,
                        "top_leaks": [],
                    }
                total_occ = sum(int(r.occurrences or 1) for r in rows)
                total_ev_lost = round(sum(float(r.ev_lost_bb or 0.0) for r in rows), 2)
                avg_sev = round(sum(float(r.severity_score or 1.0) for r in rows) / len(rows), 3)
                top = sorted(rows, key=lambda r: float(r.severity_score or 0.0), reverse=True)[:5]
                return {
                    "total_lessons": len(rows),
                    "total_occurrences": total_occ,
                    "total_ev_lost_bb": total_ev_lost,
                    "avg_severity": avg_sev,
                    "top_leaks": [
                        {
                            "similarity_key": r.similarity_key,
                            "street": r.street,
                            "action_taken": r.action_taken,
                            "occurrences": int(r.occurrences or 1),
                            "severity_score": float(r.severity_score or 1.0),
                        }
                        for r in top
                    ],
                }

    def close(self) -> None:
        """Cierra el pool de conexiones SQLite de la memoria de experiencia."""
        with self._lock:
            if hasattr(self, "_engine") and self._engine:
                try:
                    self._engine.dispose()
                except Exception:
                    pass

    def dispose(self) -> None:
        self.close()

