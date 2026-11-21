"""
utils/baja_gimnasio.py -- resumen y purga definitiva de los datos de un gimnasio.

Dos funciones con proposito distinto y deliberadamente separadas:

  * `resumen_datos(gym_ids)`  -- SOLO CUENTA. Es lo que el propietario ve antes
    de confirmar la eliminacion: "esto es lo que vas a perder", con numeros
    reales de su propia base, no una lista genarica de categorias.
  * `purgar_gimnasios(gym_ids)` -- BORRA. Equivalente en producto de
    scripts/maintenance/borrar_gimnasios.py, que hasta ahora era la unica forma
    de dar de baja un gimnasio (y exigia pedirselo al equipo por correo).

La purga sigue el mismo orden que el script, por las mismas razones:
  1. `bitacora_eventos` del gimnasio -- su FK es SET NULL, quedarian huerfanas.
  2. `usuarios` del gimnasio -- su FK tambien es SET NULL: sin este paso
     quedarian cuentas sin gimnasio con el correo ocupado.
  3. `gimnasios` -- el resto (suscripciones, facturas, pasarelas, asignaciones,
     tipos de membresia, reportes...) cae por ON DELETE CASCADE.
  4. `organizaciones` que se queden sin ningun gimnasio (su FK es RESTRICT).
  5. Mongo: documentos del tenant y sus hijos sin campo de tenant.

Nunca borra un gimnasio con usuarios superadmin ligados: eso indicaria que algo
esta mal en los datos, y un superadmin no pertenece a un gimnasio.
"""
from bson import ObjectId
from sqlalchemy import func, select

from app.extensions import db
from app.models.pg.bitacora_evento import BitacoraEvento
from app.models.pg.gimnasio import Gimnasio
from app.models.pg.organizacion import Organizacion
from app.models.pg.rol import Rol
from app.models.pg.usuario import Usuario
from app.mongo import get_db

# Un documento de Mongo puede acotar su gimnasio con cualquiera de estos dos
# campos, segun la coleccion (historico del proyecto, no hay una sola
# convencion).
CAMPOS_TENANT = ("id_gimnasio", "id_gimnasio_pg")
# Campos que apuntan a un usuario de Postgres.
CAMPOS_USUARIO_PG = ("id_usuario_pg", "id_entrenador_pg", "id_miembro_pg")


class BajaNoPermitida(Exception):
    """La purga se detiene antes de tocar nada."""


def _variantes(valores):
    """Un id puede estar guardado como ObjectId o como texto: se buscan ambos."""
    out = []
    for v in valores:
        out.append(v)
        out.append(str(v))
        if isinstance(v, str) and len(v) == 24:
            try:
                out.append(ObjectId(v))
            except Exception:
                pass
    return out


def _ids_de(mdb, coleccion, filtro):
    return [d["_id"] for d in mdb[coleccion].find(filtro, {"_id": 1})]


def resumen_datos(gym_ids: list[int]) -> dict:
    """
    Cuantos registros se eliminarian. Pensado para mostrarse tal cual: cada
    clave es una linea de la pantalla de confirmacion.
    """
    ids = [int(i) for i in gym_ids]
    tenant = {"$or": [{c: {"$in": _variantes(ids)}} for c in CAMPOS_TENANT]}
    mdb = get_db()

    miembro_ids = _ids_de(mdb, "miembros", tenant)
    por_miembro = {"id_miembro": {"$in": _variantes(miembro_ids)}} if miembro_ids else None

    def cuenta(coleccion, filtro=None):
        try:
            return mdb[coleccion].count_documents(filtro if filtro is not None else tenant)
        except Exception:
            return 0

    # Consultas con el ORM y no SQL crudo: `= ANY(:ids)` solo existe en
    # Postgres y las pruebas corren sobre SQLite.
    cuentas = db.session.scalar(
        select(func.count()).select_from(Usuario).where(Usuario.id_gimnasio.in_(ids))
    ) or 0
    sucursales = db.session.scalar(
        select(func.count()).select_from(Gimnasio).where(Gimnasio.id.in_(ids))
    ) or 0

    return {
        "sucursales":        int(sucursales),
        "cuentas_personal":  int(cuentas),
        "miembros":          len(miembro_ids),
        "membresias":        cuenta("miembro_membresia", por_miembro) if por_miembro else 0,
        "asistencias":       cuenta("asistencias"),
        "pagos":             cuenta("pagos"),
        "ventas":            cuenta("ventas"),
        "productos":         cuenta("productos"),
        "rutinas":           cuenta("rutinas"),
        "dietas":            cuenta("dietas"),
        "progreso_fisico":   cuenta("progreso_fisico", por_miembro) if por_miembro else 0,
        "mensajes_chat":     cuenta("mensajes_chat"),
        "notificaciones":    cuenta("notificaciones"),
    }


def purgar_gimnasios(gym_ids: list[int]) -> dict:
    """
    Elimina definitivamente los gimnasios indicados y todos sus datos.

    No hace commit: lo hace quien llama, para que el borrado quede en la misma
    transaccion que el evento de bitacora que lo describe. Mongo no es
    transaccional aqui, asi que se borra DESPUES de que Postgres haya validado
    sus propias restricciones (si Postgres fallara, Mongo no se habria tocado).

    Devuelve el conteo de lo borrado, por si quien llama quiere registrarlo.
    """
    ids = sorted({int(i) for i in gym_ids})
    if not ids:
        return {"postgres": {}, "mongo": {}}

    existen = db.session.scalars(select(Gimnasio.id).where(Gimnasio.id.in_(ids))).all()
    faltan = set(ids) - set(existen)
    if faltan:
        raise BajaNoPermitida(f"Estos gimnasios no existen: {sorted(faltan)}")

    usuarios = db.session.execute(
        select(Usuario.id, Usuario.email, func.lower(Rol.nombre))
        .join(Rol, Rol.id == Usuario.id_rol)
        .where(Usuario.id_gimnasio.in_(ids))
    ).all()
    if any(rol == "superadmin" for _, _, rol in usuarios):
        raise BajaNoPermitida("Hay cuentas superadmin ligadas a este gimnasio.")

    user_ids = [u[0] for u in usuarios]
    emails   = [u[1] for u in usuarios]

    # ── Mongo: qué se va a borrar (se calcula ANTES del DELETE en Postgres,
    # porque los ids de usuario hacen falta para los hijos sin tenant) ───────
    mdb = get_db()
    tenant = [{c: {"$in": _variantes(ids)}} for c in CAMPOS_TENANT]
    miembro_ids = _ids_de(mdb, "miembros", {"$or": tenant})
    rutina_ids  = _ids_de(mdb, "rutinas", {"$or": tenant})
    venta_ids   = _ids_de(mdb, "ventas", {"$or": tenant})
    dia_ids     = _ids_de(mdb, "rutina_dias", {"$or": tenant + (
        [{"id_rutina": {"$in": _variantes(rutina_ids)}}] if rutina_ids else [])})

    def filtro_total(coleccion):
        f = list(tenant)
        if miembro_ids:
            f.append({"id_miembro": {"$in": _variantes(miembro_ids)}})
        if rutina_ids:
            f.append({"id_rutina": {"$in": _variantes(rutina_ids)}})
        if dia_ids:
            f.append({"id_rutina_dia": {"$in": _variantes(dia_ids)}})
        if venta_ids:
            f.append({"id_venta": {"$in": _variantes(venta_ids)}})
        for campo in CAMPOS_USUARIO_PG:
            if user_ids:
                f.append({campo: {"$in": user_ids}})
        if emails and coleccion == "password_resets":
            f.append({"email": {"$in": emails}})
        return {"$or": f}

    colecciones = [c for c in mdb.list_collection_names() if not c.startswith("system.")]

    # ── Postgres ────────────────────────────────────────────────────────────
    borrado_pg = {}
    borrado_pg["bitacora_eventos"] = (
        db.session.query(BitacoraEvento)
        .filter(BitacoraEvento.id_gimnasio.in_(ids))
        .delete(synchronize_session=False)
    )
    if user_ids:
        borrado_pg["usuarios"] = (
            db.session.query(Usuario).filter(Usuario.id.in_(user_ids))
            .delete(synchronize_session=False)
        )
    borradas = (db.session.query(Gimnasio).filter(Gimnasio.id.in_(ids))
                .delete(synchronize_session=False))
    borrado_pg["gimnasios"] = borradas
    if borradas != len(ids):
        raise BajaNoPermitida("No se eliminaron todos los gimnasios indicados; se revierte.")

    # Organizaciones que quedaron vacías: su FK es RESTRICT, no caen por cascada.
    huerfanas = db.session.scalars(
        select(Organizacion.id).where(
            ~select(Gimnasio.id).where(Gimnasio.id_organizacion == Organizacion.id).exists()
        )
    ).all()
    borrado_pg["organizaciones"] = (
        db.session.query(Organizacion).filter(Organizacion.id.in_(huerfanas))
        .delete(synchronize_session=False) if huerfanas else 0
    )

    # Se obliga a Postgres a validar ahora (FKs, RESTRICT): si algo impide el
    # borrado, revienta aquí y Mongo queda intacto.
    db.session.flush()

    # ── Mongo ───────────────────────────────────────────────────────────────
    borrado_mongo = {}
    for coleccion in colecciones:
        try:
            res = mdb[coleccion].delete_many(filtro_total(coleccion))
        except Exception as e:                        # una colección no debe detener el resto
            print(f"[baja_gimnasio] No se pudo limpiar '{coleccion}': {e}")
            continue
        if res.deleted_count:
            borrado_mongo[coleccion] = res.deleted_count

    return {"postgres": borrado_pg, "mongo": borrado_mongo}
