"""Prueba del cerebro de JARVIS sin microfono (herramienta de diagnostico).

Abre EXACTAMENTE el mismo cerebro que usa el modo charla por voz y le hace una
pregunta por texto, imprimiendo cada mensaje del SDK con el tiempo transcurrido.
Asi se ve si busca en la web, si la herramienta se deniega o si se cuelga.

Uso:
    .venv\\Scripts\\python.exe voz\\prueba_cerebro.py "tu pregunta"
    (sin argumentos pregunta por el tiempo de manana, que obliga a buscar)
"""
from __future__ import annotations

import asyncio
import sys
import time

from jarvis_voz import Cerebro, _consola_utf8, _guardar_en_memoria

TIMEOUT_S = 180  # si en 3 minutos no ha terminado, esta colgado seguro


async def _probar() -> None:
    pregunta = " ".join(sys.argv[1:]) or "Que tiempo va a hacer manana?"
    print(f"PREGUNTA: {pregunta}", flush=True)

    t0 = time.time()
    cerebro = Cerebro()
    await cerebro.abrir()
    print(f"[{time.time() - t0:6.1f}s] cerebro abierto", flush=True)

    await cerebro._client.query(pregunta)
    respuesta = ""
    async for message in cerebro._client.receive_response():
        marca = f"[{time.time() - t0:6.1f}s] {type(message).__name__}"
        detalles = []
        for block in getattr(message, "content", None) or []:
            nombre = getattr(block, "name", None)       # ToolUseBlock
            if nombre:
                detalles.append(f"HERRAMIENTA: {nombre} {getattr(block, 'input', '')}")
            texto = getattr(block, "text", None)        # TextBlock
            if texto:
                detalles.append(f"TEXTO: {texto[:120]!r}")
                respuesta = texto
            if getattr(block, "is_error", False):       # ToolResultBlock con error
                detalles.append(f"ERROR HERRAMIENTA: {str(getattr(block, 'content', ''))[:200]}")
        # Resultado final del SDK (incluye errores de permiso/limites).
        for attr in ("subtype", "result", "total_cost_usd"):
            v = getattr(message, attr, None)
            if v is not None:
                detalles.append(f"{attr}={str(v)[:200]}")
        print(marca + ("  " + " | ".join(detalles) if detalles else ""), flush=True)

    print(f"\nRESPUESTA FINAL ({time.time() - t0:.1f}s): {respuesta}", flush=True)
    # Igual que Cerebro.preguntar(): el turno queda en la memoria persistente
    # (esta prueba usa el cliente directo para ver el detalle, y sin esto se
    # saltaba el guardado — el JARVIS real por voz si guarda siempre).
    if respuesta:
        _guardar_en_memoria(pregunta, respuesta)
    await cerebro.cerrar()


if __name__ == "__main__":
    _consola_utf8()  # que un emoji del cerebro no tumbe la consola cp1252
    try:
        asyncio.run(asyncio.wait_for(_probar(), timeout=TIMEOUT_S))
    except asyncio.TimeoutError:
        print(f"\nCOLGADO: sin respuesta tras {TIMEOUT_S}s — cuelgue confirmado.", flush=True)
        sys.exit(2)
