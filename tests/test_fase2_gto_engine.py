import os
import sys
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import config
from bot import decidir_accion_rapida
from poker_gto_engine import (
    RANGOS_PREFLOP,
    analizar_textura_mesa_y_blockers,
    calcular_pot_odds_y_sizing,
    clasificar_mano_preflop,
    decidir_por_ev,
    estimar_equidad_vs_rango,
    evaluar_combinacion_mano_y_mesa,
    evaluar_fuerza_5_cartas,
    evaluar_push_fold_nash,
    generar_frecuencia_mixta_gto,
    parsear_lista_cartas,
)


class TestFase2HandEvaluator40PlusCases(unittest.TestCase):
    def test_hand_evaluator_44_cases_categories_ties_and_board_plays(self):
        # (mis_cartas, comunitarias, expected_rank_cat, expected_mano_en_mesa, expected_substr)
        casos = [
            # Escalera real / Escalera de color (8)
            ("As Ks", "Qs Js Ts 2d 3c", 8, False, "ESCALERA"),
            ("9h 8h", "7h 6h 5h Ad Kc", 8, False, "ESCALERA"),
            ("4d 3d", "5d 2d Ad Ks Qc", 8, False, "ESCALERA"),  # Wheel straight flush
            ("2c 3c", "As Ks Qs Js Ts", 8, True, "EN MESA"),    # Royal flush on board
            # Póker (7)
            ("Ah Ad", "As Ac Kh 2d 7c", 7, False, "PÓKER"),
            ("7s 7h", "7d 7c Ks Qd Jc", 7, False, "PÓKER"),
            ("2s 3d", "Ah Ad As Ac Kc", 7, True, "PÓKER EN MESA"),
            ("Ah 3d", "Ks Kd Kh Kc 2s", 7, False, "PÓKER"),    # Kicker A beats board K-high
            # Full House (6)
            ("As Ah", "Ad Ks Kd 2c 3h", 6, False, "FULL HOUSE"),
            ("Ks Qh", "Kd Kc Qs 7d 2c", 6, False, "FULL HOUSE"),
            ("2s 3s", "Ah Ad As Kh Kd", 6, True, "FULL HOUSE EN MESA"),
            ("Jc Jh", "Jd 9s 9c 4d 2h", 6, False, "FULL HOUSE"),
            # Color (5)
            ("Ah 9h", "Kh 7h 2h Tc 3d", 5, False, "COLOR"),
            ("Jd 5d", "Ad Kd Qd 2d 9c", 5, False, "COLOR"),
            ("2c 3d", "As Ks Qs 9s 4s", 5, True, "COLOR EN MESA"),
            ("As 2c", "Ks Qs Js 9s 4s", 5, False, "COLOR"),    # As mejora el color en mesa
            # Escalera (4) incluida rueda A-2-3-4-5
            ("As Kd", "Qh Jc Ts 2d 3c", 4, False, "ESCALERA"),
            ("5h 4d", "As 2c 3d 9h Ks", 4, False, "ESCALERA"), # Wheel
            ("9c 8d", "7s 6h 5c Ad 2s", 4, False, "ESCALERA"),
            ("2c 3d", "As Kh Qd Jc Ts", 4, True, "ESCALERA EN MESA"),
            ("Ah 6d", "5c 4s 3h 2d Kc", 4, False, "ESCALERA"), # 6-high straight > wheel
            # Trío Set vs Trips (3)
            ("8s 8h", "8d Ac Kd 4c 2h", 3, False, "SET"),
            ("As Kd", "Ah Ac 7s 4d 2c", 3, False, "TRIPS"),
            ("2s 3d", "Ks Kh Kd 9c 4h", 3, True, "TRÍO EN MESA"),
            ("5c 5d", "5h Js 9c 3d 2s", 3, False, "SET"),
            # Doble Pareja (2) incl. pocket pair + mesa
            ("As Kd", "Ah Kc 7s 4d 2c", 2, False, "DOBLE PAREJA"),
            ("JJ".replace("JJ", "Js Jh"), "Ad Ac 8s 4d 2c", 2, False, "Pocket Pair"),
            ("9s 8d", "9h 8c 2d As Kh", 2, False, "DOBLE PAREJA"),
            ("2c 3d", "As Ad Ks Kd Qh", 2, True, "DOBLE PAREJA EN MESA"),
            ("As 3d", "Ks Kd 7s 7c 2h", 2, True, "DOBLE PAREJA EN MESA"), # K,K,7,7,A plays A kicker! Wait A > 2
            # Pareja (1): Overpair, Top Pair, Middle/Bottom Pair, Board Pair
            ("As Ah", "Kd 7c 2s", 1, False, "Overpair"),
            ("Qs Qh", "Kc 8d 3s", 1, False, "Pocket Pair"),
            ("As 9d", "Ah 7c 2s", 1, False, "Top Pair"),
            ("7s 6d", "Ah 7c 2s", 1, False, "Middle/Bottom Pair"),
            ("2s 3d", "Ah Kh 7c 7d 9s", 1, True, "Board Pair"),
            ("As Kd", "9h 9c 4s", 1, False, "Board Pair"),
            # Carta Alta (0)
            ("As Kd", "Qh 9c 4s 2d 7c", 0, False, "CARTA ALTA"),
            ("Js Td", "9h 7c 4s 2d 3c", 0, False, "CARTA ALTA"),
            ("2c 3d", "As Kh Qd Jc 9s", 0, True, "CARTA ALTA EN MESA"),
            ("Ah 4d", "Ks Qc Jd 9s 7c", 0, False, "CARTA ALTA"),
        ]
        # Ajustar caso 30: As 3d en Ks Kd 7s 7c 2h sí usa As como kicker (board best es K K 7 7 2)
        casos[29] = ("As 3d", "Ks Kd 7s 7c 2h", 2, False, "DOBLE PAREJA")
        self.assertGreaterEqual(len(casos), 40)

        for mis, mesa, exp_cat, exp_board_plays, exp_sub in casos:
            with self.subTest(mis=mis, mesa=mesa):
                res = evaluar_combinacion_mano_y_mesa(mis, mesa)
                self.assertEqual(res["rango_categoria"], exp_cat)
                self.assertEqual(res["mano_en_mesa"], exp_board_plays)
                self.assertIn(exp_sub.lower(), res["categoria"].lower())

    def test_hand_ties_equal_5_card_rank(self):
        r1 = evaluar_fuerza_5_cartas(parsear_lista_cartas("As 2c 7d 9h Js Kd Qc")[:7])
        r2 = evaluar_fuerza_5_cartas(parsear_lista_cartas("Ah 3s 7d 9h Js Kd Qc")[:7])
        self.assertEqual(r1, r2)

        # Rueda vs 6-high straight
        wheel = evaluar_fuerza_5_cartas(parsear_lista_cartas("As 2c 3d 4h 5s Kc Qd"))
        six_hi = evaluar_fuerza_5_cartas(parsear_lista_cartas("6s 2c 3d 4h 5s Kc Qd"))
        self.assertGreater(six_hi, wheel)

    def test_draws_and_outs_no_double_counting(self):
        # Flush draw (9 outs)
        fd = evaluar_combinacion_mano_y_mesa("Ah 9h", "Kh 7h 2c")
        self.assertTrue(fd["proyecto_color"])
        self.assertEqual(fd["outs_totales"], 9)

        # OESD (8 outs)
        oesd = evaluar_combinacion_mano_y_mesa("9s 8d", "7c 6h 2s")
        self.assertTrue(oesd["proyecto_oesd"])
        self.assertEqual(oesd["outs_totales"], 8)

        # Gutshot (4 outs)
        gs = evaluar_combinacion_mano_y_mesa("As Kd", "Qh Jc 4s")
        self.assertTrue(gs["proyecto_gutshot"])
        self.assertEqual(gs["outs_totales"], 4)

        # Combo draw: 9h 8h on 7h 6h 2c -> 9 flush + 8 OESD - 2 overlap (Th, 5h) = 15 outs
        combo = evaluar_combinacion_mano_y_mesa("9h 8h", "7h 6h 2c")
        self.assertTrue(combo["es_combo_draw"])
        self.assertEqual(combo["outs_totales"], 15)


class TestFase2BBUnitsContextualPreflopAndNash(unittest.TestCase):
    def test_bb_units_without_magic_chip_constants(self):
        res = calcular_pot_odds_y_sizing(
            bote_str="300",
            a_igualar_str="100",
            mi_stack_str="1000",
            big_blind=100.0,
            stack_rival_str="800",
        )
        self.assertEqual(res["big_blind"], 100.0)
        self.assertEqual(res["bote_bb"], 3.0)
        self.assertEqual(res["a_igualar_bb"], 1.0)
        self.assertEqual(res["stack_efectivo_bb"], 8.0)
        self.assertEqual(res["pot_odds_pct"], 25.0)
        self.assertGreaterEqual(res["sizing_cbet_pequeno"], 100)

    def test_contextual_preflop_uses_rangos_preflop(self):
        self.assertIn("AA-JJ", RANGOS_PREFLOP["S"])
        open_btn = clasificar_mano_preflop("Kh 9h", posicion="BTN", stack_efectivo_bb=25.0, situacion="open")
        vs_3bet = clasificar_mano_preflop("Kh 9h", posicion="SB", stack_efectivo_bb=25.0, situacion="vs_3bet")
        self.assertGreater(open_btn["score"], vs_3bet["score"])

    def test_nash_push_fold_10bb_direct_decision(self):
        shove = evaluar_push_fold_nash("AJo", posicion="BTN", stack_efectivo_bb=8.0, situacion="open")
        self.assertTrue(shove["en_regimen_push_fold"])
        self.assertEqual(shove["accion"], "ALL_IN")

        fold_trash = evaluar_push_fold_nash("72o", posicion="BTN", stack_efectivo_bb=8.0, situacion="open")
        self.assertEqual(fold_trash["accion"], "FOLD")

        # Integration with decidir_accion_rapida
        dec = decidir_accion_rapida({
            "es_mi_turno": True,
            "mis_cartas": "As Jd",
            "cartas_comunitarias": "",
            "bote": "30",
            "a_igualar": "20",
            "mi_stack": "160",
            "big_blind": "20",
            "posicion": "BTN",
            "botones_visibles": "FOLD | CALL | ALL_IN",
        })
        self.assertEqual(dec.accion, "ALL_IN")
        self.assertIn("Nash", dec.evaluacion)

    def test_middle_pair_does_not_value_raise_and_combo_draw_plays_aggressively(self):
        # Middle pair vs bet should CALL or CHECK, never value-RAISE
        dec_mid = decidir_accion_rapida({
            "es_mi_turno": True,
            "mis_cartas": "7s 6d",
            "cartas_comunitarias": "Ah 7c 2s",
            "bote": "120",
            "a_igualar": "40",
            "mi_stack": "500",
            "big_blind": "20",
            "botones_visibles": "FOLD | CALL | RAISE",
        })
        self.assertEqual(dec_mid.accion, "CALL")

        # Combo draw (15 outs) should semi-bluff RAISE on non-paired board
        dec_combo = decidir_accion_rapida({
            "es_mi_turno": True,
            "mis_cartas": "9h 8h",
            "cartas_comunitarias": "7h 6h 2c",
            "bote": "100",
            "a_igualar": "20",
            "mi_stack": "500",
            "big_blind": "20",
            "botones_visibles": "FOLD | CALL | RAISE",
        })
        self.assertEqual(dec_combo.accion, "RAISE")

    def test_texture_removes_dry_board_bluff_claim(self):
        tex = analizar_textura_mesa_y_blockers("As Kd", "Kh 7c 2d")
        self.assertNotIn("alerta_farol_inconsistente", tex)
        self.assertIn("estimacion_rango", tex)
        self.assertIn("sets", tex["estimacion_rango"].lower())

    def test_seeded_rng_and_ev_decision_reproducibility(self):
        f1 = generar_frecuencia_mixta_gto(seed=42)
        f2 = generar_frecuencia_mixta_gto(seed=42)
        self.assertEqual(f1["d100"], f2["d100"])

        eq1 = estimar_equidad_vs_rango("As Ah", "Kd 7c 2s", opp_archetype="TAG", iteraciones=100, seed=123)
        eq2 = estimar_equidad_vs_rango("As Ah", "Kd 7c 2s", opp_archetype="TAG", iteraciones=100, seed=123)
        self.assertEqual(eq1, eq2)
        self.assertGreater(eq1, 0.70)

        ev1 = decidir_por_ev({
            "mis_cartas": "As Ah",
            "cartas_comunitarias": "Kd 7c 2s",
            "bote": "100",
            "a_igualar": "40",
            "mi_stack": "500",
            "big_blind": "20",
            "botones_visibles": "FOLD | CALL | RAISE",
        }, seed=123)
        self.assertIn(ev1["accion"], ("CALL", "RAISE"))
        self.assertGreater(ev1["ev_bb"], 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
