// Componente Alpine del panel principal (diseño: PageDashboard).
document.addEventListener("alpine:init", () => {
  const TEXTOS = window.TEXTOS_INICIO;
  const api = window.DBA.api;
  const CLAVE_AMBIENTE = "dba.inicio.ambiente";
  const REFRESCO_MS = 30000;

  const leer = () => {
    try { return localStorage.getItem(CLAVE_AMBIENTE); } catch { return null; }
  };
  const recordar = (valor) => {
    try { localStorage.setItem(CLAVE_AMBIENTE, valor); } catch { /* sin almacenamiento local */ }
  };
  const escapar = (texto) => String(texto).replace(/[&<>"']/g, (c) => `&#${c.charCodeAt(0)};`);

  Alpine.data("inicio", () => ({
    TEXTOS, datos: null, error: null, ambiente: "TODOS",

    async iniciar() {
      const recordado = leer();
      if (recordado) this.ambiente = recordado;
      await this.cargar();
      setInterval(() => !document.hidden && this.cargar(), REFRESCO_MS);
    },

    async cambiarAmbiente() {
      recordar(this.ambiente);
      await this.cargar();
    },

    async cargar() {
      try {
        this.datos = await api("GET", `/api/inicio?ambiente=${this.ambiente}`);
        this.error = null;
      } catch (error) {
        this.error = error.message;
      }
    },

    encabezado() {
      const fecha = new Date().toLocaleDateString("es-AR", { weekday: "long", day: "numeric", month: "long", year: "numeric" });
      return `${TEXTOS.eyebrow} · ${fecha.charAt(0).toUpperCase()}${fecha.slice(1)}`;
    },

    // Texto con el ambiente resaltado, como en el diseño. Los valores se escapan.
    subtitulo() {
      if (!this.datos) return "";
      if (!this.datos.instancias.total) return escapar(TEXTOS.sinInstancias);
      const ambiente = this.ambiente === "TODOS" ? TEXTOS.todos : this.ambiente;
      const partes = [TEXTOS.resumen.replace("{ambiente}", `<strong style="color: var(--fg)">${escapar(ambiente)}</strong>`)];
      const activas = this.datos.alertas.activas;
      partes.push(activas ? TEXTOS.alertasPendientes.replace("{n}", activas) : TEXTOS.sinAlertas);
      if (this.datos.pendientes_promocion) {
        partes.push(TEXTOS.promociones.replace("{n}", this.datos.pendientes_promocion));
      }
      return partes.join(" ");
    },

    textoCriticas() {
      const n = this.datos.alertas.criticas;
      if (!n) return TEXTOS.sinCriticas;
      return n === 1 ? TEXTOS.criticaUna : TEXTOS.criticas.replace("{n}", n);
    },
    disponibilidad() {
      const valor = this.datos?.salud.disponibilidad;
      return valor === null || valor === undefined ? "—" : valor.toLocaleString("es-AR", { minimumFractionDigits: 1 });
    },
    colorDisponibilidad() {
      const valor = this.datos?.salud.disponibilidad;
      if (valor === null || valor === undefined) return "var(--fg-muted)";
      // Como en el diseño, 98,4% ya se muestra como saludable.
      return valor >= 98 ? "var(--sage)" : valor >= 95 ? "var(--mustard)" : "var(--terracotta)";
    },
  }));
});
