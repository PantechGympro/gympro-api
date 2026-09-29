"""
utils/etl_ollama.py — Helpers compartidos para AI ETL vía Ollama.

Usado por:
  - routes/entrenador/diet_routes.py     (planes alimenticios)
  - routes/entrenador/trainer_routes.py  (rutinas y ejercicios)

Funciones públicas:
  extract_text(contenido, ext)                    → str
  parse_routines_from_text(text)                  → dict | None  (parser rápido, texto plano)
  parse_routines_from_app_export(contenido)       → dict | None  (parser rápido, PDF de 2 columnas)
  check_ollama_ready()                            → (bool, str)
  call_ollama(system_prompt, document_text)       → str          (JSON crudo vía Ollama)
  get_ollama_status()                             → dict
"""
from __future__ import annotations

import io
import os

import requests as _requests

OLLAMA_BASE  = os.getenv("OLLAMA_BASE_URL", "http://ollama:11434")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL",    "phi3:mini")

# ── Tuning del ETL de IA ───────────────────────────────────────────────────────
# Antes: chunks de 3500 caracteres y timeout de 270s atado al limite de 300s de
# gunicorn (el ETL corria sincrono dentro del request). Ahora el ETL corre en un
# hilo de background (ver utils/ia_jobs.py) y ya no compite con el timeout HTTP,
# pero se mantienen limites sanos por bloque para no dejar un chunk colgado
# para siempre y para que cada llamada a Ollama sea mas rapida:
#   - Chunks mas chicos (2000 vs 3500) = menos tokens de entrada por llamada =
#     inferencia mas rapida y menos ruido para el modelo.
#   - Timeout por chunk configurable via env (OLLAMA_TIMEOUT), default 240s.
LLM_CHUNK_MAX_CHARS = int(os.getenv("OLLAMA_CHUNK_MAX_CHARS", "2000"))
LLM_CALL_TIMEOUT     = int(os.getenv("OLLAMA_TIMEOUT", "240"))


# ── Límite de llamadas concurrentes a Ollama ────────────────────────────────
# Sin esto, cada import/dieta que llega a call_ollama() dispara una inferencia
# CPU-bound independiente. Antes del ETL en background (utils/ia_jobs.py) el
# request síncrono ya limitaba esto de facto; ahora que importar rutinas ya
# no bloquea al entrenador, nada impide que varios imports (de uno o varios
# entrenadores) arranquen inferencias simultáneas -- en la máquina de
# desarrollo esto llegó a saturar CPU/RAM del host al punto de reiniciarse
# justo después de un `docker compose up -d --build` (build + inferencia sin
# tiempo de recuperación entre medio). Se limita con un lock distribuido en
# Redis (ya es dependencia de Flask-Limiter, ver config.py) en vez de un
# semáforo en memoria: gunicorn corre con WEB_CONCURRENCY workers -- procesos
# separados -- así que un semáforo in-process solo limitaría la concurrencia
# POR WORKER, no la real contra el servicio `ollama` compartido por todos.
OLLAMA_MAX_CONCURRENT = max(1, int(os.getenv("OLLAMA_MAX_CONCURRENT", "1")))
_OLLAMA_LOCK_TTL      = 300  # seguro ante crash del holder: nunca deja un cupo tomado más que esto
OLLAMA_WAIT_TIMEOUT   = int(os.getenv("OLLAMA_WAIT_TIMEOUT", "600"))  # cuanto esperar un cupo libre

_redis_client = None


def _get_redis():
    global _redis_client
    if _redis_client is None:
        import redis as _redis_lib  # noqa: PLC0415 (ya es dependencia de Flask-Limiter)
        _redis_client = _redis_lib.from_url(os.getenv("REDIS_URL", "redis://redis:6379/0"))
    return _redis_client


def _acquire_ollama_slot():
    """
    Bloquea hasta obtener uno de los OLLAMA_MAX_CONCURRENT cupos globales
    para llamar a Ollama (across todos los workers/hilos), o levanta
    TimeoutError si ninguno se libera en OLLAMA_WAIT_TIMEOUT segundos.
    Devuelve el Lock ya adquirido -- liberarlo con _release_ollama_slot().
    """
    import time  # noqa: PLC0415

    r = _get_redis()
    deadline = time.monotonic() + OLLAMA_WAIT_TIMEOUT
    while True:
        for i in range(OLLAMA_MAX_CONCURRENT):
            lock = r.lock(f"ollama:slot:{i}", timeout=_OLLAMA_LOCK_TTL)
            if lock.acquire(blocking=False):
                return lock
        if time.monotonic() >= deadline:
            raise TimeoutError(
                f"El servidor de IA está saturado: no se liberó ningún cupo "
                f"(máx {OLLAMA_MAX_CONCURRENT} llamada(s) concurrente(s) a Ollama) "
                f"en {OLLAMA_WAIT_TIMEOUT}s."
            )
        time.sleep(1)


def _release_ollama_slot(lock) -> None:
    try:
        lock.release()
    except Exception:
        pass  # ya expiró solo por _OLLAMA_LOCK_TTL, no pasa nada


# ─── Extracción de texto ──────────────────────────────────────────────────────

def _table_to_compact_rows(tabla: list) -> str:
    """Convierte una tabla de pdfplumber en filas "col | col | col" con
    espacios internos colapsados. Mucho mas compacto en tokens que el texto
    alineado con espacios que produce extract_text() sobre una tabla."""
    filas = []
    for row in tabla:
        celdas = [" ".join(str(c).split()) if c else "" for c in row]
        if any(celdas):
            filas.append(" | ".join(celdas))
    return "\n".join(filas)


def extract_text(contenido: bytes, ext: str, *, structured: bool = False) -> str:
    """
    Extrae texto plano de un PDF o Excel.

    structured=False (default): comportamiento original -- pdfplumber
    extract_text() tal cual. Lo usa el parser deterministico basado en regex
    (parse_routines_from_text), que espera columnas separadas por espacios,
    no debe tocarse o se rompe ese parser rapido.

    structured=True: SOLO para el fallback LLM (cuando el parser
    deterministico ya no reconocio el documento). Aqui conviene compactar
    las tablas detectadas a "celda | celda" y no repetir encabezados de
    columna que pdfplumber ve en cada pagina -- menos tokens de entrada para
    Ollama, inferencia mas rapida y menos ruido para el modelo.
    """
    if ext == "pdf":
        import pdfplumber  # noqa: PLC0415
        with pdfplumber.open(io.BytesIO(contenido)) as pdf:
            if not structured:
                return "\n\n".join(p.extract_text() or "" for p in pdf.pages)

            partes: list[str] = []
            encabezados_vistos: set[tuple] = set()
            for page in pdf.pages:
                tablas = page.extract_tables()
                if tablas:
                    for tabla in tablas:
                        if not tabla:
                            continue
                        header = tuple(tabla[0]) if tabla[0] else None
                        filas = tabla[1:] if header in encabezados_vistos else tabla
                        if header:
                            encabezados_vistos.add(header)
                        compacto = _table_to_compact_rows(filas)
                        if compacto:
                            partes.append(compacto)
                else:
                    txt = page.extract_text() or ""
                    if txt.strip():
                        partes.append(txt)
            return "\n\n".join(partes)

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

# Nombres de día y clasificador de grupo muscular compartidos por AMBOS
# parsers deterministicos (parse_routines_from_text y, más abajo,
# parse_routines_from_app_export) -- antes vivían anidados dentro del
# primero y se hubieran tenido que duplicar para el segundo.
DIAS_SEMANA = ["Lunes", "Martes", "Miércoles", "Jueves", "Viernes", "Sábado", "Domingo"]


def _clasificar_grupo_muscular(titulo: str) -> str:
    t = titulo.lower()
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


def parse_routines_from_text(text: str) -> dict | None:
    """
    Parser rápido (sin LLM) para PDFs de planes de entrenamiento con formato tabular.

    Reconoce:
      - Encabezado de sesión:  "Sesión N: Título ⏱ X min"
      - Fila de ejercicio:     "Nombre del ejercicio  3  12"
      - Líneas partidas:       nombre en línea 1, 'sets reps' en línea 2 (tablas con wrap)

    Retorna {rutinas, ejercicios} si encuentra ≥1 sesión con ejercicios,
    o None para que el caller haga fallback a Ollama (o pruebe
    parse_routines_from_app_export, ver más abajo).
    """
    import re  # noqa: PLC0415

    _DAYS = DIAS_SEMANA
    _muscle = _clasificar_grupo_muscular

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


def parse_routines_from_app_export(contenido: bytes) -> dict | None:
    """
    Parser rápido (sin LLM) para PDFs exportados por apps de seguimiento de
    entrenamiento (ej. Hevy, Strong y similares) con el formato:

        Rutina de Entrenamiento Semanal
        Registro Detallado de Ejercicios, Series, Pesos y Repeticiones
        Lunes — Pecho y Espalda 1h 5min | 16 Series | 5,090 kg
        Press De Banca Plano (Barra Recta)   Remo En Barra T (Máquina)
        • S1 40 kg × 10 reps                 • S1 20 kg × 12 reps
        • S2 60 kg × 8 reps                   • S2 30 kg × 10 reps
        ...

    El layout es de DOS COLUMNAS (dos ejercicios en paralelo por fila), así
    que a diferencia de parse_routines_from_text() -- que opera sobre texto
    plano -- este parser necesita las posiciones (x0/top) de cada palabra
    vía pdfplumber.extract_words(): el texto plano de pdfplumber intercala
    ambas columnas en la misma línea ("• S1 40 kg × 10 reps • S1 20 kg × 12
    reps" son en realidad DOS series de DOS ejercicios distintos) y no hay
    forma de separarlas de forma confiable sin la posición horizontal.

    Antes de este parser, cualquier PDF con este formato caía siempre al
    fallback de Ollama (el parser de texto plano nunca lo reconocía), lo que
    lo volvía innecesariamente lento para un documento perfectamente
    estructurado -- exactamente el caso reportado por el usuario.

    Retorna {rutinas, ejercicios} si reconoce ≥1 día con ejercicios, o None
    para que el caller siga con parse_routines_from_text() / Ollama.
    """
    import re  # noqa: PLC0415

    import pdfplumber  # noqa: PLC0415

    DAY_HDR = re.compile(
        r'^(' + "|".join(DIAS_SEMANA) + r')\s*—\s*(.+?)\s+((?:\d+h\s*)?\d+\s*min)\s*'
        r'\|\s*\d+\s*Series\s*\|\s*[\d.,]+\s*kg\s*$',
        re.IGNORECASE,
    )
    SET_RE = re.compile(r'S\d+\s+([\d.,]+)\s*kg\s*×\s*(\d+)\s*reps', re.IGNORECASE)
    DUR_RE = re.compile(r'(?:(\d+)h\s*)?(\d+)\s*min')

    def _duracion_a_min(s: str) -> int:
        m = DUR_RE.match(s.strip())
        if not m:
            return 0
        horas = int(m.group(1)) if m.group(1) else 0
        minutos = int(m.group(2))
        return horas * 60 + minutos

    def _agrupar_filas(words: list, tolerancia: float = 4.0) -> list[list]:
        """Agrupa palabras en 'filas' visuales por posición vertical (top),
        con tolerancia -- pdfplumber a veces reporta 1-3px de diferencia
        entre palabras que están, a simple vista, en la misma línea."""
        words = sorted(words, key=lambda w: w["top"])
        filas: list[list] = []
        actual: list = []
        top_actual = None
        for w in words:
            if top_actual is None or abs(w["top"] - top_actual) <= tolerancia:
                actual.append(w)
                if top_actual is None:
                    top_actual = w["top"]
            else:
                filas.append(actual)
                actual = [w]
                top_actual = w["top"]
        if actual:
            filas.append(actual)
        return [sorted(f, key=lambda w: w["x0"]) for f in filas]

    try:
        with pdfplumber.open(io.BytesIO(contenido)) as pdf:
            # Chequeo barato antes de pagar el costo de extract_words() en
            # cada página: si ni el texto plano de la primera página trae el
            # encabezado esperado, esto no es este formato.
            primera = pdf.pages[0].extract_text() or ""
            if "Registro Detallado de Ejercicios" not in primera:
                return None

            filas_todas: list[list] = []
            for page in pdf.pages:
                palabras = page.extract_words(use_text_flow=False, keep_blank_chars=False)
                filas_todas.extend(_agrupar_filas(palabras))

            # Título real del documento (si lo hay) en vez del genérico.
            rutina_name = "Plan de Entrenamiento Importado"
            m_titulo = re.search(r'^Rutina de Entrenamiento.*$', primera, re.MULTILINE)
            if m_titulo:
                rutina_name = m_titulo.group(0).strip()

            # Punto de corte entre columna izquierda/derecha: la brecha
            # horizontal más grande entre palabras consecutivas en filas de
            # cuerpo (no título ni encabezado de día) -- así se adapta al
            # ancho real de este PDF en vez de asumir un valor fijo.
            brechas: list[tuple[float, float]] = []
            for fila in filas_todas:
                texto_fila = " ".join(w["text"] for w in fila)
                if DAY_HDR.match(texto_fila) or "Rutina de Entrenamiento" in texto_fila \
                        or "Registro Detallado" in texto_fila:
                    continue
                xs = sorted(w["x0"] for w in fila)
                for i in range(1, len(xs)):
                    brecha = xs[i] - xs[i - 1]
                    if brecha > 50:
                        brechas.append((brecha, (xs[i - 1] + xs[i]) / 2))
            brechas.sort(reverse=True)
            split_x = brechas[0][1] if brechas else None

            dias: list[dict] = []
            ejercicios_uniq: dict[str, dict] = {}
            dia_actual: dict | None = None
            grupo_actual = "Full Body"
            ex_izq: str | None = None
            ex_der: str | None = None
            sets_izq: list[tuple] = []
            sets_der: list[tuple] = []
            duracion_total = 0

            def _cerrar_ejercicio(nombre, sets_lista):
                if not nombre or not sets_lista or dia_actual is None:
                    return
                pesos = [s[0] for s in sets_lista]
                reps_lista = [s[1] for s in sets_lista]
                peso_prom = round(sum(pesos) / len(pesos))
                reps_frecuente = max(set(reps_lista), key=reps_lista.count)
                notas = " · ".join(f"{p:g}kg×{r}" for p, r in sets_lista)
                dia_actual["exercises"].append({
                    "name": nombre, "sets": str(len(sets_lista)),
                    "reps": str(reps_frecuente), "peso": str(peso_prom), "notes": notas,
                })
                if nombre not in ejercicios_uniq:
                    ejercicios_uniq[nombre] = {
                        "nombre": nombre, "grupo_muscular": grupo_actual, "tipo": "Fuerza",
                        "series": len(sets_lista), "repeticiones": str(reps_frecuente),
                        "descripcion": "",
                    }

            for fila in filas_todas:
                texto_fila = " ".join(w["text"] for w in fila)
                if "Rutina de Entrenamiento" in texto_fila or "Registro Detallado" in texto_fila:
                    continue

                hdr = DAY_HDR.match(texto_fila)
                if hdr:
                    if dia_actual is not None:
                        _cerrar_ejercicio(ex_izq, sets_izq)
                        _cerrar_ejercicio(ex_der, sets_der)
                        dias.append(dia_actual)
                    dia_nombre = hdr.group(1)
                    titulo = hdr.group(2).strip()
                    dur_m = re.search(r'((?:\d+h\s*)?\d+\s*min)', texto_fila)
                    duracion_total += _duracion_a_min(dur_m.group(1)) if dur_m else 0
                    grupo_actual = _clasificar_grupo_muscular(titulo)
                    dia_actual = {"day": dia_nombre, "muscleGroup": titulo, "exercises": []}
                    ex_izq = ex_der = None
                    sets_izq, sets_der = [], []
                    continue

                if dia_actual is None:
                    continue

                if split_x is not None:
                    izq_palabras = [w for w in fila if w["x0"] < split_x]
                    der_palabras = [w for w in fila if w["x0"] >= split_x]
                else:
                    izq_palabras, der_palabras = fila, []
                texto_izq = " ".join(w["text"] for w in izq_palabras).strip()
                texto_der = " ".join(w["text"] for w in der_palabras).strip()

                if "•" in texto_fila:
                    m_izq = SET_RE.search(texto_izq)
                    if m_izq:
                        sets_izq.append((float(m_izq.group(1).replace(",", "")), int(m_izq.group(2))))
                    m_der = SET_RE.search(texto_der)
                    if m_der:
                        sets_der.append((float(m_der.group(1).replace(",", "")), int(m_der.group(2))))
                else:
                    if texto_izq:
                        _cerrar_ejercicio(ex_izq, sets_izq)
                        ex_izq, sets_izq = texto_izq, []
                    if texto_der:
                        _cerrar_ejercicio(ex_der, sets_der)
                        ex_der, sets_der = texto_der, []

            if dia_actual is not None:
                _cerrar_ejercicio(ex_izq, sets_izq)
                _cerrar_ejercicio(ex_der, sets_der)
                dias.append(dia_actual)

            if not dias:
                return None

            return {
                "rutinas": [{
                    "name":             rutina_name,
                    "category":         "General",
                    "difficulty":       "Intermedio",
                    "duration_minutes": duracion_total or 60,
                    "description":      "Plan importado desde archivo PDF (formato de app de seguimiento)",
                    "days":             dias,
                }],
                "ejercicios": list(ejercicios_uniq.values()),
            }
    except Exception:
        # Cualquier PDF que no calce con este formato exacto (o que
        # pdfplumber no pueda leer de esta forma) sigue con el siguiente
        # intento -- nunca debe tronar el job por esto.
        return None


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
    timeout: int | None = None,
) -> str:
    """
    Envía el documento al LLM local vía Ollama y devuelve el JSON crudo.
    `format='json'` fuerza salida JSON válido — característica nativa de Ollama.

    El ETL corre en un hilo de background (utils/ia_jobs.py), no dentro del
    request sincrono, así que este timeout ya no está atado al de gunicorn —
    solo evita que un chunk colgado bloquee el job para siempre. Default:
    LLM_CALL_TIMEOUT (env OLLAMA_TIMEOUT, 240s).
    """
    if timeout is None:
        timeout = LLM_CALL_TIMEOUT
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
    lock = _acquire_ollama_slot()
    try:
        resp = _requests.post(
            f"{OLLAMA_BASE}/api/generate",
            json=payload,
            timeout=timeout,
        )
        resp.raise_for_status()
        return resp.json().get("response", "") or ""
    finally:
        _release_ollama_slot(lock)


# ─── Troceado y parseo robusto para documentos largos ────────────────────────

def chunk_text(text: str, max_chars: int | None = None, max_chunks: int = 8) -> tuple[list[str], bool]:
    """
    Divide el texto en bloques de ~max_chars respetando saltos de línea, para
    no perder ejercicios al enviar documentos largos al LLM (antes se cortaba
    duro en 4.000 caracteres).

    Default de max_chars: LLM_CHUNK_MAX_CHARS (env OLLAMA_CHUNK_MAX_CHARS,
    2000) — bloques chicos = llamadas a Ollama mas rapidas y mas precisas.

    Returns:
        (chunks, truncado) — `truncado` es True si el documento superó
        max_chunks bloques y quedó contenido sin procesar.
    """
    if max_chars is None:
        max_chars = LLM_CHUNK_MAX_CHARS
    lines = text.splitlines()
    chunks: list[str] = []
    cur: list[str] = []
    size = 0
    truncado = False

    for ln in lines:
        if size + len(ln) + 1 > max_chars and cur:
            chunks.append("\n".join(cur))
            cur, size = [], 0
            if len(chunks) >= max_chunks:
                truncado = True   # quedan líneas sin procesar
                break
        cur.append(ln)
        size += len(ln) + 1
    else:
        if cur:
            chunks.append("\n".join(cur))

    return chunks, truncado


def parse_llm_json(raw: str) -> dict | None:
    """
    Parsea la respuesta del LLM a dict, tolerando fences ```json … ``` y texto
    sobrante. Devuelve None si no se puede obtener un objeto JSON válido.
    """
    import json as _json  # noqa: PLC0415
    texto = (raw or "").strip()
    if texto.startswith("```"):
        lineas = texto.split("\n")
        texto = "\n".join(lineas[1:] if len(lineas) > 1 else lineas)
    if texto.endswith("```"):
        texto = texto[: texto.rfind("```")].strip()
    try:
        obj = _json.loads(texto)
    except _json.JSONDecodeError:
        # Último intento: recortar al primer '{' … último '}'
        i, j = texto.find("{"), texto.rfind("}")
        if i != -1 and j != -1 and j > i:
            try:
                obj = _json.loads(texto[i : j + 1])
            except _json.JSONDecodeError:
                return None
        else:
            return None
    return obj if isinstance(obj, dict) else None


# ─── Tabla de macros por ingrediente (por 100 g) ─────────────────────────────
# (kcal, proteina_g, carb_g, grasa_g)

_MACROS_DB: dict[str, tuple[float, float, float, float]] = {
    # Proteínas animales
    "pechuga de pollo":      (165, 31.0,  0.0,  3.6),
    "pollo deshebrado":      (165, 31.0,  0.0,  3.6),
    "pollo":                 (165, 31.0,  0.0,  3.6),
    "tilapia":               (96,  20.1,  0.0,  2.0),
    "salmon":                (208, 20.4,  0.0, 13.4),
    "atun en agua":          (116, 25.5,  0.0,  0.8),
    "atun":                  (116, 25.5,  0.0,  0.8),
    "pescado":               (96,  20.0,  0.0,  1.5),
    "clara de huevo":        (52,  11.0,  0.7,  0.2),
    "claras de huevo":       (52,  11.0,  0.7,  0.2),
    "huevo":                 (155,  13.0,  1.1, 11.0),
    "jamon bajo en grasa":   (120,  16.0,  2.0,  5.0),
    "jamon":                 (145,  16.0,  3.5,  7.0),
    "bistec":                (217,  26.0,  0.0, 12.0),
    "res":                   (217,  26.0,  0.0, 12.0),
    "queso panela":          (270,  17.0,  3.0, 21.0),
    "queso cottage":         (98,   11.0,  3.4,  4.3),
    "yogurt griego":         (59,   10.0,  3.6,  0.4),
    "yogur":                 (59,   10.0,  3.6,  0.4),
    # Carbohidratos
    "arroz cocido":          (130,   2.7, 28.0,  0.3),
    "arroz":                 (130,   2.7, 28.0,  0.3),
    "pasta cocida":          (158,   5.8, 31.0,  0.9),
    "pasta":                 (158,   5.8, 31.0,  0.9),
    "pan integral":          (247,   9.0, 41.0,  3.4),
    "pan":                   (265,   9.0, 49.0,  3.2),
    "tortilla de maiz":      (218,   5.7, 46.0,  2.5),
    "tortilla":              (218,   5.7, 46.0,  2.5),
    "tostadas horneadas":    (380,   9.0, 78.0,  2.5),
    "avena en hojuela cruda":(389,  17.0, 66.0,  7.0),
    "avena":                 (389,  17.0, 66.0,  7.0),
    "tortitas de arroz":     (392,   8.0, 82.0,  3.0),
    "elote":                 (86,    3.2, 19.0,  1.2),
    "camote":                (86,    1.6, 20.0,  0.1),
    "papa":                  (77,    2.0, 17.0,  0.1),
    # Grasas saludables
    "aguacate":              (160,   2.0,  9.0, 15.0),
    "guacamole":             (160,   2.0,  9.0, 15.0),
    "aceite de oliva":       (884,   0.0,  0.0,100.0),
    "aceite":                (884,   0.0,  0.0,100.0),
    "almendra":              (579,  21.0, 22.0, 50.0),
    "almendras":             (579,  21.0, 22.0, 50.0),
    "nuez":                  (654,  15.0, 14.0, 65.0),
    "cacahuate":             (567,  26.0, 16.0, 49.0),
    "cacahuates":            (567,  26.0, 16.0, 49.0),
    "crema de cacahuate":    (588,  25.0, 20.0, 50.0),
    "mayonesa":              (680,   1.0,  0.6, 75.0),
    # Verduras
    "espinaca":              (23,    2.9,  3.6,  0.4),
    "espinacas":             (23,    2.9,  3.6,  0.4),
    "nopal cocido":          (22,    1.5,  4.0,  0.3),
    "nopal":                 (22,    1.5,  4.0,  0.3),
    "pepino":                (15,    0.7,  3.6,  0.1),
    "tomate":                (18,    0.9,  3.9,  0.2),
    "jitomate":              (18,    0.9,  3.9,  0.2),
    "tomate cherry":         (18,    0.9,  3.9,  0.2),
    "champiñon":             (22,    3.1,  3.3,  0.3),
    "champiñones":           (22,    3.1,  3.3,  0.3),
    "calabaza":              (17,    1.2,  3.4,  0.1),
    "cebolla":               (40,    1.1,  9.3,  0.1),
    "betabel":               (43,    1.6, 10.0,  0.2),
    "jicama":                (38,    0.7,  8.8,  0.1),
    # Frutas
    "fresa":                 (32,    0.7,  7.7,  0.3),
    "manzana":               (52,    0.3, 14.0,  0.2),
    "mango":                 (60,    0.8, 15.0,  0.4),
    "piña":                  (50,    0.5, 13.0,  0.1),
    "platano":               (89,    1.1, 23.0,  0.3),
    "melon":                 (34,    0.8,  8.2,  0.2),
    "ejote":                 (31,    1.8,  7.1,  0.1),
    # Lácteos
    "leche":                 (61,    3.2,  4.8,  3.3),
    "crema":                 (195,   2.5,  3.4, 20.0),
    # Otros
    "semilla de chia":       (486,  17.0, 42.0, 31.0),
    "chia":                  (486,  17.0, 42.0, 31.0),
    "semilla de linaza":     (534,  18.0, 29.0, 42.0),
    "harina de avena":       (379,  13.0, 68.0,  7.0),
    "proteina en polvo":     (380,  75.0,  5.0,  5.0),
}

_PIECE_G: dict[str, float] = {
    "huevo":         50.0,
    "aguacate":     200.0,
    "manzana":      180.0,
    "platano":      120.0,
    "naranja":      130.0,
    "tortilla":      30.0,
    "tomate":       120.0,
    "jitomate":     120.0,
    "limón":         80.0,
    "pan integral":  35.0,   # slice of bread
    "pan":           35.0,
    "tostada":       12.0,
    "tostadas":      12.0,
    # Frutos secos — piezas pequeñas
    "almendra":       1.2,
    "almendras":      1.2,
    "nuez":           5.0,
    "cacahuate":      0.7,
    "cacahuates":     0.7,
    "semilla":        3.0,
    # Frutas pequeñas
    "fresa":          8.0,
    "uva":            5.0,
    "cereza":         8.0,
}

_CONDIMENTS = {
    "sal", "pimienta", "ajo", "comino", "oregano", "canela",
    "chile", "mostaza", "salsa", "vinagre", "cilantro",
    "limon", "limón", "jugo de limon",
}


def _parse_fraction(s: str) -> float:
    """'1 1/2' → 1.5,  '2/3' → 0.667,  '1.5' → 1.5"""
    import re as _re
    s = s.strip()
    m = _re.match(r"^(\d+)\s+(\d+)/(\d+)$", s)
    if m:
        return int(m.group(1)) + int(m.group(2)) / int(m.group(3))
    m = _re.match(r"^(\d+)/(\d+)$", s)
    if m:
        return int(m.group(1)) / int(m.group(2))
    return float(s.replace(",", "."))


def _qty_to_grams(qty: float, unit: str, ingredient: str) -> float:
    u = unit.lower()
    if u in ("gramos", "gramo", "g"):
        return qty
    if u in ("kg",):
        return qty * 1000
    if u in ("taza", "tazas"):
        return qty * 240
    if u in ("cucharada", "cucharadas"):
        return qty * 15
    if u in ("cucharadita", "cucharaditas"):
        return qty * 5
    if u in ("ml",):
        return qty
    if u in ("pieza", "piezas", "mitad", "mitades"):
        ing_low = ingredient.lower()
        for key, grams in _PIECE_G.items():
            if key in ing_low:
                return qty * grams
        return qty * 80   # default pieza
    if u in ("rebanada", "rebanadas"):
        ing_low = ingredient.lower()
        for key, grams in _PIECE_G.items():
            if key in ing_low:
                return qty * grams
        return qty * 20   # default rebanada ≈ slice
    if u in ("lata", "latas"):
        return qty * 170
    return qty


def _lookup_macros(nombre: str) -> tuple[float, float, float, float] | None:
    n = nombre.lower()
    if n in _MACROS_DB:
        return _MACROS_DB[n]
    for key in sorted(_MACROS_DB.keys(), key=len, reverse=True):
        if key in n:
            return _MACROS_DB[key]
    return None


def _parse_ingredient_line(line: str) -> dict | None:
    import re as _re
    text = line.lstrip("*").strip().rstrip(".")
    text_low = text.lower()

    if any(c in text_low for c in _CONDIMENTS):
        if not _re.match(r"^[\d\s,./]+\s+\w", text):
            return None

    m = _re.match(
        r"^([\d\s,./]+)\s+"
        r"(gramos?|g\b|tazas?|cucharadas?|cucharaditas?|piezas?|rebanadas?|"
        r"latas?|mitades?|ml|kg)\s*"
        r"(?:de\s+)?(.+)$",
        text, _re.IGNORECASE,
    )
    if m:
        qty_str = m.group(1).strip()
        unit    = m.group(2).strip()
        nombre  = m.group(3).strip()
    else:
        qty_str, unit, nombre = "al gusto", "", text

    calorias = prot = carb = fat = None
    if qty_str != "al gusto":
        try:
            qty    = _parse_fraction(qty_str)
            grams  = _qty_to_grams(qty, unit, nombre)
            macros = _lookup_macros(nombre)
            if macros:
                f        = grams / 100.0
                calorias = round(macros[0] * f)
                prot     = round(macros[1] * f, 1)
                carb     = round(macros[2] * f, 1)
                fat      = round(macros[3] * f, 1)
        except Exception:
            pass

    return {
        "nombre_alimento": nombre,
        "cantidad":        qty_str,
        "unidad":          unit,
        "calorias":        calorias,
        "proteinas_g":     prot,
        "carbohidratos_g": carb,
        "grasas_g":        fat,
    }


# ─── Parser determinístico para planes alimenticios en PDF tabular ────────────

def parse_diet_plan_from_pdf(contenido: bytes) -> dict | None:
    """
    Extrae un plan alimenticio y sus recetas de un PDF tabular (formato semanal).

    Detecta por página:
      - Tipo de comida (Desayuno / Colación / Comida / Colación 2 / Cena)
      - Nombre del platillo por día (líneas antes del primer '*')
      - Ingredientes ('* cantidad unidad ingrediente')
      - Imagen del platillo (base64 JPEG, extraída directamente del PDF)

    Returns {"plan": {...}, "recetas": [...]} o None si no reconoce el formato.
    """
    import io as _io
    import base64 as _b64
    import unicodedata as _ud
    import pdfplumber
    try:
        from PIL import Image as _PILImage
        _HAS_PIL = True
    except ImportError:
        _HAS_PIL = False

    def _nd(s: str) -> str:
        return "".join(
            c for c in _ud.normalize("NFD", s.lower())
            if _ud.category(c) != "Mn"
        )

    _DAY_KEYS = ["lunes", "martes", "miercoles", "jueves", "viernes", "sabado", "domingo"]

    _MEAL_MAP: dict[str, tuple[str, str]] = {
        "colacion 2": ("Colación 2", "17:00"),
        "colacion 1": ("Colación 1", "10:30"),
        "desayuno":   ("Desayuno",   "08:00"),
        "colacion":   ("Colación",   "10:30"),
        "comida":     ("Comida",     "14:00"),
        "merienda":   ("Merienda",   "17:00"),
        "cena":       ("Cena",       "20:00"),
    }

    def _img_b64(stream) -> str | None:
        if not _HAS_PIL or stream is None:
            return None
        try:
            data = stream.get_data()
            pil  = _PILImage.open(_io.BytesIO(data)).convert("RGB")
            buf  = _io.BytesIO()
            pil.save(buf, format="JPEG", quality=72)
            return "data:image/jpeg;base64," + _b64.b64encode(buf.getvalue()).decode()
        except Exception:
            return None

    days_data: dict[str, list[dict]]  = {d: [] for d in _DAY_KEYS}
    recipe_map: dict[str, dict]       = {}

    try:
        with pdfplumber.open(_io.BytesIO(contenido)) as pdf:
            for page in pdf.pages:
                tables = page.extract_tables()
                if not tables:
                    continue
                table = tables[0]

                # 1. Encabezado de días
                header_ri = None
                col_day: dict[int, str] = {}
                for ri, row in enumerate(table):
                    for ci, cell in enumerate(row):
                        if not cell:
                            continue
                        nd = _nd(str(cell).strip())
                        if nd in _DAY_KEYS:
                            header_ri = ri
                            col_day[ci] = nd
                    if header_ri is not None:
                        break

                if not col_day:
                    continue

                # 2. Imágenes de platillos (excluir logo: Image6)
                dish_imgs = sorted(
                    [img for img in page.images
                     if img["x1"] - img["x0"] > 50
                     and img.get("stream") is not None
                     and img.get("name", "") != "Image6"],
                    key=lambda i: i["x0"],
                )
                sorted_cols = sorted(col_day.keys())
                col_img: dict[int, str | None] = {
                    ci: (_img_b64(dish_imgs[idx]["stream"]) if idx < len(dish_imgs) else None)
                    for idx, ci in enumerate(sorted_cols)
                }

                # 3. Tipo de comida de la página
                meal_key = None
                for ri in range(header_ri + 1, len(table)):
                    row = table[ri]
                    label = _nd(str(row[0] or "").strip()) if row[0] else ""
                    for mk in _MEAL_MAP:          # ya están en orden longest-first
                        if mk in label:
                            meal_key = mk
                            break
                    if meal_key:
                        break
                if not meal_key:
                    pt = _nd(page.extract_text() or "")
                    for mk in _MEAL_MAP:
                        if mk in pt:
                            meal_key = mk
                            break
                if not meal_key:
                    continue

                meal_nombre, meal_hora = _MEAL_MAP[meal_key]

                # 4. Texto por columna (filas post-encabezado)
                col_texts: dict[int, list[str]] = {ci: [] for ci in col_day}
                for ri in range(header_ri + 1, len(table)):
                    row = table[ri]
                    for ci in col_day:
                        if ci < len(row) and row[ci] and str(row[ci]).strip():
                            col_texts[ci].append(str(row[ci]).strip())

                # 5. Parsear cada columna
                for ci, day_key in col_day.items():
                    raw = " ".join(col_texts.get(ci, []))
                    if not raw.strip():
                        continue

                    parts         = raw.split("*")
                    nombre_receta = " ".join(parts[0].split())
                    if not nombre_receta:
                        continue

                    ingredientes: list[dict] = []
                    for ing_raw in parts[1:]:
                        ing_raw = ing_raw.strip().rstrip(".")
                        if not ing_raw or len(ing_raw) < 2:
                            continue
                        parsed = _parse_ingredient_line("* " + ing_raw)
                        if parsed:
                            ingredientes.append(parsed)
                        else:
                            low = ing_raw.lower()
                            if not any(c in low for c in _CONDIMENTS):
                                ingredientes.append({
                                    "nombre_alimento":  ing_raw,
                                    "cantidad":         None,
                                    "unidad":           None,
                                    "calorias":         None,
                                    "proteinas_g":      None,
                                    "carbohidratos_g":  None,
                                    "grasas_g":         None,
                                })

                    def _tot(k: str) -> float | None:
                        vals = [i[k] for i in ingredientes if i.get(k) is not None]
                        return round(sum(vals), 1) if vals else None

                    kcal  = _tot("calorias")
                    prot  = _tot("proteinas_g")
                    carb  = _tot("carbohidratos_g")
                    fat   = _tot("grasas_g")
                    imagen = col_img.get(ci)

                    days_data[day_key].append({
                        "meal_key":      meal_key,
                        "nombre":        meal_nombre,
                        "hora":          meal_hora,
                        "nombre_receta": nombre_receta,
                        "ingredientes":  ingredientes,
                        "kcal":  kcal, "prot": prot,
                        "carb":  carb, "fat":  fat,
                    })

                    rk = nombre_receta.lower()
                    if rk not in recipe_map:
                        recipe_map[rk] = {
                            "nombre":          nombre_receta,
                            "calorias":        int(kcal) if kcal else None,
                            "proteinas_g":     prot,
                            "carbohidratos_g": carb,
                            "grasas_g":        fat,
                            "ingredientes":    ingredientes,
                            "imagen":          imagen,
                            "fuente":          "pdf_import",
                        }

    except Exception:
        return None

    if not recipe_map:
        return None

    # Construir plan v2
    semana_dias = []
    for day_key in _DAY_KEYS:
        meals = days_data[day_key]
        if not meals:
            continue
        comidas = [{
            "nombre": m["nombre"],
            "hora":   m["hora"],
            "items":  [{
                "nombre_alimento": m["nombre_receta"],
                "cantidad":        None,
                "unidad":          None,
                "calorias":        m["kcal"],
                "proteinas_g":     m["prot"],
                "carbohidratos_g": m["carb"],
                "grasas_g":        m["fat"],
            }],
        } for m in meals]
        semana_dias.append({"dia": day_key, "comidas": comidas})

    # Calorías meta = promedio de totales diarios
    day_totals = [
        sum(m["kcal"] for m in days_data[d] if m.get("kcal"))
        for d in _DAY_KEYS if days_data[d]
    ]
    daily_kcal = round(sum(t for t in day_totals if t) / len([t for t in day_totals if t])) \
                 if any(day_totals) else None

    plan = {
        "nombre":               "Plan Alimenticio Importado",
        "objetivo":             "mantenimiento",
        "duracion_semanas":     1,
        "calorias_meta":        daily_kcal,
        "proteinas_meta_g":     None,
        "carbohidratos_meta_g": None,
        "grasas_meta_g":        None,
        "notas":                None,
        "fuente":               "ia_import",
        "semanas": [{"numero": 1, "notas": None, "dias": semana_dias}],
    }

    return {"plan": plan, "recetas": list(recipe_map.values())}
