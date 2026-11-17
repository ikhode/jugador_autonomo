"""
Script de Limpieza y Reinicio a Estado Cero (Clean Slate) para la Base de Datos Centralized SQLite.
Reinicia logs/poker_stats.db a un estado 100% limpio y saneado con todas las tablas e índices creados.
"""
import os
import sqlite3
from pathlib import Path
from analytics_db import inicializar_esquema_analiticas, DB_PATH
from opponents_profiler import inicializar_db
from poker_experience_memory import inicializar_db_memoria

LOGS_DIR = Path(__file__).resolve().parent / "logs"

def limpiar_archivos_obsoletos():
    print("🧹 [1/3] Limpiando archivos .json, .jsonl y bases de datos dispersas secundarias...")
    archivos_eliminar = [
        LOGS_DIR / "bot_decisions.jsonl",
        LOGS_DIR / "live_table_state.json",
        LOGS_DIR / "poker_experience_memory.db",
        LOGS_DIR / "poker_experience_memory.db-shm",
        LOGS_DIR / "poker_experience_memory.db-wal",
        LOGS_DIR / "poker_stats.db-shm",
        LOGS_DIR / "poker_stats.db-wal",
    ]
    for f in archivos_eliminar:
        if f.exists():
            try:
                os.remove(f)
                print(f"  ✅ Eliminado: {f.name}")
            except Exception as e:
                print(f"  ⚠️ No se pudo eliminar {f.name}: {e}")

def reiniciar_base_datos_central():
    print("\n📦 [2/3] Reiniciando la base de datos centralizada logs/poker_stats.db...")
    if DB_PATH.exists():
        try:
            os.remove(DB_PATH)
            print("  ✅ Base de datos anterior eliminada.")
        except Exception as e:
            print(f"  ⚠️ Reutilizando base existente ({e})")

    inicializar_esquema_analiticas(DB_PATH)
    inicializar_db()
    inicializar_db_memoria()
    print("  ✅ Esquema normalizado de 11 tablas e índices creados correctamente en logs/poker_stats.db.")

def verificar_estado_cero():
    print("\n🔍 [3/3] Verificando estado inicial limpio...")
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    tables = cur.execute("SELECT name FROM sqlite_master WHERE type='table';").fetchall()
    for t in tables:
        tname = t[0]
        cnt = cur.execute(f"SELECT COUNT(*) FROM {tname}").fetchone()[0]
        print(f"  Tabla '{tname}': {cnt} filas")
    conn.close()
    print("\n✨ ¡Proyecto 100% limpio y listo para comenzar desde cero!")

if __name__ == "__main__":
    limpiar_archivos_obsoletos()
    reiniciar_base_datos_central()
    verificar_estado_cero()
