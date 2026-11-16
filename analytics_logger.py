"""
Logger Asíncrono Instrumentado (Fase 1 y Fase 6) para integrar el Sistema de Analíticas en `bot.py`.

Características de confiabilidad industrial:
- Registra tanto la acción realmente ejecutada en el DOM (`executed_action` -> `action_taken`)
  como la acción propuesta por la IA (`intended_action`).
- Utiliza una cola acotada (`queue.Queue`) y un hilo consumidor dedicado con drenado seguro (`shutdown` + `atexit`).
- Mide empíricamente con `time.perf_counter_ns()` la latencia de encolado en el hilo principal (`enqueue_latency_ms`),
  el tiempo de escritura en SQLite (`sqlite_write_ms`), tareas pendientes, errores de almacenamiento y uso de memoria RSS,
  calculando percentiles reales P50, P95 y P99.
"""
import atexit
import queue
import re
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from analytics_db import AnalyticsStorage, DB_PATH, generar_uid_determinista
from analytics_diagnostics import obtener_uso_memoria_rss_mb, resumir_distribucion_latencia
from analytics_engine import evaluar_decision_estrategica
from poker_gto_engine import clasificar_mano_preflop


def _extraer_numero(val: Any, default: float = 0.0) -> float:
    if isinstance(val, (int, float)):
        return float(val)
    texto = str(val or "").strip()
    if not texto:
        return default
    partes = texto.split("|")
    m = re.search(r"\d+(?:[.,]\d+)?", partes[0].replace(",", ""))
    return float(m.group(0)) if m else default


def _extraer_acciones_legales(opciones_str: str, to_call: float) -> List[str]:
    if not opciones_str:
        return ["CHECK", "BET", "RAISE", "ALL_IN"] if to_call <= 0 else ["FOLD", "CALL", "RAISE", "ALL_IN"]
    acciones = []
    up = opciones_str.upper()
    for token in ("FOLD", "CHECK", "CALL", "BET", "RAISE", "ALLIN", "ALL_IN"):
        if token in up:
            norm = "ALL_IN" if token in ("ALLIN", "ALL_IN") else token
            if norm not in acciones:
                acciones.append(norm)
    return acciones or (["CHECK", "BET", "RAISE"] if to_call <= 0 else ["FOLD", "CALL", "RAISE"])


def _determinar_calle(board_str: str, current_street: str = "PREFLOP") -> str:
    cartas = [c for c in (board_str or "").strip().split() if c and c != "X"]
    n = len(cartas)
    if n == 0:
        return "PREFLOP"
    if n == 3:
        return "FLOP"
    if n == 4:
        return "TURN"
    if n >= 5:
        return "RIVER"
    return "FLOP" if current_street == "POSTFLOP" else "PREFLOP"


class PokerAnalyticsLogger:
    """
    Recolector de telemetría en tiempo real para `bot.py`.
    Mantiene el estado de la mano activa protegido por cerrojo reentrante (`RLock`)
    y despacha escrituras SQLite en un worker secundario con métricas P50/P95/P99.
    """

    def __init__(
        self,
        version_id: str = "1.0",
        version_description: str = "Configuración activa del bot",
        modified_params: Optional[Dict[str, Any]] = None,
        parent_version: str = "",
        mode: str = "live",
        model_name: str = "gemini-3.1-flash-lite",
        initial_bankroll: float = 1500.0,
        big_blind: float = 20.0,
        db_path: Path = DB_PATH,
        async_mode: bool = True,
        max_queue_size: int = 2000,
    ):
        self.storage = AnalyticsStorage(db_path=db_path)
        self.version_id = version_id
        self.mode = mode
        self.is_synthetic = (mode == "benchmark")
        self.big_blind = max(1.0, float(big_blind or 20.0))
        self.current_bankroll = float(initial_bankroll)
        self.current_ev_bankroll = float(initial_bankroll)
        self.async_mode = async_mode
        self._lock = threading.RLock()
        self._last_closed_signature: Optional[str] = None

        # Contadores e historiales de telemetría de rendimiento (Fase 6)
        self.enqueue_latencies_ms: List[float] = []
        self.sqlite_write_latencies_ms: List[float] = []
        self.decision_latencies_ms: List[float] = []
        self.storage_errors_count: int = 0
        self.dropped_records_count: int = 0
        self.processed_tasks_count: int = 0

        self._queue: Optional[queue.Queue] = queue.Queue(maxsize=max_queue_size) if async_mode else None
        self._stop_event = threading.Event()
        self._worker_thread: Optional[threading.Thread] = None

        self.storage.registrar_version(
            version_id=version_id,
            description=version_description,
            modified_params=modified_params or {},
            parent_version=parent_version,
        )
        self.session_id = self.storage.iniciar_sesion(
            version_id=version_id,
            mode=mode,
            model_name=model_name,
            initial_bankroll=initial_bankroll,
            buy_in_total=500.0,
            big_blind=self.big_blind,
            is_synthetic=self.is_synthetic,
        )
        self._reset_active_hand_state(hand_number=1)

        if self.async_mode and self._queue is not None:
            self._worker_thread = threading.Thread(
                target=self._worker_loop,
                name="PokerAnalyticsAsyncWorker",
                daemon=True,
            )
            self._worker_thread.start()
            atexit.register(self.shutdown)

    def _reset_active_hand_state(self, hand_number: int = 1) -> None:
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with self._lock:
            self.active_hand: Dict[str, Any] = {
                "hand_number": hand_number,
                "started_at": now_str,
                "scenario_id": "",
                "position": "BTN",
                "hole_cards": "",
                "board_flop": "",
                "board_turn": "",
                "board_river": "",
                "final_board": "",
                "reached_street": "PREFLOP",
                "saw_flop": False,
                "went_to_showdown": False,
                "is_allin": False,
                "vpip_flag": False,
                "pfr_flag": False,
                "three_bet_opp": False,
                "three_bet_flag": False,
                "faced_three_bet": False,
                "fold_to_three_bet": False,
                "cbet_flop_opp": False,
                "cbet_flop_flag": False,
                "faced_cbet_flop": False,
                "fold_to_cbet_flop": False,
                "hero_stack_start": 500.0,
                "hero_stack_last": 500.0,
                "effective_stack": 500.0,
                "pot_max": 0.0,
                "invested_chips": 0.0,
                "decision_count": 0,
                "ev_lost_sum_chips": 0.0,
                "last_equity": 0.5,
                "opponent_id": "",
                "opponent_nick": "",
                "opponent_profile": "REGULAR EQUILIBRADO",
                "folded": False,
                "hand_uid": generar_uid_determinista(self.session_id, self.version_id, hand_number, ""),
                "last_turn_signature": "",
            }

    def _worker_loop(self) -> None:
        while not self._stop_event.is_set() or (self._queue is not None and not self._queue.empty()):
            if self._queue is None:
                break
            try:
                item = self._queue.get(timeout=0.1)
            except queue.Empty:
                continue

            if item is None:
                self._queue.task_done()
                break

            fn, args, kwargs = item
            try:
                fn(*args, **kwargs)
                self.processed_tasks_count += 1
            except Exception:
                self.storage_errors_count += 1
            finally:
                self._queue.task_done()

    def _dispatch(self, fn: Callable, *args: Any, **kwargs: Any) -> Any:
        t0_ns = time.perf_counter_ns()
        if self.async_mode and self._queue is not None and not self._stop_event.is_set():
            try:
                self._queue.put_nowait((fn, args, kwargs))
                enq_ms = round((time.perf_counter_ns() - t0_ns) / 1_000_000.0, 4)
                self.enqueue_latencies_ms.append(enq_ms)
                if args and isinstance(args[0], dict):
                    args[0]["enqueue_latency_ms"] = enq_ms
                return None
            except queue.Full:
                self.dropped_records_count += 1
                return None
        else:
            enq_ms = round((time.perf_counter_ns() - t0_ns) / 1_000_000.0, 4)
            self.enqueue_latencies_ms.append(enq_ms)
            if args and isinstance(args[0], dict):
                args[0]["enqueue_latency_ms"] = enq_ms
            try:
                res = fn(*args, **kwargs)
                self.processed_tasks_count += 1
                return res
            except Exception:
                self.storage_errors_count += 1
                return None

    def flush(self, timeout: float = 5.0) -> None:
        """Espera a que se procesen todas las tareas actualmente encoladas."""
        if self.async_mode and self._queue is not None:
            t_start = time.time()
            while not self._queue.empty() and (time.time() - t_start) < timeout:
                time.sleep(0.01)
            try:
                self._queue.join()
            except Exception:
                pass

    def shutdown(self, wait: bool = True, timeout: float = 5.0) -> None:
        """Drena la cola asíncrona y detiene el hilo consumidor sin perder registros."""
        if self._stop_event.is_set():
            return
        if wait:
            self.flush(timeout=timeout)
        self._stop_event.set()
        if self._queue is not None:
            try:
                self._queue.put_nowait(None)
            except Exception:
                pass
        if self._worker_thread is not None and self._worker_thread.is_alive():
            self._worker_thread.join(timeout=timeout)
        try:
            self.storage.actualizar_sesion(self.session_id, status="COMPLETED")
        except Exception:
            pass

    def registrar_evento_tiempo_real(self, event_data: Dict[str, Any]) -> None:
        """Encola la escritura asíncrona de un evento en tiempo real en SQLite."""
        data = dict(event_data or {})
        if "session_id" not in data or not data["session_id"]:
            data["session_id"] = self.session_id
        if "hand_uid" not in data or not data["hand_uid"]:
            with self._lock:
                data["hand_uid"] = self.active_hand.get("hand_uid", "")
        self._dispatch(self.storage.registrar_evento_tiempo_real, data)

    def registrar_decision(
        self,
        dom_data: Dict[str, Any],
        decision: Any,
        decision_source: str = "gemini",
        response_time_ms: float = 0.0,
        hand_number: int = 1,
        position_name: str = "BTN",
        opp_id: str = "",
        opp_nick: str = "",
        scenario_id: str = "",
        executed_action: Optional[str] = None,
        ai_trace: Optional[Any] = None,
        decision_uid: Optional[str] = None,
    ) -> None:
        """
        Captura los datos de una decisión realmente ejecutada por el bot y despacha su evaluación y guardado.
        """
        intended_act = str(getattr(decision, "accion", "WAIT") or "WAIT").upper().strip()
        real_act = str(executed_action or intended_act).upper().strip()

        if response_time_ms > 0:
            self.decision_latencies_ms.append(float(response_time_ms))

        trace_dict: Dict[str, Any] = {}
        if ai_trace is not None:
            if hasattr(ai_trace, "model_dump"):
                trace_dict = ai_trace.model_dump()
            elif isinstance(ai_trace, dict):
                trace_dict = dict(ai_trace)

        snapshot = {
            "dom_data": dict(dom_data or {}),
            "decision_uid": decision_uid or trace_dict.get("decision_uid") or "",
            "intended_action": intended_act,
            "action_taken": real_act,
            "bet_amount": getattr(decision, "monto_apuesta", 0) or 0,
            "confidence": getattr(decision, "confianza", 0.8) or 0.8,
            "decision_source": decision_source,
            "response_time_ms": float(response_time_ms or 0.0),
            "enqueue_latency_ms": 0.0,
            "hand_number": hand_number,
            "position_name": position_name,
            "opp_id": opp_id,
            "opp_nick": opp_nick,
            "scenario_id": scenario_id,
            "ai_trace": trace_dict,
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }
        self._dispatch(self._procesar_decision_sync, snapshot)

    def _procesar_decision_sync(self, snap: Dict[str, Any]) -> Optional[str]:
        t_write_start = time.perf_counter_ns()
        dom = snap["dom_data"]
        act = str(snap["action_taken"] or "WAIT").upper().strip()
        intended_act = str(snap.get("intended_action") or act).upper().strip()
        if act in ("WAIT", "START_GAME", "REPLAY"):
            return None

        with self._lock:
            hand_no = int(snap["hand_number"] or self.active_hand["hand_number"])
            if hand_no != self.active_hand["hand_number"]:
                self._reset_active_hand_state(hand_number=hand_no)

            raw_hole_cards = str(dom.get("mis_cartas") or self.active_hand["hole_cards"] or "").strip()
            board = str(dom.get("cartas_comunitarias") or "").strip()
            street = _determinar_calle(board)

            # Si el DOM envió un bote explícito válido o estamos en mesa activa
            raw_bote_val = dom.get("bote")
            pot_size = _extraer_numero(raw_bote_val, default=self.big_blind * 1.5 if raw_bote_val is None else 0.0)
            to_call = _extraer_numero(dom.get("a_igualar"), default=0.0)
            my_stack = _extraer_numero(dom.get("mi_stack"), default=500.0)
            opp_stack = _extraer_numero(dom.get("stack_rival"), default=500.0)
            eff_stack = min(my_stack, opp_stack) if (my_stack > 0 and opp_stack > 0) else max(my_stack, 500.0)

            perfil_rival = dom.get("perfil_rival") or {}
            opp_profile = str(perfil_rival.get("estilo") or self.active_hand["opponent_profile"] or "REGULAR EQUILIBRADO")
            opp_id = str(snap["opp_id"] or self.active_hand["opponent_id"] or "")
            opp_nick = str(snap["opp_nick"] or perfil_rival.get("nickname") or self.active_hand["opponent_nick"] or "")

            pos_raw = str(snap.get("position_name") or dom.get("posicion") or self.active_hand["position"] or "").upper()
            if "DEALER" in pos_raw or "BUTTON" in pos_raw or "BTN" in pos_raw:
                pos = "BTN"
            elif "SB" in pos_raw or "SMALL" in pos_raw:
                pos = "SB"
            elif "BB" in pos_raw or "BIG" in pos_raw:
                pos = "BB"
            else:
                pos = str(dom.get("posicion") or "BTN").upper()
                if pos not in ("BTN", "SB", "BB"):
                    pos = "BTN"

            # Evitar incrementar el índice si se registra dos veces el mismo estado de turno
            turn_signature = f"{street}|{raw_hole_cards}|{board}|{pot_size}|{to_call}"
            if turn_signature != self.active_hand.get("last_turn_signature"):
                self.active_hand["decision_count"] += 1
                self.active_hand["last_turn_signature"] = turn_signature
            dec_idx = max(1, self.active_hand["decision_count"])

            if dec_idx == 1:
                self.active_hand["hero_stack_start"] = my_stack if my_stack > 0 else 500.0
                self.active_hand["effective_stack"] = eff_stack
            self.active_hand["hero_stack_last"] = my_stack if my_stack > 0 else self.active_hand["hero_stack_last"]
            if raw_hole_cards:
                self.active_hand["hole_cards"] = raw_hole_cards
            self.active_hand["position"] = pos
            self.active_hand["opponent_id"] = opp_id
            self.active_hand["opponent_nick"] = opp_nick
            self.active_hand["opponent_profile"] = opp_profile
            if snap.get("scenario_id"):
                self.active_hand["scenario_id"] = snap["scenario_id"]

            # UID determinista por (session_id, version_id, hand_no, scenario_id)
            self.active_hand["hand_uid"] = generar_uid_determinista(
                self.session_id,
                self.version_id,
                hand_no,
                self.active_hand["scenario_id"],
            )

            if pot_size > self.active_hand["pot_max"]:
                self.active_hand["pot_max"] = pot_size

            if street == "FLOP":
                self.active_hand["saw_flop"] = True
                self.active_hand["board_flop"] = board
                self.active_hand["reached_street"] = "FLOP"
            elif street == "TURN":
                self.active_hand["saw_flop"] = True
                self.active_hand["board_turn"] = board
                self.active_hand["reached_street"] = "TURN"
            elif street == "RIVER":
                self.active_hand["saw_flop"] = True
                self.active_hand["board_river"] = board
                self.active_hand["reached_street"] = "RIVER"
            if board:
                self.active_hand["final_board"] = board

            bet_amt = float(snap["bet_amount"] or 0.0)
            if act in ("ALL_IN", "ALLIN") or bet_amt >= eff_stack * 0.9:
                self.active_hand["is_allin"] = True

            if street == "PREFLOP":
                if act in ("CALL", "BET", "RAISE", "ALL_IN", "ALLIN"):
                    self.active_hand["vpip_flag"] = True
                if act in ("BET", "RAISE", "ALL_IN", "ALLIN"):
                    self.active_hand["pfr_flag"] = True
                if to_call > self.big_blind * 1.2:
                    if not self.active_hand["pfr_flag"] or dec_idx == 1:
                        self.active_hand["three_bet_opp"] = True
                        if act in ("RAISE", "ALL_IN", "ALLIN"):
                            self.active_hand["three_bet_flag"] = True
                    else:
                        self.active_hand["faced_three_bet"] = True
                        if act == "FOLD":
                            self.active_hand["fold_to_three_bet"] = True
            elif street == "FLOP":
                if self.active_hand["pfr_flag"] and to_call == 0:
                    self.active_hand["cbet_flop_opp"] = True
                    if act in ("BET", "RAISE", "ALL_IN", "ALLIN"):
                        self.active_hand["cbet_flop_flag"] = True
                elif to_call > 0 and not self.active_hand["cbet_flop_flag"]:
                    self.active_hand["faced_cbet_flop"] = True
                    if act == "FOLD":
                        self.active_hand["fold_to_cbet_flop"] = True

            if act == "CALL":
                self.active_hand["invested_chips"] += to_call
            elif act in ("BET", "RAISE", "ALL_IN", "ALLIN"):
                inv = bet_amt if bet_amt > to_call else max(to_call + self.big_blind, self.big_blind * 2.0)
                self.active_hand["invested_chips"] += min(eff_stack, inv)
            elif act == "FOLD":
                self.active_hand["folded"] = True

            opciones_raw = str(dom.get("opciones_disponibles") or dom.get("botones_visibles") or "")
            legal_actions = _extraer_acciones_legales(opciones_raw, to_call)

            eval_res = evaluar_decision_estrategica(
                hole_cards=raw_hole_cards,
                board=board,
                pot_size=pot_size,
                to_call=to_call,
                effective_stack=eff_stack,
                action_taken=act,
                bet_amount=bet_amt,
                position=pos,
                street=street,
                opponent_profile=opp_profile,
                legal_actions=legal_actions,
                big_blind=self.big_blind,
            )

            self.active_hand["ev_lost_sum_chips"] += float(eval_res["ev_lost_chips"])
            if eval_res.get("evaluation_available", True):
                self.active_hand["last_equity"] = float(eval_res["estimated_equity"])

            hand_uid_now = self.active_hand["hand_uid"]
            scen_id_now = self.active_hand["scenario_id"]

        enq_ms = float(snap.get("enqueue_latency_ms") or 0.0)
        write_ms_pre = round((time.perf_counter_ns() - t_write_start) / 1_000_000.0, 4)

        ai_trace = snap.get("ai_trace") or {}
        decision_record = {
            "decision_uid": snap.get("decision_uid") or ai_trace.get("decision_uid") or None,
            "hand_uid": hand_uid_now,
            "session_id": self.session_id,
            "version_id": self.version_id,
            "scenario_id": scen_id_now,
            "is_synthetic": self.is_synthetic,
            "timestamp": snap["timestamp"],
            "decision_index": dec_idx,
            "position": pos,
            "street": street,
            "hole_cards": raw_hole_cards,
            "board": board,
            "pot_size": pot_size,
            "to_call": to_call,
            "effective_stack": eff_stack,
            "big_blind": self.big_blind,
            "pot_size_bucket": eval_res.get("pot_size_bucket", "MEDIUM (4-12 BB)"),
            "stack_depth_bucket": eval_res.get("stack_depth_bucket", "DEEP (>20 BB)"),
            "intended_action": intended_act,
            "action_taken": eval_res["action_taken"],
            "bet_amount": bet_amt,
            "legal_actions": legal_actions,
            "evaluation_available": eval_res.get("evaluation_available", True),
            "gto_recommended_action": eval_res["gto_recommended_action"],
            "gto_recommended_sizing": eval_res["gto_recommended_sizing"],
            "gto_mixed_frequencies": eval_res.get("gto_mixed_frequencies", {}),
            "is_gto_aligned": eval_res["is_gto_aligned"],
            "action_deviation": eval_res["action_deviation"],
            "ev_by_action": eval_res["ev_by_action"],
            "ev_taken_chips": eval_res["ev_taken_chips"],
            "ev_best_chips": eval_res["ev_best_chips"],
            "ev_lost_chips": eval_res["ev_lost_chips"],
            "ev_lost_bb": eval_res["ev_lost_bb"],
            "estimated_equity": eval_res["estimated_equity"],
            "pot_odds_pct": eval_res["pot_odds_pct"],
            "decision_quality": eval_res["decision_quality"],
            "is_strategic_error": eval_res["is_strategic_error"],
            "is_missed_opportunity": eval_res.get("is_missed_opportunity", False),
            "error_description": eval_res["error_description"],
            "opponent_id": opp_id,
            "opponent_nick": opp_nick,
            "opponent_profile": opp_profile,
            "opponent_adaptation_aligned": eval_res["opponent_adaptation_aligned"],
            "decision_source": snap["decision_source"],
            "confidence": snap["confidence"],
            "response_time_ms": snap["response_time_ms"],
            "enqueue_latency_ms": enq_ms,
            "sqlite_write_ms": write_ms_pre,
            "prompt_version": ai_trace.get("prompt_version", ""),
            "prompt_hash": ai_trace.get("prompt_hash", ""),
            "model_used": ai_trace.get("model_used", ""),
            "retry_count": ai_trace.get("retry_count", 0),
            "raw_response": ai_trace.get("raw_response", ""),
            "fallback_reason": ai_trace.get("fallback_reason", ""),
            "trace_json": ai_trace,
        }
        saved_dec_uid = self.storage.guardar_decision(decision_record)

        # Real-time Reinforcement: registrar inmediatamente en la memoria si hubo desviación o EV subóptimo
        if float(eval_res.get("ev_lost_bb") or 0.0) >= 0.2 or eval_res.get("is_strategic_error"):
            try:
                from poker_experience_memory import PokerExperienceMemory
                exp_mem = PokerExperienceMemory()
                exp_mem.auto_evaluar_y_registrar_mano_perdedora(
                    hand_tier=dom.get("gto_preflop", {}).get("tier", "DESCONOCIDO"),
                    board_texture=dom.get("gto_textura", {}).get("textura_mesa", "DESCONOCIDA"),
                    opp_archetype=opp_profile,
                    accion_tomada=act,
                    net_chips=-float(eval_res.get("ev_lost_chips") or 0.0),
                    ev_diff=-float(eval_res.get("ev_lost_bb") or 0.0),
                    street=street,
                    position=pos,
                    big_blind=self.big_blind,
                    decision_quality=eval_res.get("decision_quality", ""),
                )
                exp_mem.close()
            except Exception:
                pass

        total_write_ms = round((time.perf_counter_ns() - t_write_start) / 1_000_000.0, 4)
        self.sqlite_write_latencies_ms.append(total_write_ms)
        pending_q = self._queue.qsize() if self._queue is not None else 0

        try:
            self.storage.registrar_telemetria({
                "session_id": self.session_id,
                "version_id": self.version_id,
                "timestamp": snap["timestamp"],
                "event_type": "DECISION_WRITE",
                "decision_latency_ms": snap["response_time_ms"],
                "enqueue_latency_ms": enq_ms,
                "sqlite_write_ms": total_write_ms,
                "memory_rss_mb": obtener_uso_memoria_rss_mb(),
                "pending_tasks": pending_q,
                "dropped_records": self.dropped_records_count,
                "storage_errors": self.storage_errors_count,
            })
        except Exception:
            pass

        return saved_dec_uid

    def finalizar_mano(
        self,
        resultado_str: str,
        hand_number: Optional[int] = None,
        hole_cards: Optional[str] = None,
        community_cards: Optional[str] = None,
        pot_final: Optional[float] = None,
        net_profit_chips: Optional[float] = None,
        ev_net_chips: Optional[float] = None,
        went_to_showdown: Optional[bool] = None,
        won_hand: Optional[bool] = None,
    ) -> None:
        """
        Cierra la mano actual y despacha el registro en `analytics_hands` evitando cierres duplicados.
        """
        with self._lock:
            snap_hand = dict(self.active_hand)
            if hand_number is not None:
                snap_hand["hand_number"] = hand_number
            if hole_cards:
                snap_hand["hole_cards"] = hole_cards
            if community_cards:
                snap_hand["final_board"] = community_cards
            if pot_final is not None:
                snap_hand["pot_max"] = max(snap_hand["pot_max"], float(pot_final))

            close_sig = f"{self.session_id}|{self.version_id}|{snap_hand['hand_number']}|{snap_hand.get('scenario_id', '')}"
            if close_sig == self._last_closed_signature and snap_hand.get("decision_count", 0) == 0:
                return
            self._last_closed_signature = close_sig

            snap_hand["resultado_str"] = resultado_str
            snap_hand["explicit_net_chips"] = net_profit_chips
            snap_hand["explicit_ev_chips"] = ev_net_chips
            snap_hand["explicit_wtsd"] = went_to_showdown
            snap_hand["explicit_won"] = won_hand
            snap_hand["ended_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

            next_no = int(snap_hand["hand_number"]) + 1
            self._reset_active_hand_state(hand_number=next_no)

        self._dispatch(self._procesar_fin_mano_sync, snap_hand)

    def _procesar_fin_mano_sync(self, h: Dict[str, Any]) -> Optional[str]:
        hole_cards = str(h.get("hole_cards") or "").strip()
        if not hole_cards and h.get("decision_count", 0) == 0:
            return None

        # Descartar manos duplicadas/fantasmas sin decisiones y sin cambio de fichas en vivo
        if h.get("decision_count", 0) == 0 and not h.get("is_synthetic"):
            res_upper_test = str(h.get("resultado_str") or "").upper()
            won_test = h.get("explicit_won") or any(k in res_upper_test for k in ("GANAD", "WON", "COBRADO"))
            net_test = float(h.get("explicit_net_chips") or 0.0)
            if not won_test and net_test == 0.0:
                return None

        res_upper = str(h.get("resultado_str") or "").upper()
        won = h.get("explicit_won")
        if won is None:
            won = ("GANADA" in res_upper or "WON" in res_upper) and not h.get("folded", False)

        pot_final = max(float(h.get("pot_max") or 0.0), self.big_blind * 1.5)
        invested = max(
            float(h.get("invested_chips") or 0.0),
            self.big_blind * 0.5 if h.get("position") == "SB" else (self.big_blind if h.get("position") == "BB" else 0.0),
        )

        if h.get("explicit_net_chips") is not None:
            net_chips = float(h["explicit_net_chips"])
        else:
            if won:
                net_chips = max(self.big_blind, pot_final - invested)
            else:
                net_chips = -invested

        eq = float(h.get("last_equity") or 0.5)
        ev_lost = float(h.get("ev_lost_sum_chips") or 0.0)
        if h.get("explicit_ev_chips") is not None:
            ev_chips = float(h["explicit_ev_chips"])
        else:
            ev_chips = round((eq * pot_final - invested) - ev_lost * 0.35, 2)

        final_board = str(h.get("final_board") or "")
        reached_st = _determinar_calle(final_board, h.get("reached_street", "PREFLOP"))
        saw_flop = bool(h.get("saw_flop") or len(final_board.split()) >= 3)

        wtsd = h.get("explicit_wtsd")
        if wtsd is None:
            wtsd = bool(saw_flop and reached_st == "RIVER" and not h.get("folded", False))

        is_allin = bool(h.get("is_allin") or invested >= float(h.get("effective_stack") or 500.0) * 0.80)
        allin_ev_chips = round(eq * pot_final - invested, 2) if (is_allin or wtsd) else ev_chips

        net_bb = round(net_chips / self.big_blind, 4)
        ev_bb = round(ev_chips / self.big_blind, 4)
        allin_ev_bb = round(allin_ev_chips / self.big_blind, 4)

        with self._lock:
            self.current_bankroll = round(self.current_bankroll + net_chips, 2)
            self.current_ev_bankroll = round(self.current_ev_bankroll + ev_chips, 2)
            br_after = self.current_bankroll
            ev_br_after = self.current_ev_bankroll

        pre_info = clasificar_mano_preflop(hole_cards)

        hand_uid = generar_uid_determinista(
            self.session_id,
            self.version_id,
            h["hand_number"],
            h.get("scenario_id", ""),
        )

        hand_record = {
            "hand_uid": hand_uid,
            "session_id": self.session_id,
            "version_id": self.version_id,
            "hand_number": h["hand_number"],
            "scenario_id": h.get("scenario_id", ""),
            "is_synthetic": self.is_synthetic,
            "started_at": h["started_at"],
            "ended_at": h["ended_at"],
            "position": h.get("position", "BTN"),
            "hole_cards": hole_cards,
            "preflop_tier": pre_info.get("tier", "DESCONOCIDO"),
            "preflop_score": pre_info.get("score", 0),
            "board_flop": h.get("board_flop", ""),
            "board_turn": h.get("board_turn", ""),
            "board_river": h.get("board_river", ""),
            "final_board": final_board,
            "reached_street": reached_st,
            "saw_flop": saw_flop,
            "went_to_showdown": wtsd,
            "won_at_showdown": bool(wtsd and won),
            "won_hand": bool(won),
            "vpip_flag": bool(h.get("vpip_flag")),
            "pfr_flag": bool(h.get("pfr_flag")),
            "three_bet_opp": bool(h.get("three_bet_opp")),
            "three_bet_flag": bool(h.get("three_bet_flag")),
            "faced_three_bet": bool(h.get("faced_three_bet")),
            "fold_to_three_bet": bool(h.get("fold_to_three_bet")),
            "cbet_flop_opp": bool(h.get("cbet_flop_opp")),
            "cbet_flop_flag": bool(h.get("cbet_flop_flag")),
            "faced_cbet_flop": bool(h.get("faced_cbet_flop")),
            "fold_to_cbet_flop": bool(h.get("fold_to_cbet_flop")),
            "hero_stack_start": float(h.get("hero_stack_start") or 500.0),
            "hero_stack_end": round(float(h.get("hero_stack_start") or 500.0) + net_chips, 2),
            "effective_stack": float(h.get("effective_stack") or 500.0),
            "big_blind": self.big_blind,
            "pot_final": pot_final,
            "invested_chips": invested,
            "won_chips": round(invested + net_chips, 2) if won else 0.0,
            "net_profit_chips": net_chips,
            "net_profit_bb": net_bb,
            "ev_net_chips": ev_chips,
            "ev_net_bb": ev_bb,
            "allin_ev_chips": allin_ev_chips,
            "allin_ev_bb": allin_ev_bb,
            "is_allin": is_allin,
            "bankroll_after": br_after,
            "ev_bankroll_after": ev_br_after,
            "opponent_id": h.get("opponent_id", ""),
            "opponent_nick": h.get("opponent_nick", ""),
            "opponent_profile": h.get("opponent_profile", "REGULAR EQUILIBRADO"),
            "result_reason": str(h.get("resultado_str") or ""),
        }
        saved_uid = self.storage.guardar_mano(hand_record)
        self.storage.actualizar_sesion(self.session_id, status="ACTIVE")
        if self.storage.db_path == DB_PATH:
            try:
                from analytics_dashboard import generar_dashboard_html
                generar_dashboard_html(db_path=self.storage.db_path, auto_seed_benchmark_if_empty=False)
            except Exception:
                pass
        return saved_uid

    def registrar_verificacion_showdown(
        self,
        opp_id: str,
        opp_nick: str,
        cartas_mostradas: str,
        perfil_estimado: Dict[str, Any],
    ) -> None:
        """
        Verifica en el Showdown si las cartas mostradas por el rival son coherentes
        con la tendencia/perfil estimado por `OpponentProfiler`.
        """
        with self._lock:
            h_uid = self.active_hand.get("hand_uid") or f"sd_{int(time.time())}"
        snap = {
            "hand_uid": h_uid,
            "opp_id": opp_id or opp_nick or "rival",
            "opp_nick": opp_nick or opp_id or "Rival",
            "cartas": cartas_mostradas,
            "perfil": dict(perfil_estimado or {}),
        }
        self._dispatch(self._procesar_verificacion_sync, snap)

    def _procesar_verificacion_sync(self, snap: Dict[str, Any]) -> str:
        pre = clasificar_mano_preflop(snap["cartas"])
        tier = pre.get("tier", "C")
        estilo = str(snap["perfil"].get("estilo") or "REGULAR EQUILIBRADO").upper()

        verified = True
        note = f"Rival ({estilo}) mostró {pre.get('mano_code') or snap['cartas']} (Tier {tier})."
        if "NIT" in estilo and tier in ("C", "BASURA"):
            verified = False
            note += " Desviación: un NIT mostró mano débil fuera de su rango esperado."
        elif "REGULAR" in estilo and tier == "BASURA":
            verified = False
            note += " Desviación: rango más amplio de lo estimado para Regular."
        else:
            note += " Patrón consistente con las tendencias estimadas."

        return self.storage.guardar_verificacion_oponente({
            "hand_uid": snap["hand_uid"],
            "session_id": self.session_id,
            "version_id": self.version_id,
            "is_synthetic": self.is_synthetic,
            "opponent_id": snap["opp_id"],
            "opponent_nick": snap["opp_nick"],
            "predicted_profile": estilo,
            "predicted_vpip": float(snap["perfil"].get("vpip_pct") or 30.0),
            "predicted_pfr": float(snap["perfil"].get("pfr_pct") or 15.0),
            "predicted_af": float(snap["perfil"].get("agresividad") or 1.0),
            "observed_cards": snap["cartas"],
            "observed_tier": tier,
            "observed_line": "SHOWDOWN",
            "is_pattern_verified": verified,
            "verification_note": note,
        })

    def obtener_telemetria_logger(self) -> Dict[str, Any]:
        """Devuelve un resumen en tiempo real de las latencias P50/P95/P99 y estado de la cola."""
        return {
            "enqueue_latency": resumir_distribucion_latencia(self.enqueue_latencies_ms),
            "sqlite_write_latency": resumir_distribucion_latencia(self.sqlite_write_latencies_ms),
            "decision_latency": resumir_distribucion_latencia(self.decision_latencies_ms),
            "pending_tasks": self._queue.qsize() if self._queue is not None else 0,
            "processed_tasks": self.processed_tasks_count,
            "dropped_records": self.dropped_records_count,
            "storage_errors": self.storage_errors_count,
            "memory_rss_mb": obtener_uso_memoria_rss_mb(),
        }
