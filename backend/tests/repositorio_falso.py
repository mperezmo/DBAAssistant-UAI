"""Doble en memoria del repositorio de usuarios (app.identidad.repositorios)."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from app.identidad import repositorios
from app.identidad.entidades import DIAS_GRACIA_BAJA, Usuario


class RepositorioUsuariosFalso:
    def __init__(self, tenant_id: str):
        self.tenant_id = tenant_id
        self.usuarios: dict[str, Usuario] = {}
        self.previos: dict[str, str] = {}
        self.ingresos: list[str] = []

    def agregar(
        self,
        email,
        rol="CONSULTA",
        estado="activo",
        auth0=None,
        nombre=None,
        ambientes=(),
        fecha_baja=None,
    ) -> Usuario:
        usuario = Usuario(
            str(uuid4()),
            self.tenant_id,
            email,
            nombre,
            rol,
            estado,
            auth0,
            tuple(ambientes),
            fecha_baja,
        )
        self.usuarios[usuario.id] = usuario
        return usuario

    def instalar(self, monkeypatch) -> RepositorioUsuariosFalso:
        for nombre in (
            "buscar_para_ingreso",
            "activar",
            "registrar_ingreso",
            "guardar_idioma",
            "obtener",
            "obtener_por_email",
            "listar",
            "crear",
            "actualizar",
            "dar_baja",
            "rehabilitar",
            "purgar_bajas",
            "contar_administradores_vigentes",
        ):
            monkeypatch.setattr(repositorios, nombre, getattr(self, nombre))
        return self

    def buscar_para_ingreso(self, base, tenant_id, auth0_id, email):
        for usuario in self.usuarios.values():
            if usuario.auth0_usuario_id == auth0_id:
                return usuario
        for usuario in self.usuarios.values():
            if usuario.auth0_usuario_id is None and usuario.email == email:
                return usuario
        return None

    def activar(self, base, usuario_id, auth0_id, nombre):
        u = self.usuarios[usuario_id]
        self.usuarios[usuario_id] = replace(
            u, auth0_usuario_id=auth0_id, nombre=u.nombre or nombre, estado="activo"
        )

    def guardar_idioma(self, base, tenant_id, usuario_id, idioma):
        self.usuarios[usuario_id] = replace(self.usuarios[usuario_id], idioma=idioma)

    def registrar_ingreso(self, base, usuario_id):
        self.ingresos.append(usuario_id)

    def obtener(self, base, tenant_id, usuario_id):
        return self.usuarios.get(usuario_id)

    def obtener_por_email(self, base, tenant_id, email):
        return next((u for u in self.usuarios.values() if u.email == email), None)

    def listar(self, base, tenant_id, bajas):
        return sorted(
            (u for u in self.usuarios.values() if (u.estado == "baja") == bajas),
            key=lambda u: u.email,
        )

    def crear(self, base, tenant_id, email, nombre, rol, ambientes):
        return self.agregar(email, rol, "invitado", nombre=nombre, ambientes=ambientes).id

    def actualizar(self, base, tenant_id, usuario_id, nombre, rol, ambientes):
        u = self.usuarios[usuario_id]
        self.usuarios[usuario_id] = replace(u, nombre=nombre, rol=rol, ambientes=tuple(ambientes))

    def dar_baja(self, base, tenant_id, usuario_id):
        u = self.usuarios[usuario_id]
        self.previos[usuario_id] = u.estado
        self.usuarios[usuario_id] = replace(u, estado="baja", fecha_baja=datetime.now(UTC))

    def rehabilitar(self, base, tenant_id, usuario_id):
        u = self.usuarios[usuario_id]
        estado = self.previos.pop(usuario_id, "invitado")
        self.usuarios[usuario_id] = replace(u, estado=estado, fecha_baja=None)

    def purgar_bajas(self, base, tenant_id):
        limite = datetime.now(UTC) - timedelta(days=DIAS_GRACIA_BAJA)
        vencidos = [
            u
            for u in self.usuarios.values()
            if u.estado == "baja" and u.fecha_baja and u.fecha_baja < limite
        ]
        for u in vencidos:
            del self.usuarios[u.id]
        return [u.email for u in vencidos]

    def contar_administradores_vigentes(self, base, tenant_id):
        return sum(
            1
            for u in self.usuarios.values()
            if u.rol == "ADMINISTRADOR" and u.estado in ("invitado", "activo")
        )
