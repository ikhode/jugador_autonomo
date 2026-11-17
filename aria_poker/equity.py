"""
equity.py — Fuerza de mano compartida por los bots y por el agente que aprende.

* Preflop: tabla de las 169 clases de manos con su equity contra una mano al azar
  (Monte Carlo, se calcula una vez y se guarda en preflop_cache.json) y su percentil.
* Postflop: percentil de la mano hecha frente a manos al azar en el tablero actual.
* Proyectos: detección simple de color / escalera por completar (solo para los bots).

Un "percentil" es la fracción de manos posibles que esta mano supera (0 = peor, 1 = mejor).
"""
from __future__ import annotations

import json
import os
import random
from bisect import bisect_left, bisect_right
from itertools import combinations

from engine import evaluate

_CACHE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "preflop_cache.json")


def hole_class(hole) -> int:
    a, b = hole
    ra, rb, suited = a >> 2, b >> 2, (a & 3) == (b & 3)
    hi, lo = max(ra, rb), min(ra, rb)
    return hi * 13 + lo + (169 if suited else 0)        # 0..337 (solo 169 usados)


def _class_combos(cls: int) -> int:
    suited = cls >= 169
    c = cls - 169 if suited else cls
    hi, lo = divmod(c, 13)
    return 6 if hi == lo else (4 if suited else 12)


def _build_table(sims: int = 6000, seed: int = 7):
    rng = random.Random(seed)
    eq = {}
    for hi in range(13):
        for lo in range(hi + 1):
            for suited in ((False,) if hi == lo else (False, True)):
                ca, cb = hi * 4, lo * 4 + (0 if suited else 1)
                if hi == lo:
                    ca, cb = hi * 4, hi * 4 + 1
                hole = (ca, cb)
                rest = [c for c in range(52) if c not in hole]
                w = 0.0
                for _ in range(sims):
                    s = rng.sample(rest, 7)
                    me = evaluate(list(hole) + s[2:])
                    op = evaluate(s[:2] + s[2:])
                    w += 1.0 if me > op else (0.5 if me == op else 0.0)
                eq[hole_class(hole)] = w / sims
    order = sorted(eq, key=lambda k: eq[k])
    total = sum(_class_combos(k) for k in order)
    pct, run = {}, 0
    for k in order:
        n = _class_combos(k)
        pct[k] = (run + n / 2) / total
        run += n
    return eq, pct


def _load():
    if os.path.exists(_CACHE):
        try:
            with open(_CACHE, "r", encoding="utf-8") as f:
                d = json.load(f)
            return {int(k): v for k, v in d["eq"].items()}, {int(k): v for k, v in d["pct"].items()}
        except Exception:
            pass
    eq, pct = _build_table()
    try:
        with open(_CACHE, "w", encoding="utf-8") as f:
            json.dump({"eq": eq, "pct": pct}, f)
    except OSError:
        pass
    return eq, pct


PREFLOP_EQ, PREFLOP_PCT = _load()


def preflop_pct(hole) -> float:
    return PREFLOP_PCT[hole_class(hole)]


def preflop_eq(hole) -> float:
    return PREFLOP_EQ[hole_class(hole)]


def unseen_cards(known) -> list:
    k = set(known)
    return [c for c in range(52) if c not in k]


import functools

@functools.lru_cache(maxsize=32768)
def _made_pct_cached(hole_tuple: tuple, board_tuple: tuple, m: int = 25) -> float:
    rest = unseen_cards(hole_tuple + board_tuple)
    me = evaluate(list(hole_tuple) + list(board_tuple))
    win = 0.0
    rng = random.Random(42)
    for _ in range(m):
        o = rng.sample(rest, 2)
        s = evaluate(o + list(board_tuple))
        win += 1.0 if me > s else (0.5 if me == s else 0.0)
    return win / m


def made_pct(hole, board, rng: random.Random = None, m: int = 25) -> float:
    """Percentil de la mano hecha con cache ultra-rápida en memoria."""
    if not board:
        return preflop_pct(hole)
    return _made_pct_cached(tuple(hole), tuple(board), m)


def draw_floor(hole, board) -> float:
    """Fuerza mínima percibida si hay un proyecto de color o escalera (flop/turn)."""
    if len(board) not in (3, 4):
        return 0.0
    cards = list(hole) + list(board)
    for s in range(4):
        if sum(1 for c in cards if c & 3 == s) == 4 and any(c & 3 == s for c in hole):
            return 0.62
    mask = 0
    for c in cards:
        mask |= 1 << (c >> 2)
    ext = (mask << 1) | (mask >> 12)
    hmask = 0
    for c in hole:
        hmask |= 1 << ((c >> 2) + 1)
    for lo in range(0, 10):
        win = ((1 << 5) - 1) << lo
        if bin(ext & win).count("1") >= 4 and (hmask & win):
            return 0.55
    return 0.0


class Pool:
    """Muestra de manos rivales posibles con su percentil en el tablero dado.
    Sirve para muestrear rangos ponderados: peso = densidad(percentil)."""

    def __init__(self, known, board, rng: random.Random, m: int = 220):
        rest = unseen_cards(known)
        self.board = tuple(board)
        if not board:
            combos = list(combinations(rest, 2))
            self.holes = combos
            self.ss = None
            self.pct = [PREFLOP_PCT[hole_class(h)] for h in combos]
        else:
            holes = [tuple(rng.sample(rest, 2)) for _ in range(m)]
            scores = [evaluate(list(h) + list(board)) for h in holes]
            ss = sorted(scores)
            n = len(ss)
            self.holes = holes
            self.ss = ss
            self.pct = [(bisect_left(ss, s) + bisect_right(ss, s)) / (2.0 * n) for s in scores]

    def pct_of(self, score: int) -> float:
        """Percentil de un puntaje (la mano propia) respecto a esta muestra."""
        n = len(self.ss)
        return (bisect_left(self.ss, score) + bisect_right(self.ss, score)) / (2.0 * n)
