"""
Módulo de Extracción de Estado DOM y Scripts JS Inyectados (DOM Parser).
Encapsula la lectura resistente del DOM, MutationObservers y la selección de marcos de juego activos.
"""
import re
import time
from typing import Any, Dict, List, Optional
import config
from opponents_profiler import sanitizar_nickname, es_nickname_invalido
from poker_gto_engine import (
    analizar_textura_mesa_y_blockers,
    calcular_pot_odds_y_sizing,
    clasificar_mano_preflop,
    evaluar_combinacion_mano_y_mesa,
    evaluar_push_fold_nash,
    formatear_cartas_canonicas,
    generar_frecuencia_mixta_gto,
)

JS_INICIALIZAR_MUTATION_OBSERVER = r"""() => {
    if (window.__POKER_OBSERVER_INIT__) return;
    window.__POKER_OBSERVER_INIT__ = true;
    window.__POKER_EVENT_CHANGED__ = false;

    const getSnapshot = () => {
        const miTimer = document.querySelector('.my-player .text-countdown, .my-player .timeout-wrapper, .my-player .countdown-text');
        const accionesArea = document.querySelector('.actions-area');
        const hayBotones = accionesArea && Array.from(accionesArea.querySelectorAll('.action-button, #FOLD, #CHECK, #CALL, #BET, #RAISE'))
            .some(el => el.offsetWidth > 0 || el.offsetHeight > 0 || window.getComputedStyle(el).display !== 'none');
        const misCartas = Array.from(document.querySelectorAll('.my-player .card-image-backup')).map(c => (c.querySelector('.card-rank')?.innerText || '') + (c.querySelector('.card-suit')?.innerText || '')).join('');
        const comunitarias = Array.from(document.querySelectorAll('.community-cards .card-image-backup')).map(c => (c.querySelector('.card-rank')?.innerText || '') + (c.querySelector('.card-suit')?.innerText || '')).join('');
        const bote = (document.querySelector('#main-pot, .total-pot-amount')?.innerText || '').trim();

        return `${miTimer || hayBotones}|${misCartas}|${comunitarias}|${bote}`;
    };

    let lastSnapshot = getSnapshot();

    const observer = new MutationObserver(() => {
        const currentSnapshot = getSnapshot();
        if (currentSnapshot !== lastSnapshot) {
            lastSnapshot = currentSnapshot;
            window.__POKER_EVENT_CHANGED__ = true;
        }
    });

    if (document.body) {
        observer.observe(document.body, { childList: true, subtree: true, attributes: true, characterData: true });
    }
}"""

JS_VERIFICAR_TURNO_O_MODAL = r"""() => {
    const isChanged = window.__POKER_EVENT_CHANGED__;
    if (isChanged) window.__POKER_EVENT_CHANGED__ = false;

    const miTurno = !!document.querySelector('.my-player .text-countdown, .my-player .timeout-wrapper, .my-player .countdown-text');
    if (miTurno) return true;

    const accionesArea = document.querySelector('.actions-area');
    const hayBotonAccion = accionesArea && Array.from(accionesArea.querySelectorAll('.action-button, #FOLD, #CHECK, #CALL, #BET, #RAISE'))
        .some(el => el.offsetWidth > 0 || el.offsetHeight > 0 || window.getComputedStyle(el).display !== 'none');
    if (hayBotonAccion) return true;

    const buscarPorTexto = (selector, texto) => {
        const elementos = document.querySelectorAll(selector);
        return Array.from(elementos).find(el => el.textContent.includes(texto));
    };

    const hayModal = document.querySelector('.filter-play-button') ||
                     buscarPorTexto('button', 'Jugar de nuevo') ||
                     buscarPorTexto('div', 'Jugar de nuevo') ||
                     buscarPorTexto('span', 'Aceptar') ||
                     (document.body && (document.body.textContent.includes('podrías hacer check sin coste alguno') || document.body.textContent.includes('¿Seguro que quieres foldear?')));

    return !!(isChanged || hayModal);
}"""

JS_EXTRAER_ESTADO_DOM = r"""() => {
    try {
        const getCardStr = (c) => {
            if (!c) return '';
            let rank = (c.querySelector('.card-rank')?.innerText || c.querySelector('.card-rank')?.textContent || '').trim();
            let suit = (c.querySelector('.card-suit')?.innerText || c.querySelector('.card-suit')?.textContent || '').trim();

            if (!rank || !suit) {
                const img = c.querySelector('img.card-image, img');
                const src = img ? (img.getAttribute('src') || '') : '';
                const fileMatch = src.match(/\/([a-z0-9]+)\.svg$/i);
                if (fileMatch) {
                    const code = fileMatch[1].toLowerCase();
                    if (code.length >= 2) {
                        const suitChar = code[0];
                        const rankStr = code.substring(1).toUpperCase();
                        const suitMap = { 'c': '♣', 'd': '♦', 'h': '♥', 's': '♠' };
                        if (suitMap[suitChar]) {
                            suit = suitMap[suitChar];
                            rank = rankStr === '10' ? '10' : rankStr;
                        }
                    }
                }
            }
            if (!rank) return '';
            if (rank === '10') rank = '10';
            return (rank + (suit || '')).trim();
        };

        const rawHeroCards = Array.from(document.querySelectorAll('.my-player .card:not(.pt-visibility-hidden), .cards-holder-hero .card:not(.pt-visibility-hidden), .my-player .card-image-backup'));
        const uniqueHeroCards = rawHeroCards.filter((el, idx, self) => self.indexOf(el) === idx);
        const misCartas = Array.from(new Set(uniqueHeroCards.map(getCardStr).filter(Boolean)));

        const rawBoardCards = Array.from(document.querySelectorAll('.community-cards .cardset-community .card:not(.pt-visibility-hidden), .community-cards .card:not(.pt-visibility-hidden)'));
        const uniqueBoardCards = rawBoardCards.filter((el, idx, self) => self.indexOf(el) === idx);
        const comunitarias = Array.from(new Set(uniqueBoardCards.map(getCardStr).filter(Boolean)));

        const bote = (document.querySelector('.total-pot .total-pot-amount, #main-pot:not(.pt-visibility-hidden), .total-pot-amount')?.innerText || '').trim();
        const mainPotText = (document.querySelector('.main-pot-container #main-pot:not(.pt-visibility-hidden) .amount, #main-pot:not(.pt-visibility-hidden)')?.innerText || '').trim();

        const blindsText = (document.querySelector('.blinds-info, .table-blinds, .level-blinds, .blinds')?.innerText || '').trim();
        const blindsMatch = blindsText.match(/(\d+(?:[.,]\d+)?)\s*\/\s*(\d+(?:[.,]\d+)?)/);
        const bigBlindDom = blindsMatch ? blindsMatch[2].replace(',', '') : '';
        const fuerzaMano = (document.querySelector('.hand-strength-text')?.innerText || '').trim();
        const montoInput = document.querySelector('.betting-input, .raise-input')?.value || '0';

        const timerTexto = (document.querySelector('.my-player .countdown-text, .my-player .text-countdown')?.innerText || '').trim();
        const miTimerActivo = !!document.querySelector('.my-player .text-countdown, .my-player .timeout-wrapper');

        const accionesArea = document.querySelector('[class*="actions-area"], [class*="table-actions"], .table-actions-wrapper, .actions-area');
        const rawBtns = Array.from(document.querySelectorAll('[class*="action-button"], [class*="pre-configured-action-button"], [id*="FOLD"], [id*="CHECK"], [id*="CALL"], [id*="RAISE"], [id*="BET"], [id*="ALLIN"], [id*="pre-configured-action-button"]'));
        const uniqueBtns = rawBtns.filter((el, idx, self) => self.indexOf(el) === idx);
        const opcionesAccion = uniqueBtns
            .filter(b => b.offsetWidth > 0 || b.offsetHeight > 0 || window.getComputedStyle(b).display !== 'none')
            .map(b => {
                let id = (b.id || '').toUpperCase();
                const titleEl = b.querySelector('.action-title, [class*="action-title"], .action-button-text');
                const txt = (titleEl ? (titleEl.innerText || titleEl.textContent || '') : b.innerText || '').toUpperCase();
                if (id.includes('FOLD') || txt.includes('FOLD') || txt.includes('RETIRAR')) id = 'FOLD';
                else if (id.includes('CHECK') || txt.includes('CHECK') || txt.includes('PASAR')) id = 'CHECK';
                else if (id.includes('CALL') || txt.includes('CALL') || txt.includes('IGUALAR') || txt.includes('PAGAR')) id = 'CALL';
                else if (id.includes('RAISE') || id.includes('BET') || txt.includes('RAISE') || txt.includes('BET') || txt.includes('SUBIR') || txt.includes('APOSTAR')) id = 'RAISE';
                else if (id.includes('ALLIN') || id.includes('ALL-IN') || id.includes('ACTION-BUTTON-0') || txt.includes('ALL-IN') || txt.includes('ALL IN')) id = 'ALL_IN';
                else id = 'BTN';
                const valEl = b.querySelector('.action-value, [class*="action-value"]');
                const valor = (valEl ? (valEl.innerText || valEl.textContent || '') : '').trim();
                return `${id}${valor ? ' (' + valor + ')' : ''}`;
            });

        const esMiTurno = miTimerActivo || opcionesAccion.length > 0;

        const apuestasRivales = Array.from(document.querySelectorAll('.player-area:not(.my-player) .player-bet .amount, .player-area:not(.my-player) .bet-amount'))
            .filter(a => {
                const parentBet = a.closest('.bet, .player-bet');
                return !parentBet || !parentBet.classList.contains('pt-visibility-hidden');
            })
            .map(a => a.innerText.trim()).filter(Boolean).join(', ');

        const miStack = (document.querySelector('.my-player .player-nameplate .amount, .my-player .player-chips, .my-player .chips-amount, .my-player .stack-amount, .my-player .player-balance')?.innerText || '0').trim();
        const stackRival = Array.from(document.querySelectorAll('.player-area:not(.my-player) .player-nameplate .amount, .player-area:not(.my-player) .player-chips, .player-area:not(.my-player) .chips-amount'))
            .map(el => el.innerText.trim()).filter(Boolean).join(' | ') || '0';

        const playerNodes = Array.from(document.querySelectorAll('.player-area, [class*="player-seat-"], [class*="table-player"]'));
        const uniquePlayerNodes = playerNodes.filter((node, index, self) => self.indexOf(node) === index);

        const jugadoresMesa = uniquePlayerNodes.map((area, idx) => {
            let seatNum = idx + 1;
            for (const c of area.classList) {
                const m = c.match(/^player-seat-(\d+)$/i) || c.match(/^seat-(\d+)$/i) || c.match(/seat_(\d+)/i);
                if (m) {
                    seatNum = parseInt(m[1], 10);
                    break;
                }
            }
            if (seatNum === idx + 1) {
                const parentSeat = area.closest('[class*="player-seat-"]');
                if (parentSeat) {
                    for (const c of parentSeat.classList) {
                        const m = c.match(/^player-seat-(\d+)$/i) || c.match(/^seat-(\d+)$/i);
                        if (m) {
                            seatNum = parseInt(m[1], 10);
                            break;
                        }
                    }
                }
            }

            const isHeroByClass = area.classList.contains('my-player');
            const isHeroByCards = !!area.querySelector('.cards-holder-hero');
            const isHeroByChipsId = !!area.querySelector('[id*="chips-Belnebu"]');

            let nickTarget = area.querySelector('.target, .player-nameplate .nickname, .player-name, .nickname, .username');
            let nick = (nickTarget ? (nickTarget.innerText || nickTarget.textContent || '') : '').trim();

            if (!nick || /^(Time|Tiempo)[\s:\n]*\d+/i.test(nick) || /^\d+$/.test(nick)) {
                const amountEl = area.querySelector('.amount, [id*="chips-"]');
                if (amountEl && amountEl.id) {
                    const idMatch = amountEl.id.match(/^chips-([^&"'-]+)/i);
                    if (idMatch && idMatch[1] && !/^(Time|Tiempo)/i.test(idMatch[1])) {
                        nick = idMatch[1].trim();
                    }
                }
            }

            const isHero = isHeroByClass || isHeroByCards || isHeroByChipsId || (nick && (nick.toLowerCase() === 'belnebu' || nick.toLowerCase() === 'amandaenlavida'));

            if (isHero && (!nick || /^(Time|Tiempo)[\s:\n]*\d+/i.test(nick))) {
                nick = 'Belnebu';
            } else if (!nick || /^(Time|Tiempo)[\s:\n]*\d+/i.test(nick) || /^\d+$/.test(nick)) {
                nick = `Rival_${idx + 1}`;
            }

            const stackTxt = (area.querySelector('.player-nameplate .amount, .player-chips, .chips-amount, .stack-amount, .player-balance')?.innerText || '0').trim();
            const betEl = area.querySelector('.player-bet .amount, .bet-amount, .chip-amount');
            const parentBetEl = betEl ? betEl.closest('.bet, .player-bet') : null;
            const betVisible = !parentBetEl || !parentBetEl.classList.contains('pt-visibility-hidden');
            const betVal = (betEl && betVisible ? (betEl.innerText || betEl.textContent || '') : '').replace(/[^\d.]/g, '');
            const actionEl = area.querySelector('.player-action, .action-label, .action-text, .bubble, .last-action');
            const actionTxt = (actionEl ? (actionEl.innerText || actionEl.textContent || '') : '').trim();

            const vpipWingsEl = area.querySelector('.hud-wings-value-wrapper:not(.right) .hud-wings-value');
            const afWingsEl = area.querySelector('.hud-wings-value-wrapper.right .hud-wings-value');
            const hudVpip = (vpipWingsEl ? (vpipWingsEl.innerText || vpipWingsEl.textContent || '') : '').trim();
            const hudAf = (afWingsEl ? (afWingsEl.innerText || afWingsEl.textContent || '') : '').trim();

            const cls = (area.className || '').toLowerCase();
            const txtArea = (area.innerText || '').toLowerCase();
            const isFold = cls.includes('fold') || txtArea.includes('fold') || txtArea.includes('retir');
            const isEmpty = txtArea.includes('vacío') || txtArea.includes('empty');
            const estado = isEmpty ? 'VACIO' : (isFold ? 'FOLD' : 'ACTIVO');
            const posContainer = area.querySelector('.game-position:not(.pt-visibility-hidden)');
            const isDealer = !!posContainer && !!posContainer.querySelector('.dealer');

            const isTurnAct = isHero ? esMiTurno : (!!area.querySelector('.turn-to-act-indicator') || !!area.querySelector('.text-countdown, .timeout-wrapper'));

            return {
                seat: seatNum,
                asiento: seatNum,
                nickname: nick,
                player_id: nick,
                stack: stackTxt,
                bet: betVal,
                action: actionTxt,
                status: estado,
                estado: estado,
                is_hero: isHero,
                is_dealer: isDealer,
                is_turn: isTurnAct,
                hud_vpip: hudVpip,
                hud_af: hudAf
            };
        });

        const btnCallEl = accionesArea ? accionesArea.querySelector('#CALL, .action-call') : null;
        const callVal = (btnCallEl?.querySelector('.action-value')?.innerText || '').trim();
        const btnAllinEl = accionesArea ? accionesArea.querySelector('#ALLIN, .action-allin') : null;
        const allinVal = (btnAllinEl?.querySelector('.action-value')?.innerText || '').trim();
        const hayCheckDom = opcionesAccion.includes('CHECK');
        const hayCallDom = opcionesAccion.includes('CALL');
        const hayAllinDom = opcionesAccion.includes('ALL_IN') || opcionesAccion.includes('ALLIN');
        const aIgualar = hayCheckDom ? '0' : (callVal || (hayAllinDom && !hayCallDom ? (allinVal || miStack) : '') || apuestasRivales || '0');

        const modales = [];
        if (document.querySelector('.filter-play-button')) modales.push('Jugar Poker');
        const btnOk = document.querySelector('#general-popup-button-action-OK');
        if (btnOk && (btnOk.offsetWidth > 0 || btnOk.offsetHeight > 0)) modales.push('Aceptar');
        if (document.body && (document.body.textContent.includes('podrías hacer check sin coste alguno') || document.body.textContent.includes('¿Seguro que quieres foldear?'))) {
            modales.push('PopupCheckGratis');
        }
        const textosModal = ['Jugar de nuevo', 'Replay', 'Volver a jugar', 'Aceptar', 'Jugar Poker'];
        for (const t of textosModal) {
            const encontrado = Array.from(document.querySelectorAll('button, a, div, span')).find(
                el => (el.offsetWidth > 0 || el.offsetHeight > 0) && (el.innerText || el.textContent || '').trim() === t
            ) || Array.from(document.querySelectorAll('button, a, div.btn, span')).find(
                el => (el.offsetWidth > 0 || el.offsetHeight > 0) && (el.textContent || '').trim().includes(t)
            );
            if (encontrado && !modales.includes(t)) modales.push(t);
        }

        const textoBody = document.body ? (document.body.innerText || '') : '';
        const vecesVacio = (textoBody.match(/Vacío|Empty/g) || []).length;
        const heroAsientoVacio = Array.from(document.querySelectorAll('div, span')).some(el => {
            const t = (el.innerText || '').trim();
            if (t !== 'Vacío' && t !== 'Empty') return false;
            const r = el.getBoundingClientRect();
            return r.width > 10 && r.height > 10 && r.top > 380 && r.left > 350 && r.left < 930;
        });
        const hayGanador1500 = stackRival.includes('1,500') || stackRival.includes('1500') || textoBody.includes('1,500');
        if (!esMiTurno && misCartas.length === 0 && (heroAsientoVacio || vecesVacio >= 2 || (vecesVacio >= 1 && hayGanador1500))) {
            modales.push('MesaFinalizadaVacia');
        }

        let posDom = '';
        const esDealerHero = !!document.querySelector('.my-player .dealer-button, .my-player .dealer-chip, .my-player .dealer-badge, .my-player.dealer, .my-player [class*="dealer"], .seat-hero [class*="dealer"]');
        if (esDealerHero) {
            posDom = 'BTN';
        } else {
            const ciegaHero = parseFloat((apuestasRivales['hero'] || '0').replace(/[^\d.]/g, '')) || 0;
            const bbNum = parseFloat(bigBlindDom.replace(/[^\d.]/g, '')) || 20;
            if (ciegaHero > 0 && ciegaHero <= 0.6 * bbNum) {
                posDom = 'SB';
            } else if (ciegaHero >= 0.8 * bbNum) {
                posDom = 'BB';
            }
        }

        return {
            mis_cartas: misCartas.join(' '),
            cartas_comunitarias: comunitarias.join(' '),
            bote: bote,
            a_igualar: aIgualar,
            mi_stack: miStack,
            stack_rival: stackRival,
            fuerza_mano: fuerzaMano,
            monto_apuesta_input: montoInput,
            opciones_disponibles: opcionesAccion.join(' | '),
            botones_visibles: opcionesAccion.join(' | '),
            modales_visibles: modales.join(' | '),
            es_mi_turno: esMiTurno,
            tiempo_restante: timerTexto,
            apuestas_rivales: apuestasRivales,
            jugadores_mesa: jugadoresMesa,
            big_blind: bigBlindDom,
        };
    } catch (e) {
        return { error: String(e) };
    }
};"""


def obtener_target_mesa(page: Any) -> Any:
    """Busca y retorna la página principal o iFrame de la mesa activa."""
    try:
        pages = page.context.pages
        for p in reversed(pages):
            if not p.is_closed():
                for frame in p.frames:
                    try:
                        tiene_mesa = frame.evaluate(
                            "() => !!document.querySelector('.my-player, .community-cards, .actions-area, .game-container')"
                        )
                        if tiene_mesa:
                            return frame
                    except Exception:
                        pass
                if p.evaluate(
                    "() => !!document.querySelector('.my-player, .community-cards, .actions-area, .game-container')"
                ):
                    return p
    except Exception:
        pass
    return page


def esperar_sincronizacion_turno(target_mesa: Any, timeout_max_ms: int = 1200) -> None:
    """Espera síncronamente la señal del MutationObserver para asegurar que el DOM esté actualizado."""
    try:
        target_mesa.evaluate(JS_VERIFICAR_TURNO_O_MODAL, timeout=timeout_max_ms)
    except Exception:
        pass


def esperar_carga_dom(target: Any, timeout_ms: int = 5000) -> None:
    try:
        target.wait_for_load_state("domcontentloaded", timeout=timeout_ms)
    except Exception:
        pass


def _parse_num_dom(valor: Any, default: float = 0.0) -> float:
    if valor is None:
        return default
    if isinstance(valor, (int, float)):
        return float(valor)
    s = str(valor).strip()
    if not s:
        return default
    m = re.search(r"(\d+(?:[.,]\d+)?)", s)
    if not m:
        return default
    num_str = m.group(1).replace(",", ".")
    try:
        return float(num_str)
    except Exception:
        return default


def enriquecer_dom_con_contexto(
    dom_data: dict,
    nuevo_dom: Optional[dict] = None,
    tracker_obj: Optional[Any] = None,
    profiler_obj: Optional[Any] = None,
    memory_obj: Optional[Any] = None,
) -> dict:
    """Enriquece los datos extraídos del DOM con cálculos GTO, contextuales y perfilado de oponentes."""
    if nuevo_dom is not None:
        if isinstance(nuevo_dom, dict):
            dom_data.update(nuevo_dom)
        elif tracker_obj is None and (hasattr(nuevo_dom, "my_cards") or hasattr(nuevo_dom, "current_street") or type(nuevo_dom).__name__ == "GameTracker"):
            tracker_obj = nuevo_dom

    if tracker_obj is not None:
        dom_data["historial_mano"] = getattr(tracker_obj, "obtener_contexto_completo", lambda: "")()
        mis_cartas = formatear_cartas_canonicas(dom_data.get("mis_cartas", "") or " ".join(getattr(tracker_obj, "my_cards", []) or []))
        dom_comm_str = str(dom_data.get("cartas_comunitarias", "") or "").strip()
        es_preflop = (
            getattr(tracker_obj, "current_street", "PREFLOP") == "PREFLOP"
            or getattr(tracker_obj, "current_round_no", 1) <= 1
            or not dom_comm_str
        )
        if es_preflop:
            comunitarias = ""
            dom_data["cartas_comunitarias"] = ""
            if hasattr(tracker_obj, "community_cards"):
                tracker_obj.community_cards.clear()
        else:
            comunitarias = formatear_cartas_canonicas(dom_comm_str or " ".join(getattr(tracker_obj, "community_cards", []) or []))
        bote = dom_data.get("bote", "") or getattr(tracker_obj, "last_pot", 0) or 0
    else:
        mis_cartas = formatear_cartas_canonicas(dom_data.get("mis_cartas", ""))
        comunitarias = formatear_cartas_canonicas(dom_data.get("cartas_comunitarias", ""))
        bote = dom_data.get("bote", 0)

    dom_data["mis_cartas"] = mis_cartas
    dom_data["cartas_comunitarias"] = comunitarias
    dom_data["bote"] = bote

    if profiler_obj is not None and tracker_obj is not None:
        jugadores = dom_data.get("jugadores_mesa") or []
        if isinstance(jugadores, list) and jugadores:
            bb_val = _parse_num_dom(dom_data.get("big_blind"), float(getattr(config, "BIG_BLIND_DEFAULT", 20.0))) or 20.0
            perfiles_multi = []
            for j in jugadores:
                if not isinstance(j, dict):
                    continue
                nick_j = sanitizar_nickname(j.get("nickname") or j.get("player_id"))
                if not nick_j or es_nickname_invalido(nick_j):
                    continue
                p_prof = profiler_obj.obtener_perfil_explotativo(nick_j, nick_j, seat=j.get("asiento"), status=j.get("estado"))
                if p_prof:
                    perfiles_multi.append(p_prof)
            if perfiles_multi:
                dom_data["perfiles_oponentes"] = perfiles_multi
                rival_target = next((p for p in perfiles_multi if not p.get("is_hero")), perfiles_multi[0])
                dom_data["perfil_rival"] = rival_target

    posicion = str(dom_data.get("posicion") or (getattr(tracker_obj, "position_name", "BTN") if tracker_obj else "BTN")).upper()
    stack_ef = _parse_num_dom(dom_data.get("mi_stack"), 500.0)
    a_igualar = _parse_num_dom(dom_data.get("a_igualar"), 0.0)
    situacion = "vs_raise" if a_igualar > 0 else "open"

    gto_math = calcular_pot_odds_y_sizing(bote, a_igualar, stack_ef, stack_rival_str=dom_data.get("stack_rival"))
    gto_pre = clasificar_mano_preflop(mis_cartas, posicion=posicion, stack_efectivo_bb=gto_math["stack_efectivo_bb"], situacion=situacion)
    gto_textura = analizar_textura_mesa_y_blockers(mis_cartas, comunitarias)
    gto_comb = evaluar_combinacion_mano_y_mesa(mis_cartas, comunitarias)

    dom_data["gto_math"] = gto_math
    dom_data["gto_preflop"] = gto_pre
    dom_data["gto_textura"] = gto_textura
    dom_data["gto_combinacion"] = gto_comb
    dom_data["gto_frecuencia"] = generar_frecuencia_mixta_gto()

    return dom_data
