"""
utils/antiabuso.py — Frena el alta repetida de gimnasios para no pagar.

El problema concreto: el plan gratuito da capacidad real, así que a alguien le
sale a cuenta abrir varios gimnasios "gratis" en vez de contratar uno de pago,
o dar de baja su cuenta y volverla a crear para estrenar otra vez el periodo
de prueba.

QUÉ NO SE PUEDE HACER, aunque se pida: bloquear por dirección MAC. La MAC es
una dirección de capa de enlace y no sale de la red local del usuario -- al
servidor nunca le llega, ningún encabezado HTTP la trae y no hay forma de
pedirla desde el navegador. Cualquier "bloqueo por MAC" en una aplicación web
es, en realidad, otra cosa con ese nombre. Lo que sí llega: la IP de salida,
una huella del navegador, y los datos de identidad que la persona escribe.

CRITERIO (decidido con el dueño del producto): la IDENTIDAD bloquea; la IP
NO. Una IP no identifica a una persona: dos gimnasios de la misma plaza
comercial, una cadena con una sola salida a internet o cualquiera con datos
móviles (CGNAT) comparten IP con desconocidos. Bloquear por IP cambia un
abusista por varios clientes legítimos rechazados, que es un peor negocio.
La IP se registra para poder investigar, y su ritmo ya lo limita el rate
limiting del endpoint (3 por hora, 10 por día).

Señales, de más a menos confiable:
  1. Correo NORMALIZADO -- 'j.uan+gym2@gmail.com' y 'juan@gmail.com' son la
     misma bandeja. La comparación exacta que ya existía no lo veía.
  2. Teléfono normalizado a 10 dígitos.
  3. Huella del navegador -- débil por sí sola (se borra reinstalando), pero
     sirve para ver altas en serie desde el mismo equipo con correos
     distintos.
  4. IP -- solo se guarda.

TODO BLOQUEO ES REVISABLE. Un falso positivo aquí es una venta perdida sin
que nadie se entere, así que cada intento queda registrado y el superadmin
puede ver los bloqueos y autorizar una identidad a mano. Un control sin
palanca de excepción no es un control, es una trampa.
"""
import hashlib
import re
from datetime import datetime, timedelta, timezone

from flask import request

from app.constants.antiabuso import (
    ALTAS_MAX_POR_HUELLA, ALTAS_VENTANA_DIAS, CABECERA_HUELLA,
)
from app.extensions import db
from app.models.pg.intento_alta_gimnasio import IntentoAltaGimnasio

# Proveedores que ignoran los puntos del usuario y cortan en '+'. Para el
# resto no se tocan los puntos: en muchos dominios corporativos
# 'juan.perez@' y 'juanperez@' SON buzones distintos y unificarlos
# rechazaría a dos personas reales de la misma empresa.
_DOMINIOS_SIN_PUNTOS = {"gmail.com", "googlemail.com"}

MOTIVO_YA_TIENE_GIMNASIO = "ya_tiene_gimnasio"
MOTIVO_CUENTA_DADA_DE_BAJA = "cuenta_dada_de_baja"
MOTIVO_ALTAS_EN_SERIE = "altas_en_serie"


def normalizar_correo(correo: str) -> str:
    """
    Forma canónica de un correo, para comparar bandejas y no cadenas.

    Siempre: recorta y pasa a minúsculas. En Gmail además quita los puntos de
    la parte local y todo lo que vaya después de un '+', porque ahí sí son
    alias de un mismo buzón.
    """
    correo = (correo or "").strip().lower()
    if "@" not in correo:
        return correo
    local, _, dominio = correo.partition("@")
    local = local.split("+", 1)[0]
    if dominio in _DOMINIOS_SIN_PUNTOS:
        local = local.replace(".", "")
    return f"{local}@{dominio}" if local else correo


def normalizar_telefono(telefono: str) -> str | None:
    """
    Últimos 10 dígitos, que es el número nacional en México. Así '+52 722 123
    4567', '7221234567' y '044 722 123 4567' coinciden. None si no quedan al
    menos 10 dígitos: un número incompleto no sirve para comparar a nadie.
    """
    digitos = re.sub(r"\D", "", telefono or "")
    return digitos[-10:] if len(digitos) >= 10 else None


def huella_de_peticion() -> str | None:
    """
    Huella del navegador que manda el cliente, saneada.

    No es un dato de confianza: la pone el cliente y puede omitirla o
    inventarla. Por eso NUNCA se usa sola para bloquear a la primera -- solo
    cuenta altas repetidas desde el mismo equipo. Se limita a hex para que no
    entre basura a la base ni a los registros.
    """
    valor = (request.headers.get(CABECERA_HUELLA) or "").strip().lower()
    if not re.fullmatch(r"[0-9a-f]{16,64}", valor or ""):
        return None
    return valor


def _ip_de_peticion() -> str | None:
    # ProxyFix ya resuelve la IP real detrás de nginx (ver utils/rate_limit.py).
    return (request.remote_addr or None)


def _hay_excepcion(correo_norm, telefono_norm) -> bool:
    consulta = IntentoAltaGimnasio.query.filter_by(resultado="excepcion")
    condiciones = [IntentoAltaGimnasio.correo_normalizado == correo_norm]
    if telefono_norm:
        condiciones.append(IntentoAltaGimnasio.telefono_normalizado == telefono_norm)
    return db.session.query(
        consulta.filter(db.or_(*condiciones)).exists()
    ).scalar()


def _altas_previas(correo_norm, telefono_norm):
    condiciones = [IntentoAltaGimnasio.correo_normalizado == correo_norm]
    if telefono_norm:
        condiciones.append(IntentoAltaGimnasio.telefono_normalizado == telefono_norm)
    return (IntentoAltaGimnasio.query
            .filter(IntentoAltaGimnasio.resultado == "creado")
            .filter(db.or_(*condiciones))
            .order_by(IntentoAltaGimnasio.created_at.desc())
            .all())


def _organizacion_viva(id_organizacion):
    """
    (existe, en_baja) de la organización de un alta previa.

    Si ya no existe es porque se purgó tras una baja: justo el caso de "crea
    y elimina la cuenta para volver a empezar".
    """
    if id_organizacion is None:
        return False, False
    from app.models.pg.gimnasio import Gimnasio
    from app.models.pg.organizacion import Organizacion

    if Organizacion.query.get(id_organizacion) is None:
        return False, False
    en_baja = db.session.query(
        Gimnasio.query.filter(
            Gimnasio.id_organizacion == id_organizacion,
            Gimnasio.baja_solicitada_en.isnot(None),
        ).exists()
    ).scalar()
    return True, bool(en_baja)


def evaluar_alta(correo: str, telefono: str | None = None):
    """
    ¿Se puede dar de alta un gimnasio con estos datos?

    Devuelve (permitido, motivo, mensaje, contexto). `contexto` trae las
    señales ya normalizadas para registrarlas sin volver a calcularlas.

    Los mensajes NO acusan a nadie de abusar: quien lee esto puede ser una
    persona honesta en un caso que no previmos, y la salida siempre es
    escribirle a soporte.
    """
    correo_norm = normalizar_correo(correo)
    telefono_norm = normalizar_telefono(telefono)
    contexto = {
        "correo_normalizado": correo_norm,
        "telefono_normalizado": telefono_norm,
        "huella": huella_de_peticion(),
        "ip": _ip_de_peticion(),
    }

    if _hay_excepcion(correo_norm, telefono_norm):
        return True, None, None, contexto

    # ── 1. Identidad que ya abrió un gimnasio ────────────────────────────────
    for previo in _altas_previas(correo_norm, telefono_norm):
        existe, en_baja = _organizacion_viva(previo.id_organizacion)
        if not existe or en_baja:
            return False, MOTIVO_CUENTA_DADA_DE_BAJA, (
                "Con estos datos ya se registró un gimnasio que después se dio de baja. "
                "Para volver a abrir una cuenta escríbenos a soporte y lo resolvemos contigo."
            ), contexto
        return False, MOTIVO_YA_TIENE_GIMNASIO, (
            "Ya hay un gimnasio registrado con estos datos. Si quieres administrar otra "
            "sede, agrégala como sucursal desde tu cuenta en vez de abrir una nueva; si "
            "se trata de otro negocio, escríbenos a soporte."
        ), contexto

    # ── 2. Altas en serie desde el mismo equipo ──────────────────────────────
    # Deliberadamente NO es a la primera: una huella se comparte entre personas
    # que usan la misma computadora (la oficina de una cadena, un cibercafé).
    if contexto["huella"]:
        desde = datetime.now(timezone.utc) - timedelta(days=ALTAS_VENTANA_DIAS)
        altas = (IntentoAltaGimnasio.query
                 .filter(IntentoAltaGimnasio.resultado == "creado",
                         IntentoAltaGimnasio.huella == contexto["huella"],
                         IntentoAltaGimnasio.created_at >= desde)
                 .count())
        if altas >= ALTAS_MAX_POR_HUELLA:
            return False, MOTIVO_ALTAS_EN_SERIE, (
                "Desde este equipo ya se registraron varios gimnasios en los últimos días. "
                "Si de verdad administras varios, escríbenos a soporte y te ayudamos a "
                "configurarlos en una sola cuenta."
            ), contexto

    return True, None, None, contexto


def registrar_intento(contexto, *, resultado, motivo=None, id_organizacion=None):
    """
    Deja constancia del intento. Se llama SIEMPRE, salga bien o mal: un
    bloqueo sin registro no se puede revisar ni corregir, y un alta sin
    registro no sirve para detectar la siguiente.

    No hace commit -- lo hace quien llama, dentro de su propia transacción,
    para que el registro y el alta se guarden o fallen juntos.
    """
    db.session.add(IntentoAltaGimnasio(
        correo_normalizado=contexto.get("correo_normalizado"),
        telefono_normalizado=contexto.get("telefono_normalizado"),
        huella=contexto.get("huella"),
        ip=contexto.get("ip"),
        resultado=resultado,
        motivo=motivo,
        id_organizacion=id_organizacion,
    ))


def huella_estable(*partes) -> str:
    """
    Huella a partir de valores estables. La usa el cliente web; aquí queda
    para pruebas y por si alguna vez hace falta calcularla del lado del
    servidor.
    """
    crudo = "|".join(str(p or "") for p in partes)
    return hashlib.sha256(crudo.encode("utf-8")).hexdigest()
