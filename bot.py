import os
import re
import json
import time
import hashlib
import concurrent.futures
from pathlib import Path
from typing import Optional
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError
import config
from poker_agent import PokerAgent, PokerDecision
from opponents_profiler import OpponentProfiler, sanitizar_nickname, es_nickname_invalido
from poker_experience_memory import PokerExperienceMemory
from poker_gto_engine import (
    clasificar_mano_preflop,
    calcular_pot_odds_y_sizing,
    analizar_textura_mesa_y_blockers,
    generar_frecuencia_mixta_gto,
    evaluar_combinacion_mano_y_mesa,
    evaluar_push_fold_nash,
    decidir_por_ev,
    formatear_cartas_canonicas,
    formatear_carta_canonica,
)
from analytics_logger import PokerAnalyticsLogger
from strategic_engine import StrategicEngine
from opponent_model import OpponentModel
from game_state_manager import GameStateManager
from decision_maker import DecisionMaker
from live_table_tracker import LiveTableMonitor, cargar_snapshot_live_archivo
from ws_decoder import configurar_interceptores as configurar_ws_interceptores

profiler = OpponentProfiler()
table_tracker = LiveTableMonitor(profiler, max_asientos=getattr(config, "MAX_TABLE_SEATS", 3))

def obtener_live_status() -> dict:
    global tracker, table_tracker
    try:
        if table_tracker is not None:
            tr = globals().get("tracker")
            h_num = getattr(tr, "hand_number", 1) if tr else 1
            c_street = getattr(tr, "current_street", "PREFLOP") if tr else "PREFLOP"
            m_cards = getattr(tr, "my_cards", []) if tr else []
            comm_cards = getattr(tr, "community_cards", []) if tr else []
            l_pot = getattr(tr, "last_pot", 0.0) if tr else 0.0
            pos_name = getattr(tr, "position_name", "DESCONOCIDA") if tr else "DESCONOCIDA"
            l_events = getattr(tr, "current_hand_history", []) if tr else []

            snap = table_tracker.obtener_snapshot_live(
                hand_number=h_num,
                current_street=c_street,
                my_cards=m_cards,
                community_cards=comm_cards,
                last_pot=l_pot,
                position_name=pos_name,
                live_events=l_events,
            )
            if snap and (snap.get("my_cards") or snap.get("community_cards") or snap.get("live_events") or snap.get("opponents")):
                return snap
    except Exception:
        pass

    tr = globals().get("tracker")
    max_seats = getattr(config, "MAX_TABLE_SEATS", 3)
    return {
        "hand_number": getattr(tr, "hand_number", 1) if tr else 1,
        "current_street": getattr(tr, "current_street", "PREFLOP") if tr else "PREFLOP",
        "my_cards": getattr(tr, "my_cards", []) if tr else [],
        "community_cards": getattr(tr, "community_cards", []) if tr else [],
        "last_pot": getattr(tr, "last_pot", 0) if tr else 0,
        "position_name": getattr(tr, "position_name", "DESCONOCIDA") if tr else "DESCONOCIDA",
        "modalidad": getattr(config, "POKER_MODE", "Twister 3-Max"),
        "table_info": {
            "max_asientos": max_seats,
            "asientos_ocupados": 1,
            "asientos_disponibles": max_seats - 1,
            "jugadores_activos": 1,
            "jugadores_en_mano": 1,
            "numero_oponentes_mesa": 0,
            "numero_oponentes_activos": 0,
            "numero_oponentes_en_mano": 0,
        },
        "opponents": [],
        "live_events": list(getattr(tr, "current_hand_history", [])) if tr and getattr(tr, "current_hand_history", []) else ["[00:00:00] Partida activa - Monitoreando mesa en vivo..."],
    }

# Pool de subprocesos persistente global para cero bloqueos de E/S o red
bg_executor = concurrent.futures.ThreadPoolExecutor(max_workers=2)

# Logger asíncrono de analíticas (cero impacto en latencia de juego)
analytics_logger = PokerAnalyticsLogger(
    version_id=getattr(config, "BOT_VERSION", "1.3"),
    version_description=getattr(config, "BOT_VERSION_DESC", "Configuración activa del bot"),
    model_name=getattr(config, "GEMINI_MODEL", "gemini-2.5-flash"),
    initial_bankroll=getattr(config, "INITIAL_BANKROLL", 1500.0),
    big_blind=getattr(config, "BIG_BLIND_DEFAULT", 20.0),
    async_mode=True,
) if getattr(config, "ANALYTICS_ENABLED", True) else None


def log_json_estructurado(
    event: str,
    hand_id: str = "",
    decision_id: str = "",
    payload: Optional[dict] = None,
    log_path: Optional[str] = None,
) -> dict:
    """Escribe un evento estructurado directamente en SQLite (realtime_game_events) y archivo de respaldo."""
    record = {
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "event_type": str(event),
        "hand_id": str(hand_id or ""),
        "decision_id": str(decision_id or ""),
        "hand_uid": str(hand_id or ""),
        "chosen_action": str(decision_id or ""),
    }
    if isinstance(payload, dict):
        record.update(payload)

    if analytics_logger is not None:
        try:
            analytics_logger.registrar_evento_tiempo_real(record)
        except Exception:
            pass

    target = Path(log_path or getattr(config, "JSON_LOG_PATH", "logs/bot_decisions.jsonl"))
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        with open(target, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")
    except Exception:
        pass
    return record


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


def detectar_desconexion(dom_data: Optional[dict] = None, page=None, err: Optional[Exception] = None) -> bool:
    """Detecta desconexión por DOM, estado de pestaña o excepción de red/navegador."""
    if page is not None:
        try:
            if page.is_closed():
                return True
        except Exception:
            return True

    if isinstance(dom_data, dict):
        texto = " ".join([
            str(dom_data.get("modales_visibles") or ""),
            str(dom_data.get("estado_mesa") or ""),
            str(dom_data.get("botones_visibles") or ""),
        ]).lower()
        for kw in ("desconect", "reconnecting", "connection lost", "sin conexión", "sin conexion", "network error", "offline"):
            if kw in texto:
                return True

    if err is not None:
        msg = f"{type(err).__name__}: {err}".lower()
        for kw in ("err_internet_disconnected", "net::err_", "connectionreset", "targetclosed", "browser has been closed", "disconnected"):
            if kw in msg:
                return True

    return False


class ControladorResiliencia:
    """Controla errores consecutivos, backoff exponencial y watchdog por mano."""

    def __init__(
        self,
        max_consecutive_errors: Optional[int] = None,
        hand_watchdog_timeout_sec: Optional[float] = None,
    ):
        self.max_consecutive_errors = int(
            max_consecutive_errors if max_consecutive_errors is not None else getattr(config, "MAX_CONSECUTIVE_ERRORS", 10)
        )
        self.hand_watchdog_timeout_sec = float(
            hand_watchdog_timeout_sec if hand_watchdog_timeout_sec is not None else getattr(config, "HAND_WATCHDOG_TIMEOUT_SEC", 120.0)
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

# ---------------------------------------------------------
# ACUMULADOR DE HISTORIAL COMPLETO DE PARTIDA Y TORNEO
# ---------------------------------------------------------
class GameTracker:
    def __init__(self):
        self.hand_number = 1
        self.current_hand_history = []
        self.past_hands_summary = []
        self.http_turn_signal = False
        self.my_cards = []
        self.community_cards = []
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
        self.table_id: str = ""
        self.game_id: str = ""
        self.round_id: str = ""
        self.current_hand_key: str = ""

    def iniciar_nueva_mano(self, table_id: str = "", game_id: str = "", round_id: str = ""):
        """Inicializa limpiamente las variables de estado al comenzar una nueva mano sin arrastrar datos ajenos."""
        nueva_clave = f"{table_id or getattr(self, 'table_id', '')}:{game_id or getattr(self, 'game_id', '')}:{round_id or getattr(self, 'round_id', '')}"
        if nueva_clave and nueva_clave == getattr(self, 'current_hand_key', '') and (self.my_cards or self.community_cards):
            return

        if getattr(self, 'current_hand_key', '') and (self.my_cards or self.community_cards or len(self.current_hand_history) > 3):
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
        self.registrar_evento("LIFECYCLE", f"🎮 Inicializada mano #{self.hand_number} (Mesa: {getattr(self, 'table_id', 'N/A')}, Juego: {getattr(self, 'game_id', 'N/A')}, Ronda: {getattr(self, 'round_id', 'N/A')})")

    def registrar_evento(self, tipo: str, detalle: str):
        timestamp = time.strftime('%H:%M:%S')
        msg = f"[{timestamp}] {tipo}: {detalle}"
        self.current_hand_history.append(msg)
        if len(self.current_hand_history) > 25:
            self.current_hand_history.pop(0)

    def finalizar_mano_actual(self, resultado_str: str):
        ahora = time.time()
        if getattr(self, "_last_finalized_hand_ts", 0) > 0 and (ahora - self._last_finalized_hand_ts < 2.0):
            return
        self._last_finalized_hand_ts = ahora
        resumen = f"Mano #{self.hand_number}: Posición {self.position_name} | Cartas {self.my_cards} | Bote: {self.last_pot} | Resultado: {resultado_str}"
        self.past_hands_summary.append(resumen)
        if len(self.past_hands_summary) > 5:
            self.past_hands_summary.pop(0)

        if analytics_logger is not None:
            try:
                analytics_logger.finalizar_mano(
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
            try:
                experience_memory.auto_evaluar_y_registrar_mano_perdedora(
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

    def reset_mano(self):
        self.finalizar_mano_actual("Reinicio de mano / Modal")
        self.position_name = "DESCONOCIDA"
        self.is_dealer = False
        self.my_cards.clear()
        self.community_cards.clear()
        self.current_street = "PREFLOP"
        self.current_round_no = 1
        self.current_hand_history.clear()

    def obtener_contexto_completo(self) -> str:
        past_str = "\n".join(self.past_hands_summary) if self.past_hands_summary else "Primera mano del torneo."
        current_str = "\n".join(self.current_hand_history) if self.current_hand_history else "Inicio de la mano."
        return f"--- HISTORIAL DE MANOS PREVIAS EN ESTE TORNEO ---\n{past_str}\n\n--- SECUENCIA EN VIVO DE LA MANO ACTUAL (# {self.hand_number}) ---\n{current_str}"

tracker = GameTracker()
profiler = OpponentProfiler()
table_tracker = LiveTableMonitor(profiler=profiler, hero_nickname=getattr(config, "HERO_NICKNAME", "Hero"))
experience_memory = PokerExperienceMemory()


def procesar_delta_player_states(
    data_json: dict,
    tracker_obj: GameTracker,
    profiler_obj: OpponentProfiler,
    hero_nick: str = "",
) -> List[Tuple[str, str, str]]:
    """
    Atribuye acciones en 3-Max usando el delta exacto de playerStates del WebSocket (con playerCode).
    Devuelve una lista de (player_id, nickname, accion) atribuidas en este delta.
    """
    states = data_json.get("playerStates")
    if not isinstance(states, list):
        return []

    atribuidas: List[Tuple[str, str, str]] = []
    calle = getattr(tracker_obj, "current_street", "PREFLOP")
    hand_id = getattr(tracker_obj, "hand_number", 1)
    max_prev_bet = max(
        (float(v.get("bet", 0.0)) for v in tracker_obj.prev_player_states.values()),
        default=0.0,
    )
    hero_nick_l = (hero_nick or "").strip().lower()

    for ps in states:
        if not isinstance(ps, dict):
            continue
        pid = str(
            ps.get("playerCode")
            or ps.get("playerId")
            or ps.get("id")
            or ps.get("code")
            or ps.get("nickname")
            or ps.get("playerName")
            or ps.get("name")
            or ""
        ).strip()
        nick = str(
            ps.get("nickname")
            or ps.get("playerName")
            or ps.get("name")
            or pid
        ).strip()
        if not pid and not nick:
            continue

        # Excluir a Hero
        cards_ps = [c for c in (ps.get("cards") or []) if isinstance(c, str) and c != "X"]
        es_hero = bool(
            ps.get("isHero")
            or (hero_nick_l and nick.lower() == hero_nick_l)
            or (cards_ps and tracker_obj.my_cards and cards_ps == tracker_obj.my_cards)
        )
        bet = float(ps.get("bet") or ps.get("roundBet") or ps.get("currentBet") or ps.get("chipsInPlay") or 0.0)
        raw_status = ps.get("status") or ps.get("state") or ("FOLD" if ps.get("folded") else "ACTIVE")
        status = str(raw_status).upper()
        action_ws = str(ps.get("action") or ps.get("lastAction") or ps.get("actionType") or "").upper()
        seat = ps.get("seat") or ps.get("seatNo")

        if es_hero:
            tracker_obj.prev_player_states[pid or nick] = {"bet": bet, "status": status, "nickname": nick}
            if bet > max_prev_bet:
                max_prev_bet = bet
            continue

        if status in ("FOLD", "FOLDED", "OUT", "ELIMINATED"):
            tracker_obj.active_opponents.pop(pid, None)
        else:
            tracker_obj.active_opponents[pid] = nick

        prev = tracker_obj.prev_player_states.get(pid, {})
        prev_bet = float(prev.get("bet", 0.0))
        prev_status = str(prev.get("status", "ACTIVE")).upper()

        accion_detectada: Optional[str] = None
        is_3bet = False
        three_bet_opp = False
        facing_3bet = False
        is_cbet = False
        cbet_opp = False
        facing_cbet = False

        if action_ws in ("FOLD", "FOLDED") or (status in ("FOLD", "FOLDED") and prev_status not in ("FOLD", "FOLDED")):
            accion_detectada = "FOLD"
            if calle == "PREFLOP":
                three_bet_opp = tracker_obj.preflop_raise_count == 1
                facing_3bet = tracker_obj.preflop_raise_count >= 2
            else:
                facing_cbet = tracker_obj.cbet_fired_in_hand
        elif bet > prev_bet:
            if bet > max_prev_bet:
                accion_detectada = "RAISE" if (calle == "PREFLOP" or max_prev_bet > 0) else "BET"
                if calle == "PREFLOP":
                    if tracker_obj.preflop_raise_count >= 1:
                        is_3bet = True
                        three_bet_opp = True
                    else:
                        tracker_obj.preflop_raiser_id = pid
                    tracker_obj.preflop_raise_count += 1
                else:
                    if tracker_obj.preflop_raiser_id == pid and not tracker_obj.cbet_fired_in_hand:
                        is_cbet = True
                        cbet_opp = True
                        tracker_obj.cbet_fired_in_hand = True
                max_prev_bet = bet
            else:
                accion_detectada = "CALL"
                if calle == "PREFLOP":
                    three_bet_opp = tracker_obj.preflop_raise_count == 1
                    facing_3bet = tracker_obj.preflop_raise_count >= 2
                else:
                    facing_cbet = tracker_obj.cbet_fired_in_hand
        elif action_ws == "CHECK":
            accion_detectada = "CHECK"
            if calle != "PREFLOP" and tracker_obj.preflop_raiser_id == pid and not tracker_obj.cbet_fired_in_hand:
                cbet_opp = True

        if accion_detectada:
            profiler_obj.registrar_accion_rival(
                player_id=pid,
                nickname=nick,
                accion=accion_detectada,
                calle=calle,
                is_3bet=is_3bet,
                three_bet_opportunity=three_bet_opp,
                facing_3bet=facing_3bet,
                is_cbet=is_cbet,
                cbet_opportunity=cbet_opp,
                facing_cbet=facing_cbet,
                seat=seat,
                status=status,
                hand_id=hand_id,
            )
            if status not in ("FOLD", "FOLDED", "OUT", "ELIMINATED") or not tracker_obj.active_opponents:
                tracker_obj.opp_id = pid
                tracker_obj.opp_nick = nick
            tracker_obj.ws_action_ts = time.time()
            tracker_obj.registrar_evento("WS_ACCION", f"{nick} ({pid}): {accion_detectada}")
            atribuidas.append((pid, nick, accion_detectada))

        tracker_obj.prev_player_states[pid] = {"bet": bet, "status": status, "nickname": nick}

    if tracker_obj.active_opponents and tracker_obj.opp_id not in tracker_obj.active_opponents:
        act_id, act_nick = next(iter(tracker_obj.active_opponents.items()))
        tracker_obj.opp_id = act_id
        tracker_obj.opp_nick = act_nick

    return atribuidas

# JS MutationObserver para reacción instantánea (0ms)
JS_INICIALIZAR_MUTATION_OBSERVER = r"""() => {
    if (window.__POKER_OBSERVER_ACTIVE__) return;
    window.__POKER_OBSERVER_ACTIVE__ = true;
    window.__POKER_EVENT_CHANGED__ = true;

    const getSnapshot = () => {
        const miTimer = !!document.querySelector('.my-player .text-countdown, .my-player .timeout-wrapper');
        const idsAccion = ['#FOLD', '#CHECK', '#CALL', '#BET', '#RAISE', '#pre-configured-action-button-0'];
        const hayBotones = idsAccion.some(id => {
            const el = document.querySelector(id);
            return el && (el.offsetWidth > 0 || el.offsetHeight > 0);
        });
        const misCartas = Array.from(document.querySelectorAll('.my-player .card-image-backup')).map(c => (c.querySelector('.card-rank')?.innerText || '') + (c.querySelector('.card-suit')?.innerText || '')).join('');
        const comunitarias = Array.from(document.querySelectorAll('.community-cards .card-image-backup')).map(c => (c.querySelector('.card-rank')?.innerText || '') + (c.querySelector('.card-suit')?.innerText || '')).join('');
        const bote = (document.querySelector('#main-pot, .total-pot-amount')?.innerText || '').trim();

        return `${miTimer || hayBotones}|${misCartas}|${comunitarias}|${bote}`;
    };

    let lastSnapshot = getSnapshot();

    const observer = new MutationObserver(() => {
        const currentSnapshot = getSnapshot();
        if (currentSnapshot !== lastSnapshot) {
            lastSnapshot = currentSnapshot;
            window.__POKER_EVENT_CHANGED__ = true;
        }
    });

    if (document.body) {
        observer.observe(document.body, { childList: true, subtree: true, attributes: true, characterData: true });
    }
}"""

JS_VERIFICAR_TURNO_O_MODAL = r"""() => {
    const isChanged = window.__POKER_EVENT_CHANGED__;
    if (isChanged) window.__POKER_EVENT_CHANGED__ = false;

    const miTurno = !!document.querySelector('.my-player .text-countdown, .my-player .timeout-wrapper, .my-player .countdown-text');
    if (miTurno) return true;

    const accionesArea = document.querySelector('.actions-area');
    const hayBotonAccion = accionesArea && Array.from(accionesArea.querySelectorAll('.action-button, #FOLD, #CHECK, #CALL, #BET, #RAISE'))
        .some(el => el.offsetWidth > 0 || el.offsetHeight > 0 || window.getComputedStyle(el).display !== 'none');
    if (hayBotonAccion) return true;

    const buscarPorTexto = (selector, texto) => {
        const elementos = document.querySelectorAll(selector);
        return Array.from(elementos).find(el => el.textContent.includes(texto));
    };

    const hayModal = document.querySelector('.filter-play-button') ||
                     buscarPorTexto('button', 'Jugar de nuevo') ||
                     buscarPorTexto('div', 'Jugar de nuevo') ||
                     buscarPorTexto('span', 'Aceptar') ||
                     (document.body && (document.body.textContent.includes('podrías hacer check sin coste alguno') || document.body.textContent.includes('¿Seguro que quieres foldear?')));

    return !!(isChanged || hayModal);
}"""

JS_EXTRAER_ESTADO_DOM = r"""() => {
    try {
        const getCardStr = (c) => {
            if (!c) return '';
            let rank = (c.querySelector('.card-rank')?.innerText || c.querySelector('.card-rank')?.textContent || '').trim();
            let suit = (c.querySelector('.card-suit')?.innerText || c.querySelector('.card-suit')?.textContent || '').trim();

            if (!rank || !suit) {
                const img = c.querySelector('img.card-image, img');
                const src = img ? (img.getAttribute('src') || '') : '';
                const fileMatch = src.match(/\/([a-z0-9]+)\.svg$/i);
                if (fileMatch) {
                    const code = fileMatch[1].toLowerCase();
                    if (code.length >= 2) {
                        const suitChar = code[0];
                        const rankStr = code.substring(1).toUpperCase();
                        const suitMap = { 'c': '♣', 'd': '♦', 'h': '♥', 's': '♠' };
                        if (suitMap[suitChar]) {
                            suit = suitMap[suitChar];
                            rank = rankStr === '10' ? '10' : rankStr;
                        }
                    }
                }
            }
            if (!rank) return '';
            if (rank === '10') rank = '10';
            return (rank + (suit || '')).trim();
        };

        // Mis cartas privadas (Hero)
        const misCartas = Array.from(document.querySelectorAll('.my-player .card:not(.pt-visibility-hidden), .cards-holder-hero .card:not(.pt-visibility-hidden), .my-player .card-image-backup'))
            .map(getCardStr)
            .filter(Boolean);

        // Cartas comunitarias en mesa
        const comunitarias = Array.from(document.querySelectorAll('.community-cards .cardset-community .card:not(.pt-visibility-hidden)'))
            .map(getCardStr)
            .filter(Boolean);

        const bote = (document.querySelector('.total-pot .total-pot-amount, #main-pot:not(.pt-visibility-hidden), .total-pot-amount')?.innerText || '').trim();
        const mainPotText = (document.querySelector('.main-pot-container #main-pot:not(.pt-visibility-hidden) .amount, #main-pot:not(.pt-visibility-hidden)')?.innerText || '').trim();

        const blindsText = (document.querySelector('.blinds-info, .table-blinds, .level-blinds, .blinds')?.innerText || '').trim();
        const blindsMatch = blindsText.match(/(\d+(?:[.,]\d+)?)\s*\/\s*(\d+(?:[.,]\d+)?)/);
        const bigBlindDom = blindsMatch ? blindsMatch[2].replace(',', '') : '';
        const fuerzaMano = (document.querySelector('.hand-strength-text')?.innerText || '').trim();
        const montoInput = document.querySelector('.betting-input, .raise-input')?.value || '0';

        const timerTexto = (document.querySelector('.my-player .countdown-text, .my-player .text-countdown')?.innerText || '').trim();
        const miTimerActivo = !!document.querySelector('.my-player .text-countdown, .my-player .timeout-wrapper');

        const accionesArea = document.querySelector('.actions-area');
        const opcionesAccion = accionesArea
            ? Array.from(accionesArea.querySelectorAll('.action-button, #FOLD, #CHECK, #CALL, #BET, #RAISE, #ALLIN, .action-fold, .action-check, .action-call, .action-raise, .action-allin'))
                .filter(b => b.offsetWidth > 0 || b.offsetHeight > 0 || window.getComputedStyle(b).display !== 'none')
                .map(b => {
                    let id = (b.id || '').toUpperCase();
                    if (!id || id === 'BTN') {
                        const cls = (b.className || '').toLowerCase();
                        const txt = (b.querySelector('.action-title')?.innerText || b.innerText || '').toUpperCase();
                        if (cls.includes('raise') || txt.includes('RAISE') || txt.includes('SUBIR')) id = 'RAISE';
                        else if (cls.includes('bet') || txt.includes('BET') || txt.includes('APOSTAR')) id = 'BET';
                        else if (cls.includes('call') || txt.includes('CALL') || txt.includes('IGUALAR') || txt.includes('PAGAR')) id = 'CALL';
                        else if (cls.includes('check') || txt.includes('CHECK') || txt.includes('PASAR')) id = 'CHECK';
                        else if (cls.includes('fold') || txt.includes('FOLD') || txt.includes('RETIRAR')) id = 'FOLD';
                        else if (cls.includes('allin') || txt.includes('ALL-IN') || txt.includes('ALL IN')) id = 'ALL_IN';
                        else id = 'BTN';
                    }
                    const valor = (b.querySelector('.action-value')?.innerText || '').trim();
                    return `${id}${valor ? ' (' + valor + ')' : ''}`;
                })
            : [];

        const esMiTurno = miTimerActivo || opcionesAccion.length > 0;

        const apuestasRivales = Array.from(document.querySelectorAll('.player-area:not(.my-player) .player-bet .amount, .player-area:not(.my-player) .bet-amount'))
            .filter(a => {
                const parentBet = a.closest('.bet, .player-bet');
                return !parentBet || !parentBet.classList.contains('pt-visibility-hidden');
            })
            .map(a => a.innerText.trim()).filter(Boolean).join(', ');

        const miStack = (document.querySelector('.my-player .player-nameplate .amount, .my-player .player-chips, .my-player .chips-amount, .my-player .stack-amount, .my-player .player-balance')?.innerText || '0').trim();
        const stackRival = Array.from(document.querySelectorAll('.player-area:not(.my-player) .player-nameplate .amount, .player-area:not(.my-player) .player-chips, .player-area:not(.my-player) .chips-amount'))
            .map(el => el.innerText.trim()).filter(Boolean).join(' | ') || '0';

        const jugadoresMesa = Array.from(document.querySelectorAll('.player-area')).map((area, idx) => {
            const isHero = area.classList.contains('my-player');

            // Extraction resistente del nickname
            let nickTarget = area.querySelector('.target, .player-nameplate .nickname, .player-name, .nickname, .username');
            let nick = (nickTarget ? (nickTarget.innerText || nickTarget.textContent || '') : '').trim();

            // Si el nickname vino como timer ("Time: 18") o número puro, extraerlo del ID del elemento amount de fichas (ej. chips-wallhaxor707"-"4 -> wallhaxor707)
            if (!nick || /^(Time|Tiempo)[\s:\n]*\d+/i.test(nick) || /^\d+$/.test(nick)) {
                const amountEl = area.querySelector('.amount, [id*="chips-"]');
                if (amountEl && amountEl.id) {
                    const idMatch = amountEl.id.match(/^chips-([^&"'-]+)/i);
                    if (idMatch && idMatch[1] && !/^(Time|Tiempo)/i.test(idMatch[1])) {
                        nick = idMatch[1].trim();
                    }
                }
            }

            if (isHero && (!nick || /^(Time|Tiempo)[\s:\n]*\d+/i.test(nick))) {
                nick = 'Belnebu';
            } else if (!nick || /^(Time|Tiempo)[\s:\n]*\d+/i.test(nick) || /^\d+$/.test(nick)) {
                nick = `Rival_${idx + 1}`;
            }

            const stackTxt = (area.querySelector('.player-nameplate .amount, .player-chips, .chips-amount, .stack-amount, .player-balance')?.innerText || '0').trim();
            const betEl = area.querySelector('.player-bet .amount, .bet-amount, .chip-amount');
            const parentBetEl = betEl ? betEl.closest('.bet, .player-bet') : null;
            const betVisible = !parentBetEl || !parentBetEl.classList.contains('pt-visibility-hidden');
            const betVal = (betEl && betVisible ? (betEl.innerText || betEl.textContent || '') : '').replace(/[^\d.]/g, '');
            const actionEl = area.querySelector('.player-action, .action-label, .action-text, .bubble, .last-action');
            const actionTxt = (actionEl ? (actionEl.innerText || actionEl.textContent || '') : '').trim();

            // HUD Wings VPIP/AF directamente de pantalla
            const vpipWingsEl = area.querySelector('.hud-wings-value-wrapper:not(.right) .hud-wings-value');
            const afWingsEl = area.querySelector('.hud-wings-value-wrapper.right .hud-wings-value');
            const hudVpip = (vpipWingsEl ? (vpipWingsEl.innerText || vpipWingsEl.textContent || '') : '').trim();
            const hudAf = (afWingsEl ? (afWingsEl.innerText || afWingsEl.textContent || '') : '').trim();

            const cls = (area.className || '').toLowerCase();
            const txtArea = (area.innerText || '').toLowerCase();
            const isFold = cls.includes('fold') || txtArea.includes('fold') || txtArea.includes('retir');
            const isEmpty = txtArea.includes('vacío') || txtArea.includes('empty');
            const estado = isEmpty ? 'VACIO' : (isFold ? 'FOLD' : 'ACTIVO');
            // Dealer button activo si .game-position no tiene .pt-visibility-hidden
            const posContainer = area.querySelector('.game-position:not(.pt-visibility-hidden)');
            const isDealer = !!posContainer && !!posContainer.querySelector('.dealer');

            const isTurnAct = isHero ? esMiTurno : (!!area.querySelector('.turn-to-act-indicator') || !!area.querySelector('.text-countdown, .timeout-wrapper'));

            return {
                seat: idx + 1,
                asiento: idx + 1,
                nickname: nick,
                player_id: nick,
                stack: stackTxt,
                bet: betVal,
                action: actionTxt,
                status: estado,
                estado: estado,
                is_hero: isHero,
                is_dealer: isDealer,
                is_turn: isTurnAct,
                hud_vpip: hudVpip,
                hud_af: hudAf
            };
        }).filter(j => j.estado !== 'VACIO');

        const btnCallEl = accionesArea ? accionesArea.querySelector('#CALL, .action-call') : null;
        const callVal = (btnCallEl?.querySelector('.action-value')?.innerText || '').trim();
        const btnAllinEl = accionesArea ? accionesArea.querySelector('#ALLIN, .action-allin') : null;
        const allinVal = (btnAllinEl?.querySelector('.action-value')?.innerText || '').trim();
        const hayCheckDom = opcionesAccion.includes('CHECK');
        const hayCallDom = opcionesAccion.includes('CALL');
        const hayAllinDom = opcionesAccion.includes('ALL_IN') || opcionesAccion.includes('ALLIN');
        const aIgualar = hayCheckDom ? '0' : (callVal || (hayAllinDom && !hayCallDom ? (allinVal || miStack) : '') || apuestasRivales || '0');

        const modales = [];
        if (document.querySelector('.filter-play-button')) modales.push('Jugar Poker');
        const btnOk = document.querySelector('#general-popup-button-action-OK');
        if (btnOk && (btnOk.offsetWidth > 0 || btnOk.offsetHeight > 0)) modales.push('Aceptar');
        if (document.body && (document.body.textContent.includes('podrías hacer check sin coste alguno') || document.body.textContent.includes('¿Seguro que quieres foldear?'))) {
            modales.push('PopupCheckGratis');
        }
        const textosModal = ['Jugar de nuevo', 'Replay', 'Volver a jugar', 'Aceptar', 'Jugar Poker'];
        for (const t of textosModal) {
            const encontrado = Array.from(document.querySelectorAll('button, a, div, span')).find(
                el => (el.offsetWidth > 0 || el.offsetHeight > 0) && (el.innerText || el.textContent || '').trim() === t
            ) || Array.from(document.querySelectorAll('button, a, div.btn, span')).find(
                el => (el.offsetWidth > 0 || el.offsetHeight > 0) && (el.textContent || '').trim().includes(t)
            );
            if (encontrado && !modales.includes(t)) modales.push(t);
        }

        // Detectar si hemos sido eliminados o la mesa quedó vacía ("Vacío" en nuestro asiento inferior o >=2 asientos vacíos)
        const textoBody = document.body ? (document.body.innerText || '') : '';
        const vecesVacio = (textoBody.match(/Vacío|Empty/g) || []).length;
        const heroAsientoVacio = Array.from(document.querySelectorAll('div, span')).some(el => {
            const t = (el.innerText || '').trim();
            if (t !== 'Vacío' && t !== 'Empty') return false;
            const r = el.getBoundingClientRect();
            return r.width > 10 && r.height > 10 && r.top > 380 && r.left > 350 && r.left < 930;
        });
        const hayGanador1500 = stackRival.includes('1,500') || stackRival.includes('1500') || textoBody.includes('1,500');
        if (!esMiTurno && misCartas.length === 0 && (heroAsientoVacio || vecesVacio >= 2 || (vecesVacio >= 1 && hayGanador1500))) {
            modales.push('MesaFinalizadaVacia');
        }

        let posDom = '';
        const esDealerHero = !!document.querySelector('.my-player .dealer-button, .my-player .dealer-chip, .my-player .dealer-badge, .my-player.dealer, .my-player [class*="dealer"], .seat-hero [class*="dealer"]');
        if (esDealerHero) {
            posDom = 'BTN';
        } else {
            const ciegaHero = parseFloat((apuestasRivales['hero'] || '0').replace(/[^\d.]/g, '')) || 0;
            const bbNum = parseFloat(bigBlindDom.replace(/[^\d.]/g, '')) || 20;
            if (ciegaHero > 0 && ciegaHero <= 0.6 * bbNum) {
                posDom = 'SB';
            } else if (ciegaHero >= 0.8 * bbNum) {
                posDom = 'BB';
            }
        }

        return {
            mis_cartas: misCartas.join(' '),
            cartas_comunitarias: comunitarias.join(' '),
            bote: bote,
            a_igualar: aIgualar,
            mi_stack: miStack,
            stack_rival: stackRival,
            fuerza_mano: fuerzaMano,
            monto_apuesta_input: montoInput,
            opciones_disponibles: opcionesAccion.join(' | '),
            botones_visibles: opcionesAccion.join(' | '),
            modales_visibles: modales.join(' | '),
            es_mi_turno: esMiTurno,
            tiempo_restante: timerTexto,
            apuestas_rivales: apuestasRivales,
            jugadores_mesa: jugadoresMesa,
            big_blind: bigBlindDom,
            posicion: posDom
        };
    } catch(e) {
        return null;
    }
}"""

def esperar_carga_dom(target, timeout_ms: int = 5000):
    try:
        target.wait_for_load_state("domcontentloaded", timeout=timeout_ms)
    except Exception:
        pass

def intentar_click(target, selector: str, descripcion: str, timeout: int = 4000) -> bool:
    selectores = [s.strip() for s in selector.split(",") if s.strip()]
    # Paso rápido (0ms): verificar si alguno de los selectores ya es visible ahora mismo
    for sel in selectores:
        try:
            elem = target.locator(sel).first
            if elem.is_visible():
                elem.wait_for(state="visible", timeout=500)
                elem.click(timeout=1200)
                print(f"✅ [CLIC OK] Clic en {descripcion} realizado.")
                return True
        except Exception:
            pass

    if timeout <= 0:
        return False

    timeout_por_sel = max(300, int(timeout / max(1, len(selectores))))
    for sel in selectores:
        try:
            target.wait_for_selector(sel, state="visible", timeout=timeout_por_sel)

            try:
                elem = target.locator(sel).first
                if elem.is_visible():
                    elem.click(timeout=1200)
                    print(f"✅ [CLIC OK] Clic en {descripcion} realizado.")
                    return True
            except Exception:
                pass

            if not sel.startswith("xpath=") and ":has-text(" not in sel:
                clicked = target.evaluate("""(selector) => {
                    try {
                        const el = document.querySelector(selector);
                        if (el && !el.disabled && el.getAttribute('aria-disabled') !== 'true') {
                            el.scrollIntoView({ block: 'center' });
                            el.dispatchEvent(new MouseEvent('mousedown', { bubbles: true, cancelable: true }));
                            el.dispatchEvent(new MouseEvent('mouseup', { bubbles: true, cancelable: true }));
                            el.click();
                            return true;
                        }
                    } catch (e) {}
                    return false;
                }""", sel)

                if clicked:
                    print(f"⚡ [JS CLICK OK] Clic por evento JS realizado en {descripcion}.")
                    return True
        except Exception:
            continue
    return False


def verificar_accion_reflejada(target, accion: str, timeout_ms: int = 1500) -> bool:
    """Verifica tras un clic que el turno o los botones de acción hayan cambiado en el DOM."""
    if accion not in ("FOLD", "CHECK", "CALL", "RAISE", "ALL_IN"):
        return True
    try:
        target.wait_for_function(
            """() => {
                const btns = Array.from(document.querySelectorAll('#FOLD, #CHECK, #CALL, #BET, #RAISE, #ALLIN, .action-button, [class*="action-button"]'));
                if (btns.length === 0) return true;
                return !btns.some(b => (b.offsetWidth > 0 || b.offsetHeight > 0) && !b.disabled);
            }""",
            timeout=timeout_ms,
        )
        return True
    except Exception:
        return False


def _parse_num_dom(valor: Any, default: float = 0.0) -> float:
    if valor is None:
        return default
    if isinstance(valor, (int, float)):
        return float(valor)
    s = str(valor).strip()
    if not s:
        return default
    if ", " in s:
        s = s.split(", ")[0].strip()
    if re.search(r"\d+,\d{3}", s):
        s = s.replace(",", "")
    else:
        s = s.replace(",", ".")
    m = re.search(r"-?\d+(?:\.\d+)?", s)
    if not m:
        return default
    try:
        return float(m.group(0))
    except Exception:
        return default


def es_mesa_dinero_real(dom_data: Optional[dict], url: str = "") -> bool:
    """Devuelve True si se detectan indicadores de mesa con dinero real (cuando PLAY_MONEY_ONLY está activo)."""
    if not getattr(config, "PLAY_MONEY_ONLY", True):
        return False
    dom_data = dom_data or {}
    campos = [
        str(url or ""),
        str(dom_data.get("bote", "")),
        str(dom_data.get("mi_stack", "")),
        str(dom_data.get("stack_rival", "")),
        str(dom_data.get("a_igualar", "")),
        str(dom_data.get("moneda", "")),
        str(dom_data.get("modales_visibles", "")),
    ]
    texto = " ".join(campos)
    texto_upper = texto.upper()
    if any(pm in texto_upper for pm in ("PLAY MONEY", "PLAY_MONEY", "FICHAS", "FUN MONEY", "PRACTICE")):
        return False
    if any(sym in texto for sym in ("€", "$", "£")):
        return True
    if re.search(r"\b(USD|EUR|GBP|MXN|REAL\s*MONEY|DINERO\s*REAL)\b", texto_upper):
        return True
    return False


def validar_accion(accion: str, monto: Any, estado_dom: Optional[dict]) -> Tuple[str, int, Optional[str]]:
    """
    Valida y normaliza una decisión contra las acciones legales disponibles en el DOM.
    Es el único punto de autorización antes de ejecutar clics en mesa.
    Devuelve (accion_legal, monto_legal, motivo_correccion_o_None).
    """
    dom = estado_dom or {}
    accion_norm = (accion or "WAIT").strip().upper()
    if accion_norm == "ALLIN":
        accion_norm = "ALL_IN"

    botones = str(dom.get("botones_visibles") or dom.get("opciones_disponibles") or "").upper()
    a_igualar = _parse_num_dom(dom.get("a_igualar"), 0.0)
    if a_igualar <= 0 and dom.get("apuestas_rivales"):
        a_igualar = _parse_num_dom(dom.get("apuestas_rivales"), 0.0)
    if a_igualar <= 0 and botones:
        import re
        m_call = re.search(r'CALL\s*\((\d+)\)', botones)
        if m_call:
            a_igualar = float(m_call.group(1))
    mi_stack = _parse_num_dom(dom.get("mi_stack"), 0.0)
    big_blind = _parse_num_dom(dom.get("big_blind"), float(getattr(config, "BIG_BLIND_DEFAULT", 20.0)))
    if big_blind <= 0:
        big_blind = float(getattr(config, "BIG_BLIND_DEFAULT", 20.0))

    min_raise = _parse_num_dom(dom.get("min_raise"), 0.0)
    if min_raise <= 0:
        min_raise = max(2.0 * big_blind, 2.0 * a_igualar) if a_igualar > 0 else big_blind

    hay_check = "CHECK" in botones
    hay_call = "CALL" in botones
    hay_raise = ("RAISE" in botones) or ("BET" in botones)
    hay_allin = ("ALL_IN" in botones) or ("ALLIN" in botones)
    hay_fold = "FOLD" in botones
    es_mano_activa = bool(dom.get("es_mi_turno") or str(dom.get("mis_cartas", "")).strip())

    try:
        monto_val = float(monto or 0)
    except Exception:
        monto_val = 0.0

    # Nunca clicar START_GAME / REPLAY cuando hay mano activa
    if es_mano_activa and accion_norm in ("START_GAME", "REPLAY"):
        if hay_check and a_igualar <= 0:
            return "CHECK", 0, f"Acción ilegal {accion_norm} en mano activa -> CHECK"
        if hay_call:
            return "CALL", 0, f"Acción ilegal {accion_norm} en mano activa -> CALL"
        return "WAIT", 0, f"Acción ilegal {accion_norm} en mano activa -> WAIT"

    if accion_norm in ("WAIT", "START_GAME", "REPLAY"):
        return accion_norm, 0, None

    modo_allin_or_fold = hay_allin and hay_fold and not hay_call and not hay_check and not hay_raise

    # Si hay botones visibles, validar estrictamente la legalidad
    if botones:
        if modo_allin_or_fold:
            if accion_norm in ("CALL", "RAISE", "ALL_IN"):
                return "ALL_IN", 0, "Modo All-in o Fold -> ALL_IN"
            if accion_norm == "FOLD":
                return "FOLD", 0, None
            if accion_norm == "CHECK":
                tier_pre = (dom.get("gto_preflop") or {}).get("tier", "DESCONOCIDO")
                gto_comb = dom.get("gto_combinacion") or {}
                if tier_pre in ("S", "A", "B") or bool(gto_comb.get("es_pareja_en_mano")) or bool(gto_comb.get("coincidencias_mesa")):
                    return "ALL_IN", 0, "Modo All-in o Fold con mano jugable -> ALL_IN"
                return "FOLD", 0, "Modo All-in o Fold con mano débil -> FOLD"

        if accion_norm == "FOLD":
            if a_igualar <= 0 or hay_check:
                return "CHECK", 0, "FOLD innecesario con CHECK gratis -> CHECK"
            if hay_fold:
                return "FOLD", 0, None
            if hay_call:
                return "CALL", 0, "FOLD no disponible -> CALL"
            return "WAIT", 0, "FOLD sin botones legales -> WAIT"

        if accion_norm == "CHECK":
            if hay_check or a_igualar <= 0:
                return "CHECK", 0, None
            if hay_call:
                return "CALL", 0, "CHECK ilegal con apuesta activa -> CALL"
            if hay_fold:
                return "FOLD", 0, "CHECK ilegal con apuesta activa -> FOLD"
            return "WAIT", 0, "CHECK sin botones legales -> WAIT"

        if accion_norm == "CALL":
            if hay_call:
                return "CALL", 0, None
            if hay_check and a_igualar <= 0:
                return "CHECK", 0, "CALL sin apuesta -> CHECK"
            if a_igualar >= mi_stack and mi_stack > 0 and hay_allin:
                return "ALL_IN", 0, "CALL de All-in -> ALL_IN"
            if hay_fold and a_igualar > 0:
                return "FOLD", 0, "CALL no disponible -> FOLD"
            if hay_check:
                return "CHECK", 0, "CALL no disponible -> CHECK"
            return "WAIT", 0, "CALL sin botones legales -> WAIT"

        if accion_norm == "RAISE":
            if hay_raise:
                monto_clamped = monto_val if monto_val > 0 else min_raise
                motivo = None
                if mi_stack > 0:
                    if min_raise > mi_stack:
                        monto_clamped = mi_stack
                        if monto_val != monto_clamped:
                            motivo = f"Monto RAISE ajustado al stack ({int(round(monto_clamped))})"
                    else:
                        if monto_clamped < min_raise:
                            monto_clamped = min_raise
                            motivo = f"Monto RAISE bajo mínimo ({int(round(min_raise))})"
                        elif monto_clamped > mi_stack:
                            monto_clamped = mi_stack
                            motivo = f"Monto RAISE excede stack ({int(round(mi_stack))})"
                else:
                    if monto_clamped < min_raise:
                        monto_clamped = min_raise
                        motivo = f"Monto RAISE bajo mínimo ({int(round(min_raise))})"
                return "RAISE", int(round(monto_clamped)), motivo
            if hay_allin:
                return "ALL_IN", 0, "RAISE no disponible -> ALL_IN"
            if hay_call:
                return "CALL", 0, "RAISE no disponible -> CALL"
            if hay_check and a_igualar <= 0:
                return "CHECK", 0, "RAISE no disponible -> CHECK"
            if hay_fold:
                return "FOLD", 0, "RAISE no disponible -> FOLD"
            return "WAIT", 0, "RAISE sin botones legales -> WAIT"

        if accion_norm == "ALL_IN":
            if hay_allin or hay_raise:
                return "ALL_IN", 0, None
            if hay_call:
                return "CALL", 0, "ALL_IN no disponible -> CALL"
            if hay_check and a_igualar <= 0:
                return "CHECK", 0, "ALL_IN no disponible -> CHECK"
            if hay_fold:
                return "FOLD", 0, "ALL_IN no disponible -> FOLD"
            return "WAIT", 0, "ALL_IN sin botones legales -> WAIT"

    return accion_norm, int(round(max(0.0, monto_val))), None


def resolver_decision_hibrida(
    dom_data: Optional[dict],
    motor_decision: PokerDecision,
    ev_res: Optional[dict],
    llm_decision: Optional[PokerDecision],
    ev_threshold_bb: Optional[float] = None,
) -> Tuple[PokerDecision, dict]:
    """
    En modo híbrido, el motor GTO propone primero; la decisión del LLM solo reemplaza
    a la del motor si es legal y la diferencia de EV en BB es <= ev_threshold_bb.
    Registra siempre ambas decisiones en dom_data['decision_hibrida'].
    """
    umbral = float(
        ev_threshold_bb
        if ev_threshold_bb is not None
        else getattr(config, "HYBRID_EV_DIFF_THRESHOLD_BB", 0.5)
    )
    evs = (ev_res or {}).get("evs") or {}
    motor_acc = (motor_decision.accion or "WAIT").upper()
    ev_motor = float(evs.get(motor_acc, (ev_res or {}).get("ev_bb", 0.0) or 0.0))

    registro: Dict[str, Any] = {
        "propuesta_motor": {"accion": motor_acc, "monto": int(getattr(motor_decision, "monto_apuesta", 0) or 0), "ev_bb": ev_motor},
        "propuesta_llm": None,
        "ev_diff_bb": None,
        "umbral_bb": umbral,
        "aceptada_llm": False,
        "razon": "",
    }

    if not llm_decision or (llm_decision.accion or "").upper() in ("WAIT", "START_GAME", "REPLAY"):
        registro["razon"] = "LLM sin propuesta válida; se mantiene propuesta del motor."
        if isinstance(dom_data, dict):
            dom_data["decision_hibrida"] = registro
        return motor_decision, registro

    llm_raw_acc = (llm_decision.accion or "WAIT").upper()
    llm_raw_monto = getattr(llm_decision, "monto_apuesta", 0) or 0
    llm_legal_acc, llm_legal_monto, motivo_ilegal = validar_accion(llm_raw_acc, llm_raw_monto, dom_data)
    es_legal = (motivo_ilegal is None) and (llm_legal_acc in evs if evs else True)
    ev_llm = float(evs.get(llm_legal_acc, ev_motor)) if llm_legal_acc in evs else None
    ev_diff = abs(ev_motor - ev_llm) if ev_llm is not None else None

    registro["propuesta_llm"] = {
        "accion_original": llm_raw_acc,
        "accion_legal": llm_legal_acc,
        "monto": llm_legal_monto,
        "ev_bb": ev_llm,
        "es_legal": es_legal,
        "motivo_ilegal": motivo_ilegal,
    }
    registro["ev_diff_bb"] = round(ev_diff, 3) if ev_diff is not None else None

    if es_legal and ev_diff is not None and ev_diff <= umbral:
        registro["aceptada_llm"] = True
        registro["razon"] = f"LLM aceptado (ΔEV={ev_diff:.2f}BB <= {umbral:.2f}BB)."
        final_dec = PokerDecision(
            evaluacion=f"[Híbrido LLM] {llm_decision.evaluacion}",
            estrategia_competitiva=llm_decision.estrategia_competitiva,
            estado_pantalla=llm_decision.estado_pantalla,
            es_mi_turno=True,
            accion=llm_legal_acc,
            monto_apuesta=llm_legal_monto,
            confianza=llm_decision.confianza,
        )
    else:
        registro["razon"] = (
            f"Motor mantenido (ilegal={not es_legal}, ΔEV={ev_diff if ev_diff is not None else 'N/A'}BB > {umbral:.2f}BB)."
        )
        final_dec = motor_decision

    if isinstance(dom_data, dict):
        dom_data["decision_hibrida"] = registro
    return final_dec, registro


def actualizar_dom_data_conservando_contexto(
    dom_data: dict,
    nuevo_dom: Optional[dict],
    tracker_obj: Any = None,
    profiler_obj: Any = None,
    memory_obj: Any = None,
) -> dict:
    """
    Actualiza dom_data sin perder historial_mano, perfil_rival, perfiles_oponentes ni memoria_errores,
    y recalcula los campos derivados GTO.
    """
    if nuevo_dom:
        dom_data.update(nuevo_dom)

    if tracker_obj is not None:
        dom_data["historial_mano"] = tracker_obj.obtener_contexto_completo()
        mis_cartas = formatear_cartas_canonicas(dom_data.get("mis_cartas", "") or " ".join(getattr(tracker_obj, "my_cards", []) or []))
        es_preflop = (
            getattr(tracker_obj, "current_street", "PREFLOP") == "PREFLOP"
            or getattr(tracker_obj, "current_round_no", 1) <= 1
        )
        if es_preflop:
            comunitarias = ""
            dom_data["cartas_comunitarias"] = ""
            if hasattr(tracker_obj, "community_cards"):
                tracker_obj.community_cards.clear()
        else:
            comunitarias = formatear_cartas_canonicas(dom_data.get("cartas_comunitarias", "") or " ".join(getattr(tracker_obj, "community_cards", []) or []))
        bote = dom_data.get("bote", "") or getattr(tracker_obj, "last_pot", 0) or 0
    else:
        mis_cartas = formatear_cartas_canonicas(dom_data.get("mis_cartas", ""))
        comunitarias = formatear_cartas_canonicas(dom_data.get("cartas_comunitarias", ""))
        bote = dom_data.get("bote", 0)

    dom_data["mis_cartas"] = mis_cartas
    dom_data["cartas_comunitarias"] = comunitarias
    dom_data["bote"] = bote

    if profiler_obj is not None and tracker_obj is not None:
        jugadores = dom_data.get("jugadores_mesa") or []
        if isinstance(jugadores, list) and jugadores:
            bb_val = _parse_num_dom(dom_data.get("big_blind"), float(getattr(config, "BIG_BLIND_DEFAULT", 20.0))) or 20.0
            perfiles_multi = []
            for j in jugadores:
                if not isinstance(j, dict):
                    continue
                raw_nick = j.get("nickname", "")
                raw_pid = j.get("player_id")
                nick_j = sanitizar_nickname(raw_nick) or f"Rival_{j.get('asiento', 1)}"
                pid_j = sanitizar_nickname(raw_pid) if raw_pid else nick_j
                if not pid_j or es_nickname_invalido(pid_j):
                    pid_j = nick_j
                if not getattr(tracker_obj, "opp_nick", ""):
                    tracker_obj.opp_nick = nick_j
                if not getattr(tracker_obj, "opp_id", ""):
                    tracker_obj.opp_id = pid_j
                if pid_j:
                    tracker_obj.active_opponents[pid_j] = nick_j

                # Sincronización y atribución de acciones por delta DOM
                bet_dom = _parse_num_dom(j.get("bet"), 0.0)
                estado_dom = str(j.get("estado", "ACTIVO")).upper()
                action_dom = str(j.get("action", "")).upper()
                prev_state = tracker_obj.prev_player_states.get(pid_j or nick_j, {})
                prev_bet = float(prev_state.get("bet", 0.0))
                prev_st = str(prev_state.get("status", "ACTIVE")).upper()
                last_act = prev_state.get("last_action", "")

                calle = getattr(tracker_obj, "current_street", "PREFLOP")
                hand_id = getattr(tracker_obj, "hand_number", 1)

                registered_action = None
                if estado_dom in ("FOLD", "FOLDED") and prev_st not in ("FOLD", "FOLDED"):
                    registered_action = "FOLD"
                elif bet_dom > prev_bet:
                    registered_action = "RAISE" if (calle == "PREFLOP" or prev_bet > 0) else "BET"
                elif ("CALL" in action_dom or "IGUALAR" in action_dom or "PAGAR" in action_dom) and last_act != "CALL":
                    registered_action = "CALL"
                elif "CHECK" in action_dom and calle != "PREFLOP" and last_act != "CHECK":
                    registered_action = "CHECK"
                elif bet_dom > 0 and abs(bet_dom - prev_bet) < 0.001 and last_act not in ("CALL", "BET", "RAISE", "CHECK") and prev_st not in ("FOLD", "FOLDED"):
                    registered_action = "CALL"

                if registered_action:
                    profiler_obj.registrar_accion_rival(
                        player_id=pid_j,
                        nickname=nick_j,
                        accion=registered_action,
                        calle=calle,
                        seat=j.get("asiento"),
                        status=estado_dom,
                        hand_id=hand_id,
                    )

                tracker_obj.prev_player_states[pid_j or nick_j] = {
                    "bet": bet_dom,
                    "status": estado_dom,
                    "nickname": nick_j,
                    "last_action": registered_action or last_act,
                }

                prof_j = dict(profiler_obj.obtener_perfil_explotativo(pid_j, nick_j))
                stack_chips = _parse_num_dom(j.get("stack"), 0.0)
                prof_j["asiento"] = j.get("asiento", len(perfiles_multi) + 1)
                prof_j["estado"] = j.get("estado", "ACTIVO")
                prof_j["stack"] = stack_chips
                prof_j["stack_bb"] = round(stack_chips / bb_val, 1) if bb_val > 0 else 0.0
                perfiles_multi.append(prof_j)
            if perfiles_multi:
                dom_data["perfiles_oponentes"] = perfiles_multi

        dom_data["perfil_rival"] = profiler_obj.obtener_perfil_explotativo(
            getattr(tracker_obj, "opp_id", ""),
            getattr(tracker_obj, "opp_nick", ""),
        )

    gto_math = calcular_pot_odds_y_sizing(
        bote,
        dom_data.get("a_igualar"),
        dom_data.get("mi_stack"),
        big_blind=_parse_num_dom(dom_data.get("big_blind"), float(getattr(config, "BIG_BLIND_DEFAULT", 20.0))) or None,
        stack_rival_str=dom_data.get("stack_rival"),
    )
    posicion = getattr(tracker_obj, "position_name", "DESCONOCIDA") if tracker_obj is not None else "DESCONOCIDA"
    if not posicion or posicion == "DESCONOCIDA":
        posicion = str(dom_data.get("posicion") or "DESCONOCIDA").upper()

    if not posicion or posicion == "DESCONOCIDA":
        if getattr(tracker_obj, "is_dealer", False) or dom_data.get("posicion") == "BTN":
            posicion = "BTN"
        elif dom_data.get("posicion") in ("SB", "BB"):
            posicion = dom_data.get("posicion")
        else:
            bb = _parse_num_dom(dom_data.get("big_blind"), float(getattr(config, "BIG_BLIND_DEFAULT", 20.0))) or 20.0
            a_ig = _parse_num_dom(dom_data.get("a_igualar"), 0.0)
            bote_f = _parse_num_dom(dom_data.get("bote"), 0.0)
            opciones_str = str(dom_data.get("opciones_disponibles", "")).upper()
            if not comunitarias and "CHECK" in opciones_str and a_ig == 0.0:
                posicion = "BB"
            elif not comunitarias and (abs(a_ig - 0.5 * bb) < 0.2 * bb or abs(a_ig - 1.5 * bb) < 0.2 * bb):
                posicion = "SB"
            elif not comunitarias and bote_f <= 1.6 * bb and abs(a_ig - bb) < 0.2 * bb:
                posicion = "BTN"
            elif not comunitarias and abs(a_ig - bb) < 0.2 * bb and bote_f >= 2.0 * bb:
                posicion = "BB"
            elif getattr(tracker_obj, "is_dealer", False):
                posicion = "BTN"
            else:
                posicion = "BTN"

    if tracker_obj is not None:
        tracker_obj.position_name = posicion
        tracker_obj.is_dealer = (posicion == "BTN")
    dom_data["posicion"] = posicion
    a_igualar_bb = float(gto_math.get("a_igualar_bb") or 0.0)
    situacion = dom_data.get("situacion") or (
        "vs_allin"
        if a_igualar_bb >= max(8.0, float(gto_math.get("stack_efectivo_bb") or 20.0) * 0.7)
        else ("vs_raise" if a_igualar_bb > 1.5 else "open")
    )

    dom_data["gto_math"] = gto_math
    dom_data["gto_preflop"] = clasificar_mano_preflop(
        mis_cartas,
        posicion=posicion,
        stack_efectivo_bb=float(gto_math.get("stack_efectivo_bb") or 25.0),
        situacion=situacion,
    )
    dom_data["gto_textura"] = analizar_textura_mesa_y_blockers(mis_cartas, comunitarias)
    dom_data["gto_frecuencia"] = generar_frecuencia_mixta_gto()
    dom_data["gto_combinacion"] = evaluar_combinacion_mano_y_mesa(mis_cartas, comunitarias)

    if memory_obj is not None:
        tier = dom_data["gto_preflop"].get("tier", "DESCONOCIDO")
        textura = dom_data["gto_textura"].get("textura_mesa", "DESCONOCIDA")
        arquetipo = (dom_data.get("perfil_rival") or {}).get("estilo", "Desconocido")
        calle_act = getattr(tracker_obj, "current_street", "") if tracker_obj is not None else str(dom_data.get("street") or "PREFLOP")
        try:
            dom_data["memoria_errores"] = memory_obj.buscar_errores_similares(
                hand_tier=tier,
                board_texture=textura,
                opp_archetype=arquetipo,
                limit=2,
                street=calle_act,
                position=posicion,
            )
        except Exception:
            dom_data.setdefault("memoria_errores", [])

        try:
            from analytics_db import AnalyticsStorage
            storage = getattr(analytics_logger, "storage", None) if 'analytics_logger' in globals() and analytics_logger is not None else AnalyticsStorage()
            dom_data["patrones_decisiones_live"] = storage.consultar_patrones_decisiones_live(
                street=calle_act,
                position=posicion,
                limit=3,
            )
        except Exception:
            dom_data.setdefault("patrones_decisiones_live", {})

    return dom_data

def hacer_login(page):
    print(f"🌐 Navegando a la página principal: {config.LOGIN_URL}")
    page.goto(config.LOGIN_URL, wait_until="domcontentloaded")
    esperar_carga_dom(page)
    page.wait_for_timeout(1000)

    print("🔑 Paso 1: Verificando estado de sesión...")
    click_acceder = intentar_click(page, config.SELECTORS["btn_acceder"], "Botón Acceder", timeout=4000)
    if not click_acceder:
        print("✅ Sesión activa detectada (cookies/storage persistentes cargados). Omitiendo login.")
        return

    try:
        page.wait_for_selector(config.SELECTORS["username_input"], state="visible", timeout=15000)
        page.wait_for_timeout(800)
    except Exception:
        pass

    try:
        print(f"👤 Paso 2: Ingresando usuario: {config.GAME_USER}")
        user_input = page.locator(config.SELECTORS["username_input"]).first
        user_input.click()
        user_input.fill(config.GAME_USER)
        page.wait_for_timeout(500)

        print("🔒 Paso 3: Ingresando contraseña...")
        pw_input = page.locator(config.SELECTORS["password_input"]).first
        pw_input.click()
        pw_input.fill(config.GAME_PASSWORD)
        page.wait_for_timeout(800)

        print("🚀 Paso 4: Iniciando sesión (Envío de formulario + Clic)...")
        pw_input.press("Enter")
        page.wait_for_timeout(1200)

        modal_abierto = page.evaluate("() => !!document.querySelector('.modal-content, .Auth-form')")
        if modal_abierto:
            intentar_click(page, config.SELECTORS["login_submit"], "Botón Iniciar Sesión", timeout=4000)

        print("⏳ Esperando a que el inicio de sesión se autentique completamente...")
        page.wait_for_timeout(3000)
        esperar_carga_dom(page, timeout_ms=8000)
    except Exception as e:
        print(f"ℹ️ Nota durante login: {e}")

def es_mesa_activa_o_turno(page) -> bool:
    if tracker.http_turn_signal:
        return True
    try:
        pages = page.context.pages
        for p in pages:
            if p.is_closed():
                continue
            for frame in p.frames:
                try:
                    activa = frame.evaluate("""() => {
                        const esVisible = (el) => !!(el && (el.offsetWidth > 0 || el.offsetHeight > 0) && window.getComputedStyle(el).display !== 'none');
                        // Si está visible el botón de lobby o fin de partida, la mesa NO está en mano activa
                        const btnPlay = document.querySelector('.filter-play-button');
                        if (esVisible(btnPlay)) return false;
                        const finPartida = Array.from(document.querySelectorAll('button, div.btn, span')).some(
                            el => esVisible(el) && ((el.textContent || '').includes('Jugar de nuevo') || (el.textContent || '').includes('Jugar Poker'))
                        );
                        if (finPartida) return false;

                        const miTimer = document.querySelector('.my-player .text-countdown, .my-player .timeout-wrapper, .my-player .countdown-text');
                        if (esVisible(miTimer)) return true;

                        const botonesAccion = Array.from(document.querySelectorAll('#FOLD, #CHECK, #CALL, #BET, #RAISE, .actions-area .action-button'));
                        if (botonesAccion.some(esVisible)) return true;

                        const cartasVisibles = Array.from(document.querySelectorAll('.my-player .card-image-backup .card-rank'));
                        return cartasVisibles.some(el => esVisible(el) && (el.innerText || '').trim().length > 0);
                    }""")
                    if activa:
                        return True
                except Exception:
                    pass
    except Exception:
        pass
    return False

def navegar_al_juego(page):
    print("\n🎰 --- VERIFICANDO ESTADO DE MESA Y LOBBY ---")

    if es_mesa_activa_o_turno(page):
        print("🎮 ¡RECONEXIÓN A PARTIDA EN CURSO DETECTADA! (Mesa activa o my_turn.mp3). Omitiendo pasos de navegación y saltando directamente al bucle de juego...")
        return page

    try:
        # Verificar si ya existe una pestaña con el cliente de iPoker abierto (games.codere.mx / poker/web)
        cliente_poker_abierto = False
        for p in reversed(page.context.pages):
            if not p.is_closed() and any(k in (p.url or "") for k in ["games.codere", "poker/web", "WebLauncher", "hostedMode"]):
                page = p
                page.bring_to_front()
                cliente_poker_abierto = True
                break

        if not cliente_poker_abierto:
            print("🎰 Paso 1: Haciendo clic en la sección de Poker...")
            if es_mesa_activa_o_turno(page):
                print("🎮 ¡Reconexión a mesa activa detectada! Saltando directamente a la mesa de juego...")
                return page

            if not intentar_click(page, config.SELECTORS["poker_nav_link"], "Enlace menú Poker", timeout=8000):
                print("⚠️ No se pudo encontrar el enlace de Poker. Reintentando...")
                page.reload()
                esperar_carga_dom(page)
                intentar_click(page, config.SELECTORS["poker_nav_link"], "Enlace menú Poker (Reintento)", timeout=5000)

            esperar_carga_dom(page)

            if es_mesa_activa_o_turno(page):
                return page

            print("🎰 Paso 2: Haciendo clic en Abrir Poker (#pokerPageOpen)...")
            if not intentar_click(page, config.SELECTORS["btn_open_poker"], "Botón Abrir Poker", timeout=10000):
                print("⏳ Botón Abrir Poker no visible aún, esperando un poco más...")
                intentar_click(page, config.SELECTORS["btn_open_poker"], "Botón Abrir Poker (Reintento)", timeout=5000)

            esperar_carga_dom(page)

            if len(page.context.pages) > 1:
                print("📑 Se detectó una nueva pestaña. Cambiando contexto al juego...")
                page = page.context.pages[-1]
                page.bring_to_front()
                esperar_carga_dom(page)
        else:
            print("⚡ Pestaña del cliente iPoker ya abierta. Saltando directamente al lobby de torneos (Paso 3)...")

        if es_mesa_activa_o_turno(page):
            print("✨ ¡Mesa o turno detectado tras abrir Poker! Cancelando clics del lobby.")
            return page

        print("🎰 Paso 3: Esperando a que la interfaz cargue el contenedor de partidas...")
        try:
            page.wait_for_selector(".home-game-categories-widget, .game-categories-widget-wrapper", state="visible", timeout=8000)
        except Exception:
            pass

        cash_game_encontrado = False
        intentos_cash = 0

        while intentos_cash < 10 and not cash_game_encontrado:
            if es_mesa_activa_o_turno(page):
                print("⚡ ¡Turno activo de juego detectado! Cancelando espera de Cash Game e ingresando a la mesa...")
                return page

            intentos_cash += 1
            if intentar_click(page, config.SELECTORS["select_game_card"], "Categoría Cash Game (.game-category-tile.cash-game)", timeout=1500):
                cash_game_encontrado = True
                break

            for frame in page.frames:
                try:
                    if intentar_click(frame, config.SELECTORS["select_game_card"], "Categoría Cash Game (iFrame)", timeout=0):
                        cash_game_encontrado = True
                        break
                except Exception:
                    pass

            if not cash_game_encontrado:
                page.wait_for_timeout(500)

        if es_mesa_activa_o_turno(page):
            return page

        if cash_game_encontrado:
            print("✅ Categoría Cash Game (Texas Hold'em) seleccionada con éxito.")
            esperar_carga_dom(page)
        else:
            print("⚠️ Cash Game no encontrado tras reintentos. Verificando si el botón de juego está visible directamente...")
            if intentar_click(page, config.SELECTORS["start_match_btn"], "Botón Jugar Poker (Directo)", timeout=3000):
                print("✨ ¡Botón Jugar Poker encontrado directamente sin pasar por Cash Game!")
                esperar_carga_dom(page)
                return page

        print("🎰 Paso 4: Haciendo clic en 'Jugar Poker' para iniciar la partida...")
        jugar_cliqueado = False
        intentos_jugar = 0

        while intentos_jugar < 6 and not jugar_cliqueado:
            if es_mesa_activa_o_turno(page):
                print("⚡ ¡Turno activo detectado! Cancelando clics del lobby...")
                return page

            intentos_jugar += 1
            if intentar_click(page, config.SELECTORS["start_match_btn"], "Botón Jugar Poker", timeout=1500):
                jugar_cliqueado = True
                break

            for frame in page.frames:
                try:
                    if intentar_click(frame, config.SELECTORS["start_match_btn"], "Botón Jugar Poker (iFrame)", timeout=0):
                        jugar_cliqueado = True
                        break
                except Exception:
                    pass

            if not jugar_cliqueado:
                page.wait_for_timeout(500)

        if jugar_cliqueado:
            esperar_carga_dom(page)
            print("✅ Navegación al juego completada. Entrando al bucle de juego reactivo.\n")
        else:
            # Si estamos atrapados en una vista de mesa finalizada ("Vacío") donde no hay lobby, abrir el selector con el botón '+' superior derecho o desde la pestaña principal
            print("🔄 No se encontró el botón en la vista actual (posible mesa finalizada 'Vacío'). Abriendo selector de mesas...")
            salir_de_mesa_vacia_o_finalizada(page, page, page.context)
            print("ℹ️ Entrando al bucle reactivo de juego.\n")

    except Exception as e:
        print(f"⚠️ Nota durante la navegación al juego: {e}")

    return page

def esperar_sincronizacion_turno(target, timeout_max_ms: int = 1500) -> bool:
    try:
        target.wait_for_function(JS_VERIFICAR_TURNO_O_MODAL, timeout=timeout_max_ms)
        return True
    except PlaywrightTimeoutError:
        return False

def obtener_target_mesa(page):
    try:
        pages = page.context.pages
        for p in reversed(pages):
            for frame in p.frames:
                try:
                    is_poker = frame.evaluate("""() => {
                        return !!(document.querySelector('.my-player, .community-cards, #FOLD, #CHECK, #CALL, #BET, #RAISE, .actions-area'));
                    }""")
                    if is_poker:
                        return frame
                except Exception:
                    pass
    except Exception:
        pass
    return page

def consultar_gemini_con_timeout(
    agent,
    screenshot_bytes,
    dom_data,
    timeout_seg: float = 13.0,
    decision_uid: str = "",
    hand_uid: str = "",
) -> Optional[PokerDecision]:
    """
    Ejecuta la consulta a Gemini AI en segundo plano con un timeout de seguridad.
    Mantiene margen antes del límite de turno de la mesa.
    """
    try:
        future = bg_executor.submit(
            agent.analizar_pantalla,
            screenshot_bytes,
            dom_data,
            decision_uid=decision_uid,
            hand_uid=hand_uid,
        )
        return future.result(timeout=timeout_seg)
    except concurrent.futures.TimeoutError:
        print(f"⏱️ [TIMEOUT GUARDIA {timeout_seg}s] Gemini no respondió a tiempo. Ejecutando respuesta GTO rápida (0ms)...")
        return None
    except Exception as e:
        print(f"⚠️ Error en consulta asíncrona: {e}")
        return None

def resolver_popup_fold_check_gratis(target) -> bool:
    """
    Detecta y resuelve al instante el recuadro:
    'Has elegido fold, pero podrías hacer check sin coste alguno. ¿Seguro que quieres foldear?'
    haciendo clic en el botón 'Check' del diálogo emergente.
    """
    try:
        resuelto = target.evaluate("""() => {
            if (!document.body) return false;
            const txt = document.body.textContent || '';
            if (!txt.includes('podrías hacer check sin coste alguno') && !txt.includes('¿Seguro que quieres foldear?')) {
                return false;
            }
            // Buscar el botón 'Check' dentro del diálogo/modal (que no sea el #CHECK de la barra inferior)
            const candidatos = Array.from(document.querySelectorAll('button, div[role="button"], span, a'))
                .filter(el => {
                    if (el.id === 'CHECK' || el.closest('.actions-area')) return false;
                    const rect = el.getBoundingClientRect();
                    const t = (el.innerText || el.textContent || '').trim();
                    return rect.width > 20 && rect.height > 15 && (t === 'Check' || t === 'Pasar');
                });
            if (candidatos.length > 0) {
                const btn = candidatos[candidatos.length - 1];
                btn.dispatchEvent(new MouseEvent('mousedown', { bubbles: true, cancelable: true }));
                btn.dispatchEvent(new MouseEvent('mouseup', { bubbles: true, cancelable: true }));
                btn.click();
                return true;
            }
            return false;
        }""")
        if resuelto:
            print("🛡️ [POPUP RESUELTO] Recuadro '¿Seguro que quieres foldear?' detectado -> Clic automático en 'Check' gratis.")
            return True
    except Exception:
        pass
    return False

def clicar_texto_modal_universal(target, textos: list[str], descripcion: str = "Modal", solo_modal: bool = False) -> bool:
    """
    Busca cualquier elemento visible (button, div, span, a) cuyo texto coincida con alguno de `textos`
    y dispara eventos reales de ratón + click(). Funciona aunque el botón en iPoker sea un <div> o <span>.
    """
    try:
        clicado = target.evaluate("""({textos, solo_modal}) => {
            if (!document.body) return false;
            const elementos = Array.from(document.querySelectorAll('button, div, span, a, [role="button"]'));
            // Preferir coincidencia exacta en el nodo más interno visible
            for (const t of textos) {
                const exactos = elementos.filter(el => {
                    if (solo_modal && el.closest('.actions-area')) return false;
                    const r = el.getBoundingClientRect();
                    const txt = (el.innerText || el.textContent || '').trim();
                    return r.width > 15 && r.height > 12 && r.width < 500 && r.height < 250 && txt.toLowerCase() === t.toLowerCase();
                });
                if (exactos.length > 0) {
                    const btn = exactos[exactos.length - 1];
                    btn.dispatchEvent(new MouseEvent('mousedown', { bubbles: true, cancelable: true }));
                    btn.dispatchEvent(new MouseEvent('mouseup', { bubbles: true, cancelable: true }));
                    btn.click();
                    return true;
                }
            }
            // Coincidencia parcial (ej. "Raise a 60" coincide con "raise a" o "raise")
            for (const t of textos) {
                const parciales = elementos.filter(el => {
                    if (solo_modal && el.closest('.actions-area')) return false;
                    const r = el.getBoundingClientRect();
                    const txt = (el.innerText || el.textContent || '').trim();
                    return r.width > 15 && r.height > 12 && r.width < 500 && r.height < 250 && txt.toLowerCase().includes(t.toLowerCase());
                });
                if (parciales.length > 0) {
                    // Seleccionar el nodo más interno/específico que contiene el texto
                    const btn = parciales.sort((a, b) => (a.clientWidth * a.clientHeight) - (b.clientWidth * b.clientHeight))[0];
                    btn.dispatchEvent(new MouseEvent('mousedown', { bubbles: true, cancelable: true }));
                    btn.dispatchEvent(new MouseEvent('mouseup', { bubbles: true, cancelable: true }));
                    btn.click();
                    return true;
                }
            }
            return false;
        }""", {"textos": textos, "solo_modal": solo_modal})
        if clicado:
            print(f"✅ [CLIC DE TEXTO OK] Clic realizado en '{descripcion}'.")
            return True
    except Exception:
        pass
    return False

def salir_de_mesa_vacia_o_finalizada(target_mesa, page, context) -> bool:
    """
    Sale de una mesa finalizada ('Vacío') donde ya fuimos eliminados o terminó el torneo:
    1. Intenta pulsar 'Jugar de nuevo' / 'Replay' / 'Aceptar' si hay un modal oculto.
    2. Pulsa el icono '+' (Añadir mesa / Lobby) en la esquina superior derecha de la mesa iPoker.
    3. Si tras abrir el panel está disponible Twister / Jugar Poker, inicia nueva partida.
    4. Si sigue bloqueado en la mesa vacía, reabre desde la pestaña principal de Codere (#pokerPageOpen).
    """
    try:
        if clicar_texto_modal_universal(target_mesa, ["Jugar de nuevo", "Replay", "Volver a jugar", "Jugar Poker"], "Jugar de nuevo / Iniciar"):
            time.sleep(1.5)
            return True

        owner_page = getattr(target_mesa, "page", page)
        print("🚪 [MESA VACÍA / ELIMINADO] Saliendo de la mesa finalizada hacia el selector de torneos...")

        # 1. Intentar clic en el botón '+' (Añadir mesa / Lobby) en la esquina superior derecha de iPoker
        clic_mas = target_mesa.evaluate("""() => {
            const candidatos = Array.from(document.querySelectorAll('button, div, span, a, svg, img')).filter(el => {
                const r = el.getBoundingClientRect();
                const cls = (el.className && typeof el.className === 'string') ? el.className.toLowerCase() : '';
                const id = (el.id || '').toLowerCase();
                if (cls.includes('add') || cls.includes('lobby') || cls.includes('multi') || id.includes('add') || id.includes('lobby')) {
                    return r.width > 12 && r.height > 12 && r.top < 150;
                }
                // Esquina superior derecha (icono '+' de añadir mesa en iPoker 1280x720)
                return r.width >= 18 && r.width <= 90 && r.height >= 18 && r.height <= 90 && r.top >= 10 && r.top <= 130 && r.left >= 1150;
            });
            if (candidatos.length > 0) {
                const btn = candidatos[candidatos.length - 1];
                btn.dispatchEvent(new MouseEvent('mousedown', { bubbles: true, cancelable: true }));
                btn.dispatchEvent(new MouseEvent('mouseup', { bubbles: true, cancelable: true }));
                btn.click();
                return true;
            }
            return false;
        }""")

        if not clic_mas and not owner_page.is_closed():
            try:
                owner_page.mouse.click(1240, 62)
            except Exception:
                pass

        time.sleep(1.2)

        # Verificar si apareció la tarjeta Cash Game o el botón Jugar Poker tras pulsar '+'
        if intentar_click(target_mesa, config.SELECTORS["select_game_card"], "Categoría Cash Game (tras salir de mesa)", timeout=1200):
            time.sleep(0.6)
        if intentar_click(target_mesa, config.SELECTORS["start_match_btn"], "Botón Jugar Poker (tras salir de mesa)", timeout=1500):
            return True
        if clicar_texto_modal_universal(target_mesa, ["Jugar Poker", "Jugar de nuevo", "Jugar"], "Iniciar Partida"):
            return True

        # 2. Respaldo definitivo: si sigue en la mesa vacía y tenemos la pestaña principal de Codere, reabrir desde ahí
        paginas_abiertas = [p for p in context.pages if not p.is_closed()]
        if len(paginas_abiertas) > 1:
            lobby_main = paginas_abiertas[0]
            if owner_page != lobby_main:
                print("🔄 Cerrando pestaña de mesa finalizada y relanzando desde el lobby principal de Codere...")
                try:
                    owner_page.close()
                except Exception:
                    pass
                lobby_main.bring_to_front()
                intentar_click(lobby_main, config.SELECTORS["btn_open_poker"], "Botón Abrir Poker (Relanzamiento)", timeout=4000)
                time.sleep(2.0)
                return True
    except Exception as e:
        print(f"ℹ️ Nota al salir de mesa vacía: {e}")
    return False

import random

def aplicar_delay_humanizado(accion: str, dom_data: Optional[dict] = None, tiempo_ia_seg: float = 0.0) -> float:
    """
    Aplica una pausa calculada y ágil para simular tiempo de reflexión humano
    evitando desincronización y timeouts en la mesa.
    """
    if os.environ.get("PYTEST_CURRENT_TEST"):
        return 0.01

    dom = dom_data or {}
    street = str((dom.get("gto_preflop") or {}).get("situacion") or dom.get("street") or "PREFLOP").upper()
    tier = str((dom.get("gto_preflop") or {}).get("tier") or "DESCONOCIDO").upper()

    # Tiempos naturales y ágiles (0.8s - 3.2s) para mantener perfecta sincronía con la mesa
    if accion == "FOLD":
        target_mean = 1.2 if tier in ("BASURA", "C") else 1.6
        sigma = 0.2
    elif accion == "CHECK":
        target_mean = 1.1
        sigma = 0.2
    elif accion == "CALL":
        target_mean = 1.5
        sigma = 0.25
    elif accion in ("RAISE", "BET"):
        target_mean = 1.9
        sigma = 0.3
    elif accion in ("ALL_IN", "ALLIN"):
        target_mean = 2.4
        sigma = 0.35
    else:
        target_mean = 1.4
        sigma = 0.25

    duracion_objetivo = random.gauss(target_mean, sigma)
    duracion_objetivo = max(0.8, min(3.5, duracion_objetivo))

    pausa_adicional = duracion_objetivo - max(0.0, tiempo_ia_seg)
    pausa_final = max(0.3, pausa_adicional)

    # Guardia de tiempo restante del timebank de iPoker
    tiempo_mesa_str = str(dom.get("tiempo_restante", "")).strip()
    m_tiempo = re.search(r"(\d+)", tiempo_mesa_str)
    if m_tiempo:
        tiempo_mesa = float(m_tiempo.group(1))
        if tiempo_mesa > 2.0:
            pausa_final = min(pausa_final, tiempo_mesa - 1.0)

    pausa_final = round(max(0.1, pausa_final), 2)

    # Dormir en incrementos cortos para mantener sincronía y permitir verificación
    elapsed = 0.0
    while elapsed < pausa_final:
        chunk = min(0.2, pausa_final - elapsed)
        time.sleep(chunk)
        elapsed += chunk
        if hasattr(tracker, "http_turn_signal") and not tracker.http_turn_signal and accion not in ("REPLAY", "START_GAME"):
            break

    return elapsed

def ejecutar_accion(target, decision, dom_data: Optional[dict] = None, tiempo_ia_seg: float = 0.0) -> str:
    target_url = ""
    try:
        u = getattr(target, "url", "")
        target_url = u() if callable(u) else str(u or "")
    except Exception:
        target_url = ""

    if getattr(config, "PLAY_MONEY_ONLY", True) and es_mesa_dinero_real(dom_data, target_url):
        print("⛔ [SEGURIDAD] Bloqueada ejecución en mesa de dinero real (PLAY_MONEY_ONLY=true)")
        return "WAIT"



    monto_raw = getattr(decision, "monto_apuesta", 0) or getattr(decision, "monto", 0) or 0
    accion, monto_legal, motivo_correccion = validar_accion(decision.accion, monto_raw, dom_data)
    if motivo_correccion:
        print(f"🛡️ [VALIDACIÓN LEGALIDAD] {motivo_correccion}")
        if isinstance(dom_data, dict):
            dom_data["correccion_legalidad"] = motivo_correccion

    accion_ejecutada = accion
    print(f"⚡ [ACCION IA]: {accion} | Estado: [{decision.estado_pantalla}] | Evaluacion: {decision.evaluacion}")
    tracker.hero_just_acted_ts = time.time()

    # Humanizar tiempos de respuesta (pausa realista pre-clic)
    if accion in ("FOLD", "CHECK", "CALL", "RAISE", "BET", "ALL_IN", "ALLIN"):
        delay_sec = aplicar_delay_humanizado(accion, dom_data=dom_data, tiempo_ia_seg=tiempo_ia_seg)
        print(f"🧠 [HUMAN TIMING] Reflexión humana: {delay_sec:.2f}s (IA previa: {tiempo_ia_seg:.2f}s | Turno total: {tiempo_ia_seg + delay_sec:.2f}s)")

    btn_fold = config.SELECTORS.get("btn_fold", config.SELECTORS["fold"])
    btn_check = config.SELECTORS.get("btn_check", config.SELECTORS["check"])
    btn_call = config.SELECTORS.get("btn_call", config.SELECTORS["call"])
    btn_raise_base = config.SELECTORS.get("btn_raise", config.SELECTORS["raise"])
    btn_raise = f"{btn_raise_base}, #BET, #RAISE, .action-raise, .action-bet, button:has-text('Raise'), button:has-text('Bet'), button:has-text('Subir'), button:has-text('Apostar'), div#RAISE, div#BET"
    btn_allin = config.SELECTORS.get("btn_allin", config.SELECTORS["allin"])
    btn_replay = config.SELECTORS.get("btn_replay", config.SELECTORS["replay"])
    raise_input_sel = config.SELECTORS.get("raise_input", "input.betting-input")

    try:
        # Si ya está abierto el popup de advertencia de Fold/Check, resolverlo primero
        if resolver_popup_fold_check_gratis(target):
            return "CHECK"

        if accion == "REPLAY":
            print("🔄 Fin de partida detectado -> Clic en 'Jugar de nuevo'")
            if not clicar_texto_modal_universal(target, ["Jugar de nuevo", "Replay", "Volver a jugar"], "Jugar de nuevo"):
                intentar_click(target, btn_replay, "Jugar de nuevo", timeout=2000)
            esperar_carga_dom(target)
            accion_ejecutada = "REPLAY"

        elif accion == "START_GAME":
            print("🎮 Lobby detectado -> Clic en Tarjeta de Juego e Iniciar Partida")
            intentar_click(target, config.SELECTORS["select_game_card"], "Tarjeta de Juego Cash Game", timeout=2000)
            esperar_carga_dom(target)
            if not intentar_click(target, config.SELECTORS["start_match_btn"], "Botón Iniciar Partida", timeout=2000):
                clicar_texto_modal_universal(target, ["Jugar Poker", "Jugar"], "Botón Jugar Poker")
            esperar_carga_dom(target)
            accion_ejecutada = "START_GAME"

        elif accion == "FOLD":
            # PREVENCIÓN: Antes de hacer clic en Fold, verificar (0ms) si el botón Check está activo.
            # Si Check está visible, NUNCA hacer clic en Fold (evita el popup de advertencia y aprovecha la carta gratis).
            if intentar_click(target, btn_check, "Botón Check (Gratis prioritario en lugar de Fold)", timeout=0):
                return "CHECK"
            intentar_click(target, btn_fold, "Botón Fold", timeout=2000)
            time.sleep(0.15)
            if resolver_popup_fold_check_gratis(target):
                return "CHECK"
            accion_ejecutada = "FOLD"

        elif accion == "CHECK":
            if intentar_click(target, btn_check, "Botón Check", timeout=1500):
                accion_ejecutada = "CHECK"
            else:
                a_igualar_val = _parse_num_dom((dom_data or {}).get("a_igualar"), 0.0)
                # Si no había apuesta rival (a_igualar == 0) o estamos en River, el botón Check desapareció
                # porque la mano/ronda concluyó (Showdown o bote entregado). NUNCA foldear aquí
                # para evitar foldear la siguiente mano preflop.
                if a_igualar_val <= 0 or (dom_data or {}).get("street") == "RIVER":
                    print("ℹ️ Check ya no disponible en mesa (ronda concluida o showdown). Manteniendo Check sin pulsar Fold.")
                    accion_ejecutada = "CHECK"
                else:
                    respaldo = decidir_accion_rapida(dom_data or {})
                    if respaldo.accion in ("RAISE", "CALL"):
                        print("⚠️ Check no disponible (hubo apuesta rival), pero mano es fuerte/jugable -> Realizando Call...")
                        if intentar_click(target, btn_call, "Botón Call (Fallback Mano Fuerte)", timeout=1500):
                            accion_ejecutada = "CALL"
                        else:
                            accion_ejecutada = "CHECK"
                    else:
                        print("🛡️ Check no disponible (hubo apuesta rival previa) y mano débil -> Realizando Fold seguro...")
                        if intentar_click(target, btn_fold, "Botón Fold (Protección Stack)", timeout=1500):
                            accion_ejecutada = "FOLD"
                        else:
                            accion_ejecutada = "CHECK"

        elif accion == "CALL":
            if intentar_click(target, btn_call, "Botón Call", timeout=2000):
                accion_ejecutada = "CALL"
            elif clicar_texto_modal_universal(target, ["Call", "Igualar", "Pagar"], "Botón Call (Texto)"):
                accion_ejecutada = "CALL"
            elif intentar_click(target, btn_check, "Botón Check (Fallback Call)", timeout=1500):
                accion_ejecutada = "CHECK"
            elif intentar_click(target, btn_allin, "Botón All-in (Fallback Call)", timeout=1500):
                accion_ejecutada = "ALL_IN"
            else:
                respaldo = decidir_accion_rapida(dom_data or {})
                tier_pre = (dom_data or {}).get("gto_preflop", {}).get("tier", "DESCONOCIDO")
                if respaldo.accion in ("RAISE", "CALL", "CHECK") or tier_pre in ("S", "A", "B"):
                    print("⚠️ Call/Check no disponible aún pero la mano es jugable/fuerte -> Evitando Fold accidental...")
                    time.sleep(0.3)
                    if intentar_click(target, btn_call, "Botón Call (Reintento)", timeout=1000):
                        accion_ejecutada = "CALL"
                    elif clicar_texto_modal_universal(target, ["Call", "Igualar", "Pagar"], "Botón Call (Texto Reintento)"):
                        accion_ejecutada = "CALL"
                    elif intentar_click(target, btn_check, "Botón Check (Reintento)", timeout=1000):
                        accion_ejecutada = "CHECK"
                    else:
                        print("🛡️ Manteniendo postura defensiva CALL sin tirar la mano...")
                        accion_ejecutada = "CALL"
                else:
                    print("⚠️ Call no disponible. Intentando Fold seguro...")
                    if intentar_click(target, btn_fold, "Botón Fold (Fallback)", timeout=1500):
                        accion_ejecutada = "FOLD"
                    else:
                        accion_ejecutada = "FOLD"

        elif accion == "RAISE":
            monto = monto_legal
            monto_actual_dom = int(re.sub(r"[^\d]", "", str((dom_data or {}).get("monto_apuesta_input", "0"))) or 0)
            if monto > 0 and monto != monto_actual_dom:
                try:
                    inp = target.locator(raise_input_sel).first
                    if inp.is_visible():
                        print(f"💰 Ajustando importe de subida a: {monto}")
                        inp.fill(str(monto), timeout=300)
                except Exception:
                    pass

            if intentar_click(target, btn_raise, "Botón Raise / Apuesta", timeout=1500):
                accion_ejecutada = "RAISE"
            elif clicar_texto_modal_universal(target, ["Raise a", "Raise", "Bet", "Subir", "Apostar"], "Botón Raise (Texto)"):
                accion_ejecutada = "RAISE"
            elif intentar_click(target, btn_call, "Botón Call (Fallback Raise)", timeout=1500):
                accion_ejecutada = "CALL"
            else:
                intentar_click(target, btn_check, "Botón Check (Fallback Raise)", timeout=1000)
                accion_ejecutada = "CHECK"

        elif accion in ("ALL_IN", "ALLIN"):
            if intentar_click(target, btn_allin, "Botón All-in", timeout=2000):
                accion_ejecutada = "ALL_IN"
            elif clicar_texto_modal_universal(target, ["All-in", "Allin", "Todo adentro"], "Botón All-in (Texto)"):
                accion_ejecutada = "ALL_IN"
            elif intentar_click(target, btn_raise, "Botón Raise (Fallback All-in)", timeout=2000):
                accion_ejecutada = "RAISE"
            else:
                intentar_click(target, btn_call, "Botón Call (Fallback All-in)", timeout=1500)
                accion_ejecutada = "CALL"

        elif accion == "WAIT":
            accion_ejecutada = "WAIT"
            try:
                # Evitar hacer clic en Sit-out si hay partida activa
                if target.evaluate("() => document.body ? document.body.textContent.includes('Se ha terminado tu tiempo') : false") and not es_mesa_activa_o_turno(target):
                    print("⚠️ Detectado modo Sit-out fuera de partida active. Intentando reactivar...")
                    intentar_click(target, config.SELECTORS.get("btn_sitout", "#general-popup-button-action-OK"), "Botón Aceptar Sit-out", timeout=2000)
            except Exception:
                pass

    except Exception as e:
        print(f"🏆 Partida o mesa finalizada/cerrada ({e}).")

    if accion_ejecutada in ("FOLD", "CHECK", "CALL", "RAISE", "ALL_IN"):
        if not verificar_accion_reflejada(target, accion_ejecutada, timeout_ms=2000):
            print(f"⚠️ [POST-CLIC] La acción {accion_ejecutada} no desapareció del DOM tras 2000ms.")

    return accion_ejecutada

_local_strategic_engine = None
_local_opponent_model = None
_local_game_state_mgr = None
_local_decision_maker = None

def get_local_decision_maker():
    global _local_strategic_engine, _local_opponent_model, _local_game_state_mgr, _local_decision_maker
    if _local_decision_maker is None:
        _local_strategic_engine = StrategicEngine(strategy_model=None)
        m_type = getattr(config, "SKLEARN_OPPONENT_MODEL_TYPE", "random_forest")
        _local_opponent_model = OpponentModel(model_type=m_type)
        # Entrenar automáticamente leyendo datos históricos reales desde SQLite (poker_stats.db)
        _local_opponent_model.train_from_database()
        _local_game_state_mgr = GameStateManager()
        _local_decision_maker = DecisionMaker(_local_strategic_engine, _local_opponent_model)
    return _local_game_state_mgr, _local_decision_maker

def decidir_accion_local(dom_data: dict) -> PokerDecision:
    if not dom_data or not dom_data.get('es_mi_turno'):
        return PokerDecision(
            evaluacion="No es nuestro turno (Local Model)",
            estrategia_competitiva="Esperando acción del rival con modelos locales",
            estado_pantalla="EN_MESA_JUEGO",
            es_mi_turno=False,
            accion="WAIT",
            monto_apuesta=0,
            confianza=0.0
        )

    t0_ns = time.perf_counter_ns()
    game_state_mgr, decision_maker = get_local_decision_maker()

    pot = _parse_num_dom(dom_data.get('bote'), 0.0)
    current_bet = _parse_num_dom(dom_data.get('a_igualar'), 0.0)
    comunitarias_str = (dom_data.get('cartas_comunitarias') or '').strip()
    board_cards_count = len(comunitarias_str.split()) if comunitarias_str else 0

    game_state = {
        "pot_size": pot,
        "current_bet": current_bet,
        "board_cards_count": board_cards_count,
        "mis_cartas": dom_data.get('mis_cartas', ''),
        "cartas_comunitarias": comunitarias_str
    }
    game_state_mgr.update_game_state(game_state)

    historical_actions = game_state_mgr.get_historical_actions()
    opponent_profile = game_state_mgr.get_opponent_profile()

    active_mode = config.obtener_modo_activo()
    tf_mode = getattr(config, "TF_MODEL_TYPE", "mlp_policy_value") if active_mode == "tensorflow" else "gto"

    decision_res = decision_maker.decidir_accion_hibrida(
        game_state, historical_actions, opponent_profile, model_mode=tf_mode
    )
    if 'table_tracker' in globals() and table_tracker is not None:
        table_tracker.imprimir_estado_mesa(decision_res)
    local_action = decision_res["final_action"]
    latency_ms = round((time.perf_counter_ns() - t0_ns) / 1_000_000.0, 3)

    base_gto_decision = decidir_accion_rapida(dom_data)

    action_upper = "CHECK"
    if local_action == "fold":
        action_upper = "FOLD" if current_bet > 0 else "CHECK"
    elif local_action == "call":
        action_upper = "CALL" if current_bet > 0 else "CHECK"
    elif "raise" in local_action:
        action_upper = "RAISE" if "RAISE" in str(dom_data.get('botones_visibles', '')).upper() or base_gto_decision.accion == "RAISE" else ("CALL" if current_bet > 0 else "CHECK")

    bluff_prob_str = f" | Bluff Prob: {decision_res.get('bluff_probability', 0.0):.2f}" if "bluff_probability" in decision_res else ""
    base_gto_decision.evaluacion = f"⚡ [Local Hybrid ML/DL: {active_mode}] Sugerencia: {local_action} -> Ejecutada: {action_upper}{bluff_prob_str} ({latency_ms}ms)"
    base_gto_decision.estrategia_competitiva = f"Modelo local híbrido (TF/Keras + Scikit-Learn {config.SKLEARN_OPPONENT_MODEL_TYPE})"
    base_gto_decision.accion = action_upper
    base_gto_decision.confianza = 0.95

    # Registrar evento en tiempo real en SQLite a través de analytics_logger
    try:
        analytics_logger.registrar_evento_tiempo_real({
            "event_type": "LOCAL_AGENT_DECISION",
            "street": str(dom_data.get("calle") or dom_data.get("street") or "PREFLOP"),
            "pot_size": pot,
            "current_bet": current_bet,
            "hero_cards": dom_data.get("mis_cartas", ""),
            "board_cards": comunitarias_str,
            "player_predictions": decision_res.get("opponent_prediction", {}),
            "gto_probs": decision_res.get("base_probabilities", {}),
            "exploitative_probs": decision_res.get("adjusted_probabilities", {}),
            "chosen_action": action_upper,
            "action_amount": base_gto_decision.monto_apuesta,
            "latency_ms": latency_ms,
            "extra_data": {
                "sugerencia_raw": local_action,
                "evaluacion": base_gto_decision.evaluacion
            }
        })
    except Exception as e:
        print(f"⚠️ Warning logging local decision to SQLite: {e}")

    return base_gto_decision

def decidir_accion_rapida(dom_data: dict) -> PokerDecision:
    if not dom_data or not dom_data.get('es_mi_turno'):
        return PokerDecision(
            evaluacion="No es nuestro turno",
            estrategia_competitiva="Esperando acción del rival",
            estado_pantalla="EN_MESA_JUEGO",
            es_mi_turno=False,
            accion="WAIT",
            monto_apuesta=0,
            confianza=0.0
        )

    fuerza = (dom_data.get('fuerza_mano') or '').strip()
    fuerza_lower = fuerza.lower()
    botones = str(dom_data.get('botones_visibles', '') or dom_data.get('opciones_disponibles', '')).upper()
    cartas = (dom_data.get('mis_cartas', '') or '').upper()
    comunitarias_str = (dom_data.get('cartas_comunitarias') or '').strip()
    es_preflop = not bool(comunitarias_str)

    bb = _parse_num_dom(dom_data.get('big_blind'), float(getattr(config, 'BIG_BLIND_DEFAULT', 20.0))) or float(getattr(config, 'BIG_BLIND_DEFAULT', 20.0))
    gto_math = dom_data.get('gto_math') or calcular_pot_odds_y_sizing(
        dom_data.get('bote', 0),
        dom_data.get('a_igualar', 0),
        dom_data.get('mi_stack', 0),
        big_blind=bb,
        stack_rival_str=dom_data.get('stack_rival'),
    )
    stack_efectivo_bb = float(gto_math.get('stack_efectivo_bb') or gto_math.get('mi_stack_bb') or 25.0)
    a_igualar_bb = float(gto_math.get('a_igualar_bb') or 0.0)
    posicion = str(dom_data.get('posicion') or 'BTN').upper()
    if posicion == 'DESCONOCIDA':
        posicion = 'BTN'
    situacion = str(dom_data.get('situacion') or ('vs_raise' if a_igualar_bb > 1.5 else 'open'))

    gto_pre = dom_data.get('gto_preflop') or clasificar_mano_preflop(
        cartas,
        posicion=posicion,
        stack_efectivo_bb=stack_efectivo_bb,
        situacion=situacion,
    )
    gto_comb = dom_data.get('gto_combinacion') or evaluar_combinacion_mano_y_mesa(cartas, comunitarias_str)
    gto_textura = dom_data.get('gto_textura') or analizar_textura_mesa_y_blockers(cartas, comunitarias_str)

    raw_monto = dom_data.get('monto_apuesta_input', '0') or '0'
    monto_dom = int(re.sub(r"[^\d]", "", str(raw_monto)) or 0)
    bote_num = float(gto_math.get('bote') or _parse_num_dom(dom_data.get('bote'), 0.0) or 0.0)
    sizing_gto = int(gto_math.get('sizing_cbet_estandar') or round(max(bb * 2.0, bote_num * 0.55)))
    if not es_preflop and (gto_comb.get('es_doble_pareja') or gto_comb.get('es_set') or gto_comb.get('es_trips')):
        sizing_gto = int(round(max(bb * 2.0, bote_num * 0.67)))
    if not es_preflop and monto_dom <= int(bb) and bote_num >= bb * 3.0:
        monto_saneado = sizing_gto
    else:
        monto_saneado = monto_dom or sizing_gto

    hay_check = 'CHECK' in botones
    hay_call = 'CALL' in botones
    hay_bet = 'BET' in botones or 'RAISE' in botones
    hay_allin = 'ALL_IN' in botones or 'ALLIN' in botones

    tier = gto_pre.get('tier', 'DESCONOCIDO')
    modo_allin_or_fold = (hay_allin or 'ALL_IN' in botones or 'ALLIN' in botones) and ('FOLD' in botones) and not hay_check and not hay_call and not hay_bet
    desc_mano = (gto_comb.get('categoria') if not es_preflop and gto_comb.get('categoria') != 'CARTA ALTA PRIVADA' else None) or fuerza or gto_pre.get('mano_code') or cartas

    if modo_allin_or_fold:
        pot_odds_ao_f = float(gto_math.get('pot_odds_pct') or 0.0)
        mano_en_mesa_ao_f = bool(gto_comb.get('mano_en_mesa'))
        subtipo_ao_f = str(gto_comb.get('categoria', ''))
        fuerte_post_ao_f = (
            bool(gto_comb.get('es_monstruo') or gto_comb.get('es_doble_pareja') or gto_comb.get('es_set') or gto_comb.get('es_trips') or int(gto_comb.get('rango_categoria') or 0) >= 2)
            or bool(gto_comb.get('es_top_pair') or gto_comb.get('es_overpair') or 'Top Pair' in subtipo_ao_f or 'Overpair' in subtipo_ao_f)
            or bool(gto_comb.get('es_color') or gto_comb.get('es_escalera') or gto_comb.get('es_combo_draw'))
        ) and not mano_en_mesa_ao_f
        if es_preflop:
            es_jugable_ao_f = (tier in ('S', 'A')) or (tier == 'B' and (pot_odds_ao_f <= 36.0 or stack_efectivo_bb <= 12.0))
        else:
            es_jugable_ao_f = fuerte_post_ao_f
        if es_jugable_ao_f:
            return PokerDecision(
                evaluacion=f"⚡ [Modo All-in or Fold] Mano jugable [{tier}] ({desc_mano}). Push All-in directo.",
                estrategia_competitiva="Empujar All-in en modalidad exclusiva All-in o Fold",
                estado_pantalla="EN_MESA_JUEGO",
                es_mi_turno=True,
                accion="ALL_IN",
                monto_apuesta=0,
                confianza=0.95,
            )
        else:
            return PokerDecision(
                evaluacion=f"⚡ [Modo All-in or Fold] Mano débil [{tier}] ({desc_mano}). Fold disciplinado.",
                estrategia_competitiva="Fold disciplinado en modalidad exclusiva All-in o Fold",
                estado_pantalla="EN_MESA_JUEGO",
                es_mi_turno=True,
                accion="FOLD",
                monto_apuesta=0,
                confianza=0.92,
            )

    # 0. Régimen Nash Push/Fold real <= PUSH_FOLD_MAX_BB (10 BB) en Preflop
    if es_preflop and stack_efectivo_bb > 0 and stack_efectivo_bb <= float(getattr(config, 'PUSH_FOLD_MAX_BB', 10.0)):
        num_activos = len([p for p in (dom_data.get('perfiles_oponentes') or []) if str(p.get('estado', 'ACTIVO')).upper() == 'ACTIVO']) + 1
        nash = evaluar_push_fold_nash(
            mano_code=gto_pre.get('mano_code', ''),
            posicion=posicion,
            stack_efectivo_bb=stack_efectivo_bb,
            situacion=situacion,
            num_activos=max(2, num_activos),
        )
        if nash.get('en_regimen_push_fold'):
            acc_nash = nash['accion']
            if acc_nash == 'ALL_IN':
                if hay_allin:
                    return PokerDecision(
                        evaluacion=f"⚡ [Nash Push/Fold] {nash['razon']}",
                        estrategia_competitiva=nash['rango_nash'],
                        estado_pantalla="EN_MESA_JUEGO",
                        es_mi_turno=True,
                        accion="ALL_IN",
                        monto_apuesta=0,
                        confianza=0.95,
                    )
                if hay_bet:
                    return PokerDecision(
                        evaluacion=f"⚡ [Nash Push/Fold] {nash['razon']}",
                        estrategia_competitiva=nash['rango_nash'],
                        estado_pantalla="EN_MESA_JUEGO",
                        es_mi_turno=True,
                        accion="RAISE",
                        monto_apuesta=int(gto_math.get('mi_stack') or round(stack_efectivo_bb * bb)),
                        confianza=0.95,
                    )
            if acc_nash == 'CALL' and hay_call:
                return PokerDecision(
                    evaluacion=f"⚡ [Nash Push/Fold] {nash['razon']}",
                    estrategia_competitiva=nash['rango_nash'],
                    estado_pantalla="EN_MESA_JUEGO",
                    es_mi_turno=True,
                    accion="CALL",
                    monto_apuesta=0,
                    confianza=0.93,
                )
            if acc_nash == 'FOLD':
                if hay_check:
                    return PokerDecision(
                        evaluacion=f"⚡ [Nash Push/Fold] Check gratis en lugar de Fold ({nash['razon']})",
                        estrategia_competitiva="Carta gratis",
                        estado_pantalla="EN_MESA_JUEGO",
                        es_mi_turno=True,
                        accion="CHECK",
                        monto_apuesta=0,
                        confianza=0.92,
                    )
                return PokerDecision(
                    evaluacion=f"⚡ [Nash Push/Fold] {nash['razon']}",
                    estrategia_competitiva=nash['rango_nash'],
                    estado_pantalla="EN_MESA_JUEGO",
                    es_mi_turno=True,
                    accion="FOLD",
                    monto_apuesta=0,
                    confianza=0.92,
                )

    tier = gto_pre.get('tier', 'DESCONOCIDO')
    es_pareja_mano = bool(gto_comb.get('es_pareja_en_mano'))
    coincidencias = bool(gto_comb.get('coincidencias_mesa'))
    es_color = bool(gto_comb.get('es_color'))
    es_escalera = bool(gto_comb.get('es_escalera'))
    mano_en_mesa = bool(gto_comb.get('mano_en_mesa'))
    subtipo_comb = str(gto_comb.get('categoria', ''))
    es_top_o_overpair = bool(gto_comb.get('es_top_pair') or gto_comb.get('es_overpair') or "Top Pair" in subtipo_comb or "Overpair" in subtipo_comb)
    es_set_o_mejor = bool(
        gto_comb.get('es_monstruo')
        or gto_comb.get('es_doble_pareja')
        or gto_comb.get('es_set')
        or gto_comb.get('es_trips')
        or int(gto_comb.get('rango_categoria') or 0) >= 2
    ) and not mano_en_mesa

    patrones_monstruo = ['doble pareja', 'trío', 'trio', 'escalera', 'color', 'full', 'póker', 'poker', 'escalera real']
    es_mano_fuerte = (
        (any(p in fuerza_lower for p in patrones_monstruo) and not mano_en_mesa)
        or es_set_o_mejor
        or es_top_o_overpair
        or es_color
        or es_escalera
        or (es_preflop and tier in ('S', 'A'))
    )

    tiene_proyecto = bool(gto_comb.get('proyecto_color') or gto_comb.get('proyecto_oesd') or gto_comb.get('es_combo_draw') or ('proyecto' in fuerza_lower))
    outs_totales = int(gto_comb.get('outs_totales') or 0)
    pot_odds_pct = float(gto_math.get('pot_odds_pct') or 0.0)
    textura_peligrosa = bool(gto_textura.get('posible_color') or gto_textura.get('posible_escalera'))

    es_mano_jugable = (
        (es_preflop and tier in ('S', 'A', 'B'))
        or tiene_proyecto
        or (es_pareja_mano and not mano_en_mesa)
        or (coincidencias and not mano_en_mesa)
        or es_color
        or es_escalera
    )

    desc_mano = (gto_comb.get('categoria') if not es_preflop and gto_comb.get('categoria') != 'CARTA ALTA PRIVADA' else None) or fuerza or gto_pre.get('mano_code') or cartas

    # Preflop: Tier S (QQ+, AKs) ante raise rival con stack efectivo <= 25 BB -> ALL_IN / 4-BET por valor
    if es_preflop and tier == 'S' and a_igualar_bb > 1.5:
        if hay_allin:
            return PokerDecision(
                evaluacion=f"⚡ [0ms GTO] Mano monstruo preflop ({desc_mano}). Push All-in por valor absoluto.",
                estrategia_competitiva="4-Bet / Push con premium hand en 3-max",
                estado_pantalla="EN_MESA_JUEGO",
                es_mi_turno=True,
                accion="ALL_IN",
                monto_apuesta=0,
                confianza=0.98,
            )
        if hay_bet:
            return PokerDecision(
                evaluacion=f"⚡ [0ms GTO] Mano monstruo preflop ({desc_mano}). Subida máxima por valor.",
                estrategia_competitiva="4-Bet con premium hand en 3-max",
                estado_pantalla="EN_MESA_JUEGO",
                es_mi_turno=True,
                accion="RAISE",
                monto_apuesta=int(gto_math.get('mi_stack') or round(stack_efectivo_bb * bb)),
                confianza=0.98,
            )

    # 1. Manos fuertes por valor (Top Pair / Overpair / Set+ / Premium Preflop) -> Apuesta/Subida por valor garantizada
    if es_mano_fuerte:
        if hay_bet or (not es_preflop and hay_check):
            return PokerDecision(
                evaluacion=f"⚡ [0ms GTO] Mano fuerte ({desc_mano}). Apostando / Subiendo por valor.",
                estrategia_competitiva="Extraer valor máximo con mano fuerte",
                estado_pantalla="EN_MESA_JUEGO",
                es_mi_turno=True,
                accion="RAISE",
                monto_apuesta=monto_saneado,
                confianza=0.96
            )
        elif hay_call:
            return PokerDecision(
                evaluacion=f"⚡ [0ms GTO] Mano fuerte ({desc_mano}). Pagando subida rival.",
                estrategia_competitiva="Igualar apuesta con mano ganadora",
                estado_pantalla="EN_MESA_JUEGO",
                es_mi_turno=True,
                accion="CALL",
                monto_apuesta=0,
                confianza=0.92
            )
        elif hay_check:
            return PokerDecision(
                evaluacion=f"⚡ [0ms GTO] Mano fuerte ({desc_mano}). Pasando gratis.",
                estrategia_competitiva="Pase táctico con mano fuerte",
                estado_pantalla="EN_MESA_JUEGO",
                es_mi_turno=True,
                accion="CHECK",
                monto_apuesta=0,
                confianza=0.90
            )

    # 2. Combo draws / proyectos fuertes (8+ outs): Semi-farol agresivo por valor/equidad
    if not es_preflop and tiene_proyecto and outs_totales >= 8:
        equidad_aprox_pct = min(65.0, outs_totales * (4.0 if len(comunitarias_str.split()) == 3 else 2.0))
        if gto_comb.get('es_combo_draw') and (hay_bet or hay_check) and not textura_peligrosa:
            return PokerDecision(
                evaluacion=f"⚡ [0ms GTO] Combo Draw ({outs_totales} outs). Semi-farol agresivo por valor.",
                estrategia_competitiva="Presión con alta equidad de proyecto",
                estado_pantalla="EN_MESA_JUEGO",
                es_mi_turno=True,
                accion="RAISE",
                monto_apuesta=int(gto_math.get('sizing_cbet_estandar') or monto_saneado),
                confianza=0.88,
            )
        if hay_call and equidad_aprox_pct >= pot_odds_pct:
            return PokerDecision(
                evaluacion=f"⚡ [0ms GTO] Proyecto rentable ({outs_totales} outs ~{equidad_aprox_pct:.0f}% eq vs {pot_odds_pct:.1f}% pot odds).",
                estrategia_competitiva="Call por odds implícitas y directas",
                estado_pantalla="EN_MESA_JUEGO",
                es_mi_turno=True,
                accion="CALL",
                monto_apuesta=0,
                confianza=0.85,
            )

    # 3. Manos de Valor Fuerte Postflop (Top Pair / Set / Two Pair): Inferencia EV para Value Bet
    if not es_preflop and (gto_comb.get('es_top_pair') or es_set_o_mejor or es_top_o_overpair):
        ev_res = decidir_por_ev(dom_data)
        ev_acc = ev_res.get('accion')
        if ev_acc == 'RAISE' and (hay_bet or hay_check) and float(ev_res.get('ev_bb', 0)) > 0.3:
            return PokerDecision(
                evaluacion=f"⚡ [0ms EV] Valor postflop con {desc_mano} (EV: {ev_res.get('ev_bb', 0):+.2f} BB). Apostando por valor.",
                estrategia_competitiva="Extracción de valor postflop por EV positivo",
                estado_pantalla="EN_MESA_JUEGO",
                es_mi_turno=True,
                accion="RAISE",
                monto_apuesta=monto_saneado,
                confianza=0.90
            )

    # 3. Pase gratis (Check prioritario)
    if hay_check:
        return PokerDecision(
            evaluacion=f"⚡ [0ms GTO] Pase gratis disponible con mano {desc_mano}.",
            estrategia_competitiva="Aprovechar carta gratis",
            estado_pantalla="EN_MESA_JUEGO",
            es_mi_turno=True,
            accion="CHECK",
            monto_apuesta=0,
            confianza=0.90
        )

    # 4. Apertura y defensa preflop calibrada (Raise or Fold para cerrar la brecha VPIP/PFR)
    if es_preflop:
        pot_odds_preflop = float(gto_math.get('pot_odds_pct') or 0.0)
        es_open_preflop = a_igualar_bb <= 0.5

        # Si abrimos la mano preflop (sin subida previa de un rival)
        if es_open_preflop:
            if tier in ('S', 'A', 'B', 'C') and hay_bet:
                return PokerDecision(
                    evaluacion=f"⚡ [0ms GTO] Open Preflop [{tier}] ({desc_mano}). Subida estándar por iniciativa.",
                    estrategia_competitiva="Apertura con iniciativa preflop",
                    estado_pantalla="EN_MESA_JUEGO",
                    es_mi_turno=True,
                    accion="RAISE",
                    monto_apuesta=int(gto_math.get('sizing_cbet_estandar') or round(bb * 2.2)),
                    confianza=0.92
                )
            if tier == 'BASURA':
                if hay_check:
                    return PokerDecision(
                        evaluacion=f"⚡ [0ms GTO] Mano basura Preflop [{tier}] ({desc_mano}). Check opción en ciega.",
                        estrategia_competitiva="Carta gratis en BB",
                        estado_pantalla="EN_MESA_JUEGO",
                        es_mi_turno=True,
                        accion="CHECK",
                        monto_apuesta=0,
                        confianza=0.90
                    )
                return PokerDecision(
                    evaluacion=f"⚡ [0ms GTO] Mano basura Preflop [{tier}] ({desc_mano}). Fold disciplinado.",
                    estrategia_competitiva="Fold de mano sin valor preflop",
                    estado_pantalla="EN_MESA_JUEGO",
                    es_mi_turno=True,
                    accion="FOLD",
                    monto_apuesta=0,
                    confianza=0.95
                )

        # Si enfrentamos una subida/apuesta previa rival
        if hay_call:
            if tier in ('S', 'A'):
                if hay_bet and (a_igualar_bb <= 3.5 or stack_efectivo_bb <= 15.0):
                    return PokerDecision(
                        evaluacion=f"⚡ [0ms GTO] Re-subida por valor Preflop [{tier}] ({desc_mano}). 3-Bet / Resubida.",
                        estrategia_competitiva="3-Bet de valor preflop",
                        estado_pantalla="EN_MESA_JUEGO",
                        es_mi_turno=True,
                        accion="RAISE",
                        monto_apuesta=int(round(a_igualar_bb * bb * 2.8)),
                        confianza=0.94
                    )
                return PokerDecision(
                    evaluacion=f"⚡ [0ms GTO] Rango fuerte Preflop [{tier}] ({desc_mano}). Defendiendo inversión con Call.",
                    estrategia_competitiva="Defensa de rango preflop",
                    estado_pantalla="EN_MESA_JUEGO",
                    es_mi_turno=True,
                    accion="CALL",
                    monto_apuesta=0,
                    confianza=0.90
                )
            if tier == 'B' and pot_odds_preflop <= 33.0:
                return PokerDecision(
                    evaluacion=f"⚡ [0ms GTO] Rango medio Preflop [{tier}] ({desc_mano}). Defendiendo con Call por Pot Odds.",
                    estrategia_competitiva="Defensa de rango medio",
                    estado_pantalla="EN_MESA_JUEGO",
                    es_mi_turno=True,
                    accion="CALL",
                    monto_apuesta=0,
                    confianza=0.86
                )
            if tier == 'C' and posicion == 'BB' and pot_odds_preflop <= 22.0:
                return PokerDecision(
                    evaluacion=f"⚡ [0ms GTO] Defensa de Ciega Grande [{tier}] ({desc_mano}). Call por precio excelente.",
                    estrategia_competitiva="Defensa de BB por odds",
                    estado_pantalla="EN_MESA_JUEGO",
                    es_mi_turno=True,
                    accion="CALL",
                    monto_apuesta=0,
                    confianza=0.82
                )
            # Fold disciplinado ante subidas para Tier C fuera de BB y Basura
            return PokerDecision(
                evaluacion=f"⚡ [0ms GTO] Mano débil vs subida Preflop [{tier}] ({desc_mano}). Fold disciplinado.",
                estrategia_competitiva="Fold disciplinado preflop",
                estado_pantalla="EN_MESA_JUEGO",
                es_mi_turno=True,
                accion="FOLD",
                monto_apuesta=0,
                confianza=0.90
            )

    # 5. Defensa postflop activa con Middle/Bottom Pair o rango jugable evaluando Pot Odds y Textura
    if hay_call:
        max_pot_odds_call = 28.0 if textura_peligrosa else 38.0
        if es_mano_jugable and not mano_en_mesa and pot_odds_pct <= max_pot_odds_call:
            return PokerDecision(
                evaluacion=f"⚡ [0ms GTO] Mano jugable ({desc_mano}). Call por Pot Odds ({pot_odds_pct:.1f}%).",
                estrategia_competitiva="Control de bote y defensa por precio",
                estado_pantalla="EN_MESA_JUEGO",
                es_mi_turno=True,
                accion="CALL",
                monto_apuesta=0,
                confianza=0.84
            )
        # Último filtro por EV cuando hay duda en postflop
        if not es_preflop and (es_mano_jugable or gto_comb.get('proyecto_gutshot')):
            ev_res = decidir_por_ev(dom_data)
            ev_acc = ev_res['accion']
            if ev_acc == 'RAISE' and not es_mano_fuerte and textura_peligrosa and float(ev_res.get('equity') or 0.0) < 0.55 and hay_call:
                ev_acc = 'CALL'
                ev_res = dict(ev_res, accion='CALL', monto=0, razon=f"{ev_res['razon']} -> Call controlado en board peligroso")
            if ev_acc in ('CALL', 'RAISE', 'ALL_IN'):
                return PokerDecision(
                    evaluacion=f"⚡ [0ms EV] {ev_res['razon']}",
                    estrategia_competitiva="Decisión óptima por Valor Esperado",
                    estado_pantalla="EN_MESA_JUEGO",
                    es_mi_turno=True,
                    accion=ev_acc,
                    monto_apuesta=ev_res['monto'],
                    confianza=0.82,
                )

    # 4. Fold defensivo solo para manos débiles fuera de rango
    if hay_call:
        return PokerDecision(
            evaluacion=f"⚡ [0ms GTO] Mano débil [{tier}] ({desc_mano}). Fold para proteger stack.",
            estrategia_competitiva="Fold disciplinado GTO",
            estado_pantalla="EN_MESA_JUEGO",
            es_mi_turno=True,
            accion="FOLD",
            monto_apuesta=0,
            confianza=0.88
        )

    if hay_check:
        accion_fallback = "CHECK"
    elif es_mano_jugable or es_mano_fuerte:
        accion_fallback = "CALL" if (hay_call or "CALL" in botones or "IGUALAR" in botones) else "FOLD"
    else:
        accion_fallback = "FOLD" if ("FOLD" in botones or "RETIRAR" in botones or "RETIRARSE" in botones) else ("CHECK" if hay_check else "CALL")
    return PokerDecision(
        evaluacion=f"⚡ [0ms GTO] Acción rápida fallback: {accion_fallback}",
        estrategia_competitiva="Acción defensiva automática",
        estado_pantalla="EN_MESA_JUEGO",
        es_mi_turno=True,
        accion=accion_fallback,
        monto_apuesta=0,
        confianza=0.80
    )

def obtener_o_crear_contexto(p, user_data_dir):
    cdp_port = getattr(config, "CDP_PORT", 9222)
    cdp_url = f"http://127.0.0.1:{cdp_port}"

    # 1. Intentar reconectar a un navegador Chromium ya abierto vía CDP (Remote Debugging)
    try:
        import urllib.request
        urllib.request.urlopen(f"{cdp_url}/json/version", timeout=1.0)
        print(f"⚡ [RECONEXIÓN CDP OK] Navegador Chromium en ejecución detectado (puerto {cdp_port}). Reanudando control instantáneo del navegador...")
        browser = p.chromium.connect_over_cdp(cdp_url)
        context = browser.contexts[0] if browser.contexts else browser.new_context()
        return context, True
    except Exception:
        pass

    # 2. Si no hay navegador en ejecución, lanzar una nueva instancia persistente con puerto CDP habilitado
    print(f"🚀 Iniciando navegador Playwright (Chromium Persistente con puerto CDP {cdp_port})...")
    args = [f"--remote-debugging-port={cdp_port}"]
    context = p.chromium.launch_persistent_context(
        user_data_dir=user_data_dir,
        headless=config.HEADLESS,
        slow_mo=config.SLOW_MO,
        viewport={"width": 1280, "height": 720},
        args=args
    )
    return context, False

def main():
    agent = PokerAgent(fallback_fn=decidir_accion_rapida)
    if analytics_logger is not None:
        try:
            from analytics_dashboard import iniciar_servidor_dashboard_background
            iniciar_servidor_dashboard_background(port=8050)
        except Exception:
            pass

    user_data_dir = os.path.abspath(config.USER_DATA_DIR)
    print(f"📁 Usando directorio de sesión persistente (Cookies/Cache/Storage): {user_data_dir}")

    try:
        with sync_playwright() as p:
            context, es_reconectado = obtener_o_crear_contexto(p, user_data_dir)
            context.add_init_script(JS_INICIALIZAR_MUTATION_OBSERVER)

            if es_reconectado:
                for pg in context.pages:
                    try:
                        pg.evaluate(JS_INICIALIZAR_MUTATION_OBSERVER)
                    except Exception:
                        pass

            def configurar_interceptores(target_page):
                configurar_ws_interceptores(target_page, tracker, profiler, analytics_logger, table_tracker=table_tracker)

            page = context.pages[0] if context.pages else context.new_page()
            configurar_interceptores(page)

            def on_new_page(new_page):
                try:
                    new_page.wait_for_load_state("domcontentloaded", timeout=5000)
                except Exception:
                    pass
                configurar_ws_interceptores(new_page, tracker, profiler, analytics_logger, table_tracker=table_tracker)

            context.on("page", on_new_page)
            print("🕵️ Interceptor de WebSockets, Red y Sonidos activado globalmente.")

            if es_reconectado and es_mesa_activa_o_turno(page):
                print("✨ [RECONEXIÓN INSTANTÁNEA] Mesa activa o turno detectado en el navegador recuperado. Omitiendo login y navegación.")
            else:
                # 1. Login inicial
                hacer_login(page)
                # 2. Navegación al juego
                page = navegar_al_juego(page)

            print("\n⚡ Bot de Póquer Autónomo Reactivo por Eventos ACTIVO.")
            print("Presiona Ctrl + C en la terminal para detener el bot.\n")

            # 3. Bucle reactivo continuo infinito (Juego automático continuo sin reinicios)
            ciclo = 1
            ultimo_turno_id = None
            ultimo_mano_street_key = None
            ultimo_modal_id = None
            ultimo_tiempo_actividad = time.time()
            resiliencia = ControladorResiliencia()

            try:
                while True:
                    try:
                        if page.is_closed():
                            print("🔄 Pestaña actual cerrada. Recuperando pestaña activa del contexto...")
                            pages_activas = [p for p in context.pages if not p.is_closed()]
                            if pages_activas:
                                page = pages_activas[-1]
                                page.bring_to_front()
                            else:
                                page = context.new_page()
                                configurar_interceptores(page)
                                hacer_login(page)

                            page = navegar_al_juego(page)
                            ultimo_tiempo_actividad = time.time()
                            continue

                        target_mesa = obtener_target_mesa(page)
                        esperar_sincronizacion_turno(target_mesa, timeout_max_ms=1200)

                        # Si el recuadro "¿Seguro que quieres foldear?" está en pantalla, clic inmediato en Check gratis
                        if resolver_popup_fold_check_gratis(target_mesa):
                            ultimo_tiempo_actividad = time.time()
                            time.sleep(0.4)
                            continue

                        dom_data = target_mesa.evaluate(JS_EXTRAER_ESTADO_DOM) or {}

                        # Comprobar si estamos en sala de espera / lobby / registro
                        try:
                            texto_body = target_mesa.evaluate("() => document.body ? document.body.innerText.toLowerCase() : ''") or ''
                            if any(w in texto_body for w in ["esperando a", "waiting for", "registro completado", "inscribiendo"]):
                                print("⏳ [LOBBY / ESPERA] La mesa está esperando jugadores o completando registro. Pausando sondeo...")
                                time.sleep(1.5)
                                continue
                        except Exception:
                            pass

                        if dom_data and 'table_tracker' in globals() and table_tracker is not None:
                            table_tracker.actualizar_desde_dom(dom_data)
                            table_tracker.imprimir_estado_mesa()

                        # Si llegó la señal de audio my_turn.mp3 pero los botones del DOM aún están renderizándose, esperar hasta 450ms
                        if tracker.http_turn_signal and not dom_data.get('botones_visibles'):
                            try:
                                target_mesa.wait_for_selector("#FOLD, #CHECK, #CALL, #BET, #RAISE, .action-button", state="visible", timeout=450)
                            except Exception:
                                time.sleep(0.25)
                            dom_data.update(target_mesa.evaluate(JS_EXTRAER_ESTADO_DOM) or {})



                        if detectar_desconexion(dom_data, page=page):
                            print("⚠️ [DESCONEXIÓN] Detectada pérdida de conexión en mesa/navegador. Esperando backoff...")
                            log_json_estructurado(
                                "disconnection",
                                hand_id=f"hand_{tracker.hand_number}",
                                payload={"modales": dom_data.get("modales_visibles", ""), "estado": dom_data.get("estado_mesa", "")},
                            )
                            should_stop, backoff = resiliencia.registrar_error()
                            time.sleep(backoff)
                            if should_stop:
                                print("🛑 [PARADA SEGURA] Límite de errores consecutivos por desconexión alcanzado.")
                                break
                            continue

                        es_mi_turno = bool(dom_data.get('es_mi_turno') or tracker.http_turn_signal)
                        if es_mi_turno:
                            ultimo_tiempo_actividad = time.time()
                            dom_data["es_mi_turno"] = True
                            if tracker.http_turn_signal:
                                tracker.http_turn_signal = False

                        if tracker.current_street == "PREFLOP" and tracker.current_round_no <= 1 and not tracker.community_cards:
                            dom_data["cartas_comunitarias"] = ""

                        actualizar_dom_data_conservando_contexto(
                            dom_data,
                            None,
                            tracker_obj=tracker,
                            profiler_obj=profiler,
                            memory_obj=experience_memory,
                        )

                        mis_cartas = dom_data["mis_cartas"]
                        comunitarias = dom_data["cartas_comunitarias"]
                        bote = dom_data["bote"]
                        botones = dom_data.get('botones_visibles', '') or dom_data.get('opciones_disponibles', '')
                        modales_o_botones = " | ".join(filter(None, [botones, dom_data.get('modales_visibles', '')]))

                        if mis_cartas:
                            resiliencia.actualizar_mano_watchdog(f"{tracker.hand_number}:{mis_cartas}")
                            if resiliencia.verificar_watchdog_mano():
                                print("⏱️ [WATCHDOG] Mano excedió tiempo máximo sin resolución; reiniciando estado local de mano.")
                                log_json_estructurado(
                                    "hand_watchdog_timeout",
                                    hand_id=f"hand_{tracker.hand_number}",
                                    payload={"mis_cartas": mis_cartas, "street": tracker.current_street},
                                )
                                resiliencia.reiniciar_watchdog_mano()
                                tracker.reset_mano()
                        else:
                            resiliencia.reiniciar_watchdog_mano()

                        current_action_context = (tracker.hand_number, tracker.current_street, _parse_num_dom(dom_data.get('a_igualar'), 0.0))

                        # Prevención de duplicados / Debounce:
                        # 1. Si acabamos de actuar hace <2.5s
                        # 2. O si estamos en el mismo contexto de apuesta exacto que la última acción ejecutada y han pasado <5.0s
                        if es_mi_turno and (
                            (time.time() - tracker.hero_just_acted_ts < 2.5)
                            or (
                                getattr(tracker, "hero_last_action_context", None) is not None
                                and current_action_context == tracker.hero_last_action_context
                                and (time.time() - tracker.hero_just_acted_ts < 5.0)
                            )
                        ):
                            es_mi_turno = False

                        # Guardia anti-turnos huérfanos: si no hay cartas de Hero ni botones de acción,
                        # no es un turno jugable (mesa finalizada, eliminado o modal de fin de partida activo).
                        if es_mi_turno and not mis_cartas and not botones:
                            es_mi_turno = False
                            tracker.http_turn_signal = False

                        # Guardia Anti-Ceguera: Espera reactiva de asentamiento de cartas si es nuestro turno pero mis_cartas está vacío
                        if es_mi_turno and not mis_cartas:
                            for _ in range(9):  # Hasta 450ms (9 x 50ms)
                                time.sleep(0.05)
                                if tracker.my_cards:
                                    mis_cartas = formatear_cartas_canonicas(tracker.my_cards)
                                    dom_data["mis_cartas"] = mis_cartas
                                    break
                                try:
                                    fresh_dom = target_mesa.evaluate(JS_EXTRAER_ESTADO_DOM) or {}
                                    if fresh_dom.get("mis_cartas"):
                                        mis_cartas = formatear_cartas_canonicas(fresh_dom["mis_cartas"])
                                        dom_data["mis_cartas"] = mis_cartas
                                        break
                                except Exception:
                                    pass

                            # Si tras la espera reactiva aún no hay cartas, NUNCA actuar ni consultar a ciegas
                            if not mis_cartas:
                                print("⚠️ [ANTI-CEGUERA] Turno detectado sin cartas privadas tras 450ms. Omitiendo acción a ciegas...")
                                es_mi_turno = False
                                tracker.http_turn_signal = False

                        turno_id_actual = f"{mis_cartas}|{comunitarias}|{bote}|{botones}"
                        turno_street_key = (tracker.hand_number, tracker.current_street, mis_cartas, comunitarias)

                        # CASO 1: ES NUESTRO TURNO Y AÚN NO HEMOS CONSULTADO A GEMINI PARA ESTE TURNO
                        if es_mi_turno and turno_id_actual != ultimo_turno_id:
                            ultimo_turno_id = turno_id_actual
                            ultimo_mano_street_key = turno_street_key
                            ultimo_tiempo_actividad = time.time()

                            # DOM es fuente primaria; captura solo como fallback si faltan cartas/botones o USAR_CAPTURA=true
                            screenshot_bytes = None
                            usar_captura = getattr(config, "USAR_CAPTURA", False) or (not mis_cartas) or (not botones)
                            if usar_captura:
                                owner_page = getattr(target_mesa, 'page', page)
                                try:
                                    if len(context.pages) > 1 and not owner_page.is_closed():
                                        owner_page.bring_to_front()
                                except Exception:
                                    pass
                                try:
                                    screenshot_bytes = owner_page.screenshot(type="jpeg", quality=85)
                                except Exception as e:
                                    print(f"⚠️ Error tomando captura fallback: {e}")

                            print(f"\n--- [Turno Hero #{ciclo}] 🎴 Mis Cartas: {mis_cartas or 'N/A'} | Mesa: {comunitarias or '-'} | Bote: {bote} ---")

                            decision = None
                            decision_source = "gemini"
                            t0 = time.time()
                            stack_ef_bb = float((dom_data.get("gto_math") or {}).get("stack_efectivo_bb") or 0.0)
                            modo_activo = config.obtener_modo_activo() if hasattr(config, "obtener_modo_activo") else str(getattr(config, "MODO", "gemini")).lower()

                            if modo_activo == "local":
                                print("🌳 [MODO LOCAL HÍBRIDO] Decidiendo con Motor Estratégico + Opponent Model (Sin LLM)...")
                                decision = decidir_accion_local(dom_data)
                                decision_source = "local_hybrid"
                            elif not comunitarias and 0 < stack_ef_bb <= float(getattr(config, "PUSH_FOLD_MAX_BB", 10.0)):
                                print(f"⚡ [Nash Push/Fold <= {config.PUSH_FOLD_MAX_BB:.0f}BB] Decisión directa sin LLM ({stack_ef_bb:.1f} BB)...")
                                decision = decidir_accion_rapida(dom_data)
                                decision_source = "nash_push_fold"
                            elif modo_activo == "determinista":
                                print("🛡️ [MODO DETERMINISTA] Decidiendo con motor GTO sin invocar LLM...")
                                decision = decidir_accion_rapida(dom_data)
                                decision_source = "gto_determinista"
                            elif modo_activo == "hibrido":
                                print("⚖️ [MODO HÍBRIDO] Evaluando motor GTO + EV y consultando LLM...")
                                motor_dec = decidir_accion_rapida(dom_data)
                                ev_res = decidir_por_ev(dom_data)
                                llm_dec = None
                                timeout_cfg = float(getattr(config, "AI_DECISION_TIMEOUT_SEC", 13.0))
                                try:
                                    llm_dec = consultar_gemini_con_timeout(agent, screenshot_bytes, dom_data=dom_data, timeout_seg=timeout_cfg)
                                except Exception as e:
                                    print(f"⚠️ Excepción al consultar LLM en modo híbrido: {e}")
                                decision, reg_hib = resolver_decision_hibrida(dom_data, motor_dec, ev_res, llm_dec)
                                decision_source = "hibrido_llm" if reg_hib.get("aceptada_llm") else "hibrido_motor"
                                print(f"⚖️ [HÍBRIDO] {reg_hib.get('razon')}")
                            else:
                                timeout_cfg = float(getattr(config, "AI_DECISION_TIMEOUT_SEC", 13.0))
                                print(f"🤖 Consultando a Gemini / IA (1 consulta única, timeout máx {timeout_cfg:.1f}s)...")
                                try:
                                    decision = consultar_gemini_con_timeout(
                                        agent,
                                        screenshot_bytes,
                                        dom_data=dom_data,
                                        timeout_seg=timeout_cfg,
                                        decision_uid=f"dec_{tracker.hand_number}_{ciclo}",
                                        hand_uid=f"hand_{tracker.hand_number}",
                                    )
                                except Exception as e:
                                    print(f"⚠️ Excepción al consultar Gemini AI: {e}")

                                ai_trace_now = getattr(agent, "last_trace", None)
                                if not decision or decision.accion in ("WAIT", "START_GAME", "REPLAY"):
                                    fb_reason = getattr(ai_trace_now, "fallback_reason", "") or ("timeout_or_none" if not decision else f"passive_{decision.accion}")
                                    print(f"🛡️ [RESPALDO DE SEGURIDAD 0ms] Motivo={fb_reason} -> Ejecutando acción defensiva rápida...")
                                    decision = decidir_accion_rapida(dom_data)
                                    decision_source = "gto_fallback_0ms"
                                elif getattr(agent, "last_result", None) is not None:
                                    decision_source = agent.last_result.decision_source

                            dt = time.time() - t0
                            print(f"⚡ [DECISIÓN IA ({dt:.2f}s)]: {decision.accion} | Confianza: {decision.confianza:.2f} | {decision.evaluacion}")

                            accion_ejecutada = ejecutar_accion(target_mesa, decision, dom_data=dom_data, tiempo_ia_seg=dt)
                            tracker.hero_just_acted_ts = time.time()
                            tracker.hero_last_action_context = (tracker.hand_number, tracker.current_street, _parse_num_dom(dom_data.get('a_igualar'), 0.0))
                            log_json_estructurado(
                                "decision",
                                hand_id=f"hand_{tracker.hand_number}",
                                decision_id=f"dec_{tracker.hand_number}_{ciclo}",
                                payload={
                                    "street": tracker.current_street,
                                    "decision_source": decision_source,
                                    "intended_action": getattr(decision, "accion", ""),
                                    "executed_action": accion_ejecutada,
                                    "monto": getattr(decision, "monto_apuesta", 0),
                                    "confianza": getattr(decision, "confianza", 0.0),
                                    "latency_ms": round(dt * 1000.0, 2),
                                    "experience_memory_hit": 1 if dom_data.get("memoria_errores") else 0,
                                },
                            )
                            tracker.last_hand_tier = (dom_data.get("gto_preflop") or {}).get("tier", "DESCONOCIDO")
                            tracker.last_board_texture = (dom_data.get("gto_textura") or {}).get("textura_mesa", "DESCONOCIDA")
                            tracker.last_opp_archetype = (dom_data.get("perfil_rival") or {}).get("estilo", "Desconocido")
                            tracker.last_action_taken = accion_ejecutada
                            tracker.last_street = tracker.current_street
                            tracker.last_position = tracker.position_name
                            tracker.last_big_blind = _parse_num_dom((dom_data.get("gto_math") or {}).get("big_blind"), float(getattr(config, "BIG_BLIND_DEFAULT", 20.0)))
                            if accion_ejecutada == "CALL":
                                tracker.last_invested_chips += _parse_num_dom((dom_data.get("gto_math") or {}).get("a_igualar"), 0.0)
                            elif accion_ejecutada in ("RAISE", "ALL_IN"):
                                tracker.last_invested_chips += _parse_num_dom(getattr(decision, "monto_apuesta", 0) or (dom_data.get("gto_math") or {}).get("a_igualar"), 0.0)
                            if analytics_logger is not None:
                                try:
                                    analytics_logger.registrar_decision(
                                        dom_data=dom_data,
                                        decision=decision,
                                        executed_action=accion_ejecutada,
                                        decision_source=decision_source,
                                        response_time_ms=dt * 1000.0,
                                        hand_number=tracker.hand_number,
                                        position_name=tracker.position_name,
                                        opp_id=tracker.opp_id,
                                        opp_nick=tracker.opp_nick,
                                        ai_trace=getattr(agent, "last_trace", None),
                                    )
                                except Exception:
                                    pass
                            time.sleep(0.55)
                            ciclo += 1

                        # CASO 2: MODAL O MESA FINALIZADA DETECTADA (Fin de partida / Eliminado 'Vacío' / Replay / Sit-Out / Iniciar partida / PopupCheckGratis)
                        elif any(b in modales_o_botones for b in ['PopupCheckGratis', 'MesaFinalizadaVacia', 'Jugar de nuevo', 'Replay', 'Volver a jugar', 'Aceptar', 'Jugar Poker', 'Jugar']):
                            if 'PopupCheckGratis' in modales_o_botones:
                                resolver_popup_fold_check_gratis(target_mesa)
                                ultimo_tiempo_actividad = time.time()
                                continue
                            if 'MesaFinalizadaVacia' in modales_o_botones or modales_o_botones != ultimo_modal_id:
                                ultimo_modal_id = None if 'MesaFinalizadaVacia' in modales_o_botones else modales_o_botones
                                ultimo_tiempo_actividad = time.time()
                                print(f"🔄 [MODAL / MESA FINALIZADA DETECTADA ({modales_o_botones})] Reaccionando en automático para iniciar nueva partida...")

                                if 'Jugar de nuevo' in modales_o_botones or 'Replay' in modales_o_botones or 'Volver a jugar' in modales_o_botones:
                                    if not clicar_texto_modal_universal(target_mesa, ["Jugar de nuevo", "Replay", "Volver a jugar"], "Botón Jugar de Nuevo"):
                                        intentar_click(target_mesa, config.SELECTORS.get("btn_replay", config.SELECTORS["replay"]), "Botón Jugar de Nuevo", timeout=2000)
                                elif 'Jugar Poker' in modales_o_botones or 'Jugar' in modales_o_botones:
                                    if not intentar_click(target_mesa, config.SELECTORS["start_match_btn"], "Botón Jugar Poker", timeout=2000):
                                        clicar_texto_modal_universal(target_mesa, ["Jugar Poker", "Jugar"], "Botón Jugar Poker")
                                elif 'Aceptar' in modales_o_botones:
                                    if not intentar_click(target_mesa, "#general-popup-button-action-OK", "Botón Aceptar Sit-out", timeout=1500):
                                        clicar_texto_modal_universal(target_mesa, ["Aceptar", "OK"], "Botón Aceptar")
                                elif 'MesaFinalizadaVacia' in modales_o_botones:
                                    salir_de_mesa_vacia_o_finalizada(target_mesa, page, context)

                                time.sleep(1.8)
                                tracker.reset_mano()

                        # CASO 3: AUTORRECUPERACIÓN SI ESTAMOS EN EL LOBBY SIN ENTRAR A MESA HACE MÁS DE 60 SEGUNDOS
                        elif not es_mi_turno and not mis_cartas and (time.time() - ultimo_tiempo_actividad > 60):
                            ultimo_tiempo_actividad = time.time()
                            print("⏳ Tiempo de inactividad detectado en lobby. Re-ejecutando inicio de torneo automático...")
                            page = navegar_al_juego(page)

                        else:
                            time.sleep(0.05)

                        resiliencia.registrar_exito()

                    except KeyboardInterrupt:
                        raise
                    except Exception as e:
                        should_stop, backoff = resiliencia.registrar_error(e)
                        is_disc = detectar_desconexion(page=page, err=e)
                        log_json_estructurado(
                            "loop_error",
                            hand_id=f"hand_{tracker.hand_number}",
                            payload={
                                "error": str(e),
                                "error_type": type(e).__name__,
                                "consecutive_errors": resiliencia.consecutive_errors,
                                "backoff_sec": backoff,
                                "disconnection": is_disc,
                            },
                        )
                        print(f"ℹ️ Nota de autorrecuperación en el bucle (#{resiliencia.consecutive_errors}, backoff={backoff:.2f}s): {e}")
                        time.sleep(backoff)
                        if should_stop:
                            print("🛑 [PARADA SEGURA] Límite de errores consecutivos alcanzado. Deteniendo el bucle.")
                            break

            except KeyboardInterrupt:
                print("\n🛑 Bot detenido por el usuario (Ctrl + C).")
                if getattr(config, "HOLD_BROWSER_OPEN", True):
                    print("📌 NAVEGADOR MANTENIDO ABIERTO. Puedes seguir navegando o jugando manualmente.")
                    print("Presiona Enter en esta terminal cuando desees cerrar la ventana del navegador...")
                    try:
                        input()
                    except Exception:
                        pass

    finally:
        if analytics_logger is not None:
            try:
                analytics_logger.shutdown(wait=True)
            except Exception:
                pass
        bg_executor.shutdown(wait=False)

if __name__ == "__main__":
    import signal

    def _handle_sigterm(_signum, _frame):
        raise SystemExit(0)

    try:
        signal.signal(signal.SIGTERM, _handle_sigterm)
    except Exception:
        pass
    main()
