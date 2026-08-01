"""
owner_gym/owner_profile.py — Perfil del propietario y de su gimnasio.

Endpoints:
    GET  /api/owner_gym/perfil            Datos del gimnasio + bloque propietario
    PUT  /api/owner_gym/perfil            Actualizar datos del gimnasio
    PUT  /api/owner_gym/perfil/propietario  Actualizar los datos de la persona

Son dos cosas distintas y por eso dos endpoints: el gimnasio es el negocio
(nombre comercial, contacto público, tipo) y el propietario es la persona que
lo administra (su nombre, su correo de acceso, su teléfono, su foto).
"""
from flask import Blueprint, jsonify, request, g
from flask_jwt_extended import jwt_required, get_jwt_identity

from app.extensions import db
from app.models.pg.gimnasio import Gimnasio
from app.models.pg.usuario import Usuario
from app.utils.tenant import require_tenant
from app.utils.security import require_role

owner_profile_bp = Blueprint("owner_profile", __name__)


@owner_profile_bp.route("/perfil", methods=["GET"])
@jwt_required()
@require_role("owner_gym")
@require_tenant
def get_perfil():
    """
    Datos del gimnasio y de su propietario.

    La app tiene dos pantallas distintas alimentadas por este endpoint:
    'Mi Perfil' (la persona) y 'Perfil del Gym' (el negocio). Antes solo se
    devolvían el nombre y la foto del dueño, así que 'Mi Perfil' terminaba
    mostrando los datos del gimnasio. Ahora el propietario viaja completo en
    su propio bloque y el tipo de gimnasio incluye su etiqueta legible.
    """
    from app.utils.gym_types import GYM_TYPES

    gym = Gimnasio.query.get(g.tenant_id)
    if not gym:
        return jsonify({"msg": "Gimnasio no encontrado"}), 404

    data = gym.to_dict()

    # Etiqueta legible del tipo: la interfaz no debe mostrar 'gimnasio_tradicional'.
    tipo = data.get("tipo_gimnasio") or "gimnasio_tradicional"
    data["tipo_gimnasio_label"] = GYM_TYPES.get(tipo, {}).get("label", "Gimnasio Tradicional")

    # Catálogo de tipos válidos, para que la app ofrezca una lista y no un
    # campo de texto libre donde se puede escribir cualquier cosa.
    data["tipos_disponibles"] = [
        {"value": clave, "label": conf.get("label", clave)}
        for clave, conf in GYM_TYPES.items()
    ]

    # ── Propietario ───────────────────────────────────────────────────────────
    try:
        owner = Usuario.query.get(int(get_jwt_identity()))
    except Exception:
        owner = None

    if owner:
        fp = getattr(owner, "foto_perfil", None)
        data["propietario"] = {
            "id":          owner.id,
            "nombre":      owner.nombre,
            "email":       owner.email,
            "telefono":    getattr(owner, "telefono", None),
            "rol":         owner.rol.nombre if getattr(owner, "rol", None) else "Owner",
            "activo":      owner.activo,
            "foto_perfil": fp if (fp and fp.startswith("data:image")) else None,
            "created_at":  owner.created_at.isoformat() if owner.created_at else None,
        }
    else:
        data["propietario"] = None

    # Se conservan por compatibilidad con pantallas que aún los leen.
    data["owner_foto"]   = (data["propietario"] or {}).get("foto_perfil")
    data["owner_nombre"] = (data["propietario"] or {}).get("nombre")

    return jsonify(data), 200


@owner_profile_bp.route("/perfil", methods=["PUT"])
@jwt_required()
@require_role("owner_gym")
@require_tenant
def update_perfil():
    """
    Actualiza los datos editables del gimnasio.
    Body JSON (todos opcionales):
        {
            "nombre":         "Mi Gym",
            "email_contacto": "gym@mail.com",
            "telefono":       "555-1234",
            "tipo_gimnasio":  "crossfit"
        }
    """
    gym = Gimnasio.query.get(g.tenant_id)
    if not gym:
        return jsonify({"msg": "Gimnasio no encontrado"}), 404

    from app.utils.gym_types import GYM_TYPES

    data = request.get_json() or {}

    # El tipo debe pertenecer al catálogo del SaaS: de él dependen las etiquetas
    # y los módulos que se activan, así que un valor libre dejaría al gimnasio
    # con una configuración inexistente.
    if "tipo_gimnasio" in data:
        tipo = (data.get("tipo_gimnasio") or "").strip()
        if tipo and tipo not in GYM_TYPES:
            return jsonify({
                "msg": "Tipo de gimnasio no válido",
                "tipos_validos": list(GYM_TYPES.keys()),
            }), 400

    EDITABLES = ["nombre", "email_contacto", "telefono", "tipo_gimnasio"]
    updated = []
    for field in EDITABLES:
        if field in data:
            setattr(gym, field, data[field])
            updated.append(field)

    # El logotipo pasa por la misma validación que las fotos de perfil.
    if "logo" in data:
        logo, error = _foto_valida(data.get("logo"))
        if error:
            return jsonify({"msg": error}), 400
        gym.logo = logo
        updated.append("logo")

    if not updated:
        return jsonify({"msg": "Sin campos para actualizar"}), 400

    db.session.commit()

    salida = gym.to_dict()
    salida["tipo_gimnasio_label"] = GYM_TYPES.get(
        salida.get("tipo_gimnasio") or "", {}
    ).get("label", "Gimnasio Tradicional")
    return jsonify({"msg": "Perfil actualizado", **salida}), 200


# ─────────────────────────────────────────────────────────────────────────────
# PERFIL DE LA PERSONA
# ─────────────────────────────────────────────────────────────────────────────

def _foto_valida(valor):
    """
    Acepta la foto solo si es una data URL de imagen y no excede ~2 MB.

    Las imágenes se guardan como base64 en la propia fila, así que un archivo
    grande hincha cada consulta que lea el usuario. El límite deja pasar
    holgadamente una foto de perfil recortada.
    """
    if valor in (None, ""):
        return None, None                     # se interpreta como "quitar la foto"
    if not isinstance(valor, str) or not valor.startswith("data:image"):
        return None, "La imagen no tiene un formato válido"
    if len(valor) > 2_800_000:                # ~2 MB reales tras base64
        return None, "La imagen es demasiado grande (máximo 2 MB)"
    return valor, None


@owner_profile_bp.route("/perfil/propietario", methods=["PUT"])
@jwt_required()
@require_role("owner_gym")
@require_tenant
def update_propietario():
    """
    Actualiza los datos de la persona propietaria.

    Body JSON (todos opcionales):
        { "nombre": str, "email": str, "telefono": str, "foto_perfil": data-url }

    El correo es la credencial de acceso, así que se comprueba que no lo tenga
    ya otra cuenta antes de cambiarlo.
    """
    try:
        owner = Usuario.query.get(int(get_jwt_identity()))
    except (TypeError, ValueError):
        owner = None
    if not owner:
        return jsonify({"msg": "Usuario no encontrado"}), 404

    data = request.get_json() or {}
    cambios = []

    if "nombre" in data:
        nombre = (data.get("nombre") or "").strip()
        if not nombre:
            return jsonify({"msg": "El nombre no puede quedar vacío"}), 400
        owner.nombre = nombre
        cambios.append("nombre")

    if "email" in data:
        email = (data.get("email") or "").strip().lower()
        if not email or "@" not in email:
            return jsonify({"msg": "El correo no es válido"}), 400
        if email != owner.email:
            ocupado = Usuario.query.filter(
                Usuario.email == email, Usuario.id != owner.id
            ).first()
            if ocupado:
                return jsonify({"msg": "Ese correo ya está registrado"}), 409
            owner.email = email
            cambios.append("email")

    if "telefono" in data:
        owner.telefono = (data.get("telefono") or "").strip() or None
        cambios.append("telefono")

    if "foto_perfil" in data:
        foto, error = _foto_valida(data.get("foto_perfil"))
        if error:
            return jsonify({"msg": error}), 400
        owner.foto_perfil = foto
        cambios.append("foto_perfil")

    if not cambios:
        return jsonify({"msg": "Sin campos para actualizar"}), 400

    db.session.commit()
    return jsonify({"msg": "Perfil actualizado", "propietario": owner.to_dict()}), 200
