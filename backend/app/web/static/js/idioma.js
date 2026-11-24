// Selector de idioma (ES | EN). Con sesión se guarda en la cuenta (usuario de la empresa u
// operador); en las pantallas sin sesión queda en una cookie del navegador. Después se
// recarga la página traducida.
(() => {
  if (window.DBA_SELECTOR_IDIOMA) return; // el parcial puede estar más de una vez
  window.DBA_SELECTOR_IDIOMA = true;
  const COOKIE = "dba_idioma";
  const GUARDAR = { usuario: "/api/perfil/idioma", operador: "/api/plataforma/idioma" };
  const UN_ANIO_S = 365 * 24 * 3600;

  document.addEventListener("click", async (evento) => {
    const boton = evento.target.closest("[data-selector-idioma] [data-idioma]");
    if (!boton || boton.classList.contains("activo")) return;
    const idioma = boton.dataset.idioma;
    const modo = boton.closest("[data-selector-idioma]").dataset.selectorIdioma;
    if (GUARDAR[modo]) {
      const respuesta = await fetch(GUARDAR[modo], {
        method: "PUT",
        headers: { "Content-Type": "application/json", "X-Solicitud": "dba" },
        body: JSON.stringify({ idioma }),
      });
      if (!respuesta.ok) return;
    } else {
      document.cookie = `${COOKIE}=${idioma}; path=/; max-age=${UN_ANIO_S}; samesite=lax`;
    }
    window.location.reload();
  });
})();
