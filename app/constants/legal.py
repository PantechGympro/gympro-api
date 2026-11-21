"""
constants/legal.py -- version vigente de los documentos legales y plazos de baja.

La version del aviso que esta fuente declara es la que el sistema exige haber
aceptado: `GET /api/auth/me` compara `usuarios.aviso_version` contra ella y, si
no coincide, el portal muestra la pasarela de aceptacion.

IMPORTANTE: debe ir sincronizada con gympro-web/src/constants/legal.js. Si se
cambia el texto del aviso, se sube la version en los DOS lados; de lo contrario
el portal pediria aceptar una version que el servidor no reconoce (o al
contrario, no la pediria nunca).
"""

VERSION_AVISO = "2.0"
VERSION_TERMINOS = "1.0"

# Dias que median entre que el propietario confirma la eliminacion de su cuenta
# y la purga definitiva. Existe para que un arrepentimiento sea reversible: la
# baja se puede cancelar desde el panel mientras no venza el plazo.
DIAS_GRACIA_BAJA = 30
