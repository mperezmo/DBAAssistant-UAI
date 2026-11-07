// Componente Alpine de Mi perfil: datos propios, contraseña y dispositivos.
document.addEventListener("alpine:init", () => {
  const TEXTOS = window.TEXTOS_PERFIL;
  const api = window.DBA.api;

  Alpine.data("miPerfil", () => ({
    TEXTOS,
    datos: null, nombre: "", ocupado: false, aviso: null, nombrePasskey: "",
    soportaPasskeys: Boolean(window.PublicKeyCredential && navigator.credentials),

    async cargar() {
      try {
        this.datos = await api("GET", "/api/perfil");
        this.nombre = this.datos.nombre || "";
      } catch (error) {
        this.aviso = { error: true, texto: error.message };
      }
    },

    async accion(hacer, exito) {
      this.ocupado = true;
      this.aviso = null;
      try {
        await hacer();
        this.aviso = { error: false, texto: exito };
        await this.cargar();
      } catch (error) {
        this.aviso = { error: true, texto: error.message };
      }
      this.ocupado = false;
    },

    guardarNombre() {
      return this.accion(() => api("PATCH", "/api/perfil", { nombre: this.nombre }), TEXTOS.guardado);
    },

    // Auth0 manda el correo con el enlace: la app nunca ve la contraseña.
    cambiarContrasena() {
      return this.accion(() => api("POST", "/api/perfil/contrasena"), TEXTOS.correoEnviado.replace("{email}", this.datos.email));
    },

    // Registrar una passkey exige haber confirmado el segundo factor hace poco (Auth0).
    async agregarPasskey() {
      this.ocupado = true;
      this.aviso = null;
      try {
        const opciones = await api("POST", "/api/perfil/passkeys/opciones");
        const credencial = await navigator.credentials.create({ publicKey: window.DBA.passkeys.opcionesDeRegistro(opciones) });
        await api("POST", "/api/perfil/passkeys", {
          credencial: window.DBA.passkeys.serializar(credencial), nombre: this.nombrePasskey,
        });
        this.nombrePasskey = "";
        this.aviso = { error: false, texto: TEXTOS.passkeyAgregada };
        await this.cargar();
      } catch (error) {
        if (error.codigo === "AMB_VAL_000" && window.confirm(TEXTOS.confirmarMfaPasskey)) {
          window.location.href = `/auth/confirmar?volver=${encodeURIComponent("/perfil")}`;
          return;
        }
        this.aviso = { error: true, texto: error.message };
      }
      this.ocupado = false;
    },

    quitarPasskey(passkey) {
      if (!window.confirm(TEXTOS.confirmarQuitarPasskey)) return;
      return this.accion(() => api("DELETE", `/api/perfil/passkeys/${passkey.id}`), TEXTOS.guardado);
    },

    quitar(dispositivo) {
      if (!window.confirm(TEXTOS.confirmarQuitar)) return;
      return this.accion(() => api("DELETE", `/api/perfil/dispositivos/${dispositivo.id}`), TEXTOS.guardado);
    },

    fecha(iso) {
      return iso ? new Date(iso).toLocaleString(window.DBA.locale) : TEXTOS.nunca;
    },
  }));
});
