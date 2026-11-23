"""
constants/planes.py — Parámetros comerciales de los planes y del periodo de prueba.

Una sola fuente para los números que decide el negocio, no el código: cuánto
dura la prueba gratuita y cuál es el plan al que se cae cuando no hay nada
contratado. Estaban repartidos como literales en onboarding.py, billing.py y
el correo de bienvenida, y por eso se habían desincronizado: el correo
prometía 14 días, la suscripción ponía el cobro a 14 y nada la terminaba.

IMPORTANTE: DIAS_PRUEBA lo decide el servidor, nunca el cliente. El endpoint
de alta de suscripción aceptaba `dias_prueba` en el cuerpo de la petición, lo
que permitía pedirse una prueba de años.
"""

# Duración del periodo de prueba de los planes de pago.
DIAS_PRUEBA = 7

# Aviso al propietario cuando a la prueba le quedan estos días o menos.
DIAS_AVISO_FIN_PRUEBA = 2

# Plan gratuito: a donde cae un gimnasio cuando su prueba termina sin
# contratar, y el default cuando no se puede resolver un plan.
PLAN_GRATUITO = "starter"
