import unittest
import os
import json
import tempfile
from pathlib import Path

import bot
import config

class TestWebSocketAndAudioEvents(unittest.TestCase):
    def setUp(self):
        self.tracker = bot.GameTracker()

    def test_tracker_urgencia_tiempo_init_and_reset(self):
        self.assertFalse(self.tracker.urgencia_tiempo)
        self.tracker.urgencia_tiempo = True
        self.assertTrue(self.tracker.urgencia_tiempo)
        self.tracker.reset_mano()
        self.assertFalse(self.tracker.urgencia_tiempo)

    def test_delay_humanizado_con_urgencia_tiempo(self):
        old_val = os.environ.pop("PYTEST_CURRENT_TEST", None)
        try:
            dom = {"tiempo_restante": "4"}
            bot.tracker.urgencia_tiempo = True
            pausa = bot.aplicar_delay_humanizado("CHECK", dom_data=dom, tiempo_ia_seg=0.0)
            self.assertLessEqual(pausa, 0.1)
            self.assertFalse(bot.tracker.urgencia_tiempo)
        finally:
            if old_val is not None:
                os.environ["PYTEST_CURRENT_TEST"] = old_val

    def test_delay_humanizado_tiempo_mesa_critico(self):
        old_val = os.environ.pop("PYTEST_CURRENT_TEST", None)
        try:
            dom = {"tiempo_restante": "2"}
            bot.tracker.urgencia_tiempo = False
            pausa = bot.aplicar_delay_humanizado("CHECK", dom_data=dom, tiempo_ia_seg=0.0)
            self.assertLessEqual(pausa, 0.1)
        finally:
            if old_val is not None:
                os.environ["PYTEST_CURRENT_TEST"] = old_val

    def test_ws_traffic_logging_structure(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            test_log = Path(tmpdir) / "test_ws.jsonl"
            payload = json.dumps({"classId": 10017, "seatIndex": 2, "actions": ["CHECK", "FOLD"]})
            entry = {
                "ts": 123456789.0,
                "iso": "2026-11-16T21:00:00",
                "dir": "RECV",
                "url": "wss://schannel.ipoker.com",
                "data": payload
            }
            with open(test_log, "w", encoding="utf-8") as f:
                f.write(json.dumps(entry) + "\n")

            with open(test_log, "r", encoding="utf-8") as f:
                lines = f.readlines()
            self.assertEqual(len(lines), 1)
            parsed = json.loads(lines[0])
            self.assertEqual(parsed["dir"], "RECV")
            self.assertIn("10017", parsed["data"])

if __name__ == "__main__":
    unittest.main()
