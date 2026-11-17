"""
Motor de Análisis GTO, Evaluador Real de 7 Cartas, Tablas Nash Push/Fold,
Proyectos/Outs, Equidad Monte Carlo/Exacta y Decisor por EV.
"""
from itertools import combinations
import random
import re
from typing import Any, Dict, List, Optional, Set, Tuple

import config

RANKS_DESC = ["A", "K", "Q", "J", "T", "9", "8", "7", "6", "5", "4", "3", "2"]
SUITS_ALL = ["S", "H", "D", "C"]

VALORES_RANGO: Dict[str, int] = {
    "A": 14, "K": 13, "Q": 12, "J": 11, "10": 10, "T": 10,
    "9": 9, "8": 8, "7": 7, "6": 6, "5": 5, "4": 4, "3": 3, "2": 2,
}

VALORES_INV: Dict[int, str] = {
    14: "A", 13: "K", 12: "Q", 11: "J", 10: "T",
    9: "9", 8: "8", 7: "7", 6: "6", 5: "5", 4: "4", 3: "3", 2: "2",
}

MAPA_PALOS: Dict[str, str] = {
    "S": "S", "H": "H", "D": "D", "C": "C",
    "♠": "S", "♥": "H", "♦": "D", "♣": "C",
}

# Fuente única de verdad para rangos preflop 3-Max (expandible por guiones)
RANGOS_PREFLOP: Dict[str, List[str]] = {
    "S": ["AA-JJ", "AKs", "AKo"],
    "A": ["TT-88", "AQs-AJs", "AQo-AJo", "KQs", "KQo", "QJs", "JTs"],
    "B": ["77-22", "ATs-A2s", "ATo", "KJs-KTs", "KJo", "QTs", "T9s", "98s", "87s", "76s", "65s", "54s", "K9s", "Q9s", "J9s"],
    "C": ["A9o-A2o", "KTo", "QJo", "QTo", "JTo", "K8s-K2s", "Q8s-Q6s", "J8s", "T8s", "97s", "86s", "75s", "64s"],
}


def _expandir_rango_tokens(tokens: List[str]) -> Set[str]:
    """Expande notación compacta como 'AA-JJ', 'A9s-A2s' a códigos individuales."""
    expandido: Set[str] = set()
    for tok in tokens:
        tok = tok.strip()
        if "-" not in tok:
            expandido.add(tok)
            continue
        inicio, fin = tok.split("-", 1)
        # Parejas: 'AA-JJ'
        if len(inicio) == 2 and len(fin) == 2 and inicio[0] == inicio[1] and fin[0] == fin[1]:
            v_hi = VALORES_RANGO.get(inicio[0], 0)
            v_lo = VALORES_RANGO.get(fin[0], 0)
            for v in range(min(v_lo, v_hi), max(v_lo, v_hi) + 1):
                r = VALORES_INV[v]
                expandido.add(f"{r}{r}")
        # Suited/Offsuit con primera carta común: 'A9s-A2s'
        elif len(inicio) == 3 and len(fin) == 3 and inicio[0] == fin[0] and inicio[2] == fin[2]:
            hi_rank, suf = inicio[0], inicio[2]
            v_hi = VALORES_RANGO.get(inicio[1], 0)
            v_lo = VALORES_RANGO.get(fin[1], 0)
            for v in range(min(v_lo, v_hi), max(v_lo, v_hi) + 1):
                expandido.add(f"{hi_rank}{VALORES_INV[v]}{suf}")
        else:
            expandido.add(inicio)
            expandido.add(fin)
    return expandido


_TABLA_PREFLOP_TIER: Dict[str, str] = {}
for _tier, _tokens in RANGOS_PREFLOP.items():
    for _code in _expandir_rango_tokens(_tokens):
        if _code not in _TABLA_PREFLOP_TIER:
            _TABLA_PREFLOP_TIER[_code] = _tier

_BASE_SCORE_TIER = {"S": 94, "A": 84, "B": 73, "C": 60, "BASURA": 32}


def parsear_carta(carta_raw: str) -> Tuple[str, str]:
    """
    Normaliza cualquier representación de carta (ej. 'S10', '10S', '10♠', 'HA', 'A♥', 'DT')
    y devuelve (rango_normalizado, palo_normalizado).
    El rango normalizado usa 'T' para el 10 ('A','K','Q','J','T','9'..'2').
    """
    if not carta_raw:
        return "", ""
    c = carta_raw.strip().upper()
    if not c or c == "X":
        return "", ""

    palo = ""
    resto = c

    if c[0] in MAPA_PALOS and len(c) > 1 and c[1:] in VALORES_RANGO:
        palo = MAPA_PALOS[c[0]]
        resto = c[1:]
    elif c[-1] in MAPA_PALOS and len(c) > 1 and c[:-1] in VALORES_RANGO:
        palo = MAPA_PALOS[c[-1]]
        resto = c[:-1]

    rango = "T" if resto == "10" else (resto if resto in VALORES_RANGO else "")
    return rango, palo


PALOS_UNICODE: Dict[str, str] = {"S": "♠", "H": "♥", "D": "♦", "C": "♣"}


def formatear_carta_canonica(carta_raw: str) -> str:
    """Normaliza cualquier carta (ej. 'SJ', 'J♠', '10S', 'H10', 'D2') a formato canónico 'J♠', '10♠', '2♦'."""
    r, s = parsear_carta(carta_raw)
    if not r or not s:
        return ""
    rank_str = "10" if r == "T" else r
    suit_str = PALOS_UNICODE.get(s, s)
    return f"{rank_str}{suit_str}"


def formatear_cartas_canonicas(cartas: Any) -> str:
    """Convierte listas o cadenas de cartas (ej. ['SJ', 'H6'], 'H5 D2 H9') a formato canónico 'J♠ 6♥', '5♥ 2♦ 9♥'."""
    if not cartas:
        return ""
    if isinstance(cartas, list):
        items = cartas
    else:
        items = str(cartas).replace(",", " ").split()
    res = [formatear_carta_canonica(c) for c in items]
    return " ".join([c for c in res if c])


def parsear_lista_cartas(cartas_str: str) -> List[Tuple[str, str]]:
    if not cartas_str:
        return []
    tokens = str(cartas_str).upper().split()
    resultado: List[Tuple[str, str]] = []
    for t in tokens:
        r, s = parsear_carta(t)
        if r:
            resultado.append((r, s))
    return resultado


def generar_frecuencia_mixta_gto(
    seed: Optional[int] = None,
    rng: Optional[random.Random] = None,
) -> Dict[str, Any]:
    """Genera frecuencia mixta controlada por semilla (nunca usa random global)."""
    generador = rng if rng is not None else random.Random(seed)
    d100 = generador.randint(1, 100)
    if d100 <= 70:
        linea = "LÍNEA ESTÁNDAR DE VALOR AGRESIVO"
    elif d100 <= 90:
        linea = "LÍNEA DE TRAMPA / SLOWPLAY / CONTROL DE BOTE"
    else:
        linea = "LÍNEA DE SEMI-FAROL / PRESION DE BOTE"

    return {
        "d100": d100,
        "linea_sugerida": linea,
        "instruccion": f"Semilla Aleatoria GTO ({d100}/100): Usar {linea} para varianza anti-explotación.",
    }


def _codigo_mano_preflop(cartas: List[Tuple[str, str]]) -> Tuple[str, bool, bool, int, int]:
    (r1, s1), (r2, s2) = cartas[0], cartas[1]
    es_pareja = (r1 == r2)
    es_suited = (s1 == s2 and s1 != "")
    v1 = VALORES_RANGO.get(r1, 5)
    v2 = VALORES_RANGO.get(r2, 5)
    if v1 < v2:
        r1, r2 = r2, r1
        v1, v2 = v2, v1
    mano_code = f"{r1}{r2}" + ("s" if es_suited and not es_pareja else ("o" if not es_pareja else ""))
    return mano_code, es_pareja, es_suited, v1, v2


def clasificar_mano_preflop(
    mis_cartas_str: str,
    posicion: str = "BTN",
    stack_efectivo_bb: float = 25.0,
    situacion: str = "open",
) -> Dict[str, Any]:
    """
    Clasifica la mano preflop usando RANGOS_PREFLOP como única fuente de verdad,
    ajustada por posición 3-Max (BTN/SB/BB), stack efectivo en BB y situación.
    """
    cartas = parsear_lista_cartas(mis_cartas_str)
    if len(cartas) < 2:
        return {
            "tier": "DESCONOCIDO",
            "score": 0,
            "mano_code": "",
            "descripcion": "Cartas no detectadas aún",
            "posicion": posicion,
            "stack_efectivo_bb": stack_efectivo_bb,
            "situacion": situacion,
        }

    mano_code, es_pareja, es_suited, v1, v2 = _codigo_mano_preflop(cartas)
    tier = _TABLA_PREFLOP_TIER.get(mano_code, "BASURA")
    score = _BASE_SCORE_TIER[tier]

    # Ajuste fino por fuerza relativa dentro del tier
    if es_pareja:
        score = min(100, score + max(0, v1 - 10))
    elif v1 == 14:
        score = min(98, score + max(0, v2 - 10))
    elif es_suited:
        score = min(95, score + 2)

    pos_norm = (posicion or "BTN").strip().upper()
    sit_norm = (situacion or "open").strip().lower()
    stack_bb = float(stack_efectivo_bb or 25.0)

    # Ajustes contextuales 3-Max
    if pos_norm == "BTN" and sit_norm == "open" and tier == "C":
        score += 5
    elif pos_norm == "SB" and sit_norm == "vs_raise" and tier in ("C", "BASURA"):
        score -= 6
    elif sit_norm in ("vs_3bet", "vs_allin"):
        if tier in ("C", "BASURA"):
            score -= 12
        elif tier == "B" and not es_pareja and (v1 < 14 and v2 < 11):
            score -= 8

    # Short-stack (<12 BB): pares y cartas altas suben; conectores bajos bajan
    if stack_bb <= 12.0:
        if es_pareja or v1 == 14 or (v1 >= 12 and v2 >= 10):
            score = min(100, score + 4)
        elif not es_pareja and v1 <= 9 and es_suited:
            score = max(10, score - 6)

    # Recalibrar tier efectivo si la situación es muy agresiva (vs_3bet / vs_allin)
    tier_contextual = tier
    if sit_norm in ("vs_3bet", "vs_allin") and score < 62 and tier == "B":
        tier_contextual = "C"

    return {
        "tier": tier_contextual,
        "tier_base": tier,
        "score": max(0, min(100, score)),
        "mano_code": mano_code,
        "es_pareja": es_pareja,
        "es_suited": es_suited,
        "posicion": pos_norm,
        "stack_efectivo_bb": round(stack_bb, 1),
        "situacion": sit_norm,
    }


# ---------------------------------------------------------
# TABLAS NASH PUSH / FOLD (<= 10 BB, 3-Max y Heads-Up)
# ---------------------------------------------------------
_NASH_SHOVE_BTN_10BB = _expandir_rango_tokens([
    "AA-22", "AKs-A2s", "AKo-A5o", "KQs-K7s", "KQo-K9o",
    "QJs-Q9s", "QJo-QTo", "JTs-J9s", "JTo", "T9s", "98s", "87s",
])
_NASH_SHOVE_BTN_6BB = _NASH_SHOVE_BTN_10BB | _expandir_rango_tokens([
    "A4o-A2o", "K6s-K2s", "K8o-K7o", "Q8s-Q5s", "Q9o", "J8s", "T8s", "76s", "65s",
])
_NASH_SHOVE_SB_10BB = _NASH_SHOVE_BTN_6BB | _expandir_rango_tokens([
    "K6o-K2o", "Q4s-Q2s", "Q8o-Q6o", "J7s-J5s", "J9o-J8o", "T9o", "T7s", "97s", "86s", "54s",
])
_NASH_CALL_VS_SHOVE_10BB = _expandir_rango_tokens([
    "AA-55", "AKs-A8s", "AKo-ATo", "KQs-KTs", "KQo", "QJs",
])
_NASH_CALL_VS_SHOVE_6BB = _NASH_CALL_VS_SHOVE_10BB | _expandir_rango_tokens([
    "44-22", "A7s-A2s", "A9o-A7o", "K9s", "KJo", "QTs", "JTs",
])


def evaluar_push_fold_nash(
    mano_code: str,
    posicion: str = "BTN",
    stack_efectivo_bb: float = 10.0,
    situacion: str = "open",
    num_activos: int = 3,
) -> Dict[str, Any]:
    """
    Evalúa tablas Nash Push/Fold para stacks efectivos <= PUSH_FOLD_MAX_BB (10 BB).
    Decide directo ALL_IN / CALL / FOLD sin necesidad de consultar al LLM.
    """
    max_bb = float(getattr(config, "PUSH_FOLD_MAX_BB", 10.0))
    stack_bb = float(stack_efectivo_bb or 0.0)
    if stack_bb <= 0 or stack_bb > max_bb or not mano_code:
        return {"en_regimen_push_fold": False, "accion": "NONE", "razon": "Fuera de régimen Push/Fold"}

    pos = (posicion or "BTN").strip().upper()
    sit = (situacion or "open").strip().lower()

    if sit in ("vs_allin", "vs_shove", "vs_raise"):
        rango = _NASH_CALL_VS_SHOVE_6BB if stack_bb <= 6.0 else _NASH_CALL_VS_SHOVE_10BB
        if stack_bb <= 4.0:
            rango = rango | _expandir_rango_tokens(["A6o-A2o", "KTo-K8o", "Q9s", "T9s"])
        en_rango = mano_code in rango
        accion = "CALL" if en_rango else "FOLD"
        return {
            "en_regimen_push_fold": True,
            "accion": accion,
            "rango_nash": "Nash Call vs Shove",
            "razon": f"Nash {stack_bb:.1f}BB ({pos} vs shove): {mano_code} -> {accion}",
        }

    # Open shove (BTN / SB / HU)
    if stack_bb <= 4.0:
        rango = _NASH_SHOVE_SB_10BB | _expandir_rango_tokens(["Q5o-Q2o", "J7o-J6o", "96s", "85s", "75s", "64s"])
    elif pos == "SB" or num_activos <= 2:
        rango = _NASH_SHOVE_SB_10BB
    else:
        rango = _NASH_SHOVE_BTN_6BB if stack_bb <= 6.0 else _NASH_SHOVE_BTN_10BB

    en_rango = mano_code in rango
    accion = "ALL_IN" if en_rango else "FOLD"
    return {
        "en_regimen_push_fold": True,
        "accion": accion,
        "rango_nash": f"Nash Open-Shove {pos}",
        "razon": f"Nash {stack_bb:.1f}BB ({pos} open): {mano_code} -> {accion}",
    }


# ---------------------------------------------------------
# EVALUADOR REAL DE MANOS (5 Y 7 CARTAS) + PROYECTOS / OUTS
# ---------------------------------------------------------
def _rank_5_cartas(cinco: Tuple[Tuple[str, str], ...]) -> Tuple[int, ...]:
    """Evalúa exactamente 5 cartas y devuelve tupla comparable (categoria, tiebreakers...)."""
    vals = sorted((VALORES_RANGO[r] for r, _ in cinco), reverse=True)
    suits = [s for _, s in cinco]
    is_flush = bool(suits[0]) and len(set(suits)) == 1

    # Detección de escalera (incluyendo rueda A-2-3-4-5)
    unique_vals = sorted(set(vals), reverse=True)
    is_straight = False
    straight_high = 0
    if len(unique_vals) == 5:
        if unique_vals[0] - unique_vals[4] == 4:
            is_straight = True
            straight_high = unique_vals[0]
        elif unique_vals == [14, 5, 4, 3, 2]:
            is_straight = True
            straight_high = 5

    counts: Dict[int, int] = {}
    for v in vals:
        counts[v] = counts.get(v, 0) + 1
    grupos = sorted(counts.items(), key=lambda x: (x[1], x[0]), reverse=True)
    freq = [g[1] for g in grupos]
    orden_vals = [g[0] for g in grupos]

    if is_straight and is_flush:
        if straight_high == 14:
            return (9, 14)  # Escalera Real (Royal Flush: 10, J, Q, K, A del mismo palo)
        return (8, straight_high)  # Escalera de Color (Straight Flush)
    if freq[0] == 4:
        return (7, orden_vals[0], orden_vals[1])  # Póker (Four of a Kind)
    if freq[0] == 3 and freq[1] == 2:
        return (6, orden_vals[0], orden_vals[1])  # Full House (Full)
    if is_flush:
        return (5, *vals)  # Color (Flush)
    if is_straight:
        return (4, straight_high)  # Escalera (Straight)
    if freq[0] == 3:
        return (3, orden_vals[0], orden_vals[1], orden_vals[2])  # Trío (Three of a Kind)
    if freq[0] == 2 and freq[1] == 2:
        return (2, orden_vals[0], orden_vals[1], orden_vals[2])  # Doble pareja (Two Pair)
    if freq[0] == 2:
        return (1, orden_vals[0], orden_vals[1], orden_vals[2], orden_vals[3])  # Pareja (One Pair)
    return (0, *vals)  # Carta alta (High Card)


def evaluar_fuerza_5_cartas(cartas: List[Tuple[str, str]]) -> Tuple[int, ...]:
    """Dada una lista de >= 5 cartas, devuelve la tupla máxima de 5 cartas."""
    if len(cartas) < 5:
        vals = sorted((VALORES_RANGO.get(r, 0) for r, _ in cartas), reverse=True)
        return (0, *vals)
    if len(cartas) == 5:
        return _rank_5_cartas(tuple(cartas))
    return max(_rank_5_cartas(comb) for comb in combinations(cartas, 5))


def evaluar_mejor_mano_7_cartas(
    mis_cartas: List[Tuple[str, str]],
    comunitarias: List[Tuple[str, str]],
) -> Tuple[int, ...]:
    return evaluar_fuerza_5_cartas(mis_cartas + comunitarias)


def _detectar_proyectos_y_outs(
    mis_cartas: List[Tuple[str, str]],
    comunitarias: List[Tuple[str, str]],
    rank_actual: int,
) -> Dict[str, Any]:
    """Detecta proyecto de color, OESD, gutshot y combo draws con outs sin doble conteo."""
    if len(comunitarias) not in (3, 4) or len(mis_cartas) < 2:
        return {
            "proyecto_color": False,
            "proyecto_oesd": False,
            "proyecto_gutshot": False,
            "es_combo_draw": False,
            "outs_totales": 0,
            "proyectos": [],
        }

    conocidas = set(mis_cartas + comunitarias)
    palos_mano = {s for _, s in mis_cartas if s}
    todas = mis_cartas + comunitarias
    palos_todas = [s for _, s in todas if s]

    proyecto_color = False
    flush_suit = ""
    if rank_actual < 5:
        for s in palos_mano:
            if palos_todas.count(s) == 4:
                proyecto_color = True
                flush_suit = s
                break

    outs_flush: Set[Tuple[str, str]] = set()
    if proyecto_color and flush_suit:
        for r in RANKS_DESC:
            c = (r, flush_suit)
            if c not in conocidas:
                outs_flush.add(c)

    # Detección exacta de outs de escalera (que involucren al menos 1 carta propia)
    outs_straight_ranks: Set[str] = set()
    outs_straight_cards: Set[Tuple[str, str]] = set()
    if rank_actual < 4:
        vals_mesa = {VALORES_RANGO[r] for r, _ in comunitarias}
        vals_mano = {VALORES_RANGO[r] for r, _ in mis_cartas}
        vals_todas = vals_mesa | vals_mano
        if 14 in vals_todas:
            vals_todas_wheel = vals_todas | {1}
        else:
            vals_todas_wheel = vals_todas

        # Ventanas posibles de escalera (1..5 hasta 10..14)
        for low in range(1, 11):
            win = set(range(low, low + 5))
            faltan = win - vals_todas_wheel
            if len(faltan) == 1:
                v_falta = next(iter(faltan))
                v_real = 14 if v_falta == 1 else v_falta
                # Verificar que nuestra mano aporta al menos 1 carta que no está duplicada únicamente en mesa
                win_real = {(14 if x == 1 else x) for x in win}
                if (vals_mano & win_real) - vals_mesa or any(v in vals_mano for v in win_real):
                    r_falta = VALORES_INV[v_real]
                    outs_straight_ranks.add(r_falta)
                    for s in SUITS_ALL:
                        c = (r_falta, s)
                        if c not in conocidas:
                            outs_straight_cards.add(c)

    proyecto_oesd = len(outs_straight_ranks) >= 2
    proyecto_gutshot = len(outs_straight_ranks) == 1
    es_combo_draw = proyecto_color and (proyecto_oesd or proyecto_gutshot)
    outs_union = outs_flush | outs_straight_cards

    proyectos: List[str] = []
    if proyecto_color:
        proyectos.append("Proyecto de Color (9 outs)")
    if proyecto_oesd:
        proyectos.append("OESD (8 outs)")
    elif proyecto_gutshot:
        proyectos.append("Gutshot (4 outs)")
    if es_combo_draw:
        proyectos.append(f"Combo Draw ({len(outs_union)} outs)")

    return {
        "proyecto_color": proyecto_color,
        "proyecto_oesd": proyecto_oesd,
        "proyecto_gutshot": proyecto_gutshot,
        "es_combo_draw": es_combo_draw,
        "outs_totales": len(outs_union),
        "proyectos": proyectos,
    }


def evaluar_combinacion_mano_y_mesa(mis_cartas_str: str, comunitarias_str: str) -> Dict[str, Any]:
    """
    Evalúa la combinación real mano+mesa (hasta 7 cartas):
    - Escalera de color / Póker / Full House / Color / Escalera (incl. rueda) / Trío (Set vs Trips) / Doble Pareja / Pareja / Carta Alta.
    - Distingue "mano hecha con mis cartas" de "mano que juega la mesa" (`mano_en_mesa`).
    - Detecta proyectos (color, OESD, gutshot, combo draw) y outs totales sin doble conteo.
    """
    mis_cartas = parsear_lista_cartas(mis_cartas_str)
    comunitarias = parsear_lista_cartas(comunitarias_str)

    if not mis_cartas:
        return {
            "categoria": "SIN_CARTAS",
            "descripcion": "Sin cartas en mano",
            "rango_categoria": -1,
            "score_tuple": (),
            "es_pareja_en_mano": False,
            "es_color": False,
            "es_escalera": False,
            "pareja_en_mesa": False,
            "coincidencias_mesa": [],
            "mano_en_mesa": False,
            "usa_mis_cartas": False,
            "es_top_pair": False,
            "es_overpair": False,
            "es_set": False,
            "es_trips": False,
            "es_doble_pareja": False,
            "es_monstruo": False,
            "proyecto_color": False,
            "proyecto_oesd": False,
            "proyecto_gutshot": False,
            "es_combo_draw": False,
            "outs_totales": 0,
            "proyectos": [],
        }

    ranks_mano = [r for r, _ in mis_cartas]
    ranks_mesa = [r for r, _ in comunitarias]
    es_pareja_en_mano = len(ranks_mano) >= 2 and ranks_mano[0] == ranks_mano[1]
    pareja_en_mesa = any(ranks_mesa.count(r) >= 2 for r in set(ranks_mesa))
    coincidencias = [r for r in ranks_mano if r in ranks_mesa]

    score_tuple: Tuple[int, ...] = ()
    rank_cat = 0
    mano_en_mesa = False
    usa_mis_cartas = True

    if len(mis_cartas) + len(comunitarias) >= 5:
        score_tuple = evaluar_fuerza_5_cartas(mis_cartas + comunitarias)
        rank_cat = score_tuple[0]
        if len(comunitarias) == 5:
            board_tuple = evaluar_fuerza_5_cartas(comunitarias)
            if score_tuple == board_tuple:
                mano_en_mesa = True
                usa_mis_cartas = False
    else:
        # Preflop (<5 cartas)
        if es_pareja_en_mano:
            rank_cat = 1
            score_tuple = (1, VALORES_RANGO.get(ranks_mano[0], 0))
        else:
            vals = sorted((VALORES_RANGO.get(r, 0) for r in ranks_mano), reverse=True)
            rank_cat = 0
            score_tuple = (0, *vals)

    es_color = (rank_cat == 5 or rank_cat in (8, 9)) and not mano_en_mesa
    es_escalera = (rank_cat == 4 or rank_cat in (8, 9)) and not mano_en_mesa
    es_set = False
    es_trips = False
    es_doble_pareja = (rank_cat == 2) and not mano_en_mesa
    es_top_pair = False
    es_overpair = False
    es_monstruo = (rank_cat >= 3) and not mano_en_mesa

    max_mesa = max((VALORES_RANGO.get(r, 0) for r in ranks_mesa), default=0)

    if mano_en_mesa:
        nombres_cat = {
            9: "ESCALERA REAL EN MESA",
            8: "ESCALERA DE COLOR EN MESA",
            7: "PÓKER EN MESA",
            6: "FULL HOUSE EN MESA",
            5: "COLOR EN MESA",
            4: "ESCALERA EN MESA",
            3: "TRÍO EN MESA",
            2: "DOBLE PAREJA EN MESA",
            1: "PAREJA COMPARTIDA EN MESA (Board Pair)",
            0: "CARTA ALTA EN MESA",
        }
        categoria = nombres_cat.get(rank_cat, "MANO EN MESA")
        descripcion = "La mejor mano de 5 cartas juega exclusivamente en la mesa comunitaria."
    elif rank_cat == 9:
        categoria = "ESCALERA REAL (Royal Flush) - MEJOR MANO POSIBLE"
        descripcion = "Escalera Real (10, J, Q, K, A del mismo palo) completada. Mano imbatible."
    elif rank_cat == 8:
        categoria = "ESCALERA DE COLOR (Straight Flush) - MONSTRUO"
        descripcion = "Escalera de color completada usando cartas privadas."
    elif rank_cat == 7:
        categoria = "PÓKER (Four of a Kind) - MONSTRUO"
        descripcion = "Póker completado. Mano prácticamente invencible."
    elif rank_cat == 6:
        categoria = "FULL HOUSE - MONSTRUO"
        descripcion = "Full House completado combinando mano y mesa."
    elif rank_cat == 5:
        categoria = "COLOR (Flush) - MONSTRUO"
        descripcion = "Color completado usando cartas privadas. Valor máximo."
    elif rank_cat == 4:
        categoria = "ESCALERA (Straight) - MONSTRUO"
        descripcion = "Escalera completada conectando mano y mesa. Valor máximo."
    elif rank_cat == 3:
        if es_pareja_en_mano and coincidencias:
            es_set = True
            categoria = "TRÍO EN SET (Mano Monstruo Oculta)"
            descripcion = f"Set oculto de {ranks_mano[0]}s en mano emparejado con la mesa."
        else:
            es_trips = True
            categoria = "TRÍO CONECTADO (Trips)"
            descripcion = f"Trío usando carta en mano y pareja de la mesa."
    elif rank_cat == 2:
        if es_pareja_en_mano and pareja_en_mesa:
            categoria = "DOBLE PAREJA (Pocket Pair + Mesa)"
            descripcion = f"Pocket pair de {ranks_mano[0]}s combinada con pareja en mesa."
        elif len(set(coincidencias)) >= 2:
            categoria = "DOBLE PAREJA PRIVADA (Two Pair)"
            descripcion = f"Ambas cartas en mano conectan con la mesa ({', '.join(sorted(set(coincidencias)))})."
        else:
            categoria = "DOBLE PAREJA (Con pareja en mesa)"
            descripcion = "Pareja privada combinada con pareja en la mesa."
    elif rank_cat == 1:
        if es_pareja_en_mano:
            val_pareja = VALORES_RANGO.get(ranks_mano[0], 0)
            es_overpair = bool(ranks_mesa and val_pareja > max_mesa)
            subtipo = "Overpair" if es_overpair else "Pocket Pair"
            categoria = f"PAREJA EN MANO ({subtipo})"
            descripcion = f"Pareja de {ranks_mano[0]}s en mano privada ({subtipo})."
        elif coincidencias:
            val_hit = max(VALORES_RANGO.get(r, 0) for r in coincidencias)
            es_top_pair = (val_hit >= max_mesa)
            subtipo = "Top Pair" if es_top_pair else "Middle/Bottom Pair"
            categoria = f"PAREJA CONECTADA CON MESA ({subtipo})"
            descripcion = f"Mano privada conectada con la mesa en {coincidencias[0]} ({subtipo})."
        else:
            usa_mis_cartas = False
            categoria = "PAREJA COMPARTIDA EN MESA (Board Pair)"
            descripcion = "Pareja compartida en la mesa pública. Se compite por Kicker alto en mano."
    else:
        alta = max(ranks_mano, key=lambda r: VALORES_RANGO.get(r, 0))
        categoria = "CARTA ALTA PRIVADA"
        descripcion = f"Carta alta en mano privada: {alta}."

    draws = _detectar_proyectos_y_outs(mis_cartas, comunitarias, rank_cat)

    return {
        "categoria": categoria,
        "descripcion": descripcion,
        "rango_categoria": rank_cat,
        "score_tuple": score_tuple,
        "es_pareja_en_mano": es_pareja_en_mano,
        "es_color": es_color,
        "es_escalera": es_escalera,
        "pareja_en_mesa": pareja_en_mesa,
        "coincidencias_mesa": coincidencias,
        "mano_en_mesa": mano_en_mesa,
        "usa_mis_cartas": usa_mis_cartas,
        "es_top_pair": es_top_pair,
        "es_overpair": es_overpair,
        "es_set": es_set,
        "es_trips": es_trips,
        "es_doble_pareja": es_doble_pareja,
        "es_monstruo": es_monstruo,
        "es_pareja_propia_o_conectada": bool(es_pareja_en_mano or coincidencias or (rank_cat >= 2 and usa_mis_cartas)),
        **draws,
    }


def _limpiar_num(s: Any) -> float:
    if isinstance(s, (int, float)):
        return float(s)
    texto = str(s or "0").strip()
    if not texto:
        return 0.0
    tok = texto.split("|")[0].strip()
    m = re.search(r"\$?\s*(\d+(?:[.,]\d+)?)", tok)
    if not m:
        return 0.0
    num_str = m.group(1)
    if "," in num_str and "." in num_str:
        if num_str.rfind(",") > num_str.rfind("."):
            num_str = num_str.replace(".", "").replace(",", ".")
        else:
            num_str = num_str.replace(",", "")
    elif "," in num_str:
        parts = num_str.split(",")
        if len(parts[-1]) in (1, 2):
            num_str = num_str.replace(",", ".")
        else:
            num_str = num_str.replace(",", "")
    try:
        val = float(num_str)
        return val if val >= 0 else 0.0
    except Exception:
        return 0.0


def calcular_pot_odds_y_sizing(
    bote_str: Any,
    a_igualar_str: Any,
    mi_stack_str: Any,
    big_blind: Optional[float] = None,
    stack_rival_str: Any = None,
) -> Dict[str, Any]:
    """Calcula pot odds, stack efectivo en BB y sizings proporcionales al bote en BB/fichas (soporta floats y micro-apuestas)."""
    bote = _limpiar_num(bote_str)
    a_igualar = _limpiar_num(a_igualar_str)
    mi_stack = _limpiar_num(mi_stack_str)
    stack_rival = _limpiar_num(stack_rival_str) if stack_rival_str is not None else 0.0

    bb = float(big_blind or getattr(config, "BIG_BLIND_DEFAULT", 20.0) or 20.0)
    # Auto-detección para partidas de micro-límites / dinero real (ej. €0.02 / €0.05)
    if (bb == 20.0 and bote > 0 and bote < 5.0) or bb <= 0:
        bb = 0.02 if (bote > 0 and bote <= 0.50) else (0.10 if bote <= 2.0 else 0.50)

    pot_odds_pct = 0.0
    if a_igualar > 0 and (bote + a_igualar) > 0:
        pot_odds_pct = round((a_igualar / (bote + a_igualar)) * 100.0, 1)

    bote_bb = round(bote / bb, 2)
    a_igualar_bb = round(a_igualar / bb, 2)
    mi_stack_bb = round(mi_stack / bb, 2)
    if stack_rival > 0 and mi_stack > 0:
        stack_efectivo_bb = round(min(mi_stack, stack_rival) / bb, 2)
    else:
        stack_efectivo_bb = mi_stack_bb

    pcts = getattr(config, "SIZING_CBET_PCTS", (0.33, 0.66, 1.0))
    bote_ref = max(bote, bb)
    es_micro_float = (bote > 0 and bote < 50.0) or (mi_stack > 0 and mi_stack < 50.0) or isinstance(bote_str, float) or isinstance(a_igualar_str, float)
    if es_micro_float:
        cbet_33 = round(max(bb, bote_ref * pcts[0]), 2)
        cbet_66 = round(max(bb, bote_ref * pcts[1]), 2)
        cbet_pot = round(max(bb, bote_ref * pcts[2]), 2)
        bote_out = round(bote, 2)
        a_igualar_out = round(a_igualar, 2)
        mi_stack_out = round(mi_stack, 2)
    else:
        cbet_33 = max(int(round(bb)), int(round(bote_ref * pcts[0])))
        cbet_66 = max(int(round(bb)), int(round(bote_ref * pcts[1])))
        cbet_pot = max(int(round(bb)), int(round(bote_ref * pcts[2])))
        bote_out = int(round(bote))
        a_igualar_out = int(round(a_igualar))
        mi_stack_out = int(round(mi_stack))

    return {
        "big_blind": bb,
        "bote": bote_out,
        "bote_bb": bote_bb,
        "a_igualar": a_igualar_out,
        "a_igualar_bb": a_igualar_bb,
        "pot_odds_pct": pot_odds_pct,
        "mi_stack": mi_stack_out,
        "mi_stack_bb": mi_stack_bb,
        "stack_efectivo_bb": stack_efectivo_bb,
        "sizing_cbet_pequeno": cbet_33,
        "sizing_cbet_estandar": cbet_66,
        "sizing_cbet_fuerte": cbet_pot,
    }


def analizar_textura_mesa_y_blockers(mis_cartas_str: str, comunitarias_str: str) -> Dict[str, Any]:
    """Analiza textura del tablero (conectada/seca/emparejada), blockers y estimación basada en rango."""
    cartas_mano = parsear_lista_cartas(mis_cartas_str)
    cartas_mesa = parsear_lista_cartas(comunitarias_str)
    cartas_visibles = cartas_mano + cartas_mesa

    blockers_ases = sum(1 for r, _ in cartas_visibles if r == "A")
    blockers_reyes = sum(1 for r, _ in cartas_visibles if r == "K")

    textura = "PREFLOP"
    posible_color = False
    posible_escalera = False
    tablero_emparejado = False
    carta_alta_mesa = ""
    estimacion_rango = "Preflop: rango dependiente de posición y stack efectivo."

    if len(cartas_mesa) >= 3:
        palos = [s for _, s in cartas_mesa if s]
        palo_counts = {p: palos.count(p) for p in set(palos)}
        if any(cnt >= 3 for cnt in palo_counts.values()):
            posible_color = True

        ranks_mesa = [r for r, _ in cartas_mesa]
        tablero_emparejado = any(ranks_mesa.count(r) >= 2 for r in set(ranks_mesa))
        carta_alta_mesa = max(ranks_mesa, key=lambda r: VALORES_RANGO.get(r, 0))

        valores_unicos = sorted(set(VALORES_RANGO.get(r, 0) for r, _ in cartas_mesa if r in VALORES_RANGO))
        if 14 in valores_unicos:
            valores_unicos = sorted(set([1] + valores_unicos))

        for i in range(len(valores_unicos) - 2):
            if valores_unicos[i + 2] - valores_unicos[i] <= 4:
                posible_escalera = True
                break

        if posible_color or posible_escalera:
            textura = "MESA CONECTADA / PELIGROSA (Posible Color o Escalera)"
            estimacion_rango = (
                "Textura coordinada: el rango rival contiene más combos de proyectos, escaleras, "
                "colores, dobles parejas y sets; proteger manos fuertes y controlar botes medios."
            )
        elif tablero_emparejado:
            textura = "MESA EMPAREJADA (Posibles Trips / Full House)"
            estimacion_rango = (
                "Tablero emparejado: combos reducidos de trips/full house; alta proporción de aire/parejas medias."
            )
        else:
            textura = "MESA SECA / DESCONECTADA"
            estimacion_rango = (
                "Textura seca: menos proyectos directos; el rango rival de valor se concentra en "
                "sets, dobles parejas y top pairs junto con faroles de backdoor o aire."
            )

    return {
        "textura_mesa": textura,
        "posible_color": posible_color,
        "posible_escalera": posible_escalera,
        "tablero_emparejado": tablero_emparejado,
        "carta_alta_mesa": carta_alta_mesa,
        "blockers_ases": blockers_ases,
        "blockers_reyes": blockers_reyes,
        "estimacion_rango": estimacion_rango,
    }


# ---------------------------------------------------------
# EQUIDAD MONTE CARLO / EXACTA Y DECISOR POR EV
# ---------------------------------------------------------
def _generar_rango_rival_combos(
    opp_archetype: str,
    cartas_muertas: Set[Tuple[str, str]],
) -> List[Tuple[Tuple[str, str], Tuple[str, str]]]:
    """Construye combos legales del rival filtrados por arquetipo."""
    arq = (opp_archetype or "Desconocido").upper()
    if "NIT" in arq:
        tiers_permitidos = {"S", "A"}
    elif "TAG" in arq:
        tiers_permitidos = {"S", "A", "B"}
    elif "LAG" in arq or "MANIAC" in arq or "CALLING" in arq:
        tiers_permitidos = {"S", "A", "B", "C"}
    else:
        tiers_permitidos = {"S", "A", "B", "C"}

    mazo = [(r, s) for r in RANKS_DESC for s in SUITS_ALL if (r, s) not in cartas_muertas]
    combos_filtrados: List[Tuple[Tuple[str, str], Tuple[str, str]]] = []
    for c1, c2 in combinations(mazo, 2):
        code, *_ = _codigo_mano_preflop([c1, c2])
        if _TABLA_PREFLOP_TIER.get(code, "BASURA") in tiers_permitidos:
            combos_filtrados.append((c1, c2))
    return combos_filtrados or list(combinations(mazo, 2))


def estimar_equidad_vs_rango(
    mis_cartas_str: str,
    comunitarias_str: str = "",
    opp_archetype: str = "Desconocido",
    iteraciones: Optional[int] = None,
    seed: Optional[int] = None,
    num_activos: int = 2,
) -> float:
    """
    Estima la equidad [0.0, 1.0] de Hero contra el rango ponderado del rival (o múltiples rivales en botes multiway).
    Usa enumeración exacta en river (5 comunitarias) o Monte Carlo con Random(seed).
    """
    mis_cartas = parsear_lista_cartas(mis_cartas_str)
    if len(mis_cartas) < 2:
        return 0.5
    comunitarias = parsear_lista_cartas(comunitarias_str)
    muertas = set(mis_cartas + comunitarias)
    combos_rival = _generar_rango_rival_combos(opp_archetype, muertas)
    if not combos_rival:
        return 0.5

    rng = random.Random(seed)
    n_iter = int(iteraciones or getattr(config, "MONTE_CARLO_ITERATIONS", 250))
    n_villanos = max(1, min(4, int(num_activos or 2) - 1))

    # River: enumeración exacta o simulación Monte Carlo
    if len(comunitarias) == 5:
        hero_rank = evaluar_fuerza_5_cartas(mis_cartas + comunitarias)
        puntos = 0.0
        validas = 0
        for _ in range(n_iter):
            if len(combos_rival) < n_villanos:
                break
            v_combos = rng.sample(combos_rival, n_villanos)
            cards_v = set()
            conflict = False
            for v1, v2 in v_combos:
                if v1 in cards_v or v2 in cards_v:
                    conflict = True
                    break
                cards_v.add(v1)
                cards_v.add(v2)
            if conflict:
                continue

            vill_ranks = [evaluar_fuerza_5_cartas([v1, v2] + comunitarias) for v1, v2 in v_combos]
            max_vill_rank = max(vill_ranks)
            if hero_rank > max_vill_rank:
                puntos += 1.0
            elif hero_rank == max_vill_rank:
                count_ties = vill_ranks.count(hero_rank)
                puntos += 1.0 / (count_ties + 1)
            validas += 1

        return round(puntos / validas, 4) if validas > 0 else 0.5

    # Preflop / Flop / Turn: Monte Carlo con semilla local
    mazo_base = [(r, s) for r in RANKS_DESC for s in SUITS_ALL if (r, s) not in muertas]
    faltantes = 5 - len(comunitarias)
    puntos = 0.0
    validas = 0
    for _ in range(n_iter):
        if len(combos_rival) < n_villanos:
            break
        v_combos = rng.sample(combos_rival, n_villanos)
        cards_v = set()
        conflict = False
        for v1, v2 in v_combos:
            if v1 in cards_v or v2 in cards_v:
                conflict = True
                break
            cards_v.add(v1)
            cards_v.add(v2)
        if conflict:
            continue

        restantes = [c for c in mazo_base if c not in cards_v]
        if len(restantes) < faltantes:
            continue
        runout = rng.sample(restantes, faltantes)
        board = comunitarias + runout
        hero_rank = evaluar_fuerza_5_cartas(mis_cartas + board)
        vill_ranks = [evaluar_fuerza_5_cartas([v1, v2] + board) for v1, v2 in v_combos]
        max_vill_rank = max(vill_ranks)
        if hero_rank > max_vill_rank:
            puntos += 1.0
        elif hero_rank == max_vill_rank:
            count_ties = vill_ranks.count(hero_rank)
            puntos += 1.0 / (count_ties + 1)
        validas += 1

    return round(puntos / validas, 4) if validas > 0 else 0.5


def decidir_por_ev(dom_data: dict, seed: Optional[int] = None) -> Dict[str, Any]:
    """
    Elige la acción legal de mayor EV (en BB) combinando equidad vs rango, pot odds,
    fold equity dinámica por HUD de oponente y frecuencias mixtas controladas por semilla.
    """
    dom = dom_data or {}
    botones = str(dom.get("botones_visibles") or dom.get("opciones_disponibles") or "").upper()
    gto_math = dom.get("gto_math") or calcular_pot_odds_y_sizing(
        dom.get("bote", 0),
        dom.get("a_igualar", 0),
        dom.get("mi_stack", 0),
        big_blind=dom.get("big_blind"),
        stack_rival_str=dom.get("stack_rival"),
    )
    bb = float(gto_math.get("big_blind") or getattr(config, "BIG_BLIND_DEFAULT", 20.0))
    bote_bb = float(gto_math.get("bote_bb") or 0.0)
    a_igualar_bb = float(gto_math.get("a_igualar_bb") or 0.0)
    stack_bb = float(gto_math.get("stack_efectivo_bb") or gto_math.get("mi_stack_bb") or 20.0)

    perfil = dom.get("perfil_rival") or {}
    opp_arq = str(perfil.get("estilo", "Desconocido"))
    num_activos = len(dom.get("perfiles_oponentes", [])) or (2 if opp_arq else 1)

    equidad = estimar_equidad_vs_rango(
        dom.get("mis_cartas", ""),
        dom.get("cartas_comunitarias", ""),
        opp_archetype=opp_arq,
        seed=seed,
        num_activos=num_activos,
    )

    evs: Dict[str, float] = {}
    if "FOLD" in botones:
        evs["FOLD"] = 0.0
    if "CHECK" in botones and a_igualar_bb <= 0:
        evs["CHECK"] = round(equidad * bote_bb, 3)
    if "CALL" in botones:
        ev_call = equidad * (bote_bb + a_igualar_bb) - (1.0 - equidad) * a_igualar_bb
        evs["CALL"] = round(ev_call, 3)

    # Fold equity ponderada según arquetipo y estadísticas reales del HUD (fold_to_cbet / fold_to_3bet)
    arq_u = opp_arq.upper()
    fe_base = 0.42 if "NIT" in arq_u else (0.15 if "CALLING" in arq_u else 0.30)
    hud_fold_cbet = float(perfil.get("fold_to_cbet_pct") or 0.0)
    cbet_opps = int(perfil.get("fold_to_cbet_opportunities") or 0)
    if cbet_opps >= 3 and hud_fold_cbet > 0:
        w_hud = min(1.0, cbet_opps / 10.0)
        fe_base = round(w_hud * (hud_fold_cbet / 100.0) + (1.0 - w_hud) * fe_base, 3)

    # Reducir fold equity si el rival ya realizó una apuesta/subida fuerte en la calle actual
    if a_igualar_bb > bote_bb * 1.2:
        fe_base *= 0.35
    elif a_igualar_bb > bote_bb * 0.6:
        fe_base *= 0.60

    # Sizing dinámico adaptado a la textura del tablero
    textura_info = dom.get("gto_textura") or analizar_textura_mesa_y_blockers(
        dom.get("mis_cartas", ""), dom.get("cartas_comunitarias", "")
    )
    if textura_info.get("posible_color") or textura_info.get("posible_escalera"):
        sizing_key = "sizing_cbet_fuerte"
    elif textura_info.get("tablero_emparejado") or "SECA" in str(textura_info.get("textura_mesa", "")).upper():
        sizing_key = "sizing_cbet_pequeno"
    else:
        sizing_key = "sizing_cbet_estandar"

    sizing_chips = int(gto_math.get(sizing_key) or gto_math.get("sizing_cbet_estandar") or round(bb * 2.0))

    if "RAISE" in botones or "BET" in botones:
        bet_bb = min(stack_bb, sizing_chips / bb) if stack_bb > 0 else sizing_chips / bb
        ev_raise = fe_base * bote_bb + (1.0 - fe_base) * (
            equidad * (bote_bb + bet_bb + a_igualar_bb) - (1.0 - equidad) * (bet_bb + a_igualar_bb)
        )
        evs["RAISE"] = round(ev_raise, 3)
    if "ALL_IN" in botones or "ALLIN" in botones:
        fe_shove = min(0.60, fe_base + 0.12)
        ev_allin = fe_shove * bote_bb + (1.0 - fe_shove) * (
            equidad * (bote_bb + stack_bb) - (1.0 - equidad) * stack_bb
        )
        evs["ALL_IN"] = round(ev_allin, 3)

    # Control de bote: si CHECK es gratis y la mano no es pareja propia ni conectada con la mesa,
    # priorizar CHECK sobre RAISE para evitar convertir cartas altas en faroles ineficientes.
    gto_comb = dom.get("gto_combinacion") or evaluar_combinacion_mano_y_mesa(
        dom.get("mis_cartas", ""), dom.get("cartas_comunitarias", "")
    )
    es_pareja_o_mejor = bool(
        gto_comb.get("es_pareja_en_mano")
        or gto_comb.get("es_top_pair")
        or gto_comb.get("es_overpair")
        or (gto_comb.get("coincidencias_mesa") and len(gto_comb.get("coincidencias_mesa")) > 0)
        or (gto_comb.get("rango_categoria", 0) >= 2 and gto_comb.get("usa_mis_cartas", True))
        or gto_comb.get("es_pareja_propia_o_conectada")
    )
    tiene_proyecto = bool(gto_comb.get("proyectos"))

    if "CHECK" in evs and "RAISE" in evs and a_igualar_bb <= 0:
        if 0.30 <= equidad < 0.62 and not es_pareja_o_mejor and not tiene_proyecto:
            evs["RAISE"] = round(evs["CHECK"] - 0.10, 3)

    if not evs:
        return {"accion": "WAIT", "monto": 0, "ev_bb": 0.0, "equidad": equidad, "evs": {}, "razon": "Sin acciones legales"}

    mejor_accion = max(evs, key=lambda k: evs[k])
    # Si la mejor acción alternativa activa tiene EV negativo, elegir FOLD (EV = 0.0)
    if "FOLD" in evs and evs[mejor_accion] < 0.0:
        mejor_accion = "FOLD"
    # Nunca foldear si CHECK es gratis
    if mejor_accion == "FOLD" and "CHECK" in evs:
        mejor_accion = "CHECK"

    frec = generar_frecuencia_mixta_gto(seed=seed)
    monto_out = sizing_chips if mejor_accion == "RAISE" else 0
    return {
        "accion": mejor_accion,
        "monto": monto_out,
        "ev_bb": evs[mejor_accion],
        "equidad": equidad,
        "evs": evs,
        "frecuencia_d100": frec["d100"],
        "razon": f"EV({mejor_accion})={evs[mejor_accion]:+.2f}BB | Eq={equidad*100:.1f}%",
    }
