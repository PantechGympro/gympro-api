from datetime import datetime, timezone
from bson.objectid import ObjectId
from app.mongo import get_db

class Backup:
    collection = "backups"

    def __init__(self, type="incremental", status="pending", progress=0, 
                 created_at=None, finished_at=None, _id=None):
        self._id = _id
        self.type = type
        self.status = status
        self.progress = int(progress)
        self.created_at = created_at or datetime.now(timezone.utc)
        self.finished_at = finished_at

    def to_dict(self):
        return {
            "id": self._id,
            "type": self.type,
            "status": self.status,
            "progress": self.progress,
            "created_at": self.created_at.isoformat() if isinstance(self.created_at, datetime) else self.created_at,
            "finished_at": self.finished_at.isoformat() if isinstance(self.finished_at, datetime) else self.finished_at
        }

    def save(self):
        db = get_db()
        data = {
            "type": self.type,
            "status": self.status,
            "progress": self.progress,
            "created_at": self.created_at,
            "finished_at": self.finished_at
        }
        if self._id:
            db[self.collection].update_one({"_id": self._id}, {"$set": data})
        else:
            result = db[self.collection].insert_one(data)
            self._id = result.inserted_id
        return self._id

    @classmethod
    def find_by_id(cls, backup_id):
        try:
            oid = ObjectId(backup_id) if isinstance(backup_id, str) else backup_id
            data = get_db()[cls.collection].find_one({"_id": oid})
            return cls(**data) if data else None
        except Exception:
            return None