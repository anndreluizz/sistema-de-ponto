from datetime import date

from relatorios_service import (
    calcular_meses_fechados,
    calcular_stats_funcionarios_periodo,
    dia_e_util_para_funcionario,
    duracao_turno_segundos,
    formatar_horas,
    horas_esperadas_no_dia_segundos,
    montar_comparativo_mensal,
    montar_folha_ponto_mensal,
    montar_linhas_central,
    montar_ranking_atrasos,
)

# ---------- Nível 1: funções puras, sem banco ----------


def test_duracao_turno_segundos_sem_almoco():
    assert duracao_turno_segundos("08:00", "17:00") == 9 * 3600


def test_duracao_turno_segundos_com_almoco():
    assert duracao_turno_segundos("08:00", "17:00", "12:00", "13:00") == 8 * 3600


def test_duracao_turno_segundos_atravessa_meia_noite():
    assert duracao_turno_segundos("22:00", "06:00") == 8 * 3600


def test_duracao_turno_segundos_almoco_atravessa_meia_noite():
    # turno 20h-04h, almoço 23h30-00h30 (atravessa meia-noite também)
    assert duracao_turno_segundos("20:00", "04:00", "23:30", "00:30") == 7 * 3600


def test_formatar_horas_none():
    assert formatar_horas(None) == "-"


def test_formatar_horas_positivo():
    assert formatar_horas(3600) == "01:00"


def test_formatar_horas_negativo():
    assert formatar_horas(-3600) == "-01:00"


def test_formatar_horas_zero():
    assert formatar_horas(0) == "00:00"


def test_dia_e_util_para_funcionario_fallback_sem_turno():
    f = {"escala_id": None, "turno_id": None}
    assert dia_e_util_para_funcionario(f, date(2026, 1, 5)) is True  # segunda
    assert dia_e_util_para_funcionario(f, date(2026, 1, 10)) is False  # sábado


def test_dia_e_util_para_funcionario_turno_com_dias_restritos():
    # turno só segunda/quarta/sexta
    f = {"escala_id": None, "turno_id": 1, "turno_dias_semana": "0,2,4"}
    assert dia_e_util_para_funcionario(f, date(2026, 1, 5)) is True  # segunda
    assert dia_e_util_para_funcionario(f, date(2026, 1, 6)) is False  # terça
    assert dia_e_util_para_funcionario(f, date(2026, 1, 7)) is True  # quarta


def test_dia_e_util_para_funcionario_turno_sem_dias_definidos():
    f = {"escala_id": None, "turno_id": 1, "turno_dias_semana": None}
    assert dia_e_util_para_funcionario(f, date(2026, 1, 10)) is True  # sábado, mas sem restrição


def test_dia_e_util_para_funcionario_escala_em_ciclo():
    # ciclo de 4 dias: 1 dia de trabalho + 3 de descanso, referência = 2026-01-01 (trabalha)
    f = {
        "escala_id": 1,
        "escala_data_referencia": "2026-01-01",
        "escala_ciclo_dias_trabalho": 1,
        "escala_ciclo_dias_descanso": 3,
    }
    assert dia_e_util_para_funcionario(f, date(2026, 1, 1)) is True  # dia de referência
    assert dia_e_util_para_funcionario(f, date(2026, 1, 2)) is False  # descanso
    assert dia_e_util_para_funcionario(f, date(2026, 1, 3)) is False  # descanso
    assert dia_e_util_para_funcionario(f, date(2026, 1, 4)) is False  # descanso
    assert dia_e_util_para_funcionario(f, date(2026, 1, 5)) is True  # próximo ciclo, trabalha de novo


def test_horas_esperadas_no_dia_segundos_dia_util():
    f = {
        "escala_id": None,
        "turno_id": 1,
        "turno_dias_semana": "0,1,2,3,4",
        "horario_entrada": "08:00",
        "horario_saida": "17:00",
        "horario_saida_almoco": "12:00",
        "horario_retorno_almoco": "13:00",
    }
    assert horas_esperadas_no_dia_segundos(f, date(2026, 1, 5)) == 8 * 3600  # segunda


def test_horas_esperadas_no_dia_segundos_dia_de_folga():
    f = {
        "escala_id": None,
        "turno_id": 1,
        "turno_dias_semana": "0,1,2,3,4",
        "horario_entrada": "08:00",
        "horario_saida": "17:00",
        "horario_saida_almoco": None,
        "horario_retorno_almoco": None,
    }
    assert horas_esperadas_no_dia_segundos(f, date(2026, 1, 10)) == 0  # sábado, fora do turno


def test_horas_esperadas_no_dia_segundos_sem_turno():
    f = {"escala_id": None, "turno_id": None, "horario_entrada": None}
    assert horas_esperadas_no_dia_segundos(f, date(2026, 1, 5)) == 0


def test_dia_e_util_para_funcionario_feriado_bloqueia_mesmo_dia_normalmente_util():
    f = {"escala_id": None, "turno_id": 1, "turno_dias_semana": "0,1,2,3,4"}
    assert dia_e_util_para_funcionario(f, date(2026, 1, 5)) is True  # segunda, sem feriado
    assert dia_e_util_para_funcionario(f, date(2026, 1, 5), feriados={"2026-01-05"}) is False
    # feriado em outra data nao afeta o dia perguntado
    assert dia_e_util_para_funcionario(f, date(2026, 1, 5), feriados={"2026-01-06"}) is True


def test_horas_esperadas_no_dia_segundos_feriado():
    f = {
        "escala_id": None,
        "turno_id": 1,
        "turno_dias_semana": "0,1,2,3,4",
        "horario_entrada": "08:00",
        "horario_saida": "17:00",
        "horario_saida_almoco": None,
        "horario_retorno_almoco": None,
    }
    assert horas_esperadas_no_dia_segundos(f, date(2026, 1, 5), feriados={"2026-01-05"}) == 0


# ---------- Nível 2: usam banco sintético (fixtures `conn` + `cenario_semana`, ver conftest.py) ----------


def test_montar_linhas_central_frequencia_por_funcionario(conn, cenario_semana):
    linhas = montar_linhas_central(conn, "2026-01-05", "2026-01-09", "funcionario", "frequencia")
    por_nome = {linha["dimensao"]: linha for linha in linhas}

    assert por_nome["Ana Pontual"]["dias_com_ponto"] == 5
    assert por_nome["Ana Pontual"]["faltas"] == 0
    assert por_nome["Ana Pontual"]["atrasos"] == 0
    assert por_nome["Ana Pontual"]["horas_trabalhadas"] == "40:00"

    assert por_nome["Beto Testado"]["dias_com_ponto"] == 3
    assert por_nome["Beto Testado"]["faltas"] == 1  # quarta sem registro nem ausência
    assert por_nome["Beto Testado"]["atrasos"] == 1  # terça
    assert por_nome["Beto Testado"]["horas_trabalhadas"] == "24:35"


def test_montar_linhas_central_atrasos_agrupado_por_funcionario(conn, cenario_semana):
    linhas = montar_linhas_central(conn, "2026-01-05", "2026-01-09", "funcionario", "atrasos")
    por_nome = {linha["dimensao"]: linha for linha in linhas}

    assert por_nome["Ana Pontual"]["atraso_acumulado"] == "00:00"
    assert por_nome["Beto Testado"]["atrasos"] == 1
    assert por_nome["Beto Testado"]["atraso_acumulado"] == "00:15"


def test_montar_linhas_central_ausencia_nao_conta_falta(conn, cenario_semana):
    linhas = montar_linhas_central(conn, "2026-01-05", "2026-01-09", "funcionario", "ausencias")
    por_nome = {linha["dimensao"]: linha for linha in linhas}
    assert por_nome["Beto Testado"]["ferias"] == 1


def test_montar_folha_ponto_mensal_ultimo_dia_do_mes(conn, cenario_semana):
    resultado = montar_folha_ponto_mensal(conn, 2026, 1)
    assert resultado["ultimo_dia"] == 31


def test_montar_folha_ponto_mensal_codigos_por_dia(conn, cenario_semana):
    resultado = montar_folha_ponto_mensal(conn, 2026, 1)
    por_nome = {linha["funcionario"]["nome"]: linha["codigos"] for linha in resultado["linhas"]}

    codigos_ana = por_nome["Ana Pontual"]
    assert codigos_ana[4] == "P"  # dia 5 (indice 4) = segunda, presente
    assert codigos_ana[0] == "F"  # dia 1 = quinta util, sem dado nenhum = falta
    assert codigos_ana[9] == ""  # dia 10 = sabado, nao e dia de trabalho
    assert codigos_ana[10] == ""  # dia 11 = domingo

    codigos_beto = por_nome["Beto Testado"]
    assert codigos_beto[4] == "P"  # dia 5, presente
    assert codigos_beto[5] == "P"  # dia 6, presente (mesmo com atraso - presenca e presenca)
    assert codigos_beto[6] == "F"  # dia 7, falta nao justificada
    assert codigos_beto[7] == "FE"  # dia 8, ausencia tipo ferias
    assert codigos_beto[8] == "P"  # dia 9, presente


def test_montar_folha_ponto_mensal_filtra_por_funcionario(conn, cenario_semana):
    resultado = montar_folha_ponto_mensal(conn, 2026, 1, funcionario_id=cenario_semana["ana_id"])
    assert len(resultado["linhas"]) == 1
    assert resultado["linhas"][0]["funcionario"]["nome"] == "Ana Pontual"


def test_montar_folha_ponto_mensal_sem_funcionario_correspondente(conn, cenario_semana):
    resultado = montar_folha_ponto_mensal(conn, 2026, 1, funcionario_id=99999)
    assert resultado["ultimo_dia"] == 31
    assert resultado["linhas"] == []


def test_montar_folha_ponto_mensal_dias_semana_abrev(conn, cenario_semana):
    resultado = montar_folha_ponto_mensal(conn, 2026, 1)
    dias = resultado["dias_semana_abrev"]
    assert len(dias) == 31
    assert dias[0] == "Qi"  # 2026-01-01 e quinta
    assert dias[4] == "Sg"  # 2026-01-05
    assert dias[9] == "Sb"  # 2026-01-10
    assert dias[10] == "Do"  # 2026-01-11


def test_montar_folha_ponto_mensal_ordena_por_categoria_e_vinculo(conn):
    turno_id = conn.execute(
        """INSERT INTO turnos (nome, horario_entrada, horario_saida, tolerancia_minutos, dias_semana)
           VALUES ('turno', '08:00', '17:00', 10, '0,1,2,3,4')"""
    ).lastrowid

    def criar(nome, codigo, categoria, vinculo):
        conn.execute(
            """INSERT INTO funcionarios (nome, codigo, situacao, turno_id, categoria, vinculo)
               VALUES (?, ?, 'ativo', ?, ?, ?)""",
            (nome, codigo, turno_id, categoria, vinculo),
        )

    # insere fora de ordem de proposito
    criar("Zelia Outros", "Z1", None, None)
    criar("Bruno Professor Contratado", "B1", "professor", "contratado")
    criar("Ana Professor Efetivo", "A1", "professor", "efetivo")
    criar("Diretora Gestao", "D1", "gestao", None)
    criar("Coordenadora Apoio", "C1", "apoio_pedagogico", None)
    conn.commit()

    resultado = montar_folha_ponto_mensal(conn, 2026, 1)
    nomes = [linha["funcionario"]["nome"] for linha in resultado["linhas"]]
    assert nomes == [
        "Diretora Gestao",
        "Coordenadora Apoio",
        "Ana Professor Efetivo",
        "Bruno Professor Contratado",
        "Zelia Outros",
    ]


def test_montar_folha_ponto_mensal_ausencia_prevalece_sobre_presenca(conn):
    turno_id = conn.execute(
        """INSERT INTO turnos (nome, horario_entrada, horario_saida, tolerancia_minutos, dias_semana)
           VALUES ('turno', '08:00', '17:00', 10, '0,1,2,3,4')"""
    ).lastrowid
    funcionario_id = conn.execute(
        "INSERT INTO funcionarios (nome, codigo, situacao, turno_id) VALUES ('Dupla Marcacao', 'DM1', 'ativo', ?)",
        (turno_id,),
    ).lastrowid
    conn.execute(
        "INSERT INTO registros (funcionario_id, tipo, timestamp) VALUES (?, 'entrada', '2026-01-05 08:00:00')",
        (funcionario_id,),
    )
    conn.execute(
        "INSERT INTO ausencias (funcionario_id, data, tipo, criado_em) VALUES (?, '2026-01-05', 'atestado', datetime('now'))",
        (funcionario_id,),
    )
    conn.commit()

    resultado = montar_folha_ponto_mensal(conn, 2026, 1, funcionario_id=funcionario_id)
    assert resultado["linhas"][0]["codigos"][4] == "AT"  # ausencia vence, nao vira "P"


def test_calcular_stats_funcionarios_periodo_vazio(conn):
    assert calcular_stats_funcionarios_periodo(conn, "2026-01-05", "2026-01-09") == {}


def test_calcular_stats_funcionarios_periodo_valores_brutos(conn, cenario_semana):
    stats = calcular_stats_funcionarios_periodo(conn, "2026-01-05", "2026-01-09")

    ana = stats[cenario_semana["ana_id"]]
    assert ana["atraso_acumulado_seg"] == 0
    assert ana["atrasos"] == 0
    assert ana["faltas"] == 0
    assert ana["total_trabalhado_seg"] == 5 * 8 * 3600

    beto = stats[cenario_semana["beto_id"]]
    assert beto["atrasos"] == 1
    assert beto["atraso_acumulado_seg"] == 15 * 60  # 15 minutos em segundos
    assert beto["faltas"] == 1
    assert beto["total_trabalhado_seg"] == 28800 + 30900 + 28800


def test_calcular_stats_funcionarios_periodo_filtra_por_funcionario(conn, cenario_semana):
    stats = calcular_stats_funcionarios_periodo(conn, "2026-01-05", "2026-01-09", cenario_semana["ana_id"])
    assert list(stats.keys()) == [cenario_semana["ana_id"]]


# ---------- Ranking de pontualidade/atrasos ----------


def test_montar_ranking_atrasos_exclui_quem_nao_tem_atraso(conn, cenario_semana):
    ranking = montar_ranking_atrasos(conn, "2026-01-05", "2026-01-09")
    nomes = [r["nome"] for r in ranking]
    assert "Ana Pontual" not in nomes
    assert "Beto Testado" in nomes


def test_montar_ranking_atrasos_ordena_por_atraso_acumulado(conn, cenario_ranking):
    ranking = montar_ranking_atrasos(conn, "2026-01-05", "2026-01-09", top_n=5)
    assert [r["nome"] for r in ranking] == ["Carlos Atrasado", "Beto Testado"]
    assert ranking[0]["atraso_acumulado_minutos"] == 50
    assert ranking[1]["atraso_acumulado_minutos"] == 15


def test_montar_ranking_atrasos_respeita_top_n(conn, cenario_ranking):
    ranking = montar_ranking_atrasos(conn, "2026-01-05", "2026-01-09", top_n=1)
    assert len(ranking) == 1
    assert ranking[0]["nome"] == "Carlos Atrasado"


# ---------- Comparativo mensal ----------


def test_calcular_meses_fechados_meio_do_ano():
    meses = calcular_meses_fechados(6, hoje=date(2026, 8, 3))
    assert meses == [(2026, 2), (2026, 3), (2026, 4), (2026, 5), (2026, 6), (2026, 7)]


def test_calcular_meses_fechados_virada_de_ano():
    meses = calcular_meses_fechados(6, hoje=date(2026, 2, 15))
    assert meses == [(2025, 8), (2025, 9), (2025, 10), (2025, 11), (2025, 12), (2026, 1)]


def test_montar_comparativo_mensal_soma_por_mes(conn, cenario_dois_meses):
    comparativo = montar_comparativo_mensal(conn, quantidade_meses=2, hoje=date(2026, 3, 1))
    por_mes = {(item["ano"], item["mes"]): item for item in comparativo}

    assert list(por_mes.keys()) == [(2026, 1), (2026, 2)]
    assert por_mes[(2026, 1)]["total_trabalhado_horas"] == 18.0  # 2 dias * 9h
    assert por_mes[(2026, 1)]["total_atrasos"] == 0
    assert por_mes[(2026, 2)]["total_trabalhado_horas"] == 9.0  # 1 dia * 9h


# ---------- Feriados (integrados na cascata acima) ----------


def _cadastrar_feriado(conn, data_iso, nome="Feriado Teste"):
    conn.execute(
        "INSERT INTO feriados (data, nome, criado_em) VALUES (?, ?, datetime('now'))", (data_iso, nome)
    )
    conn.commit()


def test_calcular_stats_funcionarios_periodo_feriado_nao_conta_falta(conn, cenario_semana):
    # antes do feriado, o dia 07/01 (quarta) era falta nao justificada do Beto
    stats_antes = calcular_stats_funcionarios_periodo(conn, "2026-01-05", "2026-01-09")
    assert stats_antes[cenario_semana["beto_id"]]["faltas"] == 1

    _cadastrar_feriado(conn, "2026-01-07")

    stats_depois = calcular_stats_funcionarios_periodo(conn, "2026-01-05", "2026-01-09")
    assert stats_depois[cenario_semana["beto_id"]]["faltas"] == 0
    assert stats_depois[cenario_semana["beto_id"]]["dias_uteis"] == 4  # 5 dias uteis da semana - 1 que virou feriado


def test_montar_folha_ponto_mensal_feriado_mostra_fd_sem_atividade(conn, cenario_semana):
    _cadastrar_feriado(conn, "2026-01-07")  # dia da falta do Beto vira feriado

    resultado = montar_folha_ponto_mensal(conn, 2026, 1)
    por_nome = {linha["funcionario"]["nome"]: linha["codigos"] for linha in resultado["linhas"]}

    assert por_nome["Beto Testado"][6] == "FD"  # dia 7 (indice 6): sem ponto, sem ausencia -> feriado


def test_montar_folha_ponto_mensal_presenca_prevalece_sobre_feriado(conn, cenario_semana):
    _cadastrar_feriado(conn, "2026-01-05")  # dia em que Ana bateu ponto normalmente

    resultado = montar_folha_ponto_mensal(conn, 2026, 1)
    por_nome = {linha["funcionario"]["nome"]: linha["codigos"] for linha in resultado["linhas"]}

    # Ana trabalhou nesse feriado -> continua "P", nao vira "FD"
    assert por_nome["Ana Pontual"][4] == "P"


def test_montar_folha_ponto_mensal_ausencia_prevalece_sobre_feriado(conn, cenario_semana):
    _cadastrar_feriado(conn, "2026-01-08")  # dia em que Beto tem ausencia (ferias)

    resultado = montar_folha_ponto_mensal(conn, 2026, 1)
    por_nome = {linha["funcionario"]["nome"]: linha["codigos"] for linha in resultado["linhas"]}

    # ausencia (ferias) continua prevalecendo sobre o feriado
    assert por_nome["Beto Testado"][7] == "FE"


# ---------- Correções da auditoria (2026-10) ----------

from relatorios_service import calcular_saldo_banco_horas_ate, montar_extrato_banco_horas  # noqa: E402


def test_banco_de_horas_nao_debita_feriado(conn, cenario_semana):
    ana_id = cenario_semana["ana_id"]
    conn.execute("UPDATE funcionarios SET banco_horas_inicio = '2026-01-05' WHERE id = ?", (ana_id,))
    conn.execute("DELETE FROM registros WHERE funcionario_id = ? AND timestamp LIKE '2026-01-06%'", (ana_id,))
    conn.execute("INSERT INTO feriados (data, nome, criado_em) VALUES ('2026-01-06', 'Feriado teste', datetime('now'))")
    conn.commit()
    f = conn.execute(
        "SELECT funcionarios.*, turnos.horario_entrada, turnos.horario_saida, turnos.horario_saida_almoco, "
        "turnos.horario_retorno_almoco, turnos.dias_semana AS turno_dias_semana, "
        "NULL AS escala_ciclo_dias_trabalho, NULL AS escala_ciclo_dias_descanso "
        "FROM funcionarios JOIN turnos ON turnos.id = funcionarios.turno_id WHERE funcionarios.id = ?",
        (ana_id,),
    ).fetchone()

    assert calcular_saldo_banco_horas_ate(conn, f, date(2026, 1, 9)) == 0
    _, _, _, saldo_final = montar_extrato_banco_horas(conn, ana_id, "2026-01-05", "2026-01-09")
    assert saldo_final == 0


def test_horas_esperadas_contam_os_dias_de_falta(conn, cenario_semana):
    beto = calcular_stats_funcionarios_periodo(conn, "2026-01-05", "2026-01-09")[cenario_semana["beto_id"]]
    # 5 dias úteis menos 1 de férias justificadas = 4 x 8h (a falta de quarta também é esperada)
    assert beto["horas_esperadas_seg"] == 4 * 8 * 3600


def test_funcionario_que_saiu_continua_nos_relatorios_do_periodo_trabalhado(conn, cenario_semana):
    beto_id = cenario_semana["beto_id"]
    conn.execute("UPDATE funcionarios SET situacao = 'inativo' WHERE id = ?", (beto_id,))
    conn.commit()

    stats = calcular_stats_funcionarios_periodo(conn, "2026-01-05", "2026-01-31")
    assert beto_id in stats
    assert stats[beto_id]["faltas"] == 1  # só a quarta; depois do dia 9 (última atividade) não conta

    folha = montar_folha_ponto_mensal(conn, 2026, 1)
    linha_beto = next(l for l in folha["linhas"] if l["funcionario"]["id"] == beto_id)
    assert linha_beto["codigos"][8] == "P"  # dia 9
    assert linha_beto["codigos"][11] == ""  # dia 12, depois de sair: em branco, não "F"

    # período sem nenhuma atividade dele: não aparece
    assert beto_id not in calcular_stats_funcionarios_periodo(conn, "2026-02-01", "2026-02-28")


def test_hoje_ainda_nao_conta_como_falta(conn):
    turno_id = conn.execute(
        "INSERT INTO turnos (nome, horario_entrada, horario_saida, tolerancia_minutos, dias_semana) "
        "VALUES ('todo dia', '08:00', '17:00', 10, '0,1,2,3,4,5,6')"
    ).lastrowid
    func_id = conn.execute(
        "INSERT INTO funcionarios (nome, codigo, situacao, turno_id) VALUES ('Eva Hoje', 'EEE555', 'ativo', ?)",
        (turno_id,),
    ).lastrowid
    conn.commit()
    hoje = date.today().isoformat()

    stats = calcular_stats_funcionarios_periodo(conn, hoje, hoje)[func_id]
    assert stats["faltas"] == 0
    assert stats["horas_esperadas_seg"] == 0
