// Componente Alpine de Buenas Prácticas (CU-000-007 A y B).
document.addEventListener("alpine:init", () => {
  const TEXTOS = window.TEXTOS_BP;
  const api = window.DBA.api;
  const CLAVE_CONEXION = "dba.bp.conexion";
  const REFRESCO_MS = 3000;
  const ORDEN = { alta: 0, media: 1, baja: 2 };

  const leer = () => {
    try { return localStorage.getItem(CLAVE_CONEXION); } catch { return null; }
  };
  const recordar = (valor) => {
    try { localStorage.setItem(CLAVE_CONEXION, valor); } catch { /* sin almacenamiento local */ }
  };

  Alpine.data("buenasPracticas", () => ({
    TEXTOS,
    conexiones: [], conexionId: "", bases: [], base: "", tiposElegidos: ["consultas", "indices", "fragmentacion", "esquema"],
    informes: [], informe: null, reglas: [], permisos: {}, textoRegla: "", analizando: false,
    validando: null, errores: {}, error: null, cargado: false, sondeo: null,

    async iniciar() {
      const params = new URLSearchParams(location.search);
      try {
        this.conexiones = (await api("GET", "/api/buenas-practicas/conexiones")).conexiones;
        await this.cargar();
      } catch (error) {
        this.error = error.message;
      }
      this.cargado = true;
      if (!this.conexiones.length) return;
      // Desde "Optimizar" del Monitoreo llegan la instancia y el tipo de análisis.
      const pedida = params.get("conexion") || leer();
      this.conexionId = this.conexiones.some((c) => c.id === pedida) ? pedida : this.conexiones[0].id;
      if (params.get("tipos")) this.tiposElegidos = params.get("tipos").split(",");
      await this.cargarBases();
      if (this.informes.length) await this.abrir(this.informes[0].id);
    },

    async cargar() {
      const r = await api("GET", "/api/buenas-practicas");
      this.informes = r.informes;
      this.reglas = r.reglas;
      this.permisos = r.permisos;
    },

    async cargarBases() {
      recordar(this.conexionId);
      this.base = "";
      try {
        this.bases = (await api("GET", `/api/buenas-practicas/conexiones/${this.conexionId}/bases`)).bases;
        this.error = null;
      } catch (error) {
        this.bases = [];
        this.error = error.message;
      }
    },

    async analizar() {
      this.analizando = true;
      this.error = null;
      try {
        const informe = await api("POST", "/api/buenas-practicas/informes", {
          conexion_id: this.conexionId, base: this.base, tipos: this.tiposElegidos,
        });
        await this.cargar();
        await this.abrir(informe.id);
      } catch (error) {
        this.error = error.message;
      }
      this.analizando = false;
    },

    // Mientras el análisis corre en segundo plano se consulta su estado.
    async abrir(id) {
      clearTimeout(this.sondeo);
      this.errores = {};
      try {
        this.informe = await api("GET", `/api/buenas-practicas/informes/${id}`);
        if (this.informe.estado === "en_curso") {
          this.sondeo = setTimeout(() => this.abrir(id), REFRESCO_MS);
        } else if (this.informes.some((i) => i.id === id && i.estado === "en_curso")) {
          await this.cargar();
        }
      } catch (error) {
        this.error = error.message;
      }
    },

    ordenadas() {
      return [...(this.informe?.sugerencias || [])].sort((a, b) => ORDEN[a.severidad] - ORDEN[b.severidad]);
    },

    async validar(s) {
      this.validando = s.id;
      this.errores = { ...this.errores, [s.id]: null };
      try {
        const actualizada = await api("POST", `/api/buenas-practicas/informes/${this.informe.id}/sugerencias/${s.id}/sandbox`);
        s.sandbox = actualizada.sandbox;
      } catch (error) {
        this.errores = { ...this.errores, [s.id]: error.message };
      }
      this.validando = null;
    },

    async nuevaRegla() {
      try {
        const regla = await api("POST", "/api/buenas-practicas/reglas", { texto: this.textoRegla, activa: true });
        this.reglas.push(regla);
        this.textoRegla = "";
      } catch (error) {
        this.error = error.message;
      }
    },
    async guardarRegla(r, cambios) {
      try {
        Object.assign(r, await api("PUT", `/api/buenas-practicas/reglas/${r.id}`, { texto: r.texto, activa: r.activa, ...cambios }));
      } catch (error) {
        this.error = error.message;
      }
    },
    async eliminarRegla(r) {
      if (!window.confirm(TEXTOS.confirmarEliminar)) return;
      try {
        await api("DELETE", `/api/buenas-practicas/reglas/${r.id}`);
        this.reglas = this.reglas.filter((x) => x.id !== r.id);
      } catch (error) {
        this.error = error.message;
      }
    },

    fecha(iso) {
      return iso ? new Date(iso).toLocaleString() : "—";
    },
    claseEstado(estado) {
      return { listo: "tag-green", en_curso: "tag-yellow", error: "tag-red", interrumpido: "tag-red" }[estado] || "";
    },
  }));
});
