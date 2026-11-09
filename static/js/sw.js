// Guarda a tela do totem (/) e os arquivos dela: se o tablet reiniciar sem internet, a página
// ainda abre e as batidas vão para a fila do kiosk.js. Só o totem — o painel nunca passa por aqui.
// Sempre tenta a rede primeiro, então uma atualização do sistema chega assim que houver conexão.
var CACHE = "totem-v1";

self.addEventListener("install", function () { self.skipWaiting(); });
self.addEventListener("activate", function (e) { e.waitUntil(self.clients.claim()); });

self.addEventListener("fetch", function (e) {
  var url = new URL(e.request.url);
  var doTotem = url.origin === location.origin && (url.pathname === "/" || url.pathname.indexOf("/static/") === 0);
  if (e.request.method !== "GET" || !doTotem) return;
  e.respondWith(
    fetch(e.request)
      .then(function (resposta) {
        if (resposta.ok) {
          var copia = resposta.clone();
          caches.open(CACHE).then(function (c) { c.put(e.request, copia); });
        }
        return resposta;
      })
      .catch(function () {
        return caches.match(e.request).then(function (r) { return r || Response.error(); });
      })
  );
});
