from datetime import datetime, timezone, time
from bson.objectid import ObjectId
from app.mongo import get_db

class Asistencia:
    collection = "asistencias"

    def __init__(self, id_miembro, fecha=None, hora_entrada=None, hora_salida=None, _id=None, **kwargs):
        self._id = _id
        self.id_miembro = ObjectId(id_miembro) if isinstance(id_miembro, str) else id_miembro
        
        # Si no se pasa fecha, tomamos la fecha de hoy, aislada a las 00:00:00 para facilitar búsquedas
        if fecha is None:
            now = datetime.now(timezone.utc)
            self.fecha = datetime(now.year, now.month, now.day, tzinfo=timezone.utc)
        else:
            self.fecha = fecha

        # Guardamos las horas como strings ("HH:MM:SS") ya que BSON no tiene tipo 'time' puro
        if isinstance(hora_entrada, time):
            self.hora_entrada = hora_entrada.strftime('%H:%M:%S')
        else:
            self.hora_entrada = hora_entrada

        if isinstance(hora_salida, time):
            self.hora_salida = hora_salida.strftime('%H:%M:%S')
        else:
            self.hora_salida = hora_salida

    def to_dict(self):
        return {
            "id_asistencia": self._id,
            "id_miembro": self.id_miembro,
            "fecha": self.fecha.strftime('%Y-%m-%d') if isinstance(self.fecha, datetime) else self.fecha,
            "hora_entrada": self.hora_entrada,
            "hora_salida": self.hora_salida
        }

    def save(self):
        db = get_db()
        data = {
            "id_miembro": self.id_miembro,
            "fecha": self.fecha,
            "hora_entrada": self.hora_entrada,
            "hora_salida": self.hora_salida
        }
        if self._id:
            db[self.collection].update_one({"_id": self._id}, {"$set": data})
        else:
            result = db[self.collection].insert_one(data)
            self._id = result.inserted_id
        return self._id

    @classmethod
    def find_by_id(cls, asistencia_id):
        try:
            oid = ObjectId(asistencia_id) if isinstance(asistencia_id, str) else asistencia_id
            data = get_db()[cls.collection].find_one({"_id": oid})
            return cls(**data) if data else None
        except Exception:
            return None
            
    def __repr__(self):
        return f'<Asistencia {self._id} - Miembro {self.id_miembro}>'