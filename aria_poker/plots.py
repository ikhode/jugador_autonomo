"""plots.py — Gráficas de la evidencia de aprendizaje (lee results.json y shift.json)."""
from __future__ import annotations

import json
import os

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

SURFACE, INK, INK2, MUTED, GRID, AXIS = "#fcfcfb", "#0b0b0b", "#52514e", "#898781", "#e1e0d9", "#c3c2b7"
BLUE, ORANGE, AQUA = "#2a78d6", "#eb6834", "#1baf7a"     # paleta categórica validada, slots 1-3
BB = 2
SCEN = ["Estación + Maníaco", "Roca + Maníaco", "Estación + TAG", "Roca + TAG"]


def style(ax, title=None, xlabel=None, ylabel=None):
    ax.set_facecolor(SURFACE)
    for s in ("top", "right"):
        ax.spines[s].set_visible(False)
    for s in ("left", "bottom"):
        ax.spines[s].set_color(AXIS)
    ax.grid(axis="y", color=GRID, linewidth=0.8)
    ax.set_axisbelow(True)
    ax.tick_params(colors=MUTED, labelsize=9, length=0)
    if title:
        ax.set_title(title, loc="left", fontsize=11, color=INK, fontweight="bold")
    if xlabel:
        ax.set_xlabel(xlabel, color=INK2, fontsize=9)
    if ylabel:
        ax.set_ylabel(ylabel, color=INK2, fontsize=9)


def roll(x, w):
    c = np.cumsum(np.insert(np.asarray(x, float), 0, 0))
    return (c[w:] - c[:-w]) / w


def fig1(res, out):
    fig, axs = plt.subplots(2, 2, figsize=(11, 7), facecolor=SURFACE, sharex=True)
    for ax, sc in zip(axs.ravel(), SCEN):
        L = {r["job"][1]: np.array(r["net"]) for r in res if r["job"][0] == sc and r["job"][3]}
        F = {r["job"][1]: np.array(r["net"]) for r in res if r["job"][0] == sc and not r["job"][3]}
        d = np.array([np.cumsum(L[s] - F[s]) / BB for s in sorted(L)])
        x = np.arange(1, d.shape[1] + 1)
        for row in d:
            ax.plot(x, row, color=MUTED, lw=0.8, alpha=0.55)
        ax.plot(x, d.mean(0), color=BLUE, lw=2)
        ax.axhline(0, color=AXIS, lw=1)
        ax.annotate(f"{d.mean(0)[-1]:+,.0f} bb", (x[-1], d.mean(0)[-1]), xytext=(-6, 14),
                    textcoords="offset points", ha="right", color=INK, fontsize=10, fontweight="bold",
                    bbox=dict(fc=SURFACE, ec="none", pad=1.5))
        style(ax, sc, None, "ventaja acumulada (bb)")
    for ax in axs[1]:
        ax.set_xlabel("manos jugadas", color=INK2, fontsize=9)
    fig.suptitle("ARIA que aprende vs su gemelo congelado, con las mismas cartas\n"
                 "azul = promedio de semillas · gris = cada semilla",
                 x=0.06, ha="left", fontsize=12, color=INK)
    fig.tight_layout(rect=(0, 0, 1, 0.93))
    fig.savefig(out, dpi=150, facecolor=SURFACE)
    plt.close(fig)


def fig2(res, out):
    fig, (a, b) = plt.subplots(1, 2, figsize=(11, 4.4), facecolor=SURFACE)
    for learning, color, name in ((True, BLUE, "aprende"), (False, ORANGE, "congelada")):
        runs = [r for r in res if r["job"][3] == learning]
        nb = max(h for r in runs for h, _, _ in r["ll"]) // 100 + 1
        acc = np.zeros(nb); cnt = np.zeros(nb)
        for r in runs:
            for h, _, v in r["ll"]:
                acc[h // 100] += v; cnt[h // 100] += 1
        y = acc / np.maximum(cnt, 1)
        x = np.arange(nb) * 100 + 50
        a.plot(x, y, color=color, lw=2)
        a.annotate(name, (x[-1], y[-1]), xytext=(-4, 8 if learning else -14), textcoords="offset points",
                   ha="right", color=INK, fontsize=10, fontweight="bold")
    style(a, "Error al predecir la próxima acción del rival", "manos", "pérdida log (menor = mejor)")
    errs = {"VPIP": {}, "agresión posflop": {}}
    for r in [r for r in res if r["job"][3]]:
        for h, st in r["trace"]:
            for q in ("1", "2"):
                rows = r["truth"][int(q)][max(0, h - 200):h]
                if len(rows) < 50:
                    continue
                v = np.mean([x[0] for x in rows])
                g = sum(x[2] for x in rows) / max(1, sum(x[2] + x[3] for x in rows))
                errs["VPIP"].setdefault(h, []).append(abs(st[q]["vpip"] - v))
                errs["agresión posflop"].setdefault(h, []).append(abs(st[q]["aggr"] - g))
    for (name, d), color in zip(errs.items(), (BLUE, ORANGE)):
        xs = sorted(d)
        ys = [np.mean(d[h]) * 100 for h in xs]
        b.plot(xs, ys, color=color, lw=2)
        b.annotate(name, (xs[-1], ys[-1]), xytext=(-4, 8), textcoords="offset points", ha="right",
                   color=INK, fontsize=10, fontweight="bold")
    style(b, "Qué tan lejos está su lectura de la realidad", "manos", "error absoluto (puntos %)")
    fig.tight_layout()
    fig.savefig(out, dpi=150, facecolor=SURFACE)
    plt.close(fig)


def fig3(res, out, at=2000, w=300):
    fig, ax = plt.subplots(figsize=(11, 4.6), facecolor=SURFACE)
    spec = [("aprende con olvido", lambda j: j[3] and j[4] < 1, BLUE),
            ("aprende sin olvido", lambda j: j[3] and j[4] == 1.0, ORANGE),
            ("congelada", lambda j: not j[3], AQUA)]
    for (name, flt, color), dy in zip(spec, (26, 6, -16)):
        rs = [np.array(r["net"]) for r in res if flt(r["job"])]
        y = roll(np.mean(rs, 0), w) / BB * 100
        x = np.arange(w, w + len(y))
        ax.plot(x, y, color=color, lw=2, label=name)
    ax.legend(frameon=False, loc="upper right", fontsize=9, labelcolor=INK2)
    ax.axvline(at, color=MUTED, lw=1, ls="--")
    ax.text(at + 20, ax.get_ylim()[1], "los rivales cambian de estilo", color=INK2, fontsize=9, va="top")
    style(ax, "Adaptación a un cambio de estilo de los rivales", "manos", f"bb/100 (ventana de {w} manos)")
    fig.tight_layout()
    fig.savefig(out, dpi=150, facecolor=SURFACE)
    plt.close(fig)


def make():
    outs = []
    if os.path.exists("results.json"):
        res = json.load(open("results.json"))
        fig1(res, "1_ventaja_vs_congelada.png")
        fig2(res, "2_aprendizaje_del_modelo.png")
        outs += ["1_ventaja_vs_congelada.png", "2_aprendizaje_del_modelo.png"]
    if os.path.exists("shift.json"):
        fig3(json.load(open("shift.json")), "3_cambio_de_estilo.png")
        outs.append("3_cambio_de_estilo.png")
    print("gráficas:", ", ".join(outs))
