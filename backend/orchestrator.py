"""Configuracion del sistema multiagente sobre el Claude Agent SDK.

Arquitectura:
    Orquestador (Opus)  -- coordina y razona sobre los resultados
      |- investigador (Haiku)  -- recopila informacion (solo lectura/web)
      |- planificador (Sonnet) -- disena el plan de pasos
      |- ejecutor (Sonnet)     -- realiza cambios (Write/Edit/Bash)

Autonomia: semi-autonoma. Las herramientas de solo lectura se auto-aprueban;
cualquier accion con efectos (Write, Edit, Bash, etc.) pasa por un checkpoint
humano via el callback `can_use_tool`.
"""

from __future__ import annotations

import os
import re
from typing import Awaitable, Callable

from claude_agent_sdk import AgentDefinition, ClaudeAgentOptions

from .skills import TOOL_NAMES as SKILL_TOOL_NAMES
from .skills import build_skills_server, load_skills_index

# Raiz del proyecto (carpeta que contiene backend/ y frontend/).
PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Herramientas de solo lectura: seguras, se auto-aprueban sin molestar al humano.
AUTO_APPROVED_TOOLS = ["Read", "Grep", "Glob", "WebSearch", "WebFetch"]

# Herramientas de escritura que se auto-aprueban SOLO si el archivo esta dentro
# del proyecto (autonomia con limites). Borrar y comandos (Bash) NUNCA se
# auto-aprueban: siempre piden permiso humano.
SAFE_WRITE_TOOLS = {"Write", "Edit", "MultiEdit", "NotebookEdit"}


# REGLA FIJA DE LUIS (en codigo, no en un modal): el agente NUNCA borra.
# Comandos de borrado en Bash/PowerShell se deniegan automaticamente, sin
# siquiera preguntar — asi un "aprobar" despistado no puede borrar nada.
_PATRON_BORRADO = re.compile(
    r"\b(rm|del|erase|rmdir|rd|remove-item|ri|format|mklink\s+/d\s+\S+\s+nul)\b",
    re.IGNORECASE,
)

def comando_prohibido(tool_name: str, input_data: dict) -> str | None:
    """Devuelve el motivo si la accion viola una regla fija; None si es aceptable."""
    if tool_name == "Bash":
        comando = str(input_data.get("command", ""))
        if _PATRON_BORRADO.search(comando):
            return (
                "Regla fija: NUNCA borrar archivos ni carpetas. En su lugar, "
                "mueve lo que sobre a la carpeta 'Para revisar' y avisa al usuario."
            )
    return None


def _is_inside_project(path: str) -> bool:
    """True si `path` apunta a un archivo dentro de la carpeta del proyecto."""
    if not path:
        return False
    try:
        abs_path = path if os.path.isabs(path) else os.path.join(PROJECT_DIR, path)
        abs_path = os.path.abspath(abs_path)
        return os.path.commonpath([abs_path, PROJECT_DIR]) == PROJECT_DIR
    except ValueError:
        # Rutas en otra unidad o formato raro -> por seguridad, tratar como fuera.
        return False

# Archivos que NUNCA se auto-aprueban aunque esten dentro del proyecto:
# secretos (.env*), historial de git, y lanzadores (.ps1/.bat/.lnk — uno de
# ellos arranca con Windows: reescribirlo seria persistir codigo sin que el
# usuario lo vea). Sobrescribir con Write equivale a borrar, y borrar esta
# prohibido; estos piden SIEMPRE permiso humano.
_SUFIJOS_PROTEGIDOS = (".ps1", ".bat", ".lnk")

def _es_archivo_protegido(path: str) -> bool:
    if not path:
        return False
    nombre = os.path.basename(path).lower()
    partes = {p.lower() for p in os.path.normpath(path).split(os.sep)}
    return (
        nombre.startswith(".env")
        or ".git" in partes
        or nombre.endswith(_SUFIJOS_PROTEGIDOS)
    )


# Tipo del callback que pide aprobacion al humano y devuelve la decision.
# Recibe (tool_name, input_data) y devuelve {"approved": bool, "reason": str}.
AskHuman = Callable[[str, dict], Awaitable[dict]]


def build_subagents() -> dict[str, AgentDefinition]:
    """Define los subagentes especializados (campos en camelCase, segun el SDK)."""
    return {
        "investigador": AgentDefinition(
            description="Recopila informacion y contexto. Usar para investigar antes de actuar.",
            prompt=(
                "Eres un investigador. Reune informacion completa y precisa usando "
                "solo herramientas de lectura y busqueda. No modifiques nada. "
                "Devuelve un resumen conciso y citando fuentes cuando uses la web."
            ),
            tools=["Read", "Grep", "Glob", "WebSearch", "WebFetch"],
            model="haiku",
            maxTurns=8,
        ),
        "planificador": AgentDefinition(
            description="Disena un plan de pasos a partir de la investigacion. No ejecuta.",
            prompt=(
                "Eres un planificador senior. A partir del objetivo y la informacion "
                "recopilada, produce un plan de pasos concreto, numerado y verificable. "
                "Marca que pasos son irreversibles o sensibles."
            ),
            tools=["Read", "Grep", "Glob"],
            model="sonnet",
            maxTurns=6,
        ),
        "ejecutor": AgentDefinition(
            description="Ejecuta los cambios del plan (escritura, edicion, comandos).",
            prompt=(
                "Eres un ejecutor cuidadoso. Realiza los pasos del plan uno a uno. "
                "Antes de cada accion con efectos secundarios, espera la aprobacion humana. "
                "Si una accion es denegada, detente y reporta."
            ),
            tools=["Read", "Grep", "Glob", "Write", "Edit", "Bash"],
            model="sonnet",
            maxTurns=12,
        ),
    }


def build_options(ask_human: AskHuman) -> ClaudeAgentOptions:
    """Construye las opciones del orquestador con checkpoints humanos.

    `ask_human` es el puente hacia la UI: se invoca cuando una herramienta no
    pre-aprobada necesita el visto bueno del humano.
    """

    async def can_use_tool(tool_name: str, input_data: dict, context) -> object:
        # Importacion local para no fallar si el SDK aun no esta instalado al
        # importar este modulo en herramientas/tests.
        from claude_agent_sdk.types import PermissionResultAllow, PermissionResultDeny

        # REGLA FIJA: borrar esta prohibido SIEMPRE (ni siquiera se pregunta).
        motivo = comando_prohibido(tool_name, input_data)
        if motivo:
            print(f"[bloqueado] {tool_name}: {motivo}", flush=True)
            return PermissionResultDeny(message=motivo, interrupt=False)

        # AUTONOMIA CON LIMITES: escribir/editar DENTRO del proyecto se auto-aprueba
        # (salvo archivos protegidos: .env, .git, lanzadores). Borrar, comandos
        # (Bash) o rutas fuera del proyecto -> piden permiso.
        ruta = input_data.get("file_path", "")
        if tool_name in SAFE_WRITE_TOOLS and _is_inside_project(ruta) and not _es_archivo_protegido(ruta):
            print(f"[auto] {tool_name} dentro del proyecto -> auto-aprobado: {ruta}", flush=True)
            return PermissionResultAllow(updated_input=input_data)

        print(f"[checkpoint] can_use_tool llamado para: {tool_name}", flush=True)
        decision = await ask_human(tool_name, input_data)
        print(f"[checkpoint] decision para {tool_name}: {decision}", flush=True)
        if decision.get("approved"):
            return PermissionResultAllow(updated_input=input_data)
        return PermissionResultDeny(
            message=decision.get("reason") or "Denegado por el humano en el checkpoint.",
            interrupt=False,
        )

    orchestrator_prompt = (
        "Eres el ORQUESTADOR de un sistema multiagente semi-autonomo. "
        f"Trabajas en el directorio del proyecto: {PROJECT_DIR}. "
        "Usa SIEMPRE rutas dentro de ese directorio para crear o editar archivos "
        "(p. ej. crea NOTAS.md como 'NOTAS.md' a secas, ruta relativa). NUNCA "
        "inventes rutas como /home/user o C:/Users/otro. "
        "Dado un objetivo del usuario: (1) delega la investigacion al subagente "
        "'investigador', (2) pide un plan al 'planificador', (3) delega la "
        "ejecucion al 'ejecutor'. Piensa paso a paso y razona a fondo antes de "
        "decidir cada accion: considera alternativas, posibles errores y la mejor "
        "via. Razona sobre los resultados de cada subagente antes de avanzar. "
        "Las acciones con efectos requieren aprobacion humana; "
        "explica brevemente por que necesitas cada accion sensible.\n\n"
        # --- AUTO-APRENDIZAJE (memoria de habilidades, estilo Hermes) ---
        "MEMORIA DE HABILIDADES: aprendes de tu experiencia. Tienes habilidades "
        "(procedimientos reutilizables) guardadas en disco.\n"
        "1) AL EMPEZAR: revisa el indice de habilidades de abajo. Si alguna "
        "encaja con el objetivo, leela con 'leer_habilidad' y aplicala en vez de "
        "empezar de cero.\n"
        "2) AL TERMINAR: si descubriste un procedimiento que servira otra vez "
        "(pasos, comandos, gotchas), guardalo con 'recordar_habilidad' (nombre "
        "corto, descripcion de cuando usarla, y los pasos concretos). Mejora una "
        "existente si ya habia una parecida. NO guardes secretos ni datos "
        "personales sensibles.\n\n"
        "Habilidades disponibles ahora mismo:\n"
        f"{load_skills_index()}"
    )

    return ClaudeAgentOptions(
        model=os.getenv("ORCHESTRATOR_MODEL", "claude-opus-4-8"),
        system_prompt=orchestrator_prompt,
        agents=build_subagents(),
        # Solo lectura + herramientas de habilidades -> auto-aprobadas (seguras:
        # las de habilidades solo escriben dentro de skills/).
        allowed_tools=AUTO_APPROVED_TOOLS + SKILL_TOOL_NAMES,
        mcp_servers={"habilidades": build_skills_server()},
        permission_mode="default",           # lo demas cae en can_use_tool
        can_use_tool=can_use_tool,
        cwd=PROJECT_DIR,                      # carpeta de trabajo del agente
        # --- Razonamiento profundo (lo que pidio el usuario: "razonar como Mythos") ---
        # OJO: budget_tokens esta ELIMINADO en Opus 4.7/4.8 (la API devuelve error 400).
        # "adaptive" deja que el modelo decida cuanto pensar; effort marca la profundidad.
        effort="high",                       # nivel de esfuerzo de razonamiento (low..max)
        thinking={"type": "adaptive"},       # piensa antes de actuar (modo soportado en Opus 4.8)
        max_turns=40,
    )
