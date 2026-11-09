"""Totem de batida de ponto e arquivos privados (fotos, QR Codes)."""

import base64
import hashlib
import secrets
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from flask import (
    abort, flash, jsonify, redirect, render_template, request, send_file, send_from_directory, session, url_for,
)
import db

import nucleo
import relatorios_service
from nucleo import (
    COOKIE_TOTEM,
    COOLDOWN_SEGUNDOS,
    PERFIS_GESTAO,
    PERFIS_LEITURA,
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


def _hash_token(token):
    return hashlib.sha256(token.encode()).hexdigest()


def totem_autorizado():
    """Id do totem deste aparelho, ou None. Só o hash do token fica no banco; revogado = fora."""
    token = request.cookies.get(COOKIE_TOTEM, "")
    if not token:
        return None
    conn = db.get_db()
    try:
        linha = conn.execute(
            "SELECT id FROM totens WHERE token_hash = ? AND revogado_em IS NULL", (_hash_token(token),)
        ).fetchone()
    finally:
        conn.close()
    return linha["id"] if linha else None


@app.route("/admin/totem/autorizar", methods=["POST"])
@login_obrigatorio
@perfil_obrigatorio(*PERFIS_SOMENTE_ADMIN)
def autorizar_totem():
    token = secrets.token_urlsafe(32)
    agora = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    nome = request.form.get("nome", "").strip()[:60] or f"Totem de {agora[:10]}"
    conn = db.get_db()
    try:
        cursor = conn.execute(
            "INSERT INTO totens (token_hash, nome, autorizado_por, criado_em) VALUES (?, ?, ?, ?)",
            (_hash_token(token), nome, session.get("usuario_login"), agora),
        )
        conn.commit()
        registrar_auditoria(
            conn, "autorizar_totem", "totem", cursor.lastrowid, f"nome={nome}; ip={request.remote_addr}"
        )
    finally:
        conn.close()
    resposta = redirect(url_for("kiosk"))
    # Longo de propósito: o tablet não pode "deslogar" sozinho. Revogação é pela tabela.
    resposta.set_cookie(COOKIE_TOTEM, token, max_age=10 * 365 * 24 * 3600, httponly=True, samesite="Lax")
    return resposta


@app.route("/admin/totem/<int:totem_id>/revogar", methods=["POST"])
@login_obrigatorio
@perfil_obrigatorio(*PERFIS_SOMENTE_ADMIN)
def revogar_totem(totem_id):
    conn = db.get_db()
    try:
        alterou = conn.execute(
            "UPDATE totens SET revogado_em = ? WHERE id = ? AND revogado_em IS NULL",
            (datetime.now().strftime("%Y-%m-%d %H:%M:%S"), totem_id),
        ).rowcount
        conn.commit()
        if alterou:
            registrar_auditoria(conn, "revogar_totem", "totem", totem_id)
            flash("Totem revogado. Aquele aparelho não bate mais ponto.", "sucesso")
    finally:
        conn.close()
    return redirect(url_for("configuracoes"))


@app.route("/admin/arquivos/<pasta>/<nome>")
@login_obrigatorio
@perfil_obrigatorio(*PERFIS_LEITURA)  # o QR Code bate ponto: o perfil Consulta não pode baixá-lo
def arquivo_privado(pasta, nome):
    pastas = {"fotos": nucleo.FOTOS_DIR, "qrcodes": nucleo.QRCODES_DIR}
    if pasta not in pastas:
        abort(404)
    if session.get("usuario_setor_id"):
        # Chefe de setor: os arquivos se chamam <id>.png/.jpg — sem isso dava pra baixar o
        # QR Code (que bate ponto) de quem é de outro setor só trocando o número na URL.
        conn = db.get_db()  # já vem filtrada pelo setor
        try:
            visivel = conn.execute("SELECT 1 FROM funcionarios WHERE id = ?", (Path(nome).stem,)).fetchone()
        finally:
            conn.close()
        if not visivel:
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


def _turno_passa_da_meia_noite(conn, funcionario):
    """Turno direto ou da escala com saída antes (ou igual) da entrada, ex.: 07:00–07:00."""
    turno = conn.execute(
        """SELECT horario_entrada, horario_saida FROM turnos
           WHERE id = COALESCE(?, (SELECT turno_id FROM escalas WHERE id = ?))""",
        (funcionario["turno_id"], funcionario["escala_id"]),
    ).fetchone()
    return turno is not None and turno["horario_saida"] <= turno["horario_entrada"]


# Quanto tempo o totem pode ficar sem internet guardando batidas (fim de semana prolongado).
LIMITE_OFFLINE_SEGUNDOS = 4 * 24 * 3600


@app.route("/api/bater-ponto", methods=["POST"])
@csrf.exempt
def api_bater_ponto():
    totem_id = totem_autorizado()
    if not totem_id:
        return jsonify({"ok": False, "erro": "Este aparelho não está autorizado como totem."}), 403
    dados = request.get_json(silent=True) or {}
    codigo = (dados.get("codigo") or "").strip()
    if not codigo:
        return jsonify({"ok": False, "erro": "Código vazio."}), 400
    # Batida guardada no tablet enquanto a internet estava fora: ele manda há quantos segundos
    # leu o crachá (diferença medida no próprio tablet), não a hora do relógio dele — atrasar o
    # relógio do tablet não muda nada. A hora gravada é a do servidor menos essa idade.
    idade = dados.get("idade_segundos")
    if idade is not None:
        if not isinstance(idade, (int, float)) or not 0 <= idade <= LIMITE_OFFLINE_SEGUNDOS:
            return jsonify({"ok": False, "erro": "Batida guardada há tempo demais."}), 400

    conn = db.get_db()
    try:
        conn.execute(
            "UPDATE totens SET ultimo_uso = ? WHERE id = ?",
            (datetime.now().strftime("%Y-%m-%d %H:%M:%S"), totem_id),
        )
        conn.commit()
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
        agora = datetime.now().replace(microsecond=0) - timedelta(seconds=int(idade or 0))
        inicio_do_dia = agora.strftime("%Y-%m-%d") + " 00:00:00"
        agora_str = agora.strftime("%Y-%m-%d %H:%M:%S")
        ultimo = conn.execute(
            """SELECT * FROM registros
               WHERE funcionario_id = ? AND timestamp >= ? AND timestamp <= ?
               ORDER BY timestamp DESC LIMIT 1""",
            (funcionario["id"], inicio_do_dia, agora_str),
        ).fetchone()

        if ultimo is None and _turno_passa_da_meia_noite(conn, funcionario):
            # Plantão (ex.: vigia 07:00–07:00): a entrada de ontem ainda aberta é fechada agora.
            vespera = conn.execute(
                """SELECT * FROM registros
                   WHERE funcionario_id = ? AND timestamp >= ? AND timestamp < ?
                   ORDER BY timestamp DESC LIMIT 1""",
                (
                    funcionario["id"],
                    (agora - timedelta(hours=relatorios_service.LIMITE_PLANTAO_HORAS)).strftime("%Y-%m-%d %H:%M:%S"),
                    inicio_do_dia,
                ),
            ).fetchone()
            if vespera is not None and vespera["tipo"] == "entrada":
                ultimo = vespera

        if ultimo:
            ultimo_ts = datetime.strptime(ultimo["timestamp"], "%Y-%m-%d %H:%M:%S")
            if (agora - ultimo_ts).total_seconds() < COOLDOWN_SEGUNDOS:
                return jsonify({"ok": False, "erro": "Ponto já registrado agora há pouco."}), 429

        tipo = "entrada" if (ultimo is None or ultimo["tipo"] == "saida") else "saida"
        timestamp_str = agora_str
        motivo = None
        if idade:
            motivo = f"Batida feita sem internet no totem; chegou ao servidor {int(idade) // 60} min depois"

        try:
            cursor = conn.execute(
                "INSERT INTO registros (funcionario_id, tipo, timestamp, manual, motivo) VALUES (?, ?, ?, 0, ?)",
                (funcionario["id"], tipo, timestamp_str, motivo),
            )
            conn.commit()
        except sqlite3.IntegrityError:  # mês já fechado pelo RH
            conn.rollback()
            return jsonify({"ok": False, "erro": "O mês dessa batida já foi fechado."}), 409
        salvar_foto_batida(cursor.lastrowid, timestamp_str, dados.get("foto"))

        return jsonify(
            {
                "ok": True,
                "nome": funcionario["nome"],
                "cargo": funcionario["cargo"],
                "foto": _foto_como_data_uri(funcionario["foto"]) if funcionario["foto"] else None,
                "tipo": tipo,
                "horario": agora.strftime("%H:%M:%S"),
                "offline": bool(idade),
            }
        )
    finally:
        conn.close()
