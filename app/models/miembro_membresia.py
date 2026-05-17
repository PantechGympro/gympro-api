from bson.objectid import ObjectId
from app.mongo import get_db


def _resolve_nombre_membresia(id_membresia):
    """
    Resuelve el nombre del tipo de membresía dado su id.
    - Si id_membresia es int (o str numérico) → busca en PG TipoMembresia.
    - Si es ObjectId → legacy, busca en colección Mongo 'membresias'.
    """
    if id_membresia is None:
        return "N/A"
    # PG path (integer id)
    try:
        pg_id = int(id_membresia)
        from app.models.pg.tipo_membresia import TipoMembresia
        tm = TipoMembresia.query.get(pg_id)
        return tm.nombre if tm else "N/A"
    except (ValueError, TypeError):
        pass
    # Legacy Mongo path
    try:
        oid = ObjectId(id_membresia) if isinstance(id_membresia, str) else id_membresia
        db = get_db()
        doc = db.membresias.find_one({"_id": oid})
        return doc["nombre"] if doc else "N/A"
    except Exception:
        return "N/A"


class MiembroMembresia:
    collection = "miembro_membresia"

    def __init__(self, id_miembro, id_membresia, fecha_inicio=None, fecha_fin=None, estado=None, _id=None, **kwargs):
        self._id = _id
        self.id_miembro   = ObjectId(id_miembro) if isinstance(id_miembro, str) else id_miembro
        # id_membresia puede ser int (PG) o ObjectId (legacy Mongo) — NO forzar ObjectId
        try:
            self.id_membresia = int(id_membresia)
        except (TypeError, ValueError):
            self.id_membresia = ObjectId(id_membresia) if isinstance(id_membresia, str) else id_membresia
        self.fecha_inicio = fecha_inicio
        self.fecha_fin    = fecha_fin
        self.estado       = estado

    def to_dict(self):
        nombre_membresia = _resolve_nombre_membresia(self.id_membresia)
        return {
            "id":               str(self._id) if self._id else None,
            "id_miembro":       str(self.id_miembro),
            "id_membresia":     self.id_membresia,
            "nombre_membresia": nombre_membresia,
            "fecha_inicio":     str(self.fecha_inicio) if self.fecha_inicio else None,
            "fecha_fin":        str(self.fecha_fin)    if self.fecha_fin    else None,
            "estado":           self.estado,
        }

    def save(self):
        db = get_db()
        data = {
            "id_miembro": self.id_miembro,
            "id_membresia": self.id_membresia,
            "fecha_inicio": self.fecha_inicio,
            "fecha_fin": self.fecha_fin,
            "estado": self.estado
        }
        if self._id:
            db[self.collection].update_one({"_id": self._id}, {"$set": data})
        else:
            result = db[self.collection].insert_one(data)
            self._id = result.inserted_id
        return self._id

    @classmethod
    def find_by_id(cls, mm_id):
        try:
            oid = ObjectId(mm_id) if isinstance(mm_id, str) else mm_id
            data = get_db()[cls.collection].find_one({"_id": oid})
            return cls(**data) if data else None
        except Exception:
            return None