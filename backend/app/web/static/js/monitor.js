// Componente Alpine del panel de monitoreo (CU-000-003 A, B y C).
document.addEventListener("alpine:init", () => {
  const TEXTOS = window.TEXTOS_MONITOR;
  const api = window.DBA.api;
  const CLAVE_CONEXION = "dba.monitor.conexion";
  const REFRESCO_MS = 3000;
  const REFRESCO_SERIE_MS = 15000;
  const REFRESCO_COSTOSAS_MS = 30000;
  const PORCENTAJES = ["cpu", "memoria", "disco"];

  // Geometría del medidor semicircular y del gráfico (mismas medidas que el diseño).
  const CX = 100, CY = 100, R = 72;
  const W = 1100, H = 240, P = 30;

  const COLORES = {
    cpu: "var(--blue-700)", memoria: "var(--terracotta)", disco: "var(--sage)",
    latencia: "var(--blue-300)", sesiones: "var(--mustard)", bloqueos: "var(--ink-500)",
    deadlocks: "var(--danger)", conexiones_seg: "var(--gray-500)",
  };

  const punto = (angulo, radio = R) => {
    const a = (angulo * Math.PI) / 180;
    return [CX + radio * Math.cos(a), CY + radio * Math.sin(a)];
  };

  const arco = (inicio, fin) => {
    if (fin <= inicio) return "";
    const [x1, y1] = punto(inicio);
    const [x2, y2] = punto(fin);
    return `M${x1},${y1} A${R},${R} 0 0,1 ${x2},${y2}`;
  };

  const MARCAS = Array.from({ length: 11 }, (_, i) => {
    const angulo = -180 + i * 18;
    const [x1, y1] = punto(angulo, i % 5 === 0 ? R - 12 : R - 6);
    const [x2, y2] = punto(angulo);
    return `M${x1},${y1} L${x2},${y2}`;
  }).join(" ");

  const leer = (clave) => {
    try { return localStorage.getItem(clave); } catch { return null; }
  };
  const recordar = (clave, valor) => {
    try { localStorage.setItem(clave, valor); } catch { /* sin almacenamiento local */ }
  };
  const hora = (iso) => (iso ? new Date(iso).toLocaleTimeString() : "—");
  const numero = (v) => (v === "" || v === null || v === undefined ? null : Number(v));

  Alpine.data("monitor", () => ({
    TEXTOS, COLORES,
    conexiones: [], puedeConfigurar: false, conexionId: null, cargado: false,
    actual: null, error: null,
    rango: "1h", seleccion: ["cpu", "memoria", "sesiones"], serie: null, errorSerie: null,
    costosas: [], errorCostosas: null,
    edicion: null,

    async iniciar() {
      try {
        const r = await api("GET", "/api/monitor");
        this.conexiones = r.conexiones;
        this.puedeConfigurar = r.puede_configurar;
      } catch (error) {
        this.error = error.message;
      }
      this.cargado = true;
      if (!this.conexiones.length) return;
      const recordada = leer(CLAVE_CONEXION);
      this.conexionId = this.conexiones.some((c) => c.id === recordada) ? recordada : this.conexiones[0].id;
      await this.refrescarTodo();
      // Con la pestaña oculta no se consulta: se retoma al volver.
      setInterval(() => !document.hidden && this.refrescarActual(), REFRESCO_MS);
      setInterval(() => !document.hidden && this.refrescarSerie(), REFRESCO_SERIE_MS);
      setInterval(() => !document.hidden && this.refrescarCostosas(), REFRESCO_COSTOSAS_MS);
    },

    async cambiarConexion() {
      recordar(CLAVE_CONEXION, this.conexionId);
      this.actual = null;
      this.serie = null;
      this.costosas = [];
      await this.refrescarTodo();
    },

    async refrescarTodo() {
      await Promise.all([this.refrescarActual(), this.refrescarSerie(), this.refrescarCostosas()]);
    },

    async refrescarActual() {
      try {
        this.actual = await api("GET", `/api/monitor/${this.conexionId}`);
        this.error = null;
      } catch (error) {
        this.error = error.message;
      }
    },

    async refrescarSerie() {
      try {
        const metricas = this.seleccion.join(",") || "cpu";
        this.serie = await api("GET", `/api/monitor/${this.conexionId}/serie?rango=${this.rango}&metricas=${metricas}`);
        this.errorSerie = null;
      } catch (error) {
        this.errorSerie = error.message;
      }
    },

    async refrescarCostosas() {
      try {
        this.costosas = (await api("GET", `/api/monitor/${this.conexionId}/consultas-costosas`)).consultas;
        this.errorCostosas = null;
      } catch (error) {
        this.errorCostosas = error.message;
      }
    },

    // --- Lectura actual -------------------------------------------------------------

    umbral(metrica) {
      return (this.actual?.umbrales || []).find((u) => u.metrica === metrica) || { habilitada: true };
    },
    habilitada(metrica) {
      return this.umbral(metrica).habilitada;
    },
    valor(metrica) {
      return this.actual?.valores?.[metrica] ?? null;
    },
    unidad(metrica) {
      return this.actual?.unidades?.[metrica] ?? "";
    },
    formato(v) {
      if (v === null || v === undefined) return "—";
      return Number.isInteger(v) ? v.toLocaleString() : v.toLocaleString(undefined, { maximumFractionDigits: 1 });
    },

    color(metrica, porDefecto) {
      const v = this.valor(metrica);
      const u = this.umbral(metrica);
      if (v === null) return "var(--fg-faint)";
      if (u.critical !== null && u.critical !== undefined && v >= u.critical) return "var(--terracotta)";
      if (u.warning !== null && u.warning !== undefined && v >= u.warning) return "var(--mustard)";
      return u.warning !== null && u.warning !== undefined ? "var(--sage)" : porDefecto;
    },
    colorContador(metrica) {
      return this.color(metrica, "var(--fg)");
    },

    arco,
    marcas: () => MARCAS,

    medidor(metrica) {
      const v = this.valor(metrica);
      const u = this.umbral(metrica);
      // Los porcentajes van de 0 a 100; el resto se escala para que el umbral critical
      // quede al 90% del recorrido.
      const maximo = PORCENTAJES.includes(metrica)
        ? 100
        : Math.max(u.critical ? u.critical / 0.9 : 0, (v || 0) * 1.1, 1);
      const angulo = (x) => -180 + Math.min(Math.max(x / maximo, 0), 1) * 180;
      return {
        color: this.color(metrica, "var(--accent)"),
        relleno: v === null ? "" : arco(-180, angulo(v)),
        zonaWarning: u.warning != null ? arco(angulo(u.warning), angulo(u.critical)) : "",
        zonaCritical: u.critical != null ? arco(angulo(u.critical), 0) : "",
        aguja: punto(angulo(v || 0), R - 18),
      };
    },

    textoEstado() {
      if (!this.actual || this.actual.estado === "sin_datos") return TEXTOS.sinLecturas;
      const prefijo = this.actual.estado === "ok" ? TEXTOS.enVivo : TEXTOS.sinConexion;
      return `${prefijo} ${hora(this.actual.ultima_lectura)}`;
    },
    textoObsoleto() {
      return TEXTOS.obsoleto.replace("{hora}", hora(this.actual?.ultima_lectura));
    },
    textoVolumenes() {
      return (this.actual?.volumenes || []).map((v) => `${v.volumen} ${v.uso}% · ${v.libre_gb} GB libres`).join(" — ");
    },

    // --- Histórico ------------------------------------------------------------------

    verHistorico(metrica) {
      this.seleccion = [metrica];
      this.refrescarSerie();
      this.$refs.historico.scrollIntoView({ behavior: "smooth" });
    },
    alternar(metrica) {
      this.seleccion = this.seleccion.includes(metrica)
        ? this.seleccion.filter((m) => m !== metrica)
        : [...this.seleccion, metrica];
      this.refrescarSerie();
    },
    cambiarRango(rango) {
      this.rango = rango;
      this.refrescarSerie();
    },
    textoRango() {
      return TEXTOS.rangos[this.rango];
    },
    tituloSerie() {
      return this.seleccion.map((m) => TEXTOS.metricas[m]).join(", ");
    },

    ejeY(v) {
      return H - P - (v / 100) * (H - P * 2);
    },
    grilla() {
      return [0, 25, 50, 75, 100].map((v) => `M${P},${this.ejeY(v)} L${W - P},${this.ejeY(v)}`).join(" ");
    },
    etiquetaX(i) {
      const segundos = { "1h": 3600, "24h": 86400, "7d": 604800, "30d": 2592000 }[this.rango] * (1 - i / 4);
      if (!segundos) return TEXTOS.ahora;
      if (segundos < 3600) return `-${Math.round(segundos / 60)}m`;
      if (segundos < 172800) return `-${Math.round(segundos / 3600)}h`;
      return `-${Math.round(segundos / 86400)}d`;
    },

    // Máximo de la escala de cada métrica: los porcentajes van sobre 0-100; el resto se
    // normaliza a su propio máximo en el rango (se informa debajo del gráfico).
    escala(metrica) {
      if (PORCENTAJES.includes(metrica)) return 100;
      const valores = (this.serie?.puntos || []).map((p) => p[metrica]).filter((v) => v !== null && v !== undefined);
      return Math.max(...valores, 1);
    },
    trazo(metrica) {
      if (!this.serie || !this.seleccion.includes(metrica)) return "";
      const desde = new Date(this.serie.desde).getTime();
      const duracion = Date.now() - desde;
      const maximo = this.escala(metrica);
      let d = "";
      let abierto = false;
      for (const p of this.serie.puntos) {
        const v = p[metrica];
        if (v === null || v === undefined) {
          abierto = false; // hueco: la instancia no respondió en ese tramo
          continue;
        }
        const x = P + ((new Date(p.ts).getTime() - desde) / duracion) * (W - P * 2);
        d += `${abierto ? "L" : "M"}${x.toFixed(1)},${this.ejeY((v / maximo) * 100).toFixed(1)} `;
        abierto = true;
      }
      return d;
    },
    textoEscalas() {
      const otras = this.seleccion.filter((m) => !PORCENTAJES.includes(m));
      if (!otras.length) return "";
      return TEXTOS.escalas + " " + otras.map((m) => `${TEXTOS.metricas[m]}: ${this.formato(this.escala(m))}`).join(" · ");
    },

    // --- Consultas costosas ---------------------------------------------------------

    anchoCostosa(c) {
      const maximo = Math.max(...this.costosas.map((x) => x.cpu_ms), 1);
      return Math.round((c.cpu_ms / maximo) * 100);
    },

    // --- Configuración de métricas (CU-000-003-B) -----------------------------------

    abrirUmbrales() {
      this.edicion = {
        umbrales: (this.actual?.umbrales || []).map((u) => ({ ...u })),
        mensaje: "",
        guardando: false,
      };
    },

    async guardarUmbrales() {
      const e = this.edicion;
      e.guardando = true;
      try {
        const umbrales = e.umbrales.map((u) => ({
          metrica: u.metrica, habilitada: u.habilitada, warning: numero(u.warning), critical: numero(u.critical),
        }));
        await api("PUT", `/api/monitor/${this.conexionId}/umbrales`, { umbrales });
        this.edicion = null;
        await this.refrescarActual();
      } catch (error) {
        e.mensaje = error.message;
        e.guardando = false;
      }
    },
  }));
});
