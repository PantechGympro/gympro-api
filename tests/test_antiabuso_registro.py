"""Altas repetidas de gimnasio: qué se frena y, sobre todo, qué NO.

El riesgo de esta función no es dejar pasar a un abusista: es rechazar a un
cliente real sin que nadie se entere. Por eso aquí hay tantas pruebas de lo
que debe PASAR como de lo que debe bloquearse, y la IP compartida tiene la
suya propia.

Nota sobre la MAC: no aparece en ninguna prueba porque un servidor web nunca
la recibe. Ver el docstring de utils/antiabuso.py.
"""
from datetime import datetime, timedelta, timezone

import pytest

from app.constants.antiabuso import ALTAS_MAX_POR_HUELLA, CABECERA_HUELLA
from app.extensions import db
from app.models.pg.gimnasio import Gimnasio
from app.models.pg.intento_alta_gimnasio import IntentoAltaGimnasio
from app.models.pg.plan_suscripcion import PlanSuscripcion
from app.models.pg.rol import Rol
from app.utils.antiabuso import (
    MOTIVO_ALTAS_EN_SERIE, MOTIVO_CUENTA_DADA_DE_BAJA, MOTIVO_YA_TIENE_GIMNASIO,
    normalizar_correo, normalizar_telefono,
)

pytestmark = pytest.mark.integration

HUELLA_A = "a" * 32
HUELLA_B = "b" * 32


@pytest.fixture(autouse=True)
def catalogo_minimo(app):
    """El alta necesita el rol Propietario y el plan gratuito."""
    with app.app_context():
        for nombre in ("Propietario", "Entrenador", "Recepcionista", "Miembro"):
            if Rol.query.filter_by(nombre=nombre).first() is None:
                db.session.add(Rol(nombre=nombre))
        if PlanSuscripcion.query.filter_by(nombre="starter").first() is None:
            db.session.add(PlanSuscripcion(nombre="starter", precio_mensual_mxn=0,
                                           limites={"max_staff": 1}))
        db.session.commit()


def _alta(client, *, correo, nombre="Gym", telefono=None, huella=None):
    cuerpo = {
        "gym":   {"nombre": nombre, "email_contacto": f"contacto-{nombre}@prueba.test"},
        "admin": {"nombre": "Dueño", "email": correo, "password": "Clave-larga-123"},
    }
    if telefono:
        cuerpo["gym"]["telefono"] = telefono
    cabeceras = {CABECERA_HUELLA: huella} if huella else {}
    return client.post("/api/onboarding/register-gym", json=cuerpo, headers=cabeceras)


# ── Normalización ────────────────────────────────────────────────────────────

@pytest.mark.parametrize("entrada,esperado", [
    ("Juan@Gmail.com",           "juan@gmail.com"),
    ("j.u.a.n@gmail.com",        "juan@gmail.com"),
    ("juan+gym2@gmail.com",      "juan@gmail.com"),
    ("j.uan+otro@googlemail.com", "juan@googlemail.com"),
    # Fuera de Gmail los puntos SÍ distinguen buzones: unificarlos rechazaría
    # a dos personas distintas de la misma empresa.
    ("juan.perez@empresa.mx",    "juan.perez@empresa.mx"),
    ("juan+etiqueta@empresa.mx", "juan@empresa.mx"),
])
def test_normalizar_correo(entrada, esperado):
    assert normalizar_correo(entrada) == esperado


@pytest.mark.parametrize("entrada,esperado", [
    ("+52 722 123 4567", "7221234567"),
    ("044-722-123-4567", "7221234567"),
    ("7221234567",       "7221234567"),
    ("12345",            None),     # incompleto: no sirve para comparar
    ("",                 None),
])
def test_normalizar_telefono(entrada, esperado):
    assert normalizar_telefono(entrada) == esperado


# ── Lo que se bloquea ────────────────────────────────────────────────────────

def test_el_mismo_correo_no_abre_un_segundo_gimnasio(client):
    assert _alta(client, correo="dueno@empresa.mx", nombre="Uno").status_code == 201
    r = _alta(client, correo="dueno@empresa.mx", nombre="Dos")
    assert r.status_code == 409


def test_un_alias_de_gmail_no_abre_un_segundo_gimnasio(client):
    """La comparación exacta anterior no veía esto: misma bandeja, otra cadena."""
    assert _alta(client, correo="juanperez@gmail.com", nombre="Uno").status_code == 201

    r = _alta(client, correo="juan.perez+gym2@gmail.com", nombre="Dos")
    assert r.status_code == 409
    assert r.get_json()["codigo"] == MOTIVO_YA_TIENE_GIMNASIO
    # El mensaje ofrece la salida buena (sucursales) y no acusa a nadie.
    assert "sucursal" in r.get_json()["msg"].lower()


def test_el_mismo_telefono_con_otro_correo_tampoco(client):
    assert _alta(client, correo="uno@empresa.mx", nombre="Uno",
                 telefono="+52 722 123 4567").status_code == 201
    r = _alta(client, correo="otro@empresa.mx", nombre="Dos", telefono="7221234567")
    assert r.status_code == 409
    assert r.get_json()["codigo"] == MOTIVO_YA_TIENE_GIMNASIO


def test_quien_dio_de_baja_su_cuenta_no_vuelve_a_empezar(client, app):
    """El ciclo de crear, usar y borrar para estrenar otra prueba."""
    assert _alta(client, correo="vuelve@empresa.mx", nombre="Uno").status_code == 201

    with app.app_context():
        gym = Gimnasio.query.filter_by(nombre="Uno").first()
        gym.baja_solicitada_en = datetime.now(timezone.utc)
        gym.baja_programada_para = datetime.now(timezone.utc) + timedelta(days=30)
        db.session.commit()

    r = _alta(client, correo="vuelve@empresa.mx", nombre="Dos")
    assert r.status_code == 409
    assert r.get_json()["codigo"] == MOTIVO_CUENTA_DADA_DE_BAJA


def test_si_la_organizacion_ya_se_purgo_tambien_se_detecta(client, app):
    """
    Tras la purga la organización desaparece. Ese hueco es la señal: por eso
    la bitácora guarda `id_organizacion` sin clave foránea, para que no se
    borre en cascada junto con lo que delata.
    """
    assert _alta(client, correo="purgado@empresa.mx", nombre="Uno").status_code == 201

    with app.app_context():
        intento = IntentoAltaGimnasio.query.filter_by(resultado="creado").first()
        intento.id_organizacion = 999_999        # organización que ya no existe
        db.session.commit()

    r = _alta(client, correo="purgado@empresa.mx", nombre="Dos")
    assert r.status_code == 409
    assert r.get_json()["codigo"] == MOTIVO_CUENTA_DADA_DE_BAJA


def test_varias_altas_desde_el_mismo_equipo_se_frenan(client):
    """Identidades distintas, mismo navegador: es la granja de cuentas."""
    for i in range(ALTAS_MAX_POR_HUELLA):
        r = _alta(client, correo=f"serie{i}@empresa.mx", nombre=f"Serie{i}", huella=HUELLA_A)
        assert r.status_code == 201, r.get_json()

    r = _alta(client, correo="serie-final@empresa.mx", nombre="SerieFinal", huella=HUELLA_A)
    assert r.status_code == 409
    assert r.get_json()["codigo"] == MOTIVO_ALTAS_EN_SERIE


# ── Lo que NO se bloquea (tan importante como lo anterior) ───────────────────

def test_dos_gimnasios_distintos_desde_la_misma_ip_si_pueden(client):
    """
    Una plaza comercial, un coworking o datos móviles con CGNAT comparten IP
    entre desconocidos. En las pruebas todas las peticiones salen de la misma
    IP, así que esto comprueba justo eso: la IP no bloquea.
    """
    assert _alta(client, correo="vecino1@empresa.mx", nombre="Vecino1").status_code == 201
    assert _alta(client, correo="vecino2@otra.mx",   nombre="Vecino2").status_code == 201


def test_equipos_distintos_no_se_estorban(client):
    assert _alta(client, correo="eq1@empresa.mx", nombre="Eq1", huella=HUELLA_A).status_code == 201
    assert _alta(client, correo="eq2@empresa.mx", nombre="Eq2", huella=HUELLA_B).status_code == 201


def test_una_huella_inventada_no_rompe_el_alta(client):
    """La huella la pone el cliente: si manda basura, se ignora y se sigue."""
    r = _alta(client, correo="basura@empresa.mx", nombre="Basura", huella="no-es-un-hash;DROP")
    assert r.status_code == 201
    assert r.get_json()["gym"]["nombre"] == "Basura"


# ── Revisión y excepciones ───────────────────────────────────────────────────

def test_cada_intento_queda_registrado(client, app):
    _alta(client, correo="registro@empresa.mx", nombre="Uno")
    _alta(client, correo="registro@empresa.mx", nombre="Dos")     # bloqueado

    with app.app_context():
        resultados = [i.resultado for i in IntentoAltaGimnasio.query
                      .order_by(IntentoAltaGimnasio.id).all()]
        assert "creado" in resultados
        assert "bloqueado" in resultados


def test_el_superadmin_puede_autorizar_una_identidad(client, app, fab):
    """
    Caso real: dos gimnasios distintos administrados desde el mismo equipo.
    Se autoriza por el correo NUEVO -- el correo del primero no sirve, porque
    un correo ya usado tampoco puede abrir otra cuenta (es único por usuario).
    """
    for i in range(ALTAS_MAX_POR_HUELLA):
        assert _alta(client, correo=f"socio{i}@empresa.mx", nombre=f"Socio{i}",
                     huella=HUELLA_A).status_code == 201

    bloqueado = _alta(client, correo="tercero@empresa.mx", nombre="Tercero", huella=HUELLA_A)
    assert bloqueado.status_code == 409
    assert bloqueado.get_json()["codigo"] == MOTIVO_ALTAS_EN_SERIE

    _, headers = fab.personal("superadmin", None)
    r = client.post("/api/superadmin/antiabuso/permitir",
                    json={"correo": "tercero@empresa.mx"}, headers=headers)
    assert r.status_code == 201

    # Con la autorización puesta, la misma alta ya pasa.
    assert _alta(client, correo="tercero@empresa.mx", nombre="Tercero",
                 huella=HUELLA_A).status_code == 201


def test_el_superadmin_ve_los_bloqueos_y_sus_motivos(client, fab):
    _alta(client, correo="visible@empresa.mx", nombre="Uno")
    _alta(client, correo="visible@empresa.mx", nombre="Dos")

    _, headers = fab.personal("superadmin", None)
    r = client.get("/api/superadmin/antiabuso/intentos?resultado=bloqueado", headers=headers)
    assert r.status_code == 200
    cuerpo = r.get_json()
    assert len(cuerpo["intentos"]) >= 1
    assert cuerpo["bloqueos_por_motivo"].get(MOTIVO_YA_TIENE_GIMNASIO, 0) >= 1


def test_solo_el_superadmin_entra_al_panel_antiabuso(client, fab):
    gym_id = fab.gimnasio("Gym cualquiera")
    _, headers = fab.personal("Propietario", gym_id)
    assert client.get("/api/superadmin/antiabuso/intentos", headers=headers).status_code == 403
