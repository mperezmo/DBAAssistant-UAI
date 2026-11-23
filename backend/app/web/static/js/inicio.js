// Componente Alpine del panel principal (Inicio).
document.addEventListener("alpine:init", () => {
  const TEXTOS = window.TEXTOS_INICIO;
  const api = window.DBA.api;
  const CLAVE_AMBIENTE = "dba.inicio.ambiente";
  const REFRESCO_MS = 30000;
  const AMBIENTES = ["TODOS", "PROD", "TESTING", "DEV"];
  const CLASES_ESTADO = { critico: "danger", observaciones: "warn", operativo: "ok" };

  const leer = () => {
    try { return localStorage.getItem(CLAVE_AMBIENTE); } catch { return null; }
  };
  const recordar = (valor) => {
    try { localStorage.setItem(CLAVE_AMBIENTE, valor); } catch { /* sin almacenamiento local */ }
  };

  Alpine.data("inicio", () => ({
    TEXTOS, datos: null, error: null, ambiente: "TODOS",

    async iniciar() {
      const recordado = leer();
      if (AMBIENTES.includes(recordado)) this.ambiente = recordado;
      await this.cargar();
      setInterval(() => !document.hidden && this.cargar(), REFRESCO_MS);
    },

    async elegirAmbiente(ambiente) {
      this.ambiente = ambiente;
      recordar(ambiente);
      await this.cargar();
    },

    async cargar() {
      try {
        this.datos = await api("GET", `/api/inicio?ambiente=${this.ambiente}`);
        this.error = null;
      } catch (error) {
        this.error = window.DBA.aviso(error);
      }
    },
    reintentar() {
      return this.cargar();
    },

    numero(valor) {
      return window.DBA.numero(valor, 0);
    },

    encabezado() {
      const fecha = new Date().toLocaleDateString(window.DBA.locale, { weekday: "long", day: "numeric", month: "long", year: "numeric" });
      return `${TEXTOS.eyebrow} · ${fecha}`;
    },

    subtitulo() {
      if (!this.datos) return "";
      if (!this.datos.instancias.total) return TEXTOS.sinInstancias;
      const partes = [`${this.ambiente === "TODOS" ? TEXTOS.todos : this.ambiente}.`];
      const activas = this.datos.alertas.activas;
      partes.push(activas ? window.DBA.plural(activas, TEXTOS.alertaPendiente, TEXTOS.alertasPendientes) : TEXTOS.sinAlertas);
      if (this.datos.pendientes_promocion) {
        partes.push(TEXTOS.promociones.replace("{n}", this.datos.pendientes_promocion));
      }
      return partes.join(" ");
    },

    // Estado general: lo decide el backend a partir de las alertas abiertas.
    claseEstado() {
      return CLASES_ESTADO[this.datos?.estado.nivel] || "ok";
    },
    detalleEstado() {
      const { alertas, estado } = this.datos;
      if (estado.nivel === "critico") {
        const ambientes = new Intl.ListFormat(window.DBA.locale, { type: "conjunction" }).format(estado.ambientes_criticos);
        return window.DBA.plural(alertas.criticas, TEXTOS.detalleCritica, TEXTOS.detalleCriticas).replace("{ambientes}", ambientes);
      }
      if (estado.nivel === "observaciones") {
        return window.DBA.plural(alertas.advertencias, TEXTOS.detalleAdvertencia, TEXTOS.detalleAdvertencias);
      }
      return TEXTOS.detalleOperativo;
    },
    textoDisponibilidad() {
      const valor = this.datos?.salud.disponibilidad;
      return TEXTOS.disponibilidad.replace("{valor}", valor === null || valor === undefined ? "—" : window.DBA.numero(valor, 1));
    },

    detalleAlertas() {
      const { criticas, advertencias } = this.datos.alertas;
      if (!criticas && !advertencias) return TEXTOS.sinCriticas;
      return [
        criticas ? window.DBA.plural(criticas, TEXTOS.criticaUna, TEXTOS.criticas) : null,
        advertencias ? window.DBA.plural(advertencias, TEXTOS.advertenciaUna, TEXTOS.advertencias) : null,
      ].filter(Boolean).join(" · ");
    },

    // Indicadores de la última hora; los que piden atención llevan un badge con texto.
    indicadores() {
      const s = this.datos?.salud;
      const n = (v) => (s ? window.DBA.numero(v, 0) : "—");
      return [
        { clave: "online", texto: TEXTOS.indicadores.online, valor: s ? `${s.online} / ${s.total}` : "—", badge: s && s.online < s.total ? "critico" : null },
        { clave: "latencia", texto: TEXTOS.indicadores.latencia, valor: s?.latencia_p95 != null ? `${window.DBA.numero(s.latencia_p95, 0)} ms` : "—", badge: null },
        { clave: "sesiones", texto: TEXTOS.indicadores.sesiones, valor: n(s?.sesiones), badge: null },
        { clave: "bloqueos", texto: TEXTOS.indicadores.bloqueos, valor: n(s?.bloqueos_prolongados), badge: s?.bloqueos_prolongados ? "advertencia" : null },
        { clave: "backups", texto: TEXTOS.indicadores.backups, valor: n(s?.backups_atrasados), badge: s?.backups_atrasados ? "critico" : null },
        { clave: "jobs", texto: TEXTOS.indicadores.jobs, valor: n(s?.jobs_con_falla), badge: s?.jobs_con_falla ? "advertencia" : null },
      ];
    },
  }));
});
