"""
Monitor Multijugador de Mesa en Vivo (LiveTableMonitor).
Detecta dinámicamente todos los jugadores sentados (2, 3, 6, 9+ asientos),
excluye al héroe, distingue jugadores sentados vs activos vs en mano actual,
unifica identidades al cambiar de asiento/ID y sincroniza cada rival con OpponentProfiler.
"""
from __future__ import annotations

import json
import os
import re
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from opponents_profiler import (
    OpponentProfiler,
    generar_analisis_explotativo_contextual,
    es_nickname_invalido,
    sanitizar_nickname,
)

_sanitizar_nickname = sanitizar_nickname

LIVE_STATE_PATH = Path("logs/live_table_state.json")

_STATUS_MAP = {
    "ACTIVE": "ACTIVE",
    "PLAYING": "ACTIVE",
    "ACTING": "ACTIVE",
    "IN_HAND": "ACTIVE",
    "ALL_IN": "ACTIVE",
    "FOLD": "FOLDED",
    "FOLDED": "FOLDED",
    "SIT_OUT": "SIT_OUT",
    "SITOUT": "SIT_OUT",
    "AWAY": "SIT_OUT",
    "AUSENTE": "SIT_OUT",
    "DISCONNECTED": "DISCONNECTED",
    "OFFLINE": "DISCONNECTED",
    "DESCONECTADO": "DISCONNECTED",
    "ELIMINATED": "ELIMINATED",
    "BUSTED": "ELIMINATED",
    "OUT": "ELIMINATED",
    "EMPTY": "EMPTY",
    "VACIO": "EMPTY",
    "VACÍO": "EMPTY",
}


def normalizar_estado_jugador(raw_status: Optional[str]) -> str:
    txt = str(raw_status or "ACTIVE").strip().upper()
    if txt in _STATUS_MAP:
        return _STATUS_MAP[txt]
    if "SIT" in txt or "AUSENT" in txt or "AWAY" in txt:
        return "SIT_OUT"
    if "DISCONN" in txt or "OFFLINE" in txt or "DESCONECT" in txt:
        return "DISCONNECTED"
    if "ELIM" in txt or "BUST" in txt:
        return "ELIMINATED"
    if "FOLD" in txt:
        return "FOLDED"
    if "EMPTY" in txt or "VACI" in txt or "VACÍ" in txt:
        return "EMPTY"
    return "ACTIVE"


def _parse_num(val: Any) -> float:
    if val is None:
        return 0.0
    if isinstance(val, (int, float)):
        return float(val)
    s = str(val).strip()
    if not s:
        return 0.0
    # Si viene en formato lista o tupla separada por coma y espacio ("500, 40" o "15, 30"), tomar el primer elemento
    if ", " in s:
        s = s.split(", ")[0].strip()
    # Si tiene coma como separador de miles (ej. "1,500")
    if re.search(r"\d+,\d{3}", s):
        s = s.replace(",", "")
    else:
        # Si tiene coma decimal (ej. "12,50")
        s = s.replace(",", ".")
    m = re.search(r"-?\d+(?:\.\d+)?", s)
    if not m:
        return 0.0
    try:
        return float(m.group(0))
    except Exception:
        return 0.0


# Reutiliza la función centralizada de sanitización de nicknames de opponents_profiler
_sanitizar_nickname = sanitizar_nickname


class LiveTableMonitor:
    """Gestiona en tiempo real todos los jugadores y asientos de una mesa de póker."""

    def __init__(
        self,
        profiler: OpponentProfiler,
        modalidad: str = "Cash Game Texas Hold'em",
        max_asientos: int = 6,
        hero_nickname: str = "",
        big_blind: float = 20.0,
        persist_live_file: bool = True,
    ):
        self._lock = threading.RLock()
        self.profiler = profiler
        self.modalidad = modalidad
        self.max_asientos = max(2, int(max_asientos or 3))
        self.hero_nickname = str(hero_nickname or "").strip()
        self.big_blind = max(1.0, float(big_blind or 20.0))
        self.persist_live_file = persist_live_file

        self.hero_player_id: str = ""
        self.hero_seat: Optional[int] = None
        self.hero_stack: float = 0.0

        # Mapa de jugadores en mesa indexado por clave canónica
        self.players: Dict[str, Dict[str, Any]] = {}
        # Últimos eventos detectados en mesa (entradas, salidas, cambios)
        self.table_events: List[str] = []

    def _es_hero(self, player_id: str = "", nickname: str = "", is_hero_flag: bool = False) -> bool:
        if is_hero_flag:
            return True
        pid = str(player_id or "").strip()
        nick = str(nickname or "").strip()
        if self.hero_player_id and pid and pid == self.hero_player_id:
            return True
        if self.hero_nickname and nick and nick.lower() == self.hero_nickname.lower():
            return True
        return False

    def _buscar_clave_existente(
        self,
        player_id: str = "",
        nickname: str = "",
        seat: Optional[int] = None,
    ) -> Optional[str]:
        pid = str(player_id or "").strip()
        nick = str(nickname or "").strip()

        if pid and pid in self.players:
            return pid
        if nick:
            for k, p in self.players.items():
                if (p.get("nickname") or "").strip().lower() == nick.lower():
                    return k
                if nick.lower() in {a.lower() for a in p.get("aliases", [])}:
                    return k
        if pid:
            for k, p in self.players.items():
                if pid.lower() in {a.lower() for a in p.get("aliases", [])}:
                    return k
        if seat is not None and not pid and not nick:
            for k, p in self.players.items():
                if p.get("seat") == seat:
                    return k
        return None

    def _registrar_evento_mesa(self, msg: str) -> None:
        ts = time.strftime("%H:%M:%S")
        self.table_events.append(f"[{ts}] {msg}")
        if len(self.table_events) > 30:
            self.table_events.pop(0)

    def configurar_modalidad(self, modalidad: str, max_asientos: Optional[int] = None) -> None:
        with self._lock:
            self.modalidad = modalidad or self.modalidad
            if max_asientos is not None and int(max_asientos) >= 2:
                self.max_asientos = int(max_asientos)

    def actualizar_jugador(
        self,
        player_id: str = "",
        nickname: str = "",
        seat: Optional[int] = None,
        status: Optional[str] = None,
        stack: Optional[float] = None,
        bet: Optional[float] = None,
        is_hero: bool = False,
        is_turn: bool = False,
        cards_shown: Optional[str] = None,
    ) -> Optional[str]:
        """Inserta o actualiza un jugador en la mesa sin duplicar ni mezclar identidades."""
        with self._lock:
            pid = str(player_id or "").strip()
            nick = _sanitizar_nickname(nickname)
            if es_nickname_invalido(pid):
                pid = nick or ""
            norm_status = normalizar_estado_jugador(status)

            if seat is not None and int(seat) > self.max_asientos:
                self.max_asientos = int(seat)

            if self._es_hero(pid, nick, is_hero):
                if pid:
                    self.hero_player_id = pid
                if nick and not self.hero_nickname:
                    self.hero_nickname = nick
                if seat is not None:
                    self.hero_seat = int(seat)
                    # Garantizar que ningún rival quede registrado en el asiento del héroe
                    for k_seat in [k for k, p in self.players.items() if p.get("seat") == int(seat)]:
                        del self.players[k_seat]
                if stack is not None:
                    self.hero_stack = _parse_num(stack)
                # Si el héroe estaba previamente en self.players por error, eliminarlo
                key_hero = self._buscar_clave_existente(pid, nick, seat)
                if key_hero and key_hero in self.players:
                    del self.players[key_hero]
                return None

            if norm_status == "EMPTY" and not pid and not nick:
                if seat is not None:
                    self.marcar_asiento_vacio(int(seat))
                return None

            if not pid and not nick and seat is None:
                return None

            existing_key = self._buscar_clave_existente(pid, nick, seat)
            now_ts = time.time()

            # Garantizar unicidad por asiento: si otro jugador distinto ocupaba este mismo seat, reemplazarlo
            if seat is not None:
                for k_old in [
                    k for k, p in self.players.items()
                    if p.get("seat") == int(seat) and k != existing_key
                ]:
                    del self.players[k_old]

            if existing_key is None:
                canonical_key = pid or nick or f"seat_{seat}"
                aliases = [x for x in (pid, nick) if x]
                self.players[canonical_key] = {
                    "player_id": pid or canonical_key,
                    "nickname": nick or pid or f"Asiento {seat}",
                    "seat": int(seat) if seat is not None else None,
                    "status": norm_status,
                    "stack": _parse_num(stack) if stack is not None else 0.0,
                    "bet": _parse_num(bet) if bet is not None else 0.0,
                    "is_turn": bool(is_turn),
                    "cards_shown": cards_shown or "",
                    "aliases": aliases,
                    "last_seen": now_ts,
                }
                self._registrar_evento_mesa(
                    f"Jugador unido: {self.players[canonical_key]['nickname']} (Asiento {seat if seat is not None else '?'})"
                )
            else:
                rec = self.players[existing_key]
                old_seat = rec.get("seat")
                # Si ahora conocemos un player_id real y la clave anterior era el nickname/asiento, migrar clave sin duplicar
                canonical_key = existing_key
                if pid and existing_key != pid:
                    del self.players[existing_key]
                    rec["player_id"] = pid
                    canonical_key = pid
                    self.players[canonical_key] = rec
                if nick:
                    rec["nickname"] = nick
                for cand in (pid, nick):
                    if cand and cand not in rec["aliases"]:
                        rec["aliases"].append(cand)
                if seat is not None and old_seat != int(seat):
                    if old_seat is not None:
                        self._registrar_evento_mesa(f"{rec['nickname']} cambió de Asiento {old_seat} -> {seat}")
                    rec["seat"] = int(seat)
                if status is not None:
                    rec["status"] = norm_status
                if stack is not None:
                    rec["stack"] = _parse_num(stack)
                if bet is not None:
                    rec["bet"] = _parse_num(bet)
                rec["is_turn"] = bool(is_turn)
                if cards_shown:
                    rec["cards_shown"] = cards_shown
                rec["last_seen"] = now_ts

            rec = self.players[canonical_key]
            self.profiler.actualizar_estado_mesa_rival(
                player_id=rec["player_id"],
                nickname=rec["nickname"],
                seat=rec["seat"],
                status=rec["status"],
            )
            return canonical_key

    def marcar_asiento_vacio(self, seat: int) -> None:
        with self._lock:
            to_remove = [k for k, p in self.players.items() if p.get("seat") == seat]
            for k in to_remove:
                nick = self.players[k].get("nickname", k)
                del self.players[k]
                self._registrar_evento_mesa(f"Jugador salió de la mesa: {nick} (Asiento {seat})")

    def remover_jugador(self, player_id: str = "", nickname: str = "", seat: Optional[int] = None) -> None:
        with self._lock:
            key = self._buscar_clave_existente(player_id, nickname, seat)
            if key and key in self.players:
                nick = self.players[key].get("nickname", key)
                s = self.players[key].get("seat")
                del self.players[key]
                self._registrar_evento_mesa(f"Jugador salió de la mesa: {nick} (Asiento {s if s is not None else '?'})")

    def iniciar_nueva_mano(self) -> None:
        """Reactiva jugadores que solo habían hecho FOLD en la mano anterior, manteniendo ausentes/desconectados."""
        with self._lock:
            for p in self.players.values():
                p["bet"] = 0.0
                p["is_turn"] = False
                p["cards_shown"] = ""
                if p.get("status") == "FOLDED":
                    p["status"] = "ACTIVE"

    def actualizar_desde_dom(self, dom_data: Optional[Dict[str, Any]]) -> None:
        """Sincroniza la lista completa de asientos/jugadores detectados en el DOM."""
        if not dom_data:
            return
        jugadores = dom_data.get("jugadores_mesa")
        if not isinstance(jugadores, list) or not jugadores:
            return

        with self._lock:
            detected_seats = set()
            max_seen_seat = len(jugadores)
            if max_seen_seat > self.max_asientos:
                self.max_asientos = max_seen_seat

            for idx, j in enumerate(jugadores, start=1):
                if not isinstance(j, dict):
                    continue
                seat = j.get("seat") or j.get("asiento") or idx
                is_hero = bool(j.get("is_hero"))
                nick = str(j.get("nickname") or j.get("nick") or "").strip()
                pid = str(j.get("player_id") or nick or "").strip()
                status = normalizar_estado_jugador(j.get("status") or j.get("estado"))
                stack = j.get("stack")
                bet = j.get("bet")
                is_turn = bool(j.get("is_turn"))

                if is_hero:
                    self.actualizar_jugador(
                        player_id=pid,
                        nickname=nick,
                        seat=seat,
                        status=status,
                        stack=stack,
                        bet=bet,
                        is_hero=True,
                        is_turn=is_turn,
                    )
                    continue

                if status == "EMPTY" or (not nick and not pid):
                    self.marcar_asiento_vacio(int(seat))
                    continue

                detected_seats.add(int(seat))
                self.actualizar_jugador(
                    player_id=pid,
                    nickname=nick,
                    seat=seat,
                    status=status,
                    stack=stack,
                    bet=bet,
                    is_hero=False,
                    is_turn=is_turn,
                )

            # Si el DOM reportó toda la tabla y un asiento ocupado previamente ya no aparece, marcar salida
            if len(jugadores) >= 2:
                for k in list(self.players.keys()):
                    s = self.players[k].get("seat")
                    if s is not None and s not in detected_seats:
                        # Si había más asientos en la captura completa y este ya desapareció
                        if int(s) <= len(jugadores):
                            self.remover_jugador(seat=s)

    def actualizar_desde_ws_player_states(self, player_states: List[Dict[str, Any]]) -> None:
        """Actualiza jugadores desde el arreglo playerStates de WebSocket iPoker."""
        if not isinstance(player_states, list):
            return
        with self._lock:
            for idx, ps in enumerate(player_states, start=1):
                if not isinstance(ps, dict):
                    continue
                seat = ps.get("playerSeat") or ps.get("seatNo") or ps.get("seat") or idx
                pid = str(ps.get("playerCode") or ps.get("playerId") or ps.get("player_id") or "").strip()
                nick = str(ps.get("nickname") or ps.get("name") or ps.get("playerName") or "").strip()
                cards = [c for c in ps.get("cards", []) if isinstance(c, str) and c != "X"] if isinstance(ps.get("cards"), list) else []
                is_hero = bool(ps.get("isHero") or ps.get("is_hero") or (self.hero_seat is not None and int(seat) == self.hero_seat))

                if not nick and not pid:
                    nick = f"Rival_Asiento_{seat}" if not is_hero else (self.hero_nickname or "Belnebu")
                    pid = f"Seat_{seat}" if not is_hero else "Hero"

                raw_state = ps.get("state") or ps.get("status") or "ACTIVO"
                if ps.get("sitOut") or ps.get("isSittingOut"):
                    raw_state = "SIT_OUT"
                elif ps.get("disconnected"):
                    raw_state = "DISCONNECTED"
                elif ps.get("folded"):
                    raw_state = "FOLDED"
                stack = ps.get("chips") if "chips" in ps else ps.get("stack")
                bet = ps.get("bet")
                is_turn = bool(ps.get("isTurn") or ps.get("hasTurn"))

                self.actualizar_jugador(
                    player_id=pid,
                    nickname=nick,
                    seat=seat,
                    status=raw_state,
                    stack=stack,
                    bet=bet,
                    is_hero=is_hero,
                    is_turn=is_turn,
                    cards_shown=" ".join(cards) if cards else None,
                )

    def obtener_rival_principal(self) -> tuple[str, str]:
        """Devuelve (player_id, nickname) del rival activo principal (excluyendo a Hero)."""
        with self._lock:
            oponentes = [
                p for p in self.players.values()
                if not p.get("is_hero") and p.get("status") not in ("EMPTY", "ELIMINATED", "SIT_OUT", "DISCONNECTED")
            ]
            if not oponentes:
                oponentes = [p for p in self.players.values() if not p.get("is_hero")]
            if not oponentes:
                return "", ""
            oponentes.sort(key=lambda p: (bool(p.get("is_turn")), _parse_num(p.get("bet", 0.0)), _parse_num(p.get("stack", 0.0))), reverse=True)
            top = oponentes[0]
            return str(top.get("player_id") or ""), str(top.get("nickname") or "")

    def obtener_conteos_mesa(self) -> Dict[str, int]:
        with self._lock:
            oponentes_sentados = [p for p in self.players.values() if p.get("status") != "EMPTY"]
            hero_presente = 1 if (self.hero_seat is not None or self.hero_player_id or self.hero_nickname) else 0
            asientos_ocupados = min(self.max_asientos, len(oponentes_sentados) + hero_presente)
            oponentes_activos = [
                p for p in oponentes_sentados
                if p.get("status") not in ("SIT_OUT", "DISCONNECTED", "ELIMINATED")
            ]
            oponentes_en_mano = [
                p for p in oponentes_activos
                if p.get("status") != "FOLDED"
            ]
            return {
                "max_asientos": self.max_asientos,
                "asientos_ocupados": asientos_ocupados,
                "asientos_disponibles": max(0, self.max_asientos - asientos_ocupados),
                "jugadores_activos": len(oponentes_activos) + hero_presente,
                "jugadores_en_mano": len(oponentes_en_mano) + hero_presente,
                "numero_oponentes_mesa": len(oponentes_sentados),
                "numero_oponentes_activos": len(oponentes_activos),
                "numero_oponentes_en_mano": len(oponentes_en_mano),
            }

    def obtener_perfiles_oponentes(
        self,
        calle: str = "PREFLOP",
        bote: float = 0.0,
        posicion_hero: str = "DESCONOCIDA",
        historial_mano: str = "",
    ) -> List[Dict[str, Any]]:
        with self._lock:
            fichas: List[Dict[str, Any]] = []
            ordenadas = sorted(
                self.players.values(),
                key=lambda p: (p.get("seat") is None, p.get("seat") or 999, p.get("nickname") or ""),
            )
            for p in ordenadas:
                if p.get("status") == "EMPTY":
                    continue
                perfil = self.profiler.obtener_perfil_explotativo(
                    player_id=p.get("player_id", ""),
                    nickname=p.get("nickname", ""),
                    seat=p.get("seat"),
                    status=p.get("status"),
                )
                opp_stack = _parse_num(p.get("stack") or 0.0)
                eff_stack = min(self.hero_stack, opp_stack) if (self.hero_stack > 0 and opp_stack > 0) else (opp_stack or self.hero_stack)
                eff_bb = round(eff_stack / self.big_blind, 1) if eff_stack > 0 else None

                perfil["stack"] = opp_stack
                perfil["bet"] = _parse_num(p.get("bet") or 0.0)
                perfil["is_turn"] = bool(p.get("is_turn"))
                perfil["en_mano"] = p.get("status") == "ACTIVE"
                perfil["recomendacion_tactica"] = generar_analisis_explotativo_contextual(
                    perfil,
                    posicion=posicion_hero,
                    stack_efectivo_bb=eff_bb,
                    calle=calle,
                    bote=bote,
                    acciones_previas=historial_mano,
                )
                fichas.append(perfil)
            return fichas

    def obtener_snapshot_live(
        self,
        hand_number: int = 1,
        current_street: str = "PREFLOP",
        my_cards: Optional[List[str]] = None,
        community_cards: Optional[List[str]] = None,
        last_pot: float = 0.0,
        position_name: str = "DESCONOCIDA",
        live_events: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        with self._lock:
            conteos = self.obtener_conteos_mesa()
            opponents = self.obtener_perfiles_oponentes(
                calle=current_street,
                bote=float(last_pot or 0.0),
                posicion_hero=position_name,
                historial_mano=" | ".join((live_events or [])[-4:]),
            )
            opp_id, opp_nick = self.obtener_rival_principal()
            opp_profile = opponents[0] if opponents else self.profiler.obtener_perfil_explotativo("", "")

            snapshot = {
                "timestamp": time.time(),
                "hand_number": hand_number,
                "current_street": current_street,
                "my_cards": list(my_cards or []),
                "community_cards": list(community_cards or []),
                "last_pot": float(last_pot or 0.0),
                "position_name": position_name,
                "modalidad": self.modalidad,
                "table_info": conteos,
                "hero": {
                    "player_id": self.hero_player_id,
                    "nickname": self.hero_nickname or "Hero",
                    "seat": self.hero_seat,
                    "stack": self.hero_stack,
                },
                "opp_id": opp_id,
                "opp_nick": opp_nick,
                "opp_profile": opp_profile,
                "opponents": opponents,
                "live_events": list(live_events or self.table_events),
            }

            if self.persist_live_file:
                self._guardar_snapshot_archivo(snapshot)
            return snapshot

    def _guardar_snapshot_archivo(self, snapshot: Dict[str, Any]) -> None:
        try:
            LIVE_STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
            tmp_path = LIVE_STATE_PATH.with_suffix(".tmp")
            tmp_path.write_text(json.dumps(snapshot, ensure_ascii=False), encoding="utf-8")
            os.replace(tmp_path, LIVE_STATE_PATH)
        except Exception:
            pass


def cargar_snapshot_live_archivo() -> Dict[str, Any]:
    try:
        if LIVE_STATE_PATH.exists():
            return json.loads(LIVE_STATE_PATH.read_text(encoding="utf-8"))
    except Exception:
        pass
    return {}
