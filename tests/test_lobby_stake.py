import pytest
import time
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

import config
from bot import (
    aumentar_apuesta_lobby,
    verificar_lobby_apuesta_visible,
    clicar_boton_jugar_poker,
    aumentar_apuesta_y_proceder_a_jugar,
    resolver_modal_comprar_fichas,
    _ultimo_incremento_lobby_ts,
)
import bot


def test_lobby_stake_config_and_selectors():
    assert hasattr(config, "LOBBY_STAKE_INCREMENTS")
    assert config.LOBBY_STAKE_INCREMENTS in (0, 1)
    assert "lobby_stake_plus" in config.SELECTORS
    assert "lobby_stake_slider" in config.SELECTORS
    assert "+" in config.SELECTORS["lobby_stake_plus"]


def test_verificar_lobby_apuesta_visible_true():
    mock_target = MagicMock()
    mock_target.evaluate.return_value = True
    assert verificar_lobby_apuesta_visible(mock_target) is True


def test_verificar_lobby_apuesta_visible_false():
    mock_target = MagicMock()
    mock_target.evaluate.return_value = False
    assert verificar_lobby_apuesta_visible(mock_target) is False


def test_aumentar_apuesta_lobby_zero_increment():
    mock_target = MagicMock()
    res = aumentar_apuesta_lobby(mock_target, incrementos=0)
    assert res is True
    mock_target.evaluate.assert_not_called()


def test_aumentar_apuesta_lobby_js_success():
    bot._ultimo_incremento_lobby_ts = 0.0
    mock_target = MagicMock()
    mock_target.evaluate.return_value = {
        "ok": True,
        "metodo": "plus_above_play",
        "antes": "€0.01 / €0.02",
        "despues": "€0.02 / €0.04",
    }
    mock_target.frames = []

    res = aumentar_apuesta_lobby(mock_target, incrementos=1)
    assert res is True
    assert mock_target.evaluate.call_count >= 1
    assert bot._ultimo_incremento_lobby_ts > 0


def test_aumentar_apuesta_lobby_debounce():
    bot._ultimo_incremento_lobby_ts = time.time()
    mock_target = MagicMock()

    # Como acabamos de ejecutarlo hace milisegundos, el debounce debe retornar True sin evaluar
    res = aumentar_apuesta_lobby(mock_target, incrementos=1)
    assert res is True
    mock_target.evaluate.assert_not_called()


def test_aumentar_apuesta_lobby_fallback_intentar_click():
    bot._ultimo_incremento_lobby_ts = 0.0
    mock_target = MagicMock()
    mock_target.evaluate.return_value = {"ok": False}
    mock_target.frames = []

    with patch("bot.intentar_click", return_value=True) as mock_click:
        res = aumentar_apuesta_lobby(mock_target, incrementos=1)
        assert res is True
        mock_click.assert_called_once()


def test_clicar_boton_jugar_poker_intentar_click():
    mock_target = MagicMock()
    mock_target.frames = []
    with patch("bot.intentar_click", return_value=True) as mock_click:
        res = clicar_boton_jugar_poker(mock_target)
        assert res is True
        mock_click.assert_called_once()


def test_clicar_boton_jugar_poker_universal_text_fallback():
    mock_target = MagicMock()
    mock_target.frames = []
    with patch("bot.intentar_click", return_value=False), \
         patch("bot.clicar_texto_modal_universal", return_value=True) as mock_text:
        res = clicar_boton_jugar_poker(mock_target)
        assert res is True
        mock_text.assert_called_once()


def test_aumentar_apuesta_y_proceder_a_jugar():
    mock_target = MagicMock()
    with patch("bot.aumentar_apuesta_lobby", return_value=True) as mock_aumentar, \
         patch("bot.clicar_boton_jugar_poker", return_value=True) as mock_jugar:
        res = aumentar_apuesta_y_proceder_a_jugar(mock_target, incrementos=1)
        assert res is True
        mock_aumentar.assert_called_once_with(mock_target, incrementos=1)
        mock_jugar.assert_called_once_with(mock_target)


def test_aumentar_apuesta_y_proceder_a_jugar_zero_increments():
    mock_target = MagicMock()
    with patch("bot.aumentar_apuesta_lobby", return_value=True) as mock_aumentar, \
         patch("bot.clicar_boton_jugar_poker", return_value=True) as mock_jugar:
        res = aumentar_apuesta_y_proceder_a_jugar(mock_target, incrementos=0)
        assert res is True
        mock_aumentar.assert_not_called()
        mock_jugar.assert_called_once_with(mock_target)


def test_resolver_modal_comprar_fichas_js_success():
    mock_target = MagicMock()
    mock_target.evaluate.return_value = {
        "ok": True,
        "metodo": "dom_comprar_btn",
        "texto": "Comprar €0.40 ($8.17)",
    }
    mock_target.frames = []

    res = resolver_modal_comprar_fichas(mock_target)
    assert res is True
    mock_target.evaluate.assert_called_once()


def test_resolver_modal_comprar_fichas_fallback_universal_text():
    mock_target = MagicMock()
    mock_target.evaluate.return_value = {"ok": False}
    mock_target.frames = []

    with patch("bot.clicar_texto_modal_universal", return_value=True) as mock_text:
        res = resolver_modal_comprar_fichas(mock_target)
        assert res is True
        mock_text.assert_called_once()


