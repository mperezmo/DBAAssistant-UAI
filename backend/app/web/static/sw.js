// Service worker de la PWA.
// Guarda en caché los recursos estáticos para acelerar la carga; las páginas y
// la API siempre se piden a la red porque muestran información en vivo.
const CACHE = "dba-assistant-v1";
const RECURSOS = [
  "/static/css/app.css",
  "/static/js/app.js",
  "/static/js/vendor/alpine-3.17.4.min.js",
  "/static/img/logo.svg",
];

self.addEventListener("install", (evento) => {
  evento.waitUntil(caches.open(CACHE).then((cache) => cache.addAll(RECURSOS)));
  self.skipWaiting();
});

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
    caches.match(evento.request).then((respuesta) => respuesta || fetch(evento.request))
  );
});
