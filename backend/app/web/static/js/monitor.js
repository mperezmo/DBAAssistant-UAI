// Componente Alpine del panel de monitoreo (CU-000-003 A, B y C).
document.addEventListener("alpine:init", () => {
  const TEXTOS = window.TEXTOS_MONITOR;
  const api = window.DBA.api;
  const CLAVE_CONEXION = "dba.monitor.conexion";
  const REFRESCO_MS = 3000;
  const REFRESCO_SERIE_MS = 15000;
  const REFRESCO_COSTOSAS_MS = 30000;
  // Query Store se lee en vivo con caché de 5 minutos en el servidor.
  const REFRESCO_QUERY_STORE_MS = 300000;
  const CLAVE_PRECARGA_SANDBOX = "dba.sandbox.precarga";
  const COLORES_PLANES = ["var(--accent)", "var(--terracotta)", "var(--sage)", "var(--mustard)", "var(--ink-500)"];
  const GRAVEDAD = { critical: 3, warning: 2, ok: 1 };
  const PORCENTAJES = ["cpu", "memoria", "disco"];

  // Geometría del medidor semicircular y del gráfico (mismas medidas que el diseño).
  const CX = 100, CY = 100;
  const W = 1100, H = 240, P = 30;

  // Series del gráfico: colores de esmalte distinguibles entre sí.
  const COLORES = {
    cpu: "var(--color-primary)", memoria: "var(--color-danger)", disco: "var(--color-ok)",
    latencia: "var(--color-info)", sesiones: "var(--color-warn-fill)", bloqueos: "var(--gray-700)",
    deadlocks: "#7A3E8E", conexiones_seg: "var(--gray-400)",
  };

  // Instrumento: semicírculo de radio 80 con centro en (100, 100). Marcas cada 10 % por fuera
  // del arco; las de 0, 50 y 100 % son más largas.
  const RADIO_ARCO = 80, LARGO_AGUJA = 62;
  const sobreArco = (porcentaje, radio) => {
    const a = Math.PI * (1 - porcentaje / 100);
    return [CX + radio * Math.cos(a), CY - radio * Math.sin(a)];
  };
  const MARCAS = Array.from({ length: 11 }, (_, i) => {
    const [x1, y1] = sobreArco(i * 10, RADIO_ARCO + 11);
    const [x2, y2] = sobreArco(i * 10, RADIO_ARCO + (i % 5 === 0 ? 20 : 16));
    return `M${x1.toFixed(1)},${y1.toFixed(1)} L${x2.toFixed(1)},${y2.toFixed(1)}`;
  }).join(" ");
  const COLOR_ESTADO = { critical: "var(--color-danger)", warning: "var(--color-warn-fill)", ok: "var(--color-ok)" };

  const leer = (clave) => {
    try { return localStorage.getItem(clave); } catch { return null; }
  };
  const recordar = (clave, valor) => {
    try { localStorage.setItem(clave, valor); } catch { /* sin almacenamiento local */ }
  };
  const hora = (iso) => window.DBA.horaSegundos(iso);
  const numero = (v) => (v === "" || v === null || v === undefined ? null : Number(v));

  Alpine.data("monitor", () => ({
    TEXTOS, COLORES,
    conexiones: [], puedeConfigurar: false, conexionId: null, cargado: false,
    actual: null, error: null,
    rango: "1h", seleccion: ["cpu", "memoria", "sesiones"], serie: null, errorSerie: null,
    costosas: [], errorCostosas: null,
    edicion: null, COLORES_PLANES,
    detalleBackup: null, detalleJob: null, detalleConsulta: null,
    qs: { estado: null, base: "", metrica: "cpu", periodo: "24h", datos: null, cargando: false, error: null },

    async iniciar() {
      try {
        const r = await api("GET", "/api/monitor");
        this.conexiones = r.conexiones;
        this.puedeConfigurar = r.puede_configurar;
      } catch (error) {
        this.error = window.DBA.aviso(error);
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
      setInterval(() => !document.hidden && this.cargarQueryStore(), REFRESCO_QUERY_STORE_MS);
      // Tras ejecutar un workaround (por ejemplo, activar Query Store) se relee el estado.
      window.addEventListener("workaround-ejecutado", () => this.cargarEstadoQueryStore());
    },

    async cambiarConexion() {
      recordar(CLAVE_CONEXION, this.conexionId);
      this.actual = null;
      this.serie = null;
      this.costosas = [];
      this.qs = { ...this.qs, estado: null, base: "", datos: null, error: null };
      await this.refrescarTodo();
    },

    async refrescarTodo() {
      await Promise.all([
        this.refrescarActual(), this.refrescarSerie(), this.refrescarCostosas(), this.cargarEstadoQueryStore(),
      ]);
    },

    async refrescarActual() {
      try {
        this.actual = await api("GET", `/api/monitor/${this.conexionId}`);
        this.error = null;
      } catch (error) {
        this.error = window.DBA.aviso(error);
      }
    },

    async refrescarSerie() {
      try {
        const metricas = this.seleccion.join(",") || "cpu";
        this.serie = await api("GET", `/api/monitor/${this.conexionId}/serie?rango=${this.rango}&metricas=${metricas}`);
        this.errorSerie = null;
      } catch (error) {
        this.errorSerie = window.DBA.aviso(error);
      }
    },

    async refrescarCostosas() {
      try {
        this.costosas = (await api("GET", `/api/monitor/${this.conexionId}/consultas-costosas`)).consultas;
        this.errorCostosas = null;
      } catch (error) {
        this.errorCostosas = window.DBA.aviso(error);
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
      return window.DBA.numero(v, 1);
    },

    color(metrica, porDefecto) {
      const v = this.valor(metrica);
      const u = this.umbral(metrica);
      if (v === null) return "var(--fg-faint)";
      if (u.critical !== null && u.critical !== undefined && v >= u.critical) return "var(--color-danger)";
      if (u.warning !== null && u.warning !== undefined && v >= u.warning) return "var(--color-warn)";
      return porDefecto;
    },
    colorContador(metrica) {
      return this.color(metrica, "var(--fg)");
    },

    marcas: () => MARCAS,

    // Estado del instrumento según los umbrales de la conexión.
    estadoMedidor(metrica) {
      const v = this.valor(metrica);
      const u = this.umbral(metrica);
      if (v === null) return null;
      if (u.critical !== null && u.critical !== undefined && v >= u.critical) return "critical";
      if (u.warning !== null && u.warning !== undefined && v >= u.warning) return "warning";
      return "ok";
    },
    claseEstadoMedidor(metrica) {
      return { critical: "tag-red", warning: "tag-yellow", ok: "tag-green" }[this.estadoMedidor(metrica)] || "";
    },
    textoEstadoMedidor(metrica) {
      return TEXTOS.estadosMedidor[this.estadoMedidor(metrica) || "sin_datos"];
    },
    colorMedidor(metrica) {
      return COLOR_ESTADO[this.estadoMedidor(metrica)] || "var(--color-primary)";
    },
    // Los porcentajes van de 0 a 100; el resto se escala para que el umbral crítico quede
    // al 90 % del recorrido (o a 100 si no hay umbral).
    escalaMedidor(metrica) {
      if (PORCENTAJES.includes(metrica)) return 100;
      const u = this.umbral(metrica);
      return Math.max(u.critical ? u.critical / 0.9 : 100, (this.valor(metrica) || 0) * 1.1);
    },
    porcentaje(metrica) {
      const v = this.valor(metrica) || 0;
      return Math.min(Math.max((v / this.escalaMedidor(metrica)) * 100, 0), 100);
    },
    aguja(metrica) {
      return sobreArco(this.porcentaje(metrica), LARGO_AGUJA);
    },
    detalleMedidor(metrica) {
      if (metrica === "disco") return this.textoVolumenes();
      if (PORCENTAJES.includes(metrica)) {
        const u = this.umbral(metrica);
        return u.warning != null ? TEXTOS.umbralMedidor.replace("{valor}", window.DBA.numero(u.warning, 1)) : "";
      }
      return TEXTOS.escalaMedidor.replace("{maximo}", window.DBA.numero(this.escalaMedidor(metrica), 0)).replace("{unidad}", this.unidad(metrica));
    },

    textoEstado() {
      if (!this.actual || this.actual.estado === "sin_datos") return TEXTOS.sinLecturas;
      const prefijo = this.actual.estado === "ok" ? TEXTOS.enVivo : TEXTOS.sinConexion;
      return `${prefijo} · ${hora(this.actual.ultima_lectura)}`;
    },
    textoObsoleto() {
      return TEXTOS.obsoleto.replace("{hora}", hora(this.actual?.ultima_lectura));
    },
    // Un volumen: "55,4 GB libres"; varios: "/datos: 78 % usado · 55,4 GB libres — ...".
    textoVolumenes() {
      const volumenes = this.actual?.volumenes || [];
      if (volumenes.length === 1) return TEXTOS.discoLibre.replace("{libre}", window.DBA.numero(volumenes[0].libre_gb, 1));
      return volumenes.map((v) => TEXTOS.discoVolumen.replace("{volumen}", v.volumen)
        .replace("{uso}", window.DBA.numero(v.uso, 1)).replace("{libre}", window.DBA.numero(v.libre_gb, 1))).join(" — ");
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

    // --- Backups ------------------------------------------------------------------

    duracion(minutos) {
      const m = Math.max(0, Math.round(minutos || 0));
      if (m < 60) return `${m} min`;
      const horas = Math.floor(m / 60);
      if (horas < 48) return m % 60 ? `${horas} h ${m % 60} min` : `${horas} h`;
      return horas % 24 ? `${Math.floor(horas / 24)} d ${horas % 24} h` : `${Math.floor(horas / 24)} d`;
    },
    hace(minutos) {
      return minutos === null || minutos === undefined ? TEXTOS.nunca : TEXTOS.hace.replace("{tiempo}", this.duracion(minutos));
    },
    textoPolitica() {
      const p = this.actual?.politica_backup;
      if (!p) return "";
      if (!p.habilitada) return TEXTOS.politicaApagada;
      return TEXTOS.politica.replace("{full}", this.duracion(p.full_horas * 60)).replace("{log}", this.duracion(p.log_minutos));
    },
    peorBackup(b) {
      const estados = [b.estado.full, b.estado.log].filter(Boolean);
      if (!estados.length) return "nulo";
      return estados.sort((x, y) => GRAVEDAD[y] - GRAVEDAD[x])[0];
    },
    backupsOrdenados() {
      return [...(this.actual?.backups || [])].sort(
        (a, b) => (GRAVEDAD[this.peorBackup(b)] || 0) - (GRAVEDAD[this.peorBackup(a)] || 0) || a.base.localeCompare(b.base));
    },
    resumenBackups() {
      const peores = (this.actual?.backups || []).map((b) => this.peorBackup(b));
      if (peores.includes("critical")) return "critical";
      return peores.includes("warning") ? "warning" : "ok";
    },
    textoResumenBackups() {
      const conProblemas = (this.actual?.backups || []).filter((b) => ["warning", "critical"].includes(this.peorBackup(b))).length;
      return conProblemas ? TEXTOS.backupsMal.replace("{n}", conProblemas) : TEXTOS.backupsOk;
    },
    claseResumen(estado) {
      return { critical: "tag-red", warning: "tag-yellow", ok: "tag-green" }[estado] || "";
    },
    claseTexto(estado) {
      return { critical: "text-danger", warning: "text-warn" }[estado] || "";
    },
    backupsProgramados() {
      return (this.actual?.jobs || []).filter((j) => j.backup_programado);
    },

    async verBackups(b) {
      this.detalleBackup = { base: b, filas: null, cargando: true, error: null };
      try {
        const params = new URLSearchParams({ base: b.base });
        this.detalleBackup.filas = (await api("GET", `/api/monitor/${this.conexionId}/backups/historial?${params}`)).backups;
      } catch (error) {
        this.detalleBackup.error = window.DBA.aviso(error);
      }
      this.detalleBackup.cargando = false;
    },

    // Abre el modal compartido de ejecución de workarounds sobre esta instancia.
    ejecutarWorkaround(workaround, extra = {}) {
      this.detalleBackup = null;
      this.detalleJob = null;
      window.dispatchEvent(new CustomEvent("ejecutar-workaround", {
        detail: { workaround, conexion_id: this.conexionId, base: "", ...extra },
      }));
    },

    // --- Jobs del SQL Server Agent ----------------------------------------------------

    textoAgente() {
      const a = this.actual?.agente;
      return TEXTOS.agente.replace("{estado}", a ? a.estado : "—");
    },
    jobsConFalla() {
      return (this.actual?.jobs || []).filter((j) => ["falla", "colgado"].includes(j.estado));
    },
    jobsOrdenados() {
      const orden = { colgado: 0, falla: 1, en_curso: 2, ok: 3, sin_ejecuciones: 4, deshabilitado: 5 };
      return [...(this.actual?.jobs || [])].sort((a, b) => orden[a.estado] - orden[b.estado] || a.nombre.localeCompare(b.nombre));
    },
    claseJob(j) {
      return { falla: "tag-red", colgado: "tag-red", en_curso: "tag-blue", ok: "tag-green" }[j.estado] || "";
    },

    async verJob(j) {
      this.detalleJob = { job: j, ejecuciones: null, cargando: true, error: null, explicacion: null, explicando: false };
      try {
        this.detalleJob.ejecuciones = (await api("GET", `/api/monitor/${this.conexionId}/jobs/${j.id}`)).ejecuciones;
      } catch (error) {
        this.detalleJob.error = window.DBA.aviso(error);
      }
      this.detalleJob.cargando = false;
    },
    ultimaFalla() {
      return (this.detalleJob?.ejecuciones || []).some((e) => e.resultado === "falla");
    },
    async explicarJob() {
      const d = this.detalleJob;
      d.explicando = true;
      d.error = null;
      try {
        d.explicacion = await api("POST", `/api/monitor/${this.conexionId}/jobs/${d.job.id}/explicar`);
      } catch (error) {
        d.error = window.DBA.aviso(error);
      }
      d.explicando = false;
    },

    // --- Consultas costosas (Query Store) -----------------------------------------------

    async cargarEstadoQueryStore() {
      try {
        this.qs.estado = await api("GET", `/api/monitor/${this.conexionId}/query-store`);
        const bases = this.qs.estado.bases || [];
        if (!bases.some((b) => b.base === this.qs.base)) {
          // Primero una base con Query Store activo.
          const activa = bases.find((b) => ["READ_WRITE", "READ_ONLY"].includes(b.estado));
          this.qs.base = (activa || bases[0])?.base || "";
        }
        await this.cargarQueryStore();
      } catch (error) {
        this.qs.error = window.DBA.aviso(error);
      }
    },
    async cargarQueryStore() {
      if (!this.qs.base) return;
      this.qs.cargando = true;
      try {
        const params = new URLSearchParams({ base: this.qs.base, metrica: this.qs.metrica, periodo: this.qs.periodo });
        this.qs.datos = await api("GET", `/api/monitor/${this.conexionId}/query-store/consultas?${params}`);
        this.qs.error = null;
      } catch (error) {
        this.qs.error = window.DBA.aviso(error);
      }
      this.qs.cargando = false;
    },
    totalConsulta(c) {
      const valor = { cpu: c.cpu_ms, duracion: c.duracion_ms, lecturas: c.lecturas, ejecuciones: c.ejecuciones }[this.qs.metrica];
      const unidad = ["cpu", "duracion"].includes(this.qs.metrica) ? " ms" : "";
      return `${this.formato(valor)}${unidad}`;
    },
    porEjecucion(c) {
      if (this.qs.metrica === "lecturas") return c.ejecuciones ? this.formato(Math.round(c.lecturas / c.ejecuciones)) : "—";
      const valor = this.qs.metrica === "cpu" ? c.cpu_ms_ejecucion : c.duracion_ms_ejecucion;
      return valor === null ? "—" : `${this.formato(valor)} ms`;
    },
    // Trazo de una serie en un SVG de 100 de ancho; ``referencia`` fija la escala común.
    miniSerie(puntos, alto = 24, referencia = null, escala = null) {
      const valores = (puntos || []).filter((p) => p.v !== null);
      if (!valores.length) return "";
      const todos = (referencia || puntos).map((p) => p.t);
      const ejeX = [...new Set(todos)].sort();
      const maximo = escala || Math.max(...valores.map((p) => p.v), 1e-9);
      const x = (t) => (ejeX.length > 1 ? (ejeX.indexOf(t) / (ejeX.length - 1)) * 100 : 50);
      const y = (v) => alto - 2 - (v / maximo) * (alto - 4);
      if (valores.length === 1) return `M${x(valores[0].t) - 2},${y(valores[0].v)} L${x(valores[0].t) + 2},${y(valores[0].v)}`;
      return valores.map((p, i) => `${i ? "L" : "M"}${x(p.t).toFixed(1)},${y(p.v).toFixed(1)}`).join(" ");
    },
    // Trazo de un plan en el gráfico del detalle, con la escala común a todos los planes.
    trazoPlan(indice) {
      const datos = this.detalleConsulta?.datos;
      const plan = datos?.planes?.[indice];
      if (!plan) return "";
      const maximo = Math.max(...datos.planes.flatMap((p) => p.serie.map((x) => x.v || 0)), 1e-9);
      return this.miniSerie(plan.serie, 40, datos.serie, maximo);
    },
    async verConsulta(c) {
      this.detalleConsulta = { consulta: c, datos: null, cargando: true, error: null };
      try {
        const params = new URLSearchParams({ base: this.qs.base, query_id: c.query_id, metrica: this.qs.metrica, periodo: this.qs.periodo });
        this.detalleConsulta.datos = await api("GET", `/api/monitor/${this.conexionId}/query-store/consulta?${params}`);
        this.detalleConsulta.consulta = { ...c, ...this.detalleConsulta.datos };
      } catch (error) {
        this.detalleConsulta.error = window.DBA.aviso(error);
      }
      this.detalleConsulta.cargando = false;
    },
    probarEnSandbox() {
      try {
        sessionStorage.setItem(CLAVE_PRECARGA_SANDBOX, JSON.stringify({
          conexion_id: this.conexionId, base: this.qs.base, sql: this.detalleConsulta.consulta.texto,
        }));
      } catch { /* sin almacenamiento de sesión */ }
      location.href = "/sandbox";
    },

    // --- Configuración de métricas (CU-000-003-B) -----------------------------------

    abrirUmbrales() {
      this.edicion = {
        umbrales: (this.actual?.umbrales || []).map((u) => ({ ...u })),
        politica: { ...(this.actual?.politica_backup || { habilitada: true, full_horas: 24, log_minutos: 60 }) },
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
        await api("PUT", `/api/monitor/${this.conexionId}/backups/politica`, e.politica);
        this.edicion = null;
        await this.refrescarActual();
      } catch (error) {
        e.mensaje = window.DBA.aviso(error);
        e.guardando = false;
      }
    },
  }));
});
