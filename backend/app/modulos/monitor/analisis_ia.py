"""Explicación con el motor IA de la falla de un job del SQL Server Agent.

Recibe la última ejecución fallida (pasos y mensajes de msdb) y devuelve, en el idioma de
quien la pide, qué pasó, la causa probable y qué revisar o hacer para resolverlo.
"""

import json

from pydantic import BaseModel, Field

from app.modulos.chat_ia import motor_ia

MAXIMO_TOKENS = 2000
TIMEOUT_SEGUNDOS = 60

INSTRUCCIONES = """\
Sos un DBA senior de Microsoft SQL Server. Te pasan la última ejecución fallida de un job
del SQL Server Agent: sus pasos con el resultado y el mensaje que registró msdb.
Explicá la falla para un DBA que tiene que resolverla:
- "resumen": qué falló, en una o dos oraciones.
- "causa": la causa más probable según los mensajes (permisos, objeto inexistente, falta de
  espacio, bloqueo, timeout, credenciales, ruta inaccesible, etc.).
- "pasos": de dos a cinco acciones concretas para verificar y resolverlo, con el T-SQL o
  el comando cuando corresponda.
No inventes datos que no estén en los mensajes; si no alcanzan para saber la causa, decilo.
"""

CONTEXTO = "DBA Assistant: monitoreo y remediación de instancias Microsoft SQL Server."


class ExplicacionJob(BaseModel):
    resumen: str
    causa: str
    pasos: list[str] = Field(description="Acciones concretas para resolverlo.")


async def explicar_falla(job: str, ejecucion: dict) -> ExplicacionJob:
    pedido = {"job": job, "ejecucion": ejecucion}
    return await motor_ia.solicitar(
        INSTRUCCIONES,
        CONTEXTO,
        [{"role": "user", "content": json.dumps(pedido, ensure_ascii=False, default=str)}],
        ExplicacionJob,
        MAXIMO_TOKENS,
        "IA_API_000",
        timeout=TIMEOUT_SEGUNDOS,
    )
