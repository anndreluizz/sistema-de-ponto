"""Relatórios, exportações, ajustes de ponto e fechamento do mês."""

import csv
import io
import re
from datetime import date, datetime, timedelta
from flask import flash, redirect, render_template, request, session, url_for
import db
import exportadores
import relatorios_service

from nucleo import (
    NOMES_MESES,
    NOMES_TIPO_AUSENCIA,
    NOMES_VISAO,
    PERFIS_GESTAO,
    PERFIS_LEITURA,
    PERFIS_SOMENTE_ADMIN,
    _resposta_exportacao,
    app,
    login_obrigatorio,
    perfil_obrigatorio,
    registrar_auditoria,
)


def normalizar_timestamp(valor):
    valor = valor.strip().replace("T", " ")
    if len(valor) == 16:
        valor += ":00"
    return valor


def _ler_filtros_central(req):
    hoje = date.today()
    data_inicio = req.args.get("data_inicio") or hoje.replace(day=1).isoformat()
    data_fim = req.args.get("data_fim") or hoje.isoformat()
    agrupar_por = req.args.get("agrupar_por") or "funcionario"
    if agrupar_por not in ("funcionario", "setor", "cargo", "turno"):
        agrupar_por = "funcionario"
    visao = req.args.get("visao") or "frequencia"
    if visao not in NOMES_VISAO:
        visao = "frequencia"
    funcionario_id = req.args.get("funcionario_id", "").strip() or None
    return data_inicio, data_fim, agrupar_por, visao, funcionario_id


@app.route("/admin/relatorios")
@login_obrigatorio
@perfil_obrigatorio(*PERFIS_LEITURA)
def relatorios():
    conn = db.get_db()
    try:
        data_inicio, data_fim, agrupar_por, visao, funcionario_id = _ler_filtros_central(request)
        linhas = relatorios_service.montar_linhas_central(conn, data_inicio, data_fim, agrupar_por, visao, funcionario_id)
        funcionarios_lista = conn.execute(
            "SELECT id, nome FROM funcionarios WHERE situacao = 'ativo' ORDER BY nome"
        ).fetchall()
        atalhos = relatorios_service.calcular_atalhos_periodo()
        config = conn.execute("SELECT * FROM configuracoes WHERE id = 1").fetchone()

        return render_template(
            "central_relatorios.html",
            linhas=linhas,
            funcionarios=funcionarios_lista,
            atalhos=atalhos,
            data_inicio=data_inicio,
            data_fim=data_fim,
            agrupar_por=agrupar_por,
            visao=visao,
            funcionario_id=funcionario_id or "",
            nomes_visao=NOMES_VISAO,
            config=config,
            mes_folha_ponto_atual=date.today().strftime("%Y-%m"),
            meses_fechados=conn.execute(
                "SELECT * FROM meses_fechados ORDER BY mes DESC LIMIT 12"
            ).fetchall(),
            mes_anterior=(date.today().replace(day=1) - timedelta(days=1)).strftime("%Y-%m"),
        )
    finally:
        conn.close()


def _dados_exportacao_central_relatorios(conn, req):
    data_inicio, data_fim, agrupar_por, visao, funcionario_id = _ler_filtros_central(req)
    linhas = relatorios_service.montar_linhas_central(conn, data_inicio, data_fim, agrupar_por, visao, funcionario_id)
    colunas = relatorios_service.colunas_para_visao(visao, agrupar_por)
    linhas_tabela = relatorios_service.linhas_para_exportacao(linhas, visao)
    cabecalho = exportadores.obter_dados_cabecalho(conn)
    titulo = "Central de Relatórios"
    subtitulo = f"{NOMES_VISAO[visao]} - {data_inicio} a {data_fim}"
    return cabecalho, titulo, subtitulo, colunas, linhas_tabela


@app.route("/admin/relatorios/exportar.pdf")
@login_obrigatorio
@perfil_obrigatorio(*PERFIS_LEITURA)
def relatorios_exportar_pdf():
    conn = db.get_db()
    try:
        dados = _dados_exportacao_central_relatorios(conn, request)
    finally:
        conn.close()
    return _resposta_exportacao("pdf", *dados, "relatorio.pdf")


@app.route("/admin/relatorios/exportar.xlsx")
@login_obrigatorio
@perfil_obrigatorio(*PERFIS_LEITURA)
def relatorios_exportar_excel():
    conn = db.get_db()
    try:
        dados = _dados_exportacao_central_relatorios(conn, request)
    finally:
        conn.close()
    return _resposta_exportacao("xlsx", *dados, "relatorio.xlsx")


def _dados_exportacao_folha_ponto(conn, req):
    mes_str = req.args.get("mes") or date.today().strftime("%Y-%m")
    try:
        ano, mes = (int(parte) for parte in mes_str.split("-"))
    except ValueError:
        hoje = date.today()
        ano, mes = hoje.year, hoje.month
    funcionario_id = req.args.get("funcionario_id", "").strip() or None

    resultado = relatorios_service.montar_folha_ponto_mensal(conn, ano, mes, funcionario_id)
    cabecalho = exportadores.obter_dados_cabecalho(conn)
    titulo = "Folha de Ponto Mensal"
    subtitulo = f"{NOMES_MESES[mes]}/{ano}"
    return (
        cabecalho, titulo, subtitulo,
        resultado["ultimo_dia"], resultado["dias_semana_abrev"], resultado["linhas"],
    )


def _resposta_exportacao_folha_ponto(
    formato, cabecalho, titulo, subtitulo, ultimo_dia, dias_semana_abrev, linhas, nome_arquivo
):
    if formato == "pdf":
        conteudo = exportadores.gerar_pdf_folha_ponto(
            cabecalho, titulo, subtitulo, ultimo_dia, dias_semana_abrev, linhas
        )
        mimetype = "application/pdf"
    else:
        conteudo = exportadores.gerar_excel_folha_ponto(
            cabecalho, titulo, subtitulo, ultimo_dia, dias_semana_abrev, linhas
        )
        mimetype = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    return app.response_class(
        conteudo, mimetype=mimetype, headers={"Content-Disposition": f"attachment; filename={nome_arquivo}"}
    )


@app.route("/admin/relatorios/dias/exportar.pdf")
@login_obrigatorio
@perfil_obrigatorio(*PERFIS_LEITURA)
def relatorios_dias_exportar_pdf():
    conn = db.get_db()
    try:
        dados = _dados_exportacao_folha_ponto(conn, request)
    finally:
        conn.close()
    return _resposta_exportacao_folha_ponto("pdf", *dados, "folha_de_ponto.pdf")


@app.route("/admin/relatorios/dias/exportar.xlsx")
@login_obrigatorio
@perfil_obrigatorio(*PERFIS_LEITURA)
def relatorios_dias_exportar_excel():
    conn = db.get_db()
    try:
        dados = _dados_exportacao_folha_ponto(conn, request)
    finally:
        conn.close()
    return _resposta_exportacao_folha_ponto("xlsx", *dados, "folha_de_ponto.xlsx")


@app.route("/admin/ajustes")
@login_obrigatorio
@perfil_obrigatorio(*PERFIS_GESTAO)
def ajustes_ponto():
    conn = db.get_db()
    try:
        funcionarios_lista = conn.execute("SELECT id, nome FROM funcionarios ORDER BY nome").fetchall()

        funcionario_id = request.args.get("funcionario_id", "").strip()
        hoje = date.today()
        data_inicio = request.args.get("data_inicio") or hoje.replace(day=1).isoformat()
        data_fim = request.args.get("data_fim") or hoje.isoformat()

        query = """
            SELECT registros.*, funcionarios.nome AS funcionario_nome
            FROM registros
            JOIN funcionarios ON funcionarios.id = registros.funcionario_id
            WHERE registros.timestamp >= date(?) AND registros.timestamp < date(?, '+1 day')
        """
        params = [data_inicio, data_fim]
        if funcionario_id:
            query += " AND registros.funcionario_id = ?"
            params.append(funcionario_id)
        query += " ORDER BY funcionario_nome, registros.timestamp"

        registros = conn.execute(query, params).fetchall()
        dias = relatorios_service.agrupar_registros_por_dia(registros)

        query_ausencias = """
            SELECT ausencias.*, funcionarios.nome AS funcionario_nome
            FROM ausencias
            JOIN funcionarios ON funcionarios.id = ausencias.funcionario_id
            WHERE ausencias.data BETWEEN ? AND ?
        """
        params_ausencias = [data_inicio, data_fim]
        if funcionario_id:
            query_ausencias += " AND ausencias.funcionario_id = ?"
            params_ausencias.append(funcionario_id)
        query_ausencias += " ORDER BY ausencias.data DESC"
        ausencias = conn.execute(query_ausencias, params_ausencias).fetchall()

        documentos_por_ausencia = {}
        if ausencias:
            ids_ausencias = [a["id"] for a in ausencias]
            marcadores = ",".join("?" * len(ids_ausencias))
            for doc in conn.execute(
                f"SELECT * FROM documentos WHERE ausencia_id IN ({marcadores}) ORDER BY criado_em DESC",
                ids_ausencias,
            ).fetchall():
                documentos_por_ausencia.setdefault(doc["ausencia_id"], []).append(doc)

        return render_template(
            "ajustes_ponto.html",
            funcionarios=funcionarios_lista,
            dias=dias,
            ausencias=ausencias,
            documentos_por_ausencia=documentos_por_ausencia,
            funcionario_id=funcionario_id,
            data_inicio=data_inicio,
            data_fim=data_fim,
            nomes_tipo_ausencia=NOMES_TIPO_AUSENCIA,
        )
    finally:
        conn.close()


@app.route("/admin/relatorios/exportar.csv")
@login_obrigatorio
@perfil_obrigatorio(*PERFIS_LEITURA)
def exportar_csv():
    conn = db.get_db()
    try:
        funcionario_id = request.args.get("funcionario_id", "").strip()
        hoje = date.today()
        data_inicio = request.args.get("data_inicio") or hoje.replace(day=1).isoformat()
        data_fim = request.args.get("data_fim") or hoje.isoformat()

        query = """
            SELECT registros.timestamp, registros.tipo, registros.manual,
                   funcionarios.nome AS funcionario_nome, funcionarios.cargo
            FROM registros
            JOIN funcionarios ON funcionarios.id = registros.funcionario_id
            WHERE registros.timestamp >= date(?) AND registros.timestamp < date(?, '+1 day')
        """
        params = [data_inicio, data_fim]
        if funcionario_id:
            query += " AND registros.funcionario_id = ?"
            params.append(funcionario_id)
        query += " ORDER BY funcionario_nome, registros.timestamp"

        registros = conn.execute(query, params).fetchall()
    finally:
        conn.close()

    saida = io.StringIO()
    escritor = csv.writer(saida, delimiter=";")
    escritor.writerow(["Funcionario", "Cargo", "Data", "Hora", "Tipo", "Lancado manualmente"])
    for r in registros:
        data_hora = datetime.strptime(r["timestamp"], "%Y-%m-%d %H:%M:%S")
        escritor.writerow(
            [
                r["funcionario_nome"],
                r["cargo"],
                data_hora.strftime("%d/%m/%Y"),
                data_hora.strftime("%H:%M:%S"),
                "Entrada" if r["tipo"] == "entrada" else "Saida",
                "Sim" if r["manual"] else "Nao",
            ]
        )

    conteudo = chr(0xFEFF) + saida.getvalue()
    return app.response_class(
        conteudo,
        mimetype="text/csv",
        headers={"Content-Disposition": "attachment; filename=relatorio_ponto.csv"},
    )


@app.route("/admin/registros/novo", methods=["POST"])
@login_obrigatorio
@perfil_obrigatorio(*PERFIS_GESTAO)
def novo_registro_manual():
    funcionario_id = request.form.get("funcionario_id", "").strip()
    tipo = request.form.get("tipo", "").strip()
    timestamp_valor = normalizar_timestamp(request.form.get("timestamp", ""))
    motivo = request.form.get("motivo", "").strip()

    if not funcionario_id or tipo not in ("entrada", "saida") or not timestamp_valor or not motivo:
        flash("Preencha todos os campos (incluindo o motivo) para adicionar um registro manual.", "erro")
        return redirect(request.referrer or url_for("ajustes_ponto"))

    conn = db.get_db()
    try:
        cursor = conn.execute(
            "INSERT INTO registros (funcionario_id, tipo, timestamp, manual, criado_por, motivo) VALUES (?, ?, ?, 1, ?, ?)",
            (funcionario_id, tipo, timestamp_valor, session.get("usuario_login"), motivo),
        )
        conn.commit()
        registrar_auditoria(
            conn, "criar", "registro", cursor.lastrowid,
            f"funcionario_id={funcionario_id}; tipo={tipo}; timestamp={timestamp_valor}; motivo={motivo}",
        )
    finally:
        conn.close()
    flash("Registro manual adicionado.", "sucesso")
    return redirect(request.referrer or url_for("ajustes_ponto"))


@app.route("/admin/registros/<int:registro_id>/editar", methods=["POST"])
@login_obrigatorio
@perfil_obrigatorio(*PERFIS_GESTAO)
def editar_registro(registro_id):
    novo_timestamp = normalizar_timestamp(request.form.get("timestamp", ""))
    novo_tipo = request.form.get("tipo", "").strip()
    motivo = request.form.get("motivo", "").strip()

    if novo_tipo not in ("entrada", "saida") or not novo_timestamp or not motivo:
        flash("Preencha os dados (incluindo o motivo) para editar o registro.", "erro")
        return redirect(request.referrer or url_for("ajustes_ponto"))

    conn = db.get_db()
    try:
        conn.execute(
            """UPDATE registros SET timestamp = ?, tipo = ?, manual = 1, atualizado_por = ?, atualizado_em = ?,
               motivo = ? WHERE id = ?""",
            (
                novo_timestamp,
                novo_tipo,
                session.get("usuario_login"),
                datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                motivo,
                registro_id,
            ),
        )
        conn.commit()
        registrar_auditoria(
            conn, "editar", "registro", registro_id, f"tipo={novo_tipo}; timestamp={novo_timestamp}; motivo={motivo}"
        )
    finally:
        conn.close()
    flash("Registro atualizado.", "sucesso")
    return redirect(request.referrer or url_for("ajustes_ponto"))


@app.route("/admin/registros/<int:registro_id>/excluir", methods=["POST"])
@login_obrigatorio
@perfil_obrigatorio(*PERFIS_GESTAO)
def excluir_registro(registro_id):
    conn = db.get_db()
    try:
        registro = conn.execute(
            """SELECT registros.*, funcionarios.nome AS funcionario_nome
               FROM registros JOIN funcionarios ON funcionarios.id = registros.funcionario_id
               WHERE registros.id = ?""",
            (registro_id,),
        ).fetchone()
        conn.execute("DELETE FROM registros WHERE id = ?", (registro_id,))
        conn.commit()
        if registro:
            registrar_auditoria(
                conn, "excluir", "registro", registro_id,
                f"funcionario={registro['funcionario_nome']}; tipo={registro['tipo']}; timestamp={registro['timestamp']}",
            )
    finally:
        conn.close()
    flash("Registro excluído.", "sucesso")
    return redirect(request.referrer or url_for("ajustes_ponto"))


@app.route("/admin/meses/fechar", methods=["POST"])
@login_obrigatorio
@perfil_obrigatorio(*PERFIS_GESTAO)
def fechar_mes():
    mes = request.form.get("mes", "").strip()
    if not re.fullmatch(r"\d{4}-\d{2}", mes) or mes >= date.today().strftime("%Y-%m"):
        # Mês corrente não fecha: o totem continua gravando nele.
        flash("Só dá para fechar meses que já terminaram.", "erro")
        return redirect(url_for("relatorios"))
    conn = db.get_db()
    try:
        conn.execute(
            "INSERT OR IGNORE INTO meses_fechados (mes, fechado_por, fechado_em) VALUES (?, ?, ?)",
            (mes, session.get("usuario_login"), datetime.now().strftime("%Y-%m-%d %H:%M:%S")),
        )
        conn.commit()
        registrar_auditoria(conn, "fechar_mes", "mes", detalhes=f"mes={mes}")
    finally:
        conn.close()
    flash(f"Mês {mes[5:]}/{mes[:4]} fechado. Os registros dele não podem mais ser alterados.", "sucesso")
    return redirect(url_for("relatorios"))


@app.route("/admin/meses/<mes>/reabrir", methods=["POST"])
@login_obrigatorio
@perfil_obrigatorio(*PERFIS_SOMENTE_ADMIN)
def reabrir_mes(mes):
    conn = db.get_db()
    try:
        conn.execute("DELETE FROM meses_fechados WHERE mes = ?", (mes,))
        conn.commit()
        registrar_auditoria(conn, "reabrir_mes", "mes", detalhes=f"mes={mes}")
    finally:
        conn.close()
    flash(f"Mês {mes[5:]}/{mes[:4]} reaberto para alterações.", "sucesso")
    return redirect(url_for("relatorios"))
