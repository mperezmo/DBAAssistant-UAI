// Selector de idioma (ES | EN). El del operador se guarda en su cuenta; en las pantallas
// sin sesión queda en una cookie del navegador. Después se recarga la página traducida.
(() => {
  if (window.DBA_SELECTOR_IDIOMA) return; // el parcial puede estar más de una vez
  window.DBA_SELECTOR_IDIOMA = true;
  const COOKIE = "dba_idioma";
  const UN_ANIO_S = 365 * 24 * 3600;

  document.addEventListener("click", async (evento) => {
    const boton = evento.target.closest("[data-selector-idioma] [data-idioma]");
    if (!boton || boton.classList.contains("activo")) return;
    const idioma = boton.dataset.idioma;
    const modo = boton.closest("[data-selector-idioma]").dataset.selectorIdioma;
    if (modo === "operador") {
      const respuesta = await fetch("/api/plataforma/idioma", {
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
