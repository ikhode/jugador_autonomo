"""Capa aislada de IA (Genkit / Google GenAI) con trazabilidad, circuit breaker y fallback determinista."""
from __future__ import annotations

import asyncio
import concurrent.futures
import json
import logging
import random
import re
import time
import uuid
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Literal, Optional, Tuple

logger = logging.getLogger(__name__)

from google import genai
from google.genai import types
from pydantic import BaseModel, Field

import config
from prompts import load_prompt_template

try:
    from genkit import Genkit as _GenkitSDK  # type: ignore
    _GENKIT_AVAILABLE = True
except Exception:
    _GenkitSDK = None
    _GENKIT_AVAILABLE = False


LEGAL_PLAY_ACTIONS = ("FOLD", "CHECK", "CALL", "RAISE", "ALL_IN")
ALL_ACTIONS = ("FOLD", "CHECK", "CALL", "RAISE", "BET", "APOSTAR", "ALL_IN", "ALLIN", "REPLAY", "START_GAME", "WAIT")


class PokerDecision(BaseModel):
    """Esquema estructurado de decisión de póker."""

    evaluacion: str = Field(description="Una frase concisa con la evaluación táctica de la mesa")
    estrategia_competitiva: str = Field(default="Estrategia defensiva de respaldo", description="Breve plan estratégico")
    estado_pantalla: Literal["EN_MESA_JUEGO", "LOBBY_SELECCION", "FIN_DE_PARTIDA", "PANTALLA_DESCONOCIDA"] = Field(
        description="Estado detectado"
    )
    es_mi_turno: bool = Field(description="True si los botones de acción están activos")
    accion: Literal["FOLD", "CHECK", "CALL", "RAISE", "ALL_IN", "ALLIN", "REPLAY", "START_GAME", "WAIT"] = Field(
        description="Acción recomendada"
    )
    monto_apuesta: int = Field(default=0, ge=0, description="Monto sugerido para apostar si la acción es RAISE")
    confianza: float = Field(default=0.5, ge=0.0, le=1.0, description="Nivel de confianza entre 0.0 y 1.0")


class PokerTableInput(BaseModel):
    """Entrada tipada del flujo de decisión en unidades de BB."""

    decision_uid: str = Field(default_factory=lambda: uuid.uuid4().hex[:24])
    hand_uid: str = ""
    es_mi_turno: bool = True
    mis_cartas: str = ""
    cartas_comunitarias: str = ""
    posicion: str = "BTN"
    bote_bb: float = 0.0
    a_igualar_bb: float = 0.0
    stack_efectivo_bb: float = 0.0
    pot_odds_pct: float = 0.0
    opciones_legales: List[str] = Field(default_factory=list)
    perfiles_rivales: List[Dict[str, Any]] = Field(default_factory=list)
    textura_mesa: str = ""
    experiencia_memoria: List[Dict[str, Any]] = Field(default_factory=list)


class StepTrace(BaseModel):
    """Traza de un paso individual dentro del flow."""

    step: str
    duration_ms: float
    status: Literal["OK", "RETRY", "FALLBACK", "ERROR"] = "OK"
    detail: str = ""


class DecisionTrace(BaseModel):
    """Traza completa de observabilidad para una decisión de IA."""

    decision_uid: str
    hand_uid: str = ""
    prompt_version: str = "v1"
    prompt_hash: str = ""
    model_used: str = ""
    retry_count: int = 0
    total_latency_ms: float = 0.0
    tokens_in: Optional[int] = None
    tokens_out: Optional[int] = None
    raw_response: str = ""
    schema_valid: bool = False
    legality_valid: bool = False
    fallback_reason: str = ""
    backend: str = "google_genai"
    steps: List[StepTrace] = Field(default_factory=list)


@dataclass
class AIDecisionResult:
    """Resultado completo devuelto por la capa de IA."""

    decision: PokerDecision
    decision_source: str
    trace: DecisionTrace


@dataclass
class _CircuitState:
    consecutive_failures: int = 0
    open_until: float = 0.0
    last_error_kind: str = ""


def classify_error(err: Exception) -> str:
    """Clasifica errores del proveedor LLM en categorías accionables."""
    msg = str(err or "")
    low = msg.lower()
    if "deadline" in low and "too short" in low:
        return "DEADLINE_TOO_SHORT"
    if "403" in msg or "permission_denied" in low or "does not have permission" in low:
        return "PERMISSION_DENIED"
    if "504" in msg or "deadline_exceeded" in low:
        return "TIMEOUT"
    if "404" in msg or "not_found" in low or "not found" in low:
        return "NOT_FOUND_404"
    if "perday" in low or "per day" in low or "daily" in low:
        return "DAILY_QUOTA"
    if "429" in msg or "503" in msg or "resource_exhausted" in low or "unavailable" in low:
        return "RATE_LIMIT_429_503"
    if isinstance(err, TimeoutError) or "timeout" in low or "timed out" in low:
        return "TIMEOUT"
    return "OTHER"


class ModelPolicy:
    """Política explícita de selección, circuit breaker y reintentos con backoff + jitter."""

    def __init__(
        self,
        models: Optional[List[str]] = None,
        failure_threshold: Optional[int] = None,
        open_sec: Optional[float] = None,
        cooldown_404_sec: Optional[float] = None,
        cooldown_daily_sec: Optional[float] = None,
        cooldown_rate_sec: Optional[float] = None,
        rng: Optional[random.Random] = None,
    ):
        base = models if models is not None else list(getattr(config, "GEMINI_MODELS_PRIORITY", [config.GEMINI_MODEL]))
        self.models: List[str] = list(dict.fromkeys(m for m in base if m))
        self.failure_threshold = int(failure_threshold or getattr(config, "AI_CIRCUIT_FAILURE_THRESHOLD", 3))
        self.open_sec = float(open_sec or getattr(config, "AI_CIRCUIT_OPEN_SEC", 60.0))
        self.cooldown_404_sec = float(cooldown_404_sec or getattr(config, "AI_COOLDOWN_404_SEC", 86400.0))
        self.cooldown_daily_sec = float(cooldown_daily_sec or getattr(config, "AI_COOLDOWN_DAILY_SEC", 3600.0))
        self.cooldown_rate_sec = float(cooldown_rate_sec or getattr(config, "AI_COOLDOWN_RATE_SEC", 45.0))
        self._rng = rng or random.Random()
        self._circuits: Dict[str, _CircuitState] = {m: _CircuitState() for m in self.models}

    def available_models(self, now: Optional[float] = None) -> List[str]:
        t = now if now is not None else time.time()
        return [m for m in self.models if t >= self._circuits.setdefault(m, _CircuitState()).open_until]

    def record_success(self, model: str) -> None:
        st = self._circuits.setdefault(model, _CircuitState())
        st.consecutive_failures = 0
        st.open_until = 0.0
        st.last_error_kind = ""

    def record_failure(self, model: str, err_kind: str, now: Optional[float] = None) -> None:
        t = now if now is not None else time.time()
        st = self._circuits.setdefault(model, _CircuitState())
        st.consecutive_failures += 1
        st.last_error_kind = err_kind
        if err_kind in ("NOT_FOUND_404", "PERMISSION_DENIED", "DEADLINE_TOO_SHORT"):
            st.open_until = t + self.cooldown_404_sec
        elif err_kind == "DAILY_QUOTA":
            st.open_until = t + self.cooldown_daily_sec
        elif err_kind == "RATE_LIMIT_429_503":
            st.open_until = t + self.cooldown_rate_sec
        elif st.consecutive_failures >= self.failure_threshold:
            st.open_until = t + self.open_sec

    def compute_backoff_sec(self, attempt: int, base_ms: int = 150) -> float:
        exp_ms = base_ms * (2 ** max(0, attempt))
        jitter_ms = self._rng.uniform(0.0, float(base_ms))
        return (exp_ms + jitter_ms) / 1000.0

    def circuit_snapshot(self) -> Dict[str, Dict[str, Any]]:
        now = time.time()
        return {
            m: {
                "state": "OPEN" if now < st.open_until else ("HALF_OPEN" if st.consecutive_failures > 0 else "CLOSED"),
                "consecutive_failures": st.consecutive_failures,
                "last_error_kind": st.last_error_kind,
            }
            for m, st in self._circuits.items()
        }


def build_table_input(dom_data: Optional[dict], decision_uid: str = "", hand_uid: str = "") -> PokerTableInput:
    """Construye PokerTableInput tipado a partir de dom_data."""
    dom = dom_data or {}
    gto_math = dom.get("gto_math") or {}
    gto_pre = dom.get("gto_preflop") or {}
    gto_tex = dom.get("gto_textura") or {}
    raw_opts = str(dom.get("opciones_disponibles") or dom.get("botones_visibles") or "").upper()
    opts = [tok.strip() for tok in re.split(r"[|,\s]+", raw_opts) if tok.strip() in ALL_ACTIONS]
    mis_c = dom.get("mis_cartas", [])
    com_c = dom.get("cartas_comunitarias", [])
    return PokerTableInput(
        decision_uid=decision_uid or uuid.uuid4().hex[:24],
        hand_uid=hand_uid,
        es_mi_turno=bool(dom.get("es_mi_turno", True)),
        mis_cartas=" ".join(mis_c) if isinstance(mis_c, list) else str(mis_c or ""),
        cartas_comunitarias=" ".join(com_c) if isinstance(com_c, list) else str(com_c or ""),
        posicion=str(gto_pre.get("posicion") or dom.get("posicion") or "BTN"),
        bote_bb=float(gto_math.get("bote_bb") or 0.0),
        a_igualar_bb=float(gto_math.get("a_igualar_bb") or 0.0),
        stack_efectivo_bb=float(gto_math.get("stack_efectivo_bb") or 0.0),
        pot_odds_pct=float(gto_math.get("pot_odds_pct") or 0.0),
        opciones_legales=opts,
        perfiles_rivales=list(dom.get("perfiles_oponentes") or []),
        textura_mesa=str(gto_tex.get("textura_mesa") or ""),
        experiencia_memoria=list(dom.get("memoria_errores") or []),
    )


def render_prompt(dom_data: Optional[dict], prompt_version: str, perfil_rival_str: str) -> Tuple[str, str, str]:
    """Renderiza el prompt versionado y devuelve (version, hash, prompt_text)."""
    ver, digest, template = load_prompt_template(prompt_version)
    dom = dom_data or {}

    mis_cartas = dom.get("mis_cartas", [])
    comunitarias = dom.get("cartas_comunitarias", [])
    mis_cartas_str = " ".join(mis_cartas) if isinstance(mis_cartas, list) else str(mis_cartas or "")
    comunitarias_str = " ".join(comunitarias) if isinstance(comunitarias, list) else str(comunitarias or "")

    dom_info_str = f"""
        ESTADO MESA EN TIEMPO REAL:
        - ES_MI_TURNO: {"SI" if dom.get("es_mi_turno", False) else "NO"} (tiempo: {dom.get("tiempo_restante", "")}s)
        - Cartas mano: {mis_cartas_str or "N/A"}
        - Comunitarias: {comunitarias_str or "Ninguna"}
        - Bote: {dom.get("bote", 0)} | A igualar: {dom.get("a_igualar", 0)}
        - Mi stack: {dom.get("mi_stack", 0)} | Stack rivales: {dom.get("stack_rival", 0)}
        - Fuerza mano: {dom.get("fuerza_mano", "N/A")}
        - OPCIONES DISPONIBLES EN PANTALLA: {dom.get("opciones_disponibles", dom.get("botones_visibles", ""))}
    """ if dom else ""

    gto_pre = dom.get("gto_preflop") or {}
    gto_math = dom.get("gto_math") or {}
    gto_section_str = ""
    if gto_pre or gto_math:
        gto_section_str = f"""
        📐 ANÁLISIS MATEMÁTICO Y MATRIZ DE RANGOS GTO:
        - Rango Preflop ({gto_pre.get("posicion", "BTN")} | {gto_pre.get("situacion", "open")}): Tier [{gto_pre.get("tier", "N/A")}] (Score: {gto_pre.get("score", 0)}/100) - Mano: {gto_pre.get("mano_code", "")}
        - Stack Efectivo: {gto_math.get("stack_efectivo_bb", 0)} BB | Bote: {gto_math.get("bote_bb", 0)} BB | A igualar: {gto_math.get("a_igualar_bb", 0)} BB
        - Pot Odds: {gto_math.get("pot_odds_pct", 0)}%
        - Sizings Sugeridos: Pequeño 33%: {gto_math.get("sizing_cbet_pequeno", 0)} | Medio 66%: {gto_math.get("sizing_cbet_estandar", 0)} | Pot: {gto_math.get("sizing_cbet_fuerte", 0)}
        """

    gto_comb = dom.get("gto_combinacion") or {}
    comb_str = ""
    if gto_comb:
        proyectos_txt = ", ".join(gto_comb.get("proyectos") or []) or "Ninguno"
        comb_str = f"""
        🎴 DISTINCIÓN DE COMBINACIÓN (MANO PRIVADA VS MESA):
        - Clasificación Táctica: {gto_comb.get("categoria")}
        - Detalle: {gto_comb.get("descripcion")}
        - ¿Juega solo la mesa (Board Plays)?: {"SI ⚠️" if gto_comb.get("mano_en_mesa") else "NO"}
        - ¿Es Pareja en Mano?: {"SI ⚡" if gto_comb.get("es_pareja_en_mano") else "NO"}
        - ¿Hay Pareja Compartida en Mesa?: {"SI" if gto_comb.get("pareja_en_mesa") else "NO"}
        - Proyectos y Outs: {proyectos_txt} (Outs totales: {gto_comb.get("outs_totales", 0)})
        """

    gto_tex = dom.get("gto_textura") or {}
    textura_str = ""
    if gto_tex:
        textura_str = f"""
        🛡️ BLOCKERS Y TEXTURA DE MESA:
        - Textura Tablero: {gto_tex.get("textura_mesa")}
        - Blockers: Ases muertos = {gto_tex.get("blockers_ases")} | Reyes muertos = {gto_tex.get("blockers_reyes")}
        - Estimación Rango: {gto_tex.get("estimacion_rango", "")}
        """

    gto_frec = dom.get("gto_frecuencia") or {}
    frecuencia_str = (
        f"\n🎲 FRECUENCIA GTO: d100={gto_frec.get('d100')} -> {gto_frec.get('linea_sugerida')}\n"
        if gto_frec else ""
    )

    memoria_lista = dom.get("memoria_errores") or []
    memoria_str = ""
    if memoria_lista:
        lines = ["🧠 RECUERDOS DE ERRORES PASADOS:"]
        for m in memoria_lista:
            lines.append(
                f"- [{m.get('hand_tier')}] {m.get('board_texture')} vs {m.get('opp_archetype')}: "
                f"'{m.get('action_taken')}' perdió {m.get('loss_amount')} fichas. {m.get('lesson_learned')}"
            )
        memoria_str = "\n".join(lines)

    patrones_live = dom.get("patrones_decisiones_live") or {}
    patrones_db_str = ""
    if isinstance(patrones_live, dict) and patrones_live.get("sample_size", 0) > 0:
        sz = patrones_live["sample_size"]
        counts = patrones_live.get("action_counts", {})
        counts_str = ", ".join(f"{k}: {v}" for k, v in counts.items())
        avg_reg = patrones_live.get("avg_ev_lost_bb", 0.0)
        recents = patrones_live.get("recent_decisions", [])
        lines_pat = ["📊 RETRIEVAL EN RUNTIME (HISTORIAL DE DECISIONES EN BD EN VIVO):"]
        lines_pat.append(f"- Muestra previa en spots similares: {sz} decisiones | Frecuencias: {counts_str} | Regret medio: {avg_reg:+.2f} BB")
        if recents:
            lines_pat.append("- Decisiones recientes en este spot:")
            for r in recents:
                lines_pat.append(
                    f"  * [{r.get('cards', '')} | {r.get('board', '')}]: Tomó '{r.get('action_taken')}' "
                    f"(GTO recomendó '{r.get('gto_action')}', ΔEV: {r.get('ev_lost_bb'):+.2f} BB)"
                )
        patrones_db_str = "\n".join(lines_pat)

    memoria_completa_str = "\n".join(filter(None, [memoria_str, patrones_db_str]))

    hist = dom.get("historial_mano", "")
    historial_section = f"\nHISTORIAL DE LA MANO:\n{hist}\n" if hist else ""

    rendered = template.format(
        dom_info_str=dom_info_str,
        gto_section_str=gto_section_str,
        comb_str=comb_str,
        textura_str=textura_str,
        frecuencia_str=frecuencia_str,
        perfil_rival_str=perfil_rival_str,
        memoria_str=memoria_completa_str,
        historial_section=historial_section,
    )
    return ver, digest, rendered


def validate_legality(decision: PokerDecision, table_input: PokerTableInput) -> Tuple[bool, PokerDecision, str]:
    """Valida legalidad básica de la acción devuelta por el LLM."""
    acc = "ALL_IN" if decision.accion == "ALLIN" else decision.accion
    decision.accion = acc  # type: ignore[assignment]
    opts = set(table_input.opciones_legales)

    if acc == "FOLD" and "CHECK" in opts and table_input.a_igualar_bb <= 0:
        decision.accion = "CHECK"
        decision.evaluacion = f"[Auto-Check Gratis] {decision.evaluacion}"
        return True, decision, "auto_check_gratis"

    if table_input.es_mi_turno and acc in ("WAIT", "START_GAME", "REPLAY"):
        return False, decision, f"illegal_passive_action:{acc}"

    if opts and acc not in opts:
        if acc in ("RAISE", "BET") and any(b in opts for b in ("BET", "RAISE", "APOSTAR")):
            return True, decision, "raise_as_bet"
        if acc == "CALL" and "CHECK" in opts and table_input.a_igualar_bb <= 0:
            decision.accion = "CHECK"
            return True, decision, "call_converted_to_check"
        return False, decision, f"action_not_in_legal_options:{acc}"

    return True, decision, ""


def _sanitizar_json_decision(raw_text: str) -> str:
    """Limpia fences markdown, mapea acciones traducidas y sanitiza tipos para PokerDecision."""
    clean = re.sub(r"```(?:json)?", "", raw_text, flags=re.I).replace("```", "").strip()
    if "<think>" in clean and "</think>" in clean:
        clean = clean.split("</think>")[-1].strip()
    if not clean.startswith("{"):
        m = re.search(r"\{.*\}", clean, flags=re.S)
        if m:
            clean = m.group(0).strip()
    try:
        data = json.loads(clean)
        if isinstance(data, dict):
            acc = str(data.get("accion") or "WAIT").upper().strip()
            mapa_acciones = {
                "LANZAR": "RAISE",
                "APOSTAR": "RAISE",
                "SUBIR": "RAISE",
                "LLAMAR": "CALL",
                "IGUALAR": "CALL",
                "PAGAR": "CALL",
                "RETIRARSE": "FOLD",
                "RETIRAR": "FOLD",
                "PASAR": "CHECK",
            }
            data["accion"] = mapa_acciones.get(acc, acc)
            if data["accion"] == "ALLIN":
                data["accion"] = "ALL_IN"
            data["estado_pantalla"] = "EN_MESA_JUEGO"
            data["es_mi_turno"] = True

            m_raw = str(data.get("monto_apuesta") or "0")
            nums = re.findall(r"\d+", m_raw)
            data["monto_apuesta"] = int(nums[0]) if nums else 0

            try:
                data["confianza"] = min(1.0, max(0.0, float(data.get("confianza", 0.8))))
            except Exception:
                data["confianza"] = 0.8
            return json.dumps(data, ensure_ascii=False)
    except Exception:
        pass
    return clean


def _default_deterministic_fallback(dom_data: Optional[dict], reason: str) -> PokerDecision:
    """Fallback determinista mínimo seguro cuando no se inyecta motor externo."""
    dom = dom_data or {}
    opts = str(dom.get("opciones_disponibles") or dom.get("botones_visibles") or "").upper()
    if "CHECK" in opts:
        acc = "CHECK"
    elif "FOLD" in opts:
        acc = "FOLD"
    elif "CALL" in opts:
        acc = "CALL"
    else:
        acc = "FOLD" if dom.get("es_mi_turno") else "WAIT"
    return PokerDecision(
        evaluacion=f"Fallback determinista ({reason})",
        estrategia_competitiva="Protección determinista ante fallo LLM",
        estado_pantalla="EN_MESA_JUEGO",
        es_mi_turno=bool(dom.get("es_mi_turno", True)),
        accion=acc,  # type: ignore[arg-type]
        monto_apuesta=0,
        confianza=0.5,
    )


class AILayer:
    """Orquestador de decisiones IA con flujo instrumentado, circuit breaker y degradación limpia."""

    def __init__(
        self,
        api_key: Optional[str] = None,
        policy: Optional[ModelPolicy] = None,
        model_callable: Optional[Callable[[str, List[Any], float], Tuple[str, Optional[int], Optional[int]]]] = None,
        fallback_fn: Optional[Callable[[Optional[dict]], PokerDecision]] = None,
        use_genkit: Optional[bool] = None,
        prompt_version: Optional[str] = None,
        sleep_fn: Callable[[float], None] = time.sleep,
    ):
        self.policy = policy or ModelPolicy()
        self._model_callable = model_callable
        self._fallback_fn = fallback_fn
        self.use_genkit = bool(getattr(config, "USE_GENKIT", False) if use_genkit is None else use_genkit)
        self.prompt_version = prompt_version or getattr(config, "PROMPT_VERSION", "v1")
        self._sleep = sleep_fn
        self._client: Optional[genai.Client] = None
        self._genkit_app: Any = None
        self._genkit_flow: Any = None

        if self._model_callable is None:
            key = api_key if api_key is not None else getattr(config, "GEMINI_API_KEY", "")
            http_opts = types.HttpOptions(
                timeout=int(getattr(config, "AI_HTTP_TIMEOUT_MS", 3500)),
                retry_options=types.HttpRetryOptions(attempts=1),
            )
            self._client = genai.Client(api_key=key, http_options=http_opts) if key else genai.Client(http_options=http_opts)

        if self.use_genkit and _GENKIT_AVAILABLE:
            self._init_genkit_flow()

    def _init_genkit_flow(self) -> None:
        try:
            self._genkit_app = _GenkitSDK()

            @self._genkit_app.flow(name="decidir_accion")
            async def _genkit_decidir_flow(payload: Dict[str, Any]) -> Dict[str, Any]:
                return payload

            self._genkit_flow = _genkit_decidir_flow
        except Exception:
            self._genkit_app = None
            self._genkit_flow = None

    def _invoke_cloudflare_ai(self, model_alias: str, contents: List[Any], timeout_sec: float) -> Tuple[str, Optional[int], Optional[int]]:
        import urllib.request
        import json
        account_id = getattr(config, "CF_ACCOUNT_ID", "ac96116c5db82e232d04042b84c09399")
        token = getattr(config, "CF_API_TOKEN", "")
        cf_model = "@cf/deepseek-ai/deepseek-r1-distill-qwen-32b" if "deepseek" in model_alias else "@cf/meta/llama-3.1-8b-instruct-fp8"
        url = f"https://api.cloudflare.com/client/v4/accounts/{account_id}/ai/run/{cf_model}"

        prompt_text = " ".join([str(c) for c in contents if isinstance(c, str)])
        payload = {
            "messages": [
                {"role": "system", "content": "Eres un motor GTO de póker. Responde únicamente en JSON válido con las llaves: evaluacion, estrategia_competitiva, estado_pantalla, es_mi_turno, accion, monto_apuesta, confianza."},
                {"role": "user", "content": prompt_text}
            ],
            "max_tokens": 300,
            "temperature": 0.1
        }
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json"
        }
        req = urllib.request.Request(url, data=json.dumps(payload).encode("utf-8"), headers=headers)
        with urllib.request.urlopen(req, timeout=timeout_sec) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            raw = data.get("result", {}).get("response", "")
            if "<think>" in raw and "</think>" in raw:
                raw = raw.split("</think>")[-1].strip()
            usage = data.get("result", {}).get("usage", {})
            return raw, usage.get("prompt_tokens"), usage.get("completion_tokens")

    def _invoke_provider(self, model_name: str, contents: List[Any], timeout_sec: float) -> Tuple[str, Optional[int], Optional[int]]:
        if self._model_callable is not None:
            return self._model_callable(model_name, contents, timeout_sec)

        if str(model_name).startswith("cf-") or "llama" in str(model_name) or "deepseek" in str(model_name):
            try:
                cf_timeout = min(11.0, timeout_sec)
                return self._invoke_cloudflare_ai(model_name, contents, cf_timeout)
            except Exception as e:
                logger.warning("Error invocando Cloudflare Workers AI (%s): %s", model_name, e)
                raise
                raise

        assert self._client is not None
        gen_config_kwargs: Dict[str, Any] = {
            "response_mime_type": "application/json",
            "response_schema": PokerDecision,
            "temperature": 0.1,
        }
        if hasattr(types, "ThinkingConfig"):
            gen_config_kwargs["thinking_config"] = types.ThinkingConfig(thinking_budget=0)

        # Timeout acotado por llamada a 6.0s para conmutar a Cloudflare Workers AI si Gemini tarda
        call_timeout = min(6.0, max(1.5, timeout_sec)) if timeout_sec > 1.5 else timeout_sec

        def _do_gemini_call():
            return self._client.models.generate_content(
                model=model_name,
                contents=contents,
                config=types.GenerateContentConfig(**gen_config_kwargs),
            )

        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(_do_gemini_call)
            response = future.result(timeout=call_timeout)

        raw_text = (getattr(response, "text", None) or "").strip()
        usage = getattr(response, "usage_metadata", None)
        t_in = getattr(usage, "prompt_token_count", None) if usage else None
        t_out = getattr(usage, "candidates_token_count", None) if usage else None
        return raw_text, t_in, t_out

    def _run_genkit_hook(self, payload: Dict[str, Any]) -> None:
        if not (self.use_genkit and self._genkit_flow is not None):
            return
        try:
            loop = asyncio.new_event_loop()
            try:
                loop.run_until_complete(self._genkit_flow(payload))
            finally:
                loop.close()
        except Exception:
            pass

    def decidir(
        self,
        dom_data: Optional[dict] = None,
        image_bytes: Optional[bytes] = None,
        perfil_rival_str: str = "",
        decision_uid: str = "",
        hand_uid: str = "",
        timeout_sec: Optional[float] = None,
        prompt_version: Optional[str] = None,
    ) -> AIDecisionResult:
        """Ejecuta el flujo instrumentado decidir_accion y garantiza respuesta segura."""
        t_start = time.perf_counter()
        budget_sec = float(timeout_sec or getattr(config, "AI_DECISION_TIMEOUT_SEC", 13.0))
        deadline = t_start + budget_sec
        uid = decision_uid or uuid.uuid4().hex[:24]

        backend_name = "genkit+google_genai" if (self.use_genkit and self._genkit_flow is not None) else "google_genai"
        trace = DecisionTrace(
            decision_uid=uid,
            hand_uid=hand_uid,
            prompt_version=prompt_version or self.prompt_version,
            backend=backend_name,
        )

        # Paso 1: build_prompt
        s0 = time.perf_counter()
        table_input = build_table_input(dom_data, decision_uid=uid, hand_uid=hand_uid)
        p_ver, p_hash, prompt_text = render_prompt(dom_data, trace.prompt_version, perfil_rival_str)
        trace.prompt_version = p_ver
        trace.prompt_hash = p_hash
        trace.steps.append(
            StepTrace(
                step="build_prompt",
                duration_ms=round((time.perf_counter() - s0) * 1000.0, 2),
                status="OK",
                detail=f"version={p_ver} hash={p_hash}",
            )
        )

        contents: List[Any] = [prompt_text]
        if image_bytes:
            contents = [types.Part.from_bytes(data=image_bytes, mime_type="image/jpeg"), prompt_text]

        self._run_genkit_hook({"decision_uid": uid, "hand_uid": hand_uid, "prompt_hash": p_hash})

        # Paso 2 & 3: model_call + schema_validation
        candidatos = self.policy.available_models() or list(self.policy.models)
        max_schema_retries = int(getattr(config, "AI_MAX_SCHEMA_RETRIES", 1))
        max_rate_attempts = int(getattr(config, "AI_RETRY_ATTEMPTS", 2))
        base_ms = int(getattr(config, "AI_RETRY_BASE_MS", 150))

        parsed_decision: Optional[PokerDecision] = None
        last_reason = "no_model_succeeded"

        for model_name in candidatos:
            if time.perf_counter() >= deadline:
                last_reason = "decision_timeout_exceeded"
                break

            schema_tries = 0
            rate_tries = 0
            while True:
                remaining = deadline - time.perf_counter()
                if remaining <= 0:
                    last_reason = "decision_timeout_exceeded"
                    break

                call_t0 = time.perf_counter()
                try:
                    raw_text, t_in, t_out = self._invoke_provider(model_name, contents, remaining)
                    call_ms = round((time.perf_counter() - call_t0) * 1000.0, 2)
                    trace.model_used = model_name
                    trace.raw_response = raw_text[:2000]
                    trace.tokens_in = t_in
                    trace.tokens_out = t_out

                    if not raw_text:
                        last_reason = "empty_response"
                        trace.steps.append(StepTrace(step="model_call", duration_ms=call_ms, status="ERROR", detail="empty_response"))
                        self.policy.record_failure(model_name, "EMPTY_RESPONSE")
                        break

                    trace.steps.append(StepTrace(step="model_call", duration_ms=call_ms, status="OK", detail=model_name))

                    val_t0 = time.perf_counter()
                    clean_json = _sanitizar_json_decision(raw_text)
                    try:
                        parsed_decision = PokerDecision.model_validate_json(clean_json)
                        trace.schema_valid = True
                        trace.steps.append(
                            StepTrace(step="schema_validation", duration_ms=round((time.perf_counter() - val_t0) * 1000.0, 2), status="OK")
                        )
                        self.policy.record_success(model_name)
                        break
                    except Exception as ve:
                        ve_msg = re.sub(r"\s+", " ", str(ve))[:180]
                        last_reason = f"schema_error:{type(ve).__name__} | {ve_msg}"
                        trace.steps.append(
                            StepTrace(
                                step="schema_validation",
                                duration_ms=round((time.perf_counter() - val_t0) * 1000.0, 2),
                                status="RETRY" if schema_tries < max_schema_retries else "ERROR",
                                detail=last_reason,
                            )
                        )
                        if schema_tries < max_schema_retries and (deadline - time.perf_counter()) > 0.2:
                            schema_tries += 1
                            trace.retry_count += 1
                            continue
                        self.policy.record_failure(model_name, "SCHEMA_ERROR")
                        break

                except Exception as exc:
                    call_ms = round((time.perf_counter() - call_t0) * 1000.0, 2)
                    kind = classify_error(exc)
                    last_reason = f"{kind}:{type(exc).__name__}"
                    err_msg = re.sub(r"\s+", " ", str(exc))[:220]
                    trace.steps.append(
                        StepTrace(step="model_call", duration_ms=call_ms, status="ERROR", detail=f"{model_name} {last_reason} | {err_msg}")
                    )
                    if kind == "RATE_LIMIT_429_503" and rate_tries < max_rate_attempts:
                        backoff = self.policy.compute_backoff_sec(rate_tries, base_ms=base_ms)
                        if (deadline - time.perf_counter()) > backoff + 0.1:
                            rate_tries += 1
                            trace.retry_count += 1
                            self._sleep(backoff)
                            continue
                    self.policy.record_failure(model_name, kind)
                    break

            if parsed_decision is not None:
                break

        # Paso 4: legality_validation
        if parsed_decision is not None:
            leg_t0 = time.perf_counter()
            is_legal, checked_dec, leg_note = validate_legality(parsed_decision, table_input)
            trace.legality_valid = is_legal
            trace.steps.append(
                StepTrace(
                    step="legality_validation",
                    duration_ms=round((time.perf_counter() - leg_t0) * 1000.0, 2),
                    status="OK" if is_legal else "FALLBACK",
                    detail=leg_note or "legal",
                )
            )
            if is_legal:
                trace.total_latency_ms = round((time.perf_counter() - t_start) * 1000.0, 2)
                trace.steps.append(StepTrace(step="final_result", duration_ms=0.0, status="OK", detail=checked_dec.accion))
                return AIDecisionResult(decision=checked_dec, decision_source=backend_name, trace=trace)
            last_reason = leg_note

        # Paso 5: fallback determinista garantizado
        fb_t0 = time.perf_counter()
        trace.fallback_reason = last_reason
        fb_dec = self._fallback_fn(dom_data) if self._fallback_fn is not None else _default_deterministic_fallback(dom_data, last_reason)
        trace.steps.append(
            StepTrace(
                step="final_result",
                duration_ms=round((time.perf_counter() - fb_t0) * 1000.0, 2),
                status="FALLBACK",
                detail=f"{last_reason} -> {fb_dec.accion}",
            )
        )
        trace.total_latency_ms = round((time.perf_counter() - t_start) * 1000.0, 2)
        return AIDecisionResult(decision=fb_dec, decision_source="gto_fallback_0ms", trace=trace)
