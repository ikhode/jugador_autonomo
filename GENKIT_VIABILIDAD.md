# Informe de Viabilidad: Genkit en Python (`GENKIT_VIABILIDAD.md`)

> **Verificación previa (Parte B, Paso 0)** sobre documentación oficial (`genkit.dev`, `firebase/genkit`), PyPI y prueba `pip install --dry-run` en el entorno actual (`macOS arm64`, `Python 3.14.7`, `google-genai 3.3.0`, `pydantic 2.14.3`, `playwright 1.65.2`).

---

## 1. Estado actual del SDK de Genkit para Python

| Aspecto | Estado verificado | Detalle |
|---|---|---|
| **SDK Python** | Disponible (**Beta**, `genkit==0.13.0`) | Pre-1.0; API evoluciona más rápido que Node.js/Go. |
| **Versión mínima Python** | `>=3.10` (probado `3.10–3.14`) | **Compatible** con el entorno actual (`Python 3.14.7`). |
| **Plugin Google GenAI** | `genkit-plugin-google-genai==0.9.0` (`GoogleAI`) | Expone modelos Gemini (`gemini-2.5-flash`, `gemini-2.0-flash`, `gemini-2.5-flash-lite`, etc.). |
| **Flujos (`@ai.flow()`)** | **Disponible** | Diseño `async`-first; soporta pasos instrumentados. |
| **Salida estructurada Pydantic** | **Disponible** (`output_schema`) | Compatible con Pydantic v2 (`2.14.3`). |
| **Tracing & Dev UI** | **Disponible** (`opentelemetry-api`, `genkit start`) | Dev UI requiere CLI externa de Node.js (`genkit-cli`); las trazas locales pueden emitirse también vía OpenTelemetry/JSON. |
| **Middleware (`genkit-plugin-middleware==0.9.0`)** | **Disponible** (`BaseMiddleware`, `Retry`, `Fallback`) | Hooks `wrap_model`, `wrap_generate`, `wrap_tool`. |
| **Evaluadores (`genkit-plugin-evaluators==0.9.0`)** | **Disponible en `0.13.x`** (`@ai.define_evaluator`, `ai.evaluate`) | Nota oficial: en `1.0+` los evaluadores se removieron temporalmente; debe fijarse `genkit>=0.13.0,<0.14.0` si se usan. |

---

## 2. Riesgos y fricciones con el stack actual

1. **Downgrade de `google-genai`:** `pip install --dry-run genkit-plugin-google-genai` exige `google-genai==2.34.0`, degradando la versión instalada (`3.3.0`), y añade ~30 dependencias pesadas (`google-cloud-aiplatform`, `google-cloud-bigquery`, `grpcio`, `uvicorn`, `uvloop`).
2. **Modelo de ejecución `async` vs `ThreadPoolExecutor`:** Los flujos y `ai.generate()` de Genkit Python son `async`, mientras que `bot.py` invoca `consultar_gemini_con_timeout(...)` desde hilos síncronos con un presupuesto estricto de `4.5s`.
3. **Madurez Beta (`0.13.0`):** Cualquier falla de importación, cambio de API o latencia de arranque de Genkit no debe bloquear ni degradar el loop de juego en vivo ni la ejecución de `validar_accion(...)` / motor GTO.

---

## 3. Plan de integración ajustado (Alternativa mínima segura)

Para cumplir todos los objetivos de robustez, trazabilidad y evaluabilidad sin acoplar el bot a una dependencia Beta ni degradar `google-genai`:

1. **Módulo aislado `ai_layer.py` (`decidir(dom_data, image_bytes, ...)`):**
   - Implementa el **flow de decisión tipado con Pydantic** (`PokerTableInput`, `PokerDecision`, `StepTrace`, `DecisionTrace`), instrumentando cada paso (`build_prompt`, `model_call`, `schema_validation`, `legality_validation`, `final_result`) con `prompt_version` + `prompt_hash`, `decision_uid`, `hand_uid`, latencia por paso, tokens, `raw_text` y `fallback_reason`.
   - **Backend dual limpio (`USE_GENKIT=true|false`):**
     - Cuando `USE_GENKIT=true` y `genkit` está disponible en el entorno, envuelve el flujo con `@ai.flow()` y trazado Genkit/OTel (con timeout duro y degradación automática si falla).
     - En caso contrario (`USE_GENKIT=false` o `genkit` no instalado), ejecuta exactamente el mismo flujo instrumentado, validaciones y política de reintentos/circuit-breaker sobre `google-genai` directo, **sin modificar `google-genai==3.3.0`** y garantizando fallback inmediato al motor determinista (`decidir_accion_rapida`).
2. **Política explícita de modelos (`ModelPolicy`):**
   - Lista ordenada configurable, reintentos con backoff exponencial + jitter para `429`/`503`, distinción entre `404` (inexistente), cuota diaria (`PerDay`), límite por minuto (`429`) y respuesta vacía/esquema inválido, más **circuit breaker** por modelo con reapertura gradual (`half-open`).
3. **Trazabilidad en BD (`analytics_decisions`):**
   - Migración idempotente que añade `prompt_version`, `prompt_hash`, `model_used`, `retry_count`, `raw_response`, `fallback_reason`, `trace_json` enlazados por `decision_uid`.
4. **Evaluación offline (`evals/run.py`):**
   - Dataset reproducible extraído de `poker_stats.db` (casos críticos: check gratis, facing all-in, `<=10 BB`, board pair vs pocket pair, proyectos) + 5 evaluadores (`legalidad`, `esquema`, `alineación_gto_o_ev`, `coherencia_eval_accion`, `latencia`), ejecutable con `python3 -m evals.run --prompt v1` sin requerir servidor externo.
