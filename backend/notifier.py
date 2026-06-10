"""Notificaciones por WhatsApp via Twilio (opcional).

Si no hay credenciales en el entorno, las notificaciones se desactivan
silenciosamente y el sistema sigue funcionando solo con la web. Asi el proyecto
no se rompe si todavia no has dado de alta tu cuenta de Twilio.

Variables de entorno (ver .env.example):
  TWILIO_ACCOUNT_SID    -> de tu consola de Twilio
  TWILIO_AUTH_TOKEN     -> de tu consola de Twilio (secreto)
  TWILIO_WHATSAPP_FROM  -> numero del sandbox, p.ej. whatsapp:+14155238886
  WHATSAPP_TO           -> tu numero en formato E.164, p.ej. whatsapp:+34600111222
"""

from __future__ import annotations

import asyncio
import os


def _config() -> tuple[str | None, str | None, str, str | None]:
    return (
        os.getenv("TWILIO_ACCOUNT_SID"),
        os.getenv("TWILIO_AUTH_TOKEN"),
        os.getenv("TWILIO_WHATSAPP_FROM", "whatsapp:+14155238886"),
        os.getenv("WHATSAPP_TO"),
    )


def is_configured() -> bool:
    sid, token, _from, to = _config()
    return bool(sid and token and to)


def _with_prefix(number: str) -> str:
    return number if number.startswith("whatsapp:") else f"whatsapp:{number}"


def _send_blocking(body: str) -> dict:
    """Envia un WhatsApp (llamada HTTP sincrona de Twilio)."""
    sid, token, from_, to = _config()
    if not (sid and token and to):
        return {"sent": False, "reason": "WhatsApp no configurado (.env incompleto)"}
    try:
        from twilio.rest import Client
    except ImportError:
        return {"sent": False, "reason": "Falta el paquete 'twilio' (pip install twilio)"}
    try:
        client = Client(sid, token)
        msg = client.messages.create(
            body=body,
            from_=_with_prefix(from_),
            to=_with_prefix(to),
        )
        return {"sent": True, "sid": msg.sid}
    except Exception as exc:  # noqa: BLE001 - reportar sin romper el agente
        return {"sent": False, "reason": f"{type(exc).__name__}: {exc}"}


async def notify(body: str) -> dict:
    """Version async: ejecuta el envio en un hilo para no bloquear el servidor."""
    return await asyncio.to_thread(_send_blocking, body)
