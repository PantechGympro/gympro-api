"""
spark_config.py — Configuración y factory de SparkSession.

IMPORTANTE: Este módulo NO ejecuta código al importarse.
La SparkSession se crea bajo demanda llamando a crear_spark_session().

Para habilitar Spark, definir la variable de entorno:
    SPARK_ENABLED=true

Si SPARK_ENABLED no está definida o es "false", los endpoints de Spark
retornarán 503 en lugar de matar el proceso Flask.

La URI de MongoDB se lee de MONGO_URI (igual que mongo.py) para mantener
una sola fuente de verdad de la conexión.
"""
import os
from dotenv import load_dotenv

load_dotenv()

MONGO_URI     = os.getenv("MONGO_URI", "mongodb://mongo:27017/gymdb")
MONGO_DB      = os.getenv("MONGO_DB", "gymdb")
POSTGRES_URI  = os.getenv("POSTGRES_URI", "postgresql+psycopg2://gymuser:gympassword@postgres:5432/gymprodb")
SPARK_ENABLED = os.getenv("SPARK_ENABLED", "false").lower() == "true"

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
