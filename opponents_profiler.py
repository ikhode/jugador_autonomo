"""
Módulo Perfilador de Oponentes Explotativo Multijugador (Poker HUD Database).
Almacena, unifica identidades y calcula estadísticas en tiempo real por cada oponente:
VPIP, PFR, 3-Bet%, Fold to 3-Bet%, CBet%, Fold to CBet%, AF, AFq, WTSD%, WWSF%,
intervalos de Wilson, suavizado bayesiano y clasificación calibrada para 3-Max.
"""
from __future__ import annotations

from contextlib import closing
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import threading
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import sessionmaker

from db_models import Base, Oponente

PROJECT_ROOT = Path(__file__).resolve().parent
DB_PATH = PROJECT_ROOT / "logs" / "poker_stats.db"

_SCHEMA_VERSION = 2

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
    "folds_preflop": "INTEGER NOT NULL DEFAULT 0",
    "folds_postflop": "INTEGER NOT NULL DEFAULT 0",
    "postflop_calls": "INTEGER NOT NULL DEFAULT 0",
    "cbet_count": "INTEGER NOT NULL DEFAULT 0",
    "cbet_opportunities": "INTEGER NOT NULL DEFAULT 0",
    "fold_to_cbet": "INTEGER NOT NULL DEFAULT 0",
    "fold_to_cbet_opportunities": "INTEGER NOT NULL DEFAULT 0",
}

MIN_SAMPLE_FOR_CLASSIFICATION = 30
PRIOR_WEIGHT_HANDS = 10
PRIOR_VPIP_3MAX = 0.28
PRIOR_PFR_3MAX = 0.20


def _configure_sqlite_engine(target_engine) -> None:
    """Configura WAL, busy_timeout y synchronous=NORMAL en cada conexión SQLite."""
    @event.listens_for(target_engine, "connect")
    def _set_sqlite_pragmas(dbapi_conn, _conn_record):
        cursor = dbapi_conn.cursor()
        try:
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA busy_timeout=10000")
            cursor.execute("PRAGMA synchronous=NORMAL")
        finally:
            cursor.close()


def _ensure_oponentes_schema(target_engine) -> None:
    """Crea la tabla oponentes y aplica migraciones versionadas idempotentes."""
    Base.metadata.create_all(target_engine)
    with target_engine.begin() as conn:
        conn.execute(
            text(
                "CREATE TABLE IF NOT EXISTS schema_migrations ("
                "version INTEGER PRIMARY KEY, "
                "applied_at TEXT NOT NULL)"
            )
        )
        rows = conn.execute(text("PRAGMA table_info(oponentes)")).fetchall()
        existing = {r[1] for r in rows}
        for col_name, col_def in _NEW_OPONENTE_COLUMNS.items():
            if col_name not in existing:
                conn.execute(text(f"ALTER TABLE oponentes ADD COLUMN {col_name} {col_def}"))
        conn.execute(
            text(
                "INSERT OR IGNORE INTO schema_migrations (version, applied_at) "
                "VALUES (:ver, :ts)"
            ),
            {"ver": _SCHEMA_VERSION, "ts": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")},
        )


engine = create_engine(
    f"sqlite:///{DB_PATH}",
    connect_args={"check_same_thread": False, "timeout": 30.0},
)
_configure_sqlite_engine(engine)
SessionLocal = sessionmaker(bind=engine)


def inicializar_db(target_engine=None) -> None:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    _ensure_oponentes_schema(target_engine or engine)


inicializar_db()


def wilson_ci(exitos: int, total: int, z: float = 1.96) -> Tuple[float, float]:
    """Calcula el intervalo de confianza de Wilson al 95% en porcentaje [0.0, 100.0]."""
    if total <= 0:
        return (0.0, 100.0)
    k = max(0, min(exitos, total))
    p = k / total
    z2 = z * z
    denom = 1.0 + z2 / total
    centro = (p + z2 / (2.0 * total)) / denom
    margen = (z * math.sqrt((p * (1.0 - p) + z2 / (4.0 * total)) / total)) / denom
    low = max(0.0, round((centro - margen) * 100.0, 1))
    high = min(100.0, round((centro + margen) * 100.0, 1))
    return (low, high)


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
        return "MUESTRA INSUFICIENTE", min(35, int(manos * 1.1))
    if manos < 60:
        return "BAJA", min(65, 35 + int((manos - 30) * 1.0))
    if manos < 120:
        return "MEDIA", min(85, 65 + int((manos - 60) * 0.33))
    return "ALTA", min(99, 85 + int((manos - 120) * 0.1))


def _clasificar_rival(
    manos: int,
    vpip: float,
    pfr: float,
    af: float,
    three_bet: float,
    acciones_observadas: Optional[int] = None,
) -> tuple[str, str]:
    """
    Clasifica el estilo del rival calibrado para 3-Max (Twister).
    No asigna estilo definitivo con menos de MIN_SAMPLE_FOR_CLASSIFICATION (30) manos
    ni cuando no se ha observado ninguna acción del rival.
    """
    if manos < MIN_SAMPLE_FOR_CLASSIFICATION:
        return "SIN DATOS", f"Muestra insuficiente ({manos}/{MIN_SAMPLE_FOR_CLASSIFICATION} manos) para clasificar"
    if acciones_observadas is not None and acciones_observadas <= 0:
        return "SIN DATOS", "Sin acciones observadas del rival para clasificar"

    if vpip < 22.0 and pfr < 16.0:
        return "NIT", "Ultra selectivo y pasivo en 3-Max; foldea demasiado ante presión"
    if vpip >= 48.0 and af >= 2.2 and (pfr >= 26.0 or three_bet >= 11.0):
        return "Maniac", "Hiper-agresivo con rangos muy amplios y alta frecuencia de farol"
    if vpip >= 44.0 and af < 1.2:
        return "Calling Station", "Paga con demasiada frecuencia y raramente toma la iniciativa"
    if 22.0 <= vpip <= 34.0 and 16.0 <= pfr <= 26.0 and af >= 1.5 and (vpip - pfr) <= 12.0:
        return "TAG", "Sólido, selectivo y agresivo en posición 3-Max"
    if vpip >= 35.0 and pfr >= 24.0 and af >= 1.6:
        return "LAG", "Amplio y agresivo; presiona botes postflop"
    return "Regular equilibrado", "Frecuencias balanceadas para 3-Max sin sesgos extremos"


def generar_analisis_explotativo_contextual(
    perfil: Dict[str, Any],
    posicion: str = "DESCONOCIDA",
    stack_efectivo_bb: Optional[float] = None,
    calle: str = "PREFLOP",
    bote: float = 0.0,
    acciones_previas: str = "",
) -> str:
    """Genera recomendación explotativa condicionada a muestra suficiente (>= 30 manos)."""
    manos = int(perfil.get("manos", 0) or 0)
    estilo = str(perfil.get("estilo", "SIN DATOS"))
    if manos < MIN_SAMPLE_FOR_CLASSIFICATION or estilo in ("SIN DATOS", "Perfil desconocido"):
        return (
            f"Muestra insuficiente ({manos} manos < {MIN_SAMPLE_FOR_CLASSIFICATION}). "
            "No aplicar ajustes explotativos absolutos; mantener estrategia GTO estándar."
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
        self._hand_flags: Dict[Tuple[str, str], set[str]] = {}
        if db_path is not None:
            self.db_path = Path(db_path).resolve()
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
            self._engine = create_engine(
                f"sqlite:///{self.db_path}",
                connect_args={"check_same_thread": False, "timeout": 30.0},
            )
            _configure_sqlite_engine(self._engine)
            self._SessionLocal = sessionmaker(bind=self._engine)
            inicializar_db(self._engine)
        else:
            self.db_path = DB_PATH
            self._engine = engine
            self._SessionLocal = SessionLocal
            inicializar_db(self._engine)

    def _marcar_flag_mano(self, canonical_id: str, hand_id: Optional[Any], flag: str) -> bool:
        """Devuelve True si el flag NO había sido marcado aún para (canonical_id, hand_id)."""
        if hand_id is None or str(hand_id).strip() == "":
            return True
        key = (canonical_id, str(hand_id))
        flags = self._hand_flags.setdefault(key, set())
        if flag in flags:
            return False
        flags.add(flag)
        if len(self._hand_flags) > 2000:
            # Limpieza FIFO de entradas antiguas
            for old_k in list(self._hand_flags.keys())[:500]:
                del self._hand_flags[old_k]
        return True

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

        if not oponente and nick:
            oponente = (
                session.query(Oponente)
                .filter((Oponente.player_id == nick) | ((Oponente.nickname != "") & (Oponente.nickname == nick)))
                .first()
            )
            if not oponente:
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
            oponente.known_aliases = _add_alias(oponente.known_aliases, oponente.player_id, pid, nick)
            if nick:
                oponente.nickname = nick
            if seat is not None:
                oponente.last_seat = int(seat)
            if status:
                oponente.last_status = str(status).upper()
            oponente.ultima_actualizacion = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
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
            folds_preflop=0,
            folds_postflop=0,
            postflop_bets=0,
            postflop_calls=0,
            postflop_checks=0,
            cbet_count=0,
            cbet_opportunities=0,
            fold_to_cbet=0,
            fold_to_cbet_opportunities=0,
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
            ultima_actualizacion=datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
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
            with closing(self._SessionLocal()) as session:
                oponente = self._resolver_oponente(
                    session, player_id, nickname, create_if_missing=True, seat=seat, status=status
                )
                if oponente:
                    session.commit()

    def registrar_accion_rival(
        self,
        player_id: str,
        nickname: str,
        accion: str,
        calle: str = "PREFLOP",
        is_3bet: bool = False,
        three_bet_opportunity: bool = False,
        facing_3bet: bool = False,
        is_cbet: bool = False,
        cbet_opportunity: bool = False,
        facing_cbet: bool = False,
        seat: Optional[int] = None,
        status: Optional[str] = None,
        hand_id: Optional[Any] = None,
    ) -> None:
        if not player_id and not nickname:
            return
        with self._lock:
            with closing(self._SessionLocal()) as session:
                oponente = self._resolver_oponente(
                    session, player_id, nickname, create_if_missing=True, seat=seat, status=status
                )
                if not oponente:
                    return
                canon_id = oponente.player_id
                if hand_id is not None:
                    if self._marcar_flag_mano(canon_id, hand_id, "HAND_COUNTED"):
                        oponente.manos_jugadas = (oponente.manos_jugadas or 0) + 1
                elif (oponente.manos_jugadas or 0) == 0:
                    oponente.manos_jugadas = 1

                accion_upper = (accion or "").upper()
                calle_upper = (calle or "PREFLOP").upper()

                if calle_upper == "PREFLOP":
                    if any(k in accion_upper for k in ("RAISE", "BET", "ALLIN", "ALL_IN")):
                        if self._marcar_flag_mano(canon_id, hand_id, "PFR"):
                            oponente.preflop_raises = (oponente.preflop_raises or 0) + 1
                            if hand_id is not None and not self._marcar_flag_mano(canon_id, hand_id, "VPIP_CALL"):
                                # Si antes hizo limp/call en esta misma mano, no contar doble en VPIP
                                if (oponente.preflop_calls or 0) > 0:
                                    oponente.preflop_calls -= 1
                            self._marcar_flag_mano(canon_id, hand_id, "VPIP")
                        if is_3bet and self._marcar_flag_mano(canon_id, hand_id, "3BET"):
                            oponente.three_bets = (oponente.three_bets or 0) + 1
                            if self._marcar_flag_mano(canon_id, hand_id, "3BET_OPP"):
                                oponente.three_bet_opportunities = (oponente.three_bet_opportunities or 0) + 1
                        elif three_bet_opportunity and self._marcar_flag_mano(canon_id, hand_id, "3BET_OPP"):
                            oponente.three_bet_opportunities = (oponente.three_bet_opportunities or 0) + 1
                    elif "CALL" in accion_upper:
                        if self._marcar_flag_mano(canon_id, hand_id, "VPIP"):
                            self._marcar_flag_mano(canon_id, hand_id, "VPIP_CALL")
                            oponente.preflop_calls = (oponente.preflop_calls or 0) + 1
                        if three_bet_opportunity and self._marcar_flag_mano(canon_id, hand_id, "3BET_OPP"):
                            oponente.three_bet_opportunities = (oponente.three_bet_opportunities or 0) + 1
                        if facing_3bet and self._marcar_flag_mano(canon_id, hand_id, "FOLD_3BET_OPP"):
                            oponente.fold_to_3bet_opportunities = (oponente.fold_to_3bet_opportunities or 0) + 1
                    elif "FOLD" in accion_upper:
                        if self._marcar_flag_mano(canon_id, hand_id, "FOLD_PRE") and self._marcar_flag_mano(canon_id, hand_id, "VPIP"):
                            oponente.folds_preflop = (oponente.folds_preflop or 0) + 1
                            oponente.folds_ante_raise = (oponente.folds_ante_raise or 0) + 1
                        if three_bet_opportunity and self._marcar_flag_mano(canon_id, hand_id, "3BET_OPP"):
                            oponente.three_bet_opportunities = (oponente.three_bet_opportunities or 0) + 1
                        if facing_3bet and self._marcar_flag_mano(canon_id, hand_id, "FOLD_3BET"):
                            oponente.fold_to_3bet = (oponente.fold_to_3bet or 0) + 1
                            if self._marcar_flag_mano(canon_id, hand_id, "FOLD_3BET_OPP"):
                                oponente.fold_to_3bet_opportunities = (oponente.fold_to_3bet_opportunities or 0) + 1
                else:
                    if any(k in accion_upper for k in ("RAISE", "BET", "ALLIN", "ALL_IN")):
                        oponente.postflop_bets = (oponente.postflop_bets or 0) + 1
                        if is_cbet and self._marcar_flag_mano(canon_id, hand_id, "CBET"):
                            oponente.cbet_count = (oponente.cbet_count or 0) + 1
                            if self._marcar_flag_mano(canon_id, hand_id, "CBET_OPP"):
                                oponente.cbet_opportunities = (oponente.cbet_opportunities or 0) + 1
                        elif cbet_opportunity and self._marcar_flag_mano(canon_id, hand_id, "CBET_OPP"):
                            oponente.cbet_opportunities = (oponente.cbet_opportunities or 0) + 1
                    elif "CALL" in accion_upper:
                        oponente.postflop_calls = (oponente.postflop_calls or 0) + 1
                        if facing_cbet and self._marcar_flag_mano(canon_id, hand_id, "FOLD_CBET_OPP"):
                            oponente.fold_to_cbet_opportunities = (oponente.fold_to_cbet_opportunities or 0) + 1
                    elif "CHECK" in accion_upper:
                        oponente.postflop_checks = (oponente.postflop_checks or 0) + 1
                        if cbet_opportunity and self._marcar_flag_mano(canon_id, hand_id, "CBET_OPP"):
                            oponente.cbet_opportunities = (oponente.cbet_opportunities or 0) + 1
                    elif "FOLD" in accion_upper:
                        oponente.folds_postflop = (oponente.folds_postflop or 0) + 1
                        oponente.folds_ante_raise = (oponente.folds_ante_raise or 0) + 1
                        if facing_cbet and self._marcar_flag_mano(canon_id, hand_id, "FOLD_CBET"):
                            oponente.fold_to_cbet = (oponente.fold_to_cbet or 0) + 1
                            if self._marcar_flag_mano(canon_id, hand_id, "FOLD_CBET_OPP"):
                                oponente.fold_to_cbet_opportunities = (oponente.fold_to_cbet_opportunities or 0) + 1

                session.commit()

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
            with closing(self._SessionLocal()) as session:
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

    def incrementar_manos_rival(
        self,
        player_id: str,
        nickname: str = "",
        seat: Optional[int] = None,
        hand_id: Optional[Any] = None,
    ) -> None:
        if not player_id and not nickname:
            return
        with self._lock:
            with closing(self._SessionLocal()) as session:
                oponente = self._resolver_oponente(
                    session, player_id, nickname, create_if_missing=True, seat=seat
                )
                if oponente:
                    if hand_id is not None and not self._marcar_flag_mano(oponente.player_id, hand_id, "HAND_COUNTED"):
                        return
                    oponente.manos_jugadas = (oponente.manos_jugadas or 0) + 1
                    session.commit()

    def registrar_showdown(self, player_id: str, nickname: str, cartas_mostradas: str) -> None:
        if not player_id and not nickname:
            return
        with self._lock:
            with closing(self._SessionLocal()) as session:
                oponente = self._resolver_oponente(session, player_id, nickname, create_if_missing=True)
                if oponente:
                    oponente.ultimas_cartas_mostradas = str(cartas_mostradas)
                    oponente.wtsd_count = (oponente.wtsd_count or 0) + 1
                    session.commit()

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
                "vpip_smoothed_pct": round(PRIOR_VPIP_3MAX * 100.0, 1),
                "pfr_smoothed_pct": round(PRIOR_PFR_3MAX * 100.0, 1),
                "vpip_ci95": (0.0, 100.0),
                "pfr_ci95": (0.0, 100.0),
                "three_bet_pct": 0.0,
                "fold_to_3bet_pct": 0.0,
                "cbet_pct": 0.0,
                "fold_to_cbet_pct": 0.0,
                "agresividad": 1.0,
                "aggression_freq_pct": 0.0,
                "wtsd_pct": 0.0,
                "wwsf_pct": 0.0,
                "estilo": "SIN DATOS",
                "tendencia": "Sin historial observado",
                "confianza_nivel": "MUESTRA INSUFICIENTE",
                "confianza_pct": 0,
                "ultimas_cartas": "Ninguna revelada",
                "ultima_actualizacion": None,
            }
            perfil["recomendacion_tactica"] = generar_analisis_explotativo_contextual(perfil)
            return perfil

        manos = oponente.manos_jugadas or 0
        calls_pre = oponente.preflop_calls or 0
        raises_pre = oponente.preflop_raises or 0
        folds_pre = getattr(oponente, "folds_preflop", None)
        if folds_pre is None:
            folds_pre = oponente.folds_ante_raise or 0
        folds_post = getattr(oponente, "folds_postflop", 0) or 0
        bets_post = oponente.postflop_bets or 0
        calls_post = getattr(oponente, "postflop_calls", 0) or 0
        checks_post = oponente.postflop_checks or 0
        three_bets = oponente.three_bets or 0
        three_bet_opps = oponente.three_bet_opportunities or 0
        fold_3bet = oponente.fold_to_3bet or 0
        fold_3bet_opps = oponente.fold_to_3bet_opportunities or 0
        cbet_cnt = getattr(oponente, "cbet_count", 0) or 0
        cbet_opps = getattr(oponente, "cbet_opportunities", 0) or 0
        fold_cbet = getattr(oponente, "fold_to_cbet", 0) or 0
        fold_cbet_opps = getattr(oponente, "fold_to_cbet_opportunities", 0) or 0
        wtsd = oponente.wtsd_count or 0
        saw_flop = oponente.saw_flop_count or 0
        wwsf = oponente.wwsf_wins or 0

        manos_efectivas = max(manos, calls_pre + raises_pre + folds_pre)
        vpip_eventos = min(manos_efectivas, calls_pre + raises_pre)
        pfr_eventos = min(manos_efectivas, raises_pre)

        vpip_pct = round((vpip_eventos / manos_efectivas) * 100.0, 1) if manos_efectivas > 0 else 0.0
        pfr_pct = round((pfr_eventos / manos_efectivas) * 100.0, 1) if manos_efectivas > 0 else 0.0

        # Suavizado con prior poblacional 3-Max
        vpip_smoothed = round(
            ((vpip_eventos + PRIOR_WEIGHT_HANDS * PRIOR_VPIP_3MAX) / (manos_efectivas + PRIOR_WEIGHT_HANDS)) * 100.0,
            1,
        )
        pfr_smoothed = round(
            ((pfr_eventos + PRIOR_WEIGHT_HANDS * PRIOR_PFR_3MAX) / (manos_efectivas + PRIOR_WEIGHT_HANDS)) * 100.0,
            1,
        )
        vpip_ci = wilson_ci(vpip_eventos, manos_efectivas)
        pfr_ci = wilson_ci(pfr_eventos, manos_efectivas)

        three_bet_pct = round(min(100.0, (three_bets / three_bet_opps) * 100.0), 1) if three_bet_opps > 0 else 0.0
        fold_to_3bet_pct = round(min(100.0, (fold_3bet / fold_3bet_opps) * 100.0), 1) if fold_3bet_opps > 0 else 0.0
        cbet_pct = round(min(100.0, (cbet_cnt / cbet_opps) * 100.0), 1) if cbet_opps > 0 else 0.0
        fold_to_cbet_pct = round(min(100.0, (fold_cbet / fold_cbet_opps) * 100.0), 1) if fold_cbet_opps > 0 else 0.0

        # AF y AFq exclusivamente postflop (separando CHECK de CALL)
        pasivas_post = calls_post + checks_post
        af = round(bets_post / max(1, calls_post), 2) if (bets_post + calls_post) > 0 else 1.0
        total_acciones_post = bets_post + pasivas_post + folds_post
        afq_pct = round((bets_post / total_acciones_post) * 100.0, 1) if total_acciones_post > 0 else 0.0

        wtsd_pct = round(min(100.0, (wtsd / saw_flop) * 100.0), 1) if saw_flop > 0 else 0.0
        wwsf_pct = round(min(100.0, (wwsf / saw_flop) * 100.0), 1) if saw_flop > 0 else 0.0

        acciones_obs_total = vpip_eventos + folds_pre + total_acciones_post
        estilo, tendencia = _clasificar_rival(
            manos_efectivas, vpip_smoothed, pfr_smoothed, af, three_bet_pct, acciones_observadas=acciones_obs_total
        )
        confianza_nivel, confianza_pct = _calcular_confianza(manos_efectivas)

        perfil = {
            "player_id": oponente.player_id,
            "nickname": oponente.nickname or oponente.player_id,
            "seat": seat if seat is not None else oponente.last_seat,
            "status": (status or oponente.last_status or "ACTIVE").upper(),
            "manos": manos_efectivas,
            "vpip_pct": vpip_pct,
            "pfr_pct": pfr_pct,
            "vpip_smoothed_pct": vpip_smoothed,
            "pfr_smoothed_pct": pfr_smoothed,
            "vpip_ci95": vpip_ci,
            "pfr_ci95": pfr_ci,
            "three_bet_pct": three_bet_pct,
            "three_bet_opportunities": three_bet_opps,
            "fold_to_3bet_pct": fold_to_3bet_pct,
            "fold_to_3bet_opportunities": fold_3bet_opps,
            "cbet_pct": cbet_pct,
            "cbet_opportunities": cbet_opps,
            "fold_to_cbet_pct": fold_to_cbet_pct,
            "fold_to_cbet_opportunities": fold_cbet_opps,
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
        with self._lock:
            with closing(self._SessionLocal()) as session:
                oponente = self._resolver_oponente(session, player_id, nickname, create_if_missing=False)
                return self._construir_ficha_desde_modelo(
                    oponente, player_id=player_id, nickname=nickname, seat=seat, status=status
                )

    def obtener_perfiles_mesa(self, jugadores: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Devuelve perfiles ordenados por asiento para una lista de jugadores activos o ausentes."""
        perfiles: List[Dict[str, Any]] = []
        for j in jugadores:
            pid = str(j.get("player_id") or "")
            nick = str(j.get("nickname") or "")
            seat = j.get("seat")
            status = str(j.get("status") or "ACTIVE").upper()
            perfil = self.obtener_perfil_explotativo(pid, nick, seat=seat, status=status)
            if "stack" in j:
                perfil["stack"] = j["stack"]
            perfiles.append(perfil)
        return sorted(perfiles, key=lambda x: (x.get("seat") if x.get("seat") is not None else 999))

    def listar_todos_los_rivales(self) -> List[Dict[str, Any]]:
        with self._lock:
            with closing(self._SessionLocal()) as session:
                oponentes = session.query(Oponente).order_by(Oponente.manos_jugadas.desc()).all()
                return [self._construir_ficha_desde_modelo(op) for op in oponentes]

    def listar_perfiles_historicos(self) -> List[Dict[str, Any]]:
        return self.listar_todos_los_rivales()
