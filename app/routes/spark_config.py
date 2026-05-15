"""
spark_config.py — Configuración, factory y utilidades compartidas de Spark.

IMPORTANTE: Este módulo NO ejecuta código al importarse.
La SparkSession se crea bajo demanda con get_spark().

Variables de entorno relevantes:
    SPARK_ENABLED=true/false         — habilita los endpoints de análisis
    ANALYTICS_CACHE_TTL_HOURS=24     — tiempo de vida del caché de analíticas (horas)
"""
import os
from datetime import datetime, timedelta
from dotenv import load_dotenv

load_dotenv()

MONGO_URI     = os.getenv("MONGO_URI", "mongodb://mongo:27017/gymdb")
MONGO_DB      = os.getenv("MONGO_DB", "gymdb")
POSTGRES_URI  = os.getenv("POSTGRES_URI", "postgresql+psycopg2://gymuser:gympassword@postgres:5432/gymprodb")
SPARK_ENABLED = os.getenv("SPARK_ENABLED", "false").lower() == "true"

# TTL del caché de analíticas: por defecto 24 h, configurable por env
CACHE_TTL_HOURS = int(os.getenv("ANALYTICS_CACHE_TTL_HOURS", "24"))

# URL JDBC para Spark (psycopg2 URI → JDBC URI)
# Spark usa jdbc:postgresql://host:port/db, no el prefijo SQLAlchemy
def _to_jdbc_url(uri: str) -> str:
    """Convierte postgresql+psycopg2://user:pass@host:port/db → jdbc:postgresql://host:port/db"""
    # Eliminar prefijo SQLAlchemy si existe
    clean = uri.replace("postgresql+psycopg2://", "postgresql://")
    # Extraer credenciales y host
    rest   = clean[len("postgresql://"):]
    if "@" in rest:
        creds, hostpart = rest.split("@", 1)
        user, password  = creds.split(":", 1) if ":" in creds else (creds, "")
    else:
        hostpart = rest
        user = password = ""
    jdbc_url = f"jdbc:postgresql://{hostpart}"
    return jdbc_url, user, password


_JDBC_URL, _JDBC_USER, _JDBC_PASS = _to_jdbc_url(POSTGRES_URI)


def crear_spark_session():
    """
    Crea y retorna una SparkSession configurada para MongoDB.

    Raises:
        RuntimeError: si SPARK_ENABLED=false o si MONGO_URI no está definida.
        ImportError: si pyspark no está instalado.
    """
    if not SPARK_ENABLED:
        raise RuntimeError(
            "Spark está deshabilitado en este entorno (SPARK_ENABLED=false). "
            "Define SPARK_ENABLED=true para habilitar los endpoints de análisis."
        )

    if not MONGO_URI:
        raise RuntimeError(
            "MONGO_URI no está definida. "
            "Ejemplo: MONGO_URI=mongodb://mongo:27017/gymdb"
        )

    from pyspark.sql import SparkSession  # import lazy — no falla al arrancar Flask

    # Java 17 requiere --add-opens para que MLlib (y la serialización interna
    # de Spark) pueda acceder a módulos del JVM que están encapsulados por defecto.
    # Sin estos flags, operaciones de MLlib (KMeans, LinearRegression, etc.)
    # fallan con IllegalAccessException o similares incluso cuando el DataFrame
    # básico funciona correctamente.
    _java17_opens = " ".join([
        "--add-opens=java.base/java.lang=ALL-UNNAMED",
        "--add-opens=java.base/java.lang.invoke=ALL-UNNAMED",
        "--add-opens=java.base/java.lang.reflect=ALL-UNNAMED",
        "--add-opens=java.base/java.io=ALL-UNNAMED",
        "--add-opens=java.base/java.net=ALL-UNNAMED",
        "--add-opens=java.base/java.nio=ALL-UNNAMED",
        "--add-opens=java.base/java.util=ALL-UNNAMED",
        "--add-opens=java.base/java.util.concurrent=ALL-UNNAMED",
        "--add-opens=java.base/java.util.concurrent.atomic=ALL-UNNAMED",
        "--add-opens=java.base/sun.nio.ch=ALL-UNNAMED",
        "--add-opens=java.base/sun.nio.cs=ALL-UNNAMED",
        "--add-opens=java.base/sun.security.action=ALL-UNNAMED",
        "--add-opens=java.base/sun.util.calendar=ALL-UNNAMED",
        "--add-opens=java.security.jgss/sun.security.krb5=ALL-UNNAMED",
    ])

    spark = (
        SparkSession.builder
        .appName("GymPro-Analytics")
        .master("local[*]")
        .config(
            "spark.jars.packages",
            # Mongo-Spark connector (colecciones operacionales)
            "org.mongodb.spark:mongo-spark-connector_2.12:10.3.0,"
            # PostgreSQL JDBC driver (entidades financieras y de plataforma)
            "org.postgresql:postgresql:42.7.3",
        )
        .config("spark.driver.extraJavaOptions",   _java17_opens)
        .config("spark.executor.extraJavaOptions", _java17_opens)
        .config("spark.mongodb.read.connection.uri",  MONGO_URI)
        .config("spark.mongodb.write.connection.uri", MONGO_URI)
        .config("spark.mongodb.read.database",  MONGO_DB)
        .config("spark.mongodb.write.database", MONGO_DB)
        .config("spark.sql.shuffle.partitions", "4")
        .config("spark.ui.showConsoleProgress", "false")
        .getOrCreate()
    )
    spark.sparkContext.setLogLevel("WARN")
    return spark


def leer_tabla_pg(spark, tabla: str, query: str = None):
    """
    Lee una tabla (o query) de PostgreSQL vía JDBC y retorna un DataFrame.

    Args:
        spark:  SparkSession activa (de crear_spark_session())
        tabla:  nombre de la tabla en PostgreSQL (ej. "pagos", "usuarios")
        query:  SQL opcional para lectura parcial, ej. "SELECT * FROM pagos WHERE id_gimnasio = 1"
                Si se pasa query, tabla actúa como alias (requerido por el conector JDBC).

    Ejemplo:
        spark = crear_spark_session()
        df = leer_tabla_pg(spark, "pagos")
        df.groupBy("id_gimnasio").sum("monto").show()
    """
    reader = (
        spark.read.format("jdbc")
        .option("url",      _JDBC_URL)
        .option("user",     _JDBC_USER)
        .option("password", _JDBC_PASS)
        .option("driver",   "org.postgresql.Driver")
    )

    if query:
        reader = reader.option("query", query)
    else:
        reader = reader.option("dbtable", tabla)

    return reader.load()


# ── Singleton SparkSession ────────────────────────────────────────────────────

_spark_instance = None


def get_spark():
    """
    Retorna la SparkSession singleton. La crea en el primer llamado.
    Todos los módulos de analíticas deben usar ESTA función en lugar de
    tener su propio _spark_instance global, para garantizar una sola JVM.
    """
    global _spark_instance
    if _spark_instance is None:
        _spark_instance = crear_spark_session()
    return _spark_instance


# ── Cache de analíticas con TTL ───────────────────────────────────────────────

_CACHE_COLLECTION = "analytics_cache"


def get_mongo_db():
    """
    Retorna la base de datos MongoDB usando pymongo (conexión directa, sin Spark).
    Usado para caché de analíticas y consultas ligeras sin necesidad de Spark.
    """
    from pymongo import MongoClient
    client = MongoClient(MONGO_URI)
    return client[MONGO_DB]


def cache_get(key: str, ttl_hours: int = None) -> dict | None:
    """
    Lee un resultado de analíticas desde MongoDB.
    Retorna None si no existe o si superó el TTL.

    Args:
        key:       identificador único del resultado (incluye gym_id para aislamiento)
        ttl_hours: horas de vigencia; usa ANALYTICS_CACHE_TTL_HOURS si se omite
    """
    ttl = ttl_hours if ttl_hours is not None else CACHE_TTL_HOURS
    try:
        db  = get_mongo_db()
        doc = db[_CACHE_COLLECTION].find_one({"_id": key})
        if not doc:
            return None
        cached_at = doc.get("cached_at")
        if cached_at and (datetime.now() - cached_at) > timedelta(hours=ttl):
            return None   # caché expirado
        doc.pop("_id", None)
        doc.pop("cached_at", None)
        return doc
    except Exception as e:
        print(f"[cache] Error leyendo '{key}': {e}")
        return None


def cache_set(key: str, payload: dict) -> None:
    """
    Guarda (upsert) un resultado de analíticas con timestamp de escritura.

    Args:
        key:     identificador único del resultado
        payload: diccionario con los datos a persistir
    """
    try:
        db = get_mongo_db()
        db[_CACHE_COLLECTION].replace_one(
            {"_id": key},
            {"_id": key, "cached_at": datetime.now(), **payload},
            upsert=True,
        )
    except Exception as e:
        print(f"[cache] Error guardando '{key}': {e}")


def leer_coleccion(spark, collection: str):
    """
    Retorna un DataFrame de la colección MongoDB indicada.

    Pasa connection.uri, database y collection explícitamente en cada
    lectura para evitar el error 'Missing configuration for: database'
    al reutilizar la SparkSession singleton.
    """
    return (
        spark.read.format("mongodb")
        .option("connection.uri", MONGO_URI)
        .option("database",       MONGO_DB)
        .option("collection",     collection)
        .load()
    )
