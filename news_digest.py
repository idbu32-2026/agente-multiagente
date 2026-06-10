"""Boletin automatico de novedades de IA -> WhatsApp.

Busca en la web las noticias mas recientes sobre inteligencia artificial,
las resume en espanol y te las envia por WhatsApp (Twilio). Pensado para
ejecutarse solo, de forma programada (Programador de tareas de Windows).

Uso manual:  .venv\\Scripts\\python.exe news_digest.py
"""

from __future__ import annotations

import asyncio
import os
import sys

# Hacer que el script funcione sin importar desde donde se ejecute (tarea programada).
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPT_DIR)

# La consola de Windows (cp1252) no maneja emojis; forzamos UTF-8 para no fallar.
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

from dotenv import load_dotenv

load_dotenv(os.path.join(SCRIPT_DIR, ".env"))

from claude_agent_sdk import ClaudeAgentOptions, query  # noqa: E402

from backend.notifier import notify  # noqa: E402

PROMPT = (
    "Busca en la web las novedades MAS RECIENTES sobre inteligencia artificial "
    "(modelos nuevos, lanzamientos, herramientas, noticias importantes de hoy o "
    "de esta semana). Resumelo en ESPANOL como un boletin breve: de 4 a 6 puntos, "
    "cada uno una sola linea con el titular y un dato clave. Maximo 1000 caracteres "
    "en total. Empieza el mensaje con '🤖 Novedades IA de hoy:'. No incluyas enlaces largos."
)


async def main() -> None:
    options = ClaudeAgentOptions(
        # Modelo mas economico para un boletin (suficiente para resumir noticias).
        model=os.getenv("NEWS_MODEL", "claude-sonnet-4-6"),
        allowed_tools=["WebSearch", "WebFetch"],  # solo lectura/busqueda
        permission_mode="dontAsk",  # nada fuera de lo permitido; sin interaccion humana
        cwd=SCRIPT_DIR,
        max_turns=10,
    )

    texto = ""
    async for message in query(prompt=PROMPT, options=options):
        content = getattr(message, "content", None)
        if content:
            for block in content:
                t = getattr(block, "text", None)
                if t:
                    texto = t  # nos quedamos con el ultimo texto (el resumen final)

    boletin = (texto or "").strip() or "No pude obtener novedades de IA esta vez."
    boletin = boletin[:1500]  # margen de seguridad para el limite de WhatsApp
    resultado = await notify(boletin)
    print("Resultado del envio:", resultado)
    print("--- Boletin enviado ---")
    print(boletin)


if __name__ == "__main__":
    asyncio.run(main())
