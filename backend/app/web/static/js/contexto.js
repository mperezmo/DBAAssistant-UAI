// Componente Alpine de Contexto de Negocio (CU-000-006 A a E).
document.addEventListener("alpine:init", () => {
  const TEXTOS = window.TEXTOS_CONTEXTO;
  const api = window.DBA.api;
  const CLAVE_CONEXION = "dba.contexto.conexion";
  const RUTAS = { sensibles: "sensibles", terminos: "terminos", reglas: "reglas" };
  const REFRESCO_DOCUMENTOS_MS = 5000;
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
    datos: { sensibles: [], terminos: [], reglas: [], documentos: [] },
    subida: null, documento: null,

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
      // Mientras la IA resume un documento, la lista se actualiza sola.
      setInterval(() => {
        if (!document.hidden && this.datos.documentos.some((d) => d.estado === "procesando")) this.cargar();
      }, REFRESCO_DOCUMENTOS_MS);
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
        this.refrescarDocumentoAbierto();
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

    // --- Documentos de negocio ---------------------------------------------------------

    claseEstado(d) {
      return { listo: "tag-green", procesando: "tag-yellow", error: "tag-red" }[d.estado] || "";
    },
    tamano(bytes) {
      const formato = new Intl.NumberFormat(window.DBA.locale, { maximumFractionDigits: 1 });
      return bytes >= 1048576 ? `${formato.format(bytes / 1048576)} MB` : `${formato.format(Math.max(1, Math.round(bytes / 1024)))} KB`;
    },
    detalleDocumento(d) {
      return TEXTOS.detalleDocumento.replace("{paginas}", d.paginas).replace("{tamano}", this.tamano(d.bytes));
    },
    cargadoPor(d) {
      const fecha = d.cargado_en ? new Date(d.cargado_en).toLocaleString(window.DBA.locale) : "—";
      return TEXTOS.cargado.replace("{usuario}", d.cargado_por).replace("{fecha}", fecha);
    },
    mensajeError(d) {
      return d.error === "interrumpido" ? TEXTOS.interrumpido : TEXTOS.errorResumen.replace("{codigo}", d.error || "—");
    },

    nuevaSubida() {
      this.subida = { archivo: null, clase: "funcional", alcance: "base", descripcion: "", mensaje: "", enviando: false };
    },

    async subir() {
      const s = this.subida;
      s.enviando = true;
      s.mensaje = "";
      const datos = new FormData();
      datos.append("archivo", s.archivo);
      datos.append("conexion_id", this.conexionId);
      datos.append("clase", s.clase);
      if (s.alcance === "base") datos.append("base", this.base);
      datos.append("descripcion", s.descripcion);
      try {
        const nuevo = await api("POST", "/api/contexto/documentos", datos);
        this.subida = null;
        await this.cargar();
        this.verDocumento(this.vigente(nuevo));
      } catch (error) {
        s.mensaje = error.message;
        s.enviando = false;
      }
    },

    // El estado más reciente del documento (el resumen corre en segundo plano).
    vigente(d) {
      return this.datos.documentos.find((x) => x.id === d.id) || d;
    },
    verDocumento(d) {
      this.documento = { ...d, borrador: d.resumen, mensaje: "", exito: false, guardando: false };
    },
    cerrarDocumento() {
      this.documento = null;
    },
    refrescarDocumentoAbierto() {
      // El resumen que llega de la IA reemplaza al del modal solo si no se estaba editando.
      const abierto = this.documento;
      if (!abierto) return;
      const actual = this.datos.documentos.find((d) => d.id === abierto.id);
      if (!actual || actual.estado === abierto.estado) return;
      this.verDocumento(actual);
    },

    async guardarResumen() {
      const d = this.documento;
      d.guardando = true;
      d.mensaje = "";
      try {
        const guardado = await api("PUT", `/api/contexto/documentos/${d.id}/resumen`, { resumen: d.borrador });
        Object.assign(d, guardado, { borrador: guardado.resumen, mensaje: TEXTOS.guardado, exito: true });
        await this.cargar();
      } catch (error) {
        d.mensaje = error.message;
        d.exito = false;
      }
      d.guardando = false;
    },

    async regenerar() {
      const d = this.documento;
      if (d.resumen && !window.confirm(TEXTOS.confirmarRegenerar)) return;
      try {
        const regenerado = await api("POST", `/api/contexto/documentos/${d.id}/resumen`);
        await this.cargar();
        this.verDocumento(this.vigente(regenerado));
      } catch (error) {
        d.mensaje = error.message;
        d.exito = false;
      }
    },

    async eliminarDocumento() {
      const d = this.documento;
      if (!window.confirm(TEXTOS.confirmarEliminar)) return;
      try {
        await api("DELETE", `/api/contexto/documentos/${d.id}`);
        this.documento = null;
        await this.cargar();
      } catch (error) {
        d.mensaje = error.message;
        d.exito = false;
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
