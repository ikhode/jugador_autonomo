import io
import os
import sys
import unittest
from unittest.mock import patch

from PIL import Image

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import config
from bot import resolver_decision_hibrida
from poker_agent import PokerDecision, optimizar_imagen, validar_ruta_gemma_local


class TestFase3ModeAndModels(unittest.TestCase):
    def test_01_modo_unificado_y_modelos_centralizados(self):
        self.assertTrue(len(config.GEMINI_MODELS_PRIORITY) >= 1)
        self.assertEqual(config.GEMINI_MODELS_PRIORITY[0], config.GEMINI_MODEL)
        self.assertNotIn("C:\\Users\\SATAP", config.GEMMA_LOCAL_MODEL_PATH)

        orig_modo, orig_active = config.MODO, config.ACTIVE_MODEL
        try:
            config.ACTIVE_MODEL = config.GEMINI_MODEL
            config.MODO = "heuristico"
            self.assertEqual(config.obtener_modo_activo(), "determinista")
            config.MODO = "determinista"
            self.assertEqual(config.obtener_modo_activo(), "determinista")
            config.MODO = "hibrido"
            self.assertEqual(config.obtener_modo_activo(), "hibrido")
            config.MODO = "local"
            self.assertEqual(config.obtener_modo_activo(), "local")
            config.ACTIVE_MODEL = "gto-heuristic"
            self.assertEqual(config.obtener_modo_activo(), "determinista")
        finally:
            config.MODO, config.ACTIVE_MODEL = orig_modo, orig_active

    def test_02_hibrido_acepta_solo_si_legal_y_diferencia_ev_menor_al_umbral(self):
        dom = {"botones_visibles": "FOLD | CALL | RAISE", "a_igualar": "40", "mi_stack": "500", "min_raise": "40"}
        motor = PokerDecision(
            evaluacion="Motor CALL",
            estrategia_competitiva="GTO",
            estado_pantalla="EN_MESA_JUEGO",
            es_mi_turno=True,
            accion="CALL",
            monto_apuesta=0,
            confianza=0.85,
        )
        ev_res = {"evs": {"FOLD": 0.0, "CALL": 2.4, "RAISE": 2.1, "CHECK": 0.0}, "ev_bb": 2.4}

        # Caso A: LLM propone RAISE (legal, ΔEV = 0.3 <= 0.5) -> acepta LLM y registra ambas
        llm_ok = PokerDecision(
            evaluacion="LLM RAISE",
            estrategia_competitiva="Explotativo",
            estado_pantalla="EN_MESA_JUEGO",
            es_mi_turno=True,
            accion="RAISE",
            monto_apuesta=80,
            confianza=0.90,
        )
        final_ok, reg_ok = resolver_decision_hibrida(dom, motor, ev_res, llm_ok, ev_threshold_bb=0.5)
        self.assertEqual(final_ok.accion, "RAISE")
        self.assertTrue(reg_ok["aceptada_llm"])
        self.assertIsNotNone(reg_ok["propuesta_motor"])
        self.assertIsNotNone(reg_ok["propuesta_llm"])

        # Caso B: LLM propone FOLD (ΔEV = 2.4 > 0.5) -> mantiene motor y registra ambas
        llm_bad_ev = PokerDecision(
            evaluacion="LLM FOLD",
            estrategia_competitiva="Tight",
            estado_pantalla="EN_MESA_JUEGO",
            es_mi_turno=True,
            accion="FOLD",
            monto_apuesta=0,
            confianza=0.80,
        )
        final_rej, reg_rej = resolver_decision_hibrida(dom, motor, ev_res, llm_bad_ev, ev_threshold_bb=0.5)
        self.assertEqual(final_rej.accion, "CALL")
        self.assertFalse(reg_rej["aceptada_llm"])
        self.assertEqual(reg_rej["propuesta_llm"]["accion_original"], "FOLD")

        # Caso C: LLM propone CHECK ilegal con apuesta activa -> rechaza y mantiene motor
        llm_illegal = PokerDecision(
            evaluacion="LLM CHECK ilegal",
            estrategia_competitiva="Error",
            estado_pantalla="EN_MESA_JUEGO",
            es_mi_turno=True,
            accion="CHECK",
            monto_apuesta=0,
            confianza=0.70,
        )
        final_ill, reg_ill = resolver_decision_hibrida(dom, motor, ev_res, llm_illegal, ev_threshold_bb=0.5)
        self.assertEqual(final_ill.accion, "CALL")
        self.assertFalse(reg_ill["aceptada_llm"])

    def test_03_gemma_local_path_validation_cross_platform(self):
        ok_empty, msg_empty = validar_ruta_gemma_local("")
        self.assertFalse(ok_empty)
        self.assertIn("no configurado", msg_empty)

        ok_missing, msg_missing = validar_ruta_gemma_local("/ruta/que/no/existe/gemma.gguf")
        self.assertFalse(ok_missing)
        self.assertIn("No se encontró", msg_missing)

    def test_04_optimizar_imagen_no_degrada_ni_silencia_errores(self):
        buf = io.BytesIO()
        Image.new("RGB", (1280, 720), color=(20, 80, 40)).save(buf, format="PNG")
        out_bytes, mime = optimizar_imagen(buf.getvalue())
        self.assertEqual(mime, "image/jpeg")
        out_img = Image.open(io.BytesIO(out_bytes))
        self.assertEqual(out_img.size, (1280, 720))

        with patch("poker_agent.logger.warning") as mock_warn:
            bad_bytes, bad_mime = optimizar_imagen(b"not-an-image")
            self.assertEqual(bad_mime, "image/png")
            self.assertEqual(bad_bytes, b"not-an-image")
            mock_warn.assert_called_once()


if __name__ == "__main__":
    unittest.main(verbosity=2)
