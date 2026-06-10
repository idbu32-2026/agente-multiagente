# Mapa de tecnologías — proyecto Jarvis

Guía práctica: "si quieres hacer *esto*, usa *esta* tecnología, y por *este* motivo".
Aterrizado en este proyecto, no en teoría genérica.

Leyenda: ✅ ya lo usas (bien) · ⚠️ decisión pendiente · 🔒 seguridad.

---

## 1. Comunicación entre piezas (cómo "se hablan" los programas)

| Si quieres... | Usa | Por qué / cuándo NO |
|---|---|---|
| Pedir algo y recibir una respuesta puntual | **HTTP REST** | Simple, universal. ✅ `GET /`, `POST /whatsapp`. NO para tiempo real continuo. |
| Flujo en tiempo real en los dos sentidos (el agente te habla mientras piensa) | **WebSocket** | ✅ Tu `/ws`. Correcto para chat/voz en vivo. |
| Lanzar trabajo a otro programa sin esperar (encolar) | **Cola de mensajes** (Redis, RabbitMQ) | Para "siempre activo". Probable fase 3-4. |
| Conectar IA con herramientas externas de forma estándar | **MCP** | ✅ `skills.py`. Estándar para dar herramientas a Claude. |

**Regla:** respuesta única → REST · conversación viva → WebSocket · trabajo diferido → cola.

---

## 2. Voz (el corazón de Jarvis)

| Si quieres... | Usa | Por qué |
|---|---|---|
| Detectar "hey jarvis" sin coste ni cuenta | **openWakeWord** | ✅ Local, gratis, sin clave. |
| Voz → texto, en tu PC, sin internet | **faster-whisper** | ✅ Privado y bueno. Sube `small`→`medium` solo si falla. |
| Texto → voz provisional y gratis | **pyttsx3** | ✅ Robótico pero offline. |
| Texto → voz natural calidad "Jarvis" | **ElevenLabs** (nube, pago) o **Piper** (local, gratis) | ⚠️ Fase posterior. Piper = gratis+offline; ElevenLabs = impresiona. |

---

## 3. Servidor / backend

| Si quieres... | Usa | Por qué |
|---|---|---|
| API web en Python moderna y rápida | **FastAPI** | ✅ Elección correcta hoy. |
| Un script mínimo de 1 página | Flask | Más simple pero sin async ni WebSocket nativo. No cambies. |
| Servicio que arranca con Windows y se reinicia solo | **Tarea Programada de Windows** o **NSSM** | ⚠️ Fase 3 "siempre activo". NSSM = tu script como servicio real. |

---

## 4. Memoria / persistencia (dónde guarda lo que aprende)

| Si quieres... | Usa | Por qué |
|---|---|---|
| Guardar pocas notas/habilidades legibles | **Archivos** (markdown/JSON) | ✅ Tu `skills/`. Perfecto para pocas cosas y que tú las leas. |
| Muchos datos con búsquedas/relaciones | **SQLite** | Un archivo, cero instalación. El salto cuando los archivos se quedan cortos. |
| Recordar por significado ("¿qué dije del coche?") | **Base vectorial** (Chroma, local) | Probable, memoria larga. Aún no. |

**Regla:** archivos → SQLite cuando duela → vectorial solo si necesitas "buscar por idea, no palabra exacta". No te saltes pasos.

---

## 5. Agentes IA / orquestación

| Si quieres... | Usa | Por qué |
|---|---|---|
| Asistente con subagentes y herramientas | **Claude Agent SDK** | ✅ Tu base. |
| Elegir modelo por tarea | **Opus** (razonar/orquestar) · **Sonnet** (trabajo general) · **Haiku** (rápido/barato, leer) | ✅ Ya repartido así en `orchestrator.py`. |

---

## 6. Seguridad / control de acceso 🔒

| Si quieres... | Usa | Por qué |
|---|---|---|
| Que solo tú mandes a Jarvis | **Token simple** en cada petición | Mínimo imprescindible al salir de `localhost`. |
| Bloquear comandos peligrosos por código | **Lista blanca/negra** de comandos | ⚠️ CRÍTICO: la regla "nunca borrar" debe vivir aquí, no en un modal. |
| Validar que un WhatsApp es de verdad tuyo | **Firma de Twilio** | Falta hoy en el webhook. |

---

## La idea de fondo

**La mejor tecnología es casi siempre la más simple que resuelve el problema de HOY.**
El mayor riesgo no es elegir mal una herramienta, es meter herramientas para problemas que aún no tienes (cola, vectorial, HUD...).

> No metas una tecnología hasta que el dolor de no tenerla sea real.

Decisiones reales pendientes en este proyecto: **TTS de calidad**, **arranque con Windows** y **seguridad por código**. El resto (FastAPI, WebSocket, openWakeWord, Claude SDK) ya está bien elegido.
