"""
Módulo Perfilador de Oponentes Explotativo (Poker HUD Database).
Almacena y calcula estadísticas en tiempo real por cada oponente (VPIP, PFR, Agresión, Folds a Raise).
"""
from pathlib import Path
from typing import Dict, Any
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from db_models import Base, Oponente

DB_PATH = Path("logs/poker_stats.db")
engine = create_engine(f"sqlite:///{DB_PATH}")
SessionLocal = sessionmaker(bind=engine)


def inicializar_db():
    DB_PATH.parent.mkdir(exist_ok=True)
    Base.metadata.create_all(engine)


inicializar_db()


class OpponentProfiler:
    def __init__(self):
        inicializar_db()

    def registrar_accion_rival(self, player_id: str, nickname: str, accion: str, calle: str = "PREFLOP"):
        if not player_id and not nickname:
            return
        target_id = str(player_id or nickname)
        nick = nickname or target_id

        session = SessionLocal()
        try:
            oponente = session.query(Oponente).filter(Oponente.player_id == target_id).first()
            if not oponente:
                oponente = Oponente(
                    player_id=target_id,
                    nickname=nick,
                    manos_jugadas=1,
                    preflop_calls=0,
                    preflop_raises=0,
                    folds_ante_raise=0,
                    postflop_bets=0,
                    postflop_checks=0
                )
                session.add(oponente)
            elif nickname:
                oponente.nickname = nickname

            accion_upper = accion.upper()
            calle_upper = (calle or "PREFLOP").upper()

            if calle_upper == "PREFLOP":
                if any(k in accion_upper for k in ("RAISE", "BET", "ALLIN", "ALL_IN")):
                    oponente.preflop_raises = (oponente.preflop_raises or 0) + 1
                elif "CALL" in accion_upper:
                    oponente.preflop_calls = (oponente.preflop_calls or 0) + 1
                elif "FOLD" in accion_upper:
                    oponente.folds_ante_raise = (oponente.folds_ante_raise or 0) + 1
            else:  # POSTFLOP
                if any(k in accion_upper for k in ("RAISE", "BET", "ALLIN", "ALL_IN")):
                    oponente.postflop_bets = (oponente.postflop_bets or 0) + 1
                elif "CHECK" in accion_upper or "CALL" in accion_upper:
                    oponente.postflop_checks = (oponente.postflop_checks or 0) + 1
                elif "FOLD" in accion_upper:
                    oponente.folds_ante_raise = (oponente.folds_ante_raise or 0) + 1

            session.commit()
        finally:
            session.close()

    def incrementar_manos_rival(self, player_id: str, nickname: str = ""):
        if not player_id and not nickname:
            return
        target_id = str(player_id or nickname)
        nick = nickname or target_id

        session = SessionLocal()
        try:
            oponente = session.query(Oponente).filter(Oponente.player_id == target_id).first()
            if not oponente:
                oponente = Oponente(player_id=target_id, nickname=nick, manos_jugadas=1)
                session.add(oponente)
            else:
                if nick:
                    oponente.nickname = nick
                oponente.manos_jugadas = (oponente.manos_jugadas or 0) + 1
            session.commit()
        finally:
            session.close()

    def registrar_showdown(self, player_id: str, nickname: str, cartas_mostradas: str):
        if not player_id and not nickname:
            return
        target_id = str(player_id or nickname)
        nick = nickname or target_id

        session = SessionLocal()
        try:
            oponente = session.query(Oponente).filter(Oponente.player_id == target_id).first()
            if not oponente:
                oponente = Oponente(player_id=target_id, nickname=nick, ultimas_cartas_mostradas=str(cartas_mostradas))
                session.add(oponente)
            else:
                if nick:
                    oponente.nickname = nick
                oponente.ultimas_cartas_mostradas = str(cartas_mostradas)
            session.commit()
        finally:
            session.close()

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
        session = SessionLocal()
        try:
            oponente = session.query(Oponente).filter(
                (Oponente.player_id == target_id) |
                ((Oponente.nickname != '') & (Oponente.nickname == nickname))
            ).first()

            if not oponente:
                return {
                    "nickname": nickname or target_id,
                    "manos": 0,
                    "estilo": "JUGADOR NUEVO (Sin historial)",
                    "vpip_pct": 30,
                    "pfr_pct": 15,
                    "agresividad": 1.0,
                    "recomendacion_tactica": "Oponente nuevo. Aplicar estrategia GTO estándar y observar tendencias iniciales."
                }

            manos = oponente.manos_jugadas or 0
            calls = oponente.preflop_calls or 0
            raises = oponente.preflop_raises or 0
            folds = oponente.folds_ante_raise or 0
            bets = oponente.postflop_bets or 0
            checks = oponente.postflop_checks or 0
            nick = oponente.nickname
            ultimas_cartas = oponente.ultimas_cartas_mostradas
        finally:
            session.close()
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
