"""
owner_gym/perfil_publico.py -- Fotos y testimonios que el gimnasio muestra
en la pagina comercial publica (ver routes/publico/landing.py).

Dos recursos independientes, mismo criterio de acceso en los dos
(Propietario y Administrador de la sucursal, igual que "Perfil del Gym"
en owner_profile.py -- a diferencia de pasarelas.py/HU-50, aqui no hay
dinero de por medio, asi que no se restringe a solo Propietario):

  - Imagenes: fotos del local. Se guardan en disco (no en la fila, a
    diferencia de Gimnasio.logo que es base64) porque puede haber varias
    y pesan mas. Carpeta PROPIA (gimnasios_publico/), separada del resto
    de /app/storage/uploads/, porque estas fotos se sirven SIN sesion
    (/api/uploads/publico/<archivo>, ver app/__init__.py) -- nunca deben
    mezclarse con fotos de perfil o certificados, que siguen siendo
    privados.

  - Testimonios: texto corto + calificacion opcional. Aparecen en la
    landing en cuanto se guardan, sin moderacion todavia (ver nota en el
    modelo Testimonio) -- por eso el DELETE existe: es la unica forma de
    retirar uno que ya no se quiera mostrar.
"""
import os
from datetime import datetime, timezone

from flask import Blueprint, request, jsonify, g
from flask_jwt_extended import jwt_required, get_jwt_identity
from werkzeug.utils import secure_filename

from app.extensions import db
from app.models.pg.gimnasio_imagen import GimnasioImagen
from app.models.pg.testimonio import Testimonio
from app.models.pg.usuario import Usuario
from app.utils.tenant import require_tenant
from app.utils.security import require_role
from app.constants.roles import ROL_PROPIETARIO, ROL_ADMINISTRADOR

perfil_publico_bp = Blueprint("perfil_publico", __name__)

ALLOWED_EXTENSIONS = {"png", "jpg", "jpeg", "gif", "webp"}
MAX_IMAGENES_POR_GIMNASIO = 4
MAX_BYTES_POR_IMAGEN      = 15 * 1024 * 1024  # 15 MB: fotos de celular pueden pesar mas que 5 MB
UPLOAD_DIR = "/app/storage/uploads/gimnasios_publico"


def _allowed_file(filename):
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS


# ─────────────────────────────────────────────────────────────────────────────
# Imágenes
# ─────────────────────────────────────────────────────────────────────────────

@perfil_publico_bp.route("/perfil-publico/imagenes", methods=["GET"])
@jwt_required()
@require_role(ROL_PROPIETARIO, ROL_ADMINISTRADOR)
@require_tenant
def listar_imagenes():
    imagenes = (
        GimnasioImagen.query
        .filter_by(id_gimnasio=g.tenant_id)
        .order_by(GimnasioImagen.orden, GimnasioImagen.creado_en)
        .all()
    )
    return jsonify([i.to_dict() for i in imagenes]), 200


@perfil_publico_bp.route("/perfil-publico/imagenes", methods=["POST"])
@jwt_required()
@require_role(ROL_PROPIETARIO, ROL_ADMINISTRADOR)
@require_tenant
def subir_imagen():
    actuales = GimnasioImagen.query.filter_by(id_gimnasio=g.tenant_id).count()
    if actuales >= MAX_IMAGENES_POR_GIMNASIO:
        return jsonify({
            "msg": f"Ya tienes el máximo de {MAX_IMAGENES_POR_GIMNASIO} fotos. "
                   "Borra alguna antes de subir otra."
        }), 409

    file = request.files.get("file")
    if not file or file.filename == "":
        return jsonify({"msg": "No se recibió ningún archivo"}), 400
    if not _allowed_file(file.filename):
        return jsonify({"msg": "Formato no permitido. Usa PNG, JPG, GIF o WEBP."}), 400

    # content_length no siempre viene del cliente; medir leyendo el stream
    # es la única forma confiable de aplicar el límite por archivo (el
    # límite global de la petición, MAX_CONTENT_LENGTH, ya cubre el abuso
    # grueso, pero es de 15 MB -- mayor al que queremos para una sola foto).
    file.stream.seek(0, os.SEEK_END)
    tamano = file.stream.tell()
    file.stream.seek(0)
    if tamano > MAX_BYTES_POR_IMAGEN:
        return jsonify({"msg": "La imagen no puede pasar de 5 MB."}), 400

    os.makedirs(UPLOAD_DIR, exist_ok=True)
    nombre_seguro    = secure_filename(file.filename)
    nombre_unico     = f"{g.tenant_id}_{datetime.now().strftime('%Y%m%d%H%M%S%f')}_{nombre_seguro}"
    file.save(os.path.join(UPLOAD_DIR, nombre_unico))

    imagen = GimnasioImagen(
        id_gimnasio=g.tenant_id,
        archivo=nombre_unico,
        orden=actuales,
    )
    db.session.add(imagen)
    db.session.commit()

    return jsonify(imagen.to_dict()), 201


@perfil_publico_bp.route("/perfil-publico/imagenes/<int:id_imagen>", methods=["DELETE"])
@jwt_required()
@require_role(ROL_PROPIETARIO, ROL_ADMINISTRADOR)
@require_tenant
def eliminar_imagen(id_imagen):
    imagen = GimnasioImagen.query.filter_by(id=id_imagen, id_gimnasio=g.tenant_id).first()
    if not imagen:
        return jsonify({"msg": "Imagen no encontrada"}), 404

    ruta = os.path.join(UPLOAD_DIR, imagen.archivo)
    try:
        if os.path.exists(ruta):
            os.remove(ruta)
    except OSError:
        # No bloquear el borrado del registro por un error de filesystem --
        # peor es dejar la fila huerfana apuntando a un archivo que de
        # todas formas no se pudo borrar.
        pass

    db.session.delete(imagen)
    db.session.commit()
    return jsonify({"msg": "Imagen eliminada"}), 200


# ─────────────────────────────────────────────────────────────────────────────
# Testimonios
# ─────────────────────────────────────────────────────────────────────────────

@perfil_publico_bp.route("/perfil-publico/testimonios", methods=["GET"])
@jwt_required()
@require_role(ROL_PROPIETARIO, ROL_ADMINISTRADOR)
@require_tenant
def listar_testimonios_propios():
    testimonios = (
        Testimonio.query
        .filter_by(id_gimnasio=g.tenant_id)
        .order_by(Testimonio.creado_en.desc())
        .all()
    )
    return jsonify([t.to_dict() for t in testimonios]), 200


@perfil_publico_bp.route("/perfil-publico/testimonios", methods=["POST"])
@jwt_required()
@require_role(ROL_PROPIETARIO, ROL_ADMINISTRADOR)
@require_tenant
def crear_testimonio():
    data = request.get_json(silent=True) or {}
    autor_nombre = (data.get("autor_nombre") or "").strip()
    texto        = (data.get("texto") or "").strip()
    autor_cargo  = (data.get("autor_cargo") or "").strip() or None
    calificacion = data.get("calificacion")

    if not autor_nombre or not texto:
        return jsonify({"msg": "Falta el nombre del autor o el texto del testimonio"}), 400
    if len(texto) > 500:
        return jsonify({"msg": "El testimonio no puede pasar de 500 caracteres"}), 400
    if calificacion is not None:
        try:
            calificacion = int(calificacion)
        except (TypeError, ValueError):
            return jsonify({"msg": "La calificación debe ser un número del 1 al 5"}), 400
        if not (1 <= calificacion <= 5):
            return jsonify({"msg": "La calificación debe ser del 1 al 5"}), 400

    testimonio = Testimonio(
        id_gimnasio=g.tenant_id,
        id_usuario=int(get_jwt_identity()),
        autor_nombre=autor_nombre,
        autor_cargo=autor_cargo,
        texto=texto,
        calificacion=calificacion,
    )
    db.session.add(testimonio)
    db.session.commit()

    return jsonify(testimonio.to_dict()), 201


@perfil_publico_bp.route("/perfil-publico/testimonios/<int:id_testimonio>", methods=["DELETE"])
@jwt_required()
@require_role(ROL_PROPIETARIO, ROL_ADMINISTRADOR)
@require_tenant
def eliminar_testimonio(id_testimonio):
    testimonio = Testimonio.query.filter_by(id=id_testimonio, id_gimnasio=g.tenant_id).first()
    if not testimonio:
        return jsonify({"msg": "Testimonio no encontrado"}), 404

    db.session.delete(testimonio)
    db.session.commit()
    return jsonify({"msg": "Testimonio eliminado"}), 200
