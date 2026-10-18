// Componente Alpine del Panel Admin: gestión de usuarios contra /api/usuarios.
// Alpine se carga con "defer", así que este registro corre antes de que inicialice.
document.addEventListener("alpine:init", () => {
  const TEXTOS = window.TEXTOS_USUARIOS;

  async function api(metodo, ruta, cuerpo) {
    const respuesta = await fetch(ruta, {
      method: metodo,
      headers: { "Content-Type": "application/json", "X-Solicitud": "dba" },
      body: cuerpo ? JSON.stringify(cuerpo) : undefined,
    });
    if (respuesta.status === 401) {
      window.location.href = "/ingresar";
      return null;
    }
    const datos = respuesta.status === 204 ? null : await respuesta.json();
    if (!respuesta.ok) throw new Error(`${datos.mensaje} (${datos.codigo})`);
    return datos;
  }

  Alpine.data("gestionUsuarios", () => ({
    usuarios: [],
    bajas: false,
    cargado: false,
    formulario: null,
    aviso: null,
    direccion: "",
    roles: TEXTOS.roles,
    estados: TEXTOS.estados,

    async cargar() {
      try {
        const datos = await api("GET", `/api/usuarios?bajas=${this.bajas}`);
        this.usuarios = datos.usuarios;
        this.direccion = datos.direccion_acceso;
      } catch (error) {
        this.aviso = { error: true, texto: error.message };
      }
      this.cargado = true;
    },

    abrirNuevo() {
      this.aviso = null;
      this.formulario = { id: null, email: "", nombre: "", rol: "CONSULTA", ambientes: [], error: null };
    },

    abrirEdicion(usuario) {
      this.aviso = null;
      this.formulario = { ...usuario, ambientes: [...usuario.ambientes], error: null };
    },

    async guardar() {
      const { id, email, nombre, rol, ambientes } = this.formulario;
      try {
        if (id) {
          await api("PATCH", `/api/usuarios/${id}`, { nombre, rol, ambientes });
        } else {
          await api("POST", "/api/usuarios", { email, nombre, rol, ambientes });
          this.aviso = { error: false, texto: TEXTOS.creado, direccion: this.direccion };
        }
        this.formulario = null;
        await this.cargar();
      } catch (error) {
        this.formulario.error = error.message;
      }
    },

    async darBaja(usuario) {
      if (!window.confirm(TEXTOS.confirmarBaja)) return;
      await this.ejecutar(() => api("DELETE", `/api/usuarios/${usuario.id}`));
    },

    async rehabilitar(usuario) {
      await this.ejecutar(() => api("POST", `/api/usuarios/${usuario.id}/rehabilitar`));
    },

    async ejecutar(accion) {
      this.aviso = null;
      try {
        await accion();
        await this.cargar();
      } catch (error) {
        this.aviso = { error: true, texto: error.message };
      }
    },

    claseEstado(estado) {
      return { activo: "tag-green", invitado: "tag-blue", baja: "tag-red" }[estado] || "";
    },

    fecha(iso) {
      return iso ? new Date(iso + "Z").toLocaleString("es-AR") : TEXTOS.nunca;
    },
  }));
});
