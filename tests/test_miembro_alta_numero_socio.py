"""El alta de un miembro devuelve su número de socio (id_usuario_pg) para
que quien lo registra pueda dárselo en el momento."""
import pytest

from app.extensions import db
from app.models.pg.plan_suscripcion import PlanSuscripcion
from app.models.pg.usuario import Usuario

pytestmark = pytest.mark.integration


@pytest.fixture()
def plan_amplio(app, fab):
    """Las bases de prueba arrancan sin planes ni el rol 'Miembro': un plan
    sin topes y el rol, que el alta busca por nombre."""
    with app.app_context():
        fab._rol("Miembro")
        db.session.add(PlanSuscripcion(nombre="basico", precio_mensual_mxn=54900, limites={}))
        db.session.commit()


@pytest.mark.parametrize("rol", ["Propietario", "Recepcionista"])
def test_alta_devuelve_numero_socio(client, fab, app, mdb, plan_amplio, monkeypatch, rol):
    # Sin SMTP real: el correo de bienvenida no es lo que se prueba.
    monkeypatch.setattr("app.routes.owner_gym.miembros.enviar_credenciales_acceso", lambda *a, **k: True)
    gym = fab.gimnasio("Sede")
    _, h = fab.personal(rol, gym)

    r = client.post("/api/miembros", json={"nombre": "Ana Nueva", "email": "ana.nueva@prueba.test"}, headers=h)
    assert r.status_code == 201, r.get_json()
    d = r.get_json()

    with app.app_context():
        uid = Usuario.query.filter_by(email="ana.nueva@prueba.test").one().id
    assert d["numero_socio"] == d["id_usuario_pg"] == uid
    # Es el mismo número que queda guardado en la ficha del miembro (el que ve la UI).
    assert mdb.miembros.find_one({"email": "ana.nueva@prueba.test"})["id_usuario_pg"] == uid
