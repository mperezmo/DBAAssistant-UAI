// Service worker de la PWA.
// Los recursos estáticos se piden siempre a la red (para tomar cada versión nueva) y
// se guardan en caché solo como respaldo cuando no hay conexión. Las páginas y la API
// no se cachean porque muestran información en vivo.
const CACHE = "dba-assistant-v2";

self.addEventListener("install", () => self.skipWaiting());

self.addEventListener("activate", (evento) => {
  evento.waitUntil(
    caches.keys().then((claves) =>
      Promise.all(claves.filter((clave) => clave !== CACHE).map((clave) => caches.delete(clave)))
    )
  );
  self.clients.claim();
});

self.addEventListener("fetch", (evento) => {
  const url = new URL(evento.request.url);
  if (evento.request.method !== "GET" || !url.pathname.startsWith("/static/")) return;
  evento.respondWith(
    fetch(evento.request)
      .then((respuesta) => {
        const copia = respuesta.clone();
        caches.open(CACHE).then((cache) => cache.put(evento.request, copia));
        return respuesta;
      })
      .catch(() => caches.match(evento.request))
  );
});
