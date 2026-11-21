"""
routes/owner_gym/cuenta.py -- el propietario cancela su plan o elimina su cuenta.

Hasta ahora las dos cosas pasaban por el equipo: cancelar implicaba pedirlo, y
eliminar un gimnasio solo era posible con scripts/maintenance/borrar_gimnasios.py.
Un SaaS tiene que dejar irse a su cliente sin intermediarios, y la LFPDPPP
respalda lo mismo (derecho de cancelacion). Endpoints, todos bajo
/api/owner_gym:

    GET    /cuenta/estado                  que se cancelaria / que se borraria
    POST   /cuenta/suscripcion/cancelar    baja del plan al terminar lo pagado
    POST   /cuenta/suscripcion/reactivar   deshacer la cancelacion programada
    GET    /cuenta/exportar                copia de los datos del gimnasio (JSON)
    POST   /cuenta/eliminar                programa la purga definitiva
    POST   /cuenta/eliminar/cancelar       conservar la cuenta
    POST   /cuenta/bajas/procesar          ejecuta las purgas vencidas (superadmin)

Dos decisiones que valen la pena explicar:

1. Cancelar la suscripcion NO borra nada y no corta el servicio de inmediato:
   el gimnasio conserva su plan hasta la fecha que ya pago. Cortar al instante
   seria cobrar un periodo y no darlo.
2. Eliminar la cuenta se PROGRAMA con DIAS_GRACIA_BAJA de margen y se puede
   deshacer en ese plazo. Un clic no deberia poder destruir el padron completo
   de un gimnasio sin vuelta atras; Google, Apple y los SaaS serios hacen lo
   mismo. La purga definitiva la ejecuta `ejecutar_bajas_programadas`.
"""
import logging
from datetime import datetime, timedelta, timezone

from flask import Blueprint, current_app, g, jsonify, request
from flask_jwt_extended import get_jwt, get_jwt_identity, jwt_required

from app.constants.legal import DIAS_GRACIA_BAJA
from app.constants.roles import ROL_PROPIETARIO, ROL_SUPERADMIN, normalizar_rol
from app.extensions import db
from app.models.pg.gimnasio import Gimnasio
from app.models.pg.suscripcion import Suscripcion
from app.models.pg.usuario import Usuario
from app.mongo import get_db
from app.services.payments import pasarela_de_plataforma
from app.utils.alcance import registrar_evento
from app.utils.baja_gimnasio import BajaNoPermitida, purgar_gimnasios, resumen_datos
from app.utils.tenant import require_tenant

logger = logging.getLogger(__name__)

cuenta_bp = Blueprint("owner_cuenta", __name__, url_prefix="/api/owner_gym")

def _aware(valor):
    """
    Fecha con zona. Las columnas son timezone=True, pero SQLite (pruebas) y los
    seeds antiguos devuelven fechas sin zona y compararlas con `now(utc)`
    revienta. Se asume UTC, que es como se guardan.
    """
    if valor is None:
        return None
    return valor if valor.tzinfo else valor.replace(tzinfo=timezone.utc)


# Frase que el propietario debe escribir para que la eliminacion proceda. No es
# teatro: obliga a leer la pantalla y descarta el clic por inercia.
CONFIRMACION = "ELIMINAR"


# ── Helpers ──────────────────────────────────────────────────────────────────

def _rol():
    return normalizar_rol(get_jwt().get("role"))


def _solo_propietario():
    """Eliminar la cuenta es del dueno; un Administrador de sucursal no puede."""
    if _rol() not in (ROL_PROPIETARIO, ROL_SUPERADMIN):
        return jsonify({"error": "Solo el propietario puede realizar esta acción."}), 403
    return None


def _usuario_actual() -> Usuario | None:
    uid = get_jwt_identity()
    return Usuario.query.get(int(uid)) if str(uid).isdigit() else None


def _gimnasios_de_la_organizacion(gym_id: int) -> list[Gimnasio]:
    """
    Todas las sucursales de la organizacion del gimnasio actual: eliminar la
    cuenta da de baja la organizacion completa, no solo la sede desde la que se
    pidio (el plan y la suscripcion son de la organizacion, no de la sucursal).
    """
    gym = db.session.get(Gimnasio, gym_id)
    if gym is None:
        return []
    return Gimnasio.query.filter_by(id_organizacion=gym.id_organizacion).all()


def _suscripcion(gym_id: int) -> Suscripcion | None:
    """
    Suscripcion que cubre al gimnasio: la propia o, si es sucursal, la de su
    organizacion -- misma regla que el panel del superadmin (SCRUM-223).
    """
    propia = (Suscripcion.query
              .filter(Suscripcion.id_gimnasio == gym_id)
              .order_by(Suscripcion.created_at.desc()).first())
    if propia:
        return propia
    gym = db.session.get(Gimnasio, gym_id)
    if gym is None:
        return None
    hermanos = db.session.query(Gimnasio.id).filter(Gimnasio.id_organizacion == gym.id_organizacion)
    return (Suscripcion.query
            .filter(Suscripcion.id_gimnasio.in_(hermanos))
            .order_by(Suscripcion.created_at.desc()).first())


def _fin_periodo_pagado(sub: Suscripcion):
    """Hasta cuando esta pagado el servicio. None si no se puede determinar."""
    return _aware(sub.fecha_fin) or _aware(sub.fecha_proximo_cobro)


# ── GET /cuenta/estado ───────────────────────────────────────────────────────

@cuenta_bp.route("/cuenta/estado", methods=["GET"])
@jwt_required()
@require_tenant
def estado_cuenta():
    """
    Todo lo que la pantalla "Cuenta y datos" necesita para que el propietario
    decida con informacion y no a ciegas: su plan, hasta cuando esta pagado, si
    ya hay una cancelacion o una baja programada, y el conteo REAL de lo que se
    eliminaria si siguiera adelante.
    """
    err = _solo_propietario()
    if err:
        return err

    gyms = _gimnasios_de_la_organizacion(g.tenant_id)
    ids  = [x.id for x in gyms]
    sub  = _suscripcion(g.tenant_id)
    gym  = db.session.get(Gimnasio, g.tenant_id)

    suscripcion = None
    if sub:
        fin = _fin_periodo_pagado(sub)
        suscripcion = {
            "id":                  sub.id,
            "plan":                sub.plan.nombre if sub.plan else None,
            "estado":              sub.estado.value if hasattr(sub.estado, "value") else sub.estado,
            "ciclo":               sub.ciclo_facturacion,
            "auto_renovar":        bool(sub.auto_renovar),
            "cargo_recurrente":    sub.estado_recurrente,
            "pasarela":            sub.pasarela_recurrente,
            "servicio_hasta":      fin.isoformat() if fin else None,
            # La cancelacion programada se reconoce asi: ya no se renueva y hay
            # fecha de fin futura. No hace falta otra columna para saberlo.
            "cancelacion_programada": bool(
                not sub.auto_renovar and _aware(sub.fecha_fin)
                and _aware(sub.fecha_fin) > datetime.now(timezone.utc)
            ),
        }

    return jsonify({
        "gimnasio":    {"id": gym.id, "nombre": gym.nombre} if gym else None,
        "sucursales":  [{"id": x.id, "nombre": x.nombre} for x in gyms],
        "suscripcion": suscripcion,
        "baja":        gym.baja if gym else None,
        "dias_gracia": DIAS_GRACIA_BAJA,
        # Lo que se perderia. Se calcula en vivo: un numero inventado o
        # cacheado haria que el propietario confirme sobre algo que no es.
        "datos":       resumen_datos(ids),
    }), 200


# ── POST /cuenta/suscripcion/cancelar ────────────────────────────────────────

@cuenta_bp.route("/cuenta/suscripcion/cancelar", methods=["POST"])
@jwt_required()
@require_tenant
def cancelar_suscripcion():
    """
    Da de baja el plan al terminar el periodo ya pagado. No borra datos y no
    corta el acceso hoy: apaga la renovacion, cancela el cargo automatico con la
    pasarela y fija la fecha de corte.
    """
    err = _solo_propietario()
    if err:
        return err

    sub = _suscripcion(g.tenant_id)
    if sub is None:
        return jsonify({"error": "Este gimnasio no tiene una suscripción que cancelar."}), 404

    estado = sub.estado.value if hasattr(sub.estado, "value") else sub.estado
    if estado == "cancelled":
        return jsonify({"error": "La suscripción ya está cancelada."}), 409

    data   = request.get_json(silent=True) or {}
    motivo = (data.get("motivo") or "").strip()[:500] or None
    ahora  = datetime.now(timezone.utc)

    # 1. Cargo recurrente: que la pasarela no vuelva a cobrar. Si falla, se
    #    informa y NO se cancela a medias -- un plan cancelado con el cobro
    #    automatico vivo es la peor combinacion posible.
    if sub.referencia_recurrente and sub.estado_recurrente in ("activo", "pendiente", "pausado"):
        try:
            pasarela = pasarela_de_plataforma(sub.pasarela_recurrente)
            ok = pasarela.cancelar_suscripcion(
                sub.referencia_recurrente,
                motivo="El propietario canceló su suscripción a GymPro",
            )
            if not ok:
                raise RuntimeError("la pasarela respondió que no")
        except Exception as e:                                  # noqa: BLE001
            logger.warning("No se pudo cancelar el cargo recurrente de sub %s: %s", sub.id, e)
            return jsonify({
                "error": "No pudimos cancelar el cobro automático con la pasarela. "
                         "Inténtalo de nuevo en unos minutos; tu plan sigue intacto.",
            }), 502
        sub.estado_recurrente     = "cancelado"
        sub.referencia_recurrente = None
        sub.pasarela_recurrente   = None

    # 2. La suscripcion deja de renovarse y queda con fecha de corte.
    sub.auto_renovar = False
    fin = _fin_periodo_pagado(sub)
    sub.fecha_fin = fin if (fin and fin > ahora) else ahora
    sub.updated_at = ahora

    usuario = _usuario_actual()
    registrar_evento(
        usuario.id if usuario else None,
        "suscripcion_cancelada_por_propietario",
        id_gimnasio=g.tenant_id,
        id_organizacion=get_jwt().get("id_organizacion"),
        detalle={"id_suscripcion": sub.id, "servicio_hasta": sub.fecha_fin.isoformat(), "motivo": motivo},
        ip_origen=request.remote_addr,
    )
    db.session.commit()

    inmediata = _aware(sub.fecha_fin) <= ahora
    return jsonify({
        "msg": ("Tu suscripción quedó cancelada." if inmediata else
                "Tu suscripción se canceló. Conservas el plan hasta la fecha ya pagada."),
        "servicio_hasta": sub.fecha_fin.isoformat(),
        "datos_eliminados": False,
    }), 200


# ── POST /cuenta/suscripcion/reactivar ───────────────────────────────────────

@cuenta_bp.route("/cuenta/suscripcion/reactivar", methods=["POST"])
@jwt_required()
@require_tenant
def reactivar_suscripcion():
    """
    Deshace la cancelacion mientras el periodo pagado no haya vencido. No cobra
    nada: solo vuelve a dejar la suscripcion sin fecha de corte. El cargo
    automatico, si lo habia, se vuelve a autorizar desde la pantalla de pagos
    (la pasarela exige al titular, no se puede resucitar un acuerdo cancelado).
    """
    err = _solo_propietario()
    if err:
        return err

    sub = _suscripcion(g.tenant_id)
    if sub is None:
        return jsonify({"error": "Este gimnasio no tiene suscripción."}), 404

    ahora   = datetime.now(timezone.utc)
    fin_fijo = _aware(sub.fecha_fin)
    if not fin_fijo or fin_fijo <= ahora:
        return jsonify({
            "error": "El periodo ya venció; para volver a tener plan hay que contratarlo de nuevo.",
        }), 409

    sub.fecha_fin  = None
    sub.updated_at = ahora
    usuario = _usuario_actual()
    registrar_evento(
        usuario.id if usuario else None,
        "suscripcion_reactivada_por_propietario",
        id_gimnasio=g.tenant_id,
        id_organizacion=get_jwt().get("id_organizacion"),
        detalle={"id_suscripcion": sub.id},
        ip_origen=request.remote_addr,
    )
    db.session.commit()
    return jsonify({"msg": "Tu suscripción sigue activa. No se canceló nada."}), 200


# ── GET /cuenta/exportar ─────────────────────────────────────────────────────

@cuenta_bp.route("/cuenta/exportar", methods=["GET"])
@jwt_required()
@require_tenant
def exportar_datos():
    """
    Copia de la informacion del gimnasio en JSON, para que el propietario se
    lleve sus datos antes de eliminar la cuenta (y para atender una solicitud de
    acceso de la LFPDPPP sin pedirsela al equipo).

    No incluye hashes de contrasena ni credenciales de pasarelas: son secretos,
    no datos del cliente.
    """
    err = _solo_propietario()
    if err:
        return err

    gyms = _gimnasios_de_la_organizacion(g.tenant_id)
    ids  = [x.id for x in gyms]
    mdb  = get_db()

    def documentos(coleccion, filtro, limite=20000):
        salida = []
        try:
            for d in mdb[coleccion].find(filtro).limit(limite):
                d["_id"] = str(d["_id"])
                salida.append({k: (str(v) if type(v).__name__ == "ObjectId" else v) for k, v in d.items()})
        except Exception as e:                                   # noqa: BLE001
            logger.warning("Exportación: no se pudo leer '%s': %s", coleccion, e)
        return salida

    tenant = {"$or": [{"id_gimnasio": {"$in": ids}}, {"id_gimnasio_pg": {"$in": ids}}]}
    miembros = documentos("miembros", tenant)
    ids_miembros = [m["_id"] for m in miembros]
    por_miembro = {"id_miembro": {"$in": ids_miembros}} if ids_miembros else {"_id": None}

    export = {
        "generado_en": datetime.now(timezone.utc).isoformat(),
        "aviso": ("Copia de los datos del gimnasio en GymPro. No incluye contraseñas "
                  "ni credenciales de las pasarelas de pago."),
        "gimnasios": [x.to_dict() for x in gyms],
        "personal": [
            {k: v for k, v in u.to_dict().items() if k != "foto_perfil"}
            for u in Usuario.query.filter(Usuario.id_gimnasio.in_(ids)).all()
        ],
        "miembros":        miembros,
        "membresias":      documentos("miembro_membresia", por_miembro),
        "pagos":           documentos("pagos", tenant),
        "ventas":          documentos("ventas", tenant),
        "productos":       documentos("productos", tenant),
        "asistencias":     documentos("asistencias", tenant),
        "rutinas":         documentos("rutinas", tenant),
        "dietas":          documentos("dietas", tenant),
        "progreso_fisico": documentos("progreso_fisico", por_miembro),
    }

    usuario = _usuario_actual()
    registrar_evento(
        usuario.id if usuario else None, "datos_gimnasio_exportados",
        id_gimnasio=g.tenant_id, id_organizacion=get_jwt().get("id_organizacion"),
        detalle={"miembros": len(miembros)}, ip_origen=request.remote_addr,
    )
    db.session.commit()

    respuesta = jsonify(export)
    nombre = f"gympro-datos-{g.tenant_id}-{datetime.now(timezone.utc):%Y%m%d}.json"
    respuesta.headers["Content-Disposition"] = f'attachment; filename="{nombre}"'
    return respuesta, 200


# ── POST /cuenta/eliminar ────────────────────────────────────────────────────

@cuenta_bp.route("/cuenta/eliminar", methods=["POST"])
@jwt_required()
@require_tenant
def programar_eliminacion():
    """
    Programa la eliminacion definitiva de la cuenta y de todos los datos del
    gimnasio, con DIAS_GRACIA_BAJA de margen.

    Exige la contrasena del propietario y la palabra de confirmacion: es la
    unica accion del sistema que destruye datos de forma irreversible, y el JWT
    por si solo no prueba que quien la pide sea el dueno y no alguien con su
    sesion abierta.
    """
    err = _solo_propietario()
    if err:
        return err

    data         = request.get_json(silent=True) or {}
    password     = data.get("password") or ""
    confirmacion = (data.get("confirmacion") or "").strip().upper()
    motivo       = (data.get("motivo") or "").strip()[:500] or None

    usuario = _usuario_actual()
    if usuario is None:
        return jsonify({"error": "Sesión no válida."}), 401
    if not password or not usuario.check_password(password):
        return jsonify({"error": "La contraseña no es correcta."}), 403
    if confirmacion != CONFIRMACION:
        return jsonify({"error": f'Escribe "{CONFIRMACION}" para confirmar.'}), 400

    gyms = _gimnasios_de_la_organizacion(g.tenant_id)
    if not gyms:
        return jsonify({"error": "No se encontró el gimnasio."}), 404
    if any(x.baja_programada_para for x in gyms):
        return jsonify({"error": "Ya hay una eliminación programada para esta cuenta."}), 409

    ahora     = datetime.now(timezone.utc)
    programada = ahora + timedelta(days=DIAS_GRACIA_BAJA)
    for x in gyms:
        x.baja_solicitada_en   = ahora
        x.baja_programada_para = programada
        x.baja_motivo          = motivo
        x.baja_solicitada_por  = usuario.id

    # Que no se siga cobrando a quien ya pidio irse.
    sub = _suscripcion(g.tenant_id)
    if sub:
        sub.auto_renovar = False
        sub.updated_at   = ahora

    registrar_evento(
        usuario.id, "baja_cuenta_programada",
        id_gimnasio=g.tenant_id, id_organizacion=get_jwt().get("id_organizacion"),
        detalle={
            "programada_para": programada.isoformat(),
            "sucursales":      [x.id for x in gyms],
            "motivo":          motivo,
            "datos":           resumen_datos([x.id for x in gyms]),
        },
        ip_origen=request.remote_addr,
    )
    db.session.commit()

    return jsonify({
        "msg": (f"Tu cuenta y tus datos se eliminarán el {programada:%d/%m/%Y}. "
                "Hasta entonces todo sigue funcionando y puedes cancelar la eliminación "
                "desde esta misma pantalla."),
        "programada_para": programada.isoformat(),
        "dias_gracia":     DIAS_GRACIA_BAJA,
    }), 200


# ── POST /cuenta/eliminar/cancelar ───────────────────────────────────────────

@cuenta_bp.route("/cuenta/eliminar/cancelar", methods=["POST"])
@jwt_required()
@require_tenant
def cancelar_eliminacion():
    """Conserva la cuenta: anula la baja programada mientras no se haya ejecutado."""
    err = _solo_propietario()
    if err:
        return err

    gyms = [x for x in _gimnasios_de_la_organizacion(g.tenant_id) if x.baja_programada_para]
    if not gyms:
        return jsonify({"error": "No hay ninguna eliminación programada."}), 404

    for x in gyms:
        x.baja_solicitada_en   = None
        x.baja_programada_para = None
        x.baja_motivo          = None
        x.baja_solicitada_por  = None

    usuario = _usuario_actual()
    registrar_evento(
        usuario.id if usuario else None, "baja_cuenta_cancelada",
        id_gimnasio=g.tenant_id, id_organizacion=get_jwt().get("id_organizacion"),
        detalle={"sucursales": [x.id for x in gyms]}, ip_origen=request.remote_addr,
    )
    db.session.commit()
    return jsonify({"msg": "Listo: tu cuenta y tus datos se conservan. No se eliminará nada."}), 200


# ── Purga de las bajas vencidas ──────────────────────────────────────────────

def ejecutar_bajas_programadas(app=None) -> dict:
    """
    Ejecuta la purga de los gimnasios cuya fecha de baja ya paso. Pensada para
    correr desde un proceso programado (igual que procesar_auto_renovaciones) o
    a mano con el endpoint de abajo.

    Cada organizacion se purga en su propia transaccion: que una falle no debe
    impedir las demas ni dejar a medias las anteriores.
    """
    contexto = app.app_context() if app else current_app.app_context()
    with contexto:
        ahora = datetime.now(timezone.utc)
        vencidos = (Gimnasio.query
                    .filter(Gimnasio.baja_programada_para.isnot(None),
                            Gimnasio.baja_programada_para <= ahora)
                    .all())
        por_organizacion: dict[int, list[int]] = {}
        for gym in vencidos:
            por_organizacion.setdefault(gym.id_organizacion, []).append(gym.id)

        resultado = {"organizaciones": 0, "gimnasios": 0, "errores": []}
        for id_org, ids in por_organizacion.items():
            try:
                borrado = purgar_gimnasios(ids)
                db.session.commit()
                resultado["organizaciones"] += 1
                resultado["gimnasios"] += len(ids)
                logger.info("Baja ejecutada: organización %s, gimnasios %s -> %s", id_org, ids, borrado)
            except (BajaNoPermitida, Exception) as e:            # noqa: BLE001
                db.session.rollback()
                resultado["errores"].append({"id_organizacion": id_org, "gimnasios": ids, "error": str(e)})
                logger.error("No se pudo ejecutar la baja de la organización %s: %s", id_org, e)
        return resultado


@cuenta_bp.route("/cuenta/bajas/procesar", methods=["POST"])
@jwt_required()
def procesar_bajas():
    """Dispara la purga de las bajas vencidas. Solo superadmin."""
    if _rol() != ROL_SUPERADMIN:
        return jsonify({"error": "Solo el superadministrador puede ejecutar esta acción."}), 403
    resultado = ejecutar_bajas_programadas(app=current_app._get_current_object())
    return jsonify({"msg": "Bajas programadas procesadas", **resultado}), 200
