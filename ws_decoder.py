"""
Módulo de Decodificación de WebSockets (iPoker / Codere) — versión completa y avanzada.

Cubre los 57 classId observados en la captura network-capture-2026-11-17T08-19-01.
Socket.IO EIO=3, evento único "message", payload discriminado por classId.

Incluye telemetría avanzada de precisión:
- Filtrado de Muck en Hand History (10069)
- Guardia de seguridad de cartas privadas de Hero (10041)
- Análisis de mesas blandas (avgPlayersFlop, avgPot) (30001)
- Tracking de Timebank de oponentes (10017)
- Device category & metadatos recreacionales (10008)
- Equity y odds de all-in (10082)
- Server-calculated HUD stats (94007)
"""
from __future__ import annotations

import json
import logging
import queue
import threading
import time
from typing import Any, Dict, List, Optional, Tuple

import config
from poker_gto_engine import formatear_carta_canonica

log = logging.getLogger("poker.ws")


# =========================================================================
# 0. Utilidades
# =========================================================================

def _parse_version(v: Any) -> Optional[Tuple[int, ...]]:
    """Normaliza versiones numéricas o string ('1.0', 3) a tuplas comparables."""
    if v is None or isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return (int(v),)
    parts = str(v).split(".")
    out: List[int] = []
    for p in parts:
        try:
            out.append(int(p))
        except ValueError:
            out.append(0)
    return tuple(out) if out else None


def _unwrap_socketio(payload_str: str) -> Optional[Dict[str, Any]]:
    """Extrae el dict de payload iPoker de una trama Socket.IO."""
    if payload_str.startswith(("42", "40", "41", "43", "44")):
        try:
            arr = json.loads(payload_str[2:])
            if isinstance(arr, list) and len(arr) >= 2 and isinstance(arr[1], dict):
                return arr[1]
        except Exception:
            pass
    i, j = payload_str.find("{"), payload_str.rfind("}")
    if i != -1 and j > i:
        try:
            return json.loads(payload_str[i:j + 1].replace('\\"', '"'))
        except Exception:
            return None
    return None


def _sum_pots(pots: Any) -> float:
    if not isinstance(pots, list):
        try:
            return float(pots or 0)
        except Exception:
            return 0.0
    return float(sum(float(p.get("sum") or p.get("pot") or 0)
                     for p in pots if isinstance(p, dict)))


def _cards_from(lista: Any) -> List[str]:
    if not isinstance(lista, list):
        return []
    return [formatear_carta_canonica(c)
            for c in lista if isinstance(c, str) and c not in ("X", "")]


def _card_from_obj(c: Any) -> Optional[str]:
    """Convierte {rank, suit} o string a notación canónica."""
    if isinstance(c, str):
        return formatear_carta_canonica(c) if c and c != "X" else None
    if isinstance(c, dict):
        r = (c.get("rank") or "").upper()
        s = (c.get("suit") or "").lower()
        if r and s:
            return formatear_carta_canonica(r + s)
    return None


# Códigos de acción
ACTION_CODES = {
    0: "FOLD",
    3: "CALL",
    4: "CHECK",
    6: "BET",
    7: "ALL_IN",
    23: "RAISE",
}


# =========================================================================
# 1. Entrada pública
# =========================================================================

def configurar_interceptores(
    target_page: Any,
    tracker: Any,
    profiler: Any,
    analytics_logger: Any,
    table_tracker: Any = None,
) -> None:
    try:
        _registrar_http(target_page, tracker, profiler)
        target_page.on("websocket", lambda ws: _registrar_ws(
            ws, tracker, profiler, analytics_logger, table_tracker))
    except Exception:
        log.exception("configurar_interceptores")


# =========================================================================
# 2. HTTP (señales de audio / assets)
# =========================================================================

def _registrar_http(target_page: Any, tracker: Any, profiler: Any) -> None:
    def on_response(response: Any) -> None:
        try:
            url = response.url
            low = url.lower()
            if not any(k in low for k in ("poker", "game", "api",
                                          "gamelauncher", "codere", "pariplay")):
                return

            if "my_turn.mp3" in url:
                print("🔊 [SEÑAL HTTP] ➔ ¡Es nuestro turno! (my_turn.mp3)")
                tracker.http_turn_signal = True
                tracker.registrar_evento("SONIDO", "Es mi turno (my_turn.mp3)")
                return
            if "card_dealt.mp3" in url:
                tracker.registrar_evento("SONIDO", "Cartas repartidas")
                return
            if "my_player_won.mp3" in url:
                print("🎉 [SEÑAL HTTP] ➔ ¡Hemos ganado la mano!")
                tracker.registrar_evento("SONIDO", "¡Mano Ganada!")
                tracker.finalizar_mano_actual("Mano Ganada (Bote cobrado)")
                return

            action_map = {
                "chips_placed.mp3": "BET", "action_bet.png": "BET",
                "player_call.mp3": "CALL", "action_call.png": "CALL",
                "chip_call.png": "CALL",
                "player_fold.mp3": "FOLD", "action_fold.png": "FOLD",
                "player_check.mp3": "CHECK", "action_check.png": "CHECK",
            }
            for key, kind in action_map.items():
                if key in url:
                    tracker.registrar_evento("ACCION_MESA", f"{kind} (audio/img)")
                    if (len(tracker.active_opponents) <= 2
                            and (tracker.opp_id or tracker.opp_nick)
                            and (time.time() - tracker.hero_just_acted_ts > 1.2)
                            and (time.time() - tracker.ws_action_ts > 1.0)):
                        profiler.registrar_accion_rival(
                            tracker.opp_id, tracker.opp_nick, kind,
                            calle=tracker.current_street, hand_id=tracker.hand_number)
                    return

            if "cards-classic-assets" in url and url.endswith(".svg"):
                card = url.split("/")[-1].replace(".svg", "").upper()
                tracker.registrar_evento("CARTA_SVG", f"Carta cargada: {card}")
        except Exception:
            pass

    target_page.on("response", on_response)


# =========================================================================
# 3. WebSocket — setup y estado local
# =========================================================================

def _registrar_ws(
    ws: Any,
    tracker: Any,
    profiler: Any,
    analytics_logger: Any,
    table_tracker: Any,
) -> None:
    print(f"🔌 [WS CONECTADO] {ws.url}")

    event_queue: queue.Queue = queue.Queue(maxsize=1000)
    stop_event = threading.Event()

    def _worker():
        while not stop_event.is_set():
            try:
                msg = event_queue.get(timeout=0.05)
                if msg is None:
                    break
                _procesar_recv_sync(msg)
                event_queue.task_done()
            except queue.Empty:
                continue
            except Exception:
                pass

    worker_thread = threading.Thread(target=_worker, daemon=True)
    worker_thread.start()

    st: Dict[str, Any] = {
        "versions": {},
        "last_seq": 0,
        "pending": {},
        "last_action_seq": {},
        "unknown_actions": {},
        "legal_actions": {},   # seat -> {seq, actions[]}
        "action_label": {},    # code -> "CALL" | "FOLD" | ...
        "pending_label": {},   # pending sent action mapping
        "hand_actions": [],    # lista ordenada de acciones de la mano
        "last_action_ts_global": None,
        "round_start_ts": time.time(),
    }

    def _label_action(a: Dict[str, Any], my_stack: float) -> str:
        mn = float(a.get("minimumSum") or 0)
        mx = float(a.get("maximumSum") or 0)
        if mn == 0 and mx == 0:
            return "CHECK_OR_FOLD"
        if mn == mx:
            return "ALL_IN" if (my_stack and mn >= my_stack) else "CALL"
        return "BET_OR_RAISE"

    def _ack_heartbeat() -> None:
        try:
            ws.send(f'42["message",{{"classId":60006,"messageSeqId":{st["last_seq"]}}}]')
        except Exception:
            pass

    def _accept_version(container: Any, version: Any) -> bool:
        vp = _parse_version(version)
        if container is None or vp is None:
            return True
        last = st["versions"].get(container)
        if last is not None and vp < last:
            return False
        st["versions"][container] = vp
        return True

    def _cont(d: Dict) -> Any:
        return d.get("containerCode") or d.get("containerId") or d.get("tableId")

    def _check_pending(d: Dict) -> None:
        rid = d.get("requestId")
        if rid and rid in st["pending"]:
            elapsed = (time.time() - st["pending"].pop(rid)) * 1000
            err = d.get("errorCode")
            if err not in (None, 0):
                tracker.registrar_evento(
                    "WS_ERR_RESP", f"req={rid} errorCode={err} ({elapsed:.0f}ms)")

    # =====================================================================
    # FAMILIA 10xxx — Juego / mesa
    # =====================================================================

    def h_10001(d):
        tracker.registrar_evento(
            "WS_SEAT_INFO",
            f"container={_cont(d)} seat={d.get('seat')} reserved={d.get('reserved')}")

    def h_10007(d):
        bal = d.get("playerBalance")
        tracker.registrar_evento(
            "WS_SESSION",
            f"balance={bal} realChips={d.get('realChips')} "
            f"playing={d.get('isPlayerPlaying')} rate={d.get('currencyRate')}")
        try:
            tracker.current_balance = float(bal or 0)
        except Exception:
            pass

    def h_10008(d):  # join/leave/update con deviceCategory y metadatos
        if table_tracker is not None and hasattr(table_tracker, "procesar_ws_10008"):
            try:
                table_tracker.procesar_ws_10008(d)
            except Exception:
                pass
        p = d.get("player") or {}
        seat = p.get("seatIndex")
        nick = p.get("nickname")
        chips = float(p.get("chips") or 0)
        device = p.get("deviceCategory")
        anonymous = p.get("anonymousPlayer")
        rem = p.get("removeSeatIndex")

        if seat is not None:
            if not hasattr(tracker, "opp_metadata"):
                tracker.opp_metadata = {}
            tracker.opp_metadata[seat] = {
                "device": device,
                "anonymous": anonymous,
                "nickname": nick,
                "chips": chips,
            }

        if rem not in (None, -1):
            tracker.registrar_evento("WS_LEAVE", f"seat={seat} {nick}")
        elif nick:
            tracker.registrar_evento(
                "WS_PLAYER", f"seat={seat} {nick} chips={chips} device={device} anonymous={anonymous}")
        if table_tracker is not None and hasattr(table_tracker, "upsert_player"):
            try:
                table_tracker.upsert_player(p)
            except Exception:
                pass

    def h_10010(d):
        tracker.registrar_evento(
            "WS_CONFIG_RONDA",
            f"dealer={d.get('dealer')} SB={d.get('sbSeatIndex')} "
            f"BB={d.get('bbSeatIndex')} dir={d.get('gameDirection')}")

    def h_10011(d):
        winners = d.get("winners") or []
        summary: List[str] = []
        for w in winners:
            for wh in (w.get("winningHands") or []):
                hs = (wh.get("handStrengthEvent") or {})
                summary.append(
                    f"seat={w.get('seatIndex')} "
                    f"hand={hs.get('handType')} "
                    f"str={hs.get('handStrength')} "
                    f"win={wh.get('winningAmount')}")
                cards = _cards_from(wh.get("winningCards"))
                if cards and (tracker.opp_id or tracker.opp_nick):
                    profiler.registrar_showdown(
                        tracker.opp_id, tracker.opp_nick, " ".join(cards))
        tracker.registrar_evento(
            "WS_SHOWDOWN",
            f"rake={d.get('rake')} winners=[{'; '.join(summary)}]")
        tracker.last_pot = _sum_pots(d.get("pots") or [])

    def h_10017(d):  # turno con timebank tracking y auto-etiquetado de acciones legales
        actions = d.get("actions") or []
        seat = d.get("seatIndex")
        timebank = d.get("timeBankLeft")
        timeout = d.get("timeout")
        seq = d.get("actionSequence")
        tracker.http_turn_signal = True
        st["last_action_seq"][_cont(d)] = seq

        if seat is not None:
            if not hasattr(tracker, "opp_timebank"):
                tracker.opp_timebank = {}
            tracker.opp_timebank[seat] = timebank

        my_stack = getattr(tracker, "my_stack", 0.0)
        st["legal_actions"][seat] = {"seq": seq, "actions": actions}
        for a in actions:
            code = a.get("actionCode")
            if code is not None:
                guess = _label_action(a, my_stack)
                if code not in st["action_label"]:
                    st["action_label"][code] = guess

        tracker.registrar_evento(
            "WS_TURNO",
            f"seat={seat} seq={seq} timeout={timeout}ms timebank={timebank}s "
            f"actions={[(a.get('actionCode'), a.get('minimumSum'), a.get('maximumSum'), st['action_label'].get(a.get('actionCode'))) for a in actions]}")
        if table_tracker is not None:
            try:
                table_tracker.actualizar_jugador(seat=seat, is_turn=True)
            except Exception:
                pass

    def h_10019(d):
        for ps in d.get("playerStates") or []:
            tracker.registrar_evento(
                "WS_PSTATE",
                f"seat={ps.get('playerSeat')} reason={d.get('reasonCode')}")

    def h_10022(d):
        pots = _sum_pots(d.get("pots"))
        tracker.last_pot = pots
        st["round_start_ts"] = time.time()
        st["last_action_ts_global"] = None
        tracker.registrar_evento(
            "WS_ROUND_CLOSE",
            f"round={d.get('roundNo')} pots={pots:.2f} rake={d.get('rake')} "
            f"acciones={[a['kind'] for a in st['hand_actions'][-10:]]}")

    def h_10029(d):
        seat = d.get("seatIndex")
        code = d.get("actionCode")
        amt = float(d.get("actionAmount") or 0.0)
        chips = float(d.get("chips") or 0.0)
        agg = bool(d.get("isAggressor"))
        src = d.get("actionSource")
        seq = d.get("actionSequence")
        now = time.time()

        hero_seat = getattr(tracker, "hero_seat", None)
        if hero_seat is not None and seat == hero_seat and code == st.get("pending_label", {}).get("code"):
            intent = st["pending_label"].get("intent")
            if intent and code not in st["action_label"]:
                st["action_label"][code] = intent
                tracker.registrar_evento("WS_MAP", f"code {code} = {intent} (confirmado por servidor)")

        kind = ACTION_CODES.get(code)
        if kind is None:
            kind = st["action_label"].get(code, "AGGRO" if agg else "PASIVO")
            if code not in st["action_label"]:
                st["unknown_actions"][code] = st["unknown_actions"].get(code, 0) + 1

        last_ts = st.get("last_action_ts_global") or st["round_start_ts"]
        think_ms = int((now - last_ts) * 1000)
        st["last_action_ts_global"] = now

        entry = {
            "seq": seq,
            "seat": seat,
            "kind": kind,
            "amount": amt,
            "stack_after": chips,
            "is_aggressor": agg,
            "auto": (src == 0),
            "think_ms": think_ms,
            "t": now,
        }
        st["hand_actions"].append(entry)

        # Perfilado: solo decisiones humanas reales (src == 1) y oponentes
        if src == 1 and seat != hero_seat:
            pcode = table_tracker.resolver_code_por_asiento(seat) if table_tracker else None
            pnick = (table_tracker.code_to_nick.get(pcode) if (table_tracker and pcode) else None) or f"seat{seat}"
            p_key = pcode or (table_tracker._fallback_key(pnick, seat) if table_tracker else f"seat:{seat}")
            if p_key:
                profiler.registrar_accion_rival(
                    p_key, pnick, kind,
                    calle=tracker.current_street,
                    hand_id=tracker.hand_number,
                )

        who = "YO" if seat == hero_seat else f"seat{seat}"
        tracker.ws_action_ts = now
        tracker.registrar_evento(
            "WS_ACCION",
            f"seq={seq} {who} {kind} amt={amt:.2f} stack={chips:.2f} agg={agg} src={src} think={think_ms}ms")

        if src == 0:
            tracker.registrar_evento("WS_TIMEOUT", f"seat={seat} acción automática ({kind}) — NO perfilable")

        if table_tracker is not None:
            try:
                table_tracker.actualizar_jugador(
                    player_id=f"Seat_{seat}", seat=seat,
                    bet=amt, stack=chips,
                    status="FOLD" if "FOLD" in kind else "ACTIVO")
            except Exception:
                pass

    def h_10041(d):  # snapshot con guardia de seguridad de Hero
        if table_tracker is not None and hasattr(table_tracker, "procesar_ws_10041"):
            try:
                table_tracker.procesar_ws_10041(d)
            except Exception:
                pass
        st["hand_actions"] = []
        st["round_start_ts"] = time.time()
        st["last_action_ts_global"] = None
        tracker.iniciar_nueva_mano(
            table_id=str(_cont(d) or ""),
            game_id=str(d.get("handId") or ""),
            round_id=str(d.get("currentRoundCode") or ""))
        comm = d.get("communityCards") or d.get("communityGameCards") or []
        tracker.community_cards = _cards_from(comm)
        tracker.last_pot = _sum_pots(d.get("pots"))

        tracker.dealer_seat = d.get("dealerSeatIndex")
        tracker.sb_seat = d.get("sbSeatIndex")
        tracker.bb_seat = d.get("bbSeatIndex")

        if table_tracker is not None and hasattr(table_tracker, "aplicar_snapshot"):
            try:
                table_tracker.aplicar_snapshot(d)
            except Exception:
                pass

        hero_seat = getattr(table_tracker, "hero_seat", None) or getattr(tracker, "hero_seat", None)
        for ps in d.get("playerStateList") or []:
            seat = ps.get("playerSeat")
            cards = _cards_from(ps.get("cards"))
            if hero_seat is not None and seat == hero_seat and len(cards) == 2:
                tracker.my_cards = cards
                tracker.registrar_evento("WS_CARTAS", f"Mis cartas (Snapshot): {cards}")
            if table_tracker is not None:
                try:
                    table_tracker.actualizar_jugador(
                        seat=seat, bet=float(ps.get("bet") or 0))
                except Exception:
                    pass

        for p in d.get("players") or []:
            if table_tracker is not None and hasattr(table_tracker, "upsert_player"):
                try:
                    table_tracker.upsert_player(p)
                except Exception:
                    pass

    def h_10044(d):
        tracker.registrar_evento(
            "WS_TIMED", f"type={d.get('type')} time={d.get('time')}")

    def h_10069(d):  # historial completo con filtrado de muck
        try:
            hero_nick = (getattr(config, "HERO_NINKNAME", "") or "").lower()
            players = d.get("players") or []
            for p in players:
                nick = str(p.get("nickname") or "").lower()
                is_hero = hero_nick and nick == hero_nick
                muck = bool(p.get("muck", False))
                cards = _cards_from(p.get("cards") or p.get("finalGameCards") or [])

                if not is_hero and cards and not muck:
                    player_code = str(p.get("playerCode") or p.get("playerSeat") or "")
                    if player_code or nick:
                        profiler.registrar_showdown(player_code, nick, " ".join(cards))
                        tracker.registrar_evento("WS_SHOWDOWN_RIVAL", f"Rival {nick} mostró cartas (no mukeó): {cards}")
                elif not is_hero and muck:
                    tracker.registrar_evento("WS_MUCK", f"Rival {nick} mukeó cartas")

            if analytics_logger is not None:
                analytics_logger.registrar_evento_tiempo_real({
                    "event_type": "HAND_HISTORY",
                    "street": "SHOWDOWN",
                    "pot_size": float(_sum_pots(d.get("roundPots") or [])),
                    "extra_data": {"handId": d.get("handId"), "rake": d.get("rake")},
                })
        except Exception:
            log.exception("h_10069")

    def h_10078(d):
        tracker.registrar_evento(
            "WS_SITOUT", f"action={d.get('action')} reason={d.get('sitOutReason')}")

    def h_10082(d):  # all-in equity tracking
        odds = d.get("seatsToAllInOdds") or []
        if not hasattr(tracker, "last_equity"):
            tracker.last_equity = {}
        for o in odds:
            seat = o.get("seatIndex")
            val = o.get("oddsInString")
            if seat is not None:
                tracker.last_equity[seat] = val
        tracker.registrar_evento(
            "WS_ALLIN_EQUITY",
            f"odds={[(o.get('seatIndex'), o.get('oddsInString')) for o in odds]}")

    def h_10089(d):  # rabbit hunt / futuro board
        cards: List[str] = []
        for c in d.get("cards") or []:
            card = _card_from_obj(c)
            if card:
                cards.append(card)
        if cards:
            tracker.registrar_evento("WS_RABBIT_HUNT", f"Cartas futuras / Rabbit hunt: {cards}")

    def h_10092(d):
        outs = d.get("playersToSeeAllInOuts") or []
        tracker.registrar_evento(
            "WS_ALLIN_OUTS",
            f"outs={[(o.get('seat'), o.get('outsCount')) for o in outs]}")

    # =====================================================================
    # FAMILIA 20xxx — Salida de mesa
    # =====================================================================

    def h_20074(d):
        tracker.registrar_evento("WS_LEAVE_TABLE", f"reason={d.get('reason')}")
        if hasattr(tracker, "destruir_estado_mano"):
            tracker.destruir_estado_mano("Salida de mesa")

    # =====================================================================
    # FAMILIA 30xxx — Listado de mesas cash con análisis de blandura
    # =====================================================================

    def h_30001(d):
        tables = d.get("cashTablesList") or []
        _check_pending(d)
        soft_tables = []
        for t in tables:
            grid = t.get("cashGrid") or {}
            side = t.get("cashSide") or {}
            avg_flop = float(grid.get("avgPlayersFlop") or 0.0)
            avg_pot = float(grid.get("avgPot") or 0.0)
            if avg_flop > 0.50:
                soft_tables.append(f"{grid.get('name')} (Flop: {avg_flop*100:.0f}%, Pot: {avg_pot})")
        tracker.registrar_evento("WS_CASH_LIST", f"mesas={len(tables)}, soft_tables={soft_tables}")
        if hasattr(tracker, "on_cash_tables"):
            try:
                tracker.on_cash_tables(tables)
            except Exception:
                pass

    # =====================================================================
    # FAMILIA 50xxx — Lobby
    # =====================================================================

    def h_50009(d):
        _check_pending(d)

    def h_50018(d):
        _check_pending(d)

    def h_50024(d):
        for j in d.get("jackpotInfoList") or []:
            tracker.registrar_evento(
                "WS_JACKPOT", f"{j.get('type')} order={j.get('order')} amt={j.get('amount')}")

    def h_50027(d):
        tracker.registrar_evento(
            "WS_CHANNEL", f"players={d.get('playersConnectedChannel')}")

    def h_50055(d):
        _check_pending(d)

    def h_50087(d):
        action = d.get("action")
        tipo = d.get("lobbyContentType")
        for w in d.get("lobbyWidgets") or []:
            wtype = w.get("lobbyWidgetType")
            for wc in w.get("widgetContainers") or []:
                cat = wc.get("gameCategoryData") or {}
                if cat.get("currentPlayersCount") is not None:
                    tracker.registrar_evento(
                        "WS_LOBBY",
                        f"{tipo}/{action} {wtype} {cat.get('currentPlayersCount')} players")

    # =====================================================================
    # FAMILIA 60xxx — Sesión / canal / notas / bonos / balances / heartbeat
    # =====================================================================

    def h_60005(d):
        _ack_heartbeat()

    def h_60028(d):
        _check_pending(d)

    def h_60034(d):
        err_msg = d.get("message", "Error en mesa")
        tracker.registrar_evento("WS_ERROR", err_msg)

    def h_60049(d):
        pass

    def h_60051(d):
        _check_pending(d)

    def h_60065(d):
        pass

    def h_60113(d):  # balance real
        bal = d.get("finalBalance")
        tracker.registrar_evento("WS_BALANCE", f"{d.get('currencyCode')} final={bal}")
        try:
            tracker.current_balance = float(bal or 0)
        except Exception:
            pass

    def h_60121(d):
        pass

    def h_60152(d):  # turno alternativo con selectores
        actions = d.get("actions") or []
        seq = d.get("actionSequence")
        st["last_action_seq"][_cont(d)] = seq
        tracker.http_turn_signal = True

        my_stack = getattr(tracker, "my_stack", 0.0)
        for a in actions:
            code = a.get("actionCode")
            if code is not None:
                guess = _label_action(a, my_stack)
                if code not in st["action_label"]:
                    st["action_label"][code] = guess

        tracker.registrar_evento(
            "WS_TURNO_SELECTORS",
            f"seq={seq} actions={[(a.get('actionCode'), a.get('minimumSum'), a.get('maximumSum'), st['action_label'].get(a.get('actionCode'))) for a in actions]}")

    def h_60218(d):
        pass

    # =====================================================================
    # FAMILIA 90xxx — Promociones / animaciones
    # =====================================================================

    def h_90018(d):
        _check_pending(d)

    def h_90020(d):
        _check_pending(d)

    def h_90029(d):
        pass

    # =====================================================================
    # FAMILIA 94xxx — Server HUD Stats
    # =====================================================================

    def h_94007(d):
        if table_tracker is not None and hasattr(table_tracker, "procesar_ws_94007"):
            try:
                table_tracker.procesar_ws_94007(d)
            except Exception:
                pass
        for g in d.get("hudGameTypeStats") or []:
            for s in g.get("hudTableStats") or []:
                seat = s.get("seatIndex")
                pcode = s.get("playerCode")
                hands_played = s.get("handsPlayed")
                stats = {st.get("hudType"): st.get("value") for st in (s.get("hudTypeStat") or [])}
                tracker.registrar_evento(
                    "WS_HUD_SERVER",
                    f"seat={seat} player={pcode} hands={hands_played} stats={stats}")

    # =====================================================================
    # Registro de handlers (recv)
    # =====================================================================

    handlers_recv = {
        10001: h_10001, 10007: h_10007, 10008: h_10008, 10010: h_10010,
        10011: h_10011, 10017: h_10017, 10019: h_10019, 10022: h_10022,
        10029: h_10029, 10041: h_10041, 10044: h_10044, 10069: h_10069,
        10078: h_10078, 10080: lambda d: _check_pending(d), 10082: h_10082,
        10089: h_10089, 10092: h_10092, 20074: h_20074, 30001: h_30001,
        50009: h_50009, 50018: h_50018, 50024: h_50024, 50027: h_50027,
        50055: h_50055, 50087: h_50087, 60005: h_60005, 60028: h_60028,
        60034: h_60034, 60049: h_60049, 60051: h_60051, 60065: h_60065,
        60113: h_60113, 60121: h_60121, 60152: h_60152, 60218: h_60218,
        90018: h_90018, 90020: h_90020, 90029: h_90029, 94007: h_94007,
    }

    def _procesar_recv_sync(payload_str: str) -> None:
        try:
            if payload_str == "2":
                try:
                    ws.send("3")
                except Exception:
                    pass
                return

            d = _unwrap_socketio(payload_str)
            if not isinstance(d, dict):
                return

            cid = d.get("classId")
            ms = d.get("messageSeqId")
            if isinstance(ms, int) and ms > st["last_seq"]:
                st["last_seq"] = ms

            if not _accept_version(_cont(d), d.get("version")):
                return

            if analytics_logger is not None:
                try:
                    analytics_logger.registrar_evento_tiempo_real({
                        "event_type": f"WS_CLASS_{cid}",
                        "street": tracker.current_street,
                        "pot_size": float(getattr(tracker, "last_pot", 0) or 0),
                        "extra_data": {"classId": cid, "container": _cont(d)},
                    })
                except Exception:
                    pass

            h = handlers_recv.get(cid)
            if h is not None:
                h(d)

            if "nickname" in d:
                tracker.opp_nick = d["nickname"]
            if "playerCode" in d:
                tracker.opp_id = str(d["playerCode"])

        except Exception:
            log.exception("_procesar_recv_sync")

    def procesar_send(payload_str: str) -> None:
        try:
            d = _unwrap_socketio(payload_str)
            if not isinstance(d, dict):
                return
            cid = d.get("classId")
            rid = d.get("requestId")
            if rid:
                st["pending"][rid] = time.time()

            if cid in (10023, 60024):
                tracker.hero_just_acted_ts = time.time()
                code = d.get("actionCode")
                st["pending_label"] = {"code": code, "intent": ACTION_CODES.get(code, "ACTION")}
                tracker.registrar_evento(
                    "WS_ENVIO_ACCION",
                    f"enviamos code={code} seq={d.get('actionSequence')}")
        except Exception:
            log.exception("procesar_send")

    def _on_frame_recv(payload: Any) -> None:
        s = payload.decode("utf-8", errors="ignore") if isinstance(payload, bytes) else str(payload)
        try:
            event_queue.put_nowait(s)
        except queue.Full:
            log.warning("WebSocket event queue full, dropping frame.")

    def _on_frame_sent(payload: Any) -> None:
        s = payload.decode("utf-8", errors="ignore") if isinstance(payload, bytes) else str(payload)
        procesar_send(s)

    try:
        ws.on("framereceived", _on_frame_recv)
        ws.on("framesent", _on_frame_sent)
    except Exception:
        log.warning("Playwright framesent no soportado, operando sólo con framereceived.")
        ws.on("framereceived", _on_frame_recv)
