"""
spark_config.py — Configuración y factory de SparkSession.

IMPORTANTE: Este módulo NO ejecuta código al importarse.
La SparkSession se crea bajo demanda llamando a crear_spark_session().

Para habilitar Spark, definir la variable de entorno:
    SPARK_ENABLED=true

Si SPARK_ENABLED no está definida o es "false", los endpoints de Spark
retornarán 503 en lugar de matar el proceso Flask.
"""
import os
from dotenv import load_dotenv

load_dotenv()

# ─── Variables de conexión ────────────────────────────────────────────────────
MONGO_USER     = os.getenv("MONGO_USER")
MONGO_PASSWORD = os.getenv("MONGO_PASSWORD")
MONGO_CLUSTER  = os.getenv("MONGO_CLUSTER")
DB_NAME        = os.getenv("MONGO_DB", "gym_db")

# Flag que controla si Spark está habilitado en este despliegue.
# Por defecto deshabilitado para que el contenedor arranque sin Java configurado.
SPARK_ENABLED = os.getenv("SPARK_ENABLED", "false").lower() == "true"


def _build_mongo_uri() -> str:
    """Construye la URI de MongoDB Atlas para el conector de Spark."""
    missing = [k for k, v in {
        "MONGO_USER": MONGO_USER,
        "MONGO_PASSWORD": MONGO_PASSWORD,
        "MONGO_CLUSTER": MONGO_CLUSTER,
    }.items() if not v]

    if missing:
        raise RuntimeError(
            f"Spark: variables de entorno requeridas no definidas: {', '.join(missing)}. "
            "Definir MONGO_USER, MONGO_PASSWORD y MONGO_CLUSTER, o deshabilitar "
            "Spark con SPARK_ENABLED=false."
        )

    return (
        f"mongodb+srv://{MONGO_USER}:{MONGO_PASSWORD}"
        f"@{MONGO_CLUSTER}/{DB_NAME}?retryWrites=true&w=majority"
    )


def crear_spark_session():
    """
    Crea y retorna una SparkSession configurada para MongoDB Atlas.

    Raises:
        RuntimeError: si SPARK_ENABLED=false o si faltan variables de entorno.
        ImportError: si pyspark no está instalado.
    """
    if not SPARK_ENABLED:
        raise RuntimeError(
            "Spark está deshabilitado en este entorno (SPARK_ENABLED=false). "
            "Define SPARK_ENABLED=true para habilitar los endpoints de análisis."
        )

    from pyspark.sql import SparkSession  # import lazy — no falla al arrancar Flask

    mongo_uri = _build_mongo_uri()

    spark = (
        SparkSession.builder
        .appName("GymPro-Analytics")
        .master("local[*]")
        .config(
            "spark.jars.packages",
            "org.mongodb.spark:mongo-spark-connector_2.12:10.3.0",
        )
        .config("spark.mongodb.read.connection.uri",  mongo_uri)
        .config("spark.mongodb.write.connection.uri", mongo_uri)
        .config("spark.mongodb.read.database",  DB_NAME)
        .config("spark.mongodb.write.database", DB_NAME)
        .config("spark.sql.shuffle.partitions", "4")
        .config("spark.ui.showConsoleProgress", "false")
        .getOrCreate()
    )
    spark.sparkContext.setLogLevel("WARN")
    return spark


def leer_coleccion(spark, collection: str):
    """
    Retorna un DataFrame de la colección MongoDB indicada.

    Pasa connection.uri, database y collection explícitamente en cada
    lectura para evitar el error 'Missing configuration for: database'
    al reutilizar la SparkSession singleton.
    """
    mongo_uri = _build_mongo_uri()
    return (
        spark.read.format("mongodb")
        .option("connection.uri", mongo_uri)
        .option("database",       DB_NAME)
        .option("collection",     collection)
        .load()
    )
