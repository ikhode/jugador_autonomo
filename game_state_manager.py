"""
Módulo de Gestión de Estado del Juego y MÁQUINA DE ESTADOS CANÓNICA 6-MAX (Codere Texas Hold'em).

Define la secuencia formal de estados y transiciones en cada mano de 6 jugadores:
1. PREPARACION: Verificación de jugadores sentados, fichas e identificación del Botón (BTN).
2. CIEGAS: Asignación de Small Blind (SB) y Big Blind (BB) a la izquierda de BTN.
3. PREFLOP: Reparto de 2 cartas privadas. Acción inicia en UTG y avanza en sentido horario (UTG -> HJ -> CO -> BTN -> SB -> BB).
4. FLOP: Revelado de 3 cartas comunitarias. Acción inicia en el primer jugador activo a la izquierda del BTN (SB/BB/UTG).
5. TURN: Revelado de la 4ª carta comunitaria. Misma secuencia de apuestas.
6. RIVER: Revelado de la 5ª y última carta comunitaria. Última ronda de apuestas.
7. FINALIZACION_ANTICIPADA: Retirada de todos excepto 1 (victoria sin mostrar) o All-in automático.
8. SHOWDOWN: Revelado de cartas privadas y comparación de mejores combinaciones de 5 cartas.
9. LIQUIDACION: Adjudicación del bote (principal y secundarios), avance del botón e inicio de la siguiente mano.
"""
from typing import Any, Dict, List, Optional

# Secuencia oficial de estados del ciclo de juego Codere 6-Max
ESTADOS_JUEGO_CODERE = [
    "PREPARACION",
    "CIEGAS",
    "PREFLOP",
    "FLOP",
    "TURN",
    "RIVER",
    "FINALIZACION_ANTICIPADA",
    "SHOWDOWN",
    "LIQUIDACION",
]

POSICIONES_6MAX = ["BTN", "SB", "BB", "UTG", "HJ", "CO"]


def obtener_nombre_posicion_6max(rel_position_from_dealer: int, total_players: int = 6) -> str:
    """
    Calcula el nombre canónico de posición en mesa de 6 jugadores (o ajustado para 2..6):
    rel=0 -> BTN, rel=1 -> SB, rel=2 -> BB, rel=3 -> UTG, rel=4 -> HJ, rel=5 -> CO.
    """
    n = max(2, min(6, int(total_players or 6)))
    rel = int(rel_position_from_dealer or 0) % n

    if n == 2:
        return "BTN" if rel == 0 else "BB"
    if n == 3:
        return ["BTN", "SB", "BB"][rel]
    if n == 4:
        return ["BTN", "SB", "BB", "CO"][rel]
    if n == 5:
        return ["BTN", "SB", "BB", "UTG", "CO"][rel]
    # Standard 6-Max
    return ["BTN", "SB", "BB", "UTG", "HJ", "CO"][rel]


class GameStateManager:
    def __init__(self):
        self.estado_actual: str = "PREPARACION"
        self.current_game_state: Dict[str, Any] = {
            "estado": "PREPARACION",
            "calle": "PREFLOP",
            "posicion": "BTN",
            "total_jugadores": 6,
            "es_mi_turno": False,
            "mis_cartas": "",
            "cartas_comunitarias": "",
            "bote": 0.0,
            "a_igualar": 0.0,
        }
        self.historical_actions: Dict[str, int] = {"folds": 0, "calls": 0, "raises": 0}
        self.opponent_profile: Dict[str, float] = {"VPIP": 0.28, "PFR": 0.20}

    def transicionar_estado(self, nuevo_estado: str) -> None:
        if nuevo_estado in ESTADOS_JUEGO_CODERE:
            self.estado_actual = nuevo_estado
            self.current_game_state["estado"] = nuevo_estado

    def update_game_state(self, new_state_data: Dict[str, Any]) -> None:
        self.current_game_state.update(new_state_data or {})

    def update_historical_actions(self, action_type: str) -> None:
        action_clean = str(action_type or "").lower()
        if "fold" in action_clean:
            self.historical_actions["folds"] += 1
        elif "call" in action_clean:
            self.historical_actions["calls"] += 1
        elif "raise" in action_clean or "bet" in action_clean or "all" in action_clean:
            self.historical_actions["raises"] += 1

    def get_current_game_state(self) -> Dict[str, Any]:
        return self.current_game_state

    def get_historical_actions(self) -> Dict[str, int]:
        return self.historical_actions

    def get_opponent_profile(self) -> Dict[str, float]:
        return self.opponent_profile

    def reset_game_state(self) -> None:
        self.estado_actual = "PREPARACION"
        self.current_game_state = {
            "estado": "PREPARACION",
            "calle": "PREFLOP",
            "posicion": "BTN",
            "total_jugadores": 6,
            "es_mi_turno": False,
            "mis_cartas": "",
            "cartas_comunitarias": "",
            "bote": 0.0,
            "a_igualar": 0.0,
        }
        self.historical_actions = {"folds": 0, "calls": 0, "raises": 0}
