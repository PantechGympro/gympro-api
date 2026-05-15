from flask import Blueprint, request, jsonify
from flask_jwt_extended import jwt_required
from datetime import date
from dateutil.relativedelta import relativedelta 
import math

from app.mongo import get_db
from app.models.pago import Pago
from app.models.membresia import Membresia
from app.models.miembro_membresia import MiembroMembresia
from app.models.miembro import Miembro
from app.utils.luhn import validar_luhn
from app.utils.tenant import get_tenant_filter

pagos_bp = Blueprint("pagos", __name__)

@pagos_bp.route("/api/pagos", methods=["POST"])
@jwt_required()
def registrar_pago():
    try:
        db = get_db()
        data = request.json

        # 1. Validaciones básicas
        required = ["id_miembro", "id_membresia", "metodo_pago"]
        for field in required:
            if field not in data:
                return jsonify({"error": f"Falta el campo {field}"}), 400

        # 2. Validar Membresía
        membresia = Membresia.find_by_id(data["id_membresia"])
        if not membresia:
            return jsonify({"error": "Membresía no válida"}), 404

        # 3. Validar Miembro
        miembro = Miembro.find_by_id(data["id_miembro"])
        if not miembro:
            return jsonify({"error": "Miembro no encontrado"}), 404

        # 4. Validar tarjeta
        if data["metodo_pago"] == "Tarjeta":
            tarjeta = data.get("numero_tarjeta", "")
            if not validar_luhn(tarjeta):
                return jsonify({"error": "Número de tarjeta inválido"}), 400

        # 5. Crear el Pago
        pago = Pago(
            id_miembro=miembro._id,
            monto=membresia.precio,
            metodo_pago=data["metodo_pago"],
            concepto=f"Pago membresía {membresia.nombre}"
        )
        pago.save()

        # 6. Calcular fechas
        inicio = date.today()
        # Manejo seguro si duracion_meses viene como string
        duracion = int(membresia.duracion_meses)
        fin = inicio + relativedelta(months=duracion)

        # 7. Crear la relación Miembro-Membresía
        # Usamos string format para MongoDB
        mm = MiembroMembresia(
            id_miembro=miembro._id,
            id_membresia=membresia._id,
            fecha_inicio=inicio.strftime('%Y-%m-%d'),
            fecha_fin=fin.strftime('%Y-%m-%d'),
            estado="Activa"
        )
        mm.save()
        
        return jsonify(pago.to_dict()), 201

    except Exception as e:
        print(f"Error en registrar_pago: {str(e)}") 
        return jsonify({"error": "Error interno del servidor", "detalle": str(e)}), 500

@pagos_bp.route("/api/pagos", methods=["GET"])
@jwt_required()
def listar_pagos():
    try:
        db            = get_db()
        tenant_filter = get_tenant_filter()
        page          = request.args.get("page", 1, type=int)
        per_page      = 6
        skip          = (page - 1) * per_page

        filtro = {}
        if tenant_filter:
            filtro["id_gimnasio"] = tenant_filter["id_gimnasio"]

        total_pagos  = db.pagos.count_documents(filtro)
        pagos_cursor = db.pagos.find(filtro).sort("fecha_pago", -1).skip(skip).limit(per_page)
        pages        = math.ceil(total_pagos / per_page) if total_pagos > 0 else 0

        pagos_lista = []
        for p_data in pagos_cursor:
            p        = Pago(**p_data)
            dict_data = p.to_dict()
            dict_data["id_pago"] = str(p_data["_id"])
            pagos_lista.append(dict_data)

        return jsonify({
            "pagos": pagos_lista,
            "total": total_pagos,
            "pages": pages,
            "page":  page,
        }), 200
    except Exception as e:
        print(f"Error listando pagos: {e}")
        return jsonify({"pagos": [], "total": 0}), 500