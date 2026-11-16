"""
engine.py — Motor de Texas Hold'em No-Limit para 3 jugadores.

Cada jugador recibe 2 cartas privadas y hay 5 cartas comunitarias
(flop 3 + turn 1 + river 1). Ciegas 1/2, stacks de 100 ciegas grandes que se
reponen en cada mano, para que cada mano sea una muestra independiente.

Abstracción de acciones (idéntica para todos los agentes):
    FOLD, CALL (igualar/pasar), R33, R66, R100 (subir a 33%/66%/100% del bote), ALLIN

Simplificación documentada: un all-in corto (menor que una subida completa)
no reabre la acción a quienes ya hablaron; solo pueden igualar o retirarse.
"""
from __future__ import annotations

import random
from dataclasses import dataclass

RANKS = "23456789TJQKA"
SUITS = "cdhs"
FOLD, CALL, R33, R66, R100, ALLIN = range(6)
ACTION_NAMES = ["fold", "call/check", "raise 33%", "raise 66%", "raise pot", "all-in"]
RAISE_FRACS = ((R33, 0.33), (R66, 0.66), (R100, 1.0))
SB, BB, START_STACK, N = 1, 2, 200, 3
POS_NAMES = ("BTN", "SB", "BB")
CAT_NAMES = ["carta alta", "par", "doble par", "trio", "escalera", "color",
             "full house", "poker", "escalera de color"]


# --------------------------------------------------------------------------
# Cartas y evaluador de manos (5 a 7 cartas). Carta = 0..51, rango = c>>2, palo = c&3
# --------------------------------------------------------------------------
def card_str(c: int) -> str:
    return RANKS[c >> 2] + SUITS[c & 3]


def parse_cards(s: str) -> tuple:
    return tuple(RANKS.index(t[0]) * 4 + SUITS.index(t[1]) for t in s.split())


def _pack(ranks) -> int:
    v = 0
    for i, r in enumerate(ranks):
        v |= r << (16 - 4 * i)
    return v


def _straight_top(mask: int) -> int:
    """Rango más alto de una escalera dentro de la máscara de rangos, o -1."""
    ext = (mask << 1) | (mask >> 12)  # el As también cuenta como carta baja
    for top in range(13, 3, -1):
        if (ext >> (top - 4)) & 31 == 31:
            return top - 1
    return -1


def evaluate(cards) -> int:
    """Puntaje entero de la mejor mano de 5 cartas (mayor = mejor)."""
    rc = [0] * 13
    suit_mask = [0, 0, 0, 0]
    suit_n = [0, 0, 0, 0]
    mask = 0
    for c in cards:
        r, s = c >> 2, c & 3
        rc[r] += 1
        suit_mask[s] |= 1 << r
        suit_n[s] += 1
        mask |= 1 << r
    fm = 0
    for s in range(4):
        if suit_n[s] >= 5:
            fm = suit_mask[s]
            break
    if fm:
        t = _straight_top(fm)
        if t >= 0:
            return (8 << 20) | (t << 16)
    quads, trips, pairs, singles = [], [], [], []
    for r in range(12, -1, -1):
        n = rc[r]
        if n == 1:
            singles.append(r)
        elif n == 2:
            pairs.append(r)
        elif n == 3:
            trips.append(r)
        elif n == 4:
            quads.append(r)
    if quads:
        return (7 << 20) | (quads[0] << 16) | (max(trips + pairs + singles) << 12)
    if trips and (len(trips) > 1 or pairs):
        return (6 << 20) | (trips[0] << 16) | (max(trips[1:] + pairs) << 12)
    if fm:
        top5 = [r for r in range(12, -1, -1) if fm >> r & 1][:5]
        return (5 << 20) | _pack(top5)
    t = _straight_top(mask)
    if t >= 0:
        return (4 << 20) | (t << 16)
    if trips:
        return (3 << 20) | _pack([trips[0]] + sorted(pairs + singles, reverse=True)[:2])
    if len(pairs) >= 2:
        return (2 << 20) | _pack([pairs[0], pairs[1], max(pairs[2:] + singles)])
    if pairs:
        return (1 << 20) | _pack([pairs[0]] + singles[:3])
    return _pack(singles[:5])


def category(score: int) -> int:
    return score >> 20


# --------------------------------------------------------------------------
# Interfaz de agentes
# --------------------------------------------------------------------------
@dataclass
class Obs:
    seat: int
    hole: tuple
    board: tuple
    street: int          # 0 preflop, 1 flop, 2 turn, 3 river
    pot: int             # bote total, incluidas las apuestas de la calle actual
    to_call: int
    stack: int           # fichas que le quedan al jugador
    bets: tuple          # apuestas de cada asiento en la calle actual
    stacks: tuple
    folded: tuple
    allin: tuple
    button: int
    legal: dict          # accion -> (para subidas) total al que sube; CALL -> fichas a poner
    facing: int          # 1 si enfrenta una apuesta/subida real
    ratio: float         # tamaño de la apuesta enfrentada / bote previo a ella
    min_raise_to: int
    pos: str
    hand_no: int


class Agent:
    name = "base"

    def new_hand(self, hand_no: int, seat: int, button: int): ...
    def act(self, obs: Obs) -> int: raise NotImplementedError
    def observe(self, ev: dict): ...          # toda acción pública, de cualquier jugador
    def showdown(self, info: dict): ...       # cartas reveladas al final
    def end_hand(self, net: float): ...


# --------------------------------------------------------------------------
# Una mano
# --------------------------------------------------------------------------
class Hand:
    def __init__(self, agents, hand_no: int, button: int, rng: random.Random):
        self.agents, self.hand_no, self.button = agents, hand_no, button
        deck = list(range(52))
        rng.shuffle(deck)
        self.hole = [tuple(deck[2 * i: 2 * i + 2]) for i in range(N)]
        self.full_board = tuple(deck[6:11])
        self.board = ()
        self.stacks = [START_STACK] * N
        self.bets = [0] * N
        self.invested = [0] * N
        self.folded = [False] * N
        self.allin = [False] * N
        self.cur_bet = 0
        self.min_raise = BB
        self.street = 0
        self.acted, self.locked = set(), set()
        self.log = []

    @property
    def pot(self) -> int:
        return sum(self.invested)

    def _put(self, p: int, amt: int) -> int:
        amt = min(amt, self.stacks[p])
        self.stacks[p] -= amt
        self.bets[p] += amt
        self.invested[p] += amt
        if self.stacks[p] == 0:
            self.allin[p] = True
        return amt

    def legal(self, p: int) -> dict:
        to_call = self.cur_bet - self.bets[p]
        stack = self.stacks[p]
        acts = {}
        if to_call > 0:
            acts[FOLD] = 0
        acts[CALL] = min(to_call, stack)
        others = [q for q in range(N) if q != p and not self.folded[q] and not self.allin[q]]
        if stack > to_call and others and p not in self.locked:
            cap = self.bets[p] + stack
            pot_after_call = self.pot + to_call
            min_to = self.cur_bet + self.min_raise
            used = set()
            for a, f in RAISE_FRACS:
                to = max(min_to, int(round(self.cur_bet + f * pot_after_call)))
                if to < cap and to not in used:
                    used.add(to)
                    acts[a] = to
            acts[ALLIN] = cap
        return acts

    def _act(self, p: int):
        legal = self.legal(p)
        to_call = self.cur_bet - self.bets[p]
        facing = int(self.cur_bet > (BB if self.street == 0 else 0))
        pot = self.pot
        ratio = to_call / max(1, pot - to_call) if (facing and to_call > 0) else 0.0
        obs = Obs(p, self.hole[p], self.board, self.street, pot, to_call, self.stacks[p],
                  tuple(self.bets), tuple(self.stacks), tuple(self.folded), tuple(self.allin),
                  self.button, legal, facing, ratio, self.cur_bet + self.min_raise,
                  POS_NAMES[(p - self.button) % N], self.hand_no)
        a = self.agents[p].act(obs)
        if a not in legal:
            a = CALL
        stack_before = self.stacks[p]
        if a == FOLD:
            self.folded[p] = True
            amount = 0
        elif a == CALL:
            amount = self._put(p, to_call)
        else:
            to = legal[a]
            amount = self._put(p, to - self.bets[p])
            inc = to - self.cur_bet
            self.cur_bet = to
            if inc >= self.min_raise:          # subida completa: reabre la acción
                self.min_raise = inc
                self.acted, self.locked = set(), set()
            else:                              # all-in corto: no reabre
                self.locked |= self.acted
        self.acted.add(p)
        ev = dict(hand=self.hand_no, seat=p, street=self.street, act=a, amount=amount,
                  to=self.bets[p], pot_before=pot, to_call=to_call, facing=facing,
                  ratio=ratio, board=self.board, stack_before=stack_before,
                  pos=POS_NAMES[(p - self.button) % N])
        self.log.append(ev)
        for ag in self.agents:
            ag.observe(ev)

    def _round(self, first: int):
        self.acted, self.locked = set(), set()
        p = first
        while True:
            live = [q for q in range(N) if not self.folded[q]]
            if len(live) == 1:
                return
            can = [q for q in live if not self.allin[q]]
            if not can:
                return
            if len(can) == 1 and self.bets[can[0]] >= self.cur_bet:
                return
            if all(q in self.acted and self.bets[q] == self.cur_bet for q in can):
                return
            for _ in range(N):
                if (not self.folded[p] and not self.allin[p]
                        and not (p in self.acted and self.bets[p] == self.cur_bet)):
                    break
                p = (p + 1) % N
            self._act(p)
            p = (p + 1) % N

    def _settle(self):
        live = [p for p in range(N) if not self.folded[p]]
        pay = [0] * N
        scores = {}
        if len(live) == 1:
            pay[live[0]] = self.pot
        else:
            scores = {p: evaluate(self.hole[p] + self.full_board) for p in live}
            prev = 0
            for lv in sorted({self.invested[p] for p in live}):
                chunk = sum(max(0, min(self.invested[q], lv) - prev) for q in range(N))
                elig = [p for p in live if self.invested[p] >= lv]
                best = max(scores[p] for p in elig)
                winners = sorted((p for p in elig if scores[p] == best),
                                 key=lambda p: (p - self.button - 1) % N)
                share, rem = divmod(chunk, len(winners))
                for i, p in enumerate(winners):
                    pay[p] += share + (1 if i < rem else 0)
                prev = lv
        return live, pay, scores

    def play(self) -> dict:
        sb, bb = (self.button + 1) % N, (self.button + 2) % N
        self._put(sb, SB)
        self._put(bb, BB)
        self.cur_bet = BB
        for p, ag in enumerate(self.agents):
            ag.new_hand(self.hand_no, p, self.button)
        for street in range(4):
            self.street = street
            if street > 0:
                self.board = self.full_board[: {1: 3, 2: 4, 3: 5}[street]]
                self.bets = [0] * N
                self.cur_bet, self.min_raise = 0, BB
            first = self.button if street == 0 else (self.button + 1) % N
            self._round(first)
            if sum(not f for f in self.folded) == 1:
                break
        live, pay, scores = self._settle()
        showdown = len(live) > 1
        net = [pay[p] - self.invested[p] for p in range(N)]
        if showdown:
            self.board = self.full_board
            info = dict(holes={p: self.hole[p] for p in live}, board=self.full_board,
                        hand_no=self.hand_no)
            for ag in self.agents:
                ag.showdown(info)
        for p, ag in enumerate(self.agents):
            ag.end_hand(net[p])
        return dict(net=net, holes=self.hole, board=self.full_board, showdown=showdown,
                    live=live, log=self.log, scores=scores, button=self.button,
                    invested=list(self.invested), hand_no=self.hand_no)


def play_match(agents, n_hands: int, seed: int = 0, start_hand: int = 0, listeners=(),
               hand_hook=None):
    """Juega n_hands manos. Los repartos dependen solo de (seed, número de mano),
    así dos partidas con la misma semilla reciben exactamente las mismas cartas."""
    profits = [0.0] * N
    for h in range(start_hand, start_hand + n_hands):
        hand = Hand(list(agents) + list(listeners)[:0], h, h % N, random.Random(seed * 1_000_003 + h))
        res = hand.play()
        for p in range(N):
            profits[p] += res["net"][p]
        for ls in listeners:
            ls.on_hand(res)
        if hand_hook:
            hand_hook(h, res)
    return profits
