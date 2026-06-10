"""Diagnostico rapido: mide el nivel de sonido de cada microfono.

No usa Whisper ni IA. Solo dice cuanto sonido entra por cada micro, para
saber cual capta tu voz. Ejecutar y hablar sin parar.
"""
import time

import numpy as np
from pvrecorder import PvRecorder

FRAME = 512
SEGUNDOS = 5

devices = PvRecorder.get_available_devices()
print(f"Micros detectados: {len(devices)}", flush=True)
for i, d in enumerate(devices):
    print(f"  [{i}] {d}", flush=True)
print(flush=True)

for idx in range(len(devices)):
    try:
        rec = PvRecorder(device_index=idx, frame_length=FRAME)
        rec.start()
        print(f"[mic {idx}] grabando {SEGUNDOS}s -> HABLA AHORA ('hola hola me oyes')", flush=True)
        audio: list[int] = []
        fin = time.time() + SEGUNDOS
        while time.time() < fin:
            audio.extend(rec.read())
        rec.stop()
        rec.delete()
        nivel = float(np.sqrt(np.mean(np.square(np.array(audio, dtype="float32")))))
        veredicto = "  <-- TE OYE" if nivel > 100 else "  (casi mudo)"
        print(f"[mic {idx}] NIVEL = {nivel:.0f}{veredicto}", flush=True)
        print(flush=True)
    except Exception as e:
        print(f"[mic {idx}] ERROR: {type(e).__name__}: {e}", flush=True)

print("=== FIN ===", flush=True)
