"""
utils/rutina_helpers.py — Helpers de deduplicación y pesos sugeridos para el ETL de rutinas.

Usado por routes/entrenador/trainer_routes.py:
  - normalizar_nombre(s)                         → str        (clave de comparación insensible a may/acentos)
  - dedupe_ejercicios(ejercicios, rutinas, existentes) → dict  (separa nuevos/omitidos y complementa)
  - sugerir_peso(nivel, grupo_muscular, tipo)    → str        (peso por tabla fija según nivel)
  - NIVELES / NIVEL_IDX                                        (catálogo de niveles válidos)

Diseño:
  - Dedup por ENTRENADOR (alineado con el UniqueConstraint uq_ejercicio_gym_trainer_nombre).
  - Pesos por TABLA FIJA por nivel + grupo muscular (determinístico, sin IA, reproducible).
  - Sin estado ni acceso a BD: funciones puras y testeables. El caller provee `existentes`.
"""
from __future__ import annotations

import re
import unicodedata


# ─── Normalización de nombres ─────────────────────────────────────────────────

def normalizar_nombre(s: str | None) -> str:
    """
    Clave canónica para comparar nombres de ejercicios:
    minúsculas, sin acentos, sin espacios redundantes.
    'Press  Banca'  ==  'press banca'  ==  'Préss Bánca'
    """
    if not s:
        return ""
    s = unicodedata.normalize("NFD", s.strip().lower())
    s = "".join(c for c in s if unicodedata.category(c) != "Mn")  # quita diacríticos
    return re.sub(r"\s+", " ", s)


# ─── Deduplicación de ejercicios extraídos por IA ─────────────────────────────

def dedupe_ejercicios(
    ejercicios: list[dict] | None,
    rutinas: list[dict] | None,
    existentes: dict[str, dict] | None,
) -> dict:
    """
    Separa los ejercicios extraídos en NUEVOS vs OMITIDOS y complementa los que
    ya están almacenados.

    Args:
        ejercicios:  lista de ejercicios candidatos para la biblioteca
                     (cada uno con al menos {"nombre": ...}).
        rutinas:     lista de rutinas con days[].exercises[] (se anotan in-place).
        existentes:  {nombre_normalizado: {series, repeticiones, grupo_muscular, tipo}}
                     — biblioteca actual del entrenador (proviene de la BD).

    Reglas:
        - Si el ejercicio ya existe y está ACTIVO → se OMITE (se reutiliza) y se reporta.
        - Si ya existe pero está INACTIVO (soft-deleted, activo=False) → se marca para
          REACTIVAR (create_routine lo reactiva al guardar la rutina), no se duplica.
        - Si está repetido dentro del mismo archivo → se colapsa a una sola copia.
        - Cada ejercicio de la rutina se anota con `ya_existe` y, si existe, se
          COMPLEMENTAN los campos vacíos (sets/reps) con los datos almacenados.

    Args (cont.):
        existentes: cada valor puede incluir `activo` (bool) para distinguir
                    reutilizar (activo) de reactivar (inactivo).

    Returns:
        {
          "nuevos":    [ejercicios que sí se agregarán],
          "omitidos":  [nombres ya existentes y activos (str)],
          "reactivar": [nombres existentes pero inactivos que volverán (str)],
          "duplicados_archivo": <int>,   # repetidos dentro del mismo archivo
        }
    """
    existentes = existentes or {}
    nuevos: list[dict] = []
    omitidos: list[str] = []
    reactivar: list[str] = []
    vistos: set[str] = set()
    duplicados_archivo = 0

    for ej in ejercicios or []:
        nombre = (ej.get("nombre") or "").strip()
        if not nombre:
            continue
        clave = normalizar_nombre(nombre)
        detalle = existentes.get(clave)
        if detalle is not None:
            if detalle.get("activo", True):
                if nombre not in omitidos:
                    omitidos.append(nombre)
            else:
                if nombre not in reactivar:
                    reactivar.append(nombre)
            continue
        if clave in vistos:
            duplicados_archivo += 1
            continue
        vistos.add(clave)
        nuevos.append(ej)

    # Anotar y complementar los ejercicios dentro de cada día de rutina
    for rutina in rutinas or []:
        for dia in rutina.get("days", []) or []:
            for ex in dia.get("exercises", []) or []:
                clave = normalizar_nombre(ex.get("name"))
                detalle = existentes.get(clave)
                ex["ya_existe"] = detalle is not None
                if detalle:
                    # Completar huecos con lo ya almacenado (complementar, no pisar)
                    if not str(ex.get("sets") or "").strip() and detalle.get("series"):
                        ex["sets"] = str(detalle["series"])
                    if not str(ex.get("reps") or "").strip() and detalle.get("repeticiones"):
                        ex["reps"] = str(detalle["repeticiones"])

    return {
        "nuevos": nuevos,
        "omitidos": omitidos,
        "reactivar": reactivar,
        "duplicados_archivo": duplicados_archivo,
    }


# ─── Pesos sugeridos por nivel (tabla fija) ───────────────────────────────────

NIVELES = ("Principiante", "Intermedio", "Avanzado")
NIVEL_IDX = {"principiante": 0, "intermedio": 1, "avanzado": 2}

# Peso base sugerido en kg por grupo muscular: (Principiante, Intermedio, Avanzado).
# Valores conservadores de ARRANQUE; el entrenador ajusta según el cliente.
_PESOS_KG: dict[str, tuple[int, int, int]] = {
    "piernas":   (30, 60, 90),
    "pecho":     (20, 40, 60),
    "espalda":   (25, 45, 65),
    "hombros":   (12, 24, 36),
    "biceps":    (8,  14, 20),
    "triceps":   (10, 18, 28),
    "full body": (15, 30, 45),
}
_PESO_DEFAULT = (10, 20, 30)

# Grupos / tipos que no usan peso externo
_TIPOS_SIN_PESO = {"cardio", "flexibilidad"}
_GRUPOS_PESO_CORPORAL = {"abdomen", "core"}


def sugerir_peso(nivel: str | None, grupo_muscular: str | None, tipo: str | None = None) -> str:
    """
    Devuelve un peso sugerido (str con unidad) según el nivel del cliente y el
    grupo muscular / tipo del ejercicio. Tabla fija, determinística.

    - Cardio / Flexibilidad           → ""              (sin peso)
    - Abdomen / Core                  → "Peso corporal" (o "+5 kg" en avanzado)
    - Resto                           → "<n> kg" por tabla
    - Nivel desconocido               → cae a Principiante
    """
    t = normalizar_nombre(tipo)
    if t in _TIPOS_SIN_PESO:
        return ""

    idx = NIVEL_IDX.get(normalizar_nombre(nivel), 0)

    g = normalizar_nombre(grupo_muscular)
    if g in _GRUPOS_PESO_CORPORAL:
        return "Peso corporal +5 kg" if idx == 2 else "Peso corporal"

    base = _PESOS_KG.get(g, _PESO_DEFAULT)
    return f"{base[idx]} kg"
