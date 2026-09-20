from datetime import datetime, timezone
from bson.objectid import ObjectId
from app.mongo import get_db

class CertificacionEntrenador:
    collection = "certificaciones_entrenador"

    def __init__(self, id_entrenador, nombre, institucion=None, fecha_obtencion=None, 
                 fecha_expiracion=None, archivo_url=None, fecha_creacion=None, _id=None):
        self._id = _id
        self.id_entrenador = ObjectId(id_entrenador) if isinstance(id_entrenador, str) else id_entrenador
        self.nombre = nombre
        self.institucion = institucion
        self.fecha_obtencion = fecha_obtencion
        self.fecha_expiracion = fecha_expiracion
        self.archivo_url = archivo_url
        self.fecha_creacion = fecha_creacion or datetime.now(timezone.utc)

    def to_dict(self):
        return {
            "id_certificacion": self._id,
            "id_entrenador": self.id_entrenador,
            "nombre": self.nombre,
            "institucion": self.institucion,
            "fecha_obtencion": self.fecha_obtencion.isoformat() if isinstance(self.fecha_obtencion, datetime) else self.fecha_obtencion,
            "fecha_expiracion": self.fecha_expiracion.isoformat() if isinstance(self.fecha_expiracion, datetime) else self.fecha_expiracion,
            "archivo_url": self.archivo_url,
            "fecha_creacion": self.fecha_creacion.isoformat() if isinstance(self.fecha_creacion, datetime) else self.fecha_creacion
        }

    def save(self):
        db = get_db()
        data = {
            "id_entrenador": self.id_entrenador,
            "nombre": self.nombre,
            "institucion": self.institucion,
            "fecha_obtencion": self.fecha_obtencion,
            "fecha_expiracion": self.fecha_expiracion,
            "archivo_url": self.archivo_url,
            "fecha_creacion": self.fecha_creacion
        }
        if self._id:
            db[self.collection].update_one({"_id": self._id}, {"$set": data})
        else:
            result = db[self.collection].insert_one(data)
            self._id = result.inserted_id
        return self._id

    @classmethod
    def find_by_id(cls, cert_id):
        try:
            oid = ObjectId(cert_id) if isinstance(cert_id, str) else cert_id
            data = get_db()[cls.collection].find_one({"_id": oid})
            return cls(**data) if data else None
        except Exception:
            return None