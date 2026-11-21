"""
Baja definitiva de gimnasios (tenants) y de TODOS sus datos -- Postgres + Mongo.

Pensado para limpiar gimnasios de prueba/demo en produccion. Por defecto es
DRY-RUN: ejecuta los DELETE de Postgres dentro de una transaccion y la revierte,
y en Mongo solo cuenta. Nada se escribe sin --apply.

Que borra (por cada id indicado):
  Postgres
    - usuarios con usuarios.id_gimnasio = id   (la FK es SET NULL: sin este paso
      quedarian huerfanos con email ocupado). Nunca borra superadmin.
    - bitacora_eventos del gimnasio            (FK SET NULL: quedarian huerfanas)
    - gimnasios                                (el resto -- suscripciones, facturas,
      pasarelas, transacciones, tipos de membresia/clase, ejercicios, testimonios,
      imagenes, asignaciones... -- cae por ON DELETE CASCADE)
    - organizaciones que se queden sin ningun gimnasio (id_organizacion es RESTRICT)
  Mongo (todas las colecciones, descubiertas en vivo)
    - documentos con id_gimnasio / id_gimnasio_pg en los ids
    - hijos sin campo de tenant: rutina_dias, rutina_ejercicios, detalle_venta,
      y cualquier documento ligado a un miembro / usuario borrado
      (id_miembro, id_usuario_pg, id_entrenador_pg, id_miembro_pg, password_resets.email)

Uso (dentro del contenedor api; lee POSTGRES_URI y MONGO_URI del entorno):
  python borrar_gimnasios.py --listar
  python borrar_gimnasios.py --ids 3,5,7            # dry-run
  python borrar_gimnasios.py --ids 3,5,7 --apply    # borra de verdad

Seguridad: se niega a borrar gimnasios cuyo nombre contenga --proteger
(default "vitazone"), ids inexistentes, o usuarios con rol superadmin.
"""
import argparse
import os
import sys

import psycopg2
from bson import ObjectId
from pymongo import MongoClient

TENANT_FIELDS = ("id_gimnasio", "id_gimnasio_pg")
USER_PG_FIELDS = ("id_usuario_pg", "id_entrenador_pg", "id_miembro_pg")


def pg_connect():
    uri = os.environ.get("POSTGRES_URI") or os.environ.get("DATABASE_URL")
    if not uri:
        sys.exit("Falta POSTGRES_URI en el entorno (corre esto DENTRO del contenedor api).")
    return psycopg2.connect(uri.replace("postgresql+psycopg2://", "postgresql://"))


def mongo_connect():
    uri = os.environ.get("MONGO_URI")
    if not uri:
        sys.exit("Falta MONGO_URI en el entorno.")
    return MongoClient(uri, serverSelectionTimeoutMS=5000).get_default_database()


def variantes(valores):
    """Un id puede estar guardado como ObjectId o como str: se buscan ambos."""
    out = []
    for v in valores:
        out.append(v)
        out.append(str(v))
        if isinstance(v, str) and len(v) == 24:
            try:
                out.append(ObjectId(v))
            except Exception:
                pass
    return out


def listar(cur, mdb):
    cur.execute("""
        SELECT g.id, g.nombre, g.plan::text, g.activo, g.id_organizacion,
               (SELECT count(*) FROM usuarios u WHERE u.id_gimnasio = g.id),
               (SELECT count(*) FROM gimnasios h WHERE h.id_organizacion = g.id_organizacion)
        FROM gimnasios g ORDER BY g.id
    """)
    print(f"{'id':>4}  {'nombre':<32} {'plan':<10} {'act':<5} {'org':>4} {'usuarios':>8} {'gyms_en_org':>11} {'miembros_mongo':>14}")
    for gid, nombre, plan, activo, org, nu, ng in cur.fetchall():
        nm = mdb.miembros.count_documents({"id_gimnasio_pg": {"$in": variantes([gid])}})
        print(f"{gid:>4}  {nombre:<32} {plan:<10} {str(activo):<5} {org:>4} {nu:>8} {ng:>11} {nm:>14}")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--listar", action="store_true", help="Lista los gimnasios y sale.")
    ap.add_argument("--ids", help="ids de gimnasios a borrar, separados por coma.")
    ap.add_argument("--apply", action="store_true", help="Aplica el borrado. Sin esto es dry-run.")
    ap.add_argument("--proteger", default="vitazone",
                    help="Subcadena (case-insensitive) de nombres que NO se pueden borrar.")
    args = ap.parse_args()

    pg = pg_connect()
    pg.autocommit = False
    cur = pg.cursor()
    mdb = mongo_connect()

    if args.listar or not args.ids:
        listar(cur, mdb)
        return

    ids = sorted({int(x) for x in args.ids.split(",") if x.strip()})
    if not ids:
        sys.exit("--ids vacio.")
    ids_mongo = variantes(ids)

    # ── Validaciones ────────────────────────────────────────────────────────
    cur.execute("SELECT id, nombre, id_organizacion FROM gimnasios WHERE id = ANY(%s) ORDER BY id", (ids,))
    gyms = cur.fetchall()
    faltan = set(ids) - {g[0] for g in gyms}
    if faltan:
        sys.exit(f"ABORTA: ids que no existen en gimnasios: {sorted(faltan)}")
    prot = (args.proteger or "").lower()
    protegidos = [g for g in gyms if prot and prot in g[1].lower()]
    if protegidos:
        sys.exit(f"ABORTA: coinciden con --proteger '{args.proteger}': {protegidos}")

    cur.execute("""
        SELECT u.id, u.email, r.nombre FROM usuarios u JOIN roles r ON r.id = u.id_rol
        WHERE u.id_gimnasio = ANY(%s) ORDER BY u.id
    """, (ids,))
    usuarios = cur.fetchall()
    if any(r.lower() == "superadmin" for _, _, r in usuarios):
        sys.exit("ABORTA: hay usuarios superadmin ligados a esos gimnasios.")
    user_ids = [u[0] for u in usuarios]
    emails = [u[1] for u in usuarios]

    org_ids = sorted({g[2] for g in gyms})
    cur.execute("""
        SELECT o.id, o.nombre FROM organizaciones o
        WHERE o.id = ANY(%s)
          AND NOT EXISTS (SELECT 1 FROM gimnasios g WHERE g.id_organizacion = o.id AND g.id <> ALL(%s))
        ORDER BY o.id
    """, (org_ids, ids))
    orgs_borrar = cur.fetchall()
    orgs_conservar = sorted(set(org_ids) - {o[0] for o in orgs_borrar})

    # Estado de los gimnasios que se conservan, para comprobar que no se tocan.
    cur.execute("SELECT id, (SELECT count(*) FROM usuarios u WHERE u.id_gimnasio = g.id) "
                "FROM gimnasios g WHERE g.id <> ALL(%s) ORDER BY id", (ids,))
    antes_conservados = cur.fetchall()

    print("=" * 78)
    print("MODO:", "APPLY (borra de verdad)" if args.apply else "DRY-RUN (no se escribe nada)")
    print("=" * 78)
    print("Gimnasios a borrar:")
    for g in gyms:
        print(f"  - id={g[0]:<4} {g[1]}  (org {g[2]})")
    print(f"Usuarios PG a borrar: {len(usuarios)}")
    por_rol = {}
    for _, _, r in usuarios:
        por_rol[r] = por_rol.get(r, 0) + 1
    print("  por rol:", por_rol)
    print("Organizaciones a borrar:", [f"{o[0]}:{o[1]}" for o in orgs_borrar] or "ninguna")
    if orgs_conservar:
        print("Organizaciones que SE CONSERVAN (tienen otros gimnasios):", orgs_conservar)

    cur.execute("SELECT count(*) FROM asignacion_usuario WHERE id_gimnasio = ANY(%s) "
                "AND id_usuario <> ALL(%s)", (ids, user_ids or [0]))
    cruzadas = cur.fetchone()[0]
    if cruzadas:
        print(f"AVISO: {cruzadas} asignaciones de usuarios que NO se borran apuntan a estos gimnasios "
              "(se eliminan solo esas asignaciones, por CASCADE).")

    # Filas por tabla con id_gimnasio (informativo; el DELETE las arrastra por CASCADE)
    cur.execute("""SELECT table_name FROM information_schema.columns
                   WHERE table_schema='public' AND column_name='id_gimnasio' ORDER BY 1""")
    print("\nPostgres -- filas con id_gimnasio en los ids:")
    for (t,) in cur.fetchall():
        cur.execute(f'SELECT count(*) FROM "{t}" WHERE id_gimnasio = ANY(%s)', (ids,))
        n = cur.fetchone()[0]
        if n:
            print(f"  {t:<36} {n}")

    # ── Mongo: descubrimiento ───────────────────────────────────────────────
    def filtro_tenant():
        return [{f: {"$in": ids_mongo}} for f in TENANT_FIELDS]

    def ids_de(col, filtro):
        return [d["_id"] for d in mdb[col].find(filtro, {"_id": 1})]

    cols = sorted(c for c in mdb.list_collection_names() if not c.startswith("system."))
    miembro_ids = ids_de("miembros", {"$or": filtro_tenant()}) if "miembros" in cols else []
    rutina_ids = ids_de("rutinas", {"$or": filtro_tenant()}) if "rutinas" in cols else []
    venta_ids = ids_de("ventas", {"$or": filtro_tenant()}) if "ventas" in cols else []
    dia_ids = (ids_de("rutina_dias", {"$or": filtro_tenant() + [{"id_rutina": {"$in": variantes(rutina_ids)}}]})
               if "rutina_dias" in cols else [])

    def filtro_total(col):
        f = filtro_tenant()
        if miembro_ids:
            f.append({"id_miembro": {"$in": variantes(miembro_ids)}})
        if rutina_ids:
            f.append({"id_rutina": {"$in": variantes(rutina_ids)}})
        if dia_ids:
            f.append({"id_rutina_dia": {"$in": variantes(dia_ids)}})
        if venta_ids:
            f.append({"id_venta": {"$in": variantes(venta_ids)}})
        if user_ids:
            for fld in USER_PG_FIELDS:
                f.append({fld: {"$in": user_ids}})
        if emails and col == "password_resets":
            f.append({"email": {"$in": emails}})
        return {"$or": f}

    print("\nMongo -- documentos a borrar por coleccion:")
    plan_mongo = {}
    for c in cols:
        n = mdb[c].count_documents(filtro_total(c))
        if n:
            plan_mongo[c] = n
            print(f"  {c:<36} {n}")
    if not plan_mongo:
        print("  (ninguno)")

    # ── Ejecucion ───────────────────────────────────────────────────────────
    try:
        cur.execute("DELETE FROM bitacora_eventos WHERE id_gimnasio = ANY(%s)", (ids,))
        print(f"\nPG bitacora_eventos borradas:  {cur.rowcount}")
        if user_ids:
            cur.execute("DELETE FROM usuarios WHERE id = ANY(%s)", (user_ids,))
            print(f"PG usuarios borrados:          {cur.rowcount}")
        cur.execute("DELETE FROM gimnasios WHERE id = ANY(%s)", (ids,))
        print(f"PG gimnasios borrados:         {cur.rowcount}")
        if cur.rowcount != len(ids):
            raise RuntimeError("rowcount de gimnasios distinto al esperado")
        if orgs_borrar:
            cur.execute("DELETE FROM organizaciones WHERE id = ANY(%s)", ([o[0] for o in orgs_borrar],))
            print(f"PG organizaciones borradas:    {cur.rowcount}")

        # Los gimnasios conservados deben quedar exactamente igual.
        cur.execute("SELECT id, (SELECT count(*) FROM usuarios u WHERE u.id_gimnasio = g.id) "
                    "FROM gimnasios g ORDER BY id")
        despues = cur.fetchall()
        if despues != antes_conservados:
            raise RuntimeError(f"los gimnasios conservados cambiaron: {antes_conservados} -> {despues}")
        print(f"PG gimnasios conservados intactos: {[d[0] for d in despues]}")

        if args.apply:
            for c, n in plan_mongo.items():
                r = mdb[c].delete_many(filtro_total(c))
                print(f"MONGO {c:<30} borrados {r.deleted_count} (esperados {n})")
            pg.commit()
            print("\nOK -- COMMIT hecho en Postgres.")
        else:
            pg.rollback()
            print("\nDRY-RUN -- ROLLBACK hecho en Postgres; Mongo sin tocar. Repite con --apply para borrar.")
    except Exception as e:
        pg.rollback()
        print(f"\nERROR -> ROLLBACK en Postgres: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
