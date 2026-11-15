# DOCUMENTACIÓN TÉCNICA Y DE MANTENIMIENTO: SISTEMA AVANZADO DE INTELIGENCIA, AUDITORÍA Y ANALÍTICAS DEL BOT DE PÓKER

## 1. Arquitectura General y Principios de Rigor Técnico

El sistema de evaluación y optimización estratégica se integra de forma modular sobre la base de datos SQLite existente (`logs/poker_stats.db` en modo `WAL`), garantizando compatibilidad total con `opponents_profiler.py` y sin alterar la lógica competitiva de `bot.py`, `poker_agent.py` ni `poker_gto_engine.py`.

### Módulos del Sistema
1. **`analytics_db.py` (`AnalyticsStorage`)**:
   - Gestiona 8 tablas normalizadas con claves primarias deterministas (`hand_uid`, `decision_uid`, `verification_uid`, `experiment_id`, `telemetry_id`) y migración automática idempotente:
     - `oponentes`: Tabla histórica original de `opponents_profiler.py` (intacta).
     - `bot_versions`: Versiones experimentales (`version_id`, `parent_version`, `modified_params`, `config_hash`, `code_hash`, `random_seed`, `experiment_conditions`).
     - `analytics_sessions`: Sesiones de juego y laboratorio con bandera explícita `is_synthetic` (`0 = live`, `1 = benchmark`).
     - `analytics_hands`: Registro por mano con indicadores HUD, ganancias reales, `ev_net_bb` y `allin_ev_bb`.
     - `analytics_decisions`: Auditoría por decisión con acción propuesta por la IA (`intended_action`), acción realmente ejecutada en el DOM (`action_taken`), disponibilidad de información (`evaluation_available`), EV de cada acción legal, Regret (`ev_lost_bb`), oportunidades perdidas (`is_missed_opportunity`), segmento de bote (`pot_size_bucket`), profundidad de stack (`stack_depth_bucket`) y tiempos de encolado/escritura.
     - `analytics_opponent_verifications`: Verificación empírica de cartas mostradas en Showdown frente al perfil predicho.
     - `analytics_experiments`: Registro formal de experimentos controlados con semillas, hashes SHA-256, intervalos de confianza y pruebas estadísticas.
     - `system_telemetry`: Telemetría empírica de latencias y salud de almacenamiento.
2. **`analytics_engine.py` (`PokerAnalyticsEngine`)**:
   - Motor estadístico y de evaluación estratégica que separa estrictamente:
     1. **Resultados Financieros Observados** (fichas/BB netas, Winrate BB/100, Drawdown, Varianza, Sharpe Ratio validado).
     2. **Expectativa Matemática (EV y All-in EV)** e identificación de desviaciones por varianza (*Run Good* vs. *Bad Beats*).
     3. **Evaluación Heurística-GTO vs. Equilibrio de Nash** (diferenciando coincidencia con la heurística de `poker_gto_engine.py`, calidad según EV, frecuencias mixtas 70/20/10, Regret y Exploitability en el subjuego Push/Fold $\le 10\text{ BB}$).
3. **`analytics_diagnostics.py`**:
   - Generador de diagnósticos inteligentes automáticos (Fase 5, *Read-Only*) y calculador de percentiles reales P50, P95 y P99 (Fase 6).
4. **`analytics_logger.py` (`PokerAnalyticsLogger`)**:
   - Recolector asíncrono con cola acotada (`queue.Queue`), cerrojo reentrante (`RLock`), drenado seguro al cierre (`shutdown` + `atexit`) y medición con `time.perf_counter_ns()`.
5. **`analytics_benchmark.py`**:
   - Laboratorio de experimentación controlada (*Duplicate Poker / Common Random Numbers*) que ejecuta versiones sobre escenarios pareados idénticos.
6. **`analytics_dashboard.py`**:
   - Centro de Control HTML5 interactivo organizado en 9 secciones con tablas ordenables, explicaciones matemáticas contextuales, 8 gráficas dinámicas y servidor HTTP con sincronización automática (`/api/analytics`).

---

## 2. Definiciones Matemáticas Documentadas (Fórmula, Numerador, Denominador y Muestra)

| Métrica | Numerador | Denominador | Muestra ($n$) | Fórmula Matemática |
| :--- | :--- | :--- | :--- | :--- |
| **Winrate Observado (BB/100)** | $\sum \text{Net Profit BB}_i$ | $N_{\text{manos}}$ | $N_{\text{manos}}$ | $\frac{\sum_{i=1}^n \text{BB}_i}{n} \times 100$ |
| **EV Winrate (EV BB/100)** | $\sum \text{EV Net BB}_i$ | $N_{\text{manos}}$ | $N_{\text{manos}}$ | $\frac{\sum_{i=1}^n \text{EV\_BB}_i}{n} \times 100$ |
| **All-in EV (BB/100)** | $\sum (\text{Eq}_{\text{SD}} \cdot \text{Pot} - \text{Inv})/\text{BB}$ | $N_{\text{manos}}$ | $N_{\text{manos}}$ | $\frac{\sum_{i=1}^n \text{AllIn\_EV\_BB}_i}{n} \times 100$ |
| **Sharpe Ratio (100 manos)** | $\text{EV BB/100}$ | $\sigma_{\text{mano}} \times 10$ | $N_{\text{manos}} \ge 30$ | $\frac{\mu_{\text{EV},100}}{s_{\text{BB}} \cdot \sqrt{100}}$ (solo si $n \ge 30, s > 0$) |
| **VPIP%** | Manos con Call/Bet/Raise voluntario preflop | Total manos jugadas | $N_{\text{manos}}$ | $\frac{\text{Count}(\text{VPIP}=1)}{N_{\text{manos}}} \times 100$ |
| **PFR%** | Manos con Raise/All-in preflop | Total manos jugadas | $N_{\text{manos}}$ | $\frac{\text{Count}(\text{PFR}=1)}{N_{\text{manos}}} \times 100$ |
| **3-Bet%** | Re-subidas preflop ejecutadas | Oportunidades de 3-Bet preflop | $N_{\text{3Bet\_opp}}$ | $\frac{\text{Count}(\text{3Bet}=1)}{N_{\text{3Bet\_opp}}} \times 100$ |
| **Fold to 3-Bet%** | Folds tras enfrentar 3-Bet | Veces que enfrentó 3-Bet | $N_{\text{Faced\_3Bet}}$ | $\frac{\text{Count}(\text{Fold\_3Bet}=1)}{N_{\text{Faced\_3Bet}}} \times 100$ |
| **Aggression Factor (AF Postflop)** | Apuestas + Subidas Postflop | Calls Postflop | $\text{Aggr} + \text{Calls}$ | $\frac{\text{Bets}_{\text{post}} + \text{Raises}_{\text{post}}}{\max(1, \text{Calls}_{\text{post}})}$ |
| **Aggression Frequency (AFq%)** | Apuestas + Subidas Postflop | Bets + Raises + Calls + Folds Postflop | Acciones no-check | $\frac{\text{Bets} + \text{Raises}}{\text{Bets} + \text{Raises} + \text{Calls} + \text{Folds}} \times 100$ |
| **WTSD%** | Manos que llegaron al Showdown | Manos que vieron el Flop | $N_{\text{Saw\_Flop}}$ | $\frac{\text{Count}(\text{WTSD}=1)}{N_{\text{Saw\_Flop}}} \times 100$ |
| **W\$SD%** | Manos ganadas en el Showdown | Total de Showdowns jugados | $N_{\text{WTSD}}$ | $\frac{\text{Count}(\text{Won\_SD}=1)}{N_{\text{WTSD}}} \times 100$ |
| **WWSF%** | Manos ganadas habiendo visto el Flop | Manos que vieron el Flop | $N_{\text{Saw\_Flop}}$ | $\frac{\text{Count}(\text{Saw\_Flop} \land \text{Won})}{N_{\text{Saw\_Flop}}} \times 100$ |
| **CBet Flop%** | Apuestas de continuación en Flop | Oportunidades de CBet en Flop | $N_{\text{CBet\_opp}}$ | $\frac{\text{Count}(\text{CBet}=1)}{N_{\text{CBet\_opp}}} \times 100$ |
| **Fold to CBet%** | Folds ante CBet rival en Flop | Veces que enfrentó CBet en Flop | $N_{\text{Faced\_CBet}}$ | $\frac{\text{Count}(\text{Fold\_CBet}=1)}{N_{\text{Faced\_CBet}}} \times 100$ |

---

## 3. Comparación Estadística de Versiones y Corrección por Comparaciones Múltiples

Para evitar falsos positivos al comparar múltiples versiones experimentales ($v1.0, v1.1, v1.2, v1.3$), el motor implementa:
1. **Prueba $t$ Pareada** sobre los escenarios compartidos (`scenario_id`) y **Prueba $t$ de Welch** para muestras independientes.
2. **Tamaño del Efecto ($d$ de Cohen)** e **Intervalo de Confianza al 95%** de la diferencia ($\text{IC}_{95\%}(\Delta\text{EV})$).
3. **Corrección Secuencial de Holm-Bonferroni ($\alpha = 0.05$)**: ordena los $m$ $p$-valores de menor a mayor ($p_{(1)} \le \dots \le p_{(m)}$) y calcula $p_{(k)}^{\text{Holm}} = \max_{j \le k} \min(1, (m - j + 1) p_{(j)})$.
4. **Tres Estados Estrictos (`clasificar_estado_comparacion`)**:
   - `MEJORA`: Evidencia favorable y suficiente ($p_{\text{Holm}} < 0.05$, $\text{IC}_{95\%,\text{inf}} > 0$, $d \ge 0.15$) **y** tamaño muestral suficiente: en partidas en vivo exige $n \ge 5000$ manos por versión (`MIN_LIVE_HANDS_FOR_IMPROVEMENT`); en benchmark pareado exige el mínimo justificado por la potencia estadística calculada (`calcular_tamano_muestra_necesario`, con $\alpha = 0.05$ y potencia $0.80$).
   - `INCONCLUSO`: Evidencia estadística insuficiente o muestra por debajo del umbral requerido.
   - `REGRESIÓN`: Deterioro estadísticamente significativo en EV o incremento crítico en la tasa de errores.

---

## 4. Comandos de Ejecución y Mantenimiento

### Ejecutar la Suite de Pruebas Automatizadas
```bash
for f in tests/test_*.py; do python3 "$f"; done
```

### Generar el Dashboard de 9 Secciones, Reporte en Consola y Exportaciones CSV/JSON
```bash
python3 analytics_dashboard.py --cli
```

### Recalcular el Laboratorio de Experimentación Controlada (Escenarios Pareados)
```bash
python3 analytics_benchmark.py --scenarios 120 --force
```

### Iniciar el Servidor Web Local con Auto-Actualización en Tiempo Real
```bash
python3 analytics_dashboard.py --serve --port 8050
```

---

## 5. Limitaciones Conocidas y Transparencia Metodológica

1. **Exploitability de Nash en Árbol Completo Postflop**: El motor `poker_gto_engine.py` es un motor heurístico-GTO fundamentado en equidad de rangos, Pot Odds, MDF y tablas Push/Fold de Sklansky-Chubukov ($\le 10\text{ BB}$). No incluye un solver CFR+ de árbol completo postflop; por ello, el indicador `full_game_nash_exploitability_status` se reporta honestamente como `NO_DISPONIBLE_SIN_SOLVER_CFR` y solo se calcula la cota analítica en el subjuego Push/Fold ($\le 10\text{ BB}$).
2. **Visibilidad DOM en Partidas en Vivo**: Cuando el cliente web oculta las cartas del rival al hacer fold sin llegar al Showdown, la equidad y el EV de la decisión se evalúan exclusivamente con la información pública disponible en ese instante (cartas propias, board, bote, stack y perfil HUD histórico del oponente). Si las cartas propias o el bote no son legibles en un frame del DOM, la decisión se registra como `EVALUACION_NO_DISPONIBLE` sin sesgar las tasas de error.
