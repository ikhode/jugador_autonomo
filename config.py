"""Configuración global del bot de póker."""
import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

# Credenciales y URLs
GAME_URL = os.getenv("GAME_URL", "https://games.codere.mx/poker/web/WebLauncher.html")
LOGIN_URL = os.getenv("LOGIN_URL", "https://www.codere.mx")
GAME_USER = os.getenv("GAME_USER", "")
GAME_PASSWORD = os.getenv("GAME_PASSWORD", "")

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-3.5-flash-lite")
GEMINI_MODELS_PRIORITY = list(dict.fromkeys([
    GEMINI_MODEL,
    "gemini-3.5-flash-lite",
    "gemini-3.8-flash",
    "gemini-3.1-flash-lite",
    "gemini-3.5-flash",
]))
ACTIVE_MODEL = os.getenv("ACTIVE_MODEL", GEMINI_MODEL)
AVAILABLE_MODELS = list(dict.fromkeys([*GEMINI_MODELS_PRIORITY, "gemma-4b-local", "gto-heuristic"]))
GEMMA_LOCAL_MODEL_PATH = os.getenv("GEMMA_LOCAL_MODEL_PATH", "")

# Cloudflare Workers AI
CF_ACCOUNT_ID = os.getenv("CF_ACCOUNT_ID", "ac96116c5db82e232d04042b84c09399")
CF_API_TOKEN = os.getenv("CF_API_TOKEN", "cfoat_Q_VSvNcUMZDjl6ZFHIteMvjmDR0azL4gGFL_SwLFHcE.XL25iEiUQyUGJg4hnZzz2N-O2QOcBwnGSPQ__Vhovzk")

MODO = os.getenv("MODO", "gemini").strip().lower()  # "determinista" | "hibrido" | "gemini"
HYBRID_EV_DIFF_THRESHOLD_BB = float(os.getenv("HYBRID_EV_DIFF_THRESHOLD_BB", "0.5"))

# Analítica honesta (Fase 5)
MIN_LIVE_HANDS_FOR_IMPROVEMENT = int(os.getenv("MIN_LIVE_HANDS_FOR_IMPROVEMENT", "5000"))
MIN_PAIRED_BENCHMARK_HANDS = int(os.getenv("MIN_PAIRED_BENCHMARK_HANDS", "30"))
POWER_TARGET = float(os.getenv("POWER_TARGET", "0.80"))
ALPHA_SIGNIFICANCE = float(os.getenv("ALPHA_SIGNIFICANCE", "0.05"))
DEFAULT_TARGET_DELTA_BB100 = float(os.getenv("DEFAULT_TARGET_DELTA_BB100", "10.0"))


def obtener_modo_activo() -> str:
    """Normaliza MODO y ACTIVE_MODEL al esquema único: determinista | hibrido | gemini | local."""
    if str(ACTIVE_MODEL or "").strip().lower() in ("gto-heuristic", "determinista", "heuristico"):
        return "determinista"
    m = str(MODO or "gemini").strip().lower()
    if m in ("heuristico", "determinista", "gto"):
        return "determinista"
    if m in ("hibrido", "hybrid"):
        return "hibrido"
    if m in ("local", "local_hybrid", "local-models", "forest", "xgboost"):
        return "local"
    return "gemini"
USAR_CAPTURA = os.getenv("USAR_CAPTURA", "false").lower() == "true"
HEADLESS = os.getenv("HEADLESS", "false").lower() == "true"
HOLD_BROWSER_OPEN = os.getenv("HOLD_BROWSER_OPEN", "true").lower() == "true"
PLAY_MONEY_ONLY = os.getenv("PLAY_MONEY_ONLY", "true").lower() == "true"
CDP_PORT = int(os.getenv("CDP_PORT", "9222"))
SLOW_MO = int(os.getenv("SLOW_MO", "100"))

USER_DATA_DIR = Path("browser_profile")

# Configuración de Modalidad y Mesa para Live HUD Multijugador
POKER_MODE = os.getenv("POKER_MODE", "Cash Game Texas Hold'em")
MAX_TABLE_SEATS = int(os.getenv("MAX_TABLE_SEATS", "6"))
HERO_NICKNAME = os.getenv("HERO_NICKNAME", GAME_USER)

# Configuración del Sistema de Analíticas y Versionado Experimental
BOT_VERSION = os.getenv("BOT_VERSION", "1.3")
BOT_VERSION_DESC = os.getenv(
    "BOT_VERSION_DESC",
    "Optimización de decisiones postflop (Distinción Pareja en Mano vs Mesa, Blockers y Sizings GTO)",
)
BIG_BLIND_DEFAULT = float(os.getenv("BIG_BLIND_DEFAULT", "20.0"))
INITIAL_BANKROLL = float(os.getenv("INITIAL_BANKROLL", "1500.0"))
ANALYTICS_ENABLED = os.getenv("ANALYTICS_ENABLED", "true").lower() == "true"

# Parámetros del Motor GTO y Decisor por EV (Fase 2)
PUSH_FOLD_MAX_BB = float(os.getenv("PUSH_FOLD_MAX_BB", "10.0"))
MONTE_CARLO_ITERATIONS = int(os.getenv("MONTE_CARLO_ITERATIONS", "250"))
SIZING_CBET_PCTS = (0.33, 0.66, 1.0)
OPEN_RAISE_BB = float(os.getenv("OPEN_RAISE_BB", "2.2"))
THREE_BET_MULTIPLIER = float(os.getenv("THREE_BET_MULTIPLIER", "3.0"))

# Selectores CSS y de elementos en la mesa de póker
SELECTORS = {
    "btn_acceder": "xpath=//*[@id=\"root\"]/nav/div/div[3]/button, #btn-login, button.btn-login, a.btn-login, .login-btn",
    "username_input": "#floatUsername, input[name*='user'], input[name*='email'], input#username",
    "password_input": "#floatPw, input[type='password']",
    "login_submit": "button.iniciar, button[type='submit'], button.submit-login",
    "poker_nav_link": "a[href*='poker'], button:has-text('Poker')",
    "btn_open_poker": "#pokerPageOpen, button:has-text('Abrir Poker'), .open-poker-btn",
    "select_game_card": ".game-category-tile.cash-game, .cash-game, xpath=//*[@id=\"Poker\"]/div/div[1]/div[2]/div[1]/div[1]/div/div[1]/div/div[3]/div[2]/div[2], .classic-twister, .twister-game-card, .game-card, [data-game*='cash'], [data-game*='twister']",
    "start_match_btn": ".filter-play-button, button:has-text('Jugar Poker'), button:has-text('Jugar'), button:has-text('Play'), .play-btn",

    # Mesa de juego (nombres estándar y alias con prefijo btn_)
    "actions_area": ".actions-area, .player-actions",
    "fold": "#FOLD, button:has-text('Fold'), button:has-text('Retirarse'), .action-fold, div#FOLD, div:has-text('Fold')",
    "btn_fold": "#FOLD, button:has-text('Fold'), button:has-text('Retirarse'), .action-fold, div#FOLD, div:has-text('Fold')",
    "check": "#CHECK, button:has-text('Check'), button:has-text('Pasar'), .action-check, div#CHECK, div:has-text('Check'), div:has-text('Pasar')",
    "btn_check": "#CHECK, button:has-text('Check'), button:has-text('Pasar'), .action-check, div#CHECK, div:has-text('Check'), div:has-text('Pasar')",
    "call": "#CALL, button:has-text('Call'), button:has-text('Igualar'), button:has-text('Pagar'), .action-call, div#CALL, div:has-text('Call'), div:has-text('Igualar'), div:has-text('Pagar'), [id*='CALL']",
    "btn_call": "#CALL, button:has-text('Call'), button:has-text('Igualar'), button:has-text('Pagar'), .action-call, div#CALL, div:has-text('Call'), div:has-text('Igualar'), div:has-text('Pagar'), [id*='CALL']",
    "raise": "#RAISE, #BET, .action-raise, .action-bet, div#RAISE, div#BET, button:has-text('Raise'), button:has-text('Bet'), button:has-text('Subir'), button:has-text('Apostar')",
    "btn_raise": "#RAISE, #BET, .action-raise, .action-bet, div#RAISE, div#BET, button:has-text('Raise'), button:has-text('Bet'), button:has-text('Subir'), button:has-text('Apostar')",
    "allin": "#ALLIN, .actions-area button:has-text('All-in'), .actions-area button:has-text('Allin'), .actions-area [role='button']:has-text('All-in'), .actions-area [role='button']:has-text('Allin'), .actions-area .action-allin, .actions-area [id*='ALLIN']",
    "btn_allin": "#ALLIN, .actions-area button:has-text('All-in'), .actions-area button:has-text('Allin'), .actions-area [role='button']:has-text('All-in'), .actions-area [role='button']:has-text('Allin'), .actions-area .action-allin, .actions-area [id*='ALLIN']",
    "raise_input": "input.betting-input, input[type='range'], input.raise-input",
    "btn_raise_input": "input.betting-input, input[type='range'], input.raise-input",
    "start": "button:has-text('Iniciar'), button:has-text('Start'), .btn-start",
    "btn_start": "button:has-text('Iniciar'), button:has-text('Start'), .btn-start",
    "replay": "#general-popup-button-action-REPLAY, #general-popup-button-action-PLAY_AGAIN, [role='dialog'] button:has-text('Jugar de nuevo'), [role='dialog'] [role='button']:has-text('Jugar de nuevo'), #general-popup button:has-text('Jugar de nuevo'), button:has-text('Jugar de nuevo'), button:has-text('Replay'), .btn-replay",
    "btn_replay": "#general-popup-button-action-REPLAY, #general-popup-button-action-PLAY_AGAIN, [role='dialog'] button:has-text('Jugar de nuevo'), [role='dialog'] [role='button']:has-text('Jugar de nuevo'), #general-popup button:has-text('Jugar de nuevo'), button:has-text('Jugar de nuevo'), button:has-text('Replay'), .btn-replay",
    "btn_sitout": "#general-popup-button-action-OK, button:has-text('Aceptar Sit-out')",
    "game_over": ".game-over-modal, [data-result]",
}

# Resiliencia y logging estructurado (Fase 6)
MAX_CONSECUTIVE_ERRORS = int(os.getenv("MAX_CONSECUTIVE_ERRORS", "10"))
ERROR_BACKOFF_BASE_SEC = float(os.getenv("ERROR_BACKOFF_BASE_SEC", "0.5"))
ERROR_BACKOFF_MAX_SEC = float(os.getenv("ERROR_BACKOFF_MAX_SEC", "16.0"))
HAND_WATCHDOG_TIMEOUT_SEC = float(os.getenv("HAND_WATCHDOG_TIMEOUT_SEC", "120.0"))
JSON_LOG_PATH = os.getenv(
    "JSON_LOG_PATH",
    str(Path(__file__).resolve().parent / "logs" / "bot_decisions.jsonl"),
)

# Capa de IA / Genkit (Parte B)
USE_GENKIT = os.getenv("USE_GENKIT", "false").lower() == "true"
GENKIT_DEV_UI = os.getenv("GENKIT_DEV_UI", "false").lower() == "true"
PROMPT_VERSION = os.getenv("PROMPT_VERSION", "v1")
AI_DECISION_TIMEOUT_SEC = float(os.getenv("AI_DECISION_TIMEOUT_SEC", "13.0"))
AI_HTTP_TIMEOUT_MS = int(os.getenv("AI_HTTP_TIMEOUT_MS", "15000"))
AI_MAX_SCHEMA_RETRIES = int(os.getenv("AI_MAX_SCHEMA_RETRIES", "1"))
AI_RETRY_ATTEMPTS = int(os.getenv("AI_RETRY_ATTEMPTS", "2"))
AI_RETRY_BASE_MS = int(os.getenv("AI_RETRY_BASE_MS", "150"))
AI_CIRCUIT_FAILURE_THRESHOLD = int(os.getenv("AI_CIRCUIT_FAILURE_THRESHOLD", "3"))
AI_CIRCUIT_OPEN_SEC = float(os.getenv("AI_CIRCUIT_OPEN_SEC", "60.0"))
AI_COOLDOWN_404_SEC = float(os.getenv("AI_COOLDOWN_404_SEC", "86400.0"))
AI_COOLDOWN_DAILY_SEC = float(os.getenv("AI_COOLDOWN_DAILY_SEC", "3600.0"))
AI_COOLDOWN_RATE_SEC = float(os.getenv("AI_COOLDOWN_RATE_SEC", "45.0"))
AI_LATENCY_OVERHEAD_BUDGET_MS = float(os.getenv("AI_LATENCY_OVERHEAD_BUDGET_MS", "150.0"))
AI_EVAL_DEFAULT_SEED = int(os.getenv("AI_EVAL_DEFAULT_SEED", "20261114"))
