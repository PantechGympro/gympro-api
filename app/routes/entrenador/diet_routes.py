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
#  AI ETL — Importar plan desde PDF / Excel
# ═════════════════════════════════════════════════════════════════════════════

_AI_PROMPT = """\
Eres un nutricionista experto. Lee el documento de plan alimenticio y extrae \
toda la información estructurada.

Devuelve SOLO un objeto JSON válido con esta estructura (sin markdown, sin texto extra):
{
  "nombre": "nombre descriptivo del plan",
  "objetivo": "perder_peso|ganar_masa|mantenimiento|definicion|rendimiento",
  "calorias_meta": <número o null>,
  "proteinas_meta_g": <número o null>,
  "carbohidratos_meta_g": <número o null>,
  "grasas_meta_g": <número o null>,
  "duracion_semanas": <número, default 1>,
  "notas": "observaciones generales",
  "semanas": [
    {
      "numero": 1,
      "notas": "",
      "dias": [
        {
          "dia": "lunes|martes|miercoles|jueves|viernes|sabado|domingo",
          "comidas": [
            {
              "nombre": "Desayuno",
              "hora": "08:00",
              "tiempo_desde_anterior_min": null,
              "items": [
                {
                  "nombre_alimento": "nombre del alimento",
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

Reglas:
- Si el plan es igual todos los días, crea 7 días (lunes a domingo) con el mismo contenido
- Si hay días distintos, crea solo los que aparecen
- Si hay varias semanas diferenciadas, crea múltiples semanas
- Infiere el objetivo del contexto si no está explícito
- Si falta un valor numérico usa null

DOCUMENTO:
"""


@diet_bp.route("/diets/import-ai", methods=["POST"])
@jwt_required()
@require_tenant
def import_diet_ai():
    """
    ETL con IA: extrae un plan alimenticio de un PDF o archivo Excel
    y devuelve la estructura JSON lista para confirmar y guardar.

    Requiere ANTHROPIC_API_KEY en el entorno.
    """
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        return jsonify({
            "error": "ANTHROPIC_API_KEY no configurada",
            "detalle": (
                "Agrega ANTHROPIC_API_KEY=<tu_clave> al archivo api/.env "
                "y reconstruye el contenedor con: "
                "docker compose build --no-cache api && docker compose up -d api"
            ),
        }), 503

    archivo = request.files.get("archivo")
    if not archivo:
        return jsonify({"error": "No se recibió archivo"}), 400

    nombre_archivo = archivo.filename or ""
    ext = nombre_archivo.rsplit(".", 1)[-1].lower()

    # ── Extracción de texto ──────────────────────────────────────────────────
    raw_text = ""
    try:
        contenido = archivo.read()

        if ext == "pdf":
            import pdfplumber  # noqa: PLC0415
            with pdfplumber.open(io.BytesIO(contenido)) as pdf:
                partes = [p.extract_text() or "" for p in pdf.pages]
                raw_text = "\n\n".join(partes)

        elif ext in {"xlsx", "xls"}:
            import openpyxl  # noqa: PLC0415
            wb = openpyxl.load_workbook(io.BytesIO(contenido), data_only=True)
            lines: list[str] = []
            for ws in wb.worksheets:
                lines.append(f"[Hoja: {ws.title}]")
                for row in ws.iter_rows(values_only=True):
                    celdas = [str(c) if c is not None else "" for c in row]
                    if any(c.strip() for c in celdas):
                        lines.append(" | ".join(celdas))
            raw_text = "\n".join(lines)

        else:
            return jsonify({
                "error": "Formato no soportado",
                "detalle": "Usa un archivo PDF (.pdf) o Excel (.xlsx / .xls)",
            }), 400

    except Exception as e:
        return jsonify({"error": f"Error leyendo el archivo: {e}"}), 400

    if not raw_text.strip():
        return jsonify({
            "error": "El archivo no contiene texto extraíble",
            "detalle": "Asegúrate de que el PDF no sea una imagen escaneada.",
        }), 422

    # ── LLM — Claude Haiku ───────────────────────────────────────────────────
    try:
        import anthropic  # noqa: PLC0415

        client = anthropic.Anthropic(api_key=api_key)
        mensaje = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=4096,
            messages=[{
                "role": "user",
                "content": _AI_PROMPT + raw_text[:10_000],
            }],
        )

        texto = mensaje.content[0].text.strip()

        # Limpiar bloque markdown si el modelo lo agrega
        if texto.startswith("```"):
            lineas = texto.split("\n")
            texto  = "\n".join(lineas[1:] if len(lineas) > 1 else lineas)
        if texto.endswith("```"):
            texto = texto[: texto.rfind("```")].strip()

        plan = json.loads(texto)
        return jsonify({
            "success":  True,
            "plan":     plan,
            "archivo":  nombre_archivo,
        }), 200

    except json.JSONDecodeError:
        return jsonify({
            "error": "La IA no pudo estructurar el documento",
            "detalle": (
                "El archivo puede tener un formato muy inusual. "
                "Prueba con un PDF con texto seleccionable o un Excel bien estructurado."
            ),
        }), 422
    except Exception as e:
        print(traceback.format_exc())
        return jsonify({"error": f"Error en el proceso de IA: {e}"}), 500
