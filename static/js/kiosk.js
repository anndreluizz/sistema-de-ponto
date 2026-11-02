(function () {
  "use strict";

  var telaInicial = document.getElementById("tela-inicial");
  var telaCamera = document.getElementById("tela-camera");
  var telaFeedback = document.getElementById("tela-feedback");
  var botaoIniciar = document.getElementById("botao-iniciar");
  var erroCamera = document.getElementById("erro-camera");
  var linkAdmin = document.getElementById("link-admin");
  var video = document.getElementById("video");
  var canvas = document.getElementById("canvas");
  var contexto = canvas.getContext("2d", { willReadFrequently: true });
  var relogio = document.getElementById("relogio");
  var feedbackFoto = document.getElementById("feedback-foto");
  var feedbackIcone = document.getElementById("feedback-icone");
  var feedbackTexto = document.getElementById("feedback-texto");
  var feedbackSubtexto = document.getElementById("feedback-subtexto");

  var LARGURA_PROCESSAMENTO = 480;
  var DURACAO_FEEDBACK_MS = 3000;
  var DURACAO_ERRO_MS = 6000; // dá tempo de ler o motivo
  // Precisa ser maior que COOLDOWN_SEGUNDOS do servidor (8s), senão quem segura o crachá
  // na frente da câmera depois de bater o ponto recebe uma tela vermelha de "erro".
  var RESFRIAMENTO_MESMO_CODIGO_MS = 10000;
  var TEMPO_LIMITE_REQUISICAO_MS = 8000;

  var ocupado = false;
  var audioContexto = null;
  var ultimoCodigoLido = null;
  var ultimoCodigoLidoEm = 0;
  var loopRodando = false;

  function atualizarRelogio() {
    var agora = new Date();
    relogio.textContent = agora.toLocaleTimeString("pt-BR", { hour: "2-digit", minute: "2-digit" });
  }
  setInterval(atualizarRelogio, 1000);
  atualizarRelogio();

  function tocarBeep(sucesso) {
    try {
      if (!audioContexto) {
        audioContexto = new (window.AudioContext || window.webkitAudioContext)();
      }
      var oscilador = audioContexto.createOscillator();
      var ganho = audioContexto.createGain();
      oscilador.frequency.value = sucesso ? 880 : 220;
      oscilador.type = "sine";
      ganho.gain.value = 0.15;
      oscilador.connect(ganho);
      ganho.connect(audioContexto.destination);
      oscilador.start();
      oscilador.stop(audioContexto.currentTime + 0.18);
    } catch (erro) {
      // Se o navegador bloquear áudio, o ponto continua sendo registrado normalmente.
    }
  }

  function mostrarFeedback(sucesso, titulo, subtitulo, foto) {
    telaFeedback.classList.remove("oculto");
    telaFeedback.classList.toggle("feedback-sucesso", sucesso);
    telaFeedback.classList.toggle("feedback-erro", !sucesso);
    feedbackIcone.innerHTML = sucesso
      ? '<i class="bi bi-check-circle-fill"></i>'
      : '<i class="bi bi-x-circle-fill"></i>';
    feedbackTexto.textContent = titulo;
    feedbackSubtexto.textContent = subtitulo || "";

    if (foto) {
      feedbackFoto.src = foto;
      feedbackFoto.classList.remove("oculto");
    } else {
      feedbackFoto.classList.add("oculto");
    }

    tocarBeep(sucesso);

    setTimeout(function () {
      telaFeedback.classList.add("oculto");
      ocupado = false;
    }, sucesso ? DURACAO_FEEDBACK_MS : DURACAO_ERRO_MS);
  }

  var canvasFoto = document.createElement("canvas");
  var LARGURA_FOTO_BATIDA = 320;

  // Foto do instante da batida (o RH confere em Ajustes de Ponto se suspeitar de crachá emprestado).
  function capturarFoto() {
    try {
      canvasFoto.width = LARGURA_FOTO_BATIDA;
      canvasFoto.height = Math.round(video.videoHeight * LARGURA_FOTO_BATIDA / video.videoWidth);
      canvasFoto.getContext("2d").drawImage(video, 0, 0, canvasFoto.width, canvasFoto.height);
      return canvasFoto.toDataURL("image/jpeg", 0.6);
    } catch (erro) {
      return null; // sem foto, a batida vale do mesmo jeito
    }
  }

  function processarCodigo(codigo) {
    ocupado = true;
    var foto = capturarFoto();
    // Sem tempo limite, um Wi-Fi que cai no meio da requisição deixava o totem travado
    // (ocupado = true) sem ler mais nenhum crachá e sem avisar ninguém.
    var controle = new AbortController();
    var temporizador = setTimeout(function () { controle.abort(); }, TEMPO_LIMITE_REQUISICAO_MS);
    fetch("/api/bater-ponto", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ codigo: codigo, foto: foto }),
      signal: controle.signal,
    })
      .then(function (resposta) {
        // Um erro 500 vem como página HTML, não JSON: antes isso aparecia como "Sem conexão".
        return resposta.json().catch(function () {
          return { ok: false, erro: "Erro no servidor (" + resposta.status + "). Chame a coordenação." };
        });
      })
      .then(function (dados) {
        if (dados.ok) {
          var tipoTexto = dados.tipo === "entrada" ? "Entrada registrada" : "Saída registrada";
          mostrarFeedback(true, dados.nome, tipoTexto + " às " + dados.horario, dados.foto);
        } else {
          var titulo = "Não foi possível registrar";
          if (dados.motivo === "ferias" || dados.motivo === "afastado" || dados.motivo === "inativo") {
            titulo = "Acesso não autorizado";
          }
          mostrarFeedback(false, titulo, dados.erro || "Tente novamente.");
        }
      })
      .catch(function () {
        mostrarFeedback(false, "Sem conexão com o servidor", "Chame a coordenação.");
      })
      .then(function () {
        clearTimeout(temporizador);
      });
  }

  function loop() {
    requestAnimationFrame(loop);

    if (ocupado || video.readyState !== video.HAVE_ENOUGH_DATA) {
      return;
    }

    var escala = LARGURA_PROCESSAMENTO / video.videoWidth;
    canvas.width = LARGURA_PROCESSAMENTO;
    canvas.height = video.videoHeight * escala;
    contexto.drawImage(video, 0, 0, canvas.width, canvas.height);

    var imagem = contexto.getImageData(0, 0, canvas.width, canvas.height);
    var resultado = jsQR(imagem.data, imagem.width, imagem.height);

    if (resultado && resultado.data) {
      var codigo = resultado.data.trim();
      var agora = Date.now();
      if (codigo === ultimoCodigoLido && (agora - ultimoCodigoLidoEm) < RESFRIAMENTO_MESMO_CODIGO_MS) {
        return;
      }
      ultimoCodigoLido = codigo;
      ultimoCodigoLidoEm = agora;
      processarCodigo(codigo);
    }
  }

  function mostrarErroCamera(mensagem) {
    erroCamera.textContent = mensagem;
    erroCamera.classList.remove("oculto");
    telaCamera.classList.add("oculto");
    telaInicial.classList.remove("oculto");
  }

  var MENSAGENS_ERRO_CAMERA = {
    NotAllowedError: "O navegador bloqueou a câmera. Toque no cadeado da barra de endereço, permita a câmera e toque em Iniciar de novo.",
    NotFoundError: "Nenhuma câmera foi encontrada neste aparelho.",
    NotReadableError: "A câmera está sendo usada por outro programa. Feche-o e toque em Iniciar de novo.",
  };

  function manterTelaLigada() {
    // Sem isso a tela do tablet apaga, a câmera para e o totem fica em "Aproxime seu crachá" sem ler nada.
    if (!("wakeLock" in navigator)) return;
    navigator.wakeLock.request("screen").catch(function () {});
  }

  function abrirCamera() {
    // A câmera só funciona em "contexto seguro" (localhost ou HTTPS). Pelo IP da rede, sem o
    // ajuste do Chrome descrito no LEIAME, navigator.mediaDevices nem existe.
    if (!window.isSecureContext || !navigator.mediaDevices) {
      mostrarErroCamera(
        "O navegador não libera a câmera neste endereço (" + location.host + "). " +
        "Veja a seção USAR UM TABLET NO TOTEM do LEIAME: o ajuste do Chrome precisa ter exatamente este endereço."
      );
      return;
    }
    navigator.mediaDevices
      .getUserMedia({ video: { facingMode: "user" }, audio: false })
      .then(function (stream) {
        stream.getVideoTracks().forEach(function (trilha) {
          trilha.addEventListener("ended", function () {
            if (!document.hidden) abrirCamera();
          });
        });
        video.srcObject = stream;
        return video.play();
      })
      .then(function () {
        if (document.documentElement.requestFullscreen && !document.fullscreenElement) {
          document.documentElement.requestFullscreen().catch(function () {});
        }
        erroCamera.classList.add("oculto");
        telaInicial.classList.add("oculto");
        telaCamera.classList.remove("oculto");
        manterTelaLigada();
        if (!loopRodando) {
          loopRodando = true;
          requestAnimationFrame(loop);
        }
      })
      .catch(function (erro) {
        mostrarErroCamera(
          MENSAGENS_ERRO_CAMERA[erro && erro.name] ||
          "Não foi possível acessar a câmera. Toque em Iniciar para tentar de novo."
        );
      });
  }

  document.addEventListener("visibilitychange", function () {
    if (document.hidden || telaCamera.classList.contains("oculto")) return;
    // Voltando de tela apagada/aba suspensa: o bloqueio de tela cai sozinho e a câmera
    // pode ter morrido — renova os dois.
    manterTelaLigada();
    var stream = video.srcObject;
    var viva = stream && stream.getVideoTracks().some(function (t) { return t.readyState === "live"; });
    if (!viva) abrirCamera();
  });

  // Engrenagem: um toque por engano tirava o totem do ar. Exige dois toques em até 3s.
  var primeiroToqueAdmin = 0;
  linkAdmin.addEventListener("click", function (evento) {
    var agora = Date.now();
    if (agora - primeiroToqueAdmin > 3000) {
      evento.preventDefault();
      primeiroToqueAdmin = agora;
      linkAdmin.classList.add("kiosk-admin-link-armado");
      setTimeout(function () { linkAdmin.classList.remove("kiosk-admin-link-armado"); }, 3000);
    }
  });

  botaoIniciar.addEventListener("click", abrirCamera);
})();
