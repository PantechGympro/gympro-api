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

# ── Zona horaria ───────────────────────────────────────────────────────────
# Determina con qué fecha y hora se guardan ventas, pagos y asistencias.
# Sin ella el contenedor usa UTC y un movimiento nocturno queda registrado
# con la fecha del día siguiente. Nombre IANA.
APP_TIMEZONE=America/Mexico_City

# ── PostgreSQL (usuarios, roles, gimnasios, suscripciones) ─────────────────
# El compose provee estos valores por defecto; sólo cámbialos en producción.
POSTGRES_DB=gymprodb
POSTGRES_USER=gymuser
POSTGRES_PASSWORD=gympassword

# ── MongoDB (miembros, rutinas, progreso, ventas, pagos) ───────────────────
MONGO_URI=mongodb://mongo:27017/gymdb

# ── Redis (caché y límite de peticiones) ───────────────────────────────────
REDIS_URL=redis://redis:6379/0

# ── Email (recuperación de contraseña y avisos de respaldo) ────────────────
# Gmail exige una contraseña de aplicación de 16 letras, no la de la cuenta.
MAIL_SERVER=smtp.gmail.com
MAIL_PORT=587
MAIL_USE_TLS=True
MAIL_USERNAME=tu_email@gmail.com
MAIL_PASSWORD=tu_app_password_de_16_letras
MAIL_DEFAULT_SENDER=tu_email@gmail.com
MAIL_RECIPIENT=tu_email@gmail.com

# ── Pagos ──────────────────────────────────────────────────────────────────
# Cifra las credenciales que cada gimnasio guarda para cobrar. Generar con:
#   docker run --rm python:3.12-slim sh -c "pip install -q cryptography && \
#     python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())'"
PAYMENTS_ENCRYPTION_KEY=

# Credenciales de LA PLATAFORMA: con estas se cobran las suscripciones al SaaS.
# Las de cada gimnasio se configuran desde el portal, no aquí.
PLATAFORMA_PAYPAL_CLIENT_ID=
PLATAFORMA_PAYPAL_SECRET=
PLATAFORMA_MP_ACCESS_TOKEN=
PLATAFORMA_PAGOS_MODO=sandbox
PLATAFORMA_PAGOS_MONEDA=MXN

# ── URLs públicas ──────────────────────────────────────────────────────────
# A dónde vuelve el navegador tras pagar y con qué dirección se anuncia la API
# a las pasarelas. En desarrollo local los valores por defecto sirven; en un
# servidor hay que poner el dominio real con https.
FRONTEND_URL=http://localhost:8080
PUBLIC_API_URL=http://localhost:5000
# Orígenes autorizados para CORS, separados por coma.
ALLOWED_ORIGINS=http://localhost:3000,http://localhost:8080

# ── Otros ──────────────────────────────────────────────────────────────────
MONGO_DB=gymdb
# Cadena de conexión completa a PostgreSQL. El compose la sobreescribe con los
# valores de los servicios internos, así que solo importa fuera de Docker.
POSTGRES_URI=postgresql+psycopg2://gymuser:gympassword@postgres:5432/gymprodb
# Interruptor histórico del stack de analítica (el nombre viene de la etapa en
# que se usaba PySpark; hoy los módulos corren con scikit-learn en proceso).
# En false, los endpoints de /api/spark/* responden 503.
SPARK_ENABLED=true
```

Detalles del alta en PayPal y Mercado Pago:
[../doc/PAGOS_CONFIGURACION.md](../doc/PAGOS_CONFIGURACION.md)

Importante: como el compose usa `env_file`, tras cambiar `api/.env` hay que recrear el
contenedor para que tome los nuevos valores: `docker compose up -d --force-recreate api`.

### Comprobar el correo

`/auth/forgot-password` responde igual haya llegado el correo o no, para no revelar qué
direcciones están registradas. Por eso conviene probar el envío directamente:

```bash
docker compose exec api python -c "
from app import create_app
from app.extensions import mail
from flask_mail import Message
app = create_app()
with app.app_context():
    mail.send(Message('Prueba GymPro',
                      sender=app.config['MAIL_DEFAULT_SENDER'],
                      recipients=[app.config['MAIL_USERNAME']],
                      body='Si lees esto, el correo funciona.'))
    print('enviado')
"
```

Un error `535` significa que se usó la contraseña de la cuenta en lugar de una de
aplicación. Si el envío falla en producción, el motivo queda en
`docker compose logs api | grep forgot-password`.

### Comprobar la zona horaria

```bash
docker compose exec api python -c "from datetime import datetime; print(datetime.now())"
```

Debe imprimir la hora local, no UTC. Si sale UTC, la imagen no se reconstruyó tras
añadir `APP_TIMEZONE`: `docker compose up -d --build api`.

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
| POST | `/api/auth/login` | — | Inicia sesión; devuelve token de acceso y de refresco |
| POST | `/api/auth/refresh` | Refresco | Renueva el token de acceso sin volver a pedir contraseña |
| POST | `/api/auth/register` | — | Registro de usuario |
| POST | `/api/auth/forgot-password` | — | Envía código de 6 dígitos por correo |
| POST | `/api/auth/reset-password` | — | Valida el código y cambia la contraseña |
| GET | `/api/user/dashboard` | JWT | Panel del miembro |
| GET/POST/PUT/DELETE | `/api/user/routines` | JWT | Rutinas propias del miembro |
| GET | `/api/user/membership` | JWT | Membresía, planes y renovación |
| GET | `/api/user/training/trainers` | JWT | Entrenadores del gimnasio |
| GET | `/api/user/training/trainers/<id>` | JWT | Ficha pública con certificaciones |
| GET | `/api/trainer/dashboard` | JWT (Entrenador) | Panel del entrenador |
| GET/PUT | `/api/trainer/profile` | JWT (Entrenador) | Perfil y certificaciones con adjunto |
| GET | `/api/trainer/members?my_clients=1` | JWT (Entrenador) | Solo sus clientes asignados |
| GET/POST/PUT/DELETE | `/api/trainer/diets` · `/recipes` | JWT (Entrenador) | Planes y recetas |
| GET | `/api/owner_gym/dashboard` | JWT (owner_gym) | Panel del gimnasio |
| GET/PUT | `/api/owner_gym/perfil` | JWT (owner_gym) | Datos del gimnasio |
| PUT | `/api/owner_gym/perfil/propietario` | JWT (owner_gym) | Datos de la persona |
| GET/POST/PUT/DELETE | `/api/owner_gym/membresias` | JWT (owner_gym) | Tipos de membresía |
| GET/POST/PUT/DELETE | `/api/owner_gym/productos` | JWT (owner_gym) | Catálogo del punto de venta |
| GET | `/api/owner_gym/reportes/opciones` | JWT (owner_gym) | Años y secciones disponibles |
| GET | `/api/owner_gym/reportes/pdf` | JWT (owner_gym) | Reporte ejecutivo configurable |
| GET | `/api/pagos/todos?tipo=&anio=&mes=&page=` | JWT | Movimientos con filtro y paginación |
| GET | `/api/pagos/metodos` · `/metodos-plataforma` | JWT | Pasarelas del gimnasio y de la plataforma |
| POST | `/api/pagos/checkout` | JWT | Inicia el cobro con PayPal o Mercado Pago |
| GET | `/api/pagos/estado/<tx>` | JWT | Confirma el estado contra la pasarela |
| GET/POST | `/api/billing/suscripcion` · `/planes` · `/facturas` | JWT (owner_gym) | Suscripción al SaaS |
| GET/POST | `/api/analytics/cancelaciones` | JWT | Riesgo de abandono (Random Forest) |
| GET/POST | `/api/analytics/modelos` | JWT | Laboratorio de modelos |
| GET | `/api/analytics/regresion/predecir/<id>?dias=N` | JWT | Predicción de peso |
| GET | `/api/superadmin/gimnasios` | JWT (superadmin) | Gestión de la plataforma |

La referencia completa de endpoints por rol está en el Manual de Referencia de la API.

### Sesión y renovación

El acceso entrega dos tokens: uno de **acceso** con 8 horas de vigencia, que viaja en
cada petición, y uno de **refresco** de 90 días, que solo se usa contra
`/api/auth/refresh`. Cuando el primero caduca, el cliente pide uno nuevo con el segundo
y reintenta la petición, de modo que la sesión se comporta como permanente sin alargar
la vida del token que circula.

Al refrescar se releen el rol y el estado del usuario desde la base, así que dar de baja
a alguien o cambiarle el rol surte efecto en la siguiente renovación.

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
  contenedor: `docker compose up -d --force-recreate api`. Como el endpoint responde
  igual aunque falle el envío, usa la prueba directa del apartado de variables de
  entorno para ver el error real.

Las fechas se guardan con un día de diferencia
: Falta `APP_TIMEZONE` o la imagen no se reconstruyó tras añadirla. El contenedor estaría
  usando UTC, así que un movimiento nocturno queda con la fecha del día siguiente.
  `docker compose up -d --build api` y comprobar con
  `docker compose exec api python -c "from datetime import datetime; print(datetime.now())"`.

El contenedor arranca y muere diciendo que una tabla ya existe
: La base se creó sin Alembic, así que intenta aplicar las migraciones desde la primera.
  Marcar la revisión que corresponde al esquema actual sin ejecutarla:
  `docker compose exec api alembic stamp <revision>` y reiniciar.

`ModuleNotFoundError` al iniciar
: Cambió `requirements.txt`. Reconstruir: `docker compose up --build api -d`.
