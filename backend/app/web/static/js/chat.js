// Componente Alpine del Chat IA (CU-000-001 A, B y C).
document.addEventListener("alpine:init", () => {
  const TEXTOS = window.TEXTOS_CHAT;
  const api = window.DBA.api;
  const CLAVE_CONEXION = "dba.chat.conexion";
  const CLAVE_PENDIENTE = "dba.chat.pendiente";
  const POR_PAGINA = 50;
  const CODIGOS_SEGUNDO_FACTOR = ["AMB_VAL_000", "CTX_VAL_001"];

  const leer = (clave) => {
    try { return localStorage.getItem(clave); } catch { return null; }
  };
  const recordar = (clave, valor) => {
    try { localStorage.setItem(clave, valor); } catch { /* sin almacenamiento local */ }
  };

  Alpine.data("chat", () => ({
    TEXTOS, POR_PAGINA,
    conexiones: [], conexionId: "", hilos: [], hiloId: "", titulo: "", mensajes: [],
    texto: "", traduciendo: false, ocupado: false, error: null, errores: {}, cargado: false,
    edicion: null, resultado: null, orden: { columna: null, ascendente: true }, filtros: {}, pagina: 0,

    async iniciar() {
      try {
        this.conexiones = (await api("GET", "/api/chat/conexiones")).conexiones;
        await this.cargarHilos();
      } catch (error) {
        this.error = error.message;
      }
      this.cargado = true;
      const recordada = leer(CLAVE_CONEXION);
      if (this.conexiones.length) {
        this.conexionId = this.conexiones.some((c) => c.id === recordada) ? recordada : this.conexiones[0].id;
      }
      this.$watch("conexionId", (valor) => recordar(CLAVE_CONEXION, valor));
      const params = new URLSearchParams(location.search);
      if (params.get("hilo")) await this.abrirHilo(params.get("hilo"));
      // Vuelta de la confirmación con segundo factor: se retoma la ejecución pendiente.
      const pendiente = sessionStorage.getItem(CLAVE_PENDIENTE);
      if (pendiente) {
        sessionStorage.removeItem(CLAVE_PENDIENTE);
        const p = JSON.parse(pendiente);
        if (p.hilo === this.hiloId) {
          const m = this.mensajePorId(p.mensaje);
          if (m) await this.ejecutar(m, false, p);
        }
      }
    },

    async cargarHilos() {
      this.hilos = (await api("GET", "/api/chat/hilos")).hilos;
    },

    conexionActual() {
      return this.conexiones.find((c) => c.id === this.conexionId);
    },

    async abrirHilo(id) {
      this.resultado = null;
      this.edicion = null;
      this.errores = {};
      if (!id) return this.nuevo();
      try {
        const hilo = await api("GET", `/api/chat/hilos/${id}`);
        this.hiloId = hilo.id;
        this.titulo = hilo.titulo;
        this.mensajes = hilo.mensajes;
        this.conexionId = hilo.conexion_id;
        history.replaceState(null, "", `/chat?hilo=${hilo.id}`);
      } catch (error) {
        this.error = error.message;
        this.nuevo();
      }
    },

    nuevo() {
      this.hiloId = "";
      this.titulo = "";
      this.mensajes = [];
      this.resultado = null;
      history.replaceState(null, "", "/chat");
    },

    async eliminarHilo() {
      if (!window.confirm(TEXTOS.confirmarEliminar)) return;
      try {
        await api("DELETE", `/api/chat/hilos/${this.hiloId}`);
        this.nuevo();
        await this.cargarHilos();
      } catch (error) {
        this.error = error.message;
      }
    },

    atajo(evento) {
      if (evento.ctrlKey || evento.metaKey) {
        evento.preventDefault();
        this.enviar();
      }
    },

    async enviar() {
      const texto = this.texto.trim();
      if (!texto || this.traduciendo) return;
      this.traduciendo = true;
      this.error = null;
      try {
        const r = await api("POST", "/api/chat/mensajes", {
          texto, hilo_id: this.hiloId || null, conexion_id: this.conexionId,
        });
        this.texto = "";
        if (!this.hiloId) {
          this.hiloId = r.hilo_id;
          this.titulo = texto.slice(0, 80);
          history.replaceState(null, "", `/chat?hilo=${r.hilo_id}`);
          await this.cargarHilos();
        }
        this.mensajes.push(r.pedido, r.respuesta);
        this.$nextTick(() => this.$refs.mensajes.lastElementChild?.scrollIntoView({ behavior: "smooth", block: "nearest" }));
      } catch (error) {
        this.error = error.message;
      }
      this.traduciendo = false;
    },

    mensajePorId(id) {
      return this.mensajes.find((m) => m.id === id);
    },
    lineas(m) {
      return (m.sql || "").split("\n").length;
    },
    revision(m) {
      return this.edicion?.id === m.id && this.edicion.revision ? this.edicion.revision : m.revision;
    },
    sqlDe(m) {
      return this.edicion?.id === m.id ? this.edicion.sql : m.sql;
    },

    alternarEdicion(m) {
      this.edicion = this.edicion?.id === m.id ? null : { id: m.id, sql: m.sql, revision: null };
    },

    async revisarEdicion(m) {
      if (this.edicion?.id !== m.id) return;
      try {
        this.edicion.revision = await api("POST", `/api/chat/hilos/${this.hiloId}/revisar`, {
          sql: this.edicion.sql, base: m.base,
        });
      } catch (error) {
        this.edicion.revision = { error: error.message };
      }
    },

    // Ejecuta (o re-ejecuta para ver el resultado). Si falta el segundo factor, guarda
    // el pedido, va a confirmarlo y lo retoma al volver.
    async ejecutar(m, soloVer = false, pendiente = null) {
      const sql = pendiente?.sql ?? (soloVer ? m.sql : this.sqlDe(m));
      const confirmar = pendiente?.confirmar ?? false;
      this.ocupado = true;
      this.errores = { ...this.errores, [m.id]: null };
      try {
        const r = await api("POST", `/api/chat/hilos/${this.hiloId}/mensajes/${m.id}/ejecutar`, {
          sql, base: m.base, confirmar_advertencias: confirmar,
        });
        m.sql = sql;
        m.ejecucion = r.ejecucion;
        if (this.edicion?.id === m.id) this.edicion = null;
        if (r.tipo === "SELECT") this.mostrar(m.id, r);
      } catch (error) {
        await this.manejarError(m, error, sql, confirmar);
      }
      this.ocupado = false;
    },

    async manejarError(m, error, sql, confirmar) {
      if (CODIGOS_SEGUNDO_FACTOR.includes(error.codigo) && window.confirm(TEXTOS.confirmarMfa)) {
        sessionStorage.setItem(CLAVE_PENDIENTE, JSON.stringify({ hilo: this.hiloId, mensaje: m.id, sql, confirmar }));
        window.location.href = `/auth/confirmar?volver=${encodeURIComponent(`/chat?hilo=${this.hiloId}`)}`;
        return;
      }
      if (error.codigo === "CTX_REG_001") {
        const reglas = error.datos.reglas.map((r) => `• ${r.descripcion}`).join("\n");
        if (window.confirm(`${TEXTOS.confirmarAdvertencias}\n\n${reglas}`)) {
          await this.ejecutar(m, false, { sql, confirmar: true });
        }
        return;
      }
      let detalle = "";
      if (error.codigo === "CTX_REG_000") detalle = error.datos.reglas.map((r) => r.descripcion).join(" · ");
      if (error.codigo === "SQL_LCK_000") {
        detalle = `${TEXTOS.sesionesBloqueando} ` + error.datos.sesiones
          .map((s) => `${s.sesion} (${s.usuario}, ${s.tablas.join(", ")})`).join(" · ");
      }
      this.errores = { ...this.errores, [m.id]: detalle ? `${error.message} ${detalle}` : error.message };
    },

    async validarEnSandbox(m) {
      this.ocupado = true;
      this.errores = { ...this.errores, [m.id]: null };
      const sql = this.sqlDe(m);
      try {
        m.sandbox = await api("POST", `/api/chat/hilos/${this.hiloId}/mensajes/${m.id}/sandbox`, { sql, base: m.base });
        m.sql = sql;
        if (this.edicion?.id === m.id) this.edicion = null;
      } catch (error) {
        this.errores = { ...this.errores, [m.id]: error.message };
      }
      this.ocupado = false;
    },

    resumenEjecucion(m) {
      const e = m.ejecucion;
      if (!e) return "";
      if (e.tipo === "SELECT") {
        return TEXTOS.ejecutada.replace("{filas}", e.filas.toLocaleString())
          .replace("{total}", e.total.toLocaleString()).replace("{ms}", e.duracion_ms);
      }
      return TEXTOS.afectadas.replace("{filas}", e.filas_afectadas.toLocaleString()).replace("{ms}", e.duracion_ms);
    },
    resumenSandbox(m) {
      const s = m.sandbox;
      if (!s) return "";
      return s.estado === "ok"
        ? TEXTOS.sandboxOk.replace("{filas}", s.filas ?? 0).replace("{ms}", s.duracion_ms ?? 0)
        : `${TEXTOS.sandboxError} ${s.error || ""}`;
    },
    hora(iso) {
      return iso ? new Date(iso).toLocaleTimeString() : "";
    },

    // --- Resultados: orden, filtros por columna y paginado local (sin re-ejecutar) ---
    mostrar(mensajeId, datos) {
      this.resultado = { mensajeId, datos };
      this.orden = { columna: null, ascendente: true };
      this.filtros = {};
      this.pagina = 0;
    },
    tituloResultado() {
      const d = this.resultado.datos;
      return TEXTOS.mostrando.replace("{filas}", d.filas.length.toLocaleString())
        .replace("{total}", d.total.toLocaleString());
    },
    ordenar(i) {
      this.orden = { columna: i, ascendente: this.orden.columna === i ? !this.orden.ascendente : true };
    },
    filtradas() {
      const filtros = Object.entries(this.filtros).filter(([, v]) => v && v.trim());
      let filas = this.resultado.datos.filas.filter((f) =>
        filtros.every(([i, v]) => String(f[i] ?? "NULL").toLowerCase().includes(v.trim().toLowerCase())));
      if (this.orden.columna !== null) {
        const i = this.orden.columna;
        const signo = this.orden.ascendente ? 1 : -1;
        filas = [...filas].sort((a, b) => {
          const x = a[i], y = b[i];
          if (x === y) return 0;
          if (x === null) return -signo;
          if (y === null) return signo;
          const nx = Number(x), ny = Number(y);
          if (!Number.isNaN(nx) && !Number.isNaN(ny)) return (nx - ny) * signo;
          return String(x).localeCompare(String(y)) * signo;
        });
      }
      return filas;
    },
    paginaActual() {
      return this.filtradas().slice(this.pagina * POR_PAGINA, (this.pagina + 1) * POR_PAGINA);
    },
    textoPaginacion() {
      const total = this.filtradas().length;
      const desde = total ? this.pagina * POR_PAGINA + 1 : 0;
      return TEXTOS.paginacion.replace("{desde}", desde)
        .replace("{hasta}", Math.min((this.pagina + 1) * POR_PAGINA, total)).replace("{total}", total.toLocaleString());
    },
  }));
});
