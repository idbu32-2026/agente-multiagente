"""Prueba de OIDO de JARVIS: que llega del micro y que puntua el wake word.

Escucha unos 75 segundos. Mientras corre, di "hey jarvis" varias veces con
pausas. Cada segundo imprime:
  - crudo:  nivel real del micro (RMS sin amplificar)
  - ampli:  nivel tras la ganancia JARVIS_GAIN (lo que oye el detector)
  - recorte: % de muestras saturadas por la ganancia (distorsion)
  - hey_jarvis: puntuacion 0..1 del detector (dispara al pasar el umbral)

Uso:  .venv\\Scripts\\python.exe voz\\prueba_microfono.py [segundos]
"""
from __future__ import annotations

import sys
import time

import numpy as np

from jarvis_voz import (
    FRAME_LENGTH,
    GANANCIA,
    WAKE_MODEL,
    WAKE_UMBRAL,
    _cargar_wakeword,
    _consola_utf8,
    _elegir_microfono,
)


def main() -> None:
    _consola_utf8()
    duracion = int(sys.argv[1]) if len(sys.argv) > 1 else 75
    from pvrecorder import PvRecorder

    oww = _cargar_wakeword()
    recorder = PvRecorder(device_index=_elegir_microfono(), frame_length=FRAME_LENGTH)
    recorder.start()
    print(f"ESCUCHANDO {duracion}s con ganancia x{GANANCIA} (umbral {WAKE_UMBRAL}). "
          "Di 'hey jarvis' varias veces...", flush=True)

    t0 = time.time()
    mejor = 0.0
    try:
        while time.time() - t0 < duracion:
            crudo_max = ampli_max = puntuacion_max = recorte_max = 0.0
            for _ in range(12):  # ~1 segundo de frames de 80 ms
                frame = np.array(recorder.read(), dtype="float32")
                crudo = float(np.sqrt(np.mean(np.square(frame))))
                amplificado = np.clip(frame * GANANCIA, -32768, 32767)
                ampli = float(np.sqrt(np.mean(np.square(amplificado))))
                recorte = float(np.mean(np.abs(amplificado) >= 32767)) * 100
                puntuacion = oww.predict(amplificado.astype(np.int16)).get(WAKE_MODEL, 0.0)
                crudo_max = max(crudo_max, crudo)
                ampli_max = max(ampli_max, ampli)
                recorte_max = max(recorte_max, recorte)
                puntuacion_max = max(puntuacion_max, float(puntuacion))
            mejor = max(mejor, puntuacion_max)
            aviso = "  <<< DETECTADO" if puntuacion_max >= WAKE_UMBRAL else ""
            print(f"[{time.time() - t0:5.1f}s] crudo={crudo_max:6.0f} ampli={ampli_max:6.0f} "
                  f"recorte={recorte_max:4.1f}% hey_jarvis={puntuacion_max:.2f}{aviso}", flush=True)
    finally:
        recorder.stop()
        recorder.delete()
    print(f"\nMEJOR PUNTUACION DE LA SESION: {mejor:.2f} (dispara con >= {WAKE_UMBRAL})", flush=True)


if __name__ == "__main__":
    main()
