import os
import sys
from dotenv import load_dotenv
from pyspark.sql import SparkSession

# Cargar variables de entorno
load_dotenv()

# ──────────────────────────────────────────────────────────────────────────────
# CONFIGURACIÓN DE BASE DE DATOS
# ──────────────────────────────────────────────────────────────────────────────
MONGO_USER     = os.getenv("MONGO_USER")
MONGO_PASSWORD = os.getenv("MONGO_PASSWORD")
MONGO_CLUSTER  = os.getenv("MONGO_CLUSTER")
DB_NAME        = os.getenv("MONGO_DB", "gym_db")

# Validar variables de entorno
_missing = [k for k, v in {
    "MONGO_USER": MONGO_USER, "MONGO_PASSWORD": MONGO_PASSWORD,
    "MONGO_CLUSTER": MONGO_CLUSTER, "MONGO_DB": DB_NAME,
}.items() if not v]

if _missing:
    print(f" Variables de entorno faltantes: {', '.join(_missing)}")
    sys.exit(1)

# URI de conexión
MONGO_URI = (
    f"mongodb+srv://{MONGO_USER}:{MONGO_PASSWORD}"
    f"@{MONGO_CLUSTER}/{DB_NAME}?retryWrites=true&w=majority"
)

# ──────────────────────────────────────────────────────────────────────────────
# INICIALIZACIÓN DE SPARK
# ──────────────────────────────────────────────────────────────────────────────
def crear_spark_session():
    spark = (
        SparkSession.builder
        .appName("Análisis")
        .master("local[*]")
        # Conector oficial MongoDB ↔ Spark
        .config(
            "spark.jars.packages",
            "org.mongodb.spark:mongo-spark-connector_2.12:10.3.0"
        )
        # Configuración de conexión MongoDB
        .config("spark.mongodb.read.connection.uri",  MONGO_URI)
        .config("spark.mongodb.write.connection.uri", MONGO_URI)
        # Reducir logs innecesarios
        .config("spark.sql.shuffle.partitions", "4")
        .config("spark.ui.showConsoleProgress", "false")
        .getOrCreate()
    )

    spark.sparkContext.setLogLevel("WARN")
    print(f"    Spark {spark.version} iniciado")
    return spark