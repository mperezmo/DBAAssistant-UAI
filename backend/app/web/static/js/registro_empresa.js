// Registro de una empresa invitada: email, código, datos y configuración en segundo plano.
document.addEventListener("alpine:init", () => {
  const TEXTOS = window.TEXTOS_REGISTRO;
  const api = window.DBA.api;
  const PATRON_DIRECCION = /^[a-z0-9](?:[a-z0-9-]{0,38}[a-z0-9])?$/;
  const ESPERA_CODIGO_S = 60;
  const REFRESCO_MS = 1500;
  const PASOS_VISIBLES = ["email", "codigo", "datos", "progreso"];

  // "Café Martínez S.A." → "cafe-martinez-s-a"
  const direccionDesde = (texto) => texto.normalize("NFD").replace(/[̀-ͯ]/g, "").toLowerCase()
    .replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "").slice(0, 40).replace(/-+$/, "");

  Alpine.data("registroEmpresa", (emailInicial) => ({
    TEXTOS, PASOS_VISIBLES,
    paso: "email", email: emailInicial || "", codigo: "", empresa: "", slug: "", nombre: "",
    slugEditado: false, direccionFija: false, disponibilidad: null, espera: 0,
    enviando: false, error: null, estado: null, temporizador: null,

    async iniciar() {
      // Si ya verificó el código (o el alta está en marcha), se retoma donde quedó.
      try {
        this.aplicarEstado(await api("GET", "/api/registro/sesion"));
      } catch {
        this.paso = "email";
      }
    },

    indicePaso() {
      return Math.max(PASOS_VISIBLES.indexOf(this.paso), 0);
    },

    aplicarEstado(estado) {
      this.estado = estado;
      this.email = estado.email;
      const avance = estado.progreso?.estado;
      if (estado.empresa) {
        // Ya hay una empresa creada para esta invitación: la dirección no cambia.
        this.slug = estado.empresa.slug;
        this.empresa = this.empresa || estado.empresa.nombre;
        this.direccionFija = true;
        this.disponibilidad = "fija";
      }
      if (["en_curso", "listo", "error", "interrumpido"].includes(avance)) {
        this.paso = "progreso";
        if (avance === "en_curso") this.seguir();
        if (avance === "listo") setTimeout(() => { window.location.href = estado.progreso.url; }, 3000);
      } else if (estado.invitacion === "pendiente" || estado.invitacion === "en_curso") {
        this.paso = "datos";
      } else {
        this.paso = "cerrada";
      }
    },

    async pedirCodigo() {
      this.enviando = true;
      this.error = null;
      try {
        await api("POST", "/api/registro/codigo", { email: this.email.trim() });
        this.paso = "codigo";
        this.esperar();
        this.$nextTick(() => this.$refs.codigo?.focus());
      } catch (error) {
        this.error = window.DBA.aviso(error);
      }
      this.enviando = false;
    },

    esperar() {
      this.espera = ESPERA_CODIGO_S;
      clearInterval(this.temporizador);
      this.temporizador = setInterval(() => {
        this.espera -= 1;
        if (this.espera <= 0) clearInterval(this.temporizador);
      }, 1000);
    },

    async verificar() {
      this.enviando = true;
      this.error = null;
      try {
        this.aplicarEstado(await api("POST", "/api/registro/verificar", { email: this.email.trim(), codigo: this.codigo.trim() }));
      } catch (error) {
        this.error = window.DBA.aviso(error);
        this.codigo = "";
      }
      this.enviando = false;
    },

    sugerirDireccion() {
      if (this.slugEditado || this.direccionFija) return;
      this.slug = direccionDesde(this.empresa);
      this.revisarDireccion();
    },

    revisarDireccion() {
      if (this.direccionFija) return;
      clearTimeout(this._revision);
      if (!PATRON_DIRECCION.test(this.slug)) {
        this.disponibilidad = this.slug ? "invalida" : null;
        return;
      }
      this.disponibilidad = "revisando";
      const slug = this.slug;
      this._revision = setTimeout(async () => {
        try {
          const r = await api("GET", `/api/registro/disponible?slug=${encodeURIComponent(slug)}`);
          if (slug === this.slug) this.disponibilidad = r.disponible ? "disponible" : "ocupada";
        } catch {
          // Reservada o inválida para la plataforma.
          if (slug === this.slug) this.disponibilidad = "invalida";
        }
      }, 400);
    },

    textoDisponible() {
      const textos = { disponible: TEXTOS.disponible, ocupada: TEXTOS.ocupada, invalida: TEXTOS.invalida, revisando: TEXTOS.revisando, fija: TEXTOS.fija };
      return (textos[this.disponibilidad] || "").replace("{direccion}", this.slug);
    },
    claseDisponible() {
      return { disponible: "text-ok", ocupada: "text-danger", invalida: "text-danger" }[this.disponibilidad] || "text-fg-muted";
    },
    puedeConfigurar() {
      return this.empresa.trim().length >= 2 && this.nombre.trim() && ["disponible", "fija"].includes(this.disponibilidad);
    },

    async configurar() {
      this.enviando = true;
      this.error = null;
      try {
        const estado = await api("POST", "/api/registro/configurar", {
          empresa: this.empresa.trim(), slug: this.slug, nombre: this.nombre.trim(),
        });
        this.estado = estado;
        this.paso = "progreso";
        this.seguir();
      } catch (error) {
        this.error = window.DBA.aviso(error);
      }
      this.enviando = false;
    },

    async seguir() {
      while (this.paso === "progreso") {
        await new Promise((resolver) => setTimeout(resolver, REFRESCO_MS));
        try {
          this.estado = await api("GET", "/api/registro/sesion");
        } catch (error) {
          this.error = window.DBA.aviso(error);
          return;
        }
        const avance = this.estado.progreso?.estado;
        if (avance !== "en_curso") {
          this.aplicarEstado(this.estado);
          return;
        }
      }
    },

    listo() {
      return this.estado?.progreso?.estado === "listo";
    },
    fallo() {
      return ["error", "interrumpido"].includes(this.estado?.progreso?.estado);
    },
    textoProgreso() {
      const avance = this.estado?.progreso?.estado;
      if (avance === "listo") return TEXTOS.progresoListo;
      if (avance === "error") return TEXTOS.progresoError;
      if (avance === "interrumpido") return TEXTOS.progresoInterrumpido;
      return TEXTOS.progresoEnCurso;
    },
    // Pasos del alta: hechos, el actual y los que faltan.
    claseAvance(p) {
      const pasos = this.estado?.pasos || [];
      const actual = this.estado?.progreso?.paso;
      if (this.listo() || pasos.indexOf(p) < pasos.indexOf(actual)) return "hecho";
      if (p === actual) return this.fallo() ? "fallido" : "actual";
      return "";
    },
    marcaAvance(p) {
      return { hecho: "✓", actual: "•", fallido: "!" }[this.claseAvance(p)] || "";
    },
    textoCerrada() {
      return this.estado?.invitacion === "usada" ? TEXTOS.cerradaUsada : TEXTOS.cerrada;
    },
  }));
});
