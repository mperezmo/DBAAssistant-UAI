// Componente Alpine de Auditoría (CU-000-009 A, B, C y D).
document.addEventListener("alpine:init", () => {
  const TEXTOS = window.TEXTOS_AUDITORIA;
  const api = window.DBA.api;
  const VACIOS = { desde: "", hasta: "", usuario: "", ambiente: "", tipo: "", texto: "" };

  Alpine.data("auditoria", () => ({
    TEXTOS,
    bitacoras: [], totales: {}, actual: "", filtros: { ...VACIOS }, registros: [], total: 0,
    pagina: 1, paginas: 1, nivel: "WARNING", puedeConfigurarNivel: false, detalle: null,
    error: null, cargado: false, exportando: false,

    async iniciar() {
      try {
        const r = await api("GET", "/api/auditoria");
        this.bitacoras = r.bitacoras;
        this.totales = r.totales;
        this.nivel = r.nivel_excepciones;
        this.puedeConfigurarNivel = r.puede_configurar_nivel;
        const pedida = new URLSearchParams(location.search).get("bitacora");
        await this.elegir(this.bitacoras.includes(pedida) ? pedida : this.bitacoras[0]);
      } catch (error) {
        this.error = error.message;
      }
    },

    async elegir(nombre) {
      this.actual = nombre;
      this.filtros = { ...VACIOS };
      await this.irA(1);
    },

    consulta() {
      const params = new URLSearchParams();
      for (const [clave, valor] of Object.entries(this.filtros)) {
        if (valor && String(valor).trim()) params.set(clave, String(valor).trim());
      }
      return params;
    },

    async buscar() {
      await this.irA(1);
    },

    async irA(pagina) {
      const params = this.consulta();
      params.set("pagina", pagina);
      try {
        const r = await api("GET", `/api/auditoria/${this.actual}?${params}`);
        Object.assign(this, { registros: r.registros, total: r.total, pagina: r.pagina, paginas: r.paginas });
        this.error = null;
      } catch (error) {
        this.error = error.message;
      }
      this.cargado = true;
    },

    async ver(r) {
      try {
        this.detalle = await api("GET", `/api/auditoria/${this.actual}/registros/${r.id}`);
      } catch (error) {
        this.error = error.message;
      }
    },

    // La descarga se pide por fetch para poder mostrar el error de límite (EXP_LIM_000).
    async exportar(formato) {
      this.exportando = true;
      this.error = null;
      const params = this.consulta();
      params.set("formato", formato);
      try {
        const respuesta = await fetch(`/api/auditoria/${this.actual}/exportar?${params}`);
        if (!respuesta.ok) {
          const datos = await respuesta.json();
          this.error = datos.codigo === "EXP_LIM_000"
            ? TEXTOS.limite.replace("{registros}", datos.datos.registros.toLocaleString()).replace("{limite}", datos.datos.limite.toLocaleString())
            : `${datos.mensaje} (${datos.codigo})`;
        } else {
          const nombre = /filename="([^"]+)"/.exec(respuesta.headers.get("Content-Disposition") || "")?.[1] || `auditoria.${formato}`;
          const enlace = document.createElement("a");
          enlace.href = URL.createObjectURL(await respuesta.blob());
          enlace.download = nombre;
          enlace.click();
          URL.revokeObjectURL(enlace.href);
        }
      } catch (error) {
        this.error = error.message;
      }
      this.exportando = false;
    },

    async cambiarNivel() {
      try {
        this.nivel = (await api("PUT", "/api/auditoria/excepciones/nivel", { nivel: this.nivel })).nivel;
      } catch (error) {
        this.error = error.message;
      }
    },

    fecha(iso) {
      return iso ? new Date(iso).toLocaleString() : "—";
    },
    textoFiltros() {
      const activos = Object.entries(this.filtros).filter(([, v]) => v && String(v).trim()).map(([, v]) => v);
      return activos.length ? `${TEXTOS.filtrosAplicados} · ${activos.join(" · ")}` : TEXTOS.sinFiltros;
    },
    textoPaginacion() {
      return TEXTOS.paginacion.replace("{n}", this.registros.length).replace("{total}", this.total.toLocaleString())
        .replace("{pagina}", this.pagina).replace("{paginas}", this.paginas);
    },
    colorResultado(resultado) {
      return ["fallida", "revertida", "error"].includes(resultado) ? "var(--terracotta)" : "var(--sage)";
    },
    colorNivel(nivel) {
      return { CRITICAL: "var(--terracotta)", ERROR: "var(--terracotta)", WARNING: "var(--mustard)" }[nivel] || "var(--fg-muted)";
    },
  }));
});
