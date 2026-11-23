// Componentes Alpine de Soporte: servicios y colas, e historia de la base de la aplicación.
// Las bitácoras usan el componente común (bitacoras.js).
document.addEventListener("alpine:init", () => {
  const TEXTOS = window.TEXTOS_SOPORTE;
  const api = window.DBA.api;
  const PESTANAS = ["servicios", "bitacoras", "historia"];
  const ACTUAL = "actual";
  const REFRESCO_MS = 10000;
  const FIN_DE_LOS_TIEMPOS = 9999;

  const fecha = (iso) => window.DBA.fecha(iso);
  const numero = (n) => window.DBA.numero(n ?? 0, 0);
  // Valor para un campo datetime-local: la hora local, sin zona.
  const ahoraLocal = () => {
    const ahora = new Date();
    return new Date(ahora.getTime() - ahora.getTimezoneOffset() * 60000).toISOString().slice(0, 19);
  };

  Alpine.data("soporte", () => ({
    pestana: "servicios",
    visitadas: { servicios: true },

    iniciar() {
      const pedida = new URLSearchParams(location.search).get("pestana");
      this.elegir(PESTANAS.includes(pedida) ? pedida : "servicios");
    },

    elegir(pestana) {
      this.pestana = pestana;
      this.visitadas[pestana] = true;
      const url = new URL(location.href);
      url.searchParams.set("pestana", pestana);
      history.replaceState(null, "", url);
    },
  }));

  Alpine.data("soporteServicios", () => ({
    TEXTOS,
    servicios: [], colas: [], procesos: [], cargado: false, temporizador: null,

    async cargar() {
      await this.refrescar();
      this.temporizador = setInterval(() => this.pestana === "servicios" && this.refrescar(), REFRESCO_MS);
    },

    destroy() {
      clearInterval(this.temporizador);
    },

    async refrescar() {
      try {
        const [servicios, procesos] = await Promise.all([
          api("GET", "/api/soporte/servicios"),
          api("GET", "/api/soporte/procesos"),
        ]);
        this.servicios = servicios.servicios;
        this.colas = procesos.colas;
        this.procesos = procesos.procesos;
      } catch {
        this.servicios = [];
      }
      this.cargado = true;
    },

    color(estado) {
      return { online: "var(--sage)", warning: "var(--mustard)" }[estado] || "var(--terracotta)";
    },
    claseEstado(estado) {
      return { online: "tag-green", warning: "tag-yellow" }[estado] || "tag-red";
    },
    claseProceso(estado) {
      return { activo: "tag-green", en_espera: "tag-yellow", desactivado: "", sin_datos: "" }[estado] ?? "tag-red";
    },
    colorProceso(estado) {
      return { activo: "var(--sage)", en_espera: "var(--mustard)", desactivado: "var(--fg-faint)", sin_datos: "var(--fg-faint)" }[estado] || "var(--terracotta)";
    },
    detalleProceso(p) {
      const partes = [];
      if (p.ultimo) partes.push(TEXTOS.ultimoLatido.replace("{fecha}", fecha(p.ultimo)));
      if (p.intervalo_segundos) partes.push(TEXTOS.cadaSegundos.replace("{n}", p.intervalo_segundos));
      if (p.conexiones !== undefined) partes.push(TEXTOS.conexiones.replace("{n}", p.conexiones));
      return partes.join(" · ") || "—";
    },
    fecha,
    numero,
  }));

  Alpine.data("historiaBase", () => ({
    TEXTOS,
    COLORES: { agregado: "var(--sage)", eliminado: "var(--terracotta)", modificado: "var(--mustard)" },
    SIGNOS: { agregado: "+", eliminado: "−", modificado: "~" },
    vista: "estructura", base: "", versiones: [], detalle: null, comparando: false, elegidas: [],
    abierto: null, capturando: false, aviso: "", error: null, cargado: false,
    ddl: { registros: [], total: 0, pagina: 1, paginas: 1, texto: "", cargado: false },
    ddlDetalle: null,
    datos: { tabla: "usuario", modo: "momento", momento: "", clave: "", resultado: null, cargando: false, consultado: null },

    async iniciar() {
      this.datos.momento = ahoraLocal();
      await this.cargarVersiones();
    },

    async cambiarVista(vista) {
      this.vista = vista;
      this.error = null;
      if (vista === "ddl" && !this.ddl.cargado) await this.cargarDdl(1);
    },

    // --- Estructura ---------------------------------------------------------------
    async cargarVersiones() {
      try {
        const r = await api("GET", "/api/soporte/estructura");
        this.base = r.base;
        this.versiones = r.versiones;
        if (this.versiones.length && !this.comparando) await this.elegir(this.versiones[0]);
      } catch (error) {
        this.error = window.DBA.aviso(error);
      }
      this.cargado = true;
    },

    async capturar() {
      this.capturando = true;
      this.aviso = "";
      try {
        const r = await api("POST", "/api/soporte/estructura");
        this.aviso = r.version ? TEXTOS.capturada.replace("{version}", r.version) : TEXTOS.sinCambios;
        await this.cargarVersiones();
      } catch (error) {
        this.error = window.DBA.aviso(error);
      }
      this.capturando = false;
    },

    subtituloVersion(v) {
      const partes = [fecha(v.ts)];
      if (v.migraciones.length) partes.push(TEXTOS.migraciones.replace("{lista}", v.migraciones.join(", ")));
      if (v.usuario) partes.push(v.usuario);
      return partes.join(" · ");
    },

    estaElegida(v) {
      return this.comparando ? this.elegidas.includes(v.version) : this.detalle?.version === v.version;
    },

    alternarComparacion() {
      this.comparando = !this.comparando;
      this.elegidas = [];
      if (!this.comparando && this.versiones.length) this.elegir(this.versiones[0]);
    },

    async elegir(v) {
      this.abierto = null;
      if (this.comparando) return this.elegirParaComparar(v);
      try {
        const r = await api("GET", `/api/soporte/estructura/${v.version}`);
        this.detalle = {
          version: r.version,
          eyebrow: `${TEXTOS.diffVersion.replace("{version}", r.version)} · ${fecha(r.ts)}`,
          titulo: TEXTOS.origenes[r.origen] || r.origen,
          inicial: r.inicial,
          cambios: r.detalle,
          script: `/api/soporte/estructura/${r.version}/script`,
        };
      } catch (error) {
        this.error = window.DBA.aviso(error);
      }
    },

    // Con una versión elegida se compara contra la estructura actual; con dos, entre ellas.
    async elegirParaComparar(v) {
      this.elegidas = this.elegidas.includes(v.version)
        ? this.elegidas.filter((n) => n !== v.version)
        : [...this.elegidas, v.version].slice(-2);
      if (!this.elegidas.length) return;
      const [desde, hasta] = this.elegidas.length === 2
        ? [Math.min(...this.elegidas), Math.max(...this.elegidas)]
        : [this.elegidas[0], ACTUAL];
      try {
        const r = await api("GET", `/api/soporte/estructura/comparar?${new URLSearchParams({ desde, hasta })}`);
        this.detalle = {
          version: null,
          eyebrow: TEXTOS.diffComparacion.replace("{desde}", desde).replace("{hasta}", hasta === ACTUAL ? TEXTOS.estadoActual : `v.${hasta}`),
          titulo: TEXTOS.tituloComparacion,
          inicial: false,
          cambios: r.cambios,
          script: null,
        };
      } catch (error) {
        this.error = window.DBA.aviso(error);
      }
    },

    // --- Cambios de estructura (trigger DDL) ----------------------------------------
    async cargarDdl(pagina) {
      const params = new URLSearchParams({ pagina });
      if (this.ddl.texto.trim()) params.set("texto", this.ddl.texto.trim());
      try {
        const r = await api("GET", `/api/soporte/ddl?${params}`);
        Object.assign(this.ddl, { registros: r.registros, total: r.total, pagina: r.pagina, paginas: r.paginas });
        this.error = null;
      } catch (error) {
        this.error = window.DBA.aviso(error);
      }
      this.ddl.cargado = true;
    },

    async verDdl(r) {
      try {
        this.ddlDetalle = await api("GET", `/api/soporte/ddl/${r.id}`);
      } catch (error) {
        this.error = window.DBA.aviso(error);
      }
    },

    // --- Datos (tablas temporales) ------------------------------------------------
    async consultarDatos() {
      const params = new URLSearchParams({ modo: this.datos.modo });
      if (this.datos.modo === "momento") {
        // El campo está en la hora local del navegador; el servidor recibe UTC. Sin fecha,
        // el estado actual.
        const momento = this.datos.momento ? new Date(this.datos.momento) : new Date();
        params.set("momento", momento.toISOString());
      }
      if (this.datos.clave.trim()) params.set("clave", this.datos.clave.trim());
      this.datos.cargando = true;
      try {
        this.datos.resultado = await api("GET", `/api/soporte/datos/${this.datos.tabla}?${params}`);
        this.datos.consultado = this.datos.modo === "momento" ? params.get("momento") : null;
        this.error = null;
      } catch (error) {
        this.error = window.DBA.aviso(error);
      }
      this.datos.cargando = false;
    },

    resumenDatos() {
      const r = this.datos.resultado;
      let texto = this.datos.consultado
        ? TEXTOS.resumenMomento.replace("{n}", numero(r.filas.length)).replace("{fecha}", fecha(this.datos.consultado))
        : TEXTOS.resumenCambios.replace("{n}", numero(r.filas.length));
      if (r.truncado) texto += ` · ${TEXTOS.truncado.replace("{n}", numero(r.limite))}`;
      return texto;
    },

    // La columna "válido hasta" de la fila vigente es el fin de los tiempos (año 9999).
    esVigente(fila) {
      const hasta = fila[this.datos.resultado.columnas.indexOf("valido_hasta")];
      return typeof hasta === "string" && new Date(hasta).getUTCFullYear() === FIN_DE_LOS_TIEMPOS;
    },

    celda(valor, indice) {
      const columna = this.datos.resultado.columnas[indice];
      if (columna === "valido_hasta" && typeof valor === "string" && new Date(valor).getUTCFullYear() === FIN_DE_LOS_TIEMPOS) return TEXTOS.vigente;
      if (columna === "valido_desde" || columna === "valido_hasta") return fecha(valor);
      if (valor === null || valor === undefined) return "—";
      if (typeof valor === "boolean") return valor ? "1" : "0";
      return String(valor);
    },

    fecha,
    numero,
  }));
});
