from flask import Blueprint, request, jsonify, g
from flask_jwt_extended import jwt_required, get_jwt_identity
from datetime import datetime, timedelta
from bson.objectid import ObjectId
from app.mongo import get_db
from app.models.pg.tipo_membresia import TipoMembresia
from app.utils.tenant import require_tenant

user_membership_bp = Blueprint('user_membership', __name__)


@user_membership_bp.route('/api/user/membership', methods=['GET'])
@jwt_required()
@require_tenant
def get_user_membership():
    """Obtiene la membresía activa del miembro autenticado."""
    try:
        mdb        = get_db()
        user_pg_id = int(get_jwt_identity())
        gym_id     = g.tenant_id

        # Buscar miembro por id_usuario_pg (campo usado desde Sprint 2)
        miembro = mdb.miembros.find_one({
            "id_usuario_pg": user_pg_id,
            "id_gimnasio_pg": gym_id
        })
        if not miembro:
            return jsonify({"error": "Miembro no encontrado"}), 404

        membresia_activa = mdb.miembro_membresia.find_one({
            "id_miembro": miembro["_id"],
            "estado":     "Activa"
        })
        if not membresia_activa:
            return jsonify({"tieneMembresia": False, "mensaje": "No tienes una membresía activa"}), 200

        # El id_membresia puede ser ObjectId (legacy Mongo) o int (PG después de US14)
        id_mem = membresia_activa.get("id_membresia")
        nombre_mem = "N/A"
        precio_mem = 0.0

        if isinstance(id_mem, int):
            # PG TipoMembresia
            tm = TipoMembresia.query.get(id_mem)
            if tm:
                nombre_mem = tm.nombre
                precio_mem = float(tm.precio)
        elif isinstance(id_mem, ObjectId):
            # Legacy Mongo
            legacy = mdb.membresias.find_one({"_id": id_mem})
            if legacy:
                nombre_mem = legacy.get("nombre", "N/A")
                precio_mem = float(legacy.get("precio", 0))

        fecha_fin = membresia_activa.get("fecha_fin")
        if isinstance(fecha_fin, str):
            fecha_fin = datetime.strptime(fecha_fin[:10], "%Y-%m-%d").date()
        elif isinstance(fecha_fin, datetime):
            fecha_fin = fecha_fin.date()

        dias_restantes = (fecha_fin - datetime.now().date()).days

        return jsonify({
            "tieneMembresia": True,
            "membresia": {
                "id":           str(membresia_activa["_id"]),
                "nombre":       nombre_mem,
                "fechaInicio":  membresia_activa.get("fecha_inicio").strftime('%Y-%m-%d')
                                if isinstance(membresia_activa.get("fecha_inicio"), datetime)
                                else str(membresia_activa.get("fecha_inicio")),
                "fechaFin":     fecha_fin.strftime('%Y-%m-%d'),
                "diasRestantes":dias_restantes,
                "estado":       "activa" if dias_restantes > 0 else "por_vencer",
                "precio":       precio_mem
            }
        }), 200

    except Exception as e:
        import traceback; traceback.print_exc()
        return jsonify({"error": str(e)}), 500


@user_membership_bp.route('/api/user/membership/plans', methods=['GET'])
@jwt_required()
@require_tenant
def get_available_plans():
    """Devuelve los planes disponibles del gimnasio (PG TipoMembresia)."""
    try:
        gym_id = g.tenant_id

        tipos = TipoMembresia.query.filter_by(id_gimnasio=gym_id, activo=True).all()

        # Precio base mensual para calcular ahorro
        precio_mensual = 950.0
        for t in tipos:
            if t.duracion_meses == 1:
                precio_mensual = float(t.precio)
                break

        planes = []
        for tm in tipos:
            duracion = tm.duracion_meses or 1
            precio   = float(tm.precio)
            ahorro   = max(0.0, (precio_mensual * duracion) - precio) if duracion > 1 else 0.0

            plan_id = "monthly" if duracion == 1 else "quarterly" if duracion == 3 else "annual"

            # Las promociones vencidas no se ofrecen al miembro
            if getattr(tm, "caducada", False):
                continue

            planes.append({
                "id":            plan_id,
                "id_membresia":  tm.id,         # entero PG — lo esperamos en /renew
                "nombre":        tm.nombre,
                "duracion_meses":duracion,
                "precio":        precio,
                "ahorro":        ahorro,
                # Información comercial para que el miembro compare planes
                "tipo":          tm.tipo or "estandar",
                "descripcion":   tm.descripcion,
                "beneficios":    tm.beneficios or [],
                "es_combo":      bool(tm.es_combo),
                "items_combo":   tm.items_combo or [],
                "fecha_fin_promo": tm.fecha_fin_promo.isoformat() if tm.fecha_fin_promo else None,
                "dias_restantes_promo": tm.dias_restantes_promo,
            })

        return jsonify({"planes": planes}), 200

    except Exception as e:
        import traceback; traceback.print_exc()
        return jsonify({"error": str(e)}), 500


@user_membership_bp.route('/api/user/membership/renew', methods=['POST'])
@jwt_required()
@require_tenant
def renew_membership():
    """Procesa la renovación de membresía usando TipoMembresia de PG."""
    try:
        mdb        = get_db()
        user_pg_id = int(get_jwt_identity())
        gym_id     = g.tenant_id
        data       = request.json or {}

        miembro = mdb.miembros.find_one({
            "id_usuario_pg":  user_pg_id,
            "id_gimnasio_pg": gym_id
        })
        if not miembro:
            return jsonify({"error": "Miembro no encontrado"}), 404

        id_membresia_val = data.get('id_membresia')
        metodo_pago      = data.get('metodo_pago', 'Tarjeta')

        if not id_membresia_val:
            return jsonify({"error": "ID de membresía requerido"}), 400

        metodos_validos = ['Efectivo', 'Tarjeta', 'Transferencia']
        if metodo_pago not in metodos_validos:
            return jsonify({"error": f"Método de pago inválido. Usar: {', '.join(metodos_validos)}"}), 400

        # id_membresia puede llegar como entero (PG) o string de entero
        try:
            tm = TipoMembresia.query.filter_by(
                id=int(id_membresia_val),
                id_gimnasio=gym_id,
                activo=True
            ).first()
        except (ValueError, TypeError):
            tm = None

        if not tm:
            return jsonify({"error": "Membresía no encontrada"}), 404

        membresia_activa = mdb.miembro_membresia.find_one({
            "id_miembro": miembro["_id"],
            "estado":     "Activa"
        })

        fecha_inicio = datetime.now()
        if membresia_activa:
            f_fin_activa = membresia_activa.get("fecha_fin")
            if isinstance(f_fin_activa, str):
                f_fin_activa = datetime.strptime(f_fin_activa[:10], "%Y-%m-%d")
            if isinstance(f_fin_activa, datetime) and f_fin_activa > fecha_inicio:
                fecha_inicio = f_fin_activa + timedelta(days=1)

            mdb.miembro_membresia.update_one(
                {"_id": membresia_activa["_id"]},
                {"$set": {"estado": "Vencida"}}
            )

        duracion  = tm.duracion_meses or 1
        fecha_fin = fecha_inicio + timedelta(days=duracion * 30)

        mdb.miembro_membresia.insert_one({
            "id_miembro":   miembro["_id"],
            "id_membresia": tm.id,          # entero PG
            "fecha_inicio": fecha_inicio,
            "fecha_fin":    fecha_fin,
            "estado":       "Activa"
        })

        mdb.pagos.insert_one({
            "id_miembro":   miembro["_id"],
            "id_gimnasio":  gym_id,
            "monto":        float(tm.precio),
            "metodo_pago":  metodo_pago,
            "concepto":     f"Renovación {tm.nombre}",
            "fecha_pago":   datetime.now()
        })

        return jsonify({
            "message": "Membresía renovada exitosamente",
            "membresia": {
                "nombre":      tm.nombre,
                "fechaInicio": fecha_inicio.strftime('%Y-%m-%d'),
                "fechaFin":    fecha_fin.strftime('%Y-%m-%d'),
                "monto":       float(tm.precio)
            }
        }), 201

    except Exception as e:
        import traceback; traceback.print_exc()
        return jsonify({"error": str(e)}), 500


@user_membership_bp.route('/api/user/membership/payment-methods', methods=['GET'])
@jwt_required()
@require_tenant
def get_payment_methods():
    """Devuelve los métodos de pago usados recientemente por el miembro."""
    try:
        mdb        = get_db()
        user_pg_id = int(get_jwt_identity())
        gym_id     = g.tenant_id

        miembro = mdb.miembros.find_one({
            "id_usuario_pg":  user_pg_id,
            "id_gimnasio_pg": gym_id
        })
        if not miembro:
            return jsonify({"error": "Miembro no encontrado"}), 404

        pagos_recientes = list(
            mdb.pagos.find({"id_miembro": miembro["_id"]})
            .sort("fecha_pago", -1)
            .limit(5)
        )

        metodos_vistos = set()
        metodos = []
        for idx, pago in enumerate(pagos_recientes):
            metodo = pago.get("metodo_pago")
            if metodo and metodo not in metodos_vistos:
                metodos_vistos.add(metodo)
                numero_display = {
                    'Tarjeta':       "**** **** **** 4242",
                    'Transferencia': "Cuenta bancaria",
                }.get(metodo, metodo)
                metodos.append({
                    "id":        idx + 1,
                    "tipo":      metodo,
                    "numero":    numero_display,
                    "principal": len(metodos) == 0
                })

        return jsonify({"metodos": metodos}), 200

    except Exception as e:
        import traceback; traceback.print_exc()
        return jsonify({"error": str(e)}), 500
