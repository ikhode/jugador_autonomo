"""
Módulo de Gestión de Navegador, CDP y Automatización Playwright (Browser Manager).
Encapsula la inicialización de contexto persistente con evasión stealth, reconexión CDP y flujos de login/navegación.
"""
import os
import time
from typing import Any, Optional, Tuple
import config
from stealth_driver import STEALTH_CHROMIUM_ARGS, JS_STEALTH_INJECTION
from dom_parser import JS_INICIALIZAR_MUTATION_OBSERVER, esperar_carga_dom, obtener_target_mesa


def intentar_click(target: Any, selector: str, descripcion: str, timeout: int = 4000) -> bool:
    from stealth_driver import hacer_clic_humano
    selectores = [s.strip() for s in selector.split(",") if s.strip()]
    for sel in selectores:
        try:
            elem = target.locator(sel).first
            if elem.is_visible():
                elem.wait_for(state="visible", timeout=500)
                if hacer_clic_humano(target, elem, descripcion):
                    return True
        except Exception:
            pass

    if timeout <= 0:
        return False

    timeout_por_sel = max(300, int(timeout / max(1, len(selectores))))
    for sel in selectores:
        try:
            target.wait_for_selector(sel, state="visible", timeout=timeout_por_sel)
            elem = target.locator(sel).first
            if elem.is_visible():
                if hacer_clic_humano(target, elem, descripcion):
                    return True
        except Exception:
            continue
    return False


def obtener_o_crear_contexto(p: Any, user_data_dir: str) -> Tuple[Any, bool]:
    cdp_port = getattr(config, "CDP_PORT", 9222)
    cdp_url = f"http://127.0.0.1:{cdp_port}"

    try:
        import urllib.request
        urllib.request.urlopen(f"{cdp_url}/json/version", timeout=1.0)
        print(f"⚡ [RECONEXIÓN CDP OK] Navegador Chromium en ejecución detectado (puerto {cdp_port}). Reanudando control instantáneo...")
        browser = p.chromium.connect_over_cdp(cdp_url)
        context = browser.contexts[0] if browser.contexts else browser.new_context()
        context.add_init_script(JS_STEALTH_INJECTION)
        return context, True
    except Exception:
        pass

    print(f"🕵️ [STEALTH MODE] Lanzando Chromium Persistente con evasión anti-detección (puerto CDP {cdp_port})...")
    args = [f"--remote-debugging-port={cdp_port}"] + STEALTH_CHROMIUM_ARGS
    context = p.chromium.launch_persistent_context(
        user_data_dir=user_data_dir,
        headless=config.HEADLESS,
        slow_mo=config.SLOW_MO,
        viewport={"width": 1280, "height": 720},
        user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
        args=args
    )
    context.add_init_script(JS_STEALTH_INJECTION)
    return context, False


def hacer_login(page: Any) -> None:
    print("\n🔐 --- INICIANDO PROCESO DE AUTENTICACIÓN ---")
    try:
        page.goto(config.LOGIN_URL, timeout=30000)
        esperar_carga_dom(page)
    except Exception as e:
        print(f"⚠️ Error cargando LOGIN_URL ({config.LOGIN_URL}): {e}")

    try:
        intentar_click(page, config.SELECTORS["btn_acceder"], "Botón Acceder / Login", timeout=4000)
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


def es_mesa_activa_o_turno(page: Any) -> bool:
    try:
        pages = page.context.pages
        for p in pages:
            if p.is_closed():
                continue
            for frame in p.frames:
                try:
                    activa = frame.evaluate("""() => {
                        const esVisible = (el) => !!(el && (el.offsetWidth > 0 || el.offsetHeight > 0) && window.getComputedStyle(el).display !== 'none');
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


def navegar_al_juego(page: Any) -> Any:
    print("\n🎰 --- VERIFICANDO ESTADO DE MESA Y LOBBY ---")

    if es_mesa_activa_o_turno(page):
        print("🎮 ¡RECONEXIÓN A PARTIDA EN CURSO DETECTADA! Omitiendo pasos de navegación...")
        return page

    try:
        cliente_poker_abierto = False
        for p in reversed(page.context.pages):
            if not p.is_closed() and any(k in (p.url or "") for k in ["games.codere", "poker/web", "WebLauncher", "hostedMode"]):
                page = p
                page.bring_to_front()
                cliente_poker_abierto = True
                break

        if not cliente_poker_abierto:
            print("🎰 Paso 1: Haciendo clic en la sección de Poker...")
            if not intentar_click(page, config.SELECTORS["poker_nav_link"], "Enlace menú Poker", timeout=8000):
                page.reload()
                esperar_carga_dom(page)
                intentar_click(page, config.SELECTORS["poker_nav_link"], "Enlace menú Poker (Reintento)", timeout=5000)

            esperar_carga_dom(page)

            print("🚪 Paso 2: Abriendo lobby / cliente de Poker...")
            if not intentar_click(page, config.SELECTORS["btn_open_poker"], "Botón Abrir Poker", timeout=10000):
                intentar_click(page, config.SELECTORS["btn_open_poker"], "Botón Abrir Poker (Reintento)", timeout=5000)

            page.wait_for_timeout(3000)
            for p in reversed(page.context.pages):
                if not p.is_closed() and any(k in (p.url or "") for k in ["games.codere", "poker/web", "WebLauncher", "hostedMode"]):
                    page = p
                    page.bring_to_front()
                    break

        print("🎯 Paso 3: Seleccionando categoría de juego de póker...")
        target_mesa = obtener_target_mesa(page)
        intentar_click(target_mesa, config.SELECTORS["select_game_card"], "Tarjeta de Juego / Categoría Cash Game", timeout=4000)
        page.wait_for_timeout(1500)

        print("🚀 Paso 4: Ingresando a la mesa de juego...")
        if not intentar_click(target_mesa, config.SELECTORS["start_match_btn"], "Botón Jugar Poker", timeout=3000):
            clicar_texto_modal_universal(target_mesa, ["Jugar Poker", "Jugar", "Play"], "Botón Jugar Poker (Texto)")

        page.wait_for_timeout(4000)
    except Exception as e:
        print(f"⚠️ Nota durante navegación: {e}")

    return page


def clicar_texto_modal_universal(target: Any, textos: list, descripcion: str) -> bool:
    try:
        for t in textos:
            clicked = target.evaluate(f"""(texto) => {{
                const els = Array.from(document.querySelectorAll('button, a, div.btn, span, [role="button"]'));
                const match = els.find(el => {{
                    const visible = (el.offsetWidth > 0 || el.offsetHeight > 0) && window.getComputedStyle(el).display !== 'none';
                    const txt = (el.innerText || el.textContent || '').trim();
                    return visible && txt === texto;
                }}) || els.find(el => {{
                    const visible = (el.offsetWidth > 0 || el.offsetHeight > 0) && window.getComputedStyle(el).display !== 'none';
                    const txt = (el.innerText || el.textContent || '').trim();
                    return visible && txt.includes(texto);
                }});
                if (match) {{
                    match.scrollIntoView({{ block: 'center' }});
                    match.click();
                    return true;
                }}
                return false;
            }}""", t)
            if clicked:
                print(f"✅ [CLIC MODAL OK] Clic universal en '{t}' ({descripcion}).")
                return True
    except Exception:
        pass
    return False
