"""
bots.py — Oponentes con estilos de juego distintos (y explotables).

Cada bot decide con la misma medida de fuerza (percentil de su mano) pero con
umbrales, faroles y tamaños diferentes. El agente que aprende NO conoce estos
parámetros: solo ve sus acciones y, de vez en cuando, sus cartas en el showdown.
"""
from __future__ import annotations

import random

from engine import Agent, FOLD, CALL, R33, R66, R100, ALLIN
from equity import made_pct, preflop_pct, draw_floor

STYLES = {
    # vpip/pfr: fracción de manos que juega / sube preflop. three_bet: sube ante una subida.
    # call_range: juega ante una subida. bet_thr: fuerza mínima para apostar; bluff: apuesta
    # con cualquier mano; call_thr: fuerza para igualar; raise_thr/raise_bluff: sube ante apuesta.
    "Roca": dict(vpip=.16, pfr=.10, three_bet=.05, call_range=.12,
                 bet_thr=.78, bluff=.04, call_thr=.70, raise_thr=.93, raise_bluff=.01,
                 sizes={R66: .6, R100: .4}),
    "Estacion": dict(vpip=.75, pfr=.06, three_bet=.04, call_range=.60,
                     bet_thr=.88, bluff=.05, call_thr=.28, raise_thr=.96, raise_bluff=.0,
                     sizes={R33: .7, R66: .3}),
    "Maniaco": dict(vpip=.88, pfr=.60, three_bet=.35, call_range=.65,
                    bet_thr=.45, bluff=.50, call_thr=.30, raise_thr=.75, raise_bluff=.25,
                    sizes={R66: .35, R100: .5, ALLIN: .15}),
    "TAG": dict(vpip=.30, pfr=.22, three_bet=.09, call_range=.20,
                bet_thr=.68, bluff=.15, call_thr=.50, raise_thr=.88, raise_bluff=.06,
                sizes={R33: .3, R66: .45, R100: .25}),
}


class Bot(Agent):
    def __init__(self, style: str, seed: int = 0):
        self.rng = random.Random(seed)
        self.set_style(style)

    def set_style(self, style: str):
        self.style_name = style
        self.name = style
        self.p = STYLES[style]

    # -- helpers ---------------------------------------------------------
    def _raise(self, obs, strong=False):
        legal = obs.legal
        opts = {a: w for a, w in self.p["sizes"].items() if a in legal}
        if not opts:
            opts = {a: 1.0 for a in (R33, R66, R100, ALLIN) if a in legal}
        if not opts:
            return CALL
        if strong and ALLIN in legal and self.rng.random() < 0.12:
            return ALLIN
        acts, ws = zip(*opts.items())
        return self.rng.choices(acts, ws)[0]

    def act(self, obs):
        p, rng = self.p, self.rng
        noise = rng.gauss(0, 0.04)
        if obs.street == 0:
            s = preflop_pct(obs.hole) + noise
            if not obs.facing:
                if s >= 1 - p["pfr"]:
                    return self._raise(obs, s > .985)
                if s >= 1 - p["vpip"]:
                    return CALL
                return FOLD if FOLD in obs.legal else CALL
            if s >= 1 - p["three_bet"]:
                return self._raise(obs, s > .97)
            if s >= 1 - p["call_range"]:
                return CALL
            return FOLD
        s = max(made_pct(obs.hole, obs.board, rng, 50), draw_floor(obs.hole, obs.board)) + noise
        if not obs.facing:
            if s >= p["bet_thr"] or rng.random() < p["bluff"]:
                return self._raise(obs, s > .97)
            return CALL
        if s >= p["raise_thr"] or rng.random() < p["raise_bluff"]:
            r = self._raise(obs, s > .97)
            if r != CALL:
                return r
        thr = min(.98, max(.05, p["call_thr"] + .25 * (obs.ratio - .6)))
        return CALL if s >= thr else FOLD
