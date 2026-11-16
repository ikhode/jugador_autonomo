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
import re
import threading
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import sessionmaker

from db_models import Base, Oponente

PROJECT_ROOT = Path(__file__).resolve().parent
DB_PATH = PROJECT_ROOT / "logs" / "poker_stats.db"

_SCHEMA_VERSION = 3

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
    "check_raises": "INTEGER NOT NULL DEFAULT 0",
    "check_raise_opps": "INTEGER NOT NULL DEFAULT 0",
    "fold_to_check_raise": "INTEGER NOT NULL DEFAULT 0",
    "fold_to_check_raise_opps": "INTEGER NOT NULL DEFAULT 0",
    "limp_count": "INTEGER NOT NULL DEFAULT 0",
    "limp_fold_count": "INTEGER NOT NULL DEFAULT 0",
    "cbet_flop_cnt": "INTEGER NOT NULL DEFAULT 0",
    "cbet_flop_opps": "INTEGER NOT NULL DEFAULT 0",
    "cbet_turn_cnt": "INTEGER NOT NULL DEFAULT 0",
    "cbet_turn_opps": "INTEGER NOT NULL DEFAULT 0",
    "cbet_river_cnt": "INTEGER NOT NULL DEFAULT 0",
    "cbet_river_opps": "INTEGER NOT NULL DEFAULT 0",
    "bets_flop": "INTEGER NOT NULL DEFAULT 0",
    "calls_flop": "INTEGER NOT NULL DEFAULT 0",
    "bets_turn": "INTEGER NOT NULL DEFAULT 0",
    "calls_turn": "INTEGER NOT NULL DEFAULT 0",
    "bets_river": "INTEGER NOT NULL DEFAULT 0",
    "calls_river": "INTEGER NOT NULL DEFAULT 0",
    "notes": "TEXT DEFAULT ''",
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
    try:
        with (target_engine or engine).begin() as conn:
            conn.execute(
                text(
                    "DELETE FROM oponentes WHERE player_id LIKE 'Time:%' OR nickname LIKE 'Time:%' "
                    "OR player_id LIKE 'Tiempo:%' OR nickname LIKE 'Tiempo:%'"
                )
            )
    except Exception:
        pass


inicializar_db()


def es_nickname_invalido(raw: Optional[str]) -> bool:
    """Detecta si una cadena es un artefacto de interfaz (timers, botones o estados)."""
    if not raw:
        return True
    txt = re.sub(r"\s+", " ", str(raw)).strip()
    if not txt:
        return True
    # Artefactos de temporizador o estado del sistema
    if re.match(r"^(Time|Tiempo|Clock|Reloj)[\s:\n]*\d+", txt, flags=re.IGNORECASE):
        return True
    if re.match(r"^\d+[\s]*s$", txt, flags=re.IGNORECASE):
        return True
    if re.match(r"^\d{1,2}:\d{2}$", txt):
        return True
    if txt.lower() in ("thinking...", "pensando...", "wait", "esperando", "dealer", "crupier", "table", "mesa"):
        return True
    return False


def sanitizar_nickname(raw_nick: Optional[str]) -> str:
    """Limpia minuciosamente artefactos de DOM, tags de posición [BTN] y montos de stack pegados."""
    txt = re.sub(r"\s+", " ", str(raw_nick or "")).strip()
    if not txt:
        return ""
    if es_nickname_invalido(txt):
        return ""

    # Quitar etiquetas de posición [BTN], (SB), [BB], etc.
    txt = re.sub(r"[\(\[\{]\s*(BTN|SB|BB|UTG|MP|CO)\s*[\)\]\}]", "", txt, flags=re.IGNORECASE).strip()

    # Quitar montos de stack pegados al final (ej. "Jugador 1,500" o "Jugador $250.00")
    cleaned = re.sub(r"\s+[\$€£]?[\d,]+(?:\.\d+)?(?:\s*BB)?$", "", txt, flags=re.IGNORECASE).strip()
    if cleaned and not cleaned.isdigit() and not es_nickname_invalido(cleaned):
        txt = cleaned

    if txt.isdigit() or len(txt) < 2:
        return ""
    return txt


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
        if c and not es_nickname_invalido(c) and c.lower() not in seen:
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


_sklearn_archetype_model = None

def _obtener_clasificador_perfil_sklearn():
    """Inicializa y entrena un clasificador supervisado RandomForest de Scikit-Learn para arquetipos de oponentes."""
    global _sklearn_archetype_model
    if _sklearn_archetype_model is not None:
        return _sklearn_archetype_model

    try:
        from sklearn.ensemble import RandomForestClassifier
        clf = RandomForestClassifier(n_estimators=50, random_state=42)

        # Dataset de calibración de arquetipos [VPIP, PFR, AF, 3Bet%]
        X_calib = [
            [12.0, 8.0, 0.8, 4.0],   # NIT
            [15.0, 10.0, 1.0, 5.0],  # NIT
            [50.0, 35.0, 3.5, 14.0], # Maniac
            [45.0, 30.0, 2.8, 12.0], # Maniac
            [42.0, 12.0, 0.8, 3.0],  # Calling Station
            [40.0, 8.0, 0.6, 2.0],   # Calling Station
            [22.0, 18.0, 2.0, 7.0],  # TAG
            [24.0, 19.0, 2.2, 8.0],  # TAG
            [32.0, 25.0, 2.5, 9.0],  # LAG
            [30.0, 24.0, 2.1, 8.5],  # LAG
            [26.0, 20.0, 1.6, 6.5],  # Regular equilibrado
        ]
        y_calib = [
            "NIT", "NIT",
            "Maniac", "Maniac",
            "Calling Station", "Calling Station",
            "TAG", "TAG",
            "LAG", "LAG",
            "Regular equilibrado"
        ]

        clf.fit(X_calib, y_calib)
        _sklearn_archetype_model = clf
        return _sklearn_archetype_model
    except Exception:
        return None


def _clasificar_rival(
    manos: int,
    vpip: float,
    pfr: float,
    af: float,
    three_bet: float,
    acciones_observadas: Optional[int] = None,
) -> tuple[str, str]:
    """
    Clasifica el estilo del rival calibrado para 3-Max (Twister) combinando
    reglas bayesianas con inferencia supervisada de Scikit-Learn.
    """
    if manos <= 0 or (acciones_observadas is not None and acciones_observadas <= 0):
        return "JUGADOR NUEVO", "Sin manos ni acciones observadas aún del rival; mantener juego GTO balanceado"

    # Inferencia supervisada con Scikit-Learn
    clf = _obtener_clasificador_perfil_sklearn()
    estilo_ml = None
    if clf is not None:
        try:
            pred = clf.predict([[float(vpip), float(pfr), float(af), float(three_bet)]])
            if pred and len(pred) > 0:
                estilo_ml = str(pred[0])
        except Exception:
            pass

    # Umbrales calibrados para Texas Hold'em (6-Max) y juego estándar
    estilo_rule = "Regular equilibrado"
    tendencia_rule = "Frecuencias balanceadas para Hold'em sin sesgos extremos"

    if vpip < 18.0 and pfr < 14.0:
        estilo_rule, tendencia_rule = "NIT", "Ultra selectivo y tight; foldea con alta frecuencia ante presión"
    elif vpip >= 42.0 and af >= 2.0 and (pfr >= 25.0 or three_bet >= 10.0):
        estilo_rule, tendencia_rule = "Maniac", "Hiper-agresivo con rangos muy amplios y alta frecuencia de farol"
    elif vpip >= 38.0 and af < 1.3:
        estilo_rule, tendencia_rule = "Calling Station", "Paga con demasiada frecuencia y raramente toma la iniciativa"
    elif 18.0 <= vpip <= 28.0 and 14.0 <= pfr <= 23.0 and af >= 1.4 and (vpip - pfr) <= 10.0:
        estilo_rule, tendencia_rule = "TAG", "Sólido, selectivo y agresivo en posición (Tight-Aggressive 6-Max)"
    elif vpip >= 29.0 and pfr >= 21.0 and af >= 1.5:
        estilo_rule, tendencia_rule = "LAG", "Amplio y agresivo; presiona botes postflop (Loose-Aggressive 6-Max)"

    final_estilo = estilo_ml if (estilo_ml and estilo_ml != "Regular equilibrado") else estilo_rule

    if manos < MIN_SAMPLE_FOR_CLASSIFICATION:
        return final_estilo, f"[Scikit-Learn ML - Muestra preliminar {manos}/30 manos] {tendencia_rule}"

    return final_estilo, f"[Scikit-Learn ML] {tendencia_rule}"


def generar_analisis_explotativo_contextual(
    perfil: Dict[str, Any],
    posicion: str = "DESCONOCIDA",
    stack_efectivo_bb: Optional[float] = None,
    calle: str = "PREFLOP",
    bote: float = 0.0,
    acciones_previas: str = "",
) -> str:
    """Genera recomendación explotativa contextualizada."""
    manos = int(perfil.get("manos", 0) or 0)
    estilo = str(perfil.get("estilo", "JUGADOR NUEVO"))

    if manos == 0 or estilo in ("JUGADOR NUEVO", "SIN DATOS", "Perfil desconocido"):
        return (
            f"Jugador nuevo ({manos} manos observadas). "
            "No aplicar desviaciones explotativas extremas; mantener estrategia GTO balanceada."
        )

    pos_txt = f" en {posicion}" if posicion and posicion != "DESCONOCIDA" else ""
    stack_txt = f" (stack ~{stack_efectivo_bb:.1f} BB)" if stack_efectivo_bb is not None else ""
    calle_txt = (calle or "PREFLOP").upper()
    bote_txt = f" ante bote {bote:g}" if bote and bote > 0 else ""
    prefix = f"[Muestra {manos}/30 hands] " if manos < MIN_SAMPLE_FOR_CLASSIFICATION else ""

    if "Calling Station" in estilo:
        return (
            f"{prefix}Contra Calling Station{pos_txt}{stack_txt} en {calle_txt}{bote_txt}: "
            "reducir faroles sin equity suficiente y priorizar apuestas por valor (value bet) con parejas medias/altas."
        )
    if "NIT" in estilo:
        return (
            f"{prefix}Contra NIT{pos_txt}{stack_txt} en {calle_txt}{bote_txt}: "
            "aprovechar oportunidades de robo de ciegas/cbet ante rangos estrechos; respetar sus subidas fuertes."
        )
    if "Maniac" in estilo:
        return (
            f"{prefix}Contra Maniac{pos_txt}{stack_txt} en {calle_txt}{bote_txt}: "
            "evaluar líneas de defensa y bluff-catch con manos medias/fuertes; evitar farolear contra su agresión."
        )
    if "TAG" in estilo:
        return (
            f"{prefix}Contra TAG{pos_txt}{stack_txt} en {calle_txt}{bote_txt}: "
            "ajustar rangos de apertura y defensa según posición y textura; evitar pagas marginales fuera de posición."
        )
    if "LAG" in estilo:
        return (
            f"{prefix}Contra LAG{pos_txt}{stack_txt} en {calle_txt}{bote_txt}: "
            "ampliar rango de 3-bet por valor/semi-farol en posición y controlar el tamaño del bote con manos medias."
        )

    extra = f" | Contexto previo: {acciones_previas[:60]}" if acciones_previas else ""
    return (
        f"{prefix}Contra Regular equilibrado{pos_txt}{stack_txt} en {calle_txt}{bote_txt}: "
        f"mantener frecuencias GTO mixtas sin desviaciones extremas{extra}."
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

        # Descartar artefactos de DOM o timers
        if es_nickname_invalido(pid):
            pid = ""
        if es_nickname_invalido(nick):
            nick = ""

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
        is_check_raise: bool = False,
        check_raise_opportunity: bool = False,
        facing_check_raise: bool = False,
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
                            if not facing_3bet and self._marcar_flag_mano(canon_id, hand_id, "LIMP"):
                                oponente.limp_count = (getattr(oponente, "limp_count", 0) or 0) + 1
                        if three_bet_opportunity and self._marcar_flag_mano(canon_id, hand_id, "3BET_OPP"):
                            oponente.three_bet_opportunities = (oponente.three_bet_opportunities or 0) + 1
                        if facing_3bet and self._marcar_flag_mano(canon_id, hand_id, "FOLD_3BET_OPP"):
                            oponente.fold_to_3bet_opportunities = (oponente.fold_to_3bet_opportunities or 0) + 1
                    elif "FOLD" in accion_upper:
                        if self._marcar_flag_mano(canon_id, hand_id, "FOLD_PRE") and self._marcar_flag_mano(canon_id, hand_id, "VPIP"):
                            oponente.folds_preflop = (oponente.folds_preflop or 0) + 1
                            oponente.folds_ante_raise = (oponente.folds_ante_raise or 0) + 1
                            if (getattr(oponente, "limp_count", 0) or 0) > 0 and self._marcar_flag_mano(canon_id, hand_id, "LIMP_FOLD"):
                                oponente.limp_fold_count = (getattr(oponente, "limp_fold_count", 0) or 0) + 1
                        if three_bet_opportunity and self._marcar_flag_mano(canon_id, hand_id, "3BET_OPP"):
                            oponente.three_bet_opportunities = (oponente.three_bet_opportunities or 0) + 1
                        if facing_3bet and self._marcar_flag_mano(canon_id, hand_id, "FOLD_3BET"):
                            oponente.fold_to_3bet = (oponente.fold_to_3bet or 0) + 1
                            if self._marcar_flag_mano(canon_id, hand_id, "FOLD_3BET_OPP"):
                                oponente.fold_to_3bet_opportunities = (oponente.fold_to_3bet_opportunities or 0) + 1
                else:
                    if any(k in accion_upper for k in ("RAISE", "BET", "ALLIN", "ALL_IN")):
                        oponente.postflop_bets = (oponente.postflop_bets or 0) + 1

                        if calle_upper == "FLOP":
                            oponente.bets_flop = (getattr(oponente, "bets_flop", 0) or 0) + 1
                            if is_cbet and self._marcar_flag_mano(canon_id, hand_id, "CBET_FLOP"):
                                oponente.cbet_flop_cnt = (getattr(oponente, "cbet_flop_cnt", 0) or 0) + 1
                                oponente.cbet_flop_opps = (getattr(oponente, "cbet_flop_opps", 0) or 0) + 1
                        elif calle_upper == "TURN":
                            oponente.bets_turn = (getattr(oponente, "bets_turn", 0) or 0) + 1
                            if is_cbet and self._marcar_flag_mano(canon_id, hand_id, "CBET_TURN"):
                                oponente.cbet_turn_cnt = (getattr(oponente, "cbet_turn_cnt", 0) or 0) + 1
                                oponente.cbet_turn_opps = (getattr(oponente, "cbet_turn_opps", 0) or 0) + 1
                        elif calle_upper == "RIVER":
                            oponente.bets_river = (getattr(oponente, "bets_river", 0) or 0) + 1
                            if is_cbet and self._marcar_flag_mano(canon_id, hand_id, "CBET_RIVER"):
                                oponente.cbet_river_cnt = (getattr(oponente, "cbet_river_cnt", 0) or 0) + 1
                                oponente.cbet_river_opps = (getattr(oponente, "cbet_river_opps", 0) or 0) + 1

                        if is_cbet and self._marcar_flag_mano(canon_id, hand_id, "CBET"):
                            oponente.cbet_count = (oponente.cbet_count or 0) + 1
                            if self._marcar_flag_mano(canon_id, hand_id, "CBET_OPP"):
                                oponente.cbet_opportunities = (oponente.cbet_opportunities or 0) + 1
                        elif cbet_opportunity and self._marcar_flag_mano(canon_id, hand_id, "CBET_OPP"):
                            oponente.cbet_opportunities = (oponente.cbet_opportunities or 0) + 1

                        if is_check_raise and self._marcar_flag_mano(canon_id, hand_id, "CHECK_RAISE"):
                            oponente.check_raises = (getattr(oponente, "check_raises", 0) or 0) + 1
                            if self._marcar_flag_mano(canon_id, hand_id, "CHECK_RAISE_OPP"):
                                oponente.check_raise_opps = (getattr(oponente, "check_raise_opps", 0) or 0) + 1
                        elif check_raise_opportunity and self._marcar_flag_mano(canon_id, hand_id, "CHECK_RAISE_OPP"):
                            oponente.check_raise_opps = (getattr(oponente, "check_raise_opps", 0) or 0) + 1

                    elif "CALL" in accion_upper:
                        oponente.postflop_calls = (oponente.postflop_calls or 0) + 1
                        if calle_upper == "FLOP": oponente.calls_flop = (getattr(oponente, "calls_flop", 0) or 0) + 1
                        elif calle_upper == "TURN": oponente.calls_turn = (getattr(oponente, "calls_turn", 0) or 0) + 1
                        elif calle_upper == "RIVER": oponente.calls_river = (getattr(oponente, "calls_river", 0) or 0) + 1

                        if facing_cbet and self._marcar_flag_mano(canon_id, hand_id, "FOLD_CBET_OPP"):
                            oponente.fold_to_cbet_opportunities = (oponente.fold_to_cbet_opportunities or 0) + 1
                        if facing_check_raise and self._marcar_flag_mano(canon_id, hand_id, "FOLD_CR_OPP"):
                            oponente.fold_to_check_raise_opps = (getattr(oponente, "fold_to_check_raise_opps", 0) or 0) + 1

                    elif "CHECK" in accion_upper:
                        oponente.postflop_checks = (oponente.postflop_checks or 0) + 1
                        if cbet_opportunity and self._marcar_flag_mano(canon_id, hand_id, "CBET_OPP"):
                            oponente.cbet_opportunities = (oponente.cbet_opportunities or 0) + 1
                        if check_raise_opportunity and self._marcar_flag_mano(canon_id, hand_id, "CHECK_RAISE_OPP"):
                            oponente.check_raise_opps = (getattr(oponente, "check_raise_opps", 0) or 0) + 1

                    elif "FOLD" in accion_upper:
                        oponente.folds_postflop = (oponente.folds_postflop or 0) + 1
                        oponente.folds_ante_raise = (oponente.folds_ante_raise or 0) + 1
                        if facing_cbet and self._marcar_flag_mano(canon_id, hand_id, "FOLD_CBET"):
                            oponente.fold_to_cbet = (oponente.fold_to_cbet or 0) + 1
                            if self._marcar_flag_mano(canon_id, hand_id, "FOLD_CBET_OPP"):
                                oponente.fold_to_cbet_opportunities = (oponente.fold_to_cbet_opportunities or 0) + 1
                        if facing_check_raise and self._marcar_flag_mano(canon_id, hand_id, "FOLD_CR"):
                            oponente.fold_to_check_raise = (getattr(oponente, "fold_to_check_raise", 0) or 0) + 1
                            if self._marcar_flag_mano(canon_id, hand_id, "FOLD_CR_OPP"):
                                oponente.fold_to_check_raise_opps = (getattr(oponente, "fold_to_check_raise_opps", 0) or 0) + 1

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
                "estilo": "JUGADOR NUEVO",
                "tendencia": "Sin historial observado; juego GTO balanceado",
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

        # Métricas avanzadas: Check-Raise, Limp-Fold y C-Bet por calle
        cr_cnt = getattr(oponente, "check_raises", 0) or 0
        cr_opps = getattr(oponente, "check_raise_opps", 0) or 0
        cr_pct = round(min(100.0, (cr_cnt / cr_opps) * 100.0), 1) if cr_opps > 0 else 0.0

        limps = getattr(oponente, "limp_count", 0) or 0
        limp_folds = getattr(oponente, "limp_fold_count", 0) or 0
        limp_fold_pct = round(min(100.0, (limp_folds / limps) * 100.0), 1) if limps > 0 else 0.0

        cb_flop = getattr(oponente, "cbet_flop_cnt", 0) or 0
        cb_flop_opps = getattr(oponente, "cbet_flop_opps", 0) or 0
        cbet_flop_pct = round(min(100.0, (cb_flop / cb_flop_opps) * 100.0), 1) if cb_flop_opps > 0 else cbet_pct

        cb_turn = getattr(oponente, "cbet_turn_cnt", 0) or 0
        cb_turn_opps = getattr(oponente, "cbet_turn_opps", 0) or 0
        cbet_turn_pct = round(min(100.0, (cb_turn / cb_turn_opps) * 100.0), 1) if cb_turn_opps > 0 else cbet_pct

        # AF y AFq exclusivamente postflop (separando CHECK de CALL)
        pasivas_post = calls_post + checks_post
        af = round(bets_post / max(1, calls_post), 2) if (bets_post + calls_post) > 0 else 1.0
        total_acciones_post = bets_post + pasivas_post + folds_post
        afq_pct = round((bets_post / total_acciones_post) * 100.0, 1) if total_acciones_post > 0 else 0.0

        # Factor de agresión por calle
        b_flop, c_flop = getattr(oponente, "bets_flop", 0) or 0, getattr(oponente, "calls_flop", 0) or 0
        b_turn, c_turn = getattr(oponente, "bets_turn", 0) or 0, getattr(oponente, "calls_turn", 0) or 0
        b_river, c_river = getattr(oponente, "bets_river", 0) or 0, getattr(oponente, "calls_river", 0) or 0

        af_flop = round(b_flop / max(1, c_flop), 2) if (b_flop + c_flop) > 0 else af
        af_turn = round(b_turn / max(1, c_turn), 2) if (b_turn + c_turn) > 0 else af
        af_river = round(b_river / max(1, c_river), 2) if (b_river + c_river) > 0 else af

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
            "cbet_flop_pct": cbet_flop_pct,
            "cbet_turn_pct": cbet_turn_pct,
            "fold_to_cbet_pct": fold_to_cbet_pct,
            "fold_to_cbet_opportunities": fold_cbet_opps,
            "check_raise_pct": cr_pct,
            "limp_fold_pct": limp_fold_pct,
            "agresividad": af,
            "af_flop": af_flop,
            "af_turn": af_turn,
            "af_river": af_river,
            "aggression_freq_pct": afq_pct,
            "wtsd_pct": wtsd_pct,
            "wwsf_pct": wwsf_pct,
            "estilo": estilo,
            "tendencia": tendencia,
            "confianza_nivel": confianza_nivel,
            "confianza_pct": confianza_pct,
            "notes": getattr(oponente, "notes", "") or "",
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

    def close(self) -> None:
        """Cierra el pool de conexiones SQLite del perfilador de oponentes."""
        with self._lock:
            if hasattr(self, "_engine") and self._engine:
                try:
                    self._engine.dispose()
                except Exception:
                    pass

    def dispose(self) -> None:
        self.close()
