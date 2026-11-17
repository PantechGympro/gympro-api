"""
mongo.py — Singleton de conexión PyMongo.

Configuración a través de una sola variable de entorno:

    MONGO_URI=mongodb://mongo:27017/gymdb          # Docker local (default)
    MONGO_URI=mongodb://user:pass@host:27017/gymdb  # EC2 con auth
    MONGO_URI=mongodb+srv://user:pass@cluster/db    # Atlas (si aplica)

MONGO_DB se puede sobreescribir para apuntar a una base de datos distinta
sin cambiar la URI completa.
"""
import os
from dotenv import load_dotenv
from pymongo import MongoClient
from pymongo.errors import ConnectionFailure

load_dotenv()

MONGO_URI = os.getenv("MONGO_URI", "mongodb://mongo:27017/gymdb")
MONGO_DB  = os.getenv("MONGO_DB", "gymdb")

_client: MongoClient | None = None


# ── Índices (auditoría nov/2026, rendimiento) ───────────────────────────────
# Los únicos índices de Mongo se creaban en seed_pg.py, que en producción no
# corre (la base ya tiene datos). Resultado: cada dashboard, reporte y
# analítica filtraba por gimnasio/miembro/fecha recorriendo la colección
# completa. Con el historial actual apenas se nota; crece linealmente con
# cada check-in y cada pago.
#
# create_index es idempotente (si ya existe con las mismas opciones no hace
# nada) y aquí son todos NO únicos, así que no pueden rechazar datos
# existentes. Cada uno va en su propio try: un índice que ya exista con
# otras opciones no debe impedir crear los demás ni tumbar el arranque.
_INDICES = [
    ("asistencias",               [("id_gimnasio", 1), ("fecha", -1)]),
    ("asistencias",               [("id_miembro", 1), ("fecha", -1)]),
    ("pagos",                     [("id_gimnasio", 1), ("fecha_pago", -1)]),
    ("pagos",                     [("id_miembro", 1), ("fecha_pago", -1)]),
    ("miembros",                  [("id_gimnasio_pg", 1), ("estado", 1)]),
    ("entrenamientos_realizados", [("id_miembro", 1), ("fecha", -1)]),
    ("progreso_fisico",           [("id_miembro", 1), ("fecha_registro", 1)]),
    ("notificaciones",            [("id_usuario_pg", 1)]),
]


def _asegurar_indices(db) -> None:
    for coleccion, claves in _INDICES:
        try:
            db[coleccion].create_index(claves, background=True)
        except Exception as exc:  # nunca bloquear el arranque por un índice
            print(f"⚠️  Índice {coleccion}{claves} no creado: {exc}")


def get_client() -> MongoClient:
    global _client
    if _client is None:
        try:
            _client = MongoClient(MONGO_URI, serverSelectionTimeoutMS=5000)
            _client.admin.command("ping")
            print(f"✅ Conectado a MongoDB: {MONGO_URI.split('@')[-1]}")  # oculta credenciales en log
            _asegurar_indices(_client[MONGO_DB])
        except ConnectionFailure as e:
            raise ConnectionError(f"❌ Error conectando a MongoDB: {e}")
    return _client


def get_db():
    return get_client()[MONGO_DB]
