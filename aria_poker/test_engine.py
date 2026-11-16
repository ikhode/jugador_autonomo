"""Pruebas del motor: evaluador contra una referencia lenta, frecuencias teóricas
de manos de 7 cartas y conservación de fichas con agentes aleatorios (incluye all-ins
y botes laterales)."""
import random
from collections import Counter
from itertools import combinations

from engine import Agent, evaluate, category, parse_cards, play_match, N


def ref5(cards):
    ranks = sorted((c >> 2 for c in cards), reverse=True)
    flush = len({c & 3 for c in cards}) == 1
    cnt = Counter(ranks)
    uniq = sorted(set(ranks), reverse=True)
    hi = None
    if len(uniq) == 5:
        if uniq[0] - uniq[4] == 4:
            hi = uniq[0]
        elif uniq == [12, 3, 2, 1, 0]:
            hi = 3
    groups = sorted(cnt.items(), key=lambda kv: (kv[1], kv[0]), reverse=True)
    pat, order = [n for _, n in groups], [r for r, _ in groups]
    if flush and hi is not None: return (8, [hi])
    if pat == [4, 1]: return (7, order)
    if pat == [3, 2]: return (6, order)
    if flush: return (5, ranks)
    if hi is not None: return (4, [hi])
    if pat == [3, 1, 1]: return (3, order)
    if pat == [2, 2, 1]: return (2, order)
    if pat == [2, 1, 1, 1]: return (1, order)
    return (0, ranks)


def ref_best(cards):
    return max(ref5(c) for c in combinations(cards, 5))


def test_known_hands():
    h = lambda s: evaluate(parse_cards(s))
    assert h("As Ks Qs Js Ts 2d 3c") > h("9s Ks Qs Js Ts 2d 3c")      # escalera de color
    assert h("9s 9h 9d 9c 2d 3c 4h") > h("Ah Ad Ac Ks Kd 2c 3c")      # poker > full
    assert h("Ah Ad Ac Ks Kd 2c 3c") > h("2h 5h 9h Jh Kh 3c 4d")      # full > color
    assert h("2h 5h 9h Jh Kh 3c 4d") > h("5s 6h 7d 8c 9s Ac Ad")      # color > escalera
    assert h("As 2h 3d 4c 5s 9c Kd") == h("As 2h 3d 4c 5s 8c Qd")     # rueda
    assert h("6s 2h 3d 4c 5s 9c Kd") > h("As 2h 3d 4c 5s 9c Kd")      # escalera al 6 > rueda
    assert h("Ah Kd 7c 5s 3d 2c 9h") < h("2h 2d 7c 5s 3d 9c Jh")      # carta alta < par
    assert h("Ah Ad Ks Kd Qc 2c 3c") > h("Ah Ad Ks Qd Jc 2c 3c")      # doble par > par
    assert h("2h 2d 2s 5d 7c 9c Jh") > h("Ah Ad Ks Kd Qc 2c 3c")      # trio > doble par
    print("manos conocidas ok")


def test_against_reference(n=30000):
    rng = random.Random(1)
    for _ in range(n):
        a, b = rng.sample(range(52), 7), rng.sample(range(52), 7)
        ea, eb = evaluate(a), evaluate(b)
        ra, rb = ref_best(a), ref_best(b)
        assert (ea > eb) - (ea < eb) == (ra > rb) - (ra < rb), (a, b)
        assert category(ea) == ra[0]
    print(f"{n} pares de manos de 7 cartas coinciden con la referencia")


def test_frequencies(n=200000):
    rng = random.Random(2)
    c = Counter(category(evaluate(rng.sample(range(52), 7))) for _ in range(n))
    theo = {0: .1741, 1: .4380, 2: .2350, 3: .0483, 4: .0462, 5: .0303, 6: .0260, 7: .00168, 8: .00031}
    for k, v in theo.items():
        got = c[k] / n
        assert abs(got - v) < max(0.004, v * 0.15), (k, got, v)
    print("frecuencias de categorías ok:", {k: round(c[k] / n, 4) for k in sorted(c)})


class RandomAgent(Agent):
    def __init__(self, seed):
        self.rng = random.Random(seed)

    def act(self, obs):
        return self.rng.choice(list(obs.legal))


def test_conservation(n=20000):
    ags = [RandomAgent(i) for i in range(N)]
    prof = play_match(ags, n, seed=5)
    assert abs(sum(prof)) < 1e-9, prof
    a = play_match([RandomAgent(i) for i in range(N)], 500, seed=9)
    b = play_match([RandomAgent(i) for i in range(N)], 500, seed=9)
    assert a == b
    print(f"{n} manos aleatorias: fichas conservadas, resultado reproducible; profits={prof}")


if __name__ == "__main__":
    test_known_hands()
    test_against_reference()
    test_frequencies()
    test_conservation()
    print("TODO OK")
