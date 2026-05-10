from flask import Blueprint, jsonify
from flask_jwt_extended import jwt_required
from app.mongo import get_db

membresias_bp = Blueprint("membresias", __name__)

@membresias_bp.route("/api/membresias", methods=["GET"])
@jwt_required()
def listar_membresias():
    db = get_db()
    
    # Obtenemos todos los documentos de la colección
    membresias_cursor = db.membresias.find({})
    
    # Formateamos manualmente el ID para que el frontend lo entienda igual que antes
    resultados = []
    for m in membresias_cursor:
        resultados.append({
            "id_membresia": str(m["_id"]),
            "nombre": m.get("nombre"),
            "duracion_meses": m.get("duracion_meses", 0),
            "precio": float(m.get("precio", 0))
        })
        
    return jsonify(resultados), 200