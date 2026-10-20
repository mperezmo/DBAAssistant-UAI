// Cliente de la API REST del backend, compartido por las pantallas.
// Las peticiones que modifican datos llevan la cabecera X-Solicitud (defensa CSRF).
window.DBA = {
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
