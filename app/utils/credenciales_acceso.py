"""
credenciales_acceso.py — Alta de cuentas creadas por un administrador.

SCRUM-224 (item E/F): antes el formulario de alta de miembros/staff hacía
que el propio Propietario/Administrador escribiera la contraseña de la
cuenta nueva (o, peor, caía a un fallback fijo "gym123" si la dejaba en
blanco — ver el crear_miembro() anterior a este cambio). Esto tenía dos
problemas: la persona nueva nunca eligió su propia contraseña (no "se
siente dueña" de su cuenta) y, sobre todo, quien la da de alta terminaba
conociendo la contraseña real de otra persona.

Este módulo centraliza las dos piezas que reemplazan ese flujo, usadas
por owner_gym/miembros.py y owner_gym/owner_trainers.py:
  1. generar_password_temporal() — contraseña aleatoria, nunca vista por
     quien da de alta la cuenta.
  2. enviar_credenciales_acceso() — el correo de bienvenida que la entrega
     a la persona (mismo patrón try/except silencioso que
     owner_gym/onboarding.py::_enviar_bienvenida(): si el correo falla, la
     cuenta ya quedó creada y no se bloquea el alta por eso, solo se
     registra un warning).

La propia persona cambia esta contraseña generada en su primer login —
ver Usuario.debe_cambiar_password (migración 025) y
PUT /api/auth/cambiar-password-inicial.
"""
import logging
import secrets
import string

from flask_mail import Message

from app.extensions import mail

logger = logging.getLogger(__name__)

_SIMBOLOS = "!@#$%*?-_"


def generar_password_temporal(longitud: int = 12) -> str:
    """
    Contraseña aleatoria criptográficamente segura (secrets, no random),
    con al menos una mayúscula, una minúscula, un dígito y un símbolo —
    para que no sea rechazada por ninguna validación de fuerza existente
    en el frontend (ver passStrength() en RegisterGym.jsx).
    """
    alfabeto = string.ascii_letters + string.digits + _SIMBOLOS
    while True:
        password = "".join(secrets.choice(alfabeto) for _ in range(longitud))
        if (any(c.islower() for c in password)
                and any(c.isupper() for c in password)
                and any(c.isdigit() for c in password)
                and any(c in _SIMBOLOS for c in password)):
            return password


def enviar_credenciales_acceso(email: str, nombre: str, password: str,
                                rol_label: str, nombre_gym: str = "") -> None:
    """Envía la contraseña generada por correo. Falla silenciosamente (solo log)."""
    try:
        msg = Message(
            subject    = "Bienvenido a GymPro" + (f" — {nombre_gym}" if nombre_gym else ""),
            recipients = [email],
            html       = f"""
<html><body style="font-family:sans-serif;color:#333;max-width:600px;margin:auto">
  <div style="background:#1a1a2e;padding:32px;border-radius:12px 12px 0 0;text-align:center">
    <h1 style="color:#fff;margin:0">GYM<span style="color:#6c63ff">PRO</span></h1>
  </div>
  <div style="background:#f9f9f9;padding:32px;border-radius:0 0 12px 12px">
    <h2>Bienvenido, {nombre}!</h2>
    <p>Se creó tu cuenta de <strong>{rol_label}</strong>
       {f'en <strong>{nombre_gym}</strong>' if nombre_gym else ''} dentro de GymPro.</p>
    <div style="background:#fff;border:1px solid #e0e0e0;border-radius:8px;padding:20px;margin:24px 0">
      <p style="margin:0 0 6px;color:#888;font-size:13px">Tu contraseña temporal es:</p>
      <p style="margin:0;font-size:22px;font-weight:700;letter-spacing:1px;font-family:monospace">{password}</p>
    </div>
    <p>Por tu seguridad, al iniciar sesión por primera vez con esta contraseña
       se te pedirá cambiarla por una propia antes de continuar.</p>
    <p style="color:#888;font-size:13px">
      Si tienes dudas escribe a soporteGympro@gmail.com<br>
      Equipo GymPro
    </p>
  </div>
</body></html>
""",
        )
        mail.send(msg)
        logger.info("Credenciales de acceso enviadas a %s", email)
    except Exception as exc:
        logger.warning("No se pudieron enviar las credenciales de acceso a %s: %s", email, exc)
