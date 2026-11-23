"""Periodo de prueba de 7 días: empieza, termina sola y no se puede encadenar.

Lo que se prueba aquí es sobre todo que la prueba TERMINE. El hueco que
existía antes: el alta creaba la suscripción en 'trialing' y nada miraba
ninguna fecha, así que quien se registraba eligiendo Enterprise se quedaba
con Enterprise gratis para siempre.

El corte no depende del trabajo nocturno (que en el VPS no está programado
todavía): se decide al resolver el plan. Por eso varias pruebas de aquí
nunca lo ejecutan.
"""
from datetime import datetime, timedelta, timezone

import pytest

from app.constants.planes import DIAS_PRUEBA, PLAN_GRATUITO
from app.extensions import db
from app.models.pg.gimnasio import Gimnasio
from app.models.pg.organizacion import Organizacion
from app.models.pg.plan_suscripcion import PlanSuscripcion
from app.models.pg.suscripcion import Suscripcion
from app.utils.capacidad import nombre_plan_vigente, permite_funcion

pytestmark = pytest.mark.integration


def _plan(nombre, precio, **limites):
    p = PlanSuscripcion.query.filter_by(nombre=nombre).first()
    if p is None:
        p = PlanSuscripcion(nombre=nombre, precio_mensual_mxn=precio)
        db.session.add(p)
    p.precio_mensual_mxn = precio
    p.activo = True
    p.limites = limites or {}
    db.session.flush()
    return p


def _catalogo():
    """Catálogo mínimo + los roles que el alta necesita (las bases de prueba
    arrancan vacías; `fab` los crea bajo demanda, pero register-gym no usa
    `fab`)."""
    from app.models.pg.rol import Rol
    for nombre in ("Propietario", "Entrenador", "Recepcionista", "Miembro"):
        if Rol.query.filter_by(nombre=nombre).first() is None:
            db.session.add(Rol(nombre=nombre))
    _plan(PLAN_GRATUITO, 0, app_movil=False, rutinas_ia=False, max_staff=1)
    _plan("enterprise", 249900, app_movil=True, rutinas_ia=True, ml_lab=True, max_staff=30)


# ── Alta ─────────────────────────────────────────────────────────────────────

def test_el_alta_en_un_plan_de_pago_arranca_la_prueba(client, app):
    with app.app_context():
        _catalogo()
        id_plan = PlanSuscripcion.query.filter_by(nombre="enterprise").first().id
        db.session.commit()

    r = client.post("/api/onboarding/register-gym", json={
        "gym":   {"nombre": "Gym con prueba", "email_contacto": "gym@prueba.test"},
        "admin": {"nombre": "Dueño", "email": "dueno-prueba@prueba.test", "password": "Clave-larga-123"},
        "id_plan": id_plan,
    })
    assert r.status_code == 201, r.get_json()

    with app.app_context():
        sub = Suscripcion.query.order_by(Suscripcion.id.desc()).first()
        assert sub.estado == "trialing"
        assert sub.en_prueba is True
        assert sub.prueba_fin is not None
        assert sub.dias_restantes_prueba == DIAS_PRUEBA
        # Queda marcado en la organización para que no se pueda repetir.
        org = Gimnasio.query.get(sub.id_gimnasio).organizacion
        assert org.prueba_usada_en is not None


def test_el_alta_en_el_plan_gratuito_no_abre_ninguna_prueba(client, app):
    with app.app_context():
        _catalogo()
        id_plan = PlanSuscripcion.query.filter_by(nombre=PLAN_GRATUITO).first().id
        db.session.commit()

    r = client.post("/api/onboarding/register-gym", json={
        "gym":   {"nombre": "Gym gratuito", "email_contacto": "gratis@prueba.test"},
        "admin": {"nombre": "Dueño", "email": "dueno-gratis@prueba.test", "password": "Clave-larga-123"},
        "id_plan": id_plan,
    })
    assert r.status_code == 201

    with app.app_context():
        sub = Suscripcion.query.order_by(Suscripcion.id.desc()).first()
        assert sub.estado == "active"
        assert sub.prueba_fin is None
        assert sub.en_prueba is False
        assert Gimnasio.query.get(sub.id_gimnasio).organizacion.prueba_usada_en is None


# ── Fin de la prueba ─────────────────────────────────────────────────────────

@pytest.fixture()
def prueba_vencida(app, fab):
    """Gimnasio cuya prueba de Enterprise venció ayer y nadie ha cerrado."""
    gym_id = fab.gimnasio("Gym con prueba vencida")   # fuera: abre su propio contexto
    with app.app_context():
        _catalogo()
        gym = Gimnasio.query.get(gym_id)
        org = Organizacion.query.get(gym.id_organizacion)
        # La copia se quedó diciendo "enterprise": es justo lo que no debe
        # seguir concediendo el plan una vez terminada la prueba.
        org.plan = "enterprise"
        org.prueba_usada_en = datetime.now(timezone.utc) - timedelta(days=8)
        inicio = datetime.now(timezone.utc) - timedelta(days=8)
        db.session.add(Suscripcion(
            id_gimnasio=gym.id, id_plan=PlanSuscripcion.query.filter_by(nombre="enterprise").first().id,
            estado="trialing", ciclo_facturacion="mensual", fecha_inicio=inicio,
            prueba_fin=datetime.now(timezone.utc) - timedelta(days=1),
            fecha_proximo_cobro=datetime.now(timezone.utc) - timedelta(days=1),
        ))
        db.session.commit()
    return gym_id


def test_una_prueba_vencida_deja_de_dar_el_plan_de_pago(app, prueba_vencida):
    """Sin ejecutar ningún trabajo: el corte ocurre al resolver el plan."""
    with app.app_context():
        assert nombre_plan_vigente(prueba_vencida) == PLAN_GRATUITO
        assert permite_funcion(prueba_vencida, "rutinas_ia") is False
        assert permite_funcion(prueba_vencida, "ml_lab") is False


def test_la_prueba_vencida_no_hereda_el_plan_de_la_copia(app, prueba_vencida):
    """
    `Organizacion.plan` quedó en "enterprise" (era lo que se estaba probando).
    Caer en esa copia regalaría el plan de pago para siempre.
    """
    with app.app_context():
        org = Gimnasio.query.get(prueba_vencida).organizacion
        assert (org.plan.value if hasattr(org.plan, "value") else org.plan) == "enterprise"
        assert nombre_plan_vigente(prueba_vencida) == PLAN_GRATUITO


def test_una_prueba_todavia_vigente_si_da_el_plan(app, fab):
    gym_id = fab.gimnasio("Gym en prueba vigente")
    with app.app_context():
        _catalogo()
        gym = Gimnasio.query.get(gym_id)
        db.session.add(Suscripcion(
            id_gimnasio=gym.id, id_plan=PlanSuscripcion.query.filter_by(nombre="enterprise").first().id,
            estado="trialing", ciclo_facturacion="mensual",
            fecha_inicio=datetime.now(timezone.utc) - timedelta(days=2),
            prueba_fin=datetime.now(timezone.utc) + timedelta(days=5),
        ))
        db.session.commit()

        assert nombre_plan_vigente(gym.id) == "enterprise"
        assert permite_funcion(gym.id, "rutinas_ia") is True


def test_el_trabajo_nocturno_cierra_la_prueba_sin_marcarla_como_deuda(app, prueba_vencida):
    """
    Terminar una prueba sin contratar no es un impago: marcarla 'past_due'
    le mostraría al propietario una deuda que no existe.
    """
    from app.routes.owner_gym.billing import procesar_auto_renovaciones

    resumen = procesar_auto_renovaciones(app=app)
    assert resumen["pruebas_terminadas"] == 1
    assert resumen["vencidas"] == 0          # no se contó como impago

    with app.app_context():
        subs = Suscripcion.query.filter_by(id_gimnasio=prueba_vencida).all()
        por_estado = {s.estado: s for s in subs}
        assert "cancelled" in por_estado
        assert por_estado["cancelled"].estado != "past_due"
        # Queda una suscripción vigente al plan gratuito, no cero.
        activa = por_estado.get("active")
        assert activa is not None
        assert activa.plan.nombre == PLAN_GRATUITO
        assert nombre_plan_vigente(prueba_vencida) == PLAN_GRATUITO


# ── Antiabuso: una sola prueba por organización ──────────────────────────────

def test_la_segunda_suscripcion_de_la_misma_organizacion_no_trae_prueba(client, app, fab):
    gym_id = fab.gimnasio("Gym que ya probó")
    with app.app_context():
        _catalogo()
        gym = Gimnasio.query.get(gym_id)
        org = Organizacion.query.get(gym.id_organizacion)
        org.prueba_usada_en = datetime.now(timezone.utc) - timedelta(days=60)
        id_plan = PlanSuscripcion.query.filter_by(nombre="enterprise").first().id
        db.session.commit()
        gym_id = gym.id

    _, headers = fab.personal("Propietario", gym_id)
    r = client.post("/api/billing/suscripcion", json={"id_plan": id_plan}, headers=headers)
    assert r.status_code == 201, r.get_json()

    with app.app_context():
        sub = Suscripcion.query.filter_by(id_gimnasio=gym_id).order_by(Suscripcion.id.desc()).first()
        assert sub.estado == "active"       # se contrata, pero sin regalo
        assert sub.prueba_fin is None


def test_el_cliente_no_puede_pedirse_una_prueba_mas_larga(client, app, fab):
    """
    El endpoint leía `dias_prueba` del cuerpo: cualquiera con sesión de
    propietario podía pedirse años de un plan de pago gratis.
    """
    gym_id = fab.gimnasio("Gym que pide de mas")
    with app.app_context():
        _catalogo()
        id_plan = PlanSuscripcion.query.filter_by(nombre="enterprise").first().id
        db.session.commit()

    _, headers = fab.personal("Propietario", gym_id)
    r = client.post("/api/billing/suscripcion",
                    json={"id_plan": id_plan, "dias_prueba": 36500}, headers=headers)
    assert r.status_code == 201

    with app.app_context():
        sub = Suscripcion.query.filter_by(id_gimnasio=gym_id).order_by(Suscripcion.id.desc()).first()
        assert sub.dias_restantes_prueba == DIAS_PRUEBA
