// Cliente de la API REST del backend, compartido por las pantallas.
// Las peticiones que modifican datos llevan la cabecera X-Solicitud (defensa CSRF).
// WebAuthn usa bytes; la API los intercambia en base64url (JSON).
const passkeys = {
  aBytes(texto) {
    const base64 = texto.replace(/-/g, "+").replace(/_/g, "/").padEnd(Math.ceil(texto.length / 4) * 4, "=");
    return Uint8Array.from(atob(base64), (c) => c.charCodeAt(0));
  },
  aTexto(bytes) {
    return btoa(String.fromCharCode(...new Uint8Array(bytes))).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
  },
  credenciales(lista) {
    return (lista || []).map((c) => ({ ...c, id: passkeys.aBytes(c.id) }));
  },
  opcionesDeRegistro(o) {
    return {
      ...o, challenge: passkeys.aBytes(o.challenge),
      user: { ...o.user, id: passkeys.aBytes(o.user.id) },
      excludeCredentials: passkeys.credenciales(o.excludeCredentials),
    };
  },
  opcionesDeConfirmacion(o) {
    return { ...o, challenge: passkeys.aBytes(o.challenge), allowCredentials: passkeys.credenciales(o.allowCredentials) };
  },
  serializar(c) {
    const r = c.response;
    const respuesta = { clientDataJSON: passkeys.aTexto(r.clientDataJSON) };
    if (r.attestationObject) {
      respuesta.attestationObject = passkeys.aTexto(r.attestationObject);
      respuesta.transports = r.getTransports ? r.getTransports() : [];
    } else {
      respuesta.authenticatorData = passkeys.aTexto(r.authenticatorData);
      respuesta.signature = passkeys.aTexto(r.signature);
      respuesta.userHandle = r.userHandle ? passkeys.aTexto(r.userHandle) : null;
    }
    return { id: c.id, rawId: passkeys.aTexto(c.rawId), type: c.type, response: respuesta, clientExtensionResults: c.getClientExtensionResults() };
  },
};

window.DBA = {
  passkeys,

  // Idioma de la interfaz y su locale para fechas y números (Intl).
  idioma: document.documentElement.lang || "es",
  locale: document.documentElement.dataset.locale || "es-AR",

  // Confirmación con segundo factor para una acción sensible. Si el usuario tiene una
  // passkey en este dispositivo, la resuelve la biometría sin salir de la app y devuelve
  // true (quien llama reintenta la acción). Si no, o si la cancela, va a Auth0: antes se
  // ejecuta antesDeSalir (para guardar lo pendiente) y se vuelve a "volver".
  async segundoFactor(volver, antesDeSalir) {
    if (window.PublicKeyCredential && navigator.credentials) {
      try {
        const opciones = await this.api("POST", "/api/perfil/passkeys/desafio");
        if (opciones) {
          const credencial = await navigator.credentials.get({ publicKey: passkeys.opcionesDeConfirmacion(opciones) });
          await this.api("POST", "/api/perfil/passkeys/confirmar", { credencial: passkeys.serializar(credencial) });
          return true;
        }
      } catch {
        // Cancelada o sin passkey en este dispositivo: se confirma con Auth0.
      }
    }
    if (antesDeSalir) antesDeSalir();
    window.location.href = `/auth/confirmar?volver=${encodeURIComponent(volver)}`;
    return false;
  },

  async api(metodo, ruta, cuerpo) {
    const respuesta = await fetch(ruta, {
      method: metodo,
      headers: { "Content-Type": "application/json", "X-Solicitud": "dba" },
      body: cuerpo ? JSON.stringify(cuerpo) : undefined,
    });
    if (respuesta.status === 401) {
      window.location.href = "/ingresar";
      return null;
    }
    const datos = respuesta.status === 204 ? null : await respuesta.json();
    if (!respuesta.ok) {
      const error = new Error(`${datos.mensaje} (${datos.codigo})`);
      error.codigo = datos.codigo;
      error.datos = datos.datos;
      throw error;
    }
    return datos;
  },
};
