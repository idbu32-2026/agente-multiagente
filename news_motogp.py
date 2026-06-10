"""Boletin automatico de novedades de MotoGP -> WhatsApp.

Igual que news_digest.py pero para MotoGP, y SIEMPRE como mensaje SEPARADO
del boletin de IA. Pensado para ejecutarse solo (Programador de tareas).

Uso manual:  .venv\\Scripts\\python.exe news_motogp.py
"""

from __future__ import annotations

import asyncio
import os
import sys

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPT_DIR)

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

from dotenv import load_dotenv

load_dotenv(os.path.join(SCRIPT_DIR, ".env"))

from claude_agent_sdk import ClaudeAgentOptions, query  # noqa: E402

from backend.notifier import notify  # noqa: E402

PROMPT = (
    "Busca en la web las novedades MAS RECIENTES sobre MotoGP (resultados de la "
    "ultima carrera, clasificacion del mundial, lesiones, fichajes, declaraciones "
    "y la proxima cita del calendario). Resumelo en ESPANOL como un boletin breve: "
    "de 4 a 6 puntos, cada uno una sola linea. Maximo 1000 caracteres en total. "
    "Empieza el mensaje con '🏍️ Novedades MotoGP de hoy:'. No incluyas enlaces largos."
)


async def main() -> None:
    options = ClaudeAgentOptions(
        model=os.getenv("NEWS_MODEL", "claude-sonnet-4-6"),
        allowed_tools=["WebSearch", "WebFetch"],
        permission_mode="dontAsk",
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
                    texto = t

    boletin = (texto or "").strip() or "No pude obtener novedades de MotoGP esta vez."
    boletin = boletin[:1500]
    resultado = await notify(boletin)
    print("Resultado del envio:", resultado)
    print("--- Boletin MotoGP enviado ---")
    print(boletin)


if __name__ == "__main__":
    asyncio.run(main())
