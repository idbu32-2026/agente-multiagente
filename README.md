# Sistema Multiagente (semi-autonomo) sobre Claude Agent SDK

Sistema de IA con un **orquestador** que coordina **subagentes especializados**,
con **interfaz web** y **checkpoints humanos**: cualquier accion con efectos
(escribir, editar, ejecutar comandos) requiere tu aprobacion en el navegador.

## Arquitectura

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

- **Solo lectura** (`Read`, `Grep`, `Glob`, `WebSearch`, `WebFetch`) se auto-aprueban.
- **Todo lo demas** dispara `can_use_tool`, que pregunta al navegador y espera tu decision.
- `permission_mode="default"` para que los checkpoints funcionen y los subagentes
  no hereden un modo permisivo.

## Requisitos

- **Claude Code CLI instalado** y autenticado (el SDK lo descubre solo).
- Python 3.10+.

## Instalacion

```powershell
cd C:\Users\travi\proyectos\agente-multiagente
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
copy .env.example .env   # opcional: ajusta modelo/host/puerto
```

## Ejecutar

```powershell
python run.py
```

Abre http://127.0.0.1:8000, escribe un objetivo y aprueba/deniega las acciones
sensibles cuando aparezca el checkpoint.

## Archivos

| Archivo | Rol |
|---|---|
| `backend/orchestrator.py` | Define orquestador + subagentes y el callback de checkpoint |
| `backend/approvals.py` | Broker async de aprobaciones pendientes |
| `backend/notifier.py` | Notificaciones por WhatsApp via Twilio (opcional; no-op si no hay credenciales) |
| `backend/main.py` | Servidor FastAPI + WebSocket + traduccion de mensajes del SDK |
| `frontend/index.html` | UI: chat + modal de aprobacion |
| `run.py` | Lanzador |

## Avisos por WhatsApp (opcional, via Twilio)

El sistema puede avisarte por WhatsApp (1) cuando necesita tu aprobacion y
(2) cuando termina una tarea. Es opcional: sin credenciales, todo funciona
igual pero sin enviar mensajes.

**Alta (una vez):**
1. Crea una cuenta gratis en [twilio.com](https://www.twilio.com/).
2. En la consola: *Messaging -> Try it out -> Send a WhatsApp message*. Activa el sandbox.
3. Desde tu WhatsApp, envia `join <codigo>` al numero del sandbox que te muestran.
4. Copia tus credenciales en `.env` (`TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN`,
   `TWILIO_WHATSAPP_FROM`, `WHATSAPP_TO`).
5. Reinicia el servidor.

**Limitaciones honestas:**
- El sandbox de Twilio caduca tras 3 dias de inactividad (reenvia el `join`).
- Solo puede escribir a numeros que hayan hecho `join`.
- WhatsApp solo permite mensajes libres dentro de las 24 h desde tu ultimo
  mensaje; fuera de eso harian falta plantillas aprobadas por Meta.
- Responder "si/no" *desde* WhatsApp para aprobar requeriria exponer un webhook
  publico (p. ej. con un tunel ngrok). De momento apruebas en la web; el
  WhatsApp solo te avisa.

## Limitaciones / siguientes pasos

- El frontend es minimo (sin build, vanilla JS). Migrable a React si crece.
- Una sola sesion concurrente por conexion WebSocket.
- Los textos de subagentes individuales se ven a traves del orquestador; para
  trazas por subagente se pueden activar hooks `SubagentStart/Stop`.
