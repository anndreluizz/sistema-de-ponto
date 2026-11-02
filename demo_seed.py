"""Cria um banco de DEMONSTRAÇÃO com dados fictícios (escola e funcionários inventados).

Uso, numa pasta separada da instalação real:
    python demo_seed.py
    python app.py            -> http://127.0.0.1:5000  (login: admin / demo1234)

Recusa rodar se já existir um ponto.db, para nunca misturar com dados reais.
"""

import random
from datetime import date, datetime, timedelta

from werkzeug.security import generate_password_hash

import db

SENHA_DEMO = "demo1234"
FUNCIONARIOS = [
    # nome, cargo, setor, categoria, vinculo
    ("Ana Beatriz Souza", "Diretora", "Direção", "gestao", "efetivo"),
    ("Carlos Henrique Lima", "Coordenador Pedagógico", "Coordenação", "apoio_pedagogico", "efetivo"),
    ("Fernanda Oliveira", "Professora de Matemática", "Ensino Fundamental", "professor", "efetivo"),
    ("João Pedro Santos", "Professor de Português", "Ensino Fundamental", "professor", "contratado"),
    ("Mariana Costa", "Professora de Ciências", "Ensino Fundamental", "professor", "efetivo"),
    ("Rafael Almeida", "Auxiliar Administrativo", "Secretaria", "outro", "contratado"),
    ("Juliana Ferreira", "Secretária Escolar", "Secretaria", "outro", "efetivo"),
    ("Paulo Ricardo Gomes", "Merendeiro", "Apoio", "outro", "contratado"),
    ("Luciana Martins", "Auxiliar de Serviços Gerais", "Apoio", "outro", "efetivo"),
    ("Thiago Barbosa", "Vigia", "Apoio", "outro", "contratado"),
]
FERIADOS = {date(2026, 10, 12): "Nossa Senhora Aparecida", date(2026, 11, 15): "Proclamação da República"}


def main():
    if db.DB_PATH.exists():
        raise SystemExit(f"Já existe {db.DB_PATH}. Rode a demonstração numa pasta separada.")
    db.init_db()
    conn = db.get_db()
    random.seed(42)
    agora = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    conn.execute("UPDATE configuracoes SET nome_instituicao = 'Escola Municipal Modelo (demonstração)' WHERE id = 1")
    conn.execute("UPDATE usuarios SET senha_hash = ? WHERE usuario = 'admin'", (generate_password_hash(SENHA_DEMO),))
    conn.execute("UPDATE admin SET senha_hash = ?", (generate_password_hash(SENHA_DEMO),))

    turno_id = conn.execute(
        """INSERT INTO turnos (nome, horario_entrada, horario_saida, tolerancia_minutos, dias_semana,
                               horario_saida_almoco, horario_retorno_almoco, tolerancia_saida_minutos)
           VALUES ('Integral 07h-16h', '07:00', '16:00', 10, '0,1,2,3,4', '11:00', '12:00', 10)"""
    ).lastrowid

    setores = {}
    for _, _, setor, _, _ in FUNCIONARIOS:
        if setor not in setores:
            setores[setor] = conn.execute("INSERT INTO setores (nome) VALUES (?)", (setor,)).lastrowid

    for data_feriado, nome in FERIADOS.items():
        conn.execute(
            "INSERT INTO feriados (data, nome, criado_por, criado_em) VALUES (?, ?, 'admin', ?)",
            (data_feriado.isoformat(), nome, agora),
        )

    hoje = date.today()
    inicio = (hoje.replace(day=1) - timedelta(days=1)).replace(day=1)  # 1º dia do mês passado
    for indice, (nome, cargo, setor, categoria, vinculo) in enumerate(FUNCIONARIOS):
        funcionario_id = conn.execute(
            """INSERT INTO funcionarios (nome, cargo, codigo, situacao, setor_id, turno_id,
                                         data_admissao, categoria, vinculo)
               VALUES (?, ?, ?, 'ativo', ?, ?, '2025-02-03', ?, ?)""",
            (nome, cargo, db.gerar_codigo(), setores[setor], turno_id, categoria, vinculo),
        ).lastrowid
        dia = inicio
        while dia <= hoje:
            if dia.weekday() < 5 and dia not in FERIADOS:
                if dia != hoje and random.random() < 0.04:
                    if indice % 3 == 0:
                        conn.execute(
                            """INSERT INTO ausencias (funcionario_id, data, tipo, observacao, criado_por, criado_em)
                               VALUES (?, ?, 'atestado', 'Atestado médico', 'admin', ?)""",
                            (funcionario_id, dia.isoformat(), agora),
                        )
                else:
                    _registrar_dia(conn, funcionario_id, dia, apenas_entrada=(dia == hoje))
            dia += timedelta(days=1)

    conn.commit()
    conn.close()
    print(f"Banco de demonstração criado em {db.DB_PATH}. Login: admin / {SENHA_DEMO}")


def _registrar_dia(conn, funcionario_id, dia, apenas_entrada):
    atraso = random.choice([0] * 8 + [5, 15, 25])
    batidas = [("entrada", 7, -random.randint(0, 9) + atraso)]
    if not apenas_entrada:
        batidas += [("saida", 11, random.randint(0, 4)), ("entrada", 12, random.randint(-3, 3)),
                    ("saida", 16, random.randint(0, 12))]
    for tipo, hora, minutos in batidas:
        momento = datetime(dia.year, dia.month, dia.day, hora) + timedelta(minutes=minutos)
        conn.execute(
            "INSERT INTO registros (funcionario_id, tipo, timestamp) VALUES (?, ?, ?)",
            (funcionario_id, tipo, momento.strftime("%Y-%m-%d %H:%M:%S")),
        )


if __name__ == "__main__":
    main()
