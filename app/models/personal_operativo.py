"""
models/personal_operativo.py — Personal del gimnasio SIN acceso al sistema
(mantenimiento, limpieza, seguridad, etc.).

Por qué es un modelo aparte del staff con login (owner_trainers.py /
Usuario + Rol en PostgreSQL): ese staff (Entrenador/Recepcionista) tiene
email, contraseña y un rol real en la tabla `roles`, con JWT y panel
propio. Dar ese mismo tratamiento a mantenimiento/limpieza los dejaría
con una cuenta que inicia sesión pero no aterriza en ningún dashboard --
hoy no existe rol ni panel para ellos en el sistema de permisos, y crear
uno solo para esto sería mucho más de lo que este personal necesita.

Este modelo es, en cambio, un directorio puro: nombre, puesto, teléfono,
notas y en qué sucursal está -- sin credenciales ni alcance que resolver.
Vive en Mongo (colección `personal_operativo`), igual que `miembros`:
no participa del sistema de roles/JWT, así que no necesita tabla ni FK
en PostgreSQL.
"""
from datetime import datetime, timezone
from bson.objectid import ObjectId
from app.mongo import get_db

# Catálogo sugerido en el formulario de alta -- no es una validación
# estricta (ver crear_personal()/editar_personal() en las rutas): el
# frontend siempre ofrece además un campo "Otro" con texto libre, así
# que un puesto que no está aquí no es un error, solo no se sugiere.
PUESTOS_SUGERIDOS = ("Mantenimiento", "Limpieza", "Seguridad", "Cocina / Snack bar")


class PersonalOperativo:
    collection = "personal_operativo"

    def __init__(self, nombre, puesto, id_gimnasio, telefono=None, notas=None,
                 activo=True, fecha_ingreso=None, _id=None, **kwargs):
        self._id          = _id
        self.nombre        = nombre
        self.puesto        = puesto
        self.id_gimnasio   = id_gimnasio
        self.telefono      = telefono
        self.notas         = notas
        self.activo        = activo
        self.fecha_ingreso = fecha_ingreso or datetime.now(timezone.utc)

    def to_dict(self):
        fi = self.fecha_ingreso
        return {
            "id":            str(self._id) if self._id else None,
            "nombre":        self.nombre,
            "puesto":        self.puesto,
            "id_gimnasio":   self.id_gimnasio,
            "telefono":      self.telefono,
            "notas":         self.notas,
            "activo":        self.activo,
            "fecha_ingreso": fi.isoformat() if hasattr(fi, "isoformat") else fi,
        }

    def save(self):
        db = get_db()
        data = {
            "nombre":        self.nombre,
            "puesto":        self.puesto,
            "id_gimnasio":   self.id_gimnasio,
            "telefono":      self.telefono,
            "notas":         self.notas,
            "activo":        self.activo,
            "fecha_ingreso": self.fecha_ingreso,
        }
        if self._id:
            db[self.collection].update_one({"_id": self._id}, {"$set": data})
        else:
            result = db[self.collection].insert_one(data)
            self._id = result.inserted_id
        return self._id

    @classmethod
    def find_by_id(cls, id_, id_gimnasio=None):
        try:
            oid = ObjectId(id_) if isinstance(id_, str) else id_
        except Exception:
            return None
        query = {"_id": oid}
        if id_gimnasio is not None:
            query["id_gimnasio"] = id_gimnasio
        data = get_db()[cls.collection].find_one(query)
        return cls(**data) if data else None
