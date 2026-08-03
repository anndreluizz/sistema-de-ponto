"""Geração de PDF e Excel para a Central de Relatórios e o Histórico por funcionário."""
from io import BytesIO
from pathlib import Path

import qrcode
from fpdf import FPDF
from openpyxl import Workbook
from openpyxl.drawing.image import Image as ExcelImage
from openpyxl.styles import Font, PatternFill
from openpyxl.utils import get_column_letter

BASE_DIR = Path(__file__).resolve().parent


def obter_dados_cabecalho(conn):
    config = conn.execute("SELECT * FROM configuracoes WHERE id = 1").fetchone()
    nome_instituicao = (config["nome_instituicao"] if config else "") or "Ponto Eletrônico"
    logo_abs_path = None
    if config and config["logo_path"]:
        candidato = BASE_DIR / "static" / config["logo_path"]
        if candidato.exists():
            logo_abs_path = candidato
    return {"nome_instituicao": nome_instituicao, "logo_abs_path": logo_abs_path}


class _PDFRelatorio(FPDF):
    def __init__(self, cabecalho, titulo, subtitulo):
        super().__init__(orientation="L")
        self._cabecalho = cabecalho
        self._titulo = titulo
        self._subtitulo = subtitulo
        self.set_margins(12, 12, 12)
        self.set_auto_page_break(auto=True, margin=15)

    def header(self):
        if self._cabecalho.get("logo_abs_path"):
            try:
                self.image(str(self._cabecalho["logo_abs_path"]), x=12, y=10, h=12)
                self.set_xy(28, 10)
            except Exception:
                self.set_xy(12, 10)
        else:
            self.set_xy(12, 10)
        self.set_font("Helvetica", "B", 13)
        self.cell(0, 6, self._cabecalho["nome_instituicao"], new_x="LMARGIN", new_y="NEXT")
        self.set_x(12)
        self.set_font("Helvetica", "B", 11)
        self.cell(0, 6, self._titulo, new_x="LMARGIN", new_y="NEXT")
        self.set_x(12)
        self.set_font("Helvetica", "", 9)
        self.set_text_color(100, 100, 100)
        self.cell(0, 5, self._subtitulo, new_x="LMARGIN", new_y="NEXT")
        self.set_text_color(0, 0, 0)
        self.ln(4)

    def footer(self):
        self.set_y(-12)
        self.set_font("Helvetica", "", 8)
        self.set_text_color(140, 140, 140)
        self.cell(0, 8, f"Página {self.page_no()}", align="C")


def gerar_pdf_tabela(cabecalho, titulo, subtitulo, colunas, linhas):
    pdf = _PDFRelatorio(cabecalho, titulo, subtitulo)
    pdf.add_page()
    pdf.set_font("Helvetica", "", 9)

    dados_tabela = [list(colunas)] + [[str(valor) for valor in linha] for linha in linhas]
    with pdf.table(text_align="LEFT") as table:
        for linha_dados in dados_tabela:
            row = table.row()
            for valor in linha_dados:
                row.cell(valor)

    return bytes(pdf.output())


def gerar_pdf_espelho(cabecalho, funcionario, mes_rotulo, colunas, linhas_tabela, totais, codigo_verificacao):
    subtitulo = f"{funcionario['nome']} - Matricula {funcionario['matricula'] or '-'} - {mes_rotulo}"
    pdf = _PDFRelatorio(cabecalho, "Espelho de Ponto", subtitulo)
    pdf.add_page()
    pdf.set_font("Helvetica", "", 8)

    dados_tabela = [list(colunas)] + [[str(valor) for valor in linha] for linha in linhas_tabela]
    with pdf.table(text_align="LEFT") as table:
        for linha_dados in dados_tabela:
            row = table.row()
            for valor in linha_dados:
                row.cell(valor)

    pdf.ln(3)
    pdf.set_font("Helvetica", "B", 10)
    pdf.cell(
        0, 6,
        f"Total de horas no mes: {totais['total_horas']}    Total de horas extras: {totais['total_extras']}",
        new_x="LMARGIN", new_y="NEXT",
    )

    pdf.ln(12)
    y_assinatura = pdf.get_y()
    largura_pagina = pdf.w - pdf.l_margin - pdf.r_margin
    largura_coluna = largura_pagina / 2 - 10

    pdf.set_font("Helvetica", "", 9)
    pdf.set_xy(pdf.l_margin, y_assinatura)
    pdf.cell(largura_coluna, 6, "_" * 40, new_x="LMARGIN", new_y="NEXT")
    pdf.set_x(pdf.l_margin)
    pdf.cell(largura_coluna, 6, "Assinatura do funcionario")

    x_col2 = pdf.l_margin + largura_coluna + 20
    pdf.set_xy(x_col2, y_assinatura)
    pdf.cell(largura_coluna, 6, "_" * 40, new_x="LMARGIN", new_y="NEXT")
    pdf.set_xy(x_col2, y_assinatura + 6)
    pdf.cell(largura_coluna, 6, "Assinatura do gestor/RH")

    y_qr = y_assinatura + 22
    texto_qr = f"ESPELHO|{funcionario['id']}|{mes_rotulo}|{codigo_verificacao}"
    imagem_qr = qrcode.make(texto_qr)
    buffer_qr = BytesIO()
    imagem_qr.save(buffer_qr, format="PNG")
    buffer_qr.seek(0)
    pdf.image(buffer_qr, x=pdf.l_margin, y=y_qr, w=22)

    pdf.set_xy(pdf.l_margin + 26, y_qr + 2)
    pdf.set_font("Helvetica", "B", 9)
    pdf.cell(0, 5, f"Codigo de verificacao: {codigo_verificacao}", new_x="LMARGIN", new_y="NEXT")
    pdf.set_x(pdf.l_margin + 26)
    pdf.set_font("Helvetica", "", 7)
    pdf.set_text_color(120, 120, 120)
    pdf.multi_cell(
        80, 4,
        "Gerado automaticamente pelo sistema. Se os dados do mes mudarem apos a emissao, o codigo muda.",
    )
    pdf.set_text_color(0, 0, 0)

    return bytes(pdf.output())


def gerar_excel_tabela(cabecalho, titulo, subtitulo, colunas, linhas):
    wb = Workbook()
    ws = wb.active
    ws.title = "Relatorio"

    linha_atual = 1
    if cabecalho.get("logo_abs_path"):
        try:
            imagem = ExcelImage(str(cabecalho["logo_abs_path"]))
            imagem.height = 50
            imagem.width = 50
            ws.add_image(imagem, "A1")
            linha_atual = 4
        except Exception:
            pass

    ws.cell(row=linha_atual, column=1, value=cabecalho["nome_instituicao"]).font = Font(bold=True, size=13)
    linha_atual += 1
    ws.cell(row=linha_atual, column=1, value=titulo).font = Font(bold=True, size=11)
    linha_atual += 1
    ws.cell(row=linha_atual, column=1, value=subtitulo).font = Font(italic=True, size=9, color="666666")
    linha_atual += 2

    for indice, coluna in enumerate(colunas, start=1):
        celula = ws.cell(row=linha_atual, column=indice, value=coluna)
        celula.font = Font(bold=True, color="FFFFFF")
        celula.fill = PatternFill("solid", fgColor="1E40AF")
    linha_atual += 1

    for linha in linhas:
        for indice, valor in enumerate(linha, start=1):
            ws.cell(row=linha_atual, column=indice, value=valor)
        linha_atual += 1

    for indice in range(1, len(colunas) + 1):
        ws.column_dimensions[get_column_letter(indice)].width = 20

    saida = BytesIO()
    wb.save(saida)
    return saida.getvalue()
