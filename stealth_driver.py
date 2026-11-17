"""
Módulo de Sigilo Total y Evasión Anti-Detección para Playwright (Stealth Driver).
Implements complete bot-detection evasion and human behavior simulation:
1. Evasión a nivel motor de Chromium (navigator.webdriver, automation flags, Chrome runtime).
2. Simulación de trayectoria curva Bézier humana de ratón (Human Mouse Bezier Curves + Micro-Jitter).
3. Clics humanos con desplazamiento suave, posicionado aleatorizado en botón y tiempo de posado (hover).
4. Tipeo de texto humano con latencia realista entre pulsaciones de tecla (human typing).
5. Pausas de reflexión de pensamiento humano según complejidad de mano (Human Reaction Timing).
"""
import math
import random
import time
from typing import Any, List, Optional, Tuple

JS_STEALTH_INJECTION = """
(() => {
    // 1. Ocultar navigator.webdriver
    try {
        Object.defineProperty(navigator, 'webdriver', {
            get: () => undefined,
        });
    } catch (e) {}

    // 2. Mockear window.chrome y runtime real de usuario
    try {
        window.chrome = {
            runtime: {
                OnInstalledReason: { INSTALL: 'install', UPDATE: 'update' },
                OnRestartRequiredReason: { APP_UPDATE: 'app_update' },
                PlatformArch: { ARM: 'arm', X86_32: 'x86-32', X86_64: 'x86-64' },
                PlatformNaclArch: { ARM: 'arm', X86_32: 'x86-32', X86_64: 'x86-64' },
                PlatformOs: { ANDROID: 'android', CROS: 'cros', LINUX: 'linux', MAC: 'mac', WIN: 'win' },
                connect: () => {},
                sendMessage: () => {},
            },
            loadTimes: () => {},
            csi: () => {},
            app: {},
        };
    } catch (e) {}

    // 3. Mascarar variables internas de Playwright / CDP / CDC / Puppeteer / Selenium
    try {
        const cleanProps = ['cdc_adoQpoasndf_', '__playwright', '__pw_debug', '_phantom', '__nightmare', 'callPhantom', '_Selenium_IDE_Recorder'];
        for (const prop of cleanProps) {
            delete window[prop];
        }
    } catch (e) {}

    // 4. Hardware Spoofing (PC de Escritorio real de 8 núcleos / 8GB RAM)
    try {
        Object.defineProperty(navigator, 'hardwareConcurrency', { get: () => 8 });
        Object.defineProperty(navigator, 'deviceMemory', { get: () => 8 });
        Object.defineProperty(navigator, 'maxTouchPoints', { get: () => 0 });
    } catch (e) {}

    // 5. WebGL Vendor & Renderer Spoofing (NVIDIA GeForce RTX GPU Real)
    try {
        const getParameter = WebGLRenderingContext.prototype.getParameter;
        WebGLRenderingContext.prototype.getParameter = function(parameter) {
            if (parameter === 37445) return 'Google Inc. (NVIDIA)';
            if (parameter === 37446) return 'ANGLE (NVIDIA, NVIDIA GeForce RTX 3060 Direct3D11 vs_5_0 ps_5_0)';
            return getParameter.apply(this, arguments);
        };
        if (window.WebGL2RenderingContext) {
            const getParameter2 = WebGL2RenderingContext.prototype.getParameter;
            WebGL2RenderingContext.prototype.getParameter = function(parameter) {
                if (parameter === 37445) return 'Google Inc. (NVIDIA)';
                if (parameter === 37446) return 'ANGLE (NVIDIA, NVIDIA GeForce RTX 3060 Direct3D11 vs_5_0 ps_5_0)';
                return getParameter2.apply(this, arguments);
            };
        }
    } catch (e) {}

    // 6. Evasión de permisos de notificación
    try {
        const originalQuery = window.navigator.permissions.query;
        window.navigator.permissions.query = (parameters) => (
            parameters.name === 'notifications' ?
                Promise.resolve({ state: Notification.permission }) :
                originalQuery(parameters)
        );
    } catch (e) {}

    // 7. Normalizar navigator.languages y plugins
    try {
        Object.defineProperty(navigator, 'languages', {
            get: () => ['es-ES', 'es', 'en-US', 'en'],
        });
        Object.defineProperty(navigator, 'plugins', {
            get: () => [
                { name: 'Chrome PDF Plugin', filename: 'internal-pdf-viewer', description: 'Portable Document Format' },
                { name: 'Chrome PDF Viewer', filename: 'mhjfbobfdafljhffepjhjldnnbfbddfd', description: '' },
                { name: 'Native Client', filename: 'internal-nacl-plugin', description: '' },
            ],
        });
    } catch (e) {}
})();
"""

STEALTH_CHROMIUM_ARGS = [
    "--disable-blink-features=AutomationControlled",
    "--disable-infobars",
    "--no-sandbox",
    "--disable-dev-shm-usage",
    "--disable-browser-side-navigation",
    "--disable-features=IsolateOrigins,site-per-process",
    "--disable-features=BlockInsecurePrivateNetworkRequests",
    "--ignore-certificate-errors",
]


def generar_curva_bezier_humana(
    x_start: float,
    y_start: float,
    x_end: float,
    y_end: float,
    num_points: int = 15,
) -> List[Tuple[float, float]]:
    """
    Genera puntos de trayectoria curva Bézier cúbica simulando el movimiento natural del brazo/ratón humano
    con aceleración, desaceleración y micro-variaciones (jitter).
    """
    # Puntos de control aleatorizados en el espacio intermedio
    dist = math.hypot(x_end - x_start, y_end - y_start)
    dev = max(10.0, dist * random.uniform(0.15, 0.35))

    cx1 = x_start + (x_end - x_start) * random.uniform(0.2, 0.4) + random.uniform(-dev, dev)
    cy1 = y_start + (y_end - y_start) * random.uniform(0.2, 0.4) + random.uniform(-dev, dev)

    cx2 = x_start + (x_end - x_start) * random.uniform(0.6, 0.8) + random.uniform(-dev, dev)
    cy2 = y_start + (y_end - y_start) * random.uniform(0.6, 0.8) + random.uniform(-dev, dev)

    puntos: List[Tuple[float, float]] = []
    n = max(5, int(num_points))

    for i in range(n + 1):
        t = i / float(n)
        # Función Bézier cúbica B(t) = (1-t)^3*P0 + 3(1-t)^2*t*P1 + 3(1-t)*t^2*P2 + t^3*P3
        u = 1.0 - t
        tt = t * t
        uu = u * u
        uuu = uu * u
        ttt = tt * t

        x = uuu * x_start + 3 * uu * t * cx1 + 3 * u * tt * cx2 + ttt * x_end
        y = uuu * y_start + 3 * uu * t * cy1 + 3 * u * tt * cy2 + ttt * y_end

        # Añadir micro-jitter (ruido leve del pulso humano) en puntos intermedios
        if 0 < i < n:
            x += random.uniform(-1.5, 1.5)
            y += random.uniform(-1.5, 1.5)

        puntos.append((round(x, 1), round(y, 1)))

    return puntos


def mover_mouse_humano(page: Any, x_dest: float, y_dest: float) -> None:
    """Mueve el cursor del ratón hacia las coordenadas destino siguiendo una trayectoria Bézier humana."""
    try:
        cur_pos = getattr(page, "_last_mouse_pos", (random.uniform(100, 600), random.uniform(100, 400)))
        x_start, y_start = cur_pos

        dist = math.hypot(y_dest - y_start, x_dest - x_start)
        if dist < 5.0:
            page.mouse.move(x_dest, y_dest)
            page._last_mouse_pos = (x_dest, y_dest)
            return

        num_pts = max(8, min(35, int(dist / 25.0)))
        puntos = generar_curva_bezier_humana(x_start, y_start, x_dest, y_dest, num_points=num_pts)

        for px, py in puntos:
            page.mouse.move(px, py)
            time.sleep(random.uniform(0.003, 0.012))

        page._last_mouse_pos = (x_dest, y_dest)
    except Exception:
        try:
            page.mouse.move(x_dest, y_dest)
        except Exception:
            pass


def hacer_clic_humano(target_page_or_frame: Any, locator_or_element: Any, descripcion: str = "") -> bool:
    """
    Realiza un clic humano completo e invisible:
    1. Despacha la secuencia completa de eventos nativos DOM (pointerdown, mousedown, pointerup, mouseup, click).
    2. Calcula el bounding box del elemento con aleatorización interna (inner 60%).
    3. Mueve el ratón por curva Bézier.
    4. Posado humano (hover de 40ms-120ms).
    5. Clic físico de ratón Playwright.
    """
    try:
        # Despachar eventos nativos en el elemento DOM antes o durante el clic físico
        try:
            locator_or_element.evaluate("""(el) => {
                if (!el) return;
                const rect = el.getBoundingClientRect();
                const x = rect.left + rect.width * 0.5;
                const y = rect.top + rect.height * 0.5;
                const opts = { bubbles: true, cancelable: true, view: window, clientX: x, clientY: y };
                el.dispatchEvent(new PointerEvent('pointerdown', opts));
                el.dispatchEvent(new MouseEvent('mousedown', opts));
                el.dispatchEvent(new PointerEvent('pointerup', opts));
                el.dispatchEvent(new MouseEvent('mouseup', opts));
                el.dispatchEvent(new MouseEvent('click', opts));
            }""")
        except Exception:
            pass

        box = locator_or_element.bounding_box()
        if not box or box["width"] <= 0 or box["height"] <= 0:
            locator_or_element.click(force=True, timeout=1500)
            return True

        cx = box["x"] + box["width"] * random.uniform(0.25, 0.75)
        cy = box["y"] + box["height"] * random.uniform(0.25, 0.75)

        page = getattr(target_page_or_frame, "page", target_page_or_frame)
        if hasattr(page, "mouse"):
            mover_mouse_humano(page, cx, cy)
            time.sleep(random.uniform(0.04, 0.12))
            page.mouse.click(cx, cy)
        else:
            locator_or_element.click(timeout=1500)

        print(f"✅ [CLIC HUMANO OK] Clic en {descripcion or 'elemento'} en pos ({cx:.0f}, {cy:.0f})")
        return True
    except Exception as e:
        try:
            locator_or_element.click(force=True, timeout=1500)
            return True
        except Exception:
            return False


def tipear_texto_humano(target_page_or_frame: Any, element_locator: Any, texto: str) -> None:
    """Llenar un campo de entrada mediante pulsaciones humanas con latencia aleatorizada por tecla."""
    try:
        element_locator.focus()
        time.sleep(random.uniform(0.08, 0.20))

        # Seleccionar texto previo y borrar si existe
        element_locator.fill("")
        time.sleep(random.uniform(0.05, 0.12))

        for char in str(texto):
            element_locator.press(char)
            # Latencia aleatorizada entre teclas (40ms - 110ms)
            time.sleep(random.uniform(0.04, 0.11))

        time.sleep(random.uniform(0.08, 0.18))
    except Exception:
        try:
            element_locator.fill(str(texto))
        except Exception:
            pass


def generar_delay_pensamiento_humano(
    accion: str = "CHECK",
    calle: str = "PREFLOP",
    es_mano_compleja: bool = False,
) -> float:
    """
    Calcula una pausa de reflexión realista con distribución gaussiana/log-normal:
    - Snap Fold / Check simple: 0.6s - 1.4s
    - Decisión estándar (Call / Raise pequeño): 1.4s - 3.2s
    - Pensamiento profundo (Check-Raise, All-in, decisiones complejas en River): 3.2s - 6.5s
    """
    act = str(accion or "CHECK").upper()
    calle_u = str(calle or "PREFLOP").upper()

    if act == "FOLD" and calle_u == "PREFLOP":
        base = random.uniform(0.6, 1.3)
    elif act in ("CHECK", "CALL") and not es_mano_compleja:
        base = random.uniform(1.2, 2.8)
    elif act in ("RAISE", "BET"):
        base = random.uniform(2.2, 4.5)
    elif act == "ALL_IN" or es_mano_compleja or calle_u == "RIVER":
        base = random.uniform(3.5, 6.2)
    else:
        base = random.uniform(1.5, 3.5)

    # Añadir pequeña varianza aleatoria (jitter positivo)
    delay_final = max(0.5, base + random.gauss(0, 0.25))
    return round(delay_final, 2)


def simular_deriva_mouse_pasivo(page: Any) -> None:
    """
    Simula micro-movimientos pasivos e imperceptibles del ratón (5px-18px) mientras Hero espera el turno,
    emulando la mano del jugador descansando sobre el ratón en la mesa.
    """
    if not page or not hasattr(page, "mouse") or random.random() > 0.35:
        return
    try:
        cur_pos = getattr(page, "_last_mouse_pos", (400.0, 300.0))
        cx, cy = cur_pos
        dx = random.uniform(-15.0, 15.0)
        dy = random.uniform(-15.0, 15.0)
        nx = max(50.0, min(1200.0, cx + dx))
        ny = max(50.0, min(700.0, cy + dy))
        mover_mouse_humano(page, nx, ny)
    except Exception:
        pass
