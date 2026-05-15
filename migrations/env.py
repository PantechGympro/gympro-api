"""
migrations/env.py -- Entorno de ejecucion de Alembic.

Lee POSTGRES_URI desde variables de entorno para evitar hardcodear credenciales.
Importa todos los modelos PG para que autogenerate detecte cambios de schema.
"""
import os
from logging.config import fileConfig

from sqlalchemy import engine_from_config, pool
from alembic import context
from dotenv import load_dotenv

# Cargar .env si existe (util al correr alembic fuera de Docker)
load_dotenv(os.path.join(os.path.dirname(__file__), '..', '.env'))

# Importar modelos para que Alembic los detecte con autogenerate.
# Agregar cada nuevo modelo aqui al crearlo.
import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from app.extensions import db
from app.models.pg.rol                 import Rol                # noqa: F401
from app.models.pg.gimnasio            import Gimnasio           # noqa: F401
from app.models.pg.usuario             import Usuario            # noqa: F401
from app.models.pg.plan_suscripcion    import PlanSuscripcion    # noqa: F401
from app.models.pg.suscripcion         import Suscripcion        # noqa: F401
from app.models.pg.factura_suscripcion import FacturaSuscripcion # noqa: F401

config = context.config

# Sobreescribir la URL con la variable de entorno
postgres_uri = os.getenv(
    "POSTGRES_URI",
    "postgresql+psycopg2://gymuser:gympassword@localhost:5432/gymprodb"
)
config.set_main_option("sqlalchemy.url", postgres_uri)

# Configurar logging solo si el archivo ini existe
# (Flask-Migrate puede no proveer config_file_name en todos los casos)
if config.config_file_name is not None:
    try:
        fileConfig(config.config_file_name)
    except FileNotFoundError:
        pass  # Flask-Migrate gestiona la config directamente; sin logging extra

target_metadata = db.metadata


def run_migrations_offline() -> None:
    """Modo offline: genera SQL sin conectarse a la base de datos."""
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Modo online: aplica las migraciones conectandose a la base de datos."""
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
