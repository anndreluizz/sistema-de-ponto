"""Totem de batida de ponto e arquivos privados (fotos, QR Codes)."""

import base64
import hashlib
import hmac
from datetime import datetime
from pathlib import Path
from flask import abort, jsonify, redirect, render_template, request, send_file, send_from_directory, url_for
import db

import nucleo
from nucleo import (
    COOKIE_TOTEM,
    COOLDOWN_SEGUNDOS,
    PERFIS_GESTAO,
    PERFIS_SOMENTE_ADMIN,
    TAMANHO_MAX_FOTO_BATIDA,
    _caminho_foto_batida,
    app,
    csrf,
    login_obrigatorio,
    perfil_obrigatorio,
    registrar_auditoria,
    tem_foto_batida,
)


@app.route("/")
def kiosk():
    conn = db.get_db()
    try:
        config = conn.execute("SELECT * FROM configuracoes WHERE id = 1").fetchone()
    finally:
        conn.close()
    return render_template("kiosk.html", config=config, totem_autorizado=totem_autorizado())


def _token_totem():
    # ponytail: token derivado da secret_key — revogar um totem perdido exige apagar .secret_key
    # (desloga todos e invalida todos os totens); tabela de totens se precisar revogar um só.
    return hmac.new(app.config["SECRET_KEY"].encode(), b"totem", hashlib.sha256).hexdigest()


def totem_autorizado():
    return hmac.compare_digest(request.cookies.get(COOKIE_TOTEM, ""), _token_totem())


@app.route("/admin/totem/autorizar", methods=["POST"])
@login_obrigatorio
@perfil_obrigatorio(*PERFIS_SOMENTE_ADMIN)
def autorizar_totem():
    conn = db.get_db()
    try:
        registrar_auditoria(conn, "autorizar_totem", "totem", detalhes=f"ip={request.remote_addr}")
    finally:
        conn.close()
    resposta = redirect(url_for("kiosk"))
    resposta.set_cookie(
        COOKIE_TOTEM, _token_totem(), max_age=10 * 365 * 24 * 3600, httponly=True, samesite="Lax"
    )
    return resposta


@app.route("/admin/arquivos/<pasta>/<nome>")
@login_obrigatorio
def arquivo_privado(pasta, nome):
    pastas = {"fotos": nucleo.FOTOS_DIR, "qrcodes": nucleo.QRCODES_DIR}
    if pasta not in pastas:
        abort(404)
    return send_from_directory(pastas[pasta], nome)


def _foto_como_data_uri(foto):
    """O totem não tem login, então recebe a foto embutida na resposta em vez de uma URL."""
    caminho = nucleo.FOTOS_DIR / Path(foto).name
    if not caminho.is_file():
        return None
    return "data:image/jpeg;base64," + base64.b64encode(caminho.read_bytes()).decode()


def salvar_foto_batida(registro_id, timestamp, foto_data_uri):
    """Foto tirada pelo totem no instante da batida — pra conferir crachá emprestado. Nunca
    impede a batida: foto ausente, inválida ou erro de disco só fica sem foto."""
    prefixo = "data:image/jpeg;base64,"
    if not isinstance(foto_data_uri, str) or not foto_data_uri.startswith(prefixo):
        return
    try:
        conteudo = base64.b64decode(foto_data_uri[len(prefixo):], validate=True)
        if len(conteudo) > TAMANHO_MAX_FOTO_BATIDA or not conteudo.startswith(b"\xff\xd8"):
            return
        caminho = _caminho_foto_batida(registro_id, timestamp)
        caminho.parent.mkdir(parents=True, exist_ok=True)
        caminho.write_bytes(conteudo)
    except Exception:
        app.logger.exception("Falha ao salvar foto da batida %s", registro_id)


@app.route("/admin/registros/<int:registro_id>/foto")
@login_obrigatorio
@perfil_obrigatorio(*PERFIS_GESTAO)
def foto_batida(registro_id):
    conn = db.get_db()
    try:
        registro = conn.execute("SELECT id, timestamp FROM registros WHERE id = ?", (registro_id,)).fetchone()
    finally:
        conn.close()
    if not registro or not tem_foto_batida(registro):
        abort(404)
    return send_file(_caminho_foto_batida(registro["id"], registro["timestamp"]), mimetype="image/jpeg")


@app.route("/api/bater-ponto", methods=["POST"])
@csrf.exempt
def api_bater_ponto():
    if not totem_autorizado():
        return jsonify({"ok": False, "erro": "Este aparelho não está autorizado como totem."}), 403
    dados = request.get_json(silent=True) or {}
    codigo = (dados.get("codigo") or "").strip()
    if not codigo:
        return jsonify({"ok": False, "erro": "Código vazio."}), 400

    conn = db.get_db()
    try:
        funcionario = conn.execute("SELECT * FROM funcionarios WHERE codigo = ?", (codigo,)).fetchone()
        if not funcionario:
            return jsonify({"ok": False, "erro": "Crachá não reconhecido."}), 404

        if funcionario["situacao"] != "ativo":
            mensagens = {
                "inativo": "Funcionário inativo. Procure a coordenação.",
                "ferias": "Funcionário em férias.",
                "afastado": "Funcionário afastado. Procure a coordenação.",
            }
            return (
                jsonify(
                    {
                        "ok": False,
                        "erro": mensagens.get(funcionario["situacao"], "Funcionário não está ativo."),
                        "motivo": funcionario["situacao"],
                    }
                ),
                403,
            )

        # Trava de escrita antes de ler a última batida: duas leituras simultâneas do mesmo
        # crachá (ex.: dois aparelhos) não conseguem passar as duas pelo cooldown.
        conn.execute("BEGIN IMMEDIATE")
        agora = datetime.now()
        inicio_do_dia = agora.strftime("%Y-%m-%d") + " 00:00:00"
        ultimo = conn.execute(
            """SELECT * FROM registros
               WHERE funcionario_id = ? AND timestamp >= ?
               ORDER BY timestamp DESC LIMIT 1""",
            (funcionario["id"], inicio_do_dia),
        ).fetchone()

        if ultimo:
            ultimo_ts = datetime.strptime(ultimo["timestamp"], "%Y-%m-%d %H:%M:%S")
            if (agora - ultimo_ts).total_seconds() < COOLDOWN_SEGUNDOS:
                return jsonify({"ok": False, "erro": "Ponto já registrado agora há pouco."}), 429

        tipo = "entrada" if (ultimo is None or ultimo["tipo"] == "saida") else "saida"
        timestamp_str = agora.strftime("%Y-%m-%d %H:%M:%S")

        cursor = conn.execute(
            "INSERT INTO registros (funcionario_id, tipo, timestamp, manual) VALUES (?, ?, ?, 0)",
            (funcionario["id"], tipo, timestamp_str),
        )
        conn.commit()
        salvar_foto_batida(cursor.lastrowid, timestamp_str, dados.get("foto"))

        return jsonify(
            {
                "ok": True,
                "nome": funcionario["nome"],
                "cargo": funcionario["cargo"],
                "foto": _foto_como_data_uri(funcionario["foto"]) if funcionario["foto"] else None,
                "tipo": tipo,
                "horario": agora.strftime("%H:%M:%S"),
            }
        )
    finally:
        conn.close()
