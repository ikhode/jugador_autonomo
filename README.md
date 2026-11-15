# ♠️ Bot Autónomo de Póquer con IA Gemini & Playwright

Este proyecto implementa un jugador autónomo de póquer web impulsado por el motor GTO en [poker_gto_engine.py](file:///Users/mompra/Documents/config%20poker/poker_gto_engine.py), **Gemini AI** (prioridad de modelos centralizada en `config.GEMINI_MODELS_PRIORITY`, por defecto `gemini-2.5-flash`) y **Playwright**.

Puede conectarse a **cualquier URL** (tanto local como `http://localhost` o cualquier sitio de poker web en Internet), iniciar sesión automáticamente con credenciales configurables, analizar la mesa visualmente mediante visión por computadora con IA y tomar decisiones óptimas en tiempo real (`FOLD`, `CHECK`, `CALL`, `RAISE`).

---

## 📁 Estructura del Proyecto

- `bot.py`: Bucle principal de automatización con Playwright (Login, navegación, capturas de pantalla y clics en botones).
- `poker_agent.py`: Agente de Inteligencia Artificial que consulta la API de Gemini mediante el SDK `google-genai` con respuestas estructuradas en JSON.
- `config.py`: Carga la configuración de URLs, credenciales y selectores web desde el archivo `.env`.
- `.env`: Variables de entorno (API key de Gemini, URLs, credenciales).
- `requirements.txt`: Dependencias requeridas.

---

## 📋 Requisitos Previos

1. **Python 3.9+** instalado en tu sistema.
2. **Clave API de Gemini** (Obtenla en [Google AI Studio](https://aistudio.google.com/)).

---

## 🚀 Instalación Paso a Paso

1. Instala las dependencias necesarias de Python:
   ```bash
   pip install -r requirements.txt
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
