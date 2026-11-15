"""
Módulo Perfilador de Oponentes Explotativo (Poker HUD Database).
Almacena y calcula estadísticas en tiempo real por cada oponente (VPIP, PFR, Agresión, Folds a Raise).
"""
import sqlite3
from pathlib import Path
from typing import Dict, Any

DB_PATH = Path("logs/poker_stats.db")


def inicializar_db():
    DB_PATH.parent.mkdir(exist_ok=True)
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS oponentes (
                player_id TEXT PRIMARY KEY,
                nickname TEXT,
                manos_jugadas INTEGER DEFAULT 0,
                preflop_calls INTEGER DEFAULT 0,
                preflop_raises INTEGER DEFAULT 0,
                folds_ante_raise INTEGER DEFAULT 0,
                postflop_bets INTEGER DEFAULT 0,
                postflop_checks INTEGER DEFAULT 0,
                ultimas_cartas_mostradas TEXT,
                ultima_actualizacion TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.commit()


inicializar_db()


class OpponentProfiler:
    def __init__(self):
        inicializar_db()

    def registrar_accion_rival(self, player_id: str, nickname: str, accion: str, calle: str = "PREFLOP"):
        if not player_id and not nickname:
            return
        target_id = str(player_id or nickname)
        nick = nickname or target_id

        with sqlite3.connect(DB_PATH) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT manos_jugadas FROM oponentes WHERE player_id = ?", (target_id,))
            row = cursor.fetchone()
            if not row:
                cursor.execute("""
                    INSERT INTO oponentes (player_id, nickname, manos_jugadas)
                    VALUES (?, ?, 1)
                """, (target_id, nick))
            elif nickname:
                cursor.execute("UPDATE oponentes SET nickname = ?, ultima_actualizacion = CURRENT_TIMESTAMP WHERE player_id = ?", (nickname, target_id))

            accion_upper = accion.upper()
            calle_upper = (calle or "PREFLOP").upper()

            if calle_upper == "PREFLOP":
                if any(k in accion_upper for k in ("RAISE", "BET", "ALLIN", "ALL_IN")):
                    cursor.execute("UPDATE oponentes SET preflop_raises = preflop_raises + 1 WHERE player_id = ?", (target_id,))
                elif "CALL" in accion_upper:
                    cursor.execute("UPDATE oponentes SET preflop_calls = preflop_calls + 1 WHERE player_id = ?", (target_id,))
                elif "FOLD" in accion_upper:
                    cursor.execute("UPDATE oponentes SET folds_ante_raise = folds_ante_raise + 1 WHERE player_id = ?", (target_id,))
            else:  # POSTFLOP
                if any(k in accion_upper for k in ("RAISE", "BET", "ALLIN", "ALL_IN")):
                    cursor.execute("UPDATE oponentes SET postflop_bets = postflop_bets + 1 WHERE player_id = ?", (target_id,))
                elif "CHECK" in accion_upper or "CALL" in accion_upper:
                    cursor.execute("UPDATE oponentes SET postflop_checks = postflop_checks + 1 WHERE player_id = ?", (target_id,))
                elif "FOLD" in accion_upper:
                    cursor.execute("UPDATE oponentes SET folds_ante_raise = folds_ante_raise + 1 WHERE player_id = ?", (target_id,))

            conn.commit()

    def incrementar_manos_rival(self, player_id: str, nickname: str = ""):
        if not player_id and not nickname:
            return
        target_id = str(player_id or nickname)
        nick = nickname or target_id
        with sqlite3.connect(DB_PATH) as conn:
            conn.execute("""
                INSERT INTO oponentes (player_id, nickname, manos_jugadas)
                VALUES (?, ?, 1)
                ON CONFLICT(player_id) DO UPDATE SET
                    nickname = COALESCE(NULLIF(excluded.nickname, ''), oponentes.nickname),
                    manos_jugadas = oponentes.manos_jugadas + 1,
                    ultima_actualizacion = CURRENT_TIMESTAMP
            """, (target_id, nick))
            conn.commit()

    def registrar_showdown(self, player_id: str, nickname: str, cartas_mostradas: str):
        if not player_id and not nickname:
            return
        target_id = str(player_id or nickname)
        nick = nickname or target_id
        with sqlite3.connect(DB_PATH) as conn:
            conn.execute("""
                INSERT INTO oponentes (player_id, nickname, ultimas_cartas_mostradas)
                VALUES (?, ?, ?)
                ON CONFLICT(player_id) DO UPDATE SET
                    ultimas_cartas_mostradas = excluded.ultimas_cartas_mostradas,
                    ultima_actualizacion = CURRENT_TIMESTAMP
            """, (target_id, nick, str(cartas_mostradas)))
            conn.commit()

    def obtener_perfil_explotativo(self, player_id: str, nickname: str = "") -> Dict[str, Any]:
        if not player_id and not nickname:
            return {
                "manos": 0,
                "estilo": "DESCONOCIDO",
                "vpip_pct": 0,
                "pfr_pct": 0,
                "agresividad": 1.0,
                "recomendacion_tactica": "Juego equilibrado GTO estándar."
            }

        target_id = str(player_id or nickname)
        with sqlite3.connect(DB_PATH) as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT manos_jugadas, preflop_calls, preflop_raises, folds_ante_raise, postflop_bets, postflop_checks, nickname, ultimas_cartas_mostradas
                FROM oponentes WHERE player_id = ? OR (nickname != '' AND nickname = ?)
            """, (target_id, nickname))
            row = cursor.fetchone()

        if not row:
            return {
                "nickname": nickname or target_id,
                "manos": 0,
                "estilo": "JUGADOR NUEVO (Sin historial)",
                "vpip_pct": 30,
                "pfr_pct": 15,
                "agresividad": 1.0,
                "recomendacion_tactica": "Oponente nuevo. Aplicar estrategia GTO estándar y observar tendencias iniciales."
            }

        manos, calls, raises, folds, bets, checks, nick, ultimas_cartas = row
        total_acciones_preflop = calls + raises + folds
        manos_efectivas = max(manos, total_acciones_preflop)

        if manos_efectivas < 2 and (bets + checks) < 2:
            return {
                "nickname": nick or nickname or target_id,
                "manos": manos_efectivas,
                "estilo": "JUGADOR NUEVO (Poco historial)",
                "vpip_pct": 30,
                "pfr_pct": 15,
                "agresividad": 1.0,
                "recomendacion_tactica": "Oponente con pocas manos registradas. Aplicar estrategia GTO estándar."
            }

        total_voluntarias = calls + raises
        vpip_pct = min(100, int((total_voluntarias / max(1, manos_efectivas)) * 100))
        pfr_pct = min(100, int((raises / max(1, manos_efectivas)) * 100))

        factor_agresion = round((raises + bets) / max(1, calls + checks), 2)

        if vpip_pct > 50 and factor_agresion < 1.0:
            estilo = "CALLING STATION (Pez Pasivo)"
            tactica = "NUNCA farolear contra este rival. Apostar por valor agresivo (Value Betting) con parejas medias y altas."
        elif vpip_pct > 40 and factor_agresion >= 2.0:
            estilo = "MANIAC (Agresivo Descontrolado)"
            tactica = "Sube y farolea demasiado. No farolearle. Inducir faroles (Slowplay) con manos monstruo y pagar con parejas altas."
        elif vpip_pct < 20 and manos_efectivas >= 4:
            estilo = "NIT (Ultra Pasivo / Miedoso)"
            tactica = "Rival muy apretado. Robarle ciegas agresivamente. Si sube la apuesta, retirarse a menos que tengamos mano monstruo."
        else:
            estilo = "REGULAR EQUILIBRADO"
            tactica = "Juego mixto balanceado GTO entre valor y semi-faroles."

        return {
            "nickname": nick or nickname or target_id,
            "manos": manos_efectivas,
            "estilo": estilo,
            "vpip_pct": vpip_pct,
            "pfr_pct": pfr_pct,
            "agresividad": factor_agresion,
            "ultimas_cartas": ultimas_cartas or "Ninguna revelada",
            "recomendacion_tactica": tactica
        }
