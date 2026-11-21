"""El plan y la suscripción de una sucursal se resuelven por su ORGANIZACIÓN
(SCRUM-223): el superadmin y el perfil del propietario no deben mostrar el
plan heredado gimnasios.plan ni dejar sin fecha de inicio a las sucursales."""
from datetime import datetime, timedelta, timezone

import pytest

from app.extensions import db
from app.models.pg.gimnasio import Gimnasio
from app.models.pg.organizacion import Organizacion
from app.models.pg.plan_suscripcion import PlanSuscripcion
from app.models.pg.suscripcion import Suscripcion

pytestmark = pytest.mark.integration


@pytest.fixture()
def org_enterprise(app, fab):
    """Organización Enterprise con sede principal (suscripción) + 1 sucursal,
    ambas con la columna heredada gimnasios.plan en 'basico'."""
    with app.app_context():
        sede = Gimnasio.query.get(fab.gimnasio("Sede"))
        sucursal = Gimnasio.query.get(fab.gimnasio("Sucursal"))
        org = Organizacion.query.get(sede.id_organizacion)
        org.plan = "enterprise"
        sucursal.id_organizacion = org.id
        sucursal.es_sucursal_principal = False
        sede.plan = sucursal.plan = "basico"
        plan = PlanSuscripcion.query.filter_by(nombre="enterprise").first()
        if plan is None:   # las bases de prueba arrancan vacías
            plan = PlanSuscripcion(nombre="enterprise", precio_mensual_mxn=249900, precio_anual_mxn=2499000)
            db.session.add(plan)
            db.session.flush()
        ini = datetime.now(timezone.utc) - timedelta(days=30)
        db.session.add(Suscripcion(
            id_gimnasio=sede.id, id_plan=plan.id, estado="active", ciclo_facturacion="mensual",
            fecha_inicio=ini, fecha_fin=ini + timedelta(days=60), fecha_proximo_cobro=ini + timedelta(days=60),
        ))
        db.session.commit()
        return {"sede": sede.id, "sucursal": sucursal.id, "inicio": ini}


def test_to_dict_usa_el_plan_de_la_organizacion(app, org_enterprise):
    with app.app_context():
        for gid in (org_enterprise["sede"], org_enterprise["sucursal"]):
            assert Gimnasio.query.get(gid).to_dict()["plan"] == "enterprise"


def test_listado_superadmin_plan_y_suscripcion_de_la_sucursal(client, fab, org_enterprise):
    _, h = fab.personal("superadmin", None)
    r = client.get("/api/superadmin/gimnasios", headers=h)
    assert r.status_code == 200
    por_id = {g["id"]: g for g in r.get_json()["gimnasios"]}

    sede, sucursal = por_id[org_enterprise["sede"]], por_id[org_enterprise["sucursal"]]
    assert sede["plan"] == sucursal["plan"] == "enterprise"
    assert sede["suscripcion"]["heredada"] is False
    # La sucursal no tiene suscripción propia: muestra la de su organización.
    assert sucursal["suscripcion"]["heredada"] is True
    assert sucursal["suscripcion"]["id_gimnasio_titular"] == org_enterprise["sede"]
    assert sucursal["suscripcion"]["fecha_inicio"] == sede["suscripcion"]["fecha_inicio"]
    assert sucursal["suscripcion"]["fecha_proximo_cobro"]


def test_sincronizar_plan_actualiza_todas_las_sucursales(app, org_enterprise):
    from app.utils.capacidad import sincronizar_plan_organizacion
    with app.app_context():
        sincronizar_plan_organizacion(org_enterprise["sede"], "pro")
        db.session.commit()
        assert {Gimnasio.query.get(i).plan for i in (org_enterprise["sede"], org_enterprise["sucursal"])} == {"pro"}
