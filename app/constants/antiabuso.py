"""
constants/antiabuso.py — Umbrales del control de altas repetidas.

Aparte en su propio archivo porque son números de POLÍTICA, no de
implementación: se van a ajustar viendo bloqueos reales en el panel de
superadmin, y conviene poder cambiarlos sin leer la lógica.

IMPORTANTE: estos umbrales nunca bloquean por IP. Ver el docstring de
utils/antiabuso.py para por qué.
"""

# Cabecera con la que el cliente manda la huella del navegador.
CABECERA_HUELLA = "X-GymPro-Huella"

# Altas ya hechas desde la MISMA huella que disparan el bloqueo, dentro de la
# ventana de abajo. En 2: la tercera alta desde el mismo navegador en un mes
# se frena. Una persona que abre tres gimnasios distintos en 30 días desde el
# mismo equipo es raro; una granja de cuentas gratuitas es exactamente eso.
ALTAS_MAX_POR_HUELLA = 2
ALTAS_VENTANA_DIAS = 30
