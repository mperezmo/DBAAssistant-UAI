// Componente Alpine de Mi perfil: datos propios, contraseña y dispositivos.
document.addEventListener("alpine:init", () => {
  const TEXTOS = window.TEXTOS_PERFIL;
  const api = window.DBA.api;

  Alpine.data("miPerfil", () => ({
    TEXTOS,
    datos: null, nombre: "", ocupado: false, aviso: null,

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

    quitar(dispositivo) {
      if (!window.confirm(TEXTOS.confirmarQuitar)) return;
      return this.accion(() => api("DELETE", `/api/perfil/dispositivos/${dispositivo.id}`), TEXTOS.guardado);
    },

    fecha(iso) {
      return iso ? new Date(iso).toLocaleString(window.DBA.locale) : TEXTOS.nunca;
    },
  }));
});
