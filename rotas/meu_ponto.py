"""Meu Ponto: o servidor consulta o próprio ponto e pede justificativas; o chefe do setor
(perfil Direção preso ao setor) ou o RH aprovam ou recusam. Aprovada vira linha em `ausencias`."""

import secrets
import sqlite3
from datetime import date, datetime, timedelta
from functools import wraps

from flask import abort, flash, redirect, render_template, request, send_file, session, url_for
from werkzeug.security import check_password_hash, generate_password_hash

import db
import nucleo
import relatorios_service
from nucleo import (
    BLOQUEIO_LOGIN_MINUTOS,
    MAX_TENTATIVAS_LOGIN,
    NOMES_TIPO_AUSENCIA,
    PERFIS_GESTAO,
    PERFIS_LEITURA,
    app,
    eh_erro_mes_fechado,
    login_obrigatorio,
    perfil_obrigatorio,
    registrar_auditoria,
)
from rotas.funcionarios import NOMES_MESES, processar_documento_upload

DIGITOS_PIN = 6
# Até quantos dias para trás/para frente o servidor pode pedir justificativa.
JANELA_PEDIDO_DIAS = 60


def _agora():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def servidor_obrigatorio(f):
    @wraps(f)
    def decorado(*args, **kwargs):
        fid = session.get("servidor_id")
        if not fid:
            return redirect(url_for("meu_ponto_entrar"))
        conn = db.get_db()
        try:
            linha = conn.execute(
                "SELECT pin_hash, situacao FROM funcionarios WHERE id = ?", (fid,)
            ).fetchone()
        finally:
            conn.close()
        # PIN trocado pelo RH (ou servidor desligado) derruba a sessão aberta na hora.
        if not linha or linha["situacao"] == "inativo" or not linha["pin_hash"] \
                or linha["pin_hash"][-16:] != session.get("servidor_pin"):
            session.clear()
            flash("Sua sessão terminou. Entre de novo.", "erro")
            return redirect(url_for("meu_ponto_entrar"))
        return f(*args, **kwargs)

    return decorado


@app.route("/meu-ponto/entrar", methods=["GET", "POST"])
def meu_ponto_entrar():
    if request.method == "GET":
        return render_template("meu_ponto_entrar.html")

    matricula = request.form.get("matricula", "").strip()
    pin = request.form.get("pin", "").strip()
    chave = f"servidor:{matricula}"
    ip = request.remote_addr or "?"
    agora = datetime.now()
    conn = db.get_db()
    try:
        # Mesma tabela de bloqueio do login do painel: 5 erros por matrícula e aparelho = 15 min.
        # ponytail: bloqueio por aparelho; se alguém testar PINs de muitos IPs, somar por matrícula.
        bloqueio = conn.execute(
            "SELECT tentativas, bloqueado_ate FROM bloqueios_login WHERE usuario = ? AND ip = ?", (chave, ip)
        ).fetchone()
        if bloqueio and bloqueio["bloqueado_ate"] and agora < datetime.strptime(bloqueio["bloqueado_ate"], "%Y-%m-%d %H:%M:%S"):
            flash(f"Muitas tentativas erradas. Tente de novo em {BLOQUEIO_LOGIN_MINUTOS} minutos.", "erro")
            return render_template("meu_ponto_entrar.html", matricula=matricula)

        candidatos = conn.execute(
            "SELECT id, nome, pin_hash FROM funcionarios WHERE matricula = ? AND pin_hash IS NOT NULL AND situacao != 'inativo'",
            (matricula,),
        ).fetchall() if matricula and pin else []
        achado = next((c for c in candidatos if check_password_hash(c["pin_hash"], pin)), None)

        if achado:
            conn.execute("DELETE FROM bloqueios_login WHERE usuario = ? AND ip = ?", (chave, ip))
            conn.commit()
            session.clear()
            session.permanent = True
            session["servidor_id"] = achado["id"]
            session["servidor_pin"] = achado["pin_hash"][-16:]
            session["usuario_login"] = chave  # é o que a auditoria grava como autor
            session["usuario_perfil"] = "servidor"
            registrar_auditoria(conn, "login_meu_ponto", "funcionario", achado["id"])
            return redirect(url_for("meu_ponto"))

        tentativas = ((bloqueio["tentativas"] if bloqueio else 0) or 0) + 1
        bloqueado_ate = None
        if tentativas >= MAX_TENTATIVAS_LOGIN:
            bloqueado_ate = (agora + timedelta(minutes=BLOQUEIO_LOGIN_MINUTOS)).strftime("%Y-%m-%d %H:%M:%S")
        conn.execute(
            """INSERT INTO bloqueios_login (usuario, ip, tentativas, bloqueado_ate) VALUES (?, ?, ?, ?)
               ON CONFLICT (usuario, ip) DO UPDATE SET tentativas = excluded.tentativas,
                                                      bloqueado_ate = excluded.bloqueado_ate""",
            (chave, ip, 0 if bloqueado_ate else tentativas, bloqueado_ate),
        )
        conn.commit()
    finally:
        conn.close()
    flash("Matrícula ou PIN incorretos.", "erro")
    return render_template("meu_ponto_entrar.html", matricula=matricula)


@app.route("/meu-ponto/sair")
def meu_ponto_sair():
    session.clear()
    return redirect(url_for("meu_ponto_entrar"))


@app.route("/meu-ponto")
@servidor_obrigatorio
def meu_ponto():
    hoje = date.today()
    try:
        ano, mes = (int(x) for x in (request.args.get("mes") or hoje.strftime("%Y-%m")).split("-"))
        date(ano, mes, 1)
    except ValueError:
        ano, mes = hoje.year, hoje.month
    conn = db.get_db()
    try:
        ficha = relatorios_service.montar_ficha_mes(conn, session["servidor_id"], ano, mes)
        if ficha is None:
            abort(404)
        solicitacoes = conn.execute(
            "SELECT * FROM solicitacoes_justificativa WHERE funcionario_id = ? ORDER BY id DESC LIMIT 20",
            (session["servidor_id"],),
        ).fetchall()
        anterior = (date(ano, mes, 1) - timedelta(days=1)).strftime("%Y-%m")
        proximo = (date(ano, mes, 28) + timedelta(days=4)).strftime("%Y-%m")
        return render_template(
            "meu_ponto.html",
            f=ficha["funcionario"],
            ficha=ficha,
            fh=relatorios_service.formatar_horas,
            saldo_banco=relatorios_service.calcular_saldo_banco_horas_ate(conn, ficha["funcionario"], hoje),
            solicitacoes=solicitacoes,
            nome_mes=NOMES_MESES[mes - 1],
            ano=ano,
            mes_anterior=anterior,
            mes_proximo=proximo if proximo <= hoje.strftime("%Y-%m") else None,
            nomes_tipo_ausencia=NOMES_TIPO_AUSENCIA,
            hoje=hoje.isoformat(),
            data_min=(hoje - timedelta(days=JANELA_PEDIDO_DIAS)).isoformat(),
            data_max=(hoje + timedelta(days=JANELA_PEDIDO_DIAS)).isoformat(),
        )
    finally:
        conn.close()


@app.route("/meu-ponto/justificativa", methods=["POST"])
@servidor_obrigatorio
def meu_ponto_pedir_justificativa():
    fid = session["servidor_id"]
    data_txt = request.form.get("data", "").strip()
    tipo = request.form.get("tipo", "").strip()
    motivo = request.form.get("motivo", "").strip()[:500]
    try:
        dia = date.fromisoformat(data_txt)
    except ValueError:
        dia = None
    hoje = date.today()
    if not dia or abs((dia - hoje).days) > JANELA_PEDIDO_DIAS or tipo not in db.TIPOS_AUSENCIA_VALIDOS or not motivo:
        flash(f"Preencha a data (até {JANELA_PEDIDO_DIAS} dias), o tipo e o motivo.", "erro")
        return redirect(url_for("meu_ponto"))

    conn = db.get_db()
    try:
        if conn.execute("SELECT 1 FROM meses_fechados WHERE mes = ?", (data_txt[:7],)).fetchone():
            flash("Esse mês já foi fechado pelo RH. Fale direto com o RH.", "erro")
            return redirect(url_for("meu_ponto"))
        if conn.execute(
            """SELECT 1 FROM solicitacoes_justificativa WHERE funcionario_id = ? AND data = ? AND status = 'pendente'
               UNION SELECT 1 FROM ausencias WHERE funcionario_id = ? AND data = ?""",
            (fid, data_txt, fid, data_txt),
        ).fetchone():
            flash("Esse dia já tem justificativa ou pedido esperando resposta.", "erro")
            return redirect(url_for("meu_ponto"))

        anexo_nome = anexo_caminho = None
        arquivo = request.files.get("arquivo")
        if arquivo and arquivo.filename:
            try:
                anexo_nome, anexo_caminho, _ = processar_documento_upload(arquivo, fid)
            except ValueError as erro:
                flash(str(erro), "erro")
                return redirect(url_for("meu_ponto"))

        cursor = conn.execute(
            """INSERT INTO solicitacoes_justificativa
                   (funcionario_id, data, tipo, motivo, anexo_nome, anexo_caminho, criado_em)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (fid, data_txt, tipo, motivo, anexo_nome, anexo_caminho, _agora()),
        )
        conn.commit()
        registrar_auditoria(conn, "pedir_justificativa", "solicitacao", cursor.lastrowid, f"data={data_txt}; tipo={tipo}")
    finally:
        conn.close()
    flash("Pedido enviado. Seu chefe ou o RH vai responder por aqui.", "sucesso")
    return redirect(url_for("meu_ponto"))


# --- Lado do painel ----------------------------------------------------------------------

@app.context_processor
def injetar_pedidos_pendentes():
    if not session.get("usuario_id") or session.get("usuario_perfil") not in PERFIS_LEITURA:
        return {}
    conn = db.get_db()  # chefe de setor: a view já filtra a equipe dele
    try:
        n = conn.execute(
            """SELECT COUNT(*) AS n FROM solicitacoes_justificativa s
               JOIN funcionarios ON funcionarios.id = s.funcionario_id WHERE s.status = 'pendente'"""
        ).fetchone()["n"]
    finally:
        conn.close()
    return {"pedidos_pendentes": n}


SQL_SOLICITACOES = """
    SELECT s.*, funcionarios.nome AS funcionario_nome, funcionarios.matricula, setores.nome AS setor_nome
    FROM solicitacoes_justificativa s
    JOIN funcionarios ON funcionarios.id = s.funcionario_id
    LEFT JOIN setores ON setores.id = funcionarios.setor_id
"""


@app.route("/admin/solicitacoes")
@login_obrigatorio
@perfil_obrigatorio(*PERFIS_LEITURA)
def solicitacoes():
    conn = db.get_db()
    try:
        pendentes = conn.execute(SQL_SOLICITACOES + " WHERE s.status = 'pendente' ORDER BY s.data").fetchall()
        decididas = conn.execute(
            SQL_SOLICITACOES + " WHERE s.status != 'pendente' ORDER BY s.decidido_em DESC LIMIT 30"
        ).fetchall()
    finally:
        conn.close()
    return render_template(
        "solicitacoes.html", pendentes=pendentes, decididas=decididas, nomes_tipo_ausencia=NOMES_TIPO_AUSENCIA
    )


def _solicitacao_visivel(conn, solicitacao_id):
    """None se não existe ou é de outro setor (a view de funcionarios corta no JOIN)."""
    return conn.execute(SQL_SOLICITACOES + " WHERE s.id = ?", (solicitacao_id,)).fetchone()


@app.route("/admin/solicitacoes/<int:solicitacao_id>/anexo")
@login_obrigatorio
@perfil_obrigatorio(*PERFIS_LEITURA)
def anexo_solicitacao(solicitacao_id):
    conn = db.get_db()
    try:
        s = _solicitacao_visivel(conn, solicitacao_id)
    finally:
        conn.close()
    if not s or not s["anexo_caminho"]:
        abort(404)
    return send_file(nucleo.DOCUMENTOS_DIR / s["anexo_caminho"], download_name=s["anexo_nome"])


@app.route("/admin/solicitacoes/<int:solicitacao_id>/decidir", methods=["POST"])
@login_obrigatorio
@perfil_obrigatorio(*PERFIS_LEITURA)
def decidir_solicitacao(solicitacao_id):
    aprovar = request.form.get("acao") == "aprovar"
    resposta = request.form.get("resposta", "").strip()[:500]
    if not aprovar and not resposta:
        flash("Para recusar, escreva o motivo — o servidor vai ler.", "erro")
        return redirect(url_for("solicitacoes"))

    conn = db.get_db()
    try:
        s = _solicitacao_visivel(conn, solicitacao_id)
        if not s:
            abort(404)
        quem = session.get("usuario_login")
        # O WHERE status = 'pendente' impede que dois chefes decidam o mesmo pedido.
        mudou = conn.execute(
            """UPDATE solicitacoes_justificativa SET status = ?, decidido_por = ?, decidido_em = ?, resposta = ?
               WHERE id = ? AND status = 'pendente'""",
            ("aprovada" if aprovar else "recusada", quem, _agora(), resposta or None, solicitacao_id),
        ).rowcount
        if not mudou:
            conn.rollback()
            flash("Esse pedido já foi respondido.", "erro")
            return redirect(url_for("solicitacoes"))
        if aprovar:
            try:
                ausencia_id = conn.execute(
                    "INSERT INTO ausencias (funcionario_id, data, tipo, observacao, criado_por, criado_em) VALUES (?, ?, ?, ?, ?, ?)",
                    (s["funcionario_id"], s["data"], s["tipo"], f"Pedido pelo servidor: {s['motivo']}", quem, _agora()),
                ).lastrowid
                if s["anexo_caminho"]:
                    conn.execute(
                        """INSERT INTO documentos (funcionario_id, ausencia_id, tipo, nome_arquivo, caminho_arquivo,
                                                   criado_por, criado_em) VALUES (?, ?, 'atestado', ?, ?, ?, ?)""",
                        (s["funcionario_id"], ausencia_id, s["anexo_nome"], s["anexo_caminho"], quem, _agora()),
                    )
            except sqlite3.IntegrityError as erro:
                conn.rollback()
                if eh_erro_mes_fechado(erro):
                    raise
                flash("Já existe justificativa nesse dia. Recuse este pedido ou ajuste a existente.", "erro")
                return redirect(url_for("solicitacoes"))
        conn.commit()
        registrar_auditoria(
            conn, "aprovar_solicitacao" if aprovar else "recusar_solicitacao", "solicitacao", solicitacao_id,
            f"funcionario={s['funcionario_nome']}; data={s['data']}; tipo={s['tipo']}",
        )
    finally:
        conn.close()
    flash("Justificativa aprovada." if aprovar else "Pedido recusado.", "sucesso")
    return redirect(url_for("solicitacoes"))


@app.route("/admin/funcionarios/<int:funcionario_id>/pin-meu-ponto", methods=["POST"])
@login_obrigatorio
@perfil_obrigatorio(*PERFIS_GESTAO)
def gerar_pin_meu_ponto(funcionario_id):
    """Gera um PIN novo (o anterior para de valer e derruba sessões abertas). Mostrado uma vez só."""
    pin = f"{secrets.randbelow(10 ** DIGITOS_PIN):0{DIGITOS_PIN}d}"
    conn = db.get_db()
    try:
        f = conn.execute("SELECT id, nome, matricula FROM funcionarios WHERE id = ?", (funcionario_id,)).fetchone()
        if not f:
            abort(404)
        if not f["matricula"]:
            flash("Cadastre a matrícula da pessoa antes: é com ela que se entra no Meu Ponto.", "erro")
            return redirect(url_for("ficha_funcionario", funcionario_id=funcionario_id))
        conn.execute("UPDATE funcionarios SET pin_hash = ? WHERE id = ?", (generate_password_hash(pin), funcionario_id))
        conn.commit()
        registrar_auditoria(conn, "gerar_pin_meu_ponto", "funcionario", funcionario_id)
        config = conn.execute("SELECT * FROM configuracoes WHERE id = 1").fetchone()
    finally:
        conn.close()
    return render_template("pin_meu_ponto.html", f=f, pin=pin, config=config)
