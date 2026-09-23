from datetime import datetime, timezone
from bson.objectid import ObjectId
from app.mongo import get_db

class Venta:
    collection = "ventas"

    def __init__(self, total=0, fecha=None, _id=None):
        self._id = _id
        self.fecha = fecha or datetime.now(timezone.utc)
        self.total = float(total)

    def to_dict(self):
        return {
            "fecha": self.fecha,
            "total": self.total
        }

    def save(self):
        db = get_db()
        if self._id:
            db[self.collection].update_one({"_id": self._id}, {"$set": self.to_dict()})
        else:
            result = db[self.collection].insert_one(self.to_dict())
            self._id = result.inserted_id
        return self._id

    # ──────────────────────────────────────────────
    # RELACIONES
    # ──────────────────────────────────────────────
    def get_detalles(self):
        """Equivalente a la relación 'detalles' en SQLAlchemy"""
        if not self._id:
            return []
        db = get_db()
        # Busca en la colección detalle_venta usando el ID de esta venta
        detalles_cursor = db.detalle_venta.find({"id_venta": self._id})
        return list(detalles_cursor)

    def to_dict_with_detalles(self):
        """Retorna el diccionario de la venta incluyendo sus detalles anidados"""
        data = self.to_dict()
        data["id_venta"] = str(self._id)
        data["detalles"] = [{**d, "_id": str(d["_id"]), "id_venta": str(d["id_venta"])} for d in self.get_detalles()]
        return data

    @classmethod
    def find_by_id(cls, venta_id):
        db = get_db()
        try:
            oid = ObjectId(venta_id) if isinstance(venta_id, str) else venta_id
        except Exception:
            return None
        
        data = db[cls.collection].find_one({"_id": oid})
        return cls(**data) if data else None