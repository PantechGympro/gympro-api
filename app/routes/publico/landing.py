"""
routes/publico/landing.py -- Datos para la pagina comercial de GymPro.

Sin autenticacion a proposito: esta es la pagina que ve cualquier
visitante ANTES de iniciar sesion (web/src/pages/publico/Landing.jsx), asi
que estos endpoints no pueden exigir JWT. Solo exponen lo que el propio
gimnasio decidio hacer publico desde su panel (ver
routes/owner_gym/perfil_publico.py): fotos que subio y testimonios que
escribio. Nunca datos operativos (miembros, pagos, etc.) -- eso sigue
detras de autenticacion en todos los demas blueprints.
"""
from flask import Blueprint, jsonify

from app.extensions import db
from app.models.pg.gimnasio import Gimnasio
from app.models.pg.gimnasio_imagen import GimnasioImagen
from app.models.pg.testimonio import Testimonio

landing_bp = Blueprint("landing", __name__)

MAX_GIMNASIOS_EN_CARRUSEL  = 24
MAX_TESTIMONIOS_EN_LANDING = 20


@landing_bp.route("/gimnasios", methods=["GET"])
def gimnasios_destacados():
    """
    Gimnasios reales que ya tienen al menos una foto publica. Se deja
    fuera cualquier gimnasio sin fotos -- mostrar una tarjeta vacia (sin
    imagen) en la landing se ve peor que simplemente no mostrarla todavia.
    """
    # Subconsulta IN en vez de JOIN + distinct(): Gimnasio.configuracion es
    # columna JSON (no JSONB) y Postgres no tiene operador de igualdad para
    # ese tipo -- un JOIN que produce filas duplicadas por gimnasio (uno
    # por cada foto) y luego las deduplica con .distinct() falla en
    # servidor con "could not identify an equality operator for type json"
    # en cuanto intenta comparar esa columna. La subconsulta evita por
    # completo necesitar deduplicar filas de Gimnasio.
    tiene_fotos = db.session.query(GimnasioImagen.id_gimnasio)
    filas = (
        Gimnasio.query
        .filter(Gimnasio.activo.is_(True), Gimnasio.id.in_(tiene_fotos))
        .order_by(Gimnasio.nombre)
        .limit(MAX_GIMNASIOS_EN_CARRUSEL)
        .all()
    )

    resultado = []
    for gym in filas:
        imagenes = (
            GimnasioImagen.query
            .filter_by(id_gimnasio=gym.id)
            .order_by(GimnasioImagen.orden, GimnasioImagen.creado_en)
            .all()
        )
        config = gym.configuracion or {}
        resultado.append({
            "id":        gym.id,
            "nombre":    gym.nombre,
            "direccion": config.get("direccion") or None,
            "imagenes":  [i.to_dict()["url"] for i in imagenes],
        })

    return jsonify(resultado), 200


@landing_bp.route("/testimonios", methods=["GET"])
def testimonios_destacados():
    filas = (
        Testimonio.query
        .join(Gimnasio, Gimnasio.id == Testimonio.id_gimnasio)
        .filter(Gimnasio.activo.is_(True))
        .order_by(Testimonio.creado_en.desc())
        .limit(MAX_TESTIMONIOS_EN_LANDING)
        .all()
    )

    resultado = []
    for t in filas:
        d = t.to_dict()
        d["gimnasio_nombre"] = t.gimnasio.nombre if t.gimnasio else None
        resultado.append(d)

    return jsonify(resultado), 200
