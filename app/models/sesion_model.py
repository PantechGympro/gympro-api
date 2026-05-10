from datetime import datetime, timezone
from bson.objectid import ObjectId
from app.mongo import get_db

class Sesion:
    collection = "sesiones"

    def __init__(self, id_entrenador, id_miembro=None, fecha=None, hora_inicio=None,
                 duracion_minutos=60, tipo="Personal", ubicacion=None, estado="scheduled",
                 nombre_sesion=None, notas=None, num_ejercicios=0, asistencia=False,
                 fecha_creacion=None, fecha_actualizacion=None, _id=None, **kwargs):
        
        self._id = _id
        self.id_entrenador = ObjectId(id_entrenador) if isinstance(id_entrenador, str) else id_entrenador
        self.id_miembro = ObjectId(id_miembro) if isinstance(id_miembro, str) and id_miembro else id_miembro
        
        self.fecha = fecha
        self.hora_inicio = hora_inicio
        self.duracion_minutos = duracion_minutos
        self.tipo = tipo
        self.ubicacion = ubicacion
        self.estado = estado
        self.nombre_sesion = nombre_sesion
        self.notas = notas
        self.num_ejercicios = num_ejercicios
        self.asistencia = asistencia
        
        self.fecha_creacion = fecha_creacion or datetime.now(timezone.utc)
        self.fecha_actualizacion = fecha_actualizacion or datetime.now(timezone.utc)

    def to_dict(self):
        return {
            "id_entrenador": self.id_entrenador,
            "id_miembro": self.id_miembro,
            "fecha": self.fecha,
            "hora_inicio": self.hora_inicio,
            "duracion_minutos": self.duracion_minutos,
            "tipo": self.tipo,
            "ubicacion": self.ubicacion,
            "estado": self.estado,
            "nombre_sesion": self.nombre_sesion,
            "notas": self.notas,
            "num_ejercicios": self.num_ejercicios,
            "asistencia": self.asistencia,
            "fecha_creacion": self.fecha_creacion,
            "fecha_actualizacion": datetime.now(timezone.utc) # Se actualiza automáticamente al guardar
        }

    def save(self):
        db = get_db()
        data = self.to_dict()
        if self._id:
            db[self.collection].update_one({"_id": self._id}, {"$set": data})
        else:
            result = db[self.collection].insert_one(data)
            self._id = result.inserted_id
        return self._id

    # ──────────────────────────────────────────────
    # MÉTODOS DE NEGOCIO
    # ──────────────────────────────────────────────
    def iniciar_sesion(self):
        self.estado = "in-progress"
        self.save()

    def completar_sesion(self, notas=None):
        self.estado = "completed"
        self.asistencia = True
        if notas:
            self.notas = notas
        self.save()

    def cancelar_sesion(self, motivo=None):
        self.estado = "cancelled"
        if motivo:
            self.notas = f"Cancelada: {motivo}"
        self.save()

    @classmethod
    def find_by_id(cls, sesion_id):
        try:
            oid = ObjectId(sesion_id) if isinstance(sesion_id, str) else sesion_id
            data = get_db()[cls.collection].find_one({"_id": oid})
            return cls(**data) if data else None
        except Exception:
            return None