"""Prueba de Vosk como detector de 'jarvis': que transcribe en una grabacion.

Pasa ultima_grabacion.wav (o el WAV que se le de) por el reconocedor espanol
e imprime cada resultado parcial y final. Sirve para decidir QUE patron debe
buscar el portero de la palabra clave (el modelo solo escribe palabras de su
vocabulario: hay que ver como escribe el 'jarvis' espanol de Luis).

Uso: .venv\\Scripts\\python.exe voz\\prueba_vosk.py [ruta.wav]
"""
from __future__ import annotations

import json
import sys
import wave
from pathlib import Path

from vosk import KaldiRecognizer, Model, SetLogLevel

SetLogLevel(-1)  # silencia el log interno de vosk

AQUI = Path(__file__).resolve().parent
MODELO = AQUI / "modelos" / "vosk-model-small-es-0.42"


def main() -> None:
    ruta = Path(sys.argv[1]) if len(sys.argv) > 1 else AQUI / "ultima_grabacion.wav"
    print(f"WAV: {ruta}")
    rec = KaldiRecognizer(Model(str(MODELO)), 16000)

    with wave.open(str(ruta), "rb") as w:
        assert w.getframerate() == 16000 and w.getnchannels() == 1, "se espera WAV 16kHz mono"
        ultimo_parcial = ""
        while True:
            datos = w.readframes(4000)
            if not datos:
                break
            if rec.AcceptWaveform(datos):
                texto = json.loads(rec.Result()).get("text", "")
                if texto:
                    print(f"FINAL  : {texto!r}")
            else:
                parcial = json.loads(rec.PartialResult()).get("partial", "")
                if parcial and parcial != ultimo_parcial:
                    print(f"parcial: {parcial!r}")
                    ultimo_parcial = parcial
        texto = json.loads(rec.FinalResult()).get("text", "")
        if texto:
            print(f"FINAL  : {texto!r}")


if __name__ == "__main__":
    main()
