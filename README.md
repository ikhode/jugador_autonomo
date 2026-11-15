# ♠️ Bot Autónomo de Póquer con IA Gemini & Playwright

Este proyecto implementa un jugador autónomo de póquer web impulsado por el motor GTO en [poker_gto_engine.py](file:///Users/mompra/Documents/config%20poker/poker_gto_engine.py), **Gemini AI** (prioridad de modelos centralizada en `config.GEMINI_MODELS_PRIORITY`, por defecto `gemini-2.5-flash`) y **Playwright**.

Puede conectarse a **cualquier URL** (tanto local como `http://localhost` o cualquier sitio de poker web en Internet), iniciar sesión automáticamente con credenciales configurables, analizar la mesa visualmente mediante visión por computadora con IA y tomar decisiones óptimas en tiempo real (`FOLD`, `CHECK`, `CALL`, `RAISE`).

---

## 📁 Estructura del Proyecto

- `bot.py`: Bucle principal de automatización con Playwright (login, navegación, extracción de DOM primaria con captura fallback, validación única `validar_accion`, resiliencia con `ControladorResiliencia`, logging JSONL estructurado y ejecución de clics).
- `poker_gto_engine.py`: Evaluador de manos de 5/7 cartas, proyectos y outs, analizador de textura/blockers, tablas Nash Push/Fold (`<= 10 BB`) y decisor por EV con equidad Monte Carlo/exacta reproducible por semilla.
- `poker_agent.py`: Agente de IA (`google-genai`) con prioridad de modelos centralizada en `config.GEMINI_MODELS_PRIORITY` y soporte de modo `hibrido` (`resolver_decision_hibrida`).
- `opponents_profiler.py`: Perfilador de rivales 3-Max en SQLite (`WAL`, banderas por mano, prior bayesiano, intervalos de Wilson y mínimo de 30 manos antes de etiquetar).
- `poker_experience_memory.py`: Memoria de experiencias pasadas filtrada por `similarity_key`.
- `analytics_*.py`: Sistema de analíticas, benchmark pareado y dashboard HTML.
- `config.py`: Configuración centralizada, variables de entorno (`MODO`, `PLAY_MONEY_ONLY`, umbrales de potencia y resiliencia) y selectores DOM.
- `requirements.txt` y `requirements-dev.txt`: Dependencias de ejecución y desarrollo (`pytest`).

---

## 📋 Requisitos Previos

1. **Python 3.9+** instalado en tu sistema.
2. **Clave API de Gemini** (Obtenla en [Google AI Studio](https://aistudio.google.com/)).

---

## 🚀 Instalación Paso a Paso

1. Instala las dependencias necesarias de Python (o `requirements-dev.txt` para ejecutar tests):
   ```bash
   pip install -r requirements.txt
   pip install -r requirements-dev.txt
   ```

2. Instala los navegadores necesarios para Playwright:
   ```bash
   playwright install chromium
   ```

3. Coloca tu **Gemini API Key** en el archivo `.env`:
   ```env
   GEMINI_API_KEY=tu_api_key_aqui
   ```

---

## ⚙️ Configuración para Cualquier URL

Puedes cambiar las variables en `.env` para apuntar a **cualquier dirección web** de poker:

```env
LOGIN_URL=http://localhost
GAME_URL=http://localhost/juego
GAME_USER=tu_usuario_aqui
GAME_PASSWORD=tu_password_aqui
HEADLESS=false
SLOW_MO=100
MODO=gemini            # determinista | hibrido | gemini
GEMINI_MODEL=gemini-2.5-flash
PLAY_MONEY_ONLY=true
GEMMA_LOCAL_MODEL_PATH=
```

- Si el juego está en una URL pública (ejemplo: `https://mipokeronline.com/login`), simplemente actualiza `LOGIN_URL` y `GAME_URL`.

---

## 🏃 Ejecución del Bot

Inicia el jugador autónomo ejecutando:

```bash
python bot.py
```
