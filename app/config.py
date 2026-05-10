import os

class Config:
    SECRET_KEY = os.getenv("SECRET_KEY")
    JWT_SECRET_KEY = os.getenv("JWT_SECRET_KEY")

    SQLALCHEMY_DATABASE_URI = (
        f"mysql+pymysql://{os.getenv('DB_USER')}:"
        f"{os.getenv('DB_PASSWORD')}@"
        f"{os.getenv('DB_HOST')}/"
        f"{os.getenv('DB_NAME')}"
    )

    SQLALCHEMY_TRACK_MODIFICATIONS = False

    # --- CONFIGURACIÓN DE CORREO (AÑADIDA Y CORREGIDA) ---
    # Convertimos el puerto a entero
    MAIL_SERVER = os.getenv('MAIL_SERVER', 'smtp.gmail.com')
    MAIL_PORT = int(os.getenv('MAIL_PORT', 587)) 
    
    # TRUCO: Comparamos con el string 'True' para obtener un booleano real
    MAIL_USE_TLS = os.getenv('MAIL_USE_TLS', 'True') == 'True'
    
    MAIL_USERNAME = os.getenv('MAIL_USERNAME')
    MAIL_PASSWORD = os.getenv('MAIL_PASSWORD') # Recuerda: App Password de 16 letras
    MAIL_DEFAULT_SENDER = os.getenv('MAIL_DEFAULT_SENDER')
    
    # Destinatario por defecto (para usar en el servicio)
    MAIL_RECIPIENT = os.getenv('MAIL_RECIPIENT', os.getenv('MAIL_USERNAME'))