"""
routes/compartido/nutricion_externa.py — Búsqueda de alimentos por código de
barras (Open Food Facts) para los formularios de dietas/recetas.

Por qué Open Food Facts: es la única opción sin trámite para esto. No pide
cuenta, ni llave de API, ni tarjeta — es un GET público y gratuito a
world.openfoodfacts.org. Un proveedor como Google exige crear un proyecto
de Google Cloud, activar facturación con tarjeta real y restringir la
llave por aplicación (y en móvil, recompilar el binario para incluirla —
la misma friccion ya vivida con el modulo offline). Open Food Facts no
tiene nada de eso, así que tanto el entrenador (web/móvil) como el miembro
(web) pueden usarlo sin que el gimnasio tenga que dar de alta ninguna
cuenta ni tarjeta.

No usa @require_tenant: esta ruta no lee ni escribe ningún dato propio del
gimnasio, solo reenvía una búsqueda pública por código de barras — basta
con que quien la llame esté autenticado (entrenador o miembro), sin
importar su rol ni su sucursal.

Punto de reutilización pensado para el frontend: la app móvil ya trae la
cámara integrada para el QR de check-in (ver
mobile/app/(member)/checkin-qr.tsx) — el mismo componente CameraView de
expo-camera sirve aquí solo cambiando barcodeTypes de ['qr'] a los tipos
de código de barras de producto (ean13/ean8/upc_a/upc_e); no hace falta
ninguna librería ni permiso nuevo.
"""
from __future__ import annotations

import requests
from flask import Blueprint, jsonify
from flask_jwt_extended import jwt_required

nutricion_externa_bp = Blueprint("nutricion_externa", __name__, url_prefix="/api/nutricion")

_OFF_URL = "https://world.openfoodfacts.org/api/v2/product/{code}.json"
_OFF_FIELDS = "product_name,product_name_es,brands,nutriments,image_front_small_url,image_small_url"
_TIMEOUT = 6  # segundos — esto corre dentro de un request síncrono del usuario


def _num(nutriments: dict, *claves):
    """Primera clave presente en `nutriments` que sea numérica; None si ninguna lo es."""
    for clave in claves:
        valor = nutriments.get(clave)
        if valor is None:
            continue
        try:
            return round(float(valor), 1)
        except (TypeError, ValueError):
            continue
    return None


@nutricion_externa_bp.route("/barcode/<code>", methods=["GET"])
@jwt_required()
def buscar_por_codigo_barras(code):
    """
    Busca un producto por su código de barras en Open Food Facts y devuelve
    sus macros por 100 g, listos para precargar un alimento/receta.

    Siempre responde 200 cuando la búsqueda en sí se pudo hacer, con
    "encontrado": true/false — así el frontend distingue "no está en la
    base de datos" (caso esperado y frecuente: Open Food Facts es una base
    abierta mantenida por voluntarios, con huecos sobre todo en marcas
    locales/regionales) de un error real de red o del propio Open Food
    Facts, que sí van con un código de error distinto de 2xx.
    """
    limpio = "".join(ch for ch in (code or "") if ch.isdigit())
    if not limpio or len(limpio) < 6:
        return jsonify({"error": "Código de barras inválido"}), 400

    try:
        resp = requests.get(
            _OFF_URL.format(code=limpio),
            params={"fields": _OFF_FIELDS},
            timeout=_TIMEOUT,
            headers={"User-Agent": "GymPro/1.0 (nutricion; +https://gympro.lat)"},
        )
    except requests.exceptions.RequestException:
        return jsonify({
            "error": "No se pudo contactar a Open Food Facts. Revisa tu conexión o captura el alimento a mano.",
        }), 503

    if resp.status_code != 200:
        return jsonify({"error": "Open Food Facts no respondió correctamente"}), 502

    try:
        data = resp.json()
    except ValueError:
        return jsonify({"error": "Open Food Facts devolvió una respuesta inválida"}), 502

    producto = data.get("product") if data.get("status") == 1 else None
    if not producto:
        return jsonify({
            "encontrado": False,
            "mensaje": "Producto no encontrado en Open Food Facts. Captúralo a mano.",
        }), 200

    nombre = (producto.get("product_name_es") or producto.get("product_name") or "").strip()
    if not nombre:
        return jsonify({
            "encontrado": False,
            "mensaje": "El producto existe en Open Food Facts pero no tiene nombre registrado.",
        }), 200

    marca = (producto.get("brands") or "").split(",")[0].strip()
    nutriments = producto.get("nutriments") or {}

    return jsonify({
        "encontrado":      True,
        "codigo_barras":   limpio,
        "nombre":          f"{nombre} ({marca})" if marca else nombre,
        "base":            "100 g",
        "calorias":        _num(nutriments, "energy-kcal_100g", "energy-kcal"),
        "proteinas_g":     _num(nutriments, "proteins_100g"),
        "carbohidratos_g": _num(nutriments, "carbohydrates_100g"),
        "grasas_g":        _num(nutriments, "fat_100g"),
        "imagen":          producto.get("image_front_small_url") or producto.get("image_small_url"),
        "fuente":          "openfoodfacts",
    }), 200
