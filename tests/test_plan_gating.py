"""El gateo por plan se resuelve SIEMPRE por la suscripción vigente.

Regresión del bug recurrente "pago Enterprise y la pantalla me dice que
mejore mi plan". Hubo dos causas distintas, las dos cubiertas aquí:

1. El plan se leía de `Organizacion.plan` / `Gimnasio.plan`, que son COPIAS
   del plan contratado y se quedaban viejas. Ahora todo pasa por el
   resolutor único de utils/capacidad.py.
2. Ese resolutor solo miraba la suscripción de la sucursal PRINCIPAL, así
   que una organización con la suscripción colgada de otra sucursal volvía
   a caer en la copia vieja.

Y el caso de la captura que lo destapó: un Entrenador de un gimnasio
Enterprise viendo "Importar rutinas con IA" bloqueado con el aviso de
"disponible desde el plan Pro".
"""
from datetime import datetime, timedelta, timezone

import pytest

from app.extensions import db
from app.models.pg.gimnasio import Gimnasio
from app.models.pg.organizacion import Organizacion
from app.models.pg.plan_suscripcion import PlanSuscripcion
from app.models.pg.suscripcion import Suscripcion
from app.utils.capacidad import (
    PLAN_POR_DEFECTO, nombre_plan_vigente, permite_funcion, plan_vigente_de,
)

pytestmark = pytest.mark.integration


# Lo mínimo del catálogo que estas pruebas necesitan. `limites` completo a
# propósito: una bandera ausente es justo lo que producía el bloqueo, así que
# aquí se siembra bien para probar el OTRO camino (la resolución del plan).
CATALOGO = {
    "starter":    {"rutinas_ia": False, "ml_lab": False, "max_staff": 1},
    "basico":     {"rutinas_ia": False, "ml_lab": False, "max_staff": 4},
    "enterprise": {"rutinas_ia": True,  "ml_lab": True,  "max_staff": 30},
}


def _plan(nombre):
    p = PlanSuscripcion.query.filter_by(nombre=nombre).first()
    if p is None:
        p = PlanSuscripcion(nombre=nombre, precio_mensual_mxn=0, limites=dict(CATALOGO[nombre]))
        db.session.add(p)
        db.session.flush()
    else:
        p.limites = dict(CATALOGO[nombre])
    return p


def _suscribir(id_gimnasio, nombre_plan, estado="active"):
    ini = datetime.now(timezone.utc) - timedelta(days=10)
    sub = Suscripcion(
        id_gimnasio=id_gimnasio, id_plan=_plan(nombre_plan).id, estado=estado,
        ciclo_facturacion="mensual", fecha_inicio=ini,
        fecha_fin=ini + timedelta(days=30), fecha_proximo_cobro=ini + timedelta(days=30),
    )
    db.session.add(sub)
    return sub


@pytest.fixture()
def org_enterprise_con_copia_vieja(app, fab):
    """
    Organización que PAGA Enterprise pero cuyas columnas copia quedaron en
    "basico" -- el estado exacto en el que aparecía el bug.
    """
    with app.app_context():
        sede = Gimnasio.query.get(fab.gimnasio("Sede principal"))
        org = Organizacion.query.get(sede.id_organizacion)
        sede.es_sucursal_principal = True
        # Las copias mienten: dicen "basico"...
        org.plan = sede.plan = "basico"
        _plan("basico")
        # ...y la suscripción, que es lo que se cobra, dice Enterprise.
        _suscribir(sede.id, "enterprise")
        db.session.commit()
        return {"org": org.id, "sede": sede.id}


def test_manda_la_suscripcion_y_no_la_copia_en_organizacion(app, org_enterprise_con_copia_vieja):
    """`Organizacion.plan` dice basico; se cobra Enterprise: gana Enterprise."""
    with app.app_context():
        sede = org_enterprise_con_copia_vieja["sede"]
        assert plan_vigente_de(sede).nombre == "enterprise"
        assert nombre_plan_vigente(sede) == "enterprise"
        assert permite_funcion(sede, "rutinas_ia") is True
        assert permite_funcion(sede, "ml_lab") is True


def test_suscripcion_colgada_de_una_sucursal_no_principal(app, fab):
    """
    La suscripción vive en una sucursal secundaria (pasa si la principal se
    creó después o se marcó a mano). Antes el resolutor solo miraba la
    principal, no la encontraba y caía en la copia vieja.
    """
    with app.app_context():
        principal = Gimnasio.query.get(fab.gimnasio("Principal sin suscripcion"))
        secundaria = Gimnasio.query.get(fab.gimnasio("Secundaria con la suscripcion"))
        org = Organizacion.query.get(principal.id_organizacion)
        principal.es_sucursal_principal = True
        secundaria.id_organizacion = org.id
        secundaria.es_sucursal_principal = False
        org.plan = "basico"
        _plan("basico")
        _suscribir(secundaria.id, "enterprise")
        db.session.commit()

        # Las dos sucursales comparten el plan de la organización.
        assert permite_funcion(principal.id, "rutinas_ia") is True
        assert permite_funcion(secundaria.id, "rutinas_ia") is True


def test_una_suscripcion_cancelada_no_concede_nada(app, fab):
    """Solo cuentan trialing/active: una cancelada cae al respaldo."""
    with app.app_context():
        gym = Gimnasio.query.get(fab.gimnasio("Con suscripcion cancelada"))
        org = Organizacion.query.get(gym.id_organizacion)
        org.plan = "basico"
        _plan("basico")
        _suscribir(gym.id, "enterprise", estado="cancelled")
        db.session.commit()

        assert nombre_plan_vigente(gym.id) == "basico"
        assert permite_funcion(gym.id, "rutinas_ia") is False


def test_sin_plan_resoluble_cae_al_gratuito_no_a_basico(app):
    """
    Ante la duda, lo mínimo: un gimnasio inexistente no debe heredar las
    funciones de un plan de pago. Antes el default era "basico".
    """
    with app.app_context():
        assert nombre_plan_vigente(999_999) == PLAN_POR_DEFECTO == "starter"
        assert permite_funcion(999_999, "rutinas_ia") is False


def test_el_entrenador_ve_la_funcion_desbloqueada_en_auth_me(client, app, fab,
                                                             org_enterprise_con_copia_vieja):
    """
    El caso de la captura: /api/auth/me alimenta el candado del frontend, y
    con la copia vieja devolvía rutinas_ia=False en un gimnasio Enterprise.
    """
    sede = org_enterprise_con_copia_vieja["sede"]
    _, headers = fab.personal("Entrenador", sede)

    r = client.get("/api/auth/me", headers=headers)
    assert r.status_code == 200
    cuerpo = r.get_json()
    assert cuerpo["funciones_plan"]["rutinas_ia"] is True
    assert cuerpo["funciones_plan"]["ml_lab"] is True


def test_el_token_de_impersonacion_lleva_el_plan_vigente(client, app, fab,
                                                         org_enterprise_con_copia_vieja):
    """
    El superadmin impersona para diagnosticar; si el token le pone el plan de
    la copia vieja, diagnostica sobre una mentira.
    """
    sede = org_enterprise_con_copia_vieja["sede"]
    uid_entrenador, _ = fab.usuario("Entrenador", sede)
    _, headers_sa = fab.personal("superadmin", None)

    r = client.post(f"/api/superadmin/usuarios/{uid_entrenador}/impersonate", headers=headers_sa)
    assert r.status_code == 200
    assert r.get_json()["user"]["plan"] == "enterprise"

    # Y con ese token, el candado tampoco aparece.
    token = r.get_json()["access_token"]
    me = client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert me.get_json()["funciones_plan"]["rutinas_ia"] is True
