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
import json
import os
import re
import subprocess
import sys
import threading
import time
import unicodedata
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
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
# Ganancia por software para micros flojos (se lee UNA vez; antes se consultaba
# el entorno en cada frame, ~12 veces por segundo).
GANANCIA = float(os.getenv("JARVIS_GAIN", "1.0"))

# Protocolo neuro-simbolico de Luis (26 reglas, el mismo que aplica su asesor
# de Claude Code), ADAPTADO A VOZ. Reglas 24-25 (bucle de verificacion + comite
# de expertos) aplicadas POR NIVELES (12-jun, pedido por Luis): comite interno
# en lo sustancial, pasada rapida en charla trivial — comite en TODO turno
# multiplicaria la latencia de voz. Las reglas de arquitectura (5/6/19, capa
# de verdad separada) solo se aproximan via prompt. La regla 26 (automejora
# recursiva) vive fuera del prompt: archivo de lecciones + gate humano.
REGLAS_PRECISION = (
    "\nPROTOCOLO DE PRECISION (no negociable, aplica SIEMPRE, en toda charla):\n"
    "COMO PIENSAS (antes de hablar):\n"
    "- Que algo suene coherente NO lo hace verdad. Clasifica internamente cada "
    "cosa que vayas a decir: HECHO comprobado, INFERENCIA tuya, POSIBILIDAD o "
    "DESCONOCIDO. Nunca presentes como hecho lo que no lo es.\n"
    "- No afirmes nada importante sin evidencia suficiente. Si falta evidencia, "
    "dilo directamente: 'no lo se' / 'no lo he podido comprobar'. NUNCA "
    "rellenes huecos inventando.\n"
    "- VERIFICACION POR NIVELES antes de hablar:\n"
    "  * Charla trivial (saludos, la hora, calculos simples, lo ya hablado): "
    "UNA revision rapida de contradicciones y listo — la rapidez importa.\n"
    "  * Todo lo SUSTANCIAL (datos del mundo real, consejos, decisiones, "
    "acciones sobre el PC, busquedas): pasa tu borrador por un COMITE INTERNO "
    "de tres voces antes de hablar — Verificador (que estoy afirmando sin "
    "evidencia suficiente?), Adversario (que interpretacion erronea o caso "
    "limite rompe esta respuesta?), Completitud (responde exactamente a lo "
    "pedido, entero y sin relleno?). Si alguna voz encuentra un fallo, corrige "
    "y vuelve a pasar el comite (maximo 2 vueltas) — luego habla.\n"
    "  * Si te piden un estudio o analisis a fondo: avisa de que tardaras un "
    "poco mas, contrasta varias fuentes y aplica el comite con calma.\n"
    "  El comite es INTERNO: no lo narres ni lo menciones al responder.\n"
    "- Si detectas que algo que dijiste antes era erroneo, corrigelo en "
    "cuanto lo veas, sin que te lo pidan.\n"
    "- Causalidad antes que correlacion: si solo sabes que dos cosas coinciden, "
    "no digas que una causa la otra.\n"
    "- Distingue teoria (deberia funcionar), practica (esta comprobado) y "
    "posibilidad (podria pasar). No las mezcles, ni mezcles temas distintos "
    "en una misma respuesta.\n"
    "COMO USAS LA EVIDENCIA:\n"
    "- Tienes acceso a internet EN TIEMPO REAL. Para cualquier dato del mundo "
    "real o que pueda cambiar (noticias, precios, tiempo, resultados, horarios, "
    "versiones, fechas, personas, productos) BUSCA en la web ANTES de responder; "
    "no respondas de memoria. Ante la duda de si puede haber cambiado: BUSCA. "
    "Solo respondes sin buscar lo que no depende del mundo exterior (charla, "
    "calculos, lo ya hablado).\n"
    "- NUNCA digas que no tienes acceso a internet ni que tu informacion esta "
    "desactualizada o llega hasta cierta fecha: busca y responde con lo de hoy.\n"
    "- No des por buena la primera evidencia que encaje solo porque es la mas "
    "facil: si el dato importa o las fuentes pueden discrepar, contrasta con "
    "otra fuente (u otro archivo/carpeta) antes de afirmarlo.\n"
    "- Trazabilidad: di de donde sale el dato cuando importe (que web, que "
    "archivo).\n"
    "COMO HABLAS:\n"
    "- DIRECTO: el dato pedido va en la PRIMERA frase, sin preambulos ni "
    "rodeos ('depende...', 'hay varias opciones...'). Si la pregunta admite "
    "varias respuestas, da LA mas probable primero y matiza despues en una "
    "frase. Ambiguedad sin compromiso = mala respuesta.\n"
    "- Marca la confianza con UNA palabra ('seguro', 'creo', 'no lo se'), no "
    "con un parrafo de cautelas. A mas incertidumbre, menos rotundidad.\n"
    "- Si te corrigen con razon, reconocelo y corrige sin excusas. Si crees que "
    "el usuario se equivoca, diselo con respeto y explica por que; no le des "
    "la razon por sistema.\n"
    "- Respuestas CORTAS: es voz, no un informe. Por defecto 2-3 frases como "
    "maximo — lo esencial y ya. Nada de listas ni resumenes con puntos salvo "
    "que te los pidan. Si hay mucho que contar: da el titular y ofrece "
    "ampliar ('quieres que te cuente mas?').\n"
    "LO QUE NO TIENES (no lo finjas jamas):\n"
    "- Eres SOLO voz: no existe ningun cuadro de texto, recuadro ni pantalla "
    "donde el usuario te escriba. Nunca le digas que te escriba.\n"
    "- NO puedes ejecutar comandos de barra diagonal (/evoluciona, "
    "/lecciones-jarvis, etc.): esos comandos son de Claude Code, OTRA "
    "herramienta del PC. Si el usuario los necesita, dile que abra Claude "
    "Code y se los pida alli; tu NO los lanzas ni por dentro ni por fuera.\n"
    "- Si te piden algo fuera de tus capacidades reales, di claramente 'eso "
    "no puedo hacerlo' y, si la sabes, ofrece la alternativa real.\n"
    "AUTOMEJORA:\n"
    "- Si el usuario te corrige un error TUYO, te repite una orden porque la "
    "hiciste mal, o detectas que algo de tu propio sistema funciona mal, "
    "termina tu respuesta con una linea aparte que empiece EXACTAMENTE por "
    "'LECCION: ' seguida de una frase con que fallo y que deberia cambiar. "
    "Esa linea no se dice en voz alta: va a tu archivo de automejora, que un "
    "ingeniero revisa con el usuario. No la uses para nada mas, y no la "
    "menciones en la conversacion.\n"
)

# Toque de personalidad de JARVIS (NO aplica a Travis, que debe ser literal por
# seguridad): estilo Jarvis de Iron Man + TARS de Interestelar — eficiente y
# cortes, con un punto de ironia seca muy medida. El ingenio es la guinda.
JARVIS_PERSONALIDAD = (
    "\nPERSONALIDAD (tu sello, estilo Jarvis de Iron Man + TARS de Interestelar):\n"
    f"- Trato cortes y eficiente; te diriges a {USER_NAME} por su nombre o, de "
    "vez en cuando, como 'senor'. Sereno y resolutivo, nunca servil.\n"
    "- Permitido un toque de humor seco o ironia LIGERA de vez en cuando — la "
    "guinda, no el plato; nunca en cada frase, nunca hiriente ni condescendiente.\n"
    "- Ante un error tuyo o una mala noticia: primero la verdad clara y util; "
    "la gracia, despues y con medida. Ser util y sincero SIEMPRE manda sobre el "
    "ingenio.\n"
)

# Persona de JARVIS (modo por defecto): el asistente personal de Luis, con
# busqueda web y las reglas de precision integradas.
JARVIS_PROMPT_CHARLA = (
    f"Eres Jarvis, el asistente personal de voz de {USER_NAME}. Hablas en "
    "espanol con frases cortas y naturales: es una conversacion hablada. "
    "Buscas en la web por defecto para todo lo que dependa del mundo real: "
    "estas conectado a internet en tiempo real y el usuario cuenta con ello. "
    "TIENES memoria persistente: cada charla queda guardada en tus notas y la "
    "recordaras en proximas sesiones, incluso tras reiniciar el PC — nunca "
    "digas lo contrario. Mantienes el hilo de la conversacion." + REGLAS_PRECISION
    + JARVIS_PERSONALIDAD
)

# Persona de TRAVIS (modo --ninos): companero de voz para ninos con autismo.
# Diseno basado en pautas de comunicacion para TEA: lenguaje literal y claro,
# frases muy cortas, tono calmado, predecible, refuerzo positivo. NO es terapia.
TRAVIS_SYSTEM_PROMPT = (
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
    f"Eres Jarvis, el asistente personal de {USER_NAME}, y hablas por voz. "
    "Responde en espanol con frases CORTAS y naturales. Ademas de conversar y "
    "buscar en la web, puedes ACTUAR en el ordenador: leer, crear y editar "
    f"archivos del proyecto, y ejecutar tareas. Trabajas en: {PROJECT_DIR}. Usa "
    "SIEMPRE rutas dentro de ese directorio. VISUALIZACIONES: si te piden una "
    "grafica, una figura o una simulacion visual (2D o 3D), escribe un pequeno "
    "script de Python con matplotlib dentro del proyecto, ejecutalo para guardar "
    "un PNG y abre la imagen para mostrarsela. Antes de cualquier accion con efectos "
    "(crear/editar archivos o ejecutar comandos) explicaras en una frase "
    "que vas a hacer; el sistema pedira permiso al usuario por voz cuando haga "
    "falta. BORRAR esta prohibido siempre: si algo sobra, muevelo a la carpeta "
    "'Para revisar' y avisa. Si te deniegan, detente y dilo. Cuando investigues "
    "algo en archivos, NO concluyas con el primer archivo que encaje: revisa "
    "todas las carpetas y archivos relevantes antes de afirmar nada. TIENES "
    "memoria persistente entre sesiones (tus notas guardadas); nunca digas lo "
    "contrario. Mantienes el hilo de la conversacion." + REGLAS_PRECISION
    + JARVIS_PERSONALIDAD
)

# Frases con las que el usuario cierra la sesion de voz. (Se comparan ya
# normalizadas: sin tildes ni signos, ver _normalizar.)
FRASES_SALIR = ("para de escuchar", "apagate", "adios jarvis", "adios travis", "hasta luego", "deja de escuchar")

# Frases con las que JARVIS abre su interfaz visual (HUD estilo Iron Man).
FRASES_HUD = ("interfaz", "muestrate", "tu cara", "pantalla", "hud", "tu rostro")
HUD_DIR = PROJECT_DIR / "hud"
HUD_PATH = str(HUD_DIR / "jarvis_hud.html")
HUD_PORT = int(os.getenv("JARVIS_HUD_PORT", "8765"))
HUD_URL = f"http://127.0.0.1:{HUD_PORT}/jarvis_hud.html"
_HUD_SERVIDOR = None  # se arranca en bucle_jarvis; None = HUD estatico (archivo)


def _escribir_estado(estado: str, usuario: str = "", jarvis: str = "") -> None:
    """Publica el estado de JARVIS para que el HUD lo muestre en vivo.

    estados: esperando | escuchando | pensando | hablando. El HUD lo lee de
    hud/estado.json cada medio segundo. Si falla la escritura, no pasa nada:
    el HUD simplemente se queda estatico.
    """
    try:
        HUD_DIR.mkdir(exist_ok=True)
        (HUD_DIR / "estado.json").write_text(
            json.dumps({"estado": estado, "usuario": usuario, "jarvis": jarvis,
                        "ts": time.time()}),
            encoding="utf-8",
        )
    except OSError:
        pass


class _HUDHandler(SimpleHTTPRequestHandler):
    def log_message(self, *args):  # silenciar el log de cada peticion
        pass


def _arrancar_servidor_hud():
    """Sirve la carpeta hud/ en localhost para que el HUD pueda leer estado.json.

    (Abierto como archivo suelto, el navegador bloquea esas lecturas; servido
    por http funcionan.) Si el puerto esta ocupado (otro JARVIS abierto),
    devuelve None y el HUD se abrira en modo estatico.
    """
    global _HUD_SERVIDOR
    try:
        servidor = ThreadingHTTPServer(
            ("127.0.0.1", HUD_PORT), partial(_HUDHandler, directory=str(HUD_DIR))
        )
    except OSError:
        return None
    threading.Thread(target=servidor.serve_forever, daemon=True).start()
    _HUD_SERVIDOR = servidor
    return servidor

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


def _es_orden(texto_cmd: str, frases: tuple, max_palabras: int = 4) -> bool:
    """True si el texto (ya normalizado) es una ORDEN corta que contiene una frase.

    El limite de palabras evita falsos positivos por subcadena: sin el,
    preguntar 'que resolucion tiene mi pantalla' abria el HUD (contiene
    'pantalla') en vez de responder, y una frase larga con 'hasta luego'
    dentro cerraba la sesion sin querer.
    """
    if len(texto_cmd.split()) > max_palabras:
        return False
    return any(p in texto_cmd for p in frases)


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
#  MEMORIA PERSISTENTE entre reinicios (pedida por Luis, 2026-06-11).
#  Cada turno queda apuntado en un archivo al que solo se ANADE (nunca se
#  borra, regla de la casa); al abrir el cerebro se le inyectan las ultimas
#  lineas para que recuerde charlas anteriores aunque se reinicie el PC.
# ===========================================================================
MEMORIA_PATH = PROJECT_DIR / "voz" / "memoria_charlas.md"
MEMORIA_INYECTAR = 4000  # ultimos caracteres que se recuerdan al arrancar


def _guardar_en_memoria(pregunta: str, respuesta: str) -> None:
    """Apunta un turno de charla en el archivo de memoria (solo anade)."""
    try:
        marca = time.strftime("%Y-%m-%d %H:%M")
        with open(MEMORIA_PATH, "a", encoding="utf-8") as f:
            f.write(f"[{marca}] {USER_NAME}: {pregunta}\n")
            f.write(f"[{marca}] Jarvis: {respuesta[:400]}\n")
    except OSError as e:
        print(f"[memoria] No se pudo guardar el turno: {e}", flush=True)


def _prompt_memoria() -> str:
    """Trozo de prompt con lo ultimo hablado, para inyectar al abrir el cerebro."""
    try:
        memoria = MEMORIA_PATH.read_text(encoding="utf-8", errors="replace").strip()
    except OSError:
        return ""
    if not memoria:
        return ""
    return (
        f"\nMEMORIA DE CHARLAS ANTERIORES con {USER_NAME} (sobrevive a "
        "reinicios; lo mas reciente esta al final). Usala cuando venga al caso "
        "y no digas que no recuerdas lo que aparece aqui:\n"
        + memoria[-MEMORIA_INYECTAR:]
    )


# ===========================================================================
#  AUTOMEJORA CON GATE HUMANO (pedida por Luis, 2026-06-12).
#  JARVIS apunta sus fallos y las correcciones de Luis en un archivo de
#  lecciones (solo ANADIR, nunca borrar). El NO se auto-modifica: Luis le
#  dice a Claude Code "revisa las lecciones de Jarvis", Claude propone las
#  mejoras concretas y Luis las aprueba antes de tocar codigo (gate humano).
# ===========================================================================
LECCIONES_PATH = PROJECT_DIR / "voz" / "lecciones_jarvis.md"


def _apuntar_leccion(tipo: str, detalle: str) -> None:
    """Apunta una leccion de automejora (solo anade; el archivo nunca se borra)."""
    try:
        nuevo = not LECCIONES_PATH.exists()
        with open(LECCIONES_PATH, "a", encoding="utf-8") as f:
            if nuevo:
                f.write(
                    "# Lecciones de JARVIS — automejora con gate humano\n"
                    "# Fallos y correcciones apuntados en vivo. Para convertirlos\n"
                    "# en mejoras: decir a Claude Code 'revisa las lecciones de\n"
                    "# Jarvis'. NUNCA borrar lineas: una leccion resuelta se marca\n"
                    "# anadiendo debajo una linea 'TRATADA [fecha]: que se hizo'.\n\n"
                )
            marca = time.strftime("%Y-%m-%d %H:%M")
            detalle = " ".join(detalle.split())[:300]
            f.write(f"[{marca}] {tipo}: {detalle}\n")
        print(f"[leccion] {tipo}: {detalle[:80]}", flush=True)
    except OSError as e:
        print(f"[leccion] No se pudo apuntar: {e}", flush=True)


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

    def __init__(self, ninos: bool = False) -> None:
        self._client = None
        self._ninos = ninos

    async def _opciones(self):
        """Opciones del SDK para este cerebro. Las subclases la sobreescriben."""
        from claude_agent_sdk import ClaudeAgentOptions

        if self._ninos:
            # SEGURIDAD INFANTIL: sin web ni herramientas. Travis solo conversa,
            # asi no hay riesgo de contenido inapropiado desde internet.
            return ClaudeAgentOptions(
                model=os.getenv("CHAT_MODEL", "claude-sonnet-4-6"),
                system_prompt=TRAVIS_SYSTEM_PROMPT,
                allowed_tools=[],
                permission_mode="dontAsk",
                cwd=str(PROJECT_DIR),
                max_turns=8,
            )
        # JARVIS por defecto: charla + busqueda web (solo lectura, no toca el PC).
        return ClaudeAgentOptions(
            model=os.getenv("CHAT_MODEL", "claude-sonnet-4-6"),
            system_prompt=JARVIS_PROMPT_CHARLA + _prompt_memoria(),
            allowed_tools=["WebSearch", "WebFetch"],
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
            # Nos quedamos con el ULTIMO MENSAJE que tenga texto, pero entero:
            # antes se guardaba solo el ultimo bloque y, si la respuesta final
            # venia en varios bloques, se perdia todo menos el final.
            partes = [t for block in content if (t := getattr(block, "text", None))]
            if partes:
                respuesta = " ".join(partes)
        final = (respuesta or f"Ahora mismo no puedo responder, {USER_NAME}.").strip()
        # AUTOMEJORA: si el cerebro marco una leccion ('LECCION: ...' al final,
        # tambien acepta la tilde), se aparta al archivo y NO se dice en voz alta.
        partes_leccion = re.split(r"LECCI[OÓ]N:\s*", final, maxsplit=1)
        if len(partes_leccion) == 2 and not self._ninos:
            final = partes_leccion[0].strip() or f"Apuntado, {USER_NAME}."
            _apuntar_leccion("CORRECCION", partes_leccion[1].strip())
        # El modo ninos NO guarda memoria: las charlas de un nino no se
        # registran en archivo (privacidad; decision deliberada).
        if not self._ninos:
            _guardar_en_memoria(texto, final)
        return final

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
            _es_archivo_protegido,
            _is_inside_project,
            comando_prohibido,
        )

        ask_human = self._ask_human

        async def can_use_tool(tool_name: str, input_data: dict, context) -> object:
            # REGLA FIJA: borrar esta prohibido SIEMPRE (ni se pregunta por voz;
            # asi un "si" despistado no puede borrar nada).
            motivo = comando_prohibido(tool_name, input_data)
            if motivo:
                return PermissionResultDeny(message=motivo, interrupt=False)
            # Escribir/editar DENTRO del proyecto -> auto-aprobado (autonomia con
            # limites), salvo archivos protegidos (.env, .git, lanzadores).
            ruta = input_data.get("file_path", "")
            if tool_name in SAFE_WRITE_TOOLS and _is_inside_project(ruta) and not _es_archivo_protegido(ruta):
                print(f"[auto] {tool_name} dentro del proyecto -> auto-aprobado: {ruta}", flush=True)
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
            system_prompt=JARVIS_PROMPT_ACTUAR + _prompt_memoria(),
            allowed_tools=list(AUTO_APPROVED_TOOLS),
            permission_mode="default",  # lo no pre-aprobado cae en can_use_tool
            can_use_tool=can_use_tool,
            cwd=str(PROJECT_DIR),
            max_turns=12,
        )


# ===========================================================================
#  VOZ DE SALIDA: pyttsx3 (local, gratis). Provisional hasta ElevenLabs.
# ===========================================================================
class CerebroLocal:
    """Cerebro de RESPALDO sin Claude (pedido por Luis, 12-jun): Ollama local.

    Si Claude (o internet) falla, JARVIS no se queda mudo: responde charla
    basica con un modelo local corriendo en la grafica del PC. Limites
    DELIBERADOS de este modo (el modelo los conoce y los dice): sin busqueda
    web, sin datos actuales, sin tocar el PC, respuestas cortas. El modo
    ninos NO usa este respaldo (un modelo local no garantiza filtro
    infantil). Modelo en JARVIS_CEREBRO_LOCAL (defecto llama3.1:8b, ya
    descargado); Ollama arranca con Windows. Llamada HTTP bloqueante con
    timeout — el bucle de voz es sincrono en este punto, no hace falta async.
    """

    def __init__(self) -> None:
        self._url = os.getenv("JARVIS_OLLAMA_URL", "http://127.0.0.1:11434")
        self._modelo = os.getenv("JARVIS_CEREBRO_LOCAL", "llama3.1:8b")
        self._historia: list[dict] = []

    def _sistema(self) -> str:
        return (
            f"Eres Jarvis, el asistente de voz de {USER_NAME}, funcionando en "
            "MODO LOCAL de emergencia porque tu cerebro principal esta caido. "
            "Hablas espanol, 2-3 frases como maximo (es voz). En este modo NO "
            "tienes internet, ni datos actuales, ni puedes tocar el ordenador: "
            "si te piden algo de eso, di que estas en modo local y que lo "
            "reintenten en un rato. NUNCA inventes datos: si no sabes algo, "
            "di que no lo sabes." + _prompt_memoria()
        )

    def preguntar(self, texto: str, timeout: int = 60) -> str:
        """Un turno contra Ollama. Devuelve '' si el local tampoco responde."""
        import urllib.request

        self._historia.append({"role": "user", "content": texto})
        cuerpo = json.dumps({
            "model": self._modelo,
            "stream": False,
            "messages": ([{"role": "system", "content": self._sistema()}]
                         + self._historia[-10:]),
            "options": {"num_predict": 220},
            # Mantener el modelo cargado en la grafica: la 1a respuesta tarda
            # ~45s (carga); con esto las siguientes bajan a pocos segundos.
            "keep_alive": "30m",
        }).encode("utf-8")
        try:
            peticion = urllib.request.Request(
                self._url + "/api/chat", data=cuerpo,
                headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(peticion, timeout=timeout) as r:
                contenido = json.loads(r.read().decode("utf-8"))
            respuesta = (contenido.get("message") or {}).get("content", "").strip()
        except Exception as e:
            print(f"[local] Ollama tampoco responde: {e}", flush=True)
            self._historia.pop()  # el turno no llego a existir
            return ""
        if respuesta:
            self._historia.append({"role": "assistant", "content": respuesta})
            _guardar_en_memoria(texto, f"(modo local) {respuesta}")
        return respuesta


def _texto_hablable(texto: str) -> str:
    """Limpia marcas de escritura (markdown) que el altavoz leeria en voz alta.

    El cerebro a veces responde con enlaces [nombre](url), negritas o vinetas;
    leidos tal cual suenan a ruido ("corchete, hache te te pe ese..."). Se deja
    solo el texto que un humano diria. Se imprime/loguea el original entero.
    """
    t = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", texto)     # [nombre](url) -> nombre
    t = re.sub(r"https?://\S+", "", t)                      # URLs sueltas, fuera
    t = re.sub(r"[*_`#|]+", " ", t)                         # negritas/titulos/tablas
    t = re.sub(r"^\s*[-•]\s+", "", t, flags=re.MULTILINE)   # vinetas
    return re.sub(r"[ \t]+", " ", t).strip()


class Voz:
    """Voz de salida. Cada frase se dice en un PROCESO APARTE (voz/_tts_worker.py).

    Motivo: pyttsx3 enmudece al reutilizar el motor dentro del bucle principal;
    en un proceso suelto suena siempre. La deteccion de la voz espanola se hace
    una sola vez aqui (solo lee la lista de voces, no reproduce nada).
    """

    def __init__(self, nombre: str = "JARVIS") -> None:
        self.nombre = nombre
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

    def _trocear_frases(self, texto: str) -> list[str]:
        """Divide en trozos de frase (~60+ caracteres) para la tuberia de voz."""
        partes = re.split(r"(?<=[.!?…:])\s+", texto)
        trozos: list[str] = []
        actual = ""
        for p in partes:
            actual = f"{actual} {p}".strip()
            if len(actual) >= 60:
                trozos.append(actual)
                actual = ""
        if actual:
            trozos.append(actual)
        return trozos

    def _worker_run(self, texto: str, solo_sintetizar: bool = False) -> None:
        args = [sys.executable, self._worker, str(self.rate), self.voice_id]
        if solo_sintetizar:
            args.append("solo")
        subprocess.run(args, input=texto.encode("utf-8"),
                       timeout=max(60, len(texto) // 8))

    def decir(self, texto: str) -> None:
        print(f"{self.nombre}> {texto}", flush=True)
        hablado = _texto_hablable(texto)
        if not hablado:
            return
        try:
            # TUBERIA DE FRASES: en respuestas largas, empieza a HABLAR la
            # primera frase mientras las siguientes se sintetizan en paralelo
            # (quedan en el cache; al llegarles el turno suenan al instante).
            # Antes se sintetizaba el texto entero antes de abrir la boca.
            frases = self._trocear_frases(hablado) if len(hablado) > 160 else [hablado]
            for frase in frases[1:]:
                threading.Thread(target=self._worker_run, args=(frase, True),
                                 daemon=True).start()
            for frase in frases:
                self._worker_run(frase)
        except Exception as e:  # si el TTS falla, JARVIS ya respondio por texto
            print(f"[voz] No se pudo reproducir la voz: {e}", flush=True)


# ===========================================================================
#  OIDO: Porcupine (palabra "Jarvis") + grabacion del mandato + Whisper.
# ===========================================================================
def _cargar_whisper():
    from faster_whisper import WhisperModel
    tamano = os.getenv("WHISPER_MODEL", "small")  # tiny/base/small/medium
    print(f"[voz] Cargando modelo de voz->texto '{tamano}' (la 1a vez se descarga)...")
    # Todos los nucleos disponibles: la transcripcion era el grueso de los
    # ~6s de silencio entre que el usuario calla y JARVIS reacciona.
    return WhisperModel(tamano, device="cpu", compute_type="int8",
                        cpu_threads=max(4, os.cpu_count() or 4))


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
    if not audio_int16:  # silencio (ver _grabar_mandato): nada que transcribir
        return ""
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
    if GANANCIA == 1.0:
        return frame
    amplificado = np.clip(np.array(frame, dtype="float32") * GANANCIA, -32768, 32767)
    return amplificado.astype(np.int16).tolist()


def _drenar_microfono(recorder, segundos: float = 1.0) -> None:
    """Descarta ~1 s del audio acumulado mientras JARVIS hablaba (anti-eco).

    El altavoz suena mientras el PvRecorder sigue buffereando; sin esto,
    Whisper transcribiria la propia voz de JARVIS mezclada con la tuya.
    Se calcula en TIEMPO, no en frames: el tamano de frame depende del
    detector (Porcupine 512, openWakeWord 1280).
    """
    frames = max(1, int(segundos * SAMPLE_RATE / recorder.frame_length))
    for _ in range(frames):
        recorder.read()


def _grabar_mandato(recorder):
    """Graba lo que dices tras 'Jarvis' hasta que te callas (o 8 s)."""
    import numpy as np
    umbral = int(os.getenv("JARVIS_SILENCIO", "350"))   # nivel de silencio (RMS)
    max_seg = 8.0
    # 0.9s de silencio = fin de frase (era 1.1; cada decima cuenta en voz).
    silencio_fin = float(os.getenv("JARVIS_SILENCIO_FIN", "0.9"))
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
    if not hubo_voz:
        # Solo hubo silencio: NO se lo pases a Whisper. Con silencio, Whisper
        # ALUCINA frases enteras ("Subtitulos por la comunidad...", o un "Si")
        # — inaceptable justo donde se decide un permiso por voz.
        return []
    return audio


def _elegir_microfono() -> int:
    """Devuelve el indice del microfono a usar, eligiendo POR NOMBRE si se puede.

    Los indices de microfono CAMBIAN al reiniciar Windows o cuando arrancan
    dispositivos virtuales (leccion del 2026-06-11: tras un reinicio, el [0]
    paso de ser el micro USB real al micro virtual de Steam, que solo da
    silencio — JARVIS quedo sordo sin error alguno). JARVIS_MIC en .env busca
    por nombre; MIC_INDEX queda solo como respaldo.
    """
    from pvrecorder import PvRecorder

    dispositivos = PvRecorder.get_available_devices()
    for i, nombre in enumerate(dispositivos):
        print(f"[voz] Micro disponible: [{i}] {nombre}")
    buscado = os.getenv("JARVIS_MIC", "").strip().lower()
    if buscado:
        for i, nombre in enumerate(dispositivos):
            if buscado in nombre.lower():
                print(f"[voz] Microfono elegido por nombre '{buscado}': [{i}] {nombre}")
                return i
        print(f"[voz] AVISO: ningun microfono contiene '{buscado}'; uso MIC_INDEX.")
    indice = int(os.getenv("MIC_INDEX", "-1"))
    detalle = dispositivos[indice] if 0 <= indice < len(dispositivos) else "el de por defecto del sistema"
    print(f"[voz] Microfono por indice: [{indice}] {detalle}")
    return indice


def _nivel_microfono(recorder, frames: int = 15) -> float:
    """Nivel medio (RMS) de ~1 s de microfono. ~0 = micro mudo o mal elegido.

    Desecha el primer medio segundo: justo tras recorder.start() el flujo
    llega vacio y daba falsas alarmas de "micro mudo" (visto 2 veces el 11-12
    de junio con el micro funcionando perfectamente).
    """
    import numpy as np

    calentamiento = max(1, int(0.5 * SAMPLE_RATE / recorder.frame_length))
    for _ in range(calentamiento):
        recorder.read()
    total = 0.0
    for _ in range(frames):
        frame = np.array(_leer_amplificado(recorder), dtype="float32")
        total += float(np.sqrt(np.mean(np.square(frame))))
    return total / frames


def _motor_wake():
    """Elige el detector de palabra clave: (nombre, motor, frame_length, palabra).

    Historia (2026-06-11/12): el modelo openWakeWord 'hey_jarvis' esta entrenado
    con voces inglesas y puntuaba la pronunciacion espanola natural de Luis
    ("ey yarbis") a 0.16 sobre un umbral de 0.38 — sordo para el, mientras el
    audio de un juego puntuaba 0.32+. Porcupine (que traia 'jarvis' de fabrica)
    retiro su plan gratuito en 2026. Solucion: VOSK, un reconocedor de espanol
    local que transcribe el 'jarvis' espanol de Luis como "jarvis" (verificado
    con su grabacion real, 3/3). Orden: lo que fuerce JARVIS_WAKE_MOTOR; si no,
    Vosk si su modelo esta descargado; Porcupine si hay clave; openWakeWord.
    """
    eleccion = (os.getenv("JARVIS_WAKE_MOTOR") or "").strip().lower()
    ruta_vosk = Path(__file__).resolve().parent / "modelos" / "vosk-model-small-es-0.42"
    clave = (os.getenv("PICOVOICE_ACCESS_KEY") or "").strip()

    if eleccion in ("", "vosk") and ruta_vosk.is_dir():
        from vosk import KaldiRecognizer, Model, SetLogLevel

        SetLogLevel(-1)  # sin ruido de logs internos
        motor = KaldiRecognizer(Model(str(ruta_vosk)), SAMPLE_RATE)
        print("[voz] Detector: Vosk espanol — di 'Jarvis' (local, sin cuentas)")
        return "vosk", motor, FRAME_LENGTH, "Jarvis"
    if eleccion in ("", "porcupine") and clave:
        import pvporcupine

        sens = float(os.getenv("JARVIS_PORCUPINE_SENS", "0.6"))
        motor = pvporcupine.create(access_key=clave, keywords=["jarvis"],
                                   sensitivities=[sens])
        print(f"[voz] Detector: Porcupine, palabra 'jarvis' (sensibilidad {sens})")
        return "porcupine", motor, motor.frame_length, "Jarvis"
    print("[voz] Detector: openWakeWord 'hey_jarvis' (fragil con acento espanol).")
    return "oww", _cargar_wakeword(), FRAME_LENGTH, "Hey Jarvis"


def _juego_activo() -> str:
    """Devuelve el motivo si hay un juego en marcha en el PC, o '' si no.

    Dos senales baratas:
      1) Steam mantiene en el registro el id del juego abierto (RunningAppID).
      2) La ventana activa ocupa TODA la pantalla sin estar "maximizada"
         (juegos y video a pantalla completa; una ventana maximizada normal
         esta 'zoomed' y NO cuenta — si no, un navegador maximizado callaria
         a JARVIS).
    """
    try:
        import winreg

        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam") as k:
            if winreg.QueryValueEx(k, "RunningAppID")[0]:
                return "juego de Steam en marcha"
    except OSError:
        pass
    try:
        import ctypes
        import ctypes.wintypes

        u = ctypes.windll.user32
        h = u.GetForegroundWindow()
        if h and not u.IsZoomed(h):
            clase = ctypes.create_unicode_buffer(64)
            u.GetClassNameW(h, clase, 64)
            if clase.value not in ("Progman", "WorkerW", "Shell_TrayWnd"):
                r = ctypes.wintypes.RECT()
                u.GetWindowRect(h, ctypes.byref(r))
                if (r.left <= 0 and r.top <= 0
                        and r.right >= u.GetSystemMetrics(0)
                        and r.bottom >= u.GetSystemMetrics(1)):
                    return "aplicacion a pantalla completa"
    except Exception:
        pass
    return ""


class _SilencioJuego:
    """Modo silencio automatico: si alguien juega en el PC, JARVIS calla.

    Pedido por Luis (2026-06-12): el PC es compartido con su hijo; con un
    juego en marcha JARVIS no habla ni salta solo. Matiz (mismo dia): la
    palabra clave SI debe despertarlo durante el juego — silencio significa
    "no molestes", no "desconectado del todo". Con Vosk/Porcupine es seguro
    (solo disparan con la palabra literal); con openWakeWord NO se permite
    (el audio del juego le daba falsos despertares). Se comprueba cada ~5s;
    al entrar y salir del silencio no habla (seria molestar), solo lo anota
    en el log y en el HUD. Desactivable con JARVIS_SILENCIO_JUEGO=0 en .env.
    """

    def __init__(self, frame_length: int) -> None:
        self.activo = False
        self._frames = 0
        self._cada = max(1, int(5 * SAMPLE_RATE / frame_length))  # ~5 segundos
        self._on = (os.getenv("JARVIS_SILENCIO_JUEGO", "1").strip() != "0")

    def chequear(self) -> bool:
        """Llamar una vez por frame de audio. True = hay que callar."""
        if not self._on:
            return False
        self._frames += 1
        if self._frames >= self._cada:
            self._frames = 0
            motivo = _juego_activo()
            if motivo and not self.activo:
                self.activo = True
                print(f"[silencio] {motivo}: JARVIS calla "
                      "(decir 'Jarvis' lo despierta igualmente).", flush=True)
                _escribir_estado("silencio")
            elif not motivo and self.activo:
                self.activo = False
                print("[silencio] Fin del juego: JARVIS vuelve a escuchar.", flush=True)
                _escribir_estado("esperando")
        return self.activo


class _MicroMuerto(Exception):
    """El microfono lleva demasiado entregando silencio absoluto: reiniciar."""


class _VigiaOido:
    """Detector de microfono REALMENTE muerto (leccion sordera 12-jun;
    correccion por falsa alarma de silencio nocturno 13-jun).

    Un flujo de audio degradado entrega ceros SIN dar error: Jarvis se queda
    sordo y nadie lo nota (paso de 15:00 a 15:08 y hubo que reiniciar a mano).
    PERO mirar solo el VOLUMEN era un error: una habitacion en silencio de
    verdad tambien da niveles ~0 con un micro perfectamente sano. La noche del
    12->13 el vigia mato a Jarvis cada pocos minutos por silencio real (niveles
    0.00-1.84, fluctuando) hasta que el vigilante se rindio a las 03:17 y dejo
    a Jarvis apagado toda la mañana.

    Distincion clave: un micro MUERTO entrega una señal PLANA (todos los
    valores casi iguales, desviacion ~0); un micro VIVO, aun en una habitacion
    callada, tiene ruido de fondo que FLUCTUA (desviacion > 0). Por eso ahora
    se vigila la DESVIACION media por frame, no el volumen, y solo se da por
    muerto si el audio sigue plano durante varios MINUTOS seguidos (una noche
    silenciosa fluctua; un stream congelado no). Asi nunca mata por silencio.
    Umbrales en JARVIS_OIDO_PLANO (defecto 0.5) y JARVIS_OIDO_VENTANAS (10).
    """

    def __init__(self, frame_length: int) -> None:
        self._cada = max(1, int(30 * SAMPLE_RATE / frame_length))  # ~30s
        # Por debajo de esta desviacion el audio se considera PLANO (muerto).
        # El ruido de fondo de cualquier micro vivo la supera con holgura.
        self._plano = float(os.getenv("JARVIS_OIDO_PLANO", "0.5"))
        # Ventanas planas SEGUIDAS para darlo por muerto (10 * 30s = ~5 min).
        self._ventanas_muerto = max(2, int(os.getenv("JARVIS_OIDO_VENTANAS", "10")))
        self._suma_desv = 0.0
        self._suma_nivel = 0.0
        self._frames = 0
        self._muertas = 0

    def chequear(self, frame) -> None:
        import numpy as np

        arr = np.asarray(frame, dtype=np.float64)
        self._suma_desv += float(arr.std())          # fluctuacion = señal viva
        self._suma_nivel += float(np.abs(arr).mean())  # solo para el log
        self._frames += 1
        if self._frames < self._cada:
            return
        desviacion = self._suma_desv / self._frames
        nivel = self._suma_nivel / self._frames
        self._suma_desv = 0.0
        self._suma_nivel = 0.0
        self._frames = 0
        if desviacion >= self._plano:   # hay fluctuacion -> micro VIVO
            self._muertas = 0
            return
        self._muertas += 1
        print(f"[oido] 30s de audio PLANO (desv {desviacion:.2f}, nivel "
              f"{nivel:.2f}) — micro sospechoso "
              f"({self._muertas}/{self._ventanas_muerto}).", flush=True)
        if self._muertas >= self._ventanas_muerto:
            minutos = self._ventanas_muerto * 30 // 60
            raise _MicroMuerto(
                f"audio plano (desv {desviacion:.2f}) durante ~{minutos} min")


def _esperar_palabra_clave(recorder, motor_nombre: str, motor) -> bool:
    """Bloquea hasta oir la palabra clave. Devuelve True al detectarla.

    Porcupine: process() devuelve >= 0 en el frame donde reconoce 'jarvis'.
    openWakeWord: confianza 0..1 por frame; dispara al superar WAKE_UMBRAL.
    Con un juego en marcha (modo silencio), Vosk y Porcupine SIGUEN atentos a
    la palabra clave: decir 'Jarvis' lo despierta tambien durante la partida
    (al acabar el turno vuelve solo al silencio). openWakeWord en cambio queda
    sordo durante el juego: el audio del juego le provocaba falsos despertares.
    Lanza KeyboardInterrupt hacia arriba si el usuario pulsa Ctrl+C.
    """
    import numpy as np

    silencio = _SilencioJuego(recorder.frame_length)
    oido = _VigiaOido(recorder.frame_length)
    reanudar = False  # tras el silencio, limpiar el motor (no arrastrar juego)

    if motor_nombre == "vosk":
        # Reconocedor espanol continuo: dispara si en lo dicho aparece "jarvis"
        # (asi escribe Vosk la pronunciacion espanola; verificado con audio real).
        while True:
            frame = _leer_amplificado(recorder)
            oido.chequear(frame)
            callado = silencio.chequear()
            datos = np.array(frame, dtype=np.int16).tobytes()
            if motor.AcceptWaveform(datos):
                texto = json.loads(motor.Result()).get("text", "")
            else:
                texto = json.loads(motor.PartialResult()).get("partial", "")
            if "jarvis" in texto:
                motor.Reset()  # que el proximo turno empiece de cero
                if callado:
                    print("[silencio] Palabra clave durante el juego: "
                          "JARVIS despierta para este turno.", flush=True)
                return True

    if motor_nombre == "porcupine":
        while True:
            frame = _leer_amplificado(recorder)
            oido.chequear(frame)
            callado = silencio.chequear()
            if motor.process(frame) >= 0:
                if callado:
                    print("[silencio] Palabra clave durante el juego: "
                          "JARVIS despierta para este turno.", flush=True)
                return True

    motor.reset()  # limpia el buffer interno para no arrastrar audio viejo
    while True:
        frame = _leer_amplificado(recorder)
        oido.chequear(frame)
        if silencio.chequear():
            reanudar = True
            continue
        if reanudar:
            motor.reset()
            reanudar = False
        arr = np.array(frame, dtype=np.int16)
        puntuaciones = motor.predict(arr)
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

        def _preguntar_por_voz() -> str:
            voz.decir(f"Necesito tu permiso para usar {tool_name}{sobre}. ¿Lo autorizas?")
            _drenar_microfono(recorder)
            audio = _grabar_mandato(recorder)
            return _transcribir(whisper, audio)

        # En un hilo aparte: hablar + grabar + transcribir tarda muchos segundos
        # y es codigo SINCRONO. Hecho directamente aqui congelaria el event loop
        # entero (incluida la conexion del SDK) mientras dura el permiso.
        respuesta = await asyncio.get_running_loop().run_in_executor(None, _preguntar_por_voz)
        print(f"[permiso] {USER_NAME} respondio: {respuesta!r}", flush=True)
        if _es_afirmativo(respuesta):
            voz.decir("Hecho.")
            return {"approved": True, "reason": f"Autorizado por voz: '{respuesta}'"}
        voz.decir("Entendido, no lo hago.")
        return {"approved": False, "reason": f"Denegado por voz: '{respuesta}'"}

    return ask_human


def _abrir_hud(voz: "Voz") -> None:
    """Abre la interfaz visual (HUD) de JARVIS en el navegador.

    Si el mini-servidor esta activo se abre la version EN VIVO (muestra estado
    y conversacion); si no, el archivo estatico de siempre.
    """
    voz.decir("Mostrando mi interfaz.")
    destino = HUD_URL if _HUD_SERVIDOR is not None else HUD_PATH
    try:
        os.startfile(destino)  # Windows: navegador por defecto
    except Exception as e:
        print(f"[hud] No se pudo abrir la interfaz: {e}")
        voz.decir("No he podido abrir la interfaz.")


def _asegurar_instancia_unica():
    """Cerrojo de instancia unica: un socket local que solo UN proceso puede abrir.

    Doble clic repetido en JARVIS.lnk creaba varios JARVIS a la vez peleando
    por el microfono (el 2026-06-11 llego a haber tres). El primero abre este
    puerto y lo retiene mientras vive; los siguientes no pueden abrirlo y se
    retiran avisando por voz, con salida 0 para que su watchdog
    (Iniciar-Jarvis.ps1) no los reintente. Devuelve el socket (hay que
    mantenerlo vivo toda la sesion) o None si ya hay otro JARVIS.
    """
    import socket

    candado = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        candado.bind(("127.0.0.1", int(os.getenv("JARVIS_PUERTO_UNICO", "8766"))))
        candado.listen(1)
        return candado
    except OSError:
        candado.close()
        return None


def bucle_jarvis(actuar: bool = False, ninos: bool = False) -> None:
    """Bucle principal: escucha 'Jarvis', te entiende y te responde por voz.

    El audio es sincrono; el cerebro vive en un unico event loop persistente
    para conservar la conexion del SDK (y con ella la memoria de la charla).
    `actuar`: JARVIS puede tocar el PC pidiendo permiso por voz.
    `ninos`:  persona Travis para ninos (sin internet ni herramientas).
    """
    from pvrecorder import PvRecorder

    if ninos and actuar:
        print("[seguridad] El modo ninos NO puede actuar sobre el PC; se ignora --actuar.")
        actuar = False
    nombre = "Travis" if ninos else "Jarvis"

    voz = Voz(nombre=nombre.upper())

    # ANTES de cargar nada pesado: si ya hay un JARVIS, este sobra y se va.
    candado = _asegurar_instancia_unica()
    if candado is None:
        print("[voz] Ya hay otro JARVIS en marcha: este duplicado se cierra solo.", flush=True)
        voz.decir(f"Tranquilo, {USER_NAME}: ya estoy en marcha. No hace falta abrirme otra vez.")
        return  # salida normal (codigo 0): el watchdog tampoco lo reintenta

    whisper = _cargar_whisper()
    motor_nombre, motor, frame_len, palabra = _motor_wake()
    _arrancar_servidor_hud()  # HUD en vivo (di "muestrate" para abrirlo)
    _escribir_estado("esperando")

    # El tamano de frame lo dicta el detector (Porcupine 512, openWakeWord 1280).
    recorder = PvRecorder(device_index=_elegir_microfono(),
                          frame_length=frame_len)

    # Un unico event loop para toda la sesion: lo necesita el cerebro persistente.
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    if actuar:
        ask_human = _hacer_ask_human_voz(voz, recorder, whisper)
        cerebro: Cerebro = CerebroActuador(ask_human)
    else:
        cerebro = Cerebro(ninos=ninos)
    # Respaldo sin Claude (modo ninos NO lo usa: sin filtro infantil local).
    local = None if ninos else CerebroLocal()
    modo_local = False
    try:
        loop.run_until_complete(cerebro.abrir())
    except Exception as e:
        print(f"[cerebro] No se pudo abrir el cerebro principal: {e}", flush=True)
        modo_local = local is not None

    async def _reabrir_cerebro() -> None:
        """Cierra (si procede) y reabre el cerebro principal."""
        try:
            await cerebro.cerrar()
        except Exception:
            pass
        await cerebro.abrir()

    def _reconectar_cerebro(aviso: str) -> None:
        """Avisa por voz, cierra la sesion del cerebro y abre una nueva.

        La sesion nueva recarga la memoria persistente (memoria_charlas.md),
        asi que conserva lo ya hablado; puede perder solo el ultimo detalle.
        """
        voz.decir(aviso)
        try:
            loop.run_until_complete(cerebro.cerrar())
        except Exception:
            pass
        try:
            loop.run_until_complete(cerebro.abrir())
            voz.decir("Listo. Conservo mis notas de la charla. Repiteme lo ultimo, por favor.")
        except Exception as e2:
            print(f"[cerebro] No se pudo reconectar: {e2}", flush=True)
            voz.decir("No consigo reconectar. Revisa internet o reiniciame.")

    recorder.start()
    if actuar:
        modo = "ACTUAR (puede tocar el PC, pide permiso por voz)"
    elif ninos:
        modo = "NINOS (Travis: solo conversa, sin internet)"
    else:
        modo = "CHARLA (conversa y busca en la web)"
    print("\n========================================")
    print(f"  {nombre.upper()} activo - modo {modo}.")
    print(f"  Di '{palabra}' para despertarlo. (Ctrl+C para salir)")
    print("========================================\n")
    # Chequeo de oido al arrancar: un micro virtual (Steam/Oculus) da silencio
    # absoluto y JARVIS quedaria sordo SIN ningun error. Mejor avisar por voz.
    nivel = _nivel_microfono(recorder)
    if nivel < 2:
        # El flujo a veces tarda 1-2s en despertar tras reabrir el micro:
        # segunda oportunidad antes de dar la falsa alarma (vista 3 veces).
        time.sleep(1.5)
        nivel = _nivel_microfono(recorder)
    print(f"[voz] Nivel de ruido ambiente del micro: {nivel:.0f}", flush=True)
    if nivel < 2:
        voz.decir(f"Aviso, {USER_NAME}: no me llega ningun sonido del microfono. "
                  "Asi no podre oirte. Revisa que el micro este conectado.")
    voz.decir(f"Hola {USER_NAME}. Soy {nombre}. Te escucho.")
    if modo_local:
        voz.decir("Aviso: arranco sin mi cerebro principal, en modo local — "
                  "sin internet ni acciones hasta que se recupere.")

    try:
        while True:
            _esperar_palabra_clave(recorder, motor_nombre, motor)
            # Detectada la palabra clave.
            print("[voz] Te escucho...")
            _escribir_estado("escuchando")
            voz.decir("¿Sí?")
            _drenar_microfono(recorder)          # anti-eco: descarta el "¿Sí?"
            audio = _grabar_mandato(recorder)
            # (Hubo un bip aqui como "te he oido"; Luis pidio quitarlo 12-jun.)
            if not audio:
                # FALSO DESPERTAR (1a mejora salida de la automejora, aprobada
                # por Luis 12-jun): desperto por ruido de fondo y nadie hablo
                # despues. Antes interrumpia la habitacion con "No te he
                # entendido"; ahora vuelve a escuchar EN SILENCIO y lo apunta.
                _apuntar_leccion("FALSO DESPERTAR", "desperte sin que hubiera "
                                 "voz despues (ruido de fondo); volvi a "
                                 "escuchar sin hablar")
                _escribir_estado("esperando")
                continue
            texto = _transcribir(whisper, audio)
            if not texto:
                voz.decir(f"No te he entendido, {USER_NAME}.")
                _apuntar_leccion("OIDO", "capte voz tras la palabra clave "
                                 "pero no la entendi (ruido o transcripcion)")
                _escribir_estado("esperando")
                continue
            print(f"{USER_NAME}> {texto}")
            # Normalizado (sin tildes/signos): asi 'Adiós, Travis.' si coincide.
            texto_cmd = _normalizar(texto)
            if _es_orden(texto_cmd, FRASES_SALIR):
                voz.decir(f"Hasta luego, {USER_NAME}.")
                break
            if _es_orden(texto_cmd, FRASES_HUD):
                _abrir_hud(voz)
                _escribir_estado("esperando", texto)
                continue
            # "Apunta una leccion: ..." -> directo al archivo de automejora,
            # sin pasar por el cerebro (es una orden, no una pregunta).
            if texto_cmd.startswith("apunta") and "leccion" in texto_cmd:
                _apuntar_leccion(f"ORDEN DE {USER_NAME.upper()}", texto)
                voz.decir(f"Apuntado, {USER_NAME}. Lo revisaremos para mejorarme.")
                _escribir_estado("esperando", texto)
                continue
            _escribir_estado("pensando", texto)
            # MODO LOCAL (respaldo sin Claude, pedido por Luis 12-jun): si el
            # cerebro principal esta caido, primero se intenta recuperar (12s
            # como mucho) y, si no vuelve, responde el modelo local de Ollama.
            if modo_local and local is not None:
                try:
                    loop.run_until_complete(
                        asyncio.wait_for(_reabrir_cerebro(), timeout=12))
                    modo_local = False
                    voz.decir("Mi cerebro principal ha vuelto. Modo completo otra vez.")
                except Exception:
                    voz.decir("Dame unos segundos.")  # la carga local puede tardar
                    respuesta = local.preguntar(texto)
                    if respuesta:
                        _escribir_estado("hablando", texto, respuesta)
                        voz.decir(respuesta)
                        _drenar_microfono(recorder)
                        _escribir_estado("esperando", texto, respuesta)
                    else:
                        voz.decir(f"Sigo sin cerebro principal y el local "
                                  f"tampoco me responde, {USER_NAME}. Revisa "
                                  "internet u Ollama.")
                        _escribir_estado("esperando")
                    continue
            # Un fallo del cerebro (sin internet, error de API, SDK caido) NO
            # debe matar a JARVIS: es un asistente siempre activo. Se avisa por
            # voz, se reconecta y se sigue escuchando.
            #
            # Ademas, dos lecciones del 2026-06-11:
            #  - Una busqueda web tarda 20-60s; ese silencio parece averia. A los
            #    8s sin respuesta, JARVIS avisa de que sigue trabajando.
            #  - Sin limite de tiempo, un cuelgue del SDK dejaba a JARVIS mudo
            #    PARA SIEMPRE (sin excepcion no hay reconexion). Tope: 180s.
            t_pensando = time.time()
            tarea = loop.create_task(cerebro.preguntar(texto))
            try:
                try:
                    respuesta = loop.run_until_complete(
                        asyncio.wait_for(asyncio.shield(tarea), timeout=5))
                except asyncio.TimeoutError:
                    # Aviso EN PARALELO: antes voz.decir() congelaba el event
                    # loop (el cerebro no avanzaba mientras JARVIS hablaba) y
                    # sumaba ~3s a cada turno largo. En un hilo, el cerebro
                    # sigue trabajando mientras suena el aviso.
                    aviso = threading.Thread(
                        target=voz.decir,
                        args=("Dame unos segundos, lo estoy mirando.",),
                        daemon=True)
                    aviso.start()
                    respuesta = loop.run_until_complete(
                        asyncio.wait_for(tarea, timeout=180))
                    aviso.join(timeout=10)  # no pisar el aviso con la respuesta
            except asyncio.TimeoutError:
                print(f"[cerebro] COLGADO: sin respuesta tras {time.time() - t_pensando:.0f}s. Reconectando...", flush=True)
                tarea.cancel()
                try:
                    loop.run_until_complete(tarea)
                except Exception:
                    pass
                _apuntar_leccion("CEREBRO", f"colgado mas de 180s con: {texto}")
                _reconectar_cerebro("Me he atascado pensando, perdona. Reinicio mi cerebro.")
                _escribir_estado("esperando")
                continue
            except Exception as e:
                print(f"[cerebro] Error en el turno: {e}", flush=True)
                _apuntar_leccion("CEREBRO", f"error de conexion ({e}) con: {texto}")
                # Antes de molestar con una reconexion hablada, que el local
                # salve el turno; el proximo turno intentara volver a Claude.
                respuesta = local.preguntar(texto) if local is not None else ""
                if respuesta:
                    modo_local = True
                    voz.decir("Mi cerebro principal ha fallado; sigo contigo "
                              "en modo local, sin internet ni acciones. "
                              + respuesta)
                    _drenar_microfono(recorder)
                    _escribir_estado("esperando", texto, respuesta)
                else:
                    _reconectar_cerebro("He perdido la conexion con mi cerebro. Dame un momento, lo reinicio.")
                    _escribir_estado("esperando")
                continue
            t_respuesta = time.time() - t_pensando
            print(f"[cerebro] Respondio en {t_respuesta:.1f}s", flush=True)
            if t_respuesta > 45:
                _apuntar_leccion("LENTO", f"{t_respuesta:.0f}s en responder a: {texto}")
            _escribir_estado("hablando", texto, respuesta)
            voz.decir(respuesta)
            _drenar_microfono(recorder)  # anti-eco: que no se oiga a si mismo
            _escribir_estado("esperando", texto, respuesta)
    except KeyboardInterrupt:
        print("\n[voz] Cerrando JARVIS...")
    except _MicroMuerto as e:
        # Sordera silenciosa (leccion 12-jun): avisar y salir con codigo != 0
        # para que el vigilante relance a JARVIS con el audio reabierto.
        print(f"[oido] MICRO MUERTO ({e}). Salgo para que el vigilante "
              "me relance con el audio reabierto.", flush=True)
        _apuntar_leccion("SORDERA", f"micro muerto ({e}); me reinicie solo "
                         "via vigilante para reabrir el audio")
        voz.decir(f"{USER_NAME}, llevo varios minutos con el microfono "
                  "congelado. Me reinicio para arreglarlo.")
        sys.exit(3)
    finally:
        _escribir_estado("apagado")  # el HUD muestra DESCONECTADO, no "en linea"
        recorder.stop()
        recorder.delete()
        if motor_nombre == "porcupine":
            try:
                motor.delete()  # libera el detector nativo
            except Exception:
                pass
        loop.run_until_complete(cerebro.cerrar())
        loop.close()
        try:
            candado.close()  # libera el cerrojo de instancia unica
        except Exception:
            pass


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
        v.decir(f"Hola {USER_NAME}, soy Jarvis. La voz funciona.")
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


class _Espejo:
    """Duplica un flujo de salida: consola + archivo de registro con hora.

    En el archivo cada linea va precedida de [HH:MM:SS]; asi se puede medir
    despues cuanto tardo cada fase ("me hablo y no contesto" deja de ser un
    misterio). Si el archivo falla, la consola sigue funcionando igual.
    """

    def __init__(self, consola, archivo) -> None:
        self._consola = consola
        self._archivo = archivo
        self._inicio_linea = True

    def write(self, texto: str) -> None:
        self._consola.write(texto)
        try:
            for trozo in texto.splitlines(keepends=True):
                if self._inicio_linea:
                    self._archivo.write(time.strftime("[%H:%M:%S] "))
                self._archivo.write(trozo)
                self._inicio_linea = trozo.endswith("\n")
            self._archivo.flush()
        except Exception:
            pass

    def flush(self) -> None:
        self._consola.flush()
        try:
            self._archivo.flush()
        except Exception:
            pass

    def __getattr__(self, nombre):  # delega el resto (encoding, isatty...)
        return getattr(self._consola, nombre)


def _registrar_a_archivo() -> None:
    """Todo lo que JARVIS imprime queda tambien en logs/jarvis.log.

    El watchdog (Iniciar-Jarvis.ps1) solo anota CAIDAS; un fallo que no tumba
    el proceso (cuelgue, TTS mudo, error de herramienta) no dejaba rastro.
    """
    try:
        carpeta = PROJECT_DIR / "logs"
        carpeta.mkdir(exist_ok=True)
        archivo = open(carpeta / "jarvis.log", "a", encoding="utf-8", errors="replace")
        archivo.write(f"\n===== JARVIS arranca {time.strftime('%Y-%m-%d %H:%M:%S')} =====\n")
        sys.stdout = _Espejo(sys.stdout, archivo)
        sys.stderr = _Espejo(sys.stderr, archivo)
    except Exception:
        pass  # sin registro se sigue funcionando igual


def main() -> None:
    _consola_utf8()
    _registrar_a_archivo()
    parser = argparse.ArgumentParser(description="JARVIS — capa de voz")
    parser.add_argument("--check", action="store_true", help="prueba las piezas sin arrancar el bucle")
    parser.add_argument("--actuar", action="store_true", help="permite que JARVIS actue sobre el PC (pide permiso por voz)")
    parser.add_argument("--ninos", action="store_true", help="modo Travis para ninos: solo conversa, sin internet ni herramientas")
    args = parser.parse_args()
    if args.check:
        autoprueba()
    else:
        bucle_jarvis(actuar=args.actuar, ninos=args.ninos)


if __name__ == "__main__":
    main()
