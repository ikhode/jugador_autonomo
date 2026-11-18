"""
Inicialización de pruebas con limpieza explícita de motores SQLAlchemy y sesiones para eliminar ResourceWarnings de SQLite.
"""
import tracemalloc
import warnings
import gc
import sqlite3
import atexit

tracemalloc.start(25)
warnings.simplefilter("always", ResourceWarning)

def _cleanup_all_sqlalchemy_engines():
    try:
        from sqlalchemy.orm import close_all_sessions
        close_all_sessions()
    except Exception:
        pass

    try:
        from analytics_db import analytics_engine
        analytics_engine.dispose()
    except Exception:
        pass

    try:
        from opponents_profiler import engine as profiler_engine
        profiler_engine.dispose()
    except Exception:
        pass

    try:
        from poker_experience_memory import engine as memory_engine
        memory_engine.dispose()
    except Exception:
        pass

    gc.collect()

atexit.register(_cleanup_all_sqlalchemy_engines)
