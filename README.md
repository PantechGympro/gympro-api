# GymPro API

Backend de GymPro: API REST construida con Flask y servida por Gunicorn dentro de un
contenedor Docker. Persiste en PostgreSQL (datos relacionales) y MongoDB (datos
flexibles), usa Redis para caché y rate limiting, y expone módulos de inteligencia
artificial basados en scikit-learn.

Documentación completa del proyecto: [../doc/README.md](../doc/README.md)

---

## Requisitos

| Herramienta | Versión mínima | Notas |
|---|---|---|
| Docker | 24.x o superior | Ver instalación abajo si no lo tienes |
| Docker Compose | 2.x (plugin) | Incluido con Docker Desktop / Engine moderno |
| Git | cualquiera | Para clonar el repositorio |

Nota WSL: si ejecutas desde WSL 2 (Ubuntu), Docker debe estar instalado dentro de WSL o
usar Docker Desktop con la integración WSL 2 habilitada.

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
docker --version
docker compose version
docker run hello-world
```

Arranque automático en WSL: agrega `sudo service docker start` a tu `~/.bashrc` o
`~/.zshrc` para que Docker arranque al abrir la terminal.

---

## Variables de entorno

Copia el archivo de ejemplo y completa los valores:

```bash
cp .env.example .env
```

El archivo `api/.env` se inyecta al contenedor mediante `env_file` en el compose. Las
conexiones a PostgreSQL, MongoDB y Redis ya vienen resueltas por `docker-compose.yml`
(apuntan a los servicios internos), así que en desarrollo normalmente solo necesitas
completar los secretos y el correo:

```env
# ── Flask / seguridad ──────────────────────────────────────────────────────
SECRET_KEY=cambia_esto_por_un_secreto_largo_y_aleatorio
JWT_SECRET_KEY=cambia_esto_por_otro_secreto_diferente
FLASK_DEBUG=0

# ── PostgreSQL (usuarios, roles, gimnasios, suscripciones) ─────────────────
# El compose provee estos valores por defecto; sólo cámbialos en producción.
POSTGRES_DB=gymprodb
POSTGRES_USER=gymuser
POSTGRES_PASSWORD=gympassword

# ── MongoDB (miembros, rutinas, progreso, pagos) ───────────────────────────
MONGO_URI=mongodb://mongo:27017/gymdb

# ── Redis (caché y rate limiting) ──────────────────────────────────────────
REDIS_URL=redis://redis:6379/0

# ── Email (recuperación de contraseña y notificaciones de backup) ──────────
# Gmail requiere una App Password de 16 caracteres, no la contraseña normal.
MAIL_SERVER=smtp.gmail.com
MAIL_PORT=587
MAIL_USE_TLS=True
MAIL_USERNAME=tu_email@gmail.com
MAIL_PASSWORD=tu_app_password_de_16_letras
MAIL_DEFAULT_SENDER=tu_email@gmail.com
MAIL_RECIPIENT=tu_email@gmail.com
```

Importante: como el compose usa `env_file`, tras cambiar `api/.env` hay que recrear el
contenedor para que tome los nuevos valores: `docker compose up -d --force-recreate api`.

---

## Levantar con Docker Compose (recomendado)

Desde la raíz del proyecto (`SistemaGYM/`):

```bash
# Primera vez o tras cambios en código/dependencias: construir y levantar
docker compose up --build -d

# Ver logs de la API en tiempo real
docker compose logs -f api

# Estado de los contenedores
docker compose ps

# Detener todo (los datos persisten en volúmenes)
docker compose down

# Detener y borrar volúmenes (limpieza total: elimina TODOS los datos)
docker compose down -v
```

Servicios tras `docker compose up`:

| Servicio | URL / Puerto | Descripción |
|---|---|---|
| Web (React) | http://localhost:8080 | Frontend; nginx proxea /api hacia la API |
| API (Flask) | http://localhost:5000 | API REST |
| PostgreSQL | localhost:5433 | Usuarios, roles, gimnasios, suscripciones |
| MongoDB | localhost:27035 | Miembros, rutinas, progreso, pagos |
| Redis | interno | Caché y rate limiting |
| Ollama | localhost:11434 | IA local para ETL de rutinas (opcional) |

Verificación de salud: `curl http://localhost:5000/api/health`.

---

## Migraciones de base de datos

El `entrypoint` del contenedor ejecuta `flask db upgrade` (Alembic) al arrancar, por lo
que el esquema de PostgreSQL se crea/actualiza automáticamente. Las colecciones de
MongoDB se crean bajo demanda en el primer uso. No se requiere ejecución manual.

---

## Comandos útiles de desarrollo

```bash
# Shell interactiva dentro del contenedor de la API
docker compose exec api bash

# Reiniciar sólo la API sin reconstruir
docker compose restart api

# Reconstruir sólo la API (tras cambios en requirements.txt o Dockerfile)
docker compose up --build api -d

# Recrear la API para tomar cambios de api/.env
docker compose up -d --force-recreate api

# Uso de recursos de los contenedores
docker stats

# Liberar espacio de imágenes huérfanas
docker image prune -f
```

---

## Estructura del código

```
api/
├── app/
│   ├── __init__.py           # create_app() y registro de blueprints
│   ├── config.py             # Configuración desde variables de entorno
│   ├── extensions.py         # db (SQLAlchemy), jwt, mail, limiter, migrate
│   ├── mongo.py              # Conexión y acceso a MongoDB
│   ├── auth/routes.py        # Login, registro y recuperación de contraseña
│   ├── models/pg/            # Modelos relacionales (usuario, rol, gimnasio, ...)
│   ├── models/               # Modelos/documentos de MongoDB
│   ├── routes/
│   │   ├── miembro/          # Endpoints del miembro (dashboard, rutinas, salud, ...)
│   │   ├── entrenador/       # Endpoints del entrenador
│   │   ├── owner_gym/        # Endpoints del propietario del gimnasio
│   │   ├── recepcionista/    # Endpoints de recepción
│   │   ├── superadmin/       # Endpoints de plataforma
│   │   ├── admin/            # Reportes, catálogos, billing, notificaciones
│   │   ├── compartido/       # Membresías y notificaciones compartidas
│   │   └── ia/               # Módulos de IA (spark_*.py, scikit-learn)
│   ├── backups/              # Respaldo y restauración
│   └── utils/                # Seguridad, tenant, timezone, helpers
├── migrations/               # Migraciones Alembic (PostgreSQL)
├── Dockerfile                # Imagen (entrypoint corre migraciones + gunicorn)
├── wsgi.py                   # Punto de entrada de Gunicorn (producción)
├── run.py                    # Servidor de desarrollo
└── requirements.txt          # Dependencias Python
```

Nota: los archivos de `routes/ia/` mantienen el prefijo `spark_*` por razones
históricas, pero la implementación actual usa scikit-learn en proceso (sin JVM).

---

## Endpoints principales

Todas las rutas cuelgan de `/api`. Requieren cabecera `Authorization: Bearer <token>`
salvo las de autenticación y salud, y `X-Gym-ID` para el aislamiento por gimnasio.

| Método | Endpoint | Auth | Descripción |
|---|---|---|---|
| GET | `/api/health` | — | Estado del servicio |
| POST | `/api/auth/login` | — | Inicia sesión, devuelve JWT |
| POST | `/api/auth/register` | — | Registro de usuario |
| POST | `/api/auth/forgot-password` | — | Envía código de 6 dígitos por correo |
| POST | `/api/auth/reset-password` | — | Valida el código y cambia la contraseña |
| GET | `/api/user/dashboard` | JWT | Panel del miembro |
| GET/POST/PUT/DELETE | `/api/user/routines` | JWT | Rutinas propias del miembro (Mi Rutina) |
| GET | `/api/user/membership` | JWT | Membresía, planes y renovación |
| GET | `/api/trainer/dashboard` | JWT (Entrenador) | Panel del entrenador |
| GET | `/api/trainer/reports?range=...` | JWT (Entrenador) | Reportes de desempeño |
| GET | `/api/owner_gym/dashboard` | JWT (owner_gym) | Panel del gimnasio |
| GET/POST | `/api/analytics/cancelaciones` | JWT | Riesgo de abandono (Random Forest) |
| GET/POST | `/api/analytics/modelos` | JWT | Laboratorio de modelos |
| GET | `/api/analytics/regresion/predecir/<id>?dias=N` | JWT | Predicción de peso |
| GET | `/api/superadmin/gimnasios` | JWT (superadmin) | Gestión de la plataforma |

La referencia completa de endpoints por rol está en el Manual de Referencia de la API.

---

## Solución de problemas

`docker: command not found` en WSL
: Seguir la guía de instalación de Docker de arriba.

`Cannot connect to the Docker daemon`
: `sudo service docker start`

`permission denied` al correr docker sin sudo
: `sudo usermod -aG docker $USER && newgrp docker`

`error getting credentials ... docker-credential-desktop.exe`
: Ocurre si Docker Desktop dejó su credential helper configurado. Corrige
  `~/.docker/config.json` dejándolo como `{ "credStore": "" }` y prueba `docker run hello-world`.

La API no conecta a la base de datos
: Verifica que los contenedores `postgres` y `mongo` están arriba (`docker compose ps`)
  y que `MONGO_URI` / las variables `POSTGRES_*` en `api/.env` son coherentes con el
  compose. En desarrollo, MongoDB se expone en `localhost:27035` y PostgreSQL en
  `localhost:5433`.

No llegan los correos de recuperación
: `MAIL_PASSWORD` debe ser una App Password de Gmail (no la contraseña normal) y la
  cuenta debe tener verificación en dos pasos. Tras cambiar `.env`, recrea el
  contenedor: `docker compose up -d --force-recreate api`.

`ModuleNotFoundError` al iniciar
: Cambió `requirements.txt`. Reconstruir: `docker compose up --build api -d`.
