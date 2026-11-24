"""Plataforma: operadores, invitaciones, registro de empresas por código e ingreso."""

import asyncio
from dataclasses import replace
from datetime import timedelta
from urllib.parse import parse_qs, urlparse
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.core.config import obtener_configuracion
from app.core.errores import ErrorDominio
from app.datos import correo
from app.identidad import sesiones
from app.identidad.auth0 import ClienteAuth0
from app.identidad.entidades import Sesion
from app.main import app
from app.plataforma import correos, ingreso, registro, repositorios, servicios
from app.plataforma.entidades import (
    INTENTOS_CODIGO,
    TENANT_PLATAFORMA,
    VIGENCIA_INVITACION,
    Invitacion,
    Operador,
    ahora_utc,
)
from app.tenants import resolucion
from app.tenants import servicios as tenants_servicios
from app.tenants.entidades import Tenant, validar_slug_nuevo
from tests.auth0_falso import AUDIENCIA, CLIENT_ID, DOMINIO, Auth0Falso

ORGANIZACION_PLATAFORMA = "org_plataforma"
CABECERA = {"X-Solicitud": "dba"}


class RepositorioPlataformaFalso:
    """Operadores e invitaciones en memoria, con las mismas reglas que el catálogo."""

    FUNCIONES = (
        "organizacion",
        "guardar_organizacion",
        "listar_operadores",
        "obtener_operador",
        "obtener_operador_por_email",
        "buscar_para_ingreso",
        "crear_operador",
        "activar_operador",
        "registrar_ingreso_operador",
        "dar_de_baja_operador",
        "listar_invitaciones",
        "obtener_invitacion",
        "obtener_invitacion_abierta",
        "crear_invitacion",
        "revocar_invitacion",
        "guardar_codigo",
        "registrar_intento_fallido",
        "consumir_codigo",
        "iniciar_alta",
        "vincular_empresa",
        "marcar_usada",
    )

    def __init__(self):
        self.org = ORGANIZACION_PLATAFORMA
        self.operadores: dict[str, Operador] = {}
        self.invitaciones: dict[str, Invitacion] = {}

    def instalar(self, monkeypatch):
        for nombre in self.FUNCIONES:
            monkeypatch.setattr(repositorios, nombre, getattr(self, nombre))
        return self

    def organizacion(self):
        return self.org

    def guardar_organizacion(self, organizacion_id):
        self.org = organizacion_id

    def listar_operadores(self):
        return [o for o in self.operadores.values() if o.estado != "baja"]

    def obtener_operador(self, operador_id):
        return self.operadores.get(operador_id)

    def obtener_operador_por_email(self, email):
        return next((o for o in self.operadores.values() if o.email == email), None)

    def buscar_para_ingreso(self, auth0_id, email):
        for o in self.operadores.values():
            if o.auth0_usuario_id == auth0_id:
                return o
        for o in self.operadores.values():
            if o.auth0_usuario_id is None and o.email == email:
                return o
        return None

    def crear_operador(self, email, nombre, creado_por):
        existente = self.obtener_operador_por_email(email)
        if existente and existente.estado != "baja":
            return None
        operador = Operador(
            existente.id if existente else str(uuid4()),
            email,
            nombre,
            "invitado",
            None,
            creado_por,
            ahora_utc(),
        )
        self.operadores[operador.id] = operador
        return operador

    def activar_operador(self, operador_id, auth0_id, nombre):
        o = self.operadores[operador_id]
        self.operadores[operador_id] = replace(
            o, estado="activo", auth0_usuario_id=auth0_id, nombre=o.nombre or nombre
        )

    def registrar_ingreso_operador(self, operador_id):
        o = self.operadores[operador_id]
        self.operadores[operador_id] = replace(o, ultimo_ingreso=ahora_utc())

    def dar_de_baja_operador(self, operador_id):
        o = self.operadores[operador_id]
        self.operadores[operador_id] = replace(o, estado="baja")

    def listar_invitaciones(self, cantidad=200):
        return sorted(self.invitaciones.values(), key=lambda i: i.creada_en, reverse=True)

    def obtener_invitacion(self, invitacion_id):
        return self.invitaciones.get(invitacion_id)

    def obtener_invitacion_abierta(self, email):
        return next(
            (
                i
                for i in self.invitaciones.values()
                if i.email == email and i.estado in ("pendiente", "en_curso")
            ),
            None,
        )

    def crear_invitacion(self, email, creada_por, vence_en):
        for i in list(self.invitaciones.values()):
            if i.email == email and i.estado == "pendiente":
                self.invitaciones[i.id] = replace(i, estado="revocada")
        invitacion = Invitacion(str(uuid4()), email, "pendiente", creada_por, ahora_utc(), vence_en)
        self.invitaciones[invitacion.id] = invitacion
        return invitacion

    def revocar_invitacion(self, invitacion_id):
        i = self.invitaciones.get(invitacion_id)
        if i is None or i.estado != "pendiente":
            return False
        self.invitaciones[invitacion_id] = replace(i, estado="revocada", codigo_hash=None)
        return True

    def guardar_codigo(self, invitacion_id, codigo_hash, vence_en):
        i = self.invitaciones[invitacion_id]
        self.invitaciones[invitacion_id] = replace(
            i, codigo_hash=codigo_hash, codigo_vence_en=vence_en, codigo_intentos=0
        )

    def registrar_intento_fallido(self, invitacion_id, maximo):
        i = self.invitaciones[invitacion_id]
        intentos = i.codigo_intentos + 1
        self.invitaciones[invitacion_id] = replace(
            i, codigo_intentos=intentos, codigo_hash=None if intentos >= maximo else i.codigo_hash
        )

    def consumir_codigo(self, invitacion_id):
        i = self.invitaciones[invitacion_id]
        self.invitaciones[invitacion_id] = replace(
            i, codigo_hash=None, codigo_vence_en=None, codigo_intentos=0
        )

    def iniciar_alta(self, invitacion_id, ahora):
        i = self.invitaciones[invitacion_id]
        if i.estado == "en_curso" or (i.estado == "pendiente" and i.vence_en > ahora):
            self.invitaciones[invitacion_id] = replace(i, estado="en_curso")
            return True
        return False

    def vincular_empresa(self, invitacion_id, tenant_id):
        i = self.invitaciones[invitacion_id]
        if i.tenant_id is None:
            self.invitaciones[invitacion_id] = replace(i, tenant_id=tenant_id)

    def marcar_usada(self, invitacion_id):
        i = self.invitaciones[invitacion_id]
        if i.estado == "en_curso":
            self.invitaciones[invitacion_id] = replace(i, estado="usada", usada_en=ahora_utc())


@pytest.fixture
def plataforma(monkeypatch, redis_falso):
    repo = RepositorioPlataformaFalso().instalar(monkeypatch)
    empresas: dict[str, Tenant] = {}
    eventos, correos_enviados, altas = [], [], []

    async def registrar(base, evento):
        eventos.append((evento.accion, evento.detalle))

    def enviar(destinatarios, asunto, texto):
        correos_enviados.append((destinatarios[0], asunto, texto))

    async def aprovisionar(slug, nombre, email, nombre_admin=None, progreso=None):
        altas.append((slug, nombre, email, nombre_admin))
        empresas[slug] = Tenant(uuid4(), slug, nombre, f"DB_T_{slug}", f"db_t_{slug}", "activo")
        for paso in tenants_servicios.PASOS:
            await progreso(paso)
        return empresas[slug]

    monkeypatch.setattr(servicios.auditoria, "registrar", registrar)
    monkeypatch.setattr(correo, "configurado", lambda: True)
    monkeypatch.setattr(correo, "enviar", enviar)
    monkeypatch.setattr(registro.tenants, "obtener_por_slug", lambda slug: empresas.get(slug))
    monkeypatch.setattr(
        registro.tenants,
        "obtener_por_id",
        lambda tid: next((t for t in empresas.values() if str(t.id) == tid), None),
    )
    monkeypatch.setattr(tenants_servicios, "aprovisionar", aprovisionar)
    return {
        "repo": repo,
        "empresas": empresas,
        "eventos": eventos,
        "correos": correos_enviados,
        "altas": altas,
    }


def _operador(repo, email="ops@plataforma.com", estado="activo") -> Operador:
    operador = repo.crear_operador(email, "Ops", "sistema")
    if estado == "activo":
        repo.activar_operador(operador.id, f"auth0|{email}", None)
    return repo.operadores[operador.id]


def _sesion(operador: Operador) -> Sesion:
    return Sesion(
        "s",
        operador.id,
        TENANT_PLATAFORMA,
        TENANT_PLATAFORMA,
        operador.email,
        operador.nombre or operador.email,
        "OPERADOR",
        0,
    )


def _codigo_del_correo(correos_enviados, email) -> str:
    asunto = next(a for d, a, _ in reversed(correos_enviados) if d == email)
    return asunto.rsplit(" ", 1)[-1]


# --- Subdominios reservados y entidades ---------------------------------------------


@pytest.mark.parametrize("slug", ["plataforma", "registrar-empresa", "www", "api"])
def test_subdominios_reservados_no_pueden_ser_empresas(slug):
    with pytest.raises(ErrorDominio) as error:
        validar_slug_nuevo(slug)
    assert error.value.codigo == "TEN_VAL_004"
    assert resolucion.es_reservado(slug)


def test_hosts_de_la_plataforma():
    assert resolucion.es_plataforma("plataforma.dba.app:443", "dba.app")
    assert resolucion.es_registro("registrar-empresa.dba.app", "dba.app")
    assert not resolucion.es_plataforma("acme.dba.app", "dba.app")


def test_estado_visible_de_la_invitacion():
    ahora = ahora_utc()
    vigente = Invitacion("1", "a@b.com", "pendiente", "ops", ahora, ahora + timedelta(hours=1))
    vencida = replace(vigente, vence_en=ahora - timedelta(seconds=1))

    assert vigente.vigente(ahora) and vigente.estado_visible(ahora) == "pendiente"
    assert not vencida.vigente(ahora) and vencida.estado_visible(ahora) == "vencida"
    # Una alta iniciada no vence: la persona puede volver a ver el avance o reintentar.
    assert replace(vencida, estado="en_curso").abierta(ahora)


# --- Consola: invitaciones, empresas y operadores -----------------------------------


async def test_invitar_vence_a_las_24_horas_y_revoca_la_anterior(plataforma):
    actor = _sesion(_operador(plataforma["repo"]))

    primera, _, _ = await servicios.invitar(actor, "Admin@Pepe.com ", False, None)
    segunda, enlace, envio = await servicios.invitar(actor, "admin@pepe.com", True, None)

    repo = plataforma["repo"]
    assert repo.invitaciones[primera.id].estado == "revocada"
    assert segunda.email == "admin@pepe.com"
    assert abs((segunda.vence_en - ahora_utc()) - VIGENCIA_INVITACION) < timedelta(seconds=5)
    assert enlace.endswith("/registra-tu-empresa?email=admin%40pepe.com")
    assert enlace.startswith("https://registrar-empresa.")
    assert envio == correos.ENVIADO
    destinatario, _, texto = plataforma["correos"][-1]
    assert destinatario == "admin@pepe.com" and enlace in texto
    assert plataforma["eventos"][0][1]["envio"] == "copiada"


async def test_revocar_solo_invitaciones_pendientes(plataforma):
    actor = _sesion(_operador(plataforma["repo"]))
    invitacion, _, _ = await servicios.invitar(actor, "a@b.com", False, None)

    await servicios.revocar(actor, invitacion.id, None)
    with pytest.raises(ErrorDominio) as error:
        await servicios.revocar(actor, invitacion.id, None)
    assert error.value.codigo == "PLA_VAL_004"


async def test_suspender_y_reactivar_empresa(plataforma, monkeypatch):
    actor = _sesion(_operador(plataforma["repo"]))
    tenant = Tenant(uuid4(), "pepe", "Pepe", "DB", "db", "activo")
    estados = []
    monkeypatch.setattr(servicios.tenants, "obtener_por_id", lambda tid: tenant)
    monkeypatch.setattr(
        servicios.tenants, "actualizar_estado", lambda tid, estado: estados.append(estado)
    )

    await servicios.cambiar_estado_empresa(actor, str(tenant.id), "suspender", None)
    with pytest.raises(ErrorDominio) as error:
        await servicios.cambiar_estado_empresa(actor, str(tenant.id), "reactivar", None)

    assert estados == ["suspendido"]
    assert error.value.codigo == "PLA_VAL_003"
    assert plataforma["eventos"][-1][0] == "plataforma.empresa_suspender"


async def test_operadores_alta_duplicada_y_baja(plataforma):
    repo = plataforma["repo"]
    yo = _operador(repo)
    actor = _sesion(yo)

    nuevo, aviso = await servicios.alta_operador(actor, "otro@plataforma.com", "Otro", None)
    assert nuevo.estado == "invitado" and aviso == correos.ENVIADO
    with pytest.raises(ErrorDominio) as error:
        await servicios.alta_operador(actor, "OTRO@plataforma.com", None, None)
    assert error.value.codigo == "PLA_VAL_001"

    with pytest.raises(ErrorDominio) as error:
        await servicios.baja_operador(actor, yo.id, None)
    assert error.value.codigo == "PLA_VAL_002"
    await servicios.baja_operador(actor, nuevo.id, None)
    assert repo.operadores[nuevo.id].estado == "baja"
    # Dado de baja, se lo puede volver a dar de alta.
    vuelto, _ = await servicios.alta_operador(actor, "otro@plataforma.com", None, None)
    assert vuelto.id == nuevo.id and vuelto.estado == "invitado"


# --- Registro: código por correo ----------------------------------------------------


async def _invitacion(plataforma, email="admin@pepe.com") -> Invitacion:
    actor = _sesion(_operador(plataforma["repo"]))
    invitacion, _, _ = await servicios.invitar(actor, email, False, None)
    return invitacion


async def test_codigo_sin_invitacion_no_envia_nada_ni_lo_revela(plataforma):
    await registro.solicitar_codigo("nadie@pepe.com")

    assert plataforma["correos"] == []
    assert plataforma["eventos"][-1][0] == "plataforma.registro_codigo_sin_invitacion"


async def test_codigo_limita_los_pedidos_por_email(plataforma):
    await _invitacion(plataforma)

    await registro.solicitar_codigo("admin@pepe.com")
    with pytest.raises(ErrorDominio) as error:
        await registro.solicitar_codigo("admin@pepe.com")

    assert error.value.codigo == "PLA_INV_002"
    assert len(plataforma["correos"]) == 1


async def test_codigo_guarda_solo_el_hash_y_abre_la_sesion(plataforma):
    invitacion = await _invitacion(plataforma)
    await registro.solicitar_codigo("admin@pepe.com")
    codigo = _codigo_del_correo(plataforma["correos"], "admin@pepe.com")
    guardada = plataforma["repo"].invitaciones[invitacion.id]

    assert len(codigo) == 6 and codigo.isdigit()
    assert guardada.codigo_hash == registro.hash_codigo(invitacion.id, codigo)
    assert codigo not in guardada.codigo_hash

    token = await registro.verificar_codigo("admin@pepe.com", codigo, "1.2.3.4")
    actual = await registro.exigir_sesion(token)
    assert actual.invitacion.id == invitacion.id
    # El código sirve una sola vez.
    with pytest.raises(ErrorDominio):
        await registro.verificar_codigo("admin@pepe.com", codigo, "1.2.3.4")


async def test_codigo_incorrecto_suma_intentos_y_se_invalida(plataforma):
    invitacion = await _invitacion(plataforma)
    await registro.solicitar_codigo("admin@pepe.com")
    codigo = _codigo_del_correo(plataforma["correos"], "admin@pepe.com")

    for _ in range(INTENTOS_CODIGO):
        with pytest.raises(ErrorDominio) as error:
            await registro.verificar_codigo("admin@pepe.com", "999999x", "1.2.3.4")
        assert error.value.codigo == "PLA_INV_001"

    assert plataforma["repo"].invitaciones[invitacion.id].codigo_hash is None
    with pytest.raises(ErrorDominio):
        await registro.verificar_codigo("admin@pepe.com", codigo, "1.2.3.4")


async def test_codigo_vencido_o_invitacion_vencida(plataforma):
    invitacion = await _invitacion(plataforma)
    await registro.solicitar_codigo("admin@pepe.com")
    codigo = _codigo_del_correo(plataforma["correos"], "admin@pepe.com")
    repo = plataforma["repo"]
    repo.invitaciones[invitacion.id] = replace(
        repo.invitaciones[invitacion.id], codigo_vence_en=ahora_utc() - timedelta(seconds=1)
    )
    with pytest.raises(ErrorDominio):
        await registro.verificar_codigo("admin@pepe.com", codigo, None)

    repo.invitaciones[invitacion.id] = replace(
        repo.invitaciones[invitacion.id], vence_en=ahora_utc() - timedelta(seconds=1)
    )
    await registro._redis().delete("registro:espera:admin@pepe.com")
    await registro.solicitar_codigo("admin@pepe.com")
    assert len(plataforma["correos"]) == 1  # vencida: no se envía un código nuevo


# --- Registro: alta de la empresa ---------------------------------------------------


async def _sesion_registro(plataforma, email="admin@pepe.com") -> registro.SesionRegistro:
    await _invitacion(plataforma, email)
    await registro.solicitar_codigo(email)
    codigo = _codigo_del_correo(plataforma["correos"], email)
    return await registro.exigir_sesion(await registro.verificar_codigo(email, codigo, None))


async def _esperar_alta():
    for _ in range(50):
        if not registro._tareas:
            return
        await asyncio.sleep(0.01)


async def test_configurar_crea_la_empresa_y_marca_la_invitacion(plataforma):
    actual = await _sesion_registro(plataforma)

    await registro.configurar(actual, "Pepe Argento S.A.", "pepe", "Pepe")
    await _esperar_alta()

    assert plataforma["altas"] == [("pepe", "Pepe Argento S.A.", "admin@pepe.com", "Pepe")]
    invitacion = plataforma["repo"].invitaciones[actual.invitacion.id]
    assert invitacion.estado == "usada"
    assert invitacion.tenant_id == str(plataforma["empresas"]["pepe"].id)
    estado = await registro.estado(actual)
    assert estado["progreso"]["estado"] == "listo"
    assert estado["progreso"]["url"].startswith("https://pepe.")
    assert estado["empresa"] == {"slug": "pepe", "nombre": "Pepe Argento S.A."}
    with pytest.raises(ErrorDominio) as error:
        await registro.configurar(await registro.exigir_sesion(actual.token), "X S.A.", "x", "X")
    assert error.value.codigo == "PLA_INV_003"


async def test_configurar_rechaza_direccion_ocupada_o_reservada(plataforma):
    plataforma["empresas"]["acme"] = Tenant(uuid4(), "acme", "ACME", "DB", "db", "activo")
    actual = await _sesion_registro(plataforma)

    with pytest.raises(ErrorDominio) as ocupada:
        await registro.configurar(actual, "Pepe S.A.", "acme", "Pepe")
    with pytest.raises(ErrorDominio) as reservada:
        await registro.configurar(actual, "Pepe S.A.", "plataforma", "Pepe")

    assert ocupada.value.codigo == "TEN_VAL_001"
    assert reservada.value.codigo == "TEN_VAL_004"
    assert plataforma["repo"].invitaciones[actual.invitacion.id].estado == "pendiente"
    assert await registro.disponible("pepe") and not await registro.disponible("acme")


async def test_falla_del_alta_permite_reintentar_con_la_misma_direccion(plataforma, monkeypatch):
    actual = await _sesion_registro(plataforma)
    intentos = []

    async def falla_y_despues_anda(slug, nombre, email, nombre_admin=None, progreso=None):
        plataforma["empresas"][slug] = Tenant(uuid4(), slug, nombre, "DB", "db", "error")
        await progreso("base_sql")
        intentos.append(slug)
        if len(intentos) == 1:
            raise ErrorDominio("TEN_APR_000", "SQL Server no responde")
        return plataforma["empresas"][slug]

    monkeypatch.setattr(tenants_servicios, "aprovisionar", falla_y_despues_anda)
    await registro.configurar(actual, "Pepe S.A.", "pepe", "Pepe")
    await _esperar_alta()
    assert (await registro.estado(actual))["progreso"]["estado"] == "error"

    actual = await registro.exigir_sesion(actual.token)
    with pytest.raises(ErrorDominio) as error:
        await registro.configurar(actual, "Pepe S.A.", "otra", "Pepe")
    assert error.value.codigo == "PLA_INV_004"
    await registro.configurar(actual, "Pepe S.A.", "pepe", "Pepe")
    await _esperar_alta()
    assert (await registro.estado(actual))["progreso"]["estado"] == "listo"


async def test_revocar_corta_la_sesion_de_registro(plataforma):
    actual = await _sesion_registro(plataforma)
    plataforma["repo"].revocar_invitacion(actual.invitacion.id)

    with pytest.raises(ErrorDominio) as error:
        await registro.exigir_sesion(actual.token)
    assert error.value.codigo == "PLA_INV_000"


# --- API y páginas --------------------------------------------------------------------


def _navegador(host) -> TestClient:
    return TestClient(app, base_url=f"https://{host}", follow_redirects=False)


async def test_api_de_la_consola_exige_operador_en_su_subdominio(plataforma):
    operador = _operador(plataforma["repo"])
    sesion = await sesiones.crear(
        {
            "usuario_id": operador.id,
            "tenant_id": TENANT_PLATAFORMA,
            "tenant_slug": TENANT_PLATAFORMA,
            "email": operador.email,
            "nombre": "Ops",
            "rol": "OPERADOR",
        },
        3600,
        tokens={},
    )
    consola = _navegador("plataforma.localhost")
    assert consola.get("/api/plataforma/operadores").status_code == 401

    consola.cookies.set(sesiones.COOKIE, sesion.id)
    respuesta = consola.get("/api/plataforma/operadores").json()
    assert respuesta["operadores"][0]["soy_yo"] is True
    assert (
        consola.post("/api/plataforma/invitaciones", json={"email": "a@b.com"}).status_code == 403
    )
    creada = consola.post(
        "/api/plataforma/invitaciones", json={"email": "a@b.com", "enviar": False}, headers=CABECERA
    )
    assert creada.status_code == 201 and creada.json()["envio"] is None
    pagina = consola.get("/")
    assert pagina.status_code == 200 and "consolaPlataforma" in pagina.text

    # La misma cookie en el subdominio de una empresa no sirve.
    otra = _navegador("acme.localhost")
    otra.cookies.set(sesiones.COOKIE, sesion.id)
    assert otra.get("/api/plataforma/operadores").status_code == 401

    # Un operador dado de baja pierde el acceso de inmediato.
    plataforma["repo"].dar_de_baja_operador(operador.id)
    assert consola.get("/api/plataforma/operadores").status_code == 401
    assert consola.get("/").headers["location"] == "/ingresar"


async def test_registro_solo_en_su_subdominio(plataforma):
    await _invitacion(plataforma)
    registro_web = _navegador("registrar-empresa.localhost")

    pagina = registro_web.get("/registra-tu-empresa?email=Admin%40Pepe.com")
    assert pagina.status_code == 200 and "admin@pepe.com" in pagina.text
    assert registro_web.get("/").headers["location"] == "/registra-tu-empresa"
    assert _navegador("acme.localhost").get("/registra-tu-empresa").status_code == 403
    assert (
        _navegador("acme.localhost")
        .post("/api/registro/codigo", json={"email": "a@b.com"}, headers=CABECERA)
        .status_code
        == 403
    )

    pedido = registro_web.post(
        "/api/registro/codigo", json={"email": "admin@pepe.com"}, headers=CABECERA
    )
    assert pedido.status_code == 204
    codigo = _codigo_del_correo(plataforma["correos"], "admin@pepe.com")
    assert registro_web.get("/api/registro/sesion").status_code == 403
    verificado = registro_web.post(
        "/api/registro/verificar",
        json={"email": "admin@pepe.com", "codigo": codigo},
        headers=CABECERA,
    )
    assert verificado.status_code == 200 and verificado.json()["invitacion"] == "pendiente"
    assert "samesite=strict" in verificado.headers["set-cookie"].lower()
    assert registro_web.get("/api/registro/disponible?slug=pepe").json()["disponible"] is True


# --- Ingreso del operador por Auth0 --------------------------------------------------


@pytest.fixture
def auth0(monkeypatch, plataforma):
    monkeypatch.setenv("AUTH0_DOMAIN", DOMINIO)
    monkeypatch.setenv("AUTH0_CLIENT_ID", CLIENT_ID)
    monkeypatch.setenv("AUTH0_AUDIENCE", AUDIENCIA)
    monkeypatch.setenv("SECRETO_AUTH0_CLIENT_SECRET", "secreto")
    obtener_configuracion.cache_clear()
    falso = Auth0Falso()
    cliente = ClienteAuth0(obtener_configuracion(), transporte=falso.transporte())
    monkeypatch.setattr(ingreso, "cliente_auth0", lambda: cliente)
    yield falso
    obtener_configuracion.cache_clear()


def _ingresar(navegador, auth0, **identidad):
    respuesta = navegador.get("/auth/login")
    parametros = {
        k: v[0] for k, v in parse_qs(urlparse(respuesta.headers["location"]).query).items()
    }
    auth0.identidad = {
        "nonce": parametros["nonce"],
        "org_id": ORGANIZACION_PLATAFORMA,
        "amr": ["mfa"],
        "email_verified": True,
        **identidad,
    }
    return navegador.get(f"/auth/callback?code=abc&state={parametros['state']}"), parametros


async def test_ingreso_del_operador_con_la_organizacion_de_la_plataforma(plataforma, auth0):
    repo = plataforma["repo"]
    invitado = _operador(repo, "nuevo@plataforma.com", estado="invitado")
    consola = _navegador("plataforma.localhost")

    respuesta, parametros = _ingresar(
        consola, auth0, sub="google|nuevo", email="nuevo@plataforma.com", name="Nuevo"
    )

    assert parametros["organization"] == ORGANIZACION_PLATAFORMA
    assert parametros["redirect_uri"] == "https://plataforma.localhost/auth/callback"
    assert respuesta.status_code == 303 and respuesta.headers["location"] == "/"
    assert repo.operadores[invitado.id].estado == "activo"
    assert repo.operadores[invitado.id].auth0_usuario_id == "google|nuevo"
    assert consola.get("/").status_code == 200


async def test_ingreso_rechaza_a_quien_no_es_operador(plataforma, auth0):
    respuesta, _ = _ingresar(
        _navegador("plataforma.localhost"), auth0, sub="google|x", email="x@y.com"
    )

    assert respuesta.status_code == 403
    assert "PLA_PER_000" in respuesta.text
    assert plataforma["eventos"][-1][0] == "plataforma.ingreso_rechazado"


async def test_ingreso_sin_consola_configurada(plataforma, auth0):
    plataforma["repo"].org = None

    respuesta = _navegador("plataforma.localhost").get("/auth/login")
    assert respuesta.status_code == 503 and "PLA_CFG_000" in respuesta.text
