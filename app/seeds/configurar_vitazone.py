# -*- coding: utf-8 -*-
"""
configurar_vitazone.py
----------------------
Deja la base ACTUAL con la identidad oficial del cliente real:

    Organizacion  : Vitazone
    Sede principal: Vitazone - Santa Ana Tlalpaltitlán   (el entorno demo actual)
    Sucursal 2    : Vitazone - Xonacatlán

Es idempotente y NO borra nada: renombra la organización y la sede principal,
crea Xonacatlán si falta (como lo hace POST /sucursales: misma organización,
es_sucursal_principal=False, hereda el plan) y deja `organizaciones.plan`
sincronizado con la suscripción vigente (la causa de los candados en el demo
Enterprise). No toca usuarios, miembros ni historial.

Uso (dry-run por defecto; no escribe nada):
    docker compose exec api python -m app.seeds.configurar_vitazone
    docker compose exec api python -m app.seeds.configurar_vitazone --gym-id 3 --aplicar

Sin --gym-id busca, en este orden: un gimnasio cuyo nombre contenga
"Vitazone", "Panchito" o "Demo Fitness - Centro"; si hay varios o ninguno,
lista los gimnasios y pide --gym-id.
"""
import argparse
import sys

from app import create_app
from app.extensions import db
from app.models.pg.gimnasio import Gimnasio
from app.models.pg.organizacion import Organizacion
from app.models.pg.suscripcion import Suscripcion
from app.models.pg.pasarela_pago import ConfiguracionPasarela
from app.utils.capacidad import (
    LimiteDePlanExcedido, plan_de_suscripcion_vigente, verificar_capacidad_sucursal,
)

ORGANIZACION = "Vitazone"
SEDE_PRINCIPAL = "Vitazone - Santa Ana Tlalpaltitlán"
SEGUNDA_SUCURSAL = "Vitazone - Xonacatlán"
_PISTAS = ("vitazone", "panchito", "demo fitness - centro")


def _localizar(gym_id):
    if gym_id:
        return Gimnasio.query.get(gym_id)
    candidatos = [
        g for g in Gimnasio.query.order_by(Gimnasio.id).all()
        if any(p in (g.nombre or "").lower() for p in _PISTAS)
    ]
    if len(candidatos) == 1:
        return candidatos[0]
    print("No pude elegir un gimnasio de forma segura. Gimnasios existentes:")
    for g in Gimnasio.query.order_by(Gimnasio.id).all():
        print(f"  id={g.id:<4} org={g.id_organizacion:<4} principal={g.es_sucursal_principal!s:<5} {g.nombre}")
    print("Repite con --gym-id <id de la sede principal>.")
    return None


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--gym-id", type=int, help="id del gimnasio que será Santa Ana Tlalpaltitlán")
    ap.add_argument("--aplicar", action="store_true", help="escribe los cambios (por defecto solo muestra)")
    args = ap.parse_args()

    app = create_app()
    with app.app_context():
        sede = _localizar(args.gym_id)
        if sede is None or sede.organizacion is None:
            sys.exit(1)
        org: Organizacion = sede.organizacion
        cambios = []

        if org.nombre != ORGANIZACION:
            cambios.append(f"organización '{org.nombre}' -> '{ORGANIZACION}'")
            org.nombre = ORGANIZACION
        if sede.nombre != SEDE_PRINCIPAL:
            cambios.append(f"sede (id={sede.id}) '{sede.nombre}' -> '{SEDE_PRINCIPAL}'")
            sede.nombre = SEDE_PRINCIPAL
        if not sede.es_sucursal_principal:
            cambios.append("sede marcada como principal")
            sede.es_sucursal_principal = True

        # El plan de la organización debe ser el de su suscripción vigente.
        plan_sub = plan_de_suscripcion_vigente(org.id)
        plan_org = org.plan.value if hasattr(org.plan, "value") else org.plan
        if plan_sub is not None and plan_sub.nombre != plan_org:
            cambios.append(f"plan de la organización '{plan_org}' -> '{plan_sub.nombre}' (según su suscripción)")
            org.plan = plan_sub.nombre
        elif plan_sub is None:
            print("  AVISO: la sede principal no tiene suscripción vigente; el plan queda como está "
                  f"('{plan_org}'). Contrátalo desde Mi suscripción o el panel de superadmin.")

        # Segunda sucursal
        hermanas = Gimnasio.query.filter_by(id_organizacion=org.id).all()
        xona = next((g for g in hermanas if g.nombre == SEGUNDA_SUCURSAL), None)
        if xona is None:
            try:
                verificar_capacidad_sucursal(org.id)
            except LimiteDePlanExcedido as exc:
                print(f"  No se puede crear Xonacatlán: {exc}")
            else:
                xona = Gimnasio(
                    nombre=SEGUNDA_SUCURSAL, activo=True, id_organizacion=org.id,
                    es_sucursal_principal=False, tipo_gimnasio=sede.tipo_gimnasio,
                    email_contacto=None, telefono=None,
                )
                db.session.add(xona)
                db.session.flush()
                for c in ConfiguracionPasarela.query.filter_by(id_gimnasio=sede.id).all():
                    db.session.add(ConfiguracionPasarela(
                        id_gimnasio=xona.id, proveedor=c.proveedor, modo=c.modo, activo=c.activo,
                        credenciales=c.credenciales, moneda=c.moneda, titular_cuenta=c.titular_cuenta))
                cambios.append(f"crear sucursal '{SEGUNDA_SUCURSAL}' (hereda plan y cobro de la sede)")

        otras = [g for g in hermanas if g.id not in {sede.id, getattr(xona, "id", None)}]
        for g in otras:
            print(f"  AVISO: la organización tiene otra sucursal '{g.nombre}' (id={g.id}); no se toca. "
                  "Desactívala desde Sucursales si ya no aplica.")

        print("\nCambios " + ("APLICADOS:" if args.aplicar else "(dry-run, usa --aplicar para escribirlos):"))
        for c in cambios or ["ninguno: ya está configurado"]:
            print(f"  - {c}")
        if args.aplicar and cambios:
            db.session.commit()
        else:
            db.session.rollback()


if __name__ == "__main__":
    main()
