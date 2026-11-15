"""
Módulo de Memoria Vectorial y Aprendizaje de Errores Pasados (RAG para Póker Bot).
Almacena y busca similitudes vectoriales de errores tácticos pasados para impedir su repetición.
"""
from contextlib import closing
from pathlib import Path
import threading
from typing import Any, Dict, List, Optional

from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

from db_models import Base, ErrorExperiencia

PROJECT_ROOT = Path(__file__).resolve().parent
DB_MEMORY_PATH = PROJECT_ROOT / "logs" / "poker_experience_memory.db"


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


engine = create_engine(
    f"sqlite:///{DB_MEMORY_PATH}",
    connect_args={"check_same_thread": False, "timeout": 30.0},
)
_configure_sqlite_memory(engine)
SessionLocal = sessionmaker(bind=engine)


def inicializar_db_memoria():
    DB_MEMORY_PATH.parent.mkdir(parents=True, exist_ok=True)
    Base.metadata.create_all(engine)

inicializar_db_memoria()

def _construir_similarity_key(hand_tier: str, board_texture: str, opp_archetype: str) -> str:
    tier_tok = (hand_tier or "DESCONOCIDO").strip().upper()
    board_tok = ((board_texture or "DESCONOCIDA").strip().split()[0]).upper()
    opp_tok = ((opp_archetype or "DESCONOCIDO").strip().split()[0]).upper()
    return f"{tier_tok}|{board_tok}|{opp_tok}"


class PokerExperienceMemory:
    def __init__(self, db_path: Optional[Path] = None):
        self._lock = threading.RLock()
        if db_path is not None:
            db_path = Path(db_path).resolve()
            db_path.parent.mkdir(parents=True, exist_ok=True)
            eng = create_engine(
                f"sqlite:///{db_path}",
                connect_args={"check_same_thread": False, "timeout": 30.0},
            )
            _configure_sqlite_memory(eng)
            Base.metadata.create_all(eng)
            self._session_factory = sessionmaker(bind=eng)
        else:
            inicializar_db_memoria()
            self._session_factory = SessionLocal

    def registrar_error_pasado(
        self,
        hand_tier: str,
        board_texture: str,
        opp_archetype: str,
        action_taken: str,
        loss_amount: int,
        lesson_learned: str
    ):
        similarity_key = _construir_similarity_key(hand_tier, board_texture, opp_archetype)
        with self._lock:
            with closing(self._session_factory()) as session:
                nuevo_error = ErrorExperiencia(
                    hand_tier=hand_tier,
                    board_texture=board_texture,
                    opp_archetype=opp_archetype,
                    action_taken=action_taken,
                    loss_amount=loss_amount,
                    lesson_learned=lesson_learned,
                    similarity_key=similarity_key
                )
                session.add(nuevo_error)
                session.commit()

    def auto_evaluar_y_registrar_mano_perdedora(
        self,
        hand_tier: str,
        board_texture: str,
        opp_archetype: str,
        accion_tomada: str,
        net_chips: float,
        ev_diff: float = 0.0
    ):
        """
        Si la mano terminó en pérdida o desviación táctica, genera automáticamente una lección
        retrospectiva y la almacena en la BDD SQLite de memoria vectorial para futuras manos.
        """
        if net_chips >= 0 and ev_diff >= 0:
            return  # Mano ganadora o correcta, no requiere registro de error

        perdidaf = abs(int(net_chips))
        leccion = f"En Rango [{hand_tier}] y {board_texture} contra {opp_archetype}, la acción '{accion_tomada}' provocó una pérdida de {perdidaf} fichas. EV Pérdida: {ev_diff:.1f} BBs. INSTRUCCIÓN ANTI-REPETICIÓN: Evitar '{accion_tomada}' en condiciones idénticas."

        self.registrar_error_pasado(
            hand_tier=hand_tier,
            board_texture=board_texture,
            opp_archetype=opp_archetype,
            action_taken=accion_tomada,
            loss_amount=perdidaf,
            lesson_learned=leccion
        )

    def buscar_errores_similares(
        self,
        hand_tier: str,
        board_texture: str,
        opp_archetype: str,
        limit: int = 2
    ) -> List[Dict[str, Any]]:
        similarity_key = _construir_similarity_key(hand_tier, board_texture, opp_archetype)
        with self._lock:
            with closing(self._session_factory()) as session:
                results = session.query(ErrorExperiencia).filter(
                    (ErrorExperiencia.similarity_key == similarity_key) |
                    (
                        (ErrorExperiencia.hand_tier == hand_tier) &
                        (ErrorExperiencia.board_texture == board_texture) &
                        (ErrorExperiencia.opp_archetype == opp_archetype)
                    )
                ).order_by(ErrorExperiencia.loss_amount.desc(), ErrorExperiencia.id.desc()).limit(limit).all()

                return [{
                    "hand_tier": r.hand_tier,
                    "board_texture": r.board_texture,
                    "opp_archetype": r.opp_archetype,
                    "action_taken": r.action_taken,
                    "loss_amount": r.loss_amount,
                    "lesson_learned": r.lesson_learned,
                    "similarity_key": r.similarity_key,
                } for r in results]
