"""Broker de aprobaciones humanas (checkpoints).

Cada vez que un agente quiere usar una herramienta que no esta pre-aprobada,
el callback `can_use_tool` crea una solicitud aqui y se queda esperando (await)
hasta que el navegador responda aprobar/denegar.
"""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass, field


@dataclass
class ApprovalBroker:
    """Gestiona checkpoints pendientes de una sola sesion/conexion."""

    _pending: dict[str, asyncio.Future] = field(default_factory=dict)

    def create(self) -> tuple[str, asyncio.Future]:
        """Registra un checkpoint nuevo y devuelve (id, future a esperar)."""
        approval_id = str(uuid.uuid4())
        future: asyncio.Future = asyncio.get_running_loop().create_future()
        self._pending[approval_id] = future
        return approval_id, future

    def resolve(self, approval_id: str, decision: dict) -> bool:
        """Resuelve un checkpoint con la decision del humano.

        `decision` = {"approved": bool, "reason": str}. Devuelve True si habia
        un checkpoint pendiente con ese id.
        """
        future = self._pending.pop(approval_id, None)
        if future is not None and not future.done():
            future.set_result(decision)
            return True
        return False

    def cancel_all(self, reason: str = "Sesion cerrada") -> None:
        """Deniega todos los checkpoints pendientes (p. ej. al desconectar)."""
        for future in self._pending.values():
            if not future.done():
                future.set_result({"approved": False, "reason": reason})
        self._pending.clear()
