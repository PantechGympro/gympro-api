import os
import subprocess
from bson import json_util
from app.mongo import get_db

MONGORESTORE_PATH = "mongorestore"

def restore_backup_file(file_path):
    """Restaura un backup de MongoDB dependiendo de si es .archive o .json"""
    
    if not os.path.exists(file_path):
        raise Exception("Archivo de respaldo no existe")

    # Archivo de Backup Completo (Generado por mongodump)
    if file_path.endswith(".archive"):
        db_user = os.getenv("MONGO_USER")
        db_pass = os.getenv("MONGO_PASSWORD")
        db_cluster = os.getenv("MONGO_CLUSTER")
        db_name = os.getenv("MONGO_DB")

        mongo_uri = f"mongodb+srv://{db_user}:{db_pass}@{db_cluster}/"

        try:
            # --nsInclude asegura que solo restauremos nuestra base de datos específica
            # --drop elimina las colecciones actuales antes de restaurarlas para evitar duplicados
            subprocess.run(
                [
                    MONGORESTORE_PATH, 
                    "--uri", mongo_uri, 
                    "--nsInclude", f"{db_name}.*", 
                    f"--archive={file_path}", 
                    "--drop"
                ],
                check=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE
            )
        except subprocess.CalledProcessError as e:
            raise Exception(f"Error crítico en mongorestore: {e.stderr.decode('utf-8', errors='ignore')}")

    # Archivo de Backup Incremental/Diferencial (Generado por nuestro script en Python)
    elif file_path.endswith(".json"):
        try:
            db = get_db()
            with open(file_path, "r", encoding="utf-8") as f:
                # json_util convierte correctamente las fechas y los ObjectId guardados en el JSON
                data = json_util.loads(f.read())
                
                # Iteramos sobre cada colección y hacemos un "upsert" (Actualizar si existe, insertar si no)
                for coll_name, docs in data.items():
                    coll = db[coll_name]
                    for doc in docs:
                        coll.update_one({"_id": doc["_id"]}, {"$set": doc}, upsert=True)
                        
        except Exception as e:
            raise Exception(f"Error restaurando backup incremental JSON: {str(e)}")

    else:
        raise Exception("Formato de archivo no soportado para restauración. Solo se permiten .archive y .json")