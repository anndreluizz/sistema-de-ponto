// Animações das telas de admin: cards entram deslizando (AOS) e números do painel contam (CountUp).
// As marcações são postas aqui, não nos templates: sem JS (ou se algo falhar) tudo aparece normal.
(function () {
  if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) return;

  if (window.AOS) {
    document.querySelectorAll("main .card").forEach(function (el, i) {
      el.setAttribute("data-aos", "fade-up");
      el.setAttribute("data-aos-delay", String(Math.min(i, 6) * 60));
    });
    AOS.init({ once: true, duration: 550, easing: "ease-out-cubic", offset: 10 });
  }

  if (window.countUp) {
    document.querySelectorAll(".painel-valor").forEach(function (el) {
      var valor = parseInt(el.textContent, 10);
      if (isNaN(valor) || valor === 0) return;
      new countUp.CountUp(el, valor, { duration: 1.2, separator: "." }).start();
    });
  }
})();
