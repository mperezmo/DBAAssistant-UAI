// Ejecución de una remediación: destino, diagnóstico, confirmación y progreso en vivo.
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
    inicio: "", jobId: "", confirmacion: "",
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
        inicio: datos.inicio || "", jobId: datos.job_id || "", confirmacion: datos.confirmacion || "",
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
        this.mensaje = window.DBA.aviso(error);
      }
    },

    cerrar() {
      this.abierto = false;
      if (this.ejecucion) window.dispatchEvent(new CustomEvent("workaround-ejecutado"));
    },

    conexionElegida() {
      return this.conexiones.find((c) => c.id === this.conexionId) || null;
    },

    conexionesAplicables() {
      return this.conexiones.filter((c) => !this.w || this.w.ambientes.includes(c.ambiente));
    },

    async cambioConexion(conservarBase = false) {
      this.diag = null;
      this.mensaje = "";
      if (!conservarBase) {
        this.base = "";
        this.confirmacion = "";
      }
      if (!this.conexionId) return;
      if (this.w.requiere_base) {
        try {
          this.bases = (await api("GET", `/api/workarounds/conexiones/${this.conexionId}/bases`)).bases;
        } catch (error) {
          this.mensaje = window.DBA.aviso(error);
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
      if (this.esBackup()) parametros.inicio = this.inicio;
      if (this.w.id === "reintentar_job") parametros.job_id = this.jobId;
      if (this.w.id === "iniciar_agente") parametros.servicio = this.servicio;
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
        // Backup programado: se propone la hora sugerida y se arman los pasos con ella.
        if (this.esBackup() && !this.inicio && this.diag.diagnostico.sugerido) {
          this.inicio = this.diag.diagnostico.sugerido;
          this.cargando = false;
          return this.diagnosticar();
        }
      } catch (error) {
        this.mensaje = window.DBA.aviso(error);
      }
      this.cargando = false;
    },

    esBackup() {
      return ["programar_backup_full", "programar_backup_log"].includes(this.w?.id);
    },
    // Workarounds en los que se elige algo del diagnóstico antes de ver los pasos.
    conEleccion() {
      return ["kill_bloqueos", "iniciar_servicio", "iniciar_agente", "base_online", "reintentar_job"].includes(this.w?.id);
    },

    pasosVisibles() {
      if (!this.diag) return [];
      if (this.w.id === "kill_bloqueos") return this.sids.map((s) => `KILL ${s}`);
      if (["iniciar_servicio", "iniciar_agente"].includes(this.w.id)) return this.servicio ? [`Start-Service ${this.servicio}`] : [];
      if (this.w.id === "reintentar_job") {
        const job = (this.diag.diagnostico.jobs || []).find((j) => j.id === this.jobId);
        return job ? [`sp_start_job ${job.nombre}`] : [];
      }
      if (this.w.id === "base_online") return this.baseOffline ? [`ALTER DATABASE [${this.baseOffline}] SET ONLINE`] : [];
      return this.diag.pasos.map((p) => p.comando);
    },

    // En PROD, además, hay que escribir el nombre de la instancia (el alias de la conexión).
    puedeEjecutar() {
      if (!this.diag || !this.pasosVisibles().length) return false;
      return !this.diag.prod || this.confirmacion.trim() === this.conexionElegida()?.alias;
    },

    async ejecutar() {
      this.enviando = true;
      this.mensaje = "";
      try {
        const r = await api("POST", `/api/workarounds/${this.w.id}/ejecutar`, this.cuerpo());
        await this.seguir(r.ejecucion_id);
      } catch (error) {
        // El modal ya avisó que en PROD se pide el segundo factor: no se vuelve a preguntar.
        if (error.codigo === "AMB_VAL_000" && (this.diag?.prod || window.confirm(this.TEXTOS_EJ.confirmarMfa))) {
          const guardar = () => sessionStorage.setItem(CLAVE_PENDIENTE, JSON.stringify({
            workaround: this.w.id, conexion_id: this.conexionId, base: this.base,
            alerta_id: this.alertaId, sids: this.sids, servicio: this.servicio, base_offline: this.baseOffline,
            inicio: this.inicio, job_id: this.jobId, confirmacion: this.confirmacion,
          }));
          this.enviando = false;
          if (await window.DBA.segundoFactor(location.pathname + location.search, guardar)) await this.ejecutar();
          return;
        }
        this.mensaje = window.DBA.aviso(error);
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
        this.mensaje = window.DBA.aviso(error);
      }
    },
  }));
});
