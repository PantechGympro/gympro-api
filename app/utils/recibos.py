"""
utils/recibos.py -- Recibos por correo de transacciones reales de dinero
(venta POS, renovacion de membresia), mas su registro en la bitacora de
seguridad (bitacora_eventos).

Peticion explicita del usuario: "cuando se hagan transacciones como
compra de productos en el punto de venta y renovacion de membresias, que
le mande un correo al propietario y al miembro que hizo la compra con el
ticket correspondiente, para que se refleje por correo y en los
registros del sistema para una mejor seguridad de las transacciones".

Antes de este modulo, ventas.py ya le mandaba el ticket solo al miembro
(ver _send_ticket_email en ese archivo, que se deja intacta para no
romper lo que ya funcionaba) y pagos.py no mandaba nada. Este modulo
agrega lo que faltaba sin duplicar la plantilla HTML por cada punto de
entrada de dinero:

  - El mismo aviso TAMBIEN al propietario del gimnasio (via
    usuario_propietario_de(), compartida con pasarelas.py).
  - Un recibo equivalente para renovaciones de membresia (antes no
    mandaban nada en absoluto).
  - Una fila de bitacora_eventos por cada transaccion aprobada --
    registrar_evento_transaccion() hace su propio commit porque
    ventas.py/pagos.py/pagos_online.py son endpoints Mongo-first que de
    otro modo nunca tocan la sesion de SQLAlchemy.

Los 3 puntos de entrada de dinero del sistema llaman a este modulo:
  - routes/owner_gym/ventas.py   -- venta POS en persona
  - routes/owner_gym/pagos.py    -- pago/renovacion de membresia en persona
  - routes/pagos_online.py       -- venta o renovacion pagada en linea
                                     (webhook/reconciliacion de PayPal o
                                     Mercado Pago)

Envio de correo no bloqueante en los dos sentidos: un fallo de SMTP
nunca debe tumbar una transaccion que ya se guardo, y un fallo del
registro de bitacora tampoco -- ambos quedan en el log del servidor
(logger.warning/exception) para que un fallo silencioso siga siendo
diagnosticable, sin convertir un problema de correo en un 500 para el
usuario que ya pago.
"""
import logging
from flask import current_app
from flask_mail import Message

from app.extensions import mail, db
from app.utils.email_remitentes import remitente
from app.utils.alcance import registrar_evento, usuario_propietario_de

logger = logging.getLogger(__name__)

_fmt = lambda n: f"${float(n):,.2f}"


def _enviar_correo(destinatarios, asunto, html):
    """Envio no bloqueante a 1 o varios destinatarios (deduplicados --
    el propietario puede coincidir con el comprador si el dueno se vende
    algo a si mismo desde el POS)."""
    vistos, limpios = set(), []
    for d in destinatarios:
        d = (d or "").strip()
        if d and "@" in d and d.lower() not in vistos:
            vistos.add(d.lower())
            limpios.append(d)
    if not limpios:
        return
    try:
        msg = Message(subject=asunto, recipients=limpios, html=html,
                       sender=remitente("MAIL_SENDER_FACTURACION"))
        with current_app.app_context():
            mail.send(msg)
    except Exception as exc:
        logger.warning("No se pudo enviar el recibo \'%s\' a %s: %s", asunto, limpios, exc)


def _owner_email(id_gimnasio):
    propietario = usuario_propietario_de(id_gimnasio)
    return propietario.email if propietario and propietario.email else None


def _plantilla_recibo(*, encabezado, numero, fecha_str, lineas_html, total_label,
                       total, destinatario_label=None, pie_extra=""):
    """Plantilla compartida por el recibo de venta y el de membresia --
    mismo estilo visual que _send_ticket_email ya usaba en ventas.py."""
    destinatario_html = (
        f"<p style=\'font-size:13px;color:#94a3b8;margin-bottom:12px\'>{destinatario_label}</p>"
        if destinatario_label else ""
    )
    return f"""
    <div style="background:#0f1117;color:#f1f5f9;font-family:Arial,sans-serif;max-width:480px;margin:0 auto;border-radius:12px;overflow:hidden">
      <div style="background:#6366f1;padding:20px 24px;text-align:center">
        <h1 style="margin:0;font-size:22px;letter-spacing:2px;color:#fff">GYM PRO</h1>
        <p style="margin:4px 0 0;font-size:12px;color:rgba(255,255,255,.8)">{encabezado}</p>
      </div>
      <div style="padding:20px 24px">
        <div style="text-align:center;margin-bottom:16px">
          <p style="margin:0;font-size:11px;color:#64748b"># {numero}</p>
          <p style="margin:4px 0 0;font-size:12px;color:#94a3b8">{fecha_str}</p>
        </div>
        {destinatario_html}
        <table width="100%" cellspacing="0" style="border-collapse:collapse;margin-bottom:16px">
          <tbody>{lineas_html}</tbody>
        </table>
        <div style="border-top:2px dashed #3d4166;padding-top:12px;display:flex;justify-content:space-between">
          <span style="font-size:16px;font-weight:700">{total_label}</span>
          <span style="font-size:20px;font-weight:900;color:#10b981">{_fmt(total)}</span>
        </div>
        {pie_extra}
        <p style="text-align:center;margin-top:20px;font-size:11px;color:#64748b">GYM PRO -- comprobante generado automaticamente</p>
      </div>
    </div>
    """


def enviar_recibo_venta(id_gimnasio, *, venta_num, fecha_str, items, total,
                         metodo_pago, nombre_miembro="", email_miembro=None,
                         referencia_pasarela=None):
    """
    Recibo de una venta del punto de venta (en persona o pagada en
    linea). Va al miembro (si se conoce su correo) Y al propietario del
    gimnasio -- antes de este modulo, ventas.py solo se lo mandaba al
    miembro.
    """
    filas = "".join(
        f"""<tr>
              <td style="padding:6px 10px;border-bottom:1px solid #2d3148">{it.get('nombre','')} x{it.get('qty',1)}</td>
              <td style="padding:6px 10px;border-bottom:1px solid #2d3148;text-align:right;color:#10b981;font-weight:700">{_fmt(float(it.get('precio',0))*int(it.get('qty',1)))}</td>
            </tr>"""
        for it in items
    )
    pie = ""
    if referencia_pasarela:
        pie = f"<p style=\'margin:12px 0 0;font-size:12px;color:#94a3b8\'>Referencia: {referencia_pasarela}</p>"

    html = _plantilla_recibo(
        encabezado="Punto de Venta -- Comprobante",
        numero=venta_num,
        fecha_str=fecha_str,
        lineas_html=filas,
        total_label="TOTAL",
        total=total,
        destinatario_label=(f"Cliente: <strong style=\'color:#f1f5f9\'>{nombre_miembro}</strong>" if nombre_miembro else None),
        pie_extra=pie,
    )
    asunto = f"[GYM PRO] Ticket de compra #{venta_num}"
    destinatarios = [email_miembro, _owner_email(id_gimnasio)]
    _enviar_correo(destinatarios, asunto, html)


def enviar_recibo_membresia(id_gimnasio, *, numero, fecha_str, membresia_nombre,
                             monto, metodo_pago, fecha_inicio_str, fecha_fin_str,
                             nombre_miembro="", email_miembro=None,
                             referencia_pasarela=None, tipo_pago="renovacion",
                             costo_inscripcion=None):
    """
    Recibo de una renovacion -- o de una inscripcion y primer pago -- de
    membresia (pagada en persona o en linea). Antes de este modulo, una
    renovacion no mandaba ningun correo -- ni al miembro ni al propietario.

    tipo_pago/costo_inscripcion son opcionales y retrocompatibles: los
    llamadores que no los mandan (pagos_online.py, user_membership.py)
    siguen viendo exactamente el recibo de renovacion de siempre. Cuando
    tipo_pago="inscripcion" (cobro manual en la sucursal, ver
    owner_gym/pagos.py::registrar_pago), el recibo desglosa el costo de
    inscripcion -- que el staff captura a mano, no es un precio fijo del
    catalogo -- del primer pago de la membresia en si.
    """
    es_inscripcion = tipo_pago == "inscripcion" and costo_inscripcion is not None
    if es_inscripcion:
        monto_membresia_calc = monto - costo_inscripcion
        filas = f"""<tr>
              <td style="padding:6px 10px;border-bottom:1px solid #2d3148">Costo de inscripción</td>
              <td style="padding:6px 10px;border-bottom:1px solid #2d3148;text-align:right;color:#10b981;font-weight:700">{_fmt(costo_inscripcion)}</td>
            </tr>
            <tr>
              <td style="padding:6px 10px;border-bottom:1px solid #2d3148">Primer pago — Membresía: {membresia_nombre}</td>
              <td style="padding:6px 10px;border-bottom:1px solid #2d3148;text-align:right;color:#10b981;font-weight:700">{_fmt(monto_membresia_calc)}</td>
            </tr>
            <tr>
              <td colspan="2" style="padding:6px 10px;font-size:11px;color:#94a3b8">Vigencia: {fecha_inicio_str} a {fecha_fin_str}</td>
            </tr>"""
        encabezado = "Inscripción y primer pago -- Comprobante"
        asunto     = f"[GYM PRO] Comprobante de inscripción #{numero}"
    else:
        filas = f"""<tr>
              <td style="padding:6px 10px;border-bottom:1px solid #2d3148">Membresia: {membresia_nombre}</td>
              <td style="padding:6px 10px;border-bottom:1px solid #2d3148;text-align:right;color:#10b981;font-weight:700">{_fmt(monto)}</td>
            </tr>
            <tr>
              <td colspan="2" style="padding:6px 10px;font-size:11px;color:#94a3b8">Vigencia: {fecha_inicio_str} a {fecha_fin_str}</td>
            </tr>"""
        encabezado = "Renovacion de membresia -- Comprobante"
        asunto     = f"[GYM PRO] Comprobante de renovacion de membresia #{numero}"

    pie = f"<p style=\'margin:12px 0 0;font-size:12px;color:#94a3b8\'>Metodo: <strong style=\'color:#f1f5f9\'>{metodo_pago}</strong></p>"
    if referencia_pasarela:
        pie += f"<p style=\'margin:4px 0 0;font-size:12px;color:#94a3b8\'>Referencia: {referencia_pasarela}</p>"

    html = _plantilla_recibo(
        encabezado=encabezado,
        numero=numero,
        fecha_str=fecha_str,
        lineas_html=filas,
        total_label="TOTAL PAGADO",
        total=monto,
        destinatario_label=(f"Miembro: <strong style=\'color:#f1f5f9\'>{nombre_miembro}</strong>" if nombre_miembro else None),
        pie_extra=pie,
    )
    destinatarios = [email_miembro, _owner_email(id_gimnasio)]
    _enviar_correo(destinatarios, asunto, html)


def enviar_recibo_lote_capacidad(id_gimnasio, *, numero, fecha_str, cantidad_miembros,
                                  ciclo, monto, metodo_pago, fecha_inicio_str, fecha_fin_str,
                                  referencia_pasarela=None):
    """
    Recibo de la compra de un lote de capacidad extra (ver
    routes/pagos_online.py::_aplicar_lote_capacidad y
    models/pg/complemento_capacidad.py). A diferencia de venta/membresia,
    aqui no hay "miembro comprador" -- quien paga es el propio gimnasio
    para ampliar su propio tope, asi que el recibo va solo al propietario.
    """
    filas = f"""<tr>
              <td style="padding:6px 10px;border-bottom:1px solid #2d3148">+{cantidad_miembros} miembros ({'anual' if ciclo == 'anual' else 'mensual'})</td>
              <td style="padding:6px 10px;border-bottom:1px solid #2d3148;text-align:right;color:#10b981;font-weight:700">{_fmt(monto)}</td>
            </tr>
            <tr>
              <td colspan="2" style="padding:6px 10px;font-size:11px;color:#94a3b8">Vigencia: {fecha_inicio_str} a {fecha_fin_str}</td>
            </tr>"""

    pie = f"<p style=\'margin:12px 0 0;font-size:12px;color:#94a3b8\'>Metodo: <strong style=\'color:#f1f5f9\'>{metodo_pago}</strong></p>"
    if referencia_pasarela:
        pie += f"<p style=\'margin:4px 0 0;font-size:12px;color:#94a3b8\'>Referencia: {referencia_pasarela}</p>"

    html = _plantilla_recibo(
        encabezado="Lote de capacidad -- Comprobante",
        numero=numero,
        fecha_str=fecha_str,
        lineas_html=filas,
        total_label="TOTAL PAGADO",
        total=monto,
        pie_extra=pie,
    )
    asunto = f"[GYM PRO] Comprobante de lote de capacidad #{numero}"
    _enviar_correo([_owner_email(id_gimnasio)], asunto, html)


def registrar_evento_transaccion(accion, *, id_usuario_actor, id_gimnasio, detalle):
    """
    Fila de bitacora_eventos para una transaccion de dinero ya aprobada
    (venta o renovacion). A diferencia de registrar_evento() -- que no
    hace commit porque se usa dentro de un endpoint que ya va a comitear
    su propia transaccion de SQLAlchemy -- esta SI comitea: ventas.py,
    pagos.py y pagos_online.py son endpoints Mongo-first que de otro
    modo nunca tocan la sesion de SQLAlchemy, asi que el evento se
    quedaria pendiente y se perderia al cerrar la request.
    """
    try:
        registrar_evento(
            id_usuario_actor, accion,
            id_gimnasio=id_gimnasio, detalle=detalle,
        )
        db.session.commit()
    except Exception:
        db.session.rollback()
        logger.exception("No se pudo registrar en bitacora la transaccion \'%s\' (gimnasio %s)", accion, id_gimnasio)
