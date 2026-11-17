import logging
import os
from pathlib import Path
import platform
import re
import io
import json
import time
from pydantic import BaseModel, Field
from typing import Literal, Optional, Tuple
from google import genai
from google.genai import types
import config
from ai_layer import AIDecisionResult, AILayer, DecisionTrace, PokerDecision

logger = logging.getLogger(__name__)


def optimizar_imagen(image_bytes: bytes) -> tuple[bytes, str]:
    """
    Comprime la captura de pantalla a JPEG conservando legibilidad de números pequeños y cartas
    (sin reducir por debajo de 1920x1080 y con subsampling=0). Registra cualquier error en logger.
    """
    if not image_bytes or len(image_bytes) < 8:
        return image_bytes or b"", "image/png"

    try:
        from PIL import Image
        img = Image.open(io.BytesIO(image_bytes))
        if img.width > 1920 or img.height > 1080:
            resampling = getattr(getattr(Image, "Resampling", Image), "LANCZOS", 1)
            img.thumbnail((1920, 1080), resampling)
        buf = io.BytesIO()
        img.convert("RGB").save(buf, format="JPEG", quality=85, subsampling=0)
        return buf.getvalue(), "image/jpeg"
    except Exception as e:
        logger.warning("⚠️ [optimizar_imagen] Error procesando captura (%s): %s", type(e).__name__, e)
        return image_bytes, "image/png"


def formatear_perfiles_para_prompt(dom_data: Optional[dict]) -> str:
    """Formatea perfiles multijugador (perfiles_oponentes) o perfil único (perfil_rival) para el prompt."""
    if not dom_data:
        return ""

    perfiles = dom_data.get("perfiles_oponentes")
    bb_val = float(dom_data.get("big_blind") or getattr(config, "BIG_BLIND_DEFAULT", 20.0) or 20.0)
    if isinstance(perfiles, list) and len(perfiles) > 0:
        lineas = ["            ANÁLISIS EXPLOTATIVO DE RIVALES EN MESA (3-MAX):"]
        for p in perfiles:
            if not isinstance(p, dict):
                continue
            asiento = p.get("asiento", p.get("seat", "?"))
            nick = p.get("nickname", f"Rival_{asiento}")
            estado = str(p.get("estado", "ACTIVO")).upper()
            if p.get("stack_bb") is not None:
                stack_bb = float(p.get("stack_bb") or 0.0)
            else:
                raw_stack = p.get("stack", p.get("stack_chips", 0))
                try:
                    stack_chips = float(re.sub(r"[^\d.]", "", str(raw_stack)) or 0.0)
                except Exception:
                    stack_chips = 0.0
                stack_bb = (stack_chips / bb_val) if bb_val > 0 else 0.0
            arquetipo = p.get("estilo") or p.get("arquetipo") or "Desconocido"
            muestra = int(p.get("manos_jugadas") or p.get("manos") or p.get("sample_size") or 0)
            vpip = p.get("vpip_pct", 0)
            pfr = p.get("pfr_pct", 0)
            af = p.get("agresividad", 1.0)
            rec = p.get("recomendacion_tactica", "Juego estándar")
            lineas.append(
                f"            - Asiento {asiento} ({nick}) [{estado}] | Stack: {stack_bb:.1f} BB | "
                f"Arquetipo: {arquetipo} | Stats (n={muestra}): VPIP {vpip}% / PFR {pfr}% / AF {af} | "
                f"Directriz: {rec}"
            )
        return "\n".join(lineas)

    perfil = dom_data.get("perfil_rival", {})
    if perfil:
        muestra = int(perfil.get("manos_jugadas") or perfil.get("manos") or perfil.get("sample_size") or 0)
        return f"""
            ANÁLISIS EXPLOTATIVO DEL RIVAL DE LA MESA ({perfil.get('nickname', 'Rival')}):
            - Estilo de juego detectado: {perfil.get('estilo', 'Desconocido')} (n={muestra} manos)
            - VPIP: {perfil.get('vpip_pct', 0)}% | PFR: {perfil.get('pfr_pct', 0)}% | Factor Agresión: {perfil.get('agresividad', 1.0)}
            - DIRECTRIZ TÁCTICA EXPLOTATIVA: {perfil.get('recomendacion_tactica', 'Juego estándar')}
            """
    return ""


class PokerAgent:
    """Adaptador compatible hacia atrás que delega la inferencia a ai_layer.AILayer."""

    def __init__(self, api_key: str = None, ai_layer: Optional[AILayer] = None, fallback_fn=None):
        key = api_key if api_key is not None else config.GEMINI_API_KEY
        if not key:
            print("⚠️ ADVERTENCIA: No se encontró GEMINI_API_KEY en el archivo .env o variables de entorno.")
        self.primary_model = config.GEMINI_MODEL
        self.ai_layer = ai_layer or AILayer(api_key=key, fallback_fn=fallback_fn)
        self.last_result: Optional[AIDecisionResult] = None
        self.last_trace: Optional[DecisionTrace] = None

    def analizar_pantalla(
        self,
        image_bytes: Optional[bytes] = None,
        dom_data: Optional[dict] = None,
        decision_uid: str = "",
        hand_uid: str = "",
    ) -> PokerDecision:
        active_model = getattr(config, "ACTIVE_MODEL", config.GEMINI_MODEL)
        if active_model == "gemma-4b-local":
            print("💻 Consultando a Gemma 4B Local (GGUF)...")
            prompt_local = f"Mesa de póker: {dom_data}. Responde en JSON con la acción (FOLD, CHECK, CALL, RAISE, ALL_IN), evaluacion y confianza."
            dec_local = consultar_gemma_local(prompt_local, dom_data=dom_data)
            if dec_local:
                return dec_local
            print("⚠️ Gemma 4B Local no retornó respuesta, derivando a Gemini / GTO...")

        img_opt: Optional[bytes] = None
        if image_bytes:
            img_opt, _ = optimizar_imagen(image_bytes)

        perfil_rival_str = formatear_perfiles_para_prompt(dom_data) if dom_data else ""
        result = self.ai_layer.decidir(
            dom_data=dom_data,
            image_bytes=img_opt,
            perfil_rival_str=perfil_rival_str,
            decision_uid=decision_uid,
            hand_uid=hand_uid,
        )
        self.last_result = result
        self.last_trace = result.trace
        if result.trace.fallback_reason:
            logger.info("AI fallback reason=%s model=%s", result.trace.fallback_reason, result.trace.model_used)
        return result.decision


_gemma_model_instance = None


def validar_ruta_gemma_local(path_str: Optional[str] = None) -> Tuple[bool, str]:
    """Valida GEMMA_LOCAL_MODEL_PATH según el SO actual y degrada con aviso claro si no existe."""
    so = platform.system()
    raw = path_str if path_str is not None else getattr(config, "GEMMA_LOCAL_MODEL_PATH", "")
    raw = str(raw or "").strip()
    if not raw:
        return False, f"GEMMA_LOCAL_MODEL_PATH no configurado en {so}; inferencia local deshabilitada."
    if so != "Windows" and re.match(r"^[A-Za-z]:\\", raw):
        return False, f"Ruta Windows '{raw}' incompatible con {so}; configura GEMMA_LOCAL_MODEL_PATH en .env."
    expanded = Path(os.path.expandvars(os.path.expanduser(raw)))
    if not expanded.is_file():
        return False, f"No se encontró el archivo GGUF en '{expanded}' ({so}); degradando a Gemini/GTO."
    return True, str(expanded)


def precargar_gemma_local():
    """Carga de forma perezosa el modelo Gemma 4B Local GGUF en memoria."""
    global _gemma_model_instance
    if _gemma_model_instance is not None:
        return _gemma_model_instance
    ok, info = validar_ruta_gemma_local()
    if not ok:
        print(f"⚠️ [LLM LOCAL] {info}")
        return None
    try:
        from llama_cpp import Llama
        print(f"💻 [LLM LOCAL] Cargando Gemma 4B desde: {info}...")
        _gemma_model_instance = Llama(
            model_path=info,
            n_ctx=2048,
            n_threads=6,
            verbose=False
        )
        print("✅ [LLM LOCAL] Gemma 4B cargado en memoria correctamente.")
        return _gemma_model_instance
    except Exception as e:
        print(f"⚠️ [LLM LOCAL ERROR] No se pudo cargar Gemma 4B: {e}")
        return None

def consultar_gemma_local(prompt: str, dom_data: Optional[dict] = None) -> Optional[PokerDecision]:
    """Ejecuta inferencia local con Gemma 4B GGUF utilizando estructuración JSON sin gramática estricta para evitar crash C++."""
    llm = precargar_gemma_local()
    if not llm:
        return None
    try:
        sys_prompt = "Eres un bot profesional de póker GTO. Responde únicamente en formato JSON válido con los campos: \"accion\" (\"FOLD\", \"CHECK\", \"CALL\", \"RAISE\", \"ALL_IN\"), \"evaluacion\", \"confianza\" (float 0.0 a 1.0), \"monto_apuesta\" (entero)."
        messages = [
            {"role": "system", "content": sys_prompt},
            {"role": "user", "content": prompt}
        ]
        # Omitimos response_format={"type": "json_object"} porque activa el muestreador de gramática en C++ que causa GGML_ASSERT en Gemma-4 / ops.cpp
        res = llm.create_chat_completion(
            messages=messages,
            temperature=0.1,
            max_tokens=350
        )
        raw_content = res['choices'][0]['message']['content'].strip()
        json_match = re.search(r"\{.*\}", raw_content, flags=re.DOTALL)
        clean_json = json_match.group(0) if json_match else raw_content
        clean_json = re.sub(r"^```(?:json)?\s*|\s*```$", "", clean_json, flags=re.S).strip()
        data = json.loads(clean_json)

        accion = str(data.get("accion") or data.get("action") or "CHECK").upper()
        if accion == "ALLIN": accion = "ALL_IN"
        if accion not in ("FOLD", "CHECK", "CALL", "RAISE", "ALL_IN", "ALLIN", "REPLAY", "START_GAME", "WAIT"):
            accion = "CHECK"

        # Safe parsing of numeric values
        raw_monto = data.get("monto_apuesta") or data.get("bet_amount") or 0
        try:
            monto_val = int(re.sub(r"[^\d]", "", str(raw_monto)) or 0)
        except Exception:
            monto_val = 0

        raw_conf = str(data.get("confianza") or "0.85").strip().lower()
        if "alta" in raw_conf or "high" in raw_conf:
            conf_val = 0.90
        elif "media" in raw_conf or "medium" in raw_conf:
            conf_val = 0.70
        elif "baja" in raw_conf or "low" in raw_conf:
            conf_val = 0.50
        else:
            try:
                match = re.search(r"0?\.\d+|1\.0|0|1", raw_conf)
                conf_val = float(match.group(0)) if match else 0.85
            except Exception:
                conf_val = 0.85

        decision = PokerDecision(
            evaluacion=str(data.get("evaluacion") or data.get("reasoning") or "Evaluación Gemma 4B Local"),
            estrategia_competitiva=str(data.get("estrategia_competitiva") or "Juego local GTO Gemma"),
            estado_pantalla="EN_MESA_JUEGO",
            es_mi_turno=True,
            accion=accion,
            monto_apuesta=monto_val,
            confianza=conf_val
        )

        opciones_dom = str((dom_data or {}).get("opciones_disponibles", "") or (dom_data or {}).get("botones_visibles", "")).upper()
        if decision.accion == "FOLD" and "CHECK" in opciones_dom:
            decision.accion = "CHECK"
            decision.evaluacion = f"[Auto-Check Gratis] {decision.evaluacion}"

        return decision
    except Exception as e:
        print(f"⚠️ [LLM LOCAL] Error durante inferencia Gemma 4B: {e}")
        return None
        if decision.accion == "FOLD" and "CHECK" in opciones_dom:
            decision.accion = "CHECK"
            decision.evaluacion = f"[Auto-Check Gratis] {decision.evaluacion}"

        return decision
    except Exception as e:
        print(f"⚠️ [LLM LOCAL] Error durante inferencia Gemma 4B: {e}")
        return None
