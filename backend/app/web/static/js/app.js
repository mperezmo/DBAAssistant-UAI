// Registro del service worker para que la aplicación sea instalable (PWA).
if ("serviceWorker" in navigator) {
  window.addEventListener("load", () => {
    navigator.serviceWorker.register("/sw.js").catch((error) => {
      console.warn("No se pudo registrar el service worker:", error);
    });
  });
}
