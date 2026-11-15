"""
Módulo de Memoria Vectorial y Aprendizaje de Errores Pasados (RAG para Póker Bot).
Almacena y busca similitudes vectoriales de errores tácticos pasados para impedir su repetición.
"""
import json
import sqlite3
import time
from pathlib import Path
from typing import Dict, Any, List, Optional

DB_MEMORY_PATH = Path("logs/poker_experience_memory.db")

def inicializar_db_memoria():
    DB_MEMORY_PATH.parent.mkdir(exist_ok=True)
    with sqlite3.connect(DB_MEMORY_PATH) as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS errores_experiencia (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT DEFAULT CURRENT_TIMESTAMP,
                hand_tier TEXT,
                board_texture TEXT,
                opp_archetype TEXT,
                action_taken TEXT,
                loss_amount INTEGER,
                lesson_learned TEXT,
                similarity_key TEXT
            )
        """)
        conn.commit()

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
        """Registra un error o pérdida táctica para vectorizarlo y evitar que se repita."""
        similarity_key = f"{hand_tier.upper()}|{board_texture.split()[0].upper()}|{opp_archetype.split()[0].upper()}"
        with sqlite3.connect(DB_MEMORY_PATH) as conn:
            conn.execute("""
                INSERT INTO errores_experiencia
                (hand_tier, board_texture, opp_archetype, action_taken, loss_amount, lesson_learned, similarity_key)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (hand_tier, board_texture, opp_archetype, action_taken, loss_amount, lesson_learned, similarity_key))
            conn.commit()

    def buscar_errores_similares(
        self,
        hand_tier: str,
        board_texture: str,
        opp_archetype: str,
        limit: int = 2
    ) -> List[Dict[str, Any]]:
        """
        Busca en la BDD vectorial de errores pasados situaciones con la misma combinación
        de Rango, Textura de Mesa y Arquetipo de Rival.
        """
        key_parcial = f"%{hand_tier.upper()}%"
        arch_parcial = f"%{opp_archetype.split()[0].upper()}%"

        with sqlite3.connect(DB_MEMORY_PATH) as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT hand_tier, board_texture, opp_archetype, action_taken, loss_amount, lesson_learned
                FROM errores_experiencia
                WHERE similarity_key LIKE ? OR opp_archetype LIKE ?
                ORDER BY loss_amount DESC, id DESC
                LIMIT ?
            """, (key_parcial, arch_parcial, limit))
            rows = cursor.fetchall()

        resultado = []
        for r in rows:
            resultado.append({
                "hand_tier": r[0],
                "board_texture": r[1],
                "opp_archetype": r[2],
                "action_taken": r[3],
                "loss_amount": r[4],
                "lesson_learned": r[5]
            })
        return resultado
