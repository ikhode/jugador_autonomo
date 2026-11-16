"""
run.py — Demostración y experimentos.

    python run.py demo          # partida en vivo: ARIA lee a sus rivales y se adapta
    python run.py experiments   # aprende vs gemelo congelado (mismas cartas), varias semillas
    python run.py shift         # los rivales cambian de estilo a mitad de partida
    python run.py plots         # gráficas a partir de results.json / shift.json
    python run.py all           # experiments + shift + plots

Seat 0 = ARIA (o su gemelo congelado). Seats 1 y 2 = bots con estilos explotables.
"""
from __future__ import annotations

import json
import math
import sys
import time
from concurrent.futures import ProcessPoolExecutor

from bots import Bot
from engine import play_match
from learner import Learner, label
from stats import GroundTruth

BB = 2
SCENARIOS = {
    "Estación + Maníaco": ("Estacion", "Maniaco"),
    "Roca + Maníaco": ("Roca", "Maniaco"),
    "Estación + TAG": ("Estacion", "TAG"),
    "Roca + TAG": ("Roca", "TAG"),
}
SHIFT = dict(start=("Estacion", "Maniaco"), to=("Roca", "TAG"), at=2000)


def run_one(job):
    scen, seed, hands, learning, decay, shift_at = job
    start = SCENARIOS[scen] if scen in SCENARIOS else SHIFT["start"]
    L = Learner(seed=seed, learning=learning, decay=decay)
    bots = [Bot(start[0], 100 + seed), Bot(start[1], 200 + seed)]
    gt, net = GroundTruth(), []

    def hook(h, res):
        net.append(res["net"][0])
        if shift_at and h + 1 == shift_at:
            for b, s in zip(bots, SHIFT["to"]):
                b.set_style(s)

    play_match([L] + bots, hands, seed=seed, listeners=[gt], hand_hook=hook)
    return dict(job=list(job), net=net, ll=[(h, q, v) for h, q, v in L.ll],
                trace=L.trace, truth=gt.rows)


def _pool_run(jobs, log):
    out, t0 = [], time.time()
    with ProcessPoolExecutor(2) as ex:
        for i, r in enumerate(ex.map(run_one, jobs), 1):
            out.append(r)
            print(f"  [{i}/{len(jobs)}] {r['job'][0]} semilla {r['job'][1]} "
                  f"{'aprende' if r['job'][3] else 'congelada'}  ({time.time() - t0:.0f}s)", flush=True)
    json.dump(out, open(log, "w"))
    return out


def experiments(hands=3000, seeds=(1, 2, 3, 4)):
    jobs = [(s, sd, hands, lr, 0.995, 0) for s in SCENARIOS for sd in seeds for lr in (True, False)]
    out = _pool_run(jobs, "results.json")
    summarize(out)


def shift(hands=4000, seeds=(1, 2, 3)):
    jobs = [("shift", sd, hands, lr, dc, SHIFT["at"])
            for sd in seeds for lr, dc in ((True, 0.995), (True, 1.0), (False, 0.995))]
    out = _pool_run(jobs, "shift.json")
    for tag, flt in (("aprende con olvido", lambda j: j[3] and j[4] < 1),
                     ("aprende sin olvido", lambda j: j[3] and j[4] == 1.0),
                     ("congelada", lambda j: not j[3])):
        rs = [r for r in out if flt(r["job"])]
        a = [sum(r["net"][:SHIFT["at"]]) / BB / SHIFT["at"] * 100 for r in rs]
        b = [sum(r["net"][SHIFT["at"]:]) / BB / (hands - SHIFT["at"]) * 100 for r in rs]
        print(f"  {tag:20s} antes del cambio: {sum(a)/len(a):7.1f} bb/100 | despues: {sum(b)/len(b):7.1f} bb/100")


def summarize(out):
    print("\nRESULTADOS (bb/100, media de semillas; diferencia pareada = aprende - congelada)")
    for scen in SCENARIOS:
        L = {r["job"][1]: r["net"] for r in out if r["job"][0] == scen and r["job"][3]}
        F = {r["job"][1]: r["net"] for r in out if r["job"][0] == scen and not r["job"][3]}
        hands = len(next(iter(L.values())))
        third = hands // 3
        diffs = [(sum(L[s]) - sum(F[s])) / BB / hands * 100 for s in L]
        d1 = [(sum(L[s][:third]) - sum(F[s][:third])) / BB / third * 100 for s in L]
        d3 = [(sum(L[s][-third:]) - sum(F[s][-third:])) / BB / third * 100 for s in L]
        m = sum(diffs) / len(diffs)
        se = math.sqrt(sum((x - m) ** 2 for x in diffs) / (len(diffs) - 1) / len(diffs))
        lm = sum(sum(v) for v in L.values()) / len(L) / BB / hands * 100
        fm = sum(sum(v) for v in F.values()) / len(F) / BB / hands * 100
        print(f"  {scen:20s} aprende {lm:7.1f} | congelada {fm:7.1f} | dif {m:+7.1f} ± {se:5.1f} "
              f"| dif primer tercio {sum(d1)/len(d1):+7.1f} -> ultimo tercio {sum(d3)/len(d3):+7.1f}")


def demo(hands=1500, scen="Estación + Maníaco", seed=21, every=250):
    styles = SCENARIOS[scen]
    L = Learner(seed=seed)
    bots = [Bot(styles[0], 100 + seed), Bot(styles[1], 200 + seed)]
    gt, total = GroundTruth(), [0.0]
    print(f"Mesa: ARIA (asiento 0) vs {styles[0]} (asiento 1) vs {styles[1]} (asiento 2)\n"
          "ARIA empieza sin saber nada de ellos; solo ve sus acciones y sus cartas en showdown.\n")

    def hook(h, res):
        total[0] += res["net"][0]
        if (h + 1) % every:
            return
        n = h + 1
        w0 = n - every
        print(f"=== Mano {n}: ARIA {total[0] / BB:+.0f} bb ({total[0] / BB / n * 100:+.0f} bb/100)")
        a_now, a_0 = gt.summary(0, w0, n), gt.summary(0, 0, every)
        print(f"    agresión posflop de ARIA: {a_now['aggr']:.0%} (primeras {every} manos: {a_0['aggr']:.0%})")
        for q, st in L.read_opponents().items():
            real = gt.summary(q, max(0, n - 200), n)
            print(f"    asiento {q}: lee VPIP {st['vpip']:.0%} PFR {st['pfr']:.0%} agr {st['aggr']:.0%} "
                  f"fold-ante-apuesta {st['fold_to_bet']:.0%} -> {label(st)}")
            print(f"{'':14s}real (últ. 200): VPIP {real['vpip']:.0%} PFR {real['pfr']:.0%} "
                  f"agr {real['aggr']:.0%} fold-ante-apuesta {real['fold_to_bet']:.0%}")

    play_match([L] + bots, hands, seed=seed, listeners=[gt], hand_hook=hook)


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "demo"
    if cmd == "demo":
        demo()
    elif cmd == "experiments":
        experiments()
    elif cmd == "shift":
        shift()
    elif cmd == "plots":
        import plots
        plots.make()
    elif cmd == "all":
        experiments()
        shift()
        import plots
        plots.make()
    else:
        print(__doc__)
