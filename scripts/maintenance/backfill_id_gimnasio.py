"""
Backfill: agrega "id_gimnasio" a documentos historicos de `asistencias`
que no lo tienen.

Contexto (nov/2026): register_checkin() (recepcionista_routes.py) y
_registrar_visita() (verificacion_sucursal.py) nunca escribieron el campo
"id_gimnasio" -- solo "id_gimnasio_visita". Las metricas de negocio
(reportes_negocio.py, spark_mapreduce.py::_mapreduce_asistencia "que dias
hay mas gente", spark_platform.py) filtran por "id_gimnasio", asi que todo
check-in de staff anterior al fix de codigo queda invisible para ellas
aunque el documento exista. El fix de codigo ya corrige los inserts nuevos;
este script repara los documentos que ya estaban en la base.

Estrategia de inferencia, en orden:
  1. Si el documento ya tiene "id_gimnasio" -> no se toca.
  2. Si tiene "id_gimnasio_visita" -> se usa ese valor. Es correcto en los
     dos casos reales: check-in normal de recepcion (donde siempre
     coincide con la sucursal de origen del miembro) y verificacion entre
     sucursales (donde id_gimnasio_visita YA es la sucursal visitada,
     que es el valor que id_gimnasio deberia tener).
  3. Fallback, solo si no hay "id_gimnasio_visita" (no deberia pasar con
     los 3 call-sites conocidos, pero por si hay datos de otro origen):
     se usa miembros.id_gimnasio_pg (la sucursal de origen del miembro).
  4. Si tampoco se puede resolver el fallback -> se reporta el _id para
     revision manual, nunca se adivina con un valor arbitrario.

Uso:
  python backfill_id_gimnasio.py            # dry-run: solo reporta, no escribe
  python backfill_id_gimnasio.py --apply    # aplica los cambios

Variables de entorno esperadas (las mismas que usa la app -- correr este
script DENTRO del contenedor/entorno de la API para heredarlas):
  MONGO_URI, MONGO_DB
"""
import argparse
import os
import sys

from pymongo import MongoClient, UpdateOne


def get_db():
    uri = os.environ.get("MONGO_URI")
    dbname = os.environ.get("MONGO_DB")
    if not uri or not dbname:
        sys.exit("Faltan MONGO_URI / MONGO_DB en el entorno.")
    client = MongoClient(uri, serverSelectionTimeoutMS=5000)
    return client[dbname]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--apply", action="store_true",
        help="Escribe los cambios. Sin esta bandera es dry-run (solo reporta).",
    )
    parser.add_argument(
        "--batch-size", type=int, default=500,
        help="Tamaño de lote para bulk_write al aplicar (default 500).",
    )
    args = parser.parse_args()

    db = get_db()

    # Cache simple de miembro -> id_gimnasio_pg, para no repetir find_one
    # por cada asistencia del mismo miembro en el fallback del paso 3.
    cache_miembro = {}

    def gimnasio_de_miembro(id_miembro):
        if id_miembro not in cache_miembro:
            m = db.miembros.find_one({"_id": id_miembro}, {"id_gimnasio_pg": 1})
            cache_miembro[id_miembro] = m.get("id_gimnasio_pg") if m else None
        return cache_miembro[id_miembro]

    cursor = db.asistencias.find(
        {"id_gimnasio": {"$exists": False}},
        {"id_gimnasio_visita": 1, "id_miembro": 1},
    )

    total = 0
    por_visita = 0
    por_fallback_miembro = 0
    sin_dato = []
    operaciones = []

    for doc in cursor:
        total += 1
        id_gim = doc.get("id_gimnasio_visita")
        origen = "id_gimnasio_visita"

        if id_gim is None:
            id_gim = gimnasio_de_miembro(doc["id_miembro"])
            origen = "fallback:miembro.id_gimnasio_pg"

        if id_gim is None:
            sin_dato.append(doc["_id"])
            continue

        if origen == "id_gimnasio_visita":
            por_visita += 1
        else:
            por_fallback_miembro += 1

        operaciones.append(
            UpdateOne({"_id": doc["_id"]}, {"$set": {"id_gimnasio": int(id_gim)}})
        )

    print(f"Documentos sin id_gimnasio encontrados: {total}")
    print(f"  -> inferidos desde id_gimnasio_visita:          {por_visita}")
    print(f"  -> inferidos desde miembro.id_gimnasio_pg (fallback): {por_fallback_miembro}")
    print(f"  -> SIN DATO para inferir (requieren revision manual): {len(sin_dato)}")
    if sin_dato:
        muestra = [str(i) for i in sin_dato[:20]]
        print("     _id de ejemplo:", muestra, "..." if len(sin_dato) > 20 else "")

    if not args.apply:
        print("\n[DRY-RUN] No se escribio nada. Vuelve a correr con --apply para aplicar.")
        return

    aplicados = 0
    for i in range(0, len(operaciones), args.batch_size):
        lote = operaciones[i : i + args.batch_size]
        if lote:
            res = db.asistencias.bulk_write(lote, ordered=False)
            aplicados += res.modified_count

    print(f"\n[APLICADO] {aplicados} documentos actualizados con id_gimnasio.")


if __name__ == "__main__":
    main()
