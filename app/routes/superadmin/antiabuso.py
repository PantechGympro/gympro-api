"""
superadmin/antiabuso.py — Revisar y corregir los bloqueos de alta.

Existe porque un control automático sin palanca de excepción no es un
control, es una trampa: el día que la regla frene a un cliente real, alguien
tiene que poder verlo y autorizarlo sin tocar la base de datos a mano.

Rutas (todas bajo /api/superadmin, solo superadmin):
    GET  /antiabuso/intentos            ultimos intentos, filtrables
    POST /antiabuso/permitir            autoriza una identidad a registrarse
    DELETE /antiabuso/permitir          retira esa autorizacion
"""
from flask import Blueprint, jsonify, request
from flask_jwt_extended import jwt_required

from app.extensions import db
from app.models.pg.intento_alta_gimnasio import IntentoAltaGimnasio
from app.utils.antiabuso import normalizar_correo, normalizar_telefono
from app.utils.security import require_role

antiabuso_admin_bp = Blueprint("antiabuso_admin", __name__)

_LIMITE_MAX = 200


@antiabuso_admin_bp.route("/antiabuso/intentos", methods=["GET"])
@jwt_required()
@require_role("superadmin")
def listar_intentos():
    """
    Últimos intentos de alta. Por defecto muestra TODO, no solo los bloqueos:
    para juzgar si una regla está pasada de estricta hay que ver también lo
    que sí entró.

    Query: ?resultado=bloqueado|creado|excepcion  ?correo=  ?limite=50
    """
    consulta = IntentoAltaGimnasio.query

    resultado = (request.args.get("resultado") or "").strip()
    if resultado:
        consulta = consulta.filter(IntentoAltaGimnasio.resultado == resultado)

    correo = (request.args.get("correo") or "").strip()
    if correo:
        consulta = consulta.filter(
            IntentoAltaGimnasio.correo_normalizado == normalizar_correo(correo)
        )

    try:
        limite = min(int(request.args.get("limite", 50)), _LIMITE_MAX)
    except (TypeError, ValueError):
        limite = 50

    intentos = (consulta.order_by(IntentoAltaGimnasio.created_at.desc())
                .limit(limite).all())

    # Conteo por motivo: de un vistazo se ve si una regla está disparando de
    # más, que es lo que hay que notar a tiempo.
    por_motivo = dict(
        db.session.query(IntentoAltaGimnasio.motivo, db.func.count(IntentoAltaGimnasio.id))
        .filter(IntentoAltaGimnasio.resultado == "bloqueado")
        .group_by(IntentoAltaGimnasio.motivo).all()
    )

    return jsonify({
        "intentos": [i.to_dict() for i in intentos],
        "bloqueos_por_motivo": por_motivo,
    }), 200


@antiabuso_admin_bp.route("/antiabuso/permitir", methods=["POST"])
@jwt_required()
@require_role("superadmin")
def permitir_identidad():
    """
    Autoriza a una identidad a registrar un gimnasio aunque las reglas la
    frenen. Es la salida para el falso positivo: dos negocios distintos del
    mismo dueño, una cuenta que se dio de baja por error, un cliente que
    vuelve.

    Body: { "correo": "...", "telefono": "..." }  (al menos el correo)
    """
    data = request.get_json() or {}
    correo = normalizar_correo(data.get("correo") or "")
    if not correo or "@" not in correo:
        return jsonify({"msg": "Hace falta un correo válido"}), 400
    telefono = normalizar_telefono(data.get("telefono"))

    ya = IntentoAltaGimnasio.query.filter_by(
        correo_normalizado=correo, resultado="excepcion"
    ).first()
    if ya is not None:
        return jsonify({"msg": "Esa identidad ya estaba autorizada",
                        "intento": ya.to_dict()}), 200

    excepcion = IntentoAltaGimnasio(
        correo_normalizado=correo,
        telefono_normalizado=telefono,
        resultado="excepcion",
        motivo="autorizado_superadmin",
    )
    db.session.add(excepcion)
    db.session.commit()
    return jsonify({"msg": "Identidad autorizada para registrar un gimnasio",
                    "intento": excepcion.to_dict()}), 201


@antiabuso_admin_bp.route("/antiabuso/permitir", methods=["DELETE"])
@jwt_required()
@require_role("superadmin")
def retirar_autorizacion():
    """Quita la autorización manual de una identidad. Body: { "correo": "..." }"""
    data = request.get_json() or {}
    correo = normalizar_correo(data.get("correo") or "")
    if not correo:
        return jsonify({"msg": "Hace falta el correo"}), 400

    borradas = IntentoAltaGimnasio.query.filter_by(
        correo_normalizado=correo, resultado="excepcion"
    ).delete(synchronize_session=False)
    db.session.commit()
    return jsonify({"msg": f"{borradas} autorización(es) retirada(s)"}), 200
