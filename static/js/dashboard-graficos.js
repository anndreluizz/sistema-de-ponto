(function () {
  "use strict";

  document.addEventListener("DOMContentLoaded", function () {
    var elementoDados = document.getElementById("dashboard-graficos-dados");
    if (!elementoDados || typeof Chart === "undefined") return;

    var dados = JSON.parse(elementoDados.textContent);
    var corPrimaria = dados.corPrimaria || "#1E40AF";
    var corAtraso = "#b45309";
    var corFalta = "#DC2626";

    // Cria os gráficos só depois que o navegador terminou pelo menos um ciclo de layout —
    // sem isso o Chart.js pode medir o wrapper com largura 0 (o card ainda não tinha
    // tamanho definitivo no momento em que o script rodou) e o canvas fica invisível.
    // setTimeout (em vez de requestAnimationFrame) porque não depende do navegador estar
    // compondo frames visualmente — funciona igual em aba visível ou em segundo plano.
    setTimeout(function () {
      var canvasComparativo = document.getElementById("grafico-comparativo-mensal");
      if (canvasComparativo && dados.comparativoMensal && dados.comparativoMensal.length) {
        var comparativo = dados.comparativoMensal;
        new Chart(canvasComparativo, {
          type: "bar",
          data: {
            labels: comparativo.map(function (item) { return item.rotulo; }),
            datasets: [
              {
                type: "line",
                label: "Horas trabalhadas",
                data: comparativo.map(function (item) { return item.total_trabalhado_horas; }),
                borderColor: corPrimaria,
                backgroundColor: corPrimaria,
                yAxisID: "yHoras",
                tension: 0.3,
              },
              {
                label: "Faltas",
                data: comparativo.map(function (item) { return item.total_faltas; }),
                backgroundColor: corFalta,
                yAxisID: "yContagem",
              },
              {
                label: "Atrasos",
                data: comparativo.map(function (item) { return item.total_atrasos; }),
                backgroundColor: corAtraso,
                yAxisID: "yContagem",
              },
            ],
          },
          options: {
            responsive: true,
            maintainAspectRatio: false,
            scales: {
              yContagem: {
                type: "linear", position: "left", beginAtZero: true,
                title: { display: true, text: "Faltas / atrasos" },
                ticks: { precision: 0 },
              },
              yHoras: {
                type: "linear", position: "right", beginAtZero: true,
                grid: { drawOnChartArea: false },
                title: { display: true, text: "Horas trabalhadas" },
              },
            },
          },
        });
      }

      var canvasRanking = document.getElementById("grafico-ranking-atrasos");
      if (canvasRanking && dados.rankingAtrasos && dados.rankingAtrasos.length) {
        var ranking = dados.rankingAtrasos;
        new Chart(canvasRanking, {
          type: "bar",
          data: {
            labels: ranking.map(function (item) { return item.nome; }),
            datasets: [
              {
                label: "Atraso acumulado (minutos)",
                data: ranking.map(function (item) { return item.atraso_acumulado_minutos; }),
                backgroundColor: corAtraso,
              },
            ],
          },
          options: {
            indexAxis: "y",
            responsive: true,
            maintainAspectRatio: false,
            plugins: {
              legend: { display: false },
              tooltip: {
                callbacks: {
                  label: function (contexto) {
                    var item = ranking[contexto.dataIndex];
                    return item.atrasos + " atraso(s) - " + item.atraso_acumulado_formatado + " acumulado(s)";
                  },
                },
              },
            },
            scales: {
              x: { beginAtZero: true, title: { display: true, text: "Minutos de atraso" }, ticks: { precision: 0 } },
            },
          },
        });
      }
    });
  });
})();
