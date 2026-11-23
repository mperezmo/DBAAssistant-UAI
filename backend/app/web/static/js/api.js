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

  // Formatos comunes a todas las pantallas: separadores del idioma (55,4 · 10.000) y
  // horas siempre en 24 h. dd/MM/yyyy HH:mm:ss en detalles, HH:mm en listas.
  numero(valor, decimales = 1) {
    if (valor === null || valor === undefined || valor === "" || Number.isNaN(Number(valor))) return "—";
    return new Intl.NumberFormat(this.locale, { maximumFractionDigits: decimales }).format(Number(valor));
  },
  _fechaHora(iso, opciones) {
    if (!iso) return "—";
    const fecha = iso instanceof Date ? iso : new Date(iso);
    if (Number.isNaN(fecha.getTime())) return "—";
    return new Intl.DateTimeFormat(this.locale, { hourCycle: "h23", ...opciones }).format(fecha).replace(", ", " ");
  },
  fecha(iso) {
    return this._fechaHora(iso, { day: "2-digit", month: "2-digit", year: "numeric", hour: "2-digit", minute: "2-digit", second: "2-digit" });
  },
  fechaCorta(iso) {
    return this._fechaHora(iso, { day: "2-digit", month: "2-digit", year: "numeric", hour: "2-digit", minute: "2-digit" });
  },
  hora(iso) {
    return this._fechaHora(iso, { hour: "2-digit", minute: "2-digit" });
  },
  horaSegundos(iso) {
    return this._fechaHora(iso, { hour: "2-digit", minute: "2-digit", second: "2-digit" });
  },
  // Singular o plural según la cantidad: las claves de texto traen las dos formas.
  plural(n, uno, varios) {
    return (Number(n) === 1 ? uno : varios).replace("{n}", this.numero(n, 0));
  },

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

  // Error de una llamada listo para mostrar: el texto es el mensaje para la persona y
  // lleva aparte el código técnico (HTTP 500, IA_API_000) para "Detalle técnico". Es un
  // String para que las pantallas lo sigan mostrando con x-text.
  aviso(error) {
    const textos = window.DBA_TEXTOS || {};
    const red = error instanceof TypeError;
    const texto = new String(red ? textos.sinConexion || error.message : error?.message || String(error));
    texto.codigo = red ? "NET" : error?.codigo ?? null;
    texto.estado = error?.estado ?? null;
    texto.servidor = red || (error?.estado ?? 0) >= 500;
    return texto;
  },

  async api(metodo, ruta, cuerpo) {
    // Un FormData (archivos) viaja como multipart: el navegador arma el Content-Type.
    const formulario = cuerpo instanceof FormData;
    const respuesta = await fetch(ruta, {
      method: metodo,
      headers: formulario
        ? { "X-Solicitud": "dba" }
        : { "Content-Type": "application/json", "X-Solicitud": "dba" },
      body: formulario ? cuerpo : cuerpo ? JSON.stringify(cuerpo) : undefined,
    });
    if (respuesta.status === 401) {
      window.location.href = "/ingresar";
      return null;
    }
    let datos = null;
    if (respuesta.status !== 204) {
      try {
        datos = await respuesta.json();
      } catch {
        // Respuesta sin JSON (por ejemplo, un error interno o el proxy rechazó un archivo
        // demasiado grande): se muestra un mensaje humano y el estado HTTP como detalle.
        datos = { mensaje: null, codigo: `HTTP ${respuesta.status}` };
      }
    }
    if (!respuesta.ok) {
      const textos = window.DBA_TEXTOS || {};
      const generico = respuesta.status >= 500 ? textos.servidor : textos.solicitud;
      const error = new Error(datos?.mensaje || generico || respuesta.statusText);
      error.codigo = datos?.codigo ?? `HTTP ${respuesta.status}`;
      error.estado = respuesta.status;
      error.datos = datos?.datos;
      throw error;
    }
    return datos;
  },
};
