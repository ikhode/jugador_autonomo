"""
Módulo de Diagnóstico Inteligente (Fase 5) y Telemetría de Rendimiento P50/P95/P99 (Fase 6).

Cumple estrictamente con la regla de separación de responsabilidades:
- Analiza métricas observadas, estimaciones de EV, estadísticas HUD y comparaciones de versiones.
- Genera observaciones estructuradas con evidencia estadística verificable.
- Es 100% informativo (Read-Only): NUNCA modifica automáticamente la estrategia ni los parámetros de `bot.py`.
"""
import math
import os
import sys
try:
    import resource
except ImportError:
    resource = None
from typing import Any, Dict, List, Optional


def calcular_percentil(valores: List[float], percentil: float) -> float:
    """
    Calcula el percentil exacto (interpolación lineal estándar) para P50, P95, P99.
    """
    if not valores:
        return 0.0
    ordenados = sorted(float(v) for v in valores)
    n = len(ordenados)
    if n == 1:
        return round(ordenados[0], 4)
    pos = (percentil / 100.0) * (n - 1)
    low_idx = int(math.floor(pos))
    high_idx = int(math.ceil(pos))
    if low_idx == high_idx:
        return round(ordenados[low_idx], 4)
    weight = pos - low_idx
    val = ordenados[low_idx] * (1.0 - weight) + ordenados[high_idx] * weight
    return round(val, 4)


def resumir_distribucion_latencia(valores: List[float]) -> Dict[str, float]:
    """Devuelve N, Media, Mín, P50, P95, P99 y Máx en milisegundos."""
    limpios = [float(v) for v in valores if v is not None and float(v) >= 0.0]
    n = len(limpios)
    if n == 0:
        return {
            "count": 0,
            "mean_ms": 0.0,
            "min_ms": 0.0,
            "p50_ms": 0.0,
            "p95_ms": 0.0,
            "p99_ms": 0.0,
            "max_ms": 0.0,
        }
    return {
        "count": n,
        "mean_ms": round(sum(limpios) / n, 4),
        "min_ms": round(min(limpios), 4),
        "p50_ms": calcular_percentil(limpios, 50.0),
        "p95_ms": calcular_percentil(limpios, 95.0),
        "p99_ms": calcular_percentil(limpios, 99.0),
        "max_ms": round(max(limpios), 4),
    }


def obtener_uso_memoria_rss_mb() -> float:
    """
    Obtiene el consumo real de memoria RSS del proceso actual en Megabytes (MB).
    Soporta Windows, macOS y Linux.
    """
    if resource is not None:
        try:
            usage = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
            if sys.platform == "darwin":
                return round(float(usage) / (1024.0 * 1024.0), 2)
            return round(float(usage) / 1024.0, 2)
        except Exception:
            pass
    try:
        import psutil
        return round(float(psutil.Process(os.getpid()).memory_info().rss) / (1024.0 * 1024.0), 2)
    except Exception:
        return 0.0


def construir_reporte_confiabilidad_sistema(
    telemetry_rows: List[Dict[str, Any]],
    decisions: List[Dict[str, Any]],
    db_integrity: Dict[str, Any],
    stat_processing_ms: float = 0.0,
) -> Dict[str, Any]:
    """
    Construye el diagnóstico de rendimiento y confiabilidad del sistema (Fase 6),
    reportando percentiles reales P50, P95 y P99 para cada etapa del pipeline.
    """
    decision_latencies = [
        float(d.get("response_time_ms") or 0.0)
        for d in decisions
        if float(d.get("response_time_ms") or 0.0) > 0.0
    ]
    if not decision_latencies and telemetry_rows:
        decision_latencies = [
            float(t.get("decision_latency_ms") or 0.0)
            for t in telemetry_rows
            if float(t.get("decision_latency_ms") or 0.0) > 0.0
        ]

    enqueue_latencies = [
        float(t.get("enqueue_latency_ms") or 0.0)
        for t in telemetry_rows
        if float(t.get("enqueue_latency_ms") or 0.0) > 0.0
    ] + [
        float(d.get("enqueue_latency_ms") or 0.0)
        for d in decisions
        if float(d.get("enqueue_latency_ms") or 0.0) > 0.0
    ]

    sqlite_latencies = [
        float(t.get("sqlite_write_ms") or 0.0)
        for t in telemetry_rows
        if float(t.get("sqlite_write_ms") or 0.0) > 0.0
    ] + [
        float(d.get("sqlite_write_ms") or 0.0)
        for d in decisions
        if float(d.get("sqlite_write_ms") or 0.0) > 0.0
    ]

    stat_latencies = [
        float(t.get("stat_processing_ms") or 0.0)
        for t in telemetry_rows
        if float(t.get("stat_processing_ms") or 0.0) > 0.0
    ]
    if stat_processing_ms > 0.0:
        stat_latencies.append(float(stat_processing_ms))

    total_storage_errors = sum(int(t.get("storage_errors") or 0) for t in telemetry_rows)
    total_dropped = sum(int(t.get("dropped_records") or 0) for t in telemetry_rows)
    max_pending = max([int(t.get("pending_tasks") or 0) for t in telemetry_rows] or [0])
    current_rss_mb = obtener_uso_memoria_rss_mb()

    dec_stats = resumir_distribucion_latencia(decision_latencies)
    enq_stats = resumir_distribucion_latencia(enqueue_latencies)
    sql_stats = resumir_distribucion_latencia(sqlite_latencies)
    proc_stats = resumir_distribucion_latencia(stat_latencies)

    return {
        "decision_latency": dec_stats,
        "enqueue_latency": enq_stats,
        "sqlite_write_latency": sql_stats,
        "stat_processing_latency": proc_stats,
        "memory_rss_mb": current_rss_mb,
        "storage_errors_total": total_storage_errors,
        "dropped_records_total": total_dropped,
        "max_pending_queue_tasks": max_pending,
        "db_integrity": db_integrity,
        "latency_methodology_note": (
            "Mediciones empíricas con reloj monotónico de alta resolución (time.perf_counter_ns). "
            "El hilo principal del bot solo incurre en la latencia de encolado (enqueue_latency_ms), "
            "mientras que la escritura SQLite ocurre en el worker secundario con drenado garantizado al cierre."
        ),
    }


def generar_diagnosticos_inteligentes(analytics_data: Dict[str, Any]) -> List[Dict[str, Any]]:
    """
    Analiza las métricas reales del bot y genera diagnósticos estructurados (Fase 5).
    Cada diagnóstico contiene:
      - id_diagnostico
      - severidad ('CRITICA', 'ADVERTENCIA', 'INFORMATIVA', 'POSITIVA')
      - problema_detectado
      - evidencia_estadistica
      - metricas_afectadas
      - nivel_confianza
      - posible_causa
      - recomendacion_investigacion
    """
    diagnosticos: List[Dict[str, Any]] = []

    gp = analytics_data.get("general_performance", {})
    hud = analytics_data.get("gameplay_hud", {})
    strat = analytics_data.get("strategic_analysis", {})
    opp = analytics_data.get("opponent_analysis", {})
    ver_comp = analytics_data.get("version_comparison", {})
    decisions = analytics_data.get("recent_decisions", [])

    n_hands = int(gp.get("hands_played") or 0)
    n_decs = int(strat.get("total_decisions") or 0)

    def _confianza_por_muestra(n: int) -> str:
        if n >= 100:
            return f"ALTA (n = {n})"
        if n >= 30:
            return f"MEDIA (n = {n})"
        return f"BAJA / EXPLORATORIA (n = {n} < 30)"

    # 1. Diagnóstico de Tamaño de Muestra / Separación Live vs Benchmark
    if n_hands < 30:
        diagnosticos.append({
            "id_diagnostico": "DIAG_SAMPLE_SIZE",
            "severidad": "ADVERTENCIA",
            "problema_detectado": "Tamaño de muestra insuficiente para inferencia paramétrica de Winrate real",
            "evidencia_estadistica": (
                f"Muestra actual de n = {n_hands} manos (< 30 mínimo requerido para Teorema Central del Límite). "
                f"IC 95% de Winrate: [{gp.get('bb_per_100_ci95', [0, 0])[0]:+.2f}, {gp.get('bb_per_100_ci95', [0, 0])[1]:+.2f}] BB/100."
            ),
            "metricas_afectadas": ["Winrate BB/100", "Sharpe Ratio", "Intervalo de Confianza 95%"],
            "nivel_confianza": _confianza_por_muestra(n_hands),
            "posible_causa": "Pocas manos registradas en el filtro o modo seleccionado (ej. sesiones en vivo recién iniciadas).",
            "recomendacion_investigacion": (
                "Acumular al menos 100 manos antes de evaluar rentabilidad financiera neta; "
                "mientras tanto, guiar el análisis por la pérdida media de EV por decisión (Regret) y errores estratégicos."
            ),
        })

    # 2. Brecha entre VPIP y PFR (Juego pasivo preflop / exceso de limps o cold-calls)
    vpip_pct = float(hud.get("vpip_pct") or 0.0)
    pfr_pct = float(hud.get("pfr_pct") or 0.0)
    vpip_pfr_gap = round(vpip_pct - pfr_pct, 2)
    if n_hands >= 10 and vpip_pfr_gap > 14.0:
        diagnosticos.append({
            "id_diagnostico": "DIAG_VPIP_PFR_GAP",
            "severidad": "ADVERTENCIA" if vpip_pfr_gap <= 22.0 else "CRITICA",
            "problema_detectado": "Brecha excesiva entre VPIP y PFR (tendencia pasiva preflop)",
            "evidencia_estadistica": (
                f"VPIP = {vpip_pct:.1f}% vs PFR = {pfr_pct:.1f}% (Brecha = {vpip_pfr_gap:.1f} puntos porcentuales; "
                f"rango óptimo en 3-Max/Spin & Go: 5% - 12% sobre n = {n_hands} manos)."
            ),
            "metricas_afectadas": ["VPIP%", "PFR%", "Iniciativa Postflop (CBet Opps)", "WWSF%"],
            "nivel_confianza": _confianza_por_muestra(n_hands),
            "posible_causa": (
                "El agente está igualando (Call/Limp) demasiadas manos marginales preflop en lugar de "
                "seleccionar entre Open-Raise/3-Bet o Fold disciplinado."
            ),
            "recomendacion_investigacion": (
                "Revisar los umbrales de `preflop_min_score` en defensa de ciegas (SB/BB) y verificar si el fallback "
                "rápido convierte subidas recomendadas en Calls pasivos."
            ),
        })

    # 3. Pérdidas concentradas en una posición específica (Positional Leak)
    pos_perf = strat.get("performance_by_position", {})
    for pos_name, pdata in pos_perf.items():
        pos_h = int(pdata.get("hands") or 0)
        pos_ev_bb100 = float(pdata.get("ev_bb_per_100") or 0.0)
        pos_real_bb100 = float(pdata.get("bb_per_100") or 0.0)
        pos_ev_lost = float(pdata.get("ev_lost_bb") or 0.0)
        # En BB se espera perder parte de la ciega obligatoria, pero en BTN o SB una pérdida fuerte de EV es crítica
        threshold_ev = -15.0 if pos_name == "BB" else (-5.0 if pos_name == "SB" else 0.0)
        if pos_h >= 10 and (pos_ev_bb100 < threshold_ev or (pos_ev_lost / max(1, pos_h)) * 100.0 > 25.0):
            diagnosticos.append({
                "id_diagnostico": f"DIAG_POS_LEAK_{pos_name}",
                "severidad": "CRITICA" if pos_ev_bb100 < threshold_ev - 10.0 else "ADVERTENCIA",
                "problema_detectado": f"Fuga de rentabilidad (Leak) concentrada en la posición {pos_name}",
                "evidencia_estadistica": (
                    f"En {pos_name} ({pos_h} manos): EV Winrate = {pos_ev_bb100:+.2f} BB/100, "
                    f"Winrate Real = {pos_real_bb100:+.2f} BB/100, EV perdido acumulado = {pos_ev_lost:.2f} BB "
                    f"(Alineación heurística-GTO: {pdata.get('gto_alignment_pct', 0):.1f}%)."
                ),
                "metricas_afectadas": [f"EV BB/100 ({pos_name})", f"Winrate Real ({pos_name})", "EV Lost BB"],
                "nivel_confianza": _confianza_por_muestra(pos_h),
                "posible_causa": (
                    f"Defensa demasiado amplia fuera de posición en {pos_name} o pagos postflop fuera de Pot Odds "
                    "sin ventaja posicional."
                ),
                "recomendacion_investigacion": (
                    f"Filtrar el Historial de Manos por posición = {pos_name} y auditar las decisiones con mayor "
                    "Regret (ΔEV) en Flop y Turn."
                ),
            })

    # 4. Exceso de faroles o errores de adaptación contra Calling Stations
    vs_profiles = opp.get("performance_vs_profiles", {})
    cs_data = vs_profiles.get("CALLING STATION", {})
    cs_decs = int(cs_data.get("decisions") or 0)
    cs_adapt_pct = float(cs_data.get("adaptation_pct") or 100.0)
    cs_err_pct = float(cs_data.get("error_rate_pct") or 0.0)
    if cs_decs >= 5 and (cs_adapt_pct < 85.0 or cs_err_pct > 12.0):
        diagnosticos.append({
            "id_diagnostico": "DIAG_BLUFF_VS_CALLING_STATION",
            "severidad": "ADVERTENCIA" if cs_adapt_pct >= 70.0 else "CRITICA",
            "problema_detectado": "Sub-adaptación o exceso de faroles frente a perfiles Calling Station",
            "evidencia_estadistica": (
                f"Contra Calling Stations ({cs_decs} decisiones): Tasa de adaptación = {cs_adapt_pct:.1f}%, "
                f"Tasa de error estratégico = {cs_err_pct:.1f}%, EV BB/100 = {cs_data.get('ev_bb_per_100', 0):+.2f}."
            ),
            "metricas_afectadas": ["Adaptación a Perfiles%", "EV Perdido Postflop", "W$SD%"],
            "nivel_confianza": _confianza_por_muestra(cs_decs),
            "posible_causa": (
                "Ejecución de apuestas de continuación (C-Bet) de farol sin equity frente a rivales con "
                "Fold-to-CBet extremadamente bajo (<15%)."
            ),
            "recomendacion_investigacion": (
                "Verificar que el perfil de `OpponentProfiler` llegue correctamente al prompt de decisión y eliminar "
                "semifaroles sin equity suficiente contra rivales etiquetados como CALLING STATION."
            ),
        })

    # 5. Oportunidades perdidas de valor (Missed Opportunities / Pasividad con alta Equity)
    missed_opps_count = int(strat.get("missed_opportunities_count") or 0)
    missed_opps_pct = round((missed_opps_count / max(1, n_decs)) * 100.0, 2) if n_decs > 0 else 0.0
    if n_decs >= 10 and missed_opps_count >= 2 and missed_opps_pct >= 4.0:
        diagnosticos.append({
            "id_diagnostico": "DIAG_MISSED_VALUE_OPPS",
            "severidad": "ADVERTENCIA",
            "problema_detectado": "Oportunidades perdidas de extracción de valor (Missed Value Bets)",
            "evidencia_estadistica": (
                f"Se detectaron {missed_opps_count} decisiones ({missed_opps_pct:.1f}% del total de {n_decs} decisiones) "
                "donde el bot eligió una línea pasiva (CHECK/CALL) teniendo equity claramente favorable para apostar/subir por valor."
            ),
            "metricas_afectadas": ["Oportunidades Perdidas", "Aggression Factor Postflop", "EV BB/100"],
            "nivel_confianza": _confianza_por_muestra(n_decs),
            "posible_causa": (
                "Exceso de precaución en Turn/River con manos de valor medio-fuerte (Top Pair buen kicker / Overpair) "
                "o fallbacks de UI cuando el sizing input no está visible."
            ),
            "recomendacion_investigacion": (
                "Inspeccionar las decisiones marcadas como `PASSIVE_DEVIATION` y `is_missed_opportunity=1` en la sección "
                "'Estrategia y Decisiones'."
            ),
        })

    # 6. Deterioro o Regresión del EV entre versiones experimentales
    versions_list = ver_comp.get("versions", [])
    for v_row in versions_list:
        cvp = v_row.get("comparison_vs_prev") or {}
        v_state = str(cvp.get("verdict_state") or "")
        delta_ev = float(cvp.get("delta_ev_bb_per_100") or 0.0)
        if v_state == "REGRESIÓN" or delta_ev <= -5.0:
            diagnosticos.append({
                "id_diagnostico": f"DIAG_VERSION_REGRESSION_{v_row.get('version_id')}",
                "severidad": "CRITICA",
                "problema_detectado": f"Deterioro de EV detectado en la versión v{v_row.get('version_id')} respecto a v{cvp.get('base_version')}",
                "evidencia_estadistica": (
                    f"ΔEV = {delta_ev:+.2f} BB/100, p-ajustado (Holm-Bonferroni) = {cvp.get('holm_corrected_p_value', 1.0):.4f}, "
                    f"Tamaño del efecto d = {cvp.get('cohens_d', 0.0):+.3f} (Estado: {v_state})."
                ),
                "metricas_afectadas": ["EV BB/100", "Regret Acumulado", "Puntuación Compuesta de Versión"],
                "nivel_confianza": _confianza_por_muestra(int(v_row.get("hands_evaluated") or 0)),
                "posible_causa": f"Los parámetros modificados en v{v_row.get('version_id')} deterioraron la selección de manos o el sizing postflop.",
                "recomendacion_investigacion": f"Revertir a la versión estable ({ver_comp.get('best_overall_statistical_version')}) y aislar los parámetros modificados en el Laboratorio de Experimentación.",
            })

    # 7. Diagnóstico positivo / evolutivo cuando la última versión muestra mejora confirmada
    if versions_list:
        last_v = versions_list[-1]
        last_cvp = last_v.get("comparison_vs_prev") or {}
        if last_cvp.get("verdict_state") == "MEJORA":
            diagnosticos.append({
                "id_diagnostico": f"DIAG_VERSION_IMPROVEMENT_{last_v.get('version_id')}",
                "severidad": "POSITIVA",
                "problema_detectado": f"Mejora estadística confirmada en versión activa v{last_v.get('version_id')}",
                "evidencia_estadistica": (
                    f"v{last_v.get('version_id')} supera a v{last_cvp.get('base_version')} por "
                    f"{last_cvp.get('delta_ev_bb_per_100', 0):+.2f} EV BB/100 "
                    f"(p-Holm = {last_cvp.get('holm_corrected_p_value', 1.0):.4f}, Cohen's d = {last_cvp.get('cohens_d', 0.0):+.2f}). "
                    f"Reducción de errores estratégicos: {last_cvp.get('delta_error_rate_pct', 0):+.1f}%."
                ),
                "metricas_afectadas": ["EV BB/100", "Alineación Heurística-GTO%", "Tasa de Error Estratégico"],
                "nivel_confianza": _confianza_por_muestra(int(last_v.get("hands_evaluated") or 0)),
                "posible_causa": "Distinción correcta entre pareja en mano vs pareja en mesa, uso de blockers y sizing dinámico postflop.",
                "recomendacion_investigacion": "Mantener v1.3 como configuración de producción y continuar monitoreando la estabilidad en sesiones en vivo.",
            })

    return diagnosticos
