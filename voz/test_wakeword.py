"""Medidor en vivo del wake word: ves si te oye y si detecta 'Hey Jarvis'.

Muestra, frame a frame:
  - nivel: cuanto sonido entra por el micro (0 = no te coge).
  - confianza: probabilidad 0..1 de que hayas dicho 'hey jarvis'.

Di 'Hey Jarvis' varias veces y mira los numeros. Ctrl+C para terminar y ver el
diagnostico. No gasta nada de IA ni necesita clave.

Uso:
    python voz/test_wakeword.py        (microfono por defecto)
    python voz/test_wakeword.py 0      (probar el microfono indice 0)
    python voz/test_wakeword.py 1      (probar el indice 1, etc.)
"""
import sys

import numpy as np
from pvrecorder import PvRecorder

from voz.jarvis_voz import _cargar_wakeword, FRAME_LENGTH, WAKE_MODEL, WAKE_UMBRAL


def main() -> None:
    indice = int(sys.argv[1]) if len(sys.argv) > 1 else -1

    oww = _cargar_wakeword()
    rec = PvRecorder(device_index=indice, frame_length=FRAME_LENGTH)
    rec.start()

    print(f"\nMicrofono en uso: {rec.selected_device}")
    print(f"Umbral de deteccion actual: {WAKE_UMBRAL}")
    print("\n>>> Di 'HEY JARVIS' varias veces, claro. (Ctrl+C para terminar) <<<\n")

    maxp = 0.0
    nivel_max = 0.0
    n = 0
    try:
        while True:
            frame = np.array(rec.read(), dtype=np.int16)
            nivel = float(np.sqrt(np.mean(np.square(frame.astype("float32")))))
            p = oww.predict(frame).get(WAKE_MODEL, 0.0)
            maxp = max(maxp, p)
            nivel_max = max(nivel_max, nivel)
            # Imprimimos SIEMPRE (1 de cada 3 frames, ~4/seg) para que veas que
            # esta vivo aunque no entre sonido.
            n += 1
            if n % 3 == 0:
                marca = "  <<< DETECTADO!" if p >= WAKE_UMBRAL else ""
                barra_n = "#" * min(20, int(nivel / 100))   # nivel de micro
                barra_p = "#" * int(p * 20)                  # confianza
                print(f"nivel:{nivel:6.0f} |{barra_n:<20}|  conf:{p:4.2f} |{barra_p:<20}|{marca}", flush=True)
    except KeyboardInterrupt:
        pass
    finally:
        rec.stop()
        rec.delete()

    print("\n===== DIAGNOSTICO =====")
    print(f"Nivel de sonido maximo: {nivel_max:.0f}")
    print(f"Confianza maxima de 'hey jarvis': {maxp:.2f} (umbral {WAKE_UMBRAL})")
    if nivel_max < 100:
        print("\n-> El microfono NO te esta cogiendo (nivel casi 0).")
        print("   Es el microfono equivocado. Prueba otro indice:")
        print("      python voz/test_wakeword.py 0")
        print("      python voz/test_wakeword.py 1")
        print("      python voz/test_wakeword.py 2")
    elif maxp >= WAKE_UMBRAL:
        print("\n-> FUNCIONA: la palabra se detecta. JARVIS deberia responderte.")
        print("   Si en JARVIS no saltaba, era que decias 'Jarvis' sin el 'Hey'.")
    elif maxp >= 0.25:
        sugerido = round(maxp - 0.05, 2)
        print(f"\n-> Te oye pero se queda corto. Baja el umbral. Antes de arrancar JARVIS:")
        print(f"      $env:JARVIS_WAKE_UMBRAL = \"{sugerido}\"")
    else:
        print("\n-> Te oye (hay nivel) pero el modelo casi no reconoce la palabra.")
        print("   Di 'HEY JARVIS' bien claro y separado. Si aun asi no sube,")
        print("   probamos otra palabra (p.ej. 'alexa') o ajustamos el micro.")


if __name__ == "__main__":
    main()
