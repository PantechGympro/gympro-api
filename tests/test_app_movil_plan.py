"""La app móvil es una función de plan y se hace valer en el login.

El Starter dejó de incluir app móvil (migración 039). Para que eso sea una
restricción real y no un texto en la tarjeta de precios, el API tiene que
distinguir quién entra desde la app: lo hace con la cabecera
`X-GymPro-Client: movil` que manda gympro-mobile.

Criterios que se prueban aquí:
  - Sin la cabecera (web, o una versión vieja de la app) NUNCA se bloquea.
  - Con la cabecera y plan sin `app_movil` → 402 con mensaje accionable.
  - Con la cabecera y plan que sí la incluye → entra normal.
  - El superadmin nunca se bloquea.
"""
from datetime import datetime, timedelta, timezone

import pytest

from app.extensions import db
from app.models.pg.gimnasio import Gimnasio
from app.models.pg.organizacion import Organizacion
from app.models.pg.plan_suscripcion import PlanSuscripcion
from app.models.pg.suscripcion import Suscripcion
from app.utils.security import CABECERA_CLIENTE, CLIENTE_MOVIL

from tests.conftest import PASSWORD

pytestmark = pytest.mark.integration

CABECERA_APP = {CABECERA_CLIENTE: CLIENTE_MOVIL}


def _gimnasio_con_plan(app, fab, nombre_plan, *, app_movil):
    """Gimnasio con una suscripción vigente al plan indicado."""
    with app.app_context():
        gym = Gimnasio.query.get(fab.gimnasio(f"Gym {nombre_plan}"))
        org = Organizacion.query.get(gym.id_organizacion)
        org.plan = nombre_plan
        plan = PlanSuscripcion.query.filter_by(nombre=nombre_plan).first()
        if plan is None:
            plan = PlanSuscripcion(nombre=nombre_plan, precio_mensual_mxn=0)
            db.session.add(plan)
        plan.limites = {"app_movil": app_movil, "max_staff": 1}
        db.session.flush()
        ini = datetime.now(timezone.utc) - timedelta(days=5)
        db.session.add(Suscripcion(
            id_gimnasio=gym.id, id_plan=plan.id, estado="active",
            ciclo_facturacion="mensual", fecha_inicio=ini,
            fecha_fin=ini + timedelta(days=30), fecha_proximo_cobro=ini + timedelta(days=30),
        ))
        db.session.commit()
        return gym.id


def _login(client, email, headers=None):
    return client.post("/api/auth/login", json={"email": email, "password": PASSWORD},
                       headers=headers or {})


def test_la_web_entra_aunque_el_plan_no_incluya_app_movil(client, app, fab):
    """Sin la cabecera de la app no se bloquea nada: el plan limita la app, no la web."""
    gym = _gimnasio_con_plan(app, fab, "starter", app_movil=False)
    _, email = fab.usuario("Miembro", gym)

    assert _login(client, email).status_code == 200


def test_la_app_se_bloquea_si_el_plan_no_la_incluye(client, app, fab):
    gym = _gimnasio_con_plan(app, fab, "starter", app_movil=False)
    _, email = fab.usuario("Miembro", gym)

    r = _login(client, email, CABECERA_APP)
    assert r.status_code == 402
    cuerpo = r.get_json()
    assert cuerpo["funcion_requerida"] == "app_movil"
    assert cuerpo["upgrade_requerido"] is True
    # El mensaje tiene que decir qué puede hacer la persona, no solo que no puede.
    assert "web" in cuerpo["msg"].lower()


def test_la_app_entra_si_el_plan_la_incluye(client, app, fab):
    gym = _gimnasio_con_plan(app, fab, "basico", app_movil=True)
    _, email = fab.usuario("Miembro", gym)

    assert _login(client, email, CABECERA_APP).status_code == 200


def test_el_bloqueo_alcanza_al_staff_no_solo_a_los_miembros(client, app, fab):
    """`app_movil` cubre la app de recepción y la de entrenadores, no solo la del socio."""
    gym = _gimnasio_con_plan(app, fab, "starter", app_movil=False)
    for rol in ("Entrenador", "Recepcionista", "Propietario"):
        _, email = fab.usuario(rol, gym)
        assert _login(client, email, CABECERA_APP).status_code == 402, rol


def test_el_superadmin_nunca_se_bloquea(client, app, fab):
    _, email = fab.usuario("superadmin", None)
    assert _login(client, email, CABECERA_APP).status_code == 200


def test_la_cabecera_no_distingue_mayusculas(client, app, fab):
    gym = _gimnasio_con_plan(app, fab, "starter", app_movil=False)
    _, email = fab.usuario("Miembro", gym)

    r = _login(client, email, {CABECERA_CLIENTE: "MOVIL"})
    assert r.status_code == 402
