# Auditoría Técnica del Repositorio (Fase 0)

> Documento generado tras la lectura integral de todos los módulos, pruebas y documentación del repositorio en el commit `f5225a6`. No se ha modificado código productivo ni de pruebas en esta fase.

---

## 1. Mapa de Módulos y Dependencias

```text
config.py (.env)
 ├── bot.py (Bucle principal Playwright, WS/HTTP interceptors, GameTracker, ejecutar_accion, decidir_accion_rapida)
 │    ├── poker_agent.py (PokerAgent, PokerDecision, Gemini API, Gemma local GGUF)
 │    ├── poker_gto_engine.py (parsear_carta, clasificar_mano_preflop, evaluar_combinacion_mano_y_mesa,
 │    │                        calcular_pot_odds_y_sizing, analizar_textura_mesa_y_blockers, generar_frecuencia_mixta_gto)
 │    ├── opponents_profiler.py (OpponentProfiler, db_models.Oponente -> logs/poker_stats.db)
 │    ├── poker_experience_memory.py [IMPORTADO PERO NO USADO] (PokerExperienceMemory -> logs/poker_experience_memory.db)
 │    └── analytics_logger.py (PokerAnalyticsLogger, cola asíncrona + RLock)
 │         ├── analytics_db.py (AnalyticsStorage -> logs/poker_stats.db WAL)
 │         │    └── db_models.py (SQLAlchemy ORM: 9 tablas)
 │         ├── analytics_engine.py (evaluar_decision_estrategica, estimar_equity_mano, PokerAnalyticsEngine)
 │         ├── analytics_diagnostics.py (percentiles P50/P95/P99, RSS, generar_diagnosticos_inteligentes)
 │         └── analytics_dashboard.py (generar_dashboard_html, servidor HTTP :8050)
 │              └── analytics_benchmark.py (escenarios pareados sintéticos)
 ├── live_table_tracker.py [DESCONECTADO DE bot.py en f5225a6] (LiveTableMonitor, logs/live_table_state.json)
 ├── agente.py [DEPRECADO - Reexporta PokerAgent, PokerDecision]
 └── tests/
      ├── test_analytics.py (unittest)
      ├── test_multiplayer_hud.py (unittest)
      └── test_gto_fixes.py (unittest)
```

---

## 2. Flujo Completo de una Decisión

1. **Detección de turno / evento ([bot.py](file:///Users/mompra/Documents/config%20poker/bot.py#L1215-L1265)):**
   - `obtener_target_mesa(page)` busca el frame con `.my-player`, `.community-cards`, `.actions-area`, etc.
   - `esperar_sincronizacion_turno(...)` ejecuta `JS_VERIFICAR_TURNO_O_MODAL` (apoyado por `MutationObserver` en `JS_INICIALIZAR_MUTATION_OBSERVER`).
   - Adicionalmente, `on_response` detecta `my_turn.mp3` y activa `tracker.http_turn_signal = True`.
2. **Extracción del DOM ([bot.py](file:///Users/mompra/Documents/config%20poker/bot.py#L158-L255), [bot.py](file:///Users/mompra/Documents/config%20poker/bot.py#L1249-L1257)):**
   - `target_mesa.evaluate(JS_EXTRAER_ESTADO_DOM)` extrae `mis_cartas`, `cartas_comunitarias`, `bote`, `a_igualar`, `mi_stack`, `stack_rival`, `fuerza_mano`, `monto_apuesta_input`, `opciones_disponibles`, `botones_visibles`, `modales_visibles`, `es_mi_turno`, `jugadores_mesa`.
   - Si `tracker.http_turn_signal` está activo y no hay `botones_visibles`, espera hasta 450 ms y re-evalúa `dom_data = target_mesa.evaluate(JS_EXTRAER_ESTADO_DOM) or {}`.
3. **Enriquecimiento de contexto ([bot.py](file:///Users/mompra/Documents/config%20poker/bot.py#L1266-L1290)):**
   - Inyecta en `dom_data`: `historial_mano`, `perfil_rival` (único rival), `gto_preflop`, `gto_math`, `gto_textura`, `gto_frecuencia`, `gto_combinacion`.
   - **Fallo detectado:** `live_monitor` fue desconectado de `bot.py`, por lo que `perfiles_oponentes` (3-Max) no se genera ni inyecta en `dom_data`.
   - **Fallo detectado:** `memoria_errores` nunca se inyecta porque `PokerExperienceMemory` no se instancia ni consulta.
4. **Decisión (Motor rápido / LLM) ([bot.py](file:///Users/mompra/Documents/config%20poker/bot.py#L1300-L1330), [poker_agent.py](file:///Users/mompra/Documents/config%20poker/poker_agent.py#L48-L260)):**
   - Toma captura JPEG (`owner_page.screenshot(...)`) y llama a `consultar_gemini_con_timeout(agent, screenshot_bytes, dom_data=dom_data, timeout_seg=4.5)` (en `poker_agent.py`, si hay DOM completo `mis_cartas` + `botones`, omite enviar la imagen).
   - Si falla, vence el timeout o devuelve `WAIT`/`START_GAME`/`REPLAY`, cae a `decidir_accion_rapida(dom_data)`.
5. **Validación y ejecución en DOM ([bot.py](file:///Users/mompra/Documents/config%20poker/bot.py#L729-L872)):**
   - No existe una función única de validación de legalidad (`validar_accion`).
   - `ejecutar_accion(target_mesa, decision, dom_data)` intenta evitar `FOLD` cuando hay `CHECK` gratis, rellena el input de subida **solo si `monto > 60`**, y hace fallback ad-hoc entre botones si el selector primario falla.
6. **Registro y analíticas ([bot.py](file:///Users/mompra/Documents/config%20poker/bot.py#L1332-L1350), [analytics_logger.py](file:///Users/mompra/Documents/config%20poker/analytics_logger.py#L256-L294)):**
   - `analytics_logger.registrar_decision(...)` encola de forma asíncrona la decisión (`intended_action` y `action_taken`).
   - Al terminar la mano (`my_player_won.mp3`, cambio a `roundNo == 1` o modal), `tracker.finalizar_mano_actual(...)` llama a `analytics_logger.finalizar_mano(...)`. No registra errores en `PokerExperienceMemory`.

---

## 3. Discrepancias entre Documentación y Código Real

| Fuente | Qué afirma la documentación | Realidad en el código |
| :--- | :--- | :--- |
| [README.md](file:///Users/mompra/Documents/config%20poker/README.md#L3) | Usa el modelo `gemini-3.8-flash` | [config.py](file:///Users/mompra/Documents/config%20poker/config.py#L15-L16) usa `gemini-2.5-flash`; [poker_agent.py](file:///Users/mompra/Documents/config%20poker/poker_agent.py#L192-L198) lista `gemini-2.5-flash`, `gemini-2.0-flash`, `gemini-1.5-flash`, `gemini-2.5-flash-lite`; [analytics_dashboard.py](file:///Users/mompra/Documents/config%20poker/analytics_dashboard.py#L2641-L2642) aún expone `gemini-3.1-flash-lite` |
| [README.md](file:///Users/mompra/Documents/config%20poker/README.md#L11-L15) | Lista solo 4 archivos (`bot.py`, `poker_agent.py`, `config.py`, `.env`) | Faltan `poker_gto_engine.py`, `live_table_tracker.py`, `opponents_profiler.py`, `poker_experience_memory.py`, `analytics_*.py`, `db_models.py` |
| [README.md](file:///Users/mompra/Documents/config%20poker/README.md#L56) | Documenta `INTERVALO_ANALISIS_SEG=3` en `.env` | No existe en `config.py` ni en `bot.py` (el bucle es reactivo por eventos) |
| [DOCUMENTACION_ANALITICAS.md](file:///Users/mompra/Documents/config%20poker/DOCUMENTACION_ANALITICAS.md#L73) | Indica `python3 tests/test_analytics.py` | Omite `tests/test_multiplayer_hud.py` y `tests/test_gto_fixes.py`. `pytest` no está en `requirements.txt` ni instalado en el entorno actual |
| [DOCUMENTACION_ANALITICAS.md](file:///Users/mompra/Documents/config%20poker/DOCUMENTACION_ANALITICAS.md#L88) | Dashboard en puerto 8050 | `/api/bot/model` ya existe en `analytics_dashboard.py`, pero `bot.py` nunca lee `bot_paused` ni `bot_force_rejoin` |
| [poker_agent.py](file:///Users/mompra/Documents/config%20poker/poker_agent.py#L14) | Comentario: *"reduce latencia de Gemini de 32s a ~0.5s"* | Solo aplica cuando hay captura; cuando hay DOM completo `screenshot_bytes = None` |

---

## 4. Código Muerto, Configuraciones Ignoradas e Imports sin Uso

- **[agente.py](file:///Users/mompra/Documents/config%20poker/agente.py):** Módulo deprecado que reexporta `PokerAgent` y `PokerDecision`. Ningún archivo del repositorio lo importa.
- **[live_table_tracker.py](file:///Users/mompra/Documents/config%20poker/live_table_tracker.py):** Desconectado de `bot.py` en `f5225a6`; actualmente solo es importado por `tests/test_multiplayer_hud.py`.
- **[bot.py](file:///Users/mompra/Documents/config%20poker/bot.py#L5-L13):**
  - `import hashlib` (línea 5) y `from pathlib import Path` (línea 7) sin uso.
  - `from poker_experience_memory import PokerExperienceMemory` (línea 13) importado y nunca instanciado/llamado.
- **[poker_gto_engine.py](file:///Users/mompra/Documents/config%20poker/poker_gto_engine.py#L8-L13):** `RANGOS_PREFLOP` está definido pero no es usado por `clasificar_mano_preflop` ni por ningún otro módulo.
- **[config.py](file:///Users/mompra/Documents/config%20poker/config.py#L15-L20):**
  - `MODO` (línea 19) y `USAR_CAPTURA` (línea 20) nunca son leídos en `bot.py` ni `poker_agent.py`.
  - `GEMMA_LOCAL_MODEL_PATH` (línea 17) apunta a una ruta fija de Windows (`C:\Users\SATAP\...`) en entorno macOS.
  - `SELECTORS["game_over"]` (línea 72) nunca se utiliza.
- **[analytics_dashboard.py](file:///Users/mompra/Documents/config%20poker/analytics_dashboard.py#L2638-L2712):** Los flags `bot.bot_paused` / `bot.bot_force_rejoin` que escribe `do_POST` nunca se comprueban en el bucle principal de `bot.py`.

---

## 5. Verificación de Hallazgos de la Fase 1

| # | Hallazgo propuesto | Estado | Evidencia exacta en el código |
| :-: | :--- | :---: | :--- |
| 1 | **Pérdida de contexto del DOM al re-evaluar** (`bot.py`) | **PARCIALMENTE RESUELTO / VIGILAR** | En `f5225a6`, el bloque `if not mis_cartas` fue eliminado y la re-evaluación por `botones_visibles` ([bot.py:1257](file:///Users/mompra/Documents/config%20poker/bot.py#L1257)) ahora ocurre *antes* de inyectar `historial_mano`, `perfil_rival` y `gto_*` ([bot.py:1266-1290](file:///Users/mompra/Documents/config%20poker/bot.py#L1266-L1290)). Aun así, `dom_data = target_mesa.evaluate(...) or {}` sigue reemplazando el dict en lugar de usar `dom_data.update(... or {})`, lo cual conviene blindar con test. |
| 2 | **`PokerExperienceMemory` desconectada** (`bot.py`, `poker_experience_memory.py`) | **CONFIRMADO** | Importada en [bot.py:13](file:///Users/mompra/Documents/config%20poker/bot.py#L13) y leída en [poker_agent.py:139](file:///Users/mompra/Documents/config%20poker/poker_agent.py#L139) (`dom_data.get('memoria_errores', [])`), pero nunca instanciada ni llamada en `bot.py`. Además, [poker_experience_memory.py:88-91](file:///Users/mompra/Documents/config%20poker/poker_experience_memory.py#L88-L91) filtra solo por `hand_tier` OR `opp_archetype` ignorando `board_texture` y `similarity_key`. |
| 3 | **Perfiles multijugador (`perfiles_oponentes`) no llegan al prompt** (`bot.py`, `poker_agent.py`) | **CONFIRMADO** | `live_monitor` fue desconectado de `bot.py` (ya ni siquiera rellena `dom_data["perfiles_oponentes"]`), y [poker_agent.py:93-100](file:///Users/mompra/Documents/config%20poker/poker_agent.py#L93-L100) solo formatea `dom_data.get('perfil_rival', {})`. |
| 4 | **Límite artificial `monto > 60` en `ejecutar_accion`** (`bot.py`) | **CONFIRMADO** | [bot.py:829](file:///Users/mompra/Documents/config%20poker/bot.py#L829): `if monto > 0 and monto != monto_actual_dom and monto > 60:` omite rellenar el input para subidas `<= 60` fichas. |
| 5 | **Ausencia de validación de legalidad única (`validar_accion`) y `.upper()` en botones** (`bot.py`) | **CONFIRMADO** | No existe `validar_accion`; `ejecutar_accion` y `decidir_accion_rapida` hacen comprobaciones dispersas. En [bot.py:898-900](file:///Users/mompra/Documents/config%20poker/bot.py#L898-L900) `botones` se consulta sin `.upper()`. |
| 6 | **Selectores frágiles (`div:has-text(...)`) y falta de verificación post-clic** (`config.py`, `bot.py`) | **CONFIRMADO** | [config.py:63-64, 69-70](file:///Users/mompra/Documents/config%20poker/config.py#L63-L70) contienen `div:has-text('All-in')` y `div:has-text('Jugar de nuevo')` / `span:has-text('Jugar de nuevo')`. `intentar_click` ([bot.py:262-313](file:///Users/mompra/Documents/config%20poker/bot.py#L262-L313)) usa `click(force=True)` sin verificar que la acción se reflejó en la mesa. |
| + | **`PLAY_MONEY_ONLY=true` por defecto** (`config.py`, `bot.py`) | **CONFIRMADO (FALTANTE)** | No existe actualmente ninguna variable `PLAY_MONEY_ONLY` ni bloqueo frente a mesas de dinero real en `config.py` ni en `bot.py`. |

---

## 6. Estado de las Pruebas en `f5225a6`

- `python3 tests/test_analytics.py`: **12/12 OK** (`0.838s`).
- `python3 tests/test_multiplayer_hud.py`: **9/9 OK** (`0.203s`).
- `python3 tests/test_gto_fixes.py`: **ERROR DE SINTAXIS al importar `bot.py`** — el commit `f5225a6` introdujo `except KeyboardInterrupt:` en [bot.py:1392](file:///Users/mompra/Documents/config%20poker/bot.py#L1392) sin un bloque `try:` correspondiente envolviendo el bucle `while True:`, lo que provoca `SyntaxError: invalid syntax` e impide ejecutar `bot.py` o `tests/test_gto_fixes.py`.

---

*Fin de la Fase 0. En espera de confirmación para iniciar la Fase 1.*
