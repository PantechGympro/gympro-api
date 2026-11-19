"""Regresión de GET /api/membresias/por-vencer.

Bug original: `fecha_fin` guardada como texto "YYYY-MM-DD" (cobros de recepción
y seeds) nunca coincidía con un rango de datetimes y la lista salía vacía aunque
el dashboard sí contaba esas membresías."""
from datetime import datetime, timedelta, timezone

import pytest

from app.routes.compartido.membresias_staff import DIAS_DEFECTO, DIAS_MAX

URL = "/api/membresias/por-vencer"
pytestmark = pytest.mark.integration


def _ahora():
    return datetime.now(timezone.utc)


@pytest.fixture()
def esc(fab):
    """Escenario: un gimnasio con recepcionista autenticado."""
    gym = fab.gimnasio("Gym A")
    _, h = fab.personal("Recepcionista", gym)
    return gym, h


def _ids(resp):
    return [m["id"] for m in resp.get_json()["miembros"]]


def test_fecha_fin_como_texto_aparece(client, fab, esc):
    gym, h = esc
    oid, *_ = fab.miembro(gym, con_cuenta=False)
    fab.membresia(oid, (_ahora() + timedelta(days=3)).strftime("%Y-%m-%d"))
    r = client.get(URL, headers=h)
    assert r.status_code == 200
    assert _ids(r) == [str(oid)]
    fila = r.get_json()["miembros"][0]
    assert fila["estado"] == "por_vencer"
    assert 2 <= fila["dias_restantes"] <= 3


def test_fecha_fin_datetime_aware_y_naive(client, fab, esc):
    gym, h = esc
    a, *_ = fab.miembro(gym, nombre="Aware", con_cuenta=False)
    b, *_ = fab.miembro(gym, nombre="Naive", con_cuenta=False)
    fab.membresia(a, _ahora() + timedelta(days=2))
    fab.membresia(b, (_ahora() + timedelta(days=4)).replace(tzinfo=None))
    assert set(_ids(client.get(URL, headers=h))) == {str(a), str(b)}


def test_fuera_de_ventana_no_aparece_y_dias_la_amplia(client, fab, esc):
    gym, h = esc
    oid, *_ = fab.miembro(gym, con_cuenta=False)
    fab.membresia(oid, _ahora() + timedelta(days=30))
    assert _ids(client.get(URL, headers=h)) == []
    assert _ids(client.get(URL + "?dias=60", headers=h)) == [str(oid)]


def test_dias_invalido_usa_default_y_se_acota_al_maximo(client, fab, esc):
    _, h = esc
    assert client.get(URL + "?dias=abc", headers=h).get_json()["dias"] == DIAS_DEFECTO
    assert client.get(URL + "?dias=9999", headers=h).get_json()["dias"] == DIAS_MAX
    assert client.get(URL + "?dias=0", headers=h).get_json()["dias"] == 1


def test_renovado_no_aparece_como_por_vencer(client, fab, esc):
    gym, h = esc
    oid, *_ = fab.miembro(gym, con_cuenta=False)
    fab.membresia(oid, _ahora() + timedelta(days=2))
    fab.membresia(oid, _ahora() + timedelta(days=40))   # renovó
    assert _ids(client.get(URL, headers=h)) == []


def test_sin_membresia_no_aparece(client, fab, esc):
    gym, h = esc
    fab.miembro(gym, con_cuenta=False)
    assert _ids(client.get(URL, headers=h)) == []


def test_miembro_inactivo_no_aparece(client, fab, esc, mdb):
    gym, h = esc
    oid, *_ = fab.miembro(gym, con_cuenta=False)
    fab.membresia(oid, _ahora() + timedelta(days=2))
    mdb.miembros.update_one({"_id": oid}, {"$set": {"estado": "Inactivo"}})
    assert _ids(client.get(URL, headers=h)) == []


def test_aislamiento_entre_gimnasios(client, fab, esc):
    gym_a, h_a = esc
    gym_b = fab.gimnasio("Gym B")
    ajeno, *_ = fab.miembro(gym_b, nombre="De B", con_cuenta=False)
    propio, *_ = fab.miembro(gym_a, nombre="De A", con_cuenta=False)
    for oid in (ajeno, propio):
        fab.membresia(oid, _ahora() + timedelta(days=2))
    assert _ids(client.get(URL, headers=h_a)) == [str(propio)]


def test_incluir_vencidas_solo_con_bandera(client, fab, esc):
    gym, h = esc
    oid, *_ = fab.miembro(gym, con_cuenta=False)
    fab.membresia(oid, _ahora() - timedelta(days=2))
    assert _ids(client.get(URL, headers=h)) == []
    r = client.get(URL + "?incluir_vencidas=true", headers=h)
    fila = r.get_json()["miembros"][0]
    assert fila["estado"] == "vencida" and fila["dias_restantes"] < 0


def test_orden_vence_primero(client, fab, esc):
    gym, h = esc
    tarde, *_ = fab.miembro(gym, nombre="Tarde", con_cuenta=False)
    pronto, *_ = fab.miembro(gym, nombre="Pronto", con_cuenta=False)
    fab.membresia(tarde, _ahora() + timedelta(days=6))
    fab.membresia(pronto, _ahora() + timedelta(days=1))
    assert _ids(client.get(URL, headers=h)) == [str(pronto), str(tarde)]


@pytest.mark.parametrize("rol", ["Propietario", "Administrador", "Recepcionista"])
def test_roles_permitidos(client, fab, rol):
    gym = fab.gimnasio()
    _, h = fab.personal(rol, gym)
    assert client.get(URL, headers=h).status_code == 200


@pytest.mark.parametrize("rol", ["Miembro", "Entrenador"])
def test_roles_no_permitidos_reciben_403(client, fab, rol):
    gym = fab.gimnasio()
    _, h = fab.personal(rol, gym)
    assert client.get(URL, headers=h).status_code == 403


def test_sin_token_401(client):
    assert client.get(URL).status_code == 401
