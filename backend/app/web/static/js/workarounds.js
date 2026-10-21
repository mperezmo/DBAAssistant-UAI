// Componente Alpine de la biblioteca de workarounds (CU-000-005-A a D).
document.addEventListener("alpine:init", () => {
  const TEXTOS = window.TEXTOS_WORKAROUNDS;
  const api = window.DBA.api;
  const CLAVE_AUTOMATIZACION = "dba.workarounds.automatizacion";

  const fecha = (iso) => (iso ? new Date(iso).toLocaleString(window.DBA.locale) : "—");
  const formularioVacio = () => ({
    id: null, nombre: "", descripcion: "", categoria: "mantenimiento", criticidad: "info",
    ambientes: ["DEV", "TESTING"], roles: ["ADMINISTRADOR", "DBA"], transaccional: true, condicion: "", pasos: [""],
    validar_conexion_id: "", validar_base: "", bases: [], mensaje: "", guardando: false,
  });

  Alpine.data("workarounds", () => ({
    TEXTOS,
    lista: [], permisos: {}, cargado: false, error: null,
    categoria: "", busqueda: "",
    detalle: null, formulario: null, conexiones: [],
    validacion: { conexion_id: "", base: "", bases: [], mensaje: "", ok: false, enviando: false },
    panel: null, mensajePanel: "", autorizaciones: [], automatizaciones: [], ejecuciones: [],
    nuevaAutomatizacion: { evento: "log", workaround: "shrink_log", conexion_id: "" },

    async iniciar() {
      await Promise.all([this.cargar(), this.cargarConexiones(), this.cargarAutorizaciones()]);
      const params = new URLSearchParams(location.search);
      if (params.get("autorizaciones")) this.abrirPanel("autorizaciones");
      if (params.get("ejecucion")) this.abrirPanel("ejecuciones");
      // Vuelta de la confirmación con segundo factor al crear una automatización.
      const pendiente = sessionStorage.getItem(CLAVE_AUTOMATIZACION);
      if (pendiente) {
        sessionStorage.removeItem(CLAVE_AUTOMATIZACION);
        this.nuevaAutomatizacion = JSON.parse(pendiente);
        await this.abrirPanel("automatizaciones");
        await this.crearAutomatizacion();
      }
    },

    async cargar() {
      try {
        const r = await api("GET", "/api/workarounds");
        this.lista = r.workarounds;
        this.permisos = r.permisos;
      } catch (error) {
        this.error = error.message;
      }
      this.cargado = true;
    },

    async cargarConexiones() {
      try {
        this.conexiones = (await api("GET", "/api/monitor")).conexiones;
      } catch {
        this.conexiones = [];
      }
    },

    async cargarAutorizaciones() {
      try {
        this.autorizaciones = (await api("GET", "/api/workarounds/autorizaciones")).autorizaciones;
      } catch {
        this.autorizaciones = [];
      }
    },

    subtitulo() {
      return TEXTOS.subtitulo.replace("{cantidad}", this.lista.length);
    },

    filtrados() {
      const texto = this.busqueda.trim().toLowerCase();
      return this.lista
        .filter((w) => !this.categoria || w.categoria === this.categoria)
        .filter((w) => !texto || `${w.nombre} ${w.descripcion} ${w.codigo}`.toLowerCase().includes(texto));
    },

    puedeEjecutar(w) {
      return w && w.roles.includes(window.DBA_ROL);
    },
    claseCriticidad(c) {
      return { critical: "tag-red", warning: "tag-yellow", info: "tag-blue" }[c];
    },
    claseEstado(e) {
      return { exitosa: "tag-green", fallida: "tag-red", revertida: "tag-red", en_curso: "tag-blue" }[e];
    },
    fecha,

    async verDetalle(w) {
      this.validacion = { conexion_id: "", base: "", bases: [], mensaje: "", ok: false, enviando: false };
      try {
        this.detalle = await api("GET", `/api/workarounds/${w.id}`);
      } catch (error) {
        this.error = error.message;
      }
    },

    // Bases ya sincronizadas en el Sandbox del servidor elegido.
    async basesSandbox() {
      const v = this.validacion;
      v.base = "";
      v.bases = [];
      v.mensaje = "";
      if (!v.conexion_id) return;
      try {
        const estado = await api("GET", `/api/sandbox/conexiones/${v.conexion_id}`);
        v.bases = estado.bases.filter((b) => b.estado === "ok").map((b) => b.base);
        if (!v.bases.length) v.mensaje = TEXTOS.sinBasesSandbox;
      } catch (error) {
        v.mensaje = error.message;
      }
    },

    async validar() {
      const v = this.validacion;
      v.enviando = true;
      v.mensaje = "";
      try {
        this.detalle = await api("POST", `/api/workarounds/${this.detalle.id}/validar`, {
          conexion_id: v.conexion_id, base: v.base,
        });
        v.ok = true;
        v.mensaje = TEXTOS.validado;
        await this.cargar();
      } catch (error) {
        v.ok = false;
        v.mensaje = error.message;
      }
      v.enviando = false;
    },

    ejecutar(w) {
      this.detalle = null;
      window.dispatchEvent(new CustomEvent("ejecutar-workaround", { detail: { workaround: w.id } }));
    },

    nuevo() {
      this.formulario = formularioVacio();
    },

    editar(w) {
      this.detalle = null;
      this.formulario = {
        ...formularioVacio(), ...w, condicion: w.condicion || "", pasos: [...w.pasos],
        ambientes: [...w.ambientes], roles: [...w.roles],
      };
    },

    async basesValidacion() {
      const f = this.formulario;
      f.validar_base = "";
      f.bases = [];
      if (!f.validar_conexion_id) return;
      try {
        f.bases = (await api("GET", `/api/workarounds/conexiones/${f.validar_conexion_id}/bases`)).bases;
      } catch (error) {
        f.mensaje = error.message;
      }
    },

    async guardar() {
      const f = this.formulario;
      f.guardando = true;
      f.mensaje = "";
      const cuerpo = {
        nombre: f.nombre, descripcion: f.descripcion, categoria: f.categoria, criticidad: f.criticidad,
        ambientes: f.ambientes, roles: f.roles, transaccional: f.transaccional,
        condicion: f.condicion || null, pasos: f.pasos.filter((p) => p.trim()),
        validar_conexion_id: f.validar_conexion_id, validar_base: f.validar_base || null,
      };
      try {
        const guardado = f.id
          ? await api("PUT", `/api/workarounds/${f.id}`, cuerpo)
          : await api("POST", "/api/workarounds", cuerpo);
        this.formulario = null;
        await this.cargar();
        this.detalle = guardado;
      } catch (error) {
        f.mensaje = error.message;
        f.guardando = false;
      }
    },

    async eliminar(w) {
      if (!window.confirm(TEXTOS.confirmarEliminar.replace("{nombre}", w.nombre))) return;
      try {
        await api("DELETE", `/api/workarounds/${w.id}`);
        this.detalle = null;
        await this.cargar();
      } catch (error) {
        this.error = error.message;
      }
    },

    // --- Paneles --------------------------------------------------------------------

    async abrirPanel(panel) {
      this.panel = panel;
      this.mensajePanel = "";
      try {
        if (panel === "autorizaciones") await this.cargarAutorizaciones();
        if (panel === "automatizaciones") {
          this.automatizaciones = (await api("GET", "/api/workarounds/automatizaciones")).automatizaciones;
        }
        if (panel === "ejecuciones") {
          this.ejecuciones = (await api("GET", "/api/workarounds/ejecuciones")).ejecuciones;
        }
      } catch (error) {
        this.mensajePanel = error.message;
      }
    },

    async resolver(a, aprobar) {
      try {
        await api("POST", `/api/workarounds/autorizaciones/${a.id}`, { aprobar });
        await this.cargarAutorizaciones();
      } catch (error) {
        this.mensajePanel = error.message;
      }
    },

    nombreDe(id) {
      return this.lista.find((w) => w.id === id)?.nombre || id;
    },
    nombreConexion(id) {
      return id ? this.conexiones.find((c) => c.id === id)?.alias || "—" : TEXTOS.todasConexiones;
    },
    automatizables() {
      return this.lista.filter((w) => w.eventos_automaticos.includes(this.nuevaAutomatizacion.evento));
    },

    async crearAutomatizacion() {
      const datos = { ...this.nuevaAutomatizacion, conexion_id: this.nuevaAutomatizacion.conexion_id || null };
      try {
        await api("POST", "/api/workarounds/automatizaciones", datos);
        await this.abrirPanel("automatizaciones");
      } catch (error) {
        if (error.codigo === "AMB_VAL_000" && window.confirm(TEXTOS.confirmarMfa)) {
          sessionStorage.setItem(CLAVE_AUTOMATIZACION, JSON.stringify(this.nuevaAutomatizacion));
          window.location.href = `/auth/confirmar?volver=${encodeURIComponent("/workarounds")}`;
          return;
        }
        this.mensajePanel = error.message;
      }
    },

    async eliminarAutomatizacion(a) {
      try {
        await api("DELETE", `/api/workarounds/automatizaciones/${a.id}`);
        await this.abrirPanel("automatizaciones");
      } catch (error) {
        this.mensajePanel = error.message;
      }
    },
  }));
});
