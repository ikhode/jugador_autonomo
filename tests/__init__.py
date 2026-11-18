"""
Diagnóstico avanzado de fugas de SQLite mediante tracemalloc y volcado de trazas de creación.
"""
import tracemalloc
import warnings
import gc
import sqlite3
import atexit

tracemalloc.start(25)
warnings.simplefilter("always", ResourceWarning)

def _report_unclosed_sqlite():
    gc.collect()
    print("\n" + "=" * 90)
    print("🔍 [TRACEMALLOC DIAGNÓSTICO PROFUNDO] Rastro de Conexiones SQLite no cerradas:")
    print("=" * 90)
    count = 0
    for obj in gc.get_objects():
        try:
            if isinstance(obj, sqlite3.Connection):
                count += 1
                tb = tracemalloc.get_object_traceback(obj)
                if tb:
                    print(f"\n--- Conexión SQLite no cerrada #{count} ---")
                    for frame in tb:
                        print(f"  {frame.filename}:{frame.lineno}: {frame.line}")
                else:
                    print(f"\n--- Conexión SQLite no cerrada #{count} (sin traza tracemalloc disponible) ---")
        except Exception:
            pass
    if count == 0:
        print("✅ No se detectaron conexiones sqlite3.Connection huérfanas.")
    print("=" * 90)

atexit.register(_report_unclosed_sqlite)
