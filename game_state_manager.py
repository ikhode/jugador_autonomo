"""
Módulo de Gestión de Estado del Juego y MÁQUINA DE ESTADOS CANÓNICA (Clean Game State Architecture).
Basado en tres principios: snapshot autoritativo (10041), deltas versionados, e invariantes verificables.
"""
import time
import json
import threading
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple


@dataclass
class JugadorEstado:
    seat: int
    player_code: Optional[int] = None
    nickname: str = ""
    anonymous: bool = False
    device: str = ""
    chips: float = 0.0            # stack actual
    real_chips: float = 0.0
    bet_this_round: float = 0.0   # apuesta en la calle actual
    bet_this_hand: float = 0.0    # apuesta acumulada en la mano
    status: str = "UNKNOWN"       # ACTIVE | FOLDED | ALLIN | SITOUT | OUT | UNKNOWN
    is_aggressor: bool = False    # en la calle actual
    cards: List[str] = field(default_factory=list)  # solo si reveladas
    last_action_seq: int = -1
    last_action_ts: float = 0.0
    bounty: float = 0.0


@dataclass
class TablaEstado:
    container_code: int
    # --- versionado ---
    version: Tuple[int, ...] = (0,)          # última versión aplicada
    hand_id: Optional[int] = None
    # --- estructura de la mano ---
    phase: str = "WAITING"         # WAITING | ACTIVE | HAND_CLOSED
    round_no: int = 0              # 0=pre, 1..4
    board: List[str] = field(default_factory=list)
    pots: List[Dict[str, Any]] = field(default_factory=list)
    total_pot: float = 0.0
    # --- posiciones ---
    dealer_seat: Optional[int] = None
    sb_seat: Optional[int] = None
    bb_seat: Optional[int] = None
    turn_clockwise: bool = True
    # --- héroe ---
    hero_seat: Optional[int] = None
    hero_cards: List[str] = field(default_factory=list)
    # --- jugadores ---
    players: Dict[int, JugadorEstado] = field(default_factory=dict)
    seat_to_code: Dict[int, int] = field(default_factory=dict)
    code_to_seat: Dict[int, int] = field(default_factory=dict)
    # --- turno ---
    legal_actions: List[Dict[str, Any]] = field(default_factory=list)
    action_sequence: int = -1      # última seq conocida
    turn_timeout_ms: int = 0
    time_bank_left: int = 0
    # --- control de integridad ---
    expected_seq: int = 0          # siguiente actionSequence esperada
    last_msg_seq: int = 0
    desync: bool = False
    pending_resync: bool = False


# Helpers de parseo
def _cards_from(raw: Any) -> List[str]:
    if not raw:
        return []
    if isinstance(raw, list):
        res = []
        for c in raw:
            if isinstance(c, str):
                res.append(c)
            elif isinstance(c, dict):
                # Formato objeto iPoker {rank, suit}
                rank = str(c.get("rank") or c.get("r") or "")
                suit = str(c.get("suit") or c.get("s") or "")
                if rank and suit:
                    res.append(f"{rank}{suit}")
        return res
    return []


def _parse_version(v: Any) -> Optional[Tuple[int, ...]]:
    if v is None:
        return None
    if isinstance(v, (list, tuple)):
        return tuple(int(x) for x in v)
    if isinstance(v, int):
        return (v,)
    if isinstance(v, str):
        try:
            parts = [int(p) for p in v.replace(".", ",").split(",") if p.isdigit()]
            return tuple(parts) if parts else (0,)
        except Exception:
            return (0,)
    return (0,)


def _sum_pots(pots: List[Dict[str, Any]]) -> float:
    total = 0.0
    for p in pots:
        if isinstance(p, dict):
            total += float(p.get("sum") or p.get("pot") or p.get("amount") or 0.0)
    return total


def _map_status(raw_status: Any) -> str:
    if raw_status is None:
        return "ACTIVE"
    s = str(raw_status).upper()
    if "FOLD" in s:
        return "FOLDED"
    if "ALL" in s:
        return "ALLIN"
    if "SIT" in s or "AWAY" in s or "OUT" in s:
        return "SITOUT"
    return "ACTIVE"


def _inferir_round(board_len: int) -> int:
    return {0: 0, 3: 1, 4: 2, 5: 3}.get(board_len, 0)


def _inferir_hero(t: TablaEstado) -> Optional[int]:
    for seat, j in t.players.items():
        if j.cards or j.nickname.lower() in ("belnebu", "hero"):
            return seat
    return next(iter(t.players.keys())) if t.players else None


def _nombre_calle(round_no: int) -> str:
    return {0: "PREFLOP", 1: "FLOP", 2: "TURN", 3: "RIVER"}.get(round_no, "PREFLOP")


ACTION_CODES = {
    0: "FOLD",
    1: "CHECK",
    2: "CALL",
    3: "BET",
    4: "RAISE",
    5: "ALL_IN",
}


INVARIANTES = [
    ("pots_sum_consistente",
     lambda t: abs(t.total_pot - sum(float(p.get("sum", 0) or p.get("pot", 0) or p.get("amount", 0)) for p in t.pots)) < 1.0 if t.pots else True,
     "WARN"),
    ("seat_to_code_sin_duplicados",
     lambda t: len(t.seat_to_code) == len(set(t.seat_to_code.values())),
     "ERROR"),
    ("code_to_seat_consistente",
     lambda t: all(t.code_to_seat.get(c) == s for s, c in t.seat_to_code.items()),
     "ERROR"),
    ("hero_seat_valido",
     lambda t: t.hero_seat is None or t.hero_seat in t.players,
     "ERROR"),
    ("board_creciente",
     lambda t: len(t.board) in (0, 3, 4, 5),
     "ERROR"),
    ("chips_no_negativos",
     lambda t: all(p.chips >= 0 for p in t.players.values()),
     "WARN"),
]


def verificar_invariantes(tabla: TablaEstado, tracker=None) -> List[str]:
    fallos = []
    for nombre, fn, sev in INVARIANTES:
        try:
            if not fn(tabla):
                fallos.append(f"{sev}:{nombre}")
                if sev == "ERROR":
                    tabla.desync = True
        except Exception as e:
            fallos.append(f"ERR_EXC:{nombre}:{e}")
    if fallos and tracker and hasattr(tracker, "registrar_evento"):
        tracker.registrar_evento("WS_INVARIANTE", " | ".join(fallos))
    return fallos


def pedir_resync(ws, tabla: TablaEstado, tracker=None) -> None:
    if tabla.pending_resync:
        return
    tabla.pending_resync = True
    tabla.desync = False
    if tracker and hasattr(tracker, "registrar_evento"):
        tracker.registrar_evento("WS_RESYNC", f"estado corrupto en contenedor {tabla.container_code} → solicitando snapshot")

    try:
        if ws and hasattr(ws, "send"):
            payload = json.dumps({
                "classId": 50008,
                "containerIdsList": [tabla.container_code],
                "containerType": 0,
                "subscriptionType": 1,
                "playerMetadata": None,
                "requestId": f"resync_{tabla.container_code}_{time.time()}",
            })
            ws.send(payload)
    except Exception as e:
        if tracker and hasattr(tracker, "registrar_evento"):
            tracker.registrar_evento("WS_RESYNC_ERR", str(e))
        tabla.pending_resync = False
        return

    threading.Timer(2.0, lambda: _resync_timeout(ws, tabla, tracker)).start()


def _resync_timeout(ws, tabla: TablaEstado, tracker=None) -> None:
    if tabla.pending_resync and tabla.phase != "WAITING":
        if tracker and hasattr(tracker, "registrar_evento"):
            tracker.registrar_evento("WS_RESYNC_TIMEOUT", "reintentando resync...")
        tabla.pending_resync = False
        pedir_resync(ws, tabla, tracker)


class GameStateManager:
    """
    Gestor de estado centralizado y máquina de estados con arquitectura limpia (Snapshot + Deltas + Invariantes).
    Mantiene compatibilidad total con métodos legados.
    """
    def __init__(self):
        self.tablas: Dict[int, TablaEstado] = {}
        self.hero_seat_global: Optional[int] = None
        # Atributos legados de compatibilidad
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

    def tabla(self, container_code: int) -> TablaEstado:
        if container_code not in self.tablas:
            self.tablas[container_code] = TablaEstado(container_code=container_code)
        return self.tablas[container_code]

    def aplicar(self, d: dict, ws=None, tracker=None, profiler=None) -> None:
        if not isinstance(d, dict):
            return
        cid = d.get("classId")
        cont = d.get("containerCode") or d.get("containerId") or d.get("tableId") or 1

        # 1. Versionado para deltas
        if cid in {10008, 10010, 10019, 10022, 10029, 10011} and cont is not None:
            t = self.tabla(cont)
            v = _parse_version(d.get("version"))
            if v and v < t.version:
                return  # Reorder descartado
            if v:
                t.version = v

        # 2. Snapshot 10041 (Reemplazo total)
        if cid == 10041:
            self._aplicar_snapshot(d, tracker, profiler)
            return

        # 3. Deltas validados
        t = self.tabla(cont) if cont is not None else self.tabla(1)
        handler = self._DISPATCH.get(cid)
        if handler:
            handler(self, t, d, tracker, profiler, ws)

        # 4. Invariantes
        if t is not None:
            verificar_invariantes(t, tracker)
            if t.desync:
                pedir_resync(ws, t, tracker)

    def _aplicar_snapshot(self, d: dict, tracker=None, profiler=None) -> None:
        cont = d.get("containerCode") or d.get("tableId") or 1
        old = self.tabla(cont)
        t = TablaEstado(container_code=cont)

        t.seat_to_code = dict(old.seat_to_code)
        t.code_to_seat = dict(old.code_to_seat)

        t.hand_id = d.get("handId")
        t.phase = "ACTIVE"
        t.board = _cards_from(d.get("communityCards") or d.get("communityGameCards"))
        t.round_no = _inferir_round(len(t.board))
        t.pots = d.get("pots") or []
        t.total_pot = _sum_pots(t.pots)
        t.dealer_seat = d.get("dealerSeatIndex")
        t.sb_seat = d.get("sbSeatIndex")
        t.bb_seat = d.get("bbSeatIndex")
        t.turn_clockwise = bool(d.get("turnDirectionClockwise", True))
        t.version = _parse_version(d.get("version")) or (0,)
        t.action_sequence = -1
        t.expected_seq = 0

        for p in d.get("players") or []:
            seat = p.get("seatIndex")
            if seat is None:
                continue
            code = p.get("playerCode") or t.seat_to_code.get(seat)
            nick = p.get("nickname") or ""
            j = JugadorEstado(
                seat=seat,
                player_code=code,
                nickname=nick,
                anonymous=bool(p.get("anonymousPlayer")),
                device=p.get("deviceCategory") or "",
                chips=float(p.get("chips") or 0),
                real_chips=float(p.get("realChips") or 0),
                status=_map_status(p.get("status")),
                bounty=float(p.get("bounty") or 0),
            )
            t.players[seat] = j
            if code:
                t.seat_to_code[seat] = code
                t.code_to_seat[code] = seat

        for ps in d.get("playerStateList") or []:
            seat = ps.get("playerSeat")
            j = t.players.get(seat)
            if not j:
                continue
            j.bet_this_round = float(ps.get("bet") or 0)
            j.bet_this_hand = float(ps.get("gameBet") or 0)
            cards = _cards_from(ps.get("cards"))
            if cards and (seat == self.hero_seat_global or j.nickname.lower() in ("belnebu", "hero")):
                t.hero_cards = cards

        t.hero_seat = self.hero_seat_global or _inferir_hero(t)
        self.tablas[cont] = t
        t.pending_resync = False

        if tracker and hasattr(tracker, "registrar_evento"):
            tracker.registrar_evento(
                "WS_SNAPSHOT",
                f"hand={t.hand_id} players={len(t.players)} pot={t.total_pot:.2f} board={t.board}"
            )

        # Sincronizar estado legado
        self.current_game_state.update({
            "bote": t.total_pot,
            "cartas_comunitarias": ",".join(t.board),
            "calle": _nombre_calle(t.round_no),
        })

    def _aplicar_10008(self, t: TablaEstado, d: dict, tracker=None) -> None:
        p = d.get("player") or d
        seat = p.get("seatIndex") or p.get("seat")
        code = p.get("playerCode")
        if seat is not None and code is not None:
            t.seat_to_code[int(seat)] = int(code)
            t.code_to_seat[int(code)] = int(seat)
            if seat in t.players:
                t.players[seat].player_code = int(code)

    def _aplicar_10010(self, t: TablaEstado, d: dict, tracker=None) -> None:
        if "dealerSeatIndex" in d:
            t.dealer_seat = d.get("dealerSeatIndex")
        if "sbSeatIndex" in d:
            t.sb_seat = d.get("sbSeatIndex")
        if "bbSeatIndex" in d:
            t.bb_seat = d.get("bbSeatIndex")

    def _aplicar_10011(self, t: TablaEstado, d: dict, tracker=None, profiler=None) -> None:
        t.phase = "HAND_CLOSED"
        if "pots" in d:
            t.pots = d.get("pots") or []
            t.total_pot = _sum_pots(t.pots)

    def _aplicar_10017(self, t: TablaEstado, d: dict, tracker=None, ws=None) -> None:
        seq = d.get("actionSequence")
        if seq is not None:
            t.action_sequence = int(seq)
        if "legalActions" in d:
            t.legal_actions = d.get("legalActions") or []

    def _aplicar_10019(self, t: TablaEstado, d: dict, tracker=None) -> None:
        seat = d.get("seatIndex") or d.get("seat")
        if seat in t.players:
            t.players[seat].status = _map_status(d.get("status"))

    def _aplicar_10022(self, t: TablaEstado, d: dict, tracker=None, profiler=None, ws=None) -> None:
        if "pots" in d:
            t.pots = d.get("pots") or []
            t.total_pot = _sum_pots(t.pots)
        for j in t.players.values():
            j.bet_this_round = 0.0
            j.is_aggressor = False

    def _aplicar_10029(self, t: TablaEstado, d: dict, tracker=None, profiler=None, ws=None) -> None:
        seq = d.get("actionSequence")
        if seq is not None:
            seq_int = int(seq)
            if t.expected_seq > 0 and seq_int != t.expected_seq:
                if seq_int < t.expected_seq:
                    return
                t.desync = True
                return
            t.action_sequence = seq_int
            t.expected_seq = seq_int + 1

        seat = d.get("seatIndex")
        j = t.players.get(seat)
        if not j:
            return

        amt = float(d.get("actionAmount") or 0)
        new_chips = float(d.get("chips") or 0)
        j.chips = new_chips
        j.real_chips = float(d.get("realChips") or new_chips)
        j.bet_this_round += amt
        j.bet_this_hand += amt
        j.is_aggressor = bool(d.get("isAggressor"))
        j.last_action_seq = t.action_sequence
        j.last_action_ts = time.time()

        code = d.get("actionCode")
        if code == 0:
            j.status = "FOLDED"
        elif d.get("isAggressor") and new_chips == 0:
            j.status = "ALLIN"
        elif j.status not in ("ALLIN", "FOLDED"):
            j.status = "ACTIVE"

        if amt > 0:
            t.total_pot += amt

        kind = ACTION_CODES.get(code, f"ACTION_{code}")
        self.update_historical_actions(kind)

        if profiler and hasattr(profiler, "registrar_accion_rival") and seat != t.hero_seat and j.player_code:
            try:
                profiler.registrar_accion_rival(
                    str(j.player_code), j.nickname, kind,
                    calle=_nombre_calle(t.round_no),
                    hand_id=t.hand_id
                )
            except Exception:
                pass

    def _verificar_con_10069(self, t: TablaEstado, d: dict, tracker=None) -> None:
        if d.get("handId") and d.get("handId") != t.hand_id:
            return
        t.phase = "HAND_CLOSED"

    _DISPATCH = {
        10008: _aplicar_10008,
        10010: _aplicar_10010,
        10011: _aplicar_10011,
        10017: _aplicar_10017,
        10019: _aplicar_10019,
        10022: _aplicar_10022,
        10029: _aplicar_10029,
        10069: _verificar_con_10069,
        60152: _aplicar_10017,
    }

    # Métodos de compatibilidad legados
    def transicionar_estado(self, nuevo_estado: str) -> None:
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
        self.tablas.clear()
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
