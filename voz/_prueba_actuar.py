"""Prueba de integracion del modo ACTUAR (sin microfono).

Simula el permiso por voz con un callback de texto que registra que paso por el
checkpoint. Verifica:
  CASO 1: crear archivo DENTRO del proyecto -> se auto-aprueba (0 permisos).
  CASO 2: ejecutar un comando Bash           -> pide permiso (1 permiso).

Hace 2 llamadas reales al modelo y crea/borra un archivo temporal.
"""
import asyncio
from pathlib import Path

from voz.jarvis_voz import CerebroActuador, PROJECT_DIR

llamadas: list[tuple[str, dict]] = []


async def ask_human_falso(tool_name: str, input_data: dict) -> dict:
    detalle = input_data.get("command") or input_data.get("file_path") or input_data
    print(f"[PERMISO PEDIDO] {tool_name} -> {detalle}")
    llamadas.append((tool_name, input_data))
    return {"approved": True, "reason": "Aprobado en la prueba"}


async def main() -> None:
    archivo = Path(PROJECT_DIR) / "prueba_jarvis_temp.txt"
    if archivo.exists():
        archivo.unlink()

    cerebro = await CerebroActuador(ask_human_falso).abrir()
    creado = False
    permisos_caso1 = 0
    pidio_permiso_bash = False
    try:
        print("\n--- CASO 1: crear archivo DENTRO del proyecto (no debe pedir permiso) ---")
        r1 = await cerebro.preguntar(
            "Crea un archivo llamado prueba_jarvis_temp.txt en la carpeta del "
            "proyecto, con el texto: hola jarvis. Usa la herramienta de escritura "
            "(Write). Responde solo 'listo' al terminar."
        )
        print("JARVIS:", r1)
        creado = archivo.exists()
        permisos_caso1 = len(llamadas)
        print(f"Archivo creado: {creado}  |  permisos pedidos: {permisos_caso1} (esperado 0)")

        print("\n--- CASO 2: ejecutar comando Bash (debe pedir permiso) ---")
        antes = len(llamadas)
        # Salida IMPOSIBLE de adivinar: obliga al modelo a ejecutar Bash de verdad
        # en vez de inventarse el resultado.
        r2 = await cerebro.preguntar(
            "Usa la herramienta Bash para ejecutar exactamente este comando y dime "
            "el numero EXACTO que imprima: "
            "python -c \"import random; print(random.randint(100000,999999))\""
        )
        print("JARVIS:", r2)
        pidio_permiso_bash = len(llamadas) > antes
        print(f"Pidio permiso para Bash: {pidio_permiso_bash} (esperado True)")
    finally:
        await cerebro.cerrar()
        if archivo.exists():
            archivo.unlink()
            print("\n(limpieza: archivo temporal borrado)")

    print("\n=== RESULTADO ===")
    ok1 = creado and permisos_caso1 == 0
    ok2 = pidio_permiso_bash
    print(f"CASO 1 (escribe dentro sin permiso): {'OK' if ok1 else 'FALLO'}")
    print(f"CASO 2 (Bash pide permiso):          {'OK' if ok2 else 'FALLO'}")
    print("TODO OK" if ok1 and ok2 else "REVISAR: algo no cuadra")


if __name__ == "__main__":
    asyncio.run(main())
