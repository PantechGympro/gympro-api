"""
utils/etl_ollama.py — Helpers compartidos para AI ETL vía Ollama.

Usado por:
  - routes/entrenador/diet_routes.py     (planes alimenticios)
  - routes/entrenador/trainer_routes.py  (rutinas y ejercicios)

Funciones públicas:
  extract_text(contenido, ext)   → str
  check_ollama_ready()           → (bool, str)
  call_ollama(system_prompt, document_text) → str   (JSON crudo)
  get_ollama_status()            → dict
"""
from __future__ import annotations

import io
import os

import requests as _requests

OLLAMA_BASE  = os.getenv("OLLAMA_BASE_URL", "http://ollama:11434")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL",    "phi3:mini")


# ─── Extracción de texto ──────────────────────────────────────────────────────

def extract_text(contenido: bytes, ext: str) -> str:
    """Extrae texto plano de un PDF o Excel."""
    if ext == "pdf":
        import pdfplumber  # noqa: PLC0415
        with pdfplumber.open(io.BytesIO(contenido)) as pdf:
            return "\n\n".join(p.extract_text() or "" for p in pdf.pages)

    if ext in {"xlsx", "xls"}:
        import openpyxl  # noqa: PLC0415
        wb = openpyxl.load_workbook(io.BytesIO(contenido), data_only=True)
        lines: list[str] = []
        for ws in wb.worksheets:
            lines.append(f"[Hoja: {ws.title}]")
            for row in ws.iter_rows(values_only=True):
                celdas = [str(c) if c is not None else "" for c in row]
                if any(c.strip() for c in celdas):
                    lines.append(" | ".join(celdas))
        return "\n".join(lines)

    raise ValueError(f"Formato no soportado: .{ext}")


# ─── Verificación de disponibilidad ──────────────────────────────────────────

def check_ollama_ready() -> tuple[bool, str]:
    """Verifica que Ollama esté activo y el modelo configurado esté disponible."""
    try:
        r = _requests.get(f"{OLLAMA_BASE}/api/tags", timeout=5)
        r.raise_for_status()
        modelos = [m["name"] for m in r.json().get("models", [])]
        modelo_ok = any(
            OLLAMA_MODEL in m or m.startswith(OLLAMA_MODEL.split(":")[0])
            for m in modelos
        )
        if not modelo_ok:
            return False, (
                f"El modelo '{OLLAMA_MODEL}' no está descargado. "
                f"Ejecuta: docker compose exec ollama ollama pull {OLLAMA_MODEL}"
            )
        return True, "ok"
    except _requests.exceptions.ConnectionError:
        return False, (
            "Ollama no está disponible. "
            "Asegúrate de que el servicio esté corriendo: docker compose up -d ollama"
        )
    except Exception as e:
        return False, f"Error verificando Ollama: {e}"


def get_ollama_status() -> dict:
    """Devuelve el estado de Ollama como dict JSON-serializable."""
    try:
        r = _requests.get(f"{OLLAMA_BASE}/api/tags", timeout=5)
        r.raise_for_status()
        modelos = [m["name"] for m in r.json().get("models", [])]
        modelo_ok = any(
            OLLAMA_MODEL in m or m.startswith(OLLAMA_MODEL.split(":")[0])
            for m in modelos
        )
        return {
            "disponible":    True,
            "modelo_activo": modelo_ok,
            "modelo":        OLLAMA_MODEL,
            "modelos":       modelos,
        }
    except Exception:
        return {
            "disponible":    False,
            "modelo_activo": False,
            "modelo":        OLLAMA_MODEL,
            "modelos":       [],
        }


# ─── Llamada al LLM ──────────────────────────────────────────────────────────

def call_ollama(system_prompt: str, document_text: str, max_tokens: int = 4096) -> str:
    """
    Envía el documento al LLM local vía Ollama y devuelve el JSON crudo.
    `format='json'` fuerza salida JSON válido — característica nativa de Ollama.
    """
    payload = {
        "model":  OLLAMA_MODEL,
        "prompt": f"{system_prompt}\n\nDOCUMENTO A PROCESAR:\n{document_text}",
        "stream": False,
        "format": "json",
        "options": {
            "temperature": 0.1,
            "num_predict": max_tokens,
            "top_p":       0.9,
        },
    }
    resp = _requests.post(
        f"{OLLAMA_BASE}/api/generate",
        json=payload,
        timeout=180,  # Modelos locales en CPU pueden tardar 60-120s
    )
    resp.raise_for_status()
    return resp.json().get("response", "")
