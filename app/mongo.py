import os
from dotenv import load_dotenv
from pymongo import MongoClient
from pymongo.errors import ConnectionFailure

# Cargar variables de entorno
load_dotenv()

# ──────────────────────────────────────────────
# VARIABLES
# ──────────────────────────────────────────────
MONGO_USER     = os.getenv("MONGO_USER")
MONGO_PASSWORD = os.getenv("MONGO_PASSWORD")
MONGO_CLUSTER  = os.getenv("MONGO_CLUSTER")
MONGO_DB       = os.getenv("MONGO_DB")

# Validación
missing = [k for k, v in {
    "MONGO_USER": MONGO_USER,
    "MONGO_PASSWORD": MONGO_PASSWORD,
    "MONGO_CLUSTER": MONGO_CLUSTER,
    "MONGO_DB": MONGO_DB
}.items() if not v]

if missing:
    raise EnvironmentError(
        f"❌ Variables de entorno faltantes: {', '.join(missing)}"
    )

# ──────────────────────────────────────────────
# URI
# ──────────────────────────────────────────────
MONGO_URI = (
    f"mongodb+srv://{MONGO_USER}:{MONGO_PASSWORD}"
    f"@{MONGO_CLUSTER}/{MONGO_DB}"
    "?retryWrites=true&w=majority"
)

# ──────────────────────────────────────────────
# CLIENTE GLOBAL (singleton)
# ──────────────────────────────────────────────
_client = None


def get_client():
    global _client
    if _client is None:
        try:
            _client = MongoClient(
                MONGO_URI,
                serverSelectionTimeoutMS=5000
            )
            _client.admin.command("ping")
            print("✅ Conectado a MongoDB Atlas")
        except ConnectionFailure as e:
            raise ConnectionError(f"❌ Error conectando a MongoDB: {e}")
    return _client


def get_db():
    client = get_client()
    return client[MONGO_DB]