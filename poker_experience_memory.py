"""
Módulo de Memoria Vectorial y Aprendizaje de Errores Pasados (RAG para Póker Bot).
Almacena y busca similitudes vectoriales de errores tácticos pasados para impedir su repetición.
"""
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from db_models import Base, ErrorExperiencia
from pathlib import Path
from typing import Dict, Any, List, Optional

DB_MEMORY_PATH = Path("logs/poker_experience_memory.db")
engine = create_engine(f"sqlite:///{DB_MEMORY_PATH}")
SessionLocal = sessionmaker(bind=engine)

def inicializar_db_memoria():
    DB_MEMORY_PATH.parent.mkdir(exist_ok=True)
    Base.metadata.create_all(engine)

inicializar_db_memoria()

class PokerExperienceMemory:
    def __init__(self):
        inicializar_db_memoria()

    def registrar_error_pasado(
        self,
        hand_tier: str,
        board_texture: str,
        opp_archetype: str,
        action_taken: str,
        loss_amount: int,
        lesson_learned: str
    ):
        similarity_key = f"{hand_tier.upper()}|{board_texture.split()[0].upper()}|{opp_archetype.split()[0].upper()}"
        session = SessionLocal()
        try:
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
        finally:
            session.close()

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
        session = SessionLocal()
        try:
            results = session.query(ErrorExperiencia).filter(
                (ErrorExperiencia.hand_tier.ilike(f"%{hand_tier}%")) |
                (ErrorExperiencia.opp_archetype.ilike(f"%{opp_archetype.split()[0]}%"))
            ).order_by(ErrorExperiencia.loss_amount.desc(), ErrorExperiencia.id.desc()).limit(limit).all()

            return [{
                "hand_tier": r.hand_tier,
                "board_texture": r.board_texture,
                "opp_archetype": r.opp_archetype,
                "action_taken": r.action_taken,
                "loss_amount": r.loss_amount,
                "lesson_learned": r.lesson_learned
            } for r in results]
        finally:
            session.close()
