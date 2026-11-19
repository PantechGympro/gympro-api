"""Pagos manuales por confirmar (ventas y renovaciones): permisos, aislamiento,
idempotencia ante doble clic y efectos (stock, membresía, pago)."""
from datetime import datetime, timedelta, timezone

import pytest
from bson import ObjectId

from app.utils.pagos_pendientes import (
    PAGO_PENDIENTE, activar_renovacion, ajustar_stock, puede_confirmar_pago,
)

BASE = "/api/confirmaciones-pago"
pytestmark = pytest.mark.integration


def _ahora():
    return datetime.now(timezone.utc)


@pytest.fixture()
def esc(fab, mdb):
    """Gimnasio con recepcionista + una venta pendiente (2 aguas) y producto con stock."""
    gym = fab.gimnasio("Gym A")
    _, h = fab.personal("Recepcionista", gym)
    prod = mdb.productos.insert_one({"id_gimnasio": gym, "nombre": "Agua", "stock": 8, "precio": 20}).inserted_id
    venta = mdb.ventas.insert_one({
        "id_gimnasio": gym, "estado_pago": PAGO_PENDIENTE, "estado_entrega": "pendiente",
        "total": 40.0, "metodo_pago": "Efectivo", "fecha": _ahora(), "nombre_miembro": "Ana",
        "items": [{"id": str(prod), "nombre": "Agua", "qty": 2}],
    }).inserted_id
    return {"gym": gym, "h": h, "prod": prod, "venta": venta}


def _renovacion(mdb, gym, oid, **extra):
    doc = {
        "id_gimnasio": gym, "estado": PAGO_PENDIENTE, "id_miembro": oid, "id_membresia": 1,
        "membresia_nombre": "Mensual", "duracion_meses": 1, "monto": 500.0, "metodo_pago": "Efectivo",
        "creado_en": _ahora(), "nombre_miembro": "Ana",
    }
    doc.update(extra)
    return mdb.renovaciones_pendientes.insert_one(doc).inserted_id


# ── Permisos ────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("rol,esperado", [
    ("Recepcionista", 200), ("Administrador", 200), ("Propietario", 200),
    ("Entrenador", 403), ("Miembro", 403),
])
def test_listar_segun_rol(client, fab, rol, esperado):
    gym = fab.gimnasio()
    _, h = fab.personal(rol, gym)
    assert client.get(BASE, headers=h).status_code == esperado


def test_sin_token_401(client):
    assert client.get(BASE).status_code == 401


@pytest.mark.parametrize("rol", ["Entrenador", "Miembro"])
def test_entrenador_y_miembro_no_confirman(client, fab, esc, rol):
    _, h = fab.personal(rol, esc["gym"])
    r = client.post(f"{BASE}/ventas/{esc['venta']}/confirmar", headers=h)
    assert r.status_code == 403


# ── Bandeja ─────────────────────────────────────────────────────────────────
def test_listado_y_resumen_solo_del_propio_gimnasio(client, fab, mdb, esc):
    otro = fab.gimnasio("Gym B")
    mdb.ventas.insert_one({"id_gimnasio": otro, "estado_pago": PAGO_PENDIENTE, "total": 1, "items": []})
    oid, *_ = fab.miembro(esc["gym"], con_cuenta=False)
    _renovacion(mdb, esc["gym"], oid)
    data = client.get(BASE, headers=esc["h"]).get_json()
    assert (len(data["ventas"]), len(data["renovaciones"]), data["total"]) == (1, 1, 2)
    assert client.get(f"{BASE}/resumen", headers=esc["h"]).get_json() == {"ventas": 1, "renovaciones": 1, "total": 2}


# ── Ventas ──────────────────────────────────────────────────────────────────
def test_confirmar_venta_la_marca_pagada(client, mdb, esc):
    r = client.post(f"{BASE}/ventas/{esc['venta']}/confirmar", headers=esc["h"])
    assert r.status_code == 200 and r.get_json()["estado_pago"] == "pagado"
    v = mdb.ventas.find_one({"_id": esc["venta"]})
    assert v["estado_pago"] == "pagado" and v["pagado_en"] and v["cobrado_por_pg"]


def test_doble_confirmacion_da_409(client, esc):
    url = f"{BASE}/ventas/{esc['venta']}/confirmar"
    assert client.post(url, headers=esc["h"]).status_code == 200
    assert client.post(url, headers=esc["h"]).status_code == 409


def test_venta_de_otro_gimnasio_o_id_invalido_404(client, fab, mdb, esc):
    otro = fab.gimnasio("Gym B")
    ajena = mdb.ventas.insert_one({"id_gimnasio": otro, "estado_pago": PAGO_PENDIENTE, "items": []}).inserted_id
    assert client.post(f"{BASE}/ventas/{ajena}/confirmar", headers=esc["h"]).status_code == 404
    assert client.post(f"{BASE}/ventas/no-es-oid/confirmar", headers=esc["h"]).status_code == 404
    assert mdb.ventas.find_one({"_id": ajena})["estado_pago"] == PAGO_PENDIENTE


def test_rechazar_venta_cancela_entrega_y_devuelve_stock(client, mdb, esc):
    r = client.post(f"{BASE}/ventas/{esc['venta']}/rechazar", headers=esc["h"], json={"motivo": "No llegó el SPEI"})
    assert r.status_code == 200 and r.get_json()["estado_pago"] == "rechazado"
    v = mdb.ventas.find_one({"_id": esc["venta"]})
    assert v["estado_pago"] == "rechazado" and v["estado_entrega"] == "cancelado"
    assert v["motivo_rechazo"] == "No llegó el SPEI"
    assert mdb.productos.find_one({"_id": esc["prod"]})["stock"] == 10          # 8 + 2 devueltas
    assert client.post(f"{BASE}/ventas/{esc['venta']}/rechazar", headers=esc["h"]).status_code == 409


def test_no_se_puede_confirmar_una_venta_ya_rechazada(client, esc):
    client.post(f"{BASE}/ventas/{esc['venta']}/rechazar", headers=esc["h"])
    assert client.post(f"{BASE}/ventas/{esc['venta']}/confirmar", headers=esc["h"]).status_code == 409


# ── Renovaciones ────────────────────────────────────────────────────────────
def test_confirmar_renovacion_activa_membresia_y_registra_pago(client, fab, mdb, esc):
    oid, *_ = fab.miembro(esc["gym"], con_cuenta=False)
    sol = _renovacion(mdb, esc["gym"], oid)
    r = client.post(f"{BASE}/renovaciones/{sol}/confirmar", headers=esc["h"])
    assert r.status_code == 200 and r.get_json()["membresia"]["nombre"] == "Mensual"
    activa = mdb.miembro_membresia.find_one({"id_miembro": oid, "estado": "Activa"})
    assert activa and activa["fecha_fin"] > activa["fecha_inicio"]
    pago = mdb.pagos.find_one({"id_miembro": oid})
    assert pago["monto"] == 500.0 and pago["metodo_pago"] == "Efectivo"
    assert mdb.renovaciones_pendientes.find_one({"_id": sol})["estado"] == "confirmada"


def test_doble_confirmacion_de_renovacion_no_duplica_membresia(client, fab, mdb, esc):
    oid, *_ = fab.miembro(esc["gym"], con_cuenta=False)
    sol = _renovacion(mdb, esc["gym"], oid)
    url = f"{BASE}/renovaciones/{sol}/confirmar"
    assert client.post(url, headers=esc["h"]).status_code == 200
    assert client.post(url, headers=esc["h"]).status_code == 409
    assert mdb.miembro_membresia.count_documents({"id_miembro": oid}) == 1
    assert mdb.pagos.count_documents({"id_miembro": oid}) == 1


def test_rechazar_renovacion_no_activa_nada(client, fab, mdb, esc):
    oid, *_ = fab.miembro(esc["gym"], con_cuenta=False)
    sol = _renovacion(mdb, esc["gym"], oid)
    assert client.post(f"{BASE}/renovaciones/{sol}/rechazar", headers=esc["h"], json={"motivo": "x"}).status_code == 200
    assert mdb.miembro_membresia.count_documents({"id_miembro": oid}) == 0
    assert mdb.renovaciones_pendientes.find_one({"_id": sol})["estado"] != PAGO_PENDIENTE
    assert client.post(f"{BASE}/renovaciones/{sol}/rechazar", headers=esc["h"]).status_code == 409


def test_renovacion_de_otro_gimnasio_404(client, fab, mdb, esc):
    otro = fab.gimnasio("Gym B")
    oid, *_ = fab.miembro(otro, con_cuenta=False)
    sol = _renovacion(mdb, otro, oid)
    assert client.post(f"{BASE}/renovaciones/{sol}/confirmar", headers=esc["h"]).status_code == 404


# ── Reglas puras ────────────────────────────────────────────────────────────
@pytest.mark.unit
@pytest.mark.parametrize("rol,ok", [
    ("Recepcionista", True), ("owner_gym", True), ("admin", True),
    ("Entrenador", False), ("Miembro", False), ("", False), (None, False),
])
def test_puede_confirmar_pago(rol, ok):
    assert puede_confirmar_pago({"role": rol}) is ok
    assert puede_confirmar_pago(None) is False


def test_renovar_con_membresia_vigente_encadena_dias_pagados(mdb):
    miembro_id = ObjectId()
    ahora = datetime(2026, 11, 18, 12, 0)
    mdb.miembro_membresia.insert_one({"id_miembro": miembro_id, "estado": "Activa", "fecha_fin": "2026-11-25"})
    act = activar_renovacion(mdb, 1, {"_id": miembro_id}, id_membresia=1, nombre="Mensual",
                             duracion_meses=1, monto=500, metodo_pago="Efectivo", ahora=ahora)
    assert act["fecha_inicio"] == datetime(2026, 11, 26)          # al terminar la actual, sin perder días
    assert act["fecha_fin"] == datetime(2026, 11, 26) + timedelta(days=30)
    estados = sorted(d["estado"] for d in mdb.miembro_membresia.find({"id_miembro": miembro_id}))
    assert estados == ["Activa", "Vencida"]


def test_ajustar_stock_nunca_baja_de_cero_y_combos_mueven_componentes(mdb):
    a = mdb.productos.insert_one({"id_gimnasio": 1, "stock": 1}).inserted_id
    b = mdb.productos.insert_one({"id_gimnasio": 1, "stock": 5}).inserted_id
    combo = mdb.productos.insert_one({"id_gimnasio": 1, "es_combo": True,
                                      "items_combo": [{"id_producto": str(b), "cantidad": 2}]}).inserted_id
    ajustar_stock(mdb, 1, [{"id": str(a), "qty": 3}, {"id": str(combo), "qty": 2}], signo=-1)
    assert mdb.productos.find_one({"_id": a})["stock"] == 0        # piso en 0
    assert mdb.productos.find_one({"_id": b})["stock"] == 1        # 5 - 2*2
    ajustar_stock(mdb, 1, [{"id": str(combo), "qty": 2}], signo=+1)
    assert mdb.productos.find_one({"_id": b})["stock"] == 5
    ajustar_stock(mdb, 1, [{"id": "basura"}, {}], signo=-1)         # no revienta con datos corruptos
