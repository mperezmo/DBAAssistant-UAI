// Componente Alpine de la pantalla de conexiones (CU-000-000).
document.addEventListener("alpine:init", () => {
  const TEXTOS = window.TEXTOS_CONEXIONES;
  const api = window.DBA.api;
  // Acción pendiente mientras el usuario confirma el segundo factor en Auth0.
  const CLAVE_PENDIENTE = "dba.conexiones.eliminar";

  const vacio = () => ({
    id: null, alias: "", host: "", puerto: null, instancia: "", ambiente: "DEV",
    usuario_sql: "", password_sql: "", winrm_usuario: "", winrm_password: "",
    winrm_puerto: null, winrm_https: false, tiene_winrm: false,
    ventana_inicio: "", ventana_fin: "", zona_horaria: "America/Argentina/Buenos_Aires",
    sandbox_sincronizacion: "03:00",
    mensaje: "", mensajeError: false, probando: false, probandoWinrm: false,
  });

  const textoResultado = (r) =>
    TEXTOS.resultado
      .replace("{version}", r.version)
      .replace("{edicion}", r.edicion)
      .replace("{bases}", r.bases);

  // Servicios de SQL Server informados por Windows, p. ej. "MSSQLSERVER: Running".
  const textoWinrm = (r) =>
    r.servicios.length
      ? TEXTOS.winrmResultado.replace(
          "{servicios}",
          r.servicios.map((s) => `${s.nombre}: ${s.estado}`).join(", "),
        )
      : TEXTOS.winrmSinServicios;

  Alpine.data("gestionConexiones", () => ({
    conexiones: [],
    cargado: false,
    formulario: null,
    aviso: null,
    pruebas: {},
    pruebasWinrm: {},

    async iniciar() {
      await this.cargar();
      const pendiente = sessionStorage.getItem(CLAVE_PENDIENTE);
      if (pendiente) {
        sessionStorage.removeItem(CLAVE_PENDIENTE);
        const conexion = this.conexiones.find((c) => c.id === pendiente);
        if (conexion) await this.eliminar(conexion);
      }
    },

    async cargar() {
      try {
        this.conexiones = (await api("GET", "/api/conexiones")).conexiones;
      } catch (error) {
        this.aviso = { error: true, texto: error.message };
      }
      this.cargado = true;
    },

    servidor(c) {
      if (c.puerto) return `${c.host},${c.puerto}`;
      return c.instancia ? `${c.host}\\${c.instancia}` : c.host;
    },

    claseAmbiente(ambiente) {
      return { PROD: "tag-red", TESTING: "tag-yellow", DEV: "tag-green" }[ambiente];
    },

    abrirNueva() {
      this.aviso = null;
      this.formulario = vacio();
    },

    abrirEdicion(conexion) {
      this.aviso = null;
      this.formulario = { ...vacio(), ...conexion, password_sql: "", winrm_password: "" };
    },

    cuerpo() {
      const f = this.formulario;
      const texto = (v) => (v && String(v).trim()) || null;
      return {
        alias: f.alias, host: f.host, puerto: f.puerto || null, instancia: texto(f.instancia),
        ambiente: f.ambiente, usuario_sql: f.usuario_sql, password_sql: texto(f.password_sql),
        winrm_usuario: texto(f.winrm_usuario), winrm_password: texto(f.winrm_password),
        winrm_puerto: f.winrm_puerto || null, winrm_https: f.winrm_https,
        ventana_inicio: f.ventana_inicio || null, ventana_fin: f.ventana_fin || null,
        zona_horaria: texto(f.zona_horaria) || "America/Argentina/Buenos_Aires",
        sandbox_sincronizacion: f.sandbox_sincronizacion || "03:00",
      };
    },

    async probarFormulario() {
      const f = this.formulario;
      f.probando = true;
      try {
        const r = await api("POST", "/api/conexiones/probar", { ...this.cuerpo(), id: f.id });
        f.mensaje = textoResultado(r);
        f.mensajeError = false;
      } catch (error) {
        f.mensaje = error.message;
        f.mensajeError = true;
      }
      f.probando = false;
    },

    async probarGuardada(c) {
      this.pruebas[c.id] = { estado: "probando" };
      try {
        const r = await api("POST", "/api/conexiones/probar", { ...c, id: c.id });
        this.pruebas[c.id] = { estado: "ok", texto: textoResultado(r) };
      } catch (error) {
        this.pruebas[c.id] = { estado: "error", texto: error.message };
      }
    },

    async probarWinrmFormulario() {
      const f = this.formulario;
      f.probando = f.probandoWinrm = true;
      f.mensaje = "";
      try {
        const r = await api("POST", "/api/conexiones/probar-winrm", { ...this.cuerpo(), id: f.id });
        f.mensaje = textoWinrm(r);
        f.mensajeError = false;
      } catch (error) {
        f.mensaje = error.message;
        f.mensajeError = true;
      }
      f.probando = f.probandoWinrm = false;
    },

    async probarWinrmGuardada(c) {
      this.pruebasWinrm[c.id] = { estado: "probando" };
      try {
        const r = await api("POST", "/api/conexiones/probar-winrm", { ...c, id: c.id });
        this.pruebasWinrm[c.id] = { estado: "ok", texto: textoWinrm(r) };
      } catch (error) {
        this.pruebasWinrm[c.id] = { estado: "error", texto: error.message };
      }
    },

    async guardar() {
      const f = this.formulario;
      try {
        if (f.id) await api("PATCH", `/api/conexiones/${f.id}`, this.cuerpo());
        else await api("POST", "/api/conexiones", this.cuerpo());
        this.formulario = null;
        await this.cargar();
      } catch (error) {
        f.mensaje = error.message;
        f.mensajeError = true;
      }
    },

    async eliminar(conexion) {
      if (!window.confirm(TEXTOS.confirmarEliminar)) return;
      this.aviso = null;
      try {
        await api("DELETE", `/api/conexiones/${conexion.id}`);
        await this.cargar();
      } catch (error) {
        if (error.codigo === "AMB_VAL_000" && window.confirm(TEXTOS.confirmarMfa)) {
          sessionStorage.setItem(CLAVE_PENDIENTE, conexion.id);
          window.location.href = `/auth/confirmar?volver=${encodeURIComponent("/conexiones")}`;
          return;
        }
        this.aviso = { error: true, texto: error.message };
      }
    },
  }));
});
