(function () {
  "use strict";

  var radios = document.querySelectorAll('input[name="modo_agenda"]');
  var blocoTurno = document.getElementById("bloco-agenda-turno");
  var blocoEscala = document.getElementById("bloco-agenda-escala");
  if (!radios.length || !blocoTurno || !blocoEscala) {
    return;
  }

  function atualizar() {
    var selecionado = document.querySelector('input[name="modo_agenda"]:checked');
    var valor = selecionado ? selecionado.value : "nenhum";
    blocoTurno.classList.toggle("d-none", valor !== "turno");
    blocoEscala.classList.toggle("d-none", valor !== "escala");
  }

  radios.forEach(function (radio) {
    radio.addEventListener("change", atualizar);
  });

  atualizar();
})();

// Campo inválido dentro de "Mais informações" fechado: abre a seção para o aviso aparecer.
document.addEventListener("invalid", function (evento) {
  var secao = evento.target.closest && evento.target.closest("details");
  if (secao) secao.open = true;
}, true);
