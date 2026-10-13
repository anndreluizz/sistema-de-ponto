"""Lógica de cálculo de relatórios e histórico de ponto — sem rotas, sem I/O externo."""
import calendar
import json
from datetime import date, datetime, timedelta

DIAS_UTEIS = (0, 1, 2, 3, 4)  # datetime.weekday(): segunda=0 ... sexta=4

SQL_JOIN_TURNO_EFETIVO = """
    LEFT JOIN turnos turno_direto ON turno_direto.id = funcionarios.turno_id
    LEFT JOIN escalas ON escalas.id = funcionarios.escala_id
    LEFT JOIN turnos turno_escala ON turno_escala.id = escalas.turno_id
"""
SQL_COLUNAS_TURNO_EFETIVO = """
    COALESCE(turno_direto.horario_entrada, turno_escala.horario_entrada) AS horario_entrada,
    COALESCE(turno_direto.horario_saida, turno_escala.horario_saida) AS horario_saida,
    COALESCE(turno_direto.tolerancia_minutos, turno_escala.tolerancia_minutos) AS tolerancia_minutos,
    COALESCE(turno_direto.tolerancia_saida_minutos, turno_escala.tolerancia_saida_minutos) AS tolerancia_saida_minutos,
    COALESCE(turno_direto.horario_saida_almoco, turno_escala.horario_saida_almoco) AS horario_saida_almoco,
    COALESCE(turno_direto.horario_retorno_almoco, turno_escala.horario_retorno_almoco) AS horario_retorno_almoco,
    COALESCE(turno_direto.dias_semana, turno_escala.dias_semana) AS turno_dias_semana,
    COALESCE(turno_direto.nome, escalas.nome) AS turno_ou_escala_nome,
    escalas.ciclo_dias_trabalho AS escala_ciclo_dias_trabalho,
    escalas.ciclo_dias_descanso AS escala_ciclo_dias_descanso
"""

# --- Histórico de jornada -------------------------------------------------------------------
# Tudo que decide "esse dia era de trabalho? quantas horas? atrasou?" para um funcionário.
# (O nome do turno fica de fora: renomear não muda a jornada.)
COLUNAS_JORNADA = (
    "turno_id", "escala_id", "escala_data_referencia",
    "horario_entrada", "horario_saida", "tolerancia_minutos", "tolerancia_saida_minutos",
    "horario_saida_almoco", "horario_retorno_almoco", "turno_dias_semana",
    "escala_ciclo_dias_trabalho", "escala_ciclo_dias_descanso",
)


class FuncionarioComHistorico(dict):
    """Linha de funcionário (jornada atual) + `historico`: [(vigente_ate_iso, linha_naquela_época)],
    em ordem crescente de vigente_ate."""
    historico = ()


def jornadas_atuais(conn):
    """{funcionario_id: {coluna: valor}} com a jornada efetiva de hoje de todo mundo."""
    linhas = conn.execute(
        f"""SELECT funcionarios.id, funcionarios.turno_id, funcionarios.escala_id,
                   funcionarios.escala_data_referencia, {SQL_COLUNAS_TURNO_EFETIVO}
            FROM funcionarios {SQL_JOIN_TURNO_EFETIVO}"""
    ).fetchall()
    return {linha["id"]: {c: linha[c] for c in COLUNAS_JORNADA} for linha in linhas}


def registrar_mudancas_de_jornada(conn, jornadas_antes, hoje=None):
    """Chamar DEPOIS de alterar funcionário/turno/escala, passando jornadas_atuais() de ANTES.
    Quem teve a jornada mudada ganha uma foto da antiga valendo até ontem — a nova vale de hoje
    em diante. Várias mudanças no mesmo dia: fica a foto da primeira (a que valia até ontem).
    Não faz commit (quem chama faz)."""
    ontem = ((hoje or date.today()) - timedelta(days=1)).isoformat()
    depois = jornadas_atuais(conn)
    for funcionario_id, antes in jornadas_antes.items():
        if depois.get(funcionario_id) != antes:
            conn.execute(
                """INSERT OR IGNORE INTO jornada_historico (funcionario_id, vigente_ate, dados, criado_em)
                   VALUES (?, ?, ?, datetime('now', 'localtime'))""",
                (funcionario_id, ontem, json.dumps(antes)),
            )


def anexar_historico_jornada(conn, funcionarios):
    """Transforma linhas de funcionário em FuncionarioComHistorico (mesmas chaves + histórico)."""
    funcionarios = list(funcionarios)
    if not funcionarios:
        return []
    ids = [f["id"] for f in funcionarios]
    placeholders = ",".join("?" * len(ids))
    por_funcionario = {}
    for linha in conn.execute(
        f"""SELECT funcionario_id, vigente_ate, dados FROM jornada_historico
            WHERE funcionario_id IN ({placeholders}) ORDER BY vigente_ate""",
        ids,
    ).fetchall():
        por_funcionario.setdefault(linha["funcionario_id"], []).append((linha["vigente_ate"], linha["dados"]))

    resultado = []
    for f in funcionarios:
        novo = FuncionarioComHistorico({chave: f[chave] for chave in f.keys()})
        novo.historico = [
            (vigente_ate, {**novo, **json.loads(dados)}) for vigente_ate, dados in por_funcionario.get(f["id"], [])
        ]
        resultado.append(novo)
    return resultado


def jornada_na_data(f, data_obj):
    """A linha do funcionário com a jornada que valia em `data_obj` (a atual, se não houver
    foto mais antiga cobrindo essa data). Aceita linha sem histórico (devolve ela mesma)."""
    historico = getattr(f, "historico", ())
    if historico:
        iso = data_obj.isoformat()
        for vigente_ate, linha in historico:
            if iso <= vigente_ate:
                return linha
    return f


COLUNAS_POR_VISAO = {
    "frequencia": ["Dias com ponto", "Faltas", "Atrasos", "Horas trabalhadas"],
    "faltas": ["Dias úteis", "Faltas", "% de faltas"],
    "atrasos": ["Dias com ponto", "Atrasos", "Atraso acumulado", "Saídas antecipadas"],
    "horas_extras": ["Horas trabalhadas", "Horas esperadas", "Horas extras"],
    "banco_horas": ["Horas trabalhadas", "Horas esperadas", "Saldo", "Saldo acumulado"],
    "ausencias": [
        "Atestados", "Licenças", "Férias", "Folgas",
        "Lic. maternidade/paternidade", "Falecimento", "Abono legal",
    ],
}
CHAVES_POR_VISAO = {
    "frequencia": ["dias_com_ponto", "faltas", "atrasos", "horas_trabalhadas"],
    "faltas": ["dias_uteis", "faltas", "percentual_faltas"],
    "atrasos": ["dias_com_ponto", "atrasos", "atraso_acumulado", "saidas_antecipadas"],
    "horas_extras": ["horas_trabalhadas", "horas_esperadas", "horas_extras"],
    "banco_horas": ["horas_trabalhadas", "horas_esperadas", "saldo", "saldo_acumulado"],
    "ausencias": [
        "atestados", "licencas", "ferias", "folgas",
        "licencas_maternidade_paternidade", "falecimentos", "abonos_legais",
    ],
}
# Quem aparece num relatório de período: os ativos, mais quem saiu (inativo/férias/afastado)
# mas tem ponto ou ausência dentro do período — senão mudar a situação apagaria o histórico.
# Parâmetros: (data_inicio, data_fim, data_inicio, data_fim).
SQL_FUNCIONARIO_NO_PERIODO = """(funcionarios.situacao = 'ativo'
    OR EXISTS (SELECT 1 FROM registros r WHERE r.funcionario_id = funcionarios.id
               AND r.timestamp >= date(?) AND r.timestamp < date(?, '+1 day'))
    OR EXISTS (SELECT 1 FROM ausencias a WHERE a.funcionario_id = funcionarios.id
               AND a.data BETWEEN ? AND ?))"""


def limitar_dias_de_quem_saiu(f, datas, datas_com_atividade):
    """Para quem não está mais ativo, só considera dias até a última atividade no período.
    ponytail: sem histórico de mudança de situação; tabela de histórico se precisar da data exata."""
    if f["situacao"] == "ativo":
        return datas
    if not datas_com_atividade:
        return []
    ultima = max(datas_com_atividade)
    return [d for d in datas if d <= ultima]


ROTULOS_DIMENSAO = {"funcionario": "Funcionário", "setor": "Setor", "cargo": "Cargo", "turno": "Turno"}


def colunas_para_visao(visao, agrupar_por):
    rotulo_dimensao = ROTULOS_DIMENSAO.get(agrupar_por, "Funcionário")
    return [rotulo_dimensao] + COLUNAS_POR_VISAO.get(visao, COLUNAS_POR_VISAO["frequencia"])


def linhas_para_exportacao(linhas, visao):
    chaves = CHAVES_POR_VISAO.get(visao, CHAVES_POR_VISAO["frequencia"])
    return [[linha["dimensao"]] + [linha.get(chave, "-") for chave in chaves] for linha in linhas]


def agrupar_registros_por_dia(registros):
    grupos = {}
    ordem = []
    for r in registros:
        ts = datetime.strptime(r["timestamp"], "%Y-%m-%d %H:%M:%S")
        chave = (r["funcionario_id"], r["funcionario_nome"], ts.date())
        if chave not in grupos:
            grupos[chave] = []
            ordem.append(chave)
        grupos[chave].append(r)

    dias = []
    for chave in ordem:
        funcionario_id, funcionario_nome, data_dia = chave
        eventos = grupos[chave]
        pares = []
        total_segundos = 0
        entrada_pendente = None

        for evento in eventos:
            if evento["tipo"] == "entrada":
                if entrada_pendente is not None:
                    pares.append({"entrada": entrada_pendente, "saida": None})
                entrada_pendente = evento
            else:
                if entrada_pendente is not None:
                    ts_entrada = datetime.strptime(entrada_pendente["timestamp"], "%Y-%m-%d %H:%M:%S")
                    ts_saida = datetime.strptime(evento["timestamp"], "%Y-%m-%d %H:%M:%S")
                    total_segundos += (ts_saida - ts_entrada).total_seconds()
                    pares.append({"entrada": entrada_pendente, "saida": evento})
                    entrada_pendente = None
                else:
                    pares.append({"entrada": None, "saida": evento})

        if entrada_pendente is not None:
            pares.append({"entrada": entrada_pendente, "saida": None})

        horas = int(total_segundos // 3600)
        minutos = int((total_segundos % 3600) // 60)
        dias.append(
            {
                "funcionario_id": funcionario_id,
                "funcionario_nome": funcionario_nome,
                "data": data_dia,
                "pares": pares,
                "total_segundos": total_segundos,
                "total_horas": f"{horas:02d}:{minutos:02d}",
            }
        )
    return dias


def formatar_horas(total_segundos):
    if total_segundos is None:
        return "-"
    sinal = "-" if total_segundos < 0 else ""
    total_segundos = abs(total_segundos)
    horas = int(total_segundos // 3600)
    minutos = int((total_segundos % 3600) // 60)
    return f"{sinal}{horas:02d}:{minutos:02d}"


def duracao_turno_segundos(horario_entrada, horario_saida, horario_saida_almoco=None, horario_retorno_almoco=None):
    entrada = datetime.strptime(horario_entrada, "%H:%M")
    saida = datetime.strptime(horario_saida, "%H:%M")
    delta = saida - entrada
    if delta.total_seconds() <= 0:
        delta += timedelta(days=1)
    if horario_saida_almoco and horario_retorno_almoco:
        inicio_almoco = datetime.strptime(horario_saida_almoco, "%H:%M")
        fim_almoco = datetime.strptime(horario_retorno_almoco, "%H:%M")
        duracao_almoco = fim_almoco - inicio_almoco
        if duracao_almoco.total_seconds() <= 0:
            duracao_almoco += timedelta(days=1)
        delta -= duracao_almoco
    return max(0, delta.total_seconds())


def dia_e_util_para_funcionario(f, data_obj, feriados=None):
    """Diz se `data_obj` é dia de trabalho para esse funcionário (turno direto, escala em ciclo, ou
    fallback legado). `feriados`, se passado, é um conjunto de datas ISO ("AAAA-MM-DD") — nesses
    dias ninguém tem expediente, não importa o turno/escala."""
    if feriados and data_obj.isoformat() in feriados:
        return False
    f = jornada_na_data(f, data_obj)
    if f["escala_id"] is not None and f["escala_data_referencia"]:
        referencia = datetime.strptime(f["escala_data_referencia"], "%Y-%m-%d").date()
        ciclo_total = (f["escala_ciclo_dias_trabalho"] or 0) + (f["escala_ciclo_dias_descanso"] or 0)
        if ciclo_total <= 0:
            return True
        deslocamento = (data_obj - referencia).days % ciclo_total
        return deslocamento < f["escala_ciclo_dias_trabalho"]
    if f["turno_id"] is not None:
        dias = f["turno_dias_semana"]
        if not dias:
            return True
        return data_obj.weekday() in {int(d) for d in dias.split(",") if d.strip() != ""}
    return data_obj.weekday() in DIAS_UTEIS


def horas_esperadas_no_dia_segundos(f, data_obj, feriados=None):
    """Segundos esperados de trabalho nesse dia para esse funcionário — 0 se for dia de folga
    ou feriado."""
    f = jornada_na_data(f, data_obj)
    if f["horario_entrada"] is None or not dia_e_util_para_funcionario(f, data_obj, feriados):
        return 0
    return duracao_turno_segundos(
        f["horario_entrada"], f["horario_saida"], f["horario_saida_almoco"], f["horario_retorno_almoco"]
    )


def delta_banco_no_dia(trabalhado_seg, esperado_seg, data_obj):
    """Hoje (dia ainda em andamento) e dias futuros só podem somar hora extra, nunca débito —
    senão às 7h da manhã todo mundo já aparece devendo o dia inteiro."""
    delta = trabalhado_seg - esperado_seg
    return max(0, delta) if data_obj >= date.today() else delta


def obter_feriados_no_periodo(conn, data_inicio, data_fim):
    """Conjunto de datas ISO ("AAAA-MM-DD") cadastradas como feriado dentro do período
    (data_inicio/data_fim já em formato ISO string)."""
    linhas = conn.execute(
        "SELECT data FROM feriados WHERE data BETWEEN ? AND ?", (data_inicio, data_fim)
    ).fetchall()
    return {linha["data"] for linha in linhas}


def calcular_atalhos_periodo(hoje=None):
    hoje = hoje or date.today()
    inicio_semana = hoje - timedelta(days=hoje.weekday())
    return {
        "hoje": (hoje, hoje),
        "semana": (inicio_semana, hoje),
        "mes": (hoje.replace(day=1), hoje),
        "ano": (hoje.replace(month=1, day=1), hoje),
    }


def preencher_colunas_historico(pares):
    """Sempre retorna (entrada, saida_almoco, retorno, saida) a partir dos pares de um dia."""
    if len(pares) == 2:
        return pares[0]["entrada"], pares[0]["saida"], pares[1]["entrada"], pares[1]["saida"]
    if len(pares) == 1:
        return pares[0]["entrada"], None, None, pares[0]["saida"]
    if len(pares) == 0:
        return None, None, None, None
    return pares[0]["entrada"], None, None, pares[-1]["saida"]


def montar_historico_funcionario(conn, funcionario_id, data_inicio, data_fim):
    funcionario = conn.execute(
        f"""SELECT funcionarios.*, setores.nome AS setor_nome,
                   {SQL_COLUNAS_TURNO_EFETIVO}
           FROM funcionarios
           {SQL_JOIN_TURNO_EFETIVO}
           LEFT JOIN setores ON setores.id = funcionarios.setor_id
           WHERE funcionarios.id = ?""",
        (funcionario_id,),
    ).fetchone()
    if not funcionario:
        return None, []
    funcionario = anexar_historico_jornada(conn, [funcionario])[0]

    registros = conn.execute(
        """SELECT registros.*, funcionarios.nome AS funcionario_nome
           FROM registros
           JOIN funcionarios ON funcionarios.id = registros.funcionario_id
           WHERE registros.funcionario_id = ? AND registros.timestamp >= date(?) AND registros.timestamp < date(?, '+1 day')
           ORDER BY registros.timestamp""",
        (funcionario_id, data_inicio, data_fim),
    ).fetchall()

    dias_por_data = {dia["data"]: dia for dia in agrupar_registros_por_dia(registros)}

    ausencias = conn.execute(
        "SELECT * FROM ausencias WHERE funcionario_id = ? AND data BETWEEN ? AND ? ORDER BY data",
        (funcionario_id, data_inicio, data_fim),
    ).fetchall()
    ausencias_por_data = {
        datetime.strptime(a["data"], "%Y-%m-%d").date(): a for a in ausencias
    }
    feriados = obter_feriados_no_periodo(conn, data_inicio, data_fim)

    todas_as_datas = sorted(set(dias_por_data.keys()) | set(ausencias_por_data.keys()))

    linhas = []
    for data_dia in todas_as_datas:
        dia = dias_por_data.get(data_dia)
        ausencia = ausencias_por_data.get(data_dia)

        pares = dia["pares"] if dia else []
        total_segundos = dia["total_segundos"] if dia else 0
        total_horas = dia["total_horas"] if dia else "00:00"

        entrada, saida_almoco, retorno, saida = preencher_colunas_historico(pares)

        if ausencia is not None or jornada_na_data(funcionario, data_dia)["horario_entrada"] is None:
            horas_extras_texto = "-"
        else:
            horas_extras_texto = formatar_horas(
                max(0, total_segundos - horas_esperadas_no_dia_segundos(funcionario, data_dia, feriados))
            )

        linhas.append(
            {
                "data": data_dia,
                "entrada": entrada,
                "saida_almoco": saida_almoco,
                "retorno": retorno,
                "saida": saida,
                "pares": pares,
                "total_horas": total_horas,
                "horas_extras": horas_extras_texto,
                "ausencia": ausencia,
            }
        )

    return funcionario, linhas


def calcular_saldo_banco_horas_ate(conn, f, ate_data):
    """Saldo (segundos, sinalizado) acumulado desde f['banco_horas_inicio'] até ate_data (inclusive).
    Retorna None se o funcionário não tiver banco de horas habilitado."""
    if not f["banco_horas_inicio"]:
        return None

    data_inicio = f["banco_horas_inicio"]
    data_fim = ate_data.isoformat() if hasattr(ate_data, "isoformat") else ate_data
    data_ini_obj = datetime.strptime(data_inicio, "%Y-%m-%d").date()
    data_fim_obj = datetime.strptime(data_fim, "%Y-%m-%d").date() if isinstance(data_fim, str) else ate_data

    if data_fim_obj < data_ini_obj:
        return 0

    registros = conn.execute(
        """SELECT registros.*, funcionarios.nome AS funcionario_nome
           FROM registros JOIN funcionarios ON funcionarios.id = registros.funcionario_id
           WHERE registros.funcionario_id = ? AND registros.timestamp >= date(?) AND registros.timestamp < date(?, '+1 day')
           ORDER BY registros.timestamp""",
        (f["id"], data_ini_obj.isoformat(), data_fim_obj.isoformat()),
    ).fetchall()
    dias_por_data = {dia["data"]: dia for dia in agrupar_registros_por_dia(registros)}

    ausencias_datas = {
        row["data"]
        for row in conn.execute(
            "SELECT data FROM ausencias WHERE funcionario_id = ? AND data BETWEEN ? AND ?",
            (f["id"], data_ini_obj.isoformat(), data_fim_obj.isoformat()),
        ).fetchall()
    }

    feriados = obter_feriados_no_periodo(conn, data_ini_obj.isoformat(), data_fim_obj.isoformat())

    saldo_seg = 0
    cursor = data_ini_obj
    while cursor <= data_fim_obj:
        if cursor.isoformat() not in ausencias_datas:
            dia = dias_por_data.get(cursor)
            trabalhado = dia["total_segundos"] if dia else 0
            esperado = horas_esperadas_no_dia_segundos(f, cursor, feriados)
            saldo_seg += delta_banco_no_dia(trabalhado, esperado, cursor)
        cursor += timedelta(days=1)

    compensacoes_total = conn.execute(
        """SELECT COALESCE(SUM(segundos), 0) AS total FROM compensacoes_banco_horas
           WHERE funcionario_id = ? AND data BETWEEN ? AND ?""",
        (f["id"], data_ini_obj.isoformat(), data_fim_obj.isoformat()),
    ).fetchone()["total"]

    return saldo_seg + compensacoes_total


def montar_extrato_banco_horas(conn, funcionario_id, data_inicio, data_fim):
    funcionario = conn.execute(
        f"""SELECT funcionarios.*, {SQL_COLUNAS_TURNO_EFETIVO}
            FROM funcionarios
            {SQL_JOIN_TURNO_EFETIVO}
            WHERE funcionarios.id = ?""",
        (funcionario_id,),
    ).fetchone()
    if not funcionario:
        return None, None, [], None
    if not funcionario["banco_horas_inicio"]:
        return funcionario, None, [], None
    funcionario = anexar_historico_jornada(conn, [funcionario])[0]

    banco_inicio_obj = datetime.strptime(funcionario["banco_horas_inicio"], "%Y-%m-%d").date()
    data_ini_obj = datetime.strptime(data_inicio, "%Y-%m-%d").date()
    data_fim_obj = datetime.strptime(data_fim, "%Y-%m-%d").date()
    data_ini_efetiva = max(data_ini_obj, banco_inicio_obj)

    dia_anterior = data_ini_efetiva - timedelta(days=1)
    saldo_anterior_seg = calcular_saldo_banco_horas_ate(conn, funcionario, dia_anterior) or 0

    registros = conn.execute(
        """SELECT registros.*, funcionarios.nome AS funcionario_nome
           FROM registros JOIN funcionarios ON funcionarios.id = registros.funcionario_id
           WHERE registros.funcionario_id = ? AND registros.timestamp >= date(?) AND registros.timestamp < date(?, '+1 day')
           ORDER BY registros.timestamp""",
        (funcionario_id, data_ini_efetiva.isoformat(), data_fim_obj.isoformat()),
    ).fetchall()
    dias_por_data = {dia["data"]: dia for dia in agrupar_registros_por_dia(registros)}

    ausencias = conn.execute(
        "SELECT * FROM ausencias WHERE funcionario_id = ? AND data BETWEEN ? AND ?",
        (funcionario_id, data_ini_efetiva.isoformat(), data_fim_obj.isoformat()),
    ).fetchall()
    ausencias_por_data = {a["data"]: a for a in ausencias}

    compensacoes = conn.execute(
        "SELECT * FROM compensacoes_banco_horas WHERE funcionario_id = ? AND data BETWEEN ? AND ? ORDER BY data, id",
        (funcionario_id, data_ini_efetiva.isoformat(), data_fim_obj.isoformat()),
    ).fetchall()
    compensacoes_por_data = {}
    for c in compensacoes:
        compensacoes_por_data.setdefault(c["data"], []).append(c)

    feriados = obter_feriados_no_periodo(conn, data_ini_efetiva.isoformat(), data_fim_obj.isoformat())

    linhas = []
    saldo_seg = saldo_anterior_seg
    cursor = data_ini_efetiva
    while cursor <= data_fim_obj:
        cursor_iso = cursor.isoformat()
        ausencia = ausencias_por_data.get(cursor_iso)
        dia = dias_por_data.get(cursor)
        trabalhado_seg = dia["total_segundos"] if dia else 0
        esperado_seg = horas_esperadas_no_dia_segundos(funcionario, cursor, feriados)
        delta_automatico_seg = 0 if ausencia else delta_banco_no_dia(trabalhado_seg, esperado_seg, cursor)
        compensacoes_dia = compensacoes_por_data.get(cursor_iso, [])
        delta_compensacoes_seg = sum(c["segundos"] for c in compensacoes_dia)
        saldo_seg += delta_automatico_seg + delta_compensacoes_seg

        linhas.append(
            {
                "data": cursor,
                "trabalhado": formatar_horas(trabalhado_seg),
                "esperado": formatar_horas(esperado_seg),
                "delta_automatico": formatar_horas(delta_automatico_seg),
                "compensacoes": compensacoes_dia,
                "ausencia": ausencia,
                "saldo_apos_o_dia": formatar_horas(saldo_seg),
            }
        )
        cursor += timedelta(days=1)

    return funcionario, saldo_anterior_seg, linhas, saldo_seg


def calcular_horas_extras_hoje(conn):
    hoje = date.today().isoformat()
    feriados = obter_feriados_no_periodo(conn, hoje, hoje)
    funcionarios = conn.execute(
        f"""SELECT funcionarios.id, funcionarios.turno_id, funcionarios.escala_id,
                   funcionarios.escala_data_referencia,
                   {SQL_COLUNAS_TURNO_EFETIVO}
           FROM funcionarios
           {SQL_JOIN_TURNO_EFETIVO}
           WHERE funcionarios.situacao = 'ativo'"""
    ).fetchall()
    funcionarios = [f for f in funcionarios if f["horario_entrada"] is not None]
    if not funcionarios:
        return 0

    ids_funcionarios = [f["id"] for f in funcionarios]
    placeholders = ",".join("?" * len(ids_funcionarios))
    registros = conn.execute(
        f"""SELECT registros.*, funcionarios.nome AS funcionario_nome
            FROM registros
            JOIN funcionarios ON funcionarios.id = registros.funcionario_id
            WHERE registros.funcionario_id IN ({placeholders}) AND registros.timestamp >= ? AND registros.timestamp < date(?, '+1 day')
            ORDER BY funcionarios.nome, registros.timestamp""",
        [*ids_funcionarios, hoje, hoje],
    ).fetchall()

    dias_por_funcionario = {}
    for dia in agrupar_registros_por_dia(registros):
        dias_por_funcionario.setdefault(dia["funcionario_id"], []).append(dia)

    total_extras_seg = 0
    for f in funcionarios:
        for dia in dias_por_funcionario.get(f["id"], []):
            total_extras_seg += max(
                0, dia["total_segundos"] - horas_esperadas_no_dia_segundos(f, dia["data"], feriados)
            )
    return total_extras_seg


def montar_espelho_mensal(conn, funcionario_id, ano, mes):
    funcionario = conn.execute(
        f"""SELECT funcionarios.*, setores.nome AS setor_nome,
                   {SQL_COLUNAS_TURNO_EFETIVO}
           FROM funcionarios
           {SQL_JOIN_TURNO_EFETIVO}
           LEFT JOIN setores ON setores.id = funcionarios.setor_id
           WHERE funcionarios.id = ?""",
        (funcionario_id,),
    ).fetchone()
    if not funcionario:
        return None, [], {}
    funcionario = anexar_historico_jornada(conn, [funcionario])[0]

    _, ultimo_dia = calendar.monthrange(ano, mes)
    data_inicio = date(ano, mes, 1).isoformat()
    data_fim = date(ano, mes, ultimo_dia).isoformat()

    registros = conn.execute(
        """SELECT registros.*, funcionarios.nome AS funcionario_nome
           FROM registros
           JOIN funcionarios ON funcionarios.id = registros.funcionario_id
           WHERE registros.funcionario_id = ? AND registros.timestamp >= date(?) AND registros.timestamp < date(?, '+1 day')
           ORDER BY registros.timestamp""",
        (funcionario_id, data_inicio, data_fim),
    ).fetchall()
    dias_por_data = {dia["data"]: dia for dia in agrupar_registros_por_dia(registros)}

    ausencias = conn.execute(
        "SELECT * FROM ausencias WHERE funcionario_id = ? AND data BETWEEN ? AND ? ORDER BY data",
        (funcionario_id, data_inicio, data_fim),
    ).fetchall()
    ausencias_por_data = {datetime.strptime(a["data"], "%Y-%m-%d").date(): a for a in ausencias}
    feriados = obter_feriados_no_periodo(conn, data_inicio, data_fim)

    linhas = []
    total_segundos_mes = 0
    total_extras_segundos_mes = 0

    for dia_num in range(1, ultimo_dia + 1):
        data_dia = date(ano, mes, dia_num)
        dia = dias_por_data.get(data_dia)
        ausencia = ausencias_por_data.get(data_dia)

        pares = dia["pares"] if dia else []
        total_segundos = dia["total_segundos"] if dia else 0
        total_horas = dia["total_horas"] if dia else "00:00"

        entrada, saida_almoco, retorno, saida = preencher_colunas_historico(pares)

        if ausencia is not None or jornada_na_data(funcionario, data_dia)["horario_entrada"] is None:
            horas_extras_segundos = 0
            horas_extras_texto = "-"
        else:
            horas_extras_segundos = max(
                0, total_segundos - horas_esperadas_no_dia_segundos(funcionario, data_dia, feriados)
            )
            horas_extras_texto = formatar_horas(horas_extras_segundos)

        total_segundos_mes += total_segundos
        total_extras_segundos_mes += horas_extras_segundos

        linhas.append(
            {
                "data": data_dia,
                "entrada": entrada,
                "saida_almoco": saida_almoco,
                "retorno": retorno,
                "saida": saida,
                "total_horas": total_horas,
                "horas_extras": horas_extras_texto,
                "ausencia": ausencia,
                "total_segundos": total_segundos,
                "horas_extras_segundos": horas_extras_segundos,
            }
        )

    totais = {
        "total_segundos": total_segundos_mes,
        "total_horas": formatar_horas(total_segundos_mes),
        "total_extras_segundos": total_extras_segundos_mes,
        "total_extras": formatar_horas(total_extras_segundos_mes),
    }

    return funcionario, linhas, totais


def calcular_stats_funcionarios_periodo(conn, data_inicio, data_fim, funcionario_id=None):
    """Estatísticas brutas por funcionário ativo no período: dias com ponto, faltas, atrasos,
    atraso_acumulado_seg (bruto, em segundos — não formatado), saídas antecipadas, horas
    trabalhadas/esperadas/extras em segundos, contagem de ausências por tipo. Não agrupa nem
    formata nada — quem chama decide isso (ver montar_linhas_central e montar_ranking_atrasos).
    Retorna {} se não houver funcionário correspondente."""
    query = f"""SELECT funcionarios.*, setores.nome AS setor_nome,
                      {SQL_COLUNAS_TURNO_EFETIVO}
               FROM funcionarios
               LEFT JOIN setores ON setores.id = funcionarios.setor_id
               {SQL_JOIN_TURNO_EFETIVO}
               WHERE {SQL_FUNCIONARIO_NO_PERIODO}"""
    params = [data_inicio, data_fim, data_inicio, data_fim]
    if funcionario_id:
        query += " AND funcionarios.id = ?"
        params.append(funcionario_id)
    funcionarios = anexar_historico_jornada(conn, conn.execute(query, params).fetchall())

    if not funcionarios:
        return {}

    feriados = obter_feriados_no_periodo(conn, data_inicio, data_fim)

    ids_funcionarios = [f["id"] for f in funcionarios]
    placeholders = ",".join("?" * len(ids_funcionarios))

    registros = conn.execute(
        f"""SELECT registros.*, funcionarios.nome AS funcionario_nome
            FROM registros
            JOIN funcionarios ON funcionarios.id = registros.funcionario_id
            WHERE registros.funcionario_id IN ({placeholders})
              AND registros.timestamp >= date(?) AND registros.timestamp < date(?, '+1 day')
            ORDER BY funcionarios.nome, registros.timestamp""",
        [*ids_funcionarios, data_inicio, data_fim],
    ).fetchall()

    dias_por_funcionario = {}
    for dia in agrupar_registros_por_dia(registros):
        dias_por_funcionario.setdefault(dia["funcionario_id"], []).append(dia)

    ausencias = conn.execute(
        f"""SELECT * FROM ausencias WHERE funcionario_id IN ({placeholders})
            AND data BETWEEN ? AND ?""",
        [*ids_funcionarios, data_inicio, data_fim],
    ).fetchall()
    ausencias_por_funcionario = {}
    tipos_ausencia_por_funcionario = {}
    for a in ausencias:
        ausencias_por_funcionario.setdefault(a["funcionario_id"], set()).add(a["data"])
        tipos_ausencia_por_funcionario.setdefault(a["funcionario_id"], []).append(a["tipo"])

    data_ini_obj = datetime.strptime(data_inicio, "%Y-%m-%d").date()
    data_fim_obj = datetime.strptime(data_fim, "%Y-%m-%d").date()
    todas_as_datas_periodo = []
    cursor_data = data_ini_obj
    while cursor_data <= data_fim_obj:
        todas_as_datas_periodo.append(cursor_data.isoformat())
        cursor_data += timedelta(days=1)

    hoje_iso = date.today().isoformat()
    stats_por_funcionario = {}
    for f in funcionarios:
        dias = dias_por_funcionario.get(f["id"], [])
        datas_com_registro = {d["data"].isoformat() for d in dias}
        datas_ausencia = ausencias_por_funcionario.get(f["id"], set())

        total_trabalhado_seg = sum(d["total_segundos"] for d in dias)
        atrasos = 0
        atraso_acumulado_seg = 0
        saidas_antecipadas = 0
        horas_extras_seg = 0
        horas_esperadas_seg = 0

        for d in dias:
            fd = jornada_na_data(f, d["data"])  # jornada que valia nesse dia
            if fd["horario_entrada"] is not None:
                dia_util = dia_e_util_para_funcionario(fd, d["data"], feriados)
                horas_esperadas_dia = horas_esperadas_no_dia_segundos(fd, d["data"], feriados)
                if dia_util:
                    entrada_dia, _saida_almoco_dia, _retorno_dia, saida_dia = preencher_colunas_historico(d["pares"])
                    limite_entrada = (
                        datetime.strptime(fd["horario_entrada"], "%H:%M") + timedelta(minutes=fd["tolerancia_minutos"])
                    ).time()
                    if entrada_dia:
                        chegada = datetime.strptime(entrada_dia["timestamp"], "%Y-%m-%d %H:%M:%S").time()
                        if chegada > limite_entrada:
                            atrasos += 1
                            atraso_acumulado_seg += (
                                datetime.combine(d["data"], chegada) - datetime.combine(d["data"], limite_entrada)
                            ).total_seconds()
                    if saida_dia and fd["tolerancia_saida_minutos"] is not None:
                        limite_saida = (
                            datetime.strptime(fd["horario_saida"], "%H:%M")
                            - timedelta(minutes=fd["tolerancia_saida_minutos"])
                        ).time()
                        saida_hora = datetime.strptime(saida_dia["timestamp"], "%Y-%m-%d %H:%M:%S").time()
                        if saida_hora < limite_saida:
                            saidas_antecipadas += 1
                horas_extras_seg += max(0, d["total_segundos"] - horas_esperadas_dia)

        # Hoje e dias futuros só entram se já houver ponto: ainda não dá pra chamar de falta.
        dias_uteis_considerados = [
            d for d in todas_as_datas_periodo
            if (not f["data_admissao"] or d >= f["data_admissao"])
            and (d < hoje_iso or d in datas_com_registro)
            and dia_e_util_para_funcionario(f, datetime.strptime(d, "%Y-%m-%d").date(), feriados)
        ]
        dias_uteis_considerados = limitar_dias_de_quem_saiu(
            f, dias_uteis_considerados, datas_com_registro | datas_ausencia
        )

        # Esperado conta todo dia útil (inclusive as faltas), menos os justificados — igual ao
        # banco de horas. Hoje só "espera" o que já foi trabalhado (dia ainda em andamento).
        if f["horario_entrada"] is not None:
            trabalhado_por_data = {d["data"].isoformat(): d["total_segundos"] for d in dias}
            for d_iso in dias_uteis_considerados:
                if d_iso in datas_ausencia:
                    continue
                esperado = horas_esperadas_no_dia_segundos(f, datetime.strptime(d_iso, "%Y-%m-%d").date(), feriados)
                if d_iso >= hoje_iso:
                    esperado = min(esperado, trabalhado_por_data.get(d_iso, 0))
                horas_esperadas_seg += esperado

        faltas = sum(
            1
            for dia_util in dias_uteis_considerados
            if dia_util not in datas_com_registro and dia_util not in datas_ausencia
        )

        contagem_tipos_ausencia = {}
        for tipo in tipos_ausencia_por_funcionario.get(f["id"], []):
            contagem_tipos_ausencia[tipo] = contagem_tipos_ausencia.get(tipo, 0) + 1

        stats_por_funcionario[f["id"]] = {
            "funcionario": f,
            "dias_com_ponto": len(dias),
            "total_trabalhado_seg": total_trabalhado_seg,
            "faltas": faltas,
            "dias_uteis": len(dias_uteis_considerados),
            "atrasos": atrasos,
            "atraso_acumulado_seg": atraso_acumulado_seg,
            "saidas_antecipadas": saidas_antecipadas,
            "horas_extras_seg": horas_extras_seg,
            "horas_esperadas_seg": horas_esperadas_seg,
            "tem_turno": f["horario_entrada"] is not None,
            "ausencias_tipos": contagem_tipos_ausencia,
        }

    return stats_por_funcionario


def montar_linhas_central(conn, data_inicio, data_fim, agrupar_por, visao, funcionario_id=None):
    stats_por_funcionario = calcular_stats_funcionarios_periodo(conn, data_inicio, data_fim, funcionario_id)
    if not stats_por_funcionario:
        return []
    data_fim_obj = datetime.strptime(data_fim, "%Y-%m-%d").date()

    grupos = {}
    for stats in stats_por_funcionario.values():
        f = stats["funcionario"]
        if agrupar_por == "setor":
            chave = f["setor_nome"] or "Sem setor"
        elif agrupar_por == "cargo":
            chave = f["cargo"] or "Sem cargo"
        elif agrupar_por == "turno":
            chave = f["turno_ou_escala_nome"] or "Sem turno"
        else:
            chave = f["nome"]
        grupos.setdefault(chave, []).append(stats)

    linhas = []
    for chave in sorted(grupos.keys()):
        membros = grupos[chave]
        linha = {"dimensao": chave}

        if visao == "faltas":
            total_dias_uteis = sum(m["dias_uteis"] for m in membros)
            total_faltas = sum(m["faltas"] for m in membros)
            linha["dias_uteis"] = total_dias_uteis
            linha["faltas"] = total_faltas
            linha["percentual_faltas"] = round(total_faltas / total_dias_uteis * 100, 1) if total_dias_uteis else 0
        elif visao == "atrasos":
            linha["dias_com_ponto"] = sum(m["dias_com_ponto"] for m in membros)
            linha["atrasos"] = sum(m["atrasos"] for m in membros)
            linha["atraso_acumulado"] = formatar_horas(sum(m["atraso_acumulado_seg"] for m in membros))
            linha["saidas_antecipadas"] = sum(m["saidas_antecipadas"] for m in membros)
        elif visao in ("horas_extras", "banco_horas"):
            com_turno = [m for m in membros if m["tem_turno"]]
            if com_turno:
                trabalhadas = sum(m["total_trabalhado_seg"] for m in com_turno)
                esperadas = sum(m["horas_esperadas_seg"] for m in com_turno)
                linha["horas_trabalhadas"] = formatar_horas(trabalhadas)
                linha["horas_esperadas"] = formatar_horas(esperadas)
                if visao == "horas_extras":
                    linha["horas_extras"] = formatar_horas(sum(m["horas_extras_seg"] for m in com_turno))
                else:
                    linha["saldo"] = formatar_horas(trabalhadas - esperadas)
                    com_banco_ativo = [m for m in com_turno if m["funcionario"]["banco_horas_inicio"]]
                    if com_banco_ativo:
                        soma_saldo_acumulado = sum(
                            calcular_saldo_banco_horas_ate(conn, m["funcionario"], data_fim_obj)
                            for m in com_banco_ativo
                        )
                        linha["saldo_acumulado"] = formatar_horas(soma_saldo_acumulado)
                    else:
                        linha["saldo_acumulado"] = "-"
            else:
                linha["horas_trabalhadas"] = linha["horas_esperadas"] = "-"
                linha["horas_extras" if visao == "horas_extras" else "saldo"] = "-"
                if visao == "banco_horas":
                    linha["saldo_acumulado"] = "-"
        elif visao == "ausencias":
            linha["atestados"] = sum(m["ausencias_tipos"].get("atestado", 0) for m in membros)
            linha["licencas"] = sum(m["ausencias_tipos"].get("licenca", 0) for m in membros)
            linha["ferias"] = sum(m["ausencias_tipos"].get("ferias", 0) for m in membros)
            linha["folgas"] = sum(m["ausencias_tipos"].get("folga", 0) for m in membros)
            linha["licencas_maternidade_paternidade"] = sum(
                m["ausencias_tipos"].get("licenca_maternidade_paternidade", 0) for m in membros
            )
            linha["falecimentos"] = sum(m["ausencias_tipos"].get("falecimento", 0) for m in membros)
            linha["abonos_legais"] = sum(m["ausencias_tipos"].get("abono_legal", 0) for m in membros)
        else:  # frequencia (padrão)
            linha["dias_com_ponto"] = sum(m["dias_com_ponto"] for m in membros)
            linha["faltas"] = sum(m["faltas"] for m in membros)
            linha["atrasos"] = sum(m["atrasos"] for m in membros)
            linha["horas_trabalhadas"] = formatar_horas(sum(m["total_trabalhado_seg"] for m in membros))

        linhas.append(linha)

    return linhas


def montar_ranking_atrasos(conn, data_inicio, data_fim, top_n=5):
    """Top-N funcionários com mais atraso acumulado no período (só quem teve algum atraso)."""
    stats_por_funcionario = calcular_stats_funcionarios_periodo(conn, data_inicio, data_fim)
    candidatos = [s for s in stats_por_funcionario.values() if s["atrasos"] > 0]
    candidatos.sort(key=lambda s: (-s["atraso_acumulado_seg"], -s["atrasos"], s["funcionario"]["nome"]))

    ranking = []
    for s in candidatos[:top_n]:
        ranking.append(
            {
                "funcionario_id": s["funcionario"]["id"],
                "nome": s["funcionario"]["nome"],
                "atrasos": s["atrasos"],
                "atraso_acumulado_minutos": round(s["atraso_acumulado_seg"] / 60),
                "atraso_acumulado_formatado": formatar_horas(s["atraso_acumulado_seg"]),
            }
        )
    return ranking


def calcular_meses_fechados(quantidade=6, hoje=None):
    """Últimos `quantidade` meses fechados (não inclui o mês corrente), do mais antigo pro mais
    recente. Ex.: hoje=2026-08-03 -> [(2026,2), (2026,3), (2026,4), (2026,5), (2026,6), (2026,7)]."""
    hoje = hoje or date.today()
    meses = []
    ano, mes = hoje.year, hoje.month
    for _ in range(quantidade):
        mes -= 1
        if mes == 0:
            mes, ano = 12, ano - 1
        meses.append((ano, mes))
    meses.reverse()
    return meses


def montar_comparativo_mensal(conn, quantidade_meses=6, hoje=None):
    """Totais da escola inteira, mês a mês, pros últimos `quantidade_meses` meses fechados.
    Reaproveita calcular_stats_funcionarios_periodo (mesma lógica usada pela Central de
    Relatórios e pelo ranking de atrasos) — nada de cálculo de atraso/falta duplicado."""
    resultado = []
    for ano, mes in calcular_meses_fechados(quantidade_meses, hoje):
        _, ultimo_dia = calendar.monthrange(ano, mes)
        data_inicio = date(ano, mes, 1).isoformat()
        data_fim = date(ano, mes, ultimo_dia).isoformat()
        stats = calcular_stats_funcionarios_periodo(conn, data_inicio, data_fim).values()
        total_trabalhado_seg = sum(s["total_trabalhado_seg"] for s in stats)
        resultado.append(
            {
                "ano": ano,
                "mes": mes,
                "total_trabalhado_horas": round(total_trabalhado_seg / 3600, 1),
                "total_faltas": sum(s["faltas"] for s in stats),
                "total_atrasos": sum(s["atrasos"] for s in stats),
            }
        )
    return resultado


CODIGO_AUSENCIA = {
    "atestado": "AT",
    "ferias": "FE",
    "licenca": "LC",
    "folga": "FG",
    "licenca_maternidade_paternidade": "LM",
    "falecimento": "FL",
    "abono_legal": "AB",
    "outro": "OU",
}

NOMES_DIAS_SEMANA_ABREV = ["Sg", "Te", "Qa", "Qi", "Sx", "Sb", "Do"]

ORDEM_CATEGORIA = {"gestao": 0, "apoio_pedagogico": 1, "professor": 2}
NOMES_CATEGORIA = {
    "gestao": "Gestão",
    "apoio_pedagogico": "Apoio Pedagógico",
    "professor": "Professores",
    "outro": "Outros funcionários",
}


def _chave_ordenacao_folha_ponto(f):
    ordem_categoria = ORDEM_CATEGORIA.get(f["categoria"], 3)
    ordem_vinculo = 0
    if f["categoria"] == "professor":
        ordem_vinculo = {"efetivo": 0, "contratado": 1}.get(f["vinculo"], 2)
    return (ordem_categoria, ordem_vinculo, f["nome"])


def montar_folha_ponto_mensal(conn, ano, mes, funcionario_id=None):
    """Grade mensal: uma linha por funcionário ativo, um código curto por dia do mês
    (P=presença, F=falta, FD=feriado, ou o código de CODIGO_AUSENCIA). Célula em branco quando
    o dia não é de trabalho pra pessoa (turno/escala), é anterior à admissão, ou ainda não
    chegou. Se houver ponto batido E ausência no mesmo dia, a ausência prevalece no código;
    feriado só aparece como FD se não tiver ausência nem ponto batido nesse dia.
    Linhas saem ordenadas por categoria (Gestão, Apoio Pedagógico, Professores — Efetivo
    antes de Contratado —, Outros/não informado) e depois por nome."""
    _, ultimo_dia = calendar.monthrange(ano, mes)
    data_inicio = date(ano, mes, 1)
    data_fim = date(ano, mes, ultimo_dia)
    limite = min(data_fim, date.today())
    dias_semana_abrev = [
        NOMES_DIAS_SEMANA_ABREV[date(ano, mes, dia_num).weekday()] for dia_num in range(1, ultimo_dia + 1)
    ]

    query = f"""SELECT funcionarios.*, {SQL_COLUNAS_TURNO_EFETIVO}
                FROM funcionarios
                {SQL_JOIN_TURNO_EFETIVO}
                WHERE {SQL_FUNCIONARIO_NO_PERIODO}"""
    params = [data_inicio.isoformat(), data_fim.isoformat()] * 2
    if funcionario_id:
        query += " AND funcionarios.id = ?"
        params.append(funcionario_id)
    funcionarios = sorted(
        anexar_historico_jornada(conn, conn.execute(query, params).fetchall()), key=_chave_ordenacao_folha_ponto
    )
    if not funcionarios:
        return {"ultimo_dia": ultimo_dia, "dias_semana_abrev": dias_semana_abrev, "linhas": []}

    ids_funcionarios = [f["id"] for f in funcionarios]
    placeholders = ",".join("?" * len(ids_funcionarios))

    registros = conn.execute(
        f"""SELECT DISTINCT funcionario_id, date(timestamp) AS dia FROM registros
            WHERE funcionario_id IN ({placeholders})
              AND timestamp >= date(?) AND timestamp < date(?, '+1 day')""",
        [*ids_funcionarios, data_inicio.isoformat(), data_fim.isoformat()],
    ).fetchall()
    dias_com_ponto = {}
    for r in registros:
        dias_com_ponto.setdefault(r["funcionario_id"], set()).add(r["dia"])

    ausencias = conn.execute(
        f"""SELECT funcionario_id, data, tipo FROM ausencias
            WHERE funcionario_id IN ({placeholders}) AND data BETWEEN ? AND ?""",
        [*ids_funcionarios, data_inicio.isoformat(), data_fim.isoformat()],
    ).fetchall()
    tipo_ausencia_por_dia = {(a["funcionario_id"], a["data"]): a["tipo"] for a in ausencias}
    feriados = obter_feriados_no_periodo(conn, data_inicio.isoformat(), data_fim.isoformat())

    linhas = []
    for f in funcionarios:
        datas_atividade = dias_com_ponto.get(f["id"], set()) | {
            data for (fid, data) in tipo_ausencia_por_dia if fid == f["id"]
        }
        limite_f = limite
        if f["situacao"] != "ativo" and datas_atividade:
            limite_f = min(limite, date.fromisoformat(max(datas_atividade)))
        codigos = []
        for dia_num in range(1, ultimo_dia + 1):
            data_dia = date(ano, mes, dia_num)
            iso = data_dia.isoformat()

            if data_dia > limite_f or (f["data_admissao"] and iso < f["data_admissao"]):
                codigos.append("")
                continue

            tipo_ausencia = tipo_ausencia_por_dia.get((f["id"], iso))
            if tipo_ausencia:
                codigos.append(CODIGO_AUSENCIA.get(tipo_ausencia, "OU"))
            elif iso in dias_com_ponto.get(f["id"], ()):
                codigos.append("P")
            elif iso in feriados:
                codigos.append("FD")
            elif data_dia < date.today() and dia_e_util_para_funcionario(f, data_dia, feriados):
                codigos.append("F")  # hoje ainda não é falta: a pessoa pode chegar mais tarde
            else:
                codigos.append("")
        linhas.append({"funcionario": f, "codigos": codigos, "categoria": f["categoria"] or "outro"})

    return {"ultimo_dia": ultimo_dia, "dias_semana_abrev": dias_semana_abrev, "linhas": linhas}
