from pyspark.sql.functions import sum
from spark_config import crear_spark_session, DB_NAME
spark = crear_spark_session()

print("=== MAPREDUCE: INGRESOS POR MÉTODO DE PAGO ===")

df = (
    spark.read
    .format("mongodb")
    .option("database", DB_NAME)
    .option("collection", "pagos")
    .load()
)

resultado = (
    df.groupBy("metodo_pago")  
      .agg(sum("monto").alias("total_ingreso"))  
      .orderBy("total_ingreso", ascending=False)
)

resultado.show()

spark.stop()