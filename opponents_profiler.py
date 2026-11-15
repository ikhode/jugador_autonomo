"""
Módulo Perfilador de Oponentes Explotativo Multijugador (Poker HUD Database).
Almacena, unifica identidades y calcula estadísticas en tiempo real por cada oponente:
VPIP, PFR, 3-Bet%, Fold to 3-Bet%, AF, Aggression Frequency, WTSD%, WWSF%,
clasificación y análisis explotativo contextualizado.
"""
from __future__ import annotations

import json
import threading
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from db_models import Base, Oponente

DB_PATH = Path("logs/poker_stats.db")

_NEW_OPONENTE_COLUMNS = {
    "three_bets": "INTEGER NOT NULL DEFAULT 0",
    "three_bet_opportunities": "INTEGER NOT NULL DEFAULT 0",
    "fold_to_3bet": "INTEGER NOT NULL DEFAULT 0",
    "fold_to_3bet_opportunities": "INTEGER NOT NULL DEFAULT 0",
    "wtsd_count": "INTEGER NOT NULL DEFAULT 0",
    "saw_flop_count": "INTEGER NOT NULL DEFAULT 0",
    "wwsf_wins": "INTEGER NOT NULL DEFAULT 0",
    "last_seat": "INTEGER",
    "last_status": "TEXT DEFAULT 'ACTIVE'",
    "known_aliases": "TEXT DEFAULT '[]'",
}

MIN_SAMPLE_FOR_CLASSIFICATION = 10


def _ensure_oponentes_schema(target_engine) -> None:
    """Crea la tabla oponentes si no existe y migra columnas nuevas de forma idempotente."""
    Base.metadata.create_all(target_engine)
    with target_engine.begin() as conn:
        rows = conn.execute(text("PRAGMA table_info(oponentes)")).fetchall()
        existing = {r[1] for r in rows}
        for col_name, col_def in _NEW_OPONENTE_COLUMNS.items():
            if col_name not in existing:
                conn.execute(text(f"ALTER TABLE oponentes ADD COLUMN {col_name} {col_def}"))


engine = create_engine(f"sqlite:///{DB_PATH}", connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(bind=engine)


def inicializar_db(target_engine=None) -> None:
    DB_PATH.parent.mkdir(exist_ok=True)
    _ensure_oponentes_schema(target_engine or engine)


inicializar_db()


def _parse_aliases(raw: Optional[str]) -> List[str]:
    if not raw:
        return []
    try:
        parsed = json.loads(raw)
        if isinstance(parsed, list):
            return [str(x).strip() for x in parsed if str(x).strip()]
    except Exception:
        pass
    return []


def _add_alias(existing_raw: Optional[str], *candidates: str) -> str:
    aliases = _parse_aliases(existing_raw)
    seen = {a.lower() for a in aliases}
    for cand in candidates:
        c = str(cand or "").strip()
        if c and c.lower() not in seen:
            aliases.append(c)
            seen.add(c.lower())
    return json.dumps(aliases, ensure_ascii=False)


def _calcular_confianza(manos: int) -> tuple[str, int]:
    if manos < MIN_SAMPLE_FOR_CLASSIFICATION:
        return "MUESTRA INSUFICIENTE", min(35, manos * 3)
    if manos < 30:
        return "BAJA", min(60, 35 + int((manos - 10) * 1.25))
    if manos < 80:
        return "MEDIA", min(85, 60 + int((manos - 30) * 0.5))
    return "ALTA", min(99, 85 + int((manos - 80) * 0.15))


def _clasificar_rival(
    manos: int,
    vpip: float,
    pfr: float,
    af: float,
    three_bet: float,
) -> tuple[str, str]:
    """Clasifica el estilo del rival sin asignar perfiles definitivos con muestra < 10 manos."""
    if manos < MIN_SAMPLE_FOR_CLASSIFICATION:
        return "Perfil desconocido", "Muestra insuficiente para clasificar tendencia"

    if vpip < 18.0 and pfr < 14.0:
        return "NIT", "Ultra selectivo y pasivo; foldea demasiado ante presión"
    if vpip >= 42.0 and af >= 2.2 and (pfr >= 22.0 or three_bet >= 10.0):
        return "Maniac", "Hiper-agresivo con rangos muy amplios y alta frecuencia de farol"
    if vpip >= 40.0 and af < 1.2:
        return "Calling Station", "Paga con demasiada frecuencia y raramente toma la iniciativa"
    if 18.0 <= vpip <= 29.0 and 14.0 <= pfr <= 24.0 and af >= 1.5 and (vpip - pfr) <= 10.0:
        return "TAG", "Sólido, selectivo y agresivo en posición"
    if vpip >= 30.0 and pfr >= 21.0 and af >= 1.6:
        return "LAG", "Amplio y agresivo; presiona botes postflop"
    return "Regular equilibrado", "Frecuencias balanceadas sin sesgos extremos detectados"


def generar_analisis_explotativo_contextual(
    perfil: Dict[str, Any],
    posicion: str = "DESCONOCIDA",
    stack_efectivo_bb: Optional[float] = None,
    calle: str = "PREFLOP",
    bote: float = 0.0,
    acciones_previas: str = "",
) -> str:
    """Genera recomendación explotativa individual condicionada a evidencia estadística suficiente."""
    manos = int(perfil.get("manos", 0) or 0)
    estilo = str(perfil.get("estilo", "Perfil desconocido"))
    if manos < MIN_SAMPLE_FOR_CLASSIFICATION or estilo == "Perfil desconocido":
        return (
            f"Muestra insuficiente ({manos} manos < {MIN_SAMPLE_FOR_CLASSIFICATION}). "
            "No aplicar ajustes explotativos; mantener estrategia GTO estándar y recopilar datos."
        )

    pos_txt = f" en {posicion}" if posicion and posicion != "DESCONOCIDA" else ""
    stack_txt = f" (stack ~{stack_efectivo_bb:.1f} BB)" if stack_efectivo_bb is not None else ""
    calle_txt = (calle or "PREFLOP").upper()
    bote_txt = f" ante bote {bote:g}" if bote and bote > 0 else ""

    if estilo == "Calling Station":
        return (
            f"Contra Calling Station{pos_txt}{stack_txt} en {calle_txt}{bote_txt}: "
            "reducir faroles sin equity suficiente y priorizar apuestas por valor (value bet) con parejas medias/altas."
        )
    if estilo == "NIT":
        return (
            f"Contra NIT{pos_txt}{stack_txt} en {calle_txt}{bote_txt}: "
            "aprovechar oportunidades de robo de ciegas/cbet ante rangos estrechos; respetar sus subidas fuertes."
        )
    if estilo == "Maniac":
        return (
            f"Contra Maniac{pos_txt}{stack_txt} en {calle_txt}{bote_txt}: "
            "evaluar líneas de defensa y bluff-catch con manos medias/fuertes; evitar farolear contra su agresión."
        )
    if estilo == "TAG":
        return (
            f"Contra TAG{pos_txt}{stack_txt} en {calle_txt}{bote_txt}: "
            "ajustar rangos de apertura y defensa según posición y textura; evitar pagas marginales fuera de posición."
        )
    if estilo == "LAG":
        return (
            f"Contra LAG{pos_txt}{stack_txt} en {calle_txt}{bote_txt}: "
            "ampliar rango de 3-bet por valor/semi-farol en posición y controlar el tamaño del bote con manos medias."
        )

    extra = f" | Contexto previo: {acciones_previas[:60]}" if acciones_previas else ""
    return (
        f"Contra Regular equilibrado{pos_txt}{stack_txt} en {calle_txt}{bote_txt}: "
        f"mantener frecuencias GTO mixtas sin desviaciones extremas{extra}."
    )


class OpponentProfiler:
    def __init__(self, db_path: Optional[Path | str] = None):
        self._lock = threading.RLock()
        if db_path is not None:
            self.db_path = Path(db_path)
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
            self._engine = create_engine(
                f"sqlite:///{self.db_path}",
                connect_args={"check_same_thread": False},
            )
            self._SessionLocal = sessionmaker(bind=self._engine)
            inicializar_db(self._engine)
        else:
            self.db_path = DB_PATH
            self._engine = engine
            self._SessionLocal = SessionLocal
            inicializar_db(self._engine)

    def _resolver_oponente(
        self,
        session,
        player_id: str,
        nickname: str = "",
        create_if_missing: bool = False,
        seat: Optional[int] = None,
        status: Optional[str] = None,
    ) -> Optional[Oponente]:
        """Resuelve un oponente por player_id, nickname o aliases conocidos sin duplicar registros."""
        pid = str(player_id or "").strip()
        nick = str(nickname or "").strip()
        target_id = pid or nick
        if not target_id:
            return None

        oponente = session.query(Oponente).filter(Oponente.player_id == target_id).first()

        # Si viene player_id numérico/código y antes se registró usando solo el nickname como player_id
        if not oponente and nick:
            oponente = (
                session.query(Oponente)
                .filter((Oponente.player_id == nick) | ((Oponente.nickname != "") & (Oponente.nickname == nick)))
                .first()
            )
            if not oponente:
                # Búsqueda case-insensitive / aliases entre registros existentes
                for cand in session.query(Oponente).all():
                    aliases_lower = {a.lower() for a in _parse_aliases(cand.known_aliases)}
                    if nick.lower() == (cand.nickname or "").lower() or nick.lower() in aliases_lower:
                        oponente = cand
                        break

        if not oponente and pid:
            for cand in session.query(Oponente).all():
                aliases_lower = {a.lower() for a in _parse_aliases(cand.known_aliases)}
                if pid.lower() in aliases_lower:
                    oponente = cand
                    break

        if oponente:
            # Si antes su clave era el nickname y ahora tenemos un player_id distinto, guardamos ambos en aliases
            oponente.known_aliases = _add_alias(oponente.known_aliases, oponente.player_id, pid, nick)
            if nick:
                oponente.nickname = nick
            if seat is not None:
                oponente.last_seat = int(seat)
            if status:
                oponente.last_status = str(status).upper()
            oponente.ultima_actualizacion = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
            return oponente

        if not create_if_missing:
            return None

        oponente = Oponente(
            player_id=target_id,
            nickname=nick or target_id,
            manos_jugadas=0,
            preflop_calls=0,
            preflop_raises=0,
            folds_ante_raise=0,
            postflop_bets=0,
            postflop_checks=0,
            three_bets=0,
            three_bet_opportunities=0,
            fold_to_3bet=0,
            fold_to_3bet_opportunities=0,
            wtsd_count=0,
            saw_flop_count=0,
            wwsf_wins=0,
            last_seat=int(seat) if seat is not None else None,
            last_status=str(status).upper() if status else "ACTIVE",
            known_aliases=_add_alias("[]", target_id, nick),
            ultima_actualizacion=datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S"),
        )
        session.add(oponente)
        return oponente

    def actualizar_estado_mesa_rival(
        self,
        player_id: str,
        nickname: str = "",
        seat: Optional[int] = None,
        status: Optional[str] = None,
    ) -> None:
        if not player_id and not nickname:
            return
        with self._lock:
            session = self._SessionLocal()
            try:
                oponente = self._resolver_oponente(
                    session, player_id, nickname, create_if_missing=True, seat=seat, status=status
                )
                if oponente:
                    session.commit()
            finally:
                session.close()

    def registrar_accion_rival(
        self,
        player_id: str,
        nickname: str,
        accion: str,
        calle: str = "PREFLOP",
        is_3bet: bool = False,
        three_bet_opportunity: bool = False,
        facing_3bet: bool = False,
        seat: Optional[int] = None,
        status: Optional[str] = None,
    ) -> None:
        if not player_id and not nickname:
            return
        with self._lock:
            session = self._SessionLocal()
            try:
                oponente = self._resolver_oponente(
                    session, player_id, nickname, create_if_missing=True, seat=seat, status=status
                )
                if not oponente:
                    return
                if (oponente.manos_jugadas or 0) == 0:
                    oponente.manos_jugadas = 1

                accion_upper = (accion or "").upper()
                calle_upper = (calle or "PREFLOP").upper()

                if calle_upper == "PREFLOP":
                    if any(k in accion_upper for k in ("RAISE", "BET", "ALLIN", "ALL_IN")):
                        oponente.preflop_raises = (oponente.preflop_raises or 0) + 1
                        if is_3bet:
                            oponente.three_bets = (oponente.three_bets or 0) + 1
                            oponente.three_bet_opportunities = (oponente.three_bet_opportunities or 0) + 1
                        elif three_bet_opportunity:
                            oponente.three_bet_opportunities = (oponente.three_bet_opportunities or 0) + 1
                    elif "CALL" in accion_upper:
                        oponente.preflop_calls = (oponente.preflop_calls or 0) + 1
                        if three_bet_opportunity:
                            oponente.three_bet_opportunities = (oponente.three_bet_opportunities or 0) + 1
                        if facing_3bet:
                            oponente.fold_to_3bet_opportunities = (oponente.fold_to_3bet_opportunities or 0) + 1
                    elif "FOLD" in accion_upper:
                        oponente.folds_ante_raise = (oponente.folds_ante_raise or 0) + 1
                        if three_bet_opportunity:
                            oponente.three_bet_opportunities = (oponente.three_bet_opportunities or 0) + 1
                        if facing_3bet:
                            oponente.fold_to_3bet = (oponente.fold_to_3bet or 0) + 1
                            oponente.fold_to_3bet_opportunities = (oponente.fold_to_3bet_opportunities or 0) + 1
                else:
                    if any(k in accion_upper for k in ("RAISE", "BET", "ALLIN", "ALL_IN")):
                        oponente.postflop_bets = (oponente.postflop_bets or 0) + 1
                    elif "CHECK" in accion_upper or "CALL" in accion_upper:
                        oponente.postflop_checks = (oponente.postflop_checks or 0) + 1
                    elif "FOLD" in accion_upper:
                        oponente.folds_ante_raise = (oponente.folds_ante_raise or 0) + 1

                session.commit()
            finally:
                session.close()

    def registrar_evento_calle(
        self,
        player_id: str,
        nickname: str = "",
        saw_flop: bool = False,
        went_to_showdown: bool = False,
        won_when_saw_flop: bool = False,
    ) -> None:
        if not player_id and not nickname:
            return
        with self._lock:
            session = self._SessionLocal()
            try:
                oponente = self._resolver_oponente(session, player_id, nickname, create_if_missing=True)
                if not oponente:
                    return
                if saw_flop:
                    oponente.saw_flop_count = (oponente.saw_flop_count or 0) + 1
                if went_to_showdown:
                    oponente.wtsd_count = (oponente.wtsd_count or 0) + 1
                if won_when_saw_flop:
                    oponente.wwsf_wins = (oponente.wwsf_wins or 0) + 1
                session.commit()
            finally:
                session.close()

    def incrementar_manos_rival(self, player_id: str, nickname: str = "", seat: Optional[int] = None) -> None:
        if not player_id and not nickname:
            return
        with self._lock:
            session = self._SessionLocal()
            try:
                oponente = self._resolver_oponente(
                    session, player_id, nickname, create_if_missing=True, seat=seat
                )
                if oponente:
                    oponente.manos_jugadas = (oponente.manos_jugadas or 0) + 1
                    session.commit()
            finally:
                session.close()

    def registrar_showdown(self, player_id: str, nickname: str, cartas_mostradas: str) -> None:
        if not player_id and not nickname:
            return
        with self._lock:
            session = self._SessionLocal()
            try:
                oponente = self._resolver_oponente(session, player_id, nickname, create_if_missing=True)
                if oponente:
                    oponente.ultimas_cartas_mostradas = str(cartas_mostradas)
                    oponente.wtsd_count = (oponente.wtsd_count or 0) + 1
                    session.commit()
            finally:
                session.close()

    def _construir_ficha_desde_modelo(
        self,
        oponente: Optional[Oponente],
        player_id: str = "",
        nickname: str = "",
        seat: Optional[int] = None,
        status: Optional[str] = None,
    ) -> Dict[str, Any]:
        if not oponente:
            pid = str(player_id or nickname or "")
            nick = str(nickname or pid or "Desconocido")
            perfil = {
                "player_id": pid,
                "nickname": nick,
                "seat": seat,
                "status": (status or "ACTIVE").upper(),
                "manos": 0,
                "vpip_pct": 0.0,
                "pfr_pct": 0.0,
                "three_bet_pct": 0.0,
                "fold_to_3bet_pct": 0.0,
                "agresividad": 1.0,
                "aggression_freq_pct": 0.0,
                "wtsd_pct": 0.0,
                "wwsf_pct": 0.0,
                "estilo": "Perfil desconocido",
                "tendencia": "Sin historial observado",
                "confianza_nivel": "MUESTRA INSUFICIENTE",
                "confianza_pct": 0,
                "ultimas_cartas": "Ninguna revelada",
                "ultima_actualizacion": None,
            }
            perfil["recomendacion_tactica"] = generar_analisis_explotativo_contextual(perfil)
            return perfil

        manos = oponente.manos_jugadas or 0
        calls = oponente.preflop_calls or 0
        raises = oponente.preflop_raises or 0
        folds = oponente.folds_ante_raise or 0
        bets = oponente.postflop_bets or 0
        checks = oponente.postflop_checks or 0
        three_bets = oponente.three_bets or 0
        three_bet_opps = oponente.three_bet_opportunities or 0
        fold_3bet = oponente.fold_to_3bet or 0
        fold_3bet_opps = oponente.fold_to_3bet_opportunities or 0
        wtsd = oponente.wtsd_count or 0
        saw_flop = oponente.saw_flop_count or 0
        wwsf = oponente.wwsf_wins or 0

        total_acciones_preflop = calls + raises + folds
        manos_efectivas = max(manos, total_acciones_preflop)

        vpip_pct = round(min(100.0, ((calls + raises) / max(1, manos_efectivas)) * 100.0), 1) if manos_efectivas > 0 else 0.0
        pfr_pct = round(min(100.0, (raises / max(1, manos_efectivas)) * 100.0), 1) if manos_efectivas > 0 else 0.0
        three_bet_pct = round(min(100.0, (three_bets / three_bet_opps) * 100.0), 1) if three_bet_opps > 0 else 0.0
        fold_to_3bet_pct = round(min(100.0, (fold_3bet / fold_3bet_opps) * 100.0), 1) if fold_3bet_opps > 0 else 0.0

        agresivas = raises + bets
        pasivas = calls + checks
        af = round(agresivas / max(1, pasivas), 2) if (agresivas + pasivas) > 0 else 1.0
        total_acciones_afq = agresivas + pasivas + folds
        afq_pct = round((agresivas / total_acciones_afq) * 100.0, 1) if total_acciones_afq > 0 else 0.0

        wtsd_pct = round(min(100.0, (wtsd / saw_flop) * 100.0), 1) if saw_flop > 0 else 0.0
        wwsf_pct = round(min(100.0, (wwsf / saw_flop) * 100.0), 1) if saw_flop > 0 else 0.0

        estilo, tendencia = _clasificar_rival(manos_efectivas, vpip_pct, pfr_pct, af, three_bet_pct)
        confianza_nivel, confianza_pct = _calcular_confianza(manos_efectivas)

        perfil = {
            "player_id": oponente.player_id,
            "nickname": oponente.nickname or nickname or oponente.player_id,
            "seat": seat if seat is not None else oponente.last_seat,
            "status": (status or oponente.last_status or "ACTIVE").upper(),
            "manos": manos_efectivas,
            "vpip_pct": vpip_pct,
            "pfr_pct": pfr_pct,
            "three_bet_pct": three_bet_pct,
            "fold_to_3bet_pct": fold_to_3bet_pct,
            "agresividad": af,
            "aggression_freq_pct": afq_pct,
            "wtsd_pct": wtsd_pct,
            "wwsf_pct": wwsf_pct,
            "estilo": estilo,
            "tendencia": tendencia,
            "confianza_nivel": confianza_nivel,
            "confianza_pct": confianza_pct,
            "ultimas_cartas": oponente.ultimas_cartas_mostradas or "Ninguna revelada",
            "ultima_actualizacion": oponente.ultima_actualizacion,
        }
        perfil["recomendacion_tactica"] = generar_analisis_explotativo_contextual(perfil)
        return perfil

    def obtener_perfil_explotativo(
        self,
        player_id: str,
        nickname: str = "",
        seat: Optional[int] = None,
        status: Optional[str] = None,
    ) -> Dict[str, Any]:
        if not player_id and not nickname:
            return {
                "player_id": "",
                "nickname": "",
                "seat": seat,
                "status": "UNKNOWN",
                "manos": 0,
                "estilo": "Perfil desconocido",
                "tendencia": "Sin identificador de oponente",
                "vpip_pct": 0.0,
                "pfr_pct": 0.0,
                "three_bet_pct": 0.0,
                "fold_to_3bet_pct": 0.0,
                "agresividad": 1.0,
                "aggression_freq_pct": 0.0,
                "wtsd_pct": 0.0,
                "wwsf_pct": 0.0,
                "confianza_nivel": "MUESTRA INSUFICIENTE",
                "confianza_pct": 0,
                "recomendacion_tactica": "Muestra insuficiente (0 manos). Aplicar estrategia GTO estándar.",
            }

        with self._lock:
            session = self._SessionLocal()
            try:
                oponente = self._resolver_oponente(session, player_id, nickname, create_if_missing=False)
                return self._construir_ficha_desde_modelo(
                    oponente, player_id=player_id, nickname=nickname, seat=seat, status=status
                )
            finally:
                session.close()

    def listar_perfiles_historicos(self, limit: int = 100) -> List[Dict[str, Any]]:
        with self._lock:
            session = self._SessionLocal()
            try:
                rows = (
                    session.query(Oponente)
                    .order_by(Oponente.manos_jugadas.desc(), Oponente.ultima_actualizacion.desc())
                    .limit(limit)
                    .all()
                )
                return [self._construir_ficha_desde_modelo(r) for r in rows]
            finally:
                session.close()
