"""
app/utils/racha.py -- Cálculo de racha de asistencia (días consecutivos).

Extraído de app/routes/miembro/user_dashboard.py (donde vivía como
`_calcular_racha`, privada) a un módulo compartido: tanto el dashboard del
miembro como el motor de logros (app/utils/logros.py, HU-26 / SCRUM-206)
necesitan exactamente el mismo número -- importarla desde un blueprint hacia
otro habría creado un import circular (app.utils.logros <-> user_dashboard).
"""
from datetime import datetime, timedelta


def calcular_racha(mdb, id_miembro):
    try:
        asistencias = list(mdb.asistencias.find(
            {"id_miembro": id_miembro}
        ).sort("fecha", -1))
        if not asistencias:
            return 0

        fechas = []
        for a in asistencias:
            f = a.get("fecha")
            if isinstance(f, datetime): f = f.date()
            elif isinstance(f, str):    f = datetime.strptime(f[:10], "%Y-%m-%d").date()
            if f not in fechas: fechas.append(f)
        fechas.sort(reverse=True)

        hoy = datetime.now().date()
        if (hoy - fechas[0]).days > 1:
            return 0

        racha          = 0
        fecha_esperada = hoy if (hoy - fechas[0]).days == 0 else hoy - timedelta(days=1)
        for f in fechas:
            if f == fecha_esperada:
                racha          += 1
                fecha_esperada -= timedelta(days=1)
            elif f < fecha_esperada:
                break
        return racha
    except Exception as e:
        print(f"Error calcular_racha: {e}")
        return 0
