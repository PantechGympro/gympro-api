"""GET /api/owner_gym/alertas — alertas del panel del Propietario.

Bug original: las alertas de membresías consultaban `miembro_membresias`
(plural, estado "activa", con id_gimnasio), colección y campos que no existen,
así que nunca contaban nada. Ahora usan la regla única de utils/membresia.py
(la misma que el listado "Por vencer"). Además se añadió la alerta de entregas
pendientes del punto de venta."""
from datetime import datetime, timedelta, timezone

import pytest

from app.utils.timezone import local_now_naive

URL = "/api/owner_gym/alertas"
pytestmark = pytest.mark.integration


def _ahora():
    return datetime.now(timezone.utc)


@pytest.fixture()
def esc(fab):
    gym = fab.gimnasio("Gym A")
    _, h = fab.personal("Propietario", gym)
    return gym, h


def _tipos(client, h):
    return {a["tipo"]: a for a in client.get(URL, headers=h).get_json()["alertas"]}


def test_sin_datos_no_hay_alertas(client, esc):
    _, h = esc
    r = client.get(URL, headers=h)
    assert r.status_code == 200
    assert r.get_json() == {"alertas": [], "total": 0}


def test_membresias_por_vencer_se_cuentan_con_fecha_texto_y_datetime(client, fab, esc):
    gym, h = esc
    a, *_ = fab.miembro(gym, nombre="A", con_cuenta=False)
    b, *_ = fab.miembro(gym, nombre="B", con_cuenta=False)
    c, *_ = fab.miembro(gym, nombre="C", con_cuenta=False)
    fab.membresia(a, (_ahora() + timedelta(days=3)).strftime("%Y-%m-%d"))   # texto
    fab.membresia(b, _ahora() + timedelta(days=2))                          # datetime
    fab.membresia(c, _ahora() + timedelta(days=30))                         # fuera de ventana
    al = _tipos(client, h)["membresias_por_vencer"]
    assert al["nivel"] == "warning"
    assert al["titulo"].startswith("2 membresías")
    assert al["ruta"] == "/owner/por-vencer"
    assert "membresias_vencidas" not in _tipos(client, h)


def test_renovado_no_cuenta_y_vencida_reciente_si(client, fab, esc):
    gym, h = esc
    renovado, *_ = fab.miembro(gym, nombre="R", con_cuenta=False)
    fab.membresia(renovado, _ahora() + timedelta(days=2))
    fab.membresia(renovado, _ahora() + timedelta(days=40))                  # ya renovó
    caido, *_ = fab.miembro(gym, nombre="V", con_cuenta=False)
    fab.membresia(caido, _ahora() - timedelta(days=3))                      # venció hace 3 días
    antiguo, *_ = fab.miembro(gym, nombre="X", con_cuenta=False)
    fab.membresia(antiguo, _ahora() - timedelta(days=90))                   # venció hace meses
    t = _tipos(client, h)
    assert "membresias_por_vencer" not in t
    assert t["membresias_vencidas"]["nivel"] == "error"
    assert t["membresias_vencidas"]["titulo"].startswith("1 membresía vencida")


def test_otro_gimnasio_no_se_mezcla(client, fab, esc):
    _, h = esc
    otro = fab.gimnasio("Gym B")
    m, *_ = fab.miembro(otro, con_cuenta=False)
    fab.membresia(m, _ahora() + timedelta(days=2))
    assert client.get(URL, headers=h).get_json()["alertas"] == []


def _venta(mdb, gym, hora_delta_min, estado_entrega="pendiente"):
    ref = local_now_naive() + timedelta(minutes=hora_delta_min)
    mdb.ventas.insert_one({
        "id_gimnasio": gym, "estado_entrega": estado_entrega,
        "fecha_recogida": ref.strftime("%Y-%m-%d"), "hora_recogida": ref.strftime("%H:%M"),
        "nombre_miembro": "Ana", "items": [], "total": 10,
    })


def test_entregas_pendientes_warning_y_error_si_pasaron_de_hora(client, mdb, esc):
    gym, h = esc
    _venta(mdb, gym, +180)                                  # aún en tiempo
    _venta(mdb, gym, +240, estado_entrega="entregada")      # ya entregada: no cuenta
    al = _tipos(client, h)["entregas_pendientes"]
    assert al["nivel"] == "warning" and al["titulo"].startswith("1 entrega pendiente")
    assert al["ruta"] == "/owner/deliveries"

    _venta(mdb, gym, -180)                                  # pasó la hora acordada
    al = _tipos(client, h)["entregas_pendientes"]
    assert al["nivel"] == "error"
    assert al["titulo"].startswith("2 entregas pendientes")
    assert "1 ya pasó la hora" in al["detalle"]


def test_entregas_de_otro_gimnasio_no_cuentan(client, fab, mdb, esc):
    _, h = esc
    _venta(mdb, fab.gimnasio("Gym B"), -10)
    assert "entregas_pendientes" not in _tipos(client, h)
