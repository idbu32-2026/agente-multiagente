# NOTAS — Sistema Multiagente Semi-Autónomo

## Resumen

Este proyecto implementa un sistema de orquestación multiagente donde un
**Orquestador** coordina varios subagentes especializados para resolver un
objetivo del usuario de forma semi-autónoma (con aprobación humana para las
acciones sensibles).

## Componentes

| Agente | Rol | Capacidades |
|--------|-----|-------------|
| **Orquestador** | Coordina el flujo de trabajo y razona sobre los resultados de cada paso. | Acceso completo a herramientas. |
| **investigador** | Recopila información y contexto antes de actuar. | Lectura, búsqueda, web. |
| **planificador** | Diseña un plan de pasos a partir de la investigación. No ejecuta. | Lectura y búsqueda. |
| **ejecutor** | Ejecuta los cambios del plan (escritura, edición, comandos). | Lectura, escritura, edición, comandos. |

## Flujo de Trabajo

1. **Investigar** — El orquestador delega en el `investigador` para reunir el
   contexto necesario.
2. **Planificar** — Se solicita al `planificador` un plan de pasos concretos.
3. **Ejecutar** — El `ejecutor` aplica los cambios definidos en el plan.

Entre cada fase, el orquestador razona sobre los resultados antes de avanzar.

## Principios

- **Aprobación humana**: las acciones con efectos (escritura, comandos,
  envíos) requieren confirmación; cada acción sensible se justifica brevemente.
- **Separación de responsabilidades**: cada subagente tiene un alcance y un
  conjunto de herramientas acotado.
- **Trabajo dentro del proyecto**: todas las rutas se mantienen dentro del
  directorio del proyecto.
