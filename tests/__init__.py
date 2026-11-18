"""
Inicialización de pruebas y diagnóstico de fugas de conexiones SQLite mediante tracemalloc.
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
    unclosed_sqlite = 0
    for obj in gc.get_objects():
        try:
            if isinstance(obj, sqlite3.Connection):
                unclosed_sqlite += 1
        except Exception:
            pass
    if unclosed_sqlite > 0:
        print(f"\n🔍 [TRACEMALLOC DIAGNÓSTICO] Conexiones sqlite3.Connection pendientes de recolección al finalizar: {unclosed_sqlite}")

atexit.register(_report_unclosed_sqlite)
