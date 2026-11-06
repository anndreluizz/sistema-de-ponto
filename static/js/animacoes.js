// Animações das telas de admin: cards entram deslizando (AOS) e números do painel contam (CountUp).
// As marcações são postas aqui, não nos templates: sem JS (ou se algo falhar) tudo aparece normal.
// Anima mesmo com "reduzir movimento" ligado no sistema: pedido do dono (03/11).
(function () {
  if (window.AOS) {
    document.querySelectorAll("main .card").forEach(function (el, i) {
      el.setAttribute("data-aos", "fade-up");
      el.setAttribute("data-aos-delay", String(Math.min(i, 6) * 60));
    });
    AOS.init({ once: true, duration: 550, easing: "ease-out-cubic", offset: 10 });
  }

  if (window.countUp) {
    document.querySelectorAll(".painel-valor").forEach(function (el) {
      // so' numero inteiro puro: "08:00" e "-24:00" (horas) ficam como estao
      if (!/^\s*-?\d+\s*$/.test(el.textContent)) return;
      var valor = parseInt(el.textContent, 10);
      if (valor === 0) return;
      new countUp.CountUp(el, valor, { duration: 1.2, separator: "." }).start();
    });
  }
})();
