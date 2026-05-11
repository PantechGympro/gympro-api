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


def get_client() -> MongoClient:
    global _client
    if _client is None:
        try:
            _client = MongoClient(MONGO_URI, serverSelectionTimeoutMS=5000)
            _client.admin.command("ping")
            print(f"✅ Conectado a MongoDB: {MONGO_URI.split('@')[-1]}")  # oculta credenciales en log
        except ConnectionFailure as e:
            raise ConnectionError(f"❌ Error conectando a MongoDB: {e}")
    return _client


def get_db():
    return get_client()[MONGO_DB]
