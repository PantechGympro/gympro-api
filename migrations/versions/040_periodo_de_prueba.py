"""Periodo de prueba de 7 dias con fin real y una sola prueba por organizacion.

Revision ID: 040
Revises: 039
Create Date: 2026-11-21

Agrega:
  - suscripciones.prueba_fin      -- hasta cuando es gratis. NULL = no es prueba.
  - organizaciones.prueba_usada_en -- cuando estreno su prueba. NULL = nunca.
    No se limpia jamas: es lo que impide encadenar pruebas dando de baja la
    cuenta y volviendola a crear.

CIERRA UN HUECO QUE YA ESTABA ABIERTO. El alta de gimnasios creaba la
suscripcion en estado 'trialing' con el cobro a 14 dias, pero nada verificaba
que esa prueba terminara: 'trialing' contaba como vigente sin mirar ninguna
fecha. Quien se registraba eligiendo Enterprise se quedaba con Enterprise
gratis indefinidamente. El trabajo nocturno que lo habria cerrado
(procesar_auto_renovaciones) existe, pero no hay ningun programador
ejecutandolo en el VPS, asi que en la practica no corria nunca.

A partir de aqui el corte NO depende de ese trabajo: una prueba vencida deja
de dar servicio en el momento en que se consulta el plan (ver
Suscripcion.prueba_vencida y utils/capacidad.py). El trabajo nocturno solo
ordena los registros.

BACKFILL CON PERIODO DE GRACIA -- decision deliberada:
  Las suscripciones que hoy estan en 'trialing' llevan quien sabe cuanto
  tiempo asi. Ponerles la fecha que les habria tocado (fecha_inicio + 14 dias)
  le cortaria el servicio HOY MISMO a gimnasios que estan operando, sin que
  nadie les avise. Eso no es corregir un error de datos: es apagarle el
  sistema a un cliente a media jornada.

  Por eso a cada prueba que ya deberia haber vencido se le da hasta
  DIAS_GRACIA dias desde la migracion. Las que aun no vencian conservan su
  fecha original. El upgrade() imprime la lista completa para que quede
  constancia de a quien hay que convertir a 'active' (si es un cliente real)
  o avisar (si no) ANTES de que se acabe la gracia.

  Lo que se gana con la gracia: el hueco se cierra para todas las altas
  nuevas de inmediato, y las existentes quedan acotadas en vez de abiertas
  para siempre.
"""
from datetime import datetime, timedelta, timezone

import sqlalchemy as sa
from alembic import op

revision = "040"
down_revision = "039"
branch_labels = None
depends_on = None

# Lo que duraba la "prueba" implicita del alta anterior a esta migracion.
DIAS_PRUEBA_ANTERIOR = 14
# Margen que se le da a una prueba que ya deberia haber vencido, contado
# desde el dia en que se aplica esta migracion.
DIAS_GRACIA = 7


def upgrade():
    op.add_column("suscripciones",
                  sa.Column("prueba_fin", sa.DateTime(timezone=True), nullable=True))
    op.create_index("ix_suscripciones_prueba_fin", "suscripciones", ["prueba_fin"])
    op.add_column("organizaciones",
                  sa.Column("prueba_usada_en", sa.DateTime(timezone=True), nullable=True))

    con = op.get_bind()
    ahora = datetime.now(timezone.utc)
    limite_gracia = ahora + timedelta(days=DIAS_GRACIA)

    filas = con.execute(sa.text("""
        SELECT s.id, s.id_gimnasio, s.fecha_inicio, s.fecha_proximo_cobro,
               g.id_organizacion, g.nombre AS gimnasio, p.nombre AS plan,
               p.precio_mensual_mxn
          FROM suscripciones      s
          JOIN gimnasios          g ON g.id = s.id_gimnasio
          JOIN planes_suscripcion p ON p.id = s.id_plan
         WHERE s.estado = 'trialing'
         ORDER BY s.id
    """)).fetchall()

    if not filas:
        print("[040] No hay suscripciones en prueba: nada que respaldar.")
        return

    con_gracia, intactas = [], []
    for f in filas:
        # Fin "natural" de esa prueba: el cobro que se le agendo o, si no
        # tiene, 14 dias desde que empezo.
        fin = f.fecha_proximo_cobro
        if fin is None and f.fecha_inicio is not None:
            fin = f.fecha_inicio + timedelta(days=DIAS_PRUEBA_ANTERIOR)
        if fin is None:
            fin = ahora
        if fin.tzinfo is None:
            fin = fin.replace(tzinfo=timezone.utc)

        if fin <= ahora:
            fin = limite_gracia
            con_gracia.append(f)
        else:
            intactas.append(f)

        con.execute(sa.text("UPDATE suscripciones SET prueba_fin = :fin WHERE id = :id"),
                    {"fin": fin, "id": f.id})

        # La organizacion queda marcada como "ya uso su prueba".
        if f.id_organizacion is not None:
            con.execute(sa.text("""
                UPDATE organizaciones
                   SET prueba_usada_en = COALESCE(prueba_usada_en, :inicio)
                 WHERE id = :id
            """), {"inicio": f.fecha_inicio or ahora, "id": f.id_organizacion})

    print(f"[040] {len(filas)} suscripcion(es) en prueba actualizadas.")
    if intactas:
        print(f"[040] {len(intactas)} conservan su fecha original (aun no vencian).")
    if con_gracia:
        print(f"[040] ATENCION: {len(con_gracia)} prueba(s) YA habrian vencido. En vez de")
        print(f"[040] cortarles el servicio hoy, se les da hasta {limite_gracia:%Y-%m-%d}:")
        for f in con_gracia:
            de_pago = "DE PAGO" if (f.precio_mensual_mxn or 0) > 0 else "gratuito"
            print(f"[040]   - suscripcion {f.id}: '{f.gimnasio}' plan {f.plan} ({de_pago})")
        print("[040] Antes de esa fecha hay que pasar a 'active' las que sean clientes")
        print("[040] reales, o avisarles. Despues, caen al plan gratuito solas.")


def downgrade():
    op.drop_index("ix_suscripciones_prueba_fin", table_name="suscripciones")
    op.drop_column("suscripciones", "prueba_fin")
    op.drop_column("organizaciones", "prueba_usada_en")
