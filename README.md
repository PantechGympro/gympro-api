# GymPro API

Backend de GymPro: API REST construida con Flask + PySpark para analíticas. Corre en Docker con Gunicorn como servidor WSGI de producción.

→ [Documentación completa del proyecto](../doc/README.md)

---

## Requisitos

| Herramienta | Versión mínima | Notas |
|---|---|---|
| Docker | 24.x+ | Ver instalación abajo si no lo tienes |
| Docker Compose | 2.x+ (plugin) | Incluido con Docker Desktop / Engine moderno |
| Git | cualquiera | Para clonar el repo |

> **Nota WSL**: si ejecutas desde WSL 2 (Ubuntu), Docker debe estar instalado dentro de WSL o usar Docker Desktop con integración WSL 2 habilitada.

---

## Instalación de Docker en WSL 2 (Ubuntu)

Si `docker --version` no funciona en tu terminal WSL, sigue estos pasos:

```bash
# 1. Actualizar paquetes
sudo apt update && sudo apt upgrade -y

# 2. Instalar dependencias
sudo apt install -y ca-certificates curl gnupg lsb-release

# 3. Agregar clave GPG oficial de Docker
sudo install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg \
  | sudo gpg --dearmor -o /etc/apt/keyrings/docker.gpg
sudo chmod a+r /etc/apt/keyrings/docker.gpg

# 4. Agregar repositorio de Docker
echo \
  "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] \
  https://download.docker.com/linux/ubuntu \
  $(lsb_release -cs) stable" \
  | sudo tee /etc/apt/sources.list.d/docker.list > /dev/null

# 5. Instalar Docker Engine + Compose plugin
sudo apt update
sudo apt install -y docker-ce docker-ce-cli containerd.io \
                    docker-buildx-plugin docker-compose-plugin

# 6. Agregar tu usuario al grupo docker (evita usar sudo en cada comando)
sudo usermod -aG docker $USER
newgrp docker

# 7. Iniciar el servicio (en WSL hay que hacerlo manualmente)
sudo service docker start

# 8. Verificar instalación
docker --version        # Docker version 27.x.x
docker compose version  # Docker Compose version v2.x.x
docker run hello-world  # debe imprimir "Hello from Docker!"
```

> **Arranque automático en WSL**: agrega `sudo service docker start` a tu `~/.bashrc` o `~/.zshrc` para que Docker arranque automáticamente al abrir la terminal.

---

## Variables de entorno

Copia el archivo de ejemplo y completa los valores:

```bash
cp .env.example .env
```

Edita `.env` con tus credenciales reales:

```env
# ── Flask ──────────────────────────────────────────────────────────────────
SECRET_KEY=cambia_esto_por_un_secreto_largo_y_aleatorio
JWT_SECRET_KEY=cambia_esto_por_otro_secreto_diferente
FLASK_DEBUG=0

# ── MongoDB Atlas ──────────────────────────────────────────────────────────
# Obtén la URI desde Atlas → Connect → Drivers
MONGO_USER=tu_usuario_atlas
MONGO_PASSWORD=tu_password_atlas
MONGO_CLUSTER=cluster0.xxxxx.mongodb.net
MONGO_DB=gymdb

# ── Redis (para rate limiting y cache de tenant) ───────────────────────────
REDIS_URL=redis://redis:6379/0

# ── CORS ───────────────────────────────────────────────────────────────────
# Desarrollo: http://localhost:3000
# Producción: https://tugimnasio.gymsaas.com
ALLOWED_ORIGINS=http://localhost:3000

# ── Email (backups y notificaciones) ──────────────────────────────────────
MAIL_SERVER=smtp.gmail.com
MAIL_PORT=587
MAIL_USE_TLS=True
MAIL_USERNAME=tu_email@gmail.com
MAIL_PASSWORD=tu_app_password_de_16_letras
MAIL_DEFAULT_SENDER=tu_email@gmail.com
MAIL_RECIPIENT=tu_email@gmail.com

# ── Spark (opcional) ───────────────────────────────────────────────────────
# false = endpoints Spark retornan 503 sin afectar el resto de la API
SPARK_ENABLED=false
```

---

## Levantar con Docker Compose (recomendado)

Desde la **raíz del proyecto** (`SistemaGYM/`):

```bash
# Primera vez: construir imágenes y levantar
docker compose up --build -d

# Ver logs de la API en tiempo real
docker compose logs -f api

# Ver logs de todos los servicios
docker compose logs -f

# Detener todos los servicios
docker compose down

# Detener y eliminar volúmenes (limpieza total)
docker compose down -v
```

Servicios disponibles después de `docker compose up`:

| Servicio | URL | Descripción |
|---|---|---|
| API (Flask) | http://localhost:5000 | API REST |
| Web (React) | http://localhost:3000 | Frontend |
| Redis | localhost:6379 | Cache interno |

---

## Construir solo la imagen de la API

Si solo quieres construir o probar la imagen de la API de forma aislada:

```bash
# Desde la carpeta api/
cd api/

# Construir
docker build -t gympro-api:dev .

# Correr el contenedor standalone (sin compose)
docker run --rm -p 5000:5000 \
  --env-file .env \
  gympro-api:dev

# Verificar que responde
curl http://localhost:5000/api/health
```

---

## Comandos útiles de desarrollo

```bash
# Entrar al contenedor en ejecución (shell interactiva)
docker compose exec api bash

# Ejecutar un comando puntual dentro del contenedor
docker compose exec api python -c "from app import create_app; print('OK')"

# Ver variables de entorno activas dentro del contenedor
docker compose exec api env | grep -E "FLASK|MONGO|REDIS"

# Reiniciar solo la API sin reconstruir
docker compose restart api

# Reconstruir solo la API (después de cambios en requirements.txt o Dockerfile)
docker compose up --build api -d

# Ver el uso de recursos de los contenedores
docker stats

# Limpiar imágenes huérfanas (liberar espacio en disco)
docker image prune -f
```

---

## Estructura del código

```
api/
├── app/
│   ├── __init__.py          # Factory function create_app()
│   ├── config.py            # Configuración desde variables de entorno
│   ├── extensions.py        # Inicialización de extensiones Flask
│   ├── auth/
│   │   └── routes.py        # /api/auth/login, /api/auth/register
│   ├── backups/
│   │   ├── routes.py        # /api/backups/* (requieren JWT + Admin)
│   │   └── service.py       # Lógica de backup/restore
│   ├── models/              # Modelos de datos (PyMongo)
│   ├── routes/              # Blueprints: miembros, pagos, spark, etc.
│   └── utils/               # Helpers: seguridad, Luhn, etc.
├── spark/                   # Scripts PySpark standalone
├── Dockerfile               # Multi-stage: builder + runtime
├── wsgi.py                  # Entry point de Gunicorn (producción)
├── run.py                   # Servidor de desarrollo (FLASK_DEBUG=1)
├── requirements.txt         # Dependencias Python
└── .dockerignore            # Excluye venv, .env, uploads del build
```

---

## Endpoints principales

| Método | Endpoint | Auth requerida | Descripción |
|---|---|---|---|
| GET | `/api/health` | — | Health check del servicio |
| POST | `/api/auth/login` | — | Login → retorna JWT |
| POST | `/api/auth/register` | — | Registro de usuario |
| GET | `/api/miembros` | JWT | Listado paginado de miembros |
| POST | `/api/miembros` | JWT + Admin | Crear miembro |
| GET | `/api/pagos` | JWT | Historial de pagos |
| GET | `/api/dashboard/summary` | JWT | Métricas del dashboard |
| GET | `/api/spark/kmeans` | JWT + Analytics | Segmentación K-Means |
| GET | `/api/spark/regression` | JWT + Analytics | Predicción de progreso |
| POST | `/api/backups/trigger` | JWT + Admin | Ejecutar backup |
| GET | `/api/backups/history` | JWT + Admin | Historial de backups |

---

## Solución de problemas

**`docker: command not found` en WSL**
→ Seguir la guía de instalación de Docker de arriba.

**`permission denied` al correr docker sin sudo**
→ `sudo usermod -aG docker $USER && newgrp docker`

**`Cannot connect to the Docker daemon`**
→ `sudo service docker start`

**La API responde con error de MongoDB**
→ Verificar que `MONGO_USER`, `MONGO_PASSWORD` y `MONGO_CLUSTER` en `.env` son correctos. Confirmar que tu IP está en la whitelist de MongoDB Atlas.

**`ModuleNotFoundError` al iniciar**
→ El `requirements.txt` cambió. Reconstruir: `docker compose up --build api -d`
