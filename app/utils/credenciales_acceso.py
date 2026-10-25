"""
credenciales_acceso.py — Alta de cuentas creadas por un administrador.

SCRUM-224 (item E/F): antes el formulario de alta de miembros/staff hacía
que el propio Propietario/Administrador escribiera la contraseña de la
cuenta nueva (o, peor, caía a un fallback fijo "gym123" si la dejaba en
blanco — ver el crear_miembro() anterior a este cambio). Esto tenía dos
problemas: la persona nueva nunca eligió su propia contraseña (no "se
siente dueña" de su cuenta) y, sobre todo, quien la da de alta terminaba
conociendo la contraseña real de otra persona.

Este módulo centraliza las piezas que reemplazan ese flujo, usadas por
owner_gym/miembros.py, owner_gym/owner_trainers.py y recepcionista_routes.py
(a través de crear_miembro de miembros.py):
  1. generar_password_temporal() — contraseña aleatoria, nunca vista por
     quien da de alta la cuenta.
  2. enviar_credenciales_acceso() — el correo de bienvenida que la entrega
     a la persona (mismo patrón try/except silencioso que
     owner_gym/onboarding.py::_enviar_bienvenida(): si el correo falla, la
     cuenta ya quedó creada y no se bloquea el alta por eso).
  3. reenviar_credenciales_acceso() — para cuando el correo original nunca
     llegó (caso real: una recepcionista registrada con un correo @hotmail
     nunca lo recibió). Genera una contraseña NUEVA (invalida la anterior,
     por si de verdad se perdió o quedó atrapada en el correo que nunca
     llegó) y la reenvía — no reenvía la misma contraseña del alta original.

Sobre por qué un correo puede no llegar
----------------------------------------
enviar_credenciales_acceso() solo sabe si el SMTP (Resend, ver MAIL_SERVER en
config.py) aceptó el mensaje para su entrega — no si terminó en la bandeja
de entrada, en spam/correo no deseado, o si el proveedor de destino lo
descartó en silencio. Esto era especialmente común con Hotmail/Outlook/Live
(dominios de Microsoft), que filtran de forma agresiva correo transaccional
nuevo que contiene una contraseña. Por eso:
  - El correo incluye una versión en texto plano además del HTML (`body=`,
    no solo `html=`) -- un correo sin alternativa de texto plano es una
    señal común de spam para varios filtros.
  - enviar_credenciales_acceso() devuelve True/False según si el envío por
    SMTP tuvo éxito, para que quien llama pueda avisar si de plano el envío
    falló -- pero un True no garantiza que la persona la vea en su bandeja
    de entrada, por eso existe el botón de reenviar.
  - Se migró de SMTP de Gmail a Resend con dominio propio verificado
    (gympro.lat, SPF/DKIM/DMARC bajo nuestro control) -- mejora real de
    entregabilidad que ningún cambio de código puede lograr por sí solo
    mientras se use una cuenta de Gmail genérica. Ver utils/email_remitentes.py
    para el remitente que le corresponde a cada tipo de correo.

La propia persona cambia esta contraseña generada en su primer login —
ver Usuario.debe_cambiar_password (migración 025) y
PUT /api/auth/cambiar-password-inicial.
"""
import logging
import secrets
import string

from flask_mail import Message

from app.extensions import mail
from app.utils.email_remitentes import remitente, correo_contacto

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


def _mensaje_credenciales(email: str, nombre: str, password: str,
                           rol_label: str, nombre_gym: str, reenvio: bool) -> Message:
    asunto = ("Tus credenciales de GymPro" if reenvio else "Bienvenido a GymPro") \
        + (f" — {nombre_gym}" if nombre_gym else "")
    texto_plano = (
        f"Hola {nombre},\n\n"
        + ("Te reenviamos tu contraseña de acceso a GymPro" if reenvio else
           f"Se creó tu cuenta de {rol_label} en GymPro")
        + (f" en {nombre_gym}.\n\n" if nombre_gym else ".\n\n")
        + f"Tu contraseña temporal es: {password}\n\n"
        "Por tu seguridad, al iniciar sesión por primera vez con esta "
        "contraseña se te pedirá cambiarla por una propia antes de continuar.\n\n"
        "Si no esperabas este correo o no te llegó a la bandeja de entrada "
        "principal, revisa también la carpeta de correo no deseado / spam.\n\n"
        f"Si tienes dudas escribe a {correo_contacto()}\n"
        "Equipo GymPro"
    )
    return Message(
        subject    = asunto,
        sender     = remitente("MAIL_SENDER_CUENTAS"),
        recipients = [email],
        body       = texto_plano,
        html       = f"""
<html><body style="font-family:sans-serif;color:#333;max-width:600px;margin:auto">
  <div style="background:#1a1a2e;padding:32px;border-radius:12px 12px 0 0;text-align:center">
    <h1 style="color:#fff;margin:0">GYM<span style="color:#6c63ff">PRO</span></h1>
  </div>
  <div style="background:#f9f9f9;padding:32px;border-radius:0 0 12px 12px">
    <h2>{"Hola de nuevo" if reenvio else "Bienvenido"}, {nombre}!</h2>
    <p>{
      "Te reenviamos tu contraseña de acceso a GymPro" if reenvio else
      f"Se creó tu cuenta de <strong>{rol_label}</strong>"
      + (f' en <strong>{nombre_gym}</strong>' if nombre_gym else '')
      + " dentro de GymPro."
    }</p>
    <div style="background:#fff;border:1px solid #e0e0e0;border-radius:8px;padding:20px;margin:24px 0">
      <p style="margin:0 0 6px;color:#888;font-size:13px">Tu contraseña temporal es:</p>
      <p style="margin:0;font-size:22px;font-weight:700;letter-spacing:1px;font-family:monospace">{password}</p>
    </div>
    <p>Por tu seguridad, al iniciar sesión por primera vez con esta contraseña
       se te pedirá cambiarla por una propia antes de continuar.</p>
    <p style="color:#888;font-size:13px">
      ¿No te llegó este correo a tiempo? Revisa también tu carpeta de correo
      no deseado / spam — algunos proveedores (Hotmail, Outlook) filtran de
      forma estricta los correos con contraseñas.
    </p>
    <p style="color:#888;font-size:13px">
      Si tienes dudas escribe a {correo_contacto()}<br>
      Equipo GymPro
    </p>
  </div>
</body></html>
""",
    )


def enviar_credenciales_acceso(email: str, nombre: str, password: str,
                                rol_label: str, nombre_gym: str = "") -> bool:
    """
    Envía la contraseña generada por correo. Falla silenciosamente (solo
    log) -- la cuenta ya quedó creada antes de llamar a esta función, así
    que un correo que no sale nunca debe tumbar el alta.

    Devuelve True si el SMTP aceptó el envío, False si hubo una excepción
    (ver nota del módulo: True no garantiza que la persona lo reciba en su
    bandeja principal, solo que el envío no falló del lado del servidor).
    """
    try:
        msg = _mensaje_credenciales(email, nombre, password, rol_label, nombre_gym, reenvio=False)
        mail.send(msg)
        logger.info("Credenciales de acceso enviadas a %s", email)
        return True
    except Exception as exc:
        logger.warning("No se pudieron enviar las credenciales de acceso a %s: %s", email, exc)
        return False


def reenviar_credenciales_acceso(usuario, rol_label: str, nombre_gym: str = "") -> bool:
    """
    Genera una contraseña temporal NUEVA para `usuario` (invalida la que se
    le haya mandado antes -- no tiene caso reenviar la misma si existe la
    sospecha de que nunca llegó o se perdió) y la reenvía por correo.

    Marca debe_cambiar_password=True y dejar el commit al llamador (igual
    que el resto de este módulo, no abre su propia transacción).

    Devuelve True/False igual que enviar_credenciales_acceso() -- ver su
    docstring sobre qué significa (y qué NO significa) ese resultado.
    """
    nueva_password = generar_password_temporal()
    usuario.set_password(nueva_password)
    usuario.debe_cambiar_password = True
    try:
        msg = _mensaje_credenciales(usuario.email, usuario.nombre, nueva_password, rol_label, nombre_gym, reenvio=True)
        mail.send(msg)
        logger.info("Credenciales reenviadas a %s", usuario.email)
        return True
    except Exception as exc:
        logger.warning("No se pudieron reenviar las credenciales a %s: %s", usuario.email, exc)
        return False
