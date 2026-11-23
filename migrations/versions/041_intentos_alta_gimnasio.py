"""Bitacora de altas de gimnasio para frenar multicuentas.

Revision ID: 041
Revises: 040
Create Date: 2026-11-21

Tabla que guarda cada intento de registrar un gimnasio con las senales ya
normalizadas (correo sin alias, telefono a 10 digitos, huella del navegador,
IP). Con ella se detecta a quien abre varias cuentas gratuitas en vez de
contratar un plan, o da de baja su cuenta y la vuelve a crear para estrenar
otra vez el periodo de prueba. Ver utils/antiabuso.py para el criterio.

`id_organizacion` queda SIN clave foranea a proposito. Cuando un gimnasio se
da de baja y se purga, su organizacion desaparece; ese hueco es justo la
senal que delata el ciclo de crear y borrar. Con una FK en cascada el rastro
se borraria solo, que es lo contrario de para lo que existe esta tabla.
(Mismo criterio que gimnasios.baja_solicitada_por en la migracion 037.)

Sobre datos personales: no se guarda el correo ni el telefono tal cual, solo
sus formas normalizadas, que sirven para comparar y no para contactar. La
huella es un hash. La IP se guarda para poder investigar y NO se usa para
bloquear a nadie.

Backfill de los gimnasios que ya existen: se siembra una fila 'creado' por
cada propietario actual. Sin eso, la regla de "esta identidad ya abrio un
gimnasio" no veria nada anterior a esta migracion y cualquiera con una
cuenta existente podria abrir una segunda gratis.
"""
import re

import sqlalchemy as sa
from alembic import op

revision = "041"
down_revision = "040"
branch_labels = None
depends_on = None

_DOMINIOS_SIN_PUNTOS = {"gmail.com", "googlemail.com"}


def _normalizar_correo(correo):
    """Copia de utils.antiabuso.normalizar_correo -- una migracion no debe
    depender del codigo de la aplicacion, que cambia con el tiempo."""
    correo = (correo or "").strip().lower()
    if "@" not in correo:
        return correo
    local, _, dominio = correo.partition("@")
    local = local.split("+", 1)[0]
    if dominio in _DOMINIOS_SIN_PUNTOS:
        local = local.replace(".", "")
    return f"{local}@{dominio}" if local else correo


def _normalizar_telefono(telefono):
    digitos = re.sub(r"\D", "", telefono or "")
    return digitos[-10:] if len(digitos) >= 10 else None


def upgrade():
    op.create_table(
        "intentos_alta_gimnasio",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("correo_normalizado", sa.String(254), nullable=False),
        sa.Column("telefono_normalizado", sa.String(10), nullable=True),
        sa.Column("huella", sa.String(64), nullable=True),
        sa.Column("ip", sa.String(45), nullable=True),
        sa.Column("resultado", sa.String(20), nullable=False),
        sa.Column("motivo", sa.String(40), nullable=True),
        sa.Column("id_organizacion", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
    )
    op.create_index("ix_intentos_alta_correo", "intentos_alta_gimnasio", ["correo_normalizado"])
    op.create_index("ix_intentos_alta_telefono", "intentos_alta_gimnasio", ["telefono_normalizado"])
    op.create_index("ix_intentos_alta_huella", "intentos_alta_gimnasio", ["huella"])
    op.create_index("ix_intentos_alta_resultado", "intentos_alta_gimnasio", ["resultado"])
    op.create_index("ix_intentos_alta_organizacion", "intentos_alta_gimnasio", ["id_organizacion"])
    op.create_index("ix_intentos_alta_created_at", "intentos_alta_gimnasio", ["created_at"])

    # ── Backfill: un 'creado' por cada propietario que ya existe ─────────────
    con = op.get_bind()
    filas = con.execute(sa.text("""
        SELECT u.email, g.telefono, g.id_organizacion, u.created_at
          FROM usuarios  u
          JOIN roles     r ON r.id = u.id_rol
          JOIN gimnasios g ON g.id = u.id_gimnasio
         WHERE r.nombre = 'Propietario'
           AND g.id_organizacion IS NOT NULL
    """)).fetchall()

    sembradas = 0
    vistas = set()
    for f in filas:
        correo = _normalizar_correo(f.email)
        if not correo or (correo, f.id_organizacion) in vistas:
            continue
        vistas.add((correo, f.id_organizacion))
        con.execute(sa.text("""
            INSERT INTO intentos_alta_gimnasio
                   (correo_normalizado, telefono_normalizado, resultado,
                    motivo, id_organizacion, created_at)
            VALUES (:correo, :telefono, 'creado', 'backfill_041', :org,
                    COALESCE(:creado, CURRENT_TIMESTAMP))
        """), {
            "correo": correo,
            "telefono": _normalizar_telefono(f.telefono),
            "org": f.id_organizacion,
            "creado": f.created_at,
        })
        sembradas += 1

    print(f"[041] Bitacora de altas creada. {sembradas} propietario(s) existentes registrados.")
    print("[041] A partir de ahora, un segundo gimnasio con la misma identidad se frena")
    print("[041] y queda registrado para que el superadmin pueda autorizarlo a mano.")


def downgrade():
    op.drop_table("intentos_alta_gimnasio")
