"""
learner.py — ARIA: un agente que modela a cada rival en tiempo real y explota lo aprendido.

Cómo aprende (todo se actualiza con cada acción pública y cada showdown):
  1. Frecuencias por contexto (calle, ¿enfrenta apuesta?, tamaño): probabilidades de
     fold / pasivo / agresivo de cada rival, con priors bayesianos, jerarquía entre calles
     y olvido exponencial (para detectar que un rival cambió de estilo).
  2. Rangos: si un rival hizo la acción A, su mano está en la franja de percentiles que
     implica su frecuencia (modelo de umbrales) corregida con los showdowns reales: un
     histograma no paramétrico de qué fuerza tenía cuando apostó / igualó (captura faroles).
  3. Decisión: simula 300 "mundos" (manos rivales muestreadas de esos rangos + cartas que
     faltan) y elige la acción de mayor valor esperado, modelando cómo respondería cada
     rival (fold / call / re-subida) según lo aprendido. Los faroles, value bets finos y
     las igualadas "ligeras" surgen solos del cálculo; no hay reglas por tipo de rival.

Con learning=False el agente es idéntico pero conserva para siempre sus priors genéricos
(el "gemelo congelado" que sirve de grupo de control).
"""
from __future__ import annotations

import math
import random
from bisect import bisect_left, bisect_right
from itertools import accumulate

from engine import Agent, FOLD, CALL, N, evaluate
from equity import (PREFLOP_PCT, Pool, hole_class, made_pct, preflop_pct, unseen_cards)

NB = 10
Z = (0.0, 0.0, 0.0)
PRIOR = {  # (¿preflop?, enfrenta, bucket) -> (fold, pasivo, agresivo): "rival promedio"
    (True, 0, 0): (.45, .35, .20),
    (True, 1, 0): (.62, .28, .10),
    (False, 0, 0): (0.0, .62, .38),
    (False, 1, 0): (.33, .50, .17),
    (False, 1, 1): (.50, .36, .14),
}
K0, KS, KB = 6.0, 5.0, 8.0            # fuerza de los priors (en observaciones equivalentes)
R_CALL, R_CHECK, R_RAISE, CONT = .92, .97, 1.0, .78
STREET_LEN = {0: 0, 1: 3, 2: 4, 3: 5}


def bucket(ratio: float) -> int:
    return 1 if ratio > 0.7 else 0


def share(h: int, others) -> float:
    best = max(others) if others else -1
    if h > best:
        return 1.0
    if h < best:
        return 0.0
    return 1.0 / (1 + sum(1 for o in others if o == h))


class OppModel:
    """Lo que ARIA cree de un rival."""

    def __init__(self):
        self.c = {}   # ctx -> [fold, pasivo, agresivo] observados (con olvido)
        self.h = {}   # (calle, clase) -> histograma de percentiles vistos en showdown
        self.d = dict(hands=0., vpip=0., pfr=0., agg=0., pas=0., faced=0., fold=0.)

    def decay(self, f: float):
        for v in self.c.values():
            v[:] = [x * f for x in v]
        for v in self.h.values():
            v[:] = [x * f for x in v]
        for k in self.d:
            self.d[k] *= f

    def probs(self, ctx):
        st, fc, bk = ctx
        base = PRIOR[(st == 0, fc, bk)]
        if st > 0:                     # jerarquía: un rival suele jugar igual en todas las calles
            pooled = [sum(self.c.get((s, fc, bk), Z)[i] for s in (1, 2, 3)) for i in range(3)]
            n = sum(pooled)
            base = tuple((pooled[i] + K0 * base[i]) / (n + K0) for i in range(3))
        c = self.c.get(ctx, Z)
        n = sum(c)
        return tuple((c[i] + KS * base[i]) / (n + KS) for i in range(3))

    def update(self, ctx, cls: int):
        self.c.setdefault(ctx, [0., 0., 0.])[cls] += 1

    def density(self, ctx, cls: int, street: int):
        """Densidad (NB bins) del percentil de su mano dado que hizo la acción `cls`."""
        pf, pp, _ = self.probs(ctx)
        lo, hi = ((0.0, pf), (pf, pf + pp), (pf + pp, 1.0))[cls]
        hi = min(1.0, max(hi, lo + 0.04))
        lo = hi - max(hi - lo, 0.04)
        h = self.h.get((street, cls), None)
        n = sum(h) if h else 0.0
        out = []
        for b in range(NB):
            ov = max(0.0, min((b + 1) / NB, hi) - max(b / NB, lo))
            box = 0.97 * ov / (hi - lo) * NB + 0.03
            hist = ((h[b] + 0.3) / (n + 0.3 * NB) * NB) if n > 0 else 0.0
            out.append((KB * box + n * hist) / (KB + n))
        return out

    def stats(self):
        d = self.d
        n = d["hands"]
        return dict(n=n, vpip=(d["vpip"] + 2.0) / (n + 5), pfr=(d["pfr"] + 1.0) / (n + 5),
                    aggr=(d["agg"] + 6 * .38) / (d["agg"] + d["pas"] + 6),
                    fold_to_bet=(d["fold"] + 6 * .40) / (d["faced"] + 6))


class Learner(Agent):
    def __init__(self, seed: int = 0, learning: bool = True, decay: float = 0.995,
                 n_worlds: int = 300):
        self.rng = random.Random(seed)
        self.learning, self.decay, self.n_worlds = learning, decay, n_worlds
        self.name = "ARIA" if learning else "ARIA-congelada"
        self.models = {}
        self.seat = 0
        self.ll = []          # (mano, asiento, -log p): error de predicción de la próxima acción
        self.trace = []       # (mano, {asiento: stats}) para graficar la convergencia
        self.last = None
        self.acts, self.fl, self.pools, self.pcache = {}, {}, {}, {}

    def model(self, q: int) -> OppModel:
        return self.models.setdefault(q, OppModel())

    # ------------------------------------------------------------------ aprendizaje
    def new_hand(self, hand_no, seat, button):
        self.seat = seat
        if self.learning:
            for m in self.models.values():
                m.decay(self.decay)
        self.acts = {q: {} for q in range(N)}
        self.fl = {q: [0, 0, 0, 0, 0, 0] for q in range(N)}
        self.pools, self.pcache = {}, {}

    def observe(self, ev):
        q = ev["seat"]
        if q == self.seat:
            return
        a, st = ev["act"], ev["street"]
        cls = 0 if a == FOLD else (1 if a == CALL else 2)
        ctx = (st, ev["facing"], bucket(ev["ratio"]) if (st > 0 and ev["facing"]) else 0)
        m = self.model(q)
        self.ll.append((ev["hand"], q, -math.log(max(1e-9, m.probs(ctx)[cls]))))
        if self.learning:
            m.update(ctx, cls)
        if cls > 0:
            self.acts[q][st] = (ctx, cls)
        f = self.fl[q]
        if st == 0:
            if a > CALL:
                f[0] = f[1] = 1
            elif a == CALL and ev["amount"] > 0:
                f[0] = 1
        else:
            if ev["facing"]:
                f[4] += 1
                f[5] += a == FOLD
            if a > CALL:
                f[2] += 1
            elif a == CALL and ev["amount"] > 0:
                f[3] += 1

    def showdown(self, info):
        if not self.learning:
            return
        for q, hole in info["holes"].items():
            if q == self.seat:
                continue
            m = self.model(q)
            for st, (ctx, cls) in self.acts[q].items():
                b = info["board"][: STREET_LEN[st]]
                p = made_pct(hole, b, self.rng, 150)
                m.h.setdefault((st, cls), [0.0] * NB)[min(NB - 1, int(p * NB))] += 1

    def end_hand(self, net):
        if not self.learning:
            return
        for q, f in self.fl.items():
            if q == self.seat:
                continue
            d = self.model(q).d
            d["hands"] += 1
            for key, v in zip(("vpip", "pfr", "agg", "pas", "faced", "fold"), f):
                d[key] += v
        if len(self.ll) and self.ll[-1][0] % 25 == 0:
            self.trace.append((self.ll[-1][0], {q: m.stats() for q, m in self.models.items()}))

    # ------------------------------------------------------------------ rangos
    def _pct_arr(self, pool, st, obs):
        key = (obs.street, st)
        if key not in self.pcache:
            if st == 0:
                arr = [PREFLOP_PCT[hole_class(h)] for h in pool.holes]
            else:
                b = obs.board[: STREET_LEN[st]]
                sc = [evaluate(list(h) + list(b)) for h in pool.holes]
                ss = sorted(sc)
                n = len(ss)
                arr = [(bisect_left(ss, s) + bisect_right(ss, s)) / (2.0 * n) for s in sc]
            self.pcache[key] = arr
        return self.pcache[key]

    def _weights(self, q, pool, obs):
        m = self.model(q)
        w = [1.0] * len(pool.holes)
        for st, (ctx, cls) in self.acts[q].items():
            if st > obs.street:
                continue
            dens = m.density(ctx, cls, st)
            parr = pool.pct if st == obs.street else self._pct_arr(pool, st, obs)
            g = 1.0 if st == obs.street else 0.6
            w = [wi * dens[min(NB - 1, int(p * NB))] ** g for wi, p in zip(w, parr)]
        return w

    # ------------------------------------------------------------------ decisión
    def act(self, obs):
        legal = obs.legal
        opps = [q for q in range(N) if q != obs.seat and not obs.folded[q]]
        if len(legal) == 1 or not opps:
            return CALL
        hero, board, street = obs.hole, obs.board, obs.street
        pool = self.pools.get(street)
        if pool is None:
            pool = self.pools[street] = Pool(hero + board, board, self.rng, 220)
        unseen = unseen_cards(hero + board)
        cum = {q: list(accumulate(self._weights(q, pool, obs))) for q in opps}
        k = int(self.n_worlds * 1.4)
        idx = {q: self.rng.choices(range(len(pool.holes)), cum_weights=cum[q], k=k) for q in opps}
        need = 5 - len(board)
        worlds = []
        for i in range(k):
            hs = [pool.holes[idx[q][i]] for q in opps]
            used = {c for h in hs for c in h}
            if len(used) < 2 * len(opps):
                continue
            run = self.rng.sample([c for c in unseen if c not in used], need) if need else []
            fb = list(board) + run
            worlds.append((evaluate(list(hero) + fb), [evaluate(list(h) + fb) for h in hs],
                           [pool.pct[idx[q][i]] for q in opps]))
            if len(worlds) >= self.n_worlds:
                break
        ph = preflop_pct(hero) if not board else pool.pct_of(evaluate(list(hero) + list(board)))

        P, C, bh = obs.pot, obs.to_call, obs.bets[obs.seat]
        cap = bh + obs.stack
        raises = [a for a in legal if a > CALL]
        resp = [j for j, q in enumerate(opps) if not obs.allin[q]]
        allin = [j for j, q in enumerate(opps) if obs.allin[q]]
        params = {}
        for a in raises:
            T = legal[a]
            add = T - bh
            th = []
            for j in resp:
                q = opps[j]
                tc = T - obs.bets[q]
                bk = bucket(tc / max(1.0, P + add - tc)) if street > 0 else 0
                pf, _, pa = self.model(q).probs((street, 1, bk))
                th.append((pf, 1 - pa))
            params[a] = (T, add, th)

        ev = {a: 0.0 for a in legal}
        for hs, osc, pc in worlds:
            ev[CALL] += (R_CALL if C > 0 else R_CHECK) * share(hs, osc) * (P + C) - C
            for a in raises:
                T, add, th = params[a]
                rr, callers = None, []
                for n_, j in enumerate(resp):
                    pf, rthr = th[n_]
                    if pc[j] < pf:
                        continue
                    if pc[j] >= rthr and rr is None and T < cap:
                        rr = j
                    else:
                        callers.append(j)
                if rr is not None:
                    q = opps[rr]
                    tc2 = min(cap, obs.bets[q] + obs.stacks[q], int(2.6 * T))
                    if ph < CONT:
                        ev[a] -= add
                    elif tc2 <= T:
                        callers.append(rr)
                        rr = None
                    else:
                        pot_f = P + (tc2 - bh) + (tc2 - obs.bets[q])
                        sc = [osc[rr]] + [osc[j] for j in allin]
                        ev[a] += R_RAISE * share(hs, sc) * pot_f - (tc2 - bh)
                        continue
                    if ph < CONT:
                        continue
                if not callers:
                    ev[a] += share(hs, [osc[j] for j in allin]) * P
                else:
                    pot_f = P + add + sum(T - obs.bets[opps[j]] for j in callers)
                    sc = [osc[j] for j in callers] + [osc[j] for j in allin]
                    ev[a] += R_RAISE * share(hs, sc) * pot_f - add
        nw = max(1, len(worlds))
        ev = {a: v / nw for a, v in ev.items()}

        best, bv = CALL, ev[CALL]
        if FOLD in ev and ev[FOLD] > bv:
            best, bv = FOLD, ev[FOLD]
        margin = 0.015 * (P + C) + 0.1
        base = bv
        for a in raises:
            if ev[a] > base + margin and (best <= CALL or ev[a] > bv):
                best, bv = a, ev[a]
        self.last = dict(ev=ev, opps=opps, best=best, street=street, ph=ph)
        return best

    # ------------------------------------------------------------------ informe
    def read_opponents(self) -> dict:
        return {q: m.stats() for q, m in self.models.items()}


def label(st: dict) -> str:
    """Etiqueta legible del estilo estimado."""
    if st["n"] < 30:
        return "aún sin datos"
    v, p, a = st["vpip"], st["pfr"], st["aggr"]
    if v < .27:
        return "Roca (muy selectivo)"
    if v > .55 and p > .35:
        return "Maníaco (agresivo y suelto)"
    if v > .5 and a < .4:
        return "Estación de llamadas (suelto y pasivo)"
    return "Regular (equilibrado)"
