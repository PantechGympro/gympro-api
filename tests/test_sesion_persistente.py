"""Sesión del portal web: las cookies deben sobrevivir al cierre del navegador.

Antes JWT_SESSION_COOKIE tenía su valor por defecto (True): las cookies HttpOnly
eran de sesión y el navegador las borraba al cerrar la pestaña/ventana, por lo
que el usuario tenía que iniciar sesión cada vez. Ahora llevan Max-Age con la
vigencia real de cada token y el refresco rota sin perder la persistencia."""
import re
from datetime import timedelta

import pytest

from tests.conftest import PASSWORD

pytestmark = pytest.mark.integration


def _cookies(resp):
    """{nombre: atributos} a partir de las cabeceras Set-Cookie."""
    out = {}
    for raw in resp.headers.getlist("Set-Cookie"):
        nombre = raw.split("=", 1)[0]
        out[nombre] = raw
    return out


def _max_age(set_cookie):
    m = re.search(r"Max-Age=(\d+)", set_cookie, re.I)
    return int(m.group(1)) if m else None


@pytest.fixture()
def login(client, fab):
    gym = fab.gimnasio("Gym A")
    _, email = fab.usuario("Propietario", gym)
    return client.post("/api/auth/login", json={"email": email, "password": PASSWORD})


def test_login_pone_cookies_persistentes_con_vigencia_real(app, login):
    assert login.status_code == 200
    c = _cookies(login)
    assert _max_age(c["access_token_cookie"]) == int(timedelta(hours=8).total_seconds())
    assert _max_age(c["refresh_token_cookie"]) == int(timedelta(days=90).total_seconds())
    for raw in c.values():
        assert "HttpOnly" in raw          # JS no puede leerlas


def test_refresh_renueva_y_mantiene_cookies_persistentes(client, login):
    refresh = login.get_json()["refresh_token"]
    r = client.post("/api/auth/refresh", headers={"Authorization": f"Bearer {refresh}"})
    assert r.status_code == 200
    body = r.get_json()
    assert body["access_token"] and body["refresh_token"] and body["refresh_token"] != refresh
    c = _cookies(r)
    assert _max_age(c["access_token_cookie"]) == int(timedelta(hours=8).total_seconds())
    assert _max_age(c["refresh_token_cookie"]) == int(timedelta(days=90).total_seconds())


def test_refresh_reusado_revoca_la_familia(client, login):
    refresh = login.get_json()["refresh_token"]
    h = {"Authorization": f"Bearer {refresh}"}
    assert client.post("/api/auth/refresh", headers=h).status_code == 200
    assert client.post("/api/auth/refresh", headers=h).status_code == 401   # ya rotado


def test_un_access_token_no_sirve_para_refrescar(client, login):
    acceso = login.get_json()["access_token"]
    r = client.post("/api/auth/refresh", headers={"Authorization": f"Bearer {acceso}"})
    assert r.status_code in (401, 422)


def test_logout_revoca_el_refresh_y_borra_cookies(client, login):
    refresh = login.get_json()["refresh_token"]
    h = {"Authorization": f"Bearer {refresh}"}
    r = client.post("/api/auth/logout", headers=h)
    assert r.status_code == 200
    assert "refresh_token_cookie" in _cookies(r)          # expirada por unset_jwt_cookies
    assert client.post("/api/auth/refresh", headers=h).status_code == 401
