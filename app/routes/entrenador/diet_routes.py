"""
routes/entrenador/diet_routes.py — Módulo completo de Nutrición para entrenadores.

Endpoints:
  Recetas  — /api/trainer/recipes          CRUD  (biblioteca privada por entrenador)
  Dietas   — /api/trainer/diets            CRUD  (planes multi-semana, multi-día)
  AI ETL   — /api/trainer/diets/import-ai  POST  (PDF/Excel → plan estructurado vía Ollama (LLM local))

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
    # ObjectId en id_miembro → string para serialización JSON
    if hasattr(d.get("id_miembro"), "binary"):  # ObjectId check
        d["id_miembro"] = str(d["id_miembro"])
    return d


def _safe_int(val):
    """Convierte val a int o None. Acepta '123', 123, None, ''. Rechaza ObjectId hex strings."""
    if not val:
        return None
    try:
        return int(val)
    except (ValueError, TypeError):
        return None


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


@diet_bp.route("/recipes/bulk-delete", methods=["POST"])
@jwt_required()
@require_tenant
def bulk_delete_recipes():
    """Elimina (soft-delete) múltiples recetas en una sola operación MongoDB."""
    try:
        mdb        = get_db()
        trainer_id = int(get_jwt_identity())
        gym_id     = g.tenant_id
        data       = request.get_json(force=True) or {}
        ids        = data.get("ids", [])

        if not ids or not isinstance(ids, list):
            return jsonify({"error": "Se requiere lista 'ids'"}), 400

        object_ids = []
        for raw_id in ids:
            try:
                object_ids.append(ObjectId(raw_id))
            except Exception:
                pass  # ignorar IDs malformados

        result = mdb.recetas.update_many(
            {
                "_id": {"$in": object_ids},
                "id_entrenador_pg": trainer_id,
                "id_gimnasio_pg": gym_id,
            },
            {"$set": {"activo": False}},
        )
        return jsonify({"success": True, "deleted": result.modified_count}), 200
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

        id_miembro_pg = _safe_int(data.get("id_miembro_pg"))

        # Resolver ObjectId de MongoDB del miembro para que el portal del miembro
        # pueda encontrar el plan con su query habitual (id_miembro = ObjectId).
        id_miembro_oid = None
        if id_miembro_pg:
            miembro_doc = mdb.miembros.find_one(
                {"id_usuario_pg": id_miembro_pg, "id_gimnasio_pg": gym_id},
                {"_id": 1},
            )
            if miembro_doc:
                id_miembro_oid = miembro_doc["_id"]

        doc = {
            "id_entrenador_pg":      trainer_id,
            "id_gimnasio_pg":        gym_id,
            "id_miembro_pg":         id_miembro_pg,
            "id_miembro":            id_miembro_oid,   # ObjectId — usado por portal miembro
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
            update["id_miembro_pg"] = _safe_int(data.get("id_miembro_pg"))
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


@diet_bp.route("/diets/bulk-delete", methods=["POST"])
@jwt_required()
@require_tenant
def bulk_delete_diets():
    """Elimina (soft-delete) múltiples planes en una sola operación MongoDB."""
    try:
        mdb        = get_db()
        trainer_id = int(get_jwt_identity())
        gym_id     = g.tenant_id
        data       = request.get_json(force=True) or {}
        ids        = data.get("ids", [])

        if not ids or not isinstance(ids, list):
            return jsonify({"error": "Se requiere lista 'ids'"}), 400

        object_ids = []
        for raw_id in ids:
            try:
                object_ids.append(ObjectId(raw_id))
            except Exception:
                pass

        result = mdb.dietas.update_many(
            {
                "_id": {"$in": object_ids},
                "id_entrenador_pg": trainer_id,
                "id_gimnasio_pg": gym_id,
            },
            {"$set": {"eliminada": True}},
        )
        return jsonify({"success": True, "deleted": result.modified_count}), 200
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

        id_miembro_pg = _safe_int(data.get("id_miembro_pg"))
        set_fields = {"id_miembro_pg": id_miembro_pg}
        if id_miembro_pg:
            miembro_doc = mdb.miembros.find_one(
                {"id_usuario_pg": id_miembro_pg, "id_gimnasio_pg": gym_id}, {"_id": 1}
            )
            if miembro_doc:
                set_fields["id_miembro"] = miembro_doc["_id"]
        mdb.dietas.update_one({"_id": ObjectId(diet_id)}, {"$set": set_fields})
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

# ─── Prompt ETL específico para planes alimenticios ──────────────────────────
# Las funciones extract_text / check_ollama_ready / call_ollama se importan
# desde app.utils.etl_ollama (módulo compartido con el ETL de rutinas).

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


@diet_bp.route("/diets/ai-status", methods=["GET"])
@jwt_required()
@require_tenant
def diet_ai_status():
    """Estado del servicio Ollama (disponibilidad + modelo cargado)."""
    try:
        from app.utils.etl_ollama import get_ollama_status  # noqa: PLC0415
        return jsonify(get_ollama_status()), 200
    except Exception as e:
        return jsonify({"available": False, "error": str(e)}), 200


def _derivar_recetas_de_plan(plan: dict) -> list:
    """
    Deriva recetas a partir de un plan importado por IA cuando el parser
    determinístico no extrajo recetas. Crea una receta por cada comida
    (deduplicada por nombre + ingredientes), sumando los macros de sus items.
    Garantiza que la importación poble la biblioteca de recetas aunque el
    documento no tenga el formato tabular que reconoce el parser.
    """
    if not isinstance(plan, dict):
        return []

    def _num(v):
        try:
            return float(v)
        except (TypeError, ValueError):
            return 0.0

    vistas: set = set()
    recetas: list = []

    for semana in plan.get("semanas", []) or []:
        dias = semana.get("dias", []) if isinstance(semana, dict) else []
        for dia in dias or []:
            comidas = dia.get("comidas", []) if isinstance(dia, dict) else []
            for comida in comidas or []:
                if not isinstance(comida, dict):
                    continue
                items = comida.get("items", []) or []
                if not items:
                    continue
                nombre = (comida.get("nombre") or "Comida").strip() or "Comida"

                ingredientes, cal, prot, carb, fat = [], 0.0, 0.0, 0.0, 0.0
                for it in items:
                    if not isinstance(it, dict):
                        continue
                    ali = (it.get("nombre_alimento") or "").strip()
                    if not ali:
                        continue
                    cant = str(it.get("cantidad") or "").strip()
                    uni = (it.get("unidad") or "").strip()
                    ingredientes.append(" ".join(x for x in [cant, uni, ali] if x).strip())
                    cal += _num(it.get("calorias"))
                    prot += _num(it.get("proteinas_g"))
                    carb += _num(it.get("carbohidratos_g"))
                    fat += _num(it.get("grasas_g"))

                if not ingredientes:
                    continue

                firma = (nombre.lower(), tuple(sorted(i.lower() for i in ingredientes)))
                if firma in vistas:
                    continue
                vistas.add(firma)

                recetas.append({
                    "nombre":          nombre,
                    "descripcion":     f"Receta extraída del plan importado por IA",
                    "calorias":        round(cal) or None,
                    "proteinas_g":     round(prot, 1) or None,
                    "carbohidratos_g": round(carb, 1) or None,
                    "grasas_g":        round(fat, 1) or None,
                    "ingredientes":    ingredientes,
                    "fuente":          "ia_import",
                })
                if len(recetas) >= 40:
                    return recetas
    return recetas


@diet_bp.route("/diets/import-ai", methods=["POST"])
@jwt_required()
@require_tenant
def import_diet_ai():
    """
    ETL en dos fases:
      1. Parser determinístico (parse_diet_plan_from_pdf) para PDFs tabulares conocidos.
         Si tiene éxito devuelve { plan, recetas } sin invocar Ollama.
      2. Fallback LLM local (Ollama) para formatos no estructurados.
         En este caso devuelve { plan, recetas: [] }.

    El frontend muestra una vista previa y llama a /confirm-import para persistir.
    """
    try:
        from app.utils.etl_ollama import (  # noqa: PLC0415
            check_ollama_ready, extract_text, call_ollama,
            parse_diet_plan_from_pdf,
        )

        archivo = request.files.get("archivo")
        if not archivo:
            return jsonify({"error": "No se recibió archivo"}), 400

        nombre_archivo = archivo.filename or ""
        ext = nombre_archivo.rsplit(".", 1)[-1].lower()
        if ext not in {"pdf", "xlsx", "xls"}:
            return jsonify({
                "error": "Formato no soportado",
                "detalle": "Usa un archivo PDF (.pdf) o Excel (.xlsx / .xls)",
            }), 400

        contenido = archivo.read()

        # ── Fase 1: parser determinístico (PDF tabular) ───────────────────────
        if ext == "pdf":
            resultado = parse_diet_plan_from_pdf(contenido)
            if resultado:
                return jsonify({
                    "success":  True,
                    "plan":     resultado["plan"],
                    "recetas":  resultado["recetas"],
                    "archivo":  nombre_archivo,
                    "via":      "parser",
                }), 200

        # ── Fase 2: fallback LLM local ────────────────────────────────────────
        try:
            raw_text = extract_text(contenido, ext)
        except Exception as e:
            return jsonify({"error": f"Error leyendo el archivo: {e}"}), 400

        if not raw_text.strip():
            return jsonify({
                "error": "El archivo no contiene texto extraíble",
                "detalle": "Asegúrate de que el PDF no sea una imagen escaneada.",
            }), 422

        ready, msg = check_ollama_ready()
        if not ready:
            return jsonify({"error": "Servicio de IA no disponible", "detalle": msg}), 503

        respuesta_raw = call_ollama(_ETL_SYSTEM_PROMPT, raw_text[:4_000])

        texto = respuesta_raw.strip()
        if texto.startswith("```"):
            lineas = texto.split("\n")
            texto  = "\n".join(lineas[1:] if len(lineas) > 1 else lineas)
        if texto.endswith("```"):
            texto = texto[: texto.rfind("```")].strip()

        try:
            plan = json.loads(texto)
        except json.JSONDecodeError:
            return jsonify({
                "error": "La IA no pudo estructurar el plan en formato JSON.",
                "detalle": texto[:300],
            }), 422

        # El parser no extrajo recetas; derivarlas del plan para poblar la
        # biblioteca también cuando la importación pasa por el LLM.
        recetas_ia = _derivar_recetas_de_plan(plan)
        return jsonify({
            "success": True,
            "plan":    plan,
            "recetas": recetas_ia,
            "via":     "llm",
        }), 200

    except ImportError as exc:
        return jsonify({"error": f"Dependencia faltante: {exc}"}), 500
    except Exception as exc:
        import traceback
        return jsonify({"error": str(exc), "trace": traceback.format_exc()[-500:]}), 500


# ─── Confirmar importación: persistir plan + recetas ─────────────────────────

@diet_bp.route("/diets/confirm-import", methods=["POST"])
@jwt_required()
@require_tenant
def confirm_diet_import():
    """
    Persiste el plan y las recetas extraídas por el parser/IA.

    Body JSON:
      plan          dict    Plan v2 completo
      recetas       list    Recetas a crear en la biblioteca (pueden tener 'imagen' base64)
      id_miembro_pg int?    ID PostgreSQL del cliente a asignar
      nombre_plan   str?    Sobrescribe plan.nombre
      archivo       str?    Nombre del archivo origen (metadata)

    Returns:
      { success, diet_id, recetas_creadas }
    """
    trainer_id = int(get_jwt_identity())
    gym_id     = g.tenant_id
    data       = request.get_json(silent=True) or {}

    plan          = data.get("plan") or {}
    recetas_in    = data.get("recetas") or []
    # Si el frontend no envió recetas (p. ej. importación vía LLM), derivarlas
    # del plan para poblar siempre la biblioteca de recetas individuales.
    if not recetas_in:
        recetas_in = _derivar_recetas_de_plan(plan)
    id_miembro_pg = data.get("id_miembro_pg")
    nombre_plan   = data.get("nombre_plan")
    archivo       = data.get("archivo")

    if not plan:
        return jsonify({"error": "Plan vacío"}), 400

    db = get_db()

    # ── 1. Crear / deduplicar recetas ─────────────────────────────────────────
    recetas_creadas = 0
    for receta in recetas_in:
        nombre = (receta.get("nombre") or "").strip()
        if not nombre:
            continue

        existing = db.recetas.find_one({
            "nombre":            nombre,
            "id_entrenador_pg":  trainer_id,
            "id_gimnasio_pg":    gym_id,
        })
        if existing:
            # Si la receta existe pero estaba desactivada (soft-delete), reactivarla
            # para que vuelva a aparecer en la biblioteca en lugar de omitirla.
            if not existing.get("activo", True):
                db.recetas.update_one({"_id": existing["_id"]}, {"$set": {"activo": True}})
                recetas_creadas += 1
            continue

        doc = {
            "nombre":                 nombre,
            "descripcion":            receta.get("descripcion"),
            "calorias":               receta.get("calorias"),
            "proteinas_g":            receta.get("proteinas_g"),
            "carbohidratos_g":        receta.get("carbohidratos_g"),
            "grasas_g":               receta.get("grasas_g"),
            "ingredientes":           receta.get("ingredientes") or [],
            "instrucciones":          receta.get("instrucciones"),
            "tiempo_preparacion_min": receta.get("tiempo_preparacion_min"),
            "imagen":                 receta.get("imagen"),   # base64 data URI
            "fuente":                 receta.get("fuente", "pdf_import"),
            "id_entrenador_pg":       trainer_id,
            "id_gimnasio_pg":         gym_id,
            "activo":                 True,
            "created_at":             datetime.now(timezone.utc),
        }
        db.recetas.insert_one(doc)
        recetas_creadas += 1

    # ── 2. Resolver id_miembro (ObjectId) desde pg_id ─────────────────────────
    id_miembro_oid = None
    if id_miembro_pg is not None:
        try:
            pg_int = int(id_miembro_pg)
            member = db.users.find_one(
                {"pg_id": pg_int, "id_gimnasio_pg": gym_id},
                {"_id": 1},
            )
            if member:
                id_miembro_oid = str(member["_id"])
        except (ValueError, TypeError):
            pass

    # ── 3. Guardar plan ───────────────────────────────────────────────────────
    if nombre_plan:
        plan["nombre"] = nombre_plan

    plan.update({
        "id_entrenador_pg": trainer_id,
        "id_gimnasio_pg":   gym_id,
        "id_miembro_pg":    int(id_miembro_pg) if id_miembro_pg is not None else None,
        "id_miembro":       id_miembro_oid,
        "fuente":           plan.get("fuente", "ia_import"),
        "archivo_origen":   archivo,
        "eliminada":        False,
        "fecha_creacion":   datetime.now(timezone.utc),
    })

    result  = db.dietas.insert_one(plan)
    diet_id = str(result.inserted_id)

    return jsonify({
        "success":         True,
        "diet_id":         diet_id,
        "recetas_creadas": recetas_creadas,
    }), 201
