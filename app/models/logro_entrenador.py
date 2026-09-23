from datetime import datetime, timezone
from bson.objectid import ObjectId
from app.mongo import get_db

class LogroEntrenador:
    collection = "logros_entrenador"

    def __init__(self, id_entrenador, titulo, descripcion=None, fecha=None, tipo=None, fecha_creacion=None, _id=None):
        self._id = _id
        self.id_entrenador = ObjectId(id_entrenador) if isinstance(id_entrenador, str) else id_entrenador
        self.titulo = titulo
        self.descripcion = descripcion
        self.fecha = fecha
        self.tipo = tipo
        self.fecha_creacion = fecha_creacion or datetime.now(timezone.utc)

    def to_dict(self):
        return {
            "id_logro": self._id,
            "id_entrenador": self.id_entrenador,
            "titulo": self.titulo,
            "descripcion": self.descripcion,
            "fecha": self.fecha.isoformat() if isinstance(self.fecha, datetime) else self.fecha,
            "tipo": self.tipo,
            "fecha_creacion": self.fecha_creacion.isoformat() if isinstance(self.fecha_creacion, datetime) else self.fecha_creacion
        }

    def save(self):
        db = get_db()
        data = {
            "id_entrenador": self.id_entrenador,
            "titulo": self.titulo,
            "descripcion": self.descripcion,
            "fecha": self.fecha,
            "tipo": self.tipo,
            "fecha_creacion": self.fecha_creacion
        }
        if self._id:
            db[self.collection].update_one({"_id": self._id}, {"$set": data})
        else:
            result = db[self.collection].insert_one(data)
            self._id = result.inserted_id
        return self._id