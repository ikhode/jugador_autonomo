# Changelog

## [Unreleased] - Parte B (Capa de IA, Trazabilidad y Evaluación Offline)

### Agregado
- `GENKIT_VIABILIDAD.md` y `REPORTE_COMPORTAMIENTO.md` (auditoría de solo lectura y verificación de Genkit Python `0.13.0`).
- `ai_layer.py`: módulo aislado con flujo instrumentado `decidir_accion`, esquemas Pydantic (`PokerTableInput`, `PokerDecision`, `StepTrace`, `DecisionTrace`), `ModelPolicy` (circuit breaker, backoff exponencial con jitter, clasificación `404`/`PerDay`/`429-503`/`TIMEOUT`), validación de legalidad y fallback determinista garantizado.
- `prompts/v1.txt` y `prompts/__init__.py`: prompts versionados con hash SHA-256 (`prompt_hash`).
- Migración idempotente `v2_ai_traceability` en `analytics_db.py` y `db_models.py` añadiendo `prompt_version`, `prompt_hash`, `model_used`, `retry_count`, `raw_response`, `fallback_reason`, `trace_json` a `analytics_decisions`.
- `evals/` (`dataset.py`, `evaluators.py`, `run.py`): dataset reproducible por semilla y 5 evaluadores (`legalidad`, `esquema`, `gto_ev_alignment`, `coherencia`, `latencia`) con CLI `python3 -m evals.run`.
- `tests/test_ai_layer_genkit.py`: pruebas unitarias, de degradación, latencia, trazabilidad en BD y evaluación reproducible.

### Modificado
- `poker_agent.py`: convertido en adaptador compatible hacia atrás sobre `ai_layer.AILayer`.
- `bot.py` y `analytics_logger.py`: propagación de `decision_uid` y `ai_trace` + registro explícito de `fallback_reason`.
- `config.py`: actualizado `AI_HTTP_TIMEOUT_MS = 15000` (la API de Google GenAI rechaza HTTP deadlines < 10 s con error 400); fijado modelo prioritario a `gemini-3.1-flash-lite`.
- `ai_layer.py`: añadido soporte para clasificar `DEADLINE_TOO_SHORT` (400), `PERMISSION_DENIED` (403) y `TIMEOUT` (504); corregidos imports de `concurrent.futures` y `logging`; añadido `"BET"` y `"APOSTAR"` a `ALL_ACTIONS` y mapeo a legalidad `RAISE` (`raise_as_bet`); extracción tolerante de JSON envuelto en bloques de código o prosa; guardado el mensaje de error completo en `StepTrace.detail`.
- `.env`: fijado `GEMINI_MODEL=gemini-3.1-flash-lite`.
- `config.py`, `requirements.txt`, `README.md`, `DOCUMENTACION_ANALITICAS.md`.

### Validaciones en Vivo
- Validación live con prompt real (20 llamadas consecutivas): **95% de éxito LLM (19/20)** respondiendo con esquema válido (`PokerDecision`) y latencia adecuada (`p50 = 4.27 s`), confirmando la resolución definitiva de la causa de 100% fallback de `H-01`.

### Riesgos conocidos y siguientes pasos
- Los hallazgos operativos de `REPORTE_COMPORTAMIENTO.md` (`H-02` posición fija en `BTN`, `H-03` `OpponentProfiler` sin eventos WS, `H-04`/`H-05` parseo de `a_igualar` en `ALL_IN`/`CHECK`, `H-10` rangos preflop *nit*) quedan listos para corregirse en el siguiente ciclo y validarse con `python3 -m evals.run` y benchmark pareado.
