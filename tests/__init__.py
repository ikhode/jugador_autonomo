# tests/__init__.py
"""
Instrumentación de la suite de tests para localizar conexiones SQLite
que no se cierrates correctamente.

Qué hace
--------
1. Arranca `tracemalloc` ANTES de que se cargue cualquier test, para que
   Python enriquezca automáticamente los `ResourceWarning` con la traza
   de asignación.
2. Parchea `sqlite3.connect` para registrar, en el momento de la creación,
   la traza exacta (archivo:línea + cadena de llamadas) de cada conexión.
3. Parchea `sqlite3.Connection.close` para marcar conexiones cerradas.
4. Al terminar la sesión (atexit), vuelca un informe con:
   - conexiones que siguen abiertas,
   - conexiones que el GC recolectó sin cierre explícito,
   - la traza de creación de cada una (frames de usuario resaltados con >>>).

Uso
---
    python -m unittest discover tests

Variables de entorno
--------------------
    SQLITE_TRACE_DISABLE=1   desactiva la instrumentación
    SQLITE_TRACE_FRAMES=N    frames guardados por tracemalloc (default: 25)
"""

from __future__ import annotations

import atexit
import os
import sqlite3
import sys
import traceback
import tracemalloc
import warnings
import weakref

# ---------- Configuración ----------
_ORIGINAL_CONNECT = sqlite3.connect
_ORIGINAL_CLOSE = sqlite3.Connection.close
_REGISTRY: dict[int, dict] = {}
_STDLIB_PREFIXES = tuple(p for p in (sys.base_prefix, sys.prefix) if p)


def _is_library(filename: str) -> bool:
    """True si el frame pertenece a stdlib / site-packages / código congelado."""
    if not filename or filename.startswith("<frozen"):
        return True
    if "site-packages" in filename or "dist-packages" in filename:
        return True
    return filename.startswith(_STDLIB_PREFIXES)


# ---------- Parches ----------
def _patched_connect(*args, **kwargs):
    """Registra la traza de creación ANTES de crear la conexión."""
    stack = traceback.extract_stack()[:-1]  # sin el frame de este wrapper
    conn = _ORIGINAL_CONNECT(*args, **kwargs)
    _REGISTRY[id(conn)] = {
        "ref": weakref.ref(conn),
        "args": args,
        "kwargs": {k: v for k, v in kwargs.items() if k != "factory"},
        "stack": stack,
        "closed": False,
    }
    return conn


def _patched_close(self):
    info = _REGISTRY.get(id(self))
    if info is not None:
        info["closed"] = True
    return _ORIGINAL_CLOSE(self)


def _install():
    sqlite3.connect = _patched_connect
    try:
        sqlite3.Connection.close = _patched_close
    except TypeError:
        # Algunas builds de sqlite3 no permiten parchear el tipo C.
        # En ese caso, caemos al chequeo final con SELECT 1.
        pass

    tracemalloc.start(int(os.environ.get("SQLITE_TRACE_FRAMES", "25")))
    warnings.simplefilter("always", ResourceWarning)
    atexit.register(_dump_report)


# ---------- Utilidades ----------
def _safe_tracemalloc_tb(obj):
    try:
        return tracemalloc.get_object_traceback(obj)
    except Exception:
        return None


def _is_open(conn) -> bool:
    try:
        conn.execute("SELECT 1")
        return True
    except sqlite3.ProgrammingError:
        return False
    except sqlite3.Error:
        # Error distinto al de "database closed": asumimos abierta
        # para no ocultar posibles fugas.
        return True


def _find_trigger_frame(stack):
    """Devuelve el frame de usuario más profundo (el que originó la conexión)."""
    for frame in reversed(stack):
        if not _is_library(frame.filename):
            return frame
    return stack[-1] if stack else None


def _format_stack(stack) -> str:
    trigger = _find_trigger_frame(stack)
    out = []
    for frame in stack:
        if frame is trigger:
            marker = ">>>"
        elif _is_library(frame.filename):
            marker = "   "
        else:
            marker = " ->"
        src = (frame.line or "").strip()
        out.append(f"   {marker} {frame.filename}:{frame.lineno} en {frame.name}()")
        if src:
            out.append(f"          {src}")
    return "\n".join(out)


def _format_tracemalloc_tb(tb) -> str:
    return "\n".join(f"     {line}" for line in tb.format())


# ---------- Informe final ----------
def _dump_report():
    import gc
    gc.collect()

    abiertas: list[tuple[object, dict]] = []
    recolectadas: list[dict] = []

    for key, info in list(_REGISTRY.items()):
        conn = info["ref"]()
        if conn is None:
            if not info["closed"]:
                recolectadas.append(info)
            continue
        if info["closed"]:
            continue
        if _is_open(conn):
            abiertas.append((conn, info))

    if not abiertas and not recolectadas:
        print(
            "\n[tests.tracemalloc] OK: no se detectaron conexiones "
            "SQLite sin cerrar.",
            file=sys.stderr,
        )
        return

    total = len(abiertas) + len(recolectadas)
    print(
        f"\n[tests.tracemalloc] RESULTADO: {total} conexión(es) SQLite "
        f"sin cerrar ({len(abiertas)} aún abiertas, "
        f"{len(recolectadas)} recolectadas por GC sin cierre).",
        file=sys.stderr,
    )

    for i, (conn, info) in enumerate(abiertas, 1):
        print(f"\n--- Fuga #{i} (aún abierta) " + "-" * 30, file=sys.stderr)
        print(f"  args:   {info['args']}", file=sys.stderr)
        print(f"  kwargs: {info['kwargs']}", file=sys.stderr)
        print("  traza de creación (sqlite3.connect):", file=sys.stderr)
        print(_format_stack(info["stack"]), file=sys.stderr)
        tm_tb = _safe_tracemalloc_tb(conn)
        if tm_tb is not None:
            print("  traza de asignación (tracemalloc):", file=sys.stderr)
            print(_format_tracemalloc_tb(tm_tb), file=sys.stderr)

    for j, info in enumerate(recolectadas, len(abiertas) + 1):
        print(
            f"\n--- Fuga #{j} (recolectada sin cierre) " + "-" * 20,
            file=sys.stderr,
        )
        print(f"  args:   {info['args']}", file=sys.stderr)
        print(f"  kwargs: {info['kwargs']}", file=sys.stderr)
        print("  traza de creación:", file=sys.stderr)
        print(_format_stack(info["stack"]), file=sys.stderr)

    print(
        "\n[tests.tracemalloc] Sugerencia: usar `contextlib.closing(conn)`, "
        "`conn.close()` explícito o `engine.dispose()` en tearDown.",
        file=sys.stderr,
    )


# ---------- Arranque ----------
if os.environ.get("SQLITE_TRACE_DISABLE") != "1":
    _install()
