// Componente Alpine del Histórico de Schema (CU-000-008 A y B).
document.addEventListener("alpine:init", () => {
  const TEXTOS = window.TEXTOS_SCHEMA;
  const api = window.DBA.api;
  const CLAVE_CONEXION = "dba.schema.conexion";
  const ACTUAL = "actual";

  const leer = () => {
    try { return localStorage.getItem(CLAVE_CONEXION); } catch { return null; }
  };
  const recordar = (valor) => {
    try { localStorage.setItem(CLAVE_CONEXION, valor); } catch { /* sin almacenamiento local */ }
  };

  Alpine.data("historicoSchema", () => ({
    TEXTOS,
    COLORES: { agregado: "var(--sage)", eliminado: "var(--terracotta)", modificado: "var(--mustard)" },
    SIGNOS: { agregado: "+", eliminado: "−", modificado: "~" },
    conexiones: [], conexionId: "", bases: [], base: "", versiones: [], vista: null,
    comparando: false, elegidas: [], abierto: null, verDdl: false, error: null, cargado: false,

    async iniciar() {
      try {
        this.conexiones = (await api("GET", "/api/schema/conexiones")).conexiones;
      } catch (error) {
        this.error = error.message;
      }
      this.cargado = true;
      if (!this.conexiones.length) return;
      const recordada = leer();
      this.conexionId = this.conexiones.some((c) => c.id === recordada) ? recordada : this.conexiones[0].id;
      await this.cambioConexion();
    },

    async cambioConexion() {
      recordar(this.conexionId);
      try {
        this.bases = (await api("GET", `/api/schema/conexiones/${this.conexionId}/bases`)).bases;
        this.base = this.bases[0] || "";
        await this.cargarVersiones();
      } catch (error) {
        this.error = error.message;
      }
    },

    ruta() {
      return `/api/schema/conexiones/${this.conexionId}/bases/${encodeURIComponent(this.base)}`;
    },

    async cargarVersiones() {
      this.vista = null;
      this.comparando = false;
      this.elegidas = [];
      if (!this.base) return;
      try {
        this.versiones = (await api("GET", `${this.ruta()}/versiones`)).versiones;
        this.error = null;
        if (this.versiones.length) await this.elegir(this.versiones[0]);
      } catch (error) {
        this.error = error.message;
      }
    },

    estaElegida(v) {
      return this.comparando ? this.elegidas.includes(v.version) : this.vista?.version?.version === v.version;
    },

    alternarComparacion() {
      this.comparando = !this.comparando;
      this.elegidas = [];
      if (!this.comparando && this.versiones.length) this.elegir(this.versiones[0]);
    },

    async elegir(v) {
      this.abierto = null;
      this.verDdl = false;
      if (this.comparando) return this.elegirParaComparar(v);
      try {
        const version = await api("GET", `${this.ruta()}/versiones/${v.version}`);
        this.vista = {
          eyebrow: TEXTOS.diffVersion.replace("{version}", version.version),
          titulo: TEXTOS.tituloVersion.replace("{origen}", TEXTOS.origenes[version.origen] || version.origen),
          cambios: version.detalle,
          sinDiff: version.sin_diff,
          version,
          descarga: null,
        };
      } catch (error) {
        this.error = error.message;
      }
    },

    // Con una versión elegida se compara contra el estado actual; con dos, entre ellas.
    async elegirParaComparar(v) {
      this.elegidas = this.elegidas.includes(v.version)
        ? this.elegidas.filter((n) => n !== v.version)
        : [...this.elegidas, v.version].slice(-2);
      if (!this.elegidas.length) return;
      const [desde, hasta] = this.elegidas.length === 2
        ? [Math.min(...this.elegidas), Math.max(...this.elegidas)]
        : [this.elegidas[0], ACTUAL];
      try {
        const params = new URLSearchParams({ desde, hasta });
        const r = await api("GET", `${this.ruta()}/comparar?${params}`);
        const destino = hasta === ACTUAL ? TEXTOS.estadoActual : `v.${hasta}`;
        this.vista = {
          eyebrow: TEXTOS.diffComparacion.replace("{desde}", desde).replace("{hasta}", destino),
          titulo: TEXTOS.tituloComparacion,
          cambios: r.cambios,
          sinDiff: false,
          version: null,
          descarga: `${this.ruta()}/comparar.sql?${params}`,
        };
      } catch (error) {
        this.error = error.message;
      }
    },

    fecha(iso) {
      return iso ? new Date(iso).toLocaleString() : "—";
    },
    pieVersion() {
      const v = this.vista.version;
      let texto = TEXTOS.pie.replace("{usuario}", v.usuario).replace("{fecha}", this.fecha(v.ts));
      if (v.autorizado_por) texto += ` · ${TEXTOS.autorizo.replace("{usuario}", v.autorizado_por)}`;
      return texto;
    },
  }));
});
