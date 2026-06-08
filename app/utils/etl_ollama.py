"""
utils/etl_ollama.py — Helpers compartidos para AI ETL vía Ollama.

Usado por:
  - routes/entrenador/diet_routes.py     (planes alimenticios)
  - routes/entrenador/trainer_routes.py  (rutinas y ejercicios)

Funciones públicas:
  extract_text(contenido, ext)              → str
  parse_routines_from_text(text)            → dict | None   (parser rápido sin LLM)
  check_ollama_ready()                      → (bool, str)
  call_ollama(system_prompt, document_text) → str           (JSON crudo vía Ollama)
  get_ollama_status()                       → dict
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


# ─── Parser determinístico para rutinas estructuradas ────────────────────────

def parse_routines_from_text(text: str) -> dict | None:
    """
    Parser rápido (sin LLM) para PDFs de planes de entrenamiento con formato tabular.

    Reconoce:
      - Encabezado de sesión:  "Sesión N: Título ⏱ X min"
      - Fila de ejercicio:     "Nombre del ejercicio  3  12"
      - Líneas partidas:       nombre en línea 1, 'sets reps' en línea 2 (tablas con wrap)

    Retorna {rutinas, ejercicios} si encuentra ≥1 sesión con ejercicios,
    o None para que el caller haga fallback a Ollama.
    """
    import re  # noqa: PLC0415

    _DAYS = ["Lunes", "Martes", "Miércoles", "Jueves", "Viernes", "Sábado", "Domingo"]

    def _muscle(title: str) -> str:
        t = title.lower()
        for keywords, muscle in [
            (["pierna", "glút", "femoral", "cuádric", "gemelo", "prensa", "rodilla", "talón"], "Piernas"),
            (["pecho", "empuje", "banca", "press"],                                             "Pecho"),
            (["espalda", "tracción", "remo", "jalón", "dorsal"],                               "Espalda"),
            (["hombro", "deltoid", "lateral"],                                                  "Hombros"),
            (["bícep"],                                                                          "Bíceps"),
            (["trícep"],                                                                         "Tríceps"),
            (["abdomen", "abdomin", "crunch", "core"],                                          "Abdomen"),
        ]:
            if any(k in t for k in keywords):
                return muscle
        return "Full Body"

    # "Sesión 1: Pierna & Abdomen (Enfoque Cuádriceps/Glúteo) ⏱ 72 min"
    # The \S{0,3} covers ⏱ (1 char) + optional whitespace before the number
    HDR = re.compile(
        r'^Sesi[oó]n\s+(\d+):\s+(.+?)(?:\s+\S{0,3}\s+(\d+)\s*min)?\s*$',
        re.IGNORECASE,
    )
    # Skip table-header rows — handles "EJERCICIO", "EJERCICIO SERIES REPETICIONES", etc.
    SKIP = re.compile(
        r'^\s*(EJERCICIO(\s+SERIES(\s+REPETICIONES)?)?|SERIES(\s+REPETICIONES)?|REPETICIONES)\s*$',
        re.IGNORECASE,
    )
    # "Exercise Name   3   12"  — one or more spaces between name and the two integers
    EX = re.compile(r'^(.+?)\s+(\d{1,2})\s+(\d{1,3})\s*$')

    days:              list[dict]         = []
    ejercicios_uniq:   dict[str, dict]    = {}
    cur_day:           str | None         = None
    cur_muscle                            = "Full Body"
    plan_duration                         = 60
    cur_exs:           list[dict]         = []
    partial                               = ""  # first line of a two-line wrapped cell

    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            partial = ""
            continue

        # ── Session header ──────────────────────────────────────────────────
        hdr = HDR.match(line)
        if hdr:
            if cur_day is not None and cur_exs:
                days.append({"day": cur_day, "muscleGroup": cur_muscle, "exercises": cur_exs})
            idx        = int(hdr.group(1)) - 1
            cur_day    = _DAYS[idx] if idx < len(_DAYS) else f"Día {idx + 1}"
            cur_muscle = _muscle(hdr.group(2))
            if hdr.group(3):
                plan_duration = int(hdr.group(3))
            cur_exs = []
            partial = ""
            continue

        # Ignore table headers and any lines before the first session
        if SKIP.match(line) or cur_day is None:
            partial = ""
            continue

        # ── Exercise row ─────────────────────────────────────────────────────
        # Attempt match with partial prefix first (handles two-line cell wrap)
        candidate = (partial + " " + line).strip() if partial else line
        ex = EX.match(candidate)
        if ex:
            name = ex.group(1).strip()
            sets = ex.group(2)
            reps = ex.group(3)
            cur_exs.append({"name": name, "sets": sets, "reps": reps, "peso": "", "notes": ""})
            if name not in ejercicios_uniq:
                ejercicios_uniq[name] = {
                    "nombre":         name,
                    "grupo_muscular": cur_muscle,
                    "tipo":           "Fuerza",
                    "series":         int(sets),
                    "repeticiones":   reps,
                    "descripcion":    "",
                }
            partial = ""
        else:
            # Could be first half of a wrapped name; stash only if it doesn't already
            # look like a complete exercise (avoids building bogus partials forever)
            partial = line if not EX.match(line) else ""

    # Flush last session
    if cur_day is not None and cur_exs:
        days.append({"day": cur_day, "muscleGroup": cur_muscle, "exercises": cur_exs})

    if not days:
        return None

    return {
        "rutinas": [{
            "name":             "Plan de Entrenamiento Importado",
            "category":         "General",
            "difficulty":       "Intermedio",
            "duration_minutes": plan_duration,
            "description":      "Plan importado desde archivo PDF/Excel",
            "days":             days,
        }],
        "ejercicios": list(ejercicios_uniq.values()),
    }


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

def call_ollama(
    system_prompt: str,
    document_text: str,
    max_tokens: int = 4096,
    timeout: int = 270,  # segundos — bajo el límite de gunicorn (300s)
) -> str:
    """
    Envía el documento al LLM local vía Ollama y devuelve el JSON crudo.
    `format='json'` fuerza salida JSON válido — característica nativa de Ollama.

    `timeout` debe ser menor que el gunicorn --timeout (300s) para que el worker
    no sea SIGKILLed antes de que requests pueda devolver el TimeoutError controlado.
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
        timeout=timeout,
    )
    resp.raise_for_status()
    return resp.json().get("response", "") or ""
