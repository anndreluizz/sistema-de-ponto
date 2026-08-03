(function () {
  "use strict";

  var telaInicial = document.getElementById("tela-inicial");
  var telaCamera = document.getElementById("tela-camera");
  var telaFeedback = document.getElementById("tela-feedback");
  var botaoIniciar = document.getElementById("botao-iniciar");
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
  var RESFRIAMENTO_MESMO_CODIGO_MS = 5000;

  var ocupado = false;
  var audioContexto = null;
  var ultimoCodigoLido = null;
  var ultimoCodigoLidoEm = 0;

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
    }, DURACAO_FEEDBACK_MS);
  }

  function processarCodigo(codigo) {
    ocupado = true;
    fetch("/api/bater-ponto", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ codigo: codigo }),
    })
      .then(function (resposta) {
        return resposta.json();
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

  function iniciar() {
    navigator.mediaDevices
      .getUserMedia({ video: { facingMode: "environment" }, audio: false })
      .then(function (stream) {
        video.srcObject = stream;
        return video.play();
      })
      .then(function () {
        if (document.documentElement.requestFullscreen) {
          document.documentElement.requestFullscreen().catch(function () {});
        }
        telaInicial.classList.add("oculto");
        telaCamera.classList.remove("oculto");
        requestAnimationFrame(loop);
      })
      .catch(function () {
        alert("Não foi possível acessar a câmera. Verifique as permissões do navegador e tente novamente.");
      });
  }

  botaoIniciar.addEventListener("click", iniciar);
})();
