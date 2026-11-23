"""
utils/avisos_citas.py -- Aviso unico (in-app + push + email) cuando se crea
o asigna una cita, sin importar quien la haya agendado.

Antes esta logica vivia duplicada e incompleta: recepcionista_routes.create_cita
la tenia completa (notificacion in-app -> dispara push via crear_notificacion
-> y ademas un correo HTML), pero trainer_routes.create_session (cuando el
ENTRENADOR agenda una sesion directamente con su cliente) no avisaba nada en
absoluto -- ni in-app, ni push, ni correo, y la cita ni siquiera aparecia en
"Mis Citas" del miembro porque se guardaba en una coleccion distinta
(`sesiones`, no `citas`). Ver trainer_routes.create_session() para el lado
que ahora escribe tambien en `citas` y llama esta funcion.

Se extrae aqui para que cualquier flujo futuro que cree una cita (o la
reagende) use el mismo aviso, en vez de reinventarlo.
"""
from datetime import datetime, timezone

from app.routes.compartido.notificaciones import crear_notificacion


def _email_base(titulo: str, saludo: str, cuerpo_html: str, fecha_fmt: str, hora_fmt: str,
                 tipo: str, notas: str = "") -> str:
    notas_html = (
        f"<p style='color:#9AA4B8;font-size:13px;'><em>{notas}</em></p>" if notas else ""
    )
    return f"""
    <div style="font-family:sans-serif;max-width:580px;margin:0 auto;
                background:#0f1117;color:#F3F5F9;padding:32px;border-radius:14px;
                border:1px solid rgba(255,255,255,.08);">
      <div style="display:flex;align-items:center;gap:10px;margin-bottom:24px;">
        <div style="width:36px;height:36px;border-radius:8px;background:#3377FF;
                    display:flex;align-items:center;justify-content:center;
                    font-weight:800;font-size:16px;color:#fff;">G</div>
        <span style="font-weight:800;font-size:18px;letter-spacing:.5px;">GYM PRO</span>
      </div>
      <h2 style="color:#3377FF;margin:0 0 8px;">{titulo}</h2>
      <p style="margin:0 0 20px;color:#9AA4B8;">Hola <strong style="color:#F3F5F9;">{saludo}</strong>,</p>
      {cuerpo_html}
      <div style="margin-top:28px;padding:16px;background:#121520;border-radius:10px;
                  border:1px solid rgba(255,255,255,.07);">
        <p style="margin:0 0 6px;font-size:13px;color:#9AA4B8;">📅 Fecha</p>
        <p style="margin:0 0 12px;font-weight:700;font-size:15px;">{fecha_fmt}</p>
        <p style="margin:0 0 6px;font-size:13px;color:#9AA4B8;">🕐 Hora</p>
        <p style="margin:0 0 12px;font-weight:700;font-size:15px;">{hora_fmt}</p>
        <p style="margin:0 0 6px;font-size:13px;color:#9AA4B8;">📋 Tipo</p>
        <p style="margin:0;font-weight:700;font-size:15px;">{tipo}</p>
      </div>
      {notas_html}
      <p style="margin-top:28px;font-size:11px;color:#555;">
        GymPro — Este mensaje es automático, por favor no respondas.
      </p>
    </div>"""


def notificar_cita(
    db,
    *,
    gym_id: int,
    cita_id: str,
    fecha_fmt: str,
    hora_fmt: str,
    tipo: str,
    client_name: str,
    trainer_name: str,
    notas: str = "",
    miembro_doc: dict | None = None,
    trainer_pg=None,
    avisar_miembro: bool = True,
    avisar_entrenador: bool = True,
) -> None:
    """
    Avisa al miembro y/o al entrenador de una cita nueva: notificacion in-app
    (que ya dispara push, Expo en movil y WebPush en navegador -- ver
    compartido/notificaciones._enviar_push) y, si hay correo, tambien email.
    Best-effort en ambos canales: un fallo aqui nunca debe tumbar la creacion
    de la cita que ya se confirmo en la base de datos.
    """
    if avisar_miembro and miembro_doc and miembro_doc.get("id_usuario_pg"):
        crear_notificacion(
            db=db,
            id_usuario_pg=miembro_doc["id_usuario_pg"],
            id_gimnasio_pg=gym_id,
            tipo="cita_nueva",
            titulo="Nueva cita agendada",
            mensaje=(
                f"Se agendó una cita para el {fecha_fmt} a las {hora_fmt}. "
                f"Tipo: {tipo}."
                + (f" Responsable: {trainer_name}." if trainer_pg else "")
            ),
            referencia_tipo="cita",
            referencia_id=cita_id,
        )

    if avisar_entrenador and trainer_pg:
        crear_notificacion(
            db=db,
            id_usuario_pg=trainer_pg.id,
            id_gimnasio_pg=gym_id,
            tipo="cita_nueva",
            titulo="Nueva cita asignada",
            mensaje=(
                f"Se te asignó una cita con {client_name} "
                f"el {fecha_fmt} a las {hora_fmt}. "
                f"Tipo: {tipo}."
            ),
            referencia_tipo="cita",
            referencia_id=cita_id,
        )

    # ── Emails (best-effort, igual que antes) ────────────────────────────────
    try:
        from flask_mail import Message
        from app.extensions import mail
        from app.utils.email_remitentes import remitente

        if avisar_miembro and miembro_doc and miembro_doc.get("email") and "@" in miembro_doc["email"]:
            cuerpo = (
                "<p>Tu cita ha sido agendada exitosamente.</p>"
                + (f'<p style="color:#9AA4B8;">Tu cita será atendida por '
                   f'<strong style="color:#F3F5F9;">{trainer_name}</strong>.</p>' if trainer_pg else "")
            )
            mail.send(Message(
                subject=f"GymPro — Cita agendada para el {fecha_fmt}",
                sender=remitente("MAIL_SENDER_NOTIFICACIONES"),
                recipients=[miembro_doc["email"]],
                html=_email_base("Cita confirmada", miembro_doc.get("nombre", client_name),
                                  cuerpo, fecha_fmt, hora_fmt, tipo, notas),
            ))

        if avisar_entrenador and trainer_pg and trainer_pg.email:
            cuerpo = (
                "<p>Se te ha asignado una nueva cita.</p>"
                f'<p style="color:#9AA4B8;">Cliente: <strong style="color:#F3F5F9;">{client_name}</strong></p>'
            )
            mail.send(Message(
                subject=f"GymPro — Nueva cita asignada: {client_name} el {fecha_fmt}",
                sender=remitente("MAIL_SENDER_NOTIFICACIONES"),
                recipients=[trainer_pg.email],
                html=_email_base("Nueva cita asignada", trainer_pg.nombre,
                                  cuerpo, fecha_fmt, hora_fmt, tipo, notas),
            ))
    except Exception as ex:
        import logging
        logging.getLogger(__name__).warning("Email de cita no enviado: %s", ex)
