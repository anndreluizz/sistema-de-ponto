from datetime import date

from relatorios_service import (
    calcular_meses_fechados,
    calcular_stats_funcionarios_periodo,
    dia_e_util_para_funcionario,
    duracao_turno_segundos,
    formatar_horas,
    horas_esperadas_no_dia_segundos,
    montar_comparativo_mensal,
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
