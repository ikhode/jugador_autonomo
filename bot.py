import os
import re
import json
import time
import hashlib
import concurrent.futures
from pathlib import Path
from typing import Optional
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError
import config
from poker_agent import PokerAgent, PokerDecision
from opponents_profiler import OpponentProfiler
from poker_experience_memory import PokerExperienceMemory
from poker_gto_engine import clasificar_mano_preflop, calcular_pot_odds_y_sizing, analizar_textura_mesa_y_blockers, generar_frecuencia_mixta_gto, evaluar_combinacion_mano_y_mesa
from analytics_logger import PokerAnalyticsLogger

# Pool de subprocesos persistente global para cero bloqueos de E/S o red
bg_executor = concurrent.futures.ThreadPoolExecutor(max_workers=2)

# Logger asíncrono de analíticas (cero impacto en latencia de juego)
analytics_logger = PokerAnalyticsLogger(
    version_id=getattr(config, "BOT_VERSION", "1.3"),
    version_description=getattr(config, "BOT_VERSION_DESC", "Configuración activa del bot"),
    model_name=getattr(config, "GEMINI_MODEL", "gemini-3.1-flash-lite"),
    initial_bankroll=getattr(config, "INITIAL_BANKROLL", 1500.0),
    big_blind=getattr(config, "BIG_BLIND_DEFAULT", 20.0),
    async_mode=True,
) if getattr(config, "ANALYTICS_ENABLED", True) else None

# ---------------------------------------------------------
# ACUMULADOR DE HISTORIAL COMPLETO DE PARTIDA Y TORNEO
# ---------------------------------------------------------
class GameTracker:
    def __init__(self):
        self.hand_number = 1
        self.current_hand_history = []
        self.past_hands_summary = []
        self.http_turn_signal = False
        self.my_cards = []
        self.community_cards = []
        self.last_pot = 0
        self.opp_id = ""
        self.opp_nick = ""
        self.is_dealer = False
        self.position_name = "DESCONOCIDA"
        self.current_street = "PREFLOP"
        self.current_round_no = 1
        self.hero_just_acted_ts = 0.0

    def registrar_evento(self, tipo: str, detalle: str):
        timestamp = time.strftime('%H:%M:%S')
        msg = f"[{timestamp}] {tipo}: {detalle}"
        self.current_hand_history.append(msg)
        if len(self.current_hand_history) > 25:
            self.current_hand_history.pop(0)

    def finalizar_mano_actual(self, resultado_str: str):
        resumen = f"Mano #{self.hand_number}: Posición {self.position_name} | Cartas {self.my_cards} | Bote: {self.last_pot} | Resultado: {resultado_str}"
        self.past_hands_summary.append(resumen)
        if len(self.past_hands_summary) > 5:
            self.past_hands_summary.pop(0)

        if analytics_logger is not None:
            try:
                analytics_logger.finalizar_mano(
                    resultado_str=resultado_str,
                    hand_number=self.hand_number,
                    hole_cards=" ".join(self.my_cards) if self.my_cards else None,
                    community_cards=" ".join(self.community_cards) if self.community_cards else None,
                    pot_final=float(self.last_pot or 0),
                )
            except Exception:
                pass

        self.hand_number += 1
        self.current_hand_history.clear()
        self.my_cards.clear()
        self.community_cards.clear()
        self.last_pot = 0
        self.http_turn_signal = False
        self.is_dealer = False
        self.position_name = "DESCONOCIDA"
        self.current_street = "PREFLOP"
        self.current_round_no = 1

    def reset_mano(self):
        self.finalizar_mano_actual("Reinicio de mano / Modal")

    def obtener_contexto_completo(self) -> str:
        past_str = "\n".join(self.past_hands_summary) if self.past_hands_summary else "Primera mano del torneo."
        current_str = "\n".join(self.current_hand_history) if self.current_hand_history else "Inicio de la mano."
        return f"--- HISTORIAL DE MANOS PREVIAS EN ESTE TORNEO ---\n{past_str}\n\n--- SECUENCIA EN VIVO DE LA MANO ACTUAL (# {self.hand_number}) ---\n{current_str}"

tracker = GameTracker()
profiler = OpponentProfiler()

# JS MutationObserver para reacción instantánea (0ms)
JS_INICIALIZAR_MUTATION_OBSERVER = r"""() => {
    if (window.__POKER_OBSERVER_ACTIVE__) return;
    window.__POKER_OBSERVER_ACTIVE__ = true;
    window.__POKER_EVENT_CHANGED__ = true;

    const getSnapshot = () => {
        const miTimer = !!document.querySelector('.my-player .text-countdown, .my-player .timeout-wrapper');
        const idsAccion = ['#FOLD', '#CHECK', '#CALL', '#BET', '#RAISE', '#pre-configured-action-button-0'];
        const hayBotones = idsAccion.some(id => {
            const el = document.querySelector(id);
            return el && (el.offsetWidth > 0 || el.offsetHeight > 0);
        });
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
        const misCartas = Array.from(document.querySelectorAll('.my-player .card-image-backup')).map(c => {
            const rank = c.querySelector('.card-rank')?.innerText || '';
            const suit = c.querySelector('.card-suit')?.innerText || '';
            return (rank + suit).trim();
        }).filter(Boolean);

        const comunitarias = Array.from(document.querySelectorAll('.community-cards .card-image-backup')).map(c => {
            const rank = c.querySelector('.card-rank')?.innerText || '';
            const suit = c.querySelector('.card-suit')?.innerText || '';
            return (rank + suit).trim();
        }).filter(Boolean);

        const bote = (document.querySelector('#main-pot, .total-pot-amount')?.innerText || '').trim();
        const fuerzaMano = (document.querySelector('.hand-strength-text')?.innerText || '').trim();
        const montoInput = document.querySelector('.betting-input, .raise-input')?.value || '0';

        const timerTexto = (document.querySelector('.my-player .countdown-text, .my-player .text-countdown')?.innerText || '').trim();
        const miTimerActivo = !!document.querySelector('.my-player .text-countdown, .my-player .timeout-wrapper');

        const accionesArea = document.querySelector('.actions-area');
        const opcionesAccion = accionesArea
            ? Array.from(accionesArea.querySelectorAll('.action-button, #FOLD, #CHECK, #CALL, #BET, #RAISE'))
                .filter(b => b.offsetWidth > 0 || b.offsetHeight > 0 || window.getComputedStyle(b).display !== 'none')
                .map(b => {
                    const id = b.id || 'BTN';
                    const titulo = (b.querySelector('.action-title')?.innerText || b.innerText || '').replace(/\s+/g, ' ').trim();
                    const valor = (b.querySelector('.action-value')?.innerText || '').trim();
                    return `${id}${valor ? ' (' + valor + ')' : ''}`;
                })
            : [];

        const esMiTurno = miTimerActivo || opcionesAccion.length > 0;

        const apuestasRivales = Array.from(document.querySelectorAll('.player-area:not(.my-player) .player-bet .amount'))
            .map(a => a.innerText.trim()).filter(Boolean).join(', ');

        const miStack = (document.querySelector('.my-player .player-chips, .my-player .chips-amount, .my-player .stack-amount, .my-player .player-balance')?.innerText || '0').trim();
        const stackRival = Array.from(document.querySelectorAll('.player-area:not(.my-player) .player-chips, .player-area:not(.my-player) .chips-amount, .player-area:not(.my-player) .stack-amount, .player-area:not(.my-player) .player-balance'))
            .map(el => el.innerText.trim()).filter(Boolean).join(' | ') || '0';

        const btnCallEl = accionesArea ? accionesArea.querySelector('#CALL, .action-call') : null;
        const callVal = (btnCallEl?.querySelector('.action-value')?.innerText || '').trim();
        const aIgualar = callVal || apuestasRivales || '0';

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

        // Detectar si hemos sido eliminados o la mesa quedó vacía ("Vacío" en nuestro asiento inferior o >=2 asientos vacíos)
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
            apuestas_rivales: apuestasRivales
        };
    } catch(e) {
        return null;
    }
}"""

def esperar_carga_dom(target, timeout_ms: int = 5000):
    try:
        target.wait_for_load_state("domcontentloaded", timeout=timeout_ms)
    except Exception:
        pass

def intentar_click(target, selector: str, descripcion: str, timeout: int = 4000) -> bool:
    selectores = [s.strip() for s in selector.split(",") if s.strip()]
    # Paso rápido (0ms): verificar si alguno de los selectores ya es visible ahora mismo
    for sel in selectores:
        try:
            elem = target.locator(sel).first
            if elem.is_visible():
                elem.click(force=True, timeout=1200)
                print(f"✅ [CLIC OK] Clic en {descripcion} realizado.")
                return True
        except Exception:
            pass

    if timeout <= 0:
        return False

    timeout_por_sel = max(300, int(timeout / max(1, len(selectores))))
    for sel in selectores:
        try:
            target.wait_for_selector(sel, state="attached", timeout=timeout_por_sel)

            try:
                elem = target.locator(sel).first
                if elem.is_visible():
                    elem.click(force=True, timeout=1200)
                    print(f"✅ [CLIC OK] Clic en {descripcion} realizado.")
                    return True
            except Exception:
                pass

            if not sel.startswith("xpath=") and ":has-text(" not in sel:
                clicked = target.evaluate("""(selector) => {
                    try {
                        const el = document.querySelector(selector);
                        if (el && !el.disabled && el.getAttribute('aria-disabled') !== 'true') {
                            el.scrollIntoView({ block: 'center' });
                            el.dispatchEvent(new MouseEvent('mousedown', { bubbles: true, cancelable: true }));
                            el.dispatchEvent(new MouseEvent('mouseup', { bubbles: true, cancelable: true }));
                            el.click();
                            return true;
                        }
                    } catch (e) {}
                    return false;
                }""", sel)

                if clicked:
                    print(f"⚡ [JS CLICK OK] Clic por evento JS realizado en {descripcion}.")
                    return True
        except Exception:
            continue
    return False

def hacer_login(page):
    print(f"🌐 Navegando a la página principal: {config.LOGIN_URL}")
    page.goto(config.LOGIN_URL, wait_until="domcontentloaded")
    esperar_carga_dom(page)
    page.wait_for_timeout(1000)

    print("🔑 Paso 1: Verificando estado de sesión...")
    click_acceder = intentar_click(page, config.SELECTORS["btn_acceder"], "Botón Acceder", timeout=4000)
    if not click_acceder:
        print("✅ Sesión activa detectada (cookies/storage persistentes cargados). Omitiendo login.")
        return

    try:
        page.wait_for_selector(config.SELECTORS["username_input"], state="visible", timeout=15000)
        page.wait_for_timeout(800)
    except Exception:
        pass

    try:
        print(f"👤 Paso 2: Ingresando usuario: {config.GAME_USER}")
        user_input = page.locator(config.SELECTORS["username_input"]).first
        user_input.click()
        user_input.fill(config.GAME_USER)
        page.wait_for_timeout(500)

        print("🔒 Paso 3: Ingresando contraseña...")
        pw_input = page.locator(config.SELECTORS["password_input"]).first
        pw_input.click()
        pw_input.fill(config.GAME_PASSWORD)
        page.wait_for_timeout(800)

        print("🚀 Paso 4: Iniciando sesión (Envío de formulario + Clic)...")
        pw_input.press("Enter")
        page.wait_for_timeout(1200)

        modal_abierto = page.evaluate("() => !!document.querySelector('.modal-content, .Auth-form')")
        if modal_abierto:
            intentar_click(page, config.SELECTORS["login_submit"], "Botón Iniciar Sesión", timeout=4000)

        print("⏳ Esperando a que el inicio de sesión se autentique completamente...")
        page.wait_for_timeout(3000)
        esperar_carga_dom(page, timeout_ms=8000)
    except Exception as e:
        print(f"ℹ️ Nota durante login: {e}")

def es_mesa_activa_o_turno(page) -> bool:
    if tracker.http_turn_signal:
        return True
    try:
        pages = page.context.pages
        for p in pages:
            if p.is_closed():
                continue
            for frame in p.frames:
                try:
                    activa = frame.evaluate("""() => {
                        const esVisible = (el) => !!(el && (el.offsetWidth > 0 || el.offsetHeight > 0) && window.getComputedStyle(el).display !== 'none');
                        // Si está visible el botón de lobby o fin de partida, la mesa NO está en mano activa
                        const btnPlay = document.querySelector('.filter-play-button');
                        if (esVisible(btnPlay)) return false;
                        const finPartida = Array.from(document.querySelectorAll('button, div.btn, span')).some(
                            el => esVisible(el) && ((el.textContent || '').includes('Jugar de nuevo') || (el.textContent || '').includes('Jugar Poker'))
                        );
                        if (finPartida) return false;

                        const miTimer = document.querySelector('.my-player .text-countdown, .my-player .timeout-wrapper, .my-player .countdown-text');
                        if (esVisible(miTimer)) return true;

                        const botonesAccion = Array.from(document.querySelectorAll('#FOLD, #CHECK, #CALL, #BET, #RAISE, .actions-area .action-button'));
                        if (botonesAccion.some(esVisible)) return true;

                        const cartasVisibles = Array.from(document.querySelectorAll('.my-player .card-image-backup .card-rank'));
                        return cartasVisibles.some(el => esVisible(el) && (el.innerText || '').trim().length > 0);
                    }""")
                    if activa:
                        return True
                except Exception:
                    pass
    except Exception:
        pass
    return False

def navegar_al_juego(page):
    print("\n🎰 --- VERIFICANDO ESTADO DE MESA Y LOBBY ---")

    if es_mesa_activa_o_turno(page):
        print("🎮 ¡RECONEXIÓN A PARTIDA EN CURSO DETECTADA! (Mesa activa o my_turn.mp3). Omitiendo pasos de navegación y saltando directamente al bucle de juego...")
        return page

    try:
        # Verificar si ya existe una pestaña con el cliente de iPoker abierto (games.codere.mx / poker/web)
        cliente_poker_abierto = False
        for p in reversed(page.context.pages):
            if not p.is_closed() and any(k in (p.url or "") for k in ["games.codere", "poker/web", "WebLauncher", "hostedMode"]):
                page = p
                page.bring_to_front()
                cliente_poker_abierto = True
                break

        if not cliente_poker_abierto:
            print("🎰 Paso 1: Haciendo clic en la sección de Poker...")
            if es_mesa_activa_o_turno(page):
                print("🎮 ¡Reconexión a mesa activa detectada! Saltando directamente a la mesa de juego...")
                return page

            if not intentar_click(page, config.SELECTORS["poker_nav_link"], "Enlace menú Poker", timeout=8000):
                print("⚠️ No se pudo encontrar el enlace de Poker. Reintentando...")
                page.reload()
                esperar_carga_dom(page)
                intentar_click(page, config.SELECTORS["poker_nav_link"], "Enlace menú Poker (Reintento)", timeout=5000)

            esperar_carga_dom(page)

            if es_mesa_activa_o_turno(page):
                return page

            print("🎰 Paso 2: Haciendo clic en Abrir Poker (#pokerPageOpen)...")
            if not intentar_click(page, config.SELECTORS["btn_open_poker"], "Botón Abrir Poker", timeout=10000):
                print("⏳ Botón Abrir Poker no visible aún, esperando un poco más...")
                intentar_click(page, config.SELECTORS["btn_open_poker"], "Botón Abrir Poker (Reintento)", timeout=5000)

            esperar_carga_dom(page)

            if len(page.context.pages) > 1:
                print("📑 Se detectó una nueva pestaña. Cambiando contexto al juego...")
                page = page.context.pages[-1]
                page.bring_to_front()
                esperar_carga_dom(page)
        else:
            print("⚡ Pestaña del cliente iPoker ya abierta. Saltando directamente al lobby de torneos (Paso 3)...")

        if es_mesa_activa_o_turno(page):
            print("✨ ¡Mesa o turno detectado tras abrir Poker! Cancelando clics del lobby.")
            return page

        print("🎰 Paso 3: Esperando a que la interfaz cargue el contenedor de partidas...")
        try:
            page.wait_for_selector(".home-game-categories-widget, .game-categories-widget-wrapper", state="visible", timeout=8000)
        except Exception:
            pass

        twister_encontrado = False
        intentos_twister = 0

        while intentos_twister < 10 and not twister_encontrado:
            if es_mesa_activa_o_turno(page):
                print("⚡ ¡Turno activo de juego detectado! Cancelando espera de Twister e ingresando a la mesa...")
                return page

            intentos_twister += 1
            if intentar_click(page, config.SELECTORS["select_game_card"], "Categoría Twister (.classic-twister)", timeout=1500):
                twister_encontrado = True
                break

            for frame in page.frames:
                try:
                    if intentar_click(frame, config.SELECTORS["select_game_card"], "Categoría Twister (iFrame)", timeout=0):
                        twister_encontrado = True
                        break
                except Exception:
                    pass

            if not twister_encontrado:
                page.wait_for_timeout(500)

        if es_mesa_activa_o_turno(page):
            return page

        if twister_encontrado:
            print("✅ Categoría Twister seleccionada con éxito.")
            esperar_carga_dom(page)
        else:
            print("⚠️ Twister no encontrado tras reintentos. Verificando si el botón de juego está visible directamente...")
            if intentar_click(page, config.SELECTORS["start_match_btn"], "Botón Jugar Poker (Directo)", timeout=3000):
                print("✨ ¡Botón Jugar Poker encontrado directamente sin pasar por Twister!")
                esperar_carga_dom(page)
                return page

        print("🎰 Paso 4: Haciendo clic en 'Jugar Poker' para iniciar la partida...")
        jugar_cliqueado = False
        intentos_jugar = 0

        while intentos_jugar < 6 and not jugar_cliqueado:
            if es_mesa_activa_o_turno(page):
                print("⚡ ¡Turno activo detectado! Cancelando clics del lobby...")
                return page

            intentos_jugar += 1
            if intentar_click(page, config.SELECTORS["start_match_btn"], "Botón Jugar Poker", timeout=1500):
                jugar_cliqueado = True
                break

            for frame in page.frames:
                try:
                    if intentar_click(frame, config.SELECTORS["start_match_btn"], "Botón Jugar Poker (iFrame)", timeout=0):
                        jugar_cliqueado = True
                        break
                except Exception:
                    pass

            if not jugar_cliqueado:
                page.wait_for_timeout(500)

        if jugar_cliqueado:
            esperar_carga_dom(page)
            print("✅ Navegación al juego completada. Entrando al bucle de juego reactivo.\n")
        else:
            # Si estamos atrapados en una vista de mesa finalizada ("Vacío") donde no hay lobby, abrir el selector con el botón '+' superior derecho o desde la pestaña principal
            print("🔄 No se encontró el botón en la vista actual (posible mesa finalizada 'Vacío'). Abriendo selector de mesas...")
            salir_de_mesa_vacia_o_finalizada(page, page, page.context)
            print("ℹ️ Entrando al bucle reactivo de juego.\n")

    except Exception as e:
        print(f"⚠️ Nota durante la navegación al juego: {e}")

    return page

def esperar_sincronizacion_turno(target, timeout_max_ms: int = 1500) -> bool:
    try:
        target.wait_for_function(JS_VERIFICAR_TURNO_O_MODAL, timeout=timeout_max_ms)
        return True
    except PlaywrightTimeoutError:
        return False

def obtener_target_mesa(page):
    try:
        pages = page.context.pages
        for p in reversed(pages):
            for frame in p.frames:
                try:
                    is_poker = frame.evaluate("""() => {
                        return !!(document.querySelector('.my-player, .community-cards, #FOLD, #CHECK, #CALL, #BET, #RAISE, .actions-area'));
                    }""")
                    if is_poker:
                        return frame
                except Exception:
                    pass
    except Exception:
        pass
    return page

def consultar_gemini_con_timeout(agent, screenshot_bytes, dom_data, timeout_seg: float = 8.0) -> Optional[PokerDecision]:
    """
    Ejecuta la consulta a Gemini AI en segundo plano con un timeout de 8.0 segundos.
    Permite que la API complete su análisis estratégico (suele demorar ~3s) garantizando que sea Gemini quien juegue.
    Mantiene 7.0s de margen de seguridad antes de los 15s de la mesa.
    """
    try:
        future = bg_executor.submit(agent.analizar_pantalla, screenshot_bytes, dom_data)
        return future.result(timeout=timeout_seg)
    except concurrent.futures.TimeoutError:
        print(f"⏱️ [TIMEOUT GUARDIA {timeout_seg}s] Gemini no respondió a tiempo. Ejecutando respuesta GTO rápida (0ms)...")
        return None
    except Exception as e:
        print(f"⚠️ Error en consulta asíncrona: {e}")
        return None

def resolver_popup_fold_check_gratis(target) -> bool:
    """
    Detecta y resuelve al instante el recuadro:
    'Has elegido fold, pero podrías hacer check sin coste alguno. ¿Seguro que quieres foldear?'
    haciendo clic en el botón 'Check' del diálogo emergente.
    """
    try:
        resuelto = target.evaluate("""() => {
            if (!document.body) return false;
            const txt = document.body.textContent || '';
            if (!txt.includes('podrías hacer check sin coste alguno') && !txt.includes('¿Seguro que quieres foldear?')) {
                return false;
            }
            // Buscar el botón 'Check' dentro del diálogo/modal (que no sea el #CHECK de la barra inferior)
            const candidatos = Array.from(document.querySelectorAll('button, div[role="button"], span, a'))
                .filter(el => {
                    if (el.id === 'CHECK' || el.closest('.actions-area')) return false;
                    const rect = el.getBoundingClientRect();
                    const t = (el.innerText || el.textContent || '').trim();
                    return rect.width > 20 && rect.height > 15 && (t === 'Check' || t === 'Pasar');
                });
            if (candidatos.length > 0) {
                const btn = candidatos[candidatos.length - 1];
                btn.dispatchEvent(new MouseEvent('mousedown', { bubbles: true, cancelable: true }));
                btn.dispatchEvent(new MouseEvent('mouseup', { bubbles: true, cancelable: true }));
                btn.click();
                return true;
            }
            return false;
        }""")
        if resuelto:
            print("🛡️ [POPUP RESUELTO] Recuadro '¿Seguro que quieres foldear?' detectado -> Clic automático en 'Check' gratis.")
            return True
    except Exception:
        pass
    return False

def clicar_texto_modal_universal(target, textos: list[str], descripcion: str = "Modal") -> bool:
    """
    Busca cualquier elemento visible (button, div, span, a) cuyo texto coincida con alguno de `textos`
    y dispara eventos reales de ratón + click(). Funciona aunque el botón en iPoker sea un <div> o <span>.
    """
    try:
        clicado = target.evaluate("""(textos) => {
            if (!document.body) return false;
            const elementos = Array.from(document.querySelectorAll('button, div, span, a'));
            // Preferir coincidencia exacta en el nodo más interno visible
            for (const t of textos) {
                const exactos = elementos.filter(el => {
                    if (el.closest('.actions-area')) return false;
                    const r = el.getBoundingClientRect();
                    const txt = (el.innerText || el.textContent || '').trim();
                    return r.width > 20 && r.height > 14 && r.width < 500 && txt.toLowerCase() === t.toLowerCase();
                });
                if (exactos.length > 0) {
                    const btn = exactos[exactos.length - 1];
                    btn.dispatchEvent(new MouseEvent('mousedown', { bubbles: true, cancelable: true }));
                    btn.dispatchEvent(new MouseEvent('mouseup', { bubbles: true, cancelable: true }));
                    btn.click();
                    return true;
                }
            }
            for (const t of textos) {
                const parciales = elementos.filter(el => {
                    if (el.closest('.actions-area')) return false;
                    const r = el.getBoundingClientRect();
                    const txt = (el.innerText || el.textContent || '').trim();
                    return r.width > 20 && r.height > 14 && r.width < 450 && r.height < 120 && txt.toLowerCase().includes(t.toLowerCase());
                });
                if (parciales.length > 0) {
                    const btn = parciales[parciales.length - 1];
                    btn.dispatchEvent(new MouseEvent('mousedown', { bubbles: true, cancelable: true }));
                    btn.dispatchEvent(new MouseEvent('mouseup', { bubbles: true, cancelable: true }));
                    btn.click();
                    return true;
                }
            }
            return false;
        }""", textos)
        if clicado:
            print(f"✅ [MODAL UNIVERSAL OK] Clic realizado en '{descripcion}'.")
            return True
    except Exception:
        pass
    return False

def salir_de_mesa_vacia_o_finalizada(target_mesa, page, context) -> bool:
    """
    Sale de una mesa finalizada ('Vacío') donde ya fuimos eliminados o terminó el torneo:
    1. Intenta pulsar 'Jugar de nuevo' / 'Replay' / 'Aceptar' si hay un modal oculto.
    2. Pulsa el icono '+' (Añadir mesa / Lobby) en la esquina superior derecha de la mesa iPoker.
    3. Si tras abrir el panel está disponible Twister / Jugar Poker, inicia nueva partida.
    4. Si sigue bloqueado en la mesa vacía, reabre desde la pestaña principal de Codere (#pokerPageOpen).
    """
    try:
        if clicar_texto_modal_universal(target_mesa, ["Jugar de nuevo", "Replay", "Volver a jugar", "Jugar Poker"], "Jugar de nuevo / Iniciar"):
            time.sleep(1.5)
            return True

        owner_page = getattr(target_mesa, "page", page)
        print("🚪 [MESA VACÍA / ELIMINADO] Saliendo de la mesa finalizada hacia el selector de torneos...")

        # 1. Intentar clic en el botón '+' (Añadir mesa / Lobby) en la esquina superior derecha de iPoker
        clic_mas = target_mesa.evaluate("""() => {
            const candidatos = Array.from(document.querySelectorAll('button, div, span, a, svg, img')).filter(el => {
                const r = el.getBoundingClientRect();
                const cls = (el.className && typeof el.className === 'string') ? el.className.toLowerCase() : '';
                const id = (el.id || '').toLowerCase();
                if (cls.includes('add') || cls.includes('lobby') || cls.includes('multi') || id.includes('add') || id.includes('lobby')) {
                    return r.width > 12 && r.height > 12 && r.top < 150;
                }
                // Esquina superior derecha (icono '+' de añadir mesa en iPoker 1280x720)
                return r.width >= 18 && r.width <= 90 && r.height >= 18 && r.height <= 90 && r.top >= 10 && r.top <= 130 && r.left >= 1150;
            });
            if (candidatos.length > 0) {
                const btn = candidatos[candidatos.length - 1];
                btn.dispatchEvent(new MouseEvent('mousedown', { bubbles: true, cancelable: true }));
                btn.dispatchEvent(new MouseEvent('mouseup', { bubbles: true, cancelable: true }));
                btn.click();
                return true;
            }
            return false;
        }""")

        if not clic_mas and not owner_page.is_closed():
            try:
                owner_page.mouse.click(1240, 62)
            except Exception:
                pass

        time.sleep(1.2)

        # Verificar si apareció la tarjeta Twister o el botón Jugar Poker tras pulsar '+'
        if intentar_click(target_mesa, config.SELECTORS["select_game_card"], "Categoría Twister (tras salir de mesa)", timeout=1200):
            time.sleep(0.6)
        if intentar_click(target_mesa, config.SELECTORS["start_match_btn"], "Botón Jugar Poker (tras salir de mesa)", timeout=1500):
            return True
        if clicar_texto_modal_universal(target_mesa, ["Jugar Poker", "Jugar de nuevo", "Jugar"], "Iniciar Partida"):
            return True

        # 2. Respaldo definitivo: si sigue en la mesa vacía y tenemos la pestaña principal de Codere, reabrir desde ahí
        paginas_abiertas = [p for p in context.pages if not p.is_closed()]
        if len(paginas_abiertas) > 1:
            lobby_main = paginas_abiertas[0]
            if owner_page != lobby_main:
                print("🔄 Cerrando pestaña de mesa finalizada y relanzando desde el lobby principal de Codere...")
                try:
                    owner_page.close()
                except Exception:
                    pass
                lobby_main.bring_to_front()
                intentar_click(lobby_main, config.SELECTORS["btn_open_poker"], "Botón Abrir Poker (Relanzamiento)", timeout=4000)
                time.sleep(2.0)
                return True
    except Exception as e:
        print(f"ℹ️ Nota al salir de mesa vacía: {e}")
    return False

def ejecutar_accion(target, decision, dom_data: Optional[dict] = None) -> str:
    accion = decision.accion.upper()
    accion_ejecutada = accion
    print(f"⚡ [ACCION IA]: {accion} | Estado: [{decision.estado_pantalla}] | Evaluacion: {decision.evaluacion}")
    tracker.hero_just_acted_ts = time.time()

    btn_fold = config.SELECTORS.get("btn_fold", config.SELECTORS["fold"])
    btn_check = config.SELECTORS.get("btn_check", config.SELECTORS["check"])
    btn_call = config.SELECTORS.get("btn_call", config.SELECTORS["call"])
    btn_raise = config.SELECTORS.get("btn_raise", config.SELECTORS["raise"])
    btn_allin = config.SELECTORS.get("btn_allin", config.SELECTORS["allin"])
    btn_replay = config.SELECTORS.get("btn_replay", config.SELECTORS["replay"])
    raise_input_sel = config.SELECTORS.get("raise_input", "input.betting-input")

    try:
        # Si ya está abierto el popup de advertencia de Fold/Check, resolverlo primero
        if resolver_popup_fold_check_gratis(target):
            return "CHECK"

        if accion == "REPLAY":
            print("🔄 Fin de partida detectado -> Clic en 'Jugar de nuevo'")
            if not clicar_texto_modal_universal(target, ["Jugar de nuevo", "Replay", "Volver a jugar"], "Jugar de nuevo"):
                intentar_click(target, btn_replay, "Jugar de nuevo", timeout=2000)
            esperar_carga_dom(target)
            accion_ejecutada = "REPLAY"

        elif accion == "START_GAME":
            print("🎮 Lobby detectado -> Clic en Tarjeta de Juego e Iniciar Partida")
            intentar_click(target, config.SELECTORS["select_game_card"], "Tarjeta de Juego Twister", timeout=2000)
            esperar_carga_dom(target)
            if not intentar_click(target, config.SELECTORS["start_match_btn"], "Botón Iniciar Partida", timeout=2000):
                clicar_texto_modal_universal(target, ["Jugar Poker", "Jugar"], "Botón Jugar Poker")
            esperar_carga_dom(target)
            accion_ejecutada = "START_GAME"

        elif accion == "FOLD":
            # PREVENCIÓN: Antes de hacer clic en Fold, verificar (0ms) si el botón Check está activo.
            # Si Check está visible, NUNCA hacer clic en Fold (evita el popup de advertencia y aprovecha la carta gratis).
            if intentar_click(target, btn_check, "Botón Check (Gratis prioritario en lugar de Fold)", timeout=0):
                return "CHECK"
            intentar_click(target, btn_fold, "Botón Fold", timeout=2000)
            time.sleep(0.15)
            if resolver_popup_fold_check_gratis(target):
                return "CHECK"
            accion_ejecutada = "FOLD"

        elif accion == "CHECK":
            if intentar_click(target, btn_check, "Botón Check", timeout=1500):
                accion_ejecutada = "CHECK"
            else:
                respaldo = decidir_accion_rapida(dom_data or {})
                if respaldo.accion in ("RAISE", "CALL"):
                    print("⚠️ Check no disponible (hubo apuesta), pero mano es fuerte/jugable -> Realizando Call...")
                    if intentar_click(target, btn_call, "Botón Call (Fallback Mano Fuerte)", timeout=1500):
                        accion_ejecutada = "CALL"
                    else:
                        intentar_click(target, btn_fold, "Botón Fold (Fallback)", timeout=1500)
                        accion_ejecutada = "FOLD"
                else:
                    print("🛡️ Check no disponible (hubo apuesta rival) y mano débil -> Realizando Fold seguro...")
                    if intentar_click(target, btn_fold, "Botón Fold (Protección Stack)", timeout=1500):
                        accion_ejecutada = "FOLD"
                    else:
                        intentar_click(target, btn_call, "Botón Call (Último recurso)", timeout=1500)
                        accion_ejecutada = "CALL"

        elif accion == "CALL":
            if intentar_click(target, btn_call, "Botón Call", timeout=2000):
                accion_ejecutada = "CALL"
            else:
                print("⚠️ Call no disponible. Intentando Check o Fold...")
                if intentar_click(target, btn_check, "Botón Check (Fallback)", timeout=1500):
                    accion_ejecutada = "CHECK"
                else:
                    intentar_click(target, btn_fold, "Botón Fold (Fallback)", timeout=1500)
                    accion_ejecutada = "FOLD"

        elif accion == "RAISE":
            monto = getattr(decision, "monto_apuesta", 0) or getattr(decision, "monto", 0) or 0
            monto_actual_dom = int(re.sub(r"[^\d]", "", str((dom_data or {}).get("monto_apuesta_input", "0"))) or 0)
            if monto > 0 and monto != monto_actual_dom and monto > 60:
                try:
                    inp = target.locator("input.betting-input, input.raise-input").first
                    if inp.is_visible():
                        print(f"💰 Ajustando importe de subida a: {monto}")
                        inp.fill(str(monto), timeout=300)
                except Exception:
                    pass

            if intentar_click(target, btn_raise, "Botón Raise / Apuesta", timeout=1500):
                accion_ejecutada = "RAISE"
            elif clicar_texto_modal_universal(target, ["Raise a", "Raise", "Bet", "Subir", "Apostar"], "Botón Raise (Texto)"):
                accion_ejecutada = "RAISE"
            elif intentar_click(target, btn_call, "Botón Call (Fallback Raise)", timeout=1500):
                accion_ejecutada = "CALL"
            else:
                intentar_click(target, btn_check, "Botón Check (Fallback Raise)", timeout=1000)
                accion_ejecutada = "CHECK"

        elif accion in ("ALL_IN", "ALLIN"):
            if intentar_click(target, btn_allin, "Botón All-in", timeout=2000):
                accion_ejecutada = "ALL_IN"
            elif intentar_click(target, btn_raise, "Botón Raise (Fallback All-in)", timeout=2000):
                accion_ejecutada = "RAISE"
            else:
                intentar_click(target, btn_call, "Botón Call (Fallback All-in)", timeout=1500)
                accion_ejecutada = "CALL"

        elif accion == "WAIT":
            accion_ejecutada = "WAIT"
            try:
                if target.evaluate("() => document.body ? document.body.textContent.includes('Se ha terminado tu tiempo') : false"):
                    print("⚠️ Detectado modo Sit-out. Intentando reactivar...")
                    intentar_click(target, "#general-popup-button-action-OK", "Botón Aceptar Sit-out", timeout=2000)
            except Exception:
                pass

    except Exception as e:
        print(f"🏆 Partida o mesa finalizada/cerrada ({e}).")

    return accion_ejecutada

def decidir_accion_rapida(dom_data: dict) -> PokerDecision:
    if not dom_data or not dom_data.get('es_mi_turno'):
        return PokerDecision(
            evaluacion="No es nuestro turno",
            estrategia_competitiva="Esperando acción del rival",
            estado_pantalla="EN_MESA_JUEGO",
            es_mi_turno=False,
            accion="WAIT",
            monto_apuesta=0,
            confianza=0.0
        )

    fuerza = (dom_data.get('fuerza_mano') or '').strip()
    fuerza_lower = fuerza.lower()
    botones = dom_data.get('botones_visibles', '') or dom_data.get('opciones_disponibles', '')
    cartas = (dom_data.get('mis_cartas', '') or '').upper()

    gto_pre = dom_data.get('gto_preflop') or clasificar_mano_preflop(cartas)
    gto_comb = dom_data.get('gto_combinacion') or evaluar_combinacion_mano_y_mesa(cartas, dom_data.get('cartas_comunitarias', ''))
    gto_math = dom_data.get('gto_math') or {}

    raw_monto = dom_data.get('monto_apuesta_input', '0') or '0'
    monto_saneado = int(re.sub(r"[^\d]", "", str(raw_monto)) or 0) or gto_math.get('sizing_cbet_estandar', 40)

    hay_check = 'CHECK' in botones
    hay_call = 'CALL' in botones
    hay_bet = 'BET' in botones or 'RAISE' in botones

    tier = gto_pre.get('tier', 'DESCONOCIDO')
    es_pareja_mano = bool(gto_comb.get('es_pareja_en_mano'))
    coincidencias = bool(gto_comb.get('coincidencias_mesa'))
    pareja_solo_en_mesa = bool(gto_comb.get('pareja_en_mesa')) and not es_pareja_mano and not coincidencias
    es_preflop = not bool((dom_data.get('cartas_comunitarias') or '').strip())

    patrones_fuertes = ['doble pareja', 'trío', 'trio', 'escalera', 'color', 'full', 'póker', 'poker', 'escalera real']
    if not pareja_solo_en_mesa:
        patrones_fuertes.append('pareja')

    es_mano_fuerte = any(p in fuerza_lower for p in patrones_fuertes) or es_pareja_mano or coincidencias or (es_preflop and tier in ('S', 'A'))
    es_mano_jugable = (es_preflop and tier in ('S', 'A', 'B')) or ('proyecto' in fuerza_lower) or es_pareja_mano or coincidencias

    if es_mano_fuerte:
        if hay_bet:
            return PokerDecision(
                evaluacion=f"⚡ [0ms GTO] Mano fuerte ({fuerza or gto_pre.get('mano_code') or cartas}). Subiendo por valor.",
                estrategia_competitiva="Extraer valor máximo con mano fuerte",
                estado_pantalla="EN_MESA_JUEGO",
                es_mi_turno=True,
                accion="RAISE",
                monto_apuesta=monto_saneado,
                confianza=0.95
            )
        elif hay_call:
            return PokerDecision(
                evaluacion=f"⚡ [0ms GTO] Mano fuerte ({fuerza or gto_pre.get('mano_code') or cartas}). Pagando call rival.",
                estrategia_competitiva="Igualar apuesta con mano ganadora",
                estado_pantalla="EN_MESA_JUEGO",
                es_mi_turno=True,
                accion="CALL",
                monto_apuesta=0,
                confianza=0.92
            )
        elif hay_check:
            return PokerDecision(
                evaluacion=f"⚡ [0ms GTO] Mano fuerte ({fuerza or cartas}). Pasando gratis.",
                estrategia_competitiva="Pase táctico con mano fuerte",
                estado_pantalla="EN_MESA_JUEGO",
                es_mi_turno=True,
                accion="CHECK",
                monto_apuesta=0,
                confianza=0.92
            )

    if hay_check:
        return PokerDecision(
            evaluacion=f"⚡ [0ms GTO] Pase gratis disponible con mano {fuerza or gto_pre.get('mano_code') or cartas}.",
            estrategia_competitiva="Aprovechar carta gratis",
            estado_pantalla="EN_MESA_JUEGO",
            es_mi_turno=True,
            accion="CHECK",
            monto_apuesta=0,
            confianza=0.90
        )

    if hay_call and es_mano_jugable and gto_math.get('pot_odds_pct', 0) <= 38:
        return PokerDecision(
            evaluacion=f"⚡ [0ms GTO] Rango jugable [{tier}] ({gto_pre.get('mano_code') or cartas}). Pagando call con Pot Odds favorables.",
            estrategia_competitiva="Defensa de rango en precio correcto",
            estado_pantalla="EN_MESA_JUEGO",
            es_mi_turno=True,
            accion="CALL",
            monto_apuesta=0,
            confianza=0.85
        )

    if hay_call:
        return PokerDecision(
            evaluacion=f"⚡ [0ms GTO] Mano débil [{tier}] ({fuerza or cartas}). Fold para proteger stack.",
            estrategia_competitiva="Fold disciplinado GTO",
            estado_pantalla="EN_MESA_JUEGO",
            es_mi_turno=True,
            accion="FOLD",
            monto_apuesta=0,
            confianza=0.88
        )

    accion_fallback = "CHECK" if hay_check else ("FOLD" if "FOLD" in botones else "CALL")
    return PokerDecision(
        evaluacion=f"⚡ [0ms GTO] Acción rápida fallback: {accion_fallback}",
        estrategia_competitiva="Acción defensiva automática",
        estado_pantalla="EN_MESA_JUEGO",
        es_mi_turno=True,
        accion=accion_fallback,
        monto_apuesta=0,
        confianza=0.80
    )

def main():
    agent = PokerAgent()
    if analytics_logger is not None:
        try:
            from analytics_dashboard import iniciar_servidor_dashboard_background
            iniciar_servidor_dashboard_background(port=8050)
        except Exception:
            pass

    user_data_dir = os.path.abspath(config.USER_DATA_DIR)
    print(f"📁 Usando directorio de sesión persistente (Cookies/Cache/Storage): {user_data_dir}")

    try:
        with sync_playwright() as p:
            print("🚀 Iniciando navegador Playwright (Chromium Persistente)...")
            context = p.chromium.launch_persistent_context(
                user_data_dir=user_data_dir,
                headless=config.HEADLESS,
                slow_mo=config.SLOW_MO,
                viewport={"width": 1280, "height": 720}
            )

            context.add_init_script(JS_INICIALIZAR_MUTATION_OBSERVER)

            def configurar_interceptores(target_page):
                try:
                    def on_response(response):
                        url = response.url
                        if any(k in url for k in ["poker", "game", "api"]):
                            if "my_turn.mp3" in url:
                                print("🔊 [SEÑAL HTTP] ➔ ¡Es nuestro turno! (my_turn.mp3)")
                                tracker.http_turn_signal = True
                                tracker.registrar_evento("SONIDO", "Es mi turno (my_turn.mp3)")
                            elif "card_dealt.mp3" in url:
                                tracker.registrar_evento("SONIDO", "Cartas repartidas")
                            elif "my_player_won.mp3" in url:
                                print("🎉 [SEÑAL HTTP] ➔ ¡Hemos ganado la mano!")
                                tracker.registrar_evento("SONIDO", "¡Mano Ganada!")
                                tracker.finalizar_mano_actual("Mano Ganada (Bote cobrado)")
                            elif "chips_placed.mp3" in url or "action_bet.png" in url:
                                tracker.registrar_evento("ACCION_MESA", "Apuesta/Fichas colocadas")
                                if (tracker.opp_id or tracker.opp_nick) and (time.time() - tracker.hero_just_acted_ts > 1.5):
                                    profiler.registrar_accion_rival(tracker.opp_id, tracker.opp_nick, "BET", calle=tracker.current_street)
                            elif "player_fold.mp3" in url or "action_fold.png" in url:
                                tracker.registrar_evento("ACCION_MESA", "Rival hizo Fold")
                                if (tracker.opp_id or tracker.opp_nick) and (time.time() - tracker.hero_just_acted_ts > 1.5):
                                    profiler.registrar_accion_rival(tracker.opp_id, tracker.opp_nick, "FOLD", calle=tracker.current_street)
                            elif "player_check.mp3" in url or "action_check.png" in url:
                                tracker.registrar_evento("ACCION_MESA", "Jugador hizo Check")
                                if (tracker.opp_id or tracker.opp_nick) and (time.time() - tracker.hero_just_acted_ts > 1.5):
                                    profiler.registrar_accion_rival(tracker.opp_id, tracker.opp_nick, "CHECK", calle=tracker.current_street)
                            elif "cards-classic-assets" in url and url.endswith(".svg"):
                                card_name = url.split("/")[-1].replace(".svg", "").upper()
                                tracker.registrar_evento("CARTA_SVG", f"Carta cargada en mesa: {card_name}")

                    target_page.on("response", on_response)

                    def handle_websocket(ws):
                        print(f"🔌 [WEBSOCKET CONECTADO] {ws.url}")

                        def procesar_payload_ipoker(payload_str):
                            try:
                                if any(k in payload_str for k in ["playerStates", "pots", "cards", "roundNo", "gameCards", "nickname", "playerCode", "isDealer", "winningHand"]):
                                    start_idx = payload_str.find('{')
                                    end_idx = payload_str.rfind('}')
                                    if start_idx != -1 and end_idx != -1 and end_idx > start_idx:
                                        json_str = payload_str[start_idx:end_idx+1].replace('\\"', '"')
                                        data_json = json.loads(json_str)

                                        if "nickname" in data_json:
                                            tracker.opp_nick = data_json["nickname"]
                                        if "playerCode" in data_json:
                                            tracker.opp_id = str(data_json["playerCode"])

                                        if "roundNo" in data_json:
                                            ronda = int(data_json["roundNo"] or 1)
                                            calle_nueva = "PREFLOP" if ronda <= 1 else "POSTFLOP"
                                            if ronda == 1:
                                                if tracker.current_street == "POSTFLOP" or len(tracker.current_hand_history) > 3:
                                                    tracker.finalizar_mano_actual("Fin de mano previa")
                                                    if tracker.opp_id or tracker.opp_nick:
                                                        profiler.incrementar_manos_rival(tracker.opp_id, tracker.opp_nick)
                                                else:
                                                    tracker.community_cards.clear()
                                            tracker.current_round_no = ronda
                                            tracker.current_street = calle_nueva
                                            print(f"🔄 [iPoker WS DECODER] ➔ Ronda #{ronda} ({tracker.current_street})")
                                            tracker.registrar_evento("WS_RONDA", f"Calle/Ronda #{ronda} ({tracker.current_street})")

                                        if "gameCards" in data_json and isinstance(data_json["gameCards"], list):
                                            comm = [c for c in data_json["gameCards"] if isinstance(c, str) and c != 'X']
                                            if comm and tracker.current_round_no in (2, 3, 4):
                                                tracker.community_cards = comm

                                        if "cards" in data_json and isinstance(data_json["cards"], list):
                                            comm = [c for c in data_json["cards"] if isinstance(c, str) and c != 'X']
                                            if comm and tracker.current_round_no in (2, 3, 4):
                                                for c in comm:
                                                    if c not in tracker.community_cards and len(tracker.community_cards) < 5:
                                                        tracker.community_cards.append(c)
                                                print(f"🃏 [iPoker WS DECODER] ➔ Mesa Comunitaria: {tracker.community_cards}")

                                        if "playerStates" in data_json:
                                            for ps in data_json["playerStates"]:
                                                if "isDealer" in ps and ps["isDealer"]:
                                                    if "cards" in ps and any(c != 'X' for c in ps.get('cards', [])):
                                                        tracker.is_dealer = True
                                                        tracker.position_name = "BUTTON (Dealer)"
                                                        tracker.registrar_evento("WS_POSICION", "Hero es DEALER (Ventaja Posicional)")

                                                if "cards" in ps and isinstance(ps["cards"], list):
                                                    cards_list = [c for c in ps["cards"] if isinstance(c, str) and c != 'X']
                                                    # Solo guardar como nuestras cartas privadas la primera vez que recibimos 2 cartas en Preflop
                                                    if len(cards_list) == 2 and len(ps["cards"]) == 2 and tracker.current_street == "PREFLOP":
                                                        if not tracker.my_cards:
                                                            tracker.my_cards = cards_list
                                                            print(f"🎴 [iPoker WS DECODER] ➔ Mis Cartas: {cards_list}")
                                                            tracker.registrar_evento("WS_CARTAS", f"Mis cartas: {cards_list}")
                                                        elif cards_list != tracker.my_cards:
                                                            print(f"👀 [ALL-IN SHOWDOWN WS] ➔ Cartas Rival Reveladas: {cards_list}")
                                                            tracker.registrar_evento("SHOWDOWN_RIVAL", f"Rival muestra: {cards_list}")
                                                            if tracker.opp_id or tracker.opp_nick:
                                                                profiler.registrar_showdown(tracker.opp_id, tracker.opp_nick, " ".join(cards_list))
                                                                if analytics_logger is not None:
                                                                    try:
                                                                        perfil_est = profiler.obtener_perfil_explotativo(tracker.opp_id, tracker.opp_nick)
                                                                        analytics_logger.registrar_verificacion_showdown(tracker.opp_id, tracker.opp_nick, " ".join(cards_list), perfil_est)
                                                                    except Exception:
                                                                        pass
                                                    elif cards_list and tracker.current_round_no in (2, 3, 4) and len(ps["cards"]) in (1, 3) and "playerCode" not in ps:
                                                        for c in cards_list:
                                                            if c not in tracker.community_cards and len(tracker.community_cards) < 5:
                                                                tracker.community_cards.append(c)

                                        if "pots" in data_json:
                                            raw_pots = data_json["pots"]
                                            if isinstance(raw_pots, list):
                                                pot_sum = int(sum(float(p.get("sum", 0)) for p in raw_pots if isinstance(p, dict)))
                                            else:
                                                pot_sum = raw_pots
                                            tracker.last_pot = pot_sum
                                            print(f"💰 [iPoker WS DECODER] ➔ Bote Actualizado: {pot_sum}")
                                            tracker.registrar_evento("WS_BOTE", f"Bote: {pot_sum}")
                                        if "winningHand" in data_json or "showdown" in data_json:
                                            print(f"🃏 [SHOWDOWN WS] Cartas mostradas al final: {data_json}")
                                            tracker.registrar_evento("SHOWDOWN", f"Cartas reveladas: {data_json}")
                                            if tracker.opp_id or tracker.opp_nick:
                                                cartas_sd = data_json.get("winningHand") or data_json.get("showdown")
                                                profiler.registrar_showdown(tracker.opp_id, tracker.opp_nick, str(cartas_sd))
                            except Exception:
                                pass

                        def handle_recv(payload):
                            try:
                                p_str = payload.decode('utf-8', errors='ignore') if isinstance(payload, bytes) else str(payload)
                                procesar_payload_ipoker(p_str)
                            except Exception:
                                pass

                        def handle_sent(payload):
                            pass

                        ws.on("framereceived", handle_recv)
                        ws.on("framesent", handle_sent)

                    target_page.on("websocket", handle_websocket)
                except Exception:
                    pass

            page = context.pages[0] if context.pages else context.new_page()
            configurar_interceptores(page)

            def on_new_page(new_page):
                try:
                    new_page.wait_for_load_state("domcontentloaded", timeout=5000)
                except Exception:
                    pass
                configurar_interceptores(new_page)

            context.on("page", on_new_page)
            print("🕵️ Interceptor de WebSockets, Red y Sonidos activado globalmente.")

            # 1. Login inicial
            hacer_login(page)

            # 2. Navegación al juego
            page = navegar_al_juego(page)

            print("\n⚡ Bot de Póquer Autónomo Reactivo por Eventos ACTIVO.")
            print("Presiona Ctrl + C en la terminal para detener el bot.\n")

            # 3. Bucle reactivo continuo infinito (Juego automático continuo sin reinicios)
            ciclo = 1
            ultimo_turno_id = None
            ultimo_modal_id = None
            ultimo_tiempo_actividad = time.time()

            while True:
                try:
                    if page.is_closed():
                        print("🔄 Pestaña actual cerrada. Recuperando pestaña activa del contexto...")
                        pages_activas = [p for p in context.pages if not p.is_closed()]
                        if pages_activas:
                            page = pages_activas[-1]
                            page.bring_to_front()
                        else:
                            page = context.new_page()
                            configurar_interceptores(page)
                            hacer_login(page)

                        page = navegar_al_juego(page)
                        ultimo_tiempo_actividad = time.time()
                        continue

                    target_mesa = obtener_target_mesa(page)
                    esperar_sincronizacion_turno(target_mesa, timeout_max_ms=1200)

                    # Si el recuadro "¿Seguro que quieres foldear?" está en pantalla, clic inmediato en Check gratis
                    if resolver_popup_fold_check_gratis(target_mesa):
                        ultimo_tiempo_actividad = time.time()
                        time.sleep(0.4)
                        continue

                    dom_data = target_mesa.evaluate(JS_EXTRAER_ESTADO_DOM) or {}

                    # Si llegó la señal de audio my_turn.mp3 pero los botones del DOM aún están renderizándose, esperar hasta 450ms
                    if tracker.http_turn_signal and not dom_data.get('botones_visibles'):
                        try:
                            target_mesa.wait_for_selector("#FOLD, #CHECK, #CALL, #BET, #RAISE, .action-button", state="visible", timeout=450)
                        except Exception:
                            time.sleep(0.25)
                        dom_data = target_mesa.evaluate(JS_EXTRAER_ESTADO_DOM) or {}

                    es_mi_turno = bool(dom_data.get('es_mi_turno') or tracker.http_turn_signal)
                    if es_mi_turno:
                        ultimo_tiempo_actividad = time.time()
                        dom_data["es_mi_turno"] = True
                        if tracker.http_turn_signal:
                            tracker.http_turn_signal = False

                    dom_data["historial_mano"] = tracker.obtener_contexto_completo()
                    dom_data["perfil_rival"] = profiler.obtener_perfil_explotativo(tracker.opp_id, tracker.opp_nick)

                    mis_cartas = dom_data.get('mis_cartas', '') or " ".join(tracker.my_cards)
                    comunitarias = dom_data.get('cartas_comunitarias', '') or " ".join(tracker.community_cards)
                    if comunitarias and tracker.current_street == "PREFLOP":
                        tracker.current_street = "POSTFLOP"
                    bote = dom_data.get('bote', '') or tracker.last_pot or 0
                    botones = dom_data.get('botones_visibles', '') or dom_data.get('opciones_disponibles', '')
                    modales_o_botones = " | ".join(filter(None, [botones, dom_data.get('modales_visibles', '')]))

                    dom_data["mis_cartas"] = mis_cartas
                    dom_data["cartas_comunitarias"] = comunitarias
                    dom_data["bote"] = bote
                    dom_data["gto_preflop"] = clasificar_mano_preflop(mis_cartas)
                    dom_data["gto_math"] = calcular_pot_odds_y_sizing(bote, dom_data.get('a_igualar'), dom_data.get('mi_stack'))
                    dom_data["gto_textura"] = analizar_textura_mesa_y_blockers(mis_cartas, comunitarias)
                    dom_data["gto_frecuencia"] = generar_frecuencia_mixta_gto()
                    dom_data["gto_combinacion"] = evaluar_combinacion_mano_y_mesa(mis_cartas, comunitarias)

                    turno_id_actual = f"{mis_cartas}|{comunitarias}|{bote}|{botones}"

                    # CASO 1: ES NUESTRO TURNO Y AÚN NO HEMOS CONSULTADO A GEMINI PARA ESTE TURNO
                    if es_mi_turno and turno_id_actual != ultimo_turno_id:
                        ultimo_turno_id = turno_id_actual
                        ultimo_tiempo_actividad = time.time()

                        owner_page = getattr(target_mesa, 'page', page)
                        try:
                            if len(context.pages) > 1 and not owner_page.is_closed():
                                owner_page.bring_to_front()
                        except Exception:
                            pass
                        screenshot_bytes = owner_page.screenshot(type="jpeg", quality=75)

                        print(f"\n--- [Turno Hero #{ciclo}] 🎴 Mis Cartas: {mis_cartas or 'N/A'} | Mesa: {comunitarias or '-'} | Bote: {bote} ---")
                        print("🤖 Consultando a Gemini AI (1 consulta única, timeout máx 4.5s)...")

                        decision = None
                        decision_source = "gemini"
                        t0 = time.time()
                        try:
                            decision = consultar_gemini_con_timeout(agent, screenshot_bytes, dom_data=dom_data, timeout_seg=4.5)
                        except Exception as e:
                            print(f"⚠️ Excepción al consultar Gemini AI: {e}")

                        if not decision or decision.accion in ("WAIT", "START_GAME", "REPLAY"):
                            print("🛡️ [RESPALDO DE SEGURIDAD 0ms] Ejecutando acción defensiva rápida...")
                            decision = decidir_accion_rapida(dom_data)
                            decision_source = "gto_fallback_0ms"

                        dt = time.time() - t0
                        print(f"⚡ [DECISIÓN IA ({dt:.2f}s)]: {decision.accion} | Confianza: {decision.confianza:.2f} | {decision.evaluacion}")

                        accion_ejecutada = ejecutar_accion(target_mesa, decision, dom_data=dom_data)
                        if analytics_logger is not None:
                            try:
                                analytics_logger.registrar_decision(
                                    dom_data=dom_data,
                                    decision=decision,
                                    executed_action=accion_ejecutada,
                                    decision_source=decision_source,
                                    response_time_ms=dt * 1000.0,
                                    hand_number=tracker.hand_number,
                                    position_name=tracker.position_name,
                                    opp_id=tracker.opp_id,
                                    opp_nick=tracker.opp_nick,
                                )
                            except Exception:
                                pass
                        time.sleep(0.55)
                        ciclo += 1

                    # CASO 2: MODAL O MESA FINALIZADA DETECTADA (Fin de partida / Eliminado 'Vacío' / Replay / Sit-Out / Iniciar partida / PopupCheckGratis)
                    elif any(b in modales_o_botones for b in ['PopupCheckGratis', 'MesaFinalizadaVacia', 'Jugar de nuevo', 'Replay', 'Volver a jugar', 'Aceptar', 'Jugar Poker', 'Jugar']):
                        if 'PopupCheckGratis' in modales_o_botones:
                            resolver_popup_fold_check_gratis(target_mesa)
                            ultimo_tiempo_actividad = time.time()
                            continue
                        if 'MesaFinalizadaVacia' in modales_o_botones or modales_o_botones != ultimo_modal_id:
                            ultimo_modal_id = None if 'MesaFinalizadaVacia' in modales_o_botones else modales_o_botones
                            ultimo_tiempo_actividad = time.time()
                            print(f"🔄 [MODAL / MESA FINALIZADA DETECTADA ({modales_o_botones})] Reaccionando en automático para iniciar nueva partida...")

                            if 'Jugar de nuevo' in modales_o_botones or 'Replay' in modales_o_botones or 'Volver a jugar' in modales_o_botones:
                                if not clicar_texto_modal_universal(target_mesa, ["Jugar de nuevo", "Replay", "Volver a jugar"], "Botón Jugar de Nuevo"):
                                    intentar_click(target_mesa, config.SELECTORS.get("btn_replay", config.SELECTORS["replay"]), "Botón Jugar de Nuevo", timeout=2000)
                            elif 'Jugar Poker' in modales_o_botones or 'Jugar' in modales_o_botones:
                                if not intentar_click(target_mesa, config.SELECTORS["start_match_btn"], "Botón Jugar Poker", timeout=2000):
                                    clicar_texto_modal_universal(target_mesa, ["Jugar Poker", "Jugar"], "Botón Jugar Poker")
                            elif 'Aceptar' in modales_o_botones:
                                if not intentar_click(target_mesa, "#general-popup-button-action-OK", "Botón Aceptar Sit-out", timeout=1500):
                                    clicar_texto_modal_universal(target_mesa, ["Aceptar", "OK"], "Botón Aceptar")
                            elif 'MesaFinalizadaVacia' in modales_o_botones:
                                salir_de_mesa_vacia_o_finalizada(target_mesa, page, context)

                            time.sleep(1.8)
                            tracker.reset_mano()

                    # CASO 3: AUTORRECUPERACIÓN SI ESTAMOS EN EL LOBBY SIN ENTRAR A MESA HACE MÁS DE 15 SEGUNDOS
                    elif not es_mi_turno and (time.time() - ultimo_tiempo_actividad > 15):
                        ultimo_tiempo_actividad = time.time()
                        print("⏳ Tiempo de inactividad detectado en lobby. Re-ejecutando inicio de torneo automático...")
                        page = navegar_al_juego(page)

                    else:
                        time.sleep(0.05)

                except Exception as e:
                    print(f"ℹ️ Nota de autorrecuperación en el bucle: {e}")
                    time.sleep(0.5)

    finally:
        if analytics_logger is not None:
            try:
                analytics_logger.shutdown(wait=True)
            except Exception:
                pass
        bg_executor.shutdown(wait=False)

if __name__ == "__main__":
    main()
