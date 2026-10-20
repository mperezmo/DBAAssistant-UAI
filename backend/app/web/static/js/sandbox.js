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

  Alpine.data("sandbox", () => ({
    TEXTOS,
    conexiones: [], conexionId: "", base: "", sql: "", estado: null, error: null,
    resultado: null, ejecutando: false, historial: [], puedePromover: false,
    filtros: { estado: "", usuario: "" }, promocion: null,

    async iniciar() {
      try {
        this.conexiones = (await api("GET", "/api/monitor")).conexiones;
      } catch (error) {
        this.error = error.message;
        return;
      }
      if (!this.conexiones.length) return;
      const recordada = leer(CLAVE_CONEXION);
      this.conexionId = this.conexiones.some((c) => c.id === recordada) ? recordada : this.conexiones[0].id;
      await Promise.all([this.cargarEstado(), this.cargarHistorial()]);
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
        this.error = error.message;
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
      const texto = horas < 1 ? `${Math.round(horas * 60)} min` : `${horas.toFixed(1)} h`;
      return TEXTOS.hace.replace("{tiempo}", texto);
    },
    fecha(iso) {
      return iso ? new Date(iso).toLocaleString() : "—";
    },
    puedeEjecutar() {
      return !this.ejecutando && this.base && this.sql.trim();
    },

    async sincronizar() {
      try {
        this.estado = await api("POST", `/api/sandbox/conexiones/${this.conexionId}/sincronizar`);
      } catch (error) {
        this.error = error.message;
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
        this.error = error.message;
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
        this.puedePromover = r.puede_promover;
      } catch (error) {
        this.error = error.message;
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
        this.error = error.message;
      }
    },

    async previa(r) {
      this.promocion = { id: r.id, datos: null, mensaje: "", exito: false, enviando: false };
      try {
        this.promocion.datos = await api("POST", `/api/sandbox/historial/${r.id}/previa`);
      } catch (error) {
        this.promocion.mensaje = error.message;
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
          sessionStorage.setItem(CLAVE_PROMOCION, p.id);
          window.location.href = `/auth/confirmar?volver=${encodeURIComponent("/sandbox")}`;
          return;
        }
        p.mensaje = error.message;
      }
      p.enviando = false;
    },
  }));
});
