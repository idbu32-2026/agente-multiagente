"""Test rapido de microfono: graba 4 segundos y te dice que entendio.

Sirve para confirmar, ANTES de arrancar JARVIS entero, que tu microfono se oye
y que Whisper te transcribe bien. No necesita clave ni gasta nada de IA.

Uso:
    python voz/test_micro.py            (usa el microfono por defecto)
    python voz/test_micro.py 1          (usa el microfono con indice 1)
"""
import sys
import time

import numpy as np
from pvrecorder import PvRecorder

from voz.jarvis_voz import _cargar_whisper, _transcribir, FRAME_LENGTH

SEGUNDOS = 4


def main() -> None:
    indice = int(sys.argv[1]) if len(sys.argv) > 1 else -1

    print("Cargando el reconocedor de voz (Whisper)...")
    whisper = _cargar_whisper()

    rec = PvRecorder(device_index=indice, frame_length=FRAME_LENGTH)
    rec.start()
    print(f"\nMicrofono en uso: {rec.selected_device}")
    print(f"\n>>> HABLA AHORA durante {SEGUNDOS} segundos (di algo como 'hola, me oyes?') <<<\n")

    audio: list[int] = []
    fin = time.time() + SEGUNDOS
    while time.time() < fin:
        audio.extend(rec.read())
    rec.stop()
    rec.delete()

    # Nivel de sonido: para saber si entro algo de voz.
    nivel = float(np.sqrt(np.mean(np.square(np.array(audio, dtype="float32")))))
    texto = _transcribir(whisper, audio)

    print(f"Nivel de sonido captado: {nivel:.0f}  (si es casi 0, el micro no te cogio)")
    print(f"Lo que entendi: {texto!r}")
    if texto and nivel > 100:
        print("\nTEST OK: tu microfono funciona. Ya puedes arrancar JARVIS.")
    elif nivel <= 100:
        print("\nCASI NADA DE SONIDO: prueba otro indice de microfono, p.ej.:")
        print("   python voz/test_micro.py 1")
        print("   python voz/test_micro.py 0")
    else:
        print("\nSe oyo sonido pero no se entendio. Habla mas claro y reintenta.")


if __name__ == "__main__":
    main()
