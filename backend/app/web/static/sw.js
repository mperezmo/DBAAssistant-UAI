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

// Notificación push de una alerta (CU-000-004-E). El contenido lo arma el backend.
self.addEventListener("push", (evento) => {
  let datos = {};
  try {
    datos = evento.data ? evento.data.json() : {};
  } catch {
    datos = { cuerpo: evento.data ? evento.data.text() : "" };
  }
  evento.waitUntil(
    self.registration.showNotification(datos.titulo || "DBA Assistant", {
      body: datos.cuerpo || "",
      tag: datos.etiqueta,
      icon: "/static/img/logo.svg",
      badge: "/static/img/logo.svg",
      data: { url: datos.url || "/alertas" },
      requireInteraction: true,
    })
  );
});

// Al tocar la notificación se abre el detalle de la alerta; si la sesión venció, la
// propia app redirige al ingreso.
self.addEventListener("notificationclick", (evento) => {
  evento.notification.close();
  const destino = new URL(evento.notification.data?.url || "/alertas", self.location.origin).href;
  evento.waitUntil(
    self.clients.matchAll({ type: "window", includeUncontrolled: true }).then((ventanas) => {
      const abierta = ventanas.find((v) => v.url.startsWith(self.location.origin));
      if (abierta) return abierta.focus().then(() => abierta.navigate(destino));
      return self.clients.openWindow(destino);
    })
  );
});
