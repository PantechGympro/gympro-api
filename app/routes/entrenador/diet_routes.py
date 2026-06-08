"""
routes/entrenador/diet_routes.py — Módulo completo de Nutrición para entrenadores.

Endpoints:
  Recetas  — /api/trainer/recipes          CRUD  (biblioteca privada por entrenador)
  Dietas   — /api/trainer/diets            CRUD  (planes multi-semana, multi-día)
  AI ETL   — /api/trainer/diets/import-ai  POST  (PDF/Excel → plan estructurado vía Claude)

Colecciones MongoDB:
  mdb.recetas  — biblioteca de recetas del entrenador
  mdb.dietas   — planes alimenticios (reemplaza colección anterior con schema enriquecido)

Schema de dieta v2:
  {
    id_entrenador_pg, id_gimnasio_pg, id_miembro_pg?,
    nombre, objetivo, calorias_meta, proteinas_meta_g, carbohidratos_meta_g, grasas_meta_g,
    duracion_semanas, notas,
    semanas: [{ numero, notas, dias: [{ dia, comidas: [{ nombre, hora,
      tiempo_desde_anterior_min, items: [{ id_receta?, nombre_alimento, cantidad, unidad,
      calorias, proteinas_g, carbohidratos_g, grasas_g, imagen? }] }] }] }],
    fuente: "manual"|"ia_import", archivo_fuente?,
    eliminada, fecha_creacion
  }
"""
from __future__ import annotations

import io
import json
import os
import traceback
from datetime import datetime, timezone

from bson.objectid import ObjectId
from flask import Blueprint, g, jsonify, request
from flask_jwt_extended import get_jwt_identity, jwt_required

from app.mongo import get_db
from app.utils.tenant import require_tenant

diet_bp = Blueprint("trainer_diet", __name__, url_prefix="/api/trainer")


# ─────────────────────────────────────────────────────────────────────────────
#  Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _ser_receta(doc: dict) -> dict:
    d = dict(doc)
    d["id"] = str(d.pop("_id"))
    if isinstance(d.get("created_at"), datetime):
        d["created_at"] = d["created_at"].isoformat()
    return d


def _ser_dieta(doc: dict) -> dict:
    d = dict(doc)
    d["id"] = str(d.pop("_id"))
    if isinstance(d.get("fecha_creacion"), datetime):
        d["fecha_creacion"] = d["fecha_creacion"].strftime("%Y-%m-%d")
    return d


# ═════════════════════════════════════════════════════════════════════════════
#  RECETAS — Biblioteca privada del entrenador
# ═════════════════════════════════════════════════════════════════════════════

@diet_bp.route("/recipes", methods=["GET"])
@jwt_required()
@require_tenant
def list_recipes():
    """Lista recetas activas del entrenador logueado."""
    try:
        mdb        = get_db()
        trainer_id = int(get_jwt_identity())
        gym_id     = g.tenant_id

        recetas = list(mdb.recetas.find(
            {"id_entrenador_pg": trainer_id, "id_gimnasio_pg": gym_id, "activo": True},
            sort=[("created_at", -1)],
        ))
        return jsonify({"recipes": [_ser_receta(r) for r in recetas]}), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@diet_bp.route("/recipes", methods=["POST"])
@jwt_required()
@require_tenant
def create_recipe():
    """Crea una receta en la biblioteca del entrenador."""
    try:
        mdb        = get_db()
        trainer_id = int(get_jwt_identity())
        gym_id     = g.tenant_id
        data       = request.get_json() or {}

        nombre = (data.get("nombre") or "").strip()
        if not nombre:
            return jsonify({"error": "El nombre es obligatorio"}), 400

        doc = {
            "id_entrenador_pg":      trainer_id,
            "id_gimnasio_pg":        gym_id,
            "nombre":                nombre,
            "descripcion":           data.get("descripcion", ""),
            "imagen":                data.get("imagen"),           # base64 o URL
            "calorias":              data.get("calorias"),
            "proteinas_g":           data.get("proteinas_g"),
            "carbohidratos_g":       data.get("carbohidratos_g"),
            "grasas_g":              data.get("grasas_g"),
            "tiempo_preparacion_min": data.get("tiempo_preparacion_min"),
            "ingredientes":          data.get("ingredientes", []),  # [{nombre, cantidad, unidad}]
            "instrucciones":         data.get("instrucciones", ""),
            "activo":                True,
            "created_at":            datetime.now(timezone.utc),
        }
        result = mdb.recetas.insert_one(doc)
        doc["_id"] = result.inserted_id
        return jsonify({"success": True, "recipe": _ser_receta(doc)}), 201
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@diet_bp.route("/recipes/<recipe_id>", methods=["PUT"])
@jwt_required()
@require_tenant
def update_recipe(recipe_id):
    try:
        mdb        = get_db()
        trainer_id = int(get_jwt_identity())
        gym_id     = g.tenant_id
        data       = request.get_json() or {}

        if not mdb.recetas.find_one({
            "_id": ObjectId(recipe_id),
            "id_entrenador_pg": trainer_id,
            "id_gimnasio_pg":   gym_id,
        }):
            return jsonify({"error": "Receta no encontrada"}), 404

        fields = [
            "nombre", "descripcion", "imagen", "calorias", "proteinas_g",
            "carbohidratos_g", "grasas_g", "tiempo_preparacion_min",
            "ingredientes", "instrucciones",
        ]
        update = {k: data[k] for k in fields if k in data}
        if update:
            mdb.recetas.update_one({"_id": ObjectId(recipe_id)}, {"$set": update})
        return jsonify({"success": True}), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@diet_bp.route("/recipes/<recipe_id>", methods=["DELETE"])
@jwt_required()
@require_tenant
def delete_recipe(recipe_id):
    try:
        mdb        = get_db()
        trainer_id = int(get_jwt_identity())
        gym_id     = g.tenant_id

        result = mdb.recetas.update_one(
            {"_id": ObjectId(recipe_id), "id_entrenador_pg": trainer_id, "id_gimnasio_pg": gym_id},
            {"$set": {"activo": False}},
        )
        if result.matched_count == 0:
            return jsonify({"error": "Receta no encontrada"}), 404
        return jsonify({"success": True}), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ═════════════════════════════════════════════════════════════════════════════
#  DIETAS — Planes alimenticios v2 (multi-semana, multi-día)
# ═════════════════════════════════════════════════════════════════════════════

_DIET_FIELDS = [
    "nombre", "objetivo", "calorias_meta", "proteinas_meta_g",
    "carbohidratos_meta_g", "grasas_meta_g", "duracion_semanas", "notas", "semanas",
]


@diet_bp.route("/diets", methods=["GET"])
@jwt_required()
@require_tenant
def list_diets():
    """Lista planes activos del entrenador."""
    try:
        mdb        = get_db()
        trainer_id = int(get_jwt_identity())
        gym_id     = g.tenant_id

        dietas = list(mdb.dietas.find(
            {"id_entrenador_pg": trainer_id, "id_gimnasio_pg": gym_id, "eliminada": {"$ne": True}},
            sort=[("fecha_creacion", -1)],
        ))
        return jsonify({"diets": [_ser_dieta(d) for d in dietas]}), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@diet_bp.route("/diets", methods=["POST"])
@jwt_required()
@require_tenant
def create_diet():
    """Crea un plan alimenticio (manual o desde IA)."""
    try:
        mdb        = get_db()
        trainer_id = int(get_jwt_identity())
        gym_id     = g.tenant_id
        data       = request.get_json() or {}

        nombre = (data.get("nombre") or "").strip()
        if not nombre:
            return jsonify({"error": "El nombre es obligatorio"}), 400

        doc = {
            "id_entrenador_pg":      trainer_id,
            "id_gimnasio_pg":        gym_id,
            "id_miembro_pg":         int(data["id_miembro_pg"]) if data.get("id_miembro_pg") else None,
            "nombre":                nombre,
            "objetivo":              data.get("objetivo", "mantenimiento"),
            "calorias_meta":         data.get("calorias_meta"),
            "proteinas_meta_g":      data.get("proteinas_meta_g"),
            "carbohidratos_meta_g":  data.get("carbohidratos_meta_g"),
            "grasas_meta_g":         data.get("grasas_meta_g"),
            "duracion_semanas":      data.get("duracion_semanas", 1),
            "notas":                 data.get("notas", ""),
            "semanas":               data.get("semanas", []),
            # Backward-compat — clientes antiguos pueden enviar "comidas" plano
            "comidas":               data.get("comidas", []),
            "fuente":                data.get("fuente", "manual"),
            "archivo_fuente":        data.get("archivo_fuente"),
            "eliminada":             False,
            "fecha_creacion":        datetime.now(timezone.utc),
        }
        result = mdb.dietas.insert_one(doc)
        doc["_id"] = result.inserted_id
        return jsonify({"success": True, "diet": _ser_dieta(doc)}), 201
    except Exception as e:
        print(traceback.format_exc())
        return jsonify({"error": str(e)}), 500


@diet_bp.route("/diets/<diet_id>", methods=["PUT"])
@jwt_required()
@require_tenant
def update_diet(diet_id):
    try:
        mdb        = get_db()
        trainer_id = int(get_jwt_identity())
        gym_id     = g.tenant_id
        data       = request.get_json() or {}

        if not mdb.dietas.find_one({
            "_id": ObjectId(diet_id),
            "id_entrenador_pg": trainer_id,
            "id_gimnasio_pg":   gym_id,
        }):
            return jsonify({"error": "Plan no encontrado"}), 404

        update = {k: data[k] for k in _DIET_FIELDS if k in data}

        if "id_miembro_pg" in data:
            update["id_miembro_pg"] = int(data["id_miembro_pg"]) if data["id_miembro_pg"] else None
        # backward-compat
        if "comidas" in data:
            update["comidas"] = data["comidas"]

        if update:
            mdb.dietas.update_one({"_id": ObjectId(diet_id)}, {"$set": update})
        return jsonify({"success": True}), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@diet_bp.route("/diets/<diet_id>", methods=["DELETE"])
@jwt_required()
@require_tenant
def delete_diet(diet_id):
    try:
        mdb        = get_db()
        trainer_id = int(get_jwt_identity())
        gym_id     = g.tenant_id

        result = mdb.dietas.update_one(
            {"_id": ObjectId(diet_id), "id_entrenador_pg": trainer_id, "id_gimnasio_pg": gym_id},
            {"$set": {"eliminada": True}},
        )
        if result.matched_count == 0:
            return jsonify({"error": "Plan no encontrado"}), 404
        return jsonify({"success": True}), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@diet_bp.route("/diets/<diet_id>/assign", methods=["POST"])
@jwt_required()
@require_tenant
def assign_diet(diet_id):
    """Asigna o desasigna un plan a un cliente."""
    try:
        mdb        = get_db()
        trainer_id = int(get_jwt_identity())
        gym_id     = g.tenant_id
        data       = request.get_json() or {}

        if not mdb.dietas.find_one({
            "_id": ObjectId(diet_id),
            "id_entrenador_pg": trainer_id,
            "id_gimnasio_pg":   gym_id,
        }):
            return jsonify({"error": "Plan no encontrado"}), 404

        id_miembro_pg = int(data["id_miembro_pg"]) if data.get("id_miembro_pg") else None
        mdb.dietas.update_one(
            {"_id": ObjectId(diet_id)},
            {"$set": {"id_miembro_pg": id_miembro_pg}},
        )
        return jsonify({"success": True}), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ═════════════════════════════════════════════════════════════════════════════
#  AI ETL — Importar plan desde PDF / Excel usando LLM local (Ollama)
#
#  Arquitectura ETL:
#    Extract  — pdfplumber / openpyxl → texto crudo
#    Transform — LLM local (phi3:mini, llama3.2:3b, mistral:7b…) vía Ollama
#    Load      — JSON estructurado → mdb.dietas (MongoDB)
#
#  Sin API externa, sin costo, sin internet requerido.
#  Ollama corre como servicio Docker en http://ollama:11434
# ═════════════════════════════════════════════════════════════════════════════

import requests as _requests  # alias para evitar conflicto con flask.request

# Configuración desde variables de entorno (con defaults)
_OLLAMA_BASE  = os.getenv("OLLAMA_BASE_URL", "http://ollama:11434")
_OLLAMA_MODEL = os.getenv("OLLAMA_MODEL",    "phi3:mini")

# Prompt del sistema — instrucciones de extracción
_ETL_SYSTEM_PROMPT = """\
Eres un nutricionista experto en análisis de planes alimenticios.
Tu tarea es extraer la información de un documento de dieta y devolver
ÚNICAMENTE un objeto JSON válido, sin explicaciones, sin markdown, sin texto extra.

La estructura JSON que debes devolver es exactamente:
{
  "nombre": "nombre descriptivo del plan",
  "objetivo": "perder_peso|ganar_masa|mantenimiento|definicion|rendimiento",
  "calorias_meta": <número entero o null>,
  "proteinas_meta_g": <número o null>,
  "carbohidratos_meta_g": <número o null>,
  "grasas_meta_g": <número o null>,
  "duracion_semanas": <número entero, mínimo 1>,
  "notas": "observaciones generales del plan",
  "semanas": [
    {
      "numero": 1,
      "notas": "",
      "dias": [
        {
          "dia": "lunes",
          "comidas": [
            {
              "nombre": "Desayuno",
              "hora": "08:00",
              "tiempo_desde_anterior_min": null,
              "items": [
                {
                  "nombre_alimento": "nombre del alimento o receta",
                  "cantidad": "100",
                  "unidad": "g",
                  "calorias": <número o null>,
                  "proteinas_g": <número o null>,
                  "carbohidratos_g": <número o null>,
                  "grasas_g": <número o null>
                }
              ]
            }
          ]
        }
      ]
    }
  ]
}

Reglas importantes:
- dias válidos: lunes, martes, miercoles, jueves, viernes, sabado, domingo
- Si el plan es igual cada día, crea los 7 días con el mismo contenido
- Si hay días distintos, crea solo los que aparecen en el documento
- Si hay varias semanas diferenciadas, crea múltiples semanas en el array
- Infiere el objetivo del contexto (perder peso, ganar masa, etc.)
- Usa null para valores numéricos no especificados
- RESPONDE SOLO CON EL JSON, nada más"""


def _extract_text(contenido: bytes, ext: str) -> str:
    """Extract raw text from PDF or Excel bytes."""
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


def _check_ollama_ready() -> tuple[bool, str]:
    """Verifica que Ollama esté activo y el modelo disponible."""
    try:
        r = _requests.get(f"{_OLLAMA_BASE}/api/tags", timeout=5)
        r.raise_for_status()
        modelos = [m["name"] for m in r.json().get("models", [])]
        modelo_disponible = any(
            _OLLAMA_MODEL in m or m.startswith(_OLLAMA_MODEL.split(":")[0])
            for m in modelos
        )
        if not modelo_disponible:
            return False, (
                f"El modelo '{_OLLAMA_MODEL}' no está descargado. "
                f"Ejecuta: docker compose exec ollama ollama pull {_OLLAMA_MODEL}"
            )
        return True, "ok"
    except _requests.exceptions.ConnectionError:
        return False, (
            "Ollama no está disponible. "
            "Asegúrate de que el servicio esté corriendo: docker compose up -d ollama"
        )
    except Exception as e:
        return False, f"Error verificando Ollama: {e}"


def _call_ollama(prompt_text: str) -> str:
    """
    Llama al LLM local vía Ollama API.
    Usa format='json' para forzar salida JSON válido.
    """
    payload = {
        "model":  _OLLAMA_MODEL,
        "prompt": f"{_ETL_SYSTEM_PROMPT}\n\nDOCUMENTO A PROCESAR:\n{prompt_text}",
        "stream": False,
        "format": "json",           # Fuerza respuesta JSON válido — característica clave de Ollama
        "options": {
            "temperature":  0.1,    # Baja temperatura = respuestas deterministas y precisas
            "num_predict":  4096,   # Tokens máximos de respuesta
            "top_p":        0.9,
        },
    }
    resp = _requests.post(
        f"{_OLLAMA_BASE}/api/generate",
        json=payload,
        timeout=120,   # Los modelos locales pueden tardar 30-60s en CPUs
    )
    resp.raise_for_status()
    return resp.json().get("response", "")


@diet_bp.route("/diets/import-ai", methods=["POST"])
@jwt_required()
@require_tenant
def import_diet_ai():
    """
    ETL con IA local (Ollama):
      1. Extract  — lee texto del PDF o Excel subido
      2. Transform — envía al LLM local (phi3:mini / llama3.2:3b / mistral:7b)
      3. Retorna  — JSON estructurado listo para que el entrenador confirme y guarde

    El modelo corre completamente en Docker, sin A