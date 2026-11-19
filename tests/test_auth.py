"""Autenticación: login, /me, rotación de refresh con detección de reuso, logout."""
import pytest

from tests.conftest import PASSWORD

pytestmark = pytest.mark.integration


@pytest.fixture()
def cuenta(fab):
    gym = fab.gimnasio()
    uid, email = fab.usuario("Recepcionista", gym)
    return gym, uid, email


def _login(client, email, password=PASSWORD):
    return client.post("/api/auth/login", json={"email": email, "password": password})


def test_login_correcto_devuelve_tokens_y_usuario(client, cuenta):
    _, _, email = cuenta
    r = _login(client, email)
    body = r.get_json()
    assert r.status_code == 200
    assert body["access_token"] and body["refresh_token"]


def test_login_email_no_distingue_mayusculas_ni_espacios(client, cuenta):
    _, _, email = cuenta
    assert _login(client, f"  {email.upper()} ").status_code == 200


def test_login_password_incorrecta_401(client, cuenta):
    assert _login(client, cuenta[2], "otra-clave").status_code == 401


def test_login_usuario_inexistente_401_mismo_mensaje(client, cuenta):
    malo = _login(client, "nadie@prueba.test").get_json()["msg"]
    mala_pw = _login(client, cuenta[2], "x").get_json()["msg"]
    assert malo == mala_pw == "Credenciales inválidas"      # no revela si el correo existe


@pytest.mark.parametrize("payload", [{}, {"email": "a@b.c"}, {"password": "x"}, None])
def test_login_datos_incompletos_400(client, payload):
    r = client.post("/api/auth/login", json=payload) if payload is not None else client.post("/api/auth/login")
    assert r.status_code == 400


def test_login_usuario_inactivo_403(client, fab):
    gym = fab.gimnasio()
    _, email = fab.usuario("Recepcionista", gym, activo=False)
    assert _login(client, email).status_code == 403


def test_login_gimnasio_inactivo_403(client, fab, app):
    from app.extensions import db
    from app.models.pg.gimnasio import Gimnasio
    gym = fab.gimnasio()
    _, email = fab.usuario("Recepcionista", gym)
    with app.app_context():
        Gimnasio.query.get(gym).activo = False
        db.session.commit()
    assert _login(client, email).status_code == 403


def test_me_requiere_token(client):
    assert client.get("/api/auth/me").status_code == 401


def test_me_con_token(client, fab, cuenta):
    h = fab.headers(cuenta[2])
    r = client.get("/api/auth/me", headers=h)
    assert r.status_code == 200
    assert r.get_json()["email"] == cuenta[2]


def test_access_token_no_sirve_para_refresh(client, cuenta):
    acceso = _login(client, cuenta[2]).get_json()["access_token"]
    r = client.post("/api/auth/refresh", headers={"Authorization": f"Bearer {acceso}"})
    assert r.status_code in (401, 422)


def test_refresh_rota_el_token(client, cuenta):
    refresh = _login(client, cuenta[2]).get_json()["refresh_token"]
    r = client.post("/api/auth/refresh", headers={"Authorization": f"Bearer {refresh}"})
    assert r.status_code == 200
    nuevo = r.get_json()
    assert nuevo["access_token"] and nuevo["refresh_token"] and nuevo["refresh_token"] != refresh


def test_reuso_de_refresh_revoca_toda_la_familia(client, cuenta):
    r1 = _login(client, cuenta[2]).get_json()["refresh_token"]
    r2 = client.post("/api/auth/refresh", headers={"Authorization": f"Bearer {r1}"}).get_json()["refresh_token"]
    # Reuso del token ya canjeado → 401 y se invalida la familia
    assert client.post("/api/auth/refresh", headers={"Authorization": f"Bearer {r1}"}).status_code == 401
    # El token "legítimo" más reciente también queda revocado
    assert client.post("/api/auth/refresh", headers={"Authorization": f"Bearer {r2}"}).status_code == 401


def test_logout_revoca_el_refresh(client, cuenta):
    refresh = _login(client, cuenta[2]).get_json()["refresh_token"]
    h = {"Authorization": f"Bearer {refresh}"}
    assert client.post("/api/auth/logout", headers=h).status_code == 200
    assert client.post("/api/auth/refresh", headers=h).status_code == 401


def test_fuerza_bruta_a_una_cuenta_se_limita_con_429(client, cuenta):
    codigos = [_login(client, cuenta[2], "mala").status_code for _ in range(7)]
    assert 429 in codigos
    assert codigos[0] == 401
