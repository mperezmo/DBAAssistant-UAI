"""Condiciones de alerta que se evalúan sobre cada lectura de una instancia.

Tres orígenes: umbrales del monitoreo (CU-000-003-B), eventos fijos y reglas propias
(CU-000-004-A). Las funciones son puras: reciben la lectura y la configuración y
devuelven las condiciones que se cumplen en ese momento.
"""

from app.identidad.entidades import ROLES_OPERATIVOS
from app.modulos.alertas.entidades import Condicion, Regla
from app.modulos.monitor.entidades import METRICAS, Lectura, Umbral
from app.web.i18n import IDIOMA_PREDETERMINADO, formatear

# Destinatarios de las alertas que no vienen de una regla propia.
DESTINATARIOS_SISTEMA = ROLES_OPERATIVOS

CLAVE_INSTANCIA = "evento:instancia"
# Confirmación antes de declarar caída una instancia o una base (dos lecturas fallidas);
# deja margen para notificar dentro de los 30 s que exige el TFI.
CONFIRMACION_CAIDA_SEGUNDOS = 5
BLOQUEO_SOSTENIDO_SEGUNDOS = 30
LOG_LLENO_PORCENTAJE = 90
SOSTENIDA_UMBRAL_SEGUNDOS = 60


def _ref(clave: str, **parametros) -> list:
    """Texto traducible: clave y parámetros (los que empiezan con @ son otra clave)."""
    return [clave, parametros]


def _textos(titulo: list | None, descripcion: list) -> dict:
    """Texto guardado (español, para búsquedas) y referencias para cada idioma."""
    textos = {"descripcion": descripcion}
    if titulo:
        textos["titulo"] = titulo
    return {
        "titulo": formatear(titulo[0], IDIOMA_PREDETERMINADO, **titulo[1]) if titulo else None,
        "descripcion": formatear(descripcion[0], IDIOMA_PREDETERMINADO, **descripcion[1]),
        "textos": textos,
    }


def _num(valor: float | None) -> str:
    if valor is None:
        return "—"
    return f"{valor:g}" if float(valor).is_integer() else f"{valor:.1f}"


def instancia_caida(servidor: str) -> Condicion:
    return Condicion(
        clave=CLAVE_INSTANCIA,
        origen="evento",
        severidad="critical",
        **_textos(
            _ref("alertas.evento.instancia.titulo"),
            _ref("alertas.evento.instancia.descripcion", servidor=servidor),
        ),
        sostenida_segundos=CONFIRMACION_CAIDA_SEGUNDOS,
        destinatarios=DESTINATARIOS_SISTEMA,
        push=True,
        email=True,
    )


def por_umbrales(lectura: Lectura, umbrales: dict[str, Umbral]) -> list[Condicion]:
    condiciones = []
    for metrica, definicion in METRICAS.items():
        u = umbrales.get(metrica) or Umbral.por_defecto(metrica)
        valor = lectura.valores.get(metrica)
        if not u.habilitada or u.warning is None or valor is None or valor < u.warning:
            continue
        severidad, umbral = (
            ("critical", u.critical) if valor >= u.critical else ("warning", u.warning)
        )
        nombre = f"@monitor.metrica.{metrica}"
        condiciones.append(
            Condicion(
                clave=f"umbral:{metrica}",
                origen="umbral",
                severidad=severidad,
                **_textos(
                    _ref("alertas.umbral.titulo", metrica=nombre, severidad=severidad),
                    _ref(
                        "alertas.umbral.descripcion",
                        metrica=nombre,
                        valor=_num(valor),
                        umbral=_num(umbral),
                        unidad=definicion.unidad,
                    ),
                ),
                sostenida_segundos=SOSTENIDA_UMBRAL_SEGUNDOS,
                destinatarios=DESTINATARIOS_SISTEMA,
                # Por defecto, solo las críticas se notifican (push y correo).
                push=severidad == "critical",
                email=severidad == "critical",
                metrica=metrica,
                valor=valor,
                umbral=umbral,
                unidad=definicion.unidad,
            )
        )
    return condiciones


def por_eventos(lectura: Lectura) -> list[Condicion]:
    condiciones = [
        Condicion(
            clave=f"evento:base:{b['base']}",
            origen="evento",
            severidad="critical",
            **_textos(
                _ref("alertas.evento.base.titulo", base=b["base"]),
                _ref("alertas.evento.base.descripcion", base=b["base"], estado=b["estado"]),
            ),
            sostenida_segundos=CONFIRMACION_CAIDA_SEGUNDOS,
            destinatarios=DESTINATARIOS_SISTEMA,
            push=True,
            email=True,
        )
        for b in lectura.bases_no_disponibles
    ]

    largos = [b for b in lectura.bloqueos if b["espera_s"] >= BLOQUEO_SOSTENIDO_SEGUNDOS]
    if largos:
        peor = max(largos, key=lambda b: b["espera_s"])
        condiciones.append(
            Condicion(
                clave="evento:bloqueo",
                origen="evento",
                severidad="warning",
                **_textos(
                    _ref("alertas.evento.bloqueo.titulo", segundos=BLOQUEO_SOSTENIDO_SEGUNDOS),
                    _ref(
                        "alertas.evento.bloqueo.descripcion",
                        sid=peor["sid"],
                        bloqueador=peor["bloqueador"],
                        espera=_num(peor["espera_s"]),
                        recurso=f"{peor['base'] or ''} {peor['recurso'] or ''}".strip(),
                        cantidad=len(largos),
                    ),
                ),
                sostenida_segundos=0,
                destinatarios=DESTINATARIOS_SISTEMA,
                push=False,
                valor=peor["espera_s"],
                umbral=BLOQUEO_SOSTENIDO_SEGUNDOS,
                unidad="s",
            )
        )

    condiciones += [
        Condicion(
            clave=f"evento:log:{log['base']}",
            origen="evento",
            severidad="warning",
            **_textos(
                _ref("alertas.evento.log.titulo", base=log["base"]),
                _ref("alertas.evento.log.descripcion", base=log["base"], uso=_num(log["uso"])),
            ),
            sostenida_segundos=SOSTENIDA_UMBRAL_SEGUNDOS,
            destinatarios=DESTINATARIOS_SISTEMA,
            push=False,
            valor=log["uso"],
            umbral=LOG_LLENO_PORCENTAJE,
            unidad="%",
        )
        for log in lectura.logs
        if log["uso"] > LOG_LLENO_PORCENTAJE
    ]
    return condiciones


def por_reglas(lectura: Lectura, reglas: list[Regla], conexion_id: str) -> list[Condicion]:
    condiciones = []
    for r in reglas:
        if not r.activa or (r.conexion_id and r.conexion_id != conexion_id):
            continue
        valor = lectura.valores.get(r.metrica)
        if not r.cumple(valor):
            continue
        unidad = METRICAS[r.metrica].unidad
        condiciones.append(
            Condicion(
                clave=f"regla:{r.id}",
                origen="regla",
                severidad=r.severidad,
                **{
                    **_textos(
                        None,
                        _ref(
                            "alertas.regla.descripcion",
                            metrica=f"@monitor.metrica.{r.metrica}",
                            operador=r.operador,
                            umbral=_num(r.valor),
                            valor=_num(valor),
                            unidad=unidad,
                        ),
                    ),
                    "titulo": r.nombre,
                },
                sostenida_segundos=r.sostenida_segundos,
                destinatarios=r.destinatarios,
                push=r.push,
                email=r.email,
                metrica=r.metrica,
                valor=valor,
                umbral=r.valor,
                unidad=unidad,
                regla_id=r.id,
            )
        )
    return condiciones


def evaluar(
    lectura: Lectura | None,
    servidor: str,
    umbrales: dict[str, Umbral],
    reglas: list[Regla],
    conexion_id: str,
) -> tuple[list[Condicion], bool]:
    """Condiciones que se cumplen y si la lectura permitió evaluar todas.

    Con la instancia caída solo se evalúa ese evento: el resto de las alertas
    activas no se puede dar por normalizado.
    """
    if lectura is None:
        return [instancia_caida(servidor)], False
    return (
        por_umbrales(lectura, umbrales)
        + por_eventos(lectura)
        + por_reglas(lectura, reglas, conexion_id),
        True,
    )
