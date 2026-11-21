"""Cancelación del plan y eliminación de cuenta por el propietario.

Antes ninguna de las dos existía en el producto: cancelar había que pedirlo y
eliminar un gimnasio solo era posible con el script de mantenimiento. Lo que se
prueba aquí es lo que el propietario puede hacer solo, y sobre todo lo que NO
debe poder pasar: que un clic borre datos sin confirmación, que un Administrador
elimine la cuenta, o que la cancelación corte el servicio ya pagado."""
from datetime import datetime, timedelta, timezone

import pytest

from app.constants.legal import DIAS_GRACIA_BAJA
from app.extensions import db
from app.models.pg.gimnasio import Gimnasio
from app.models.pg.plan_suscripcion import PlanSuscripcion
from app.models.pg.suscripcion import Suscripcion
from tests.conftest import PASSWORD

pytestmark = pytest.mark.integration

BASE = "/api/owner_gym/cuenta"


@pytest.fixture()
def escenario(app, fab):
    """Gimnasio con plan activo pagado hasta dentro de 20 días y su propietario."""
    gym = fab.gimnasio("Hyper Gym")
    uid, h = fab.personal("Propietario", gym)
    _, h_admin = fab.personal("Administrador", gym)
    with app.app_context():
        plan = PlanSuscripcion.query.filter_by(nombre="pro").first()
        if plan is None:
            plan = PlanSuscripcion(nombre="pro", precio_mensual_mxn=99900)
            db.session.add(plan)
            db.session.flush()
        ahora = datetime.now(timezone.utc)
        sub = Suscripcion(
            id_gimnasio=gym, id_plan=plan.id, estado="active", ciclo_facturacion="mensual",
            fecha_inicio=ahora - timedelta(days=10),
            fecha_proximo_cobro=ahora + timedelta(days=20),
            auto_renovar=True,
        )
        db.session.add(sub)
        db.session.commit()
        return {"gym": gym, "uid": uid, "h": h, "h_admin": h_admin, "sub": sub.id}


# ── Estado ───────────────────────────────────────────────────────────────────

def test_estado_informa_plan_y_lo_que_se_eliminaria(client, fab, escenario):
    gym, h = escenario["gym"], escenario["h"]
    fab.miembro(gym, con_cuenta=False)
    r = client.get(f"{BASE}/estado", headers=h)
    assert r.status_code == 200
    cuerpo = r.get_json()
    assert cuerpo["suscripcion"]["plan"] == "pro"
    assert cuerpo["suscripcion"]["servicio_hasta"]
    assert cuerpo["suscripcion"]["cancelacion_programada"] is False
    assert cuerpo["baja"] is None
    assert cuerpo["dias_gracia"] == DIAS_GRACIA_BAJA
    # Conteos reales, no categorías vacías.
    assert cuerpo["datos"]["miembros"] == 1
    assert cuerpo["datos"]["cuentas_personal"] >= 2


def test_un_recepcionista_no_ve_el_estado_de_la_cuenta(client, fab, escenario):
    _, h = fab.personal("Recepcionista", escenario["gym"])
    assert client.get(f"{BASE}/estado", headers=h).status_code == 403


# ── Cancelar la suscripción ──────────────────────────────────────────────────

def test_cancelar_conserva_el_servicio_hasta_lo_pagado_y_no_borra_datos(app, client, escenario):
    r = client.post(f"{BASE}/suscripcion/cancelar", json={"motivo": "Cerramos en diciembre"},
                    headers=escenario["h"])
    assert r.status_code == 200
    cuerpo = r.get_json()
    assert cuerpo["datos_eliminados"] is False

    with app.app_context():
        sub = db.session.get(Suscripcion, escenario["sub"])
        assert sub.auto_renovar is False
        # El corte es la fecha ya pagada, no hoy.
        fin = sub.fecha_fin if sub.fecha_fin.tzinfo else sub.fecha_fin.replace(tzinfo=timezone.utc)
        assert fin > datetime.now(timezone.utc) + timedelta(days=19)
        # Nada se eliminó: el gimnasio sigue ahí.
        assert db.session.get(Gimnasio, escenario["gym"]) is not None

    estado = client.get(f"{BASE}/estado", headers=escenario["h"]).get_json()
    assert estado["suscripcion"]["cancelacion_programada"] is True


def test_cancelar_dos_veces_responde_conflicto(app, client, escenario):
    with app.app_context():
        sub = db.session.get(Suscripcion, escenario["sub"])
        sub.estado = "cancelled"
        db.session.commit()
    r = client.post(f"{BASE}/suscripcion/cancelar", json={}, headers=escenario["h"])
    assert r.status_code == 409


def test_reactivar_deshace_la_cancelacion(app, client, escenario):
    client.post(f"{BASE}/suscripcion/cancelar", json={}, headers=escenario["h"])
    r = client.post(f"{BASE}/suscripcion/reactivar", json={}, headers=escenario["h"])
    assert r.status_code == 200
    with app.app_context():
        assert db.session.get(Suscripcion, escenario["sub"]).fecha_fin is None


def test_no_se_reactiva_un_periodo_ya_vencido(app, client, escenario):
    with app.app_context():
        sub = db.session.get(Suscripcion, escenario["sub"])
        sub.fecha_fin = datetime.now(timezone.utc) - timedelta(days=1)
        db.session.commit()
    assert client.post(f"{BASE}/suscripcion/reactivar", json={}, headers=escenario["h"]).status_code == 409


# ── Eliminar la cuenta ───────────────────────────────────────────────────────

def test_eliminar_exige_contrasena_y_palabra_de_confirmacion(app, client, escenario):
    h = escenario["h"]
    assert client.post(f"{BASE}/eliminar", json={}, headers=h).status_code == 403
    assert client.post(f"{BASE}/eliminar", json={"password": "otra", "confirmacion": "ELIMINAR"},
                       headers=h).status_code == 403
    assert client.post(f"{BASE}/eliminar", json={"password": PASSWORD, "confirmacion": "si"},
                       headers=h).status_code == 400
    # Nada se programó con los intentos fallidos.
    with app.app_context():
        assert db.session.get(Gimnasio, escenario["gym"]).baja_programada_para is None


def test_un_administrador_no_puede_eliminar_la_cuenta(client, escenario):
    r = client.post(f"{BASE}/eliminar", json={"password": PASSWORD, "confirmacion": "ELIMINAR"},
                    headers=escenario["h_admin"])
    assert r.status_code == 403


def test_eliminar_programa_la_baja_con_margen_y_no_borra_todavia(app, client, escenario):
    r = client.post(f"{BASE}/eliminar",
                    json={"password": PASSWORD, "confirmacion": "eliminar", "motivo": "Ya no lo uso"},
                    headers=escenario["h"])
    assert r.status_code == 200
    assert r.get_json()["dias_gracia"] == DIAS_GRACIA_BAJA

    with app.app_context():
        gym = db.session.get(Gimnasio, escenario["gym"])
        assert gym is not None                      # todavía existe
        assert gym.baja_motivo == "Ya no lo uso"
        # SQLite devuelve la fecha sin zona; en Postgres llega con ella.
        programada = gym.baja_programada_para
        if programada.tzinfo is None:
            programada = programada.replace(tzinfo=timezone.utc)
        faltan = (programada - datetime.now(timezone.utc)).days
        assert DIAS_GRACIA_BAJA - 1 <= faltan <= DIAS_GRACIA_BAJA
        # Y deja de cobrarse a quien ya pidió irse.
        assert db.session.get(Suscripcion, escenario["sub"]).auto_renovar is False

    estado = client.get(f"{BASE}/estado", headers=escenario["h"]).get_json()
    assert estado["baja"]["dias_restantes"] >= DIAS_GRACIA_BAJA - 1


def test_no_se_programa_dos_veces(client, escenario):
    datos = {"password": PASSWORD, "confirmacion": "ELIMINAR"}
    assert client.post(f"{BASE}/eliminar", json=datos, headers=escenario["h"]).status_code == 200
    assert client.post(f"{BASE}/eliminar", json=datos, headers=escenario["h"]).status_code == 409


def test_cancelar_la_eliminacion_conserva_la_cuenta(app, client, escenario):
    client.post(f"{BASE}/eliminar", json={"password": PASSWORD, "confirmacion": "ELIMINAR"},
                headers=escenario["h"])
    r = client.post(f"{BASE}/eliminar/cancelar", json={}, headers=escenario["h"])
    assert r.status_code == 200
    with app.app_context():
        gym = db.session.get(Gimnasio, escenario["gym"])
        assert gym.baja_programada_para is None and gym.baja_motivo is None
    # Sin baja pendiente, cancelarla de nuevo no tiene sentido.
    assert client.post(f"{BASE}/eliminar/cancelar", json={}, headers=escenario["h"]).status_code == 404


def test_exportar_entrega_los_datos_como_descarga(client, fab, escenario):
    fab.miembro(escenario["gym"], nombre="Ana Export", con_cuenta=False)
    r = client.get(f"{BASE}/exportar", headers=escenario["h"])
    assert r.status_code == 200
    assert "attachment" in r.headers.get("Content-Disposition", "")
    cuerpo = r.get_json()
    assert [m["nombre"] for m in cuerpo["miembros"]] == ["Ana Export"]
    assert cuerpo["gimnasios"] and cuerpo["personal"]
    # Nada de secretos en la copia.
    texto = r.get_data(as_text=True)
    assert "password_hash" not in texto


# ── Purga definitiva ─────────────────────────────────────────────────────────

def test_la_purga_solo_toca_las_bajas_vencidas(app, client, fab, escenario):
    """El proceso programado no debe adelantarse ni llevarse a otro gimnasio."""
    otro = fab.gimnasio("Gym Vecino")
    fab.miembro(otro, nombre="Intacto", con_cuenta=False)
    fab.miembro(escenario["gym"], nombre="Se va", con_cuenta=False)
    client.post(f"{BASE}/eliminar", json={"password": PASSWORD, "confirmacion": "ELIMINAR"},
                headers=escenario["h"])

    from app.routes.owner_gym.cuenta import ejecutar_bajas_programadas
    with app.app_context():
        # Aún no vence: no se purga nada.
        assert ejecutar_bajas_programadas(app=app)["gimnasios"] == 0
        assert db.session.get(Gimnasio, escenario["gym"]) is not None

        # Se cumple el plazo.
        gym = db.session.get(Gimnasio, escenario["gym"])
        gym.baja_programada_para = datetime.now(timezone.utc) - timedelta(minutes=1)
        db.session.commit()

        resultado = ejecutar_bajas_programadas(app=app)
        assert resultado["gimnasios"] == 1 and not resultado["errores"]
        assert db.session.get(Gimnasio, escenario["gym"]) is None
        assert db.session.get(Gimnasio, otro) is not None      # el vecino, intacto

        from app.mongo import get_db
        mdb = get_db()
        nombres = {m["nombre"] for m in mdb.miembros.find({}, {"nombre": 1})}
        assert nombres == {"Intacto"}


def test_la_purga_no_la_dispara_cualquiera(client, fab, escenario):
    assert client.post(f"{BASE}/bajas/procesar", headers=escenario["h"]).status_code == 403
    _, h_super = fab.personal("superadmin", None)
    assert client.post(f"{BASE}/bajas/procesar", headers=h_super).status_code == 200
