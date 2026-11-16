"""
stats.py — Estadísticas "de verdad" de cada asiento, medidas desde fuera del juego.
Sirven para comprobar si lo que el agente cree de sus rivales coincide con la realidad.
"""
from __future__ import annotations

from engine import FOLD, CALL, N


class GroundTruth:
    """Listener: se pasa a play_match(listeners=[...]). Guarda una fila por mano y asiento."""

    def __init__(self):
        self.rows = [[] for _ in range(N)]   # (vpip, pfr, agg, passive, faced, folded_to_bet)

    def on_hand(self, res):
        vpip, pfr = [0] * N, [0] * N
        agg, pas, faced, fold = [0] * N, [0] * N, [0] * N, [0] * N
        for ev in res["log"]:
            p, a = ev["seat"], ev["act"]
            if ev["street"] == 0:
                if a > CALL:
                    vpip[p] = pfr[p] = 1
                elif a == CALL and ev["amount"] > 0:
                    vpip[p] = 1
            else:
                if ev["facing"]:
                    faced[p] += 1
                    fold[p] += a == FOLD
                if a > CALL:
                    agg[p] += 1
                elif a == CALL and ev["amount"] > 0:
                    pas[p] += 1
        for p in range(N):
            self.rows[p].append((vpip[p], pfr[p], agg[p], pas[p], faced[p], fold[p]))

    def summary(self, seat: int, lo: int = 0, hi: int | None = None) -> dict:
        r = self.rows[seat][lo:hi]
        n = max(1, len(r))
        s = [sum(x[i] for x in r) for i in range(6)]
        return dict(hands=len(r), vpip=s[0] / n, pfr=s[1] / n,
                    aggr=s[2] / max(1, s[2] + s[3]), fold_to_bet=s[5] / max(1, s[4]))
