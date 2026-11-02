(function () {
  "use strict";

  document.addEventListener("DOMContentLoaded", function () {
    var modalEl = document.getElementById("modal-confirmar");
    if (!modalEl) return;

    var modal = new bootstrap.Modal(modalEl);
    var textoEl = document.getElementById("modal-confirmar-texto");
    var botaoConfirmar = document.getElementById("modal-confirmar-botao");
    var formularioPendente = null;

    document.querySelectorAll(".form-confirmar").forEach(function (formulario) {
      formulario.addEventListener("submit", function (evento) {
        if (formulario.dataset.confirmado === "1") return;
        evento.preventDefault();
        formularioPendente = formulario;
        textoEl.textContent = formulario.getAttribute("data-confirmar") || "Confirma esta ação?";
        modal.show();
      });
    });

    botaoConfirmar.addEventListener("click", function () {
      if (formularioPendente) {
        formularioPendente.dataset.confirmado = "1";
        modal.hide();
        formularioPendente.submit();
      }
    });
  });

  document.addEventListener("DOMContentLoaded", function () {
    var selectFuncionario = document.getElementById("novo_funcionario_id");
    var selectTipo = document.getElementById("novo_tipo");
    var campoTimestamp = document.getElementById("novo_timestamp");
    if (!selectFuncionario || !selectTipo) return;

    document.querySelectorAll(".btn-duplicar-registro").forEach(function (botao) {
      botao.addEventListener("click", function (evento) {
        evento.preventDefault();
        var painel = document.getElementById("form-batida");
        if (painel) bootstrap.Collapse.getOrCreateInstance(painel).show();
        var aberto = botao.closest("details");
        if (aberto) aberto.removeAttribute("open");
        selectFuncionario.value = botao.dataset.funcionarioId;
        selectTipo.value = botao.dataset.tipo;
        if (campoTimestamp) {
          campoTimestamp.scrollIntoView({ behavior: "smooth", block: "center" });
          campoTimestamp.focus();
        }
      });
    });
  });
})();
