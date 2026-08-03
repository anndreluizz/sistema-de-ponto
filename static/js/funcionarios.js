(function () {
  "use strict";

  var LINHAS_POR_PAGINA = 10;
  var tabela = document.getElementById("tabela-funcionarios");
  if (!tabela) return;

  var linhas = Array.prototype.slice.call(tabela.querySelectorAll("tbody tr[data-busca]"));
  var campoBusca = document.getElementById("busca-funcionario");
  var filtroSituacao = document.getElementById("filtro-situacao");
  var filtroSetor = document.getElementById("filtro-setor");
  var filtroTurno = document.getElementById("filtro-turno");
  var paginacaoEl = document.getElementById("paginacao-funcionarios");
  var contadorEl = document.getElementById("contador-funcionarios");
  var paginaAtual = 1;

  function linhaPassaFiltros(linha) {
    var termo = (campoBusca.value || "").toLowerCase().trim();
    if (termo && linha.dataset.busca.indexOf(termo) === -1) return false;
    if (filtroSituacao.value && linha.dataset.situacao !== filtroSituacao.value) return false;
    if (filtroSetor.value && linha.dataset.setor !== filtroSetor.value) return false;
    if (filtroTurno.value && linha.dataset.turno !== filtroTurno.value) return false;
    return true;
  }

  function renderizar() {
    var visiveis = linhas.filter(linhaPassaFiltros);
    var totalPaginas = Math.max(1, Math.ceil(visiveis.length / LINHAS_POR_PAGINA));
    paginaAtual = Math.min(paginaAtual, totalPaginas);

    linhas.forEach(function (linha) {
      linha.classList.add("d-none");
    });

    var inicio = (paginaAtual - 1) * LINHAS_POR_PAGINA;
    visiveis.slice(inicio, inicio + LINHAS_POR_PAGINA).forEach(function (linha) {
      linha.classList.remove("d-none");
    });

    if (contadorEl) {
      contadorEl.textContent =
        visiveis.length + (visiveis.length === 1 ? " funcionário encontrado" : " funcionários encontrados");
    }

    paginacaoEl.innerHTML = "";
    if (totalPaginas > 1) {
      for (var pagina = 1; pagina <= totalPaginas; pagina++) {
        var botao = document.createElement("button");
        botao.type = "button";
        botao.className = "btn btn-sm " + (pagina === paginaAtual ? "btn-primary" : "btn-outline-secondary");
        botao.textContent = pagina;
        botao.addEventListener(
          "click",
          (function (p) {
            return function () {
              paginaAtual = p;
              renderizar();
            };
          })(pagina)
        );
        paginacaoEl.appendChild(botao);
      }
    }
  }

  [campoBusca, filtroSituacao, filtroSetor, filtroTurno].forEach(function (elemento) {
    if (elemento) {
      elemento.addEventListener("input", function () {
        paginaAtual = 1;
        renderizar();
      });
      elemento.addEventListener("change", function () {
        paginaAtual = 1;
        renderizar();
      });
    }
  });

  renderizar();
})();
