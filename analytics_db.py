"""
Capa de Almacenamiento, Trazabilidad y Telemetría Persistente para el Sistema de Analíticas del Bot de Póker.
Reutiliza la base de datos SQLite existente (`logs/poker_stats.db`) en modo WAL,
manteniendo compatibilidad total con `opponents_profiler.py` y separando estrictamente
datos reales (`is_synthetic=0`, `mode='live'`) de experimentos controlados (`is_synthetic=1`, `mode='benchmark'`).
"""
import csv
import hashlib
import io
import json
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from sqlalchemy import create_engine
from db_models import Base

DB_PATH = Path("logs/poker_stats.db")
analytics_engine = create_engine(f"sqlite:///{DB_PATH}")

PROJECT_CORE_FILES = [
    "config.py",
    "poker_gto_engine.py",
    "poker_agent.py",
    "opponents_profiler.py",
    "bot.py",
]


def calcular_hash_codigo_proyecto(base_dir: Optional[Path] = None) -> str:
    """
    Calcula un hash SHA-256 determinista del código fuente y configuración estratégica del bot
    para garantizar trazabilidad exacta entre resultados y estado del repositorio.
    """
    root = base_dir or Path(__file__).resolve().parent
    hasher = hashlib.sha256()
    for fname in PROJECT_CORE_FILES:
        fpath = root / fname
        if fpath.exists():
            try:
                hasher.update(fname.encode("utf-8"))
                hasher.update(fpath.read_bytes())
            except Exception:
                pass
    return hasher.hexdigest()[:16]


@contextmanager
def get_connection(db_path: Path = DB_PATH):
    """Obtiene una conexión SQLite optimizada con WAL, Row factory y cierre garantizado."""
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path), timeout=15.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA synchronous=NORMAL;")
    conn.execute("PRAGMA foreign_keys=ON;")
    try:
        yield conn
    finally:
        conn.close()


def _ensure_column(conn: sqlite3.Connection, table: str, col_name: str, col_def: str) -> None:
    """Agrega una columna a una tabla existente si aún no existe (migración idempotente)."""
    rows = conn.execute(f"PRAGMA table_info({table});").fetchall()
    existing = {r["name"] for r in rows}
    if col_name not in existing:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {col_name} {col_def};")


def inicializar_esquema_analiticas(db_path: Path = DB_PATH) -> None:
    """
    Inicializa y migra todas las tablas necesarias para el sistema avanzado de analíticas,
    conservando intacta la tabla `oponentes` existente.
    """
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    Base.metadata.create_all(analytics_engine)

    with get_connection(db_path) as conn:
        # 1. Tabla de oponentes (compatibilidad con opponents_profiler.py)
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

        # 2. Tabla de versiones experimentales del bot
        conn.execute("""
            CREATE TABLE IF NOT EXISTS bot_versions (
                version_id TEXT PRIMARY KEY,
                created_at TEXT NOT NULL,
                description TEXT NOT NULL,
                modified_params TEXT DEFAULT '{}',
                parent_version TEXT DEFAULT '',
                config_hash TEXT DEFAULT '',
                code_hash TEXT DEFAULT '',
                random_seed INTEGER DEFAULT 20261114,
                experiment_conditions TEXT DEFAULT '{}',
                notes TEXT DEFAULT ''
            )
        """)
        _ensure_column(conn, "bot_versions", "code_hash", "TEXT DEFAULT ''")
        _ensure_column(conn, "bot_versions", "random_seed", "INTEGER DEFAULT 20261114")
        _ensure_column(conn, "bot_versions", "experiment_conditions", "TEXT DEFAULT '{}'")

        # 3. Tabla de sesiones de juego / evaluación
        conn.execute("""
            CREATE TABLE IF NOT EXISTS analytics_sessions (
                session_id TEXT PRIMARY KEY,
                version_id TEXT NOT NULL,
                started_at TEXT NOT NULL,
                ended_at TEXT,
                mode TEXT DEFAULT 'live',
                is_synthetic INTEGER DEFAULT 0,
                code_hash TEXT DEFAULT '',
                model_name TEXT DEFAULT '',
                initial_bankroll REAL DEFAULT 1500.0,
                final_bankroll REAL DEFAULT 1500.0,
                buy_in_total REAL DEFAULT 500.0,
                big_blind REAL DEFAULT 20.0,
                hands_played INTEGER DEFAULT 0,
                net_profit_chips REAL DEFAULT 0.0,
                status TEXT DEFAULT 'ACTIVE'
            )
        """)
        _ensure_column(conn, "analytics_sessions", "is_synthetic", "INTEGER DEFAULT 0")
        _ensure_column(conn, "analytics_sessions", "code_hash", "TEXT DEFAULT ''")

        # 4. Tabla de manos jugadas (con clave única hand_uid anti-duplicados)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS analytics_hands (
                hand_uid TEXT PRIMARY KEY,
                session_id TEXT NOT NULL,
                version_id TEXT NOT NULL,
                hand_number INTEGER NOT NULL,
                scenario_id TEXT DEFAULT '',
                is_synthetic INTEGER DEFAULT 0,
                code_hash TEXT DEFAULT '',
                started_at TEXT NOT NULL,
                ended_at TEXT NOT NULL,
                date_str TEXT NOT NULL,
                position TEXT DEFAULT 'DESCONOCIDA',
                hole_cards TEXT DEFAULT '',
                preflop_tier TEXT DEFAULT 'DESCONOCIDO',
                preflop_score INTEGER DEFAULT 0,
                board_flop TEXT DEFAULT '',
                board_turn TEXT DEFAULT '',
                board_river TEXT DEFAULT '',
                final_board TEXT DEFAULT '',
                reached_street TEXT DEFAULT 'PREFLOP',
                saw_flop INTEGER DEFAULT 0,
                went_to_showdown INTEGER DEFAULT 0,
                won_at_showdown INTEGER DEFAULT 0,
                won_hand INTEGER DEFAULT 0,
                vpip_flag INTEGER DEFAULT 0,
                pfr_flag INTEGER DEFAULT 0,
                three_bet_opp INTEGER DEFAULT 0,
                three_bet_flag INTEGER DEFAULT 0,
                faced_three_bet INTEGER DEFAULT 0,
                fold_to_three_bet INTEGER DEFAULT 0,
                cbet_flop_opp INTEGER DEFAULT 0,
                cbet_flop_flag INTEGER DEFAULT 0,
                faced_cbet_flop INTEGER DEFAULT 0,
                fold_to_cbet_flop INTEGER DEFAULT 0,
                hero_stack_start REAL DEFAULT 500.0,
                hero_stack_end REAL DEFAULT 500.0,
                effective_stack REAL DEFAULT 500.0,
                big_blind REAL DEFAULT 20.0,
                pot_final REAL DEFAULT 0.0,
                invested_chips REAL DEFAULT 0.0,
                won_chips REAL DEFAULT 0.0,
                net_profit_chips REAL DEFAULT 0.0,
                net_profit_bb REAL DEFAULT 0.0,
                ev_net_chips REAL DEFAULT 0.0,
                ev_net_bb REAL DEFAULT 0.0,
                allin_ev_chips REAL DEFAULT 0.0,
                allin_ev_bb REAL DEFAULT 0.0,
                is_allin INTEGER DEFAULT 0,
                bankroll_after REAL DEFAULT 1500.0,
                ev_bankroll_after REAL DEFAULT 1500.0,
                opponent_id TEXT DEFAULT '',
                opponent_nick TEXT DEFAULT '',
                opponent_profile TEXT DEFAULT 'DESCONOCIDO',
                result_reason TEXT DEFAULT ''
            )
        """)
        _ensure_column(conn, "analytics_hands", "is_synthetic", "INTEGER DEFAULT 0")
        _ensure_column(conn, "analytics_hands", "code_hash", "TEXT DEFAULT ''")
        _ensure_column(conn, "analytics_hands", "allin_ev_chips", "REAL DEFAULT 0.0")
        _ensure_column(conn, "analytics_hands", "allin_ev_bb", "REAL DEFAULT 0.0")
        _ensure_column(conn, "analytics_hands", "is_allin", "INTEGER DEFAULT 0")

        # 5. Tabla de decisiones detalladas y evaluación de calidad / EV
        conn.execute("""
            CREATE TABLE IF NOT EXISTS analytics_decisions (
                decision_uid TEXT PRIMARY KEY,
                hand_uid TEXT NOT NULL,
                session_id TEXT NOT NULL,
                version_id TEXT NOT NULL,
                scenario_id TEXT DEFAULT '',
                is_synthetic INTEGER DEFAULT 0,
                code_hash TEXT DEFAULT '',
                timestamp TEXT NOT NULL,
                date_str TEXT NOT NULL,
                decision_index INTEGER DEFAULT 1,
                position TEXT DEFAULT 'DESCONOCIDA',
                street TEXT DEFAULT 'PREFLOP',
                hole_cards TEXT DEFAULT '',
                board TEXT DEFAULT '',
                pot_size REAL DEFAULT 0.0,
                to_call REAL DEFAULT 0.0,
                effective_stack REAL DEFAULT 500.0,
                big_blind REAL DEFAULT 20.0,
                pot_size_bucket TEXT DEFAULT 'MEDIUM',
                stack_depth_bucket TEXT DEFAULT 'DEEP',
                intended_action TEXT DEFAULT '',
                action_taken TEXT NOT NULL,
                bet_amount REAL DEFAULT 0.0,
                legal_actions TEXT DEFAULT '[]',
                evaluation_available INTEGER DEFAULT 1,
                gto_recommended_action TEXT NOT NULL,
                gto_recommended_sizing REAL DEFAULT 0.0,
                gto_mixed_freq_json TEXT DEFAULT '{}',
                is_gto_aligned INTEGER DEFAULT 1,
                action_deviation TEXT DEFAULT 'EXACT_MATCH',
                ev_by_action_json TEXT DEFAULT '{}',
                ev_taken_chips REAL DEFAULT 0.0,
                ev_best_chips REAL DEFAULT 0.0,
                ev_lost_chips REAL DEFAULT 0.0,
                ev_lost_bb REAL DEFAULT 0.0,
                estimated_equity REAL DEFAULT 0.5,
                pot_odds_pct REAL DEFAULT 0.0,
                decision_quality TEXT DEFAULT 'OPTIMA',
                is_strategic_error INTEGER DEFAULT 0,
                is_missed_opportunity INTEGER DEFAULT 0,
                error_description TEXT DEFAULT '',
                opponent_id TEXT DEFAULT '',
                opponent_nick TEXT DEFAULT '',
                opponent_profile TEXT DEFAULT 'DESCONOCIDO',
                opponent_adaptation_aligned INTEGER DEFAULT 1,
                decision_source TEXT DEFAULT 'gemini',
                confidence REAL DEFAULT 0.8,
                response_time_ms REAL DEFAULT 0.0,
                enqueue_latency_ms REAL DEFAULT 0.0,
                sqlite_write_ms REAL DEFAULT 0.0,
                hand_final_result_chips REAL DEFAULT 0.0,
                hand_final_result_bb REAL DEFAULT 0.0
            )
        """)
        _ensure_column(conn, "analytics_decisions", "is_synthetic", "INTEGER DEFAULT 0")
        _ensure_column(conn, "analytics_decisions", "code_hash", "TEXT DEFAULT ''")
        _ensure_column(conn, "analytics_decisions", "pot_size_bucket", "TEXT DEFAULT 'MEDIUM'")
        _ensure_column(conn, "analytics_decisions", "stack_depth_bucket", "TEXT DEFAULT 'DEEP'")
        _ensure_column(conn, "analytics_decisions", "intended_action", "TEXT DEFAULT ''")
        _ensure_column(conn, "analytics_decisions", "evaluation_available", "INTEGER DEFAULT 1")
        _ensure_column(conn, "analytics_decisions", "gto_mixed_freq_json", "TEXT DEFAULT '{}'")
        _ensure_column(conn, "analytics_decisions", "is_missed_opportunity", "INTEGER DEFAULT 0")
        _ensure_column(conn, "analytics_decisions", "enqueue_latency_ms", "REAL DEFAULT 0.0")
        _ensure_column(conn, "analytics_decisions", "sqlite_write_ms", "REAL DEFAULT 0.0")
        _ensure_column(conn, "analytics_decisions", "prompt_version", "TEXT DEFAULT ''")
        _ensure_column(conn, "analytics_decisions", "prompt_hash", "TEXT DEFAULT ''")
        _ensure_column(conn, "analytics_decisions", "model_used", "TEXT DEFAULT ''")
        _ensure_column(conn, "analytics_decisions", "retry_count", "INTEGER DEFAULT 0")
        _ensure_column(conn, "analytics_decisions", "raw_response", "TEXT DEFAULT ''")
        _ensure_column(conn, "analytics_decisions", "fallback_reason", "TEXT DEFAULT ''")
        _ensure_column(conn, "analytics_decisions", "trace_json", "TEXT DEFAULT '{}'")
        conn.execute("""
            CREATE TABLE IF NOT EXISTS schema_migrations (
                version TEXT PRIMARY KEY,
                applied_at TEXT NOT NULL
            )
        """)
        conn.execute(
            "INSERT OR IGNORE INTO schema_migrations (version, applied_at) VALUES (?, ?)",
            ("v2_ai_traceability", datetime.now().strftime("%Y-%m-%d %H:%M:%S")),
        )

        # 6. Tabla de verificación de tendencias de oponentes (Showdowns y patrones verificables)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS analytics_opponent_verifications (
                verification_uid TEXT PRIMARY KEY,
                hand_uid TEXT NOT NULL,
                session_id TEXT NOT NULL,
                version_id TEXT NOT NULL,
                is_synthetic INTEGER DEFAULT 0,
                timestamp TEXT NOT NULL,
                opponent_id TEXT NOT NULL,
                opponent_nick TEXT DEFAULT '',
                predicted_profile TEXT NOT NULL,
                predicted_vpip REAL DEFAULT 0.0,
                predicted_pfr REAL DEFAULT 0.0,
                predicted_af REAL DEFAULT 1.0,
                observed_cards TEXT DEFAULT '',
                observed_tier TEXT DEFAULT '',
                observed_line TEXT DEFAULT '',
                is_pattern_verified INTEGER DEFAULT 1,
                verification_note TEXT DEFAULT ''
            )
        """)
        _ensure_column(conn, "analytics_opponent_verifications", "is_synthetic", "INTEGER DEFAULT 0")

        # 7. Tabla de laboratorio de experimentación controlada (Fase 3)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS analytics_experiments (
                experiment_id TEXT PRIMARY KEY,
                version_id TEXT NOT NULL,
                parent_version TEXT DEFAULT '',
                created_at TEXT NOT NULL,
                changes_description TEXT NOT NULL,
                modified_params_json TEXT DEFAULT '{}',
                code_hash TEXT NOT NULL,
                config_hash TEXT NOT NULL,
                random_seeds_json TEXT DEFAULT '[]',
                conditions_json TEXT DEFAULT '{}',
                num_scenarios INTEGER DEFAULT 0,
                results_json TEXT DEFAULT '{}',
                ci95_json TEXT DEFAULT '{}',
                stat_diff_json TEXT DEFAULT '{}',
                verdict_state TEXT DEFAULT 'INCONCLUSO'
            )
        """)

        # 8. Tabla de telemetría de rendimiento y confiabilidad del sistema (Fase 6)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS system_telemetry (
                telemetry_id TEXT PRIMARY KEY,
                session_id TEXT DEFAULT '',
                version_id TEXT DEFAULT '',
                timestamp TEXT NOT NULL,
                event_type TEXT DEFAULT 'DECISION_WRITE',
                decision_latency_ms REAL DEFAULT 0.0,
                enqueue_latency_ms REAL DEFAULT 0.0,
                sqlite_write_ms REAL DEFAULT 0.0,
                stat_processing_ms REAL DEFAULT 0.0,
                memory_rss_mb REAL DEFAULT 0.0,
                pending_tasks INTEGER DEFAULT 0,
                dropped_records INTEGER DEFAULT 0,
                storage_errors INTEGER DEFAULT 0
            )
        """)

        # Asegurar etiquetado retroactivo de sesiones/manos/decisiones de benchmark como is_synthetic = 1
        conn.execute("UPDATE analytics_sessions SET is_synthetic = 1 WHERE mode = 'benchmark' OR session_id LIKE 'bench_%';")
        conn.execute("UPDATE analytics_hands SET is_synthetic = 1 WHERE session_id LIKE 'bench_%' OR scenario_id LIKE 'SCEN_%';")
        conn.execute("UPDATE analytics_hands SET allin_ev_chips = ev_net_chips, allin_ev_bb = ev_net_bb WHERE allin_ev_chips = 0.0 AND ev_net_chips != 0.0;")
        conn.execute("UPDATE analytics_decisions SET is_synthetic = 1 WHERE session_id LIKE 'bench_%' OR scenario_id LIKE 'SCEN_%';")
        conn.execute("UPDATE analytics_opponent_verifications SET is_synthetic = 1 WHERE session_id LIKE 'bench_%';")

        # Índices para consultas analíticas y filtros ultrarrápidos
        conn.execute("CREATE INDEX IF NOT EXISTS idx_hands_version ON analytics_hands(version_id);")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_hands_session ON analytics_hands(session_id);")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_hands_synthetic ON analytics_hands(is_synthetic);")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_hands_date ON analytics_hands(date_str);")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_hands_pos ON analytics_hands(position);")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_hands_opp ON analytics_hands(opponent_profile);")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_decisions_version ON analytics_decisions(version_id);")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_decisions_hand ON analytics_decisions(hand_uid);")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_decisions_synthetic ON analytics_decisions(is_synthetic);")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_decisions_street ON analytics_decisions(street);")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_decisions_pos ON analytics_decisions(position);")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_decisions_opp ON analytics_decisions(opponent_profile);")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_telemetry_ts ON system_telemetry(timestamp);")
        conn.commit()


def generar_uid_determinista(*partes: Any) -> str:
    """Genera un identificador SHA-256 corto y determinista para evitar registros duplicados."""
    raw = "|".join(str(p) for p in partes)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]


class AnalyticsStorage:
    """
    Gestor de persistencia para versiones, sesiones, manos, decisiones, experimentos,
    telemetría de sistema y exportación trazable.
    """

    def __init__(self, db_path: Path = DB_PATH):
        self.db_path = Path(db_path)
        self.default_code_hash = calcular_hash_codigo_proyecto()
        inicializar_esquema_analiticas(self.db_path)

    # ------------------------------------------------------------------
    # GESTIÓN DE VERSIONES Y EXPERIMENTOS CONTROLADOS (FASE 3)
    # ------------------------------------------------------------------
    def registrar_version(
        self,
        version_id: str,
        description: str,
        modified_params: Optional[Dict[str, Any]] = None,
        parent_version: str = "",
        created_at: Optional[str] = None,
        notes: str = "",
        code_hash: Optional[str] = None,
        random_seed: int = 20261114,
        experiment_conditions: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        params_dict = modified_params or {}
        params_json = json.dumps(params_dict, ensure_ascii=False, sort_keys=True)
        config_hash = hashlib.sha256(f"{version_id}|{params_json}".encode("utf-8")).hexdigest()[:12]
        c_hash = code_hash or self.default_code_hash
        cond_json = json.dumps(experiment_conditions or {}, ensure_ascii=False, sort_keys=True)
        ts = created_at or datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        with get_connection(self.db_path) as conn:
            conn.execute(
                """
                INSERT INTO bot_versions (
                    version_id, created_at, description, modified_params, parent_version,
                    config_hash, code_hash, random_seed, experiment_conditions, notes
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(version_id) DO UPDATE SET
                    description = excluded.description,
                    modified_params = excluded.modified_params,
                    parent_version = COALESCE(NULLIF(excluded.parent_version, ''), bot_versions.parent_version),
                    config_hash = excluded.config_hash,
                    code_hash = excluded.code_hash,
                    random_seed = excluded.random_seed,
                    experiment_conditions = excluded.experiment_conditions,
                    notes = COALESCE(NULLIF(excluded.notes, ''), bot_versions.notes)
                """,
                (version_id, ts, description, params_json, parent_version, config_hash, c_hash, int(random_seed), cond_json, notes),
            )
            conn.commit()

        return {
            "version_id": version_id,
            "created_at": ts,
            "description": description,
            "modified_params": params_dict,
            "parent_version": parent_version,
            "config_hash": config_hash,
            "code_hash": c_hash,
            "random_seed": int(random_seed),
            "experiment_conditions": experiment_conditions or {},
            "notes": notes,
        }

    def obtener_versiones(self) -> List[Dict[str, Any]]:
        with get_connection(self.db_path) as conn:
            rows = conn.execute("SELECT * FROM bot_versions ORDER BY created_at ASC, version_id ASC").fetchall()
            result = []
            for r in rows:
                d = dict(r)
                try:
                    d["modified_params_dict"] = json.loads(d.get("modified_params") or "{}")
                except Exception:
                    d["modified_params_dict"] = {}
                try:
                    d["experiment_conditions_dict"] = json.loads(d.get("experiment_conditions") or "{}")
                except Exception:
                    d["experiment_conditions_dict"] = {}
                result.append(d)
            return result

    def guardar_experimento(self, exp_data: Dict[str, Any]) -> str:
        """Registra un experimento controlado en `analytics_experiments` con semillas, hashes e ICs."""
        vid = str(exp_data.get("version_id") or "1.0")
        seeds = exp_data.get("random_seeds") or [20261114]
        num_scen = int(exp_data.get("num_scenarios") or 0)
        exp_id = exp_data.get("experiment_id") or f"exp_v{vid.replace('.', '_')}_{num_scen}_{seeds[0] if seeds else 0}"
        ts = str(exp_data.get("created_at") or datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
        c_hash = str(exp_data.get("code_hash") or self.default_code_hash)
        cfg_hash = str(exp_data.get("config_hash") or generar_uid_determinista(vid, exp_data.get("modified_params", {}))[:12])

        with get_connection(self.db_path) as conn:
            conn.execute(
                """
                INSERT INTO analytics_experiments (
                    experiment_id, version_id, parent_version, created_at, changes_description,
                    modified_params_json, code_hash, config_hash, random_seeds_json,
                    conditions_json, num_scenarios, results_json, ci95_json, stat_diff_json, verdict_state
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(experiment_id) DO UPDATE SET
                    created_at = excluded.created_at,
                    changes_description = excluded.changes_description,
                    modified_params_json = excluded.modified_params_json,
                    code_hash = excluded.code_hash,
                    config_hash = excluded.config_hash,
                    random_seeds_json = excluded.random_seeds_json,
                    conditions_json = excluded.conditions_json,
                    num_scenarios = excluded.num_scenarios,
                    results_json = excluded.results_json,
                    ci95_json = excluded.ci95_json,
                    stat_diff_json = excluded.stat_diff_json,
                    verdict_state = excluded.verdict_state
                """,
                (
                    exp_id,
                    vid,
                    str(exp_data.get("parent_version") or ""),
                    ts,
                    str(exp_data.get("changes_description") or ""),
                    json.dumps(exp_data.get("modified_params") or {}, ensure_ascii=False, sort_keys=True),
                    c_hash,
                    cfg_hash,
                    json.dumps(seeds, ensure_ascii=False),
                    json.dumps(exp_data.get("conditions") or {}, ensure_ascii=False),
                    num_scen,
                    json.dumps(exp_data.get("results") or {}, ensure_ascii=False),
                    json.dumps(exp_data.get("ci95") or {}, ensure_ascii=False),
                    json.dumps(exp_data.get("stat_diff") or {}, ensure_ascii=False),
                    str(exp_data.get("verdict_state") or "INCONCLUSO"),
                ),
            )
            conn.commit()
        return exp_id

    def obtener_experimentos(self) -> List[Dict[str, Any]]:
        with get_connection(self.db_path) as conn:
            rows = conn.execute("SELECT * FROM analytics_experiments ORDER BY created_at ASC, version_id ASC").fetchall()
            out = []
            for r in rows:
                d = dict(r)
                for k, target in (
                    ("modified_params_json", "modified_params"),
                    ("random_seeds_json", "random_seeds"),
                    ("conditions_json", "conditions"),
                    ("results_json", "results"),
                    ("ci95_json", "ci95"),
                    ("stat_diff_json", "stat_diff"),
                ):
                    try:
                        d[target] = json.loads(d.get(k) or "{}")
                    except Exception:
                        d[target] = {}
                out.append(d)
            return out

    # ------------------------------------------------------------------
    # GESTIÓN DE SESIONES
    # ------------------------------------------------------------------
    def iniciar_sesion(
        self,
        version_id: str,
        mode: str = "live",
        model_name: str = "gemini-3.1-flash-lite",
        initial_bankroll: float = 1500.0,
        buy_in_total: float = 500.0,
        big_blind: float = 20.0,
        session_id: Optional[str] = None,
        started_at: Optional[str] = None,
        is_synthetic: Optional[bool] = None,
        code_hash: Optional[str] = None,
    ) -> str:
        sid = session_id or f"ses_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}"
        ts = started_at or datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        synth_flag = int(bool(is_synthetic)) if is_synthetic is not None else (1 if mode == "benchmark" else 0)
        c_hash = code_hash or self.default_code_hash

        with get_connection(self.db_path) as conn:
            cur = conn.execute("SELECT version_id FROM bot_versions WHERE version_id = ?", (version_id,)).fetchone()
            if not cur:
                conn.execute(
                    """
                    INSERT INTO bot_versions (version_id, created_at, description, modified_params, parent_version, config_hash, code_hash)
                    VALUES (?, ?, ?, '{}', '', ?, ?)
                    """,
                    (version_id, ts, f"Versión {version_id} registrada automáticamente", generar_uid_determinista(version_id)[:12], c_hash),
                )

            conn.execute(
                """
                INSERT INTO analytics_sessions (
                    session_id, version_id, started_at, mode, is_synthetic, code_hash, model_name,
                    initial_bankroll, final_bankroll, buy_in_total, big_blind, hands_played, net_profit_chips, status
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, 0.0, 'ACTIVE')
                ON CONFLICT(session_id) DO UPDATE SET
                    is_synthetic = excluded.is_synthetic,
                    code_hash = excluded.code_hash
                """,
                (sid, version_id, ts, mode, synth_flag, c_hash, model_name, initial_bankroll, initial_bankroll, buy_in_total, big_blind),
            )
            conn.commit()
        return sid

    def actualizar_sesion(self, session_id: str, ended_at: Optional[str] = None, status: str = "COMPLETED") -> None:
        ts = ended_at or datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with get_connection(self.db_path) as conn:
            stats = conn.execute(
                """
                SELECT COUNT(*) as cnt, COALESCE(SUM(net_profit_chips), 0.0) as net
                FROM analytics_hands WHERE session_id = ?
                """,
                (session_id,),
            ).fetchone()
            cnt = int(stats["cnt"]) if stats else 0
            net = float(stats["net"]) if stats else 0.0

            ses = conn.execute(
                "SELECT initial_bankroll FROM analytics_sessions WHERE session_id = ?",
                (session_id,),
            ).fetchone()
            init_br = float(ses["initial_bankroll"]) if ses else 1500.0

            conn.execute(
                """
                UPDATE analytics_sessions
                SET ended_at = ?, hands_played = ?, net_profit_chips = ?, final_bankroll = ?, status = ?
                WHERE session_id = ?
                """,
                (ts, cnt, round(net, 2), round(init_br + net, 2), status, session_id),
            )
            conn.commit()

    def obtener_sesiones(self, version_id: Optional[str] = None, data_source: Optional[str] = None) -> List[Dict[str, Any]]:
        with get_connection(self.db_path) as conn:
            clauses = []
            params: List[Any] = []
            if version_id and version_id != "ALL":
                clauses.append("version_id = ?")
                params.append(version_id)
            if data_source == "live":
                clauses.append("is_synthetic = 0")
            elif data_source == "benchmark":
                clauses.append("is_synthetic = 1")
            where_sql = (" WHERE " + " AND ".join(clauses)) if clauses else ""
            rows = conn.execute(f"SELECT * FROM analytics_sessions {where_sql} ORDER BY started_at DESC", params).fetchall()
            return [dict(r) for r in rows]

    # ------------------------------------------------------------------
    # REGISTRO DE MANOS Y DECISIONES (CON DEDUPLICACIÓN ESTRICTA)
    # ------------------------------------------------------------------
    def guardar_mano(self, hand_data: Dict[str, Any]) -> str:
        """
        Inserta o actualiza una mano jugada evitando registros duplicados mediante `hand_uid`.
        """
        session_id = str(hand_data.get("session_id") or "default_session")
        version_id = str(hand_data.get("version_id") or "1.0")
        hand_number = int(hand_data.get("hand_number") or 1)
        scenario_id = str(hand_data.get("scenario_id") or "")
        hole_cards = str(hand_data.get("hole_cards") or "")

        # Determinismo estricto por (session_id, version_id, hand_number, scenario_id)
        hand_uid = hand_data.get("hand_uid") or generar_uid_determinista(
            session_id, version_id, hand_number, scenario_id
        )

        is_synth = int(bool(hand_data.get("is_synthetic", session_id.startswith("bench_") or bool(scenario_id))))
        c_hash = str(hand_data.get("code_hash") or self.default_code_hash)

        started_at = str(hand_data.get("started_at") or datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
        ended_at = str(hand_data.get("ended_at") or started_at)
        date_str = ended_at[:10]

        big_blind = max(1.0, float(hand_data.get("big_blind") or 20.0))
        net_chips = float(hand_data.get("net_profit_chips") or 0.0)
        net_bb = float(hand_data.get("net_profit_bb") if "net_profit_bb" in hand_data else (net_chips / big_blind))
        ev_chips = float(hand_data.get("ev_net_chips") if "ev_net_chips" in hand_data else net_chips)
        ev_bb = float(hand_data.get("ev_net_bb") if "ev_net_bb" in hand_data else (ev_chips / big_blind))
        allin_ev_chips = float(hand_data.get("allin_ev_chips") if "allin_ev_chips" in hand_data else ev_chips)
        allin_ev_bb = float(hand_data.get("allin_ev_bb") if "allin_ev_bb" in hand_data else (allin_ev_chips / big_blind))
        is_allin = int(bool(hand_data.get("is_allin", False)))

        with get_connection(self.db_path) as conn:
            conn.execute(
                """
                INSERT INTO analytics_hands (
                    hand_uid, session_id, version_id, hand_number, scenario_id,
                    is_synthetic, code_hash, started_at, ended_at, date_str,
                    position, hole_cards, preflop_tier, preflop_score,
                    board_flop, board_turn, board_river, final_board,
                    reached_street, saw_flop, went_to_showdown, won_at_showdown,
                    won_hand, vpip_flag, pfr_flag, three_bet_opp, three_bet_flag,
                    faced_three_bet, fold_to_three_bet, cbet_flop_opp, cbet_flop_flag,
                    faced_cbet_flop, fold_to_cbet_flop, hero_stack_start, hero_stack_end,
                    effective_stack, big_blind, pot_final, invested_chips, won_chips,
                    net_profit_chips, net_profit_bb, ev_net_chips, ev_net_bb,
                    allin_ev_chips, allin_ev_bb, is_allin,
                    bankroll_after, ev_bankroll_after, opponent_id, opponent_nick,
                    opponent_profile, result_reason
                ) VALUES (
                    ?, ?, ?, ?, ?,
                    ?, ?, ?, ?, ?,
                    ?, ?, ?, ?,
                    ?, ?, ?, ?,
                    ?, ?, ?, ?,
                    ?, ?, ?, ?, ?,
                    ?, ?, ?, ?,
                    ?, ?, ?, ?,
                    ?, ?, ?, ?, ?,
                    ?, ?, ?, ?,
                    ?, ?, ?,
                    ?, ?, ?, ?,
                    ?, ?
                )
                ON CONFLICT(hand_uid) DO UPDATE SET
                    is_synthetic = excluded.is_synthetic,
                    code_hash = excluded.code_hash,
                    ended_at = excluded.ended_at,
                    hole_cards = COALESCE(NULLIF(excluded.hole_cards, ''), analytics_hands.hole_cards),
                    final_board = excluded.final_board,
                    reached_street = excluded.reached_street,
                    saw_flop = excluded.saw_flop,
                    went_to_showdown = excluded.went_to_showdown,
                    won_at_showdown = excluded.won_at_showdown,
                    won_hand = excluded.won_hand,
                    vpip_flag = excluded.vpip_flag,
                    pfr_flag = excluded.pfr_flag,
                    three_bet_opp = excluded.three_bet_opp,
                    three_bet_flag = excluded.three_bet_flag,
                    faced_three_bet = excluded.faced_three_bet,
                    fold_to_three_bet = excluded.fold_to_three_bet,
                    cbet_flop_opp = excluded.cbet_flop_opp,
                    cbet_flop_flag = excluded.cbet_flop_flag,
                    faced_cbet_flop = excluded.faced_cbet_flop,
                    fold_to_cbet_flop = excluded.fold_to_cbet_flop,
                    hero_stack_end = excluded.hero_stack_end,
                    pot_final = excluded.pot_final,
                    invested_chips = excluded.invested_chips,
                    won_chips = excluded.won_chips,
                    net_profit_chips = excluded.net_profit_chips,
                    net_profit_bb = excluded.net_profit_bb,
                    ev_net_chips = excluded.ev_net_chips,
                    ev_net_bb = excluded.ev_net_bb,
                    allin_ev_chips = excluded.allin_ev_chips,
                    allin_ev_bb = excluded.allin_ev_bb,
                    is_allin = excluded.is_allin,
                    bankroll_after = excluded.bankroll_after,
                    ev_bankroll_after = excluded.ev_bankroll_after,
                    result_reason = excluded.result_reason
                """,
                (
                    hand_uid,
                    session_id,
                    version_id,
                    hand_number,
                    scenario_id,
                    is_synth,
                    c_hash,
                    started_at,
                    ended_at,
                    date_str,
                    str(hand_data.get("position") or "DESCONOCIDA"),
                    hole_cards,
                    str(hand_data.get("preflop_tier") or "DESCONOCIDO"),
                    int(hand_data.get("preflop_score") or 0),
                    str(hand_data.get("board_flop") or ""),
                    str(hand_data.get("board_turn") or ""),
                    str(hand_data.get("board_river") or ""),
                    str(hand_data.get("final_board") or ""),
                    str(hand_data.get("reached_street") or "PREFLOP"),
                    int(bool(hand_data.get("saw_flop"))),
                    int(bool(hand_data.get("went_to_showdown"))),
                    int(bool(hand_data.get("won_at_showdown"))),
                    int(bool(hand_data.get("won_hand"))),
                    int(bool(hand_data.get("vpip_flag"))),
                    int(bool(hand_data.get("pfr_flag"))),
                    int(bool(hand_data.get("three_bet_opp"))),
                    int(bool(hand_data.get("three_bet_flag"))),
                    int(bool(hand_data.get("faced_three_bet"))),
                    int(bool(hand_data.get("fold_to_three_bet"))),
                    int(bool(hand_data.get("cbet_flop_opp"))),
                    int(bool(hand_data.get("cbet_flop_flag"))),
                    int(bool(hand_data.get("faced_cbet_flop"))),
                    int(bool(hand_data.get("fold_to_cbet_flop"))),
                    float(hand_data.get("hero_stack_start") or 500.0),
                    float(hand_data.get("hero_stack_end") or 500.0),
                    float(hand_data.get("effective_stack") or 500.0),
                    big_blind,
                    float(hand_data.get("pot_final") or 0.0),
                    float(hand_data.get("invested_chips") or 0.0),
                    float(hand_data.get("won_chips") or 0.0),
                    round(net_chips, 2),
                    round(net_bb, 4),
                    round(ev_chips, 2),
                    round(ev_bb, 4),
                    round(allin_ev_chips, 2),
                    round(allin_ev_bb, 4),
                    is_allin,
                    float(hand_data.get("bankroll_after") or 1500.0),
                    float(hand_data.get("ev_bankroll_after") or 1500.0),
                    str(hand_data.get("opponent_id") or ""),
                    str(hand_data.get("opponent_nick") or ""),
                    str(hand_data.get("opponent_profile") or "DESCONOCIDO"),
                    str(hand_data.get("result_reason") or ""),
                ),
            )

            conn.execute(
                """
                UPDATE analytics_decisions
                SET hand_final_result_chips = ?, hand_final_result_bb = ?
                WHERE hand_uid = ?
                """,
                (round(net_chips, 2), round(net_bb, 4), hand_uid),
            )
            conn.commit()

        return hand_uid

    def guardar_decision(self, decision_data: Dict[str, Any]) -> str:
        """
        Registra una decisión realmente ejecutada con su análisis de EV y calidad.
        Evita duplicados mediante `decision_uid`.
        """
        hand_uid = str(decision_data.get("hand_uid") or "unknown_hand")
        street = str(decision_data.get("street") or "PREFLOP").upper()
        decision_idx = int(decision_data.get("decision_index") or 1)
        pot_size = float(decision_data.get("pot_size") or 0.0)
        board = str(decision_data.get("board") or "")

        decision_uid = decision_data.get("decision_uid") or generar_uid_determinista(
            hand_uid, street, decision_idx, pot_size, board
        )

        session_id = str(decision_data.get("session_id") or "default_session")
        scenario_id = str(decision_data.get("scenario_id") or "")
        is_synth = int(bool(decision_data.get("is_synthetic", session_id.startswith("bench_") or bool(scenario_id))))
        c_hash = str(decision_data.get("code_hash") or self.default_code_hash)

        ts = str(decision_data.get("timestamp") or datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
        date_str = ts[:10]

        legal_actions = decision_data.get("legal_actions") or []
        legal_actions_json = json.dumps(legal_actions, ensure_ascii=False) if isinstance(legal_actions, list) else str(legal_actions)

        ev_by_action = decision_data.get("ev_by_action") or {}
        ev_by_action_json = json.dumps(ev_by_action, ensure_ascii=False) if isinstance(ev_by_action, dict) else str(ev_by_action)

        gto_mixed = decision_data.get("gto_mixed_frequencies") or {}
        gto_mixed_json = json.dumps(gto_mixed, ensure_ascii=False) if isinstance(gto_mixed, dict) else str(gto_mixed)

        bb = max(1.0, float(decision_data.get("big_blind") or 20.0))
        eff_stack = float(decision_data.get("effective_stack") or 500.0)

        pot_bb = pot_size / bb
        pot_bucket = str(decision_data.get("pot_size_bucket") or ("SMALL (<4 BB)" if pot_bb < 4.0 else ("MEDIUM (4-12 BB)" if pot_bb <= 12.0 else "LARGE (>12 BB)")))
        stack_bb = eff_stack / bb
        stack_bucket = str(decision_data.get("stack_depth_bucket") or ("SHORT (<10 BB)" if stack_bb < 10.0 else ("MEDIUM (10-20 BB)" if stack_bb <= 20.0 else "DEEP (>20 BB)")))

        action_taken = str(decision_data.get("action_taken") or "WAIT")
        intended_action = str(decision_data.get("intended_action") or action_taken)
        trace_val = decision_data.get("trace_json")
        if isinstance(trace_val, dict):
            trace_json_str = json.dumps(trace_val, ensure_ascii=False)
        else:
            trace_json_str = str(trace_val or "{}")

        with get_connection(self.db_path) as conn:
            conn.execute(
                """
                INSERT INTO analytics_decisions (
                    decision_uid, hand_uid, session_id, version_id, scenario_id,
                    is_synthetic, code_hash, timestamp, date_str, decision_index,
                    position, street, hole_cards, board, pot_size, to_call,
                    effective_stack, big_blind, pot_size_bucket, stack_depth_bucket,
                    intended_action, action_taken, bet_amount, legal_actions,
                    evaluation_available, gto_recommended_action, gto_recommended_sizing,
                    gto_mixed_freq_json, is_gto_aligned, action_deviation,
                    ev_by_action_json, ev_taken_chips, ev_best_chips, ev_lost_chips,
                    ev_lost_bb, estimated_equity, pot_odds_pct, decision_quality,
                    is_strategic_error, is_missed_opportunity, error_description,
                    opponent_id, opponent_nick, opponent_profile,
                    opponent_adaptation_aligned, decision_source, confidence,
                    response_time_ms, enqueue_latency_ms, sqlite_write_ms,
                    hand_final_result_chips, hand_final_result_bb,
                    prompt_version, prompt_hash, model_used, retry_count,
                    raw_response, fallback_reason, trace_json
                ) VALUES (
                    ?, ?, ?, ?, ?,
                    ?, ?, ?, ?, ?,
                    ?, ?, ?, ?, ?, ?,
                    ?, ?, ?, ?,
                    ?, ?, ?, ?,
                    ?, ?, ?,
                    ?, ?, ?,
                    ?, ?, ?, ?,
                    ?, ?, ?, ?,
                    ?, ?, ?,
                    ?, ?, ?,
                    ?, ?, ?,
                    ?, ?, ?,
                    ?, ?,
                    ?, ?, ?, ?,
                    ?, ?, ?
                )
                ON CONFLICT(decision_uid) DO UPDATE SET
                    is_synthetic = excluded.is_synthetic,
                    code_hash = excluded.code_hash,
                    intended_action = excluded.intended_action,
                    action_taken = excluded.action_taken,
                    bet_amount = excluded.bet_amount,
                    evaluation_available = excluded.evaluation_available,
                    gto_recommended_action = excluded.gto_recommended_action,
                    gto_mixed_freq_json = excluded.gto_mixed_freq_json,
                    is_gto_aligned = excluded.is_gto_aligned,
                    action_deviation = excluded.action_deviation,
                    ev_by_action_json = excluded.ev_by_action_json,
                    ev_taken_chips = excluded.ev_taken_chips,
                    ev_best_chips = excluded.ev_best_chips,
                    ev_lost_chips = excluded.ev_lost_chips,
                    ev_lost_bb = excluded.ev_lost_bb,
                    decision_quality = excluded.decision_quality,
                    is_strategic_error = excluded.is_strategic_error,
                    is_missed_opportunity = excluded.is_missed_opportunity,
                    error_description = excluded.error_description,
                    enqueue_latency_ms = excluded.enqueue_latency_ms,
                    sqlite_write_ms = excluded.sqlite_write_ms,
                    hand_final_result_chips = excluded.hand_final_result_chips,
                    hand_final_result_bb = excluded.hand_final_result_bb,
                    prompt_version = excluded.prompt_version,
                    prompt_hash = excluded.prompt_hash,
                    model_used = excluded.model_used,
                    retry_count = excluded.retry_count,
                    raw_response = excluded.raw_response,
                    fallback_reason = excluded.fallback_reason,
                    trace_json = excluded.trace_json
                """,
                (
                    decision_uid,
                    hand_uid,
                    session_id,
                    str(decision_data.get("version_id") or "1.0"),
                    scenario_id,
                    is_synth,
                    c_hash,
                    ts,
                    date_str,
                    decision_idx,
                    str(decision_data.get("position") or "DESCONOCIDA"),
                    street,
                    str(decision_data.get("hole_cards") or ""),
                    board,
                    pot_size,
                    float(decision_data.get("to_call") or 0.0),
                    eff_stack,
                    bb,
                    pot_bucket,
                    stack_bucket,
                    intended_action,
                    action_taken,
                    float(decision_data.get("bet_amount") or 0.0),
                    legal_actions_json,
                    int(bool(decision_data.get("evaluation_available", True))),
                    str(decision_data.get("gto_recommended_action") or "CHECK"),
                    float(decision_data.get("gto_recommended_sizing") or 0.0),
                    gto_mixed_json,
                    int(bool(decision_data.get("is_gto_aligned", True))),
                    str(decision_data.get("action_deviation") or "EXACT_MATCH"),
                    ev_by_action_json,
                    round(float(decision_data.get("ev_taken_chips") or 0.0), 2),
                    round(float(decision_data.get("ev_best_chips") or 0.0), 2),
                    round(float(decision_data.get("ev_lost_chips") or 0.0), 2),
                    round(float(decision_data.get("ev_lost_bb") or 0.0), 4),
                    round(float(decision_data.get("estimated_equity") or 0.5), 4),
                    round(float(decision_data.get("pot_odds_pct") or 0.0), 2),
                    str(decision_data.get("decision_quality") or "OPTIMA"),
                    int(bool(decision_data.get("is_strategic_error", False))),
                    int(bool(decision_data.get("is_missed_opportunity", False))),
                    str(decision_data.get("error_description") or ""),
                    str(decision_data.get("opponent_id") or ""),
                    str(decision_data.get("opponent_nick") or ""),
                    str(decision_data.get("opponent_profile") or "DESCONOCIDO"),
                    int(bool(decision_data.get("opponent_adaptation_aligned", True))),
                    str(decision_data.get("decision_source") or "gemini"),
                    round(float(decision_data.get("confidence") or 0.8), 3),
                    round(float(decision_data.get("response_time_ms") or 0.0), 3),
                    round(float(decision_data.get("enqueue_latency_ms") or 0.0), 4),
                    round(float(decision_data.get("sqlite_write_ms") or 0.0), 4),
                    round(float(decision_data.get("hand_final_result_chips") or 0.0), 2),
                    round(float(decision_data.get("hand_final_result_bb") or 0.0), 4),
                    str(decision_data.get("prompt_version") or ""),
                    str(decision_data.get("prompt_hash") or ""),
                    str(decision_data.get("model_used") or ""),
                    int(decision_data.get("retry_count") or 0),
                    str(decision_data.get("raw_response") or "")[:2000],
                    str(decision_data.get("fallback_reason") or ""),
                    trace_json_str,
                ),
            )
            conn.commit()
        return decision_uid

    def guardar_verificacion_oponente(self, verif_data: Dict[str, Any]) -> str:
        hand_uid = str(verif_data.get("hand_uid") or "")
        opp_id = str(verif_data.get("opponent_id") or "")
        verif_uid = verif_data.get("verification_uid") or generar_uid_determinista(hand_uid, opp_id, verif_data.get("observed_cards", ""))
        ts = str(verif_data.get("timestamp") or datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
        session_id = str(verif_data.get("session_id") or "")
        is_synth = int(bool(verif_data.get("is_synthetic", session_id.startswith("bench_"))))

        with get_connection(self.db_path) as conn:
            conn.execute(
                """
                INSERT INTO analytics_opponent_verifications (
                    verification_uid, hand_uid, session_id, version_id, is_synthetic, timestamp,
                    opponent_id, opponent_nick, predicted_profile, predicted_vpip,
                    predicted_pfr, predicted_af, observed_cards, observed_tier,
                    observed_line, is_pattern_verified, verification_note
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(verification_uid) DO UPDATE SET
                    is_synthetic = excluded.is_synthetic,
                    is_pattern_verified = excluded.is_pattern_verified,
                    verification_note = excluded.verification_note
                """,
                (
                    verif_uid,
                    hand_uid,
                    session_id,
                    str(verif_data.get("version_id") or "1.0"),
                    is_synth,
                    ts,
                    opp_id,
                    str(verif_data.get("opponent_nick") or ""),
                    str(verif_data.get("predicted_profile") or "DESCONOCIDO"),
                    float(verif_data.get("predicted_vpip") or 0.0),
                    float(verif_data.get("predicted_pfr") or 0.0),
                    float(verif_data.get("predicted_af") or 1.0),
                    str(verif_data.get("observed_cards") or ""),
                    str(verif_data.get("observed_tier") or ""),
                    str(verif_data.get("observed_line") or ""),
                    int(bool(verif_data.get("is_pattern_verified", True))),
                    str(verif_data.get("verification_note") or ""),
                ),
            )
            conn.commit()
        return verif_uid

    # ------------------------------------------------------------------
    # TELEMETRÍA Y DIAGNÓSTICO DE INTEGRIDAD (FASE 6)
    # ------------------------------------------------------------------
    def registrar_telemetria(self, telemetry_data: Dict[str, Any]) -> str:
        tid = str(telemetry_data.get("telemetry_id") or f"tel_{uuid.uuid4().hex[:16]}")
        ts = str(telemetry_data.get("timestamp") or datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
        with get_connection(self.db_path) as conn:
            conn.execute(
                """
                INSERT INTO system_telemetry (
                    telemetry_id, session_id, version_id, timestamp, event_type,
                    decision_latency_ms, enqueue_latency_ms, sqlite_write_ms,
                    stat_processing_ms, memory_rss_mb, pending_tasks,
                    dropped_records, storage_errors
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    tid,
                    str(telemetry_data.get("session_id") or ""),
                    str(telemetry_data.get("version_id") or "1.0"),
                    ts,
                    str(telemetry_data.get("event_type") or "DECISION_WRITE"),
                    round(float(telemetry_data.get("decision_latency_ms") or 0.0), 4),
                    round(float(telemetry_data.get("enqueue_latency_ms") or 0.0), 4),
                    round(float(telemetry_data.get("sqlite_write_ms") or 0.0), 4),
                    round(float(telemetry_data.get("stat_processing_ms") or 0.0), 4),
                    round(float(telemetry_data.get("memory_rss_mb") or 0.0), 2),
                    int(telemetry_data.get("pending_tasks") or 0),
                    int(telemetry_data.get("dropped_records") or 0),
                    int(telemetry_data.get("storage_errors") or 0),
                ),
            )
            conn.commit()
        return tid

    def consultar_telemetria(self, limit: int = 500) -> List[Dict[str, Any]]:
        with get_connection(self.db_path) as conn:
            rows = conn.execute(
                "SELECT * FROM system_telemetry ORDER BY timestamp DESC LIMIT ?",
                (int(limit),),
            ).fetchall()
            return [dict(r) for r in rows]

    def verificar_integridad_db(self) -> Dict[str, Any]:
        """Ejecuta comprobaciones físicas y lógicas de integridad en SQLite (`PRAGMA integrity_check`)."""
        with get_connection(self.db_path) as conn:
            integrity_row = conn.execute("PRAGMA integrity_check;").fetchone()
            integrity_status = str(integrity_row[0]) if integrity_row else "unknown"
            journal_row = conn.execute("PRAGMA journal_mode;").fetchone()
            journal_mode = str(journal_row[0]) if journal_row else "unknown"

            dup_hands = conn.execute(
                """
                SELECT session_id, version_id, hand_number, scenario_id, COUNT(*) as c
                FROM analytics_hands
                GROUP BY session_id, version_id, hand_number, scenario_id
                HAVING c > 1
                """
            ).fetchall()

            dup_decs = conn.execute(
                """
                SELECT hand_uid, street, decision_index, pot_size, board, COUNT(*) as c
                FROM analytics_decisions
                GROUP BY hand_uid, street, decision_index, pot_size, board
                HAVING c > 1
                """
            ).fetchall()

            counts = {
                "live_hands": int(conn.execute("SELECT COUNT(*) FROM analytics_hands WHERE is_synthetic = 0").fetchone()[0]),
                "benchmark_hands": int(conn.execute("SELECT COUNT(*) FROM analytics_hands WHERE is_synthetic = 1").fetchone()[0]),
                "live_decisions": int(conn.execute("SELECT COUNT(*) FROM analytics_decisions WHERE is_synthetic = 0").fetchone()[0]),
                "benchmark_decisions": int(conn.execute("SELECT COUNT(*) FROM analytics_decisions WHERE is_synthetic = 1").fetchone()[0]),
                "opponents_profiled": int(conn.execute("SELECT COUNT(*) FROM oponentes").fetchone()[0]),
                "versions_registered": int(conn.execute("SELECT COUNT(*) FROM bot_versions").fetchone()[0]),
                "experiments_registered": int(conn.execute("SELECT COUNT(*) FROM analytics_experiments").fetchone()[0]),
                "telemetry_events": int(conn.execute("SELECT COUNT(*) FROM system_telemetry").fetchone()[0]),
            }

        db_size_kb = round(self.db_path.stat().st_size / 1024.0, 2) if self.db_path.exists() else 0.0
        return {
            "sqlite_integrity_check": integrity_status,
            "is_healthy": integrity_status.lower() == "ok" and len(dup_hands) == 0 and len(dup_decs) == 0,
            "journal_mode": journal_mode,
            "duplicate_hands_groups": len(dup_hands),
            "duplicate_decisions_groups": len(dup_decs),
            "db_size_kb": db_size_kb,
            "code_hash": self.default_code_hash,
            "counts": counts,
        }

    # ------------------------------------------------------------------
    # CONSULTAS CON FILTROS MULTIDIMENSIONALES Y SEPARACIÓN LIVE/BENCHMARK
    # ------------------------------------------------------------------
    def _build_where_clause(
        self,
        filters: Optional[Dict[str, Any]] = None,
        table_type: str = "hands",
    ) -> Tuple[str, List[Any]]:
        if not filters:
            return "", []

        clauses = []
        params: List[Any] = []

        # Separación explícita entre datos reales ('live', is_synthetic=0) y sintéticos ('benchmark', is_synthetic=1)
        data_source = str(filters.get("data_source") or "").lower()
        if data_source == "live" or filters.get("is_synthetic") is False or filters.get("is_synthetic") == 0:
            clauses.append("is_synthetic = 0")
        elif data_source == "benchmark" or filters.get("is_synthetic") is True or filters.get("is_synthetic") == 1:
            clauses.append("is_synthetic = 1")

        if filters.get("date_from"):
            clauses.append("date_str >= ?")
            params.append(str(filters["date_from"])[:10])
        if filters.get("date_to"):
            clauses.append("date_str <= ?")
            params.append(str(filters["date_to"])[:10])
        if filters.get("session_id") and filters["session_id"] != "ALL":
            clauses.append("session_id = ?")
            params.append(str(filters["session_id"]))
        if filters.get("version_id") and filters["version_id"] != "ALL":
            clauses.append("version_id = ?")
            params.append(str(filters["version_id"]))
        if filters.get("position") and filters["position"] != "ALL":
            clauses.append("position = ?")
            params.append(str(filters["position"]))
        if filters.get("opponent_profile") and filters["opponent_profile"] != "ALL":
            clauses.append("opponent_profile = ?")
            params.append(str(filters["opponent_profile"]))
        if filters.get("street") and filters["street"] != "ALL":
            if table_type == "decisions":
                clauses.append("street = ?")
                params.append(str(filters["street"]).upper())
            else:
                clauses.append("reached_street = ?")
                params.append(str(filters["street"]).upper())

        where_sql = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        return where_sql, params

    def consultar_manos(self, filters: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
        where_sql, params = self._build_where_clause(filters, table_type="hands")
        limit_n = int(filters.get("last_n_hands") or 0) if filters else 0

        with get_connection(self.db_path) as conn:
            if limit_n > 0:
                query = f"""
                    SELECT * FROM (
                        SELECT * FROM analytics_hands
                        {where_sql}
                        ORDER BY ended_at DESC, hand_number DESC
                        LIMIT ?
                    ) ORDER BY ended_at ASC, hand_number ASC
                """
                rows = conn.execute(query, params + [limit_n]).fetchall()
            else:
                query = f"SELECT * FROM analytics_hands {where_sql} ORDER BY ended_at ASC, hand_number ASC"
                rows = conn.execute(query, params).fetchall()
            return [dict(r) for r in rows]

    def consultar_decisiones(self, filters: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
        where_sql, params = self._build_where_clause(filters, table_type="decisions")
        limit_n = int(filters.get("last_n_hands") or 0) if filters else 0

        with get_connection(self.db_path) as conn:
            if limit_n > 0:
                hands = self.consultar_manos(filters)
                hand_uids = {h["hand_uid"] for h in hands}
                if not hand_uids:
                    return []
                rows = conn.execute(
                    f"SELECT * FROM analytics_decisions {where_sql} ORDER BY timestamp ASC, decision_index ASC",
                    params,
                ).fetchall()
                result = []
                for r in rows:
                    if r["hand_uid"] in hand_uids:
                        result.append(self._hidratar_decision(dict(r)))
                return result
            else:
                rows = conn.execute(
                    f"SELECT * FROM analytics_decisions {where_sql} ORDER BY timestamp ASC, decision_index ASC",
                    params,
                ).fetchall()
                return [self._hidratar_decision(dict(r)) for r in rows]

    def _hidratar_decision(self, d: Dict[str, Any]) -> Dict[str, Any]:
        try:
            d["ev_by_action"] = json.loads(d.get("ev_by_action_json") or "{}")
        except Exception:
            d["ev_by_action"] = {}
        try:
            d["legal_actions_list"] = json.loads(d.get("legal_actions") or "[]")
        except Exception:
            d["legal_actions_list"] = []
        try:
            d["gto_mixed_frequencies"] = json.loads(d.get("gto_mixed_freq_json") or "{}")
        except Exception:
            d["gto_mixed_frequencies"] = {}
        return d

    def consultar_oponentes_db(self) -> List[Dict[str, Any]]:
        with get_connection(self.db_path) as conn:
            rows = conn.execute("SELECT * FROM oponentes ORDER BY manos_jugadas DESC").fetchall()
            return [dict(r) for r in rows]

    def consultar_verificaciones_oponentes(self, filters: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
        with get_connection(self.db_path) as conn:
            clauses = []
            params: List[Any] = []
            if filters:
                if filters.get("version_id") and filters["version_id"] != "ALL":
                    clauses.append("version_id = ?")
                    params.append(str(filters["version_id"]))
                ds = str(filters.get("data_source") or "").lower()
                if ds == "live" or filters.get("is_synthetic") is False or filters.get("is_synthetic") == 0:
                    clauses.append("is_synthetic = 0")
                elif ds == "benchmark" or filters.get("is_synthetic") is True or filters.get("is_synthetic") == 1:
                    clauses.append("is_synthetic = 1")
            where_sql = (" WHERE " + " AND ".join(clauses)) if clauses else ""
            rows = conn.execute(
                f"SELECT * FROM analytics_opponent_verifications {where_sql} ORDER BY timestamp DESC",
                params,
            ).fetchall()
            return [dict(r) for r in rows]

    # ------------------------------------------------------------------
    # EXPORTACIÓN Y TRAZABILIDAD COMPLETA (FASE 7)
    # ------------------------------------------------------------------
    def exportar_csv(
        self,
        output_dir: Path = Path("logs/exports"),
        filters: Optional[Dict[str, Any]] = None,
        extra_summary: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, str]:
        """
        Exporta las 6 entidades requeridas en CSV con trazabilidad completa (`code_hash`, `version_id`, `is_synthetic`):
        1. Manos (`analytics_hands.csv`)
        2. Sesiones (`analytics_sessions.csv`)
        3. Decisiones (`analytics_decisions.csv`)
        4. Métricas calculadas (`analytics_metrics.csv`)
        5. Resultados de benchmark/experimentos (`analytics_benchmarks.csv`)
        6. Comparación de versiones (`analytics_version_comparisons.csv`)
        """
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        hands = self.consultar_manos(filters)
        decisions = self.consultar_decisiones(filters)
        sessions = self.obtener_sesiones()
        versions = self.obtener_versiones()
        experiments = self.obtener_experimentos()

        paths: Dict[str, str] = {}

        # 1. Manos
        hands_csv_path = output_dir / "analytics_hands.csv"
        if hands:
            with open(hands_csv_path, "w", newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(f, fieldnames=list(hands[0].keys()))
                writer.writeheader()
                writer.writerows(hands)
        else:
            hands_csv_path.write_text("hand_uid,session_id,version_id,is_synthetic,code_hash,net_profit_chips,net_profit_bb,ev_net_bb,allin_ev_bb\n", encoding="utf-8")
        paths["hands_csv"] = str(hands_csv_path)

        # 2. Sesiones
        sessions_csv_path = output_dir / "analytics_sessions.csv"
        if sessions:
            with open(sessions_csv_path, "w", newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(f, fieldnames=list(sessions[0].keys()))
                writer.writeheader()
                writer.writerows(sessions)
        else:
            sessions_csv_path.write_text("session_id,version_id,mode,is_synthetic,code_hash,hands_played,net_profit_chips\n", encoding="utf-8")
        paths["sessions_csv"] = str(sessions_csv_path)

        # 3. Decisiones
        decisions_csv_path = output_dir / "analytics_decisions.csv"
        if decisions:
            clean_decisions = [
                {k: v for k, v in d.items() if k not in ("ev_by_action", "legal_actions_list", "gto_mixed_frequencies")}
                for d in decisions
            ]
            with open(decisions_csv_path, "w", newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(f, fieldnames=list(clean_decisions[0].keys()))
                writer.writeheader()
                writer.writerows(clean_decisions)
        else:
            decisions_csv_path.write_text("decision_uid,hand_uid,version_id,is_synthetic,code_hash,action_taken,gto_recommended_action,ev_lost_bb\n", encoding="utf-8")
        paths["decisions_csv"] = str(decisions_csv_path)

        # 4. Métricas (Resumen plano o tabla principal de métricas)
        metrics_csv_path = output_dir / "analytics_metrics.csv"
        metrics_rows: List[Dict[str, Any]] = []
        if extra_summary and "main_metrics_table" in extra_summary:
            metrics_rows = extra_summary["main_metrics_table"]
        elif extra_summary and "general_performance" in extra_summary:
            gp = extra_summary.get("general_performance", {})
            for k, v in gp.items():
                if not isinstance(v, (list, dict)):
                    metrics_rows.append({"category": "general_performance", "metric": k, "value": v, "code_hash": self.default_code_hash})
        if not metrics_rows:
            metrics_rows = [{
                "category": "resumen",
                "metric": "hands_count",
                "value": len(hands),
                "decisions_count": len(decisions),
                "code_hash": self.default_code_hash,
            }]
        with open(metrics_csv_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=list(metrics_rows[0].keys()))
            writer.writeheader()
            writer.writerows(metrics_rows)
        paths["metrics_csv"] = str(metrics_csv_path)

        # 5. Benchmarks / Experimentos
        benchmarks_csv_path = output_dir / "analytics_benchmarks.csv"
        if experiments:
            clean_exps = [
                {
                    "experiment_id": e.get("experiment_id"),
                    "version_id": e.get("version_id"),
                    "parent_version": e.get("parent_version"),
                    "created_at": e.get("created_at"),
                    "code_hash": e.get("code_hash"),
                    "config_hash": e.get("config_hash"),
                    "num_scenarios": e.get("num_scenarios"),
                    "verdict_state": e.get("verdict_state"),
                    "changes_description": e.get("changes_description"),
                    "results_json": e.get("results_json"),
                    "ci95_json": e.get("ci95_json"),
                }
                for e in experiments
            ]
            with open(benchmarks_csv_path, "w", newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(f, fieldnames=list(clean_exps[0].keys()))
                writer.writeheader()
                writer.writerows(clean_exps)
        else:
            benchmarks_csv_path.write_text("experiment_id,version_id,code_hash,config_hash,num_scenarios,verdict_state\n", encoding="utf-8")
        paths["benchmarks_csv"] = str(benchmarks_csv_path)

        # 6. Comparaciones de versiones
        comparisons_csv_path = output_dir / "analytics_version_comparisons.csv"
        comp_rows: List[Dict[str, Any]] = []
        if extra_summary and "version_comparison" in extra_summary:
            for vr in extra_summary["version_comparison"].get("versions", []):
                cvp = vr.get("comparison_vs_prev") or {}
                comp_rows.append({
                    "version_id": vr.get("version_id"),
                    "parent_version": vr.get("parent_version"),
                    "code_hash": vr.get("code_hash") or self.default_code_hash,
                    "config_hash": vr.get("config_hash"),
                    "hands_evaluated": vr.get("hands_evaluated"),
                    "bb_per_100": vr.get("bb_per_100"),
                    "ev_bb_per_100": vr.get("ev_bb_per_100"),
                    "gto_alignment_pct": vr.get("gto_alignment_pct"),
                    "strategic_error_rate_pct": vr.get("strategic_error_rate_pct"),
                    "delta_ev_bb_per_100": cvp.get("delta_ev_bb_per_100", 0.0),
                    "p_value_raw": cvp.get("effective_p_value", 1.0),
                    "p_value_holm": cvp.get("holm_corrected_p_value", 1.0),
                    "cohens_d": cvp.get("cohens_d", 0.0),
                    "verdict_state": cvp.get("verdict_state", "LINEA_BASE"),
                })
        elif versions:
            for v in versions:
                comp_rows.append({
                    "version_id": v.get("version_id"),
                    "parent_version": v.get("parent_version"),
                    "code_hash": v.get("code_hash") or self.default_code_hash,
                    "config_hash": v.get("config_hash"),
                    "description": v.get("description"),
                })
        if comp_rows:
            with open(comparisons_csv_path, "w", newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(f, fieldnames=list(comp_rows[0].keys()))
                writer.writeheader()
                writer.writerows(comp_rows)
        else:
            comparisons_csv_path.write_text("version_id,parent_version,code_hash,verdict_state\n", encoding="utf-8")
        paths["version_comparisons_csv"] = str(comparisons_csv_path)
        paths["versions_csv"] = str(comparisons_csv_path)

        return paths

    def generar_csv_en_memoria(self, tipo: str = "hands", filters: Optional[Dict[str, Any]] = None) -> str:
        """Genera contenido CSV en memoria para descarga directa."""
        buf = io.StringIO()
        if tipo == "decisions":
            rows = self.consultar_decisiones(filters)
            rows = [{k: v for k, v in d.items() if k not in ("ev_by_action", "legal_actions_list", "gto_mixed_frequencies")} for d in rows]
        elif tipo == "sessions":
            rows = self.obtener_sesiones()
        elif tipo == "experiments":
            rows = self.obtener_experimentos()
        else:
            rows = self.consultar_manos(filters)

        if not rows:
            return "sin_datos\n"
        writer = csv.DictWriter(buf, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)
        return buf.getvalue()

    def exportar_json(
        self,
        output_path: Path = Path("logs/exports/analytics_report.json"),
        filters: Optional[Dict[str, Any]] = None,
        extra_summary: Optional[Dict[str, Any]] = None,
    ) -> str:
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        hands = self.consultar_manos(filters)
        decisions = self.consultar_decisiones(filters)
        payload = {
            "exported_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "traceability": {
                "code_hash": self.default_code_hash,
                "db_path": str(self.db_path),
                "filters_applied": filters or {},
            },
            "filters_applied": filters or {},
            "versions": self.obtener_versiones(),
            "experiments": self.obtener_experimentos(),
            "sessions": self.obtener_sesiones(),
            "summary": extra_summary or {},
            "hands_count": len(hands),
            "decisions_count": len(decisions),
            "hands": hands,
            "decisions": decisions,
        }
        output_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
        return str(output_path)
