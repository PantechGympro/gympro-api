"""Salud del servicio: lo que el despliegue y el monitoreo consultan."""
import pytest

pytestmark = pytest.mark.integration


def test_health_responde_sin_sesion(client):
    r = client.get("/api/health")
    assert r.status_code == 200
    assert r.get_json()["status"]


def test_health_no_exige_tenant_ni_token(client):
    # El health check no debe depender de un JWT: lo usa el orquestador.
    assert client.get("/api/health").status_code != 401


def test_ruta_inexistente_devuelve_json_sin_detalles_internos(client):
    r = client.get("/api/esta-ruta-no-existe")
    assert r.status_code == 404
    assert r.is_json
    cuerpo = r.get_data(as_text=True).lower()
    assert "traceback" not in cuerpo and "werkzeug" not in cuerpo


def test_cabeceras_de_seguridad(client):
    r = client.get("/api/health")
    assert r.headers.get("X-Content-Type-Options") == "nosniff"
    assert r.headers.get("X-Frame-Options") == "DENY"


def test_readiness_ok_con_dependencias_vivas(client):
    r = client.get("/api/health/ready")
    cuerpo = r.get_json()
    assert r.status_code == 200 and cuerpo["status"] == "ready"
    assert set(cuerpo["checks"]) == {"postgres", "mongo", "redis"}
    assert all(c["estado"] == "ok" for c in cuerpo["checks"].values())


def test_readiness_503_sin_filtrar_detalles_si_cae_una_dependencia(client, monkeypatch):
    from app.routes.compartido import health

    class _Caida:
        def ping(self):
            raise ConnectionError("redis://usuario:SECRETO@host:6379 rechazó la conexión")

    monkeypatch.setattr(health, "redis_cliente", _Caida())
    r = client.get("/api/health/ready")
    assert r.status_code == 503 and r.get_json()["status"] == "degraded"
    assert r.get_json()["checks"]["redis"] == {"estado": "error", "error": "ConnectionError"}
    assert "SECRETO" not in r.get_data(as_text=True)


def test_health_expone_version(client, monkeypatch):
    monkeypatch.setenv("GIT_SHA", "abcdef1234567890")
    cuerpo = client.get("/api/health").get_json()
    assert cuerpo["status"] == "API GYM activa" and cuerpo["commit"] == "abcdef123456"
