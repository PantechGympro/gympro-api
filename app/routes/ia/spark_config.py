"""
spark_config.py — Utilidades compartidas de analítica (cache MongoDB + acceso a datos).

NOTA: El stack de IA fue migrado de PySpark → scikit-learn para eliminar la
dependencia de JVM y la descarga de JARs desde Maven Central en tiempo de build.
Todos los modelos corren en proceso, sin internet, con latencias < 1 s.

Variables de entorno:
    MONGO_URI                  — URI de conexión MongoDB (default: mongodb://mongo:27017/gymdb)
    MONGO_DB                   — Nombre de la base de datos (default: gymdb)
    ANALYTICS_CACHE_TTL_HOURS  — TTL del caché en horas (default: 24)
"""
import os
from datetime import datetime, timedelta
from dotenv import load_dotenv

load_dotenv()

MONGO_URI         = os.getenv("MONGO_URI", "mongodb://mongo:27017/gymdb")
MONGO_DB          = os.getenv("MONGO_DB", "gymdb")
CACHE_TTL_HOURS   = int(os.getenv("ANALYTICS_CACHE_TTL_HOURS", "24"))

# ── Acceso a MongoDB (pymongo) ────────────────────────────────────────────────

def get_mongo_db():
    """Retorna la base de datos MongoDB via pymongo (conexión directa, sin Spark)."""
    from pymongo import MongoClient
    client = MongoClient(MONGO_URI)
    return client[MONGO_DB]


# ── Caché de analíticas con TTL ───────────────────────────────────────────────

_CACHE_COLLECTION = "analytics_cache"


def cache_get(key: str, ttl_hours: int = None) -> dict | None:
    """
    Lee un resultado de analíticas desde MongoDB.
    Retorna None si no existe o si superó el TTL.
    """
    ttl = ttl_hours if ttl_hours is not None else CACHE_TTL_HOURS
    try:
        db  = get_mongo_db()
        doc = db[_CACHE_COLLECTION].find_one({"_id": key})
        if not doc:
            return None
        cached_at = doc.get("cached_at")
        if cached_at and (datetime.now() - cached_at) > timedelta(hours=ttl):
            return None
        doc.pop("_id", None)
        doc.pop("cached_at", None)
        return doc
    except Exception as e:
        print(f"[cache] Error leyendo '{key}': {e}")
        return None


def cache_set(key: str, payload: dict) -> None:
    """Guarda (upsert) un resultado de analíticas con timestamp de escritura."""
    try:
        db = get_mongo_db()
        db[_CACHE_COLLECTION].replace_one(
            {"_id": key},
            {"_id": key, "cached_at": datetime.now(), **payload},
            upsert=True,
        )
    except Exception as e:
        print(f"[cache] Error guardando '{key}': {e}")


# ── Resolución de gimnasio (con override para superadmin) ─────────────────────

def resolve_gym_id():
    """
    Devuelve el id de gimnasio al que se acota la petición de analítica actual.

    - Usuarios de un gimnasio (owner_gym, entrenador, recepcionista): usa el
      id_gimnasio contenido en el JWT.
    - superadmin: al no estar ligado a un gimnasio, puede acotar el análisis a
      uno específico mediante el parámetro de query 'gym_id' o la cabecera
      'X-Gym-ID'. Sin ese override, devuelve None.
    """
    from flask import request
    from flask_jwt_extended import get_jwt
    claims = get_jwt()
    if claims.get("role") == "superadmin":
        gid = request.args.get("gym_id") or request.headers.get("X-Gym-ID")
        if gid:
            try:
                return int(gid)
            except (TypeError, ValueError):
                return None
        return None
    return claims.get("id_gimnasio")


# ── Quién puede ver qué analítica (auditoría nov/2026, OWASP A01) ────────────
# Antes estos endpoints solo pedían sesión (@jwt_required): un Miembro podía
# consultar por la API los ingresos del gimnasio, la segmentación de clientes
# o el riesgo de cancelación de otros socios, y disparar los /train (que
# recalculan todo el modelo) sin límite. La interfaz ya ocultaba esas
# pantallas, pero ocultar un botón no es control de acceso.
from app.constants.roles import (  # noqa: E402
    ROL_SUPERADMIN, ROL_PROPIETARIO, ROL_ADMINISTRADOR,
    ROL_ENTRENADOR, ROL_RECEPCIONISTA,
)

# Datos de negocio (ingresos, churn, RFM): solo dirección del gimnasio.
ROLES_ANALITICA_NEGOCIO = (ROL_PROPIETARIO, ROL_ADMINISTRADOR, ROL_SUPERADMIN)
# Recepción consulta el resumen de ingresos/afluencia, pero no lo recalcula.
ROLES_ANALITICA_RECEPCION = ROLES_ANALITICA_NEGOCIO + (ROL_RECEPCIONISTA,)
# Segmentación y progreso de miembros: también el entrenador (acotado a los suyos).
ROLES_ANALITICA_ENTRENO = ROLES_ANALITICA_NEGOCIO + (ROL_ENTRENADOR,)
# Lectura para todo el personal.
ROLES_ANALITICA_PERSONAL = ROLES_ANALITICA_ENTRENO + (ROL_RECEPCIONISTA,)
