#!/usr/bin/env python3
"""
UT-01 · Prueba unitaria de resolución de la hora de captura.

Ejercita resolver_capturado_en() (gympro-api/app/utils/sync.py) con los
8 casos descritos en el Documento de Casos de Prueba, caso UT-01, usando
una hora de servidor simulada fija: 2026-11-05T20:30:00.

Uso:
    python3 prueba_capturado_en.py ruta/a/gympro-api
"""
import sys
import os
from datetime import datetime

def main():
    if len(sys.argv) != 2:
        print("Uso: python3 prueba_capturado_en.py ruta/a/gympro-api")
        sys.exit(2)

    repo_path = os.path.abspath(sys.argv[1])
    if not os.path.isdir(os.path.join(repo_path, "app", "utils")):
        print(f"ERROR: no se encontró app/utils dentro de {repo_path}")
        sys.exit(2)

    # Se carga app/utils/sync.py directamente por ruta (importlib), sin pasar
    # por el paquete `app` (cuyo __init__ arrastra Flask y otras dependencias
    # del servicio completo). sync.py solo depende de datetime, así que esto
    # basta y evita requerir un entorno virtual con todas las dependencias
    # del proyecto solo para esta prueba unitaria.
    import importlib.util
    module_path = os.path.join(repo_path, "app", "utils", "sync.py")
    if not os.path.isfile(module_path):
        print(f"ERROR: no existe {module_path}")
        sys.exit(2)
    try:
        spec = importlib.util.spec_from_file_location("sync_bajo_prueba", module_path)
        sync_module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(sync_module)
        resolver_capturado_en = sync_module.resolver_capturado_en
    except Exception as exc:
        print(f"ERROR: no se pudo importar resolver_capturado_en desde {module_path}: {type(exc).__name__}: {exc}")
        sys.exit(1)

    print(f"Script arrancó correctamente. Repositorio: {repo_path}")
    print(f"Python: {sys.version.split()[0]}")

    ahora = datetime(2026, 11, 5, 20, 30, 0)
    print(f"Hora de servidor simulada (ahora): {ahora.isoformat()}\n")

    # Valor esperado para el caso con sufijo Z: se calcula dinámicamente
    # convirtiendo la marca UTC a la hora local del sistema donde corre
    # este script, igual que hace resolver_capturado_en().
    esperado_z = datetime.fromisoformat("2026-11-05T13:15:00+00:00").astimezone().replace(tzinfo=None)

    casos = [
        {
            "id": 1,
            "desc": "sin campo (_capturado_en ausente)",
            "data": {},
            "esperado": ahora,
            "regla": "cae a la hora del servidor",
        },
        {
            "id": 2,
            "desc": "13 horas antes (mismo día): 2026-11-05T07:15:00",
            "data": {"_capturado_en": "2026-11-05T07:15:00"},
            "esperado": datetime(2026, 11, 5, 7, 15, 0),
            "regla": "fecha resuelta idéntica a la enviada",
        },
        {
            "id": 3,
            "desc": "3 días antes: 2026-11-02T06:40:00",
            "data": {"_capturado_en": "2026-11-02T06:40:00"},
            "esperado": datetime(2026, 11, 2, 6, 40, 0),
            "regla": "fecha resuelta idéntica a la enviada (sin límite de antigüedad)",
        },
        {
            "id": 4,
            "desc": "zona horaria Z (UTC): 2026-11-05T13:15:00Z",
            "data": {"_capturado_en": "2026-11-05T13:15:00Z"},
            "esperado": esperado_z,
            "regla": "se convierte a hora local y se conserva (no se descarta)",
        },
        {
            "id": 5,
            "desc": "reloj adelantado 2 horas: 2026-11-05T22:30:00",
            "data": {"_capturado_en": "2026-11-05T22:30:00"},
            "esperado": ahora,
            "regla": "valor futuro fuera de tolerancia -> cae a la hora del servidor",
        },
        {
            "id": 6,
            "desc": "reloj adelantado 3 minutos (dentro de tolerancia): 2026-11-05T20:33:00",
            "data": {"_capturado_en": "2026-11-05T20:33:00"},
            "esperado": datetime(2026, 11, 5, 20, 33, 0),
            "regla": "dentro de la tolerancia de 5 min -> se conserva, no se descarta",
        },
        {
            "id": 7,
            "desc": 'formato inválido: "05/11/2026 7:15 am"',
            "data": {"_capturado_en": "05/11/2026 7:15 am"},
            "esperado": ahora,
            "regla": "formato no parseable -> cae a la hora del servidor, sin excepción",
        },
        {
            "id": 8,
            "desc": "campo vacío: \"\"",
            "data": {"_capturado_en": ""},
            "esperado": ahora,
            "regla": "cadena vacía -> cae a la hora del servidor, sin excepción",
        },
    ]

    conformes = 0
    resultados = []
    for caso in casos:
        try:
            obtenido = resolver_capturado_en(caso["data"], ahora=ahora)
            error = None
        except Exception as exc:  # no debe lanzar excepción nunca
            obtenido = None
            error = f"{type(exc).__name__}: {exc}"

        ok = (error is None) and (obtenido == caso["esperado"])
        conformes += 1 if ok else 0
        resultados.append({**caso, "obtenido": obtenido, "error": error, "ok": ok})

        marca = "✓" if ok else "✗"
        print(f"Caso {caso['id']}: {caso['desc']}")
        print(f"  Regla esperada : {caso['regla']}")
        print(f"  Esperado       : {caso['esperado'].isoformat()}")
        if error:
            print(f"  Obtenido       : EXCEPCIÓN -> {error}")
        else:
            print(f"  Obtenido       : {obtenido.isoformat()}")
        print(f"  Conforme       : {marca}\n")

    print(f"El conteo reporta {conformes} de {len(casos)} casos conformes.")
    if conformes == len(casos):
        print("RESULTADO: APROBADA")
    else:
        print("RESULTADO: FALLIDA")

    return 0 if conformes == len(casos) else 1


if __name__ == "__main__":
    sys.exit(main())