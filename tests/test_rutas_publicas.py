"""Barrido de seguridad: ninguna ruta GET sin parámetros debe responder 2xx ni 5xx
sin token, salvo las públicas declaradas explícitamente aquí.

Si agregas un endpoint público a propósito, añádelo a RUTAS_PUBLICAS (la revisión
del PR obliga a justificarlo). Si este test falla con 2xx, hay una ruta sin auth."""
import pytest

pytestmark = pytest.mark.integration

RUTAS_PUBLICAS = {
    "/api/health",
    "/api/health/ready",
    "/api/onboarding/gym-types",
    "/api/onboarding/planes",
    "/api/publico/gimnasios",
    "/api/publico/planes",
    "/api/publico/testimonios",
}


def _rutas_get_sin_parametros(app):
    for rule in app.url_map.iter_rules():
        if "GET" in rule.methods and "<" not in rule.rule and not rule.endpoint.endswith("static"):
            yield rule.rule


def test_solo_las_rutas_publicas_declaradas_responden_sin_token(app, client):
    abiertas, con_error = set(), []
    for ruta in _rutas_get_sin_parametros(app):
        codigo = client.get(ruta).status_code
        if codigo < 400:
            abiertas.add(ruta)
        elif codigo >= 500:
            con_error.append((ruta, codigo))
    assert not con_error, f"Rutas con 5xx sin token: {con_error}"
    assert abiertas == RUTAS_PUBLICAS, (
        f"Sin declarar: {sorted(abiertas - RUTAS_PUBLICAS)} · ya no públicas: {sorted(RUTAS_PUBLICAS - abiertas)}"
    )


def test_las_publicas_no_filtran_datos_internos(client):
    for ruta in RUTAS_PUBLICAS - {"/api/health", "/api/health/ready"}:
        cuerpo = client.get(ruta).get_data(as_text=True).lower()
        for prohibido in ("password", "password_hash", "stripe_customer_id", "traceback"):
            assert prohibido not in cuerpo, f"{ruta} expone '{prohibido}'"
