"""Comandos de administración de la plataforma.

Uso (dentro del contenedor del backend):
    python -m app.cli crear-tenant --slug acme --nombre "ACME S.A." --admin admin@acme.com
    python -m app.cli migrar
    python -m app.cli sincronizar-auth0 --slug acme
    python -m app.cli generar-clave
"""

import argparse
import asyncio
import sys

from app.core import cifrado
from app.core.config import obtener_configuracion
from app.core.errores import ErrorDominio
from app.core.registro import configurar_registro
from app.tenants import servicios


def _crear_tenant(args: argparse.Namespace) -> None:
    tenant = asyncio.run(servicios.aprovisionar(args.slug, args.nombre, args.admin))
    print(f"Tenant '{tenant.slug}' listo.")
    print(f"  SQL Server: {tenant.base_sqlserver}")
    print(f"  MongoDB:    {tenant.base_mongodb}")
    print(f"  Administrador invitado: {args.admin}")
    if tenant.auth0_organizacion_id:
        print(f"  Organización Auth0: {tenant.auth0_organizacion_id}")
        print(f"  Acceso: https://{tenant.slug}.{obtener_configuracion().dominio_base}")
    else:
        print("  Atención: Auth0 no está configurado; el tenant no tiene organización.")


def _migrar(_: argparse.Namespace) -> None:
    servicios.migrar_todo()
    print("Estructura actualizada en el catálogo y en todos los tenants.")


def _sincronizar_auth0(args: argparse.Namespace) -> None:
    url = asyncio.run(servicios.sincronizar_auth0(args.slug))
    print(f"Direcciones de {url} registradas en Auth0.")


def _generar_clave(_: argparse.Namespace) -> None:
    print("Agregar al .env (no compartir ni versionar):")
    print(f"SECRETO_CLAVE_CIFRADO={cifrado.generar_clave()}")


def main(argumentos: list[str] | None = None) -> int:
    configurar_registro(obtener_configuracion().log_nivel)
    parser = argparse.ArgumentParser(prog="python -m app.cli")
    comandos = parser.add_subparsers(dest="comando", required=True)

    crear = comandos.add_parser("crear-tenant", help="Da de alta y aprovisiona un cliente.")
    crear.add_argument("--slug", required=True, help="Identificador corto, ej. acme")
    crear.add_argument("--nombre", required=True, help="Razón social o nombre visible")
    crear.add_argument("--admin", required=True, help="Email del primer Administrador")
    crear.set_defaults(funcion=_crear_tenant)

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

    args = parser.parse_args(argumentos)
    try:
        args.funcion(args)
    except ErrorDominio as error:
        print(f"Error: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
