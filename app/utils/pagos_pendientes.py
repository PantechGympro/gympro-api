"""
Confirmación de pagos manuales del miembro por el personal (nov/2026).

Problema: desde su cuenta el miembro podía "pagar" con Efectivo o
Transferencia y el sistema lo daba por bueno sin que nadie verificara que el
dinero llegó. Eso aplicaba a dos flujos:

  1. Compras de la tienda (colección `ventas`).
  2. Renovación de membresía (`POST /api/user/membership/renew`), que además
     activaba la membresía al instante con cualquier referencia escrita.

Regla nueva: todo pago que el miembro declara a mano nace PENDIENTE y solo
Recepcionista / Administrador / Propietario pueden confirmarlo o rechazarlo.
Los pagos por pasarela (PayPal, Mercado Pago) NO pasan por aquí: ya los
verifica el webhook del proveedor.

Este módulo concentra la lógica compartida para que la ruta del miembro, la
bandeja del personal y el flujo de entrega usen exactamente las mismas reglas.
"""
from datetime import datetime, timedelta, timezone

from bson import ObjectId

from app.constants.roles import (
    ROL_PROPIETARIO, ROL_ADMINISTRADOR, ROL_RECEPCIONISTA, normalizar_rol,
)

# ── Estados ──────────────────────────────────────────────────────────────────
PAGO_PENDIENTE = "pendiente"
PAGO_PAGADO = "pagado"
PAGO_RECHAZADO = "rechazado"

# Métodos que el miembro puede declarar él mismo y que requieren verificación.
METODOS_MANUALES_MIEMBRO_VENTA = ("Efectivo", "Transferencia", "TotalPass")
METODOS_MANUALES_MIEMBRO_MEMBRESIA = ("Efectivo", "Transferencia")

# Roles que pueden confirmar/rechazar. El entrenador queda fuera a propósito:
# puede entregar un producto ya pagado, pero no validar dinero.
ROLES_CONFIRMAN_PAGO = {ROL_PROPIETARIO, ROL_ADMINISTRADOR, ROL_RECEPCIONISTA}

# Filtro Mongo para ingresos REALES: excluye lo pendiente y lo rechazado.
# Los documentos anteriores a esta función no traen `estado_pago`; $nin los
# deja pasar (campo ausente cuenta como "no está en la lista"), así que el
# histórico se sigue contando como cobrado sin migración.
FILTRO_VENTA_COBRADA = {"estado_pago": {"$nin": [PAGO_PENDIENTE, PAGO_RECHAZADO]}}


def puede_confirmar_pago(claims) -> bool:
    return normalizar_rol((claims or {}).get("role", "")) in ROLES_CONFIRMAN_PAGO


# ── Stock ────────────────────────────────────────────────────────────────────
def ajustar_stock(db, id_gimnasio, items, signo=-1):
    """
    Aplica (signo=-1, venta) o revierte (signo=+1, rechazo) el stock de los
    artículos de una venta. Un combo no lleva stock propio: mueve el de cada
    componente multiplicado por la cantidad de combos. Al descontar, el stock
    nunca baja de 0 (misma regla que el POS).
    """
    for item in items or []:
        try:
            oid = ObjectId(str(item["id"]))
            qty = max(1, int(item.get("qty", 1)))
            prod = db.productos.find_one(
                {"_id": oid, "id_gimnasio": id_gimnasio},
                {"es_combo": 1, "items_combo": 1},
            )
            if prod and prod.get("es_combo"):
                for comp in (prod.get("items_combo") or []):
                    try:
                        _mover(db, id_gimnasio, ObjectId(str(comp["id_producto"])),
                               max(1, int(comp.get("cantidad", 1))) * qty, signo)
                    except Exception:
                        continue
            else:
                _mover(db, id_gimnasio, oid, qty, signo)
        except Exception:
            continue  # no-bloqueante: un producto borrado no impide resolver el pago


def _mover(db, id_gimnasio, oid, cantidad, signo):
    expr = (
        {"$max": [0, {"$subtract": ["$stock", cantidad]}]} if signo < 0
        else {"$add": [{"$ifNull": ["$stock", 0]}, cantidad]}
    )
    db.productos.update_one(
        {"_id": oid, "id_gimnasio": id_gimnasio}, [{"$set": {"stock": expr}}]
    )


# ── Avisos al personal ───────────────────────────────────────────────────────
def usuarios_que_confirman(id_gimnasio):
    """IDs PG de propietario, administradores y recepcionistas activos del gimnasio."""
    try:
        from app.models.pg.usuario import Usuario
        from app.models.pg.rol import Rol

        roles = Rol.query.filter(
            Rol.nombre.in_([ROL_PROPIETARIO, ROL_ADMINISTRADOR, ROL_RECEPCIONISTA])
        ).all()
        ids_rol = [r.id for r in roles]
        if not ids_rol:
            return []
        return [
            u.id for u in Usuario.query.filter(
                Usuario.id_gimnasio == id_gimnasio,
                Usuario.id_rol.in_(ids_rol),
                Usuario.activo.is_(True),
            ).all()
        ]
    except Exception as e:
        print(f"[pagos_pendientes] No se pudo resolver al personal: {e}")
        return []


def notificar_personal(db, id_gimnasio, tipo, titulo, mensaje, referencia_tipo, referencia_id):
    """Notificación no-bloqueante a quienes pueden confirmar pagos."""
    from app.routes.compartido.notificaciones import crear_notificacion

    for uid in usuarios_que_confirman(id_gimnasio):
        try:
            crear_notificacion(
                db=db, id_usuario_pg=uid, id_gimnasio_pg=id_gimnasio, tipo=tipo,
                titulo=titulo, mensaje=mensaje,
                referencia_tipo=referencia_tipo, referencia_id=referencia_id,
            )
        except Exception as e:
            print(f"[pagos_pendientes] No se pudo notificar a {uid}: {e}")


def notificar_miembro(db, id_gimnasio, id_miembro_pg, tipo, titulo, mensaje,
                      referencia_tipo, referencia_id):
    if not id_miembro_pg:
        return
    from app.routes.compartido.notificaciones import crear_notificacion
    try:
        crear_notificacion(
            db=db, id_usuario_pg=int(id_miembro_pg), id_gimnasio_pg=id_gimnasio,
            tipo=tipo, titulo=titulo, mensaje=mensaje,
            referencia_tipo=referencia_tipo, referencia_id=referencia_id,
        )
    except Exception as e:
        print(f"[pagos_pendientes] No se pudo notificar al miembro: {e}")


# ── Renovación de membresía ──────────────────────────────────────────────────
def activar_renovacion(mdb, id_gimnasio, miembro, *, id_membresia, nombre, duracion_meses,
                       monto, metodo_pago, referencia=None, ahora=None):
    """
    Activa la membresía y deja el pago registrado. Es la ÚNICA implementación de
    "renovar": la usa tanto la confirmación del personal como cualquier otro
    camino que ya haya verificado el pago.

    Si ya hay una membresía activa, la nueva empieza al terminar la actual (no
    se pierden días pagados). Devuelve fechas y el id del pago.
    """
    ahora = ahora or datetime.now()
    fecha_inicio = ahora
    activa = mdb.miembro_membresia.find_one({"id_miembro": miembro["_id"], "estado": "Activa"})
    if activa:
        f_fin = activa.get("fecha_fin")
        if isinstance(f_fin, str):
            f_fin = datetime.strptime(f_fin[:10], "%Y-%m-%d")
        if isinstance(f_fin, datetime) and f_fin > ahora:
            fecha_inicio = f_fin + timedelta(days=1)
        mdb.miembro_membresia.update_one({"_id": activa["_id"]}, {"$set": {"estado": "Vencida"}})

    fecha_fin = fecha_inicio + timedelta(days=(duracion_meses or 1) * 30)
    mdb.miembro_membresia.insert_one({
        "id_miembro": miembro["_id"], "id_membresia": id_membresia,
        "fecha_inicio": fecha_inicio, "fecha_fin": fecha_fin, "estado": "Activa",
    })
    pago = mdb.pagos.insert_one({
        "id_miembro": miembro["_id"], "id_gimnasio": id_gimnasio,
        "monto": float(monto), "metodo_pago": metodo_pago,
        "concepto": f"Renovación {nombre}", "fecha_pago": ahora,
        "referencia": referencia or None,
    })
    return {"fecha_inicio": fecha_inicio, "fecha_fin": fecha_fin, "id_pago": pago.inserted_id}


def utcnow():
    return datetime.now(timezone.utc)


# ═════════════════════════════════════════════════════════════════════════════
# Resolución (confirmar / rechazar). Cada función hace la transición con un
# update condicionado a estado "pendiente": si dos personas pulsan a la vez, solo
# una gana y la otra recibe False (la ruta responde 409), sin doble activación.
# ═════════════════════════════════════════════════════════════════════════════
def _resumen(items):
    partes = []
    for it in items or []:
        try:
            qty = max(1, int(it.get("qty", 1)))
        except (TypeError, ValueError):
            qty = 1
        partes.append(f"{qty}× {it.get('nombre', 'Producto')}")
    return ", ".join(partes) or "producto"


def confirmar_venta(db, venta, id_actor, nombre_actor, *, notificar=True, ahora=None):
    """Marca una venta pendiente como pagada. False si ya no estaba pendiente."""
    ahora = ahora or utcnow()
    res = db.ventas.update_one(
        {"_id": venta["_id"], "estado_pago": PAGO_PENDIENTE},
        {"$set": {
            "estado_pago": PAGO_PAGADO,
            "cobrado_por_pg": id_actor, "cobrado_por_nombre": nombre_actor, "pagado_en": ahora,
        }},
    )
    if not res.modified_count:
        return False

    gym_id = venta.get("id_gimnasio")
    venta_id = str(venta["_id"])
    if notificar:
        notificar_miembro(
            db, gym_id, venta.get("id_miembro_pg"), "pago_confirmado", "Pago confirmado",
            f"Recepción confirmó tu pago de ${float(venta.get('total', 0)):.2f} "
            f"({_resumen(venta.get('items'))}).",
            "venta", venta_id,
        )
    # El recibo se difiere hasta aquí: antes de verificar el pago no hay nada que
    # certificar. No-bloqueante.
    try:
        from app.utils.recibos import enviar_recibo_venta
        miembro = db.miembros.find_one({"_id": venta.get("id_miembro")}) if venta.get("id_miembro") else None
        enviar_recibo_venta(
            gym_id, venta_num=venta_id[-8:].upper(),
            fecha_str=ahora.strftime("%d/%m/%Y %H:%M"), items=venta.get("items", []),
            total=venta.get("total", 0), metodo_pago=venta.get("metodo_pago"),
            nombre_miembro=venta.get("nombre_miembro", ""),
            email_miembro=(miembro or {}).get("email"),
            referencia_pasarela=venta.get("referencia") or None,
        )
    except Exception as e:
        print(f"[pagos_pendientes] Error enviando recibo de venta: {e}")
    return True


def rechazar_venta(db, venta, id_actor, nombre_actor, motivo, *, ahora=None):
    """
    Rechaza una venta pendiente: ya no cuenta como ingreso, la entrega se cancela
    y el stock que se había apartado vuelve al inventario. False si ya no estaba
    pendiente.
    """
    ahora = ahora or utcnow()
    cambios = {
        "estado_pago": PAGO_RECHAZADO, "motivo_rechazo": motivo or None,
        "rechazado_por_pg": id_actor, "rechazado_por_nombre": nombre_actor, "rechazado_en": ahora,
    }
    if venta.get("estado_entrega") == "pendiente":
        cambios["estado_entrega"] = "cancelado"
    res = db.ventas.update_one({"_id": venta["_id"], "estado_pago": PAGO_PENDIENTE}, {"$set": cambios})
    if not res.modified_count:
        return False

    gym_id = venta.get("id_gimnasio")
    ajustar_stock(db, gym_id, venta.get("items"), +1)
    notificar_miembro(
        db, gym_id, venta.get("id_miembro_pg"), "pago_rechazado", "No pudimos confirmar tu pago",
        f"Tu compra ({_resumen(venta.get('items'))}) fue cancelada porque no se verificó el pago."
        + (f" Motivo: {motivo}." if motivo else "") + " Si ya pagaste, acércate a recepción.",
        "venta", str(venta["_id"]),
    )
    return True


def confirmar_renovacion(mdb, solicitud, id_actor, nombre_actor, *, ahora=None):
    """
    Verifica el pago y activa la membresía. Devuelve el dict de activar_renovacion
    o None si la solicitud ya no estaba pendiente. El "claim" atómico va PRIMERO:
    si dos personas confirman a la vez, solo una activa.
    """
    ahora_utc = ahora or utcnow()
    res = mdb.renovaciones_pendientes.update_one(
        {"_id": solicitud["_id"], "estado": PAGO_PENDIENTE},
        {"$set": {"estado": "confirmada", "resuelto_por_pg": id_actor,
                  "resuelto_por_nombre": nombre_actor, "resuelto_en": ahora_utc}},
    )
    if not res.modified_count:
        return None

    gym_id = solicitud["id_gimnasio"]
    miembro = mdb.miembros.find_one({"_id": solicitud["id_miembro"]}) or {"_id": solicitud["id_miembro"]}
    try:
        act = activar_renovacion(
            mdb, gym_id, miembro,
            id_membresia=solicitud["id_membresia"], nombre=solicitud["membresia_nombre"],
            duracion_meses=solicitud["duracion_meses"], monto=solicitud["monto"],
            metodo_pago=solicitud["metodo_pago"], referencia=solicitud.get("referencia"),
        )
    except Exception:
        # No dejar la solicitud "confirmada" sin membresía: se devuelve a pendiente
        # para que el personal pueda reintentar.
        mdb.renovaciones_pendientes.update_one(
            {"_id": solicitud["_id"]},
            {"$set": {"estado": PAGO_PENDIENTE},
             "$unset": {"resuelto_por_pg": "", "resuelto_por_nombre": "", "resuelto_en": ""}},
        )
        raise

    mdb.renovaciones_pendientes.update_one({"_id": solicitud["_id"]}, {"$set": {"id_pago": act["id_pago"]}})
    notificar_miembro(
        mdb, gym_id, solicitud.get("id_miembro_pg"), "renovacion_confirmada", "Membresía renovada",
        f"Confirmamos tu pago. «{solicitud['membresia_nombre']}» vigente hasta "
        f"{act['fecha_fin'].strftime('%d/%m/%Y')}.",
        "renovacion", str(solicitud["_id"]),
    )
    try:
        from app.utils.recibos import enviar_recibo_membresia
        enviar_recibo_membresia(
            gym_id, numero=str(act["id_pago"])[-8:].upper(),
            fecha_str=datetime.now().strftime("%d/%m/%Y %H:%M"),
            membresia_nombre=solicitud["membresia_nombre"], monto=float(solicitud["monto"]),
            metodo_pago=solicitud["metodo_pago"],
            fecha_inicio_str=act["fecha_inicio"].strftime("%d/%m/%Y"),
            fecha_fin_str=act["fecha_fin"].strftime("%d/%m/%Y"),
            nombre_miembro=solicitud.get("nombre_miembro", ""),
            email_miembro=miembro.get("email") or solicitud.get("email_miembro"),
        )
    except Exception as e:
        print(f"[pagos_pendientes] Error enviando recibo de membresía: {e}")
    return act


def rechazar_renovacion(mdb, solicitud, id_actor, nombre_actor, motivo, *, ahora=None):
    ahora = ahora or utcnow()
    res = mdb.renovaciones_pendientes.update_one(
        {"_id": solicitud["_id"], "estado": PAGO_PENDIENTE},
        {"$set": {"estado": "rechazada", "motivo_rechazo": motivo or None,
                  "resuelto_por_pg": id_actor, "resuelto_por_nombre": nombre_actor, "resuelto_en": ahora}},
    )
    if not res.modified_count:
        return False
    notificar_miembro(
        mdb, solicitud["id_gimnasio"], solicitud.get("id_miembro_pg"), "renovacion_rechazada",
        "No pudimos confirmar tu pago",
        f"Tu solicitud de renovación de «{solicitud['membresia_nombre']}» no se activó porque no se verificó el pago."
        + (f" Motivo: {motivo}." if motivo else "") + " Si ya pagaste, acércate a recepción.",
        "renovacion", str(solicitud["_id"]),
    )
    return True
