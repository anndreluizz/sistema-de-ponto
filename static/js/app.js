(function () {
  "use strict";

  document.addEventListener("DOMContentLoaded", function () {
    document.querySelectorAll(".toast").forEach(function (el) {
      new bootstrap.Toast(el).show();
    });

    document.querySelectorAll('[data-bs-toggle="tooltip"]').forEach(function (el) {
      new bootstrap.Tooltip(el);
    });

    var horaEl = document.getElementById("topbar-hora");
    var dataEl = document.getElementById("topbar-data");
    if (horaEl) {
      var atualizarRelogio = function () {
        var agora = new Date();
        horaEl.textContent = agora.toLocaleTimeString("pt-BR", {
          hour: "2-digit",
          minute: "2-digit",
          second: "2-digit",
        });
        if (dataEl) {
          var dataFormatada = agora.toLocaleDateString("pt-BR", {
            weekday: "long",
            day: "2-digit",
            month: "long",
            year: "numeric",
          });
          dataEl.textContent = dataFormatada;
        }
      };
      atualizarRelogio();
      setInterval(atualizarRelogio, 1000);
    }

    var botaoSidebar = document.getElementById("botao-recolher-sidebar");
    if (botaoSidebar) {
      if (localStorage.getItem("sidebarRecolhida") === "1") {
        document.body.classList.add("sidebar-recolhida");
      }
      botaoSidebar.addEventListener("click", function () {
        document.body.classList.toggle("sidebar-recolhida");
        localStorage.setItem(
          "sidebarRecolhida",
          document.body.classList.contains("sidebar-recolhida") ? "1" : "0"
        );
      });
    }

    var botaoAbrirMobile = document.getElementById("botao-abrir-sidebar");
    if (botaoAbrirMobile) {
      botaoAbrirMobile.addEventListener("click", function (evento) {
        evento.stopPropagation();
        document.body.classList.toggle("sidebar-mobile-aberta");
      });
      // No celular, tocar fora do menu fecha ele.
      document.addEventListener("click", function (evento) {
        if (document.body.classList.contains("sidebar-mobile-aberta") && !evento.target.closest(".sidebar")) {
          document.body.classList.remove("sidebar-mobile-aberta");
        }
      });
    }

    var previewFoto = document.getElementById("preview-foto");
    var inputFoto = document.getElementById("input-foto");
    if (previewFoto && inputFoto) {
      inputFoto.addEventListener("change", function () {
        var arquivo = inputFoto.files && inputFoto.files[0];
        if (!arquivo) return;
        var leitor = new FileReader();
        leitor.onload = function (evento) {
          previewFoto.src = evento.target.result;
        };
        leitor.readAsDataURL(arquivo);
      });
    }
  });
})();
