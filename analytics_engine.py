"""
Motor Avanzado de Procesamiento Estadístico, Evaluación de Calidad Estratégica (EV/GTO)
y Laboratorio Científico de Comparación entre Versiones del Bot de Póker.

Principios de rigor técnico (Fases 1, 2, 3, 5 y 6):
1. Separación estricta entre:
   - Resultados Reales Observados (fichas/BB ganadas, winrate observado, drawdown).
   - Estimaciones Matemáticas de EV (All-in EV, EV por acción, Regret / EV perdido).
   - Evaluación Heurística-GTO vs. Equilibrio de Nash (sin presentar heurísticas como soluciones CFR exactas).
2. Cada indicador HUD y financiero documenta su fórmula, numerador, denominador y tamaño de muestra (n).
3. Cuando falta información de cartas o bote en tiempo real, la decisión se marca como `EVALUACION_NO_DISPONIBLE`.
4. Comparaciones entre versiones con pruebas pareadas/Welch, tamaño del efecto (Cohen's d), corrección de
   Holm-Bonferroni para comparaciones múltiples y clasificación en 3 estados: `MEJORA`, `INCONCLUSO`, `REGRESIÓN`.
"""
import json
import math
import time
from typing import Any, Dict, List, Optional, Tuple

import config
from analytics_db import AnalyticsStorage, DB_PATH
from analytics_diagnostics import (
    construir_reporte_confiabilidad_sistema,
    generar_diagnosticos_inteligentes,
)
from poker_gto_engine import (
    analizar_textura_mesa_y_blockers,
    calcular_pot_odds_y_sizing,
    clasificar_mano_preflop,
    evaluar_combinacion_mano_y_mesa,
    parsear_lista_cartas,
    VALORES_RANGO,
)


# ==============================================================================
# 1. UTILIDADES MATEMÁTICAS Y ESTADÍSTICAS VERIFICABLES
# ==============================================================================

def _normal_cdf(x: float) -> float:
    """Función de distribución acumulada normal estándar Phi(x)."""
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def _t_critical_95(df: float) -> float:
    """
    Valor crítico t_{0.975, df} para Intervalos de Confianza del 95% (dos colas).
    Usa valores exactos tabulados para df pequeños y aproximación de Peiser para df > 5.
    """
    if df <= 0:
        return 1.96
    if df == 1:
        return 12.706
    if df == 2:
        return 4.303
    if df == 3:
        return 3.182
    if df == 4:
        return 2.776
    if df == 5:
        return 2.571
    z = 1.959964
    return z + (z**3 + z) / (4.0 * df) + (5.0 * z**5 + 16.0 * z**3 + 3.0 * z) / (96.0 * (df**2))


def _t_test_p_value_two_sided(t_stat: float, df: float) -> float:
    """
    Calcula el p-valor bilateral para un estadístico t con `df` grados de libertad.
    Usa la transformación normal de Bailey/Wallace para la distribución t de Student.
    """
    if df <= 0 or math.isnan(t_stat):
        return 1.0
    abs_t = abs(t_stat)
    if abs_t == 0:
        return 1.0
    z = math.sqrt(df * math.log(1.0 + (abs_t * abs_t) / df)) * (1.0 - 1.0 / (4.0 * df))
    p = 2.0 * (1.0 - _normal_cdf(z))
    return max(0.0001, min(1.0, round(p, 4)))


def wilson_confidence_interval(successes: int, total: int, z: float = 1.96) -> Tuple[float, float]:
    """
    Calcula el Intervalo de Confianza de Wilson al 95% para una proporción binomial (en porcentaje 0-100).
    Garantiza límites válidos [0%, 100%] incluso en muestras pequeñas o proporciones extremas.
    """
    if total <= 0:
        return 0.0, 0.0
    s = max(0, min(total, int(successes)))
    p = s / total
    denom = 1.0 + (z * z) / total
    center = (p + (z * z) / (2.0 * total)) / denom
    half_width = (z * math.sqrt((p * (1.0 - p) + (z * z) / (4.0 * total)) / total)) / denom
    low = max(0.0, (center - half_width) * 100.0)
    high = min(100.0, (center + half_width) * 100.0)
    return round(low, 2), round(high, 2)


def mean_std_ci95(values: List[float], scale: float = 1.0) -> Dict[str, float]:
    """
    Calcula media, desviación estándar muestral (s), varianza (s^2), error estándar (SE)
    e intervalo de confianza al 95% escalado por `scale` (ej. scale=100 para BB/100).
    """
    n = len(values)
    if n == 0:
        return {
            "n": 0,
            "mean": 0.0,
            "std": 0.0,
            "std_per_unit": 0.0,
            "variance": 0.0,
            "variance_scaled": 0.0,
            "se": 0.0,
            "ci95_low": 0.0,
            "ci95_high": 0.0,
            "ci95_margin": 0.0,
        }
    mean_val = sum(values) / n
    if n < 2:
        scaled_mean = round(mean_val * scale, 2)
        return {
            "n": n,
            "mean": scaled_mean,
            "std": 0.0,
            "std_per_unit": 0.0,
            "variance": 0.0,
            "variance_scaled": 0.0,
            "se": 0.0,
            "ci95_low": scaled_mean,
            "ci95_high": scaled_mean,
            "ci95_margin": 0.0,
        }

    var_val = sum((x - mean_val) ** 2 for x in values) / (n - 1)
    std_val = math.sqrt(var_val)
    se_val = std_val / math.sqrt(n)
    t_crit = _t_critical_95(n - 1)
    margin = t_crit * se_val * scale
    scaled_mean = mean_val * scale

    return {
        "n": n,
        "mean": round(scaled_mean, 2),
        "std": round(std_val * scale, 2),
        "std_per_unit": round(std_val, 4),
        "variance": round(var_val, 4),
        "variance_scaled": round(var_val * (scale ** 2), 2),
        "se": round(se_val * scale, 2),
        "ci95_low": round(scaled_mean - margin, 2),
        "ci95_high": round(scaled_mean + margin, 2),
        "ci95_margin": round(margin, 2),
    }


def holm_bonferroni_correction(p_values: List[float], alpha: float = 0.05) -> List[Dict[str, Any]]:
    """
    Corrección secuencial de Holm-Bonferroni para comparaciones múltiples (Fase 3).
    Controla la tasa de error familiar (FWER) sin ser tan conservadora como Bonferroni simple.
    """
    m = len(p_values)
    if m == 0:
        return []
    indexed = sorted(enumerate(p_values), key=lambda x: x[1])
    adjusted = [0.0] * m
    rejected = [False] * m
    running_max = 0.0
    stop_rejecting = False

    for rank_idx, (orig_idx, p_val) in enumerate(indexed):
        multiplier = m - rank_idx
        adj_p = min(1.0, max(0.0, float(p_val)) * multiplier)
        running_max = max(running_max, adj_p)
        adjusted[orig_idx] = round(running_max, 4)
        if not stop_rejecting and running_max < alpha:
            rejected[orig_idx] = True
        else:
            stop_rejecting = True
            rejected[orig_idx] = False

    return [
        {
            "raw_p_value": round(float(p_values[i]), 4),
            "holm_adjusted_p_value": adjusted[i],
            "significant_after_holm": rejected[i],
        }
        for i in range(m)
    ]


def welch_t_test(values_a: List[float], values_b: List[float], scale: float = 100.0) -> Dict[str, Any]:
    """
    Prueba t de Welch para dos muestras independientes (B respecto a A).
    Incluye intervalo de confianza al 95% de la diferencia y tamaño del efecto d de Cohen.
    """
    na, nb = len(values_a), len(values_b)
    mean_a = (sum(values_a) / na) if na > 0 else 0.0
    mean_b = (sum(values_b) / nb) if nb > 0 else 0.0
    delta_scaled = round((mean_b - mean_a) * scale, 2)

    if na < 2 or nb < 2:
        return {
            "n_a": na,
            "n_b": nb,
            "t_stat": 0.0,
            "df": 0.0,
            "p_value": 1.0,
            "cohens_d": 0.0,
            "delta_scaled": delta_scaled,
            "ci95_diff": [delta_scaled, delta_scaled],
            "significant_95": False,
            "verdict": "INCONCLUSO",
            "verdict_state": "INCONCLUSO",
        }

    var_a = sum((x - mean_a) ** 2 for x in values_a) / (na - 1)
    var_b = sum((x - mean_b) ** 2 for x in values_b) / (nb - 1)

    se_diff_sq = (var_a / na) + (var_b / nb)
    if se_diff_sq <= 1e-12:
        has_diff = abs(mean_b - mean_a) > 1e-9
        sig_const = has_diff and na >= 30 and nb >= 30
        v_state = ("MEJORA" if delta_scaled > 0 else "REGRESIÓN") if sig_const else "INCONCLUSO"
        return {
            "n_a": na,
            "n_b": nb,
            "t_stat": 99.0 if (has_diff and delta_scaled > 0) else (-99.0 if has_diff else 0.0),
            "df": float(na + nb - 2),
            "p_value": 0.0001 if has_diff else 1.0,
            "cohens_d": 10.0 if (has_diff and delta_scaled > 0) else (-10.0 if has_diff else 0.0),
            "delta_scaled": delta_scaled,
            "ci95_diff": [delta_scaled, delta_scaled],
            "significant_95": sig_const,
            "verdict": ("MEJORA_SIGNIFICATIVA" if delta_scaled > 0 else "RETROCESO_SIGNIFICATIVO") if sig_const else "SIN_CAMBIO_SIGNIFICATIVO",
            "verdict_state": v_state,
        }

    se_diff = math.sqrt(se_diff_sq)
    t_stat = (mean_b - mean_a) / se_diff
    num_df = se_diff_sq ** 2
    den_df = ((var_a / na) ** 2) / (na - 1) + ((var_b / nb) ** 2) / (nb - 1)
    df = num_df / den_df if den_df > 0 else float(na + nb - 2)

    p_val = _t_test_p_value_two_sided(t_stat, df)
    pooled_sd = math.sqrt(((na - 1) * var_a + (nb - 1) * var_b) / max(1, na + nb - 2))
    cohens_d = (mean_b - mean_a) / pooled_sd if pooled_sd > 1e-9 else 0.0

    t_crit = _t_critical_95(df)
    margin_scaled = t_crit * se_diff * scale
    ci_low = round(delta_scaled - margin_scaled, 2)
    ci_high = round(delta_scaled + margin_scaled, 2)

    # No declarar mejora únicamente por p < 0.05: exigir muestra mínima y tamaño del efecto
    sig = p_val < 0.05 and na >= 30 and nb >= 30 and abs(cohens_d) >= 0.15
    if sig and delta_scaled > 0 and ci_low > 0:
        v_state = "MEJORA"
        verdict_legacy = "MEJORA_SIGNIFICATIVA"
    elif sig and delta_scaled < 0 and ci_high < 0:
        v_state = "REGRESIÓN"
        verdict_legacy = "RETROCESO_SIGNIFICATIVO"
    else:
        v_state = "INCONCLUSO"
        verdict_legacy = "MUESTRA_INSUFICIENTE" if (na < 30 or nb < 30) else "SIN_CAMBIO_SIGNIFICATIVO"

    return {
        "n_a": na,
        "n_b": nb,
        "t_stat": round(t_stat, 3),
        "df": round(df, 1),
        "p_value": p_val,
        "cohens_d": round(cohens_d, 3),
        "delta_scaled": delta_scaled,
        "ci95_diff": [ci_low, ci_high],
        "significant_95": sig,
        "verdict": verdict_legacy,
        "verdict_state": v_state,
    }


def paired_t_test(pairs_a_b: List[Tuple[float, float]], scale: float = 100.0) -> Optional[Dict[str, Any]]:
    """
    Prueba t pareada cuando dos versiones han sido evaluadas en exactamente los mismos escenarios (`scenario_id`).
    Elimina la varianza del reparto de cartas (Common Random Numbers / Duplicate Poker).
    """
    n = len(pairs_a_b)
    if n < 5:
        return None
    diffs = [b - a for a, b in pairs_a_b]
    mean_d = sum(diffs) / n
    var_d = sum((d - mean_d) ** 2 for d in diffs) / (n - 1)
    delta_scaled = round(mean_d * scale, 2)

    if var_d <= 1e-12:
        has_diff = abs(mean_d) > 1e-9
        sig = has_diff and n >= 20
        return {
            "n_paired": n,
            "t_stat": 99.0 if (has_diff and mean_d > 0) else (-99.0 if has_diff else 0.0),
            "df": n - 1,
            "p_value": 0.0001 if has_diff else 1.0,
            "cohens_d_paired": 10.0 if (has_diff and mean_d > 0) else (-10.0 if has_diff else 0.0),
            "delta_scaled": delta_scaled,
            "ci95_diff": [delta_scaled, delta_scaled],
            "significant_95": sig,
        }
    std_d = math.sqrt(var_d)
    se_d = std_d / math.sqrt(n)
    t_stat = mean_d / se_d
    p_val = _t_test_p_value_two_sided(t_stat, n - 1)
    cohens_dz = mean_d / std_d if std_d > 1e-9 else 0.0
    t_crit = _t_critical_95(n - 1)
    margin_scaled = t_crit * se_d * scale

    return {
        "n_paired": n,
        "t_stat": round(t_stat, 3),
        "df": n - 1,
        "p_value": p_val,
        "cohens_d_paired": round(cohens_dz, 3),
        "delta_scaled": delta_scaled,
        "ci95_diff": [round(delta_scaled - margin_scaled, 2), round(delta_scaled + margin_scaled, 2)],
        "significant_95": p_val < 0.05 and n >= 20 and abs(cohens_dz) >= 0.15,
    }


def _normal_ppf(p: float) -> float:
    """Inversa aproximada de la normal estándar (búsqueda binaria sobre _normal_cdf)."""
    p = max(1e-9, min(1.0 - 1e-9, float(p)))
    lo, hi = -8.0, 8.0
    for _ in range(50):
        mid = 0.5 * (lo + hi)
        if _normal_cdf(mid) < p:
            lo = mid
        else:
            hi = mid
    return 0.5 * (lo + hi)


def calcular_tamano_muestra_necesario(
    delta_bb100: float = config.DEFAULT_TARGET_DELTA_BB100,
    std_bb_per_hand: float = 15.0,
    alpha: float = config.ALPHA_SIGNIFICANCE,
    power: float = config.POWER_TARGET,
    paired: bool = False,
) -> Dict[str, Any]:
    """
    Calcula cuántas manos hacen falta para detectar una diferencia de `delta_bb100`
    (en BB/100) con nivel de significancia `alpha` (bilateral) y potencia estadística `power`.
    """
    delta_clean = abs(float(delta_bb100 or 0.0))
    if delta_clean <= 1e-6:
        delta_clean = float(config.DEFAULT_TARGET_DELTA_BB100)

    used_default_std = std_bb_per_hand is None or float(std_bb_per_hand) <= 0.0
    std_hand = 15.0 if used_default_std else float(std_bb_per_hand)

    z_alpha = _normal_ppf(1.0 - float(alpha) / 2.0)
    z_beta = _normal_ppf(float(power))
    delta_per_hand = delta_clean / 100.0
    factor = 1.0 if paired else 2.0
    required_n = math.ceil(factor * (((z_alpha + z_beta) * std_hand / delta_per_hand) ** 2))

    return {
        "required_hands": int(required_n),
        "target_delta_bb100": round(delta_clean, 2),
        "std_bb_per_hand": round(std_hand, 4),
        "used_default_std": used_default_std,
        "alpha": float(alpha),
        "power": float(power),
        "paired": bool(paired),
    }


def clasificar_estado_comparacion(
    n_prev: int,
    n_curr: int,
    delta_ev_bb100: float,
    holm_p_value: float,
    ci95_diff: List[float],
    cohens_d: float,
    delta_error_rate_pct: float = 0.0,
    is_live: bool = True,
    paired_power_min_n: Optional[int] = None,
) -> Dict[str, Any]:
    """
    Clasifica rigurosamente una comparación experimental en los tres estados exigidos:
    - `MEJORA`: Evidencia favorable y suficiente (en vivo >= MIN_LIVE_HANDS_FOR_IMPROVEMENT; en benchmark pareado >= mínimo de potencia).
    - `REGRESIÓN`: Evidencia de deterioro (n >= 30 y [p_Holm < 0.05 con ΔEV < 0 o degradación sustancial]).
    - `INCONCLUSO`: Evidencia insuficiente o muestra exploratoria.
    """
    min_n_observed = min(n_prev, n_curr)
    if is_live:
        min_hands_threshold = int(config.MIN_LIVE_HANDS_FOR_IMPROVEMENT)
    else:
        min_hands_threshold = max(
            int(config.MIN_PAIRED_BENCHMARK_HANDS),
            int(paired_power_min_n or config.MIN_PAIRED_BENCHMARK_HANDS),
        )

    is_exploratory = min_n_observed < min_hands_threshold
    ci_low = float(ci95_diff[0]) if len(ci95_diff) >= 2 else delta_ev_bb100
    ci_high = float(ci95_diff[1]) if len(ci95_diff) >= 2 else delta_ev_bb100

    if n_prev < 30 or n_curr < 30:
        return {
            "verdict_state": "INCONCLUSO",
            "legacy_verdict": "MUESTRA_INSUFICIENTE",
            "is_exploratory": True,
            "n_prev": n_prev,
            "n_curr": n_curr,
            "min_hands_threshold": min_hands_threshold,
            "is_live": is_live,
            "sample_tag": f"EXPLORATORIA (n={min_n_observed} < 30)",
            "reason": (
                f"Evidencia insuficiente: muestra exploratoria (n_prev={n_prev}, n_curr={n_curr} < 30). "
                "No se declara MEJORA ni REGRESIÓN hasta alcanzar tamaño muestral suficiente."
            ),
        }

    sample_tag = (
        f"EXPLORATORIA (n={min_n_observed} < {min_hands_threshold})"
        if is_exploratory
        else f"CONFIRMATORIA (n={min_n_observed} >= {min_hands_threshold})"
    )

    if holm_p_value < 0.05 and delta_ev_bb100 > 0 and ci_low > 0 and cohens_d >= 0.15 and delta_error_rate_pct <= 2.0:
        if is_exploratory:
            return {
                "verdict_state": "INCONCLUSO",
                "legacy_verdict": "MUESTRA_INSUFICIENTE",
                "is_exploratory": True,
                "n_prev": n_prev,
                "n_curr": n_curr,
                "min_hands_threshold": min_hands_threshold,
                "is_live": is_live,
                "sample_tag": sample_tag,
                "reason": (
                    f"INCONCLUSO: tendencia positiva (ΔEV = {delta_ev_bb100:+.2f} BB/100, p-Holm = {holm_p_value:.4f}), "
                    f"pero muestra insuficiente para declarar MEJORA (n_prev={n_prev}, n_curr={n_curr} < mínimo {min_hands_threshold})."
                ),
            }
        return {
            "verdict_state": "MEJORA",
            "legacy_verdict": "MEJORA_CONFIRMADA",
            "is_exploratory": False,
            "n_prev": n_prev,
            "n_curr": n_curr,
            "min_hands_threshold": min_hands_threshold,
            "is_live": is_live,
            "sample_tag": sample_tag,
            "reason": (
                f"MEJORA verificada (n_prev={n_prev}, n_curr={n_curr} >= {min_hands_threshold}): "
                f"ΔEV = {delta_ev_bb100:+.2f} BB/100 (IC 95%: [{ci_low:+.2f}, {ci_high:+.2f}]), "
                f"p-Holm = {holm_p_value:.4f} < 0.05, tamaño del efecto d = {cohens_d:+.2f}."
            ),
        }

    if (holm_p_value < 0.05 and delta_ev_bb100 < 0 and ci_high < 0 and cohens_d <= -0.15) or (
        delta_ev_bb100 <= -8.0 and delta_error_rate_pct >= 3.0
    ):
        return {
            "verdict_state": "REGRESIÓN",
            "legacy_verdict": "RETROCESO_CONFIRMADO",
            "is_exploratory": is_exploratory,
            "n_prev": n_prev,
            "n_curr": n_curr,
            "min_hands_threshold": min_hands_threshold,
            "is_live": is_live,
            "sample_tag": sample_tag,
            "reason": (
                f"REGRESIÓN detectada: ΔEV = {delta_ev_bb100:+.2f} BB/100 (IC 95%: [{ci_low:+.2f}, {ci_high:+.2f}]), "
                f"p-Holm = {holm_p_value:.4f}, d = {cohens_d:+.2f}, ΔErrores = {delta_error_rate_pct:+.1f}%."
            ),
        }

    return {
        "verdict_state": "INCONCLUSO",
        "legacy_verdict": "SIN_CAMBIO_RELEVANTE",
        "is_exploratory": is_exploratory,
        "n_prev": n_prev,
        "n_curr": n_curr,
        "min_hands_threshold": min_hands_threshold,
        "is_live": is_live,
        "sample_tag": sample_tag,
        "reason": (
            f"INCONCLUSO: evidencia insuficiente para rechazar igualdad o tamaño del efecto pequeño "
            f"(n_prev={n_prev}, n_curr={n_curr}, ΔEV = {delta_ev_bb100:+.2f} BB/100, IC 95%: [{ci_low:+.2f}, {ci_high:+.2f}], p-Holm = {holm_p_value:.4f}, d = {cohens_d:+.2f})."
        ),
    }


# ==============================================================================
# 2. MOTOR DE EVALUACIÓN DE CALIDAD DE DECISIONES Y ESTIMACIÓN DE EV
# ==============================================================================

def _detectar_proyectos_postflop(hole_cards_str: str, board_str: str) -> Dict[str, Any]:
    """Detecta proyectos de color (Flush Draw) y escalera (OESD / Gutshot) para cálculo de Equity."""
    mano = parsear_lista_cartas(hole_cards_str)
    mesa = parsear_lista_cartas(board_str)
    if not mano or len(mesa) < 3 or len(mesa) >= 5:
        return {"flush_draw": False, "straight_draw": False, "outs": 0, "draw_equity": 0.0}

    todas = mano + mesa
    palos_mano = [s for _, s in mano if s]
    palos_todas = [s for _, s in todas if s]

    flush_draw = False
    for p in set(palos_mano):
        if palos_todas.count(p) == 4:
            flush_draw = True
            break

    valores = sorted(set(VALORES_RANGO.get(r, 0) for r, _ in todas if r in VALORES_RANGO))
    if 14 in valores:
        valores = sorted(set([1] + valores))

    straight_draw = False
    for i in range(len(valores) - 3):
        if valores[i + 3] - valores[i] <= 4:
            straight_draw = True
            break

    outs = (9 if flush_draw else 0) + (6 if straight_draw else 0)
    cartas_por_venir = 2 if len(mesa) == 3 else 1
    draw_equity = min(0.45, (outs * (0.04 if cartas_por_venir == 2 else 0.02)))
    return {
        "flush_draw": flush_draw,
        "straight_draw": straight_draw,
        "outs": outs,
        "draw_equity": round(draw_equity, 3),
    }


def estimar_equity_mano(
    hole_cards_str: str,
    board_str: str,
    position: str = "BTN",
    opponent_profile: str = "REGULAR EQUILIBRADO",
    to_call: float = 0.0,
    pot_size: float = 40.0,
) -> float:
    """
    Estima la Equity matemática condicional al rango percibido del rival [0.05, 0.96]
    reutilizando las funciones deterministas de `poker_gto_engine.py`.
    """
    pre = clasificar_mano_preflop(hole_cards_str)
    comb = evaluar_combinacion_mano_y_mesa(hole_cards_str, board_str)
    textura = analizar_textura_mesa_y_blockers(hole_cards_str, board_str)
    draws = _detectar_proyectos_postflop(hole_cards_str, board_str)

    es_preflop = not bool((board_str or "").strip())
    score_pre = pre.get("score", 45) or 45

    if es_preflop:
        if to_call > 0:
            base_eq = 0.18 + ((score_pre - 25) / 70.0) * 0.58
        else:
            base_eq = 0.28 + ((score_pre - 25) / 70.0) * 0.52
    else:
        cat = comb.get("categoria", "")
        if "TRÍO EN SET" in cat or "FULL" in cat:
            base_eq = 0.91
        elif "DOBLE PAREJA PRIVADA" in cat or "TRÍO CONECTADO" in cat:
            base_eq = 0.82
        elif "Overpair" in cat:
            base_eq = 0.75
        elif "Top Pair" in cat:
            base_eq = 0.68
        elif "Pocket Pair" in cat or "Middle/Bottom Pair" in cat:
            base_eq = 0.52 if to_call == 0 else 0.44
        elif "Board Pair" in cat:
            base_eq = (0.30 if to_call == 0 else 0.21) + (max(0, score_pre - 50) / 100.0) * 0.25
        else:
            base_eq = (0.24 if to_call == 0 else 0.16) + (max(0, score_pre - 40) / 100.0) * 0.20

        if base_eq < 0.72:
            base_eq = min(0.76, base_eq + draws["draw_equity"])

        if textura.get("posible_color") or textura.get("posible_escalera"):
            if 0.48 <= base_eq <= 0.76 and not draws["flush_draw"]:
                base_eq -= 0.05

    pos_upper = (position or "").upper()
    if "BTN" in pos_upper or "DEALER" in pos_upper:
        base_eq += 0.02
    elif "SB" in pos_upper:
        base_eq -= 0.01

    opp_upper = (opponent_profile or "").upper()
    if to_call > 0:
        if "NIT" in opp_upper:
            base_eq -= 0.07
        elif "MANIAC" in opp_upper:
            base_eq += 0.06
        elif "CALLING STATION" in opp_upper and to_call > pot_size * 0.5:
            base_eq -= 0.04

    return round(max(0.05, min(0.96, base_eq)), 4)


def evaluar_decision_estrategica(
    hole_cards: str,
    board: str,
    pot_size: float,
    to_call: float,
    effective_stack: float,
    action_taken: str,
    bet_amount: float = 0.0,
    position: str = "BTN",
    street: str = "PREFLOP",
    opponent_profile: str = "REGULAR EQUILIBRADO",
    legal_actions: Optional[List[str]] = None,
    big_blind: float = 20.0,
) -> Dict[str, Any]:
    """
    Evalúa la calidad de una decisión con la información disponible en el momento de tomarla.
    - Cuando no existe información suficiente (ej. `hole_cards` vacío/ilegible o `pot_size <= 0`),
      indica explícitamente `evaluation_available = False` y `decision_quality = 'EVALUACION_NO_DISPONIBLE'`.
    - Nunca clasifica una decisión como incorrecta por haber perdido la mano a posteriori.
    """
    bb = max(1.0, float(big_blind or 20.0))
    raw_pot = float(pot_size) if pot_size is not None else 0.0
    call_amt = max(0.0, float(to_call or 0.0))
    eff_stack = max(call_amt + bb, float(effective_stack or 500.0))

    act = (action_taken or "CHECK").upper().strip()
    if act == "ALLIN":
        act = "ALL_IN"

    # Clasificación por tamaño de bote y profundidad de stack (Fase 2.C)
    pot_bb_val = max(0.0, raw_pot) / bb
    pot_size_bucket = "SMALL (<4 BB)" if pot_bb_val < 4.0 else ("MEDIUM (4-12 BB)" if pot_bb_val <= 12.0 else "LARGE (>12 BB)")
    stack_bb_val = eff_stack / bb
    stack_depth_bucket = "SHORT (<10 BB)" if stack_bb_val < 10.0 else ("MEDIUM (10-20 BB)" if stack_bb_val <= 20.0 else "DEEP (>20 BB)")

    cartas_parseadas = parsear_lista_cartas(hole_cards or "")
    if len(cartas_parseadas) < 2 or raw_pot <= 0.0:
        return {
            "evaluation_available": False,
            "action_taken": act,
            "gto_recommended_action": "NO_DISPONIBLE",
            "gto_recommended_sizing": 0.0,
            "gto_mixed_frequencies": {},
            "in_mixed_strategy_support": True,
            "is_gto_aligned": False,
            "action_deviation": "EVALUACION_NO_DISPONIBLE",
            "ev_by_action": {},
            "ev_taken_chips": 0.0,
            "ev_best_chips": 0.0,
            "ev_lost_chips": 0.0,
            "ev_lost_bb": 0.0,
            "estimated_equity": 0.0,
            "pot_odds_pct": 0.0,
            "decision_quality": "EVALUACION_NO_DISPONIBLE",
            "is_strategic_error": False,
            "is_missed_opportunity": False,
            "error_description": "EVALUACION_NO_DISPONIBLE: Información insuficiente de cartas propias o bote en el momento de la decisión",
            "opponent_adaptation_aligned": True,
            "pot_size_bucket": pot_size_bucket,
            "stack_depth_bucket": stack_depth_bucket,
        }

    pot = max(bb, raw_pot)

    if not legal_actions:
        if call_amt <= 0:
            legal_actions = ["CHECK", "BET", "RAISE", "ALL_IN"]
        else:
            legal_actions = ["FOLD", "CALL", "RAISE", "ALL_IN"]
    else:
        legal_actions = [a.upper().strip() for a in legal_actions]

    hay_check_gratis = (call_amt <= 0) and ("CHECK" in legal_actions)

    # 1. Estimar Equity y Pot Odds
    equity = estimar_equity_mano(
        hole_cards_str=hole_cards,
        board_str=board,
        position=position,
        opponent_profile=opponent_profile,
        to_call=call_amt,
        pot_size=pot,
    )
    math_info = calcular_pot_odds_y_sizing(str(int(pot)), str(int(call_amt)), str(int(eff_stack)))
    pot_odds_pct = float(math_info.get("pot_odds_pct") or 0.0)
    sizing_std = float(math_info.get("sizing_cbet_estandar") or max(bb, pot * 0.66))
    raise_size = float(bet_amount) if bet_amount and bet_amount > call_amt else max(call_amt + sizing_std, sizing_std)
    raise_size = min(eff_stack, raise_size)

    # 2. Estimar Fold Equity (FE) según perfil del rival y tamaño de apuesta
    opp_upper = (opponent_profile or "REGULAR EQUILIBRADO").upper()
    if "CALLING STATION" in opp_upper:
        base_fe = 0.12
    elif "MANIAC" in opp_upper:
        base_fe = 0.22
    elif "NIT" in opp_upper:
        base_fe = 0.56
    else:
        base_fe = 0.35

    sizing_factor = min(1.35, max(0.65, raise_size / max(1.0, pot)))
    fold_equity = min(0.75, max(0.05, base_fe * sizing_factor))

    if equity >= 0.66:
        equity_when_called = max(0.50, equity - 0.05 * (1.0 - fold_equity))
    elif equity >= 0.54 and ("BTN" in position.upper() or "DEALER" in position.upper()) and hay_check_gratis:
        equity_when_called = max(0.35, equity - 0.10)
    else:
        equity_when_called = max(0.08, equity * 0.56)

    # 3. Calcular EV de cada acción alternativa (en fichas respecto al punto de decisión)
    ev_fold = 0.0
    ev_check = round(equity * pot, 2) if hay_check_gratis else None
    ev_call = round(equity * (pot + call_amt) - call_amt, 2) if not hay_check_gratis else round(equity * pot, 2)

    add_raise = max(bb, raise_size - call_amt)
    ev_raise_raw = fold_equity * pot + (1.0 - fold_equity) * (
        equity_when_called * (pot + call_amt + 2.0 * add_raise) - (call_amt + add_raise)
    )
    ev_raise = round(ev_raise_raw, 2)

    fe_allin = min(0.80, fold_equity * 1.18) if eff_stack <= pot * 3.0 else fold_equity * 0.85
    eq_allin_called = equity if equity >= 0.72 else (equity * 0.82 if eff_stack <= 9.0 * bb else equity * 0.52)
    add_allin = max(bb, eff_stack - call_amt)
    ev_allin = round(
        fe_allin * pot + (1.0 - fe_allin) * (eq_allin_called * (pot + call_amt + 2.0 * add_allin) - eff_stack),
        2,
    )

    ev_by_action: Dict[str, float] = {"FOLD": ev_fold}
    if hay_check_gratis and ev_check is not None:
        ev_by_action["CHECK"] = ev_check
        ev_by_action["BET"] = ev_raise
        ev_by_action["RAISE"] = ev_raise
    else:
        ev_by_action["CALL"] = ev_call
        ev_by_action["RAISE"] = ev_raise
    ev_by_action["ALL_IN"] = ev_allin

    # 4. Recomendación del motor Heurístico-GTO (`poker_gto_engine.py`) y Frecuencias Mixtas
    comb_info = evaluar_combinacion_mano_y_mesa(hole_cards, board)
    cat = comb_info.get("categoria", "")

    if eff_stack <= 9.0 * bb and equity >= 0.52:
        gto_action = "ALL_IN"
        gto_sizing = eff_stack
        gto_mixed_freq = {"ALL_IN": 0.85, "RAISE": 0.15, "FOLD": 0.0}
    elif equity >= 0.66:
        gto_action = "RAISE" if not hay_check_gratis else "BET"
        gto_sizing = raise_size
        sec_act = "CHECK" if hay_check_gratis else "CALL"
        gto_mixed_freq = {gto_action: 0.70, sec_act: 0.20, "ALL_IN": 0.10}
    elif hay_check_gratis:
        if equity >= 0.56 and ("BTN" in position.upper() or "DEALER" in position.upper()):
            gto_action = "BET"
            gto_sizing = sizing_std
            gto_mixed_freq = {"BET": 0.70, "CHECK": 0.20, "RAISE": 0.10}
        elif equity >= 0.44:
            gto_action = "CHECK"
            gto_sizing = 0.0
            gto_mixed_freq = {"CHECK": 0.70, "BET": 0.20, "RAISE": 0.10}
        else:
            gto_action = "CHECK"
            gto_sizing = 0.0
            gto_mixed_freq = {"CHECK": 0.90, "BET": 0.10, "FOLD": 0.0}
    else:
        required_eq = (pot_odds_pct / 100.0)
        if equity >= required_eq + 0.16 and equity >= 0.65:
            gto_action = "RAISE"
            gto_sizing = raise_size
            gto_mixed_freq = {"RAISE": 0.70, "CALL": 0.20, "ALL_IN": 0.10}
        elif equity >= required_eq - 0.02:
            gto_action = "CALL"
            gto_sizing = call_amt
            gto_mixed_freq = {"CALL": 0.70, "RAISE": 0.20, "FOLD": 0.10}
        else:
            gto_action = "FOLD"
            gto_sizing = 0.0
            gto_mixed_freq = {"FOLD": 0.85, "CALL": 0.10, "RAISE": 0.05} if equity >= required_eq - 0.08 else {"FOLD": 1.0}

    def _norm_act(a: str) -> str:
        a = a.upper().strip()
        if a in ("BET", "RAISE"):
            return "RAISE"
        if a in ("ALLIN", "ALL_IN"):
            return "ALL_IN"
        return a

    act_norm = _norm_act(act)
    gto_norm = _norm_act(gto_action)

    legal_norms = {_norm_act(a) for a in legal_actions}

    # Adaptar recomendación GTO si las acciones de mesa restringen opciones (ej. Push/Fold)
    if gto_norm == "RAISE" and "RAISE" not in legal_norms and "ALL_IN" in legal_norms:
        gto_norm = "ALL_IN"
        gto_action = "ALL_IN"
    elif gto_norm == "CALL" and "CALL" not in legal_norms and "ALL_IN" in legal_norms:
        gto_norm = "ALL_IN"
        gto_action = "ALL_IN"

    # Verificar si la acción pertenece al soporte de la estrategia mixta (> 0%)
    mixed_support_norms = {_norm_act(k) for k, v in gto_mixed_freq.items() if v > 0.0}
    in_mixed_support = act_norm in mixed_support_norms

    ev_taken = ev_by_action.get(act, ev_by_action.get(act_norm, 0.0))
    candidate_actions = ["CHECK", "BET", "RAISE"] if hay_check_gratis else ["FOLD", "CALL", "RAISE"]
    if eff_stack <= 9.0 * bb or gto_norm == "ALL_IN":
        candidate_actions.append("ALL_IN")

    # Restringir candidate_actions a las opciones legalmente disponibles
    valid_candidates = [k for k in candidate_actions if _norm_act(k) in legal_norms or k in legal_actions]
    if valid_candidates:
        candidate_actions = valid_candidates
    else:
        candidate_actions = list(legal_actions)

    ev_best = max(ev_by_action.get(k, -9999.0) for k in candidate_actions)
    if act_norm == gto_norm:
        ev_best = max(ev_best, ev_taken)
        ev_lost_chips = 0.0
    else:
        ev_lost_chips = max(0.0, round(ev_best - ev_taken, 2))
    ev_lost_bb = round(ev_lost_chips / bb, 4)

    # 5. Evaluar Adaptación Explotativa al Perfil del Oponente
    adaptation_aligned = True
    is_exploitative_play = False
    error_reasons: List[str] = []

    if hay_check_gratis and act_norm == "FOLD":
        adaptation_aligned = False
        error_reasons.append("Fold ilegal/subóptimo teniendo Check gratuito disponible")

    if "CALLING STATION" in opp_upper:
        if act_norm in ("RAISE", "ALL_IN") and equity < 0.45:
            adaptation_aligned = False
            error_reasons.append("Farol contra Calling Station (rival que nunca foldea)")
        elif act_norm in ("RAISE", "ALL_IN") and 0.55 <= equity < 0.66:
            is_exploitative_play = True
    elif "NIT" in opp_upper:
        if act_norm in ("RAISE", "ALL_IN") and call_amt == 0 and equity < 0.50:
            is_exploitative_play = True
        elif act_norm in ("CALL", "RAISE", "ALL_IN") and call_amt > pot * 0.5 and equity < 0.60:
            adaptation_aligned = False
            error_reasons.append("Pagar apuesta fuerte de un NIT con mano marginal")
        elif act_norm == "FOLD" and gto_norm == "CALL" and equity < 0.55:
            is_exploitative_play = True
    elif "MANIAC" in opp_upper:
        if act_norm == "CALL" and gto_norm in ("RAISE", "FOLD") and 0.44 <= equity <= 0.72:
            is_exploitative_play = True
        elif act_norm == "FOLD" and equity >= 0.48 and call_amt <= pot * 0.75:
            adaptation_aligned = False
            error_reasons.append("Overfold contra Maniac teniendo equity suficiente para cazar farol")

    # 6. Detectar otros errores estratégicos y Oportunidades Perdidas (Fase 2.C)
    is_missed_opportunity = False
    if act_norm in ("CHECK", "CALL") and gto_norm in ("RAISE", "ALL_IN") and equity >= 0.62 and ev_lost_bb >= 0.60 and "MANIAC" not in opp_upper:
        is_missed_opportunity = True

    if not hay_check_gratis and act_norm == "CALL" and (equity * 100.0) < (pot_odds_pct - 10.0) and not is_exploitative_play:
        error_reasons.append(f"Call fuera de Pot Odds (Equity {equity*100:.1f}% vs Pot Odds {pot_odds_pct:.1f}%)")
    if "Board Pair" in cat and not comb_info.get("es_pareja_en_mano") and not comb_info.get("coincidencias_mesa"):
        if act_norm in ("RAISE", "ALL_IN") and equity < 0.48 and "NIT" not in opp_upper:
            error_reasons.append("Sobrevalorar pareja compartida en mesa sin kicker/conexión privada")
    if equity >= 0.80 and act_norm in ("FOLD", "CHECK") and "MANIAC" not in opp_upper:
        if act_norm == "FOLD":
            error_reasons.append("Fold catastrófico con mano monstruo")
        elif street.upper() == "RIVER" and act_norm == "CHECK":
            is_missed_opportunity = True
            error_reasons.append("Pérdida de valor (Missed Value Bet) en el River con mano muy fuerte")

    # 7. Clasificar alineación Heurística-GTO, tipo de desviación y calidad según EV
    is_gto_aligned = (act_norm == gto_norm) or (ev_lost_bb <= 0.35 and not error_reasons)

    if act_norm == gto_norm:
        action_deviation = "EXACT_MATCH"
    elif is_exploitative_play and ev_lost_bb <= 1.2:
        action_deviation = "EXPLOITATIVE_ADAPTATION"
        is_gto_aligned = True
    elif act_norm == "FOLD" and gto_norm in ("CALL", "RAISE", "CHECK"):
        action_deviation = f"OVERFOLD ({act_norm} vs {gto_norm})"
    elif act_norm in ("CHECK", "CALL") and gto_norm in ("RAISE", "ALL_IN"):
        action_deviation = f"PASSIVE_DEVIATION ({act_norm} vs {gto_norm})"
    elif act_norm in ("RAISE", "ALL_IN") and gto_norm in ("FOLD", "CHECK", "CALL"):
        action_deviation = f"AGGRESSIVE_DEVIATION ({act_norm} vs {gto_norm})"
    else:
        action_deviation = f"DEVIATION ({act_norm} vs {gto_norm})"

    is_strategic_error = bool(error_reasons) or (ev_lost_bb > 2.0 and not is_exploitative_play)

    if not is_strategic_error and is_exploitative_play and act_norm != gto_norm:
        decision_quality = "ADAPTACION_EXPLOTATIVA"
    elif not is_strategic_error and ev_lost_bb <= 0.35:
        decision_quality = "OPTIMA"
    elif not is_strategic_error and ev_lost_bb <= 1.10:
        decision_quality = "ACEPTABLE"
    elif ev_lost_bb <= 2.25 and not error_reasons:
        decision_quality = "INACCURACY"
    elif ev_lost_bb > 4.50 or (hay_check_gratis and act_norm == "FOLD"):
        decision_quality = "BLUNDER_EV"
        is_strategic_error = True
    else:
        decision_quality = "ERROR_ESTRATEGICO"
        is_strategic_error = True

    if (is_strategic_error or ev_lost_bb > 1.10) and not error_reasons:
        error_reasons.append(f"Desviación subóptima ({action_deviation}) con pérdida de {ev_lost_bb:.2f} BB de EV")

    return {
        "evaluation_available": True,
        "action_taken": act,
        "gto_recommended_action": gto_action,
        "gto_recommended_sizing": round(gto_sizing, 2),
        "gto_mixed_frequencies": gto_mixed_freq,
        "in_mixed_strategy_support": in_mixed_support,
        "is_gto_aligned": is_gto_aligned,
        "action_deviation": action_deviation,
        "ev_by_action": ev_by_action,
        "ev_taken_chips": ev_taken,
        "ev_best_chips": ev_best,
        "ev_lost_chips": ev_lost_chips,
        "ev_lost_bb": ev_lost_bb,
        "estimated_equity": equity,
        "pot_odds_pct": pot_odds_pct,
        "decision_quality": decision_quality,
        "is_strategic_error": is_strategic_error,
        "is_missed_opportunity": is_missed_opportunity,
        "error_description": "; ".join(error_reasons) if error_reasons else "Decisión sólida en EV",
        "opponent_adaptation_aligned": adaptation_aligned,
        "pot_size_bucket": pot_size_bucket,
        "stack_depth_bucket": stack_depth_bucket,
    }


# ==============================================================================
# 3. MOTOR CENTRAL DE ANALÍTICAS Y PROCESAMIENTO ESTADÍSTICO (FASE 2 & 3)
# ==============================================================================

class PokerAnalyticsEngine:
    """
    Procesa manos, decisiones, perfiles de oponentes, versiones y telemetría para producir:
    - Métricas Financieras y Estadísticas (incluyendo All-in EV, Sharpe Ratio validado, Varianza).
    - Estadísticas HUD con numerador, denominador, tamaño de muestra, fórmula y serie de evolución.
    - Calidad Estratégica (por posición, calle, tamaño de bote, profundidad de stack y oportunidades perdidas).
    - Evaluación GTO Rigurosa (separando coincidencia heurística, EV, frecuencias mixtas, Regret y Exploitability).
    - Comparación Científica de Versiones (Welch, Pareada, Holm-Bonferroni, Cohen's d y 3 estados).
    - Diagnósticos Inteligentes (Fase 5) y Reporte de Confiabilidad P50/P95/P99 (Fase 6).
    """

    def __init__(self, storage: Optional[AnalyticsStorage] = None, db_path=DB_PATH):
        self.storage = storage or AnalyticsStorage(db_path=db_path)

    def calcular_analiticas_completas(self, filters: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        t0_ns = time.perf_counter_ns()
        hands = self.storage.consultar_manos(filters)
        decisions = self.storage.consultar_decisiones(filters)
        versions = self.storage.obtener_versiones()
        experiments = self.storage.obtener_experimentos()
        sessions = self.storage.obtener_sesiones()
        oponentes_db = self.storage.consultar_oponentes_db()
        verificaciones = self.storage.consultar_verificaciones_oponentes(filters)

        # ------------------------------------------------------------------
        # A. RENDIMIENTO FINANCIERO Y ESTADÍSTICO (FASE 2.A)
        # ------------------------------------------------------------------
        n_hands = len(hands)
        session_ids_in_hands = {h["session_id"] for h in hands}
        n_sessions = len(session_ids_in_hands) if n_hands > 0 else 0

        live_hands_count = sum(1 for h in hands if not h.get("is_synthetic"))
        benchmark_hands_count = sum(1 for h in hands if h.get("is_synthetic"))

        hands_won = sum(1 for h in hands if h.get("won_hand") or float(h.get("net_profit_chips") or 0) > 0)
        hands_lost = sum(1 for h in hands if float(h.get("net_profit_chips") or 0) < 0)
        hands_pushed = max(0, n_hands - hands_won - hands_lost)

        win_pct = round((hands_won / n_hands) * 100.0, 2) if n_hands > 0 else 0.0
        win_ci_low, win_ci_high = wilson_confidence_interval(hands_won, n_hands)

        gross_won_chips = round(sum(float(h.get("net_profit_chips") or 0.0) for h in hands if float(h.get("net_profit_chips") or 0.0) > 0), 2)
        gross_lost_chips = round(sum(float(h.get("net_profit_chips") or 0.0) for h in hands if float(h.get("net_profit_chips") or 0.0) < 0), 2)
        gross_won_bb = round(sum(float(h.get("net_profit_bb") or 0.0) for h in hands if float(h.get("net_profit_bb") or 0.0) > 0), 2)
        gross_lost_bb = round(sum(float(h.get("net_profit_bb") or 0.0) for h in hands if float(h.get("net_profit_bb") or 0.0) < 0), 2)

        net_profit_chips = round(sum(float(h.get("net_profit_chips") or 0.0) for h in hands), 2)
        net_profit_bb = round(sum(float(h.get("net_profit_bb") or 0.0) for h in hands), 2)
        ev_net_chips = round(sum(float(h.get("ev_net_chips") or 0.0) for h in hands), 2)
        ev_net_bb = round(sum(float(h.get("ev_net_bb") or 0.0) for h in hands), 2)

        # All-in EV (Fase 2.A): valor esperado ajustado en manos con exposición total o showdown
        allin_ev_chips = round(
            sum(float(h.get("allin_ev_chips") if h.get("allin_ev_chips") is not None else (h.get("ev_net_chips") or 0.0)) for h in hands),
            2,
        )
        allin_ev_bb = round(
            sum(float(h.get("allin_ev_bb") if h.get("allin_ev_bb") is not None else (h.get("ev_net_bb") or 0.0)) for h in hands),
            2,
        )
        allin_hands_count = sum(
            1
            for h in hands
            if h.get("is_allin") or h.get("went_to_showdown") or float(h.get("invested_chips") or 0.0) >= float(h.get("effective_stack") or 500.0) * 0.75
        )

        # ROI cuando exista una base de inversión adecuada
        total_invested_chips = sum(max(20.0, float(h.get("invested_chips") or 20.0)) for h in hands)
        session_buyins = sum(
            float(s.get("buy_in_total") or 500.0)
            for s in sessions
            if s["session_id"] in session_ids_in_hands
        )
        investment_base = max(session_buyins, total_invested_chips * 0.25) if n_hands > 0 else 0.0
        roi_applicable = investment_base >= 100.0 and n_hands > 0
        roi_pct = round((net_profit_chips / investment_base) * 100.0, 2) if roi_applicable else 0.0
        ev_roi_pct = round((ev_net_chips / investment_base) * 100.0, 2) if roi_applicable else 0.0

        bb_values = [float(h.get("net_profit_bb") or 0.0) for h in hands]
        ev_bb_values = [float(h.get("ev_net_bb") or 0.0) for h in hands]
        allin_ev_bb_values = [
            float(h.get("allin_ev_bb") if h.get("allin_ev_bb") is not None else (h.get("ev_net_bb") or 0.0))
            for h in hands
        ]

        wr_stats = mean_std_ci95(bb_values, scale=100.0)
        ev_wr_stats = mean_std_ci95(ev_bb_values, scale=100.0)
        allin_wr_stats = mean_std_ci95(allin_ev_bb_values, scale=100.0)

        avg_profit_chips = round(net_profit_chips / n_hands, 2) if n_hands > 0 else 0.0
        avg_profit_bb = round(net_profit_bb / n_hands, 3) if n_hands > 0 else 0.0

        # Curva de evolución de Bankroll, Winrate, All-in EV y Drawdown
        initial_br = 1500.0
        cur_br = initial_br
        cur_ev_br = initial_br
        cur_allin_br = initial_br
        peak_br = initial_br
        peak_bb = 0.0
        cum_bb = 0.0
        cum_ev_bb = 0.0
        cum_allin_bb = 0.0
        max_drawdown_chips = 0.0
        max_drawdown_bb = 0.0

        bankroll_curve: List[Dict[str, Any]] = []
        rolling_window = 25

        for idx, h in enumerate(hands, start=1):
            p_chips = float(h.get("net_profit_chips") or 0.0)
            e_chips = float(h.get("ev_net_chips") or 0.0)
            a_chips = float(h.get("allin_ev_chips") if h.get("allin_ev_chips") is not None else e_chips)
            p_bb = float(h.get("net_profit_bb") or 0.0)
            e_bb = float(h.get("ev_net_bb") or 0.0)
            a_bb = float(h.get("allin_ev_bb") if h.get("allin_ev_bb") is not None else e_bb)

            cur_br += p_chips
            cur_ev_br += e_chips
            cur_allin_br += a_chips
            cum_bb += p_bb
            cum_ev_bb += e_bb
            cum_allin_bb += a_bb

            if cur_br > peak_br:
                peak_br = cur_br
            if cum_bb > peak_bb:
                peak_bb = cum_bb

            dd_chips = peak_br - cur_br
            dd_bb = peak_bb - cum_bb
            if dd_chips > max_drawdown_chips:
                max_drawdown_chips = dd_chips
            if dd_bb > max_drawdown_bb:
                max_drawdown_bb = dd_bb

            win_slice = bb_values[max(0, idx - rolling_window): idx]
            rolling_bb100 = round((sum(win_slice) / len(win_slice)) * 100.0, 2) if win_slice else 0.0
            cum_bb100 = round((cum_bb / idx) * 100.0, 2)
            cum_ev_bb100 = round((cum_ev_bb / idx) * 100.0, 2)
            cum_allin_bb100 = round((cum_allin_bb / idx) * 100.0, 2)

            bankroll_curve.append({
                "hand_index": idx,
                "hand_uid": h.get("hand_uid"),
                "version_id": h.get("version_id"),
                "date_str": h.get("date_str"),
                "bankroll_real": round(cur_br, 2),
                "bankroll_ev": round(cur_ev_br, 2),
                "bankroll_allin_ev": round(cur_allin_br, 2),
                "cumulative_bb": round(cum_bb, 2),
                "cumulative_ev_bb": round(cum_ev_bb, 2),
                "cumulative_allin_ev_bb": round(cum_allin_bb, 2),
                "cumulative_bb100": cum_bb100,
                "cumulative_ev_bb100": cum_ev_bb100,
                "cumulative_allin_ev_bb100": cum_allin_bb100,
                "rolling_bb100": rolling_bb100,
                "drawdown_bb": round(dd_bb, 2),
            })

        # ------------------------------------------------------------------
        # B. ESTADÍSTICAS HUD CON DEFINICIONES MATEMÁTICAS DOCUMENTADAS (FASE 2.B)
        # ------------------------------------------------------------------
        vpip_count = sum(1 for h in hands if h.get("vpip_flag"))
        pfr_count = sum(1 for h in hands if h.get("pfr_flag"))
        three_bet_opps = sum(1 for h in hands if h.get("three_bet_opp"))
        three_bet_count = sum(1 for h in hands if h.get("three_bet_flag"))
        faced_3bet_count = sum(1 for h in hands if h.get("faced_three_bet"))
        fold_to_3bet_count = sum(1 for h in hands if h.get("fold_to_three_bet"))

        saw_flop_count = sum(1 for h in hands if h.get("saw_flop"))
        wtsd_count = sum(1 for h in hands if h.get("saw_flop") and h.get("went_to_showdown"))
        wsd_count = sum(1 for h in hands if h.get("went_to_showdown") and h.get("won_at_showdown"))
        wwsf_count = sum(1 for h in hands if h.get("saw_flop") and h.get("won_hand"))

        cbet_opps = sum(1 for h in hands if h.get("cbet_flop_opp"))
        cbet_count = sum(1 for h in hands if h.get("cbet_flop_flag"))
        faced_cbet_count = sum(1 for h in hands if h.get("faced_cbet_flop"))
        fold_to_cbet_count = sum(1 for h in hands if h.get("fold_to_cbet_flop"))

        action_counts = {"FOLD": 0, "CHECK": 0, "CALL": 0, "BET": 0, "RAISE": 0}
        postflop_aggr = 0
        postflop_calls = 0
        postflop_folds = 0
        postflop_checks = 0
        postflop_total_act = 0

        for d in decisions:
            raw_a = (d.get("action_taken") or "").upper()
            st = (d.get("street") or "PREFLOP").upper()
            to_c = float(d.get("to_call") or 0.0)

            if raw_a in ("ALL_IN", "ALLIN", "RAISE"):
                if to_c == 0 and st != "PREFLOP":
                    action_counts["BET"] += 1
                else:
                    action_counts["RAISE"] += 1
            elif raw_a == "BET":
                action_counts["BET"] += 1
            elif raw_a == "CALL":
                action_counts["CALL"] += 1
            elif raw_a == "CHECK":
                action_counts["CHECK"] += 1
            elif raw_a == "FOLD":
                action_counts["FOLD"] += 1

            if st != "PREFLOP":
                postflop_total_act += 1
                if raw_a in ("BET", "RAISE", "ALL_IN", "ALLIN"):
                    postflop_aggr += 1
                elif raw_a == "CALL":
                    postflop_calls += 1
                elif raw_a == "FOLD":
                    postflop_folds += 1
                elif raw_a == "CHECK":
                    postflop_checks += 1

        total_decisions = len(decisions)
        total_aggr_actions = action_counts["BET"] + action_counts["RAISE"]
        total_calls = action_counts["CALL"]
        total_non_check_actions = total_aggr_actions + total_calls + action_counts["FOLD"]
        postflop_non_check_actions = postflop_aggr + postflop_calls + postflop_folds

        # Aggression Factor y Aggression Frequency estándar (Postflop, con fallback documentado si aún no hay postflop)
        af_num = postflop_aggr if postflop_total_act > 0 else total_aggr_actions
        af_den = postflop_calls if postflop_total_act > 0 else total_calls
        af_sample = postflop_aggr + postflop_calls if postflop_total_act > 0 else (total_aggr_actions + total_calls)
        aggression_factor = round(af_num / max(1, af_den), 2) if af_sample > 0 else 0.0
        postflop_af = round(postflop_aggr / max(1, postflop_calls), 2) if (postflop_aggr + postflop_calls) > 0 else 0.0

        afq_num = postflop_aggr if postflop_non_check_actions > 0 else total_aggr_actions
        afq_den = postflop_non_check_actions if postflop_non_check_actions > 0 else total_non_check_actions
        aggression_frequency_pct = round((afq_num / max(1, afq_den)) * 100.0, 2) if afq_den > 0 else 0.0

        vpip_pct = round((vpip_count / n_hands) * 100.0, 2) if n_hands > 0 else 0.0
        pfr_pct = round((pfr_count / n_hands) * 100.0, 2) if n_hands > 0 else 0.0
        three_bet_pct = round((three_bet_count / max(1, three_bet_opps)) * 100.0, 2) if three_bet_opps > 0 else 0.0
        fold_to_3bet_pct = round((fold_to_3bet_count / max(1, faced_3bet_count)) * 100.0, 2) if faced_3bet_count > 0 else 0.0
        wtsd_pct = round((wtsd_count / max(1, saw_flop_count)) * 100.0, 2) if saw_flop_count > 0 else 0.0
        wsd_pct = round((wsd_count / max(1, wtsd_count)) * 100.0, 2) if wtsd_count > 0 else 0.0
        wwsf_pct = round((wwsf_count / max(1, saw_flop_count)) * 100.0, 2) if saw_flop_count > 0 else 0.0
        cbet_flop_pct = round((cbet_count / max(1, cbet_opps)) * 100.0, 2) if cbet_opps > 0 else 0.0
        fold_to_cbet_pct = round((fold_to_cbet_count / max(1, faced_cbet_count)) * 100.0, 2) if faced_cbet_count > 0 else 0.0

        # Tabla documentada de definiciones HUD (Fórmula, Numerador, Denominador, Muestra e IC 95%)
        hud_definitions_table = [
            {
                "metric_id": "VPIP",
                "name": "VPIP (Voluntarily Put Money In Pot)",
                "value": vpip_pct,
                "unit": "%",
                "numerator": vpip_count,
                "denominator": n_hands,
                "sample_size": n_hands,
                "ci95": list(wilson_confidence_interval(vpip_count, n_hands)),
                "formula": "(Manos con Call/Bet/Raise voluntario preflop / Total manos jugadas) × 100",
                "definition": "Porcentaje de manos en las que el bot invierte fichas voluntariamente preflop (excluyendo ciegas obligatorias).",
                "optimal_range_3max": "28% - 42%",
            },
            {
                "metric_id": "PFR",
                "name": "PFR (Preflop Raise)",
                "value": pfr_pct,
                "unit": "%",
                "numerator": pfr_count,
                "denominator": n_hands,
                "sample_size": n_hands,
                "ci95": list(wilson_confidence_interval(pfr_count, n_hands)),
                "formula": "(Manos con subida/all-in preflop / Total manos jugadas) × 100",
                "definition": "Porcentaje de manos en las que el bot entra o continúa preflop mediante una acción agresiva (Raise / 3-Bet / All-in).",
                "optimal_range_3max": "22% - 34%",
            },
            {
                "metric_id": "3BET",
                "name": "3-Bet Preflop",
                "value": three_bet_pct,
                "unit": "%",
                "numerator": three_bet_count,
                "denominator": three_bet_opps,
                "sample_size": three_bet_opps,
                "ci95": list(wilson_confidence_interval(three_bet_count, three_bet_opps)),
                "formula": "(Manos con re-subida preflop / Oportunidades de 3-Bet ante Open-Raise rival) × 100",
                "definition": "Frecuencia con la que el bot re-sube preflop cuando enfrenta una subida previa del oponente.",
                "optimal_range_3max": "9% - 16%",
            },
            {
                "metric_id": "FOLD_TO_3BET",
                "name": "Fold to 3-Bet",
                "value": fold_to_3bet_pct,
                "unit": "%",
                "numerator": fold_to_3bet_count,
                "denominator": faced_3bet_count,
                "sample_size": faced_3bet_count,
                "ci95": list(wilson_confidence_interval(fold_to_3bet_count, faced_3bet_count)),
                "formula": "(Folds ante 3-Bet / Veces que el bot enfrentó un 3-Bet) × 100",
                "definition": "Porcentaje de veces que el bot abandona tras enfrentar una re-subida preflop.",
                "optimal_range_3max": "40% - 58%",
            },
            {
                "metric_id": "AF",
                "name": "Aggression Factor (AF Postflop)",
                "value": aggression_factor,
                "unit": "ratio",
                "numerator": af_num,
                "denominator": af_den,
                "sample_size": af_sample,
                "ci95": [aggression_factor, aggression_factor],
                "formula": "(Apuestas + Subidas Postflop) / max(1, Calls Postflop)",
                "definition": "Razón entre acciones agresivas (Bet + Raise) y acciones pasivas pagadas (Call) postflop.",
                "optimal_range_3max": "1.80 - 3.20",
            },
            {
                "metric_id": "AFQ",
                "name": "Aggression Frequency (AFq%)",
                "value": aggression_frequency_pct,
                "unit": "%",
                "numerator": afq_num,
                "denominator": afq_den,
                "sample_size": afq_den,
                "ci95": list(wilson_confidence_interval(afq_num, afq_den)),
                "formula": "((Bets + Raises) / (Bets + Raises + Calls + Folds)) × 100",
                "definition": "Proporción de decisiones no-check en las que el bot toma la iniciativa apostando o subiendo.",
                "optimal_range_3max": "45% - 62%",
            },
            {
                "metric_id": "WTSD",
                "name": "WTSD (Went to Showdown)",
                "value": wtsd_pct,
                "unit": "%",
                "numerator": wtsd_count,
                "denominator": saw_flop_count,
                "sample_size": saw_flop_count,
                "ci95": list(wilson_confidence_interval(wtsd_count, saw_flop_count)),
                "formula": "(Manos que llegaron al Showdown / Manos que vieron el Flop) × 100",
                "definition": "Porcentaje de veces que el bot llega hasta el Showdown tras haber visto el Flop.",
                "optimal_range_3max": "26% - 36%",
            },
            {
                "metric_id": "WSD",
                "name": "W$SD (Won Money at Showdown)",
                "value": wsd_pct,
                "unit": "%",
                "numerator": wsd_count,
                "denominator": wtsd_count,
                "sample_size": wtsd_count,
                "ci95": list(wilson_confidence_interval(wsd_count, wtsd_count)),
                "formula": "(Manos ganadas en el Showdown / Total de Showdowns jugados) × 100",
                "definition": "Efectividad del bot al mostrar cartas en el Showdown.",
                "optimal_range_3max": "51% - 62%",
            },
            {
                "metric_id": "WWSF",
                "name": "WWSF (Won When Saw Flop)",
                "value": wwsf_pct,
                "unit": "%",
                "numerator": wwsf_count,
                "denominator": saw_flop_count,
                "sample_size": saw_flop_count,
                "ci95": list(wilson_confidence_interval(wwsf_count, saw_flop_count)),
                "formula": "(Manos ganadas habiendo visto el Flop / Total manos que vieron el Flop) × 100",
                "definition": "Capacidad de lucha por los botes postflop (tanto por derribo como en showdown).",
                "optimal_range_3max": "46% - 56%",
            },
            {
                "metric_id": "CBET_FLOP",
                "name": "CBet Flop (Continuation Bet)",
                "value": cbet_flop_pct,
                "unit": "%",
                "numerator": cbet_count,
                "denominator": cbet_opps,
                "sample_size": cbet_opps,
                "ci95": list(wilson_confidence_interval(cbet_count, cbet_opps)),
                "formula": "(Apuestas de continuación en Flop / Oportunidades de CBet como agresor preflop) × 100",
                "definition": "Frecuencia con la que el bot mantiene la iniciativa apostando en el Flop tras haber subido preflop.",
                "optimal_range_3max": "55% - 75%",
            },
            {
                "metric_id": "FOLD_TO_CBET",
                "name": "Fold to CBet Flop",
                "value": fold_to_cbet_pct,
                "unit": "%",
                "numerator": fold_to_cbet_count,
                "denominator": faced_cbet_count,
                "sample_size": faced_cbet_count,
                "ci95": list(wilson_confidence_interval(fold_to_cbet_count, faced_cbet_count)),
                "formula": "(Folds ante CBet en Flop / Veces que enfrentó CBet rival en Flop) × 100",
                "definition": "Frecuencia de abandono en el Flop frente a una apuesta de continuación del rival.",
                "optimal_range_3max": "35% - 52%",
            },
        ]

        # Serie de evolución de los indicadores HUD por bloques de manos (Fase 4: Gráfica Evolución HUD)
        hud_evolution: List[Dict[str, Any]] = []
        hud_block_size = max(10, len(hands) // 12) if len(hands) >= 20 else max(1, len(hands))
        if hands and hud_block_size > 0:
            for i in range(0, len(hands), hud_block_size):
                h_chunk = hands[i: i + hud_block_size]
                if not h_chunk:
                    continue
                cn = len(h_chunk)
                c_saw = sum(1 for h in h_chunk if h.get("saw_flop"))
                c_wtsd = sum(1 for h in h_chunk if h.get("saw_flop") and h.get("went_to_showdown"))
                c_wsd = sum(1 for h in h_chunk if h.get("went_to_showdown") and h.get("won_at_showdown"))
                c_cb_opp = sum(1 for h in h_chunk if h.get("cbet_flop_opp"))
                c_cb = sum(1 for h in h_chunk if h.get("cbet_flop_flag"))
                c_3b_opp = sum(1 for h in h_chunk if h.get("three_bet_opp"))
                c_3b = sum(1 for h in h_chunk if h.get("three_bet_flag"))
                hud_evolution.append({
                    "block_index": (i // hud_block_size) + 1,
                    "hand_range": f"{i + 1}-{i + cn}",
                    "version_id": h_chunk[-1].get("version_id"),
                    "hands_in_block": cn,
                    "vpip_pct": round((sum(1 for h in h_chunk if h.get("vpip_flag")) / cn) * 100.0, 1),
                    "pfr_pct": round((sum(1 for h in h_chunk if h.get("pfr_flag")) / cn) * 100.0, 1),
                    "three_bet_pct": round((c_3b / max(1, c_3b_opp)) * 100.0, 1) if c_3b_opp > 0 else 0.0,
                    "cbet_flop_pct": round((c_cb / max(1, c_cb_opp)) * 100.0, 1) if c_cb_opp > 0 else 0.0,
                    "wtsd_pct": round((c_wtsd / max(1, c_saw)) * 100.0, 1) if c_saw > 0 else 0.0,
                    "wsd_pct": round((c_wsd / max(1, c_wtsd)) * 100.0, 1) if c_wtsd > 0 else 0.0,
                })

        hud_stats = {
            "vpip_pct": vpip_pct,
            "pfr_pct": pfr_pct,
            "vpip_pfr_gap_pct": round(vpip_pct - pfr_pct, 2),
            "three_bet_pct": three_bet_pct,
            "fold_to_three_bet_pct": fold_to_3bet_pct,
            "aggression_factor": aggression_factor,
            "postflop_aggression_factor": postflop_af,
            "aggression_frequency_pct": aggression_frequency_pct,
            "wtsd_pct": wtsd_pct,
            "wsd_pct": wsd_pct,
            "wwsf_pct": wwsf_pct,
            "cbet_flop_pct": cbet_flop_pct,
            "fold_to_cbet_pct": fold_to_cbet_pct,
            "definitions_table": hud_definitions_table,
            "hud_evolution": hud_evolution,
            "counts": {
                "hands": n_hands,
                "vpip": vpip_count,
                "pfr": pfr_count,
                "three_bet_opps": three_bet_opps,
                "three_bet": three_bet_count,
                "faced_3bet": faced_3bet_count,
                "fold_to_3bet": fold_to_3bet_count,
                "saw_flop": saw_flop_count,
                "wtsd": wtsd_count,
                "wsd": wsd_count,
                "wwsf": wwsf_count,
                "cbet_opps": cbet_opps,
                "cbet": cbet_count,
                "faced_cbet": faced_cbet_count,
                "fold_to_cbet": fold_to_cbet_count,
                "postflop_aggr": postflop_aggr,
                "postflop_calls": postflop_calls,
                "postflop_non_check": postflop_non_check_actions,
            },
        }

        # ------------------------------------------------------------------
        # C & D. CALIDAD ESTRATÉGICA Y EVALUACIÓN GTO RIGUROSA (FASE 2.C & 2.D)
        # ------------------------------------------------------------------
        evaluable_decisions = [
            d for d in decisions
            if d.get("evaluation_available", 1) != 0 and d.get("decision_quality") != "EVALUACION_NO_DISPONIBLE"
        ]
        unavailable_decisions_count = total_decisions - len(evaluable_decisions)
        eval_denom = max(1, len(evaluable_decisions))

        gto_aligned_count = sum(1 for d in evaluable_decisions if d.get("is_gto_aligned"))
        gto_alignment_pct = round((gto_aligned_count / eval_denom) * 100.0, 2) if evaluable_decisions else 0.0
        gto_ci_low, gto_ci_high = wilson_confidence_interval(gto_aligned_count, len(evaluable_decisions))

        exact_gto_match_count = sum(1 for d in evaluable_decisions if d.get("action_deviation") == "EXACT_MATCH")
        exact_gto_match_pct = round((exact_gto_match_count / eval_denom) * 100.0, 2) if evaluable_decisions else 0.0

        strategic_errors = [d for d in evaluable_decisions if d.get("is_strategic_error")]
        strategic_errors_count = len(strategic_errors)
        strategic_error_rate_pct = (
            round((strategic_errors_count / eval_denom) * 100.0, 2) if evaluable_decisions else 0.0
        )

        # Oportunidades perdidas (Missed Value Opportunities)
        missed_opps = [
            d for d in evaluable_decisions
            if d.get("is_missed_opportunity") or (
                "PASSIVE_DEVIATION" in str(d.get("action_deviation") or "")
                and float(d.get("estimated_equity") or 0.0) >= 0.62
                and float(d.get("ev_lost_bb") or 0.0) >= 0.60
            )
        ]
        missed_opportunities_count = len(missed_opps)
        missed_opportunities_rate_pct = round((missed_opportunities_count / eval_denom) * 100.0, 2) if evaluable_decisions else 0.0
        missed_opportunities_ev_lost_bb = round(sum(float(d.get("ev_lost_bb") or 0.0) for d in missed_opps), 2)

        total_ev_lost_chips = round(sum(float(d.get("ev_lost_chips") or 0.0) for d in evaluable_decisions), 2)
        total_ev_lost_bb = round(sum(float(d.get("ev_lost_bb") or 0.0) for d in evaluable_decisions), 2)
        ev_lost_per_100_hands_bb = round((total_ev_lost_bb / n_hands) * 100.0, 2) if n_hands > 0 else 0.0
        avg_ev_lost_per_decision_bb = round(total_ev_lost_bb / eval_denom, 3) if evaluable_decisions else 0.0

        deviation_counts: Dict[str, int] = {
            "EXACT_MATCH": 0,
            "EXPLOITATIVE_ADAPTATION": 0,
            "PASSIVE_DEVIATION": 0,
            "AGGRESSIVE_DEVIATION": 0,
            "OVERFOLD": 0,
            "OTHER_DEVIATION": 0,
            "EVALUACION_NO_DISPONIBLE": unavailable_decisions_count,
        }
        quality_counts: Dict[str, int] = {
            "OPTIMA": 0,
            "ADAPTACION_EXPLOTATIVA": 0,
            "ACEPTABLE": 0,
            "INACCURACY": 0,
            "ERROR_ESTRATEGICO": 0,
            "BLUNDER_EV": 0,
            "EVALUACION_NO_DISPONIBLE": unavailable_decisions_count,
        }

        # Evaluación de frecuencias de estrategias mixtas (70% / 20% / 10%) y subjuego Push/Fold
        mixed_spot_counts = {"PRIMARY_70": 0, "SECONDARY_20": 0, "ALTERNATIVE_10": 0, "OUT_OF_SUPPORT": 0}
        push_fold_decisions = 0
        push_fold_nash_aligned = 0
        push_fold_regret_bb = 0.0

        for d in evaluable_decisions:
            dev = str(d.get("action_deviation") or "EXACT_MATCH")
            qual = str(d.get("decision_quality") or "OPTIMA")
            quality_counts[qual] = quality_counts.get(qual, 0) + 1

            if dev == "EXACT_MATCH":
                deviation_counts["EXACT_MATCH"] += 1
                mixed_spot_counts["PRIMARY_70"] += 1
            elif "EXPLOITATIVE" in dev:
                deviation_counts["EXPLOITATIVE_ADAPTATION"] += 1
                mixed_spot_counts["SECONDARY_20"] += 1
            elif "PASSIVE" in dev:
                deviation_counts["PASSIVE_DEVIATION"] += 1
                if float(d.get("ev_lost_bb") or 0.0) <= 1.1:
                    mixed_spot_counts["SECONDARY_20"] += 1
                else:
                    mixed_spot_counts["OUT_OF_SUPPORT"] += 1
            elif "AGGRESSIVE" in dev:
                deviation_counts["AGGRESSIVE_DEVIATION"] += 1
                if float(d.get("ev_lost_bb") or 0.0) <= 1.1:
                    mixed_spot_counts["ALTERNATIVE_10"] += 1
                else:
                    mixed_spot_counts["OUT_OF_SUPPORT"] += 1
            elif "OVERFOLD" in dev:
                deviation_counts["OVERFOLD"] += 1
                mixed_spot_counts["OUT_OF_SUPPORT"] += 1
            else:
                deviation_counts["OTHER_DEVIATION"] += 1
                mixed_spot_counts["ALTERNATIVE_10"] += 1

            eff_s = float(d.get("effective_stack") or 500.0)
            bb_d = max(1.0, float(d.get("big_blind") or 20.0))
            if eff_s <= 10.0 * bb_d:
                push_fold_decisions += 1
                if d.get("is_gto_aligned"):
                    push_fold_nash_aligned += 1
                push_fold_regret_bb += float(d.get("ev_lost_bb") or 0.0)

        obs_primary_pct = round((mixed_spot_counts["PRIMARY_70"] / eval_denom) * 100.0, 1) if evaluable_decisions else 0.0
        obs_secondary_pct = round((mixed_spot_counts["SECONDARY_20"] / eval_denom) * 100.0, 1) if evaluable_decisions else 0.0
        obs_alt_pct = round((mixed_spot_counts["ALTERNATIVE_10"] / eval_denom) * 100.0, 1) if evaluable_decisions else 0.0
        obs_out_pct = round((mixed_spot_counts["OUT_OF_SUPPORT"] / eval_denom) * 100.0, 1) if evaluable_decisions else 0.0
        in_support_pct = round(100.0 - obs_out_pct, 1) if evaluable_decisions else 0.0
        tvd_mixed_pct = round(
            0.5 * (abs(obs_primary_pct - 70.0) + abs(obs_secondary_pct - 20.0) + abs(obs_alt_pct - 10.0)),
            2,
        ) if evaluable_decisions else 0.0

        # Evaluación GTO (Fase 2.D): diferenciación rigurosa entre heurística, EV, estrategia mixta, Regret y Exploitability
        gto_evaluation = {
            "methodology_disclaimer": (
                "El motor `poker_gto_engine.py` implementa una línea base Heurística-GTO fundamentada en equidad de rangos, "
                "Pot Odds, Minimum Defense Frequency (MDF) y tablas Push/Fold de Jennings-Sklansky-Chubukov (<10 BB). "
                "NO constituye un solver CFR de árbol completo postflop; por tanto, las métricas distinguen explícitamente "
                "entre coincidencia heurística, pérdida de EV estimada (Regret) y exploitability analítica en subjuegos Push/Fold."
            ),
            "heuristic_gto_match_pct": exact_gto_match_pct,
            "ev_acceptable_alignment_pct": gto_alignment_pct,
            "ev_acceptable_alignment_ci95": [gto_ci_low, gto_ci_high],
            "mixed_strategy_evaluation": {
                "target_distribution_pct": {"primary": 70.0, "secondary": 20.0, "alternative": 10.0},
                "observed_distribution_pct": {
                    "primary": obs_primary_pct,
                    "secondary": obs_secondary_pct,
                    "alternative": obs_alt_pct,
                    "out_of_support": obs_out_pct,
                },
                "in_mixed_support_pct": in_support_pct,
                "total_variation_distance_pct": tvd_mixed_pct,
            },
            "regret_analysis": {
                "total_regret_bb": total_ev_lost_bb,
                "total_regret_chips": total_ev_lost_chips,
                "regret_bb_per_100_hands": ev_lost_per_100_hands_bb,
                "avg_regret_bb_per_decision": avg_ev_lost_per_decision_bb,
            },
            "exploitability_analysis": {
                "full_game_nash_exploitability_status": "NO_DISPONIBLE_SIN_SOLVER_CFR",
                "full_game_note": (
                    "El cálculo exacto de Exploitability de Nash (mbb/g en árbol completo) requiere resolver estrategias "
                    "mediante Counterfactual Regret Minimization (CFR+) sobre todo el árbol de juego. Se reporta únicamente "
                    "la cota verificable en el subjuego Push/Fold (≤ 10 BB)."
                ),
                "push_fold_subgame_sample": push_fold_decisions,
                "push_fold_nash_compliance_pct": (
                    round((push_fold_nash_aligned / push_fold_decisions) * 100.0, 2)
                    if push_fold_decisions > 0
                    else None
                ),
                "push_fold_estimated_exploitability_bb100": (
                    round((push_fold_regret_bb / push_fold_decisions) * 100.0, 2)
                    if push_fold_decisions > 0
                    else None
                ),
            },
        }

        action_distribution_pct = {
            k: round((v / total_decisions) * 100.0, 2) if total_decisions > 0 else 0.0
            for k, v in action_counts.items()
        }

        sorted_by_ev_loss = sorted(
            evaluable_decisions,
            key=lambda x: (float(x.get("ev_lost_bb") or 0.0), float(x.get("ev_lost_chips") or 0.0)),
            reverse=True,
        )
        top_ev_loss_decisions = sorted_by_ev_loss[:15]

        # Rendimiento por Posición (BTN, SB, BB)
        positions_order = ["BTN", "SB", "BB"]
        performance_by_position: Dict[str, Dict[str, Any]] = {}
        for pos in positions_order:
            pos_hands = [h for h in hands if self._normalize_pos(h.get("position")) == pos]
            pos_decs = [d for d in evaluable_decisions if self._normalize_pos(d.get("position")) == pos]
            nh = len(pos_hands)
            nd = len(pos_decs)
            pos_net_bb = sum(float(h.get("net_profit_bb") or 0.0) for h in pos_hands)
            pos_ev_bb = sum(float(h.get("ev_net_bb") or 0.0) for h in pos_hands)
            pos_won = sum(1 for h in pos_hands if h.get("won_hand") or float(h.get("net_profit_chips") or 0) > 0)
            pos_vpip = sum(1 for h in pos_hands if h.get("vpip_flag"))
            pos_pfr = sum(1 for h in pos_hands if h.get("pfr_flag"))
            pos_gto = sum(1 for d in pos_decs if d.get("is_gto_aligned"))
            pos_ev_lost = sum(float(d.get("ev_lost_bb") or 0.0) for d in pos_decs)

            performance_by_position[pos] = {
                "position": pos,
                "hands": nh,
                "decisions": nd,
                "win_pct": round((pos_won / nh) * 100.0, 2) if nh > 0 else 0.0,
                "vpip_pct": round((pos_vpip / nh) * 100.0, 2) if nh > 0 else 0.0,
                "pfr_pct": round((pos_pfr / nh) * 100.0, 2) if nh > 0 else 0.0,
                "net_profit_chips": round(sum(float(h.get("net_profit_chips") or 0.0) for h in pos_hands), 2),
                "net_profit_bb": round(pos_net_bb, 2),
                "bb_per_100": round((pos_net_bb / nh) * 100.0, 2) if nh > 0 else 0.0,
                "ev_bb_per_100": round((pos_ev_bb / nh) * 100.0, 2) if nh > 0 else 0.0,
                "gto_alignment_pct": round((pos_gto / nd) * 100.0, 2) if nd > 0 else 0.0,
                "ev_lost_bb": round(pos_ev_lost, 2),
            }

        # Rendimiento por Calle (PREFLOP, FLOP, TURN, RIVER)
        streets_order = ["PREFLOP", "FLOP", "TURN", "RIVER"]
        performance_by_street: Dict[str, Dict[str, Any]] = {}
        for st in streets_order:
            st_decs = [d for d in evaluable_decisions if (d.get("street") or "").upper() == st]
            st_hands = [h for h in hands if (h.get("reached_street") or "").upper() == st]
            nd = len(st_decs)
            nh = len(st_hands)
            st_gto = sum(1 for d in st_decs if d.get("is_gto_aligned"))
            st_err = sum(1 for d in st_decs if d.get("is_strategic_error"))
            st_ev_lost = sum(float(d.get("ev_lost_bb") or 0.0) for d in st_decs)
            st_net_bb = sum(float(h.get("net_profit_bb") or 0.0) for h in st_hands)
            st_ev_bb = sum(float(h.get("ev_net_bb") or 0.0) for h in st_hands)

            st_act_counts = {"FOLD": 0, "CHECK": 0, "CALL": 0, "BET": 0, "RAISE": 0}
            for d in st_decs:
                ra = (d.get("action_taken") or "").upper()
                tc = float(d.get("to_call") or 0.0)
                if ra in ("ALL_IN", "ALLIN", "RAISE"):
                    st_act_counts["BET" if (tc == 0 and st != "PREFLOP") else "RAISE"] += 1
                elif ra in st_act_counts:
                    st_act_counts[ra] += 1

            performance_by_street[st] = {
                "street": st,
                "decisions": nd,
                "hands_ended_here": nh,
                "gto_alignment_pct": round((st_gto / nd) * 100.0, 2) if nd > 0 else 0.0,
                "strategic_errors": st_err,
                "error_rate_pct": round((st_err / nd) * 100.0, 2) if nd > 0 else 0.0,
                "ev_lost_bb": round(st_ev_lost, 2),
                "avg_ev_lost_bb": round(st_ev_lost / nd, 3) if nd > 0 else 0.0,
                "net_profit_bb": round(st_net_bb, 2),
                "ev_net_bb": round(st_ev_bb, 2),
                "action_counts": st_act_counts,
            }

        # Rendimiento por Tamaño de Bote (Fase 2.C: SMALL <4 BB, MEDIUM 4-12 BB, LARGE >12 BB)
        pot_buckets_order = ["SMALL (<4 BB)", "MEDIUM (4-12 BB)", "LARGE (>12 BB)"]
        performance_by_pot_size: Dict[str, Dict[str, Any]] = {}
        for pb in pot_buckets_order:
            pb_hands = [
                h for h in hands
                if self._bucket_pot_size(float(h.get("pot_final") or 0.0), float(h.get("big_blind") or 20.0)) == pb
            ]
            pb_decs = [
                d for d in evaluable_decisions
                if (d.get("pot_size_bucket") or self._bucket_pot_size(float(d.get("pot_size") or 0.0), float(d.get("big_blind") or 20.0))) == pb
            ]
            nh, nd = len(pb_hands), len(pb_decs)
            pb_net_bb = sum(float(h.get("net_profit_bb") or 0.0) for h in pb_hands)
            pb_ev_bb = sum(float(h.get("ev_net_bb") or 0.0) for h in pb_hands)
            pb_gto = sum(1 for d in pb_decs if d.get("is_gto_aligned"))
            pb_err = sum(1 for d in pb_decs if d.get("is_strategic_error"))
            pb_ev_lost = sum(float(d.get("ev_lost_bb") or 0.0) for d in pb_decs)

            performance_by_pot_size[pb] = {
                "bucket": pb,
                "hands": nh,
                "decisions": nd,
                "net_profit_bb": round(pb_net_bb, 2),
                "bb_per_100": round((pb_net_bb / nh) * 100.0, 2) if nh > 0 else 0.0,
                "ev_bb_per_100": round((pb_ev_bb / nh) * 100.0, 2) if nh > 0 else 0.0,
                "gto_alignment_pct": round((pb_gto / nd) * 100.0, 2) if nd > 0 else 0.0,
                "error_rate_pct": round((pb_err / nd) * 100.0, 2) if nd > 0 else 0.0,
                "ev_lost_bb": round(pb_ev_lost, 2),
            }

        # Rendimiento por Profundidad de Stack (Fase 2.C: SHORT <10 BB, MEDIUM 10-20 BB, DEEP >20 BB)
        stack_buckets_order = ["SHORT (<10 BB)", "MEDIUM (10-20 BB)", "DEEP (>20 BB)"]
        performance_by_stack_depth: Dict[str, Dict[str, Any]] = {}
        for sb in stack_buckets_order:
            sb_hands = [
                h for h in hands
                if self._bucket_stack_depth(float(h.get("effective_stack") or 500.0), float(h.get("big_blind") or 20.0)) == sb
            ]
            sb_decs = [
                d for d in evaluable_decisions
                if (d.get("stack_depth_bucket") or self._bucket_stack_depth(float(d.get("effective_stack") or 500.0), float(d.get("big_blind") or 20.0))) == sb
            ]
            nh, nd = len(sb_hands), len(sb_decs)
            sb_net_bb = sum(float(h.get("net_profit_bb") or 0.0) for h in sb_hands)
            sb_ev_bb = sum(float(h.get("ev_net_bb") or 0.0) for h in sb_hands)
            sb_gto = sum(1 for d in sb_decs if d.get("is_gto_aligned"))
            sb_err = sum(1 for d in sb_decs if d.get("is_strategic_error"))
            sb_ev_lost = sum(float(d.get("ev_lost_bb") or 0.0) for d in sb_decs)

            performance_by_stack_depth[sb] = {
                "bucket": sb,
                "hands": nh,
                "decisions": nd,
                "net_profit_bb": round(sb_net_bb, 2),
                "bb_per_100": round((sb_net_bb / nh) * 100.0, 2) if nh > 0 else 0.0,
                "ev_bb_per_100": round((sb_ev_bb / nh) * 100.0, 2) if nh > 0 else 0.0,
                "gto_alignment_pct": round((sb_gto / nd) * 100.0, 2) if nd > 0 else 0.0,
                "error_rate_pct": round((sb_err / nd) * 100.0, 2) if nd > 0 else 0.0,
                "ev_lost_bb": round(sb_ev_lost, 2),
            }

        # Evolución de la Calidad Estratégica
        quality_evolution: List[Dict[str, Any]] = []
        chunk_size = max(10, len(evaluable_decisions) // 12) if len(evaluable_decisions) >= 20 else max(1, len(evaluable_decisions))
        if evaluable_decisions and chunk_size > 0:
            for i in range(0, len(evaluable_decisions), chunk_size):
                chunk = evaluable_decisions[i: i + chunk_size]
                if not chunk:
                    continue
                c_n = len(chunk)
                c_gto = sum(1 for d in chunk if d.get("is_gto_aligned"))
                c_err = sum(1 for d in chunk if d.get("is_strategic_error"))
                c_adapt = sum(1 for d in chunk if d.get("opponent_adaptation_aligned"))
                c_ev_lost = sum(float(d.get("ev_lost_bb") or 0.0) for d in chunk)
                quality_evolution.append({
                    "block_index": (i // chunk_size) + 1,
                    "decision_range": f"{i + 1}-{i + c_n}",
                    "version_id": chunk[-1].get("version_id"),
                    "gto_alignment_pct": round((c_gto / c_n) * 100.0, 2),
                    "error_rate_pct": round((c_err / c_n) * 100.0, 2),
                    "adaptation_pct": round((c_adapt / c_n) * 100.0, 2),
                    "avg_ev_lost_bb": round(c_ev_lost / c_n, 3),
                })

        # ------------------------------------------------------------------
        # 4. ANÁLISIS DE OPONENTES
        # ------------------------------------------------------------------
        unique_opps_in_hands = {
            h.get("opponent_id") or h.get("opponent_nick")
            for h in hands
            if (h.get("opponent_id") or h.get("opponent_nick"))
        }
        total_opps_analyzed = max(len(oponentes_db), len(unique_opps_in_hands))

        hands_with_detected_pattern = sum(
            1
            for h in hands
            if (h.get("opponent_profile") or "").upper()
            not in ("", "DESCONOCIDO", "JUGADOR NUEVO (SIN HISTORIAL)", "JUGADOR NUEVO (POCO HISTORIAL)", "JUGADOR NUEVO")
        )
        pattern_detection_freq_pct = (
            round((hands_with_detected_pattern / n_hands) * 100.0, 2) if n_hands > 0 else 0.0
        )

        verif_total = len(verificaciones)
        verif_ok = sum(1 for v in verificaciones if v.get("is_pattern_verified"))
        pattern_accuracy_pct = round((verif_ok / verif_total) * 100.0, 2) if verif_total > 0 else 0.0

        adapt_aligned_count = sum(1 for d in evaluable_decisions if d.get("opponent_adaptation_aligned"))
        adaptation_capacity_pct = (
            round((adapt_aligned_count / eval_denom) * 100.0, 2) if evaluable_decisions else 0.0
        )

        profiles_order = [
            "CALLING STATION",
            "MANIAC",
            "NIT",
            "REGULAR EQUILIBRADO",
            "JUGADOR NUEVO",
        ]
        performance_vs_profiles: Dict[str, Dict[str, Any]] = {}
        for prof_key in profiles_order:
            prof_hands = [
                h for h in hands if self._normalize_profile(h.get("opponent_profile")) == prof_key
            ]
            prof_decs = [
                d for d in evaluable_decisions if self._normalize_profile(d.get("opponent_profile")) == prof_key
            ]
            nh = len(prof_hands)
            nd = len(prof_decs)
            p_won = sum(1 for h in prof_hands if h.get("won_hand") or float(h.get("net_profit_chips") or 0) > 0)
            p_net_bb = sum(float(h.get("net_profit_bb") or 0.0) for h in prof_hands)
            p_ev_bb = sum(float(h.get("ev_net_bb") or 0.0) for h in prof_hands)
            p_adapt = sum(1 for d in prof_decs if d.get("opponent_adaptation_aligned"))
            p_gto = sum(1 for d in prof_decs if d.get("is_gto_aligned"))
            p_err = sum(1 for d in prof_decs if d.get("is_strategic_error"))

            performance_vs_profiles[prof_key] = {
                "profile": prof_key,
                "hands": nh,
                "decisions": nd,
                "win_pct": round((p_won / nh) * 100.0, 2) if nh > 0 else 0.0,
                "net_profit_chips": round(sum(float(h.get("net_profit_chips") or 0.0) for h in prof_hands), 2),
                "net_profit_bb": round(p_net_bb, 2),
                "bb_per_100": round((p_net_bb / nh) * 100.0, 2) if nh > 0 else 0.0,
                "ev_bb_per_100": round((p_ev_bb / nh) * 100.0, 2) if nh > 0 else 0.0,
                "adaptation_pct": round((p_adapt / nd) * 100.0, 2) if nd > 0 else 0.0,
                "gto_alignment_pct": round((p_gto / nd) * 100.0, 2) if nd > 0 else 0.0,
                "error_rate_pct": round((p_err / nd) * 100.0, 2) if nd > 0 else 0.0,
            }

        # ------------------------------------------------------------------
        # 5. EVALUACIÓN ESTADÍSTICA, VARIANZA, SHARPE Y ESTABILIDAD
        # ------------------------------------------------------------------
        stat_eval = self._build_statistical_evaluation(
            n_hands=n_hands,
            wr_stats=wr_stats,
            ev_wr_stats=ev_wr_stats,
            allin_wr_stats=allin_wr_stats,
            net_profit_bb=net_profit_bb,
            ev_net_bb=ev_net_bb,
            allin_ev_bb=allin_ev_bb,
            max_drawdown_bb=max_drawdown_bb,
            max_drawdown_chips=max_drawdown_chips,
            bb_values=bb_values,
            ev_bb_values=ev_bb_values,
            gto_alignment_pct=gto_alignment_pct,
            gto_ci=(gto_ci_low, gto_ci_high),
        )

        # ------------------------------------------------------------------
        # 6. COMPARACIÓN CIENTÍFICA ENTRE VERSIONES (FASE 3)
        # ------------------------------------------------------------------
        version_comparison = self.comparar_versiones()

        # ------------------------------------------------------------------
        # 7. TABLA PRINCIPAL ORDENABLE DE MÉTRICAS CON DEFINICIÓN CONTEXTUAL
        # ------------------------------------------------------------------
        main_metrics_table = self._construir_tabla_principal_metricas(
            n_hands=n_hands,
            n_sessions=n_sessions,
            hands_won=hands_won,
            hands_lost=hands_lost,
            win_pct=win_pct,
            win_ci=(win_ci_low, win_ci_high),
            net_profit_chips=net_profit_chips,
            net_profit_bb=net_profit_bb,
            ev_net_bb=ev_net_bb,
            allin_ev_bb=allin_ev_bb,
            roi_pct=roi_pct,
            roi_applicable=roi_applicable,
            investment_base=investment_base,
            wr_stats=wr_stats,
            ev_wr_stats=ev_wr_stats,
            allin_wr_stats=allin_wr_stats,
            avg_profit_chips=avg_profit_chips,
            avg_profit_bb=avg_profit_bb,
            max_drawdown_bb=max_drawdown_bb,
            max_drawdown_chips=max_drawdown_chips,
            stat_eval=stat_eval,
            hud_definitions_table=hud_definitions_table,
            total_decisions=total_decisions,
            evaluable_decisions_count=len(evaluable_decisions),
            gto_aligned_count=gto_aligned_count,
            gto_alignment_pct=gto_alignment_pct,
            gto_ci=(gto_ci_low, gto_ci_high),
            exact_gto_match_count=exact_gto_match_count,
            exact_gto_match_pct=exact_gto_match_pct,
            strategic_errors_count=strategic_errors_count,
            strategic_error_rate_pct=strategic_error_rate_pct,
            missed_opportunities_count=missed_opportunities_count,
            missed_opportunities_rate_pct=missed_opportunities_rate_pct,
            total_ev_lost_bb=total_ev_lost_bb,
            ev_lost_per_100_hands_bb=ev_lost_per_100_hands_bb,
            total_opps_analyzed=total_opps_analyzed,
            hands_with_detected_pattern=hands_with_detected_pattern,
            pattern_detection_freq_pct=pattern_detection_freq_pct,
            verif_ok=verif_ok,
            verif_total=verif_total,
            pattern_accuracy_pct=pattern_accuracy_pct,
            adapt_aligned_count=adapt_aligned_count,
            adaptation_capacity_pct=adaptation_capacity_pct,
        )

        # Medir el tiempo real de procesamiento estadístico en milisegundos (Fase 6)
        stat_processing_ms = round((time.perf_counter_ns() - t0_ns) / 1_000_000.0, 3)

        telemetry_rows = self.storage.consultar_telemetria(limit=500)
        db_integrity = self.storage.verificar_integridad_db()
        system_reliability = construir_reporte_confiabilidad_sistema(
            telemetry_rows=telemetry_rows,
            decisions=decisions,
            db_integrity=db_integrity,
            stat_processing_ms=stat_processing_ms,
        )

        sample_power_info = calcular_tamano_muestra_necesario(
            delta_bb100=config.DEFAULT_TARGET_DELTA_BB100,
            std_bb_per_hand=wr_stats["std_per_unit"] if wr_stats["std_per_unit"] > 0 else 15.0,
            alpha=config.ALPHA_SIGNIFICANCE,
            power=config.POWER_TARGET,
            paired=(live_hands_count == 0 and benchmark_hands_count > 0),
        )
        stat_eval["observed_metrics"]["n"] = n_hands
        stat_eval["observed_metrics"]["required_hands_for_10bb100"] = sample_power_info["required_hands"]

        illegal_corrected_count = sum(
            1
            for d in decisions
            if d.get("illegal_action_corrected")
            or d.get("action_was_corrected")
            or "ILEGAL" in str(d.get("correction_reason") or d.get("error_description") or d.get("notes") or d.get("error_type") or "").upper()
            or (
                bool(d.get("intended_action"))
                and bool(d.get("action_taken"))
                and str(d.get("intended_action")).upper() != str(d.get("action_taken")).upper()
                and "CORREG" in str(d.get("error_description") or d.get("correction_reason") or "").upper()
            )
        )
        illegal_corrected_rate_pct = (
            round((illegal_corrected_count / total_decisions) * 100.0, 2) if total_decisions > 0 else 0.0
        )
        dom_errors_count = sum(int(t.get("dom_errors") or 0) for t in telemetry_rows) + sum(
            1
            for d in decisions
            if d.get("dom_error")
            or "DOM" in str(d.get("error_type") or d.get("error_description") or "").upper()
        )
        experience_memory_hits = sum(
            int(d.get("experience_memory_hits") or d.get("memory_hits") or (1 if d.get("experience_memory_hit") else 0))
            for d in decisions
        )
        dec_lat = system_reliability.get("decision_latency", {})
        operational_kpis = {
            "n_hands": n_hands,
            "live_hands_count": live_hands_count,
            "benchmark_hands_count": benchmark_hands_count,
            "winrate_bb100": wr_stats["mean"],
            "winrate_ci95": [wr_stats["ci95_low"], wr_stats["ci95_high"]],
            "allin_ev_bb100": allin_wr_stats["mean"],
            "allin_ev_ci95": [allin_wr_stats["ci95_low"], allin_wr_stats["ci95_high"]],
            "hero_vpip_pct": hud_stats.get("vpip_pct", 0.0),
            "hero_pfr_pct": hud_stats.get("pfr_pct", 0.0),
            "illegal_actions_corrected_count": illegal_corrected_count,
            "illegal_actions_corrected_rate_pct": illegal_corrected_rate_pct,
            "avg_decision_latency_ms": float(dec_lat.get("mean_ms") or 0.0),
            "p95_decision_latency_ms": float(dec_lat.get("p95_ms") or 0.0),
            "dom_errors_count": dom_errors_count,
            "experience_memory_hits": experience_memory_hits,
            "required_hands_for_10bb100": sample_power_info["required_hands"],
            "min_live_hands_for_improvement": int(config.MIN_LIVE_HANDS_FOR_IMPROVEMENT),
        }

        result_payload = {
            "filters_applied": filters or {},
            "data_source_summary": {
                "live_hands_count": live_hands_count,
                "benchmark_hands_count": benchmark_hands_count,
                "is_exclusively_live": benchmark_hands_count == 0 and live_hands_count > 0,
                "is_exclusively_benchmark": live_hands_count == 0 and benchmark_hands_count > 0,
                "code_hash": self.storage.default_code_hash,
            },
            "general_performance": {
                "hands_played": n_hands,
                "live_hands_played": live_hands_count,
                "benchmark_hands_played": benchmark_hands_count,
                "sessions_count": n_sessions,
                "hands_won": hands_won,
                "hands_lost": hands_lost,
                "hands_pushed": hands_pushed,
                "win_pct": win_pct,
                "win_pct_ci95": [win_ci_low, win_ci_high],
                "gross_won_chips": gross_won_chips,
                "gross_lost_chips": gross_lost_chips,
                "gross_won_bb": gross_won_bb,
                "gross_lost_bb": gross_lost_bb,
                "net_profit_chips": net_profit_chips,
                "net_profit_bb": net_profit_bb,
                "ev_net_chips": ev_net_chips,
                "ev_net_bb": ev_net_bb,
                "allin_ev_chips": allin_ev_chips,
                "allin_ev_bb": allin_ev_bb,
                "allin_hands_count": allin_hands_count,
                "roi_pct": roi_pct,
                "ev_roi_pct": ev_roi_pct,
                "roi_applicable": roi_applicable,
                "investment_base_chips": round(investment_base, 2),
                "initial_bankroll_chips": initial_br,
                "final_bankroll_chips": round(cur_br, 2),
                "ev_final_bankroll_chips": round(cur_ev_br, 2),
                "bb_per_100": wr_stats["mean"],
                "bb_per_100_ci95": [wr_stats["ci95_low"], wr_stats["ci95_high"]],
                "ev_bb_per_100": ev_wr_stats["mean"],
                "ev_bb_per_100_ci95": [ev_wr_stats["ci95_low"], ev_wr_stats["ci95_high"]],
                "allin_ev_bb_per_100": allin_wr_stats["mean"],
                "allin_ev_bb_per_100_ci95": [allin_wr_stats["ci95_low"], allin_wr_stats["ci95_high"]],
                "avg_profit_per_hand_chips": avg_profit_chips,
                "avg_profit_per_hand_bb": avg_profit_bb,
                "std_bb_per_hand": wr_stats["std_per_unit"],
                "std_bb_per_100": round(wr_stats["std_per_unit"] * 10.0, 2),
                "variance_bb_per_hand": wr_stats["variance"],
                "variance_bb_per_100": round(wr_stats["variance"] * 100.0, 2),
                "max_drawdown_chips": round(max_drawdown_chips, 2),
                "max_drawdown_bb": round(max_drawdown_bb, 2),
                "sharpe_ratio_100": stat_eval["stability_indicators"]["sharpe_ratio_100"],
                "sharpe_assumptions_met": stat_eval["stability_indicators"]["sharpe_assumptions_met"],
                "sharpe_status_note": stat_eval["stability_indicators"]["sharpe_status_note"],
                "bankroll_curve": bankroll_curve,
            },
            "gameplay_hud": hud_stats,
            "strategic_analysis": {
                "total_decisions": total_decisions,
                "evaluable_decisions": len(evaluable_decisions),
                "unavailable_evaluations_count": unavailable_decisions_count,
                "gto_alignment_pct": gto_alignment_pct,
                "gto_alignment_ci95": [gto_ci_low, gto_ci_high],
                "exact_gto_match_pct": exact_gto_match_pct,
                "action_deviation_breakdown": deviation_counts,
                "decision_quality_breakdown": quality_counts,
                "strategic_errors_count": strategic_errors_count,
                "strategic_error_rate_pct": strategic_error_rate_pct,
                "missed_opportunities_count": missed_opportunities_count,
                "missed_opportunities_rate_pct": missed_opportunities_rate_pct,
                "missed_opportunities_ev_lost_bb": missed_opportunities_ev_lost_bb,
                "total_ev_lost_chips": total_ev_lost_chips,
                "total_ev_lost_bb": total_ev_lost_bb,
                "ev_lost_per_100_hands_bb": ev_lost_per_100_hands_bb,
                "avg_ev_lost_per_decision_bb": avg_ev_lost_per_decision_bb,
                "action_counts": action_counts,
                "action_distribution_pct": action_distribution_pct,
                "performance_by_position": performance_by_position,
                "performance_by_street": performance_by_street,
                "performance_by_pot_size": performance_by_pot_size,
                "performance_by_stack_depth": performance_by_stack_depth,
                "gto_evaluation": gto_evaluation,
                "quality_evolution": quality_evolution,
                "top_ev_loss_decisions": top_ev_loss_decisions,
            },
            "opponent_analysis": {
                "opponents_analyzed_count": total_opps_analyzed,
                "unique_opponents_in_sample": len(unique_opps_in_hands),
                "pattern_detection_frequency_pct": pattern_detection_freq_pct,
                "verifications_count": verif_total,
                "pattern_accuracy_pct": pattern_accuracy_pct,
                "adaptation_capacity_pct": adaptation_capacity_pct,
                "performance_vs_profiles": performance_vs_profiles,
                "opponents_hud_table": oponentes_db,
            },
            "statistical_evaluation": stat_eval,
            "operational_kpis": operational_kpis,
            "sample_power": sample_power_info,
            "version_comparison": version_comparison,
            "experiments_history": experiments,
            "versions_history": versions,
            "sessions_list": sessions,
            "main_metrics_table": main_metrics_table,
            "system_reliability": system_reliability,
            "recent_decisions": decisions[-60:][::-1] if decisions else [],
            "recent_hands": hands[-60:][::-1] if hands else [],
        }

        result_payload["intelligent_diagnostics"] = generar_diagnosticos_inteligentes(result_payload)
        return result_payload

    def _normalize_pos(self, pos_raw: Optional[str]) -> str:
        p = (pos_raw or "").upper()
        if "BTN" in p or "DEALER" in p or "BUTTON" in p:
            return "BTN"
        if "SB" in p or "SMALL" in p or "PEQUE" in p:
            return "SB"
        if "BB" in p or "BIG" in p or "GRANDE" in p:
            return "BB"
        return "DESCONOCIDA"

    def _normalize_profile(self, prof_raw: Optional[str]) -> str:
        p = (prof_raw or "").upper()
        if "CALLING" in p or "STATION" in p or "PEZ" in p:
            return "CALLING STATION"
        if "MANIAC" in p:
            return "MANIAC"
        if "NIT" in p:
            return "NIT"
        if "REGULAR" in p or "EQUILIBRADO" in p:
            return "REGULAR EQUILIBRADO"
        return "JUGADOR NUEVO"

    def _bucket_pot_size(self, pot_chips: float, bb: float) -> str:
        pot_bb = pot_chips / max(1.0, bb)
        if pot_bb < 4.0:
            return "SMALL (<4 BB)"
        if pot_bb <= 12.0:
            return "MEDIUM (4-12 BB)"
        return "LARGE (>12 BB)"

    def _bucket_stack_depth(self, stack_chips: float, bb: float) -> str:
        stack_bb = stack_chips / max(1.0, bb)
        if stack_bb < 10.0:
            return "SHORT (<10 BB)"
        if stack_bb <= 20.0:
            return "MEDIUM (10-20 BB)"
        return "DEEP (>20 BB)"

    def _build_statistical_evaluation(
        self,
        n_hands: int,
        wr_stats: Dict[str, float],
        ev_wr_stats: Dict[str, float],
        allin_wr_stats: Dict[str, float],
        net_profit_bb: float,
        ev_net_bb: float,
        allin_ev_bb: float,
        max_drawdown_bb: float,
        max_drawdown_chips: float,
        bb_values: List[float],
        ev_bb_values: List[float],
        gto_alignment_pct: float,
        gto_ci: Tuple[float, float],
    ) -> Dict[str, Any]:
        """Construye el diagnóstico estadístico completo separando Resultados Observados de EV Estimado."""
        if n_hands < 30:
            sample_status = "MUESTRA_CRITICA"
            sample_alert = (
                f"⚠️ Muestra exploratoria ({n_hands} manos < 30). Los resultados observados están dominados "
                "por la varianza a corto plazo. Priorice el EV BB/100 y el Regret por decisión."
            )
            is_sufficient = False
        elif n_hands < 100:
            sample_status = "MUESTRA_PRELIMINAR"
            sample_alert = (
                f"ℹ️ Muestra preliminar ({n_hands} manos). Útil para detectar errores estratégicos "
                "y tendencias de EV, pero el Winrate Real aún tiene un intervalo de confianza amplio."
            )
            is_sufficient = False
        elif n_hands < 300:
            sample_status = "MUESTRA_MODERADA"
            sample_alert = (
                f"✅ Muestra moderada ({n_hands} manos). Permite comparaciones estadísticamente válidas "
                "en EV ajustado y calidad estratégica."
            )
            is_sufficient = True
        else:
            sample_status = "MUESTRA_SOLIDA"
            sample_alert = (
                f"🏆 Muestra sólida ({n_hands} manos). Alta confiabilidad estadística tanto en EV como en Winrate."
            )
            is_sufficient = True

        luck_diff_bb = round(net_profit_bb - ev_net_bb, 2)
        luck_diff_bb100 = round(wr_stats["mean"] - ev_wr_stats["mean"], 2)

        if abs(luck_diff_bb100) <= 3.5:
            variance_state = "ALINEADO_CON_EV"
            variance_explanation = "Los resultados reales están estrechamente alineados con la expectativa matemática (EV)."
        elif luck_diff_bb100 > 3.5:
            variance_state = "VARIANZA_POSITIVA_RUN_GOOD"
            variance_explanation = (
                f"El bot está corriendo +{luck_diff_bb100:.2f} BB/100 por encima de su EV esperado (suerte favorable en showdowns). "
                "No sobreestime las ganancias observadas; guíese por el EV BB/100."
            )
        else:
            variance_state = "VARIANZA_NEGATIVA_BAD_BEATS"
            variance_explanation = (
                f"El bot está corriendo {luck_diff_bb100:.2f} BB/100 por debajo de su EV esperado (varianza adversa / bad beats). "
                "La calidad de las decisiones es superior a lo que refleja el beneficio neto."
            )

        # Validación de supuestos para Sharpe Ratio (n >= 30 y desviación estándar > 0)
        std_unit = wr_stats.get("std_per_unit", 0.0) or 0.0
        sharpe_assumptions_met = (n_hands >= 30) and (std_unit > 1e-6)
        if sharpe_assumptions_met:
            sharpe_100 = round(ev_wr_stats["mean"] / (std_unit * 10.0), 3)
            sharpe_note = f"Supuestos válidos (n = {n_hands} >= 30, σ = {std_unit:.2f} BB/mano > 0)."
        else:
            sharpe_100 = None
            sharpe_note = f"No aplicable: requiere n >= 30 manos y varianza no nula (actual n = {n_hands})."

        block_size = 20
        positive_ev_blocks = 0
        total_blocks = 0
        if len(ev_bb_values) >= block_size:
            for i in range(0, len(ev_bb_values) - block_size + 1, block_size):
                total_blocks += 1
                if sum(ev_bb_values[i: i + block_size]) > 0:
                    positive_ev_blocks += 1
        elif ev_bb_values:
            total_blocks = 1
            if sum(ev_bb_values) > 0:
                positive_ev_blocks = 1

        consistency_pct = round((positive_ev_blocks / max(1, total_blocks)) * 100.0, 1)
        sharpe_for_score = sharpe_100 if sharpe_100 is not None else 0.0
        stability_score = min(
            100.0,
            max(
                0.0,
                round(
                    0.45 * consistency_pct
                    + 0.35 * gto_alignment_pct
                    + 20.0 * max(0.0, min(1.0, (sharpe_for_score + 0.5) / 2.0)),
                    1,
                ),
            ),
        )

        return {
            "sample_size": n_hands,
            "sample_status": sample_status,
            "sample_sufficient": is_sufficient,
            "sample_alert": sample_alert,
            "observed_metrics": {
                "net_profit_bb": net_profit_bb,
                "winrate_bb100": wr_stats["mean"],
                "winrate_se_bb100": wr_stats["se"],
                "winrate_ci95_low": wr_stats["ci95_low"],
                "winrate_ci95_high": wr_stats["ci95_high"],
                "std_bb_per_hand": std_unit,
                "std_bb_per_100": round(std_unit * 10.0, 2),
                "variance_bb_per_hand": wr_stats["variance"],
                "max_drawdown_bb": round(max_drawdown_bb, 2),
                "max_drawdown_chips": round(max_drawdown_chips, 2),
            },
            "gto_ev_estimates": {
                "ev_net_bb": ev_net_bb,
                "allin_ev_bb": allin_ev_bb,
                "ev_winrate_bb100": ev_wr_stats["mean"],
                "ev_winrate_se_bb100": ev_wr_stats["se"],
                "ev_winrate_ci95_low": ev_wr_stats["ci95_low"],
                "ev_winrate_ci95_high": ev_wr_stats["ci95_high"],
                "allin_ev_bb100": allin_wr_stats["mean"],
                "allin_ev_ci95_low": allin_wr_stats["ci95_low"],
                "allin_ev_ci95_high": allin_wr_stats["ci95_high"],
                "gto_alignment_pct": gto_alignment_pct,
                "gto_alignment_ci95": list(gto_ci),
            },
            "variance_analysis": {
                "luck_diff_bb": luck_diff_bb,
                "luck_diff_bb100": luck_diff_bb100,
                "variance_state": variance_state,
                "variance_explanation": variance_explanation,
            },
            "stability_indicators": {
                "sharpe_ratio_100": sharpe_100 if sharpe_100 is not None else 0.0,
                "sharpe_assumptions_met": sharpe_assumptions_met,
                "sharpe_status_note": sharpe_note,
                "positive_ev_windows_pct": consistency_pct,
                "stability_score": stability_score,
            },
        }

    def _construir_tabla_principal_metricas(
        self,
        n_hands: int,
        n_sessions: int,
        hands_won: int,
        hands_lost: int,
        win_pct: float,
        win_ci: Tuple[float, float],
        net_profit_chips: float,
        net_profit_bb: float,
        ev_net_bb: float,
        allin_ev_bb: float,
        roi_pct: float,
        roi_applicable: bool,
        investment_base: float,
        wr_stats: Dict[str, float],
        ev_wr_stats: Dict[str, float],
        allin_wr_stats: Dict[str, float],
        avg_profit_chips: float,
        avg_profit_bb: float,
        max_drawdown_bb: float,
        max_drawdown_chips: float,
        stat_eval: Dict[str, Any],
        hud_definitions_table: List[Dict[str, Any]],
        total_decisions: int,
        evaluable_decisions_count: int,
        gto_aligned_count: int,
        gto_alignment_pct: float,
        gto_ci: Tuple[float, float],
        exact_gto_match_count: int,
        exact_gto_match_pct: float,
        strategic_errors_count: int,
        strategic_error_rate_pct: float,
        missed_opportunities_count: int,
        missed_opportunities_rate_pct: float,
        total_ev_lost_bb: float,
        ev_lost_per_100_hands_bb: float,
        total_opps_analyzed: int,
        hands_with_detected_pattern: int,
        pattern_detection_freq_pct: float,
        verif_ok: int,
        verif_total: int,
        pattern_accuracy_pct: float,
        adapt_aligned_count: int,
        adaptation_capacity_pct: float,
    ) -> List[Dict[str, Any]]:
        """Construye la Tabla Maestra Ordenable con definición matemática, numerador, denominador y muestra."""
        rows: List[Dict[str, Any]] = [
            {
                "category": "Rendimiento Financiero",
                "metric_name": "Manos Jugadas (N)",
                "value_num": float(n_hands),
                "value_display": f"{n_hands}",
                "ci95_display": f"{n_sessions} sesiones",
                "numerator": n_hands,
                "denominator": n_hands,
                "sample_size": n_hands,
                "formula": "Conteo de manos únicas (hand_uid) cerradas",
                "explanation": "Tamaño total de la muestra evaluada sin duplicados.",
            },
            {
                "category": "Rendimiento Financiero",
                "metric_name": "Porcentaje de Manos Ganadas (Win%)",
                "value_num": win_pct,
                "value_display": f"{win_pct:.2f}%",
                "ci95_display": f"[{win_ci[0]:.1f}%, {win_ci[1]:.1f}%]",
                "numerator": hands_won,
                "denominator": n_hands,
                "sample_size": n_hands,
                "formula": "(Manos ganadas / Total manos) × 100",
                "explanation": f"Victorias: {hands_won} | Derrotas: {hands_lost}. IC 95% binomial de Wilson.",
            },
            {
                "category": "Rendimiento Financiero",
                "metric_name": "Beneficio Neto Observado",
                "value_num": net_profit_bb,
                "value_display": f"{net_profit_bb:+.2f} BB ({net_profit_chips:+.0f} fichas)",
                "ci95_display": f"Media: {avg_profit_bb:+.3f} BB/mano",
                "numerator": net_profit_bb,
                "denominator": n_hands,
                "sample_size": n_hands,
                "formula": "Σ (Fichas ganadas - Fichas invertidas) / Big Blind",
                "explanation": "Resultado financiero real observado en la muestra seleccionada.",
            },
            {
                "category": "Rendimiento Financiero",
                "metric_name": "Winrate Observado (BB/100)",
                "value_num": wr_stats["mean"],
                "value_display": f"{wr_stats['mean']:+.2f} BB/100",
                "ci95_display": f"[{wr_stats['ci95_low']:+.2f}, {wr_stats['ci95_high']:+.2f}]",
                "numerator": net_profit_bb,
                "denominator": n_hands,
                "sample_size": n_hands,
                "formula": "(Beneficio Neto en BB / N manos) × 100",
                "explanation": "Velocidad de ganancia real normalizada cada 100 manos.",
            },
            {
                "category": "Rendimiento Financiero",
                "metric_name": "EV Winrate Ajustado (EV BB/100)",
                "value_num": ev_wr_stats["mean"],
                "value_display": f"{ev_wr_stats['mean']:+.2f} BB/100",
                "ci95_display": f"[{ev_wr_stats['ci95_low']:+.2f}, {ev_wr_stats['ci95_high']:+.2f}]",
                "numerator": ev_net_bb,
                "denominator": n_hands,
                "sample_size": n_hands,
                "formula": "(Σ EV Neto en BB / N manos) × 100",
                "explanation": "Expectativa matemática de ganancia por cada 100 manos filtrando varianza de reparto en showdown.",
            },
            {
                "category": "Rendimiento Financiero",
                "metric_name": "All-in EV Ajustado (BB/100)",
                "value_num": allin_wr_stats["mean"],
                "value_display": f"{allin_wr_stats['mean']:+.2f} BB/100 ({allin_ev_bb:+.2f} BB)",
                "ci95_display": f"[{allin_wr_stats['ci95_low']:+.2f}, {allin_wr_stats['ci95_high']:+.2f}]",
                "numerator": allin_ev_bb,
                "denominator": n_hands,
                "sample_size": n_hands,
                "formula": "Σ (Equity_Showdown × Bote_Total - Inversión) en exposición total",
                "explanation": "Rentabilidad ajustada por equidad en situaciones de exposición total o showdown.",
            },
            {
                "category": "Rendimiento Financiero",
                "metric_name": "ROI (Retorno sobre Inversión)",
                "value_num": roi_pct,
                "value_display": f"{roi_pct:+.2f}%" if roi_applicable else "N/A (Sin base)",
                "ci95_display": f"Base: {investment_base:.0f} fichas",
                "numerator": net_profit_chips,
                "denominator": round(investment_base, 1),
                "sample_size": n_hands,
                "formula": "(Beneficio Neto Fichas / Base de Buy-ins) × 100",
                "explanation": "Retorno porcentual sobre el capital puesto en juego en las sesiones evaluadas.",
            },
            {
                "category": "Rendimiento Financiero",
                "metric_name": "Desviación Estándar y Varianza",
                "value_num": round(wr_stats["std_per_unit"] * 10.0, 2),
                "value_display": f"σ = {wr_stats['std_per_unit']*10.0:.2f} BB/100 (s² = {wr_stats['variance']:.2f})",
                "ci95_display": f"σ/mano = {wr_stats['std_per_unit']:.2f} BB",
                "numerator": wr_stats["variance"],
                "denominator": max(0, n_hands - 1),
                "sample_size": n_hands,
                "formula": "s = sqrt(Σ(x_i - x̄)² / (n - 1)); σ_100 = s × 10",
                "explanation": "Dispersión muestral de los resultados por mano y cada 100 manos.",
            },
            {
                "category": "Rendimiento Financiero",
                "metric_name": "Máximo Drawdown (Caída Pico-Valle)",
                "value_num": max_drawdown_bb,
                "value_display": f"-{max_drawdown_bb:.2f} BB (-{max_drawdown_chips:.0f} fichas)",
                "ci95_display": "Riesgo de cola observado",
                "numerator": max_drawdown_bb,
                "denominator": 1,
                "sample_size": n_hands,
                "formula": "max_{t} (max_{s <= t} Bankroll_s - Bankroll_t)",
                "explanation": "Mayor caída acumulada desde un máximo histórico de bankroll.",
            },
            {
                "category": "Rendimiento Financiero",
                "metric_name": "Sharpe Ratio (por 100 manos)",
                "value_num": float(stat_eval["stability_indicators"]["sharpe_ratio_100"]),
                "value_display": (
                    f"{stat_eval['stability_indicators']['sharpe_ratio_100']:.3f}"
                    if stat_eval["stability_indicators"]["sharpe_assumptions_met"]
                    else "NO APLICABLE (n < 30)"
                ),
                "ci95_display": stat_eval["stability_indicators"]["sharpe_status_note"],
                "numerator": ev_wr_stats["mean"],
                "denominator": round(wr_stats["std_per_unit"] * 10.0, 2),
                "sample_size": n_hands,
                "formula": "EV_BB100 / (σ_mano × 10) [válido si n >= 30 y σ > 0]",
                "explanation": "Retorno esperado ajustado por unidad de riesgo/volatilidad.",
            },
        ]

        # Agregar todas las métricas HUD documentadas
        for h_item in hud_definitions_table:
            rows.append({
                "category": "Estadísticas HUD",
                "metric_name": h_item["name"],
                "value_num": float(h_item["value"]),
                "value_display": f"{h_item['value']:.2f}%" if h_item["unit"] == "%" else f"{h_item['value']:.2f}",
                "ci95_display": (
                    f"[{h_item['ci95'][0]:.1f}%, {h_item['ci95'][1]:.1f}%]"
                    if h_item["unit"] == "%"
                    else f"Óptimo: {h_item['optimal_range_3max']}"
                ),
                "numerator": h_item["numerator"],
                "denominator": h_item["denominator"],
                "sample_size": h_item["sample_size"],
                "formula": h_item["formula"],
                "explanation": f"{h_item['definition']} (Rango 3-Max: {h_item['optimal_range_3max']})",
            })

        # Agregar métricas Estratégicas, GTO y de Oponentes
        rows.extend([
            {
                "category": "Calidad Estratégica y GTO",
                "metric_name": "Coincidencia Exacta Heurística-GTO",
                "value_num": exact_gto_match_pct,
                "value_display": f"{exact_gto_match_pct:.2f}%",
                "ci95_display": f"{exact_gto_match_count}/{evaluable_decisions_count} decisiones",
                "numerator": exact_gto_match_count,
                "denominator": evaluable_decisions_count,
                "sample_size": evaluable_decisions_count,
                "formula": "(Decisiones idénticas a acción primaria heurística-GTO / Decisiones evaluables) × 100",
                "explanation": "Coincidencia directa con la recomendación primaria de `poker_gto_engine.py`.",
            },
            {
                "category": "Calidad Estratégica y GTO",
                "metric_name": "Calidad de Decisión Aceptable según EV",
                "value_num": gto_alignment_pct,
                "value_display": f"{gto_alignment_pct:.2f}%",
                "ci95_display": f"[{gto_ci[0]:.1f}%, {gto_ci[1]:.1f}%]",
                "numerator": gto_aligned_count,
                "denominator": evaluable_decisions_count,
                "sample_size": evaluable_decisions_count,
                "formula": "(Decisiones con ΔEV <= 0.35 BB o adaptación explotativa válida / Decisiones evaluables) × 100",
                "explanation": "Proporción de decisiones matemáticamente sólidas en valor esperado.",
            },
            {
                "category": "Calidad Estratégica y GTO",
                "metric_name": "Tasa de Errores Estratégicos",
                "value_num": strategic_error_rate_pct,
                "value_display": f"{strategic_error_rate_pct:.2f}% ({strategic_errors_count} errores)",
                "ci95_display": f"Muestra: {evaluable_decisions_count} dec.",
                "numerator": strategic_errors_count,
                "denominator": evaluable_decisions_count,
                "sample_size": evaluable_decisions_count,
                "formula": "(Decisiones con error estratégico o pérdida > 2.0 BB / Decisiones evaluables) × 100",
                "explanation": "Frecuencia de errores técnicos verificados con la información disponible en el turno.",
            },
            {
                "category": "Calidad Estratégica y GTO",
                "metric_name": "Oportunidades Perdidas (Missed Value)",
                "value_num": missed_opportunities_rate_pct,
                "value_display": f"{missed_opportunities_rate_pct:.2f}% ({missed_opportunities_count} casos)",
                "ci95_display": f"Total: {total_decisions} dec.",
                "numerator": missed_opportunities_count,
                "denominator": evaluable_decisions_count,
                "sample_size": evaluable_decisions_count,
                "formula": "(Acciones pasivas con Equity >= 62% y ΔEV >= 0.60 BB / Decisiones evaluables) × 100",
                "explanation": "Situaciones donde el bot dejó de extraer valor claro apostando o subiendo.",
            },
            {
                "category": "Calidad Estratégica y GTO",
                "metric_name": "Regret / EV Perdido por 100 Manos",
                "value_num": ev_lost_per_100_hands_bb,
                "value_display": f"{ev_lost_per_100_hands_bb:.2f} BB/100 (Total: {total_ev_lost_bb:.2f} BB)",
                "ci95_display": f"Media: {total_ev_lost_bb/max(1, evaluable_decisions_count):.3f} BB/dec",
                "numerator": total_ev_lost_bb,
                "denominator": n_hands,
                "sample_size": n_hands,
                "formula": "(Σ (EV_Mejor_Acción - EV_Acción_Elegida) en BB / N manos) × 100",
                "explanation": "Costo acumulado en ciegas grandes por desviaciones subóptimas.",
            },
            {
                "category": "Análisis de Oponentes",
                "metric_name": "Frecuencia de Detección de Patrones",
                "value_num": pattern_detection_freq_pct,
                "value_display": f"{pattern_detection_freq_pct:.2f}% ({total_opps_analyzed} rivales)",
                "ci95_display": f"{hands_with_detected_pattern}/{n_hands} manos",
                "numerator": hands_with_detected_pattern,
                "denominator": n_hands,
                "sample_size": n_hands,
                "formula": "(Manos con arquetipo rival clasificado / Total manos) × 100",
                "explanation": "Porcentaje de manos donde `OpponentProfiler` dispone de perfil activo del rival.",
            },
            {
                "category": "Análisis de Oponentes",
                "metric_name": "Precisión de Tendencias en Showdown",
                "value_num": pattern_accuracy_pct,
                "value_display": f"{pattern_accuracy_pct:.2f}% ({verif_ok}/{verif_total})",
                "ci95_display": f"{verif_total} showdowns verificados",
                "numerator": verif_ok,
                "denominator": verif_total,
                "sample_size": verif_total,
                "formula": "(Showdowns coherentes con el rango del perfil estimado / Total showdowns verificados) × 100",
                "explanation": "Exactitud empírica de la clasificación del rival frente a las cartas mostradas.",
            },
            {
                "category": "Análisis de Oponentes",
                "metric_name": "Capacidad de Adaptación Explotativa",
                "value_num": adaptation_capacity_pct,
                "value_display": f"{adaptation_capacity_pct:.2f}%",
                "ci95_display": f"{adapt_aligned_count}/{evaluable_decisions_count} dec.",
                "numerator": adapt_aligned_count,
                "denominator": evaluable_decisions_count,
                "sample_size": evaluable_decisions_count,
                "formula": "(Decisiones alineadas con la estrategia óptima contra el arquetipo rival / Decisiones evaluables) × 100",
                "explanation": "Grado de ajuste táctico frente a Calling Stations, Maniacs, Nits y Regulares.",
            },
        ])
        return rows

    # ------------------------------------------------------------------
    # COMPARADOR CIENTÍFICO DE VERSIONES EXPERIMENTALES (FASE 3)
    # ------------------------------------------------------------------
    def comparar_versiones(self, data_source: Optional[str] = None) -> Dict[str, Any]:
        """
        Evalúa todas las versiones registradas, ejecuta pruebas pareadas (por `scenario_id`)
        y pruebas t de Welch, aplica la corrección de Holm-Bonferroni por comparaciones múltiples
        y clasifica cada versión en `MEJORA`, `INCONCLUSO` o `REGRESIÓN`.
        """
        versions = self.storage.obtener_versiones()
        version_rows: List[Dict[str, Any]] = []
        hands_by_version: Dict[str, List[Dict[str, Any]]] = {}
        decisions_by_version: Dict[str, List[Dict[str, Any]]] = {}

        for v in versions:
            vid = v["version_id"]
            q_filter: Dict[str, Any] = {"version_id": vid}
            if data_source in ("live", "benchmark"):
                q_filter["data_source"] = data_source
            v_hands = self.storage.consultar_manos(q_filter)
            v_decs = self.storage.consultar_decisiones(q_filter)
            hands_by_version[vid] = v_hands
            decisions_by_version[vid] = v_decs

            nh = len(v_hands)
            eval_decs = [
                d for d in v_decs
                if d.get("evaluation_available", 1) != 0 and d.get("decision_quality") != "EVALUACION_NO_DISPONIBLE"
            ]
            nd = len(eval_decs)
            bb_vals = [float(h.get("net_profit_bb") or 0.0) for h in v_hands]
            ev_vals = [float(h.get("ev_net_bb") or 0.0) for h in v_hands]

            wr_s = mean_std_ci95(bb_vals, scale=100.0)
            ev_s = mean_std_ci95(ev_vals, scale=100.0)

            won_c = sum(1 for h in v_hands if h.get("won_hand") or float(h.get("net_profit_chips") or 0) > 0)
            vpip_c = sum(1 for h in v_hands if h.get("vpip_flag"))
            pfr_c = sum(1 for h in v_hands if h.get("pfr_flag"))
            gto_c = sum(1 for d in eval_decs if d.get("is_gto_aligned"))
            err_c = sum(1 for d in eval_decs if d.get("is_strategic_error"))
            adapt_c = sum(1 for d in eval_decs if d.get("opponent_adaptation_aligned"))
            ev_lost_bb = sum(float(d.get("ev_lost_bb") or 0.0) for d in eval_decs)
            ev_lost_100 = round((ev_lost_bb / nh) * 100.0, 2) if nh > 0 else 0.0

            net_chips = round(sum(float(h.get("net_profit_chips") or 0.0) for h in v_hands), 2)
            net_bb = round(sum(bb_vals), 2)
            ev_bb = round(sum(ev_vals), 2)

            gto_pct = round((gto_c / nd) * 100.0, 2) if nd > 0 else 0.0
            err_pct = round((err_c / nd) * 100.0, 2) if nd > 0 else 0.0
            adapt_pct = round((adapt_c / nd) * 100.0, 2) if nd > 0 else 0.0
            win_pct = round((won_c / nh) * 100.0, 2) if nh > 0 else 0.0

            sample_weight = min(1.0, math.sqrt(nh / 100.0)) if nh > 0 else 0.0
            composite_score = round(
                sample_weight * (
                    ev_s["mean"] * 1.5
                    + wr_s["mean"] * 0.5
                    + (gto_pct - 50.0) * 0.8
                    + (adapt_pct - 50.0) * 0.4
                    - ev_lost_100 * 0.6
                ),
                2,
            )

            version_rows.append({
                "version_id": vid,
                "created_at": v.get("created_at", ""),
                "description": v.get("description", ""),
                "parent_version": v.get("parent_version", ""),
                "modified_params": v.get("modified_params_dict", {}),
                "config_hash": v.get("config_hash", ""),
                "code_hash": v.get("code_hash") or self.storage.default_code_hash,
                "random_seed": v.get("random_seed", 20261114),
                "experiment_conditions": v.get("experiment_conditions_dict", {}),
                "hands_evaluated": nh,
                "decisions_evaluated": nd,
                "is_exploratory_sample": nh < 50,
                "win_pct": win_pct,
                "vpip_pct": round((vpip_c / nh) * 100.0, 2) if nh > 0 else 0.0,
                "pfr_pct": round((pfr_c / nh) * 100.0, 2) if nh > 0 else 0.0,
                "net_profit_chips": net_chips,
                "net_profit_bb": net_bb,
                "ev_net_bb": ev_bb,
                "bb_per_100": wr_s["mean"],
                "bb_per_100_ci95": [wr_s["ci95_low"], wr_s["ci95_high"]],
                "ev_bb_per_100": ev_s["mean"],
                "ev_bb_per_100_ci95": [ev_s["ci95_low"], ev_s["ci95_high"]],
                "gto_alignment_pct": gto_pct,
                "strategic_error_rate_pct": err_pct,
                "ev_lost_per_100_bb": ev_lost_100,
                "adaptation_pct": adapt_pct,
                "composite_score": composite_score,
                "comparison_vs_prev": None,
            })

        # Primera pasada: calcular estadísticos t pareados y de Welch para cada comparación
        pending_comparisons: List[Tuple[int, Dict[str, Any]]] = []
        raw_p_values: List[float] = []

        for idx, row in enumerate(version_rows):
            if idx == 0 and not row.get("parent_version"):
                row["comparison_vs_prev"] = {
                    "base_version": None,
                    "verdict": "LINEA_BASE",
                    "verdict_state": "LINEA_BASE",
                    "is_exploratory": row["hands_evaluated"] < 50,
                    "sample_tag": "EXPLORATORIA" if row["hands_evaluated"] < 50 else "CONFIRMATORIA",
                    "summary": "Versión inicial de referencia (Baseline).",
                    "improved_metrics": [],
                    "degraded_metrics": [],
                    "unchanged_metrics": [],
                }
                continue

            parent_id = row.get("parent_version") or (version_rows[idx - 1]["version_id"] if idx > 0 else None)
            prev_row = next((r for r in version_rows if r["version_id"] == parent_id), None)
            if not prev_row and idx > 0:
                prev_row = version_rows[idx - 1]
                parent_id = prev_row["version_id"]

            if not prev_row:
                continue

            vid = row["version_id"]
            prev_hands = hands_by_version.get(parent_id, [])
            curr_hands = hands_by_version.get(vid, [])

            prev_bb = [float(h.get("net_profit_bb") or 0.0) for h in prev_hands]
            curr_bb = [float(h.get("net_profit_bb") or 0.0) for h in curr_hands]
            prev_ev = [float(h.get("ev_net_bb") or 0.0) for h in prev_hands]
            curr_ev = [float(h.get("ev_net_bb") or 0.0) for h in curr_hands]

            welch_real = welch_t_test(prev_bb, curr_bb, scale=100.0)
            welch_ev = welch_t_test(prev_ev, curr_ev, scale=100.0)

            prev_by_scen = {h["scenario_id"]: float(h.get("ev_net_bb") or 0.0) for h in prev_hands if h.get("scenario_id")}
            paired_ev_list = [
                (prev_by_scen[h["scenario_id"]], float(h.get("ev_net_bb") or 0.0))
                for h in curr_hands
                if h.get("scenario_id") and h["scenario_id"] in prev_by_scen
            ]
            paired_ev_res = paired_t_test(paired_ev_list, scale=100.0)

            effective_p = paired_ev_res["p_value"] if paired_ev_res else welch_ev["p_value"]
            effective_ci95_diff = paired_ev_res["ci95_diff"] if paired_ev_res else welch_ev["ci95_diff"]
            effective_cohens_d = paired_ev_res["cohens_d_paired"] if paired_ev_res else welch_ev["cohens_d"]

            raw_p_values.append(effective_p)
            pending_comparisons.append((
                idx,
                {
                    "parent_id": parent_id,
                    "prev_row": prev_row,
                    "welch_real": welch_real,
                    "welch_ev": welch_ev,
                    "paired_ev_res": paired_ev_res,
                    "effective_p": effective_p,
                    "effective_ci95_diff": effective_ci95_diff,
                    "effective_cohens_d": effective_cohens_d,
                },
            ))

        # Aplicar corrección de Holm-Bonferroni sobre todas las comparaciones evaluadas
        holm_results = holm_bonferroni_correction(raw_p_values, alpha=0.05)

        for comp_i, (row_idx, cdata) in enumerate(pending_comparisons):
            row = version_rows[row_idx]
            prev_row = cdata["prev_row"]
            parent_id = cdata["parent_id"]
            holm_info = holm_results[comp_i] if comp_i < len(holm_results) else {
                "raw_p_value": cdata["effective_p"],
                "holm_adjusted_p_value": cdata["effective_p"],
                "significant_after_holm": False,
            }

            delta_bb100 = round(row["bb_per_100"] - prev_row["bb_per_100"], 2)
            delta_ev_bb100 = round(row["ev_bb_per_100"] - prev_row["ev_bb_per_100"], 2)
            delta_gto = round(row["gto_alignment_pct"] - prev_row["gto_alignment_pct"], 2)
            delta_err = round(row["strategic_error_rate_pct"] - prev_row["strategic_error_rate_pct"], 2)
            delta_ev_lost = round(row["ev_lost_per_100_bb"] - prev_row["ev_lost_per_100_bb"], 2)
            delta_adapt = round(row["adaptation_pct"] - prev_row["adaptation_pct"], 2)
            delta_win = round(row["win_pct"] - prev_row["win_pct"], 2)

            improved: List[str] = []
            degraded: List[str] = []
            unchanged: List[str] = []

            if delta_ev_bb100 >= 1.5:
                improved.append(f"EV Winrate (+{delta_ev_bb100:.2f} BB/100)")
            elif delta_ev_bb100 <= -1.5:
                degraded.append(f"EV Winrate ({delta_ev_bb100:.2f} BB/100)")
            else:
                unchanged.append("EV Winrate")

            if delta_bb100 >= 2.0:
                improved.append(f"Winrate Real (+{delta_bb100:.2f} BB/100)")
            elif delta_bb100 <= -2.0:
                degraded.append(f"Winrate Real ({delta_bb100:.2f} BB/100)")
            else:
                unchanged.append("Winrate Real")

            if delta_gto >= 1.5:
                improved.append(f"Alineación GTO (+{delta_gto:.1f}%)")
            elif delta_gto <= -1.5:
                degraded.append(f"Alineación GTO ({delta_gto:.1f}%)")
            else:
                unchanged.append("Alineación GTO")

            if delta_err <= -1.0:
                improved.append(f"Tasa de Error ({delta_err:.1f}%)")
            elif delta_err >= 1.0:
                degraded.append(f"Tasa de Error (+{delta_err:.1f}%)")
            else:
                unchanged.append("Tasa de Error")

            if delta_ev_lost <= -1.5:
                improved.append(f"EV Perdido ({delta_ev_lost:.2f} BB/100)")
            elif delta_ev_lost >= 1.5:
                degraded.append(f"EV Perdido (+{delta_ev_lost:.2f} BB/100)")
            else:
                unchanged.append("EV Perdido")

            if delta_adapt >= 1.5:
                improved.append(f"Adaptación Rival (+{delta_adapt:.1f}%)")
            elif delta_adapt <= -1.5:
                degraded.append(f"Adaptación Rival ({delta_adapt:.1f}%)")
            else:
                unchanged.append("Adaptación Rival")

            is_live_comparison = data_source != "benchmark"
            paired_res = cdata.get("paired_ev_res")
            std_for_power = (
                float(paired_res.get("std_diff", 15.0))
                if isinstance(paired_res, dict) and paired_res.get("std_diff", 0.0) > 0
                else (float(row.get("std_bb_per_hand") or 15.0))
            )
            power_info = calcular_tamano_muestra_necesario(
                delta_bb100=config.DEFAULT_TARGET_DELTA_BB100,
                std_bb_per_hand=std_for_power,
                alpha=config.ALPHA_SIGNIFICANCE,
                power=config.POWER_TARGET,
                paired=not is_live_comparison,
            )

            state_info = clasificar_estado_comparacion(
                n_prev=prev_row["hands_evaluated"],
                n_curr=row["hands_evaluated"],
                delta_ev_bb100=delta_ev_bb100,
                holm_p_value=holm_info["holm_adjusted_p_value"],
                ci95_diff=cdata["effective_ci95_diff"],
                cohens_d=cdata["effective_cohens_d"],
                delta_error_rate_pct=delta_err,
                is_live=is_live_comparison,
                paired_power_min_n=power_info["required_hands"],
            )

            row["comparison_vs_prev"] = {
                "base_version": parent_id,
                "n_prev": prev_row["hands_evaluated"],
                "n_curr": row["hands_evaluated"],
                "min_hands_threshold": state_info["min_hands_threshold"],
                "required_hands_for_power": power_info["required_hands"],
                "is_live": is_live_comparison,
                "delta_bb_per_100": delta_bb100,
                "delta_ev_bb_per_100": delta_ev_bb100,
                "delta_gto_pct": delta_gto,
                "delta_error_rate_pct": delta_err,
                "delta_ev_lost_per_100_bb": delta_ev_lost,
                "delta_adaptation_pct": delta_adapt,
                "delta_win_pct": delta_win,
                "ci95_ev_diff": cdata["effective_ci95_diff"],
                "cohens_d": cdata["effective_cohens_d"],
                "welch_real_test": cdata["welch_real"],
                "welch_ev_test": cdata["welch_ev"],
                "paired_ev_test": cdata["paired_ev_res"],
                "effective_p_value": cdata["effective_p"],
                "holm_corrected_p_value": holm_info["holm_adjusted_p_value"],
                "significant_after_holm": holm_info["significant_after_holm"],
                "improved_metrics": improved,
                "degraded_metrics": degraded,
                "unchanged_metrics": unchanged,
                "verdict": state_info["legacy_verdict"],
                "verdict_state": state_info["verdict_state"],
                "is_exploratory": state_info["is_exploratory"],
                "sample_tag": state_info["sample_tag"],
                "summary": state_info["reason"],
            }

        eligible_versions = [r for r in version_rows if r["hands_evaluated"] >= 30] or version_rows
        best_version_row = max(eligible_versions, key=lambda r: r["composite_score"]) if eligible_versions else None
        best_real_wr_row = max(eligible_versions, key=lambda r: r["bb_per_100"]) if eligible_versions else None
        best_ev_wr_row = max(eligible_versions, key=lambda r: r["ev_bb_per_100"]) if eligible_versions else None
        best_gto_row = max(eligible_versions, key=lambda r: r["gto_alignment_pct"]) if eligible_versions else None

        return {
            "versions": version_rows,
            "multiple_comparisons_method": "Holm-Bonferroni Step-Down (α = 0.05)",
            "total_comparisons_tested": len(pending_comparisons),
            "best_overall_statistical_version": best_version_row["version_id"] if best_version_row else None,
            "best_ev_version": best_ev_wr_row["version_id"] if best_ev_wr_row else None,
            "best_real_winrate_version": best_real_wr_row["version_id"] if best_real_wr_row else None,
            "best_gto_version": best_gto_row["version_id"] if best_gto_row else None,
        }
