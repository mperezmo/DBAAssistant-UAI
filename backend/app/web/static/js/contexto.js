// Componente Alpine de Contexto de Negocio (CU-000-006 A a E).
document.addEventListener("alpine:init", () => {
  const TEXTOS = window.TEXTOS_CONTEXTO;
  const api = window.DBA.api;
  const CLAVE_CONEXION = "dba.contexto.conexion";
  const RUTAS = { sensibles: "sensibles", terminos: "terminos", reglas: "reglas" };
  const ROLES = Object.keys(TEXTOS.roles);

  const leer = (clave) => {
    try { return localStorage.getItem(clave); } catch { return null; }
  };
  const recordar = (clave, valor) => {
    try { localStorage.setItem(clave, valor); } catch { /* sin almacenamiento local */ }
  };
  const lista = (texto) => (texto || "").split(",").map((s) => s.trim()).filter(Boolean);

  Alpine.data("contexto", () => ({
    TEXTOS,
    conexiones: [], conexionId: "", base: "", esquema: {}, puedeEditar: false, cargado: false,
    leyendoEsquema: false, categoria: "sensibles", busqueda: "", error: null, formulario: null,
    datos: { sensibles: [], terminos: [], reglas: [] },

    async iniciar() {
      try {
        const r = await api("GET", "/api/contexto/conexiones");
        this.conexiones = r.conexiones;
        this.puedeEditar = r.puede_editar;
      } catch (error) {
        this.error = error.message;
      }
      this.cargado = true;
      if (!this.conexiones.length) return;
      const recordada = leer(CLAVE_CONEXION);
      this.conexionId = this.conexiones.some((c) => c.id === recordada) ? recordada : this.conexiones[0].id;
      await this.cambioConexion();
    },

    conexionActual() {
      return this.conexiones.find((c) => c.id === this.conexionId);
    },
    subtitulo() {
      return TEXTOS.subtitulo.replace("{tablas}", this.tablas().length)
        .replace("{terminos}", this.datos.terminos.length).replace("{reglas}", this.datos.reglas.length);
    },

    async cambioConexion() {
      recordar(CLAVE_CONEXION, this.conexionId);
      this.esquema = {};
      this.base = "";
      await this.cargarEsquema(false);
    },

    async cargarEsquema(refrescar) {
      this.leyendoEsquema = true;
      this.error = null;
      try {
        this.esquema = (await api("GET", `/api/contexto/conexiones/${this.conexionId}/esquema?refrescar=${refrescar}`)).bases;
        if (!this.bases().includes(this.base)) this.base = this.bases()[0] || "";
        await this.cargar();
      } catch (error) {
        this.error = error.message;
      }
      this.leyendoEsquema = false;
    },

    async cargar() {
      if (!this.base) return;
      try {
        const params = new URLSearchParams({ base: this.base });
        this.datos = await api("GET", `/api/contexto/conexiones/${this.conexionId}?${params}`);
      } catch (error) {
        this.error = error.message;
      }
    },

    bases() {
      return Object.keys(this.esquema);
    },
    tablas() {
      return (this.esquema[this.base] || []).map((t) => `${t.esquema}.${t.nombre}`);
    },
    columnasDe(nombre) {
      const tabla = (this.esquema[this.base] || []).find((t) => `${t.esquema}.${t.nombre}` === nombre);
      return tabla ? tabla.columnas.map((c) => c.nombre) : [];
    },
    restricciones(s) {
      const r = [];
      if (s.doble_confirmacion) r.push(TEXTOS.restricciones.doble);
      if (s.columnas.length && s.roles_ven_columnas.length < s.roles_consulta.length) r.push(TEXTOS.restricciones.mascara);
      if (s.roles_consulta.length < ROLES.length) r.push(TEXTOS.restricciones.rol);
      return r;
    },
    ambito(r) {
      const partes = [];
      partes.push(r.conexion_id ? (this.conexiones.find((c) => c.id === r.conexion_id)?.alias || "—") : TEXTOS.todas);
      if (r.base) partes.push(r.base);
      if (r.ambientes.length) partes.push(r.ambientes.join("/"));
      if (r.tablas.length) partes.push(r.tablas.join(", "));
      if (r.desde) partes.push(`${r.desde}–${r.hasta}`);
      return partes.join(" · ");
    },
    terminosFiltrados() {
      const q = this.busqueda.trim().toLowerCase();
      if (!q) return this.datos.terminos;
      return this.datos.terminos.filter((g) =>
        [g.termino, g.entidad, g.descripcion, ...g.sinonimos].some((v) => (v || "").toLowerCase().includes(q)));
    },

    nuevo(tipo) {
      const base = { tipo, id: null, mensaje: "", guardando: false };
      if (tipo === "sensibles") {
        this.formulario = { ...base, tabla: "", columnas: [], roles_consulta: [...ROLES], roles_ven_columnas: ROLES.slice(0, 2), doble_confirmacion: false, descripcion: "" };
      } else if (tipo === "terminos") {
        this.formulario = { ...base, termino: "", entidad: "", sinonimos: "", descripcion: "" };
      } else {
        this.formulario = { ...base, descripcion: "", acciones: [], efecto: "advertir", prioridad: 2, solo_esta: "no", base: "", ambientes: [], tablas: "", desde: "", hasta: "", activa: true };
      }
    },

    editar(tipo, e) {
      if (!this.puedeEditar) return;
      const base = { tipo, id: e.id, mensaje: "", guardando: false };
      if (tipo === "sensibles") {
        this.formulario = { ...base, tabla: e.tabla, columnas: [...e.columnas], roles_consulta: [...e.roles_consulta], roles_ven_columnas: [...e.roles_ven_columnas], doble_confirmacion: e.doble_confirmacion, descripcion: e.descripcion };
      } else if (tipo === "terminos") {
        this.formulario = { ...base, termino: e.termino, entidad: e.entidad, sinonimos: e.sinonimos.join(", "), descripcion: e.descripcion };
      } else {
        this.formulario = { ...base, descripcion: e.descripcion, acciones: [...e.acciones], efecto: e.efecto, prioridad: e.prioridad, solo_esta: e.conexion_id ? "si" : "no", base: e.base || "", ambientes: [...e.ambientes], tablas: e.tablas.join(", "), desde: e.desde || "", hasta: e.hasta || "", activa: e.activa };
      }
    },

    cuerpo(f) {
      if (f.tipo === "sensibles") {
        return { conexion_id: this.conexionId, base: this.base, tabla: f.tabla, columnas: f.columnas, roles_consulta: f.roles_consulta, roles_ven_columnas: f.roles_ven_columnas, doble_confirmacion: f.doble_confirmacion, descripcion: f.descripcion };
      }
      if (f.tipo === "terminos") {
        return { conexion_id: this.conexionId, base: this.base, termino: f.termino, entidad: f.entidad, sinonimos: lista(f.sinonimos), descripcion: f.descripcion };
      }
      return {
        descripcion: f.descripcion, acciones: f.acciones, efecto: f.efecto, prioridad: f.prioridad,
        conexion_id: f.solo_esta === "si" ? this.conexionId : null, base: f.base || null,
        ambientes: f.ambientes, tablas: lista(f.tablas), desde: f.desde || null, hasta: f.hasta || null, activa: f.activa,
      };
    },

    async guardar() {
      const f = this.formulario;
      f.guardando = true;
      f.mensaje = "";
      try {
        const ruta = `/api/contexto/${RUTAS[f.tipo]}${f.id ? `/${f.id}` : ""}`;
        await api(f.id ? "PUT" : "POST", ruta, this.cuerpo(f));
        this.formulario = null;
        await this.cargar();
      } catch (error) {
        f.mensaje = error.message;
        f.guardando = false;
      }
    },

    async eliminar() {
      const f = this.formulario;
      if (!window.confirm(TEXTOS.confirmarEliminar)) return;
      try {
        await api("DELETE", `/api/contexto/${RUTAS[f.tipo]}/${f.id}`);
        this.formulario = null;
        await this.cargar();
      } catch (error) {
        f.mensaje = error.message;
      }
    },
  }));
});
