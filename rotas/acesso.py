"""Login, logout, troca de senha e painel inicial."""

from datetime import date, datetime, timedelta
from flask import flash, redirect, render_template, request, session, url_for
from werkzeug.security import check_password_hash, generate_password_hash
import db
import relatorios_service

from nucleo import (
    BLOQUEIO_LOGIN_MINUTOS,
    MAX_TENTATIVAS_LOGIN,
    NOMES_MESES,
    QUANTIDADE_MESES_COMPARATIVO,
    TOP_N_RANKING_ATRASOS,
    app,
    login_obrigatorio,
    registrar_auditoria,
)


def calcular_dashboard(conn):
    hoje_date = date.today()
    hoje = hoje_date.isoformat()
    agora = datetime.now()

    ativos = conn.execute(
        f"""SELECT funcionarios.*, {relatorios_service.SQL_COLUNAS_TURNO_EFETIVO}
           FROM funcionarios
           {relatorios_service.SQL_JOIN_TURNO_EFETIVO}
           WHERE funcionarios.situacao = 'ativo'"""
    ).fetchall()

    registros_hoje = conn.execute(
        """SELECT registros.funcionario_id, registros.tipo, registros.timestamp,
                  funcionarios.nome AS funcionario_nome
           FROM registros
           JOIN funcionarios ON funcionarios.id = registros.funcionario_id
           WHERE registros.timestamp >= ? AND registros.timestamp < date(?, '+1 day') ORDER BY registros.funcionario_id, registros.timestamp""",
        (hoje, hoje),
    ).fetchall()

    primeiro_por_func, ultimo_por_func = {}, {}
    for r in registros_hoje:
        fid = r["funcionario_id"]
        primeiro_por_func.setdefault(fid, r)
        ultimo_por_func[fid] = r

    dias_por_funcionario_hoje = {
        dia["funcionario_id"]: dia for dia in relatorios_service.agrupar_registros_por_dia(registros_hoje)
    }

    ausencias_hoje = {
        r["funcionario_id"]
        for r in conn.execute("SELECT funcionario_id FROM ausencias WHERE data = ?", (hoje,)).fetchall()
    }
    feriados_hoje = relatorios_service.obter_feriados_no_periodo(conn, hoje, hoje)

    presentes, ausentes, atrasados, faltas, em_intervalo, saidas_antecipadas = [], [], [], [], [], []

    for f in ativos:
        fid = f["id"]
        primeiro, ultimo = primeiro_por_func.get(fid), ultimo_por_func.get(fid)

        if ultimo is not None and ultimo["tipo"] == "entrada":
            presentes.append(f)

        # Sábado, feriado ou folga da escala não é "ausente" — a pessoa nem trabalharia hoje.
        trabalha_hoje = relatorios_service.dia_e_util_para_funcionario(f, hoje_date, feriados_hoje) and not (
            f["data_admissao"] and f["data_admissao"] > hoje
        )
        if primeiro is None and fid not in ausencias_hoje and trabalha_hoje:
            ausentes.append(f)

        tem_turno_efetivo = f["horario_entrada"] is not None
        dia_util_hoje = tem_turno_efetivo and relatorios_service.dia_e_util_para_funcionario(
            f, hoje_date, feriados_hoje
        )

        if dia_util_hoje:
            limite = (
                datetime.strptime(f["horario_entrada"], "%H:%M") + timedelta(minutes=f["tolerancia_minutos"])
            ).time()
            if primeiro is not None and primeiro["tipo"] == "entrada":
                chegada = datetime.strptime(primeiro["timestamp"], "%Y-%m-%d %H:%M:%S").time()
                if chegada > limite:
                    atrasados.append(f)
            elif primeiro is None and agora.time() > limite and fid not in ausencias_hoje:
                faltas.append(f)

            dia_hoje = dias_por_funcionario_hoje.get(fid)
            pares_hoje = dia_hoje["pares"] if dia_hoje else []
            _entrada_hoje, _saida_almoco_hoje, _retorno_hoje, saida_hoje = relatorios_service.preencher_colunas_historico(
                pares_hoje
            )
            lunch_configurado = bool(f["horario_saida_almoco"] and f["horario_retorno_almoco"])

            esta_em_intervalo = False
            if lunch_configurado:
                if ultimo is not None and ultimo["tipo"] == "saida":
                    inicio_almoco = datetime.strptime(f["horario_saida_almoco"], "%H:%M").time()
                    fim_almoco = datetime.strptime(f["horario_retorno_almoco"], "%H:%M").time()
                    if inicio_almoco <= agora.time() <= fim_almoco:
                        esta_em_intervalo = True
            elif ultimo is not None and ultimo["tipo"] == "saida":
                limite_saida_heuristica = datetime.strptime(f["horario_saida"], "%H:%M").time()
                if agora.time() < limite_saida_heuristica:
                    esta_em_intervalo = True

            if esta_em_intervalo:
                em_intervalo.append(f)
            elif saida_hoje is not None and f["tolerancia_saida_minutos"] is not None:
                limite_saida_antecipada = (
                    datetime.strptime(f["horario_saida"], "%H:%M")
                    - timedelta(minutes=f["tolerancia_saida_minutos"])
                ).time()
                saida_hora = datetime.strptime(saida_hoje["timestamp"], "%Y-%m-%d %H:%M:%S").time()
                if saida_hora < limite_saida_antecipada:
                    saidas_antecipadas.append(f)

    def _ativos_com_ausencia_hoje(*tipos):
        marcadores = ",".join("?" * len(tipos))
        return conn.execute(
            f"""SELECT funcionarios.* FROM ausencias
               JOIN funcionarios ON funcionarios.id = ausencias.funcionario_id
               WHERE ausencias.data = ? AND ausencias.tipo IN ({marcadores}) AND funcionarios.situacao = 'ativo'""",
            (hoje, *tipos),
        ).fetchall()

    ferias_hoje = _ativos_com_ausencia_hoje("ferias")
    # Licença maternidade/paternidade soma junto de "licença" — mesmo critério de "folga"/"outro",
    # que também não têm indicador próprio no dashboard.
    licencas_hoje = _ativos_com_ausencia_hoje("licenca", "licenca_maternidade_paternidade")
    atestados_hoje = _ativos_com_ausencia_hoje("atestado")
    horas_extras_hoje_seg = relatorios_service.calcular_horas_extras_hoje(conn)

    ultimo_ponto = conn.execute(
        """SELECT registros.*, funcionarios.nome, funcionarios.foto, funcionarios.cargo
           FROM registros JOIN funcionarios ON funcionarios.id = registros.funcionario_id
           WHERE registros.timestamp >= ? AND registros.timestamp < date(?, '+1 day')
           ORDER BY registros.timestamp DESC LIMIT 1""",
        (hoje, hoje),
    ).fetchone()

    return {
        "total_ativos": len(ativos),
        "presentes": presentes,
        "ausentes": ausentes,
        "atrasados": atrasados,
        "faltas": faltas,
        "em_intervalo": em_intervalo,
        "saidas_antecipadas": saidas_antecipadas,
        "ferias_hoje": ferias_hoje,
        "licencas_hoje": licencas_hoje,
        "atestados_hoje": atestados_hoje,
        "horas_extras_hoje": relatorios_service.formatar_horas(horas_extras_hoje_seg),
        "registros_hoje": len(registros_hoje),
        "ultimo_ponto": ultimo_ponto,
    }


def calcular_presenca_ultimos_dias(conn, dias=7):
    total_ativos = conn.execute("SELECT COUNT(*) AS n FROM funcionarios WHERE situacao = 'ativo'").fetchone()["n"]
    resultado = []
    for i in range(dias - 1, -1, -1):
        dia = date.today() - timedelta(days=i)
        presentes = conn.execute(
            """SELECT COUNT(DISTINCT registros.funcionario_id) AS n
               FROM registros JOIN funcionarios ON funcionarios.id = registros.funcionario_id
               WHERE registros.timestamp >= ? AND registros.timestamp < date(?, '+1 day') AND funcionarios.situacao = 'ativo'""",
            (dia.isoformat(), dia.isoformat()),
        ).fetchone()["n"]
        resultado.append(
            {
                "data": dia,
                "presentes": presentes,
                "total": total_ativos,
                "percentual": round(presentes / total_ativos * 100) if total_ativos else 0,
            }
        )
    return resultado


def calcular_presenca_por_setor(conn, dias=7):
    data_inicio = date.today() - timedelta(days=dias - 1)
    data_fim = date.today()

    setores = conn.execute("SELECT id, nome FROM setores ORDER BY nome").fetchall()
    grupos = [(s["id"], s["nome"]) for s in setores] + [(None, "Sem setor")]

    resultado = []
    for setor_id, setor_nome in grupos:
        if setor_id is not None:
            funcionarios = conn.execute(
                "SELECT id FROM funcionarios WHERE situacao = 'ativo' AND setor_id = ?", (setor_id,)
            ).fetchall()
        else:
            funcionarios = conn.execute(
                "SELECT id FROM funcionarios WHERE situacao = 'ativo' AND setor_id IS NULL"
            ).fetchall()

        if not funcionarios:
            continue

        ids_funcionarios = [f["id"] for f in funcionarios]
        placeholders = ",".join("?" * len(ids_funcionarios))
        dias_com_presenca = conn.execute(
            f"""SELECT COUNT(DISTINCT date(timestamp)) AS n FROM registros
                WHERE funcionario_id IN ({placeholders})
                  AND timestamp >= date(?) AND timestamp < date(?, '+1 day')""",
            [*ids_funcionarios, data_inicio.isoformat(), data_fim.isoformat()],
        ).fetchone()["n"]

        resultado.append(
            {
                "setor_nome": setor_nome,
                "percentual": round(dias_com_presenca / dias * 100) if dias else 0,
            }
        )

    return resultado


@app.route("/admin")
@login_obrigatorio
def admin_index():
    return redirect(url_for("dashboard"))


@app.route("/admin/dashboard")
@login_obrigatorio
def dashboard():
    periodo_grafico = request.args.get("periodo_grafico", "7")
    periodo_grafico = 30 if periodo_grafico == "30" else 7

    conn = db.get_db()
    try:
        indicadores = calcular_dashboard(conn)
        presenca_semana = calcular_presenca_ultimos_dias(conn, dias=periodo_grafico)
        presenca_setor = calcular_presenca_por_setor(conn, dias=periodo_grafico)

        comparativo_mensal = relatorios_service.montar_comparativo_mensal(
            conn, quantidade_meses=QUANTIDADE_MESES_COMPARATIVO
        )
        for item in comparativo_mensal:
            item["rotulo"] = f"{NOMES_MESES[item['mes']][:3]}/{item['ano']}"

        ranking_atrasos = []
        if session.get("usuario_perfil") != "consulta":
            hoje = date.today()
            data_inicio_ranking = (hoje - timedelta(days=periodo_grafico - 1)).isoformat()
            ranking_atrasos = relatorios_service.montar_ranking_atrasos(
                conn, data_inicio_ranking, hoje.isoformat(), top_n=TOP_N_RANKING_ATRASOS
            )

        return render_template(
            "dashboard.html",
            indicadores=indicadores,
            presenca_semana=presenca_semana,
            presenca_setor=presenca_setor,
            periodo_grafico=periodo_grafico,
            comparativo_mensal=comparativo_mensal,
            ranking_atrasos=ranking_atrasos,
        )
    finally:
        conn.close()


@app.route("/admin/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        usuario = request.form.get("usuario", "").strip()
        senha = request.form.get("senha", "")

        conn = db.get_db()
        try:
            registro = conn.execute(
                "SELECT * FROM usuarios WHERE usuario = ? AND ativo = 1", (usuario,)
            ).fetchone()
            agora = datetime.now()

            if registro and registro["bloqueado_ate"]:
                bloqueado_ate = datetime.strptime(registro["bloqueado_ate"], "%Y-%m-%d %H:%M:%S")
                if agora < bloqueado_ate:
                    registrar_auditoria(conn, "login_bloqueado", "usuario", registro["id"])
                    minutos_restantes = int((bloqueado_ate - agora).total_seconds() // 60) + 1
                    flash(
                        f"Conta bloqueada por tentativas seguidas incorretas. "
                        f"Tente novamente em {minutos_restantes} minuto(s).",
                        "erro",
                    )
                    return render_template("login.html")

            if registro and check_password_hash(registro["senha_hash"], senha):
                if registro["tentativas_falhas"] or registro["bloqueado_ate"]:
                    conn.execute(
                        "UPDATE usuarios SET tentativas_falhas = 0, bloqueado_ate = NULL WHERE id = ?",
                        (registro["id"],),
                    )
                    conn.commit()
                session.clear()
                session.permanent = True
                if senha == db.ADMIN_PADRAO_SENHA:
                    session["senha_padrao"] = True
                session["usuario_id"] = registro["id"]
                session["usuario_login"] = registro["usuario"]
                session["usuario_nome"] = registro["nome"] or registro["usuario"]
                session["usuario_perfil"] = registro["perfil"]
                registrar_auditoria(conn, "login_sucesso", "usuario", registro["id"])
                proximo = request.args.get("proximo") or ""
                # Só caminhos internos: "//site" ou "/\site" o navegador trata como outro domínio.
                if not proximo.startswith("/") or proximo[1:2] in ("/", "\\"):
                    proximo = url_for("dashboard")
                return redirect(proximo)

            if registro:
                tentativas = (registro["tentativas_falhas"] or 0) + 1
                if tentativas >= MAX_TENTATIVAS_LOGIN:
                    bloqueado_ate_novo = (agora + timedelta(minutes=BLOQUEIO_LOGIN_MINUTOS)).strftime(
                        "%Y-%m-%d %H:%M:%S"
                    )
                    conn.execute(
                        "UPDATE usuarios SET tentativas_falhas = ?, bloqueado_ate = ? WHERE id = ?",
                        (tentativas, bloqueado_ate_novo, registro["id"]),
                    )
                    conn.commit()
                    registrar_auditoria(
                        conn, "login_bloqueado", "usuario", registro["id"], detalhes=f"tentativas={tentativas}"
                    )
                    flash(
                        f"Conta bloqueada por {BLOQUEIO_LOGIN_MINUTOS} minutos após "
                        f"{MAX_TENTATIVAS_LOGIN} tentativas seguidas incorretas.",
                        "erro",
                    )
                    return render_template("login.html")
                conn.execute(
                    "UPDATE usuarios SET tentativas_falhas = ? WHERE id = ?",
                    (tentativas, registro["id"]),
                )
                conn.commit()

            registrar_auditoria(conn, "login_falha", "usuario", detalhes=f"usuario_tentado={usuario}")
        finally:
            conn.close()

        flash("Usuário ou senha inválidos.", "erro")

    return render_template("login.html")


@app.route("/admin/logout")
def logout():
    if session.get("usuario_id"):
        conn = db.get_db()
        try:
            registrar_auditoria(conn, "logout", "usuario", session.get("usuario_id"))
        finally:
            conn.close()
    session.clear()
    return redirect(url_for("login"))


@app.route("/admin/senha", methods=["GET", "POST"])
@login_obrigatorio
def trocar_senha():
    if request.method == "POST":
        senha_atual = request.form.get("senha_atual", "")
        nova_senha = request.form.get("nova_senha", "")
        confirmar_senha = request.form.get("confirmar_senha", "")

        conn = db.get_db()
        try:
            registro_usuario = conn.execute(
                "SELECT * FROM usuarios WHERE id = ?", (session["usuario_id"],)
            ).fetchone()

            if not registro_usuario or not check_password_hash(registro_usuario["senha_hash"], senha_atual):
                flash("Senha atual incorreta.", "erro")
            elif len(nova_senha) < 6:
                flash("A nova senha precisa ter pelo menos 6 caracteres.", "erro")
            elif nova_senha == db.ADMIN_PADRAO_SENHA:
                flash("Escolha uma senha diferente da senha padrão.", "erro")
            elif nova_senha != confirmar_senha:
                flash("A confirmação não corresponde à nova senha.", "erro")
            else:
                conn.execute(
                    "UPDATE usuarios SET senha_hash = ? WHERE id = ?",
                    (generate_password_hash(nova_senha), session["usuario_id"]),
                )
                conn.commit()
                registrar_auditoria(conn, "trocar_senha", "usuario", session["usuario_id"])
                session.pop("senha_padrao", None)
                flash("Senha alterada com sucesso.", "sucesso")
        finally:
            conn.close()

    return render_template("trocar_senha.html")
