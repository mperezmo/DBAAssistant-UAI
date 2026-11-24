// Componente Alpine del Panel Admin: gestión de usuarios contra /api/usuarios.
// Alpine se carga con "defer", así que este registro corre antes de que inicialice.
document.addEventListener("alpine:init", () => {
  const TEXTOS = window.TEXTOS_USUARIOS;

  const api = window.DBA.api;

  Alpine.data("gestionUsuarios", () => ({
    usuarios: [],
    bajas: false,
    cargado: false,
    formulario: null,
    permisos: null,
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
      this.formulario = { id: null, email: "", nombre: "", rol: "LECTURA", ambientes: [], error: null };
    },

    abrirEdicion(usuario) {
      this.aviso = null;
      this.formulario = { ...usuario, rolAnterior: usuario.rol, ambientes: [...usuario.ambientes], error: null };
    },

    async guardar() {
      const { id, email, nombre, rol, ambientes, rolAnterior } = this.formulario;
      // Cambiar el rol cambia lo que puede hacer en cada ambiente (CU-001-002-B).
      if (id && rol !== rolAnterior && !window.confirm(TEXTOS.confirmarRol)) return;
      try {
        if (id) {
          await api("PATCH", `/api/usuarios/${id}`, { nombre, rol, ambientes });
        } else {
          const creado = await api("POST", "/api/usuarios", { email, nombre, rol, ambientes });
          // Si el correo salió se muestra a quién; si no, la dirección para pasársela a mano.
          this.aviso = {
            error: creado.invitacion === "fallida",
            texto: TEXTOS.creado[creado.invitacion],
            direccion: creado.invitacion === "enviada" ? creado.email : this.direccion,
          };
        }
        this.formulario = null;
        await this.cargar();
      } catch (error) {
        this.formulario.error = window.DBA.aviso(error);
      }
    },

    // Permisos por ambiente (CU-001-002-C): solo se puede quitar lo que el rol permite.
    async abrirPermisos(usuario) {
      this.aviso = null;
      try {
        const matriz = await api("GET", `/api/usuarios/${usuario.id}/permisos`);
        const marcadas = {};
        for (const [ambiente, acciones] of Object.entries(matriz.permitidas)) marcadas[ambiente] = [...acciones];
        this.permisos = { usuario, ambientes: [...matriz.ambientes], techo: matriz.techo, marcadas, error: null };
      } catch (error) {
        this.aviso = { error: true, texto: error.message };
      }
    },

    puedeMarcar(ambiente, accion) {
      return this.permisos.ambientes.includes(ambiente) && this.permisos.techo[ambiente].includes(accion);
    },

    async guardarPermisos() {
      const { usuario, ambientes, marcadas, techo } = this.permisos;
      const permitidas = {};
      for (const ambiente of ambientes) permitidas[ambiente] = marcadas[ambiente].filter((a) => techo[ambiente].includes(a));
      try {
        await api("PUT", `/api/usuarios/${usuario.id}/permisos`, { ambientes, permitidas });
        this.permisos = null;
        await this.cargar();
      } catch (error) {
        this.permisos.error = window.DBA.aviso(error);
      }
    },

    async reenviar(usuario) {
      this.aviso = null;
      try {
        await api("POST", `/api/usuarios/${usuario.id}/invitacion`);
        this.aviso = { error: false, texto: TEXTOS.reenviada, direccion: usuario.email };
      } catch (error) {
        this.aviso = { error: true, texto: error.message };
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
      return iso ? window.DBA.fecha(iso + "Z") : TEXTOS.nunca;
    },
  }));
});
