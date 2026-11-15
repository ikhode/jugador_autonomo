"""
Motor de Análisis GTO, Rangos de Manos, Combinatoria Mano vs Mesa, Blockers y Frecuencias.
"""
import re
import random
from typing import Dict, Any, Tuple, List

RANGOS_PREFLOP = {
    "S": ["AA", "KK", "QQ", "JJ", "AKs", "AKo"],
    "A": ["TT", "99", "88", "AQs", "AQo", "AJs", "AJo", "KQs", "KQo"],
    "B": ["77", "66", "55", "44", "33", "22", "ATs", "ATo", "A9s-A2s", "KJs", "KJo", "KTs", "QJs", "JTs"],
    "C": ["K9s", "QTs", "Q9s", "J9s", "T9s", "98s", "87s", "76s", "65s", "K9o", "QKo"]
}

VALORES_RANGO = {
    'A': 14, 'K': 13, 'Q': 12, 'J': 11, '10': 10, 'T': 10,
    '9': 9, '8': 8, '7': 7, '6': 6, '5': 5, '4': 4, '3': 3, '2': 2
}

MAPA_PALOS = {
    'S': 'S', 'H': 'H', 'D': 'D', 'C': 'C',
    '♠': 'S', '♥': 'H', '♦': 'D', '♣': 'C'
}


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

    # Caso 1: Empieza con el palo (ej. 'S10', 'HA', '♠A')
    if c[0] in MAPA_PALOS and len(c) > 1 and c[1:] in VALORES_RANGO:
        palo = MAPA_PALOS[c[0]]
        resto = c[1:]
    # Caso 2: Termina con el palo (ej. '10S', '10♠', 'AH', 'A♥')
    elif c[-1] in MAPA_PALOS and len(c) > 1 and c[:-1] in VALORES_RANGO:
        palo = MAPA_PALOS[c[-1]]
        resto = c[:-1]

    rango = "T" if resto == "10" else (resto if resto in VALORES_RANGO else "")
    return rango, palo


def parsear_lista_cartas(cartas_str: str) -> List[Tuple[str, str]]:
    if not cartas_str:
        return []
    tokens = str(cartas_str).upper().split()
    resultado = []
    for t in tokens:
        r, s = parsear_carta(t)
        if r:
            resultado.append((r, s))
    return resultado


def generar_frecuencia_mixta_gto() -> Dict[str, Any]:
    d100 = random.randint(1, 100)
    if d100 <= 70:
        linea = "LÍNEA ESTÁNDAR DE VALOR AGRESIVO"
    elif d100 <= 90:
        linea = "LÍNEA DE TRAMPA / SLOWPLAY / CONTROL DE BOTE"
    else:
        linea = "LÍNEA DE SEMI-FAROL / PRESION DE BOTE"

    return {
        "d100": d100,
        "linea_sugerida": linea,
        "instruccion": f"Semilla Aleatoria GTO ({d100}/100): Usar {linea} para varianza anti-explotación."
    }


def clasificar_mano_preflop(mis_cartas_str: str) -> Dict[str, Any]:
    cartas = parsear_lista_cartas(mis_cartas_str)
    if len(cartas) < 2:
        return {"tier": "DESCONOCIDO", "score": 0, "mano_code": "", "descripcion": "Cartas no detectadas aún"}

    (r1, s1), (r2, s2) = cartas[0], cartas[1]

    es_pareja = (r1 == r2)
    es_suited = (s1 == s2 and s1 != '')

    v1 = VALORES_RANGO.get(r1, 5)
    v2 = VALORES_RANGO.get(r2, 5)

    if v1 < v2:
        r1, r2 = r2, r1
        v1, v2 = v2, v1

    mano_code = f"{r1}{r2}" + ("s" if es_suited and not es_pareja else ("o" if not es_pareja else ""))

    if es_pareja:
        if v1 >= 10:
            tier, score = "S", 95
        elif v1 >= 7:
            tier, score = "A", 85
        else:
            tier, score = "B", 75
    elif v1 == 14:
        if v2 >= 11:
            tier, score = ("S" if v2 == 13 else "A"), (92 if es_suited else 88)
        elif v2 == 10:
            tier, score = ("A" if es_suited else "B"), (84 if es_suited else 76)
        elif es_suited:
            tier, score = "B", 78
        else:
            tier, score = "B", 70
    elif v1 == 13:
        if v2 >= 10:
            tier, score = "A", (82 if es_suited else 78)
        elif es_suited:
            tier, score = "B", 72
        else:
            tier, score = "C", 60
    elif es_suited and (v1 - v2 == 1) and v2 >= 5:
        tier, score = "B", 70
    elif v1 >= 10 and v2 >= 9:
        tier, score = "C", 64
    else:
        tier, score = "BASURA", 30

    return {
        "tier": tier,
        "score": score,
        "mano_code": mano_code,
        "es_pareja": es_pareja,
        "es_suited": es_suited
    }


def evaluar_combinacion_mano_y_mesa(mis_cartas_str: str, comunitarias_str: str) -> Dict[str, Any]:
    """
    Diferencia con precisión matemática si la pareja/trío/doble pareja está en nuestra mano (Pocket Pair / Set)
    o si está en la mesa compartida (Board Pair / Trips).
    """
    mis_cartas = parsear_lista_cartas(mis_cartas_str)
    comunitarias = parsear_lista_cartas(comunitarias_str)

    if not mis_cartas:
        return {
            "categoria": "SIN_CARTAS",
            "descripcion": "Sin cartas en mano",
            "es_pareja_en_mano": False,
            "pareja_en_mesa": False,
            "coincidencias_mesa": []
        }

    ranks_mano = [r for r, _ in mis_cartas]
    ranks_mesa = [r for r, _ in comunitarias]

    es_pareja_en_mano = len(ranks_mano) >= 2 and ranks_mano[0] == ranks_mano[1]
    pareja_en_mesa = any(ranks_mesa.count(r) >= 2 for r in set(ranks_mesa))
    coincidencias = [r for r in ranks_mano if r in ranks_mesa]

    if es_pareja_en_mano and coincidencias:
        categoria = "TRÍO EN SET / FULL (Mano Monstruo Oculta)"
        descripcion = f"Set de {ranks_mano[0]}s en mano emparejado con la mesa. Valor máximo."
    elif not es_pareja_en_mano and len(set(coincidencias)) >= 2:
        categoria = "DOBLE PAREJA PRIVADA (Two Pair)"
        descripcion = f"Ambas cartas en mano conectan con la mesa ({', '.join(set(coincidencias))}). Mano muy fuerte."
    elif not es_pareja_en_mano and len(coincidencias) >= 1 and ranks_mesa.count(coincidencias[0]) >= 2:
        categoria = "TRÍO CONECTADO (Trips)"
        descripcion = f"Trío de {coincidencias[0]}s usando carta en mano y pareja de la mesa."
    elif es_pareja_en_mano:
        max_mesa = max((VALORES_RANGO.get(r, 0) for r in ranks_mesa), default=0)
        val_pareja = VALORES_RANGO.get(ranks_mano[0], 0)
        subtipo = "Overpair" if (ranks_mesa and val_pareja > max_mesa) else "Pocket Pair"
        categoria = f"PAREJA EN MANO ({subtipo})"
        descripcion = f"Pareja de {ranks_mano[0]}s en mano privada ({subtipo})."
    elif coincidencias:
        max_mesa = max((VALORES_RANGO.get(r, 0) for r in ranks_mesa), default=0)
        val_hit = max(VALORES_RANGO.get(r, 0) for r in coincidencias)
        es_top = (val_hit >= max_mesa)
        subtipo = "Top Pair" if es_top else "Middle/Bottom Pair"
        categoria = f"PAREJA CONECTADA CON MESA ({subtipo})"
        descripcion = f"Mano privada conectada con la mesa en {coincidencias[0]} ({subtipo})."
    elif pareja_en_mesa:
        categoria = "PAREJA COMPARTIDA EN MESA (Board Pair)"
        descripcion = "Pareja compartida en la mesa pública. Se compite por Kicker alto en mano."
    else:
        alta = max(ranks_mano, key=lambda r: VALORES_RANGO.get(r, 0)) if ranks_mano else "N/A"
        categoria = "CARTA ALTA PRIVADA"
        descripcion = f"Carta alta en mano privada: {alta}."

    return {
        "categoria": categoria,
        "descripcion": descripcion,
        "es_pareja_en_mano": es_pareja_en_mano,
        "pareja_en_mesa": pareja_en_mesa,
        "coincidencias_mesa": coincidencias
    }


def calcular_pot_odds_y_sizing(bote_str: str, a_igualar_str: str, mi_stack_str: str) -> Dict[str, Any]:
    def limpiar_num(s):
        if isinstance(s, (int, float)):
            return int(s)
        texto = str(s or "0").strip()
        m = re.search(r"\d+(?:[.,]\d+)?", texto.replace(",", ""))
        return int(float(m.group(0))) if m else 0

    bote = limpiar_num(bote_str)
    a_igualar = limpiar_num(a_igualar_str)
    mi_stack = limpiar_num(mi_stack_str)

    pot_odds_pct = 0.0
    if a_igualar > 0 and (bote + a_igualar) > 0:
        pot_odds_pct = round((a_igualar / (bote + a_igualar)) * 100, 1)

    bote_efectivo = max(bote, 30)
    cbet_33 = max(20, int(bote_efectivo * 0.33))
    cbet_66 = max(40, int(bote_efectivo * 0.66))
    cbet_pot = max(60, bote_efectivo)

    return {
        "bote": bote,
        "a_igualar": a_igualar,
        "pot_odds_pct": pot_odds_pct,
        "mi_stack": mi_stack,
        "sizing_cbet_pequeno": cbet_33,
        "sizing_cbet_estandar": cbet_66,
        "sizing_cbet_fuerte": cbet_pot
    }


def analizar_textura_mesa_y_blockers(mis_cartas_str: str, comunitarias_str: str) -> Dict[str, Any]:
    cartas_mano = parsear_lista_cartas(mis_cartas_str)
    cartas_mesa = parsear_lista_cartas(comunitarias_str)
    cartas_visibles = cartas_mano + cartas_mesa

    blockers_ases = sum(1 for r, _ in cartas_visibles if r == 'A')
    blockers_reyes = sum(1 for r, _ in cartas_visibles if r == 'K')

    textura = "PREFLOP"
    posible_color = False
    posible_escalera = False

    if len(cartas_mesa) >= 3:
        palos = [s for _, s in cartas_mesa if s]
        palo_counts = {p: palos.count(p) for p in set(palos)}
        if any(cnt >= 3 for cnt in palo_counts.values()):
            posible_color = True

        valores_unicos = sorted(set(VALORES_RANGO.get(r, 0) for r, _ in cartas_mesa if r in VALORES_RANGO))
        # Si hay un As (14), también puede formar escalera baja (A-2-3-4-5) como valor 1
        if 14 in valores_unicos:
            valores_unicos = sorted(set([1] + valores_unicos))

        # Ventana deslizante de 3 cartas: si existen 3 cartas en mesa dentro de un rango de 4 (ej. 5-6-8), hay posible escalera
        for i in range(len(valores_unicos) - 2):
            if valores_unicos[i + 2] - valores_unicos[i] <= 4:
                posible_escalera = True
                break

        if posible_color or posible_escalera:
            textura = "MESA CONECTADA / PELIGROSA (Posible Color o Escalera)"
        else:
            textura = "MESA SECA / DESCONECTADA (Imposible Color o Escalera directa)"

    alerta_farol = ""
    if textura.startswith("MESA SECA") and len(cartas_mesa) >= 3:
        alerta_farol = "TEXTURA SECA: Es matemáticamente inviable que el rival tenga Color o Escalera. Si sube fuerte, alta probabilidad de FAROL / LÍNEA INCONSISTENTE."

    return {
        "textura_mesa": textura,
        "posible_color": posible_color,
        "posible_escalera": posible_escalera,
        "blockers_ases": blockers_ases,
        "blockers_reyes": blockers_reyes,
        "alerta_farol_inconsistente": alerta_farol
    }
