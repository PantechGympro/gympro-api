"""Recordatorios de "Mis Citas" a 7, 3 y 1 día(s) antes
(app/routes/compartido/recordatorios_citas.py)."""
from datetime import timedelta

import pytest

from app.routes.compartido.recordatorios_citas import (
    DIAS_AVISO, chequear_recordatorios_citas,
)
from app.utils.timezone import local_now_naive

pytestmark = pytest.mark.integration


def _fecha(dias_desde_hoy):
    return (local_now_naive().date() + timedelta(days=dias_desde_hoy)).strftime("%Y-%m-%d")


def _cita(mdb, miembro_uid, gym_id, dias_desde_hoy, status="pendiente", trainer=None):
    return mdb.citas.insert_one({
        "id_gimnasio_pg": gym_id, "date": _fecha(dias_desde_hoy), "time": "09:00",
        "client": "Cliente", "client_id_pg": miembro_uid, "type": "Personal",
        "trainer": trainer, "status": status,
    }).inserted_id


@pytest.fixture()
def esc(fab):
    gym = fab.gimnasio("Gym Recordatorios")
    _, miembro_uid, _ = fab.miembro(gym, con_cuenta=True)
    return gym, miembro_uid


def test_avisa_en_cada_uno_de_los_tres_horizontes(app, mdb, esc):
    gym, miembro_uid = esc
    for dias in DIAS_AVISO:
        _cita(mdb, miembro_uid, gym, dias)

    resultado = chequear_recordatorios_citas(app=app)

    assert resultado["recordatorios_enviados"] == len(DIAS_AVISO)
    for dias in DIAS_AVISO:
        tipo = f"recordatorio_cita_{dias}d"
        assert mdb.notificaciones.count_documents({"id_usuario_pg": miembro_uid, "tipo": tipo}) == 1


@pytest.mark.parametrize("dias_fuera", [0, 2, 4, 5, 6, 8, 10])
def test_no_avisa_fuera_de_los_tres_horizontes(app, mdb, esc, dias_fuera):
    gym, miembro_uid = esc
    _cita(mdb, miembro_uid, gym, dias_fuera)

    resultado = chequear_recordatorios_citas(app=app)

    assert resultado["recordatorios_enviados"] == 0
    assert mdb.notificaciones.count_documents({}) == 0


@pytest.mark.parametrize("estado", ["cancelada", "completada"])
def test_no_avisa_citas_canceladas_o_completadas(app, mdb, esc, estado):
    gym, miembro_uid = esc
    _cita(mdb, miembro_uid, gym, 7, status=estado)

    resultado = chequear_recordatorios_citas(app=app)

    assert resultado["recordatorios_enviados"] == 0


def test_no_avisa_cita_sin_client_id_pg(app, mdb, esc):
    """Una cita creada para un miembro SIN cuenta (client_id_pg=None, caso de
    recepcionista_routes con un miembro sin usuario PG) no debe reventar el
    job ni generar una notificación huérfana."""
    gym, _ = esc
    mdb.citas.insert_one({
        "id_gimnasio_pg": gym, "date": _fecha(7), "time": "09:00",
        "client": "Sin cuenta", "client_id_pg": None, "type": "Personal",
        "status": "pendiente",
    })

    resultado = chequear_recordatorios_citas(app=app)

    assert resultado["recordatorios_enviados"] == 0
    assert mdb.notificaciones.count_documents({}) == 0


def test_es_idempotente_si_se_ejecuta_dos_veces_el_mismo_dia(app, mdb, esc):
    gym, miembro_uid = esc
    _cita(mdb, miembro_uid, gym, 3)

    primero  = chequear_recordatorios_citas(app=app)
    segundo  = chequear_recordatorios_citas(app=app)

    assert primero["recordatorios_enviados"] == 1
    assert segundo["recordatorios_enviados"] == 0
    assert mdb.notificaciones.count_documents({"tipo": "recordatorio_cita_3d"}) == 1


def test_el_mensaje_menciona_al_entrenador_cuando_lo_hay(app, mdb, esc):
    gym, miembro_uid = esc
    _cita(mdb, miembro_uid, gym, 1, trainer="Juan Pérez")

    chequear_recordatorios_citas(app=app)

    notif = mdb.notificaciones.find_one({"id_usuario_pg": miembro_uid, "tipo": "recordatorio_cita_1d"})
    assert "mañana" in notif["mensaje"]
    assert "Juan Pérez" in notif["mensaje"]
