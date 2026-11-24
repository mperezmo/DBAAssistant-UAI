// Consola de la plataforma: empresas, invitaciones y operadores.
document.addEventListener("alpine:init", () => {
  const TEXTOS = window.TEXTOS_PLATAFORMA;
  const api = window.DBA.api;
  const CLASES_EMPRESA = { activo: "tag-green", suspendido: "tag-yellow", error: "tag-red", aprovisionando: "tag-blue" };
  const CLASES_INVITACION = { pendiente: "tag-blue", en_curso: "tag-yellow", usada: "tag-green", revocada: "", vencida: "" };

  Alpine.data("consolaPlataforma", () => ({
    TEXTOS, pestana: "empresas", cargado: false, error: null, aviso: "", enviando: false,
    empresas: [], invitaciones: [], operadores: [],
    nuevaInvitacion: "", enlace: "", textoEnlace: "", nuevoOperador: { email: "", nombre: "" },

    async iniciar() {
      // Vuelta de la confirmación con segundo factor: se sigue entrando a la empresa elegida.
      const url = new URL(location.href);
      const pendiente = url.searchParams.get("soporte");
      if (pendiente) {
        url.searchParams.delete("soporte");
        history.replaceState(null, "", url);
        await this.entrarSoporte({ id: pendiente });
      }
      await this.cargar();
    },

    // Entrar a una empresa como soporte: pide el segundo factor si no es reciente.
    async entrarSoporte(empresa) {
      try {
        const r = await api("POST", `/api/plataforma/empresas/${empresa.id}/soporte`);
        window.location.href = r.url;
      } catch (error) {
        if (error.codigo === "AMB_VAL_000") {
          window.location.href = `/auth/confirmar?volver=${encodeURIComponent(`/?soporte=${empresa.id}`)}`;
          return;
        }
        this.error = window.DBA.aviso(error);
      }
    },
    reintentar() {
      return this.cargar();
    },

    async cargar() {
      try {
        const [e, i, o] = await Promise.all([
          api("GET", "/api/plataforma/empresas"),
          api("GET", "/api/plataforma/invitaciones"),
          api("GET", "/api/plataforma/operadores"),
        ]);
        this.empresas = e.empresas;
        this.invitaciones = i.invitaciones;
        this.operadores = o.operadores;
        this.error = null;
      } catch (error) {
        this.error = window.DBA.aviso(error);
      }
      this.cargado = true;
    },

    tituloPestana(p) {
      const cantidad = { empresas: this.empresas.length, invitaciones: this.pendientes(), operadores: this.operadores.length }[p];
      return this.cargado && cantidad ? `${TEXTOS.pestanas[p]} · ${cantidad}` : TEXTOS.pestanas[p];
    },
    pendientes() {
      return this.invitaciones.filter((i) => i.estado === "pendiente").length;
    },
    fecha(iso) {
      return window.DBA.fechaCorta(iso);
    },
    claseEmpresa(estado) {
      return CLASES_EMPRESA[estado] || "";
    },
    claseInvitacion(estado) {
      return CLASES_INVITACION[estado] ?? "";
    },

    async accion(fn, mensaje) {
      this.enviando = true;
      this.aviso = "";
      try {
        await fn();
        if (mensaje) this.aviso = mensaje;
        this.error = null;
        await this.cargar();
      } catch (error) {
        this.error = window.DBA.aviso(error);
      }
      this.enviando = false;
    },

    cambiarEstado(empresa, accion) {
      const pregunta = accion === "suspender" ? TEXTOS.confirmarSuspender : TEXTOS.confirmarReactivar;
      if (!window.confirm(pregunta.replace("{empresa}", empresa.nombre))) return;
      return this.accion(() => api("POST", `/api/plataforma/empresas/${empresa.id}/${accion}`));
    },

    // Enviar por correo o copiar el link: en los dos casos empiezan a correr las 24 h.
    invitar(enviar) {
      const email = this.nuevaInvitacion.trim();
      return this.accion(async () => {
        const r = await api("POST", "/api/plataforma/invitaciones", { email, enviar });
        this.enlace = r.enlace;
        if (enviar) {
          this.textoEnlace = (r.envio === "enviado" ? TEXTOS.enviada : TEXTOS.sinCorreo).replace("{email}", email);
        } else {
          this.textoEnlace = (await this.copiar()) ? TEXTOS.copiada : TEXTOS.copiarManual;
        }
        this.nuevaInvitacion = "";
      });
    },

    async copiar() {
      try {
        await navigator.clipboard.writeText(this.enlace);
        return true;
      } catch {
        // Sin permiso de portapapeles: queda el link seleccionado para copiarlo a mano.
        this.$nextTick(() => this.$refs.enlace?.select());
        return false;
      }
    },

    revocar(invitacion) {
      if (!window.confirm(TEXTOS.confirmarRevocar.replace("{email}", invitacion.email))) return;
      return this.accion(() => api("POST", `/api/plataforma/invitaciones/${invitacion.id}/revocar`));
    },

    altaOperador() {
      const datos = { email: this.nuevoOperador.email.trim(), nombre: this.nuevoOperador.nombre.trim() || null };
      return this.accion(async () => {
        const r = await api("POST", "/api/plataforma/operadores", datos);
        this.aviso = (r.aviso === "enviado" ? TEXTOS.operadorAlta : TEXTOS.operadorSinCorreo).replace("{email}", datos.email);
        this.nuevoOperador = { email: "", nombre: "" };
      });
    },

    bajaOperador(operador) {
      if (!window.confirm(TEXTOS.confirmarBaja.replace("{email}", operador.email))) return;
      return this.accion(() => api("DELETE", `/api/plataforma/operadores/${operador.id}`));
    },
  }));
});
