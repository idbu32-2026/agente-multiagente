"""Proceso aparte que dice UNA frase por voz y termina.

Se lanza desde Voz.decir() en jarvis_voz.py. Aislar cada frase en su propio
proceso evita el fallo de pyttsx3 que enmudece al reutilizar el motor dentro
del bucle principal (en proceso suelto, como aqui, siempre suena).

Uso (lo invoca el programa, no tu a mano):
    python voz/_tts_worker.py <rate> <voice_id>   # el texto entra por stdin (UTF-8)
"""
import sys


def main() -> None:
    rate = int(sys.argv[1]) if len(sys.argv) > 1 else 185
    voice_id = sys.argv[2] if len(sys.argv) > 2 else ""

    # El texto llega por stdin en UTF-8 (asi no se rompen acentos ni signos).
    texto = sys.stdin.buffer.read().decode("utf-8", "replace").strip()
    if not texto:
        return

    import pyttsx3
    engine = pyttsx3.init()
    engine.setProperty("rate", rate)
    if voice_id:
        engine.setProperty("voice", voice_id)
    engine.say(texto)
    engine.runAndWait()


if __name__ == "__main__":
    main()
