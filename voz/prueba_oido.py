"""Prueba de OIDO completa: graba, puntua el wake word Y transcribe con Whisper.

Sirve para separar dos averias que por fuera se ven igual:
  - Si la TRANSCRIPCION sale bien pero el wake word puntua bajo -> el audio
    llega sano y el problema es el detector (modelo/umbral/pronunciacion).
  - Si la transcripcion sale rara o vacia -> el audio llega roto (muestreo,
    driver, micro equivocado) y el detector es victima, no culpable.

Uso:  .venv\\Scripts\\python.exe voz\\prueba_oido.py [segundos]
Mientras corre: di "hey jarvis" dos veces y luego una frase normal.
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

import numpy as np

from jarvis_voz import (
    FRAME_LENGTH,
    WAKE_MODEL,
    WAKE_UMBRAL,
    _cargar_wakeword,
    _cargar_whisper,
    _consola_utf8,
    _elegir_microfono,
    _transcribir,
)


def main() -> None:
    _consola_utf8()
    segundos = int(sys.argv[1]) if len(sys.argv) > 1 else 20
    from pvrecorder import PvRecorder

    oww = _cargar_wakeword()
    whisper = _cargar_whisper()
    recorder = PvRecorder(device_index=_elegir_microfono(), frame_length=FRAME_LENGTH)
    recorder.start()
    print(f"GRABANDO {segundos}s: di 'hey jarvis' dos veces y luego una frase...", flush=True)

    audio: list[int] = []
    mejor = 0.0
    fin = time.time() + segundos
    try:
        while time.time() < fin:
            frame = recorder.read()
            audio.extend(frame)
            puntuacion = oww.predict(np.array(frame, dtype=np.int16)).get(WAKE_MODEL, 0.0)
            mejor = max(mejor, float(puntuacion))
    finally:
        recorder.stop()
        recorder.delete()

    print(f"MEJOR PUNTUACION WAKE WORD: {mejor:.2f} (dispara con >= {WAKE_UMBRAL})", flush=True)

    # Guarda la grabacion en un WAV y lo abre: que el usuario ESCUCHE lo que
    # oye JARVIS es el diagnostico mas fiable (voz normal vs deformada).
    import wave
    ruta_wav = Path(__file__).resolve().parent / "ultima_grabacion.wav"
    with wave.open(str(ruta_wav), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)
        w.writeframes(np.array(audio, dtype=np.int16).tobytes())
    print(f"GRABACION GUARDADA: {ruta_wav}", flush=True)

    # Analisis numerico de la zona mas sonora: un tono fundamental de voz
    # normal ronda 80-250 Hz; si sale muy fuera, el audio llega deformado.
    arr = np.array(audio, dtype="float32")
    ventana = 16000  # 1 segundo
    if len(arr) > ventana:
        energias = [float(np.sum(np.square(arr[i:i + ventana]))) for i in range(0, len(arr) - ventana, ventana)]
        mejor_seg = int(np.argmax(energias)) * ventana
        trozo = arr[mejor_seg:mejor_seg + ventana]
        trozo = trozo - trozo.mean()
        ac = np.correlate(trozo, trozo, mode="full")[ventana:]
        zona = ac[40:400]  # 40..400 muestras = 400..40 Hz a 16 kHz
        f0 = 16000.0 / (40 + int(np.argmax(zona)))
        print(f"TONO FUNDAMENTAL del segundo mas sonoro: ~{f0:.0f} Hz "
              "(voz normal: 80-250 Hz)", flush=True)

    print("Transcribiendo lo grabado con Whisper...", flush=True)
    print(f"TRANSCRIPCION: {_transcribir(whisper, audio)!r}", flush=True)
    try:
        os.startfile(ruta_wav)  # que el usuario escuche lo que oyo JARVIS
    except Exception:
        pass


if __name__ == "__main__":
    main()
