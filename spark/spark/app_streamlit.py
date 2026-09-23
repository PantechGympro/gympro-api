import streamlit as st
import pandas as pd
from pyspark.sql.functions import sum
from spark_config import crear_spark_session, DB_NAME

# ──────────────────────────────────────────────
# CONFIG STREAMLIT
# ──────────────────────────────────────────────
st.set_page_config(page_title="Análisis GYM", layout="wide")
st.title(" Ingresos por Método de Pago")

# ──────────────────────────────────────────────
# CREAR SPARK SESSION
# ──────────────────────────────────────────────
@st.cache_resource
def get_spark():
    return crear_spark_session()

spark = get_spark()

# ──────────────────────────────────────────────
# CARGAR DATOS DESDE MONGODB
# ──────────────────────────────────────────────
@st.cache_data
def cargar_datos():
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

    return resultado.toPandas()

df_pandas = cargar_datos()

# ──────────────────────────────────────────────
# MOSTRAR RESULTADOS
# ──────────────────────────────────────────────
if df_pandas.empty:
    st.warning("No hay datos en la colección pagos.")
else:
    st.subheader("Tabla de resultados")
    st.dataframe(df_pandas, use_container_width=True)

    st.subheader("Gráfica de ingresos")
    st.bar_chart(
        df_pandas.set_index("metodo_pago")["total_ingreso"]
    )