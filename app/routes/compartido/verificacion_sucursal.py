"""
routes/compartido/verificacion_sucursal.py — Verificación de membresía de un
miembro de OTRA sucursal de la misma organización (HU-47 / SCRUM-218).

Para quién: staff de cualquier sucursal (Recepcionista, Entrenador,
Administrador, Propietario) -- no vive bajo /owner_gym porque Recepcionista
y Entrenador también lo necesitan, igual que miembro_membresias.py y
notificaciones.py en este mismo paquete "compartido".

El riesgo que define el diseño (ver la historia en Jira): esta pantalla es,
por construcción, un buscador de personas entre sucursales. Si permitiera
listar o buscar por fragmento, cualquier recepcionista podría enumerar el
padrón completo de la organización. Por eso:
  - Es búsqueda PUNTUAL por un identificador exacto -- numero_socio
    (hoy, id_usuario_pg: el proyecto no tiene todavía número de socio ni QR
    como conceptos propios: a_usuario_pg ya es único, secuencial y nunca se
    reutiliza, así que cumple el mismo papel; un QR futuro simplemente
    codificaría este mismo número) o nombre completo EXACTO -- nunca $regex
    parcial ni un listado paginado.
  - La respuesta trae solo los 5 campos que pide el AC2: nombre, foto,
    estado de membresía, fecha de vencimiento y sucursal de origen. Nada de
    teléfono, pagos, salud ni rutinas.
  - Si no hay exactamente un resultado, la respuesta es la misma genérica
    ("Miembro no encontrado") tanto si no existe como si el nombre no fue
    exacto -- no se distingue, para no ayudar a tantear nombres por prueba
    y error.

AC7 (una sola sucursal → esta pantalla no aplica) se refuerza también aquí,
no solo ocultando el botón en el frontend: con una sola sucursal en la
organización no hay "otra sucursal" posible, así que se responde 404 igual
que "no encontrado".
"""
from datetime import datetime, timezone

from flask import Blueprint, request, jsonify, g
from flask_jwt_extended import jwt_required, get_jwt_identity

from app.mongo import get_db
from app.models.pg.gimnasio import Gimnasio
from app.utils.tenant import require_tenant
from app.utils.security import require_role
from app.utils.alcance import registrar_evento
from app.utils.busqueda import normalizar_texto

verificacion_bp = Blueprint("verificacion_sucursal", __name__)


def _ids_y_organizacion_de_mi_sucursal():
    """Ids de Gimnasio de la organización del gimnasio actual (g.tenant_id) + su id_organizacion."""
    gym = Gimnasio.query.get(g.tenant_id)
    if not gym:
        return [], None
    filas = (Gimnasio.query
             .filter_by(id_organizacion=gym.id_organizacion)
             .with_entities(Gimnasio.id)
             .all())
    return [f.id for f in filas], gym.id_organizacion


def _estado_membresia(fecha_fin, now):
    """Mismo criterio que recepcionista_routes.py::_mem_status -- se duplica
    aquí (no se importa de un módulo de rutas de otro rol) porque es un
    helper de 8 líneas y mantener cada blueprint autocontenido evita un
    acoplamiento cruzado entre paneles que no aporta nada."""
    if fecha_fin is None:
        return "sin_membresia"
    if isinstance(fecha_fin, str):
        fecha_fin = datetime.fromisoformat(fecha_fin)
    if fecha_fin.tzinfo is None:
        fecha_fin = fecha_fin.replace(tzinfo=timezone.utc)
    dias = (fecha_fin - now).days
    if dias < 0:
        return "vencida"
    if dias <= 7:
        return "por_vencer"
    return "activa"


@verificacion_bp.route("/miembros/verificar", methods=["GET"])
@jwt_required()
@require_role("owner_gym", "Administrador", "Entrenador", "Recepcionista")
@require_tenant
def verificar_miembro():
    """
    Query params (exactamente uno de los dos):
        numero_socio  — id_usuario_pg exacto
        nombre        — nombre completo, comparado exacto (insensible a
                         mayúsculas/acentos vía normalizar_texto, SCRUM-105)
    """
    numero_socio = (request.args.get("numero_socio") or "").strip()
    nombre       = (request.args.get("nombre") or "").strip()

    if bool(numero_socio) == bool(nombre):
        return jsonify({"msg": "Indica numero_socio o nombre (uno solo de los dos)"}), 400

    ids_org, id_organizacion = _ids_y_organizacion_de_mi_sucursal()
    if len(ids_org) < 2:
        return jsonify({"msg": "Miembro no encontrado"}), 404

    mdb = get_db()
    base = {"id_gimnasio_pg": {"$in": ids_org}, "estado": "Activo"}

    miembro = None
    if numero_socio:
        try:
            miembro = mdb.miembros.find_one({**base, "id_usuario_pg": int(numero_socio)})
        except ValueError:
            miembro = None
    else:
        objetivo = normalizar_texto(nombre)
        candidatos = list(mdb.miembros.find(base, {"nombre": 1}))
        coincidencias = [c["_id"] for c in candidatos if normalizar_texto(c.get("nombre", "")) == objetivo]
        if len(coincidencias) == 1:
            miembro = mdb.miembros.find_one({"_id": coincidencias[0]})

    if not miembro:
        return jsonify({"msg": "Miembro no encontrado"}), 404

    now = datetime.now(timezone.utc)
    mm = mdb.miembro_membresia.find_one({"id_miembro": miembro["_id"], "estado": "Activa"})
    fecha_fin = mm.get("fecha_fin") if mm else None
    estado    = _estado_membresia(fecha_fin, now)
    if isinstance(fecha_fin, str):
        fecha_fin_out = fecha_fin[:10]
    elif fecha_fin:
        fecha_fin_out = fecha_fin.strftime("%Y-%m-%d")
    else:
        fecha_fin_out = None

    origen = Gimnasio.query.get(miembro.get("id_gimnasio_pg"))
    es_visita = miembro.get("id_gimnasio_pg") != g.tenant_id

    # AC3: cada consulta queda en bitácora (quién consultó a quién y cuándo).
    registrar_evento(
        int(get_jwt_identity()), "verificacion.consultar_membresia",
        id_organizacion=id_organizacion, id_gimnasio=g.tenant_id,
        detalle={"id_miembro": str(miembro["_id"]), "es_visita": es_visita},
    )

    # AC4: la asistencia del visitante se registra en la sucursal donde se
    # hizo la consulta (no en la de origen) -- solo si de verdad es una
    # visita a otra sucursal; si el miembro ya pertenece a esta, no hay nada
    # que registrar aquí (lo cubre el check-in normal de recepción).
    if es_visita:
        _registrar_visita(mdb, miembro["_id"], g.tenant_id)

    foto = miembro.get("foto_perfil")
    return jsonify({
        "id_miembro":        str(miembro["_id"]),
        "nombre":            miembro.get("nombre"),
        "foto":              foto if (foto or "").startswith("data:image") else None,
        "estado_membresia":  estado,
        "fecha_vencimiento": fecha_fin_out,
        "sucursal_origen":   {"id": origen.id, "nombre": origen.nombre} if origen else None,
        "es_visita":         es_visita,
    }), 200


def _registrar_visita(mdb, id_miembro, id_gimnasio_visitado):
    """
    Inserta la asistencia de un visitante en la sucursal donde se le
    verificó -- misma colección `asistencias` que usa el check-in normal de
    recepción (recepcionista_routes.py::register_checkin), con el campo
    adicional `id_gimnasio_visita` para que el historial del miembro (ver
    owner_gym/miembros.py::listar_asistencias_miembro) pueda mostrar dónde
    fue cuando es distinto de su sucursal de origen.

    Sin chequeo de "ya tiene un check-in abierto hoy" (a diferencia del
    check-in normal): un miembro puede, el mismo día, entrenar en su
    sucursal Y que lo verifiquen de visita en otra -- son hechos distintos,
    no un duplicado del mismo evento.
    """
    from app.utils.timezone import local_now_naive
    now = local_now_naive()
    mdb.asistencias.insert_one({
        "id_miembro":         id_miembro,
        "fecha":              datetime(now.year, now.month, now.day),
        "hora_entrada":       now.strftime("%H:%M:%S"),
        "hora_salida":        None,
        "origen":             "verificacion_sucursal",
        "id_gimnasio_visita":  id_gimnasio_visitado,
    })
