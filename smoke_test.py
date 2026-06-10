"""Prueba de humo: verifica que el orquestador arranca tras el fix del thinking.

Conecta al WebSocket de la app EN PROCESO (sin levantar servidor), envia un
objetivo trivial y comprueba que llegan mensajes del agente sin el error 400
de `budget_tokens`. Uso:  .venv\\Scripts\\python.exe smoke_test.py
"""

from __future__ import annotations

import json
import sys

sys.stdout.reconfigure(encoding="utf-8")

from starlette.testclient import TestClient

from backend.main import app

GOAL = (
    "Responde solo con la palabra 'FUNCIONO'. No uses ninguna herramienta, "
    "no delegues en subagentes, no investigues. Solo responde y termina."
)


def main() -> int:
    client = TestClient(app)
    eventos: list[dict] = []
    with client.websocket_connect("/ws") as ws:
        ws.send_json({"type": "goal", "text": GOAL})
        while True:
            data = ws.receive_json()
            eventos.append(data)
            tipo = data.get("type")
            if tipo == "agent":
                print(f"[agente] {data.get('text', '')[:120]}")
            elif tipo == "error":
                print(f"[ERROR] {data.get('text')}")
            elif tipo == "result":
                print(f"[coste] ${data.get('cost_usd', 0):.4f}")
            if tipo == "done":
                break

    errores = [e for e in eventos if e.get("type") == "error"]
    respuestas = [e for e in eventos if e.get("type") == "agent"]
    if errores:
        print("\nRESULTADO: FALLO -", errores[0].get("text", ""))
        return 1
    if not respuestas:
        print("\nRESULTADO: FALLO - el agente no respondio nada")
        return 1
    print("\nRESULTADO: OK - el orquestador funciona tras el fix")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
