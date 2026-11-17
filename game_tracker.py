"""
Módulo de Seguimiento de Estado de Juego, Historial y Resiliencia (Game Tracker & Watchdog).
Encapsula el seguimiento del ciclo de vida por mesa/juego/ronda, la resiliencia contra errores y el watchdog de turno.
"""
import time
from typing import Any, Dict, List, Optional
import config
from poker_gto_engine import formatear_cartas_canonicas


def calcular_backoff_segundos(
    consecutive_errors: int,
    base: Optional[float] = None,
    cap: Optional[float] = None,
) -> float:
    """Calcula espera exponencial con tope ante errores consecutivos."""
    b = float(base if base is not None else getattr(config, "ERROR_BACKOFF_BASE_SEC", 0.5))
    c = float(cap if cap is not None else getattr(config, "ERROR_BACKOFF_MAX_SEC", 16.0))
    if consecutive_errors <= 0:
        return 0.0
    return min(c, b * (2 ** (consecutive_errors - 1)))


class ControladorResiliencia:
    """Gestiona el backoff exponencial y el watchdog para congelamientos en turno."""

    def __init__(
        self,
        max_consecutive_errors: Optional[int] = None,
        watchdog_sec: Optional[float] = None,
        hand_watchdog_timeout_sec: Optional[float] = None,
    ):
        self.max_consecutive_errors = int(
            max_consecutive_errors
            if max_consecutive_errors is not None
            else getattr(config, "MAX_CONSECUTIVE_ERRORS", 5)
        )
        wd = hand_watchdog_timeout_sec if hand_watchdog_timeout_sec is not None else watchdog_sec
        self.hand_watchdog_timeout_sec = float(
            wd
            if wd is not None
            else getattr(config, "HAND_WATCHDOG_TIMEOUT_SEC", 35.0)
        )
        self.consecutive_errors = 0
        self.hand_started_ts: Optional[float] = None
        self.current_hand_key: Optional[str] = None

    def registrar_exito(self) -> None:
        self.consecutive_errors = 0

    def registrar_error(self, err: Optional[Exception] = None) -> tuple[bool, float]:
        self.consecutive_errors += 1
        backoff = calcular_backoff_segundos(self.consecutive_errors)
        should_stop = self.consecutive_errors >= self.max_consecutive_errors
        return should_stop, backoff

    def actualizar_mano_watchdog(self, hand_key: str, now: Optional[float] = None) -> None:
        t = time.time() if now is None else float(now)
        if hand_key != self.current_hand_key:
            self.current_hand_key = hand_key
            self.hand_started_ts = t

    def verificar_watchdog_mano(self, now: Optional[float] = None) -> bool:
        if self.hand_started_ts is None:
            return False
        t = time.time() if now is None else float(now)
        return (t - self.hand_started_ts) > self.hand_watchdog_timeout_sec

    def reiniciar_watchdog_mano(self) -> None:
        self.hand_started_ts = None
        self.current_hand_key = None


class GameTracker:
    def __init__(self, analytics_logger=None, experience_memory=None):
        self.analytics_logger = analytics_logger
        self.experience_memory = experience_memory
        self.hand_number = 1
        self.current_hand_history: List[str] = []
        self.past_hands_summary: List[str] = []
        self.http_turn_signal = False
        self.my_cards: List[str] = []
        self.community_cards: List[str] = []
        self.last_pot = 0
        self.opp_id = ""
        self.opp_nick = ""
        self.is_dealer = False
        self.position_name = "DESCONOCIDA"
        self.hero_last_action_context = None
        self.current_street = "PREFLOP"
        self.current_round_no = 1
        self.hero_just_acted_ts = 0.0
        self.last_hand_tier = "DESCONOCIDO"
        self.last_board_texture = "DESCONOCIDA"
        self.last_opp_archetype = "Desconocido"
        self.last_action_taken = "WAIT"
        self.last_hand_net_chips = 0.0
        self.last_street = "PREFLOP"
        self.last_position = "DESCONOCIDA"
        self.last_ev_diff = 0.0
        self.last_decision_quality = ""
        self.last_invested_chips = 0.0
        self.last_big_blind = float(getattr(config, "BIG_BLIND_DEFAULT", 20.0))
        self.prev_player_states: Dict[str, Dict[str, Any]] = {}
        self.active_opponents: Dict[str, str] = {}
        self.ws_action_ts: float = 0.0
        self.preflop_raise_count: int = 0
        self.preflop_raiser_id: Optional[str] = None
        self.cbet_fired_in_hand: bool = False

        # Identificadores estructurados de ciclo de vida (Mesa / Juego / Ronda / Mano)
        self.table_id: str = ""
        self.game_id: str = ""
        self.round_id: str = ""
        self.current_hand_key: str = ""
        self._last_finalized_hand_ts = 0.0

    def iniciar_nueva_mano(self, table_id: str = "", game_id: str = "", round_id: str = ""):
        """Inicializa limpiamente las variables de estado al comenzar una nueva mano sin arrastrar datos ajenos."""
        nueva_clave = f"{table_id or self.table_id}:{game_id or self.game_id}:{round_id or self.round_id}"
        if nueva_clave and nueva_clave == self.current_hand_key and (self.my_cards or self.community_cards):
            return

        if self.current_hand_key and (self.my_cards or self.community_cards or len(self.current_hand_history) > 3):
            self.finalizar_mano_actual("Transición a nueva mano/ronda")

        if table_id: self.table_id = str(table_id)
        if game_id: self.game_id = str(game_id)
        if round_id: self.round_id = str(round_id)
        self.current_hand_key = nueva_clave or f"hand_{self.hand_number}"

        self.my_cards.clear()
        self.community_cards.clear()
        self.current_hand_history.clear()
        self.prev_player_states.clear()
        self.last_pot = 0
        self.http_turn_signal = False
        self.current_street = "PREFLOP"
        self.current_round_no = 1
        self.cbet_fired_in_hand = False
        self.preflop_raise_count = 0
        self.preflop_raiser_id = None
        self.registrar_evento("LIFECYCLE", f"🎮 Inicializada mano #{self.hand_number} (Mesa: {self.table_id or 'N/A'}, Juego: {self.game_id or 'N/A'}, Ronda: {self.round_id or 'N/A'})")

    def destruir_estado_mano(self, motivo_str: str = "Mano concluida"):
        """Destruye y limpia completamente el estado transitorio de la mano al finalizar la ronda."""
        self.finalizar_mano_actual(motivo_str)
        self.my_cards.clear()
        self.community_cards.clear()
        self.current_hand_history.clear()
        self.prev_player_states.clear()
        self.last_pot = 0
        self.http_turn_signal = False
        self.current_street = "PREFLOP"
        self.current_round_no = 1
        self.current_hand_key = ""
        self.round_id = ""
        self.registrar_evento("LIFECYCLE", f"🧹 Estado de mano limpiado/destruido ({motivo_str})")

    def registrar_evento(self, tipo: str, detalle: str):
        timestamp = time.strftime('%H:%M:%S')
        msg = f"[{timestamp}] {tipo}: {detalle}"
        self.current_hand_history.append(msg)
        if len(self.current_hand_history) > 25:
            self.current_hand_history.pop(0)

    def finalizar_mano_actual(self, resultado_str: str):
        ahora = time.time()
        if self._last_finalized_hand_ts > 0 and (ahora - self._last_finalized_hand_ts < 2.0):
            return
        self._last_finalized_hand_ts = ahora
        resumen = f"Mano #{self.hand_number}: Posición {self.position_name} | Cartas {self.my_cards} | Bote: {self.last_pot} | Resultado: {resultado_str}"
        self.past_hands_summary.append(resumen)
        if len(self.past_hands_summary) > 5:
            self.past_hands_summary.pop(0)

        if self.analytics_logger is not None:
            try:
                self.analytics_logger.finalizar_mano(
                    resultado_str=resultado_str,
                    hand_number=self.hand_number,
                    hole_cards=formatear_cartas_canonicas(self.my_cards) if self.my_cards else None,
                    community_cards=formatear_cartas_canonicas(self.community_cards) if self.community_cards else None,
                    pot_final=float(self.last_pot or 0),
                )
            except Exception:
                pass

        res_lower = (resultado_str or "").lower()
        net_chips = float(self.last_hand_net_chips or 0.0)
        mano_ganada = any(k in res_lower for k in ("ganad", "won", "victoria", "cobrado"))
        if net_chips >= 0 and not mano_ganada:
            if any(k in res_lower for k in ("perdid", "lost", "derrota", "eliminad")):
                net_chips = -float(self.last_invested_chips or self.last_pot or getattr(config, "BIG_BLIND_DEFAULT", 20.0))
            elif self.last_invested_chips > 0:
                net_chips = -float(self.last_invested_chips)

        tiene_error_tactico = (
            float(self.last_ev_diff or 0.0) < -0.5
            or str(self.last_decision_quality or "").upper() in ("INACCURACY", "MISTAKE", "BLUNDER_EV")
        )
        if (net_chips < 0 or tiene_error_tactico) and self.last_action_taken not in ("WAIT", "START_GAME", "REPLAY"):
            if self.experience_memory is not None:
                try:
                    self.experience_memory.auto_evaluar_y_registrar_mano_perdedora(
                        hand_tier=self.last_hand_tier or "DESCONOCIDO",
                        board_texture=self.last_board_texture or "DESCONOCIDA",
                        opp_archetype=self.last_opp_archetype or "Desconocido",
                        accion_tomada=self.last_action_taken,
                        net_chips=net_chips,
                        ev_diff=float(self.last_ev_diff or 0.0),
                        street=self.last_street or self.current_street or "PREFLOP",
                        position=self.last_position or self.position_name or "DESCONOCIDA",
                        big_blind=float(self.last_big_blind or getattr(config, "BIG_BLIND_DEFAULT", 20.0)),
                        decision_quality=self.last_decision_quality or "",
                    )
                except Exception:
                    pass

        self.hand_number += 1
        self.current_hand_history.clear()
        self.my_cards.clear()
        self.community_cards.clear()
        self.last_pot = 0
        self.http_turn_signal = False
        self.is_dealer = False
        self.position_name = "DESCONOCIDA"
        self.hero_last_action_context = None
        self.current_street = "PREFLOP"
        self.current_round_no = 1
        self.last_hand_tier = "DESCONOCIDO"
        self.last_board_texture = "DESCONOCIDA"
        self.last_opp_archetype = "Desconocido"
        self.last_action_taken = "WAIT"
        self.last_hand_net_chips = 0.0
        self.last_street = "PREFLOP"
        self.last_position = "DESCONOCIDA"
        self.last_ev_diff = 0.0
        self.last_decision_quality = ""
        self.last_invested_chips = 0.0
        self.last_big_blind = float(getattr(config, "BIG_BLIND_DEFAULT", 20.0))
        self.prev_player_states.clear()
        self.active_opponents.clear()
        self.ws_action_ts = 0.0
        self.preflop_raise_count = 0
        self.preflop_raiser_id = None
        self.cbet_fired_in_hand = False

    def reset_mano(self):
        self.finalizar_mano_actual("Reinicio de mano / Modal")
        self.position_name = "DESCONOCIDA"
        self.is_dealer = False
        self.my_cards.clear()
        self.community_cards.clear()
        self.current_street = "PREFLOP"
        self.current_round_no = 1
        self.current_hand_history.clear()
