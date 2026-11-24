"""Comandos de administración de la plataforma.

Uso (dentro del contenedor del backend):
    python -m app.cli crear-tenant --slug acme --nombre "ACME S.A." --admin admin@acme.com
    python -m app.cli crear-operador --email ops@dbaassistant.app --nombre "Martín"
    python -m app.cli invitar-empresa --email admin@acme.com
    python -m app.cli migrar
    python -m app.cli sincronizar-auth0 --slug acme
    python -m app.cli generar-clave
    python -m app.cli generar-vapid
    python -m app.cli probar-correo --para dba@empresa.com
    python -m app.cli certificado-interno
"""

import argparse
import asyncio
import sys
from pathlib import Path

from app.core import cifrado, tls_interno
from app.core.config import obtener_configuracion
from app.core.errores import ErrorDominio
from app.core.registro import configurar_registro
from app.identidad import invitaciones
from app.identidad import repositorios as repositorios_usuarios
from app.tenants import servicios


def _avisar_administrador(tenant, email: str) -> str:
    usuario = repositorios_usuarios.obtener_por_email(tenant.base_sqlserver, str(tenant.id), email)
    if usuario is None or usuario.estado != "invitado":
        return "no corresponde (ya ingresó)"
    return asyncio.run(invitaciones.enviar(tenant, usuario, None))


def _crear_tenant(args: argparse.Namespace) -> None:
    tenant = asyncio.run(servicios.aprovisionar(args.slug, args.nombre, args.admin))
    print(f"Tenant '{tenant.slug}' listo.")
    print(f"  SQL Server: {tenant.base_sqlserver}")
    print(f"  MongoDB:    {tenant.base_mongodb}")
    aviso = _avisar_administrador(tenant, args.admin)
    print(f"  Administrador: {args.admin} · aviso de acceso: {aviso}")
    if tenant.auth0_organizacion_id:
        print(f"  Organización Auth0: {tenant.auth0_organizacion_id}")
        print(f"  Acceso: https://{tenant.slug}.{obtener_configuracion().dominio_base}")
    else:
        print("  Atención: Auth0 no está configurado; el tenant no tiene organización.")


def _crear_operador(args: argparse.Namespace) -> None:
    from app.plataforma import servicios as plataforma

    operador, aviso, organizacion = asyncio.run(
        plataforma.crear_operador_inicial(args.email, args.nombre)
    )
    print(f"Operador {operador.email} ({operador.estado}) · aviso de acceso: {aviso}")
    if organizacion:
        print(f"  Organización Auth0 de la plataforma: {organizacion}")
    else:
        print("  Atención: Auth0 no está configurado; la consola no va a permitir ingresar.")
    print(f"  Consola: {plataforma.correos.direccion_plataforma()}")


def _invitar_empresa(args: argparse.Namespace) -> None:
    from app.identidad.entidades import Sesion
    from app.plataforma import servicios as plataforma

    sistema = Sesion("cli", "cli", "plataforma", "plataforma", "sistema", "DBA Assistant", "", 0)
    invitacion, enlace, envio = asyncio.run(
        plataforma.invitar(sistema, args.email, not args.sin_correo, None)
    )
    print(f"Invitación para {invitacion.email}, vence {invitacion.vence_en:%Y-%m-%d %H:%M} UTC.")
    print(f"  Link: {enlace}")
    print(f"  Correo: {envio or 'no enviado (--sin-correo)'}")


def _migrar(_: argparse.Namespace) -> None:
    servicios.migrar_todo()
    print("Estructura actualizada en el catálogo y en todos los tenants.")


def _sincronizar_auth0(args: argparse.Namespace) -> None:
    url = asyncio.run(servicios.sincronizar_auth0(args.slug))
    print(f"Direcciones de {url} registradas en Auth0.")


def _generar_clave(_: argparse.Namespace) -> None:
    print("Agregar al .env (no compartir ni versionar):")
    print(f"SECRETO_CLAVE_CIFRADO={cifrado.generar_clave()}")


def _certificado_interno(_: argparse.Namespace) -> None:
    cfg = obtener_configuracion()
    if tls_interno.generar_si_falta(
        Path(cfg.tls_interno_certificado), Path(cfg.tls_interno_clave), cfg.tls_interno_nombre
    ):
        print(f"Certificado interno generado en {cfg.tls_interno_certificado}")


def _generar_vapid(_: argparse.Namespace) -> None:
    from app.modulos.web_push.servicios import generar_claves

    publica, privada = generar_claves()
    print("Agregar al .env (la clave privada no se comparte ni se versiona):")
    print(f"VAPID_CLAVE_PUBLICA={publica}")
    print(f"SECRETO_VAPID_CLAVE_PRIVADA={privada}")
    print("VAPID_CONTACTO=mailto:<email de contacto del responsable>")


def _probar_correo(args: argparse.Namespace) -> None:
    from app.datos import correo

    if not correo.configurado():
        raise ErrorDominio("NTF_EML_000", "Falta configurar SMTP_HOST y SMTP_REMITENTE.")
    try:
        correo.enviar(
            [args.para],
            "DBA Assistant · correo de prueba",
            "La configuración de correo de DBA Assistant funciona.",
        )
    except Exception as error:
        raise ErrorDominio("NTF_EML_000", str(error)) from error
    print(f"Correo de prueba enviado a {args.para}.")


def main(argumentos: list[str] | None = None) -> int:
    configurar_registro(obtener_configuracion().log_nivel)
    parser = argparse.ArgumentParser(prog="python -m app.cli")
    comandos = parser.add_subparsers(dest="comando", required=True)

    crear = comandos.add_parser("crear-tenant", help="Da de alta y aprovisiona un cliente.")
    crear.add_argument("--slug", required=True, help="Identificador corto, ej. acme")
    crear.add_argument("--nombre", required=True, help="Razón social o nombre visible")
    crear.add_argument("--admin", required=True, help="Email del primer Administrador")
    crear.set_defaults(funcion=_crear_tenant)

    operador = comandos.add_parser(
        "crear-operador",
        help="Prepara la consola de la plataforma y da de alta un operador.",
    )
    operador.add_argument("--email", required=True)
    operador.add_argument("--nombre", default=None)
    operador.set_defaults(funcion=_crear_operador)

    invitar = comandos.add_parser(
        "invitar-empresa", help="Invita a registrar una empresa (vence a las 24 h)."
    )
    invitar.add_argument("--email", required=True, help="Email del primer Administrador")
    invitar.add_argument(
        "--sin-correo", action="store_true", help="No enviar el correo; solo mostrar el link"
    )
    invitar.set_defaults(funcion=_invitar_empresa)

    migrar = comandos.add_parser("migrar", help="Aplica los cambios de estructura pendientes.")
    migrar.set_defaults(funcion=_migrar)

    sincronizar = comandos.add_parser(
        "sincronizar-auth0", help="Registra en Auth0 las direcciones de acceso de un tenant."
    )
    sincronizar.add_argument("--slug", required=True)
    sincronizar.set_defaults(funcion=_sincronizar_auth0)

    generar = comandos.add_parser(
        "generar-clave", help="Genera la clave maestra para cifrar credenciales."
    )
    generar.set_defaults(funcion=_generar_clave)

    vapid = comandos.add_parser(
        "generar-vapid", help="Genera las claves VAPID para las notificaciones push."
    )
    vapid.set_defaults(funcion=_generar_vapid)

    probar = comandos.add_parser("probar-correo", help="Envía un correo de prueba por SMTP.")
    probar.add_argument("--para", required=True, help="Email de destino")
    probar.set_defaults(funcion=_probar_correo)

    interno = comandos.add_parser(
        "certificado-interno",
        help="Genera el certificado HTTPS del backend si falta o está por vencer.",
    )
    interno.set_defaults(funcion=_certificado_interno)

    args = parser.parse_args(argumentos)
    try:
        args.funcion(args)
    except ErrorDominio as error:
        print(f"Error: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
