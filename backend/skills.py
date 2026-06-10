"""Auto-aprendizaje: el agente guarda y consulta 'habilidades' aprendidas.

Idea (estilo Hermes, en local):
    Una *habilidad* es un procedimiento reutilizable que el agente descubre al
    resolver una tarea. Se guarda como un archivo markdown en la carpeta
    `skills/` del proyecto. La proxima vez, el orquestador ve el indice de
    habilidades disponibles y puede leer la que necesite para no empezar de cero.

Esto da dos cosas a la vez:
    1. Auto-aprendizaje  -> el sistema mejora con el uso.
    2. Memoria persistente -> lo aprendido sobrevive entre sesiones (esta en disco).

Se expone al orquestador como tres herramientas (via SDK MCP en proceso):
    - listar_habilidades  : ver que habilidades hay.
    - leer_habilidad      : leer el contenido completo de una.
    - recordar_habilidad  : crear o actualizar una habilidad.

Seguridad: solo se escribe DENTRO de `skills/`. Nunca fuera. El nombre se
convierte en un slug seguro, asi que el modelo no puede escapar de la carpeta.
"""

from __future__ import annotations

import re
from datetime import date
from pathlib import Path

from claude_agent_sdk import create_sdk_mcp_server, tool

# Carpeta donde viven las habilidades aprendidas (junto a backend/ y frontend/).
PROJECT_DIR = Path(__file__).resolve().parent.parent
SKILLS_DIR = PROJECT_DIR / "skills"


def _slug(nombre: str) -> str:
    """Convierte un nombre libre en un slug seguro para nombre de archivo.

    'Enviar boletin por WhatsApp' -> 'enviar-boletin-por-whatsapp'. Esto evita
    rutas peligrosas (../, barras, etc.): el resultado solo tiene a-z, 0-9 y '-'.
    """
    s = nombre.strip().lower()
    s = re.sub(r"[^a-z0-9]+", "-", s).strip("-")
    return s or "habilidad"


def _parse_descripcion(texto: str) -> str:
    """Saca la 'description:' del frontmatter de una habilidad, si existe."""
    for linea in texto.splitlines():
        m = re.match(r"\s*description:\s*(.+)", linea, re.IGNORECASE)
        if m:
            return m.group(1).strip()
        if linea.strip() == "---" and texto.find("---") != texto.rfind("---"):
            # seguimos dentro del frontmatter; continua buscando
            continue
    return "(sin descripcion)"


def load_skills_index() -> str:
    """Texto con las habilidades disponibles (nombre + para que sirve).

    Se inyecta en el prompt del orquestador para que sepa que sabe hacer ya
    sin tener que abrir cada archivo. Si no hay ninguna, lo indica.
    """
    if not SKILLS_DIR.exists():
        return "(Aun no hay habilidades guardadas. Iras creando las tuyas con el uso.)"

    fichas = sorted(SKILLS_DIR.glob("*.md"))
    if not fichas:
        return "(Aun no hay habilidades guardadas. Iras creando las tuyas con el uso.)"

    lineas = []
    for p in fichas:
        try:
            desc = _parse_descripcion(p.read_text(encoding="utf-8"))
        except OSError:
            desc = "(no se pudo leer)"
        lineas.append(f"- {p.stem}: {desc}")
    return "\n".join(lineas)


# --------------------------------------------------------------------------- #
# Herramientas que se exponen al orquestador.
# --------------------------------------------------------------------------- #

@tool(
    "listar_habilidades",
    "Lista las habilidades (procedimientos reutilizables) que ya has aprendido, "
    "con su nombre y para que sirven. Usala al empezar una tarea para ver si ya "
    "sabes hacer algo parecido.",
    {},
)
async def listar_habilidades(args: dict) -> dict:
    return {"content": [{"type": "text", "text": load_skills_index()}]}


@tool(
    "leer_habilidad",
    "Lee el contenido completo de una habilidad por su nombre (slug). Usala "
    "cuando una habilidad del indice encaje con la tarea actual, para aplicar "
    "los pasos que aprendiste.",
    {"nombre": str},
)
async def leer_habilidad(args: dict) -> dict:
    slug = _slug(args.get("nombre", ""))
    ruta = SKILLS_DIR / f"{slug}.md"
    if not ruta.exists():
        disponibles = load_skills_index()
        return {
            "content": [
                {
                    "type": "text",
                    "text": f"No existe la habilidad '{slug}'.\n\nDisponibles:\n{disponibles}",
                }
            ]
        }
    return {"content": [{"type": "text", "text": ruta.read_text(encoding="utf-8")}]}


@tool(
    "recordar_habilidad",
    "Guarda (o actualiza) una habilidad reutilizable que acabas de aprender, "
    "para usarla en el futuro. Llamala al terminar una tarea si descubriste un "
    "procedimiento que servira otra vez. NO guardes secretos, contrasenas ni "
    "datos personales sensibles. Parametros: 'nombre' (corto y descriptivo), "
    "'descripcion' (una linea: cuando conviene usar esta habilidad) y "
    "'contenido' (los pasos concretos, en markdown).",
    {"nombre": str, "descripcion": str, "contenido": str},
)
async def recordar_habilidad(args: dict) -> dict:
    nombre = (args.get("nombre") or "").strip()
    descripcion = (args.get("descripcion") or "").strip().replace("\n", " ")
    contenido = (args.get("contenido") or "").strip()

    if not nombre or not contenido:
        return {
            "content": [
                {"type": "text", "text": "Faltan datos: necesito 'nombre' y 'contenido'."}
            ]
        }

    SKILLS_DIR.mkdir(parents=True, exist_ok=True)
    slug = _slug(nombre)
    ruta = SKILLS_DIR / f"{slug}.md"
    existia = ruta.exists()

    documento = (
        "---\n"
        f"name: {slug}\n"
        f"description: {descripcion or '(sin descripcion)'}\n"
        f"updated: {date.today().isoformat()}\n"
        "---\n\n"
        f"# {nombre}\n\n"
        f"{contenido}\n"
    )
    ruta.write_text(documento, encoding="utf-8")

    verbo = "actualizada" if existia else "guardada"
    return {
        "content": [
            {"type": "text", "text": f"Habilidad '{slug}' {verbo} en skills/{slug}.md"}
        ]
    }


# Nombres con los que el orquestador vera estas herramientas (prefijo MCP del SDK).
# Se usan para auto-aprobarlas (son seguras: solo tocan la carpeta skills/).
SERVER_NAME = "habilidades"
TOOL_NAMES = [
    f"mcp__{SERVER_NAME}__listar_habilidades",
    f"mcp__{SERVER_NAME}__leer_habilidad",
    f"mcp__{SERVER_NAME}__recordar_habilidad",
]


def build_skills_server():
    """Crea el servidor MCP en proceso con las tres herramientas de habilidades."""
    return create_sdk_mcp_server(
        name=SERVER_NAME,
        version="1.0.0",
        tools=[listar_habilidades, leer_habilidad, recordar_habilidad],
    )
