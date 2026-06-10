"""Servidor web del sistema multiagente.

Expone:
  - GET /            -> sirve la interfaz web (frontend/index.html)
  - WS  /ws          -> canal bidireccional: el navegador envia el objetivo y
                        responde a los checkpoints; el servidor transmite los
                        mensajes del agente y las solicitudes de aprobacion.

Protocolo de mensajes WebSocket (JSON):
  Navegador -> servidor:
    {"type": "goal", "text": "..."}
    {"type": "approval_response", "id": "...", "approved": true, "reason": "..."}
  Servidor -> navegador:
    {"type": "agent", "role": "...", "text": "..."}
    {"type": "tool", "name": "...", "input": {...}}
    {"type": "approval_request", "id": "...", "tool": "...", "input": {...}}
    {"type": "result", "cost_usd": 0.0}
    {"type": "error", "text": "..."}
    {"type": "done"}
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path

from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, Response

# Carga variables de .env (modelo, credenciales de Twilio, etc.) si existe.
try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:  # python-dotenv no instalado: se usan las env del sistema
    pass

from .approvals import ApprovalBroker
from .notifier import notify
from .orchestrator import build_options

FRONTEND = Path(__file__).resolve().parent.parent / "frontend" / "index.html"

app = FastAPI(title="Sistema Multiagente (Claude Agent SDK)")


@app.get("/")
async def index() -> FileResponse:
    return FileResponse(FRONTEND)


async def _answer_whatsapp(text: str) -> None:
    """Responde por WhatsApp a un mensaje entrante usando la IA."""
    from claude_agent_sdk import ClaudeAgentOptions, query

    options = ClaudeAgentOptions(
        model=os.getenv("CHAT_MODEL", "claude-sonnet-4-6"),
        system_prompt=(
            "Eres el asistente personal de WhatsApp del usuario. Responde en "
            "espanol, breve y claro (maximo 1000 caracteres). Si te preguntan por "
            "noticias o datos actuales (IA, MotoGP, etc.), busca en la web."
        ),
        allowed_tools=["WebSearch", "WebFetch"],
        permission_mode="dontAsk",  # headless: sin interaccion humana
        cwd=str(Path(__file__).resolve().parent.parent),
        max_turns=8,
    )

    respuesta = ""
    async for message in query(prompt=text, options=options):
        content = getattr(message, "content", None)
        if content:
            for block in content:
                t = getattr(block, "text", None)
                if t:
                    respuesta = t

    respuesta = (respuesta or "Ahora mismo no puedo responder, intentalo de nuevo.").strip()[:1500]
    await notify(respuesta)


@app.post("/whatsapp")
async def whatsapp_webhook(request: Request) -> Response:
    """Buzon de entrada: Twilio envia aqui los WhatsApp que tu escribes.

    Responde 200 al instante (para no agotar el tiempo de espera de Twilio) y
    procesa la respuesta de la IA en segundo plano, enviandola por WhatsApp.
    """
    form = await request.form()
    body = (form.get("Body") or "").strip()
    sender = (form.get("From") or "").strip()

    # SEGURIDAD: solo atendemos mensajes de TU numero (WHATSAPP_TO). Sin esto,
    # cualquiera que escribiera al sandbox podria usar tu IA y gastar tu saldo.
    allowed = (os.getenv("WHATSAPP_TO") or "").strip()
    if allowed and not allowed.startswith("whatsapp:"):
        allowed = f"whatsapp:{allowed}"

    if body and allowed and sender == allowed:
        asyncio.create_task(_answer_whatsapp(body))
    elif body:
        print(f"[whatsapp] mensaje ignorado de remitente no autorizado: {sender}", flush=True)
    # TwiML vacio: confirmamos recepcion; la respuesta real va aparte.
    return Response(content="<Response></Response>", media_type="application/xml")


def _message_to_events(message: object) -> list[dict]:
    """Traduce un mensaje del SDK a eventos simples para la UI.

    Se hace de forma defensiva (hasattr/getattr) para no acoplarse a cambios
    menores de los dataclasses del SDK.
    """
    events: list[dict] = []
    content = getattr(message, "content", None)
    if content is not None:
        for block in content:
            text = getattr(block, "text", None)
            if text:
                events.append({"type": "agent", "role": "asistente", "text": text})
                continue
            tool_name = getattr(block, "name", None)
            if tool_name:
                events.append(
                    {
                        "type": "tool",
                        "name": tool_name,
                        "input": getattr(block, "input", {}),
                    }
                )
    # ResultMessage trae el coste total.
    cost = getattr(message, "total_cost_usd", None)
    if cost is not None:
        events.append({"type": "result", "cost_usd": cost})
    return events


async def _run_agent(ws: WebSocket, broker: ApprovalBroker, goal: str) -> None:
    """Ejecuta el orquestador para un objetivo y transmite su salida."""
    from claude_agent_sdk import ClaudeSDKClient

    async def ask_human(tool_name: str, input_data: dict) -> dict:
        approval_id, future = broker.create()
        print(f"[ask_human] enviando modal de aprobacion para {tool_name} (id={approval_id})", flush=True)
        await ws.send_json(
            {
                "type": "approval_request",
                "id": approval_id,
                "tool": tool_name,
                "input": input_data,
            }
        )
        # AVISO 1: el agente necesita tu aprobacion -> WhatsApp (sin bloquear).
        asyncio.create_task(
            notify(
                f"⏸️ Aprobacion necesaria.\n"
                f"El agente quiere usar la herramienta '{tool_name}'.\n"
                f"Abre http://127.0.0.1:8000 para aprobar o denegar."
            )
        )
        return await future  # se resuelve cuando el navegador responde

    options = build_options(ask_human)

    last_text = ""
    try:
        # ClaudeSDKClient mantiene la conexion abierta en ambos sentidos, que es
        # lo que necesita can_use_tool para poder pedir aprobacion. Con query()
        # de un solo mensaje el canal se cerraba antes de tiempo ("Stream closed").
        async with ClaudeSDKClient(options=options) as client:
            await client.query(goal)
            async for message in client.receive_response():
                for event in _message_to_events(message):
                    if event["type"] == "agent":
                        last_text = event["text"]
                    await ws.send_json(event)
    except Exception as exc:  # noqa: BLE001 - reportar cualquier fallo a la UI
        try:
            await ws.send_json({"type": "error", "text": f"{type(exc).__name__}: {exc}"})
        except Exception:
            pass  # navegador cerrado: el aviso por WhatsApp va igualmente
        await notify(f"❌ La tarea fallo: {type(exc).__name__}: {exc}")
    finally:
        # El navegador puede haberse cerrado: no dejar que eso impida el aviso
        # por WhatsApp de abajo.
        try:
            await ws.send_json({"type": "done"})
        except Exception:
            pass
        # AVISO 2: tarea terminada -> WhatsApp con un resumen breve.
        resumen = (last_text[:300] + "…") if len(last_text) > 300 else last_text
        await notify(
            f"✅ Tarea terminada.\nObjetivo: {goal[:80]}\n\n{resumen}".strip()
        )


@app.websocket("/ws")
async def ws_endpoint(ws: WebSocket) -> None:
    await ws.accept()
    broker = ApprovalBroker()
    agent_task: asyncio.Task | None = None
    try:
        while True:
            data = await ws.receive_json()
            kind = data.get("type")

            if kind == "goal":
                if agent_task and not agent_task.done():
                    await ws.send_json(
                        {"type": "error", "text": "Ya hay una tarea en curso."}
                    )
                    continue
                goal = (data.get("text") or "").strip()
                if not goal:
                    continue
                agent_task = asyncio.create_task(_run_agent(ws, broker, goal))

            elif kind == "approval_response":
                broker.resolve(
                    data.get("id", ""),
                    {
                        "approved": bool(data.get("approved")),
                        "reason": data.get("reason", ""),
                    },
                )
    except WebSocketDisconnect:
        broker.cancel_all()
        if agent_task and not agent_task.done():
            agent_task.cancel()


def main() -> None:
    import uvicorn

    host = os.getenv("HOST", "127.0.0.1")
    port = int(os.getenv("PORT", "8000"))
    uvicorn.run("backend.main:app", host=host, port=port, reload=False)


if __name__ == "__main__":
    main()
