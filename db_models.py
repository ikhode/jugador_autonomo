"""
Modelos ORM de SQLAlchemy para el Bot de Póker Autónomo.
Cubre memoria de experiencia, perfilado de oponentes y telemetría/analíticas.
"""
from datetime import datetime
from typing import Optional
from sqlalchemy import (
    Column, Integer, String, Float, DateTime, Text, Boolean, BigInteger
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

class Base(DeclarativeBase):
    pass

# ==============================================================================
# 1. MEMORIA VECTORIAL / EXPERIENCIA DE ERRORES PASADOS
# ==============================================================================
class ErrorExperiencia(Base):
    __tablename__ = "errores_experiencia"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    timestamp: Mapped[Optional[str]] = mapped_column(String, default=lambda: datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S"))
    hand_tier: Mapped[Optional[str]] = mapped_column(String)
    board_texture: Mapped[Optional[str]] = mapped_column(String)
    opp_archetype: Mapped[Optional[str]] = mapped_column(String)
    action_taken: Mapped[Optional[str]] = mapped_column(String)
    loss_amount: Mapped[Optional[int]] = mapped_column(Integer)
    lesson_learned: Mapped[Optional[str]] = mapped_column(Text)
    similarity_key: Mapped[Optional[str]] = mapped_column(String)

# ==============================================================================
# 2. PERFILADOR DE OPONENTES (HUD)
# ==============================================================================
class Oponente(Base):
    __tablename__ = "oponentes"

    player_id: Mapped[str] = mapped_column(String, primary_key=True)
    nickname: Mapped[Optional[str]] = mapped_column(String)
    manos_jugadas: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    preflop_calls: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    preflop_raises: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    folds_ante_raise: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    folds_preflop: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    folds_postflop: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    postflop_bets: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    postflop_calls: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    postflop_checks: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    cbet_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    cbet_opportunities: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    fold_to_cbet: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    fold_to_cbet_opportunities: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    three_bets: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    three_bet_opportunities: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    fold_to_3bet: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    fold_to_3bet_opportunities: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    wtsd_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    saw_flop_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    wwsf_wins: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    last_seat: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    last_status: Mapped[Optional[str]] = mapped_column(String, default="ACTIVE")
    known_aliases: Mapped[Optional[str]] = mapped_column(Text, default="[]")
    ultimas_cartas_mostradas: Mapped[Optional[str]] = mapped_column(Text)
    ultima_actualizacion: Mapped[Optional[str]] = mapped_column(String, default=lambda: datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S"))

# ==============================================================================
# 3. VERSIÓN DEL BOT (EXPERIMENTACIÓN Y TRAZABILIDAD)
# ==============================================================================
class BotVersion(Base):
    __tablename__ = "bot_versions"

    version_id: Mapped[str] = mapped_column(String, primary_key=True)
    created_at: Mapped[str] = mapped_column(String)
    description: Mapped[str] = mapped_column(Text)
    modified_params: Mapped[Optional[str]] = mapped_column(Text, default="{}")
    parent_version: Mapped[Optional[str]] = mapped_column(String, default="")
    config_hash: Mapped[Optional[str]] = mapped_column(String, default="")
    code_hash: Mapped[Optional[str]] = mapped_column(String, default="")
    random_seed: Mapped[int] = mapped_column(Integer, default=20261114)
    experiment_conditions: Mapped[Optional[str]] = mapped_column(Text, default="{}")
    notes: Mapped[Optional[str]] = mapped_column(Text, default="")

# ==============================================================================
# 4. SESIONES DE ANALÍTICA / JUEGO
# ==============================================================================
class AnalyticsSession(Base):
    __tablename__ = "analytics_sessions"

    session_id: Mapped[str] = mapped_column(String, primary_key=True)
    version_id: Mapped[str] = mapped_column(String)
    started_at: Mapped[str] = mapped_column(String)
    ended_at: Mapped[Optional[str]] = mapped_column(String)
    mode: Mapped[Optional[str]] = mapped_column(String, default="live")
    is_synthetic: Mapped[int] = mapped_column(Integer, default=0)
    code_hash: Mapped[Optional[str]] = mapped_column(String, default="")
    model_name: Mapped[Optional[str]] = mapped_column(String, default="")
    initial_bankroll: Mapped[float] = mapped_column(Float, default=1500.0)
    final_bankroll: Mapped[float] = mapped_column(Float, default=1500.0)
    buy_in_total: Mapped[float] = mapped_column(Float, default=500.0)
    big_blind: Mapped[float] = mapped_column(Float, default=20.0)
    hands_played: Mapped[int] = mapped_column(Integer, default=0)
    net_profit_chips: Mapped[float] = mapped_column(Float, default=0.0)
    status: Mapped[Optional[str]] = mapped_column(String, default="ACTIVE")

# ==============================================================================
# 5. MANOS JUGADAS (TELEMETRÍA DE MANO)
# ==============================================================================
class AnalyticsHand(Base):
    __tablename__ = "analytics_hands"

    hand_uid: Mapped[str] = mapped_column(String, primary_key=True)
    session_id: Mapped[str] = mapped_column(String, index=True)
    version_id: Mapped[str] = mapped_column(String, index=True)
    hand_number: Mapped[int] = mapped_column(Integer)
    scenario_id: Mapped[Optional[str]] = mapped_column(String, default="")
    is_synthetic: Mapped[int] = mapped_column(Integer, default=0, index=True)
    code_hash: Mapped[Optional[str]] = mapped_column(String, default="")
    started_at: Mapped[str] = mapped_column(String)
    ended_at: Mapped[str] = mapped_column(String)
    date_str: Mapped[str] = mapped_column(String, index=True)
    position: Mapped[Optional[str]] = mapped_column(String, default="DESCONOCIDA", index=True)
    hole_cards: Mapped[Optional[str]] = mapped_column(String, default="")
    preflop_tier: Mapped[Optional[str]] = mapped_column(String, default="DESCONOCIDO")
    preflop_score: Mapped[Optional[int]] = mapped_column(Integer, default=0)
    board_flop: Mapped[Optional[str]] = mapped_column(String, default="")
    board_turn: Mapped[Optional[str]] = mapped_column(String, default="")
    board_river: Mapped[Optional[str]] = mapped_column(String, default="")
    final_board: Mapped[Optional[str]] = mapped_column(String, default="")
    reached_street: Mapped[Optional[str]] = mapped_column(String, default="PREFLOP")
    saw_flop: Mapped[Optional[int]] = mapped_column(Integer, default=0)
    went_to_showdown: Mapped[Optional[int]] = mapped_column(Integer, default=0)
    won_at_showdown: Mapped[Optional[int]] = mapped_column(Integer, default=0)
    won_hand: Mapped[Optional[int]] = mapped_column(Integer, default=0)
    vpip_flag: Mapped[Optional[int]] = mapped_column(Integer, default=0)
    pfr_flag: Mapped[Optional[int]] = mapped_column(Integer, default=0)
    three_bet_opp: Mapped[Optional[int]] = mapped_column(Integer, default=0)
    three_bet_flag: Mapped[Optional[int]] = mapped_column(Integer, default=0)
    faced_three_bet: Mapped[Optional[int]] = mapped_column(Integer, default=0)
    fold_to_three_bet: Mapped[Optional[int]] = mapped_column(Integer, default=0)
    cbet_flop_opp: Mapped[Optional[int]] = mapped_column(Integer, default=0)
    cbet_flop_flag: Mapped[Optional[int]] = mapped_column(Integer, default=0)
    faced_cbet_flop: Mapped[Optional[int]] = mapped_column(Integer, default=0)
    fold_to_cbet_flop: Mapped[Optional[int]] = mapped_column(Integer, default=0)
    hero_stack_start: Mapped[Optional[float]] = mapped_column(Float, default=500.0)
    hero_stack_end: Mapped[Optional[float]] = mapped_column(Float, default=500.0)
    effective_stack: Mapped[Optional[float]] = mapped_column(Float, default=500.0)
    big_blind: Mapped[Optional[float]] = mapped_column(Float, default=20.0)
    pot_final: Mapped[Optional[float]] = mapped_column(Float, default=0.0)
    invested_chips: Mapped[Optional[float]] = mapped_column(Float, default=0.0)
    won_chips: Mapped[Optional[float]] = mapped_column(Float, default=0.0)
    net_profit_chips: Mapped[Optional[float]] = mapped_column(Float, default=0.0)
    net_profit_bb: Mapped[Optional[float]] = mapped_column(Float, default=0.0)
    ev_net_chips: Mapped[Optional[float]] = mapped_column(Float, default=0.0)
    ev_net_bb: Mapped[Optional[float]] = mapped_column(Float, default=0.0)
    allin_ev_chips: Mapped[Optional[float]] = mapped_column(Float, default=0.0)
    allin_ev_bb: Mapped[Optional[float]] = mapped_column(Float, default=0.0)
    is_allin: Mapped[Optional[int]] = mapped_column(Integer, default=0)
    bankroll_after: Mapped[Optional[float]] = mapped_column(Float, default=1500.0)
    ev_bankroll_after: Mapped[Optional[float]] = mapped_column(Float, default=1500.0)
    opponent_id: Mapped[Optional[str]] = mapped_column(String, default="")
    opponent_nick: Mapped[Optional[str]] = mapped_column(String, default="")
    opponent_profile: Mapped[Optional[str]] = mapped_column(String, default="DESCONOCIDO", index=True)
    result_reason: Mapped[Optional[str]] = mapped_column(Text, default="")

# ==============================================================================
# 6. DECISIONES TÁCTICAS
# ==============================================================================
class AnalyticsDecision(Base):
    __tablename__ = "analytics_decisions"

    decision_uid: Mapped[str] = mapped_column(String, primary_key=True)
    hand_uid: Mapped[str] = mapped_column(String, index=True)
    session_id: Mapped[str] = mapped_column(String)
    version_id: Mapped[str] = mapped_column(String, index=True)
    scenario_id: Mapped[Optional[str]] = mapped_column(String, default="")
    is_synthetic: Mapped[int] = mapped_column(Integer, default=0, index=True)
    code_hash: Mapped[Optional[str]] = mapped_column(String, default="")
    timestamp: Mapped[str] = mapped_column(String)
    date_str: Mapped[str] = mapped_column(String)
    decision_index: Mapped[int] = mapped_column(Integer, default=1)
    position: Mapped[Optional[str]] = mapped_column(String, default="DESCONOCIDA", index=True)
    street: Mapped[Optional[str]] = mapped_column(String, default="PREFLOP", index=True)
    hole_cards: Mapped[Optional[str]] = mapped_column(String, default="")
    board: Mapped[Optional[str]] = mapped_column(String, default="")
    pot_size: Mapped[Optional[float]] = mapped_column(Float, default=0.0)
    to_call: Mapped[Optional[float]] = mapped_column(Float, default=0.0)
    effective_stack: Mapped[Optional[float]] = mapped_column(Float, default=500.0)
    big_blind: Mapped[Optional[float]] = mapped_column(Float, default=20.0)
    pot_size_bucket: Mapped[Optional[str]] = mapped_column(String, default="MEDIUM")
    stack_depth_bucket: Mapped[Optional[str]] = mapped_column(String, default="DEEP")
    intended_action: Mapped[Optional[str]] = mapped_column(String, default="")
    action_taken: Mapped[str] = mapped_column(String)
    bet_amount: Mapped[Optional[float]] = mapped_column(Float, default=0.0)
    legal_actions: Mapped[Optional[str]] = mapped_column(Text, default="[]")
    evaluation_available: Mapped[Optional[int]] = mapped_column(Integer, default=1)
    gto_recommended_action: Mapped[str] = mapped_column(String)
    gto_recommended_sizing: Mapped[Optional[float]] = mapped_column(Float, default=0.0)
    gto_mixed_freq_json: Mapped[Optional[str]] = mapped_column(Text, default="{}")
    is_gto_aligned: Mapped[Optional[int]] = mapped_column(Integer, default=1)
    action_deviation: Mapped[Optional[str]] = mapped_column(String, default="EXACT_MATCH")
    ev_by_action_json: Mapped[Optional[str]] = mapped_column(Text, default="{}")
    ev_taken_chips: Mapped[Optional[float]] = mapped_column(Float, default=0.0)
    ev_best_chips: Mapped[Optional[float]] = mapped_column(Float, default=0.0)
    ev_lost_chips: Mapped[Optional[float]] = mapped_column(Float, default=0.0)
    ev_lost_bb: Mapped[Optional[float]] = mapped_column(Float, default=0.0)
    estimated_equity: Mapped[Optional[float]] = mapped_column(Float, default=0.5)
    pot_odds_pct: Mapped[Optional[float]] = mapped_column(Float, default=0.0)
    decision_quality: Mapped[Optional[str]] = mapped_column(String, default="OPTIMA")
    is_strategic_error: Mapped[Optional[int]] = mapped_column(Integer, default=0)
    is_missed_opportunity: Mapped[Optional[int]] = mapped_column(Integer, default=0)
    error_description: Mapped[Optional[str]] = mapped_column(Text, default="")
    opponent_id: Mapped[Optional[str]] = mapped_column(String, default="")
    opponent_nick: Mapped[Optional[str]] = mapped_column(String, default="")
    opponent_profile: Mapped[Optional[str]] = mapped_column(String, default="DESCONOCIDO", index=True)
    opponent_adaptation_aligned: Mapped[Optional[int]] = mapped_column(Integer, default=1)
    decision_source: Mapped[Optional[str]] = mapped_column(String, default="gemini")
    confidence: Mapped[Optional[float]] = mapped_column(Float, default=0.8)
    response_time_ms: Mapped[Optional[float]] = mapped_column(Float, default=0.0)
    enqueue_latency_ms: Mapped[Optional[float]] = mapped_column(Float, default=0.0)
    sqlite_write_ms: Mapped[Optional[float]] = mapped_column(Float, default=0.0)
    hand_final_result_chips: Mapped[Optional[float]] = mapped_column(Float, default=0.0)
    hand_final_result_bb: Mapped[Optional[float]] = mapped_column(Float, default=0.0)

# ==============================================================================
# 7. VERIFICACIÓN DE OPONENTES
# ==============================================================================
class AnalyticsOpponentVerification(Base):
    __tablename__ = "analytics_opponent_verifications"

    verification_uid: Mapped[str] = mapped_column(String, primary_key=True)
    hand_uid: Mapped[str] = mapped_column(String)
    session_id: Mapped[str] = mapped_column(String)
    version_id: Mapped[str] = mapped_column(String)
    is_synthetic: Mapped[int] = mapped_column(Integer, default=0)
    timestamp: Mapped[str] = mapped_column(String)
    opponent_id: Mapped[str] = mapped_column(String)
    opponent_nick: Mapped[Optional[str]] = mapped_column(String, default="")
    predicted_profile: Mapped[str] = mapped_column(String)
    predicted_vpip: Mapped[Optional[float]] = mapped_column(Float, default=0.0)
    predicted_pfr: Mapped[Optional[float]] = mapped_column(Float, default=0.0)
    predicted_af: Mapped[Optional[float]] = mapped_column(Float, default=1.0)
    observed_cards: Mapped[Optional[str]] = mapped_column(String, default="")
    observed_tier: Mapped[Optional[str]] = mapped_column(String, default="")
    observed_line: Mapped[Optional[str]] = mapped_column(String, default="")
    is_pattern_verified: Mapped[Optional[int]] = mapped_column(Integer, default=1)
    verification_note: Mapped[Optional[str]] = mapped_column(Text, default="")

# ==============================================================================
# 8. EXPERIMENTOS DE LABORATORIO
# ==============================================================================
class AnalyticsExperiment(Base):
    __tablename__ = "analytics_experiments"

    experiment_id: Mapped[str] = mapped_column(String, primary_key=True)
    version_id: Mapped[str] = mapped_column(String)
    parent_version: Mapped[Optional[str]] = mapped_column(String, default="")
    created_at: Mapped[str] = mapped_column(String)
    changes_description: Mapped[str] = mapped_column(Text)
    modified_params_json: Mapped[Optional[str]] = mapped_column(Text, default="{}")
    code_hash: Mapped[str] = mapped_column(String)
    config_hash: Mapped[str] = mapped_column(String)
    random_seeds_json: Mapped[Optional[str]] = mapped_column(Text, default="[]")
    conditions_json: Mapped[Optional[str]] = mapped_column(Text, default="{}")
    num_scenarios: Mapped[Optional[int]] = mapped_column(Integer, default=0)
    results_json: Mapped[Optional[str]] = mapped_column(Text, default="{}")
    ci95_json: Mapped[Optional[str]] = mapped_column(Text, default="{}")
    stat_diff_json: Mapped[Optional[str]] = mapped_column(Text, default="{}")
    verdict_state: Mapped[Optional[str]] = mapped_column(String, default="INCONCLUSO")

# ==============================================================================
# 9. TELEMETRÍA DEL SISTEMA
# ==============================================================================
class SystemTelemetry(Base):
    __tablename__ = "system_telemetry"

    telemetry_id: Mapped[str] = mapped_column(String, primary_key=True)
    session_id: Mapped[Optional[str]] = mapped_column(String, default="")
    version_id: Mapped[Optional[str]] = mapped_column(String, default="")
    timestamp: Mapped[str] = mapped_column(String, index=True)
    event_type: Mapped[Optional[str]] = mapped_column(String, default="DECISION_WRITE")
    decision_latency_ms: Mapped[Optional[float]] = mapped_column(Float, default=0.0)
    enqueue_latency_ms: Mapped[Optional[float]] = mapped_column(Float, default=0.0)
    sqlite_write_ms: Mapped[Optional[float]] = mapped_column(Float, default=0.0)
    stat_processing_ms: Mapped[Optional[float]] = mapped_column(Float, default=0.0)
    memory_rss_mb: Mapped[Optional[float]] = mapped_column(Float, default=0.0)
    pending_tasks: Mapped[Optional[int]] = mapped_column(Integer, default=0)
    dropped_records: Mapped[Optional[int]] = mapped_column(Integer, default=0)
    storage_errors: Mapped[Optional[int]] = mapped_column(Integer, default=0)
