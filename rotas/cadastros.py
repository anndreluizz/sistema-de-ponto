"""Cadastros de apoio: setores, feriados, turnos e escalas."""

import sqlite3
from datetime import datetime
from flask import flash, redirect, render_template, request, session, url_for
import db
import relatorios_service

from nucleo import (
    ICONES_SETOR_DISPONIVEIS,
    PERFIS_GESTAO,
    app,
    eh_erro_mes_fechado,
    login_obrigatorio,
    perfil_obrigatorio,
    registrar_auditoria,
)


def _ler_dados_setor_form():
    return {
        "nome": request.form.get("nome", "").strip(),
        "icone": request.form.get("icone", "").strip() or None,
        "cor": request.form.get("cor", "").strip() or None,
        "responsavel": request.form.get("responsavel", "").strip() or None,
        "centro_custo": request.form.get("centro_custo", "").strip() or None,
    }


def _validar_dados_setor(dados):
    if not dados["nome"]:
        return "Informe o nome do setor."
    return None


@app.route("/admin/setores", methods=["GET", "POST"])
@login_obrigatorio
@perfil_obrigatorio(*PERFIS_GESTAO)
def setores():
    conn = db.get_db()
    try:
        if request.method == "POST":
            dados = _ler_dados_setor_form()
            erro = _validar_dados_setor(dados)
            if erro:
                flash(erro, "erro")
            else:
                try:
                    cursor = conn.execute(
                        """INSERT INTO setores (nome, icone, cor, responsavel, centro_custo)
                           VALUES (?, ?, ?, ?, ?)""",
                        (dados["nome"], dados["icone"], dados["cor"], dados["responsavel"], dados["centro_custo"]),
                    )
                    conn.commit()
                    registrar_auditoria(conn, "criar", "setor", cursor.lastrowid, f"nome={dados['nome']}")
                    flash("Setor adicionado.", "sucesso")
                except sqlite3.IntegrityError:
                    flash("Já existe um setor com esse nome.", "erro")
            return redirect(url_for("setores"))

        lista = conn.execute(
            """SELECT setores.*,
                      (SELECT COUNT(*) FROM funcionarios WHERE funcionarios.setor_id = setores.id) AS total_funcionarios
               FROM setores ORDER BY nome"""
        ).fetchall()
        return render_template("setores.html", setores=lista, icones_disponiveis=ICONES_SETOR_DISPONIVEIS)
    finally:
        conn.close()


@app.route("/admin/setores/<int:setor_id>/editar", methods=["POST"])
@login_obrigatorio
@perfil_obrigatorio(*PERFIS_GESTAO)
def editar_setor(setor_id):
    dados = _ler_dados_setor_form()
    erro = _validar_dados_setor(dados)
    conn = db.get_db()
    try:
        if erro:
            flash(erro, "erro")
        else:
            try:
                conn.execute(
                    """UPDATE setores SET nome = ?, icone = ?, cor = ?, responsavel = ?, centro_custo = ?
                       WHERE id = ?""",
                    (dados["nome"], dados["icone"], dados["cor"], dados["responsavel"], dados["centro_custo"], setor_id),
                )
                conn.commit()
                registrar_auditoria(conn, "editar", "setor", setor_id, f"nome={dados['nome']}")
                flash("Setor atualizado.", "sucesso")
            except sqlite3.IntegrityError:
                flash("Já existe um setor com esse nome.", "erro")
    finally:
        conn.close()
    return redirect(url_for("setores"))


@app.route("/admin/setores/<int:setor_id>/excluir", methods=["POST"])
@login_obrigatorio
@perfil_obrigatorio(*PERFIS_GESTAO)
def excluir_setor(setor_id):
    conn = db.get_db()
    try:
        setor = conn.execute("SELECT nome FROM setores WHERE id = ?", (setor_id,)).fetchone()
        try:
            conn.execute("DELETE FROM setores WHERE id = ?", (setor_id,))
            conn.commit()
            registrar_auditoria(conn, "excluir", "setor", setor_id, f"nome={setor['nome']}" if setor else None)
            flash("Setor excluído.", "sucesso")
        except sqlite3.IntegrityError:
            flash("Não é possível excluir: existem funcionários nesse setor.", "erro")
    finally:
        conn.close()
    return redirect(url_for("setores"))


def _ler_dados_feriado_form():
    return {
        "data": request.form.get("data", "").strip(),
        "nome": request.form.get("nome", "").strip(),
    }


def _validar_dados_feriado(dados):
    if not dados["data"] or not dados["nome"]:
        return "Informe a data e o nome do feriado."
    try:
        datetime.strptime(dados["data"], "%Y-%m-%d")
    except ValueError:
        return "Data inválida."
    return None


@app.route("/admin/feriados", methods=["GET", "POST"])
@login_obrigatorio
@perfil_obrigatorio(*PERFIS_GESTAO)
def feriados():
    conn = db.get_db()
    try:
        if request.method == "POST":
            dados = _ler_dados_feriado_form()
            erro = _validar_dados_feriado(dados)
            if erro:
                flash(erro, "erro")
            else:
                try:
                    cursor = conn.execute(
                        "INSERT INTO feriados (data, nome, criado_por, criado_em) VALUES (?, ?, ?, ?)",
                        (
                            dados["data"], dados["nome"], session.get("usuario_login"),
                            datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                        ),
                    )
                    conn.commit()
                    registrar_auditoria(
                        conn, "criar", "feriado", cursor.lastrowid, f"data={dados['data']}; nome={dados['nome']}"
                    )
                    flash("Feriado adicionado.", "sucesso")
                except sqlite3.IntegrityError as erro_integridade:
                    if eh_erro_mes_fechado(erro_integridade):
                        raise
                    flash("Já existe um feriado cadastrado nessa data.", "erro")
            return redirect(url_for("feriados"))

        lista = conn.execute("SELECT * FROM feriados ORDER BY data").fetchall()
        return render_template("feriados.html", feriados=lista)
    finally:
        conn.close()


@app.route("/admin/feriados/<int:feriado_id>/editar", methods=["POST"])
@login_obrigatorio
@perfil_obrigatorio(*PERFIS_GESTAO)
def editar_feriado(feriado_id):
    dados = _ler_dados_feriado_form()
    erro = _validar_dados_feriado(dados)
    conn = db.get_db()
    try:
        if erro:
            flash(erro, "erro")
        else:
            try:
                conn.execute(
                    "UPDATE feriados SET data = ?, nome = ? WHERE id = ?",
                    (dados["data"], dados["nome"], feriado_id),
                )
                conn.commit()
                registrar_auditoria(
                    conn, "editar", "feriado", feriado_id, f"data={dados['data']}; nome={dados['nome']}"
                )
                flash("Feriado atualizado.", "sucesso")
            except sqlite3.IntegrityError as erro_integridade:
                if eh_erro_mes_fechado(erro_integridade):
                    raise
                flash("Já existe um feriado cadastrado nessa data.", "erro")
    finally:
        conn.close()
    return redirect(url_for("feriados"))


@app.route("/admin/feriados/<int:feriado_id>/excluir", methods=["POST"])
@login_obrigatorio
@perfil_obrigatorio(*PERFIS_GESTAO)
def excluir_feriado(feriado_id):
    conn = db.get_db()
    try:
        feriado = conn.execute("SELECT nome, data FROM feriados WHERE id = ?", (feriado_id,)).fetchone()
        conn.execute("DELETE FROM feriados WHERE id = ?", (feriado_id,))
        conn.commit()
        registrar_auditoria(
            conn, "excluir", "feriado", feriado_id,
            f"data={feriado['data']}; nome={feriado['nome']}" if feriado else None,
        )
        flash("Feriado excluído.", "sucesso")
    finally:
        conn.close()
    return redirect(url_for("feriados"))


def _ler_dados_turno_form():
    dias_semana_lista = request.form.getlist("dias_semana")
    return {
        "nome": request.form.get("nome", "").strip(),
        "horario_entrada": request.form.get("horario_entrada", "").strip(),
        "horario_saida": request.form.get("horario_saida", "").strip(),
        "tolerancia_minutos": request.form.get("tolerancia_minutos", "10").strip(),
        "tolerancia_saida_minutos": request.form.get("tolerancia_saida_minutos", "10").strip(),
        "dias_semana": ",".join(dias_semana_lista) if dias_semana_lista else None,
        "horario_saida_almoco": request.form.get("horario_saida_almoco", "").strip() or None,
        "horario_retorno_almoco": request.form.get("horario_retorno_almoco", "").strip() or None,
    }


def _validar_dados_turno(dados):
    if not dados["nome"] or not dados["horario_entrada"] or not dados["horario_saida"]:
        return "Preencha nome, horário de entrada e de saída."
    if dados["horario_saida"] <= dados["horario_entrada"]:
        # ponytail: turno que vira a meia-noite não é suportado (totem e cálculos agrupam por data).
        return "O horário de saída precisa ser depois da entrada (turno que passa da meia-noite não é suportado)."
    if not dados["dias_semana"]:
        return "Marque pelo menos um dia da semana."
    if bool(dados["horario_saida_almoco"]) != bool(dados["horario_retorno_almoco"]):
        return "Preencha os dois horários de almoço, ou deixe os dois em branco."
    if dados["horario_saida_almoco"] and dados["horario_retorno_almoco"]:
        if dados["horario_retorno_almoco"] <= dados["horario_saida_almoco"]:
            return "O horário de retorno do almoço precisa ser depois da saída para o almoço."
    return None


@app.route("/admin/turnos", methods=["GET", "POST"])
@login_obrigatorio
@perfil_obrigatorio(*PERFIS_GESTAO)
def turnos():
    conn = db.get_db()
    try:
        if request.method == "POST":
            dados = _ler_dados_turno_form()
            erro = _validar_dados_turno(dados)
            if erro:
                flash(erro, "erro")
            else:
                cursor = conn.execute(
                    """INSERT INTO turnos
                       (nome, horario_entrada, horario_saida, tolerancia_minutos, tolerancia_saida_minutos,
                        dias_semana, horario_saida_almoco, horario_retorno_almoco)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        dados["nome"],
                        dados["horario_entrada"],
                        dados["horario_saida"],
                        int(dados["tolerancia_minutos"] or 10),
                        int(dados["tolerancia_saida_minutos"] or 10),
                        dados["dias_semana"],
                        dados["horario_saida_almoco"],
                        dados["horario_retorno_almoco"],
                    ),
                )
                conn.commit()
                registrar_auditoria(conn, "criar", "turno", cursor.lastrowid, f"nome={dados['nome']}")
                flash("Turno adicionado.", "sucesso")
            return redirect(url_for("turnos"))

        lista = conn.execute(
            """SELECT turnos.*,
                      (SELECT COUNT(*) FROM funcionarios WHERE funcionarios.turno_id = turnos.id) AS total_funcionarios
               FROM turnos ORDER BY nome"""
        ).fetchall()
        return render_template("turnos.html", turnos=lista)
    finally:
        conn.close()


@app.route("/admin/turnos/<int:turno_id>/editar", methods=["POST"])
@login_obrigatorio
@perfil_obrigatorio(*PERFIS_GESTAO)
def editar_turno(turno_id):
    dados = _ler_dados_turno_form()
    erro = _validar_dados_turno(dados)
    conn = db.get_db()
    try:
        if erro:
            flash(erro, "erro")
        else:
            jornadas_antes = relatorios_service.jornadas_atuais(conn)
            conn.execute(
                """UPDATE turnos SET nome=?, horario_entrada=?, horario_saida=?, tolerancia_minutos=?,
                   tolerancia_saida_minutos=?, dias_semana=?, horario_saida_almoco=?, horario_retorno_almoco=?
                   WHERE id=?""",
                (
                    dados["nome"],
                    dados["horario_entrada"],
                    dados["horario_saida"],
                    int(dados["tolerancia_minutos"] or 10),
                    int(dados["tolerancia_saida_minutos"] or 10),
                    dados["dias_semana"],
                    dados["horario_saida_almoco"],
                    dados["horario_retorno_almoco"],
                    turno_id,
                ),
            )
            relatorios_service.registrar_mudancas_de_jornada(conn, jornadas_antes)
            conn.commit()
            registrar_auditoria(conn, "editar", "turno", turno_id, f"nome={dados['nome']}")
            flash("Turno atualizado.", "sucesso")
    finally:
        conn.close()
    return redirect(url_for("turnos"))


@app.route("/admin/turnos/<int:turno_id>/excluir", methods=["POST"])
@login_obrigatorio
@perfil_obrigatorio(*PERFIS_GESTAO)
def excluir_turno(turno_id):
    conn = db.get_db()
    try:
        turno = conn.execute("SELECT nome FROM turnos WHERE id = ?", (turno_id,)).fetchone()
        try:
            conn.execute("DELETE FROM turnos WHERE id = ?", (turno_id,))
            conn.commit()
            registrar_auditoria(conn, "excluir", "turno", turno_id, f"nome={turno['nome']}" if turno else None)
            flash("Turno excluído.", "sucesso")
        except sqlite3.IntegrityError:
            flash("Não é possível excluir: existem funcionários ou escalas usando esse turno.", "erro")
    finally:
        conn.close()
    return redirect(url_for("turnos"))


def _ler_dados_escala_form():
    return {
        "nome": request.form.get("nome", "").strip(),
        "turno_id": request.form.get("turno_id", "").strip(),
        "ciclo_dias_trabalho": request.form.get("ciclo_dias_trabalho", "").strip(),
        "ciclo_dias_descanso": request.form.get("ciclo_dias_descanso", "").strip(),
    }


def _validar_dados_escala(dados):
    if not dados["nome"] or not dados["turno_id"]:
        return "Preencha o nome e o turno base da escala."
    if not dados["ciclo_dias_trabalho"].isdigit() or int(dados["ciclo_dias_trabalho"]) <= 0:
        return "Dias de trabalho do ciclo precisa ser um número maior que zero."
    if not dados["ciclo_dias_descanso"].isdigit() or int(dados["ciclo_dias_descanso"]) <= 0:
        return "Dias de descanso do ciclo precisa ser um número maior que zero."
    return None


@app.route("/admin/escalas", methods=["GET", "POST"])
@login_obrigatorio
@perfil_obrigatorio(*PERFIS_GESTAO)
def escalas():
    conn = db.get_db()
    try:
        if request.method == "POST":
            dados = _ler_dados_escala_form()
            erro = _validar_dados_escala(dados)
            if erro:
                flash(erro, "erro")
            else:
                cursor = conn.execute(
                    """INSERT INTO escalas (nome, turno_id, ciclo_dias_trabalho, ciclo_dias_descanso)
                       VALUES (?, ?, ?, ?)""",
                    (dados["nome"], int(dados["turno_id"]), int(dados["ciclo_dias_trabalho"]), int(dados["ciclo_dias_descanso"])),
                )
                conn.commit()
                registrar_auditoria(conn, "criar", "escala", cursor.lastrowid, f"nome={dados['nome']}")
                flash("Escala adicionada.", "sucesso")
            return redirect(url_for("escalas"))

        lista = conn.execute(
            """SELECT escalas.*, turnos.nome AS turno_nome,
                      (SELECT COUNT(*) FROM funcionarios WHERE funcionarios.escala_id = escalas.id) AS total_funcionarios
               FROM escalas
               JOIN turnos ON turnos.id = escalas.turno_id
               ORDER BY escalas.nome"""
        ).fetchall()
        turnos_lista = conn.execute("SELECT id, nome FROM turnos ORDER BY nome").fetchall()
        return render_template("escalas.html", escalas=lista, turnos=turnos_lista)
    finally:
        conn.close()


@app.route("/admin/escalas/<int:escala_id>/editar", methods=["POST"])
@login_obrigatorio
@perfil_obrigatorio(*PERFIS_GESTAO)
def editar_escala(escala_id):
    dados = _ler_dados_escala_form()
    erro = _validar_dados_escala(dados)
    conn = db.get_db()
    try:
        if erro:
            flash(erro, "erro")
        else:
            jornadas_antes = relatorios_service.jornadas_atuais(conn)
            conn.execute(
                """UPDATE escalas SET nome=?, turno_id=?, ciclo_dias_trabalho=?, ciclo_dias_descanso=?
                   WHERE id=?""",
                (
                    dados["nome"],
                    int(dados["turno_id"]),
                    int(dados["ciclo_dias_trabalho"]),
                    int(dados["ciclo_dias_descanso"]),
                    escala_id,
                ),
            )
            relatorios_service.registrar_mudancas_de_jornada(conn, jornadas_antes)
            conn.commit()
            registrar_auditoria(conn, "editar", "escala", escala_id, f"nome={dados['nome']}")
            flash("Escala atualizada.", "sucesso")
    finally:
        conn.close()
    return redirect(url_for("escalas"))


@app.route("/admin/escalas/<int:escala_id>/excluir", methods=["POST"])
@login_obrigatorio
@perfil_obrigatorio(*PERFIS_GESTAO)
def excluir_escala(escala_id):
    conn = db.get_db()
    try:
        escala = conn.execute("SELECT nome FROM escalas WHERE id = ?", (escala_id,)).fetchone()
        try:
            conn.execute("DELETE FROM escalas WHERE id = ?", (escala_id,))
            conn.commit()
            registrar_auditoria(conn, "excluir", "escala", escala_id, f"nome={escala['nome']}" if escala else None)
            flash("Escala excluída.", "sucesso")
        except sqlite3.IntegrityError:
            flash("Não é possível excluir: existem funcionários nessa escala.", "erro")
    finally:
        conn.close()
    return redirect(url_for("escalas"))
