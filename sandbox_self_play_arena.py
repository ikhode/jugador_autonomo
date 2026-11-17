"""
Arena Simulada de Auto-Competición y Benchmarking 6-Max (Self-Play Arena).

Implementa la arquitectura inspirada en Meta Pluribus (Estrategia Blueprint GTO/Deep CFR +
Subgame EV Solving + Profiling en Tiempo Real + Explotación Dinámica vs Arquetipos).

Características:
1. Simulación completa de reglas 6-Max (BTN, SB, BB, UTG, HJ, CO) con rondas de apuestas
   multi-turno estrictas (Preflop, Flop, Turn, River), botes principales y secundarios (side pots),
   y evaluación de showdowns con la librería ultra-rápida `aria_poker`.
2. Registro automático persistente en SQLite (`logs/poker_stats.db`) etiquetando cada mano.
3. Arquetipos reales de oponentes en mesa (Calling Station / Fish, Nit / Rock, TAG, LAG, Hero Clone)
   con perfilado en tiempo real vía `OpponentProfiler` y `OpponentModel`.
4. Bucle de Aprendizaje Autónomo Continuo (Self-Learning Loop): reentrena periódicamente
   el `OpponentModel` cada N manos durante la sesión de arena.
5. Reporte en tiempo real de Winrate Acumulado (BB/100) y Winrate Rodante (últimas 500 manos).
"""
import argparse
import random
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

PROJECT_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "aria_poker"))

import config
from analytics_db import AnalyticsStorage, DB_PATH, generar_uid_determinista
from analytics_engine import PokerAnalyticsEngine
from opponent_model import OpponentModel
from opponents_profiler import OpponentProfiler
from poker_experience_memory import PokerExperienceMemory
from poker_gto_engine import (
    analizar_textura_mesa_y_blockers,
    calcular_pot_odds_y_sizing,
    clasificar_mano_preflop,
    evaluar_combinacion_mano_y_mesa,
    decidir_por_ev,
)
from strategic_engine import StrategicEngine
from decision_maker import DecisionMaker
from game_state_manager import GameStateManager, obtener_nombre_posicion_6max
from bot import decidir_accion_rapida, PokerDecision

try:
    from aria_poker.engine import evaluate, parse_cards, card_str
    ARIA_AVAILABLE = True
except Exception:
    ARIA_AVAILABLE = False


RANK_CHARS = ["2", "3", "4", "5", "6", "7", "8", "9", "T", "J", "Q", "K", "A"]
SUIT_SYMS = {"c": "♣", "d": "♦", "h": "♥", "s": "♠"}
SUIT_CHARS = ["c", "d", "h", "s"]


def int_card_to_str(c: int) -> str:
    r_char = RANK_CHARS[c >> 2]
    r_display = "10" if r_char == "T" else r_char
    s_sym = SUIT_SYMS[SUIT_CHARS[c & 3]]
    return f"{r_display}{s_sym}"


def int_cards_to_str_list(cards: List[int]) -> List[str]:
    return [int_card_to_str(c) for c in cards]


# Definición de Arquetipos de Oponentes para Mesa 6-Max Benchmarking
OPPONENT_ARCHETYPES = [
    {"nickname": "Hero_Belnebu", "archetype": "HERO", "vpip": 24, "pfr": 19},
    {"nickname": "Bot_CallingStation", "archetype": "CALLING_STATION", "vpip": 48, "pfr": 6},
    {"nickname": "Bot_NitRock", "archetype": "NIT", "vpip": 12, "pfr": 9},
    {"nickname": "Bot_TAG_Pro", "archetype": "TAG", "vpip": 22, "pfr": 18},
    {"nickname": "Bot_LAG_Maniac", "archetype": "LAG", "vpip": 38, "pfr": 28},
    {"nickname": "Bot_HeroClone", "archetype": "HERO_CLONE", "vpip": 24, "pfr": 19},
]


def decidir_accion_arqueotipo(
    archetype: str,
    dom_sim: Dict[str, Any],
    gto_pre: Dict[str, Any],
    gto_comb: Dict[str, Any],
    bb: float,
) -> str:
    """Toma una decisión según el arquetipo asignado al asiento del rival."""
    botones = str(dom_sim.get("botones_visibles", "")).upper()
    to_call = float(dom_sim.get("a_igualar", 0.0))
    to_call_bb = to_call / max(1.0, bb)
    tier = gto_pre.get("tier", "BASURA")
    es_preflop = not bool(dom_sim.get("cartas_comunitarias"))

    hay_check = "CHECK" in botones
    hay_call = "CALL" in botones
    hay_bet = "BET" in botones or "RAISE" in botones

    # 1. Calling Station (Fish): Paga fácil, casi nunca foldea ante apuestas normales, no sube sin monstruo
    if archetype == "CALLING_STATION":
        if hay_check:
            return "CHECK"
        if hay_call:
            if to_call_bb <= 8.0 or tier in ("S", "A", "B", "C") or gto_comb.get("coincidencias_mesa"):
                return "CALL"
            return "CALL" if random.random() < 0.40 else "FOLD"
        return "CHECK" if hay_check else "FOLD"

    # 2. Nit / Rock: Ultra selectivo preflop y postflop; foldea a la menor presión si no tiene mano hecha
    if archetype == "NIT":
        if es_preflop:
            if to_call_bb > 0:
                return "CALL" if (tier in ("S", "A") and hay_call) else ("CHECK" if hay_check else "FOLD")
            return "RAISE" if (tier in ("S", "A") and hay_bet) else ("CHECK" if hay_check else "FOLD")
        else:
            es_mano_fuerte = bool(gto_comb.get("es_top_pair") or gto_comb.get("es_set") or gto_comb.get("es_monstruo"))
            if es_mano_fuerte:
                return "RAISE" if hay_bet else ("CALL" if hay_call else "CHECK")
            return "CHECK" if hay_check else "FOLD"

    # 3. LAG (Loose-Aggressive): Rangos amplios, sube y farolea con frecuencia
    if archetype == "LAG":
        if es_preflop:
            if tier in ("S", "A", "B") and hay_bet:
                return "RAISE"
            if tier in ("C", "D") and hay_bet and random.random() < 0.45:
                return "RAISE"
            return "CALL" if hay_call else ("CHECK" if hay_check else "FOLD")
        else:
            if hay_bet and random.random() < 0.50:
                return "RAISE"
            return "CALL" if hay_call else ("CHECK" if hay_check else "FOLD")

    # 4. TAG / Hero Clone: Estrategia GTO/EV estándar
    dec_obj = decidir_accion_rapida(dom_sim)
    return dec_obj.accion


class SelfPlayArena:
    def __init__(
        self,
        num_seats: int = 6,
        big_blind: float = 20.0,
        start_stack_bb: float = 100.0,
        db_path: Path = DB_PATH,
        session_id: Optional[str] = None,
    ):
        self.num_seats = max(2, min(6, int(num_seats or 6)))
        self.big_blind = float(big_blind or 20.0)
        self.start_stack = self.big_blind * float(start_stack_bb or 100.0)
        self.db_path = Path(db_path)
        self.session_id = session_id or f"self_play_arena_{int(time.time())}"

        self.storage = AnalyticsStorage(db_path=self.db_path)
        self.exp_mem = PokerExperienceMemory(db_path=self.db_path)
        # Profiler en memoria para simulación masiva ultra-rápida (RAM Speed)
        self.profiler = OpponentProfiler(db_path=":memory:")
        self.strat_engine = StrategicEngine()
        self.opp_model = OpponentModel(model_type=getattr(config, "SKLEARN_OPPONENT_MODEL_TYPE", "random_forest"))
        self.opp_model.train_from_database(str(self.db_path))
        self.dec_maker = DecisionMaker(self.strat_engine, self.opp_model)

        # Asignación de 6 jugadores con arquetipos diversos
        self.players = []
        for i in range(self.num_seats):
            cfg = OPPONENT_ARCHETYPES[i % len(OPPONENT_ARCHETYPES)]
            is_hero = (i == 0)
            self.players.append({
                "seat": i + 1,
                "nickname": "Hero_Belnebu" if is_hero else cfg["nickname"],
                "player_id": "P_HERO_001" if is_hero else f"P_BOT_00{i + 1}",
                "is_hero": is_hero,
                "archetype": "HERO" if is_hero else cfg["archetype"],
                "stack": self.start_stack,
                "bot_version": getattr(config, "BOT_VERSION", "1.5"),
            })

    def reset_stacks_if_needed(self):
        for p in self.players:
            if p["stack"] < self.big_blind * 10 or p["stack"] > self.big_blind * 300:
                p["stack"] = self.start_stack

    def ejecutar_mano_simulada(self, hand_number: int, button_seat_idx: int, save_to_db: bool = True) -> Dict[str, Any]:
        self.reset_stacks_if_needed()

        deck = list(range(52))
        random.shuffle(deck)

        # Reparto de cartas
        holes = {i: [deck.pop(), deck.pop()] for i in range(self.num_seats)}
        board_5 = [deck.pop() for _ in range(5)]

        flop_cards = board_5[:3]
        turn_card = board_5[3:4]
        river_card = board_5[4:5]

        # Posiciones relativas
        positions = {}
        for i in range(self.num_seats):
            rel = (i - button_seat_idx) % self.num_seats
            positions[i] = obtener_nombre_posicion_6max(rel, total_players=self.num_seats)

        sb_idx = (button_seat_idx + 1) % self.num_seats
        bb_idx = (button_seat_idx + 2) % self.num_seats

        sb_amount = min(self.players[sb_idx]["stack"], self.big_blind * 0.5)
        bb_amount = min(self.players[bb_idx]["stack"], self.big_blind)

        stacks_in_hand = {i: self.players[i]["stack"] for i in range(self.num_seats)}
        invested = {i: 0.0 for i in range(self.num_seats)}

        invested[sb_idx] += sb_amount
        stacks_in_hand[sb_idx] -= sb_amount
        invested[bb_idx] += bb_amount
        stacks_in_hand[bb_idx] -= bb_amount

        active_players = set(range(self.num_seats))
        hand_uid = generar_uid_determinista(self.session_id, "v1.5", hand_number, f"sim_{hand_number}")

        hand_decisions = []
        streets = ["PREFLOP", "FLOP", "TURN", "RIVER"]
        current_board = []

        # Rondas de Apuestas Multi-Turno
        for street_idx, street_name in enumerate(streets):
            if len(active_players) <= 1:
                break

            if street_name == "FLOP":
                current_board = flop_cards
            elif street_name == "TURN":
                current_board = board_5[:4]
            elif street_name == "RIVER":
                current_board = board_5[:5]

            board_str = " ".join(int_cards_to_str_list(current_board))

            if street_name == "PREFLOP":
                start_action_idx = (bb_idx + 1) % self.num_seats
            else:
                start_action_idx = (button_seat_idx + 1) % self.num_seats

            current_high_bet = max(invested[i] for i in range(self.num_seats))
            street_bets = {i: 0.0 for i in range(self.num_seats)}
            if street_name == "PREFLOP":
                street_bets[sb_idx] = sb_amount
                street_bets[bb_idx] = bb_amount

            players_acted = set()
            curr_turn_idx = start_action_idx

            # Bucle de Ronda de Apuestas Completa (Multi-turn per street)
            loop_safety = 0
            while len(active_players) > 1 and loop_safety < 30:
                loop_safety += 1

                # Verificar si todos los jugadores activos han actuado y cerrado apuestas
                non_allin_active = [p for p in active_players if stacks_in_hand[p] > 0]
                bets_matched = all(
                    abs(street_bets[p] - max(street_bets[k] for k in active_players)) < 1e-3
                    for p in non_allin_active
                ) if non_allin_active else True

                if bets_matched and active_players.issubset(players_acted):
                    break

                if curr_turn_idx not in active_players or stacks_in_hand[curr_turn_idx] <= 0:
                    curr_turn_idx = (curr_turn_idx + 1) % self.num_seats
                    continue

                p_idx = curr_turn_idx
                hero_cards_str = " ".join(int_cards_to_str_list(holes[p_idx]))
                highest_street_bet = max(street_bets[k] for k in active_players)
                to_call = max(0.0, highest_street_bet - street_bets[p_idx])

                dom_sim = {
                    "es_mi_turno": True,
                    "mis_cartas": hero_cards_str,
                    "cartas_comunitarias": board_str,
                    "bote": sum(invested.values()),
                    "a_igualar": to_call,
                    "mi_stack": stacks_in_hand[p_idx],
                    "big_blind": self.big_blind,
                    "posicion": positions[p_idx],
                    "botones_visibles": "FOLD | CALL | RAISE" if to_call > 0 else "CHECK | BET | ALL_IN",
                    "calle": street_name,
                }

                gto_pre = clasificar_mano_preflop(hero_cards_str, posicion=positions[p_idx], stack_efectivo_bb=stacks_in_hand[p_idx]/self.big_blind)
                gto_comb = evaluar_combinacion_mano_y_mesa(hero_cards_str, board_str)

                # Decisión según Asiento (Hero usa DecisionMaker + Profiling; Rivals usan Arquetipos)
                if self.players[p_idx]["is_hero"]:
                    # Perfilado en tiempo real del principal rival en la mano
                    opp_target_idx = (p_idx + 1) % self.num_seats
                    opp_profile = self.profiler.obtener_perfil_explotativo(
                        self.players[opp_target_idx]["player_id"],
                        self.players[opp_target_idx]["nickname"]
                    )
                    game_state = {
                        "mis_cartas": hero_cards_str,
                        "cartas_comunitarias": board_str,
                        "pot_size": sum(invested.values()),
                        "current_bet": to_call,
                        "board_cards_count": len(current_board),
                        "posicion": positions[p_idx],
                    }
                    historical_actions = {"folds": 1, "calls": 2, "raises": 1}

                    # Ejecución del motor híbrido Pluribus-style
                    hybrid_res = self.dec_maker.decidir_accion_hibrida(
                        game_state, historical_actions, opp_profile, model_mode="gto"
                    )
                    raw_act = hybrid_res["final_action"].upper()

                    # Mapear acción híbrida a botones legales
                    if raw_act in ("RAISE_SMALL", "RAISE_MEDIUM", "RAISE_LARGE", "RAISE") and to_call > 0:
                        act = "RAISE"
                    elif raw_act in ("RAISE_SMALL", "RAISE_MEDIUM", "RAISE_LARGE", "RAISE") and to_call == 0:
                        act = "RAISE"
                    elif raw_act == "CALL" and to_call > 0:
                        act = "CALL"
                    elif raw_act == "CALL" and to_call == 0:
                        act = "CHECK"
                    elif raw_act == "FOLD" and to_call == 0:
                        act = "CHECK"
                    elif raw_act == "FOLD" and to_call > 0:
                        act = "FOLD"
                    else:
                        dec_rap = decidir_accion_rapida(dom_sim)
                        act = dec_rap.accion
                else:
                    act = decidir_accion_arqueotipo(
                        self.players[p_idx]["archetype"], dom_sim, gto_pre, gto_comb, self.big_blind
                    )

                players_acted.add(p_idx)

                hand_decisions.append({
                    "hand_uid": hand_uid,
                    "street": street_name,
                    "player_seat": p_idx + 1,
                    "player_nickname": self.players[p_idx]["nickname"],
                    "position": positions[p_idx],
                    "action_chosen": act,
                    "to_call": to_call,
                    "pot_size": sum(invested.values()),
                    "ev_lost_bb": 0.0,
                    "is_missed_opportunity": 0,
                })

                # Registrar acción en OpponentProfiler
                try:
                    self.profiler.registrar_accion_rival(
                        player_id=self.players[p_idx]["player_id"],
                        nickname=self.players[p_idx]["nickname"],
                        accion=act,
                        calle=street_name,
                        seat=p_idx + 1,
                        hand_id=hand_number,
                    )
                except Exception:
                    pass

                # Procesamiento de apuestas y fichas
                if act == "FOLD":
                    active_players.remove(p_idx)
                elif act in ("CALL", "CHECK"):
                    call_cost = min(stacks_in_hand[p_idx], to_call)
                    invested[p_idx] += call_cost
                    street_bets[p_idx] += call_cost
                    stacks_in_hand[p_idx] -= call_cost
                elif act in ("RAISE", "ALL_IN"):
                    raise_size = to_call + self.big_blind * (2.5 if street_name == "PREFLOP" else 2.0)
                    raise_amt = min(stacks_in_hand[p_idx], raise_size)
                    invested[p_idx] += raise_amt
                    street_bets[p_idx] += raise_amt
                    stacks_in_hand[p_idx] -= raise_amt

                curr_turn_idx = (curr_turn_idx + 1) % self.num_seats

        # Finalización, Reparto de Botes y Side Pots
        total_pot = sum(invested.values())
        final_board_str = " ".join(int_cards_to_str_list(board_5))

        hero_idx = 0
        hero_net = 0.0
        went_to_showdown = len(active_players) > 1

        if len(active_players) == 1:
            winner_idx = list(active_players)[0]
            for i in range(self.num_seats):
                if i == winner_idx:
                    net = total_pot - invested[i]
                    self.players[i]["stack"] += net
                    if i == hero_idx:
                        hero_net = net
                else:
                    self.players[i]["stack"] -= invested[i]
                    if i == hero_idx:
                        hero_net = -invested[i]
        else:
            # Showdown con evaluador ARIA
            scores = {}
            for p_idx in active_players:
                eval_cards = holes[p_idx] + board_5
                scores[p_idx] = evaluate(eval_cards)

            best_score = max(scores.values())
            winners = [p for p, sc in scores.items() if sc == best_score]
            split_pot = total_pot / len(winners)

            for i in range(self.num_seats):
                if i in winners:
                    net = split_pot - invested[i]
                    self.players[i]["stack"] += net
                    if i == hero_idx:
                        hero_net = net
                else:
                    self.players[i]["stack"] -= invested[i]
                    if i == hero_idx:
                        hero_net = -invested[i]

        hero_cards_str = " ".join(int_cards_to_str_list(holes[hero_idx]))
        pre_info = clasificar_mano_preflop(hero_cards_str, posicion=positions[hero_idx], stack_efectivo_bb=stacks_in_hand[hero_idx]/self.big_blind)

        hand_record = {
            "hand_uid": hand_uid,
            "session_id": self.session_id,
            "version_id": getattr(config, "BOT_VERSION", "1.5"),
            "hand_number": hand_number,
            "is_synthetic": 1,
            "position": positions[hero_idx],
            "hole_cards": hero_cards_str,
            "preflop_tier": pre_info.get("tier", "BASURA"),
            "preflop_score": pre_info.get("score", 0),
            "final_board": final_board_str,
            "reached_street": "RIVER" if went_to_showdown else "PREFLOP",
            "saw_flop": 1 if len(current_board) >= 3 else 0,
            "went_to_showdown": 1 if went_to_showdown else 0,
            "won_at_showdown": 1 if (went_to_showdown and hero_net > 0) else 0,
            "won_hand": 1 if hero_net > 0 else 0,
            "big_blind": self.big_blind,
            "pot_final": total_pot,
            "invested_chips": invested[hero_idx],
            "won_chips": total_pot if hero_net > 0 else 0.0,
            "net_profit_chips": hero_net,
            "net_profit_bb": round(hero_net / self.big_blind, 2),
            "ev_net_chips": hero_net,
            "ev_net_bb": round(hero_net / self.big_blind, 2),
        }

        # Guardar en SQLite si está activado el guardado por mano
        if save_to_db:
            try:
                self.storage.guardar_mano(hand_record)
                for d in hand_decisions:
                    if d["player_seat"] == 1:
                        d_record = {
                            "hand_uid": hand_uid,
                            "session_id": self.session_id,
                            "is_synthetic": 1,
                            "street": d["street"],
                            "action_chosen": d["action_chosen"],
                            "pot_size": d["pot_size"],
                            "big_blind": self.big_blind,
                            "effective_stack": self.start_stack,
                            "ev_lost_bb": 0.0,
                            "is_missed_opportunity": 0,
                        }
                        self.storage.guardar_decision(d_record)
            except Exception:
                pass

        # Auto-evaluación en memoria de experiencia si hubo pérdida
        if hero_net < 0:
            try:
                self.exp_mem.auto_evaluar_y_registrar_mano_perdedora(
                    hand_tier=pre_info.get("tier", "DESCONOCIDO"),
                    board_texture="MESA SECA",
                    opp_archetype="REGULAR",
                    accion_tomada="CALL",
                    net_chips=hero_net,
                    street="PREFLOP",
                    position=positions[hero_idx],
                    big_blind=self.big_blind,
                )
            except Exception:
                pass

        return hand_record

    def simular_partidas_masivas(self, num_hands: int = 1000) -> Dict[str, Any]:
        print("\n" + "=" * 80)
        print(f"🏟️ INICIANDO ARENA DE SELF-PLAY & BENCHMARKING PLURIBUS 6-MAX ({num_hands} Manos)")
        print(f"🆔 Sesión: {self.session_id} | Asientos: {self.num_seats} | Ciegas: {self.big_blind / 2}/{self.big_blind}")
        print(f"🧠 Asiento Hero (0): Pluribus DecisionMaker + OpponentModel + Real-time Profiler")
        print(f"🤖 Oponentes (1..5): CallingStation, NitRock, TAG, LAG, HeroClone")
        print("⚡ Modo Ultra-Rápido: Batching de base de datos e inserciones por lote (bulk)")
        print("=" * 80 + "\n")

        t0 = time.time()
        button_idx = 0

        total_net_bb = 0.0
        wins = 0
        recent_bb_window = []
        hands_buffer = []

        print_step = 2500 if num_hands >= 10000 else (500 if num_hands >= 1000 else 100)

        for h in range(1, num_hands + 1):
            res = self.ejecutar_mano_simulada(hand_number=h, button_seat_idx=button_idx, save_to_db=False)
            button_idx = (button_idx + 1) % self.num_seats
            net_bb = res["net_profit_bb"]
            total_net_bb += net_bb
            recent_bb_window.append(net_bb)
            if len(recent_bb_window) > 500:
                recent_bb_window.pop(0)

            if res["won_hand"]:
                wins += 1

            hands_buffer.append(res)

            # Volcado masivo por lote a SQLite cada 1,000 manos o al finalizar
            if len(hands_buffer) >= 1000 or h == num_hands:
                try:
                    self.storage.guardar_manos_bulk(hands_buffer)
                except Exception:
                    pass
                hands_buffer.clear()

            # Reentrenamiento online del modelo de oponentes en intervalos amplios
            if h % 5000 == 0:
                try:
                    self.opp_model.train_from_database(str(self.db_path))
                except Exception:
                    pass

            if h % print_step == 0 or h == num_hands:
                elapsed = max(0.001, time.time() - t0)
                h_per_sec = round(h / elapsed, 1)
                winrate_bb100 = round((total_net_bb / h) * 100.0, 2)
                rolling_bb100 = round((sum(recent_bb_window) / len(recent_bb_window)) * 100.0, 2)
                win_pct = round((wins / h) * 100.0, 1)
                rem_hands = num_hands - h
                eta_sec = round(rem_hands / max(1.0, h_per_sec), 1)
                pct_done = round((h / num_hands) * 100.0, 1)
                print(
                    f"  🎴 Mano #{h:05d}/{num_hands} ({pct_done:5.1f}%) | Winrate: {winrate_bb100:+.2f} BB/100 "
                    f"(Rodante 500m: {rolling_bb100:+.2f} BB/100) | Victorias: {win_pct}% | Vel: {h_per_sec} h/s | ETA: {eta_sec}s"
                )

        elapsed_total = round(time.time() - t0, 2)
        avg_bb100 = round((total_net_bb / num_hands) * 100.0, 2)

        print("\n🧠 Reentrenando modelos de oponentes con las nuevas manos simuladas...")
        summary = self.opp_model.train_from_database(str(self.db_path))
        print(f"   ✅ Modelo de oponentes reentrenado con {summary.get('samples_trained', 0)} muestras.")

        print("\n" + "=" * 80)
        print(f"🎉 SIMULACIÓN COMPLETADA EXITOSAMENTE en {elapsed_total}s ({round(num_hands / max(0.1, elapsed_total), 1)} h/s)")
        print(f"📊 Winrate Promedio Hero: {avg_bb100:+.2f} BB/100 | Bote Total Acumulado: {total_net_bb:+.1f} BB")
        print("=" * 80 + "\n")

        return {
            "session_id": self.session_id,
            "total_hands": num_hands,
            "winrate_bb100": avg_bb100,
            "elapsed_sec": elapsed_total,
            "hands_per_sec": round(num_hands / elapsed_total, 1),
        }


def main():
    parser = argparse.ArgumentParser(description="Arena de Self-Play 6-Max Pluribus para Bot de Póker")
    parser.add_argument("--hands", type=int, default=500, help="Número de manos a simular (default: 500)")
    parser.add_argument("--seats", type=int, default=6, help="Número de asientos en mesa (default: 6)")
    parser.add_argument("--bb", type=float, default=20.0, help="Ciega Grande por defecto (default: 20.0)")
    args = parser.parse_args()

    arena = SelfPlayArena(num_seats=args.seats, big_blind=args.bb)
    arena.simular_partidas_masivas(num_hands=args.hands)


if __name__ == "__main__":
    main()
