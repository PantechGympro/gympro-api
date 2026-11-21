"""Destinatarios configurables de los reportes automáticos (migración 035):
Propietario, Administrador(es) de la sucursal, ambos y/o correos libres."""
import pytest

from app.extensions import db, mail
from app.models.pg.asignacion_usuario import AsignacionUsuario
from app.models.pg.gimnasio import Gimnasio
from app.models.pg.plan_suscripcion import PlanSuscripcion
from app.models.pg.reporte_automatico import ConfiguracionReporteAutomatico
from app.models.pg.rol import Rol
from app.models.pg.usuario import Usuario
from app.routes.owner_gym import reportes_automaticos as ra
from app.utils.alcance import administradores_de

pytestmark = pytest.mark.integration

URL = "/api/owner_gym/reportes/automaticos"


def _asignar(fab, uid, gym_id, *, organizacion_completa=False):
    """asignacion_usuario del Administrador (fab.usuario no la crea)."""
    with fab.app.app_context():
        gym = Gimnasio.query.get(gym_id)
        rol = Rol.query.filter_by(nombre="Administrador").first()
        db.session.add(AsignacionUsuario(
            id_usuario=uid, id_organizacion=gym.id_organizacion,
            id_gimnasio=None if organizacion_completa else gym_id, id_rol=rol.id,
        ))
        db.session.commit()


@pytest.fixture()
def plan_con_reportes(app):
    """Las bases de prueba arrancan sin planes: uno que habilite reportes_pdf
    (el candado de plan de estos endpoints) para la organización 'basico'."""
    with app.app_context():
        db.session.add(PlanSuscripcion(nombre="basico", precio_mensual_mxn=54900,
                                       limites={"reportes_pdf": True}))
        db.session.commit()


@pytest.fixture()
def escenario(app, fab, plan_con_reportes):
    """Gimnasio con Propietario, 2 Administradores activos (uno directo a la
    sucursal, otro de toda la organización), 1 inactivo y 1 de OTRA organización."""
    gym = fab.gimnasio("Sede")
    otro_gym = fab.gimnasio("Ajeno")
    _, owner_h = fab.personal("Propietario", gym, email="dueno@prueba.test")

    uid_a, _ = fab.usuario("Administrador", gym, email="admin.directo@prueba.test")
    uid_b, _ = fab.usuario("Administrador", gym, email="admin.org@prueba.test")
    uid_c, _ = fab.usuario("Administrador", gym, email="admin.baja@prueba.test", activo=False)
    uid_d, _ = fab.usuario("Administrador", otro_gym, email="admin.ajeno@prueba.test")
    _asignar(fab, uid_a, gym)
    _asignar(fab, uid_b, gym, organizacion_completa=True)
    _asignar(fab, uid_c, gym)
    _asignar(fab, uid_d, otro_gym)
    return {"gym": gym, "owner_h": owner_h}


def _cfg(app, gym, **kw):
    with app.app_context():
        cfg = ConfiguracionReporteAutomatico(
            id_gimnasio=gym, nombre="Corte", frecuencia="semanal", **kw)
        db.session.add(cfg)
        db.session.commit()
        db.session.refresh(cfg)
        db.session.expunge(cfg)
        return cfg


# ── Resolución de Administradores ───────────────────────────────────────────
def test_administradores_de_solo_activos_con_alcance(app, escenario):
    with app.app_context():
        emails = [a.email for a in administradores_de(escenario["gym"])]
    assert emails == ["admin.directo@prueba.test", "admin.org@prueba.test"]


def test_administradores_de_gimnasio_inexistente(app):
    with app.app_context():
        assert administradores_de(999999) == []


# ── Resolución de destinatarios ─────────────────────────────────────────────
def test_por_omision_solo_propietario(app, escenario):
    cfg = _cfg(app, escenario["gym"])
    with app.app_context():
        correos, avisos = ra._resolver_destinatarios(cfg)
    assert correos == ["dueno@prueba.test"] and avisos == []


@pytest.mark.parametrize("propietario, administrador, extras, esperado", [
    (False, True,  None, ["admin.directo@prueba.test", "admin.org@prueba.test"]),
    (True,  True,  None, ["dueno@prueba.test", "admin.directo@prueba.test", "admin.org@prueba.test"]),
    (False, False, "conta@x.com,socio@x.com", ["conta@x.com", "socio@x.com"]),
    # El mismo correo en dos casillas llega UNA sola vez (sin importar mayúsculas).
    (True,  False, "DUENO@prueba.test,conta@x.com", ["dueno@prueba.test", "conta@x.com"]),
])
def test_combinaciones(app, escenario, propietario, administrador, extras, esperado):
    cfg = _cfg(app, escenario["gym"], enviar_propietario=propietario,
               enviar_administrador=administrador, correos_extra=extras)
    with app.app_context():
        correos, avisos = ra._resolver_destinatarios(cfg)
    assert correos == esperado and avisos == []


def test_sin_administrador_avisa_pero_envia_al_resto(app, fab):
    gym = fab.gimnasio("Sin admin")
    fab.usuario("Propietario", gym, email="solo@prueba.test")
    cfg = _cfg(app, gym, enviar_administrador=True)
    with app.app_context():
        correos, avisos = ra._resolver_destinatarios(cfg)
    assert correos == ["solo@prueba.test"]
    assert avisos == ["la sucursal no tiene un Administrador activo asignado"]


# ── Envío real (PDF y SMTP simulados) ───────────────────────────────────────
@pytest.fixture()
def pdf_falso(app, monkeypatch):
    """Sin PDF real ni SMTP: el correo solo se registra (mail.record_messages)."""
    monkeypatch.setattr(ra, "_construir_reporte_pdf", lambda *a, **k: (b"%PDF-falso", "reporte.pdf"))
    monkeypatch.setattr(app.extensions["mail"], "suppress", True)


def test_envio_a_todos_en_un_solo_correo(app, escenario, pdf_falso):
    cfg = _cfg(app, escenario["gym"], enviar_propietario=True,
               enviar_administrador=True, correos_extra="conta@x.com")
    with app.app_context():
        with mail.record_messages() as salida:
            res = ra._enviar_reporte_automatico(cfg)
        assert res["ok"] is True, res
        assert len(salida) == 1
        assert salida[0].recipients == [
            "dueno@prueba.test", "admin.directo@prueba.test", "admin.org@prueba.test", "conta@x.com"]
        assert len(salida[0].attachments) == 1
    assert res["destino"].count(",") == 3


def test_envio_sin_ningun_destinatario_registra_error(app, fab, pdf_falso):
    gym = fab.gimnasio("Vacío")   # sin Propietario ni Administradores
    cfg = _cfg(app, gym, enviar_propietario=True, enviar_administrador=True)
    with app.app_context():
        with mail.record_messages() as salida:
            res = ra._enviar_reporte_automatico(cfg)
        assert res["ok"] is False and salida == []
        assert "No hay a quién enviar" in res["error"]
        assert "Propietario" in res["error"] and "Administrador" in res["error"]


# ── API: validación y compatibilidad ────────────────────────────────────────
def test_crear_sin_campos_de_destinatario_es_solo_propietario(client, escenario):
    r = client.post(URL, json={"nombre": "Viejo", "frecuencia": "diaria"}, headers=escenario["owner_h"])
    assert r.status_code == 201, r.get_json()
    d = r.get_json()
    assert (d["enviar_propietario"], d["enviar_administrador"], d["correos_extra"]) == (True, False, [])
    assert d["destinatarios_label"] == "Propietario"


def test_crear_con_ambos_y_correos_normaliza(client, escenario):
    r = client.post(URL, json={
        "nombre": "Todos", "frecuencia": "mensual", "enviar_propietario": True,
        "enviar_administrador": True,
        "correos_extra": [" Conta@X.com ", "conta@x.com", "socio@x.com"],
    }, headers=escenario["owner_h"])
    assert r.status_code == 201, r.get_json()
    d = r.get_json()
    assert d["correos_extra"] == ["conta@x.com", "socio@x.com"]
    assert d["destinatarios_label"] == "Propietario, Administrador, conta@x.com, socio@x.com"


def test_correos_extra_acepta_texto_csv(client, escenario):
    r = client.post(URL, json={
        "nombre": "Csv", "frecuencia": "diaria", "enviar_propietario": False,
        "correos_extra": "a@x.com; b@x.com\nc@x.com",
    }, headers=escenario["owner_h"])
    assert r.status_code == 201 and r.get_json()["correos_extra"] == ["a@x.com", "b@x.com", "c@x.com"]


@pytest.mark.parametrize("payload, texto", [
    ({"correos_extra": ["no-es-correo"]}, "Correo no válido"),
    ({"correos_extra": ["a@x"]}, "Correo no válido"),
    ({"correos_extra": [f"c{i}@x.com" for i in range(6)]}, "Máximo 5"),
    ({"enviar_propietario": False}, "al menos un destinatario"),
    ({"enviar_propietario": False, "enviar_administrador": False, "correos_extra": []}, "al menos un destinatario"),
])
def test_crear_rechaza_destinatarios_invalidos(client, escenario, payload, texto):
    r = client.post(URL, json={"nombre": "X", "frecuencia": "diaria", **payload}, headers=escenario["owner_h"])
    assert r.status_code == 400 and texto in r.get_json()["msg"]


def test_editar_valida_el_estado_final(client, escenario):
    h = escenario["owner_h"]
    cid = client.post(URL, json={"nombre": "X", "frecuencia": "diaria"}, headers=h).get_json()["id"]

    # Apagar al único destinatario deja la receta sin nadie -> 400.
    r = client.put(f"{URL}/{cid}", json={"enviar_propietario": False}, headers=h)
    assert r.status_code == 400

    # Con un correo extra en el mismo PUT sí se puede apagar al Propietario.
    r = client.put(f"{URL}/{cid}", json={"enviar_propietario": False, "correos_extra": ["conta@x.com"]}, headers=h)
    assert r.status_code == 200 and r.get_json()["destinatarios_label"] == "conta@x.com"

    # Un PUT que no toca destinatarios (pausar) los conserva.
    r = client.put(f"{URL}/{cid}", json={"activo": False}, headers=h)
    assert r.status_code == 200 and r.get_json()["correos_extra"] == ["conta@x.com"]

    # Vaciar los extras estando el Propietario apagado -> 400.
    assert client.put(f"{URL}/{cid}", json={"correos_extra": []}, headers=h).status_code == 400


def test_endpoint_destinatarios_disponibles(client, escenario):
    r = client.get(f"{URL}/destinatarios", headers=escenario["owner_h"])
    assert r.status_code == 200
    d = r.get_json()
    assert d["propietario"]["email"] == "dueno@prueba.test"
    assert [a["email"] for a in d["administradores"]] == ["admin.directo@prueba.test", "admin.org@prueba.test"]
    assert d["max_correos_extra"] == 5
