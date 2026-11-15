# REPORTE DE AUDITORÍA Y COMPORTAMIENTO (`REPORTE_COMPORTAMIENTO.md`)

> **Alcance:** Auditoría de solo lectura sobre copia de `logs/poker_stats.db` (`mode=ro`), `logs/poker_experience_memory.db` (`mode=ro`), `logs/bot_decisions.jsonl` y logs de consola `task-661.log` (`ses_20261115_023605_2d6732`) y `task-670.log` (`ses_20261115_033400_ceb4e3`). Muestra live: **66 manos / 130 decisiones en BD** (132 en JSONL). Benchmark sintético `v1.3`: **120 manos / 284 decisiones** (480 manos / 1,293 decisiones totales en `v1.0–v1.3`).

---

## 1. Resumen Ejecutivo

El bot **no está operando con su capa de IA ni con su motor de explotación reales**: en el **100% de las decisiones en vivo (130/130 en BD, 132/132 en JSONL)**, la llamada a Gemini devuelve `WAIT` o cae silenciosamente al respaldo heurístico (`gto_fallback_0ms`) sin registrar la causa ni la respuesta cruda. Además, **el 100% de las manos y decisiones en vivo (66/66, 130/130) se registran como `BTN`**, el **perfilador de rivales (`oponentes`) tiene `manos_jugadas=47` con `0` acciones registradas** (etiquetando erróneamente al rival como `NIT` en 22 decisiones), y la lectura de `to_call` / `modo_allin_or_fold` provoca **shoves `ALL_IN` ciegos ante apuesta rival** (`-9.86 BB` y `-8.78 BB`) y **`CHECK` con `to_call > 0` (10/130)**. **Veredicto honesto:** hoy juega como un bot heurístico *nit/abc* predecible (`VPIP 18.2%`, `PFR 9.1%`), muy lejos de un jugador profesional de 3-Max/Spin&Go; **confianza alta** en los bugs deterministas y huecos de observabilidad, **confianza baja/media** en el winrate real (`+48.48 BB/100`, IC95 `[-131.7, +228.6]`) por `n=66`.

---

## 2. Tabla Consolidada de Hallazgos

| ID | Tipo | Severidad | Evidencia (Consola + BD) | n | Causa raíz (`archivo:línea`) | Impacto estimado | Confianza |
|---|---|---|---|---:|---|---|---|
| **H-01** | Bug + Observabilidad | **CRÍTICA** | Consola: `task-661.log` (73/73) y `task-670.log` (58/58) muestran `🤖 Consultando a Gemini AI...` seguido de `🛡️ [RESPALDO DE SEGURIDAD 0ms]`. BD: `decision_source='gto_fallback_0ms'` en `130/130`. | 130 | [bot.py:2362-2371](file:///Users/mompra/Documents/config%20poker/bot.py#L2362-L2371), [poker_agent.py:261-309](file:///Users/mompra/Documents/config%20poker/poker_agent.py#L261-L309) | LLM 100% inoperante en vivo; cero trazabilidad del motivo | **Alta** |
| **H-02** | Bug determinista | **CRÍTICA** | BD: `analytics_hands` (`66/66`) y `analytics_decisions` (`130/130`) tienen `position='BTN'`. `poker_experience_memory.db` tiene `position='DESCONOCIDA'` en `6/6`. | 66 / 130 | [bot.py:2092-2120](file:///Users/mompra/Documents/config%20poker/bot.py#L2092-L2120), [bot.py:987-989](file:///Users/mompra/Documents/config%20poker/bot.py#L987-L989), [analytics_engine.py:2027-2033](file:///Users/mompra/Documents/config%20poker/analytics_engine.py#L2027-L2033) | Rangos preflop, EV y memoria pierden la posición real (`SB`/`BB`/`BTN`) | **Alta** |
| **H-03** | Bug determinista | **CRÍTICA** | BD `oponentes`: `41203111` (`doctorwave953`) tiene `manos_jugadas=47`, `preflop_calls=0`, `preflop_raises=0`, `postflop_bets=0`, `saw_flop_count=0`, `wtsd_count=2` (imposible). `22/130` decisiones lo etiquetan `NIT`. | 47 manos / 130 decs | [bot.py:2005-2029](file:///Users/mompra/Documents/config%20poker/bot.py#L2005-L2029), [opponents_profiler.py:372-430](file:///Users/mompra/Documents/config%20poker/opponents_profiler.py#L372-L430) | HUD ciego en 3-Max y clasificación falsa como `NIT` | **Alta** |
| **H-04** | Bug + Fuga estratégica | **ALTA** | BD: `e385b5946f` (`5♠ A♠` Turn, `-9.859 BB`) y `bd4d9a7e48` (`A♠ 8♠` Flop, `-8.780 BB`) tienen `legal_actions='["FOLD","ALL_IN"]'`, `to_call=0.0`, `action_taken='ALL_IN'`. | 2 | [bot.py:1662-1686](file:///Users/mompra/Documents/config%20poker/bot.py#L1662-L1686), [bot.py:751-765](file:///Users/mompra/Documents/config%20poker/bot.py#L751-L765) | Paga/empuja all-in sin pot odds con segunda pareja o kicker débil; `to_call=0` distorsiona EV | **Alta** |
| **H-05** | Bug determinista | **ALTA** | BD: `10/130` decisiones tienen `action_taken='CHECK'` con `to_call` entre `30` y `80` (ej. `888dd141e3`, `080499a3b9`) y `legal_actions='["FOLD","CHECK","RAISE"]'`. | 10 | [bot.py:570](file:///Users/mompra/Documents/config%20poker/bot.py#L570), [bot.py:778-785](file:///Users/mompra/Documents/config%20poker/bot.py#L778-L785) | `to_call` desincronizado del DOM infla `ev_lost_bb` falso (`1.45–3.65 BB`) | **Alta** |
| **H-06** | Bug determinista | **MEDIA** | BD: `78db377ed2` (`H9 S10`, `to_call=0.0`, `legal_actions='["FOLD","ALL_IN"]'`) ejecutó `FOLD` (`-1.232 BB`); `2/130` tuvieron `intended_action != action_taken` (`85db1774d8`, `ce9fa3e85e`). | 3 | [bot.py:767-776](file:///Users/mompra/Documents/config%20poker/bot.py#L767-L776), [bot.py:1535-1605](file:///Users/mompra/Documents/config%20poker/bot.py#L1535-L1605) | Falla al ejecutar `RAISE` -> cae a `CALL`; `to_call=0` ante all-in | **Alta** |
| **H-07** | Bug de consola / DOM | **MEDIA** | Consola `task-661.log:50,75`: `Mis Cartas: H4 S3 \| Mesa: CQ D7 SQ HJ S2 \| Bote: 30` (tablero viejo en mano nueva); `Ronda #4` antes de `Ronda #2`. `4` warnings `POST-CLIC`. | 5+ | [bot.py:2039-2165](file:///Users/mompra/Documents/config%20poker/bot.py#L2039-L2165), [bot.py:566-580](file:///Users/mompra/Documents/config%20poker/bot.py#L566-L580) | Contaminación visual de tablero entre manos y clics no reflejados en 700ms | **Alta** |
| **H-08** | Bug de integridad BD | **MEDIA** | `10/66` manos live sin decisiones (`5b9c210147` duplicada de `76223594b2` `DJ S8` 1s después); JSONL tiene `132` filas vs `130` en BD (`ses_2`: `58` vs `56`, `dec_33_58` fue `WAIT`). | 10 manos / 2 decs | [bot.py:2376-2418](file:///Users/mompra/Documents/config%20poker/bot.py#L2376-L2418), [bot.py:2445](file:///Users/mompra/Documents/config%20poker/bot.py#L2445) | Manos fantasma en `analytics_hands` y pérdida de cola al detener el proceso | **Alta** |
| **H-09** | Bug de analítica | **MEDIA** | BD: decisiones con `ev_lost_bb` entre `1.40` y `3.69 BB` (ej. `a16349dd57`, `19690d2cad`) tienen `error_description='Decisión sólida en EV'`. | 11/20 top | [analytics_engine.py:870-909](file:///Users/mompra/Documents/config%20poker/analytics_engine.py#L870-L909) | Oculta errores de `1.1–3.7 BB` como "sólidos" y distorsiona la memoria de experiencia (`ev_lost_bb=0.0` en `6/6`) | **Alta** |
| **H-10** | Fuga estratégica | **ALTA** | BD live: `VPIP 18.2%` (`12/66`), `PFR 9.1%` (`6/66`), `3-Bet 6.2%` (`2/32`). Overfolds preflop con `A4o`, `A4s`, `Q8s`, `K6s`, `A7o`, `JTo` (`1.4–3.8 BB` perdidos c/u). | 66 manos | [bot.py:1762-1890](file:///Users/mompra/Documents/config%20poker/bot.py#L1762-L1890) | Juego extremadamente *nit* en 3-Max/HU; cede ciegas y valor constantemente | **Media-Alta** (n=66) |
| **H-11** | Fuga estratégica | **MEDIA** | BD live: `12` raises postflop de `20` o `30` fichas (`1.0–1.5 BB`) en botes de `120–330` fichas (`10%–17%` del bote, min-bet/min-raise fijo). | 12/26 raises | [bot.py:1805-1815](file:///Users/mompra/Documents/config%20poker/bot.py#L1805-L1815) | Sizings no escalan con bote/textura; regala odds a proyectos rivales | **Alta** |
| **H-12** | Varianza | **BAJA** | Winrate live `+48.48 BB/100` (IC95 `[-131.7, +228.6]`) vs EV Winrate `+174.21 BB/100`; `W$SD 63.6%` (`7/11`). | 66 manos | N/A (varianza muestral) | Muestra insuficiente para concluir rentabilidad real (se requieren `>=5,000` manos) | **Alta** |

---

## 3. Detalle por Hallazgo

### H-01 — 100% Fallback de la capa LLM (`gto_fallback_0ms`)
- **Evidencia literal:** En `task-661.log:51-53` (y los 73 turnos analizados) y `task-670.log` (58/58):
  ```text
  🤖 Consultando a Gemini AI (1 consulta única, timeout máx 4.5s)...
  🛡️ [RESPALDO DE SEGURIDAD 0ms] Ejecutando acción defensiva rápida...
  ⚡ [DECISIÓN IA (0.81s)]: FOLD | Confianza: 0.88 | ⚡ [0ms GTO] ...
  ```
  En BD: `SELECT decision_source, COUNT(*) FROM analytics_decisions WHERE is_synthetic=0 GROUP BY decision_source` → `[('gto_fallback_0ms', 130)]`.
- **Causa raíz:** En [bot.py:2364-2371](file:///Users/mompra/Documents/config%20poker/bot.py#L2364-L2371), si `consultar_gemini_con_timeout` devuelve `None` o `decision.accion in ("WAIT", "START_GAME", "REPLAY")`, cae silenciosamente a `decidir_accion_rapida(dom_data)`. En [poker_agent.py:261-309](file:///Users/mompra/Documents/config%20poker/poker_agent.py#L261-L309), si `raw_text` viene vacío o si el modelo devuelve `WAIT`/`START_GAME`/`REPLAY` (lineas 274-275), **no se imprime ni se guarda el motivo**, y en `0.49s–1.67s` termina regresando `WAIT`.
- **Arreglo propuesto:** Loggear explícitamente el motivo del fallback (`empty_text`, `schema_error`, `model_wait`, `timeout`, `http_429/404`), guardar `raw_text` y `model_name` en `analytics_decisions`, y ajustar el prompt/esquema para prohibir `WAIT` cuando `es_mi_turno=True`.
- **Verificación:** Correr suite de evaluación con `USE_GENKIT=true` y verificar en BD que `decision_source='gemini'` > 90% y el resto tenga `fallback_reason` explícito.

### H-02 — Posición fija en `'BTN'` (100% en BD) y `'DESCONOCIDA'` en Memoria
- **Evidencia:** `positions (hands): [('BTN', 66)]`, `positions (decisions): [('BTN', 130)]`. En `poker_experience_memory.db`, filas `id=1..6` tienen `position='DESCONOCIDA'`.
- **Causa raíz:** En [bot.py:2092-2115](file:///Users/mompra/Documents/config%20poker/bot.py#L2092-L2115), si `playerStates` no trae `isDealer` o `hero_idx` es `None`, `tracker.position_name` permanece en `"DESCONOCIDA"` ([bot.py:164](file:///Users/mompra/Documents/config%20poker/bot.py#L164)). Luego [bot.py:989](file:///Users/mompra/Documents/config%20poker/bot.py#L989) y [analytics_engine.py:2033](file:///Users/mompra/Documents/config%20poker/analytics_engine.py#L2033) convierten `"DESCONOCIDA"` por defecto en `"BTN"`.
- **Arreglo propuesto:** Inferir posición también desde el DOM (botón `D` / ciegas posteadas `SB`/`BB` en las apuestas iniciales) y nunca colapsar `"DESCONOCIDA"` a `"BTN"` en analítica.
- **Verificación:** Comprobar distribución aproximada ~33% BTN / ~33% SB / ~33% BB en 3-Max (o 50% BTN / 50% BB en HU) en `analytics_hands`.

### H-03 — `OpponentProfiler` sin acciones y falso etiquetado `NIT`
- **Evidencia:** Tabla `oponentes`: `player_id='41203111'`, `nickname='doctorwave953'`, `manos_jugadas=47`, `preflop_calls=0`, `preflop_raises=0`, `postflop_bets=0`, `postflop_checks=0`, `saw_flop_count=0`, `wtsd_count=2`. En `analytics_decisions`: `22` filas con `opponent_profile='NIT'` y `108` con `'SIN DATOS'`.
- **Causa raíz:** `profiler.registrar_accion_rival(...)` en [bot.py:2005-2029](file:///Users/mompra/Documents/config%20poker/bot.py#L2005-L2029) solo se ejecuta por señales HTTP de sonido **cuando `len(tracker.active_opponents) == 1`** (inactivo en 3-Max) y nunca desde el decodificador WebSocket (`handle_websocket`). En cambio `incrementar_manos_rival` ([bot.py:2063](file:///Users/mompra/Documents/config%20poker/bot.py#L2063)) y `registrar_showdown` ([bot.py:2135](file:///Users/mompra/Documents/config%20poker/bot.py#L2135)) sí corren desde WS.
- **Arreglo propuesto:** Extraer acciones rivales (`BET`/`CALL`/`RAISE`/`CHECK`/`FOLD`) directamente de los eventos `playerStates` del WebSocket por `playerCode` y exigir `acciones_observadas > 0` antes de asignar un arquetipo distinto de `SIN DATOS`.
- **Verificación:** Test unitario con payloads WS de 3-Max verificando incremento de `preflop_raises`, `preflop_calls` y `saw_flop_count >= wtsd_count`.

### H-04 — Shoves `ALL_IN` ciegos ante All-In rival (`-9.86 BB` y `-8.78 BB`)
- **Evidencia:** Filas `e385b5946f14a090bec6746b` (`5♠ A♠` en Turn `3♦ 3♣ A♣ J♣`, `pot=620`, `to_call=0.0`, `legal_actions=["FOLD","ALL_IN"]`, `action_taken="ALL_IN"`, `ev_lost_bb=9.859`) y `bd4d9a7e48e48e434813081f` (`A♠ 8♠` en Flop `9♣ 4♠ 8♦`, `pot=256`, `to_call=0.0`, `legal_actions=["FOLD","ALL_IN"]`, `ev_lost_bb=8.780`).
- **Causa raíz:** Cuando el rival va all-in, los botones DOM son `["FOLD", "ALL_IN"]`, pero `a_igualar` se leyó como `0.0`. [bot.py:1662-1686](file:///Users/mompra/Documents/config%20poker/bot.py#L1662-L1686) activa `modo_allin_or_fold` y paga/empuja `ALL_IN` con cualquier `tier in ('S','A','B')` o cualquier pareja (`coincidencias_mesa`) sin evaluar pot odds ni fuerza relativa. Además, `analytics_engine` ve `to_call=0` y recomienda `CHECK` aunque no sea legal.
- **Arreglo propuesto:** Si `ALL_IN` está disponible sin `CALL`/`CHECK`, parsear el monto del botón `ALL_IN` / apuesta rival como `a_igualar` efectivo y aplicar umbral de equity vs pot odds antes de pagar.
- **Verificación:** Test con `legal_actions=["FOLD","ALL_IN"]` y segunda pareja ante shove de 2x bote → debe elegir `FOLD` si equity < pot odds.

### H-05 y H-06 — Desincronización de `to_call`, `CHECK` con `to_call > 0` y `intended != taken`
- **Evidencia:** `10/130` decisiones con `action_taken='CHECK'` y `to_call` de `30–80` teniendo `legal_actions='["FOLD","CHECK","RAISE"]'`. `1` caso `FOLD` con `to_call=0.0` (`78db377ed2`, `legal_actions='["FOLD","ALL_IN"]'`). `2/130` discrepancias `intended_action != action_taken`: `85db1774d8` (`RAISE` → `CALL`) y `ce9fa3e85e` (`CALL` → `CHECK`).
- **Causa raíz:** [bot.py:570](file:///Users/mompra/Documents/config%20poker/bot.py#L570) lee `aIgualar` de un elemento DOM que puede quedar con apuesta previa o del botón `Call` oculto mientras `CHECK` es legal; [bot.py:778-780](file:///Users/mompra/Documents/config%20poker/bot.py#L778-L780) bloquea `CHECK` si `a_igualar > 0`, y `ejecutar_accion` ([bot.py:1535-1605](file:///Users/mompra/Documents/config%20poker/bot.py#L1535-L1605)) cae a `CALL` si el clic de `RAISE` falla.
- **Arreglo propuesto:** Si `"CHECK"` está presente en botones visibles activos, forzar `a_igualar = 0.0` en la extracción DOM; registrar la causa exacta del fallback de clic en `analytics_decisions`.

### H-07 y H-08 — Estado de mesa residual, manos sin decisiones y pérdida al cerrar
- **Evidencia:** `task-661.log:50,75`: `Mis Cartas: H4 S3 | Mesa: CQ D7 SQ HJ S2 | Bote: 30` (las 5 comunitarias de la mano anterior seguían en DOM). En BD: `10/66` manos sin decisiones, incluyendo `76223594b2` (`DJ S8`, `02:45:00`, `pot=30`) y `5b9c210147` (`DJ S8`, `02:45:01`, `pot=168`). JSONL tiene `132` filas vs `130` en BD (`dec_33_58` fue `WAIT` al detener `task-670`).
- **Causa raíz:** `cartas_comunitarias` del DOM no se invalidan al detectar cambio de `my_cards` o `Ronda #1 (PREFLOP)`, y la cola asíncrona de `analytics_logger` pierde los últimos registros si no hace `flush` en `SIGINT/SIGTERM`.

### H-09 — Etiquetado erróneo `"Decisión sólida en EV"` con pérdidas de 1.40–3.69 BB
- **Evidencia:** 11 de las Top 20 decisiones por `ev_lost_bb` en BD (ej. `a16349dd57` `ev_lost_bb=3.689`, `19690d2cad` `2.740`, `383b57d547` `2.080`) tienen `error_description='Decisión sólida en EV'`. En `poker_experience_memory.db`, `6/6` filas tienen `ev_lost_bb=0.0`.
- **Causa raíz:** [analytics_engine.py:870-909](file:///Users/mompra/Documents/config%20poker/analytics_engine.py#L870-L909) excluye `is_strategic_error` si `is_exploitative_play=True` (activado incluso con perfil erróneo `NIT`) o si `ev_lost_bb <= 2.25` (`INACCURACY`), dejando `error_reasons=[]` y asignando `"Decisión sólida en EV"`.

---

## 4. Calidad Estratégica (Paso 3)

### A) Métricas Preflop y Postflop (`n=66` live, `n=120` benchmark `v1.3`) con IC 95% Wilson

| Métrica | Live (`v1.3`, n=66) | IC 95% Wilson (Live) | Benchmark `v1.3` (n=120) | Referencia sólida 3-Max / HU* | Diagnóstico |
|---|---:|---|---:|---|---|
| **VPIP** | **18.2%** (`12/66`) | `[10.7%, 29.1%]` | 26.7% (`32/120`) | ~45%–60% (BTN/SB 3-Max, mayor en HU) | **Fuga grave (muy tight / nit)** |
| **PFR** | **9.1%** (`6/66`) | `[4.2%, 18.4%]` | 19.2% (`23/120`) | ~35%–50% | **Fuga grave (muy pasivo preflop)** |
| **3-Bet** | **6.2%** (`2/32`) | `[1.7%, 20.1%]` | 10.7% (`3/28`) | ~12%–18% | Bajo (n limitado) |
| **CBet Flop** | **33.3%** (`1/3`) | `[6.1%, 79.2%]` | 31.8% (`7/22`) | ~55%–70% | Bajo (en benchmark n=22 ya es bajo) |
| **Fold to CBet** | **66.7%** (`4/6`) | `[30.0%, 90.3%]` | 50.0% (`10/20`) | ~38%–48% | Overfold (hipótesis, n=6) |
| **WTSD** | **50.0%** (`11/22`) | `[30.7%, 69.3%]` | 60.0% (`39/65`) | ~32%–40% | Alto (entra con rango fuerte o paga de más) |
| **W$SD** | **63.6%** (`7/11`) | `[35.4%, 84.8%]` | 61.5% (`24/39`) | ~52%–56% | Consistente con rango *nit* |
| **AF / AFq Postflop** | **2.20 / 55.0%** | — | — | AF 2.5–3.5 | Aceptable en muestra pequeña |
| **Winrate / EV BB/100** | **+48.48 / +174.21** | `[-131.7, +228.6]` | +89.35 / +28.58 | +5 a +15 BB/100 sostenible | **Varianza** (`n=66`; requiere `>=5,000` manos) |

*\*Nota: rangos de referencia estándar para 3-Max/Spin&Go (no verificados contra solver externo en este entorno).*

### B) Agrupación de las Top 20 decisiones por `ev_lost_bb` (Live)
1. **Legalidad / lectura `to_call` (10 casos, hasta `9.86 BB`):**
   - `ALL_IN` ciego ante shove con `to_call=0.0` (`#1`: `5♠ A♠` `9.86 BB`; `#2`: `A♠ 8♠` `8.78 BB`).
   - `CHECK` registrado con `to_call=30..80` (`#6, #9, #13, #14, #17, #18`: `1.45–3.65 BB`).
   - `CALL` con `to_call=0` (`#10`: `2.08 BB`, `#15`: `1.58 BB`) y `FOLD` con `to_call=0` (`#20`: `1.23 BB`).
2. **Regla fija / overfold preflop del motor 0ms (7 casos, `1.40–3.83 BB`):**
   - Foldea `A4o` (`3.83 BB`), `A4s` (`3.69 BB`), `Q8s` (`2.82 BB`), `K6s` (`2.74 BB`), `A3o` (`1.95 BB`), `A7o` (`1.95 BB`), `JTo` (`1.40 BB`).
3. **Sizing y evaluación postflop (1 caso en Top 20 + patrón de sizings):**
   - `#16` (`5♦ 4♠` en `K♠ 10♣ A♠ 4♥ 10♥` hace `RAISE` en river vs `FOLD`, `-1.52 BB`).
   - En `12/26` raises/bets postflop, apuesta el mínimo (`20` o `30` fichas = `10%–17%` del bote), sin adaptar al SPR ni textura.
4. **Memoria de experiencia:** Se escribe (`6` filas en `poker_experience_memory.db`), pero todas con `opp_archetype='SIN DATOS'`, `position='DESCONOCIDA'` y `ev_lost_bb=0.0`, sin evidencia de mejora aún (`n=6`).

---

## 5. Diagnóstico de la Capa de IA (Paso 5) y Huecos de Observabilidad

1. **Rotación de modelos y cooldowns (`poker_agent.py:238-289`):** Usa un `dict` en memoria (`model_cooldown_until`) con tiempos fijos (`86400s` para 404, `3600s` para cuota diaria, `45s` para 429/503) y `HttpRetryOptions(attempts=1)`. **No tiene** backoff exponencial con jitter, circuit breaker por fallos consecutivos, ni distingue entre respuesta vacía (`raw_text == ""`), error de esquema Pydantic, timeout o alucinación de `WAIT`/`START_GAME`.
2. **Validación de salida (`poker_agent.py:264-275`):** Si `PokerDecision.model_validate_json` falla, cae al `except` genérico como "modelo no disponible" en lugar de reintentar con instrucción correctiva o registrar el fallo de esquema. Si el modelo devuelve `START_GAME`/`REPLAY`, lo convierte en `WAIT`, disparando el fallback 0ms en `bot.py:2368`.
3. **Trazabilidad (hueco crítico):** Hoy **es imposible reconstruir** para una `decision_uid` dada: el prompt exacto, el modelo final usado, los reintentos, los tokens, la respuesta cruda JSON del LLM ni el motivo del fallback.
4. **Evaluación offline:** No existe un dataset fijo de regresión ni evaluadores automáticos de prompts/modelos antes de salir a vivo (solo `analytics_benchmark.py` para el motor determinista).
5. **Huecos adicionales de observabilidad:**
   - Consola `stdout` sin timestamps ISO ni `hand_uid`/`decision_uid`.
   - `position` colapsada a `BTN` y `a_igualar` no contrastado con el botón `ALL_IN`.
   - `flush` incompleto de la cola de BD al recibir `SIGINT`/`SIGTERM`.

---

## 6. Plan de Mejoras Priorizado

### Rápidas (< 1 día)
1. **Instrumentar motivo real de fallback LLM + respuesta cruda** en consola y BD (objetivo: reducir tasa de fallback silencioso de `100%` a `< 5%` con causa explícita).
2. **Corregir `modo_allin_or_fold` y parseo de `a_igualar` en `ALL_IN` / `CHECK`** ([bot.py:1662, 570](file:///Users/mompra/Documents/config%20poker/bot.py#L1662)) (objetivo: `0` `CHECK` con `to_call > 0` y `0` shoves ciegos sin pot odds).
3. **Corregir `error_description` en `analytics_engine.py:870-909`** para `ev_lost_bb > 1.10` (objetivo: 100% de errores >1.1 BB descritos y propagados con `ev_lost_bb` real a `PokerExperienceMemory`).

### Medias (1–3 días)
4. **Arreglar detección de posición (`BTN`/`SB`/`BB`)** combinando WS + DOM (`D` y ciegas) y **alimentar `OpponentProfiler` desde WS** (objetivo: `0` manos con posición `DESCONOCIDA` y `preflop_calls + preflop_raises > 0` coherente con `manos_jugadas`).
5. **Rebalancear rangos preflop 3-Max/HU y sizings postflop** del motor determinista (objetivo: elevar `VPIP` de `18.2%` a `40%–52%` y `PFR` de `9.1%` a `30%–40%`, y eliminar min-bets de `10%` del bote).
6. **Limpiar `cartas_comunitarias` al iniciar mano nueva** y hacer `flush` síncrono de `analytics_logger` en shutdown (objetivo: `0` tableros residuales y `100%` paridad JSONL ↔ BD).

### Estructurales (Parte B — Integración Genkit)
7. **Aislar capa de IA en `ai_layer.py` / `genkit_layer.py`** con `USE_GENKIT=true|false`, trazabilidad por `decision_uid`, política explícita de reintentos/circuit-breaker y fallback determinista garantizado.
8. **Suite de evaluación offline (`python -m evals.run`)** sobre casos reales extraídos de la BD (check gratis, facing all-in, ≤10 BB, board pair) como puerta obligatoria antes de cambiar prompts o modelos.

---

## 7. Experimentos A/B Recomendados

1. **Exp A/B #1 — Motor Preflop/Sizing `v1.4` vs `v1.3` (Benchmark pareado + Live):**
   - *Muestra:* `300` escenarios pareados en benchmark (`n=300` por brazo, `alpha=0.05`, `power=0.80`) antes de vivo; luego `>= 1,000` manos live para métricas de proceso (`VPIP`, `PFR`, `ev_lost_bb/decisión`) y `>= 5,000` manos para winrate.
   - *Criterio:* Reducir `ev_lost_bb` medio por decisión en `>= 30%` sin aumentar tasa de acciones ilegales (`0%`).
2. **Exp A/B #2 — Capa IA `Genkit + Gemini` vs `Motor Determinista` (`HYBRID` / Shadow Eval):**
   - *Muestra:* `500` decisiones en evaluación offline reproducible (semilla fija) + `500` manos live en modo sombra/híbrido.
   - *Métrica de decisión:* Tasa de legalidad `100%`, latencia `p95 <= 2,500 ms`, tasa de fallback `< 5%`, y `ev_lost_bb` medio <= motor determinista.

---

## 8. Addendum de Validación en Vivo: Resolución Causa Raíz de H-01

Tras implementar la capa aislada de IA (`ai_layer.py`), se diagnosticó experimentalmente por qué el bot caía al 100% en `gto_fallback_0ms`:
1. **Restricción de la API de Google GenAI:** La biblioteca oficial `google-genai` rechaza cualquier llamada con `HttpOptions(timeout < 10000ms)` arrojando `400 INVALID_ARGUMENT: Manually set deadline 4s is too short. Minimum allowed deadline is 10s.` Al configurar `AI_HTTP_TIMEOUT_MS = 3500`, el 100% de las peticiones HTTP eran abortadas en la capa de red del SDK de Google antes de salir.
2. **Disponibilidad de Modelos:**
   - `gemini-3.5-flash`: Sobrecarga en servidores de Google que resulta en `504 DEADLINE_EXCEEDED` o tiempos > 11 s.
   - `gemini-3.8-flash`: Devuelve `403 PERMISSION_DENIED` con las credenciales estándar del proyecto.
   - `gemini-3.1-flash-lite`: Operativo, disponible y validado.
3. **Solución Implementada:**
   - `config.AI_HTTP_TIMEOUT_MS` elevado a `12000` (cumple con la restricción de Google GenAI; el watchdog del turno en `bot.py` mediante `future.result(timeout=4.5)` mantiene el control estricto de la mesa).
   - `GEMINI_MODELS_PRIORITY` fijado con `gemini-3.1-flash-lite`.
   - `ai_layer.py` clasifica explícitamente `DEADLINE_TOO_SHORT` y `PERMISSION_DENIED`.
4. **Resultado de Prueba en Vivo (n=20 llamadas con prompt real y datos de mesa):**
   - **Éxito LLM:** 19/20 (95.0%).
   - **Fallbacks:** 1/20 (5.0%, por timeout de red puntual).
   - **Esquema Pydantic (`PokerDecision`):** 100% válido en respuestas recibidas.
   - **Latencia:** p50 = 4.27 s.

---
**Auditoría y Despliegue de Capa de IA completados.**
