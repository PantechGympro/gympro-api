"""
tests/conftest.py -- Infraestructura compartida de las pruebas de la API.

Dos modos, elegidos por la variable TEST_REAL_SERVICES:

  (por defecto) HERMÉTICO -- sin servicios externos. PostgreSQL -> SQLite en un
      archivo temporal, MongoDB -> mongomock, Redis -> fakeredis. Es lo que
      corre cualquier integrante del equipo con `pytest` y lo más rápido.

  TEST_REAL_SERVICES=1 -- INTEGRACIÓN REAL. Usa los servicios que indiquen
      POSTGRES_URI, MONGO_URI y REDIS_URL (en CI son los `services:` del job).
      Sirve para detectar lo que los simuladores no ven (tipos de PostgreSQL,
      agregaciones reales de Mongo, Redis de verdad). Por seguridad, las
      bases deben llamarse "*test*": las pruebas BORRAN su contenido.

Cada prueba arranca con las bases vacías (fixture autouse `limpiar_bases`).
"""
import os
import tempfile
import uuid
from datetime import datetime, timedelta, timezone

import pytest

REAL = os.getenv("TEST_REAL_SERVICES") == "1"

# ── Entorno: debe quedar fijado ANTES de importar la app (Config lee os.environ
# al definirse la clase). ──────────────────────────────────────────────────────
os.environ.setdefault("JWT_SECRET_KEY", "clave-solo-para-pruebas-" + "x" * 32)
os.environ.setdefault("SECRET_KEY", "secreto-solo-para-pruebas")
os.environ.setdefault("MAIL_SUPPRESS_SEND", "1")
os.environ["TESTING"] = "1"

if not REAL:
    _TMP = tempfile.mkdtemp(prefix="gympro-tests-")
    os.environ["POSTGRES_URI"] = f"sqlite:///{_TMP}/test.db"
    os.environ["RATELIMIT_STORAGE_URI"] = "memory://"
    os.environ["MONGO_DB"] = "gympro_test"
else:
    os.environ.setdefault("MONGO_DB", "gympro_test")
    os.environ.setdefault("RATELIMIT_STORAGE_URI", os.getenv("REDIS_URL", "redis://localhost:6379/1"))

ROLES = ["superadmin", "Propietario", "Administrador", "Entrenador", "Recepcionista", "Miembro"]
PASSWORD = "Clave-de-prueba-123"


# ── App ──────────────────────────────────────────────────────────────────────
@pytest.fixture(scope="session")
def app():
    if not REAL:
        import fakeredis
        import mongomock
        import app.mongo as mongo_mod
        from app.extensions import redis_cliente

        mongo_mod.MongoClient = mongomock.MongoClient          # get_client() crea mongomock
        redis_cliente.connection_pool = fakeredis.FakeStrictRedis(decode_responses=True).connection_pool

    from app import create_app
    from app.extensions import db

    flask_app = create_app()
    flask_app.config.update(TESTING=True)
    with flask_app.app_context():
        nombre_bd = db.engine.url.database or ""
        if REAL:
            assert "test" in nombre_bd.lower(), (
                f"Por seguridad las pruebas solo corren contra una base cuyo nombre contenga 'test' (hay '{nombre_bd}')."
            )
        db.create_all()
    return flask_app


@pytest.fixture()
def client(app):
    return app.test_client()


@pytest.fixture()
def mdb(app):
    """Base de Mongo (real o mongomock) ya limpia."""
    from app.mongo import get_db
    return get_db()


@pytest.fixture(autouse=True)
def limpiar_bases(app):
    """Deja PostgreSQL, Mongo y Redis vacíos antes de cada prueba."""
    from app.extensions import db, redis_cliente, limiter
    from app.mongo import get_db

    with app.app_context():
        db.session.rollback()
        for tabla in reversed(db.metadata.sorted_tables):
            db.session.execute(tabla.delete())
        db.session.commit()
        mongo = get_db()
        if REAL:
            assert "test" in mongo.name.lower(), "La base de Mongo de pruebas debe llamarse '*test*'"
        for coleccion in mongo.list_collection_names():
            mongo.drop_collection(coleccion)
        try:
            redis_cliente.flushdb()
        except Exception:
            pass
        try:
            limiter.reset()
        except Exception:
            pass
    yield


# ── Fábricas de datos ────────────────────────────────────────────────────────
class Fabrica:
    """Crea gimnasios, usuarios y miembros reales (PostgreSQL + Mongo)."""

    def __init__(self, app, client):
        self.app, self.client = app, client
        self._roles = {}

    def _rol(self, nombre):
        from app.extensions import db
        from app.models.pg.rol import Rol
        if nombre not in self._roles:
            rol = Rol.query.filter_by(nombre=nombre).first()
            if rol is None:
                rol = Rol(nombre=nombre)
                db.session.add(rol)
                db.session.commit()
            self._roles[nombre] = rol.id
        return self._roles[nombre]

    def gimnasio(self, nombre="Gimnasio Uno"):
        from app.extensions import db
        from app.models.pg.gimnasio import Gimnasio
        from app.models.pg.organizacion import Organizacion
        with self.app.app_context():
            org = Organizacion(nombre=f"Org {nombre}")
            db.session.add(org)
            db.session.flush()
            gym = Gimnasio(nombre=nombre, activo=True, id_organizacion=org.id)
            db.session.add(gym)
            db.session.commit()
            return gym.id

    def usuario(self, rol, gym_id, email=None, nombre=None, password=PASSWORD, activo=True):
        """Devuelve (id_usuario, email)."""
        from app.extensions import db
        from app.models.pg.usuario import Usuario
        email = email or f"{rol.lower()}-{uuid.uuid4().hex[:8]}@prueba.test"
        with self.app.app_context():
            u = Usuario(nombre=nombre or f"{rol} prueba", email=email, id_rol=self._rol(rol),
                        id_gimnasio=None if rol == "superadmin" else gym_id, activo=activo)
            u.set_password(password)
            db.session.add(u)
            db.session.commit()
            return u.id, email

    def headers(self, email, password=PASSWORD):
        """Inicia sesión por la API real y devuelve la cabecera Authorization."""
        r = self.client.post("/api/auth/login", json={"email": email, "password": password})
        assert r.status_code == 200, f"login de {email} falló: {r.status_code} {r.get_json()}"
        return {"Authorization": f"Bearer {r.get_json()['access_token']}"}

    def personal(self, rol, gym_id, **kw):
        uid, email = self.usuario(rol, gym_id, **kw)
        return uid, self.headers(email)

    def miembro(self, gym_id, nombre="Ana Prueba", con_cuenta=True):
        """Ficha de miembro en Mongo; con cuenta de acceso si `con_cuenta`.
        Devuelve (id_mongo, id_usuario_pg | None, headers | None)."""
        from app.mongo import get_db
        uid, headers = None, None
        if con_cuenta:
            uid, headers = self.personal("Miembro", gym_id, nombre=nombre)
        with self.app.app_context():
            oid = get_db().miembros.insert_one({
                "nombre": nombre, "email": f"{uuid.uuid4().hex[:6]}@prueba.test", "telefono": "7220000000",
                "id_gimnasio_pg": gym_id, "estado": "Activo", "id_usuario_pg": uid,
                "fecha_registro": datetime.now(timezone.utc),
            }).inserted_id
        return oid, uid, headers

    def membresia(self, id_miembro, fin, estado="Activa", tipo="Mensual"):
        from app.mongo import get_db
        with self.app.app_context():
            return get_db().miembro_membresia.insert_one({
                "id_miembro": id_miembro, "fecha_fin": fin, "estado": estado, "tipo_membresia": tipo,
                "fecha_inicio": datetime.now(timezone.utc) - timedelta(days=20),
            }).inserted_id


@pytest.fixture()
def fab(app, client):
    return Fabrica(app, client)
