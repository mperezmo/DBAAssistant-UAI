// Componente Alpine del Sandbox (CU-000-002-A, B y C).
document.addEventListener("alpine:init", () => {
  const TEXTOS = window.TEXTOS_SANDBOX;
  const api = window.DBA.api;
  const CLAVE_CONEXION = "dba.sandbox.conexion";
  const CLAVE_PROMOCION = "dba.sandbox.promocion";
  const REFRESCO_ESTADO_MS = 5000;
  const SEPARADOR_LOTES = /^\s*GO\s*$/gim;

  const leer = (clave) => {
    try { return localStorage.getItem(clave); } catch { return null; }
  };
  const recordar = (clave, valor) => {
    try { localStorage.setItem(clave, valor); } catch { /* sin almacenamiento local */ }
  };

  const CLAVE_PRECARGA = "dba.sandbox.precarga";

  Alpine.data("sandbox", () => ({
    TEXTOS,
    conexiones: [], conexionId: "", base: "", sql: "", estado: null, error: null,
    resultado: null, ejecutando: false, historial: [], ambientesPromover: [],
    filtros: { estado: "", usuario: "" }, promocion: null,

    async iniciar() {
      try {
        this.conexiones = (await api("GET", "/api/monitor")).conexiones;
      } catch (error) {
        this.error = window.DBA.aviso(error);
        return;
      }
      if (!this.conexiones.length) return;
      // Desde el Monitoreo ("Probar en Sandbox") llega una consulta para validar.
      let precarga = null;
      try {
        precarga = JSON.parse(sessionStorage.getItem(CLAVE_PRECARGA) || "null");
        sessionStorage.removeItem(CLAVE_PRECARGA);
      } catch { /* sin almacenamiento de sesión */ }
      const recordada = precarga?.conexion_id || leer(CLAVE_CONEXION);
      this.conexionId = this.conexiones.some((c) => c.id === recordada) ? recordada : this.conexiones[0].id;
      await Promise.all([this.cargarEstado(), this.cargarHistorial()]);
      if (precarga) Object.assign(this, { base: precarga.base || "", sql: precarga.sql || "" });
      const params = new URLSearchParams(location.search);
      if (params.get("registro")) await this.ver({ id: params.get("registro") });
      // Vuelta de la confirmación con segundo factor: se retoma la promoción.
      const pendiente = sessionStorage.getItem(CLAVE_PROMOCION);
      if (pendiente) {
        sessionStorage.removeItem(CLAVE_PROMOCION);
        await this.ver({ id: pendiente });
        await this.previa(this.resultado);
      }
      // Mientras se sincroniza se actualiza el estado solo.
      setInterval(() => !document.hidden && this.estado?.estado === "sincronizando" && this.cargarEstado(), REFRESCO_ESTADO_MS);
    },

    async cambioConexion() {
      recordar(CLAVE_CONEXION, this.conexionId);
      this.base = "";
      this.resultado = null;
      await this.cargarEstado();
    },

    async cargarEstado() {
      try {
        this.estado = await api("GET", `/api/sandbox/conexiones/${this.conexionId}`);
        this.error = null;
      } catch (error) {
        this.error = window.DBA.aviso(error);
      }
    },

    basesListas() {
      return (this.estado?.bases || []).filter((b) => b.estado === "ok").map((b) => b.base);
    },
    textoBases() {
      const bases = this.estado?.bases || [];
      if (!bases.length) return TEXTOS.sinBases;
      return bases.map((b) => `${b.base}${b.estado === "ok" ? "" : " ✕"}`).join(", ");
    },
    claseEstado() {
      return { listo: "tag-green", sincronizando: "tag-yellow", error: "tag-red" }[this.estado?.estado] || "";
    },
    subtitulo() {
      const c = this.conexiones.find((x) => x.id === this.conexionId);
      return TEXTOS.subtitulo.replace("{instancia}", c ? c.alias : "—");
    },
    estadisticas() {
      const lotes = this.sql.split(SEPARADOR_LOTES).filter((l) => l.trim()).length;
      const lineas = this.sql ? this.sql.split("\n").length : 0;
      return TEXTOS.estadisticas
        .replace("{lotes}", lotes).replace("{lineas}", lineas)
        .replace("{bytes}", new Blob([this.sql]).size);
    },
    antiguedad(iso) {
      if (!iso) return "—";
      const horas = Math.max(0, (Date.now() - new Date(iso).getTime()) / 3600000);
      const texto = horas < 1 ? `${Math.round(horas * 60)} min` : `${window.DBA.numero(horas, 1)} h`;
      return TEXTOS.hace.replace("{tiempo}", texto);
    },
    fecha(iso) {
      return window.DBA.fecha(iso);
    },
    puedeEjecutar() {
      return !this.ejecutando && this.base && this.sql.trim();
    },

    async sincronizar() {
      try {
        this.estado = await api("POST", `/api/sandbox/conexiones/${this.conexionId}/sincronizar`);
      } catch (error) {
        this.error = window.DBA.aviso(error);
      }
    },

    async ejecutar(soloSintaxis) {
      this.ejecutando = true;
      this.error = null;
      try {
        const r = await api("POST", "/api/sandbox/ejecutar", {
          conexion_id: this.conexionId, base: this.base, sql: this.sql, solo_sintaxis: soloSintaxis,
        });
        this.resultado = { ...r, solo_sintaxis: soloSintaxis };
        if (!soloSintaxis) await this.cargarHistorial();
      } catch (error) {
        this.error = window.DBA.aviso(error);
      }
      this.ejecutando = false;
    },

    async cargarHistorial() {
      try {
        const params = new URLSearchParams();
        if (this.filtros.estado) params.set("estado", this.filtros.estado);
        if (this.filtros.usuario.trim()) params.set("usuario", this.filtros.usuario.trim());
        const r = await api("GET", `/api/sandbox/historial?${params}`);
        this.historial = r.registros;
        this.ambientesPromover = r.ambientes_promover;
      } catch (error) {
        this.error = window.DBA.aviso(error);
      }
    },

    async ver(r) {
      try {
        const registro = await api("GET", `/api/sandbox/historial/${r.id}`);
        this.resultado = registro;
        this.sql = registro.sql;
        if (registro.conexion_id !== this.conexionId) {
          this.conexionId = registro.conexion_id;
          await this.cargarEstado();
        }
        this.base = registro.base;
        window.scrollTo({ top: 0, behavior: "smooth" });
      } catch (error) {
        this.error = window.DBA.aviso(error);
      }
    },

    async previa(r) {
      this.promocion = { id: r.id, datos: null, mensaje: "", exito: false, enviando: false };
      try {
        this.promocion.datos = await api("POST", `/api/sandbox/historial/${r.id}/previa`);
      } catch (error) {
        this.promocion.mensaje = window.DBA.aviso(error);
      }
    },

    async promover() {
      const p = this.promocion;
      p.enviando = true;
      try {
        const r = await api("POST", `/api/sandbox/historial/${p.id}/promover`);
        p.exito = true;
        p.mensaje = TEXTOS.promovidaOk.replace("{filas}", r.filas);
        await this.cargarHistorial();
        if (this.resultado?.id === p.id) this.resultado = await api("GET", `/api/sandbox/historial/${p.id}`);
      } catch (error) {
        if (error.codigo === "AMB_VAL_000" && window.confirm(TEXTOS.confirmarMfa)) {
          if (await window.DBA.segundoFactor("/sandbox", () => sessionStorage.setItem(CLAVE_PROMOCION, p.id))) {
            await this.promover();
          }
          return;
        }
        p.mensaje = window.DBA.aviso(error);
      }
      p.enviando = false;
    },
  }));
});
