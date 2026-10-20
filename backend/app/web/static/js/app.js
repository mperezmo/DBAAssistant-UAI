// Contador de alertas activas en el menú (como la campana del diseño).
const REFRESCO_CONTADOR_ALERTAS_MS = 30000;

async function actualizarContadorAlertas(contador) {
  try {
    const respuesta = await fetch("/api/alertas/resumen");
    if (!respuesta.ok) return;
    const { activas } = await respuesta.json();
    contador.textContent = activas;
    contador.hidden = activas === 0;
  } catch {
    // Sin conexión: se mantiene el último valor.
  }
}

window.addEventListener("load", () => {
  const contador = document.querySelector("[data-alertas-activas]");
  if (!contador) return;
  actualizarContadorAlertas(contador);
  setInterval(() => !document.hidden && actualizarContadorAlertas(contador), REFRESCO_CONTADOR_ALERTAS_MS);
});

// Menú lateral en pantallas chicas: se abre y cierra con el botón de la barra superior.
document.addEventListener("click", (evento) => {
  if (evento.target.closest("[data-alternar-menu]")) {
    document.body.classList.toggle("menu-abierto");
  } else if (!evento.target.closest(".sidebar")) {
    document.body.classList.remove("menu-abierto");
  }
});

// Registro del service worker para que la aplicación sea instalable (PWA).
if ("serviceWorker" in navigator) {
  window.addEventListener("load", () => {
    navigator.serviceWorker.register("/sw.js").catch((error) => {
      console.warn("No se pudo registrar el service worker:", error);
    });
  });
}
