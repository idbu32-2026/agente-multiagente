"""Diagnostico que CAPTURO YO: graba 40s del micro por defecto y resume por
segundo el nivel de sonido y la confianza de 'hey jarvis'. Sin interaccion.
"""
import time
import numpy as np
from pvrecorder import PvRecorder
from voz.jarvis_voz import _cargar_wakeword, FRAME_LENGTH, WAKE_MODEL

SEGUNDOS = 40

oww = _cargar_wakeword()
rec = PvRecorder(device_index=-1, frame_length=FRAME_LENGTH)
rec.start()
print(f"MIC: {rec.selected_device}", flush=True)
print("Graba 40s. (di 'Hey Jarvis' varias veces)", flush=True)

fin = time.time() + SEGUNDOS
seg_actual = int(time.time())
nivel_s = 0.0
conf_s = 0.0
nivel_glob = 0.0
conf_glob = 0.0
try:
    while time.time() < fin:
        frame = np.array(rec.read(), dtype=np.int16)
        nivel = float(np.sqrt(np.mean(np.square(frame.astype("float32")))))
        p = oww.predict(frame).get(WAKE_MODEL, 0.0)
        nivel_s = max(nivel_s, nivel)
        conf_s = max(conf_s, p)
        nivel_glob = max(nivel_glob, nivel)
        conf_glob = max(conf_glob, p)
        ahora = int(time.time())
        if ahora != seg_actual:
            print(f"  s+{ahora-(int(fin)-SEGUNDOS):02d}: nivel_max={nivel_s:6.0f}  conf_max={conf_s:.2f}", flush=True)
            seg_actual = ahora
            nivel_s = 0.0
            conf_s = 0.0
finally:
    rec.stop()
    rec.delete()

print(f"\nRESUMEN: nivel_max_global={nivel_glob:.0f}  conf_max_global={conf_glob:.2f}", flush=True)
if nivel_glob < 100:
    print("VEREDICTO: el micro por defecto NO capta voz (nivel ~0). Mic equivocado.", flush=True)
elif conf_glob >= 0.5:
    print("VEREDICTO: detecta 'hey jarvis'. El wake word funciona.", flush=True)
elif conf_glob >= 0.2:
    print(f"VEREDICTO: te oye pero el umbral 0.5 es alto. Bajar a {conf_glob-0.05:.2f}.", flush=True)
else:
    print("VEREDICTO: hay sonido pero no reconoce la palabra. Revisar mic o pronunciacion.", flush=True)
