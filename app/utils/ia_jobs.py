"""
utils/ia_jobs.py — Jobs en background para el ETL de IA de rutinas.

Antes, POST /api/trainer/routines/import-ai corria todo el ETL (parser
deterministico + fallback Ollama, hasta 8 bloques secuenciales) de forma
sincrona dentro del propio request, bloqueando al entrenador hasta que
Ollama respondiera -- o hasta que tronara con un ReadTimeout si el modelo no
alcanzaba a generar el JSON a tiempo (ver SCRUM-203 y su seguimiento sobre
tiempos de carga de PDF).

Ahora:
  1. El endpoint valida el archivo, crea un doc de job en Mongo (estado
     "procesando") y lanza un hilo de background que hace el trabajo real.
  2. El endpoint responde de inmediato (202) con el job_id -- el entrenador
     puede seguir usando el resto del sistema mientras tanto.
  3. El frontend hace polling a GET /routines/import-ai/jobs/<job_id> y
     avisa cuando el resultado esta listo.

El hilo corre dentro de `with app.app_context()`: Flask-SQLAlchemy usa
scoped_session (thread-safe, ya validado con el worker gthread + NullPool de
gunicorn -- ver entrypoint.sh) y el resto de la app funciona igual que en un
request normal. Mongo (get_db()) es un singleton de PyMongo y no depende de
ningun contexto de Flask, así que es seguro llamarlo desde el hilo también
para leer/escribir el propio doc de job.
"""
from __future__ import annotations

import threading
import traceback
from datetime import datetime, timezone

from bson.objectid import ObjectId
import requests as _requests

from app.mongo import get_db

COLLECTION = "ia_import_jobs"

# Prompt del ETL de rutinas -- vivía en trainer_routes.py; se mueve aquí
# porque ahora es este módulo el que hace las llamadas a Ollama.
_ROUTINE_ETL_PROMPT = """
Eres un experto en entrenamiento físico y planificación de rutinas de gimnasio.
Tu tarea es extraer la información del documento y devolver ÚNICAMENTE un objeto JSON
válido, sin explicaciones, sin markdown, sin texto extra.

La estructura JSON que debes devolver es exactamente:
{
  "rutinas": [
    {
      "name": "nombre de la rutina",
      "category": "Fuerza|Hipertrofia|Cardio|Funcional|Movilidad|General",
      "difficulty": "Principiante|Intermedio|Avanzado",
      "duration_minutes": <número entero o 60>,
      "description": "descripción breve de la rutina",
      "days": [
        {
          "day": "Lunes|Martes|Miércoles|Jueves|Viernes|Sábado|Domingo",
          "muscleGroup": "Pecho|Espalda|Piernas|Hombros|Bíceps|Tríceps|Abdomen|Full Body|Cardio",
          "exercises": [
            {
              "name": "nombre del ejercicio",
              "sets": "3",
              "reps": "12",
              "peso": "descripción del peso o intensidad",
              "notes": "notas técnicas o de ejecución"
            }
          ]
        }
      ]
    }
  ],
  "ejercicios": [
    {
      "nombre": "nombre del ejercicio",
      "grupo_muscular": "Pecho|Espalda|Piernas|Hombros|Bíceps|Tríceps|Abdomen|Glúteos|Cuádriceps|Isquiotibiales|Full Body",
      "tipo": "Fuerza|Cardio|Flexibilidad|Funcional|Potencia",
      "series": <número entero o null>,
      "repeticiones": "descripción de repeticiones",
      "descripcion": "descripción o instrucciones del ejercicio"
    }
  ]
}

Reglas importantes:
- Extrae TODAS las rutinas y días que encuentres en el documento
- Si no hay estructura de días, agrupa los ejercicios en un solo día "Lunes"
- Para ejercicios sin número de series usa 3 como default
- Para ejercicios sin repeticiones usa "12" como default
- El array "ejercicios" debe contener ejercicios únicos del documento para la biblioteca
- Si no encuentras rutinas estructuradas, devuelve "rutinas": []
- Si no encuentras ejercicios individuales, devuelve "ejercicios": []
- RESPONDE SOLO CON EL JSON, nada más"""


def _now() -> datetime:
    return datetime.now(timezone.utc)


def create_job(*, tipo: str, id_gimnasio: int, id_entrenador: int, archivo: str) -> str:
    """Crea el doc de job en estado 'procesando' y devuelve su id como str."""
    mdb = get_db()
    doc = {
        "tipo":            tipo,           # "rutinas" -- unico tipo por ahora
        "id_gimnasio":     id_gimnasio,
        "id_entrenador":   id_entrenador,
        "archivo":         archivo,
        "estado":          "procesando",
        "resultado":       None,
        "error":           None,
        "error_tipo":      None,
        "detalle":         None,
        "creado_en":       _now(),
        "actualizado_en":  _now(),
    }
    res = mdb[COLLECTION].insert_one(doc)
    return str(res.inserted_id)


def get_job(job_id: str, *, id_gimnasio: int, id_entrenador: int) -> dict | None:
    """Recupera un job, acotado al gimnasio y entrenador dueños (aislamiento multi-tenant)."""
    try:
        oid = ObjectId(job_id)
    except Exception:
        return None
    mdb = get_db()
    doc = mdb[COLLECTION].find_one({
        "_id": oid, "id_gimnasio": id_gimnasio, "id_entrenador": id_entrenador,
    })
    if not doc:
        return None
    doc["job_id"] = str(doc.pop("_id"))
    for k in ("creado_en", "actualizado_en"):
        if isinstance(doc.get(k), datetime):
            doc[k] = doc[k].isoformat()
    return doc


def _set_job(job_id: str, **fields) -> None:
    mdb = get_db()
    mdb[COLLECTION].update_one(
        {"_id": ObjectId(job_id)},
        {"$set": {**fields, "actualizado_en": _now()}},
    )


def start_routines_import_job(app, *, job_id, contenido, ext, nombre_archivo,
                               id_gimnasio, id_entrenador) -> None:
    """Lanza el hilo de background que hace el ETL real de rutinas."""
    hilo = threading.Thread(
        target=_run_routines_import_job,
        args=(app, job_id, contenido, ext, nombre_archivo, id_gimnasio, id_entrenador),
        daemon=True,
        name=f"ia-import-rutinas-{job_id}",
    )
    hilo.start()


def _run_routines_import_job(app, job_id, contenido, ext, nombre_archivo,
                              id_gimnasio, id_entrenador) -> None:
    with app.app_context():
        try:
            from app.utils.etl_ollama import (  # noqa: PLC0415
                extract_text, parse_routines_from_text, check_ollama_ready,
                call_ollama, chunk_text, parse_llm_json,
            )
            from app.utils.rutina_helpers import dedupe_ejercicios, normalizar_nombre  # noqa: PLC0415
            from app.models.pg.ejercicio import Ejercicio  # noqa: PLC0415

            # ── Extract ──────────────────────────────────────────────────────
            # Texto plano SIN tocar -- el parser determinista de abajo depende
            # de columnas separadas por espacios, no de "|".
            try:
                raw_text = extract_text(contenido, ext)
            except Exception as e:
                _set_job(job_id, estado="error", error_tipo="lectura",
                          error=f"Error leyendo el archivo: {e}")
                return

            if not raw_text.strip():
                _set_job(
                    job_id, estado="error", error_tipo="sin_texto",
                    error="El archivo no contiene texto extraíble",
                    detalle="Asegúrate de que el PDF no sea una imagen escaneada.",
                )
                return

            # ── Transform: parser determinístico (rápido, sin LLM) ────────────
            resultado = parse_routines_from_text(raw_text)
            aviso_truncado: str | None = None
            hubo_timeout = False

            if resultado is None:
                ready, msg = check_ollama_ready()
                if not ready:
                    _set_job(job_id, estado="error", error_tipo="ollama_no_disponible",
                              error="Servicio de IA no disponible", detalle=msg)
                    return

                # Fallback LLM: aquí sí conviene el texto compactado (tablas ->
                # "celda | celda", sin encabezados repetidos entre páginas) --
                # menos tokens de entrada, Ollama más rápido y más preciso.
                # Si algo falla, se usa el texto plano como respaldo.
                raw_text_llm = raw_text
                if ext == "pdf":
                    try:
                        raw_text_llm = extract_text(contenido, ext, structured=True) or raw_text
                    except Exception:
                        raw_text_llm = raw_text

                bloques, truncado = chunk_text(raw_text_llm)
                combinado: dict = {"rutinas": [], "ejercicios": []}
                for bloque in bloques:
                    try:
                        parsed = parse_llm_json(call_ollama(_ROUTINE_ETL_PROMPT, bloque))
                    except _requests.exceptions.Timeout:
                        hubo_timeout = True
                        print(f"[ia_jobs] timeout de Ollama procesando un bloque (job {job_id})")
                        continue
                    except TimeoutError:
                        # No se consiguio cupo en _acquire_ollama_slot() (ver etl_ollama.py):
                        # Ollama ya esta saturado por otras importaciones en curso. Se trata
                        # igual que un timeout de cara al mensaje final del job.
                        hubo_timeout = True
                        print(f"[ia_jobs] Ollama saturado (sin cupo libre) procesando un bloque (job {job_id})")
                        continue
                    except Exception:
                        print(traceback.format_exc())
                        continue
                    if not isinstance(parsed, dict):
                        continue
                    combinado["rutinas"].extend(parsed.get("rutinas") or [])
                    combinado["ejercicios"].extend(parsed.get("ejercicios") or [])

                if not combinado["rutinas"] and not combinado["ejercicios"]:
                    # Distinguir timeout de "el modelo no entendió el documento"
                    # (antes ambos casos devolvían el mismo mensaje genérico).
                    if hubo_timeout:
                        _set_job(
                            job_id, estado="error", error_tipo="timeout",
                            error="La IA tardó demasiado en procesar el documento",
                            detalle=(
                                "Ollama no respondió a tiempo en ninguno de los bloques del "
                                "documento. Si el archivo es largo o el servidor está bajo de "
                                "recursos, prueba con uno más corto o inténtalo de nuevo."
                            ),
                        )
                    else:
                        _set_job(
                            job_id, estado="error", error_tipo="sin_estructura",
                            error="La IA no pudo estructurar el documento",
                            detalle=(
                                "El archivo tiene un formato no reconocido. "
                                "Prueba con un PDF con texto seleccionable o un Excel bien estructurado."
                            ),
                        )
                    return

                resultado = combinado
                if truncado:
                    aviso_truncado = (
                        "El documento es muy largo: se procesó solo la primera parte. "
                        "Para no perder ejercicios, divídelo en archivos más pequeños."
                    )

            rutinas    = resultado.get("rutinas",   [])
            ejercicios = resultado.get("ejercicios", [])

            # ── Deduplicación contra la biblioteca del entrenador ────────────
            existentes = {
                normalizar_nombre(e.nombre): {
                    "series":         e.series,
                    "repeticiones":   e.repeticiones,
                    "grupo_muscular": e.grupo_muscular,
                    "tipo":           e.tipo,
                    "activo":         e.activo,
                }
                for e in Ejercicio.query.filter_by(
                    id_gimnasio=id_gimnasio, id_entrenador=id_entrenador
                ).all()
            }
            dedup = dedupe_ejercicios(ejercicios, rutinas, existentes)
            ejercicios = dedup["nuevos"]

            avisos: list[str] = []
            if aviso_truncado:
                avisos.append(aviso_truncado)
            if hubo_timeout and (rutinas or ejercicios):
                avisos.append(
                    "Uno o más bloques del documento tardaron demasiado y se omitieron; "
                    "revisa que no falte contenido antes de confirmar."
                )
            if dedup["omitidos"]:
                muestra = ", ".join(dedup["omitidos"][:5])
                extra   = f" y {len(dedup['omitidos']) - 5} más" if len(dedup["omitidos"]) > 5 else ""
                avisos.append(
                    f"{len(dedup['omitidos'])} ejercicio(s) ya existían en tu biblioteca "
                    f"y se omitieron: {muestra}{extra}. Se reutilizarán los ya guardados."
                )
            if dedup.get("reactivar"):
                muestra = ", ".join(dedup["reactivar"][:5])
                extra   = f" y {len(dedup['reactivar']) - 5} más" if len(dedup["reactivar"]) > 5 else ""
                avisos.append(
                    f"{len(dedup['reactivar'])} ejercicio(s) que habías eliminado se "
                    f"reactivarán al importar: {muestra}{extra}."
                )
            if dedup["duplicados_archivo"]:
                avisos.append(
                    f"{dedup['duplicados_archivo']} ejercicio(s) venían repetidos dentro "
                    f"del archivo y se consolidaron en uno solo."
                )
            if dedup["nuevos"]:
                avisos.append(f"{len(dedup['nuevos'])} ejercicio(s) nuevo(s) se agregarán a tu biblioteca.")

            resultado_final = {
                "success":             True,
                "rutinas":             rutinas,
                "ejercicios":          ejercicios,
                "ejercicios_omitidos": dedup["omitidos"],
                "avisos":              avisos,
                "archivo":             nombre_archivo,
                "resumen": {
                    "total_rutinas":          len(rutinas),
                    "ejercicios_nuevos":      len(dedup["nuevos"]),
                    "ejercicios_omitidos":    len(dedup["omitidos"]),
                    "ejercicios_reactivados": len(dedup.get("reactivar", [])),
                    "duplicados_archivo":     dedup["duplicados_archivo"],
                    "total_dias": sum(len(r.get("days", [])) for r in rutinas),
                },
            }
            _set_job(job_id, estado="listo", resultado=resultado_final)

        except Exception as e:
            print(traceback.format_exc())
            _set_job(job_id, estado="error", error_tipo="excepcion",
                      error=f"Error en el proceso de IA: {e}")
