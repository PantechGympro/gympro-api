"""
spark_negocio.py — Análisis de negocio adicionales para el gimnasio.

Tres análisis nuevos, todos con alcance por gimnasio (id_gimnasio del token):

  1. Mapa de calor de asistencia  GET /api/analytics/heatmap-asistencia
     Cruza día de la semana x franja horaria para detectar las horas concurridas.

  2. Clientes por valor (RFM)      GET /api/analytics/rfm
     Clasifica a cada miembro por qué tan reciente fue su última visita,
     cuántas veces viene y cuánto ha pagado. Sin jerga: segmentos claros.

  3. Fuerza estimada              GET /api/analytics/fuerza
     A partir de la bitácora de entrenamientos estima el levantamiento máximo
     (formula de Epley: peso x (1 + reps/30)) por ejercicio.

Motor: pymongo + cálculo en proceso (sin JVM, sin internet). Caché por gimnasio.
Lenguaje de salida pensado para el usuario común (dueño del gimnasio).
"""
from flask import Blueprint, jsonify
from flask_jwt_extended import jwt_required, get_jwt
from datetime import datetime, timezone
from statistics import median

from app.routes.ia.spark_config import cache_get, cache_set, get_mongo_db

spark_negocio_bp = Blueprint("spark_negocio", __name__)

DIAS = ["Lunes", "Martes", "Miercoles", "Jueves", "Viernes", "Sabado", "Domingo"]
FRANJAS = [
    (0, 9,  "Antes de 9:00"),
    (9, 12, "9:00 - 12:00"),
    (12, 15, "12:00 - 15:00"),
    (15, 18, "15:00 - 18:00"),
    (18, 21, "18:00 - 21:00"),
    (21, 24, "Despues de 21:00"),
]


# ── Helpers ────────────────────────────────────────────────────────────────────

def _gym_member_ids(db, gym_id):
    """Devuelve (lista de _id ObjectId, mapa id->nombre) de los miembros del gimnasio."""
    query = {}
    if gym_id is not None:
        query["id_gimnasio_pg"] = int(gym_id)
    miembros = list(db.miembros.find(query, {"_id": 1, "nombre": 1}))
    oids = [m["_id"] for m in miembros]
    nombres = {str(m["_id"]): (m.get("nombre") or "Miembro") for m in miembros}
    return oids, nombres


def _parse_fecha(val):
    if val is None:
        return None
    if isinstance(val, datetime):
        return val.replace(tzinfo=None)
    if isinstance(val, str):
        for fmt in ("%Y-%m-%dT%H:%M:%S.%f", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
            try:
                return datetime.strptime(val, fmt)
            except ValueError:
                continue
        try:
            return datetime.strptime(val[:10], "%Y-%m-%d")
        except ValueError:
            return None
    return None


def _hora_de(asis):
    """Obtiene la hora (0-23) de una asistencia: usa hora_entrada o la fecha."""
    h = asis.get("hora_entrada")
    if isinstance(h, str) and ":" in h:
        try:
            return int(h.split(":")[0])
        except ValueError:
            pass
    dt = _parse_fecha(asis.get("fecha"))
    if dt and (dt.hour or dt.minute):
        return dt.hour
    return None


def _franja_idx(hora):
    for i, (a, b, _) in enumerate(FRANJAS):
        if a <= hora < b:
            return i
    return len(FRANJAS) - 1


# ── 1. Mapa de calor de asistencia ──────────────────────────────────────────────

def _heatmap_asistencia(gym_id):
    db = get_mongo_db()
    member_oids, _ = _gym_member_ids(db, gym_id)
    if not member_oids:
        return {"error": "Aun no hay miembros registrados en este gimnasio."}

    asistencias = list(db.asistencias.find(
        {"id_miembro": {"$in": member_oids}},
        {"fecha": 1, "hora_entrada": 1},
    ))
    if not asistencias:
        return {"error": "Aun no hay visitas registradas para analizar."}

    counts = [[0] * len(FRANJAS) for _ in range(7)]
    por_dia = [0] * 7
    total = 0
    for a in asistencias:
        dt = _parse_fecha(a.get("fecha"))
        if dt is None:
            continue
        hora = _hora_de(a)
        if hora is None:
            continue
        d = dt.weekday()
        f = _franja_idx(hora)
        counts[d][f] += 1
        por_dia[d] += 1
        total += 1

    if total == 0:
        return {"error": "Las visitas registradas no tienen fecha valida."}

    celdas, max_val, pico = [], 0, None
    for d in range(7):
        for f in range(len(FRANJAS)):
            v = counts[d][f]
            celdas.append({"dia_idx": d, "franja_idx": f, "total": v})
            if v > max_val:
                max_val = v
                pico = {"dia": DIAS[d], "franja": FRANJAS[f][2], "total": v}

    dia_top_idx = max(range(7), key=lambda d: por_dia[d])
    return {
        "dias":     DIAS,
        "franjas":  [f[2] for f in FRANJAS],
        "celdas":   celdas,
        "max":      max_val,
        "pico":     pico,
        "total_visitas": total,
        "por_dia":  [{"dia": DIAS[d], "total": por_dia[d]} for d in range(7)],
        "dia_mas_concurrido": DIAS[dia_top_idx],
        "ejecutado_en": datetime.now().isoformat(),
    }


# ── 2. Clientes por valor (RFM) ─────────────────────────────────────────────────

SEGMENTOS_META = {
    "Campeones":    {"color": "#4cd964", "descripcion": "Vienen seguido, al dia y con buen gasto. Cuidalos y premialos."},
    "Fieles":       {"color": "#38bdf8", "descripcion": "Asisten con regularidad. Buen momento para ofrecerles algo extra."},
    "Prometedores": {"color": "#a78bfa", "descripcion": "Activos pero aun con poca frecuencia. Anima su habito."},
    "En riesgo":    {"color": "#ffbd2e", "descripcion": "Llevan semanas sin venir. Un mensaje a tiempo puede recuperarlos."},
    "Dormidos":     {"color": "#ff6b9d", "descripcion": "Hace mas de mes y medio que no vienen. Reactivacion urgente."},
}


def _rfm(gym_id):
    db = get_mongo_db()
    member_oids, nombres = _gym_member_ids(db, gym_id)
    if not member_oids:
        return {"error": "Aun no hay miembros registrados en este gimnasio."}

    ahora = datetime.now(timezone.utc).replace(tzinfo=None)

    asist_pipe = [
        {"$match": {"id_miembro": {"$in": member_oids}}},
        {"$group": {"_id": "$id_miembro", "ultima": {"$max": "$fecha"}, "visitas": {"$sum": 1}}},
    ]
    asist_map = {str(r["_id"]): r for r in db.asistencias.aggregate(asist_pipe)}

    pagos_pipe = [
        {"$match": {"id_miembro": {"$in": member_oids}}},
        {"$group": {"_id": "$id_miembro", "gastado": {"$sum": {"$ifNull": ["$monto", 0]}}}},
    ]
    pagos_map = {str(r["_id"]): float(r["gastado"]) for r in db.pagos.aggregate(pagos_pipe)}

    base = []
    for oid in member_oids:
        mid = str(oid)
        a = asist_map.get(mid, {})
        ultima = _parse_fecha(a.get("ultima"))
        recency = (ahora - ultima).days if ultima else 999
        base.append({
            "nombre":       nombres.get(mid, "Miembro"),
            "recency_dias": recency,
            "visitas":      int(a.get("visitas", 0)),
            "gastado":      round(pagos_map.get(mid, 0.0), 2),
        })

    visitas_vals = [b["visitas"] for b in base] or [0]
    gasto_vals   = [b["gastado"] for b in base] or [0]
    f_med = median(visitas_vals)
    m_med = median(gasto_vals)

    def _segmento(b):
        r = b["recency_dias"]
        if r > 45:
            return "Dormidos"
        if r > 21:
            return "En riesgo"
        if b["visitas"] >= f_med and b["gastado"] >= m_med:
            return "Campeones"
        if b["visitas"] >= f_med:
            return "Fieles"
        return "Prometedores"

    conteo = {k: 0 for k in SEGMENTOS_META}
    for b in base:
        b["segmento"] = _segmento(b)
        conteo[b["segmento"]] += 1

    base.sort(key=lambda x: (x["recency_dias"], -x["visitas"]))

    orden = ["Campeones", "Fieles", "Prometedores", "En riesgo", "Dormidos"]
    segmentos = [{
        "nombre":      s,
        "total":       conteo[s],
        "color":       SEGMENTOS_META[s]["color"],
        "descripcion": SEGMENTOS_META[s]["descripcion"],
    } for s in orden]

    n = len(base)
    return {
        "miembros":  base[:300],
        "segmentos": segmentos,
        "total":     n,
        "promedios": {
            "recency_dias": round(sum(b["recency_dias"] for b in base) / n, 1),
            "visitas":      round(sum(b["visitas"] for b in base) / n, 1),
            "gastado":      round(sum(b["gastado"] for b in base) / n, 2),
        },
        "ejecutado_en": datetime.now().isoformat(),
    }


# ── 3. Fuerza estimada (1RM, Epley) ─────────────────────────────────────────────

def _fuerza(gym_id):
    db = get_mongo_db()
    member_oids, nombres = _gym_member_ids(db, gym_id)
    if not member_oids:
        return {"error": "Aun no hay miembros registrados en este gimnasio."}

    entrenos = list(db.entrenamientos_realizados.find(
        {"id_miembro": {"$in": member_oids}},
        {"id_miembro": 1, "ejercicios": 1},
    ))
    if not entrenos:
        return {"error": "Aun no hay entrenamientos registrados. Cuando tus miembros registren sus series, aqui veras su fuerza estimada."}

    # Acumulador por nombre de ejercicio
    acc = {}
    mejor = {"kg": 0.0, "ejercicio": "", "miembro": ""}
    total_series = 0

    for e in entrenos:
        mid = str(e.get("id_miembro"))
        nombre_m = nombres.get(mid, "Miembro")
        for ej in (e.get("ejercicios") or []):
            nombre_ej = (ej.get("nombre") or "").strip()
            if not nombre_ej:
                continue
            best_member_lift = 0.0
            for s in (ej.get("series") or []):
                try:
                    peso = float(s.get("peso") or 0)
                    reps = int(s.get("repeticiones") or 0)
                except (TypeError, ValueError):
                    continue
                if peso <= 0 or reps <= 0:
                    continue
                total_series += 1
                rm = peso * (1 + reps / 30.0)   # Epley
                if rm > best_member_lift:
                    best_member_lift = rm
            if best_member_lift <= 0:
                continue
            a = acc.setdefault(nombre_ej, {"suma": 0.0, "max": 0.0, "miembros": set(), "series": 0})
            a["suma"] += best_member_lift
            a["series"] += 1
            a["miembros"].add(mid)
            if best_member_lift > a["max"]:
                a["max"] = best_member_lift
            if best_member_lift > mejor["kg"]:
                mejor = {"kg": round(best_member_lift, 1), "ejercicio": nombre_ej, "miembro": nombre_m}

    if not acc:
        return {"error": "Los entrenamientos registrados aun no tienen peso y repeticiones para estimar la fuerza."}

    ejercicios = sorted([
        {
            "nombre":   nombre,
            "prom_kg":  round(a["suma"] / max(1, a["series"]), 1),
            "max_kg":   round(a["max"], 1),
            "miembros": len(a["miembros"]),
            "registros": a["series"],
        }
        for nombre, a in acc.items()
    ], key=lambda x: x["prom_kg"], reverse=True)

    return {
        "ejercicios":   ejercicios[:12],
        "mejor":        mejor if mejor["kg"] > 0 else None,
        "total_series": total_series,
        "ejercicios_distintos": len(acc),
        "ejecutado_en": datetime.now().isoformat(),
    }


# ── Endpoints ───────────────────────────────────────────────────────────────────

def _run_cached(prefix, fn, gym_id):
    key = f"{prefix}_gym{gym_id}"
    cached = cache_get(key)
    if cached:
        cached["desde_cache"] = True
        return cached
    payload = fn(gym_id)
    payload["desde_cache"] = False
    if "error" not in payload:
        cache_set(key, payload)
    return payload


@spark_negocio_bp.route("/api/analytics/heatmap-asistencia", methods=["GET"])
@jwt_required()
def heatmap_asistencia():
    try:
        gym_id = get_jwt().get("id_gimnasio")
        return jsonify(_run_cached("heatmap", _heatmap_asistencia, gym_id)), 200
    except Exception as e:
        import traceback; traceback.print_exc()
        return jsonify({"error": str(e)}), 500


@spark_negocio_bp.route("/api/analytics/rfm", methods=["GET"])
@jwt_required()
def rfm_analytics():
    try:
        gym_id = get_jwt().get("id_gimnasio")
        return jsonify(_run_cached("rfm", _rfm, gym_id)), 200
    except Exception as e:
        import traceback; traceback.print_exc()
        return jsonify({"error": str(e)}), 500


@spark_negocio_bp.route("/api/analytics/fuerza", methods=["GET"])
@jwt_required()
def fuerza_analytics():
    try:
        gym_id = get_jwt().get("id_gimnasio")
        return jsonify(_run_cached("fuerza", _fuerza, gym_id)), 200
    except Exception as e:
        import traceback; traceback.print_exc()
        return jsonify({"error": str(e)}), 500
