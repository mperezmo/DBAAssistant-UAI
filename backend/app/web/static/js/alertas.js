// Componente Alpine de la pantalla de alertas (CU-000-004 A a E).
document.addEventListener("alpine:init", () => {
  const TEXTOS = window.TEXTOS_ALERTAS;
  const api = window.DBA.api;
  const REFRESCO_MS = 10000;
  const ORDEN = { critical: 0, warning: 1, info: 2 };

  const fecha = (iso) => (iso ? new Date(iso).toLocaleString() : "—");
  const hora = (iso) => (iso ? new Date(iso).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }) : "");

  // La clave pública VAPID viaja en base64url; PushManager la necesita en bytes.
  const bytesDeClave = (base64url) => {
    const relleno = "=".repeat((4 - (base64url.length % 4)) % 4);
    const binario = atob((base64url + relleno).replace(/-/g, "+").replace(/_/g, "/"));
    return Uint8Array.from(binario, (c) => c.charCodeAt(0));
  };

  Alpine.data("alertas", () => ({
    TEXTOS,
    alertas: [], desestimadas: [], cargado: false, error: null,
    estado: "activa", severidades: ["critical", "warning", "info"], ambiente: "", origen: "",
    permisos: { configurar: false, atender: false },
    seleccionada: null,
    atencion: { accion: "manual", comentario: "", duracion: "24h", hasta: "", mensaje: "", enviando: false },
    conexiones: [],
    reglas: { abierto: false, lista: [], formulario: null, mensaje: "" },
    push: { disponible: false, suscripto: false, procesando: false, mensaje: "", error: false, clave: "" },

    async iniciar() {
      await this.cargar();
      // Enlace directo desde una notificación push: /alertas?id=N.
      const id = Number(new URLSearchParams(location.search).get("id"));
      if (id) {
        const alerta = this.alertas.find((a) => a.id === id) || (await this.buscar(id));
        if (alerta) this.seleccionar(alerta);
      } else if (this.alertas.length) {
        this.seleccionar(this.filtradas()[0] || this.alertas[0]);
      }
      this.prepararPush();
      setInterval(() => !document.hidden && this.cargar(), REFRESCO_MS);
    },

    async cargar() {
      try {
        if (this.estado === "desestimada") {
          this.desestimadas = (await api("GET", "/api/alertas/desestimadas")).desestimadas;
          this.error = null;
          this.cargado = true;
          return;
        }
        const r = await api("GET", `/api/alertas?estado=${this.estado}`);
        this.alertas = r.alertas;
        this.permisos = r.permisos;
        this.error = null;
        if (this.seleccionada) {
          const actual = this.alertas.find((a) => a.id === this.seleccionada.id);
          if (actual) this.seleccionada = actual;
        }
      } catch (error) {
        this.error = error.message;
      }
      this.cargado = true;
    },

    async buscar(id) {
      try { return await api("GET", `/api/alertas/${id}`); } catch { return null; }
    },

    async cambiarEstado(estado) {
      this.estado = estado;
      this.seleccionada = null;
      await this.cargar();
      if (this.filtradas().length) this.seleccionar(this.filtradas()[0]);
    },

    alternarSeveridad(severidad) {
      this.severidades = this.severidades.includes(severidad)
        ? this.severidades.filter((s) => s !== severidad)
        : [...this.severidades, severidad];
    },

    filtradas() {
      return this.alertas
        .filter((a) => this.severidades.includes(a.severidad))
        .filter((a) => !this.ambiente || a.ambiente === this.ambiente)
        .filter((a) => !this.origen || a.origen === this.origen);
    },

    subtitulo() {
      const activas = this.estado === "activa" ? this.alertas.length : null;
      const criticas = this.alertas.filter((a) => a.severidad === "critical").length;
      if (activas === null) return "";
      return TEXTOS.subtitulo.replace("{activas}", activas).replace("{criticas}", criticas);
    },

    seleccionar(alerta) {
      this.seleccionada = alerta;
      this.atencion = { accion: "manual", comentario: "", duracion: "24h", hasta: "", mensaje: "", enviando: false };
    },

    claseSeveridad(s) {
      return { critical: "tag-red", warning: "tag-yellow", info: "tag-blue" }[s];
    },
    colorSeveridad(s) {
      return { critical: "var(--terracotta)", warning: "var(--mustard)", info: "var(--accent)" }[s];
    },
    textoEstado(a) {
      if (a.estado === "atendida") return TEXTOS.estadoAtendida;
      return a.normalizada ? `${TEXTOS.estadoNormalizada} ${fecha(a.normalizada_en)}` : TEXTOS.estadoActiva;
    },
    medida(valor, unidad) {
      const numero = Number.isInteger(valor) ? valor : Number(valor).toFixed(1);
      return `${numero}${unidad ? ` ${unidad}` : ""}`;
    },
    fecha,
    hora,

    // Vencimiento elegido para una desestimación; null = sin vencimiento.
    vencimiento() {
      const a = this.atencion;
      const horas = { "24h": 24, "7d": 168, "30d": 720 }[a.duracion];
      if (horas) return new Date(Date.now() + horas * 3600000).toISOString();
      if (a.duracion === "fecha" && a.hasta) return new Date(a.hasta).toISOString();
      return null;
    },

    async atender() {
      const a = this.atencion;
      a.enviando = true;
      try {
        const id = this.seleccionada.id;
        if (a.accion === "desestimar") {
          await api("POST", `/api/alertas/${id}/desestimar`, { motivo: a.comentario, hasta: this.vencimiento() });
        } else {
          await api("POST", `/api/alertas/${id}/atender`, { accion: a.accion, comentario: a.comentario });
        }
        this.seleccionada = null;
        await this.cargar();
        if (this.filtradas().length) this.seleccionar(this.filtradas()[0]);
      } catch (error) {
        a.mensaje = error.message;
        a.enviando = false;
      }
    },

    async reactivar(d) {
      if (!window.confirm(TEXTOS.confirmarReactivar.replace("{titulo}", d.titulo))) return;
      try {
        await api("POST", `/api/alertas/desestimadas/${d.id}/reactivar`);
        await this.cargar();
      } catch (error) {
        this.error = error.message;
      }
    },

    // --- Reglas ---------------------------------------------------------------------

    async abrirReglas() {
      this.reglas = { abierto: true, lista: [], formulario: null, mensaje: "" };
      try {
        const [reglas, monitor] = await Promise.all([
          api("GET", "/api/alertas/reglas"), api("GET", "/api/monitor"),
        ]);
        this.reglas.lista = reglas.reglas;
        this.conexiones = monitor.conexiones;
      } catch (error) {
        this.reglas.mensaje = error.message;
      }
    },

    nombreConexion(id) {
      if (!id) return TEXTOS.todas;
      return this.conexiones.find((c) => c.id === id)?.alias || "—";
    },
    textoCondicion(r) {
      return `${TEXTOS.metricas[r.metrica]} ${r.operador} ${r.valor} · ${r.sostenida_segundos}s`;
    },

    nuevaRegla() {
      this.reglas.formulario = {
        id: null, nombre: "", metrica: "cpu", operador: ">", valor: "", severidad: "warning",
        conexion_id: "", sostenida_segundos: 60, destinatarios: ["DBA"], push: false, email: false, activa: true,
        mensaje: "", guardando: false,
      };
    },
    editarRegla(r) {
      this.reglas.formulario = { ...r, conexion_id: r.conexion_id || "", mensaje: "", guardando: false };
    },

    async guardarRegla() {
      const f = this.reglas.formulario;
      f.guardando = true;
      const cuerpo = {
        nombre: f.nombre, metrica: f.metrica, operador: f.operador, valor: Number(f.valor),
        severidad: f.severidad, conexion_id: f.conexion_id || null,
        sostenida_segundos: Number(f.sostenida_segundos), destinatarios: f.destinatarios,
        push: f.push, email: f.email, activa: f.activa,
      };
      try {
        if (f.id) await api("PUT", `/api/alertas/reglas/${f.id}`, cuerpo);
        else await api("POST", "/api/alertas/reglas", cuerpo);
        this.reglas.formulario = null;
        this.reglas.lista = (await api("GET", "/api/alertas/reglas")).reglas;
      } catch (error) {
        f.mensaje = error.message;
        f.guardando = false;
      }
    },

    async eliminarRegla(r) {
      if (!window.confirm(TEXTOS.confirmarEliminar.replace("{nombre}", r.nombre))) return;
      try {
        await api("DELETE", `/api/alertas/reglas/${r.id}`);
        this.reglas.lista = this.reglas.lista.filter((x) => x.id !== r.id);
        await this.cargar();
      } catch (error) {
        this.reglas.mensaje = error.message;
      }
    },

    // --- Notificaciones push (CU-000-004-E) ------------------------------------------

    async prepararPush() {
      if (!("serviceWorker" in navigator) || !("PushManager" in window)) return;
      try {
        const estado = await api("GET", "/api/push");
        if (!estado.habilitado) return;
        this.push.clave = estado.clave_publica;
        this.push.disponible = true;
        const registro = await navigator.serviceWorker.getRegistration();
        this.push.suscripto = Boolean(registro && (await registro.pushManager.getSubscription()));
      } catch {
        this.push.disponible = false;
      }
    },

    async alternarPush() {
      const p = this.push;
      p.procesando = true;
      p.error = false;
      try {
        const registro = await navigator.serviceWorker.getRegistration();
        if (!registro) throw new Error(TEXTOS.pushSinServiceWorker);
        const actual = await registro.pushManager.getSubscription();
        if (p.suscripto && actual) {
          await api("POST", "/api/push/suscripciones/baja", { endpoint: actual.endpoint });
          await actual.unsubscribe();
          p.suscripto = false;
          p.mensaje = TEXTOS.pushDesactivado;
        } else {
          if ((await Notification.requestPermission()) !== "granted") throw new Error(TEXTOS.pushDenegado);
          const suscripcion = actual || await registro.pushManager.subscribe({
            userVisibleOnly: true, applicationServerKey: bytesDeClave(p.clave),
          });
          await api("POST", "/api/push/suscripciones", suscripcion.toJSON());
          p.suscripto = true;
          p.mensaje = TEXTOS.pushActivado;
        }
      } catch (error) {
        p.error = true;
        p.mensaje = error.message || TEXTOS.pushNoSoportado;
      }
      p.procesando = false;
    },
  }));
});
