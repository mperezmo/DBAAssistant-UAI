"""Análisis de los hallazgos con el motor IA (CU-000-007-A, paso 5).

Recibe los hallazgos de los chequeos y las reglas propias del DBA, y devuelve
sugerencias agrupadas por tipo y severidad, cada una con el T-SQL propuesto. El esquema y
el contexto de negocio van en el bloque de sistema cacheado, como en el Chat IA.
"""

import json
from typing import Literal

from pydantic import BaseModel, Field

from app.modulos.chat_ia import motor_ia

MAXIMO_TOKENS = 8000
TIMEOUT_SEGUNDOS = 120

INSTRUCCIONES = """\
Sos un DBA senior de Microsoft SQL Server que revisa una base de datos y propone mejoras.

Recibís hallazgos objetivos ya medidos en la instancia (consultas costosas, índices
faltantes, fragmentación, problemas de esquema) y, opcionalmente, reglas de buenas
prácticas definidas por el equipo. Con eso:
- Proponé sugerencias concretas, cada una con el T-SQL que la aplica. Si un hallazgo ya
  trae T-SQL, usalo salvo que tengas uno mejor. Para consultas costosas proponé el índice
  o la reescritura que corresponda.
- Agrupá hallazgos que se resuelven con la misma acción (por ejemplo, un índice que cubre
  varias consultas) y no repitas sugerencias.
- Verificá cada regla del equipo contra el esquema de la base: por cada incumplimiento
  generá una sugerencia de tipo "regla".
- Usá solo objetos que existen en el esquema. No inventes métricas: si un dato no está en
  los hallazgos, no lo afirmes.
- Severidad: "alta" si afecta mucho el rendimiento o la integridad, "media" si conviene
  resolverlo, "baja" si es una mejora menor.
- Textos en español rioplatense, breves y técnicos. "indice_hallazgo" es la posición (desde
  0) del hallazgo en que se basa la sugerencia, o null si sale de una regla del equipo.
"""


class Sugerencia(BaseModel):
    titulo: str
    tipo: Literal["consultas", "indices", "fragmentacion", "esquema", "regla"]
    severidad: Literal["alta", "media", "baja"]
    objeto: str
    descripcion: str
    sql: str | None = Field(description="T-SQL que aplica la sugerencia, o null.")
    indice_hallazgo: int | None


class Informe(BaseModel):
    resumen: str
    sugerencias: list[Sugerencia]


async def analizar(contexto: str, base: str, hallazgos: list[dict], reglas: list[str]) -> Informe:
    pedido = {
        "base": base,
        "hallazgos": [{k: v for k, v in h.items() if k != "severidad"} for h in hallazgos],
        "reglas_del_equipo": reglas,
    }
    return await motor_ia.solicitar(
        INSTRUCCIONES,
        contexto,
        [{"role": "user", "content": json.dumps(pedido, ensure_ascii=False, default=str)}],
        Informe,
        MAXIMO_TOKENS,
        "IA_API_000",
        timeout=TIMEOUT_SEGUNDOS,
    )
