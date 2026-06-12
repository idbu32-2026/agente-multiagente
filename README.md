# JARVIS — Asistente de voz siempre activo sobre un sistema multiagente

> ⚖️ **Código publicado solo para su lectura (portafolio). Todos los derechos
> reservados — ver [LICENSE.md](LICENSE.md). No está permitido copiarlo ni
> reutilizarlo sin permiso escrito del autor.**

Un "Jarvis" real para el PC: un asistente de voz **siempre encendido** que
arranca con Windows, escucha la palabra clave "Jarvis", conversa con voz
neuronal, **busca en internet en tiempo real** y puede **actuar sobre el
ordenador** pidiendo permiso por voz antes de cada acción sensible. Por
debajo, un **sistema multiagente** construido sobre el Claude Agent SDK con
checkpoints humanos, interfaz web, avisos por WhatsApp y un **bucle de
automejora con aprobación humana**.

Todo funciona en un PC doméstico con Windows, compartido con un niño que
juega en él — y esa restricción real moldeó el diseño (modo silencio
automático durante las partidas, reglas de seguridad no negociables).

---

## Cómo está construido

### 1. Capa de voz (`voz/jarvis_voz.py`)

```
micrófono ──> wake word ──> grabación ──> Whisper ──> cerebro (Claude) ──> TTS
   (pvrecorder)  (Vosk es)    (fin por        (faster-      (Agent SDK,        (edge-tts,
                              silencio)       whisper)      web + acciones)    voz neural)
```

- **Wake word con Vosk** (reconocedor español 100% local, sin cuentas ni
  claves): transcribe el audio en continuo y dispara al oír "Jarvis". Se
  eligió tras comprobar que openWakeWord puntuaba bajo con acento español y
  que Porcupine retiró su plan gratuito. El motor es conmutable
  (`JARVIS_WAKE_MOTOR`): vosk / porcupine / openwakeword.
- **Micrófono elegido por nombre, no por índice** (`JARVIS_MIC=usb audio`):
  los índices de micro cambian al reiniciar Windows; elegir por nombre
  sobrevive a reinicios. Al arrancar se mide el nivel ambiente y, si el
  micro está mudo, Jarvis lo AVISA por voz.
- **Transcripción** con faster-whisper (modelo `small`, multi-hilo). El
  silencio puro se descarta sin pasar por Whisper (alucinaba frases con
  silencio — peligroso cuando la frase es un permiso).
- **Voz de salida**: edge-tts (voz neuronal es-ES en español), con tubería
  por frases — se sintetiza la frase siguiente mientras suena la actual — y
  caché de mp3. Fallback automático a pyttsx3 sin red.
- **Robustez de asistente "siempre activo"**: timeouts con reconexión del
  cerebro (un cuelgue del SDK no lo mata), aviso hablado a los pocos
  segundos en búsquedas largas, anti-eco tras hablar, candado de instancia
  única por socket local (un segundo Jarvis se retira solo), log con marca
  de tiempo de todo lo que ocurre y un vigilante PowerShell que lo
  resucita si se cae (con contador de caídas que se auto-resetea).

### 2. Cerebro (Claude Agent SDK)

- Sesión **persistente** (`ClaudeSDKClient`): mantiene el contexto entre
  turnos sin arranque en frío.
- **Memoria entre reinicios**: cada turno se apunta en un archivo (solo
  añadir) y al abrir el cerebro se inyectan las últimas líneas en el system
  prompt — Jarvis recuerda conversaciones de días anteriores.
- **Búsqueda web por defecto**: el prompt ordena buscar cualquier dato del
  mundo real antes de responder ("ante la duda, busca") y le prohíbe alegar
  información desactualizada.
- **Protocolo de precisión** integrado en el prompt: clasificar
  hecho/inferencia/posibilidad/desconocido, no inventar, declarar
  incertidumbre con una palabra, citar la fuente, corregirse sin excusas,
  respuestas cortas (es voz).
- **Modos**: charla (busca, no toca el PC) · actuar (puede crear/editar
  archivos y ejecutar tareas, con permiso por voz) · niños (sin internet ni
  herramientas, lenguaje literal y calmado, sin memoria — privacidad).

### 3. Seguridad (en código, no en el prompt)

- **Borrar está prohibido SIEMPRE**: un filtro deniega comandos de borrado
  (rm/del/rmdir/format...) sin preguntar, acepta falsos positivos a
  propósito (fail-safe). La alternativa del agente es mover a una carpeta
  "Para revisar".
- **Archivos protegidos**: tocar `.env*`, `.git/`, lanzadores `.ps1/.bat/
  .lnk` exige aprobación humana aunque la acción esté dentro del proyecto
  (el agente no puede reescribir su propio arranque sin permiso).
- **Permisos por voz**: en modo actuar, las acciones con efectos se leen en
  voz alta y solo continúan con un "sí" claro; ante silencio o duda, se
  deniega.
- **Modo silencio automático**: si hay un juego en marcha (registro de
  Steam o ventana a pantalla completa), Jarvis ni salta ni habla — pero la
  palabra clave sí puede despertarlo a propósito.

### 4. Sistema multiagente con checkpoints humanos (`backend/`)

```
Navegador (frontend/index.html)
   | WebSocket /ws
Servidor FastAPI (backend/main.py)
   |
Orquestador (Opus)  -- backend/orchestrator.py
   |- investigador (Haiku)  -> Read/Grep/Glob/WebSearch/WebFetch
   |- planificador (Sonnet) -> Read/Grep/Glob
   |- ejecutor (Sonnet)     -> Write/Edit/Bash  (pasan por checkpoint)

Checkpoints humanos -> can_use_tool() -> backend/approvals.py -> navegador
```

- Las herramientas de **solo lectura se auto-aprueban**; todo lo demás
  dispara `can_use_tool`, que pregunta en el navegador (cola de
  aprobaciones para peticiones simultáneas) y espera la decisión.
- **Avisos por WhatsApp** (Twilio): cuando hace falta aprobar algo y cuando
  termina una tarea. El webhook entrante **verifica la firma de Twilio**
  (HMAC) y solo acepta mensajes del número autorizado.
- Boletines programados (`news_digest.py`, `news_motogp.py`) reutilizan la
  misma base como agentes de tarea única.

### 5. HUD en vivo (`hud/`)

Interfaz visual estilo película con three.js (aros, núcleo, bloom): la capa
de voz sirve `hud/` en localhost y escribe su estado real
(esperando/escuchando/pensando/hablando/silencio) en un JSON que el HUD lee
cada 400 ms. El núcleo late según el estado, suelta un **fogonazo al
empezar a responder** y la luz **vibra al ritmo de la voz** mientras habla.
Se abre por voz ("Jarvis... muéstrate").

### 6. Automejora con gate humano (`voz/lecciones_jarvis.md`)

El bucle: **fallo → lección → propuesta → aprobación humana → cambio →
commit**.

- Jarvis **apunta solo** sus fallos: falsos despertares, transcripciones
  fallidas, cuelgues, respuestas lentas. Si el usuario le corrige, el
  cerebro añade una línea `LECCION:` que se extrae antes de hablar y va al
  archivo.
- Las lecciones **nunca se borran**: se marcan `TRATADA` o `DESCARTADA`.
- Un ingeniero (humano o agente de mantenimiento) agrupa las lecciones por
  causa raíz, propone mejoras concretas y **solo aplica lo aprobado**.
  Primera vuelta real del bucle: la minería de los propios logs reveló que
  un 37% de los despertares eran falsos y acababan con un "No te he
  entendido" en voz alta que interrumpía la habitación; la mejora aprobada
  fue callar y apuntar la lección.

### 7. Herramientas de diagnóstico (`voz/prueba_*.py`)

Cada problema real de producción dejó una herramienta reutilizable: probar
el cerebro por texto sin micro, grabar y puntuar la wake word con análisis
de tono, medir niveles de micrófono, validar el detector con un WAV, y
extraer el JS del HUD para chequearlo con node. Lección aprendida: dos
procesos leyendo el mismo micro se reparten los frames y ambos quedan medio
sordos — las pruebas de audio se hacen siempre con Jarvis parado.

---

## Stack

Python 3.12 · Claude Agent SDK (claude-sonnet-4-6 como cerebro de voz,
Opus en el orquestador web) · FastAPI + WebSocket · Vosk (wake word es) ·
faster-whisper · edge-tts · pvrecorder · three.js · Twilio (WhatsApp) ·
PowerShell (vigilante/arranque con Windows).

## Estado

Proyecto personal en uso diario real. Construido de forma incremental con
pruebas end-to-end en cada fase (la regla de la casa: no ampliar sin
probar lo anterior).

## Autor

Luis Durán Ibáñez — [@idbu32-2026](https://github.com/idbu32-2026)

*Desarrollado con Claude Code como copiloto de ingeniería.*
