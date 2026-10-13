"""
utils/busqueda.py — Normalización de comparaciones de texto (SCRUM-105).

Decisión de producto confirmada con el usuario (dueño de producto): la
información capturada NUNCA se modifica para guardarse -- se guarda
exactamente como la escribió quien la capturó. Lo único que se vuelve
insensible a mayúsculas y acentos son las COMPARACIONES del lado del
servidor (hoy: búsquedas por nombre). Este módulo es el único punto
donde se implementa esa normalización -- nunca repetirla a mano
componente por componente.

Dos formas, porque el proyecto vive en dos motores de base de datos:

  normalizar_columna(...)  — expresión SQL para consultas de SQLAlchemy /
                              PostgreSQL. Requiere la extensión `unaccent`
                              (migración 018_extension_unaccent.py).
  normalizar_texto(...)    — equivalente en Python puro, para comparar
                              valores ya en memoria o como paso previo a
                              patron_insensible().
  patron_insensible(...)   — patrón de regex insensible a acentos (y, con
                              re.IGNORECASE / {"$options": "i"}, también a
                              mayúsculas) para búsquedas en MongoDB, que no
                              tiene una extensión equivalente a unaccent.

Alcance de campos (criterio del ticket): nombres de personas, nombre de
gimnasio, direcciones, descripciones cortas -- en la práctica, los campos
"nombre" que ya se usaban en una búsqueda existente. Explícitamente FUERA
de alcance: email, password, URLs e identificadores técnicos -- esos
siguen comparándose tal cual en cada call site, nunca por aquí.
"""
import re
import unicodedata
from sqlalchemy import func

# Letras del español con variante acentuada -- alcanza para nombres,
# direcciones y descripciones cortas; no pretende ser Unicode genérico.
_VARIANTES_ACENTO = {
    "a": "aá", "e": "eé", "i": "ií", "o": "oó", "u": "uúü", "n": "nñ",
}


def normalizar_texto(valor: str) -> str:
    """Minúsculas + sin acentos, en Python puro (valores en memoria, o
    como paso previo a patron_insensible)."""
    if not valor:
        return valor or ""
    descompuesto = unicodedata.normalize("NFKD", valor)
    sin_acentos = "".join(c for c in descompuesto if not unicodedata.combining(c))
    return sin_acentos.lower()


def normalizar_columna(columna_o_valor):
    """
    Envuelve una columna (o un valor literal) de SQLAlchemy para
    compararla sin distinguir mayúsculas/minúsculas ni acentos.

    Uso:
        from app.utils.busqueda import normalizar_columna as norm

        Usuario.query.filter(norm(Usuario.nombre).ilike(norm(f"%{search}%")))
    """
    return func.lower(func.unaccent(columna_o_valor))


def patron_insensible(texto: str) -> str:
    """
    Construye un patrón de regex insensible a acentos a partir de un
    texto de búsqueda (con o sin acentos -- se normaliza primero, así que
    funciona igual si el usuario tecleó "jose" o "josé").
    Combinar con re.IGNORECASE (Python) o {"$options": "i"} (MongoDB)
    para además ignorar mayúsculas/minúsculas.

    Uso (MongoDB):
        from app.utils.busqueda import patron_insensible

        filtro["nombre"] = {"$regex": patron_insensible(search), "$options": "i"}
    """
    texto_normalizado = normalizar_texto(texto)
    partes = []
    for c in texto_normalizado:
        if c in _VARIANTES_ACENTO:
            partes.append(f"[{_VARIANTES_ACENTO[c]}]")
        else:
            partes.append(re.escape(c))
    return "".join(partes)
