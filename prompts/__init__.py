"""Gestión y versionado determinista de prompts."""
import hashlib
from pathlib import Path
from typing import Tuple

PROMPTS_DIR = Path(__file__).resolve().parent


def load_prompt_template(version: str = "v1") -> Tuple[str, str, str]:
    """Carga la plantilla de prompt `version` y retorna (version, sha256_12, texto)."""
    safe_ver = (version or "v1").strip()
    path = PROMPTS_DIR / f"{safe_ver}.txt"
    if not path.exists():
        raise FileNotFoundError(f"Prompt version '{safe_ver}' no encontrado en {path}")
    text = path.read_text(encoding="utf-8")
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]
    return safe_ver, digest, text
