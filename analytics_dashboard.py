"""
Centro de Control y Dashboard Profesional de Analíticas del Bot de Póker (Fase 4, 5, 6 y 7).

Incluye las 9 secciones requeridas:
1. Overview (KPIs, Diagnóstico Inteligente Fase 5, Tabla Principal Ordenable con Fórmulas Contextuales).
2. Rendimiento financiero (Bankroll, Winrate, EV vs Resultados Reales, All-in EV, Drawdown, Sharpe validado).
3. Estrategia y decisiones (Evaluación GTO vs Heurística, Frecuencias Mixtas, Regret, Posición, Calle, Bote, Stack, Oportunidades Perdidas).
4. Estadísticas HUD (Indicadores con Numerador, Denominador, Muestra, Fórmula y Gráfica de Evolución HUD).
5. Análisis de oponentes (Tabla real `oponentes` de SQLite, Rendimiento vs Arquetipos y Verificación Showdown).
6. Laboratorio de experimentación (Trazabilidad de semillas, `code_hash`, `config_hash`, condiciones controladas).
7. Comparación de versiones (Welch, Pareada, Cohen's d, Corrección Holm-Bonferroni y estados MEJORA / INCONCLUSO / REGRESIÓN).
8. Historial de manos (Auditoría de manos y decisiones con acción propuesta vs ejecutada, EV por alternativa y resultado).
9. Diagnóstico del sistema (Telemetría real P50, P95, P99 de latencia de decisión, encolado, escritura SQLite, memoria RSS e integridad DB).
"""
import argparse
import http.server
import json
import socketserver
import urllib.parse
import webbrowser
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from analytics_benchmark import ejecutar_benchmark_versiones
from analytics_db import AnalyticsStorage, DB_PATH
from analytics_engine import PokerAnalyticsEngine

DEFAULT_DASHBOARD_PATH = Path("logs/analytics_dashboard.html")


def _build_dashboard_html_content(payload: Dict[str, Any]) -> str:
    """Construye el documento HTML5 autónomo (Centro de Control de 9 secciones) con datos embebidos."""
    json_str = json.dumps(payload, ensure_ascii=False)

    return f"""<!DOCTYPE html>
<html lang="es">
<head>
    <meta charset="UTF-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1.0" />
    <title>Poker Bot Intelligence &amp; Analytics Control Center</title>
    <script src="/static/js/chart.umd.min.js"></script>
    <script>
        if (typeof Chart === 'undefined') {{
            document.write('<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.1/dist/chart.umd.min.js"><\\/script>');
        }}
    </script>
    <style>
        :root {{
            --bg-main: #080c14;
            --bg-card: #101726;
            --bg-card-alt: #162033;
            --bg-header: #0c1220;
            --border-color: #24324d;
            --text-main: #f8fafc;
            --text-muted: #94a3b8;
            --accent-blue: #38bdf8;
            --accent-indigo: #6366f1;
            --accent-green: #10b981;
            --accent-amber: #f59e0b;
            --accent-red: #ef4444;
            --accent-purple: #a855f7;
            --accent-cyan: #06b6d4;
        }}
        * {{ box-sizing: border-box; margin: 0; padding: 0; }}
        body {{
            background-color: var(--bg-main);
            color: var(--text-main);
            font-family: 'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            line-height: 1.5;
            padding-bottom: 60px;
        }}
        .top-bar {{
            background: linear-gradient(90deg, #0b1120 0%, #162238 100%);
            border-bottom: 1px solid var(--border-color);
            padding: 16px 28px;
            display: flex;
            justify-content: space-between;
            align-items: center;
            flex-wrap: wrap;
            gap: 14px;
            position: sticky;
            top: 0;
            z-index: 100;
            box-shadow: 0 4px 20px rgba(0, 0, 0, 0.55);
        }}
        .brand-title {{
            display: flex;
            align-items: center;
            gap: 12px;
        }}
        .brand-title h1 {{
            font-size: 1.22rem;
            font-weight: 700;
            letter-spacing: -0.02em;
            color: #fff;
        }}
        .brand-subtitle {{
            font-size: 0.78rem;
            color: var(--text-muted);
        }}
        .badge {{
            display: inline-flex;
            align-items: center;
            padding: 3px 10px;
            border-radius: 999px;
            font-size: 0.73rem;
            font-weight: 600;
            letter-spacing: 0.02em;
        }}
        .badge-green {{ background: rgba(16, 185, 129, 0.16); color: #34d399; border: 1px solid rgba(16, 185, 129, 0.35); }}
        .badge-amber {{ background: rgba(245, 158, 11, 0.16); color: #fbbf24; border: 1px solid rgba(245, 158, 11, 0.35); }}
        .badge-red {{ background: rgba(239, 68, 68, 0.16); color: #f87171; border: 1px solid rgba(239, 68, 68, 0.35); }}
        .badge-blue {{ background: rgba(56, 189, 248, 0.16); color: #38bdf8; border: 1px solid rgba(56, 189, 248, 0.35); }}
        .badge-purple {{ background: rgba(168, 85, 247, 0.16); color: #c084fc; border: 1px solid rgba(168, 85, 247, 0.35); }}

        .container {{
            max-width: 1640px;
            margin: 0 auto;
            padding: 18px 24px;
        }}

        /* Barra de navegación de las 9 secciones del Centro de Control */
        .nav-tabs {{
            display: flex;
            flex-wrap: wrap;
            gap: 8px;
            background: var(--bg-card);
            border: 1px solid var(--border-color);
            border-radius: 12px;
            padding: 10px 12px;
            margin-bottom: 16px;
        }}
        .nav-tab {{
            background: var(--bg-card-alt);
            color: var(--text-muted);
            border: 1px solid var(--border-color);
            padding: 7px 13px;
            border-radius: 8px;
            font-size: 0.8rem;
            font-weight: 600;
            cursor: pointer;
            transition: all 0.15s;
        }}
        .nav-tab:hover {{
            color: #fff;
            border-color: var(--accent-blue);
        }}
        .nav-tab.active {{
            background: linear-gradient(135deg, #0284c7, #4f46e5);
            color: #fff;
            border-color: transparent;
            box-shadow: 0 2px 10px rgba(56, 189, 248, 0.25);
        }}

        /* Barra de Filtros Multidimensionales */
        .filters-panel {{
            background: var(--bg-card);
            border: 1px solid var(--border-color);
            border-radius: 12px;
            padding: 14px 18px;
            margin-bottom: 18px;
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));
            gap: 12px;
            align-items: end;
        }}
        .filter-group label {{
            display: block;
            font-size: 0.72rem;
            text-transform: uppercase;
            letter-spacing: 0.05em;
            color: var(--text-muted);
            margin-bottom: 5px;
            font-weight: 600;
        }}
        .filter-group select, .filter-group input {{
            width: 100%;
            background: var(--bg-card-alt);
            border: 1px solid var(--border-color);
            color: var(--text-main);
            padding: 7px 10px;
            border-radius: 8px;
            font-size: 0.82rem;
            outline: none;
        }}
        .filter-group select:focus, .filter-group input:focus {{
            border-color: var(--accent-blue);
        }}
        .btn {{
            display: inline-flex;
            align-items: center;
            justify-content: center;
            gap: 6px;
            padding: 8px 13px;
            border-radius: 8px;
            font-size: 0.8rem;
            font-weight: 600;
            cursor: pointer;
            border: 1px solid transparent;
            transition: all 0.15s ease;
            text-decoration: none;
        }}
        .btn-primary {{ background: var(--accent-indigo); color: #fff; }}
        .btn-primary:hover {{ background: #4f46e5; }}
        .btn-outline {{
            background: var(--bg-card-alt);
            color: var(--text-main);
            border-color: var(--border-color);
        }}
        .btn-outline:hover {{ border-color: var(--accent-blue); color: var(--accent-blue); }}
        .btn-success {{ background: rgba(16, 185, 129, 0.18); color: #34d399; border-color: rgba(16, 185, 129, 0.35); }}
        .btn-success:hover {{ background: rgba(16, 185, 129, 0.28); }}

        /* Tarjetas KPI */
        .kpi-grid {{
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
            gap: 14px;
            margin-bottom: 20px;
        }}
        .kpi-card {{
            background: var(--bg-card);
            border: 1px solid var(--border-color);
            border-radius: 12px;
            padding: 15px 18px;
            position: relative;
            overflow: hidden;
        }}
        .kpi-card::before {{
            content: '';
            position: absolute;
            top: 0; left: 0; width: 4px; height: 100%;
            background: var(--accent-blue);
        }}
        .kpi-card.kpi-green::before {{ background: var(--accent-green); }}
        .kpi-card.kpi-amber::before {{ background: var(--accent-amber); }}
        .kpi-card.kpi-red::before {{ background: var(--accent-red); }}
        .kpi-card.kpi-purple::before {{ background: var(--accent-purple); }}
        .kpi-label {{
            font-size: 0.74rem;
            text-transform: uppercase;
            letter-spacing: 0.05em;
            color: var(--text-muted);
            font-weight: 600;
        }}
        .kpi-value {{
            font-size: 1.55rem;
            font-weight: 800;
            margin: 5px 0 4px 0;
            letter-spacing: -0.02em;
        }}
        .kpi-sub {{
            font-size: 0.76rem;
            color: var(--text-muted);
            display: flex;
            justify-content: space-between;
            align-items: center;
            gap: 6px;
        }}

        .alert-banner {{
            background: rgba(30, 41, 59, 0.75);
            border: 1px solid var(--border-color);
            border-left: 4px solid var(--accent-amber);
            border-radius: 10px;
            padding: 12px 18px;
            margin-bottom: 18px;
            display: flex;
            justify-content: space-between;
            align-items: center;
            flex-wrap: wrap;
            gap: 12px;
            font-size: 0.85rem;
        }}

        .section-header {{
            display: flex;
            justify-content: space-between;
            align-items: center;
            margin: 26px 0 12px 0;
            padding-bottom: 8px;
            border-bottom: 1px solid var(--border-color);
            flex-wrap: wrap;
            gap: 10px;
        }}
        .section-header h2 {{
            font-size: 1.08rem;
            font-weight: 700;
            color: #fff;
            display: flex;
            align-items: center;
            gap: 8px;
        }}
        .grid-2 {{
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(500px, 1fr));
            gap: 18px;
            margin-bottom: 20px;
        }}
        .grid-3 {{
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(360px, 1fr));
            gap: 18px;
            margin-bottom: 20px;
        }}
        .card {{
            background: var(--bg-card);
            border: 1px solid var(--border-color);
            border-radius: 12px;
            padding: 18px;
        }}
        .card-title {{
            font-size: 0.92rem;
            font-weight: 700;
            margin-bottom: 12px;
            color: #e2e8f0;
            display: flex;
            justify-content: space-between;
            align-items: center;
        }}
        .chart-container {{
            position: relative;
            height: 290px;
            width: 100%;
        }}

        /* Tablas de Datos Ordenables */
        .table-wrapper {{
            overflow-x: auto;
            border-radius: 10px;
            border: 1px solid var(--border-color);
            background: var(--bg-card);
            margin-bottom: 20px;
        }}
        table {{
            width: 100%;
            border-collapse: collapse;
            font-size: 0.82rem;
            text-align: left;
        }}
        th {{
            background: var(--bg-card-alt);
            color: var(--text-muted);
            font-weight: 600;
            text-transform: uppercase;
            font-size: 0.71rem;
            letter-spacing: 0.04em;
            padding: 10px 13px;
            border-bottom: 1px solid var(--border-color);
            white-space: nowrap;
            user-select: none;
        }}
        th.sortable {{
            cursor: pointer;
        }}
        th.sortable:hover {{
            color: var(--accent-blue);
            background: rgba(56, 189, 248, 0.08);
        }}
        td {{
            padding: 9px 13px;
            border-bottom: 1px solid rgba(51, 65, 85, 0.45);
            vertical-align: middle;
        }}
        tr:hover td {{
            background: rgba(255, 255, 255, 0.025);
        }}
        .text-pos {{ color: #34d399; font-weight: 600; }}
        .text-neg {{ color: #f87171; font-weight: 600; }}
        .text-neutral {{ color: #94a3b8; }}
        .mono {{ font-family: 'JetBrains Mono', 'Fira Code', monospace; font-size: 0.78rem; }}

        .diag-card {{
            background: var(--bg-card-alt);
            border: 1px solid var(--border-color);
            border-left: 4px solid var(--accent-blue);
            border-radius: 10px;
            padding: 14px 16px;
            margin-bottom: 12px;
        }}
        .diag-card.CRITICA {{ border-left-color: var(--accent-red); }}
        .diag-card.ADVERTENCIA {{ border-left-color: var(--accent-amber); }}
        .diag-card.POSITIVA {{ border-left-color: var(--accent-green); }}

        .hud-mini-grid {{
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(135px, 1fr));
            gap: 10px;
            margin-bottom: 16px;
        }}
        .hud-box {{
            background: var(--bg-card-alt);
            border: 1px solid var(--border-color);
            border-radius: 8px;
            padding: 10px 12px;
            text-align: center;
        }}
        .hud-box .lbl {{ font-size: 0.7rem; color: var(--text-muted); text-transform: uppercase; font-weight: 600; }}
        .hud-box .val {{ font-size: 1.2rem; font-weight: 800; color: var(--accent-blue); margin-top: 2px; }}
        .hud-box .ref {{ font-size: 0.68rem; color: var(--text-muted); margin-top: 2px; }}

        @media (max-width: 768px) {{
            .grid-2, .grid-3 {{ grid-template-columns: 1fr; }}
        }}
    </style>
</head>
<body>

    <header class="top-bar">
        <div class="brand-title">
            <div style="font-size: 1.75rem;">♠️</div>
            <div>
                <h1>CENTRO DE CONTROL DE INTELIGENCIA Y ANALÍTICAS — BOT DE PÓKER</h1>
                <div class="brand-subtitle">
                    Auditoría Estadística • Evaluación EV / Heurística-GTO • Laboratorio de Versiones (Holm-Bonferroni) • Telemetría P50/P95/P99
                </div>
            </div>
        </div>
        <div style="display:flex; gap:8px; align-items:center; flex-wrap:wrap;">
            <div style="display:flex; align-items:center; gap:6px; background:var(--bg-card); border:1px solid var(--border-color); padding:4px 10px; border-radius:6px;">
                <span style="font-size:0.75rem; color:var(--text-muted); font-weight:700;">🤖 MODELO:</span>
                <select id="botModelSelect" onchange="switchBotModel(this.value)" style="background:transparent; color:var(--text-main); border:none; font-size:0.8rem; font-weight:600; cursor:pointer; outline:none;">
                    <option value="gemini-3.1-flash-lite">⚡ Gemini 3.1 Flash-Lite (Cloud)</option>
                    <option value="gemma-4b-local">💻 Gemma 4B Local (GGUF)</option>
                    <option value="gto-heuristic">🛡️ Motor Heurístico GTO (0ms)</option>
                </select>
            </div>
            <span id="liveSyncBadge" class="badge badge-green">🟢 Auto-Sync Activo</span>
            <span id="dataSourceBadge" class="badge badge-blue">Modo: Cargando...</span>
            <span id="codeHashBadge" class="badge badge-purple mono">Hash: --</span>
            <button class="btn btn-outline" onclick="exportFilteredData('hands')">📥 CSV Manos</button>
            <button class="btn btn-outline" onclick="exportFilteredData('decisions')">📥 CSV Decisiones</button>
            <button class="btn btn-outline" onclick="exportFilteredData('metrics')">📥 CSV Métricas</button>
            <button class="btn btn-outline" onclick="exportFilteredData('comparisons')">📥 CSV Versiones</button>
            <button class="btn btn-success" onclick="exportFilteredJSON()">📦 JSON Trazable</button>
        </div>
    </header>

    <main class="container">
        <!-- NAVEGACIÓN DE LAS 9 SECCIONES DEL CENTRO DE CONTROL -->
        <nav class="nav-tabs" id="sectionTabs">
            <button class="nav-tab active" onclick="switchSection('ALL', this)">📋 Vista Completa (9 Secciones)</button>
            <button class="nav-tab" onclick="switchSection('sec-1', this)">1. Overview</button>
            <button class="nav-tab" onclick="switchSection('sec-2', this)">2. Rendimiento Financiero</button>
            <button class="nav-tab" onclick="switchSection('sec-3', this)">3. Estrategia y Decisiones</button>
            <button class="nav-tab" onclick="switchSection('sec-4', this)">4. Estadísticas HUD</button>
            <button class="nav-tab" onclick="switchSection('sec-5', this)">5. Análisis de Oponentes</button>
            <button class="nav-tab" onclick="switchSection('sec-6', this)">6. Laboratorio Experimentación</button>
            <button class="nav-tab" onclick="switchSection('sec-7', this)">7. Comparación de Versiones</button>
            <button class="nav-tab" onclick="switchSection('sec-8', this)">8. Historial de Manos</button>
            <button class="nav-tab" onclick="switchSection('sec-9', this)">9. Diagnóstico del Sistema</button>
        </nav>

        <!-- BARRA DE FILTROS INTERACTIVOS -->
        <section class="filters-panel">
            <div class="filter-group">
                <label>Origen de Datos (Resultado en Vivo vs Benchmark Sintético)</label>
                <select id="fDataSource" onchange="applyDashboardFilters()">
                    <option value="LIVE" selected>Partidas Reales en Vivo (is_synthetic=0)</option>
                    <option value="ALL">Todas las Fuentes (En Vivo + Laboratorio)</option>
                    <option value="BENCHMARK">Laboratorio Sintético (is_synthetic=1)</option>
                </select>
            </div>
            <div class="filter-group">
                <label>Versión Experimental</label>
                <select id="fVersion" onchange="applyDashboardFilters()">
                    <option value="ALL">Todas las versiones</option>
                </select>
            </div>
            <div class="filter-group">
                <label>Sesión</label>
                <select id="fSession" onchange="applyDashboardFilters()">
                    <option value="ALL">Todas las sesiones</option>
                </select>
            </div>
            <div class="filter-group">
                <label>Posición</label>
                <select id="fPosition" onchange="applyDashboardFilters()">
                    <option value="ALL">Todas (BTN / SB / BB)</option>
                    <option value="BTN">BTN (Button / Dealer)</option>
                    <option value="SB">SB (Small Blind)</option>
                    <option value="BB">BB (Big Blind)</option>
                </select>
            </div>
            <div class="filter-group">
                <label>Calle / Etapa</label>
                <select id="fStreet" onchange="applyDashboardFilters()">
                    <option value="ALL">Todas las calles</option>
                    <option value="PREFLOP">Preflop</option>
                    <option value="FLOP">Flop</option>
                    <option value="TURN">Turn</option>
                    <option value="RIVER">River</option>
                </select>
            </div>
            <div class="filter-group">
                <label>Perfil de Oponente</label>
                <select id="fProfile" onchange="applyDashboardFilters()">
                    <option value="ALL">Todos los perfiles</option>
                    <option value="CALLING STATION">Calling Station</option>
                    <option value="MANIAC">Maniac</option>
                    <option value="NIT">Nit (Roca)</option>
                    <option value="REGULAR EQUILIBRADO">Regular Equilibrado</option>
                    <option value="JUGADOR NUEVO">Jugador Nuevo</option>
                </select>
            </div>
            <div class="filter-group">
                <label>Fecha Desde</label>
                <input type="date" id="fDateFrom" onchange="applyDashboardFilters()" />
            </div>
            <div class="filter-group">
                <label>Fecha Hasta</label>
                <input type="date" id="fDateTo" onchange="applyDashboardFilters()" />
            </div>
            <div class="filter-group">
                <label>Últimas N Manos</label>
                <select id="fLastN" onchange="applyDashboardFilters()">
                    <option value="0">Todas las manos</option>
                    <option value="30">Últimas 30 manos</option>
                    <option value="60">Últimas 60 manos</option>
                    <option value="120">Últimas 120 manos</option>
                    <option value="240">Últimas 240 manos</option>
                </select>
            </div>
            <div class="filter-group">
                <button class="btn btn-primary" style="width:100%;" onclick="resetDashboardFilters()">🔄 Limpiar Filtros</button>
            </div>
        </section>

        <!-- BANNER DE SEPARACIÓN DE DATOS Y CONFIABILIDAD ESTADÍSTICA -->
        <div id="statBanner" class="alert-banner"></div>

        <!-- CENTRO DE CONTROL DEL AGENTE EN VIVO -->
        <div style="display: flex; gap: 12px; align-items: center; background: #101726; padding: 14px 20px; border-radius: 12px; border: 1px solid #24324d; margin-bottom: 20px;">
            <span style="font-weight: 700; color: #f8fafc; font-size: 0.9rem;">🎮 CENTRO DE CONTROL DEL BOT EN VIVO:</span>
            <button onclick="controlBotAction('pause')" style="background: #f59e0b; color: #000; border: none; padding: 8px 16px; border-radius: 8px; font-weight: 700; font-size: 0.82rem; cursor: pointer;">⏸️ Pausar Agente</button>
            <button onclick="controlBotAction('resume')" style="background: #10b981; color: #fff; border: none; padding: 8px 16px; border-radius: 8px; font-weight: 700; font-size: 0.82rem; cursor: pointer;">▶️ Reanudar Agente</button>
            <button onclick="controlBotAction('rejoin')" style="background: #38bdf8; color: #000; border: none; padding: 8px 16px; border-radius: 8px; font-weight: 700; font-size: 0.82rem; cursor: pointer;">🔄 Forzar Nueva Partida</button>
            <span id="bot-live-status-indicator" class="badge badge-green" style="margin-left: auto; font-size: 0.8rem; padding: 6px 14px;">🟢 AGENTE EN VIVO / ACTIVO</span>
        </div>

        <!-- ================================================================ -->
        <!-- SECCIÓN 0: MONITOREO DE MESA Y OPONENTES EN VIVO (LIVE HUD)      -->
        <!-- ================================================================ -->
        <div id="sec-live" class="control-section" style="margin-bottom: 25px;">
            <div class="section-header">
                <h2>📡 0. Monitoreo de Mesa y Oponentes en Vivo (Live HUD &amp; Match Tracking)</h2>
                <span class="badge badge-green">Actualización en vivo a 3s</span>
            </div>

            <!-- Estado de la Mano y Capacidad de Mesa -->
            <div class="card" style="border-left: 4px solid var(--accent-blue); margin-bottom: 16px;">
                <div class="card-title">
                    <span>🎴 Estado de la Mano Actual (#<span id="live-hand-num">1</span>) — <span id="live-modalidad" style="color:#38bdf8;">Twister 3-Max</span></span>
                    <span class="badge badge-purple" id="live-street-badge">PREFLOP</span>
                </div>
                <div style="display: grid; grid-template-columns: repeat(auto-fit, minmax(170px, 1fr)); gap: 10px; margin-bottom: 12px;">
                    <div style="background: var(--bg-card-alt); padding: 10px; border-radius: 8px;">
                        <div style="font-size: 0.72rem; color: var(--text-muted);">MIS CARTAS:</div>
                        <div style="font-size: 1.15rem; font-weight: 800; color: #38bdf8;" id="live-my-cards">Esperando cartas...</div>
                    </div>
                    <div style="background: var(--bg-card-alt); padding: 10px; border-radius: 8px;">
                        <div style="font-size: 0.72rem; color: var(--text-muted);">MESA COMUNITARIA:</div>
                        <div style="font-size: 1.15rem; font-weight: 800; color: #f59e0b;" id="live-board-cards">Ninguna</div>
                    </div>
                    <div style="background: var(--bg-card-alt); padding: 10px; border-radius: 8px;">
                        <div style="font-size: 0.72rem; color: var(--text-muted);">ASIENTOS / ACTIVOS / EN MANO:</div>
                        <div style="font-size: 1.05rem; font-weight: 800; color: #e2e8f0;" id="live-table-counts">0/3 ocup. · 0 act. · 0 en mano</div>
                    </div>
                    <div style="background: var(--bg-card-alt); padding: 10px; border-radius: 8px;">
                        <div style="font-size: 0.72rem; color: var(--text-muted);">OPONENTES REALES:</div>
                        <div style="font-size: 1.05rem; font-weight: 800; color: #f59e0b;" id="live-opponents-count">0 en mesa (0 en mano)</div>
                    </div>
                </div>
                <div style="display: flex; flex-wrap: wrap; gap: 18px; justify-content: space-between; font-size: 0.85rem; color: #e2e8f0;">
                    <span>💰 Bote Actual: <strong id="live-pot" style="color:#10b981;">$0</strong></span>
                    <span>📍 Posición Hero: <strong id="live-pos" style="color:#a855f7;">Desconocida</strong></span>
                    <span>🪑 Asientos Disponibles: <strong id="live-seats-free" style="color:#38bdf8;">-</strong></span>
                </div>
            </div>

            <!-- NUEVO PANEL MULTIJUGADOR: PERFILES DE OPONENTES EN VIVO -->
            <div class="card" style="border-left: 4px solid var(--accent-amber); margin-bottom: 16px;">
                <div class="card-title" style="flex-wrap: wrap; gap: 10px;">
                    <span>👥 PERFILES DE OPONENTES EN VIVO</span>
                    <div style="display: flex; gap: 6px; flex-wrap: wrap;" id="live-opp-filter-bar">
                        <button type="button" class="btn-filter-live active" data-live-filter="all" onclick="setLiveOpponentFilter('all')" style="padding:4px 10px; border-radius:6px; border:1px solid var(--border-color); background:#1e293b; color:#f8fafc; cursor:pointer; font-size:0.78rem;">Todos en mesa</button>
                        <button type="button" class="btn-filter-live" data-live-filter="active" onclick="setLiveOpponentFilter('active')" style="padding:4px 10px; border-radius:6px; border:1px solid var(--border-color); background:#0f172a; color:#94a3b8; cursor:pointer; font-size:0.78rem;">Activos</button>
                        <button type="button" class="btn-filter-live" data-live-filter="inactive" onclick="setLiveOpponentFilter('inactive')" style="padding:4px 10px; border-radius:6px; border:1px solid var(--border-color); background:#0f172a; color:#94a3b8; cursor:pointer; font-size:0.78rem;">Inactivos / Fold / Ausentes</button>
                        <button type="button" class="btn-filter-live" data-live-filter="historical" onclick="setLiveOpponentFilter('historical')" style="padding:4px 10px; border-radius:6px; border:1px solid var(--border-color); background:#0f172a; color:#94a3b8; cursor:pointer; font-size:0.78rem;">Perfiles históricos (BDD)</button>
                    </div>
                </div>
                <div id="live-opponents-grid" style="display: grid; grid-template-columns: repeat(auto-fit, minmax(310px, 1fr)); gap: 12px; margin-top: 8px;">
                    <div style="background: var(--bg-card-alt); padding: 14px; border-radius: 8px; color: var(--text-muted); font-size: 0.85rem;">
                        Esperando detección de oponentes en la mesa...
                    </div>
                </div>
            </div>

            <!-- Feed de Eventos en Vivo -->
            <div class="card">
                <div class="card-title">
                    <span>📜 Feed de Eventos y Secuencia Cronológica de la Partida en Vivo</span>
                    <span class="badge badge-blue">Log en vivo</span>
                </div>
                <div id="live-events-feed" style="background: #080c14; border: 1px solid var(--border-color); border-radius: 8px; padding: 12px; font-family: monospace; font-size: 0.8rem; height: 140px; overflow-y: auto; color: #38bdf8;">
                    [00:00:00] Esperando conexión de partida...
                </div>
            </div>
        </div>

        <!-- ================================================================ -->
        <!-- SECCIÓN 1: OVERVIEW (KPIs + DIAGNÓSTICOS + TABLA PRINCIPAL)      -->
        <!-- ================================================================ -->
        <div id="sec-1" class="control-section">
            <div class="section-header">
                <h2>1. Overview Ejecutivo y Diagnóstico Inteligente</h2>
                <span class="badge badge-blue">Haz clic en cualquier encabezado de tabla para ordenar ▲▼</span>
            </div>

            <section id="kpiGrid" class="kpi-grid"></section>

            <div class="card" style="margin-bottom:20px;">
                <div class="card-title">
                    <span>🧠 Diagnóstico Inteligente Automático (Fase 5 — Basado en Evidencia Estadística, Read-Only)</span>
                    <span class="badge badge-purple" id="diagCountBadge">0 observaciones</span>
                </div>
                <div id="intelligentDiagnosticsContainer"></div>
            </div>

            <div class="card">
                <div class="card-title">
                    <span>📊 Tabla Principal Centralizada de Analíticas (Ordenable + Definición Matemática Contextual)</span>
                    <input type="text" id="metricSearchInput" placeholder="🔍 Filtrar métrica o fórmula..." oninput="renderMainMetricsTable()" style="background:#162033;border:1px solid #24324d;color:#fff;padding:5px 10px;border-radius:6px;font-size:0.8rem;width:250px;" />
                </div>
                <div class="table-wrapper" style="margin-bottom:0;">
                    <table id="mainMetricsTable">
                        <thead>
                            <tr>
                                <th class="sortable" onclick="sortTableBy('main', 'category')">Categoría ⇅</th>
                                <th class="sortable" onclick="sortTableBy('main', 'metric_name')">Métrica / Indicador ⇅</th>
                                <th class="sortable" onclick="sortTableBy('main', 'value_num')">Valor Observado ⇅</th>
                                <th>IC 95% / Referencia</th>
                                <th class="sortable" onclick="sortTableBy('main', 'numerator')">Numerador / Denom. ⇅</th>
                                <th class="sortable" onclick="sortTableBy('main', 'sample_size')">Muestra (n) ⇅</th>
                                <th>Fórmula Matemática Documentada</th>
                                <th>Explicación Contextual</th>
                            </tr>
                        </thead>
                        <tbody id="mainMetricsTableBody"></tbody>
                    </table>
                </div>
            </div>
        </div>

        <!-- ================================================================ -->
        <!-- SECCIÓN 2: RENDIMIENTO FINANCIERO Y ESTADÍSTICO                  -->
        <!-- ================================================================ -->
        <div id="sec-2" class="control-section">
            <div class="section-header">
                <h2>2. Rendimiento Financiero, Varianza y Expectativa Matemática (EV vs Real)</h2>
                <span class="badge badge-green">Separación Estricta: Resultados Observados vs. All-in EV / EV Ajustado</span>
            </div>

            <div class="grid-2">
                <div class="card">
                    <div class="card-title">
                        <span>Gráfica 1: Evolución del Bankroll (Real vs EV Ajustado vs All-in EV)</span>
                        <span class="badge badge-green">Fichas</span>
                    </div>
                    <div class="chart-container">
                        <canvas id="chartBankroll"></canvas>
                    </div>
                </div>
                <div class="card">
                    <div class="card-title">
                        <span>Gráfica 2: Evolución del Winrate (BB/100 Acumulado vs Media Móvil 25m)</span>
                        <span class="badge badge-blue">BB / 100</span>
                    </div>
                    <div class="chart-container">
                        <canvas id="chartWinrate"></canvas>
                    </div>
                </div>
            </div>

            <div class="grid-2">
                <div class="card">
                    <div class="card-title">
                        <span>Gráfica 3: EV frente a Resultados Reales (Acumulado en BB y Desviación de Varianza)</span>
                        <span class="badge badge-amber">Teorema Fundamental</span>
                    </div>
                    <div class="chart-container">
                        <canvas id="chartEvVsReal"></canvas>
                    </div>
                </div>
                <div class="card">
                    <div class="card-title">
                        <span>Desglose Financiero, Riesgo, Drawdown y Supuestos de Sharpe</span>
                        <span class="badge badge-purple">Riesgo y Varianza</span>
                    </div>
                    <div id="financialSummaryBox"></div>
                </div>
            </div>
        </div>

        <!-- ================================================================ -->
        <!-- SECCIÓN 3: ESTRATEGIA, EVALUACIÓN GTO Y DECISIONES               -->
        <!-- ================================================================ -->
        <div id="sec-3" class="control-section">
            <div class="section-header">
                <h2>3. Calidad Estratégica, Evaluación Heurística-GTO y Desgloses Multidimensionales</h2>
                <span class="badge badge-purple">Posición • Calle • Tamaño de Bote • Profundidad de Stack • Oportunidades Perdidas</span>
            </div>

            <div class="card" style="margin-bottom:18px;" id="gtoEvaluationCard"></div>

            <div class="grid-3">
                <div class="card">
                    <div class="card-title">
                        <span>Gráfica 5: Distribución de Acciones Ejecutadas</span>
                        <span class="badge badge-purple">Fold / Check / Call / Bet / Raise</span>
                    </div>
                    <div class="chart-container">
                        <canvas id="chartActions"></canvas>
                    </div>
                </div>
                <div class="card">
                    <div class="card-title">
                        <span>Gráfica 6: Rendimiento por Posición (BTN, SB, BB)</span>
                        <span class="badge badge-green">Real vs EV BB/100</span>
                    </div>
                    <div class="chart-container">
                        <canvas id="chartPositions"></canvas>
                    </div>
                </div>
                <div class="card">
                    <div class="card-title">
                        <span>Gráfica 7: Calidad y Pérdida de EV por Calle</span>
                        <span class="badge badge-amber">Preflop / Flop / Turn / River</span>
                    </div>
                    <div class="chart-container">
                        <canvas id="chartStreets"></canvas>
                    </div>
                </div>
            </div>

            <div class="grid-2">
                <div class="card">
                    <div class="card-title"><span>Rendimiento por Posición y Etapa de Juego</span></div>
                    <div class="table-wrapper" style="margin-bottom:0;">
                        <table>
                            <thead>
                                <tr>
                                    <th>Dimensión</th>
                                    <th>Manos / Dec.</th>
                                    <th>Win% / VPIP%</th>
                                    <th>Winrate Real</th>
                                    <th>EV BB/100</th>
                                    <th>Calidad EV/GTO%</th>
                                    <th>Regret (EV Lost)</th>
                                </tr>
                            </thead>
                            <tbody id="posStreetBreakdownBody"></tbody>
                        </table>
                    </div>
                </div>
                <div class="card">
                    <div class="card-title"><span>Rendimiento por Tamaño de Bote y Profundidad de Stack (Fase 2.C)</span></div>
                    <div class="table-wrapper" style="margin-bottom:0;">
                        <table>
                            <thead>
                                <tr>
                                    <th>Segmento (Bote / Stack)</th>
                                    <th>Manos</th>
                                    <th>Decisiones</th>
                                    <th>Winrate Real</th>
                                    <th>EV BB/100</th>
                                    <th>Alineación EV/GTO%</th>
                                    <th>Tasa Error%</th>
                                    <th>Regret Total</th>
                                </tr>
                            </thead>
                            <tbody id="potStackBreakdownBody"></tbody>
                        </table>
                    </div>
                </div>
            </div>
        </div>

        <!-- ================================================================ -->
        <!-- SECCIÓN 4: ESTADÍSTICAS HUD Y EVOLUCIÓN TEMPORAL                 -->
        <!-- ================================================================ -->
        <div id="sec-4" class="control-section">
            <div class="section-header">
                <h2>4. Estadísticas HUD Documentadas y Evolución Temporal</h2>
                <span class="badge badge-blue">Numerador • Denominador • Muestra (n) • IC 95% de Wilson • Fórmula</span>
            </div>

            <div id="hudMiniGrid" class="hud-mini-grid"></div>

            <div class="grid-2">
                <div class="card">
                    <div class="card-title">
                        <span>Gráfica 8A: Evolución de los Indicadores HUD por Bloques de Manos</span>
                        <span class="badge badge-blue">VPIP / PFR / CBet / WTSD / W$SD</span>
                    </div>
                    <div class="chart-container">
                        <canvas id="chartHudEvolution"></canvas>
                    </div>
                </div>
                <div class="card">
                    <div class="card-title">
                        <span>Gráfica 8B: Evolución de la Calidad Estratégica y Adaptación</span>
                        <span class="badge badge-green">Alineación EV/GTO vs Tasa de Error</span>
                    </div>
                    <div class="chart-container">
                        <canvas id="chartQualityEvolution"></canvas>
                    </div>
                </div>
            </div>

            <div class="table-wrapper">
                <table>
                    <thead>
                        <tr>
                            <th>Indicador HUD</th>
                            <th>Valor</th>
                            <th>Numerador</th>
                            <th>Denominador</th>
                            <th>Muestra (n)</th>
                            <th>IC 95% (Wilson)</th>
                            <th>Rango Óptimo 3-Max</th>
                            <th>Definición Matemática</th>
                        </tr>
                    </thead>
                    <tbody id="hudDefinitionsBody"></tbody>
                </table>
            </div>
        </div>

        <!-- ================================================================ -->
        <!-- SECCIÓN 5: ANÁLISIS DE OPONENTES Y PERFILADO REAL                -->
        <!-- ================================================================ -->
        <div id="sec-5" class="control-section">
            <div class="section-header">
                <h2>5. Análisis de Oponentes, Rendimiento contra Perfiles y Verificación Showdown</h2>
                <span class="badge badge-green">Integrado con tabla `oponentes` de `opponents_profiler.py`</span>
            </div>

            <div class="grid-2">
                <div class="table-wrapper" style="margin-bottom:0;">
                    <table>
                        <thead>
                            <tr>
                                <th>Perfil de Oponente</th>
                                <th>Manos</th>
                                <th>Decisiones</th>
                                <th>Win%</th>
                                <th>Beneficio Neto</th>
                                <th>Winrate Real (BB/100)</th>
                                <th>EV Winrate (BB/100)</th>
                                <th>Adaptación%</th>
                                <th>Tasa Error%</th>
                            </tr>
                        </thead>
                        <tbody id="oppProfilesTableBody"></tbody>
                    </table>
                </div>
                <div class="table-wrapper" style="margin-bottom:0;">
                    <table>
                        <thead>
                            <tr>
                                <th>Jugador Real en DB (`oponentes`)</th>
                                <th>Manos Registradas</th>
                                <th>VPIP / PFR / AF</th>
                                <th>Folds ante Raise</th>
                                <th>Últimas Cartas Showdown</th>
                                <th>Última Actualización</th>
                            </tr>
                        </thead>
                        <tbody id="oppRealDbTableBody"></tbody>
                    </table>
                </div>
            </div>
        </div>

        <!-- ================================================================ -->
        <!-- SECCIÓN 6: LABORATORIO DE EXPERIMENTACIÓN CONTROLADA (FASE 3)    -->
        <!-- ================================================================ -->
        <div id="sec-6" class="control-section">
            <div class="section-header">
                <h2>6. Laboratorio de Experimentación Controlada (Escenarios Pareados y Trazabilidad)</h2>
                <span class="badge badge-purple">Common Random Numbers • Hash SHA-256 de Código y Configuración</span>
            </div>

            <div class="card" style="margin-bottom:16px;" id="labConditionsCard"></div>

            <div class="table-wrapper">
                <table>
                    <thead>
                        <tr>
                            <th>ID Experimento</th>
                            <th>Versión</th>
                            <th>Fecha</th>
                            <th>Hash Código / Config</th>
                            <th>Semillas (Seeds)</th>
                            <th>Escenarios Pareados</th>
                            <th>EV BB/100 [IC 95%]</th>
                            <th>ΔEV vs Previa [IC 95%]</th>
                            <th>p-Holm / Cohen's d</th>
                            <th>Estado Experimental</th>
                        </tr>
                    </thead>
                    <tbody id="experimentsLabBody"></tbody>
                </table>
            </div>
        </div>

        <!-- ================================================================ -->
        <!-- SECCIÓN 7: COMPARACIÓN CIENTÍFICA DE VERSIONES                   -->
        <!-- ================================================================ -->
        <div id="sec-7" class="control-section">
            <div class="section-header">
                <h2>7. Comparación Estadística entre Versiones (Welch, Pareada y Holm-Bonferroni)</h2>
                <span class="badge badge-green">Estados Estrictos: MEJORA • INCONCLUSO • REGRESIÓN</span>
            </div>

            <div class="card" style="margin-bottom:18px;">
                <div class="card-title">
                    <span>Gráfica 4: Comparativa Multiversión (Winrate Real vs EV BB/100 vs Alineación GTO%)</span>
                    <span class="badge badge-purple">Duplicate Poker Benchmark</span>
                </div>
                <div class="chart-container">
                    <canvas id="chartVersions"></canvas>
                </div>
            </div>

            <div class="table-wrapper">
                <table>
                    <thead>
                        <tr>
                            <th class="sortable" onclick="sortTableBy('versions', 'version_id')">Versión ⇅</th>
                            <th>Descripción y Parámetros Modificados</th>
                            <th class="sortable" onclick="sortTableBy('versions', 'hands_evaluated')">Muestra ⇅</th>
                            <th class="sortable" onclick="sortTableBy('versions', 'bb_per_100')">Winrate Real [IC 95%] ⇅</th>
                            <th class="sortable" onclick="sortTableBy('versions', 'ev_bb_per_100')">EV BB/100 [IC 95%] ⇅</th>
                            <th class="sortable" onclick="sortTableBy('versions', 'gto_alignment_pct')">GTO% / Error% ⇅</th>
                            <th>ΔEV / p-Holm / Cohen's d</th>
                            <th>Métricas Mejoradas / Empeoradas</th>
                            <th>Estado (MEJORA / INCONCLUSO / REGRESIÓN)</th>
                        </tr>
                    </thead>
                    <tbody id="versionTableBody"></tbody>
                </table>
            </div>
        </div>

        <!-- ================================================================ -->
        <!-- SECCIÓN 8: HISTORIAL DE MANOS Y AUDITORÍA DE DECISIONES          -->
        <!-- ================================================================ -->
        <div id="sec-8" class="control-section">
            <div class="section-header">
                <h2>8. Historial de Manos y Auditoría de Calidad de Decisiones (EV Independiente del Desenlace)</h2>
                <span class="badge badge-amber">Registra Acción Propuesta IA vs. Acción Realmente Ejecutada en DOM</span>
            </div>

            <div class="table-wrapper">
                <table>
                    <thead>
                        <tr>
                            <th>Mano / Origen</th>
                            <th>Pos / Calle</th>
                            <th>Cartas / Board</th>
                            <th>Bote / Stack</th>
                            <th>Acción Ejecutada (Propuesta)</th>
                            <th>Recomendación Heurística-GTO</th>
                            <th>EV por Acción Alternativa</th>
                            <th class="sortable" onclick="sortTableBy('decisions', 'ev_lost_bb')">Regret (ΔEV BB) ⇅</th>
                            <th>Calidad / Diagnóstico</th>
                            <th>Rival</th>
                            <th>Desenlace Mano</th>
                        </tr>
                    </thead>
                    <tbody id="decisionsTableBody"></tbody>
                </table>
            </div>
        </div>

        <!-- ================================================================ -->
        <!-- SECCIÓN 9: DIAGNÓSTICO DEL SISTEMA Y TELEMETRÍA P50/P95/P99      -->
        <!-- ================================================================ -->
        <div id="sec-9" class="control-section">
            <div class="section-header">
                <h2>9. Diagnóstico del Sistema, Confiabilidad Asíncrona y Telemetría P50 / P95 / P99 (Fase 6)</h2>
                <span class="badge badge-green">Medición Empírica (`time.perf_counter_ns`) + `PRAGMA integrity_check`</span>
            </div>

            <div id="systemDiagnosticsContainer"></div>
        </div>
    </main>

    <script>
        let RAW_DATA = {json_str};
        let chartInstances = {{}};
        let sortState = {{
            main: {{ col: 'category', asc: true }},
            versions: {{ col: 'version_id', asc: true }},
            decisions: {{ col: 'ev_lost_bb', asc: false }}
        }};
        let currentFilteredState = null;

        function switchSection(secId, btnEl) {{
            document.querySelectorAll('#sectionTabs .nav-tab').forEach(b => b.classList.remove('active'));
            if (btnEl) btnEl.classList.add('active');
            document.querySelectorAll('.control-section').forEach(sec => {{
                sec.style.display = (secId === 'ALL' || sec.id === secId) ? 'block' : 'none';
            }});
        }}

        function initFilters() {{
            document.getElementById('fDataSource').value = 'LIVE';
            const vSelect = document.getElementById('fVersion');
            const sSelect = document.getElementById('fSession');
            const versions = RAW_DATA.versions_history || [];
            versions.forEach(v => {{
                const opt = document.createElement('option');
                opt.value = v.version_id;
                opt.textContent = `v${{v.version_id}} — ${{v.description.substring(0, 42)}}...`;
                vSelect.appendChild(opt);
            }});

            const sessions = RAW_DATA.sessions_list || [];
            sessions.forEach(s => {{
                const opt = document.createElement('option');
                opt.value = s.session_id;
                const tag = s.is_synthetic ? '[LAB]' : '[LIVE]';
                opt.textContent = `${{tag}} ${{s.session_id}} (v${{s.version_id}} | ${{s.hands_played}}m)`;
                sSelect.appendChild(opt);
            }});

            // Si existen manos reales en vivo, informar claramente; por defecto mostrar todo etiquetado
            const cHash = (RAW_DATA.data_source_summary && RAW_DATA.data_source_summary.code_hash) || '--';
            document.getElementById('codeHashBadge').textContent = `Code Hash: ${{cHash}}`;
        }}

        function getActiveFilters() {{
            return {{
                data_source: document.getElementById('fDataSource').value,
                version_id: document.getElementById('fVersion').value,
                session_id: document.getElementById('fSession').value,
                position: document.getElementById('fPosition').value,
                street: document.getElementById('fStreet').value,
                opponent_profile: document.getElementById('fProfile').value,
                date_from: document.getElementById('fDateFrom').value,
                date_to: document.getElementById('fDateTo').value,
                last_n_hands: parseInt(document.getElementById('fLastN').value || '0', 10),
            }};
        }}

        function resetDashboardFilters() {{
            document.getElementById('fDataSource').value = 'LIVE';
            document.getElementById('fVersion').value = 'ALL';
            document.getElementById('fSession').value = 'ALL';
            document.getElementById('fPosition').value = 'ALL';
            document.getElementById('fStreet').value = 'ALL';
            document.getElementById('fProfile').value = 'ALL';
            document.getElementById('fDateFrom').value = '';
            document.getElementById('fDateTo').value = '';
            document.getElementById('fLastN').value = '0';
            applyDashboardFilters();
        }}

        function normPos(p) {{
            p = (p || '').toUpperCase();
            if (p.includes('BTN') || p.includes('DEALER') || p.includes('BUTTON')) return 'BTN';
            if (p.includes('SB') || p.includes('SMALL')) return 'SB';
            if (p.includes('BB') || p.includes('BIG')) return 'BB';
            return 'BTN';
        }}

        function normProf(p) {{
            p = (p || '').toUpperCase();
            if (p.includes('CALLING') || p.includes('STATION')) return 'CALLING STATION';
            if (p.includes('MANIAC')) return 'MANIAC';
            if (p.includes('NIT')) return 'NIT';
            if (p.includes('REGULAR') || p.includes('EQUILIBRADO')) return 'REGULAR EQUILIBRADO';
            return 'JUGADOR NUEVO';
        }}

        function computeStatsFromFiltered(hands, decisions) {{
            const n = hands.length;
            const nDec = decisions.length;
            const evalDecs = decisions.filter(d => d.evaluation_available !== 0 && d.decision_quality !== 'EVALUACION_NO_DISPONIBLE');
            const nEval = evalDecs.length;

            const sessSet = new Set(hands.map(h => h.session_id));
            const won = hands.filter(h => h.won_hand || (h.net_profit_chips || 0) > 0).length;
            const lost = hands.filter(h => (h.net_profit_chips || 0) < 0).length;
            const netChips = hands.reduce((a, h) => a + (parseFloat(h.net_profit_chips) || 0), 0);
            const netBB = hands.reduce((a, h) => a + (parseFloat(h.net_profit_bb) || 0), 0);
            const evChips = hands.reduce((a, h) => a + (parseFloat(h.ev_net_chips) || 0), 0);
            const evBB = hands.reduce((a, h) => a + (parseFloat(h.ev_net_bb) || 0), 0);
            const allinEvBB = hands.reduce((a, h) => a + (parseFloat(h.allin_ev_bb !== undefined ? h.allin_ev_bb : h.ev_net_bb) || 0), 0);

            const bb100 = n > 0 ? (netBB / n) * 100 : 0;
            const evBB100 = n > 0 ? (evBB / n) * 100 : 0;
            const allinBB100 = n > 0 ? (allinEvBB / n) * 100 : 0;

            let stdBB = 0;
            let varBB = 0;
            if (n > 1) {{
                const mean = netBB / n;
                varBB = hands.reduce((a, h) => a + Math.pow((parseFloat(h.net_profit_bb) || 0) - mean, 2), 0) / (n - 1);
                stdBB = Math.sqrt(varBB);
            }}
            const ciMargin = n > 1 ? 1.96 * (stdBB / Math.sqrt(n)) * 100 : 0;

            let curBr = 1500, curEvBr = 1500, curAllinBr = 1500, peakBr = 1500, maxDdChips = 0, cumBB = 0, cumEvBB = 0, cumAllinBB = 0, peakBB = 0, maxDdBB = 0;
            const curve = [];
            hands.forEach((h, i) => {{
                const idx = i + 1;
                const pc = parseFloat(h.net_profit_chips) || 0;
                const ec = parseFloat(h.ev_net_chips) || 0;
                const ac = parseFloat(h.allin_ev_chips !== undefined ? h.allin_ev_chips : ec) || 0;
                const pb = parseFloat(h.net_profit_bb) || 0;
                const eb = parseFloat(h.ev_net_bb) || 0;
                const ab = parseFloat(h.allin_ev_bb !== undefined ? h.allin_ev_bb : eb) || 0;
                curBr += pc; curEvBr += ec; curAllinBr += ac;
                cumBB += pb; cumEvBB += eb; cumAllinBB += ab;
                if (curBr > peakBr) peakBr = curBr;
                if (cumBB > peakBB) peakBB = cumBB;
                if (peakBr - curBr > maxDdChips) maxDdChips = peakBr - curBr;
                if (peakBB - cumBB > maxDdBB) maxDdBB = peakBB - cumBB;

                const slice = hands.slice(Math.max(0, idx - 25), idx);
                const rollBB100 = (slice.reduce((a, x) => a + (parseFloat(x.net_profit_bb) || 0), 0) / slice.length) * 100;
                curve.push({{
                    hand_index: idx,
                    version_id: h.version_id,
                    bankroll_real: +curBr.toFixed(2),
                    bankroll_ev: +curEvBr.toFixed(2),
                    bankroll_allin_ev: +curAllinBr.toFixed(2),
                    cumulative_bb: +cumBB.toFixed(2),
                    cumulative_ev_bb: +cumEvBB.toFixed(2),
                    cumulative_allin_ev_bb: +cumAllinBB.toFixed(2),
                    cumulative_bb100: +((cumBB / idx) * 100).toFixed(2),
                    cumulative_ev_bb100: +((cumEvBB / idx) * 100).toFixed(2),
                    rolling_bb100: +rollBB100.toFixed(2)
                }});
            }});

            const vpip = hands.filter(h => h.vpip_flag).length;
            const pfr = hands.filter(h => h.pfr_flag).length;
            const tbOpp = hands.filter(h => h.three_bet_opp).length;
            const tb = hands.filter(h => h.three_bet_flag).length;
            const f3bOpp = hands.filter(h => h.faced_three_bet).length;
            const f3b = hands.filter(h => h.fold_to_three_bet).length;
            const sawFlop = hands.filter(h => h.saw_flop).length;
            const wtsd = hands.filter(h => h.saw_flop && h.went_to_showdown).length;
            const wsd = hands.filter(h => h.went_to_showdown && h.won_at_showdown).length;
            const wwsf = hands.filter(h => h.saw_flop && h.won_hand).length;
            const cbOpp = hands.filter(h => h.cbet_flop_opp).length;
            const cb = hands.filter(h => h.cbet_flop_flag).length;
            const fcbOpp = hands.filter(h => h.faced_cbet_flop).length;
            const fcb = hands.filter(h => h.fold_to_cbet_flop).length;

            const actCounts = {{ FOLD: 0, CHECK: 0, CALL: 0, BET: 0, RAISE: 0 }};
            let postAggr = 0, postCalls = 0, postNonCheck = 0;
            decisions.forEach(d => {{
                const a = (d.action_taken || '').toUpperCase();
                const st = (d.street || 'PREFLOP').toUpperCase();
                const tc = parseFloat(d.to_call) || 0;
                if (a === 'ALL_IN' || a === 'ALLIN' || a === 'RAISE') {{
                    if (tc === 0 && st !== 'PREFLOP') actCounts.BET++;
                    else actCounts.RAISE++;
                }} else if (actCounts[a] !== undefined) {{
                    actCounts[a]++;
                }}
                if (st !== 'PREFLOP') {{
                    if (a === 'BET' || a === 'RAISE' || a === 'ALL_IN' || a === 'ALLIN') {{ postAggr++; postNonCheck++; }}
                    else if (a === 'CALL') {{ postCalls++; postNonCheck++; }}
                    else if (a === 'FOLD') {{ postNonCheck++; }}
                }}
            }});
            const aggr = postAggr > 0 ? postAggr : (actCounts.BET + actCounts.RAISE);
            const callsDen = postAggr > 0 ? postCalls : actCounts.CALL;
            const af = +(aggr / Math.max(1, callsDen)).toFixed(2);
            const nonCheck = postNonCheck > 0 ? postNonCheck : (actCounts.BET + actCounts.RAISE + actCounts.CALL + actCounts.FOLD);
            const afq = nonCheck > 0 ? +((aggr / nonCheck) * 100).toFixed(2) : 0;

            const gtoAligned = evalDecs.filter(d => d.is_gto_aligned).length;
            const exactGto = evalDecs.filter(d => d.action_deviation === 'EXACT_MATCH').length;
            const errors = evalDecs.filter(d => d.is_strategic_error).length;
            const missedOpps = evalDecs.filter(d => d.is_missed_opportunity).length;
            const evLostBB = evalDecs.reduce((a, d) => a + (parseFloat(d.ev_lost_bb) || 0), 0);
            const adaptAligned = evalDecs.filter(d => d.opponent_adaptation_aligned).length;

            return {{
                n, nDec, nEval, sessCount: sessSet.size, won, lost,
                winPct: n > 0 ? +((won / n) * 100).toFixed(2) : 0,
                netChips: +netChips.toFixed(2), netBB: +netBB.toFixed(2),
                evChips: +evChips.toFixed(2), evBB: +evBB.toFixed(2),
                allinEvBB: +allinEvBB.toFixed(2),
                bb100: +bb100.toFixed(2), evBB100: +evBB100.toFixed(2), allinBB100: +allinBB100.toFixed(2),
                ciMargin: +ciMargin.toFixed(2),
                stdBB: +stdBB.toFixed(3), varBB: +varBB.toFixed(3),
                maxDdChips: +maxDdChips.toFixed(2), maxDdBB: +maxDdBB.toFixed(2),
                roiPct: n > 0 ? +((netChips / Math.max(500, sessSet.size * 500)) * 100).toFixed(2) : 0,
                vpipPct: n > 0 ? +((vpip / n) * 100).toFixed(2) : 0,
                pfrPct: n > 0 ? +((pfr / n) * 100).toFixed(2) : 0,
                tbPct: tbOpp > 0 ? +((tb / tbOpp) * 100).toFixed(2) : 0,
                f3bPct: f3bOpp > 0 ? +((f3b / f3bOpp) * 100).toFixed(2) : 0,
                af, afq,
                wtsdPct: sawFlop > 0 ? +((wtsd / sawFlop) * 100).toFixed(2) : 0,
                wsdPct: wtsd > 0 ? +((wsd / wtsd) * 100).toFixed(2) : 0,
                wwsfPct: sawFlop > 0 ? +((wwsf / sawFlop) * 100).toFixed(2) : 0,
                cbPct: cbOpp > 0 ? +((cb / cbOpp) * 100).toFixed(2) : 0,
                fcbPct: fcbOpp > 0 ? +((fcb / fcbOpp) * 100).toFixed(2) : 0,
                gtoPct: nEval > 0 ? +((gtoAligned / nEval) * 100).toFixed(2) : 0,
                exactGtoPct: nEval > 0 ? +((exactGto / nEval) * 100).toFixed(2) : 0,
                errors, errPct: nEval > 0 ? +((errors / nEval) * 100).toFixed(2) : 0,
                missedOpps, missedPct: nEval > 0 ? +((missedOpps / nEval) * 100).toFixed(2) : 0,
                evLostBB: +evLostBB.toFixed(2),
                evLost100: n > 0 ? +((evLostBB / n) * 100).toFixed(2) : 0,
                adaptPct: nEval > 0 ? +((adaptAligned / nEval) * 100).toFixed(2) : 0,
                actCounts, curve, hands, decisions
            }};
        }}

        function applyDashboardFilters() {{
            const f = getActiveFilters();
            let hands = (RAW_DATA.all_hands || []).filter(h => {{
                if (f.data_source === 'LIVE' && h.is_synthetic) return false;
                if (f.data_source === 'BENCHMARK' && !h.is_synthetic) return false;
                if (f.version_id !== 'ALL' && h.version_id !== f.version_id) return false;
                if (f.session_id !== 'ALL' && h.session_id !== f.session_id) return false;
                if (f.position !== 'ALL' && normPos(h.position) !== f.position) return false;
                if (f.street !== 'ALL' && (h.reached_street || '').toUpperCase() !== f.street) return false;
                if (f.opponent_profile !== 'ALL' && normProf(h.opponent_profile) !== f.opponent_profile) return false;
                if (f.date_from && h.date_str < f.date_from) return false;
                if (f.date_to && h.date_str > f.date_to) return false;
                return true;
            }});

            if (f.last_n_hands > 0 && hands.length > f.last_n_hands) {{
                hands = hands.slice(hands.length - f.last_n_hands);
            }}
            const handUids = new Set(hands.map(h => h.hand_uid));

            const decisions = (RAW_DATA.all_decisions || []).filter(d => {{
                if (!handUids.has(d.hand_uid)) return false;
                if (f.street !== 'ALL' && (d.street || '').toUpperCase() !== f.street) return false;
                return true;
            }});

            const st = computeStatsFromFiltered(hands, decisions);
            currentFilteredState = st;

            const dsBadge = document.getElementById('dataSourceBadge');
            const liveCount = hands.filter(h => !h.is_synthetic).length;
            const benchCount = hands.filter(h => h.is_synthetic).length;
            if (f.data_source === 'LIVE') {{
                dsBadge.className = 'badge badge-green';
                dsBadge.textContent = `Resultado en Vivo (n=${{liveCount}} manos)`;
            }} else if (f.data_source === 'BENCHMARK') {{
                dsBadge.className = 'badge badge-purple';
                dsBadge.textContent = `Benchmark Sintético (n=${{benchCount}} manos)`;
            }} else {{
                dsBadge.className = 'badge badge-blue';
                dsBadge.textContent = `Resultado en Vivo: ${{liveCount}}m | Benchmark Sintético: ${{benchCount}}m`;
            }}

            renderBanner(st, liveCount, benchCount);
            renderKPIs(st);
            renderIntelligentDiagnostics();
            renderMainMetricsTable();
            renderFinancialSummary(st);
            renderGtoEvaluationSection(st);
            renderHudSection(st);
            renderCharts(st);
            renderBreakdowns(st);
            renderOpponentSection(st);
            renderLaboratorySection();
            renderVersionTable();
            renderDecisionsTable(st);
            renderSystemDiagnosticsSection();
        }}

        function renderBanner(st, liveCount, benchCount) {{
            const el = document.getElementById('statBanner');
            const luckDiff = +(st.bb100 - st.evBB100).toFixed(2);
            let varBadge = `<span class="badge badge-green">Varianza Alineada con EV (${{luckDiff >= 0 ? '+' : ''}}${{luckDiff}} BB/100)</span>`;
            if (luckDiff > 3.5) varBadge = `<span class="badge badge-amber">⚡ Varianza Positiva / Run Good (+${{luckDiff}} BB/100 sobre EV)</span>`;
            else if (luckDiff < -3.5) varBadge = `<span class="badge badge-blue">🛡️ Varianza Negativa / Bad Beats (${{luckDiff}} BB/100 bajo EV)</span>`;

            let sampleMsg = `🏆 <strong>Muestra Sólida (${{st.n}} manos | ${{st.nDec}} decisiones):</strong> Alta confiabilidad estadística.`;
            if (st.n === 0) sampleMsg = `ℹ️ <strong>Sin manos en este filtro (0 manos):</strong> Si seleccionaste "Solo Partidas Reales en Vivo", inicia <code>bot.py</code> para registrar manos reales en directo o cambia a "Laboratorio Controlado" para inspeccionar el benchmark pareado.`;
            else if (st.n < 30) sampleMsg = `⚠️ <strong>Muestra Exploratoria (${{st.n}} manos &lt; 30):</strong> Resultados financieros sujetos a varianza; guíese por el EV BB/100 y Regret.`;
            else if (st.n < 100) sampleMsg = `ℹ️ <strong>Muestra Preliminar (${{st.n}} manos):</strong> Suficiente para evaluar calidad estratégica; IC 95% de Winrate aún amplio (±${{st.ciMargin}} BB/100).`;

            const reqHands = (RAW_DATA.operational_kpis && RAW_DATA.operational_kpis.required_hands_for_10bb100) || (RAW_DATA.sample_power && RAW_DATA.sample_power.required_hands) || '--';
            el.innerHTML = `
                <div>${{sampleMsg}} <span style="color:var(--text-muted);margin-left:8px;">[Resultado en Vivo: <b>${{liveCount}}</b> | Benchmark Sintético: <b>${{benchCount}}</b> | Manos necesarias (Δ=10 BB/100, 80% pot.): <b>${{reqHands}}</b> | Oponentes DB: <b>${{(RAW_DATA.opponent_analysis?.opponents_hud_table || []).length}}</b>]</span></div>
                <div style="display:flex; gap:10px; align-items:center; flex-wrap:wrap;">
                    ${{varBadge}}
                    <span class="badge badge-purple">IC 95% Winrate (n=${{st.n}}): [${{(st.bb100 - st.ciMargin).toFixed(1)}}, ${{(st.bb100 + st.ciMargin).toFixed(1)}}] BB/100</span>
                </div>
            `;
        }}

        function renderKPIs(st) {{
            const grid = document.getElementById('kpiGrid');
            const signNet = st.netChips >= 0 ? '+' : '';
            const signBB = st.bb100 >= 0 ? '+' : '';
            const signEV = st.evBB100 >= 0 ? '+' : '';
            const bestVer = RAW_DATA.version_comparison?.best_overall_statistical_version || '1.3';
            const opKpis = RAW_DATA.operational_kpis || {{}};
            const illRate = opKpis.illegal_actions_corrected_rate_pct ?? 0;
            const illCnt = opKpis.illegal_actions_corrected_count ?? 0;
            const latAvg = opKpis.avg_decision_latency_ms ?? 0;
            const latP95 = opKpis.p95_decision_latency_ms ?? 0;
            const domErrs = opKpis.dom_errors_count ?? 0;
            const memHits = opKpis.experience_memory_hits ?? 0;

            grid.innerHTML = `
                <div class="kpi-card ${{st.netChips >= 0 ? 'kpi-green' : 'kpi-red'}}">
                    <div class="kpi-label">Beneficio Neto Observado</div>
                    <div class="kpi-value ${{st.netChips >= 0 ? 'text-pos' : 'text-neg'}}">${{signNet}}${{st.netChips}} <span style="font-size:0.85rem;">fichas</span></div>
                    <div class="kpi-sub"><span>${{signNet}}${{st.netBB}} BB (n=${{st.n}})</span><span>ROI: ${{st.roiPct}}%</span></div>
                </div>
                <div class="kpi-card ${{st.bb100 >= 0 ? 'kpi-green' : 'kpi-red'}}">
                    <div class="kpi-label">Winrate Real (n=${{st.n}})</div>
                    <div class="kpi-value ${{st.bb100 >= 0 ? 'text-pos' : 'text-neg'}}">${{signBB}}${{st.bb100}} <span style="font-size:0.85rem;">BB/100</span></div>
                    <div class="kpi-sub"><span>IC 95%: [${{(st.bb100 - st.ciMargin).toFixed(1)}}, ${{(st.bb100 + st.ciMargin).toFixed(1)}}]</span><span>Win%: ${{st.winPct}}%</span></div>
                </div>
                <div class="kpi-card ${{st.evBB100 >= 0 ? 'kpi-green' : 'kpi-amber'}}">
                    <div class="kpi-label">EV Winrate &amp; All-in EV</div>
                    <div class="kpi-value ${{st.evBB100 >= 0 ? 'text-pos' : 'text-neg'}}">${{signEV}}${{st.evBB100}} <span style="font-size:0.85rem;">BB/100</span></div>
                    <div class="kpi-sub"><span>All-in EV: ${{st.allinBB100 >= 0 ? '+' : ''}}${{st.allinBB100}} BB/100</span><span>EV Neto: ${{st.evBB}} BB</span></div>
                </div>
                <div class="kpi-card kpi-purple">
                    <div class="kpi-label">HUD Hero (VPIP / PFR) &amp; GTO</div>
                    <div class="kpi-value" style="color:var(--accent-blue);">${{st.vpipPct}}% / ${{st.pfrPct}}%</div>
                    <div class="kpi-sub"><span>GTO: ${{st.gtoPct}}% (Exact: ${{st.exactGtoPct}}%)</span><span>Adaptación: ${{st.adaptPct}}%</span></div>
                </div>
                <div class="kpi-card ${{st.errPct <= 8 ? 'kpi-green' : 'kpi-amber'}}">
                    <div class="kpi-label">Salud Operativa &amp; Latencia</div>
                    <div class="kpi-value" style="color:#fbbf24;">${{latAvg}} <span style="font-size:0.85rem;">ms</span></div>
                    <div class="kpi-sub"><span>P95: ${{latP95}}ms | DOM Err: ${{domErrs}}</span><span>Ilegales: ${{illCnt}} (${{illRate}}%) | Mem: ${{memHits}}</span></div>
                </div>
                <div class="kpi-card kpi-green">
                    <div class="kpi-label">Mejor Versión Experimental</div>
                    <div class="kpi-value" style="color:#34d399;">v${{bestVer}}</div>
                    <div class="kpi-sub"><span>Manos: ${{st.n}} (${{st.sessCount}} ses.)</span><span>Max DD: -${{st.maxDdBB}} BB</span></div>
                </div>
            `;
        }}

        function renderIntelligentDiagnostics() {{
            const diags = RAW_DATA.intelligent_diagnostics || [];
            document.getElementById('diagCountBadge').textContent = `${{diags.length}} observaciones automáticas`;
            const container = document.getElementById('intelligentDiagnosticsContainer');
            if (!diags.length) {{
                container.innerHTML = `<div class="text-neutral">No se detectaron anomalías estadísticas en la muestra actual.</div>`;
                return;
            }}
            container.innerHTML = diags.map(d => {{
                const badgeCls = d.severidad === 'CRITICA' ? 'badge-red' : (d.severidad === 'ADVERTENCIA' ? 'badge-amber' : (d.severidad === 'POSITIVA' ? 'badge-green' : 'badge-blue'));
                return `
                    <div class="diag-card ${{d.severidad}}">
                        <div style="display:flex; justify-content:space-between; align-items:center; flex-wrap:wrap; gap:8px; margin-bottom:6px;">
                            <div style="font-weight:700; color:#fff; font-size:0.88rem;">
                                <span class="badge ${{badgeCls}}" style="margin-right:8px;">${{d.severidad}}</span>
                                ${{d.problema_detectado}}
                            </div>
                            <span class="badge badge-purple mono">Confianza: ${{d.nivel_confianza}}</span>
                        </div>
                        <div style="font-size:0.81rem; color:#e2e8f0; margin-bottom:4px;">
                            <strong>Evidencia Estadística:</strong> ${{d.evidencia_estadistica}}
                        </div>
                        <div style="font-size:0.78rem; color:var(--text-muted); margin-bottom:4px;">
                            <strong>Métricas afectadas:</strong> ${{(d.metricas_afectadas || []).join(' • ')}} | <strong>Posible causa:</strong> ${{d.posible_causa}}
                        </div>
                        <div style="font-size:0.79rem; color:#38bdf8;">
                            <strong>🔬 Recomendación de Investigación:</strong> ${{d.recomendacion_investigacion}}
                        </div>
                    </div>
                `;
            }}).join('');
        }}

        function sortTableBy(tableKey, colKey) {{
            const st = sortState[tableKey];
            if (st.col === colKey) st.asc = !st.asc;
            else {{ st.col = colKey; st.asc = true; }}

            if (tableKey === 'main') renderMainMetricsTable();
            else if (tableKey === 'versions') renderVersionTable();
            else if (tableKey === 'decisions' && currentFilteredState) renderDecisionsTable(currentFilteredState);
        }}

        function renderMainMetricsTable() {{
            const rows = [...(RAW_DATA.main_metrics_table || [])];
            const q = (document.getElementById('metricSearchInput')?.value || '').toLowerCase().trim();
            const filtered = q ? rows.filter(r =>
                (r.category || '').toLowerCase().includes(q) ||
                (r.metric_name || '').toLowerCase().includes(q) ||
                (r.formula || '').toLowerCase().includes(q) ||
                (r.explanation || '').toLowerCase().includes(q)
            ) : rows;

            const st = sortState.main;
            filtered.sort((a, b) => {{
                const va = a[st.col], vb = b[st.col];
                if (typeof va === 'number' && typeof vb === 'number') return st.asc ? va - vb : vb - va;
                return st.asc ? String(va).localeCompare(String(vb)) : String(vb).localeCompare(String(va));
            }});

            const tbody = document.getElementById('mainMetricsTableBody');
            tbody.innerHTML = filtered.map(r => `
                <tr>
                    <td><span class="badge badge-blue">${{r.category}}</span></td>
                    <td><strong>${{r.metric_name}}</strong></td>
                    <td class="mono" style="font-weight:700; color:#f8fafc;">${{r.value_display}}</td>
                    <td class="mono text-neutral">${{r.ci95_display}}</td>
                    <td class="mono">${{r.numerator}} / ${{r.denominator}}</td>
                    <td class="mono">n = ${{r.sample_size}}</td>
                    <td class="mono" style="color:#38bdf8; font-size:0.74rem;">${{r.formula}}</td>
                    <td style="font-size:0.77rem; color:var(--text-muted); max-width:340px;">${{r.explanation}}</td>
                </tr>
            `).join('');
        }}

        function renderFinancialSummary(st) {{
            const gp = RAW_DATA.general_performance || {{}};
            const se = RAW_DATA.statistical_evaluation || {{}};
            const stab = se.stability_indicators || {{}};
            const sharpeTxt = stab.sharpe_assumptions_met ? `${{stab.sharpe_ratio_100}} (Válido)` : `NO APLICABLE (${{stab.sharpe_status_note || 'n < 30'}})`;

            document.getElementById('financialSummaryBox').innerHTML = `
                <div class="table-wrapper" style="margin-bottom:0;">
                    <table>
                        <tbody>
                            <tr><td><strong>Ganancias Brutas / Pérdidas Brutas</strong></td><td class="mono"><span class="text-pos">+${{gp.gross_won_bb || 0}} BB (+${{gp.gross_won_chips || 0}})</span> / <span class="text-neg">${{gp.gross_lost_bb || 0}} BB (${{gp.gross_lost_chips || 0}})</span></td></tr>
                            <tr><td><strong>Beneficio Neto vs EV Neto</strong></td><td class="mono">Real: <b>${{st.netBB >= 0 ? '+' : ''}}${{st.netBB}} BB</b> | EV: <b>${{st.evBB >= 0 ? '+' : ''}}${{st.evBB}} BB</b> | All-in EV: <b>${{st.allinEvBB >= 0 ? '+' : ''}}${{st.allinEvBB}} BB</b></td></tr>
                            <tr><td><strong>Bankroll Inicial → Final</strong></td><td class="mono">${{gp.initial_bankroll_chips || 1500}} → <b>${{gp.final_bankroll_chips || 1500}} fichas</b> (EV Bankroll: ${{gp.ev_final_bankroll_chips || 1500}})</td></tr>
                            <tr><td><strong>Desviación Estándar y Varianza</strong></td><td class="mono">σ = ${{(st.stdBB * 10).toFixed(2)}} BB/100 (${{st.stdBB}} BB/mano) | Varianza s² = ${{st.varBB}} BB²/mano</td></tr>
                            <tr><td><strong>Máximo Drawdown</strong></td><td class="mono text-neg">-${{st.maxDdBB}} BB (-${{st.maxDdChips}} fichas)</td></tr>
                            <tr><td><strong>Sharpe Ratio (por 100 manos)</strong></td><td class="mono">${{sharpeTxt}}</td></tr>
                            <tr><td><strong>Índice de Estabilidad</strong></td><td class="mono">${{stab.stability_score || 0}} / 100 (${{stab.positive_ev_windows_pct || 0}}% bloques con EV &gt; 0)</td></tr>
                        </tbody>
                    </table>
                </div>
            `;
        }}

        function renderGtoEvaluationSection(st) {{
            const gtoEval = (RAW_DATA.strategic_analysis && RAW_DATA.strategic_analysis.gto_evaluation) || {{}};
            const mix = gtoEval.mixed_strategy_evaluation || {{}};
            const obsMix = mix.observed_distribution_pct || {{}};
            const reg = gtoEval.regret_analysis || {{}};
            const expl = gtoEval.exploitability_analysis || {{}};

            document.getElementById('gtoEvaluationCard').innerHTML = `
                <div class="card-title">
                    <span>🎯 Evaluación GTO Rigurosa (Fase 2.D — Separación entre Coincidencia Heurística, EV, Estrategias Mixtas, Regret y Exploitability)</span>
                    <span class="badge badge-blue">Transparencia Metodológica</span>
                </div>
                <p style="font-size:0.8rem; color:var(--text-muted); margin-bottom:12px;">
                    ${{gtoEval.methodology_disclaimer || ''}}
                </p>
                <div class="hud-mini-grid" style="margin-bottom:0;">
                    <div class="hud-box">
                        <div class="lbl">1. Coincidencia Heurística</div>
                        <div class="val">${{st.exactGtoPct}}%</div>
                        <div class="ref">Acción primaria exacta</div>
                    </div>
                    <div class="hud-box">
                        <div class="lbl">2. Calidad según EV</div>
                        <div class="val" style="color:#34d399;">${{st.gtoPct}}%</div>
                        <div class="ref">ΔEV ≤ 0.35 BB o Explotativa</div>
                    </div>
                    <div class="hud-box">
                        <div class="lbl">3. Frecuencia Mixta (70/20/10)</div>
                        <div class="val" style="font-size:0.95rem;">${{obsMix.primary || 0}}% / ${{obsMix.secondary || 0}}% / ${{obsMix.alternative || 0}}%</div>
                        <div class="ref">En soporte: ${{mix.in_mixed_support_pct || 0}}% (TVD: ${{mix.total_variation_distance_pct || 0}}%)</div>
                    </div>
                    <div class="hud-box">
                        <div class="lbl">4. Regret Acumulado</div>
                        <div class="val text-neg">-${{st.evLost100}} BB/100</div>
                        <div class="ref">Total: ${{st.evLostBB}} BB (${{reg.avg_regret_bb_per_decision || 0}} BB/dec)</div>
                    </div>
                    <div class="hud-box">
                        <div class="lbl">5. Exploitability Árbol Completo</div>
                        <div class="val" style="font-size:0.82rem; color:${{expl.deep_cfr_status === 'DEEP_CFR_KERA_ACTIVO' ? '#38bdf8' : '#fbbf24'}};">
                            ${{expl.deep_cfr_status === 'DEEP_CFR_KERA_ACTIVO' ? 'DEEP CFR (MODELO LOCAL KERA)' : 'NO DISPONIBLE SIN SOLVER CFR'}}
                        </div>
                        <div class="ref">${{expl.deep_cfr_status === 'DEEP_CFR_KERA_ACTIVO' ? 'Aproximación por Deep CFR Advantage Net' : 'No se inventan cifras CFR postflop'}}</div>
                    </div>
                    <div class="hud-box">
                        <div class="lbl">6. Subjuego Push/Fold (≤10 BB)</div>
                        <div class="val" style="color:#c084fc;">${{expl.push_fold_nash_compliance_pct !== null && expl.push_fold_nash_compliance_pct !== undefined ? expl.push_fold_nash_compliance_pct + '%' : 'N/A'}}</div>
                        <div class="ref">Muestra: ${{expl.push_fold_subgame_sample || 0}} dec. (Regret: ${{expl.push_fold_estimated_exploitability_bb100 || 0}} BB/100)</div>
                    </div>
                </div>
            `;
        }}

        function renderHudSection(st) {{
            const items = [
                {{ lbl: 'VPIP', val: st.vpipPct + '%', ref: 'Óptimo 3-Max: 28-42%' }},
                {{ lbl: 'PFR', val: st.pfrPct + '%', ref: 'Óptimo 3-Max: 22-34%' }},
                {{ lbl: 'Gap VPIP-PFR', val: (st.vpipPct - st.pfrPct).toFixed(1) + '%', ref: 'Óptimo: 5-12%' }},
                {{ lbl: '3-Bet%', val: st.tbPct + '%', ref: 'Óptimo: 9-16%' }},
                {{ lbl: 'Fold to 3-Bet', val: st.f3bPct + '%', ref: 'Óptimo: 40-58%' }},
                {{ lbl: 'AF (Postflop)', val: st.af, ref: 'Óptimo: 1.8 - 3.2' }},
                {{ lbl: 'AFq%', val: st.afq + '%', ref: 'Óptimo: 45-62%' }},
                {{ lbl: 'CBet Flop%', val: st.cbPct + '%', ref: 'Óptimo: 55-75%' }},
                {{ lbl: 'Fold to CBet%', val: st.fcbPct + '%', ref: 'Óptimo: 35-52%' }},
                {{ lbl: 'WTSD%', val: st.wtsdPct + '%', ref: 'Óptimo: 26-36%' }},
                {{ lbl: 'W$SD%', val: st.wsdPct + '%', ref: 'Óptimo: 51-62%' }},
                {{ lbl: 'WWSF%', val: st.wwsfPct + '%', ref: 'Óptimo: 46-56%' }},
            ];
            document.getElementById('hudMiniGrid').innerHTML = items.map(it => `
                <div class="hud-box">
                    <div class="lbl">${{it.lbl}}</div>
                    <div class="val">${{it.val}}</div>
                    <div class="ref">${{it.ref}}</div>
                </div>
            `).join('');

            const defs = (RAW_DATA.gameplay_hud && RAW_DATA.gameplay_hud.definitions_table) || [];
            document.getElementById('hudDefinitionsBody').innerHTML = defs.map(d => `
                <tr>
                    <td><strong>${{d.name}}</strong></td>
                    <td class="mono" style="font-weight:700; color:var(--accent-blue);">${{d.value}}${{d.unit === '%' ? '%' : ''}}</td>
                    <td class="mono">${{d.numerator}}</td>
                    <td class="mono">${{d.denominator}}</td>
                    <td class="mono">n = ${{d.sample_size}}</td>
                    <td class="mono">[${{d.ci95[0]}}%, ${{d.ci95[1]}}%]</td>
                    <td><span class="badge badge-green">${{d.optimal_range_3max}}</span></td>
                    <td style="font-size:0.76rem; color:var(--text-muted);">
                        <div class="mono" style="color:#38bdf8;">${{d.formula}}</div>
                        <div>${{d.definition}}</div>
                    </td>
                </tr>
            `).join('');
        }}

        function destroyChart(id) {{
            if (chartInstances[id]) {{
                chartInstances[id].destroy();
                delete chartInstances[id];
            }}
        }}

        function renderCharts(st) {{
            Chart.defaults.color = '#94a3b8';
            Chart.defaults.borderColor = 'rgba(51, 65, 85, 0.35)';

            const labels = st.curve.map(c => `#${{c.hand_index}}`);

            // 1. Bankroll Curve (Real vs EV vs All-in EV)
            destroyChart('chartBankroll');
            chartInstances['chartBankroll'] = new Chart(document.getElementById('chartBankroll'), {{
                type: 'line',
                data: {{
                    labels,
                    datasets: [
                        {{ label: 'Bankroll Real Observado', data: st.curve.map(c => c.bankroll_real), borderColor: '#10b981', backgroundColor: 'rgba(16, 185, 129, 0.08)', fill: true, tension: 0.25, pointRadius: 0, borderWidth: 2 }},
                        {{ label: 'Bankroll EV Ajustado', data: st.curve.map(c => c.bankroll_ev), borderColor: '#38bdf8', borderDash: [5, 4], fill: false, tension: 0.25, pointRadius: 0, borderWidth: 2 }},
                        {{ label: 'Bankroll All-in EV', data: st.curve.map(c => c.bankroll_allin_ev), borderColor: '#a855f7', borderDash: [2, 2], fill: false, tension: 0.25, pointRadius: 0, borderWidth: 1.5 }}
                    ]
                }},
                options: {{ responsive: true, maintainAspectRatio: false, plugins: {{ legend: {{ position: 'top' }} }} }}
            }});

            // 2. Winrate BB/100
            destroyChart('chartWinrate');
            chartInstances['chartWinrate'] = new Chart(document.getElementById('chartWinrate'), {{
                type: 'line',
                data: {{
                    labels,
                    datasets: [
                        {{ label: 'EV Winrate Acumulado (BB/100)', data: st.curve.map(c => c.cumulative_ev_bb100), borderColor: '#38bdf8', tension: 0.25, pointRadius: 0, borderWidth: 2.2 }},
                        {{ label: 'Winrate Real Acumulado (BB/100)', data: st.curve.map(c => c.cumulative_bb100), borderColor: '#10b981', tension: 0.25, pointRadius: 0, borderWidth: 1.8 }},
                        {{ label: 'Media Móvil 25m (BB/100)', data: st.curve.map(c => c.rolling_bb100), borderColor: 'rgba(245, 158, 11, 0.55)', borderDash: [3, 3], tension: 0.3, pointRadius: 0, borderWidth: 1.3 }}
                    ]
                }},
                options: {{ responsive: true, maintainAspectRatio: false, plugins: {{ legend: {{ position: 'top' }} }} }}
            }});

            // 3. EV frente a Resultados Reales (Cumulative BB)
            destroyChart('chartEvVsReal');
            chartInstances['chartEvVsReal'] = new Chart(document.getElementById('chartEvVsReal'), {{
                type: 'line',
                data: {{
                    labels,
                    datasets: [
                        {{ label: 'Ganancia Real Acumulada (BB)', data: st.curve.map(c => c.cumulative_bb), borderColor: '#10b981', tension: 0.25, pointRadius: 0, borderWidth: 2 }},
                        {{ label: 'EV Neto Acumulado (BB)', data: st.curve.map(c => c.cumulative_ev_bb), borderColor: '#f59e0b', tension: 0.25, pointRadius: 0, borderWidth: 2 }},
                        {{ label: 'All-in EV Acumulado (BB)', data: st.curve.map(c => c.cumulative_allin_ev_bb), borderColor: '#c084fc', borderDash: [4, 4], tension: 0.25, pointRadius: 0, borderWidth: 1.8 }}
                    ]
                }},
                options: {{ responsive: true, maintainAspectRatio: false, plugins: {{ legend: {{ position: 'top' }} }} }}
            }});

            // 4. Version Comparison Chart
            const verRows = ((RAW_DATA.version_comparison && RAW_DATA.version_comparison.versions) || []).filter(v => v.hands_evaluated > 0);
            destroyChart('chartVersions');
            chartInstances['chartVersions'] = new Chart(document.getElementById('chartVersions'), {{
                type: 'bar',
                data: {{
                    labels: verRows.map(v => `v${{v.version_id}}`),
                    datasets: [
                        {{ label: 'EV Winrate (BB/100)', data: verRows.map(v => v.ev_bb_per_100), backgroundColor: '#38bdf8', borderRadius: 6 }},
                        {{ label: 'Winrate Real (BB/100)', data: verRows.map(v => v.bb_per_100), backgroundColor: '#10b981', borderRadius: 6 }},
                        {{ label: 'Calidad EV/GTO (%)', data: verRows.map(v => v.gto_alignment_pct), backgroundColor: '#a855f7', borderRadius: 6 }}
                    ]
                }},
                options: {{ responsive: true, maintainAspectRatio: false, plugins: {{ legend: {{ position: 'top' }} }} }}
            }});

            // 5. Action Distribution
            destroyChart('chartActions');
            chartInstances['chartActions'] = new Chart(document.getElementById('chartActions'), {{
                type: 'doughnut',
                data: {{
                    labels: ['Fold', 'Check', 'Call', 'Bet', 'Raise / All-in'],
                    datasets: [{{
                        data: [st.actCounts.FOLD, st.actCounts.CHECK, st.actCounts.CALL, st.actCounts.BET, st.actCounts.RAISE],
                        backgroundColor: ['#64748b', '#06b6d4', '#f59e0b', '#38bdf8', '#10b981'],
                        borderWidth: 1,
                        borderColor: '#0f172a'
                    }}]
                }},
                options: {{ responsive: true, maintainAspectRatio: false, plugins: {{ legend: {{ position: 'right' }} }} }}
            }});

            // 6. Performance by Position
            const posOrder = ['BTN', 'SB', 'BB'];
            const posReal = [], posEV = [];
            posOrder.forEach(p => {{
                const ph = st.hands.filter(h => normPos(h.position) === p);
                const nh = ph.length;
                posReal.push(nh > 0 ? +((ph.reduce((a, x) => a + (parseFloat(x.net_profit_bb) || 0), 0) / nh) * 100).toFixed(2) : 0);
                posEV.push(nh > 0 ? +((ph.reduce((a, x) => a + (parseFloat(x.ev_net_bb) || 0), 0) / nh) * 100).toFixed(2) : 0);
            }});
            destroyChart('chartPositions');
            chartInstances['chartPositions'] = new Chart(document.getElementById('chartPositions'), {{
                type: 'bar',
                data: {{
                    labels: ['BTN (Button)', 'SB (Small Blind)', 'BB (Big Blind)'],
                    datasets: [
                        {{ label: 'Winrate Real (BB/100)', data: posReal, backgroundColor: '#10b981', borderRadius: 6 }},
                        {{ label: 'EV Winrate (BB/100)', data: posEV, backgroundColor: '#38bdf8', borderRadius: 6 }}
                    ]
                }},
                options: {{ responsive: true, maintainAspectRatio: false }}
            }});

            // 7. Performance by Street
            const stOrder = ['PREFLOP', 'FLOP', 'TURN', 'RIVER'];
            const stGto = [], stEvLost = [];
            stOrder.forEach(s => {{
                const sd = st.decisions.filter(d => (d.street || '').toUpperCase() === s && d.evaluation_available !== 0);
                const nd = sd.length;
                stGto.push(nd > 0 ? +((sd.filter(x => x.is_gto_aligned).length / nd) * 100).toFixed(1) : 0);
                stEvLost.push(+(sd.reduce((a, x) => a + (parseFloat(x.ev_lost_bb) || 0), 0)).toFixed(2));
            }});
            destroyChart('chartStreets');
            chartInstances['chartStreets'] = new Chart(document.getElementById('chartStreets'), {{
                type: 'bar',
                data: {{
                    labels: stOrder,
                    datasets: [
                        {{ label: 'Calidad EV/GTO (%)', data: stGto, backgroundColor: '#6366f1', borderRadius: 6 }},
                        {{ label: 'Regret Acumulado (BB)', data: stEvLost, backgroundColor: '#ef4444', borderRadius: 6 }}
                    ]
                }},
                options: {{ responsive: true, maintainAspectRatio: false }}
            }});

            // 8A. HUD Evolution Chart
            const hudEvo = (RAW_DATA.gameplay_hud && RAW_DATA.gameplay_hud.hud_evolution) || [];
            destroyChart('chartHudEvolution');
            chartInstances['chartHudEvolution'] = new Chart(document.getElementById('chartHudEvolution'), {{
                type: 'line',
                data: {{
                    labels: hudEvo.map(b => `Manos ${{b.hand_range}} (v${{b.version_id}})`),
                    datasets: [
                        {{ label: 'VPIP %', data: hudEvo.map(b => b.vpip_pct), borderColor: '#38bdf8', tension: 0.3 }},
                        {{ label: 'PFR %', data: hudEvo.map(b => b.pfr_pct), borderColor: '#10b981', tension: 0.3 }},
                        {{ label: 'CBet Flop %', data: hudEvo.map(b => b.cbet_flop_pct), borderColor: '#f59e0b', tension: 0.3 }},
                        {{ label: 'W$SD %', data: hudEvo.map(b => b.wsd_pct), borderColor: '#a855f7', tension: 0.3 }}
                    ]
                }},
                options: {{ responsive: true, maintainAspectRatio: false }}
            }});

            // 8B. Quality Evolution Chart
            const qEvo = (RAW_DATA.strategic_analysis && RAW_DATA.strategic_analysis.quality_evolution) || [];
            destroyChart('chartQualityEvolution');
            chartInstances['chartQualityEvolution'] = new Chart(document.getElementById('chartQualityEvolution'), {{
                type: 'line',
                data: {{
                    labels: qEvo.map(b => `Dec ${{b.decision_range}} (v${{b.version_id}})`),
                    datasets: [
                        {{ label: 'Calidad EV/GTO %', data: qEvo.map(b => b.gto_alignment_pct), borderColor: '#10b981', tension: 0.3, borderWidth: 2 }},
                        {{ label: 'Adaptación Rival %', data: qEvo.map(b => b.adaptation_pct), borderColor: '#38bdf8', tension: 0.3, borderWidth: 2 }},
                        {{ label: 'Tasa de Error %', data: qEvo.map(b => b.error_rate_pct), borderColor: '#ef4444', tension: 0.3, borderWidth: 2 }}
                    ]
                }},
                options: {{ responsive: true, maintainAspectRatio: false }}
            }});
        }}

        function renderBreakdowns(st) {{
            let htmlPosStreet = '';
            ['BTN', 'SB', 'BB'].forEach(p => {{
                const ph = st.hands.filter(h => normPos(h.position) === p);
                const pd = st.decisions.filter(d => normPos(d.position) === p && d.evaluation_available !== 0);
                const nh = ph.length, nd = pd.length;
                const won = ph.filter(h => h.won_hand || (h.net_profit_chips || 0) > 0).length;
                const vpip = ph.filter(h => h.vpip_flag).length;
                const netBB = ph.reduce((a, x) => a + (parseFloat(x.net_profit_bb) || 0), 0);
                const evBB = ph.reduce((a, x) => a + (parseFloat(x.ev_net_bb) || 0), 0);
                const gto = pd.filter(d => d.is_gto_aligned).length;
                const evLost = pd.reduce((a, d) => a + (parseFloat(d.ev_lost_bb) || 0), 0);
                const wr = nh > 0 ? ((netBB / nh) * 100).toFixed(2) : '0.00';
                const evWr = nh > 0 ? ((evBB / nh) * 100).toFixed(2) : '0.00';
                htmlPosStreet += `<tr>
                    <td><span class="badge badge-blue">Posición ${{p}}</span></td>
                    <td>${{nh}}m / ${{nd}}d</td>
                    <td>${{nh > 0 ? ((won/nh)*100).toFixed(1) : 0}}% / ${{nh > 0 ? ((vpip/nh)*100).toFixed(1) : 0}}%</td>
                    <td class="${{parseFloat(wr) >= 0 ? 'text-pos' : 'text-neg'}}">${{wr}} BB/100</td>
                    <td class="${{parseFloat(evWr) >= 0 ? 'text-pos' : 'text-neg'}}">${{evWr}} BB/100</td>
                    <td>${{nd > 0 ? ((gto/nd)*100).toFixed(1) : 0}}%</td>
                    <td class="text-neg">-${{evLost.toFixed(2)}} BB</td>
                </tr>`;
            }});

            ['PREFLOP', 'FLOP', 'TURN', 'RIVER'].forEach(s => {{
                const sh = st.hands.filter(h => (h.reached_street || '').toUpperCase() === s);
                const sd = st.decisions.filter(d => (d.street || '').toUpperCase() === s && d.evaluation_available !== 0);
                const nh = sh.length, nd = sd.length;
                const gto = sd.filter(d => d.is_gto_aligned).length;
                const err = sd.filter(d => d.is_strategic_error).length;
                const evLost = sd.reduce((a, d) => a + (parseFloat(d.ev_lost_bb) || 0), 0);
                const netBB = sh.reduce((a, x) => a + (parseFloat(x.net_profit_bb) || 0), 0);
                const evBB = sh.reduce((a, x) => a + (parseFloat(x.ev_net_bb) || 0), 0);
                htmlPosStreet += `<tr>
                    <td><span class="badge badge-purple">Calle ${{s}}</span></td>
                    <td>${{nh}}m / ${{nd}}d</td>
                    <td>Err: ${{nd > 0 ? ((err/nd)*100).toFixed(1) : 0}}%</td>
                    <td class="${{netBB >= 0 ? 'text-pos' : 'text-neg'}}">${{netBB.toFixed(2)}} BB</td>
                    <td class="${{evBB >= 0 ? 'text-pos' : 'text-neg'}}">${{evBB.toFixed(2)}} BB</td>
                    <td>${{nd > 0 ? ((gto/nd)*100).toFixed(1) : 0}}%</td>
                    <td class="text-neg">-${{evLost.toFixed(2)}} BB</td>
                </tr>`;
            }});
            document.getElementById('posStreetBreakdownBody').innerHTML = htmlPosStreet;

            // Pot Size & Stack Depth Breakdown (Fase 2.C)
            const potPerf = (RAW_DATA.strategic_analysis && RAW_DATA.strategic_analysis.performance_by_pot_size) || {{}};
            const stackPerf = (RAW_DATA.strategic_analysis && RAW_DATA.strategic_analysis.performance_by_stack_depth) || {{}};
            let htmlPotStack = '';
            Object.values(potPerf).forEach(b => {{
                htmlPotStack += `<tr>
                    <td><span class="badge badge-amber">Bote: ${{b.bucket}}</span></td>
                    <td>${{b.hands}}</td>
                    <td>${{b.decisions}}</td>
                    <td class="${{b.bb_per_100 >= 0 ? 'text-pos' : 'text-neg'}}">${{b.bb_per_100 >= 0 ? '+' : ''}}${{b.bb_per_100}} BB/100</td>
                    <td class="${{b.ev_bb_per_100 >= 0 ? 'text-pos' : 'text-neg'}}">${{b.ev_bb_per_100 >= 0 ? '+' : ''}}${{b.ev_bb_per_100}} BB/100</td>
                    <td>${{b.gto_alignment_pct}}%</td>
                    <td>${{b.error_rate_pct}}%</td>
                    <td class="text-neg">-${{b.ev_lost_bb}} BB</td>
                </tr>`;
            }});
            Object.values(stackPerf).forEach(b => {{
                htmlPotStack += `<tr>
                    <td><span class="badge badge-green">Stack: ${{b.bucket}}</span></td>
                    <td>${{b.hands}}</td>
                    <td>${{b.decisions}}</td>
                    <td class="${{b.bb_per_100 >= 0 ? 'text-pos' : 'text-neg'}}">${{b.bb_per_100 >= 0 ? '+' : ''}}${{b.bb_per_100}} BB/100</td>
                    <td class="${{b.ev_bb_per_100 >= 0 ? 'text-pos' : 'text-neg'}}">${{b.ev_bb_per_100 >= 0 ? '+' : ''}}${{b.ev_bb_per_100}} BB/100</td>
                    <td>${{b.gto_alignment_pct}}%</td>
                    <td>${{b.error_rate_pct}}%</td>
                    <td class="text-neg">-${{b.ev_lost_bb}} BB</td>
                </tr>`;
            }});
            document.getElementById('potStackBreakdownBody').innerHTML = htmlPotStack;
        }}

        function renderOpponentSection(st) {{
            const profiles = ['CALLING STATION', 'MANIAC', 'NIT', 'REGULAR EQUILIBRADO', 'JUGADOR NUEVO'];
            let html = '';
            profiles.forEach(prof => {{
                const ph = st.hands.filter(h => normProf(h.opponent_profile) === prof);
                const pd = st.decisions.filter(d => normProf(d.opponent_profile) === prof && d.evaluation_available !== 0);
                const nh = ph.length, nd = pd.length;
                if (nh === 0 && nd === 0) return;
                const won = ph.filter(h => h.won_hand || (h.net_profit_chips || 0) > 0).length;
                const netChips = ph.reduce((a, x) => a + (parseFloat(x.net_profit_chips) || 0), 0);
                const netBB = ph.reduce((a, x) => a + (parseFloat(x.net_profit_bb) || 0), 0);
                const evBB = ph.reduce((a, x) => a + (parseFloat(x.ev_net_bb) || 0), 0);
                const adapt = pd.filter(d => d.opponent_adaptation_aligned).length;
                const err = pd.filter(d => d.is_strategic_error).length;
                const wr = nh > 0 ? ((netBB / nh) * 100).toFixed(2) : '0.00';
                const evWr = nh > 0 ? ((evBB / nh) * 100).toFixed(2) : '0.00';

                html += `<tr>
                    <td><strong>${{prof}}</strong></td>
                    <td>${{nh}}</td>
                    <td>${{nd}}</td>
                    <td>${{nh > 0 ? ((won/nh)*100).toFixed(1) : 0}}%</td>
                    <td class="${{netChips >= 0 ? 'text-pos' : 'text-neg'}}">${{netChips >= 0 ? '+' : ''}}${{netChips.toFixed(0)}} fichas</td>
                    <td class="${{parseFloat(wr) >= 0 ? 'text-pos' : 'text-neg'}}">${{parseFloat(wr) >= 0 ? '+' : ''}}${{wr}} BB/100</td>
                    <td class="${{parseFloat(evWr) >= 0 ? 'text-pos' : 'text-neg'}}">${{parseFloat(evWr) >= 0 ? '+' : ''}}${{evWr}} BB/100</td>
                    <td><span class="badge badge-blue">${{nd > 0 ? ((adapt/nd)*100).toFixed(1) : 0}}%</span></td>
                    <td class="${{err > 0 ? 'text-neg' : 'text-pos'}}">${{nd > 0 ? ((err/nd)*100).toFixed(1) : 0}}%</td>
                </tr>`;
            }});
            document.getElementById('oppProfilesTableBody').innerHTML = html || `<tr><td colspan="9" class="text-neutral">Sin manos para el filtro seleccionado.</td></tr>`;

            // Tabla real de oponentes en SQLite (`oponentes`)
            const realOpps = (RAW_DATA.opponent_analysis && RAW_DATA.opponent_analysis.opponents_hud_table) || [];
            document.getElementById('oppRealDbTableBody').innerHTML = realOpps.map(o => {{
                const mj = Math.max(1, parseInt(o.manos_jugadas || 0, 10));
                const vpip = Math.min(100, Math.round(((o.preflop_calls + o.preflop_raises) / mj) * 100));
                const pfr = Math.min(100, Math.round((o.preflop_raises / mj) * 100));
                const af = ((o.postflop_bets || 0) / Math.max(1, o.postflop_checks || 1)).toFixed(2);
                return `
                    <tr>
                        <td><strong>${{o.nickname || o.player_id}}</strong> <span class="mono text-neutral">(#${{o.player_id}})</span></td>
                        <td class="mono"><b>${{o.manos_jugadas}}</b> manos reales</td>
                        <td class="mono">VPIP: ${{vpip}}% | PFR: ${{pfr}}% | AF: ${{af}}</td>
                        <td class="mono">${{o.folds_ante_raise || 0}}</td>
                        <td class="mono">${{o.ultimas_cartas_mostradas || '-'}}</td>
                        <td class="mono text-neutral">${{o.ultima_actualizacion || '-'}}</td>
                    </tr>
                `;
            }}).join('') || `<tr><td colspan="6" class="text-neutral">Sin oponentes registrados en la tabla oponentes.</td></tr>`;
        }}

        function renderLaboratorySection() {{
            const exps = RAW_DATA.experiments_history || [];
            const cond = (exps[0] && exps[0].conditions) || {{}};
            document.getElementById('labConditionsCard').innerHTML = `
                <div class="card-title">
                    <span>🔬 Protocolo de Pruebas Controladas (Duplicate Poker / Common Random Numbers)</span>
                    <span class="badge badge-green">Corrección: ${{(RAW_DATA.version_comparison && RAW_DATA.version_comparison.multiple_comparisons_method) || 'Holm-Bonferroni'}}</span>
                </div>
                <div style="font-size:0.81rem; color:var(--text-muted);">
                    <strong>Formato:</strong> ${{cond.game_format || '3-Max Spin & Go'}} |
                    <strong>Diseño:</strong> ${{cond.paired_design || 'Escenarios Pareados'}} |
                    <strong>Variables Conservadas Idénticas:</strong> ${{(cond.preserved_variables || ['hole_cards', 'positions', 'initial_stack', 'pot_sequence', 'opponent_profiles']).join(', ')}}
                </div>
            `;

            document.getElementById('experimentsLabBody').innerHTML = exps.map(e => {{
                const res = e.results || {{}};
                const ci = e.ci95 || {{}};
                const diff = e.stat_diff || {{}};
                const st = e.verdict_state || 'INCONCLUSO';
                const badgeCls = st === 'MEJORA' ? 'badge-green' : (st === 'REGRESIÓN' ? 'badge-red' : 'badge-amber');
                const evCi = ci.ev_bb_per_100_ci95 || [0, 0];
                const diffCi = ci.ev_diff_vs_prev_ci95 || [0, 0];
                return `
                    <tr>
                        <td class="mono"><strong>${{e.experiment_id}}</strong></td>
                        <td><span class="badge badge-blue">v${{e.version_id}}</span></td>
                        <td class="mono text-neutral">${{e.created_at}}</td>
                        <td class="mono">Code: <code>${{e.code_hash}}</code><br/>Cfg: <code>${{e.config_hash}}</code></td>
                        <td class="mono">${{(e.random_seeds || []).join(', ')}}</td>
                        <td class="mono">${{e.num_scenarios}} pareados</td>
                        <td class="mono ${{res.ev_bb_per_100 >= 0 ? 'text-pos' : 'text-neg'}}">
                            <b>${{res.ev_bb_per_100 >= 0 ? '+' : ''}}${{res.ev_bb_per_100}} BB/100</b><br/>
                            <small class="text-neutral">[${{evCi[0]}}, ${{evCi[1]}}]</small>
                        </td>
                        <td class="mono">
                            ${{diff.base_version ? `vs v${{diff.base_version}}: <b>${{diff.delta_ev_bb_per_100 >= 0 ? '+' : ''}}${{diff.delta_ev_bb_per_100}} BB/100</b><br/><small class="text-neutral">[${{diffCi[0]}}, ${{diffCi[1]}}]</small>` : 'Línea Base'}}
                        </td>
                        <td class="mono">
                            ${{diff.base_version ? `p-Holm: <b>${{diff.holm_corrected_p_value}}</b><br/>Cohen's d: <b>${{diff.cohens_d}}</b>` : '-'}}
                        </td>
                        <td><span class="badge ${{badgeCls}}">${{st}}</span></td>
                    </tr>
                `;
            }}).join('');
        }}

        function renderVersionTable() {{
            const tbody = document.getElementById('versionTableBody');
            const rows = [...((RAW_DATA.version_comparison && RAW_DATA.version_comparison.versions) || [])];
            const bestId = RAW_DATA.version_comparison && RAW_DATA.version_comparison.best_overall_statistical_version;

            const st = sortState.versions;
            rows.sort((a, b) => {{
                const va = a[st.col], vb = b[st.col];
                if (typeof va === 'number' && typeof vb === 'number') return st.asc ? va - vb : vb - va;
                return st.asc ? String(va).localeCompare(String(vb)) : String(vb).localeCompare(String(va));
            }});

            tbody.innerHTML = rows.map(r => {{
                const isBest = r.version_id === bestId;
                const cmp = r.comparison_vs_prev || {{}};
                const vState = cmp.verdict_state || 'LINEA_BASE';
                let badgeClass = 'badge-blue';
                if (vState === 'MEJORA') badgeClass = 'badge-green';
                else if (vState === 'REGRESIÓN') badgeClass = 'badge-red';
                else if (vState === 'INCONCLUSO') badgeClass = 'badge-amber';

                const paramsStr = Object.entries(r.modified_params || {{}})
                    .map(([k, v]) => `<code>${{k}}=${{JSON.stringify(v)}}</code>`)
                    .join(' ');

                const impList = (cmp.improved_metrics || []).map(m => `<span class="text-pos">▲ ${{m}}</span>`).join('<br/>');
                const degList = (cmp.degraded_metrics || []).map(m => `<span class="text-neg">▼ ${{m}}</span>`).join('<br/>');
                const metricsChanges = [impList, degList].filter(Boolean).join('<br/>') || '<span class="text-neutral">Línea Base (Referencia)</span>';

                let deltaHtml = '<span class="text-neutral">—</span>';
                if (cmp.base_version) {{
                    const sEV = cmp.delta_ev_bb_per_100 >= 0 ? '+' : '';
                    const ciDiff = cmp.ci95_ev_diff || [0, 0];
                    deltaHtml = `
                        <div>vs <strong>v${{cmp.base_version}}</strong>:</div>
                        <div class="${{cmp.delta_ev_bb_per_100 >= 0 ? 'text-pos' : 'text-neg'}}">ΔEV: ${{sEV}}${{cmp.delta_ev_bb_per_100}} BB/100 [${{ciDiff[0]}}, ${{ciDiff[1]}}]</div>
                        <div class="mono text-neutral">p-raw=${{cmp.effective_p_value}} | <b>p-Holm=${{cmp.holm_corrected_p_value}}</b></div>
                        <div class="mono text-neutral">Cohen's d = <b>${{cmp.cohens_d}}</b></div>
                    `;
                }}

                return `<tr style="${{isBest ? 'background: rgba(16, 185, 129, 0.06);' : ''}}">
                    <td>
                        <div style="font-weight:800; font-size:0.95rem;">v${{r.version_id}} ${{isBest ? '🏆' : ''}}</div>
                        <div class="text-neutral mono" style="font-size:0.7rem;">${{r.created_at.substring(0, 10)}}</div>
                        <div class="text-neutral mono" style="font-size:0.68rem;">#${{r.config_hash}}</div>
                    </td>
                    <td style="max-width:340px;">
                        <div style="font-weight:600; margin-bottom:4px;">${{r.description}}</div>
                        <div class="mono" style="font-size:0.71rem; color:var(--text-muted); line-height:1.35;">${{paramsStr}}</div>
                    </td>
                    <td class="mono">
                        <b>${{r.hands_evaluated}}m</b> / ${{r.decisions_evaluated}}d<br/>
                        <span class="badge ${{r.is_exploratory_sample ? 'badge-amber' : 'badge-blue'}}" style="font-size:0.65rem;">${{r.is_exploratory_sample ? 'EXPLORATORIA' : 'CONFIRMATORIA'}}</span>
                    </td>
                    <td class="mono">
                        <div class="${{r.bb_per_100 >= 0 ? 'text-pos' : 'text-neg'}}">${{r.bb_per_100 >= 0 ? '+' : ''}}${{r.bb_per_100}} BB/100</div>
                        <div class="text-neutral" style="font-size:0.71rem;">[${{r.bb_per_100_ci95[0]}}, ${{r.bb_per_100_ci95[1]}}]</div>
                    </td>
                    <td class="mono">
                        <div class="${{r.ev_bb_per_100 >= 0 ? 'text-pos' : 'text-neg'}}">${{r.ev_bb_per_100 >= 0 ? '+' : ''}}${{r.ev_bb_per_100}} BB/100</div>
                        <div class="text-neutral" style="font-size:0.71rem;">[${{r.ev_bb_per_100_ci95[0]}}, ${{r.ev_bb_per_100_ci95[1]}}]</div>
                    </td>
                    <td class="mono">
                        <div style="color:var(--accent-blue); font-weight:700;">GTO: ${{r.gto_alignment_pct}}%</div>
                        <div class="${{r.strategic_error_rate_pct <= 8 ? 'text-pos' : 'text-neg'}}">Err: ${{r.strategic_error_rate_pct}}% (-${{r.ev_lost_per_100_bb}} BB/100)</div>
                    </td>
                    <td style="font-size:0.76rem;">${{deltaHtml}}</td>
                    <td style="font-size:0.75rem; line-height:1.35;">${{metricsChanges}}</td>
                    <td style="max-width:250px;">
                        <span class="badge ${{badgeClass}}">${{vState}}</span>
                        <div style="font-size:0.73rem; color:var(--text-muted); margin-top:4px;">${{cmp.summary || ''}}</div>
                    </td>
                </tr>`;
            }}).join('');
        }}

        function renderDecisionsTable(st) {{
            const tbody = document.getElementById('decisionsTableBody');
            const decs = [...st.decisions];
            const sortCfg = sortState.decisions;
            decs.sort((a, b) => {{
                const va = parseFloat(a[sortCfg.col]) || 0;
                const vb = parseFloat(b[sortCfg.col]) || 0;
                return sortCfg.asc ? va - vb : vb - va;
            }});
            const topDecs = decs.slice(0, 50);

            tbody.innerHTML = topDecs.map(d => {{
                const evObj = d.ev_by_action || {{}};
                const evStr = Object.entries(evObj)
                    .map(([k, v]) => `${{k}}: ${{v >= 0 ? '+' : ''}}${{v}}`)
                    .join(' | ') || 'N/A';

                let qBadge = 'badge-green';
                if (d.decision_quality === 'EVALUACION_NO_DISPONIBLE') qBadge = 'badge-purple';
                else if (d.decision_quality === 'BLUNDER_EV' || d.decision_quality === 'ERROR_ESTRATEGICO') qBadge = 'badge-red';
                else if (d.decision_quality === 'INACCURACY') qBadge = 'badge-amber';
                else if (d.decision_quality === 'ADAPTACION_EXPLOTATIVA') qBadge = 'badge-blue';

                const resBB = parseFloat(d.hand_final_result_bb) || 0;
                const srcTag = d.is_synthetic ? '<span class="badge badge-purple">LAB</span>' : '<span class="badge badge-green">LIVE</span>';
                const intendedInfo = (d.intended_action && d.intended_action !== d.action_taken) ? ` <small class="text-neutral">(IA: ${{d.intended_action}})</small>` : '';

                return `<tr>
                    <td class="mono">
                        <div>${{srcTag}} <strong>v${{d.version_id}}</strong></div>
                        <div class="text-neutral">${{d.scenario_id || d.hand_uid.substring(0, 10)}}</div>
                    </td>
                    <td><strong>${{d.position}}</strong><br/><span class="text-neutral">${{d.street}}</span></td>
                    <td class="mono">
                        <div style="color:#f8fafc; font-weight:700;">🎴 ${{d.hole_cards || 'ILEGIBLE'}}</div>
                        <div class="text-neutral">🂠 ${{d.board || '(Preflop)'}}</div>
                    </td>
                    <td class="mono">Bote: ${{d.pot_size}}<br/><span class="text-neutral">Stack: ${{d.effective_stack}}</span></td>
                    <td><strong>${{d.action_taken}}</strong>${{intendedInfo}} ${{d.bet_amount > 0 ? `(${{d.bet_amount}})` : ''}}</td>
                    <td style="color:var(--accent-blue); font-weight:700;">${{d.gto_recommended_action}} ${{d.gto_recommended_sizing > 0 ? `(${{d.gto_recommended_sizing}})` : ''}}</td>
                    <td class="mono" style="font-size:0.72rem; max-width:220px;">${{evStr}}<br/><span class="text-neutral">Eq: ${{(d.estimated_equity*100).toFixed(1)}}% | PO: ${{d.pot_odds_pct}}%</span></td>
                    <td class="mono ${{d.ev_lost_bb > 0.5 ? 'text-neg' : 'text-neutral'}}">-${{d.ev_lost_bb}} BB<br/><span class="text-neutral">(-${{d.ev_lost_chips}} f.)</span></td>
                    <td style="max-width:230px;">
                        <span class="badge ${{qBadge}}">${{d.decision_quality}}</span>
                        ${{d.is_missed_opportunity ? '<span class="badge badge-amber">MISSED VALUE</span>' : ''}}
                        <div style="font-size:0.72rem; color:var(--text-muted); margin-top:3px;">${{d.error_description}}</div>
                    </td>
                    <td>
                        <strong>${{d.opponent_nick || 'Rival'}}</strong><br/>
                        <span class="badge badge-blue" style="font-size:0.67rem;">${{(!d.opponent_profile || d.opponent_profile === 'SIN DATOS' || d.opponent_profile === 'DESCONOCIDO') ? 'JUGADOR NUEVO' : d.opponent_profile}}</span>
                    </td>
                    <td class="mono ${{resBB >= 0 ? 'text-pos' : 'text-neg'}}">${{resBB >= 0 ? '+' : ''}}${{resBB}} BB</td>
                </tr>`;
            }}).join('');
        }}

        function renderSystemDiagnosticsSection() {{
            const sys = RAW_DATA.system_reliability || {{}};
            const decL = sys.decision_latency || {{}};
            const enqL = sys.enqueue_latency || {{}};
            const sqlL = sys.sqlite_write_latency || {{}};
            const procL = sys.stat_processing_latency || {{}};
            const dbInt = sys.db_integrity || {{}};
            const dbCounts = dbInt.counts || {{}};

            document.getElementById('systemDiagnosticsContainer').innerHTML = `
                <div class="grid-2">
                    <div class="card">
                        <div class="card-title">
                            <span>⏱️ Distribución Empírica de Latencias (Percentiles P50, P95 y P99 en ms)</span>
                            <span class="badge badge-blue">Medido con perf_counter_ns</span>
                        </div>
                        <div class="table-wrapper" style="margin-bottom:0;">
                            <table>
                                <thead>
                                    <tr>
                                        <th>Etapa del Sistema</th>
                                        <th>Muestras</th>
                                        <th>Media (ms)</th>
                                        <th>P50 / Mediana</th>
                                        <th>P95 (ms)</th>
                                        <th>P99 (ms)</th>
                                        <th>Máx (ms)</th>
                                    </tr>
                                </thead>
                                <tbody class="mono">
                                    <tr>
                                        <td><strong>1. Latencia de Decisión (IA / Fallback)</strong></td>
                                        <td>${{decL.count || 0}}</td>
                                        <td>${{decL.mean_ms || 0}} ms</td>
                                        <td class="text-pos">${{decL.p50_ms || 0}} ms</td>
                                        <td>${{decL.p95_ms || 0}} ms</td>
                                        <td>${{decL.p99_ms || 0}} ms</td>
                                        <td>${{decL.max_ms || 0}} ms</td>
                                    </tr>
                                    <tr>
                                        <td><strong>2. Latencia de Registro / Encolado (Hilo Bot)</strong></td>
                                        <td>${{enqL.count || 0}}</td>
                                        <td>${{enqL.mean_ms || 0}} ms</td>
                                        <td class="text-pos">${{enqL.p50_ms || 0}} ms</td>
                                        <td>${{enqL.p95_ms || 0}} ms</td>
                                        <td>${{enqL.p99_ms || 0}} ms</td>
                                        <td>${{enqL.max_ms || 0}} ms</td>
                                    </tr>
                                    <tr>
                                        <td><strong>3. Escritura Asíncrona SQLite (Worker)</strong></td>
                                        <td>${{sqlL.count || 0}}</td>
                                        <td>${{sqlL.mean_ms || 0}} ms</td>
                                        <td class="text-pos">${{sqlL.p50_ms || 0}} ms</td>
                                        <td>${{sqlL.p95_ms || 0}} ms</td>
                                        <td>${{sqlL.p99_ms || 0}} ms</td>
                                        <td>${{sqlL.max_ms || 0}} ms</td>
                                    </tr>
                                    <tr>
                                        <td><strong>4. Motor de Procesamiento Estadístico</strong></td>
                                        <td>${{procL.count || 0}}</td>
                                        <td>${{procL.mean_ms || 0}} ms</td>
                                        <td class="text-pos">${{procL.p50_ms || 0}} ms</td>
                                        <td>${{procL.p95_ms || 0}} ms</td>
                                        <td>${{procL.p99_ms || 0}} ms</td>
                                        <td>${{procL.max_ms || 0}} ms</td>
                                    </tr>
                                </tbody>
                            </table>
                        </div>
                        <p style="font-size:0.75rem; color:var(--text-muted); margin-top:10px;">
                            ${{sys.latency_methodology_note || ''}}
                        </p>
                    </div>
                    <div class="card">
                        <div class="card-title">
                            <span>🛡️ Salud de Almacenamiento SQLite, Memoria RSS y Cola Asíncrona</span>
                            <span class="badge ${{dbInt.is_healthy ? 'badge-green' : 'badge-red'}}">PRAGMA integrity_check: ${{dbInt.sqlite_integrity_check || 'ok'}}</span>
                        </div>
                        <div class="table-wrapper" style="margin-bottom:0;">
                            <table>
                                <tbody class="mono">
                                    <tr><td><strong>Modo de Journal SQLite</strong></td><td>${{(dbInt.journal_mode || 'wal').toUpperCase()}} (${{dbInt.db_size_kb || 0}} KB en disco)</td></tr>
                                    <tr><td><strong>Uso de Memoria RSS del Proceso</strong></td><td>${{sys.memory_rss_mb || 0}} MB</td></tr>
                                    <tr><td><strong>Errores de Almacenamiento / Registros Perdidos</strong></td><td class="${{(sys.storage_errors_total || 0) === 0 && (sys.dropped_records_total || 0) === 0 ? 'text-pos' : 'text-neg'}}">Errores: ${{sys.storage_errors_total || 0}} | Descartados: ${{sys.dropped_records_total || 0}} | Pico Cola: ${{sys.max_pending_queue_tasks || 0}}</td></tr>
                                    <tr><td><strong>Verificación Anti-Duplicados</strong></td><td class="text-pos">Manos duplicadas: ${{dbInt.duplicate_hands_groups || 0}} | Decisiones duplicadas: ${{dbInt.duplicate_decisions_groups || 0}}</td></tr>
                                    <tr><td><strong>Registros en Vivo vs Laboratorio</strong></td><td>Live: ${{dbCounts.live_hands || 0}}m (${{dbCounts.live_decisions || 0}}d) | Benchmark: ${{dbCounts.benchmark_hands || 0}}m (${{dbCounts.benchmark_decisions || 0}}d)</td></tr>
                                    <tr><td><strong>Hash de Código Activo</strong></td><td><code>${{dbInt.code_hash || '--'}}</code></td></tr>
                                </tbody>
                            </table>
                        </div>
                    </div>
                </div>
            `;
        }}

        function exportFilteredData(type) {{
            if (!currentFilteredState) return;
            let rows = [];
            if (type === 'hands') rows = currentFilteredState.hands;
            else if (type === 'decisions') rows = currentFilteredState.decisions;
            else if (type === 'metrics') rows = RAW_DATA.main_metrics_table || [];
            else if (type === 'comparisons') rows = (RAW_DATA.version_comparison && RAW_DATA.version_comparison.versions) || [];

            if (!rows.length) {{ alert('No hay registros para exportar.'); return; }}
            const keys = Object.keys(rows[0]).filter(k => typeof rows[0][k] !== 'object');
            const csvLines = [keys.join(',')];
            rows.forEach(r => {{
                const line = keys.map(k => {{
                    const val = r[k] === null || r[k] === undefined ? '' : String(r[k]).replace(/"/g, '""');
                    return `"${{val}}"`;
                }}).join(',');
                csvLines.push(line);
            }});
            const blob = new Blob([csvLines.join('\\n')], {{ type: 'text/csv;charset=utf-8;' }});
            const url = URL.createObjectURL(blob);
            const a = document.createElement('a');
            a.href = url;
            a.download = `poker_analytics_${{type}}.csv`;
            a.click();
        }}

        function exportFilteredJSON() {{
            if (!currentFilteredState) return;
            const payload = {{
                exported_at: new Date().toISOString(),
                traceability: {{
                    code_hash: RAW_DATA.data_source_summary?.code_hash,
                    filters: getActiveFilters()
                }},
                summary: {{
                    hands: currentFilteredState.n,
                    net_profit_bb: currentFilteredState.netBB,
                    bb_per_100: currentFilteredState.bb100,
                    ev_bb_per_100: currentFilteredState.evBB100,
                    allin_ev_bb_per_100: currentFilteredState.allinBB100,
                    gto_alignment_pct: currentFilteredState.gtoPct,
                    strategic_error_rate_pct: currentFilteredState.errPct
                }},
                version_comparison: RAW_DATA.version_comparison,
                intelligent_diagnostics: RAW_DATA.intelligent_diagnostics,
                system_reliability: RAW_DATA.system_reliability,
                hands: currentFilteredState.hands,
                decisions: currentFilteredState.decisions
            }};
            const blob = new Blob([JSON.stringify(payload, null, 2)], {{ type: 'application/json' }});
            const url = URL.createObjectURL(blob);
            const a = document.createElement('a');
            a.href = url;
            a.download = 'poker_analytics_traceable_report.json';
            a.click();
        }}

        let lastHandsCount = (RAW_DATA.all_hands || []).length;
        let lastDecisionsCount = (RAW_DATA.all_decisions || []).length;
        let lastLiveHash = JSON.stringify(RAW_DATA.live_status || {{}});
        let liveOpponentFilter = 'all';

        function setLiveOpponentFilter(filterName) {{
            liveOpponentFilter = filterName || 'all';
            document.querySelectorAll('#live-opp-filter-bar .btn-filter-live').forEach(btn => {{
                const isAct = btn.getAttribute('data-live-filter') === liveOpponentFilter;
                btn.style.background = isAct ? '#1e293b' : '#0f172a';
                btn.style.color = isAct ? '#f8fafc' : '#94a3b8';
            }});
            renderLiveSection(RAW_DATA.live_status || {{}}, RAW_DATA.historical_profiles || []);
        }}

        function _statusBadgeStyle(st) {{
            const s = (st || 'ACTIVE').toUpperCase();
            if (s === 'ACTIVE') return 'background:rgba(16,185,129,0.18); color:#34d399; border:1px solid rgba(16,185,129,0.4);';
            if (s === 'FOLDED') return 'background:rgba(148,163,184,0.18); color:#cbd5e1; border:1px solid rgba(148,163,184,0.35);';
            if (s === 'SIT_OUT') return 'background:rgba(245,158,11,0.18); color:#fbbf24; border:1px solid rgba(245,158,11,0.4);';
            if (s === 'DISCONNECTED' || s === 'ELIMINATED') return 'background:rgba(239,68,68,0.18); color:#f87171; border:1px solid rgba(239,68,68,0.4);';
            return 'background:rgba(56,189,248,0.15); color:#38bdf8; border:1px solid rgba(56,189,248,0.35);';
        }}

        function renderLiveSection(live, historical) {{
            const tInfo = live.table_info || {{}};
            const elHandNum = document.getElementById('live-hand-num');
            if (elHandNum) elHandNum.textContent = live.hand_number || 1;
            const elMod = document.getElementById('live-modalidad');
            if (elMod) elMod.textContent = live.modalidad || 'Twister 3-Max';
            const elStreet = document.getElementById('live-street-badge');
            if (elStreet) elStreet.textContent = live.current_street || 'PREFLOP';
            const elMyCards = document.getElementById('live-my-cards');
            if (elMyCards) elMyCards.textContent = (live.my_cards || []).join(' ') || 'Esperando cartas...';
            const elBoard = document.getElementById('live-board-cards');
            if (elBoard) elBoard.textContent = (live.community_cards || []).join(' ') || 'Ninguna';
            const elPot = document.getElementById('live-pot');
            if (elPot) elPot.textContent = '$' + (live.last_pot || 0);
            const elPos = document.getElementById('live-pos');
            if (elPos) elPos.textContent = live.position_name || 'Desconocida';

            const elCounts = document.getElementById('live-table-counts');
            if (elCounts) {{
                elCounts.textContent = `${{tInfo.asientos_ocupados || 0}}/${{tInfo.max_asientos || 3}} ocup. · ${{tInfo.jugadores_activos || 0}} act. · ${{tInfo.jugadores_en_mano || 0}} en mano`;
            }}
            const elOppCount = document.getElementById('live-opponents-count');
            if (elOppCount) {{
                elOppCount.textContent = `${{tInfo.numero_oponentes_mesa || 0}} en mesa (${{tInfo.numero_oponentes_en_mano || 0}} en mano)`;
            }}
            const elFree = document.getElementById('live-seats-free');
            if (elFree) elFree.textContent = tInfo.asientos_disponibles !== undefined ? tInfo.asientos_disponibles : '-';

            const grid = document.getElementById('live-opponents-grid');
            if (grid) {{
                const liveOpps = Array.isArray(live.opponents) ? live.opponents : [];
                let listToRender = [];
                if (liveOpponentFilter === 'historical') {{
                    listToRender = Array.isArray(historical) ? historical : [];
                }} else if (liveOpponentFilter === 'active') {{
                    listToRender = liveOpps.filter(o => (o.status || 'ACTIVE').toUpperCase() === 'ACTIVE');
                }} else if (liveOpponentFilter === 'inactive') {{
                    listToRender = liveOpps.filter(o => (o.status || 'ACTIVE').toUpperCase() !== 'ACTIVE');
                }} else {{
                    listToRender = liveOpps;
                }}

                if (listToRender.length === 0) {{
                    const emptyMsg = liveOpponentFilter === 'historical'
                        ? 'No hay perfiles históricos registrados en la base de datos.'
                        : 'No hay oponentes que coincidan con este filtro en la mesa actual.';
                    grid.innerHTML = `<div style="background: var(--bg-card-alt); padding: 14px; border-radius: 8px; color: var(--text-muted); font-size: 0.85rem;">${{emptyMsg}}</div>`;
                }} else {{
                    grid.innerHTML = listToRender.map(opp => {{
                        const st = (opp.status || 'ACTIVE').toUpperCase();
                        const seatTxt = opp.seat ? `Asiento #${{opp.seat}}` : 'Asiento -';
                        const stackTxt = opp.stack ? ` · Stack: ${{opp.stack}}` : '';
                        const turnBadge = opp.is_turn ? `<span class="badge badge-green" style="margin-left:6px;">TURNO</span>` : '';
                        return `
                            <div style="background: var(--bg-card-alt); border: 1px solid var(--border-color); border-top: 3px solid #f59e0b; border-radius: 10px; padding: 12px;">
                                <div style="display:flex; justify-content:space-between; align-items:center; gap:8px; margin-bottom:6px;">
                                    <div style="font-weight:700; color:#f8fafc; font-size:0.95rem;">
                                        👤 ${{opp.nickname || opp.player_id || 'Rival'}} <span style="color:var(--text-muted); font-size:0.75rem;">(${{seatTxt}}${{stackTxt}})</span>${{turnBadge}}
                                    </div>
                                    <span style="padding:2px 8px; border-radius:999px; font-size:0.72rem; font-weight:700; ${{_statusBadgeStyle(st)}}">${{st}}</span>
                                </div>
                                <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:8px; font-size:0.78rem;">
                                    <span class="badge badge-amber">${{opp.estilo || 'Perfil desconocido'}}</span>
                                    <span style="color:#94a3b8;">Confianza: <strong style="color:#e2e8f0;">${{opp.confianza_nivel || 'MUESTRA INSUFICIENTE'}} (${{opp.confianza_pct || 0}}%)</strong></span>
                                </div>
                                <div style="display:grid; grid-template-columns: repeat(4, 1fr); gap:6px; font-size:0.76rem; color:var(--text-muted); background:#0b1120; padding:8px; border-radius:6px; margin-bottom:8px;">
                                    <div>Manos: <strong style="color:#f8fafc;">${{opp.manos || 0}}</strong></div>
                                    <div>VPIP: <strong style="color:#38bdf8;">${{opp.vpip_pct || 0}}%</strong></div>
                                    <div>PFR: <strong style="color:#a855f7;">${{opp.pfr_pct || 0}}%</strong></div>
                                    <div>3-Bet: <strong style="color:#f59e0b;">${{opp.three_bet_pct || 0}}%</strong></div>
                                    <div>F3B: <strong style="color:#e2e8f0;">${{opp.fold_to_3bet_pct || 0}}%</strong></div>
                                    <div>AF: <strong style="color:#10b981;">${{opp.agresividad || 1.0}}</strong></div>
                                    <div>AFq: <strong style="color:#10b981;">${{opp.aggression_freq_pct || 0}}%</strong></div>
                                    <div>WTSD: <strong style="color:#e2e8f0;">${{opp.wtsd_pct || 0}}%</strong></div>
                                </div>
                                <div style="font-size:0.75rem; color:#cbd5e1; margin-bottom:6px;">
                                    <strong>Tendencia:</strong> ${{opp.tendencia || 'Sin historial observado'}} (WWSF: ${{opp.wwsf_pct || 0}}%)
                                </div>
                                <div style="font-size:0.75rem; background:rgba(245,158,11,0.08); border:1px dashed rgba(245,158,11,0.35); padding:7px; border-radius:6px; color:#fbbf24;">
                                    ${{opp.recomendacion_tactica || 'Muestra insuficiente. Mantener estrategia GTO estándar.'}}
                                </div>
                            </div>
                        `;
                    }}).join('');
                }}
            }}

            const eventsFeed = document.getElementById('live-events-feed');
            if (eventsFeed && live.live_events && live.live_events.length > 0) {{
                eventsFeed.innerHTML = live.live_events.map(ev => `<div>${{ev}}</div>`).join('');
                eventsFeed.scrollTop = eventsFeed.scrollHeight;
            }}
        }}

        renderLiveSection(RAW_DATA.live_status || {{}}, RAW_DATA.historical_profiles || []);
        let lastTelemetrySampleCount = (RAW_DATA.system_reliability && RAW_DATA.system_reliability.latency_sample_count) || 0;

        async function updateLiveMesaUI() {{
            try {{
                const res = await fetch('/api/live', {{ cache: 'no-store' }});
                if (!res.ok) return;
                const live = await res.json();

                if (live && (live.active || live.hand_number)) {{
                    const mergedLive = Object.assign({{}}, RAW_DATA.live_status || {{}}, live);
                    RAW_DATA.live_status = mergedLive;
                    renderLiveSection(mergedLive, RAW_DATA.historical_profiles || []);

                    const statusEl = document.getElementById('bot-live-status-indicator');
                    if (statusEl) {{
                        if (live.paused) {{
                            statusEl.className = 'badge badge-amber';
                            statusEl.textContent = '⏸️ AGENTE PAUSADO';
                        }} else {{
                            statusEl.className = 'badge badge-green';
                            statusEl.textContent = '🟢 AGENTE EN VIVO / ACTIVO';
                        }}
                    }}

                    const activeMod = live.active_model;
                    const modSelect = document.getElementById('botModelSelect');
                    if (modSelect && activeMod && document.activeElement !== modSelect) {{
                        modSelect.value = activeMod;
                    }}
                }}
            }} catch (err) {{
                // Modo estático o sin servidor activo
            }}
        }}

        async function switchBotModel(newModel) {{
            try {{
                const res = await fetch('/api/bot/model', {{
                    method: 'POST',
                    headers: {{ 'Content-Type': 'application/json' }},
                    body: JSON.stringify({{ model: newModel }})
                }});
                const data = await res.json();
                const sel = document.getElementById('botModelSelect');
                if (sel && data.active_model) sel.value = data.active_model;
                alert('🤖 ' + (data.message || 'Modelo cambiado a ' + newModel));
            }} catch(err) {{
                alert('Error al cambiar de modelo: ' + err);
            }}
        }}

        let isFetchingAnalytics = false;
        async function checkLiveUpdates() {{
            if (isFetchingAnalytics) return;
            const badge = document.getElementById('liveSyncBadge');
            try {{
                if (RAW_DATA.live_status && RAW_DATA.live_status.total_hands !== undefined) {{
                    const th = RAW_DATA.live_status.total_hands;
                    const td = RAW_DATA.live_status.total_decisions;
                    if (th === lastHandsCount && td === lastDecisionsCount) {{
                        return;
                    }}
                }}

                isFetchingAnalytics = true;
                const res = await fetch('/api/analytics', {{ cache: 'no-store' }});
                if (!res.ok) throw new Error('Modo archivo estático');
                const freshData = await res.json();
                RAW_DATA.live_status = freshData.live_status || {{}};
                RAW_DATA.historical_profiles = freshData.historical_profiles || [];
                renderLiveSection(RAW_DATA.live_status, RAW_DATA.historical_profiles);

                const newHands = (freshData.all_hands || []).length;
                const newDecs = (freshData.all_decisions || []).length;
                const newTelemCount = (freshData.system_reliability && freshData.system_reliability.latency_sample_count) || 0;
                const freshLiveHash = JSON.stringify(freshData.live_status || {{}});

                if (newHands !== lastHandsCount || newDecs !== lastDecisionsCount || newTelemCount !== lastTelemetrySampleCount || freshLiveHash !== lastLiveHash) {{
                    RAW_DATA = freshData;
                    lastHandsCount = newHands;
                    lastDecisionsCount = newDecs;
                    lastTelemetrySampleCount = newTelemCount;
                    lastLiveHash = freshLiveHash;
                    applyDashboardFilters();
                }}

                if (badge) {{
                    badge.className = 'badge badge-green';
                    badge.textContent = `⚡ Reactivo En Tiempo Real (${{newHands}} manos | ${{newDecs}} dec.)`;
                }}
            }} catch (err) {{
                if (badge) {{
                    badge.className = 'badge badge-blue';
                    badge.textContent = `📄 Vista Autónoma (${{lastHandsCount}}m | Auto-actualizable por bot.py)`;
                }}
            }} finally {{
                isFetchingAnalytics = false;
            }}
        }}

        async function controlBotAction(action) {{
            try {{
                const res = await fetch('/api/bot/' + action, {{ method: 'POST' }});
                const data = await res.json();
                const statusEl = document.getElementById('bot-live-status-indicator');
                if (statusEl) {{
                    if (data.paused) {{
                        statusEl.className = 'badge badge-amber';
                        statusEl.textContent = '⏸️ AGENTE PAUSADO';
                    }} else {{
                        statusEl.className = 'badge badge-green';
                        statusEl.textContent = '🟢 AGENTE EN VIVO / ACTIVO';
                    }}
                }}
                alert(data.message || 'Acción ejecutada con éxito');
            }} catch(err) {{
                alert('Error al comunicarse con el agente: ' + err);
            }}
        }}

        window.addEventListener('DOMContentLoaded', () => {{
            initFilters();
            applyDashboardFilters();
            updateLiveMesaUI();
            setInterval(updateLiveMesaUI, 1200);
            setInterval(checkLiveUpdates, 4000);
        }});
    </script>
</body>
</html>

"""


def _obtener_datos_live_para_dashboard(db_path: Path) -> Dict[str, Any]:
    """Obtiene el snapshot multijugador en vivo (memoria de bot o archivo) y perfiles históricos."""
    import sys
    import config
    from live_table_tracker import cargar_snapshot_live_archivo
    from opponents_profiler import OpponentProfiler

    live_status: Dict[str, Any] = {}
    bot_mod = sys.modules.get("bot")
    if bot_mod is not None and hasattr(bot_mod, "obtener_live_status"):
        try:
            live_status = bot_mod.obtener_live_status() or {}
        except Exception:
            live_status = {}
    if not live_status:
        live_status = cargar_snapshot_live_archivo()
    if not live_status:
        max_seats = getattr(config, "MAX_TABLE_SEATS", 3)
        live_status = {
            "hand_number": 1,
            "current_street": "PREFLOP",
            "my_cards": [],
            "community_cards": [],
            "last_pot": 0,
            "position_name": "Desconocida",
            "modalidad": getattr(config, "POKER_MODE", "Twister 3-Max"),
            "table_info": {
                "max_asientos": max_seats,
                "asientos_ocupados": 0,
                "asientos_disponibles": max_seats,
                "jugadores_activos": 0,
                "jugadores_en_mano": 0,
                "numero_oponentes_mesa": 0,
                "numero_oponentes_activos": 0,
                "numero_oponentes_en_mano": 0,
            },
            "opponents": [],
            "live_events": [],
        }

    try:
        prof = OpponentProfiler(db_path=db_path)
        try:
            historical = prof.listar_perfiles_historicos(limit=100)
        finally:
            prof.close()
            prof.dispose()
    except Exception:
        historical = []
    return {"live_status": live_status, "historical_profiles": historical}


def generar_dashboard_html(
    db_path: Path = DB_PATH,
    output_html: Path = DEFAULT_DASHBOARD_PATH,
    output_path: Optional[Path] = None,
    auto_seed_benchmark_if_empty: bool = False,
) -> Dict[str, Any]:
    """
    Recopila todas las métricas, ejecuta las 6 exportaciones CSV/JSON trazables
    y genera el archivo HTML interactivo de 9 secciones.
    """
    if output_path is not None:
        output_html = Path(output_path)
    storage = AnalyticsStorage(db_path=db_path)
    engine = PokerAnalyticsEngine(storage=storage)
    analytics = engine.calcular_analiticas_completas()
    analytics["all_hands"] = storage.consultar_manos()
    analytics["all_decisions"] = storage.consultar_decisiones()
    live_bundle = _obtener_datos_live_para_dashboard(db_path)
    analytics["live_status"] = live_bundle["live_status"]
    analytics["historical_profiles"] = live_bundle["historical_profiles"]

    output_html = Path(output_html)
    output_html.parent.mkdir(parents=True, exist_ok=True)
    html_str = _build_dashboard_html_content(analytics)
    output_html.write_text(html_str, encoding="utf-8")

    csv_paths = storage.exportar_csv(output_dir=output_html.parent / "exports", extra_summary=analytics)
    json_path = storage.exportar_json(
        output_path=output_html.parent / "exports" / "analytics_report.json",
        extra_summary={
            "general_performance": analytics["general_performance"],
            "gameplay_hud": analytics["gameplay_hud"],
            "statistical_evaluation": analytics["statistical_evaluation"],
            "version_comparison": analytics["version_comparison"],
            "intelligent_diagnostics": analytics["intelligent_diagnostics"],
            "system_reliability": analytics["system_reliability"],
        },
    )

    return {
        "dashboard_html_path": str(output_html.resolve()),
        "csv_exports": csv_paths,
        "json_export": str(Path(json_path).resolve()),
        "hands_analyzed": analytics["general_performance"]["hands_played"],
        "live_hands_analyzed": analytics["general_performance"]["live_hands_played"],
        "benchmark_hands_analyzed": analytics["general_performance"]["benchmark_hands_played"],
        "versions_analyzed": len(analytics["version_comparison"]["versions"]),
        "best_version": analytics["version_comparison"]["best_overall_statistical_version"],
    }


def imprimir_resumen_consola(db_path: Path = DB_PATH) -> None:
    """Imprime en terminal un informe estructurado de las 9 secciones del sistema de analíticas."""
    storage = AnalyticsStorage(db_path=db_path)
    engine = PokerAnalyticsEngine(storage=storage)
    data = engine.calcular_analiticas_completas()
    gp = data["general_performance"]
    hud = data["gameplay_hud"]
    strat = data["strategic_analysis"]
    opp = data["opponent_analysis"]
    stat = data["statistical_evaluation"]
    vcomp = data["version_comparison"]
    sys_rel = data["system_reliability"]

    print("\n" + "=" * 96)
    print(" 📊 SISTEMA AVANZADO DE INTELIGENCIA, AUDITORÍA Y ANALÍTICAS DEL BOT DE PÓKER ")
    print("=" * 96)
    print(
        f"• Separación de Origen   : Manos Reales (Live) = {gp['live_hands_played']} | "
        f"Manos Benchmark (Lab) = {gp['benchmark_hands_played']} | Code Hash = {data['data_source_summary']['code_hash']}"
    )
    print(f"• Manos Totales / Ses.   : {gp['hands_played']} manos ({gp['sessions_count']} sesiones) | Win%: {gp['win_pct']}%")
    print(f"• Beneficio Neto Real    : {gp['net_profit_chips']:+.2f} fichas ({gp['net_profit_bb']:+.2f} BB) | ROI: {gp['roi_pct']:+.2f}%")
    print(f"• Winrate Real Observado : {gp['bb_per_100']:+.2f} BB/100 (IC 95%: [{gp['bb_per_100_ci95'][0]:+.2f}, {gp['bb_per_100_ci95'][1]:+.2f}])")
    print(f"• EV Winrate / All-in EV : {gp['ev_bb_per_100']:+.2f} BB/100 | All-in EV: {gp['allin_ev_bb_per_100']:+.2f} BB/100")
    print(f"• HUD Principal          : VPIP {hud['vpip_pct']}% | PFR {hud['pfr_pct']}% | 3-Bet {hud['three_bet_pct']}% | AF(Post) {hud['aggression_factor']} | WTSD {hud['wtsd_pct']}% | W$SD {hud['wsd_pct']}%")
    print(f"• Calidad EV / Heur.-GTO : Calidad EV {strat['gto_alignment_pct']}% (Match Exacto: {strat['exact_gto_match_pct']}%) | Errores: {strat['strategic_errors_count']} ({strat['strategic_error_rate_pct']}%) | Regret: -{strat['ev_lost_per_100_hands_bb']} BB/100")
    print(f"• Oportunidades Perdidas : {strat['missed_opportunities_count']} casos ({strat['missed_opportunities_rate_pct']}%) | Evaluaciones N/D: {strat['unavailable_evaluations_count']}")
    print(f"• Perfilado de Oponentes : {opp['opponents_analyzed_count']} analizados | Detección: {opp['pattern_detection_frequency_pct']}% | Precisión Showdown: {opp['pattern_accuracy_pct']}% | Adaptación: {opp['adaptation_capacity_pct']}%")
    print(f"• Diagnóstico Varianza   : {stat['variance_analysis']['variance_state']} — {stat['variance_analysis']['variance_explanation']}")
    print(f"• Telemetría P50/P95/P99 : Enqueue P50={sys_rel['enqueue_latency']['p50_ms']}ms P95={sys_rel['enqueue_latency']['p95_ms']}ms P99={sys_rel['enqueue_latency']['p99_ms']}ms | SQLite P50={sys_rel['sqlite_write_latency']['p50_ms']}ms P95={sys_rel['sqlite_write_latency']['p95_ms']}ms | Integridad DB: {sys_rel['db_integrity']['sqlite_integrity_check']}")
    print("-" * 96)
    print(" 🔬 COMPARACIÓN DE VERSIONES EXPERIMENTALES (Con Corrección de Holm-Bonferroni):")
    for v in vcomp["versions"]:
        cmp = v.get("comparison_vs_prev") or {}
        v_state = cmp.get("verdict_state", "LINEA_BASE")
        p_holm = cmp.get("holm_corrected_p_value", "-")
        cohen_d = cmp.get("cohens_d", "-")
        print(
            f"  [v{v['version_id']}] {v['hands_evaluated']}m | Real: {v['bb_per_100']:+6.2f} BB/100 | "
            f"EV: {v['ev_bb_per_100']:+6.2f} BB/100 | GTO: {v['gto_alignment_pct']:5.1f}% | "
            f"Err: {v['strategic_error_rate_pct']:4.1f}% | p-Holm: {p_holm} | d: {cohen_d} | Estado: {v_state}"
        )
    print(f"\n🏆 Mejor Versión Estadística Global: v{vcomp['best_overall_statistical_version']}")
    print("=" * 96 + "\n")


def obtener_recuentos_rapidos_db(db_path: Path = DB_PATH) -> Dict[str, int]:
    try:
        import sqlite3
        with sqlite3.connect(db_path, timeout=1.0) as conn:
            cur = conn.cursor()
            nh = cur.execute("SELECT count(*) FROM analytics_hands").fetchone()[0]
            nd = cur.execute("SELECT count(*) FROM analytics_decisions").fetchone()[0]
            return {"total_hands": nh, "total_decisions": nd}
    except Exception:
        return {"total_hands": 0, "total_decisions": 0}


def obtener_estado_live_dict() -> Dict[str, Any]:
    """Obtiene el estado dinámico en vivo del agente (cartas, pot, calle, eventos y perfil del rival)."""
    counts = obtener_recuentos_rapidos_db()
    live_info: Dict[str, Any] = {
        "hand_number": 1,
        "current_street": "PREFLOP",
        "my_cards": [],
        "community_cards": [],
        "last_pot": 0,
        "position_name": "DESCONOCIDA",
        "opp_id": "",
        "opp_nick": "",
        "opp_profile": {},
        "live_events": [],
        "paused": False,
        "active": False,
        "total_hands": counts["total_hands"],
        "total_decisions": counts["total_decisions"],
    }
    try:
        import bot
        if hasattr(bot, "tracker") and bot.tracker:
            tr = bot.tracker
            prof = getattr(bot, "profiler", None)
            opp_p = prof.obtener_perfil_explotativo(tr.opp_id, tr.opp_nick) if prof else {}
            live_info = {
                "hand_number": getattr(tr, "hand_number", 1),
                "current_street": getattr(tr, "current_street", "PREFLOP"),
                "my_cards": list(getattr(tr, "my_cards", []) or []),
                "community_cards": list(getattr(tr, "community_cards", []) or []),
                "last_pot": getattr(tr, "last_pot", 0) or 0,
                "position_name": getattr(tr, "position_name", "DESCONOCIDA") or "DESCONOCIDA",
                "opp_id": str(getattr(tr, "opp_id", "") or ""),
                "opp_nick": str(getattr(tr, "opp_nick", "") or ""),
                "opp_profile": opp_p,
                "live_events": [str(ev) for ev in list(getattr(tr, "live_events", []) or [])[-20:]],
                "paused": getattr(bot, "bot_paused", False),
                "active": True,
                "total_hands": counts["total_hands"],
                "total_decisions": counts["total_decisions"],
            }
    except Exception:
        pass
    return live_info


def crear_app_flask_dashboard(db_path: Path = DB_PATH):
    """Crea la aplicación Flask para el Dashboard en tiempo real."""
    from flask import Flask, jsonify, request, make_response, send_from_directory
    app = Flask(__name__)
    app.config['DEBUG'] = False
    app.config['SEND_FILE_MAX_AGE_DEFAULT'] = 0

    info = generar_dashboard_html(db_path=db_path)
    html_path = Path(info["dashboard_html_path"])
    storage = AnalyticsStorage(db_path=db_path)
    engine = PokerAnalyticsEngine(storage=storage)

    @app.route('/', methods=['GET', 'HEAD'])
    @app.route('/index.html', methods=['GET', 'HEAD'])
    @app.route('/dashboard', methods=['GET', 'HEAD'])
    def index():
        if request.method == 'HEAD':
            resp = make_response('', 200)
            resp.headers['Content-Type'] = 'text/html; charset=utf-8'
            return resp
        content = html_path.read_text(encoding='utf-8')
        resp = make_response(content)
        resp.headers['Content-Type'] = 'text/html; charset=utf-8'
        resp.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
        resp.headers['Pragma'] = 'no-cache'
        resp.headers['Expires'] = '0'
        return resp

    @app.route('/static/<path:filename>', methods=['GET'])
    def static_files(filename):
        return send_from_directory('static', filename)

    @app.route('/api/live', methods=['GET'])
    def api_live():
        live_info = obtener_estado_live_dict()
        resp = jsonify(live_info)
        resp.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
        return resp

    @app.route('/api/analytics', methods=['GET'])
    def api_analytics():
        filters = {k: request.args.get(k) for k in request.args}
        data = engine.calcular_analiticas_completas(filters if filters else None)
        data["all_hands"] = storage.consultar_manos(filters if filters else None)
        data["all_decisions"] = storage.consultar_decisiones(filters if filters else None)
        live_bundle = _obtener_datos_live_para_dashboard(db_path)
        data["live_status"] = live_bundle["live_status"]
        data["historical_profiles"] = live_bundle["historical_profiles"]
        resp = jsonify(data)
        resp.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
        return resp

    @app.route('/api/bot/model', methods=['GET', 'POST'])
    def api_bot_model():
        import config
        if request.method == 'POST':
            body_data = request.get_json(silent=True) or {}
            new_mod = body_data.get("model") or getattr(config, "GEMINI_MODEL", "gemini-2.5-flash")
            config.ACTIVE_MODEL = new_mod
            if new_mod == "gemma-4b-local":
                try:
                    import threading
                    from poker_agent import precargar_gemma_local
                    threading.Thread(target=precargar_gemma_local, daemon=True).start()
                except Exception as e:
                    print(f"⚠️ Error precargando Gemma local: {e}")
            resp = jsonify({
                "status": "OK",
                "message": f"Modelo cambiado a {new_mod}",
                "active_model": config.ACTIVE_MODEL
            })
            resp.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
            return resp
        else:
            resp = jsonify({
                "active_model": getattr(config, "ACTIVE_MODEL", config.GEMINI_MODEL),
                "available_models": list(getattr(config, "AVAILABLE_MODELS", [config.GEMINI_MODEL, "gemma-4b-local", "gto-heuristic"])),
                "modo": config.obtener_modo_activo() if hasattr(config, "obtener_modo_activo") else getattr(config, "MODO", "gemini"),
            })
            resp.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
            return resp

    @app.route('/api/bot/<action>', methods=['POST'])
    def api_bot_action(action):
        import bot
        msg = ""
        if action == "pause":
            bot.bot_paused = True
            msg = "Agente pausado en vivo. No ejecutará acciones en mesa."
        elif action == "resume":
            bot.bot_paused = False
            msg = "Agente reanudado en vivo. Bucle reactivo activo."
        elif action == "rejoin":
            bot.bot_force_rejoin = True
            msg = "Solicitada reconexión y navegación automática a nueva partida."
        return jsonify({
            "status": "OK",
            "message": msg,
            "paused": getattr(bot, "bot_paused", False)
        })

    return app


def servir_dashboard_http(port: int = 8050, db_path: Path = DB_PATH, open_browser: bool = True) -> None:
    """Inicia un servidor Flask reactivo en tiempo real en segundo plano."""
    try:
        app = crear_app_flask_dashboard(db_path=db_path)
        url = f"http://127.0.0.1:{port}/dashboard"
        print(f"🌐 Servidor Flask iniciado en: {url}")
        if open_browser:
            try:
                webbrowser.open(url)
            except Exception:
                pass
        app.run(host="127.0.0.1", port=port, debug=False, use_reloader=False)
    except KeyboardInterrupt:
        print("\n🛑 Servidor detenido.")
    except Exception as e:
        print(f"⚠️ Error iniciando servidor Flask: {e}")


_dashboard_server_thread = None

def iniciar_servidor_dashboard_background(port: int = 8050, open_browser: bool = False) -> None:
    """Inicia el servidor HTTP del Dashboard de Analíticas en un hilo de fondo secundario (Daemon)."""
    global _dashboard_server_thread
    if _dashboard_server_thread and _dashboard_server_thread.is_alive():
        return

    import threading
    def _worker():
        try:
            servir_dashboard_http(port=port, open_browser=open_browser)
        except Exception:
            pass

    _dashboard_server_thread = threading.Thread(target=_worker, daemon=True)
    _dashboard_server_thread.start()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Generador y Servidor del Centro de Control de Analíticas del Bot de Póker")
    parser.add_argument("--serve", action="store_true", help="Levantar servidor HTTP interactivo en el puerto indicado")
    parser.add_argument("--port", type=int, default=8050, help="Puerto para el servidor HTTP (por defecto 8050)")
    parser.add_argument("--cli", action="store_true", help="Mostrar resumen ejecutivo en terminal")
    parser.add_argument("--reseed", action="store_true", help="Recalcular benchmark experimental de versiones")
    args = parser.parse_args()

    if args.reseed:
        ejecutar_benchmark_versiones(force_reseed=True)

    res_info = generar_dashboard_html()
    if args.cli or not args.serve:
        imprimir_resumen_consola()
        print(f"📄 Dashboard HTML generado en : {res_info['dashboard_html_path']}")
        print(f"📦 Reporte JSON exportado en  : {res_info['json_export']}")
        print(f"📊 Archivos CSV exportados en : {res_info['csv_exports']}")

    if args.serve:
        servir_dashboard_http(port=args.port)
