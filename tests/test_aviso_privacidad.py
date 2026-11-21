"""Aceptación del aviso de privacidad (migración 036).

La tabla 3.2.3 del marco normativo afirmaba que el alta registra "su aceptación,
con fecha y versión del texto aceptado". No existía la columna ni el endpoint:
el portal solo enlazaba el aviso. Esto cubre el control que faltaba."""
import pytest

from app.constants.legal import VERSION_AVISO
from app.extensions import db
from app.models.pg.usuario import Usuario

pytestmark = pytest.mark.integration

URL = "/api/auth/aviso-privacidad"


@pytest.fixture()
def propietario(fab):
    gym = fab.gimnasio("Gym A")
    uid, h = fab.personal("Propietario", gym)
    return gym, uid, h


def test_me_reporta_el_aviso_pendiente_antes_de_aceptarlo(client, propietario):
    _, _, h = propietario
    estado = client.get("/api/auth/me", headers=h).get_json()["aviso_privacidad"]
    assert estado["pendiente"] is True
    assert estado["version_vigente"] == VERSION_AVISO
    assert estado["version_aceptada"] is None and estado["aceptado_en"] is None


def test_aceptar_registra_version_y_fecha_y_deja_de_pedirlo(app, client, propietario):
    _, uid, h = propietario
    r = client.post(URL, json={"version": VERSION_AVISO}, headers=h)
    assert r.status_code == 200
    cuerpo = r.get_json()
    assert cuerpo["pendiente"] is False
    assert cuerpo["version_aceptada"] == VERSION_AVISO
    assert cuerpo["aceptado_en"]

    # Persistido, no solo en la respuesta.
    with app.app_context():
        u = db.session.get(Usuario, uid)
        assert u.aviso_version == VERSION_AVISO and u.aviso_aceptado_en is not None

    assert client.get("/api/auth/me", headers=h).get_json()["aviso_privacidad"]["pendiente"] is False


def test_una_version_distinta_se_rechaza(client, propietario):
    _, _, h = propietario
    r = client.post(URL, json={"version": "0.9"}, headers=h)
    assert r.status_code == 409
    assert r.get_json()["version_vigente"] == VERSION_AVISO


def test_sin_sesion_no_se_puede_aceptar(client):
    assert client.post(URL, json={"version": VERSION_AVISO}).status_code == 401


def test_al_cambiar_la_version_vigente_se_vuelve_a_pedir(app, client, propietario, monkeypatch):
    _, _, h = propietario
    client.post(URL, json={"version": VERSION_AVISO}, headers=h)
    # Se publica un aviso nuevo: la aceptación anterior ya no vale.
    monkeypatch.setattr("app.models.pg.usuario.VERSION_AVISO", "9.9", raising=False)
    monkeypatch.setattr("app.auth.routes.VERSION_AVISO", "9.9")
    import app.constants.legal as legal
    monkeypatch.setattr(legal, "VERSION_AVISO", "9.9")
    estado = client.get("/api/auth/me", headers=h).get_json()["aviso_privacidad"]
    assert estado["version_vigente"] == "9.9"
    assert estado["pendiente"] is True
    assert estado["version_aceptada"] == VERSION_AVISO


def test_queda_constancia_en_la_bitacora(app, client, propietario):
    _, uid, h = propietario
    client.post(URL, json={"version": VERSION_AVISO}, headers=h)
    with app.app_context():
        from app.models.pg.bitacora_evento import BitacoraEvento
        evento = (BitacoraEvento.query
                  .filter_by(accion="aviso_privacidad_aceptado", id_usuario_actor=uid).first())
        assert evento is not None
        assert evento.detalle["version"] == VERSION_AVISO
