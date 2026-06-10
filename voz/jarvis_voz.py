"""JARVIS — capa de voz para el sistema multiagente.

Convierte tu asistente en un "Jarvis" al que hablas:

    Dices "Jarvis"  ->  te escucha  ->  entiende (Claude)  ->  te responde hablando

Dos modos:
  - CHARLA (por defecto): JARVIS solo conversa y busca en la web. No toca el PC.
  - ACTUAR (--actuar):     JARVIS ademas puede crear/editar archivos y ejecutar
                           tareas, pidiendote PERMISO POR VOZ antes de cada accion
                           sensible (mismo modelo de seguridad que el orquestador).

Piezas (todas locales y gratuitas, salvo la voz premium opcional):
  - openWakeWord:          detecta "Hey Jarvis" (modelo local, SIN clave ni cuenta).
  - pvrecorder:            captura del microfono (libreria suelta, sin clave).
  - faster-whisper:        pasa tu voz a texto (local, privado).
  - Claude Agent SDK:      el cerebro (el MISMO que ya usa tu proyecto).
  - pyttsx3:               voz de respuesta local (provisional; luego ElevenLabs).

Decision de arquitectura (importante):
  El cerebro usa un ClaudeSDKClient PERSISTENTE, no query() por turno. Es la
  misma leccion que backend/main.py ya aprendio ("Stream closed" con query()).
  Una sola sesion abierta durante toda la charla da dos cosas que query() no da:
    1) MEMORIA entre turnos: "y cuanto mide?" recuerda de quien hablabas.
    2) Sin arranque en frio por turno: no se relanza el subproceso cada vez.
  Todo el audio (Porcupine/PvRecorder/pyttsx3) es SINCRONO y corre en el hilo
  principal; solo las llamadas al cerebro entran en el event loop, reutilizando
  SIEMPRE el mismo loop para mantener viva la conexion del SDK.

Uso:
    python voz/jarvis_voz.py            -> JARVIS de voz, modo CHARLA
    python voz/jarvis_voz.py --actuar   -> JARVIS de voz, puede actuar sobre el PC
    python voz/jarvis_voz.py --check    -> prueba las piezas sin micro/clave

Seguridad (modo ACTUAR): escribir/editar DENTRO del proyecto se auto-aprueba;
borrar, comandos (Bash) o rutas fuera del proyecto piden tu permiso POR VOZ.
Reutiliza las mismas reglas que backend/orchestrator.py.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import re
import subprocess
import sys
import time
import unicodedata
from pathlib import Path

# --- Rutas y carga de .env del proyecto -------------------------------------
PROJECT_DIR = Path(__file__).resolve().parent.parent

# Permite importar 'backend' aunque se arranque como 'python voz/jarvis_voz.py'
# (sin esto, Python solo ve la carpeta voz/ y falla con ModuleNotFoundError).
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))

try:
    from dotenv import load_dotenv
    load_dotenv(PROJECT_DIR / ".env")
except ImportError:
    pass

# Nombre con el que JARVIS se dirige a ti.
USER_NAME = os.getenv("JARVIS_USER_NAME", "Luis")

# --- Wake word (openWakeWord) ----------------------------------------------
# Modelo pre-entrenado que dispara JARVIS. "hey_jarvis" viene de fabrica.
WAKE_MODEL = os.getenv("JARVIS_WAKE_MODEL", "hey_jarvis")
# Confianza minima (0..1) para dar por detectada la palabra. 0.4 calibrado con
# la voz de Luis (sus 'hey jarvis' marcan 0.40-0.84, el ruido no pasa de ~0.19).
# Subelo si salta solo; bajalo si te cuesta que te oiga.
WAKE_UMBRAL = float(os.getenv("JARVIS_WAKE_UMBRAL", "0.4"))
# Tamano de frame que espera openWakeWord: 1280 muestras = 80 ms a 16 kHz.
FRAME_LENGTH = 1280
SAMPLE_RATE = 16000

# Persona de TRAVIS: companero de voz para ninos con autismo. En espanol.
# Diseno basado en pautas de comunicacion para TEA: lenguaje literal y claro,
# frases muy cortas, tono calmado, predecible, refuerzo positivo. NO es terapia.
JARVIS_SYSTEM_PROMPT = (
    f"Eres Travis, un companero de voz amable y MUY paciente para {USER_NAME}, "
    "un nino. Hablas en espanol. Tu trabajo es acompanar, calmar y ayudar a "
    "comunicarse. No ensenas a la fuerza ni corriges.\n"
    "COMO HABLAS (muy importante):\n"
    "- Frases MUY cortas. Una sola idea por frase.\n"
    "- Lenguaje literal y concreto. NUNCA uses ironia, sarcasmo, dobles sentidos, "
    "metaforas ni frases hechas. Di las cosas tal y como son.\n"
    "- Tono calmado, carinoso y constante. Nunca grites, nunca regañes, nunca "
    "metas prisa.\n"
    "- Pregunta UNA sola cosa cada vez y espera su respuesta.\n"
    "- Si no te entiende o repite algo, repite tu con calma y paciencia, sin "
    "enfadarte y sin cambiar el sentido.\n"
    "- Usa refuerzo positivo y suave: 'muy bien', 'lo estas haciendo genial'.\n"
    "- Ayuda a poner nombre a las emociones si hace falta: 'parece que estas "
    "enfadado; no pasa nada'.\n"
    "- Se PREDECIBLE: ante lo mismo, responde parecido. La rutina le da seguridad.\n"
    "SEGURIDAD (no negociable):\n"
    "- Solo temas apropiados para un nino. Nada que asuste: ni miedo, ni "
    "violencia, ni contenido adulto.\n"
    "- Si pregunta algo delicado o no apto para ninos, redirige con suavidad a "
    "algo seguro y sugiere hablarlo con un adulto de confianza.\n"
    "- No eres medico ni terapeuta y no lo aparentas. Si notas angustia de "
    "verdad, anima con calma a avisar a un adulto.\n"
    "Mantienes el hilo: recuerda lo que se hablo antes en la charla."
)

# Persona de JARVIS en modo ACTUAR: ademas de conversar, puede actuar sobre el PC.
JARVIS_PROMPT_ACTUAR = (
    f"Eres Travis, el asistente personal de {USER_NAME}, y hablas por voz. "
    "Responde en espanol con frases CORTAS y naturales. Ademas de conversar y "
    "buscar en la web, puedes ACTUAR en el ordenador: leer, crear y editar "
    f"archivos del proyecto, y ejecutar tareas. Trabajas en: {PROJECT_DIR}. Usa "
    "SIEMPRE rutas dentro de ese directorio. Antes de cualquier accion con efectos "
    "(crear/editar/borrar archivos o ejecutar comandos) explicaras en una frase "
    "que vas a hacer; el sistema pedira permiso al usuario por voz cuando haga "
    f"falta. Si te deniegan, detente y dilo. Mantienes el hilo de la conversacion."
)

# Frases con las que el usuario cierra la sesion de voz. (Se comparan ya
# normalizadas: sin tildes ni signos, ver _normalizar.)
FRASES_SALIR = ("para de escuchar", "apagate", "adios jarvis", "adios travis", "hasta luego", "deja de escuchar")

# Frases con las que JARVIS abre su interfaz visual (HUD estilo Iron Man).
FRASES_HUD = ("interfaz", "muestrate", "tu cara", "pantalla", "hud", "tu rostro")
HUD_PATH = str(PROJECT_DIR / "hud" / "jarvis_hud.html")

# Palabras con las que el usuario CONCEDE permiso por voz.
PALABRAS_SI = ("si", "sis", "vale", "claro", "adelante", "hazlo", "autorizo",
               "autorizado", "ok", "okay", "dale", "correcto", "de acuerdo",
               "venga", "perfecto", "permitido")
# Palabras con las que el usuario DENIEGA.
PALABRAS_NO = ("no", "para", "cancela", "cancelar", "espera", "mejor no",
               "nada", "deten", "detente", "ni hablar")


def _normalizar(texto: str) -> str:
    """Texto en minusculas, sin tildes y sin signos, listo para comparar.

    Whisper transcribe con tildes y puntuacion ('Sí, ¡adelante!'); las listas de
    palabras estan sin tildes ('si', 'adelante'). Sin esta normalizacion, un
    'Sí.' hablado NO coincidia con 'si' y el permiso se denegaba aunque el
    usuario hubiera dicho que si (y 'adiós' nunca cerraba la sesion).
    """
    t = unicodedata.normalize("NFD", texto.lower())
    t = "".join(c for c in t if unicodedata.category(c) != "Mn")  # quita tildes
    t = re.sub(r"[^a-z0-9 ]+", " ", t)                            # quita signos
    return re.sub(r"\s+", " ", t).strip()


def _es_afirmativo(texto: str) -> bool:
    """True si la respuesta hablada del usuario es un 'si' a conceder permiso.

    Da prioridad a la negacion: si dice 'no' en cualquier parte, es NO. Asi, ante
    la duda, NO se ejecuta la accion sensible (fallar del lado seguro).
    """
    t = f" {_normalizar(texto)} "
    if any(f" {p} " in t for p in PALABRAS_NO):
        return False
    return any(f" {p} " in t for p in PALABRAS_SI)


# ===========================================================================
#  CEREBRO: sesion PERSISTENTE con el Claude Agent SDK (memoria + sin arranque
#  en frio). Mismo patron que backend/main.py usa en produccion.
# ===========================================================================
class Cerebro:
    """Sesion de CHARLA: conversa y busca en la web. No toca el PC.

    Se abre una sola vez (`abrir`) y cada turno reutiliza la misma conexion
    (`preguntar`), conservando el contexto. La conexion queda atada al event
    loop desde el que se llama a `abrir`, por eso el bucle de voz usa SIEMPRE el
    mismo loop para hablar con el cerebro.
    """

    def __init__(self) -> None:
        self._client = None

    async def _opciones(self):
        """Opciones del SDK para este cerebro. Las subclases la sobreescriben."""
        from claude_agent_sdk import ClaudeAgentOptions

        return ClaudeAgentOptions(
            model=os.getenv("CHAT_MODEL", "claude-sonnet-4-6"),
            system_prompt=JARVIS_SYSTEM_PROMPT,
            # SEGURIDAD INFANTIL: sin web ni herramientas. Travis solo conversa,
            # asi no hay riesgo de contenido inapropiado desde internet.
            allowed_tools=[],
            permission_mode="dontAsk",  # deniega cualquier herramienta no permitida
            cwd=str(PROJECT_DIR),
            max_turns=8,
        )

    async def abrir(self) -> "Cerebro":
        from claude_agent_sdk import ClaudeSDKClient

        self._client = ClaudeSDKClient(options=await self._opciones())
        await self._client.connect()
        return self

    async def preguntar(self, texto: str) -> str:
        """Envia un turno y devuelve el ultimo texto de la respuesta de Claude."""
        assert self._client is not None, "Cerebro.abrir() no fue llamado"
        await self._client.query(texto)

        respuesta = ""
        async for message in self._client.receive_response():
            content = getattr(message, "content", None)
            if not content:
                continue
            for block in content:
                t = getattr(block, "text", None)
                if t:
                    respuesta = t  # nos quedamos con el ultimo bloque de texto
        return (respuesta or f"Ahora mismo no puedo responder, {USER_NAME}.").strip()

    async def cerrar(self) -> None:
        if self._client is not None:
            await self._client.disconnect()
            self._client = None


class CerebroActuador(Cerebro):
    """Sesion ACTUAR: ademas de conversar, puede tocar el PC con checkpoints.

    Reutiliza EXACTAMENTE las reglas de seguridad de backend/orchestrator.py:
    escribir/editar dentro del proyecto se auto-aprueba; borrar, comandos (Bash)
    o rutas fuera del proyecto piden permiso humano. Aqui ese permiso se pide por
    voz, via el callback `ask_human` que recibe el constructor.
    """

    def __init__(self, ask_human) -> None:
        super().__init__()
        self._ask_human = ask_human

    async def _opciones(self):
        from claude_agent_sdk import ClaudeAgentOptions
        from claude_agent_sdk.types import PermissionResultAllow, PermissionResultDeny

        # Reutilizamos las reglas ya probadas del orquestador (no las reinventamos).
        from backend.orchestrator import (
            AUTO_APPROVED_TOOLS,
            SAFE_WRITE_TOOLS,
            _is_inside_project,
        )

        ask_human = self._ask_human

        async def can_use_tool(tool_name: str, input_data: dict, context) -> object:
            # Escribir/editar DENTRO del proyecto -> auto-aprobado (autonomia con limites).
            if tool_name in SAFE_WRITE_TOOLS and _is_inside_project(input_data.get("file_path", "")):
                print(f"[auto] {tool_name} dentro del proyecto -> auto-aprobado: {input_data.get('file_path')}", flush=True)
                return PermissionResultAllow(updated_input=input_data)
            # El resto (Bash, borrar, fuera del proyecto) -> permiso por voz.
            decision = await ask_human(tool_name, input_data)
            if decision.get("approved"):
                return PermissionResultAllow(updated_input=input_data)
            return PermissionResultDeny(
                message=decision.get("reason") or "Denegado por voz.",
                interrupt=False,
            )

        # Herramientas: lectura/web auto-aprobadas + escritura/edicion/Bash que
        # caen en can_use_tool segun la ruta. Bash siempre pasa por permiso.
        return ClaudeAgentOptions(
            model=os.getenv("CHAT_MODEL", "claude-sonnet-4-6"),
            system_prompt=JARVIS_PROMPT_ACTUAR,
            allowed_tools=list(AUTO_APPROVED_TOOLS),
            permission_mode="default",  # lo no pre-aprobado cae en can_use_tool
            can_use_tool=can_use_tool,
            cwd=str(PROJECT_DIR),
            max_turns=12,
        )


# ===========================================================================
#  VOZ DE SALIDA: pyttsx3 (local, gratis). Provisional hasta ElevenLabs.
# ===========================================================================
class Voz:
    """Voz de salida. Cada frase se dice en un PROCESO APARTE (voz/_tts_worker.py).

    Motivo: pyttsx3 enmudece al reutilizar el motor dentro del bucle principal;
    en un proceso suelto suena siempre. La deteccion de la voz espanola se hace
    una sola vez aqui (solo lee la lista de voces, no reproduce nada).
    """

    def __init__(self) -> None:
        # Voz mas lenta y calmada (mejor para ninos con autismo). Ajustable.
        self.rate = int(os.getenv("JARVIS_TTS_RATE", "150"))
        self.voice_id = self._elegir_voz_espanol()
        self._worker = str(Path(__file__).resolve().parent / "_tts_worker.py")

    def _elegir_voz_espanol(self) -> str:
        """Devuelve el id de una voz en espanol si el sistema tiene alguna."""
        try:
            import pyttsx3
            engine = pyttsx3.init()
            preferidas = ("spanish", "espa", "es-", "es_", "helena", "sabina", "laura", "pablo")
            for v in engine.getProperty("voices"):
                campos = " ".join(filter(None, [
                    getattr(v, "name", ""),
                    getattr(v, "id", ""),
                    " ".join(getattr(v, "languages", []) or []),
                ])).lower()
                if any(p in campos for p in preferidas):
                    return v.id
        except Exception:
            pass
        return ""

    def decir(self, texto: str) -> None:
        print(f"TRAVIS> {texto}")
        try:
            subprocess.run(
                [sys.executable, self._worker, str(self.rate), self.voice_id],
                input=texto.encode("utf-8"),
                timeout=60,
            )
        except Exception as e:  # si el TTS falla, JARVIS ya respondio por texto
            print(f"[voz] No se pudo reproducir la voz: {e}")


# ===========================================================================
#  OIDO: Porcupine (palabra "Jarvis") + grabacion del mandato + Whisper.
# ===========================================================================
def _cargar_whisper():
    from faster_whisper import WhisperModel
    tamano = os.getenv("WHISPER_MODEL", "small")  # tiny/base/small/medium
    print(f"[voz] Cargando modelo de voz->texto '{tamano}' (la 1a vez se descarga)...")
    return WhisperModel(tamano, device="cpu", compute_type="int8")


def _cargar_wakeword():
    """Carga openWakeWord con el modelo 'hey jarvis' (local, sin clave).

    La 1a vez descarga los modelos pre-entrenados (~varios MB). Devuelve el
    modelo listo para `predict()`.
    """
    import openwakeword
    from openwakeword.model import Model

    print(f"[voz] Cargando wake word '{WAKE_MODEL}' (openWakeWord, sin clave)...")
    openwakeword.utils.download_models()  # idempotente: si ya estan, no baja nada
    return Model(wakeword_models=[WAKE_MODEL], inference_framework="onnx")


def _transcribir(modelo, audio_int16) -> str:
    import numpy as np
    audio = np.array(audio_int16, dtype="float32") / 32768.0
    segmentos, _ = modelo.transcribe(audio, language="es", beam_size=1)
    return " ".join(s.text for s in segmentos).strip()


def _leer_amplificado(recorder):
    """Lee un frame del micro y le sube el volumen (ganancia) por software.

    Para micros flojos: multiplica la senal por JARVIS_GAIN (con recorte para no
    distorsionar) antes de que el oido y Whisper la procesen. Asi JARVIS oye bien
    aunque el nivel de entrada sea bajo, sin tocar Windows. Devuelve list[int16].
    """
    import numpy as np
    frame = recorder.read()
    ganancia = float(os.getenv("JARVIS_GAIN", "1.0"))
    if ganancia == 1.0:
        return frame
    amplificado = np.clip(np.array(frame, dtype="float32") * ganancia, -32768, 32767)
    return amplificado.astype(np.int16).tolist()


def _drenar_microfono(recorder, frames: int = 12) -> None:
    """Descarta el audio acumulado mientras JARVIS hablaba (anti-eco).

    pyttsx3 reproduce por los altavoces mientras el PvRecorder sigue
    buffereando; sin esto, Whisper transcribiria la propia voz de JARVIS
    mezclada con la tuya. Tiramos unos frames antes de empezar a grabar.
    """
    for _ in range(frames):
        recorder.read()


def _grabar_mandato(recorder):
    """Graba lo que dices tras 'Jarvis' hasta que te callas (o 8 s)."""
    import numpy as np
    umbral = int(os.getenv("JARVIS_SILENCIO", "350"))   # nivel de silencio (RMS)
    max_seg = 8.0
    silencio_fin = 1.1                                   # seg de silencio = fin
    audio: list[int] = []
    inicio = time.time()
    hubo_voz = False
    ultimo_sonido = time.time()
    while True:
        frame = _leer_amplificado(recorder)
        audio.extend(frame)
        rms = float(np.sqrt(np.mean(np.square(np.array(frame, dtype="float32")))))
        ahora = time.time()
        if rms > umbral:
            hubo_voz = True
            ultimo_sonido = ahora
        if hubo_voz and (ahora - ultimo_sonido) > silencio_fin:
            break
        if (ahora - inicio) > max_seg:
            break
    return audio


def _esperar_palabra_clave(recorder, oww) -> bool:
    """Bloquea hasta oir 'Hey Jarvis'. Devuelve True al detectarla.

    Usa openWakeWord: por cada frame de audio calcula una confianza 0..1 para el
    modelo y dispara al superar WAKE_UMBRAL. Lanza KeyboardInterrupt hacia arriba
    si el usuario pulsa Ctrl+C.
    """
    import numpy as np

    oww.reset()  # limpia el buffer interno para no arrastrar audio viejo
    while True:
        frame = np.array(_leer_amplificado(recorder), dtype=np.int16)
        puntuaciones = oww.predict(frame)
        if puntuaciones.get(WAKE_MODEL, 0.0) >= WAKE_UMBRAL:
            return True


def _hacer_ask_human_voz(voz: "Voz", recorder, whisper):
    """Crea el callback que pide permiso POR VOZ antes de una accion sensible.

    JARVIS dice en voz alta que quiere hacer, te escucha, y solo continua si
    respondes que si. Ante el silencio o la duda, NIEGA (lado seguro).
    """

    async def ask_human(tool_name: str, input_data: dict) -> dict:
        detalle = (
            input_data.get("file_path")
            or input_data.get("command")
            or input_data.get("path")
            or ""
        )
        sobre = f" sobre {detalle}" if detalle else ""
        voz.decir(f"Necesito tu permiso para usar {tool_name}{sobre}. ¿Lo autorizas?")
        _drenar_microfono(recorder)
        audio = _grabar_mandato(recorder)
        respuesta = _transcribir(whisper, audio)
        print(f"[permiso] {USER_NAME} respondio: {respuesta!r}", flush=True)
        if _es_afirmativo(respuesta):
            voz.decir("Hecho.")
            return {"approved": True, "reason": f"Autorizado por voz: '{respuesta}'"}
        voz.decir("Entendido, no lo hago.")
        return {"approved": False, "reason": f"Denegado por voz: '{respuesta}'"}

    return ask_human


def _abrir_hud(voz: "Voz") -> None:
    """Abre la interfaz visual (HUD) de JARVIS en el navegador."""
    voz.decir("Mostrando mi interfaz.")
    try:
        os.startfile(HUD_PATH)  # Windows: abre el HTML en el navegador por defecto
    except Exception as e:
        print(f"[hud] No se pudo abrir la interfaz: {e}")
        voz.decir("No he podido abrir la interfaz.")


def bucle_jarvis(actuar: bool = False) -> None:
    """Bucle principal: escucha 'Jarvis', te entiende y te responde por voz.

    El audio es sincrono; el cerebro vive en un unico event loop persistente
    para conservar la conexion del SDK (y con ella la memoria de la charla).
    Si `actuar` es True, JARVIS puede tocar el PC pidiendo permiso por voz.
    """
    from pvrecorder import PvRecorder

    voz = Voz()
    whisper = _cargar_whisper()
    oww = _cargar_wakeword()  # openWakeWord: local, sin clave ni cuenta

    recorder = PvRecorder(device_index=int(os.getenv("MIC_INDEX", "-1")),
                          frame_length=FRAME_LENGTH)

    # Un unico event loop para toda la sesion: lo necesita el cerebro persistente.
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    if actuar:
        ask_human = _hacer_ask_human_voz(voz, recorder, whisper)
        cerebro: Cerebro = CerebroActuador(ask_human)
    else:
        cerebro = Cerebro()
    loop.run_until_complete(cerebro.abrir())

    recorder.start()
    modo = "ACTUAR (puede tocar el PC, pide permiso por voz)" if actuar else "CHARLA (solo conversa y busca)"
    print("\n========================================")
    print(f"  TRAVIS activo - modo {modo}.")
    print("  Di 'Hey Jarvis' para despertarlo. (Ctrl+C para salir)")
    print("========================================\n")
    voz.decir(f"Hola {USER_NAME}. Soy Travis. Estoy aqui contigo.")

    try:
        while True:
            _esperar_palabra_clave(recorder, oww)
            # Detectada la palabra "Hey Jarvis".
            print("[voz] Te escucho...")
            voz.decir("¿Sí?")
            _drenar_microfono(recorder)          # anti-eco: descarta el "¿Sí?"
            audio = _grabar_mandato(recorder)
            texto = _transcribir(whisper, audio)
            if not texto:
                voz.decir(f"No te he entendido, {USER_NAME}.")
                continue
            print(f"{USER_NAME}> {texto}")
            # Normalizado (sin tildes/signos): asi 'Adiós, Travis.' si coincide.
            texto_cmd = _normalizar(texto)
            if any(p in texto_cmd for p in FRASES_SALIR):
                voz.decir(f"Hasta luego, {USER_NAME}.")
                break
            if any(p in texto_cmd for p in FRASES_HUD):
                _abrir_hud(voz)
                continue
            respuesta = loop.run_until_complete(cerebro.preguntar(texto))
            voz.decir(respuesta)
    except KeyboardInterrupt:
        print("\n[voz] Cerrando JARVIS...")
    finally:
        recorder.stop()
        recorder.delete()
        loop.run_until_complete(cerebro.cerrar())
        loop.close()


# ===========================================================================
#  AUTOPRUEBA: verifica piezas sin necesitar la palabra clave ni el micro.
# ===========================================================================
def autoprueba() -> None:
    print("== Autoprueba de JARVIS voz ==\n")

    # 1) Micrófonos disponibles
    try:
        from pvrecorder import PvRecorder
        mics = PvRecorder.get_available_devices()
        print("Micrófonos detectados:")
        for i, m in enumerate(mics):
            print(f"   [{i}] {m}")
        if not mics:
            print("   (ninguno: revisa que haya un micrófono conectado)")
    except Exception as e:
        print(f"   Error listando micrófonos: {e}")

    # 2) Wake word local (openWakeWord) — sin clave ni cuenta
    try:
        import numpy as np
        oww = _cargar_wakeword()
        pred = oww.predict(np.zeros(FRAME_LENGTH, dtype=np.int16))
        tiene = WAKE_MODEL in pred
        print(f"\nWake word '{WAKE_MODEL}' (openWakeWord): {'OK (sin clave)' if tiene else 'ERROR: modelo no cargado'}")
    except Exception as e:
        print(f"\nWake word (openWakeWord): ERROR -> {e}")

    # 3) Whisper carga
    try:
        _cargar_whisper()
        print("Voz->texto (Whisper): OK")
    except Exception as e:
        print(f"Voz->texto (Whisper): ERROR -> {e}")

    # 4) Voz de salida
    try:
        v = Voz()
        v.decir(f"Hola {USER_NAME}, soy Travis. La voz funciona.")
        print("Voz de salida: OK (deberías haberla oído)")
    except Exception as e:
        print(f"Voz de salida: ERROR -> {e}")

    # 5) Reconocedor de permiso por voz (sin micro: solo la logica si/no)
    print("\nPrueba del permiso por voz (logica si/no):")
    # Casos realistas: Whisper transcribe con tildes, mayusculas y puntuacion.
    casos = {"Sí.": True, "Sí, adelante.": True, "¡Vale, hazlo!": True,
             "No, espera.": False, "Mejor no...": False, "ni idea": False}
    for frase, esperado in casos.items():
        ok = _es_afirmativo(frase) == esperado
        print(f"   {'OK ' if ok else 'MAL'} '{frase}' -> {_es_afirmativo(frase)} (esperado {esperado})")

    # 6) Cerebro: una pregunta real con sesion persistente (verifica memoria).
    try:
        async def _probar_cerebro() -> tuple[str, str]:
            cerebro = await Cerebro().abrir()
            try:
                r1 = await cerebro.preguntar("Recuerda el numero 7. Responde solo 'vale'.")
                r2 = await cerebro.preguntar("Que numero te pedi recordar? Responde solo el numero.")
                return r1, r2
            finally:
                await cerebro.cerrar()

        r1, r2 = asyncio.run(_probar_cerebro())
        print(f"\nCerebro turno 1: {r1!r}")
        print(f"Cerebro turno 2 (memoria): {r2!r}")
        print("Cerebro (sesion persistente): OK" if "7" in r2 else
              "Cerebro responde, pero revisa la memoria entre turnos.")
    except Exception as e:
        print(f"\nCerebro: ERROR -> {e}")

    print("\nSi todo está OK y tienes la clave, ejecuta:  python voz/jarvis_voz.py")


def _consola_utf8() -> None:
    """Evita que la consola de Windows (cp1252) se caiga al imprimir emojis.

    Reconfigura la salida a UTF-8 y, si un caracter aun no se puede mostrar,
    lo sustituye en vez de lanzar UnicodeEncodeError.
    """
    for flujo in (sys.stdout, sys.stderr):
        try:
            flujo.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


def main() -> None:
    _consola_utf8()
    parser = argparse.ArgumentParser(description="JARVIS — capa de voz")
    parser.add_argument("--check", action="store_true", help="prueba las piezas sin arrancar el bucle")
    parser.add_argument("--actuar", action="store_true", help="permite que JARVIS actue sobre el PC (pide permiso por voz)")
    args = parser.parse_args()
    if args.check:
        autoprueba()
    else:
        bucle_jarvis(actuar=args.actuar)


if __name__ == "__main__":
    main()
