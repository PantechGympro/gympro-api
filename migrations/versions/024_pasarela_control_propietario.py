"""024 -- Cuenta de cobro bajo control exclusivo del propietario (SCRUM-221 / HU-50)

Contexto: hasta esta migracion, un Administrador de sucursal (rol creado en
HU-45) podia configurar la cuenta de cobro (PayPal/Mercado Pago) de SU
propia sucursal igual que el Propietario -- routes/owner_gym/pasarelas.py
los trataba igual via _ROLES_ADMIN. HU-50 pide que solo el Propietario
pueda fijar o cambiar esa cuenta; el resto (Administrador incluido) queda
en solo lectura. Ese cambio de control de acceso no requiere esquema --
vive en el blueprint -- pero el criterio 6 ("ventana de enfriamiento de
24h: los cobros siguen entrando a la cuenta anterior") si lo necesita:
hay que poder resolver, en el momento de cobrar, cual era la cuenta
vigente ANTES del cambio mas reciente.

Cambios (tabla configuracion_pasarela):
  +credenciales_anteriores  TEXT, NULL. Snapshot cifrado (mismo formato que
                            `credenciales`) de las credenciales vigentes
                            justo antes del cambio mas reciente. Null si
                            nunca ha habido un cambio sobre credenciales ya
                            existentes (alta inicial) o si ya paso la
                            ventana y se sobreescribio con un cambio mas
                            reciente.
  +cambiado_en              TIMESTAMPTZ, NULL. Cuando se guardo el valor
                            actual de `credenciales` (no se toca si solo
                            cambia moneda/titular_cuenta/modo -- el
                            criterio habla de la CUENTA, no de esos otros
                            campos). `services/payments/factory.py` usa
                            esta columna para decidir, en cada cobro real,
                            si todavia esta dentro de las 24h y debe usar
                            `credenciales_anteriores` en vez de
                            `credenciales`.

No hay backfill que hacer: toda fila existente no tuvo ningun cambio
posterior a esta migracion, asi que ambas columnas nacen NULL (ninguna fila
entra en ventana de enfriamiento al desplegar esto).

Alcance de esta historia, decidido con el usuario: se mantiene el modelo
de credenciales por gimnasio que ya existia (con herencia por copia al
crear una sucursal, ver sucursales.py::crear_sucursal) en vez de subirlas a
nivel organizacion con sobreescritura por sucursal, que es lo que sugiere
la nota de implementacion original del ticket -- el modelo actual ya
cumple el mismo comportamiento observable (cada sucursal puede tener su
propia cuenta o heredar la de la principal) sin una migracion de datos
mayor ni el riesgo de decidir que pasa con una sucursal que ya tenia la
suya. Si en el futuro el negocio si necesita una cuenta compartida que se
actualice en vivo para varias sucursales a la vez (no solo al crearse),
esa es la migracion pendiente a nivel organizacion.

Riesgo (mismo patron que 017/021/022/023): sin Postgres real accesible
desde el entorno donde se escribio esta migracion -- validar antes de
aplicar contra una base real o de respaldo, y correr el downgrade tambien
antes de confiar en el.

Revision ID: 024
Revises: 023
Create Date: 2026-10-15
"""
from alembic import op
import sqlalchemy as sa

revision      = "024"
down_revision = "023"
branch_labels = None
depends_on    = None


def upgrade():
    op.add_column(
        "configuracion_pasarela",
        sa.Column("credenciales_anteriores", sa.Text(), nullable=True),
    )
    op.add_column(
        "configuracion_pasarela",
        sa.Column("cambiado_en", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade():
    op.drop_column("configuracion_pasarela", "cambiado_en")
    op.drop_column("configuracion_pasarela", "credenciales_anteriores")
