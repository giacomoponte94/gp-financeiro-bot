"""
Interpretador local de lançamentos financeiros — sem API externa.
Substitui a chamada ao Claude API no bot (bloqueada pela rede do Railway).

Uso: from interpretador_local import interpretar
     resultado = interpretar("gastei 50 de gasolina ontem no débito")
     # resultado é uma lista de dicts, um por lançamento detectado
"""

import re
from datetime import date, timedelta

# ---------- CATEGORIAS ----------
CATEGORIAS = {
    "Alimentação": ["almoço", "jantar", "lanche", "café", "pizza", "sorvete",
                    "chocolate", "amendoim", "restaurante", "ifood", "mercado",
                    "padaria", "monster", "água", "bebida"],
    "Gasolina": ["gasolina", "combustível", "posto", "álcool", "etanol"],
    "Transporte": ["uber", "99", "estacionamento", "pedágio", "ônibus"],
    "Consórcio": ["consórcio"],
    "Santander": ["santander"],
    "Moradia": ["internet", "aluguel", "condomínio", "luz", "água conta", "energia"],
    "Comunicação": ["vivo", "celular", "telefone", "claro", "tim"],
    "Seguro": ["seguro", "prudential"],
    "Negócio": ["mei", "das", "ironberg", "greenlife", "academia"],
    "Aluno": ["mensalidade", "aluno", "personal", "treino pago"],
}

# ---------- FORMA DE PAGAMENTO ----------
FORMAS_PAGAMENTO = {
    "pix": ["pix"],
    "crédito": ["crédito", "credito", "cartão de crédito", "cartao"],
    "débito": ["débito", "debito"],
    "dinheiro": ["dinheiro", "espécie", "cash"],
}

# Bancos/instituições — vira o campo "local"
BANCOS = ["inter", "c6", "santander", "nubank", "itaú", "itau", "bradesco",
          "caixa", "banco do brasil", "bb", "next", "picpay"]

DIAS_SEMANA = {
    "domingo": 6, "segunda": 0, "terça": 1, "terca": 1, "quarta": 2,
    "quinta": 3, "sexta": 4, "sábado": 5, "sabado": 5,
}


def _parse_valor(texto):
    """Extrai valor numérico: '50', '50,90', '1k', 'cinquenta reais'."""
    texto = texto.lower()

    m = re.search(r'(\d+)\s*k\b', texto)
    if m:
        return float(m.group(1)) * 1000

    m = re.search(r'r?\$?\s*(\d+(?:[.,]\d{1,2})?)', texto)
    if m:
        val = m.group(1).replace(".", "").replace(",", ".") if "," in m.group(1) else m.group(1)
        try:
            return float(val)
        except ValueError:
            pass

    numeros_extenso = {
        "um": 1, "dois": 2, "três": 3, "tres": 3, "quatro": 4, "cinco": 5,
        "seis": 6, "sete": 7, "oito": 8, "nove": 9, "dez": 10,
        "vinte": 20, "trinta": 30, "quarenta": 40, "cinquenta": 50,
        "sessenta": 60, "setenta": 70, "oitenta": 80, "noventa": 90,
        "cem": 100, "cento": 100,
    }
    for palavra, valor in numeros_extenso.items():
        if palavra in texto:
            return float(valor)

    return None


def _parse_data(texto):
    """Extrai a data: 'hoje', 'ontem', 'anteontem', 'dia 10', 'sexta'."""
    texto = texto.lower()
    hoje = date.today()

    if "anteontem" in texto:
        return hoje - timedelta(days=2)
    if "ontem" in texto:
        return hoje - timedelta(days=1)
    if "hoje" in texto:
        return hoje

    m = re.search(r'dia\s+(\d{1,2})', texto)
    if m:
        dia = int(m.group(1))
        try:
            return hoje.replace(day=dia)
        except ValueError:
            return hoje

    for nome, idx_semana in DIAS_SEMANA.items():
        if nome in texto:
            delta = (hoje.weekday() - idx_semana) % 7
            delta = delta if delta != 0 else 7  # assume última ocorrência, não hoje
            return hoje - timedelta(days=delta)

    return hoje


def _parse_categoria(texto, tipo=None):
    texto = texto.lower()
    for categoria, palavras in CATEGORIAS.items():
        for palavra in palavras:
            if palavra in texto:
                return categoria
    if tipo == "receita":
        return "Aluno"  # padrão: toda receita não identificada é mensalidade
    return "Outros"


def _parse_forma_pagamento(texto):
    texto = texto.lower()
    for forma, palavras in FORMAS_PAGAMENTO.items():
        for palavra in palavras:
            if palavra in texto:
                return forma
    return None


def _parse_local(texto):
    texto = texto.lower()
    for banco in BANCOS:
        if banco in texto:
            return banco.title()
    return None


def _parse_tipo(texto):
    texto = texto.lower()
    if any(p in texto for p in ["recebi", "entrou", "pagamento de", "ganhei", "receita"]):
        return "receita"
    return "gasto"


def _dividir_lancamentos(texto):
    """Divide frases como 'gastei 50 de almoço e 100 de gasolina' em partes."""
    partes = re.split(r'\s+e\s+(?=gastei|recebi|paguei|comprei|\d)', texto, flags=re.IGNORECASE)
    return [p.strip() for p in partes if p.strip()]


def _parse_parcelas(texto):
    """Detecta parcelamento: 'em 3x', '3 vezes', 'parcelado em 5x'."""
    texto = texto.lower()

    m = re.search(r'(\d{1,2})\s*x\b', texto)
    if m:
        n = int(m.group(1))
        if 2 <= n <= 48:
            return n

    m = re.search(r'(\d{1,2})\s*vezes', texto)
    if m:
        n = int(m.group(1))
        if 2 <= n <= 48:
            return n

    return None


def interpretar(mensagem):
    """
    Recebe a mensagem crua do usuário e devolve uma lista de lançamentos:
    [{"tipo": ..., "valor": ..., "categoria": ..., "data": ..., "forma_pagamento": ..., "local": ..., "descricao": ...}]
    """
    partes = _dividir_lancamentos(mensagem)
    resultados = []

    for parte in partes:
        valor = _parse_valor(parte)
        if valor is None:
            continue  # sem valor identificável, não é um lançamento válido

        tipo = _parse_tipo(parte)
        parcelas = _parse_parcelas(parte)

        valor_total = valor
        valor_parcela = None
        if parcelas:
            # "3x de 100" -> 100 é o valor da parcela, total = 100*3
            m = re.search(r'\d{1,2}\s*x\s*de\s*r?\$?\s*(\d+(?:[.,]\d{1,2})?)', parte.lower())
            if m:
                val = m.group(1).replace(".", "").replace(",", ".") if "," in m.group(1) else m.group(1)
                valor_parcela = float(val)
                valor_total = round(valor_parcela * parcelas, 2)
            else:
                # valor encontrado é o total; parcela = total / N
                valor_total = valor
                valor_parcela = round(valor / parcelas, 2)

        resultados.append({
            "tipo": tipo,
            "valor": valor_total,
            "parcelas": parcelas,
            "valor_parcela": valor_parcela,
            "categoria": _parse_categoria(parte, tipo=tipo),
            "data": _parse_data(parte).isoformat(),
            "forma_pagamento": _parse_forma_pagamento(parte),
            "local": _parse_local(parte),
            "descricao": parte.strip().capitalize(),
        })

    return resultados


if __name__ == "__main__":
    testes = [
        "gastei 50 de gasolina ontem no débito",
        "paguei 26,46 de almoço no crédito inter",
        "recebi 850 da Morgana hoje via pix",
        "gastei 50 de almoço e 100 de gasolina",
        "gastei 30 no consórcio dia 5 c6",
        "comprei em 3x de 100 um tênis",
        "comprei um celular de 1200 em 4x",
    ]
    for t in testes:
        print(t, "->", interpretar(t))
