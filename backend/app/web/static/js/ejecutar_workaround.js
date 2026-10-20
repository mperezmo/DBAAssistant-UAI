// Ejecución de un workaround: destino, diagnóstico, confirmación y progreso en vivo.
document.addEventListener("alpine:init", () => {
  const api = window.DBA.api;
  const CLAVE_PENDIENTE = "dba.workarounds.ejecutar";
  const REFRESCO_MS = 1000;

  // Origen de la ejecución para la bitácora (CU-000-005-F): pantalla chica o PWA instalada.
  const origen = () =>
    window.matchMedia("(max-width: 768px)").matches || window.matchMedia("(display-mode: standalone)").matches
      ? "mobile" : "web";

  Alpine.data("ejecutorWorkaround", () => ({
    TEXTOS_EJ: window.TEXTOS_EJ,
    abierto: false, w: null, conexiones: [], bases: [],
    conexionId: "", base: "", alertaId: null, sids: [], servicio: "", baseOffline: "", motivo: "",
    diag: null, cargando: false, mensaje: "", enviando: false, ejecucion: null,

    init() {
      // Vuelta de la confirmación con segundo factor: se reabre con lo elegido.
      const pendiente = sessionStorage.getItem(CLAVE_PENDIENTE);
      if (pendiente) {
        sessionStorage.removeItem(CLAVE_PENDIENTE);
        this.abrir(JSON.parse(pendiente));
      }
    },

    async abrir(datos) {
      Object.assign(this, {
        abierto: true, w: null, bases: [], diag: null, mensaje: "", ejecucion: null, enviando: false,
        conexionId: datos.conexion_id || "", base: datos.base || "", alertaId: datos.alerta_id || null,
        sids: datos.sids || [], servicio: datos.servicio || "", baseOffline: datos.base_offline || "", motivo: "",
      });
      try {
        const [w, monitor] = await Promise.all([
          api("GET", `/api/workarounds/${datos.workaround}`), api("GET", "/api/monitor"),
        ]);
        this.w = w;
        // Desde la alerta llega la base caída: es un parámetro, el workaround corre desde master.
        if (w.id === "base_online" && this.base) [this.baseOffline, this.base] = [this.base, ""];
        this.conexiones = monitor.conexiones;
        if (this.conexionId) await this.cambioConexion(true);
      } catch (error) {
        this.mensaje = error.message;
      }
    },

    cerrar() {
      this.abierto = false;
      if (this.ejecucion) window.dispatchEvent(new CustomEvent("workaround-ejecutado"));
    },

    conexionesAplicables() {
      return this.conexiones.filter((c) => !this.w || this.w.ambientes.includes(c.ambiente));
    },

    async cambioConexion(conservarBase = false) {
      this.diag = null;
      this.mensaje = "";
      if (!conservarBase) this.base = "";
      if (!this.conexionId) return;
      if (this.w.requiere_base) {
        try {
          this.bases = (await api("GET", `/api/workarounds/conexiones/${this.conexionId}/bases`)).bases;
        } catch (error) {
          this.mensaje = error.message;
          return;
        }
        if (!this.base) return;
      }
      await this.diagnosticar();
    },

    cuerpo() {
      const parametros = {};
      if (this.w.id === "kill_bloqueos") parametros.sids = this.sids;
      if (this.w.id === "iniciar_servicio") parametros.servicio = this.servicio;
      if (this.w.id === "base_online") parametros.base = this.baseOffline;
      return {
        conexion_id: this.conexionId, base: this.base || null, parametros,
        alerta_id: this.alertaId, origen: origen(),
      };
    },

    async diagnosticar() {
      if (!this.conexionId || (this.w.requiere_base && !this.base)) return;
      this.cargando = true;
      this.mensaje = "";
      try {
        this.diag = await api("POST", `/api/workarounds/${this.w.id}/diagnostico`, this.cuerpo());
        const vigentes = (this.diag.diagnostico.bloqueadores || []).map((b) => b.sid);
        this.sids = this.sids.filter((s) => vigentes.includes(s));
      } catch (error) {
        this.mensaje = error.message;
      }
      this.cargando = false;
    },

    pasosVisibles() {
      if (!this.diag) return [];
      if (this.w.id === "kill_bloqueos") return this.sids.map((s) => `KILL ${s}`);
      if (this.w.id === "iniciar_servicio") return this.servicio ? [`Start-Service ${this.servicio}`] : [];
      if (this.w.id === "base_online") return this.baseOffline ? [`ALTER DATABASE [${this.baseOffline}] SET ONLINE`] : [];
      return this.diag.pasos.map((p) => p.comando);
    },

    puedeEjecutar() {
      return this.diag && this.pasosVisibles().length > 0;
    },

    async ejecutar() {
      this.enviando = true;
      this.mensaje = "";
      try {
        const r = await api("POST", `/api/workarounds/${this.w.id}/ejecutar`, this.cuerpo());
        await this.seguir(r.ejecucion_id);
      } catch (error) {
        if (error.codigo === "AMB_VAL_000" && window.confirm(this.TEXTOS_EJ.confirmarMfa)) {
          sessionStorage.setItem(CLAVE_PENDIENTE, JSON.stringify({
            workaround: this.w.id, conexion_id: this.conexionId, base: this.base,
            alerta_id: this.alertaId, sids: this.sids, servicio: this.servicio, base_offline: this.baseOffline,
          }));
          const volver = location.pathname + location.search;
          window.location.href = `/auth/confirmar?volver=${encodeURIComponent(volver)}`;
          return;
        }
        this.mensaje = error.message;
      }
      this.enviando = false;
    },

    async seguir(id) {
      this.ejecucion = await api("GET", `/api/workarounds/ejecuciones/${id}`);
      while (this.abierto && this.ejecucion.estado === "en_curso") {
        await new Promise((resolver) => setTimeout(resolver, REFRESCO_MS));
        this.ejecucion = await api("GET", `/api/workarounds/ejecuciones/${id}`);
      }
    },

    async solicitarAutorizacion() {
      try {
        await api("POST", `/api/workarounds/${this.w.id}/autorizaciones`, {
          conexion_id: this.conexionId, motivo: this.motivo,
        });
        this.mensaje = this.TEXTOS_EJ.autorizacionPedida;
        this.motivo = "";
      } catch (error) {
        this.mensaje = error.message;
      }
    },
  }));
});
