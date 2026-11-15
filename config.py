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
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
ACTIVE_MODEL = os.getenv("ACTIVE_MODEL", "gemini-2.5-flash")  # "gemini-2.5-flash", "gemma-4b-local", "gto-heuristic"
GEMMA_LOCAL_MODEL_PATH = os.getenv("GEMMA_LOCAL_MODEL_PATH", r"C:\Users\SATAP\.android\models\gemma-4-4b\gemma-4-E4B_q4_0-it.gguf")

MODO = os.getenv("MODO", "gemini")  # "gemini" o "heuristico"
USAR_CAPTURA = os.getenv("USAR_CAPTURA", "false").lower() == "true"
HEADLESS = os.getenv("HEADLESS", "false").lower() == "true"
SLOW_MO = int(os.getenv("SLOW_MO", "100"))

USER_DATA_DIR = Path("browser_profile")

# Configuración de Modalidad y Mesa para Live HUD Multijugador
POKER_MODE = os.getenv("POKER_MODE", "Twister 3-Max")
MAX_TABLE_SEATS = int(os.getenv("MAX_TABLE_SEATS", "3"))
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

# Selectores CSS y de elementos en la mesa de póker
SELECTORS = {
    "btn_acceder": "xpath=//*[@id=\"root\"]/nav/div/div[3]/button, #btn-login, button.btn-login, a.btn-login, .login-btn",
    "username_input": "#floatUsername, input[name*='user'], input[name*='email'], input#username",
    "password_input": "#floatPw, input[type='password']",
    "login_submit": "button.iniciar, button[type='submit'], button.submit-login",
    "poker_nav_link": "a[href*='poker'], button:has-text('Poker')",
    "btn_open_poker": "#pokerPageOpen, button:has-text('Abrir Poker'), .open-poker-btn",
    "select_game_card": "xpath=//*[@id=\"Poker\"]/div/div[1]/div[2]/div[1]/div[1]/div/div[1]/div/div[3]/div[2]/div[2], .classic-twister, .twister-game-card, .game-card, [data-game*='twister']",
    "start_match_btn": ".filter-play-button, button:has-text('Jugar Poker'), button:has-text('Jugar'), button:has-text('Play'), .play-btn",

    # Mesa de juego (nombres estándar y alias con prefijo btn_)
    "actions_area": ".actions-area, .player-actions",
    "fold": "#FOLD, button:has-text('Fold'), button:has-text('Retirarse'), .action-fold, div#FOLD, div:has-text('Fold')",
    "btn_fold": "#FOLD, button:has-text('Fold'), button:has-text('Retirarse'), .action-fold, div#FOLD, div:has-text('Fold')",
    "check": "#CHECK, button:has-text('Check'), button:has-text('Pasar'), .action-check, div#CHECK, div:has-text('Check'), div:has-text('Pasar')",
    "btn_check": "#CHECK, button:has-text('Check'), button:has-text('Pasar'), .action-check, div#CHECK, div:has-text('Check'), div:has-text('Pasar')",
    "call": "#CALL, button:has-text('Call'), button:has-text('Igualar'), .action-call, div#CALL, div:has-text('Call'), div:has-text('Igualar')",
    "btn_call": "#CALL, button:has-text('Call'), button:has-text('Igualar'), .action-call, div#CALL, div:has-text('Call'), div:has-text('Igualar')",
    "raise": "#RAISE, #BET, .action-raise, .action-bet, div#RAISE, div#BET, button:has-text('Raise'), button:has-text('Bet'), button:has-text('Subir'), button:has-text('Apostar')",
    "btn_raise": "#RAISE, #BET, .action-raise, .action-bet, div#RAISE, div#BET, button:has-text('Raise'), button:has-text('Bet'), button:has-text('Subir'), button:has-text('Apostar')",
    "allin": "#ALLIN, button:has-text('All-in'), div:has-text('All-in'), .action-allin",
    "btn_allin": "#ALLIN, button:has-text('All-in'), div:has-text('All-in'), .action-allin",
    "raise_input": "input.betting-input, input[type='range'], input.raise-input",
    "btn_raise_input": "input.betting-input, input[type='range'], input.raise-input",
    "start": "button:has-text('Iniciar'), button:has-text('Start'), .btn-start",
    "btn_start": "button:has-text('Iniciar'), button:has-text('Start'), .btn-start",
    "replay": "#general-popup-button-action-REPLAY, #general-popup-button-action-PLAY_AGAIN, button:has-text('Jugar de nuevo'), div:has-text('Jugar de nuevo'), span:has-text('Jugar de nuevo'), button:has-text('Replay'), .btn-replay",
    "btn_replay": "#general-popup-button-action-REPLAY, #general-popup-button-action-PLAY_AGAIN, button:has-text('Jugar de nuevo'), div:has-text('Jugar de nuevo'), span:has-text('Jugar de nuevo'), button:has-text('Replay'), .btn-replay",
    "game_over": ".game-over-modal, [data-result]",
}
