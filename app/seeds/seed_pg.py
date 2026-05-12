"""
seeds/seed_pg.py — Datos iniciales para PostgreSQL.

Crea:
  - 4 roles del sistema (Administrador, Entrenador, Recepcionista, Miembro)
  - 1 gimnasio demo (GymPro Demo, plan pro)
  - 1 usuario administrador por defecto

Uso:
  # Desde la raíz de api/ con el entorno activo y POSTGRES_URI configurado:
  python -m app.seeds.seed_pg

  # O desde docker-compose:
  docker compose exec api python -m app.seeds.seed_pg
"""
import os
import sys

# Asegurar que la raíz del proyecto esté en el path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

from dotenv import load_dotenv
load_dotenv()

from app import create_app
from app.extensions import db
from app.models.pg.rol      import Rol
from app.models.pg.gimnasio import Gimnasio, PlanEnum
from app.models.pg.usuario  import Usuario


ROLES_INICIALES = [
    "Administrador",
    "Entrenador",
    "Recepcionista",
    "Miembro",
]

GIMNASIO_DEMO = {
    "nombre":         "GymPro Demo",
    "plan":           PlanEnum.pro,
    "activo":         True,
    "email_contacto": "admin@gymprosaas.com",
    "telefono":       "+52-664-000-0000",
}

ADMIN_DEMO = {
    "nombre":   "Administrador GymPro",
    "email":    "admin@gymprosaas.com",
    "password": "Admin1234!",
}


def seed():
    app = create_app()
    with app.app_context():
        print("─── Seed PostgreSQL ──────────────────────────────────")

        # ── Roles ──────────────────────────────────────────────
        print("Roles:")
        for nombre in ROLES_INICIALES:
            if not Rol.query.filter_by(nombre=nombre).first():
                db.session.add(Rol(nombre=nombre))
                print(f"  + {nombre}")
            else:
                print(f"  ✓ {nombre} (ya existe)")
        db.session.commit()

        # ── Gimnasio demo ───────────────────────────────────────
        print("\nGimnasio:")
        gimnasio = Gimnasio.query.filter_by(nombre=GIMNASIO_DEMO["nombre"]).first()
        if not gimnasio:
            gimnasio = Gimnasio(**GIMNASIO_DEMO)
            db.session.add(gimnasio)
            db.session.commit()
            print(f"  + {gimnasio.nombre}")
        else:
            print(f"  ✓ {gimnasio.nombre} (ya existe)")

        # ── Usuario administrador ───────────────────────────────
        print("\nUsuario admin:")
        rol_admin = Rol.query.filter_by(nombre="Administrador").first()
        admin = Usuario.query.filter_by(email=ADMIN_DEMO["email"]).first()
        if not admin:
            admin = Usuario(
                nombre=ADMIN_DEMO["nombre"],
                email=ADMIN_DEMO["email"],
                id_rol=rol_admin.id,
                id_gimnasio=gimnasio.id,
                activo=True,
            )
            admin.set_password(ADMIN_DEMO["password"])
            db.session.add(admin)
            db.session.commit()
            print(f"  + {admin.email}  (password: {ADMIN_DEMO['password']})")
            print("  ⚠️  Cambia la contraseña antes de ir a producción")
        else:
            print(f"  ✓ {admin.email} (ya existe)")

        print("\n✅ Seed completado")


if __name__ == "__main__":
    seed()
