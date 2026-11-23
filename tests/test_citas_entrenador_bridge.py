"""Regresión: la sesión que agenda el ENTRENADOR debe aparecer en "Mis Citas"
del miembro (coleccion `citas`), no solo en su agenda interna (`sesiones`).

Bug original: create_session() solo escribia en `mdb.sesiones` (indexada por
id_miembro, un ObjectId). El endpoint que lee "Mis Citas"
(compartido/citas_lectura.py, GET /api/user/citas) solo lee `mdb.citas`
(indexada por client_id_pg, un entero de PostgreSQL). Dos colecciones
distintas: el miembro nunca veia las citas de su entrenador.
"""
from datetime import datetime, timedelta

import pytest

pytestmark = pytest.mark.integration

URL_SESSIONS = "/api/trainer/sessions"


@pytest.fixture()
def esc(fab):
    """Gimnasio con un entrenador y un miembro CON cuenta de acceso."""
    gym = fab.gimnasio("Gym Bridge")
    trainer_id, h_trainer = fab.personal("Entrenador", gym)
    miembro_oid, miembro_uid, h_miembro = fab.miembro(gym, nombre="Pablo Sanchez", con_cuenta=True)
    return gym, trainer_id, h_trainer, miembro_oid, miembro_uid, h_miembro


def _mañana():
    return (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d")


def test_sesion_de_entrenador_crea_cita_visible_para_el_miembro(client, mdb, esc):
    gym, trainer_id, h_trainer, miembro_oid, miembro_uid, h_miembro = esc

    r = client.post(URL_SESSIONS, headers=h_trainer, json={
        "fecha": _mañana(), "hora_inicio": "09:00", "tipo": "Personal",
        "id_miembro": str(miembro_oid), "notas": "Primera sesión de evaluación",
    })
    assert r.status_code == 201
    id_sesion = r.get_json()["id_sesion"]

    # La cita debe existir en `citas`, indexada por el id de PostgreSQL del
    # miembro (lo que lee GET /api/user/citas), no por el ObjectId de `sesiones`.
    cita = mdb.citas.find_one({"client_id_pg": miembro_uid})
    assert cita is not None
    assert cita["status"] == "pendiente"
    assert cita["time"] == "09:00"
    assert cita["type"] == "Personal"
    assert str(cita["id_sesion"]) == id_sesion

    # Referencia cruzada bidireccional.
    sesion = mdb.sesiones.find_one({"_id": __import__("bson").ObjectId(id_sesion)})
    assert sesion["id_cita"] == cita["_id"]

    # El miembro recibe una notificación in-app de la cita nueva.
    notif = mdb.notificaciones.find_one({"id_usuario_pg": miembro_uid, "tipo": "cita_nueva"})
    assert notif is not None
    assert notif["referencia_id"] == str(cita["_id"])


def test_sesion_sin_miembro_con_cuenta_no_crea_cita_ni_notificacion(client, mdb, fab, esc):
    """Un miembro sin cuenta de acceso (con_cuenta=False) no tiene id_usuario_pg:
    no hay a quién mostrarle "Mis Citas" ni a quién notificar, así que la
    sesión se queda solo en la agenda interna del entrenador -- comportamiento
    correcto, no un bug."""
    gym, trainer_id, h_trainer, *_ = esc
    miembro_sin_cuenta, _, _ = fab.miembro(gym, nombre="Sin Cuenta", con_cuenta=False)

    r = client.post(URL_SESSIONS, headers=h_trainer, json={
        "fecha": _mañana(), "hora_inicio": "10:00",
        "id_miembro": str(miembro_sin_cuenta),
    })
    assert r.status_code == 201

    assert mdb.citas.count_documents({}) == 0
    assert mdb.notificaciones.count_documents({}) == 0


def test_cancelar_sesion_sincroniza_el_estado_de_la_cita_y_avisa_al_miembro(client, mdb, esc):
    gym, trainer_id, h_trainer, miembro_oid, miembro_uid, h_miembro = esc

    r = client.post(URL_SESSIONS, headers=h_trainer, json={
        "fecha": _mañana(), "hora_inicio": "11:00", "id_miembro": str(miembro_oid),
    })
    id_sesion = r.get_json()["id_sesion"]

    r2 = client.patch(f"{URL_SESSIONS}/{id_sesion}/status", headers=h_trainer, json={"status": "cancelled"})
    assert r2.status_code == 200

    cita = mdb.citas.find_one({"client_id_pg": miembro_uid})
    assert cita["status"] == "cancelada"

    cancelacion = mdb.notificaciones.find_one({"id_usuario_pg": miembro_uid, "tipo": "cita_cancelada"})
    assert cancelacion is not None


def test_completar_sesion_marca_la_cita_como_completada(client, mdb, esc):
    gym, trainer_id, h_trainer, miembro_oid, miembro_uid, h_miembro = esc

    r = client.post(URL_SESSIONS, headers=h_trainer, json={
        "fecha": _mañana(), "hora_inicio": "12:00", "id_miembro": str(miembro_oid),
    })
    id_sesion = r.get_json()["id_sesion"]

    r2 = client.patch(f"{URL_SESSIONS}/{id_sesion}/status", headers=h_trainer, json={"status": "completed"})
    assert r2.status_code == 200

    cita = mdb.citas.find_one({"client_id_pg": miembro_uid})
    assert cita["status"] == "completada"
