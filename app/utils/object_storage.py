"""
utils/object_storage.py — Subida de imagenes/video a MinIO (S3-compatible).

Contexto (pedido del gym, nov/2026): hasta ahora las imagenes y video se
guardan como base64 directo en columnas de Mongo/Postgres (ver
Ejercicio.imagenes/Ejercicio.video, Miembro.foto_perfil, Producto.imagen,
etc.) -- funciona, pero infla las bases de datos y no escala. Se decidio
NO migrar lo que ya existe (alto riesgo, bajo beneficio inmediato): este
modulo es el almacenamiento para subidas NUEVAS; cada consumidor se migra
cuando se tocan sus endpoints de creacion/edicion.

Diseno defensivo: si MinIO no responde (apagado, aun arrancando, mal
configurado), `subir_base64` devuelve None en vez de levantar una excepcion.
El caller SIEMPRE debe tener un fallback (guardar el base64 inline como se
hacia antes) -- igual que el resto del proyecto trata los efectos
secundarios no esenciales (recibos por correo, notificaciones): un fallo
aqui nunca debe tumbar la operacion principal (crear/editar un ejercicio,
un producto, etc.).

Uso:
    from app.utils.object_storage import subir_base64

    url = subir_base64(data.get("video"), carpeta="ejercicios/video")
    ej.video = url or data.get("video")   # fallback a inline si MinIO falla
"""
import base64
import logging
import mimetypes
import os
import re
import uuid

logger = logging.getLogger(__name__)

MINIO_ENDPOINT       = os.getenv("MINIO_ENDPOINT", "http://localhost:9000")
MINIO_PUBLIC_URL     = os.getenv("MINIO_PUBLIC_URL", MINIO_ENDPOINT)
MINIO_ACCESS_KEY     = os.getenv("MINIO_ROOT_USER", "gympro_admin")
MINIO_SECRET_KEY     = os.getenv("MINIO_ROOT_PASSWORD", "gympro_dev_secret")
MINIO_BUCKET         = os.getenv("MINIO_BUCKET", "gympro-media")

_DATA_URL_RE = re.compile(r"^data:(?P<mime>[\w/.+-]+);base64,(?P<b64>.+)$", re.DOTALL)

_client = None
_bucket_listo = False


def _get_client():
    """Cliente boto3 (S3) contra MinIO -- se crea una sola vez por proceso."""
    global _client
    if _client is not None:
        return _client
    try:
        import boto3
        from botocore.config import Config as BotoConfig

        _client = boto3.client(
            "s3",
            endpoint_url=MINIO_ENDPOINT,
            aws_access_key_id=MINIO_ACCESS_KEY,
            aws_secret_access_key=MINIO_SECRET_KEY,
            config=BotoConfig(signature_version="s3v4"),
            region_name="us-east-1",  # MinIO lo ignora, pero boto3 lo exige
        )
    except Exception as e:
        logger.warning("[object_storage] No se pudo crear el cliente de MinIO: %s", e)
        _client = False  # marcador: ya se intento y fallo, no reintentar en este proceso
    return _client


def _asegurar_bucket(client):
    """Crea el bucket si no existe y lo deja con lectura publica (son
    imagenes/video servidos directo al navegador, no datos sensibles)."""
    global _bucket_listo
    if _bucket_listo:
        return True
    try:
        existentes = {b["Name"] for b in client.list_buckets().get("Buckets", [])}
        if MINIO_BUCKET not in existentes:
            client.create_bucket(Bucket=MINIO_BUCKET)
        client.put_bucket_policy(Bucket=MINIO_BUCKET, Policy=_politica_publica())
        _bucket_listo = True
        return True
    except Exception as e:
        logger.warning("[object_storage] No se pudo preparar el bucket '%s': %s", MINIO_BUCKET, e)
        return False


def _politica_publica():
    import json
    return json.dumps({
        "Version": "2012-10-17",
        "Statement": [{
            "Effect":    "Allow",
            "Principal": {"AWS": ["*"]},
            "Action":    ["s3:GetObject"],
            "Resource":  [f"arn:aws:s3:::{MINIO_BUCKET}/*"],
        }],
    })


def _parsear_data_url(valor):
    """Separa un data URL ('data:image/jpeg;base64,...') en (bytes, mimetype).
    Devuelve (None, None) si `valor` no tiene esa forma (ya es una URL, esta
    vacio, o es base64 "pelado" sin el prefijo)."""
    if not isinstance(valor, str):
        return None, None
    m = _DATA_URL_RE.match(valor.strip())
    if not m:
        return None, None
    try:
        return base64.b64decode(m.group("b64")), m.group("mime")
    except Exception:
        return None, None


def subir_base64(valor, carpeta, nombre=None):
    """
    Sube un data URL ('data:<mime>;base64,...') a MinIO y devuelve su URL
    publica. Devuelve None si `valor` esta vacio, ya es una URL (nada que
    subir), no es un data URL valido, o si MinIO no esta disponible --
    SIEMPRE revisar el resultado y hacer fallback al valor original.

    `carpeta`: prefijo dentro del bucket, ej. "ejercicios/video".
    `nombre`:  nombre base opcional (se le agrega un uuid igual para evitar
               colisiones); por defecto solo el uuid.
    """
    if not valor:
        return None

    data, mime = _parsear_data_url(valor)
    if data is None:
        return None  # no era un data URL (podria ya ser una URL de un upload anterior)

    client = _get_client()
    if not client or not _asegurar_bucket(client):
        return None

    ext = (mimetypes.guess_extension(mime or "") or "").lstrip(".") or "bin"
    base = re.sub(r"[^a-zA-Z0-9_-]", "_", (nombre or "media")).strip("_") or "media"
    key = f"{carpeta.strip('/')}/{base}-{uuid.uuid4().hex}.{ext}"

    try:
        client.put_object(Bucket=MINIO_BUCKET, Key=key, Body=data, ContentType=mime or "application/octet-stream")
        return f"{MINIO_PUBLIC_URL.rstrip('/')}/{MINIO_BUCKET}/{key}"
    except Exception as e:
        logger.warning("[object_storage] Fallo al subir '%s' a MinIO: %s", key, e)
        return None
