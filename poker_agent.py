import re
import io
import json
import time
from pydantic import BaseModel, Field
from typing import Literal, Optional
from google import genai
from google.genai import types
import config

def optimizar_imagen(image_bytes: bytes) -> tuple[bytes, str]:
    """
    Comprime la captura de pantalla PNG (~2.5MB) a un JPEG ultraligero (~50KB) a 1024px.
    Esto reduce el tiempo de subida de red y latencia de Gemini de 32s a ~0.5s.
    """
    try:
        from PIL import Image
        img = Image.open(io.BytesIO(image_bytes))
        img.thumbnail((1024, 768))
        buf = io.BytesIO()
        img.convert("RGB").save(buf, format="JPEG", quality=70)
        return buf.getvalue(), "image/jpeg"
    except Exception:
        return image_bytes, "image/png"

class PokerDecision(BaseModel):
    evaluacion: str = Field(description="Una frase concisa con la evaluación táctica de la mesa")
    estrategia_competitiva: str = Field(default="Estrategia defensiva de respaldo", description="Breve plan estratégico")
    estado_pantalla: Literal["EN_MESA_JUEGO", "LOBBY_SELECCION", "FIN_DE_PARTIDA", "PANTALLA_DESCONOCIDA"] = Field(description="Estado detectado")
    es_mi_turno: bool = Field(description="True si los botones de acción están activos")
    accion: Literal["FOLD", "CHECK", "CALL", "RAISE", "ALL_IN", "ALLIN", "REPLAY", "START_GAME", "WAIT"] = Field(description="Acción recomendada")
    monto_apuesta: int = Field(default=0, ge=0, description="Monto sugerido para apostar si la acción es RAISE")
    confianza: float = Field(default=0.5, ge=0.0, le=1.0, description="Nivel de confianza entre 0.0 y 1.0")

class PokerAgent:
    def __init__(self, api_key: str = None):
        key = api_key or config.GEMINI_API_KEY
        if not key:
            print("⚠️ ADVERTENCIA: No se encontró GEMINI_API_KEY en el archivo .env o variables de entorno.")
        http_opts = types.HttpOptions(retry_options=types.HttpRetryOptions(attempts=1))
        self.client = genai.Client(api_key=key, http_options=http_opts) if key else genai.Client(http_options=http_opts)
        self.primary_model = config.GEMINI_MODEL
        self.model_cooldown_until: dict[str, float] = {}

    def analizar_pantalla(self, image_bytes: bytes, dom_data: Optional[dict] = None) -> PokerDecision:
        dom_info_str = ""
        historial_str = ""
        perfil_rival_str = ""
        gto_section_str = ""
        comb_str = ""
        textura_str = ""
        frecuencia_str = ""
        memoria_str = ""

        active_model = getattr(config, "ACTIVE_MODEL", "gemini-3.1-flash-lite")
        if active_model == "gemma-4b-local":
            print("💻 Consultando a Gemma 4B Local (GGUF)...")
            prompt_local = f"Mesa de póker: {dom_data}. Responde en JSON con la acción (FOLD, CHECK, CALL, RAISE, ALL_IN), evaluacion y confianza."
            dec_local = consultar_gemma_local(prompt_local, dom_data=dom_data)
            if dec_local:
                return dec_local
            print("⚠️ Gemma 4B Local no retornó respuesta, derivando a Gemini / GTO...")
        elif active_model == "gto-heuristic":
            print("🛡️ Modo Heurístico GTO activo. Retornando respuesta rápida 0ms...")
            return PokerDecision(
                evaluacion="Decisión tomada exclusivamente por el Motor Heurístico GTO 0ms",
                estrategia_competitiva="Lógica determinista GTO de respaldo",
                estado_pantalla="EN_MESA_JUEGO",
                es_mi_turno=True,
                accion="WAIT",
                monto_apuesta=0,
                confianza=0.9
            )
        historial_str = ""
        perfil_rival_str = ""
        gto_section_str = ""
        comb_str = ""
        textura_str = ""
        frecuencia_str = ""

        if dom_data:
            es_mi_turno_dom = dom_data.get('es_mi_turno', False)
            tiempo = dom_data.get('tiempo_restante', '')
            mis_cartas = dom_data.get('mis_cartas', [])
            comunitarias = dom_data.get('cartas_comunitarias', [])
            mis_cartas_str = " ".join(mis_cartas) if isinstance(mis_cartas, list) else str(mis_cartas)
            comunitarias_str = " ".join(comunitarias) if isinstance(comunitarias, list) else str(comunitarias)
            historial_str = dom_data.get('historial_mano', '')

            perfil = dom_data.get('perfil_rival', {})
            if perfil:
                perfil_rival_str = f"""
            ANÁLISIS EXPLOTATIVO DEL RIVAL DE LA MESA ({perfil.get('nickname', 'Rival')}):
            - Estilo de juego detectado: {perfil.get('estilo', 'Desconocido')}
            - VPIP: {perfil.get('vpip_pct', 0)}% | PFR: {perfil.get('pfr_pct', 0)}% | Factor Agresión: {perfil.get('agresividad', 1.0)}
            - DIRECTRIZ TÁCTICA EXPLOTATIVA: {perfil.get('recomendacion_tactica', 'Juego estándar')}
            """

            gto_pre = dom_data.get('gto_preflop', {})
            gto_math = dom_data.get('gto_math', {})
            if gto_pre or gto_math:
                gto_section_str = f"""
            📐 ANÁLISIS MATEMÁTICO Y MATRIZ DE RANGOS GTO:
            - Rango Preflop: Tier [{gto_pre.get('tier', 'N/A')}] (Score: {gto_pre.get('score', 0)}/100) - Mano: {gto_pre.get('mano_code', '')}
            - Pot Odds: {gto_math.get('pot_odds_pct', 0)}% (Precio a pagar para igualar)
            - Sizings Sugeridos (Apuestas): Pequeño 33%: {gto_math.get('sizing_cbet_pequeno', 0)} | Medio 66%: {gto_math.get('sizing_cbet_estandar', 0)} | Pot: {gto_math.get('sizing_cbet_fuerte', 0)}
            """

            gto_comb = dom_data.get('gto_combinacion', {})
            if gto_comb:
                comb_str = f"""
            🎴 DISTINCIÓN DE COMBINACIÓN (MANO PRIVADA VS MESA):
            - Clasificación Táctica: {gto_comb.get('categoria')}
            - Detalle: {gto_comb.get('descripcion')}
            - ¿Es Pareja en Mano (Pocket Pair)?: {"SI ⚡ (Privada de alto valor)" if gto_comb.get('es_pareja_en_mano') else "NO"}
            - ¿Hay Pareja Compartida en Mesa?: {"SI (Todos los jugadores comparten la pareja de la mesa)" if gto_comb.get('pareja_en_mesa') else "NO"}
            """

            gto_textura = dom_data.get('gto_textura', {})
            if gto_textura:
                textura_str = f"""
            🛡️ BLOCKERS (CARD REMOVAL) Y ANÁLISIS DE TEXTURA DE MESA:
            - Textura Tablero: {gto_textura.get('textura_mesa')}
            - Blockers en Mano / Mesa: Ases muertos = {gto_textura.get('blockers_ases')} | Reyes muertos = {gto_textura.get('blockers_reyes')}
            - ALERTA DE FAROLES E INCONSISTENCIA: {gto_textura.get('alerta_farol_inconsistente', 'Línea de apuestas coherente')}
            """

            gto_frec = dom_data.get('gto_frecuencia', {})
            if gto_frec:
                frecuencia_str = f"""
            🎲 VARIANZA GTO Y FRECUENCIA ANTI-EXPLOTACIÓN:
            - Semilla Frecuencia (1-100): {gto_frec.get('d100')} -> Sugerencia: {gto_frec.get('linea_sugerida')}
            - INSTRUCCIÓN DE IMPREDECIBILIDAD: Aplica frecuencias mixtas para que ningún rival pueda descifrar tus patrones.
            """

            memoria_lista = dom_data.get('memoria_errores', [])
            if memoria_lista:
                memoria_str = "🧠 RECUERDOS VECTORIALES Y LECCIONES APRENDIDAS DE ERRORES PASADOS:\n"
                for m in memoria_lista:
                    memoria_str += f"- ⚠️ LECCIÓN PASADA APRENDIDA: Con Rango [{m['hand_tier']}] en {m['board_texture']} contra {m['opp_archetype']}, la acción '{m['action_taken']}' causó una pérdida de {m['loss_amount']} fichas.\n  RECOMENDACIÓN ANTI-ERROR: {m['lesson_learned']}\n"

            dom_info_str = f"""
            ESTADO MESA EN TIEMPO REAL:
            - ES_MI_TURNO: {"SI" if es_mi_turno_dom else "NO"} (tiempo: {tiempo}s)
            - Cartas mano: {mis_cartas_str or 'N/A'}
            - Comunitarias: {comunitarias_str or 'Ninguna'}
            - Bote: {dom_data.get('bote', 0)} | A igualar: {dom_data.get('a_igualar', 0)}
            - Mi stack: {dom_data.get('mi_stack', 0)} | Stack rivales: {dom_data.get('stack_rival', 0)}
            - Fuerza mano: {dom_data.get('fuerza_mano', 'N/A')}
            - OPCIONES DISPONIBLES EN PANTALLA: {dom_data.get('opciones_disponibles', dom_data.get('botones_visibles', ''))}
            """

        historial_section = f"\nHISTORIAL SECUENCIAL DE LA MANO (EVENTOS PREVIOS):\n{historial_str}\n" if historial_str else ""

        prompt = f"""
        Eres un Campeón Mundial de Póker Profesional (GTO + Explotativo + Lector de Faroles + Memoria Vectorial).
        Tu objetivo es jugar con la máxima precisión matemática y táctica para dominar el torneo Twister 3-Max.

        {dom_info_str}
        {gto_section_str}
        {comb_str}
        {textura_str}
        {frecuencia_str}
        {perfil_rival_str}
        {memoria_str}
        {historial_section}

        ============================================================
        🏆 REGLAS MAESTRAS DE APRENDIZAJE Y DECISIONES:
        ============================================================
        1. APRENDIZAJE DE ERRORES PASADOS: NUNCA repitas una acción que en el pasado provocó una pérdida en una situación similar (revisa la sección RECUERDOS VECTORIALES).
        2. DISTINCIÓN ENTRE MANO PRIVADA Y MESA COMPARTIDA: Si la pareja o trío está en tu mano privada (Pocket Pair / Set Oculto), juega muy agresivo por valor. Si la pareja es de la mesa compartida, no asumas tener la mejor mano.
        3. IMPREDECIBILIDAD Y ESTRATEGIA MIXTA GTO: Mezcla tus secuencias usando la Semilla de Frecuencia para alternar entre apuestas de valor, slowplays y semifaroles.
        4. ANÁLISIS DE BLOCKERS Y LÍNEAS INCONSISTENTES: Si la mesa es SECA y el rival sube agresivamente, evalúa si es un FAROL INCONSISTENTE y paga (CALL/RAISE) para cazarlo.
        5. MODO PUSH/FOLD EN STACK CORTO (<180 fichas): En stack corto, elimina min-raises. Juega ALL_IN directo con cualquier As (A2+), Pareja (22+), Reyes (K5+) o cartas altas.
        6. ACCIONES LEGALES ESTRICTAS: Selecciona exclusivamente FOLD, CHECK, CALL, RAISE o ALL_IN. Para RAISE, indica 'monto_apuesta'.
        ============================================================
        """

        img_bytes_opt, mime_type = optimizar_imagen(image_bytes)

        candidatos = list(dict.fromkeys([
            self.primary_model,
            "gemini-3.1-flash-lite",
            "gemini-3.6-flash",
            "gemini-3.5-flash",
            "gemini-3.5-flash-lite"
        ]))
        ahora = time.time()
        modelos_activos = [m for m in candidatos if ahora >= self.model_cooldown_until.get(m, 0)]
        if not modelos_activos:
            modelos_activos = candidatos

        for model_name in modelos_activos:
            try:
                response = self.client.models.generate_content(
                    model=model_name,
                    contents=[
                        types.Part.from_bytes(data=img_bytes_opt, mime_type=mime_type),
                        prompt
                    ],
                    config=types.GenerateContentConfig(
                        response_mime_type="application/json",
                        response_schema=PokerDecision,
                        temperature=0.1
                    )
                )

                raw_text = (response.text or "").strip()
                if raw_text:
                    clean_json = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw_text, flags=re.S).strip()
                    decision = PokerDecision.model_validate_json(clean_json)
                    self.primary_model = model_name
                    if decision.accion == "ALLIN":
                        decision.accion = "ALL_IN"
                    # Si CHECK es gratis y el modelo pidió FOLD, convertir automáticamente a CHECK
                    opciones_dom = str((dom_data or {}).get("opciones_disponibles", "") or (dom_data or {}).get("botones_visibles", "")).upper()
                    if decision.accion == "FOLD" and "CHECK" in opciones_dom:
                        decision.accion = "CHECK"
                        decision.evaluacion = f"[Auto-Check Gratis] {decision.evaluacion}"
                    # Si el DOM indica opciones de juego activas y el modelo alucinó START_GAME/REPLAY, invalidar a WAIT para que actúe el motor GTO
                    if dom_data and (dom_data.get('es_mi_turno') or dom_data.get('mis_cartas')) and decision.accion in ("START_GAME", "REPLAY"):
                        decision.accion = "WAIT"
                    return decision
            except Exception as e:
                err_str = str(e)
                if "404" in err_str or "NOT_FOUND" in err_str:
                    self.model_cooldown_until[model_name] = time.time() + 86400
                elif "PerDay" in err_str or "per day" in err_str.lower():
                    self.model_cooldown_until[model_name] = time.time() + 3600
                    print(f"ℹ️ Cuota diaria alcanzada en {model_name}. Desactivado por 1h y promoviendo siguiente modelo...")
                elif "429" in err_str or "503" in err_str:
                    self.model_cooldown_until[model_name] = time.time() + 45
                    print(f"⚠️ Límite por minuto/demanda en {model_name}, rotando por 45s...")
                else:
                    print(f"⚠️ Modelo {model_name} no disponible ({type(e).__name__}), probando respaldo...")
                continue

        return PokerDecision(
            evaluacion="Respuesta rápida de fallback por timeout o API ocupada",
            estrategia_competitiva="Fallback por velocidad de API",
            estado_pantalla="EN_MESA_JUEGO",
            es_mi_turno=True,
            accion="WAIT",
            monto_apuesta=0,
            confianza=0.0
        )


_gemma_model_instance = None

def precargar_gemma_local():
    """Carga de forma perezosa el modelo Gemma 4B Local GGUF en memoria."""
    global _gemma_model_instance
    if _gemma_model_instance is not None:
        return _gemma_model_instance
    try:
        from llama_cpp import Llama
        model_path = getattr(config, "GEMMA_LOCAL_MODEL_PATH", r"C:\Users\SATAP\.android\models\gemma-4-4b\gemma-4-E4B_q4_0-it.gguf")
        print(f"💻 [LLM LOCAL] Cargando Gemma 4B desde: {model_path}...")
        _gemma_model_instance = Llama(
            model_path=model_path,
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
    """Ejecuta inferencia local con Gemma 4B GGUF utilizando estructuración JSON."""
    llm = precargar_gemma_local()
    if not llm:
        return None
    try:
        sys_prompt = "Eres un bot profesional de póker GTO. Responde estrictamente en formato JSON válido con los campos: evaluacion, estrategia_competitiva, estado_pantalla, es_mi_turno, accion, monto_apuesta, confianza."
        messages = [
            {"role": "system", "content": sys_prompt},
            {"role": "user", "content": prompt}
        ]
        res = llm.create_chat_completion(
            messages=messages,
            response_format={"type": "json_object"},
            temperature=0.1,
            max_tokens=512
        )
        raw_content = res['choices'][0]['message']['content'].strip()
        clean_json = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw_content, flags=re.S).strip()
        data = json.loads(clean_json)

        accion = str(data.get("accion") or data.get("action") or "CHECK").upper()
        if accion == "ALLIN": accion = "ALL_IN"
        if accion not in ("FOLD", "CHECK", "CALL", "RAISE", "ALL_IN", "ALLIN", "REPLAY", "START_GAME", "WAIT"):
            accion = "CHECK"

        decision = PokerDecision(
            evaluacion=str(data.get("evaluacion") or data.get("reasoning") or "Evaluación Gemma 4B Local"),
            estrategia_competitiva=str(data.get("estrategia_competitiva") or "Juego local GTO Gemma"),
            estado_pantalla="EN_MESA_JUEGO",
            es_mi_turno=True,
            accion=accion,
            monto_apuesta=int(data.get("monto_apuesta") or data.get("bet_amount") or 0),
            confianza=float(data.get("confianza") or 0.85)
        )

        opciones_dom = str((dom_data or {}).get("opciones_disponibles", "") or (dom_data or {}).get("botones_visibles", "")).upper()
        if decision.accion == "FOLD" and "CHECK" in opciones_dom:
            decision.accion = "CHECK"
            decision.evaluacion = f"[Auto-Check Gratis] {decision.evaluacion}"

        return decision
    except Exception as e:
        print(f"⚠️ [LLM LOCAL] Error durante inferencia Gemma 4B: {e}")
        return None
