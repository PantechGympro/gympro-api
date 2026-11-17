from flask import Blueprint, request, jsonify, g
from flask_jwt_extended import jwt_required, get_jwt_identity
from datetime import datetime, timezone
from bson.objectid import ObjectId
from app.mongo import get_db
from app.models.pg.tipo_membresia import TipoMembresia
from app.utils.tenant import require_tenant
from app.utils.recibos import registrar_evento_transaccion
from app.utils.pagos_pendientes import (
    METODOS_MANUALES_MIEMBRO_MEMBRESIA, PAGO_PENDIENTE, notificar_personal,
)

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
                "estado":       "vencida" if dias_restantes < 0 else ("por_vencer" if dias_restantes <= 7 else "activa"),
                "precio":       precio_mem
            }
        }), 200

    except Exception as e:
        import traceback; traceback.print_exc()
        return jsonify({"error": str(e)}), 500


@user_membership_bp.route('/api/user/membership/estado', methods=['GET'])
@jwt_required()
@require_tenant
def get_estado_membresia():
    """
    Estado resumido para que la app muestre avisos y bloquee módulos SIN depender
    de que falle otra llamada. Siempre disponible, aunque la membresía esté vencida
    (es justo la pantalla desde la que se renueva).

    { vigente, estado: activa|por_vencer|vencida|sin_membresia, codigo, nombre,
      fecha_fin_local, dias_restantes, vencio_hace_dias, mensaje, bloquea: [...] }
    """
    from app.utils.membresia import evaluar_membresia, mensaje_membresia
    mdb = get_db()
    miembro = mdb.miembros.find_one(
        {"id_usuario_pg": int(get_jwt_identity()), "id_gimnasio_pg": g.tenant_id}, {"_id": 1})
    if not miembro:
        return jsonify({"error": "No encontramos tu ficha de miembro en este gimnasio."}), 404
    est = evaluar_membresia(mdb, miembro["_id"])
    # Si ya pidió renovar con efectivo/transferencia y recepción aún no confirma,
    # la app debe mostrar "en revisión" y no ofrecer pagar otra vez.
    sol = mdb.renovaciones_pendientes.find_one(
        {"id_gimnasio": g.tenant_id, "id_miembro": miembro["_id"], "estado": "pendiente"})
    renovacion_pendiente = None
    if sol:
        renovacion_pendiente = {
            "id": str(sol["_id"]), "membresia_nombre": sol.get("membresia_nombre"),
            "monto": sol.get("monto"), "metodo_pago": sol.get("metodo_pago"),
            "referencia": sol.get("referencia"),
            "creado_en": sol["creado_en"].isoformat() if hasattr(sol.get("creado_en"), "isoformat") else None,
        }
    return jsonify({
        "renovacion_pendiente": renovacion_pendiente,
        "vigente":          est["vigente"],
        "estado":           est["estado"],
        "codigo":           est["codigo"],
        "nombre":           est["nombre"],
        "fecha_fin_local":  est["fecha_fin_local"],
        "dias_restantes":   est["dias_restantes"],
        "vencio_hace_dias": est["vencio_hace_dias"],
        "mensaje":          None if est["vigente"] else mensaje_membresia(est),
        # Módulos de la app que se restringen mientras no esté vigente.
        "bloquea":          [] if est["vigente"] else ["checkin", "rutinas", "entrenamiento", "nutricion", "chat"],
    }), 200


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
    """
    Solicita la renovación de membresía con un pago manual (Efectivo / Transferencia).

    NO activa nada: crea una solicitud pendiente (colección `renovaciones_pendientes`)
    que Recepcionista / Administrador / Propietario confirman en
    POST /api/confirmaciones-pago/renovaciones/<id>/confirmar. Con PayPal o Mercado
    Pago se usa /api/pagos/checkout, que activa por webhook.
    """
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
        metodo_pago      = data.get('metodo_pago')

        if not id_membresia_val:
            return jsonify({"error": "ID de membresía requerido"}), 400

        # Un miembro solo puede DECLARAR pagos manuales (efectivo / transferencia),
        # que quedan pendientes hasta que el personal los verifique. Los pagos con
        # tarjeta o billetera pasan por la pasarela (POST /api/pagos/checkout), cuyo
        # webhook es lo que activa la membresia. Antes esta ruta aceptaba "Tarjeta"
        # sin cobrar nada y activaba la membresia al instante.
        if metodo_pago not in METODOS_MANUALES_MIEMBRO_MEMBRESIA:
            return jsonify({
                "error": "Para pagar con tarjeta, PayPal o Mercado Pago usa el pago en línea. "
                         f"Aquí solo se registra: {', '.join(METODOS_MANUALES_MIEMBRO_MEMBRESIA)}.",
            }), 400

        # Transferencia: la referencia (folio o clave de rastreo) es obligatoria
        # -- es lo único que permite ubicar el movimiento en el estado de cuenta
        # del banco para que el personal la verifique.
        referencia = (data.get('referencia') or '').strip()
        if metodo_pago == 'Transferencia' and not referencia:
            return jsonify({"error": "Falta la referencia de la transferencia"}), 400

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

        # Una sola solicitud abierta por miembro: evita que, a base de reenviar,
        # acumule renovaciones que el personal luego tendría que depurar.
        abierta = mdb.renovaciones_pendientes.find_one({
            "id_gimnasio": gym_id, "id_miembro": miembro["_id"], "estado": PAGO_PENDIENTE,
        })
        if abierta:
            return jsonify({
                "error": "Ya tienes una renovación en revisión. Recepción la confirmará en cuanto verifique tu pago.",
                "codigo": "renovacion_en_revision",
                "id": str(abierta["_id"]),
            }), 409

        ahora = datetime.now(timezone.utc)
        solicitud = {
            "id_gimnasio":    gym_id,
            "id_miembro":     miembro["_id"],
            "id_miembro_pg":  user_pg_id,
            "nombre_miembro": miembro.get("nombre", "") or "",
            "email_miembro":  miembro.get("email"),
            "id_membresia":   tm.id,
            "membresia_nombre": tm.nombre,
            # Se guarda lo que el miembro VIO al pedir: si el gimnasio cambia el
            # precio mientras se revisa, se confirma el trato original.
            "duracion_meses": tm.duracion_meses or 1,
            "monto":          float(tm.precio),
            "metodo_pago":    metodo_pago,
            "referencia":     referencia or None,
            "estado":         PAGO_PENDIENTE,
            "creado_en":      ahora,
        }
        res = mdb.renovaciones_pendientes.insert_one(solicitud)
        solicitud_id = str(res.inserted_id)

        if metodo_pago == "Efectivo":
            detalle_pago = f"Pagará ${float(tm.precio):.2f} en efectivo en recepción."
        else:
            detalle_pago = f"Transferencia por ${float(tm.precio):.2f}, referencia {referencia}."
        notificar_personal(
            mdb, gym_id, "renovacion_por_confirmar", "Renovación por confirmar",
            f"{solicitud['nombre_miembro'] or 'Un miembro'} pidió renovar «{tm.nombre}». {detalle_pago} "
            f"Verifica el pago y confírmalo para activar la membresía.",
            "renovacion_pendiente", solicitud_id,
        )

        registrar_evento_transaccion(
            "membresia.renovacion_solicitada",
            id_usuario_actor=user_pg_id, id_gimnasio=gym_id,
            detalle={
                "solicitud_id": solicitud_id, "id_membresia": tm.id, "membresia_nombre": tm.nombre,
                "monto": float(tm.precio), "metodo_pago": metodo_pago,
                "id_miembro": str(miembro["_id"]),
            },
        )

        # 202: aceptada pero NO aplicada. Los clientes deben mostrar "en revisión",
        # nunca "renovada".
        return jsonify({
            "message": "Solicitud enviada. Tu membresía se activará cuando recepción confirme tu pago.",
            "estado": PAGO_PENDIENTE,
            "id": solicitud_id,
            "membresia": {
                "nombre": tm.nombre,
                "monto":  float(tm.precio),
                "metodo_pago": metodo_pago,
            },
        }), 202

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
