"""Proceso aparte que dice UNA frase por voz y termina.

Se lanza desde Voz.decir() en jarvis_voz.py. Aislar cada frase en su propio
proceso evita el fallo de pyttsx3 que enmudece al reutilizar el motor dentro
del bucle principal (en proceso suelto siempre suena).

Voz principal (desde 2026-06-11): NEURAL de Microsoft via edge-tts —
es-ES-AlvaroNeural (masculina, muy natural). Necesita internet; si falla
(sin red, servicio capado), cae SOLO a pyttsx3 (robotica) para que JARVIS
nunca se quede mudo.

Cache: cada frase sintetizada se guarda en voz/cache_voz/<hash>.mp3 y se
reutiliza — las frases fijas ("¿Sí?", "Dame unos segundos...") suenan al
instante y sin gastar red. Solo se ANADE al cache, nunca se borra.

Uso (lo invoca el programa, no tu a mano):
    python voz/_tts_worker.py <rate> <voice_id>   # el texto entra por stdin (UTF-8)
"""
import hashlib
import os
import sys
from pathlib import Path

CACHE_DIR = Path(__file__).resolve().parent / "cache_voz"
VOZ_NEURAL = os.getenv("JARVIS_VOZ_NEURAL", "es-ES-AlvaroNeural")


def _reproducir_mp3(ruta: str) -> None:
    """Reproduce un mp3 con MCI de Windows (winmm): sin ventanas ni programas."""
    import ctypes

    mci = ctypes.windll.winmm.mciSendStringW
    alias = "jarvis_tts"
    if mci(f'open "{ruta}" type mpegvideo alias {alias}', None, 0, None) != 0:
        raise RuntimeError("MCI no pudo abrir el mp3")
    try:
        mci(f"play {alias} wait", None, 0, None)  # bloquea hasta terminar
    finally:
        mci(f"close {alias}", None, 0, None)


def _decir_neural(texto: str) -> None:
    """Voz Alvaro (edge-tts). Sintetiza (o coge del cache) y reproduce."""
    CACHE_DIR.mkdir(exist_ok=True)
    clave = hashlib.md5(f"{VOZ_NEURAL}|{texto}".encode("utf-8")).hexdigest()
    mp3 = CACHE_DIR / f"{clave}.mp3"
    if not mp3.exists() or mp3.stat().st_size == 0:
        import asyncio

        import edge_tts

        asyncio.run(edge_tts.Communicate(texto, VOZ_NEURAL).save(str(mp3)))
    _reproducir_mp3(str(mp3))


def _decir_robotico(texto: str, rate: int, voice_id: str) -> None:
    """Respaldo local sin internet: pyttsx3 (la voz robotica de siempre)."""
    import pyttsx3

    engine = pyttsx3.init()
    engine.setProperty("rate", rate)
    if voice_id:
        engine.setProperty("voice", voice_id)
    engine.say(texto)
    engine.runAndWait()


def main() -> None:
    rate = int(sys.argv[1]) if len(sys.argv) > 1 else 185
    voice_id = sys.argv[2] if len(sys.argv) > 2 else ""

    # El texto llega por stdin en UTF-8 (asi no se rompen acentos ni signos).
    texto = sys.stdin.buffer.read().decode("utf-8", "replace").strip()
    if not texto:
        return

    try:
        _decir_neural(texto)
    except Exception as e:
        print(f"[tts] Voz neural no disponible ({e}); uso la de respaldo.", file=sys.stderr)
        _decir_robotico(texto, rate, voice_id)


if __name__ == "__main__":
    main()
