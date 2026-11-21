"""DELETE /api/pagos/<id>: un pago se puede eliminar aunque haya otros más
nuevos del mismo miembro; la membresía solo se revierte si es el último."""
from datetime import datetime, timedelta, timezone

import pytest

pytestmark = pytest.mark.integration


def _pago(mdb, gym, miembro, dias_atras, tipo="renovacion"):
    return mdb.pagos.insert_one({
        "id_miembro": miembro, "id_gimnasio": gym, "monto": 500.0, "metodo_pago": "Efectivo",
        "concepto": "Renovación Mensual", "tipo_pago": tipo,
        "fecha_pago": datetime.now(timezone.utc) - timedelta(days=dias_atras),
    }).inserted_id


@pytest.fixture()
def esc(fab, mdb):
    gym = fab.gimnasio("Gym A")
    _, h = fab.personal("Propietario", gym)
    miembro, *_ = fab.miembro(gym, con_cuenta=False)
    return {"gym": gym, "h": h, "miembro": miembro}


def test_elimina_pago_antiguo_sin_tocar_la_membresia_vigente(client, mdb, fab, esc):
    viejo = _pago(mdb, esc["gym"], esc["miembro"], 60)
    nuevo = _pago(mdb, esc["gym"], esc["miembro"], 1)
    mm = fab.membresia(esc["miembro"], datetime.now(timezone.utc) + timedelta(days=29))

    r = client.delete(f"/api/pagos/{viejo}", headers=esc["h"])

    assert r.status_code == 200
    assert r.get_json()["membresia_revertida"] is False
    assert mdb.pagos.count_documents({"_id": viejo}) == 0
    assert mdb.pagos.count_documents({"_id": nuevo}) == 1
    assert mdb.miembro_membresia.find_one({"_id": mm})["estado"] == "Activa"


def test_elimina_el_ultimo_y_revierte_la_membresia(client, mdb, fab, esc):
    _pago(mdb, esc["gym"], esc["miembro"], 60)
    ultimo = _pago(mdb, esc["gym"], esc["miembro"], 1)
    anterior = fab.membresia(esc["miembro"], datetime.now(timezone.utc) - timedelta(days=1), estado="Expirada")
    vigente = fab.membresia(esc["miembro"], datetime.now(timezone.utc) + timedelta(days=29))

    r = client.delete(f"/api/pagos/{ultimo}", headers=esc["h"])

    assert r.status_code == 200
    assert r.get_json()["membresia_revertida"] is True
    assert mdb.miembro_membresia.find_one({"_id": vigente})["estado"] == "Expirada"
    assert mdb.miembro_membresia.find_one({"_id": anterior})["estado"] == "Activa"


def test_pago_de_otro_gimnasio_da_404(client, mdb, fab, esc):
    otro = fab.gimnasio("Gym B")
    otro_miembro, *_ = fab.miembro(otro, con_cuenta=False)
    ajeno = _pago(mdb, otro, otro_miembro, 1)

    assert client.delete(f"/api/pagos/{ajeno}", headers=esc["h"]).status_code == 404
    assert mdb.pagos.count_documents({"_id": ajeno}) == 1


@pytest.mark.parametrize("rol", ["Entrenador", "Miembro"])
def test_entrenador_y_miembro_no_eliminan(client, mdb, fab, esc, rol):
    pago = _pago(mdb, esc["gym"], esc["miembro"], 1)
    _, h = fab.personal(rol, esc["gym"])
    assert client.delete(f"/api/pagos/{pago}", headers=h).status_code == 403
    assert mdb.pagos.count_documents({"_id": pago}) == 1
