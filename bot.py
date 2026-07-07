import os
import re
import json
import uuid
import calendar
import logging
import unicodedata
import httpx
from datetime import datetime, date, timedelta, time as dtime
from zoneinfo import ZoneInfo
from telegram import Update, ReplyKeyboardMarkup, KeyboardButton
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
from supabase import create_client, Client
from interpretador_local import interpretar as interpretar_local

# --- Config ---
BOT_TOKEN = os.environ.get("BOT_TOKEN")
SUPABASE_URL = os.environ.get("SUPABASE_URL", "https://ggzjrzbsjzhswzffkjfq.supabase.co")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")
ANTHROPIC_KEY = os.environ.get("ANTHROPIC_KEY")
ALLOWED_USER = os.environ.get("ALLOWED_USER_ID")

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

MESES_MAP = {
    "janeiro": 1, "fevereiro": 2, "março": 3, "marco": 3,
    "abril": 4, "maio": 5, "junho": 6, "julho": 7,
    "agosto": 8, "setembro": 9, "outubro": 10,
    "novembro": 11, "dezembro": 12
}


def _sem_acento(s: str) -> str:
    """Remove acentos e normaliza para comparação (março -> marco)."""
    return unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode("ascii")


# Mapa normalizado sem acento, para casar independente de acentuação
MESES_MAP_NORM = {_sem_acento(k): v for k, v in MESES_MAP.items()}


MESES_ABBR = ["Jan", "Fev", "Mar", "Abr", "Mai", "Jun",
              "Jul", "Ago", "Set", "Out", "Nov", "Dez"]


def add_meses(d: date, n: int) -> date:
    """Soma n meses a uma data, ajustando o dia ao último dia do mês quando necessário."""
    total = d.month - 1 + n
    ano = d.year + total // 12
    mes = total % 12 + 1
    dia = min(d.day, calendar.monthrange(ano, mes)[1])
    return date(ano, mes, dia)


def resolver_mes(param):
    """Converte comando_param (nome do mês, 'mês passado', etc.) em (mes, ano).

    Retorna (None, None) quando não há mês específico → usa mês atual.
    """
    if not param:
        return None, None
    hoje = date.today()
    p = _sem_acento(str(param).strip().lower())

    if p in ("mes passado", "mes anterior", "mes_passado", "passado",
             "anterior", "ultimo mes", "mes retrasado"):
        mes = hoje.month - 1 or 12
        ano = hoje.year if hoje.month > 1 else hoje.year - 1
        return mes, ano

    mes = MESES_MAP_NORM.get(p)
    if mes:
        # Se o mês pedido ainda não chegou neste ano, assume o ano anterior
        ano = hoje.year if mes <= hoje.month else hoje.year - 1
        return mes, ano

    return None, None


def extrair_param_mes(texto: str):
    """Procura menção a um mês (ou 'mês passado') em texto livre, para usar com resolver_mes."""
    t = _sem_acento(texto.lower())
    if re.search(r"mes\s+passado|mes\s+anterior|ultimo\s+mes|mes\s+retrasado", t):
        return "mes_passado"
    for nome in MESES_MAP.keys():
        if _sem_acento(nome) in t:
            return nome
    return None


def detectar_comando_texto(texto: str):
    """Detecta comandos de consulta em texto livre (resumo, relatório, últimos, etc.).

    Antes essa detecção vinha do campo `comando` retornado pela IA (Claude).
    Como a interpretação agora é local (interpretador_local.py, sem detecção de
    comando), essa função assume esse papel via regex antes de tentar
    interpretar a mensagem como lançamento.
    """
    t = _sem_acento(texto.lower().strip())

    if re.search(r"(resumo\s+(da\s+)?semana|essa\s+semana|semana\s+atual)", t):
        return "resumo_semana", None

    if re.search(r"por\s+categoria", t):
        return "resumo_categoria", None

    m = re.search(r"ultimos?\s+(\d+)", t)
    if m:
        return "ultimos", m.group(1)

    if re.search(r"relatorio", t):
        return "relatorio", extrair_param_mes(texto)

    if re.search(r"^(ver\s+)?limites$", t):
        return "ver_limites", None

    if re.search(r"(^resumo\b|quanto\s+gastei|^saldo$)", t):
        return "resumo_mes", extrair_param_mes(texto)

    return None, None

SYSTEM_PROMPT = """Você é um assistente financeiro pessoal do Giácomo Ponte, personal trainer em Fortaleza-CE.
Interprete mensagens sobre gastos e receitas e retorne JSON estruturado.

Data de hoje: {hoje}
Ontem: {ontem}
Anteontem: {anteontem}

=== CONTEXTO DO GIÁCOMO ===
- Personal trainer, trabalha em 3 academias (Ironberg e rede Greenlife)
- Alunos ativos: Antonio, Apollo, Camila Cirino, Camila Lopes, Cecília, Edilene, Juan, Maria Rejane, Morgana, Pablo, Patrícia, Paula, Renata
- Receitas quase sempre são pagamentos de alunos via pix ou dinheiro
- Dívidas ativas: XP (cartão irmão), Santander (cartão mãe), Nubank (negativado), financiamento carro, consórcio
- Bancos que usa: Inter, C6, Caixa, Bradesco, Santander, Mercado Pago, Nubank, Itaú, BB, Sicoob, BTG, XP

=== CATEGORIAS DE GASTO ===
- Alimentação: lanche, almoço, jantar, café, restaurante, ifood, uber eats, mercado, supermercado, padaria, açaí
- Gasolina: combustível, posto, abastecimento, etanol
- Financiamento: parcela do carro, financiamento
- Consórcio: parcela consórcio
- XP: fatura xp, cartão xp, parcela xp
- Santander: fatura santander, cartão mãe, parcela santander
- Nubank: fatura nubank, cartão nubank
- Saúde: médico, psicólogo, psicóloga, farmácia, remédio, medicamento, clomid, exame, consulta, dentista
- Lazer: ingresso, show, cinema, bar, balada, passeio, viagem, hotel, jogo
- Compras: roupa, sapato, eletrônico, americanas, shopee, amazon, magazine, presente
- Negócio: taxa academia, ironberg, greenlife, material de trabalho, equipamento
- Seguro: seguro carro, seguro vida, prudential
- Internet: internet, wifi, net
- Vivo: celular vivo, plano vivo
- MEI: das, guia mei, imposto mei
- Transporte: uber, 99, táxi, ônibus, estacionamento, pedágio
- Devolução Mateus: qualquer pagamento referente à conta conjunta com Mateus (irmão) — pix direto, pagamento de conta de casa (energia/enel, água, internet) da conta conjunta, ou menção a "devolvi pro Mateus", "conta do Mateus", "conta compartilhada"
- Outros: qualquer coisa que não se encaixa acima

=== CATEGORIAS DE RECEITA ===
- Aluno: qualquer pagamento de aluno
- Outros: outras receitas

=== FORMAS DE PAGAMENTO ===
pix, crédito, débito, dinheiro, transferência, boleto

=== REGRAS DE DATA ===
- "ontem" = {ontem}
- "hoje" ou sem menção = {hoje}
- "anteontem" = {anteontem}
- "segunda", "terça"... = dia da semana mais recente relativo a hoje
- "dia 10" = dia do mês atual
- "semana passada" = subtraia 7 dias

=== REGRAS DE INTERPRETAÇÃO ===
- Nome de aluno → tipo=receita, categoria=Aluno
- Valores: "1k"=1000, "1.5k"=1500, por extenso também
- Múltiplos lançamentos na mesma frase → múltiplos itens em lancamentos
- "psico", "psi" = psicóloga = Saúde
- banco sem forma explícita → infira pix para inter/c6/caixa/nubank
- Compra parcelada: frases como "comprei X em 3x", "parcelei em 5 vezes", "3 parcelas de 50", "em 4x de 80" → inclua parcela_total=N no lançamento. O campo valor deve ser o valor de UMA parcela, nunca o total da compra. Se só souber o valor total, calcule valor = total / N. Ex: "tênis 300 em 3x" → valor=100, parcela_total=3. "comprei geladeira em 4x de 80" → valor=80, parcela_total=4. Sem parcelamento → parcela_total=null.

=== COMANDOS ===
- "resumo", "quanto gastei", "saldo" → comando=resumo_mes. Se o usuário citar um mês específico ("resumo junho", "resumo de maio", "resumo do mês passado"), coloque em comando_param o nome do mês em minúsculas (ex: "junho") ou "mes_passado" quando disser "mês passado"/"mês anterior". Sem menção de mês → comando_param=null (mês atual).
- "resumo semana", "essa semana" → comando=resumo_semana
- "por categoria" → comando=resumo_categoria
- "últimos N" → comando=ultimos, param=N
- "apagar último" → comando=apagar_ultimo
- "apagar N" → comando=apagar_id, param=N
- "editar N" → comando=editar, param=N
- "limite categoria valor" → comando=set_limite, param="categoria|valor"
- "limites", "ver limites" → comando=ver_limites
- "relatório", "relatorio" → comando=relatorio. Mesma regra de mês do resumo_mes: mês citado vai em comando_param (nome do mês ou "mes_passado").

Retorne APENAS JSON válido:
{{
  "lancamentos": [
    {{
      "tipo": "gasto" ou "receita",
      "valor": número,
      "categoria": string,
      "descricao": string resumida,
      "data": "YYYY-MM-DD",
      "forma_pagamento": string ou null,
      "local": string ou null,
      "parcela_total": número ou null
    }}
  ],
  "comando": null ou string,
  "comando_param": null ou valor
}}

Se não entender, retorne {{"erro": "mensagem não compreendida"}}."""

def teclado_principal():
    return ReplyKeyboardMarkup([
        [KeyboardButton("💸 Gasto"), KeyboardButton("💰 Receita")],
        [KeyboardButton("📊 Mês"), KeyboardButton("📅 Semana")],
        [KeyboardButton("🗂 Categorias"), KeyboardButton("📋 Últimos")],
        [KeyboardButton("🔔 Limites"), KeyboardButton("❓ Ajuda")]
    ], resize_keyboard=True)

def is_autorizado(update: Update) -> bool:
    if not ALLOWED_USER:
        return True
    return str(update.effective_user.id) == str(ALLOWED_USER)

RESPOSTAS_CONFIRMACAO = ("sim", "s", "ok", "confirma", "confirmado", "certo", "isso", "correto")


def eh_baixa_confianca(item: dict) -> bool:
    """Baixa confiança: categoria caiu no default 'Outros' (gasto sem match em
    nenhuma palavra-chave do dicionário de categorias) ou a forma de pagamento
    não foi identificada."""
    return item.get("categoria") == "Outros" or item.get("forma_pagamento") is None

# DESATIVADO: Railway está bloqueando api.anthropic.com, então a interpretação
# de linguagem natural passou a ser feita localmente (ver interpretador_local.py).
# Mantido aqui comentado como referência caso a rede volte a permitir.
#
# async def interpretar_com_claude(texto: str) -> dict:
#     hoje = date.today()
#     ontem = hoje - timedelta(days=1)
#     anteontem = hoje - timedelta(days=2)
#
#     system = SYSTEM_PROMPT.format(
#         hoje=hoje.isoformat(),
#         ontem=ontem.isoformat(),
#         anteontem=anteontem.isoformat()
#     )
#
#     try:
#         async with httpx.AsyncClient(timeout=20) as client:
#             resp = await client.post(
#                 "https://api.anthropic.com/v1/messages",
#                 headers={
#                     "x-api-key": ANTHROPIC_KEY,
#                     "anthropic-version": "2023-06-01",
#                     "content-type": "application/json"
#                 },
#                 json={
#                     "model": "claude-sonnet-4-6",
#                     "max_tokens": 800,
#                     "system": system,
#                     "messages": [{"role": "user", "content": texto}]
#                 }
#             )
#             data = resp.json()
#             if "error" in data:
#                 logger.error(f"Claude API error: {data['error']}")
#                 return {"erro": data["error"].get("message", "erro api")}
#             raw = data["content"][0]["text"].strip()
#             raw = re.sub(r"```json|```", "", raw).strip()
#             return json.loads(raw)
#     except Exception as e:
#         logger.error(f"Erro Claude API: {e}")
#         return {"erro": str(e)}

async def verificar_alertas(update: Update, categoria: str, mes: int, ano: int):
    try:
        alerta = supabase.table("alertas_categoria").select("limite").eq("categoria", categoria).eq("ativo", True).execute()
        if not alerta.data:
            return

        limite = float(alerta.data[0]["limite"])
        inicio = f"{ano}-{mes:02d}-01"
        fim = f"{ano+1}-01-01" if mes == 12 else f"{ano}-{mes+1:02d}-01"

        total = supabase.table("financeiro").select("valor").eq("tipo", "gasto").eq("categoria", categoria).gte("data", inicio).lt("data", fim).execute()
        gasto_total = sum(float(d["valor"]) for d in total.data)

        pct = (gasto_total / limite) * 100
        if pct >= 100:
            await update.message.reply_text(f"🚨 *LIMITE ULTRAPASSADO!*\n{categoria}: R$ {gasto_total:.2f} / R$ {limite:.2f} ({pct:.0f}%)", parse_mode="Markdown")
        elif pct >= 80:
            await update.message.reply_text(f"⚠️ *Atenção!* {categoria} em {pct:.0f}% do limite\nR$ {gasto_total:.2f} / R$ {limite:.2f}", parse_mode="Markdown")
    except Exception as e:
        logger.error(f"Erro alertas: {e}")

async def registrar_item_interpretado(update: Update, item: dict, hoje: date):
    """Grava um único lançamento vindo do interpretador local, respeitando o
    fluxo de parcelamento já existente (registrar_parcelado + sincronizar_divida)
    quando aplicável."""
    if item.get("parcelas"):
        l_parcela = dict(item)
        l_parcela["valor"] = item["valor_parcela"]
        await registrar_parcelado(update, l_parcela, item["parcelas"], hoje)
        await sincronizar_divida(l_parcela, item["parcelas"], hoje)
    else:
        await registrar_lancamentos(update, [item])

async def perguntar_confirmacao(update: Update, item: dict):
    await update.message.reply_text(
        f"Entendi: {item.get('descricao', '')}, R$ {float(item['valor']):.2f}, "
        f"categoria {item.get('categoria', 'Outros')}. Confirma? (sim / corrigir categoria)"
    )

async def processar_confirmacao(update: Update, context: ContextTypes.DEFAULT_TYPE):
    fila = context.user_data.get("confirmacao_fila") or []
    if not fila:
        context.user_data.pop("confirmacao_fila", None)
        context.user_data.pop("confirmacao_hoje", None)
        return

    texto = update.message.text.strip()
    item = fila[0]
    hoje = date.fromisoformat(context.user_data.get("confirmacao_hoje", date.today().isoformat()))

    if texto.lower().strip().rstrip(".!") not in RESPOSTAS_CONFIRMACAO:
        cat_match = re.match(r"categoria\s+(.+)", texto, flags=re.IGNORECASE)
        nova_categoria = cat_match.group(1).strip() if cat_match else texto.strip()
        item["categoria"] = nova_categoria.capitalize()

    await registrar_item_interpretado(update, item, hoje)

    fila.pop(0)
    if fila:
        context.user_data["confirmacao_fila"] = fila
        await perguntar_confirmacao(update, fila[0])
    else:
        context.user_data.pop("confirmacao_fila", None)
        context.user_data.pop("confirmacao_hoje", None)

async def registrar_lancamentos(update: Update, lancamentos: list):
    if not lancamentos:
        return
    hoje = date.today()
    for l in lancamentos:
        try:
            # Compra parcelada → gera grupo e insere N linhas, uma por mês
            try:
                parcela_total = int(l.get("parcela_total")) if l.get("parcela_total") else None
            except (ValueError, TypeError):
                parcela_total = None

            if parcela_total and parcela_total > 1:
                await registrar_parcelado(update, l, parcela_total, hoje)
                continue

            result = supabase.table("financeiro").insert({
                "tipo": l["tipo"],
                "valor": l["valor"],
                "categoria": l.get("categoria", "Outros"),
                "descricao": l.get("descricao", ""),
                "data": l.get("data", hoje.isoformat()),
                "forma_pagamento": l.get("forma_pagamento"),
                "local": l.get("local")
            }).execute()

            id_reg = result.data[0]["id"] if result.data else "?"
            emoji = "💸" if l["tipo"] == "gasto" else "💰"
            sinal = "-" if l["tipo"] == "gasto" else "+"
            forma = f" • {l['forma_pagamento']}" if l.get("forma_pagamento") else ""
            local = f" • {l['local']}" if l.get("local") else ""
            data_fmt = datetime.strptime(l.get("data", hoje.isoformat()), "%Y-%m-%d").strftime("%d/%m/%Y")

            await update.message.reply_text(
                f"{emoji} *{l['tipo'].capitalize()} registrado!*\n"
                f"ID: `{id_reg}`\n"
                f"Valor: *R$ {sinal}{float(l['valor']):.2f}*\n"
                f"Categoria: {l.get('categoria', 'Outros')}{forma}{local}\n"
                f"Data: {data_fmt}\n\n"
                f"_Para apagar: `apagar {id_reg}` | editar: `editar {id_reg}`_",
                parse_mode="Markdown",
                reply_markup=teclado_principal()
            )

            if l["tipo"] == "gasto":
                data_obj = datetime.strptime(l.get("data", hoje.isoformat()), "%Y-%m-%d").date()
                await verificar_alertas(update, l.get("categoria", "Outros"), data_obj.month, data_obj.year)

        except Exception as e:
            logger.error(f"Erro ao salvar: {e}")
            await update.message.reply_text("❌ Erro ao salvar.")

async def registrar_parcelado(update: Update, l: dict, parcela_total: int, hoje: date):
    """Insere N linhas em financeiro, uma por mês, com grupo_parcela compartilhado."""
    try:
        grupo = str(uuid.uuid4())
        data_base = datetime.strptime(l.get("data", hoje.isoformat()), "%Y-%m-%d").date()
        valor_parcela = round(float(l["valor"]), 2)
        tipo = l.get("tipo", "gasto")

        ids = []
        for i in range(parcela_total):
            data_parcela = add_meses(data_base, i)
            result = supabase.table("financeiro").insert({
                "tipo": tipo,
                "valor": valor_parcela,
                "categoria": l.get("categoria", "Outros"),
                "descricao": l.get("descricao", ""),
                "data": data_parcela.isoformat(),
                "forma_pagamento": l.get("forma_pagamento"),
                "local": l.get("local"),
                "grupo_parcela": grupo,
                "parcela_atual": i + 1,
                "parcela_total": parcela_total
            }).execute()
            if result.data:
                ids.append(result.data[0]["id"])

        data_fim = add_meses(data_base, parcela_total - 1)
        ini_fmt = f"{MESES_ABBR[data_base.month - 1]}/{data_base.year}"
        fim_fmt = f"{MESES_ABBR[data_fim.month - 1]}/{data_fim.year}"
        ids_txt = ", ".join(str(x) for x in ids)
        forma = f" • {l['forma_pagamento']}" if l.get("forma_pagamento") else ""

        await update.message.reply_text(
            f"🗓️ *Compra parcelada registrada!*\n"
            f"{parcela_total}x de *R$ {valor_parcela:.2f}* — {ini_fmt} a {fim_fmt}\n"
            f"{l.get('categoria', 'Outros')}{forma}\n"
            f"Grupo: `{grupo}`\n"
            f"IDs: {ids_txt}\n\n"
            f"_Apagar parcelas futuras: `apagar grupo {grupo}`_",
            parse_mode="Markdown",
            reply_markup=teclado_principal()
        )

        if tipo == "gasto":
            await verificar_alertas(update, l.get("categoria", "Outros"), data_base.month, data_base.year)

    except Exception as e:
        logger.error(f"Erro parcelado: {e}")
        await update.message.reply_text("❌ Erro ao registrar compra parcelada.")

async def sincronizar_divida(l: dict, parcela_total: int, hoje: date):
    """Espelha uma compra parcelada detectada por linguagem natural em `dividas` +
    `dividas_parcelas`, para que ela entre no total de dívidas e na projeção de
    quitação (que não leem `financeiro`).

    Só é chamada no fluxo de texto livre (interpretador_local), não em compras
    estruturadas manuais. Falha aqui é só logada — o gasto já foi gravado em
    `financeiro` antes desta chamada e não pode ser perdido por causa disso.
    """
    try:
        data_base = datetime.strptime(l.get("data", hoje.isoformat()), "%Y-%m-%d").date()
        valor_parcela = round(float(l["valor"]), 2)
        saldo_total = round(valor_parcela * parcela_total, 2)

        divida_result = supabase.table("dividas").insert({
            "descricao": l.get("descricao", "Compra parcelada"),
            "titular": "Giácomo",
            "saldo_total": saldo_total,
            "parcela_mensal": valor_parcela,
            "parcelas_restantes": parcela_total,
            "tipo": "compra_parcelada",
            "status": "ativo",
            "observacao": f"Criada automaticamente via bot em {hoje.strftime('%d/%m/%Y')}"
        }).execute()
        divida_id = divida_result.data[0]["id"]

        parcelas_rows = []
        for i in range(parcela_total):
            data_parcela = add_meses(data_base, i)
            parcelas_rows.append({
                "divida_id": divida_id,
                "mes": data_parcela.month,
                "ano": data_parcela.year,
                "valor": valor_parcela,
                "pago": False
            })
        supabase.table("dividas_parcelas").insert(parcelas_rows).execute()

    except Exception as e:
        logger.error(f"Erro ao sincronizar dívida: {e}")

async def apagar_grupo(update: Update, grupo: str):
    """Apaga todas as parcelas futuras (data >= hoje) de uma compra parcelada."""
    try:
        hoje = date.today()
        result = supabase.table("financeiro").select(
            "id,valor,data"
        ).eq("grupo_parcela", grupo).gte("data", hoje.isoformat()).order("data").execute()
        futuras = result.data or []

        if not futuras:
            await update.message.reply_text(
                "📭 Nenhuma parcela futura encontrada para esse grupo.",
                reply_markup=teclado_principal()
            )
            return

        ids = [d["id"] for d in futuras]
        total = sum(float(d["valor"]) for d in futuras)
        supabase.table("financeiro").delete().eq("grupo_parcela", grupo).gte("data", hoje.isoformat()).execute()

        ids_txt = ", ".join(str(x) for x in ids)
        await update.message.reply_text(
            f"🗑 *{len(ids)} parcela(s) futura(s) apagada(s)!*\n"
            f"Total removido: R$ {total:.2f}\n"
            f"IDs: {ids_txt}",
            parse_mode="Markdown",
            reply_markup=teclado_principal()
        )
    except Exception as e:
        logger.error(f"Erro apagar grupo: {e}")
        await update.message.reply_text("❌ Erro ao apagar grupo.")

async def ultimos_lancamentos(update: Update, n=5):
    try:
        result = supabase.table("financeiro").select("id,tipo,valor,categoria,descricao,data,forma_pagamento").order("id", desc=True).limit(n).execute()
        dados = result.data
        if not dados:
            await update.message.reply_text("📭 Nenhum lançamento.", reply_markup=teclado_principal())
            return

        texto = f"📋 *Últimos {len(dados)} lançamentos:*\n\n"
        for d in dados:
            emoji = "💸" if d["tipo"] == "gasto" else "💰"
            sinal = "-" if d["tipo"] == "gasto" else "+"
            forma = f" • {d['forma_pagamento']}" if d.get("forma_pagamento") else ""
            texto += f"{emoji} `ID {d['id']}` — *R$ {sinal}{float(d['valor']):.2f}*\n"
            texto += f"  {d['categoria']}{forma} | {d['data']}\n"
            texto += f"  _{d['descricao'][:45]}_\n\n"

        texto += "_`apagar <ID>` | `editar <ID>`_"
        await update.message.reply_text(texto, parse_mode="Markdown", reply_markup=teclado_principal())
    except Exception as e:
        logger.error(f"Erro últimos: {e}")

async def apagar_lancamento(update: Update, id_alvo):
    try:
        if id_alvo == "ultimo":
            result = supabase.table("financeiro").select("id,tipo,valor,categoria").order("id", desc=True).limit(1).execute()
            if not result.data:
                await update.message.reply_text("📭 Nenhum lançamento para apagar.")
                return
            item = result.data[0]
            id_alvo = item["id"]
        else:
            result = supabase.table("financeiro").select("id,tipo,valor,categoria").eq("id", id_alvo).execute()
            if not result.data:
                await update.message.reply_text(f"❌ ID {id_alvo} não encontrado.")
                return
            item = result.data[0]

        supabase.table("financeiro").delete().eq("id", id_alvo).execute()
        emoji = "💸" if item["tipo"] == "gasto" else "💰"
        await update.message.reply_text(
            f"🗑 Apagado!\n{emoji} ID `{id_alvo}` — R$ {float(item['valor']):.2f} ({item['categoria']})",
            parse_mode="Markdown", reply_markup=teclado_principal()
        )
    except Exception as e:
        logger.error(f"Erro apagar: {e}")

async def corrigir_categoria(update: Update, id_alvo: int, nova_categoria: str):
    """Atualiza a categoria de uma linha existente em `financeiro` (não cria linha
    nova) — usado tanto avulso quanto em resposta à revisão semanal de 'Outros'."""
    try:
        result = supabase.table("financeiro").select("id,categoria").eq("id", id_alvo).execute()
        if not result.data:
            await update.message.reply_text(f"❌ ID {id_alvo} não encontrado.")
            return

        cat = nova_categoria.strip().capitalize()
        supabase.table("financeiro").update({"categoria": cat}).eq("id", id_alvo).execute()
        await update.message.reply_text(
            f"✅ ID `{id_alvo}` atualizado para *{cat}*.",
            parse_mode="Markdown", reply_markup=teclado_principal()
        )
    except Exception as e:
        logger.error(f"Erro corrigir categoria: {e}")
        await update.message.reply_text("❌ Erro ao corrigir categoria.")

async def iniciar_edicao(update: Update, context: ContextTypes.DEFAULT_TYPE, id_alvo: int):
    try:
        result = supabase.table("financeiro").select("*").eq("id", id_alvo).execute()
        if not result.data:
            await update.message.reply_text(f"❌ ID {id_alvo} não encontrado.")
            return
        item = result.data[0]
        context.user_data["editando_id"] = id_alvo
        context.user_data["editando_item"] = item

        emoji = "💸" if item["tipo"] == "gasto" else "💰"
        await update.message.reply_text(
            f"✏️ *Editando ID {id_alvo}:*\n"
            f"{emoji} R$ {float(item['valor']):.2f} — {item['categoria']} — {item['data']}\n\n"
            f"Diga o que corrigir:\n"
            f"  `valor 150`\n"
            f"  `categoria Alimentação`\n"
            f"  `data 15/06/2026`\n"
            f"  `forma pix`\n"
            f"  `cancelar`",
            parse_mode="Markdown"
        )
    except Exception as e:
        logger.error(f"Erro editar: {e}")

async def processar_edicao(update: Update, context: ContextTypes.DEFAULT_TYPE):
    texto = update.message.text.lower().strip()
    id_alvo = context.user_data.get("editando_id")
    item = context.user_data.get("editando_item")

    if texto == "cancelar":
        context.user_data.pop("editando_id", None)
        context.user_data.pop("editando_item", None)
        await update.message.reply_text("❌ Edição cancelada.", reply_markup=teclado_principal())
        return

    updates = {}

    valor_match = re.match(r"valor\s+(\d+(?:[.,]\d{1,2})?)", texto)
    cat_match = re.match(r"categoria\s+(.+)", texto)
    data_match = re.match(r"data\s+(\d{2}/\d{2}/\d{4})", texto)
    forma_match = re.match(r"forma\s+(.+)", texto)

    if valor_match:
        updates["valor"] = float(valor_match.group(1).replace(",", "."))
    elif cat_match:
        updates["categoria"] = cat_match.group(1).strip().capitalize()
    elif data_match:
        d, m, a = data_match.group(1).split("/")
        updates["data"] = f"{a}-{m}-{d}"
    elif forma_match:
        updates["forma_pagamento"] = forma_match.group(1).strip()
    else:
        await update.message.reply_text("⚠️ Não entendi. Tenta: `valor 150`, `categoria Alimentação`, `data 15/06/2026`, `cancelar`", parse_mode="Markdown")
        return

    try:
        supabase.table("financeiro").update(updates).eq("id", id_alvo).execute()
        context.user_data.pop("editando_id", None)
        context.user_data.pop("editando_item", None)
        await update.message.reply_text(f"✅ ID `{id_alvo}` atualizado!", parse_mode="Markdown", reply_markup=teclado_principal())
    except Exception as e:
        logger.error(f"Erro ao editar: {e}")
        await update.message.reply_text("❌ Erro ao editar.")

async def resumo_mes(update: Update, mes=None, ano=None):
    hoje = date.today()
    mes = mes or hoje.month
    ano = ano or hoje.year
    inicio = f"{ano}-{mes:02d}-01"
    fim = f"{ano+1}-01-01" if mes == 12 else f"{ano}-{mes+1:02d}-01"

    try:
        result = supabase.table("financeiro").select("tipo,valor,categoria").gte("data", inicio).lt("data", fim).execute()
        dados = result.data
        nome_mes = ["Jan","Fev","Mar","Abr","Mai","Jun","Jul","Ago","Set","Out","Nov","Dez"][mes-1]

        if not dados:
            await update.message.reply_text(f"📭 Nenhum lançamento em {nome_mes}/{ano}.", reply_markup=teclado_principal())
            return

        total_receita = sum(float(d["valor"]) for d in dados if d["tipo"] == "receita")
        total_gasto = sum(float(d["valor"]) for d in dados if d["tipo"] == "gasto")
        saldo = total_receita - total_gasto

        cats = {}
        for d in dados:
            if d["tipo"] == "gasto":
                cats[d["categoria"]] = cats.get(d["categoria"], 0) + float(d["valor"])

        cats_texto = "\n".join(f"  • {c}: R$ {v:.2f}" for c, v in sorted(cats.items(), key=lambda x: -x[1]))
        saldo_emoji = "✅" if saldo >= 0 else "🔴"

        await update.message.reply_text(
            f"📊 *Resumo — {nome_mes}/{ano}*\n\n"
            f"💰 Receitas: R$ {total_receita:.2f}\n"
            f"💸 Gastos: R$ {total_gasto:.2f}\n"
            f"{saldo_emoji} Saldo: R$ {saldo:.2f}\n\n"
            f"*Por categoria:*\n{cats_texto}",
            parse_mode="Markdown", reply_markup=teclado_principal()
        )
    except Exception as e:
        logger.error(f"Erro resumo: {e}")

async def resumo_semana(update: Update):
    hoje = date.today()
    inicio_semana = hoje - timedelta(days=hoje.weekday())
    try:
        result = supabase.table("financeiro").select("tipo,valor,categoria,data").gte("data", inicio_semana.isoformat()).lte("data", hoje.isoformat()).execute()
        dados = result.data

        if not dados:
            await update.message.reply_text("📭 Nenhum lançamento essa semana.", reply_markup=teclado_principal())
            return

        total_receita = sum(float(d["valor"]) for d in dados if d["tipo"] == "receita")
        total_gasto = sum(float(d["valor"]) for d in dados if d["tipo"] == "gasto")
        saldo = total_receita - total_gasto

        cats = {}
        for d in dados:
            if d["tipo"] == "gasto":
                cats[d["categoria"]] = cats.get(d["categoria"], 0) + float(d["valor"])

        cats_texto = "\n".join(f"  • {c}: R$ {v:.2f}" for c, v in sorted(cats.items(), key=lambda x: -x[1]))
        saldo_emoji = "✅" if saldo >= 0 else "🔴"

        await update.message.reply_text(
            f"📅 *Resumo da Semana*\n"
            f"({inicio_semana.strftime('%d/%m')} a {hoje.strftime('%d/%m')})\n\n"
            f"💰 Receitas: R$ {total_receita:.2f}\n"
            f"💸 Gastos: R$ {total_gasto:.2f}\n"
            f"{saldo_emoji} Saldo: R$ {saldo:.2f}\n\n"
            f"*Por categoria:*\n{cats_texto}",
            parse_mode="Markdown", reply_markup=teclado_principal()
        )
    except Exception as e:
        logger.error(f"Erro resumo semana: {e}")

async def resumo_categoria(update: Update):
    hoje = date.today()
    inicio = f"{hoje.year}-{hoje.month:02d}-01"
    try:
        result = supabase.table("financeiro").select("tipo,valor,categoria").gte("data", inicio).execute()
        dados = result.data
        alertas = supabase.table("alertas_categoria").select("categoria,limite").eq("ativo", True).execute()
        limites = {a["categoria"]: float(a["limite"]) for a in alertas.data}

        if not dados:
            await update.message.reply_text("📭 Nenhum lançamento este mês.", reply_markup=teclado_principal())
            return

        gastos = {}
        receitas = {}
        for d in dados:
            cat = d["categoria"]
            val = float(d["valor"])
            if d["tipo"] == "gasto":
                gastos[cat] = gastos.get(cat, 0) + val
            else:
                receitas[cat] = receitas.get(cat, 0) + val

        texto = f"🗂 *Por categoria — {hoje.strftime('%m/%Y')}*\n\n"
        if receitas:
            texto += "*Receitas:*\n"
            for cat, val in sorted(receitas.items(), key=lambda x: -x[1]):
                texto += f"  💰 {cat}: R$ {val:.2f}\n"
            texto += "\n"
        if gastos:
            texto += "*Gastos:*\n"
            for cat, val in sorted(gastos.items(), key=lambda x: -x[1]):
                if cat in limites:
                    pct = (val / limites[cat]) * 100
                    barra = "🔴" if pct >= 100 else "⚠️" if pct >= 80 else "✅"
                    texto += f"  💸 {cat}: R$ {val:.2f} / R$ {limites[cat]:.0f} {barra}\n"
                else:
                    texto += f"  💸 {cat}: R$ {val:.2f}\n"

        await update.message.reply_text(texto, parse_mode="Markdown", reply_markup=teclado_principal())
    except Exception as e:
        logger.error(f"Erro categoria: {e}")

async def set_limite(update: Update, param: str):
    try:
        partes = param.split("|")
        if len(partes) != 2:
            await update.message.reply_text("⚠️ Formato inválido.")
            return
        categoria = partes[0].strip().capitalize()
        limite = float(partes[1].strip())

        supabase.table("alertas_categoria").upsert({
            "categoria": categoria,
            "limite": limite,
            "ativo": True
        }, on_conflict="categoria").execute()

        await update.message.reply_text(
            f"🔔 Limite definido!\n*{categoria}*: R$ {limite:.2f}/mês",
            parse_mode="Markdown", reply_markup=teclado_principal()
        )
    except Exception as e:
        logger.error(f"Erro set_limite: {e}")

async def ver_limites(update: Update):
    try:
        result = supabase.table("alertas_categoria").select("categoria,limite,ativo").order("categoria").execute()
        if not result.data:
            await update.message.reply_text(
                "📭 Nenhum limite definido.\n\nDefina com: `limite Alimentação 500`",
                parse_mode="Markdown", reply_markup=teclado_principal()
            )
            return

        texto = "🔔 *Limites por categoria:*\n\n"
        for a in result.data:
            status = "✅" if a["ativo"] else "⏸"
            texto += f"{status} *{a['categoria']}*: R$ {float(a['limite']):.2f}/mês\n"

        texto += "\n_Para alterar: `limite Categoria valor`_\n_Para remover: `remover limite Categoria`_"
        await update.message.reply_text(texto, parse_mode="Markdown", reply_markup=teclado_principal())
    except Exception as e:
        logger.error(f"Erro ver_limites: {e}")

async def gerar_relatorio(update: Update, mes=None, ano=None):
    hoje = date.today()
    mes = mes or hoje.month
    ano = ano or hoje.year
    inicio = f"{ano}-{mes:02d}-01"
    fim = f"{ano+1}-01-01" if mes == 12 else f"{ano}-{mes+1:02d}-01"
    nome_mes = ["Janeiro","Fevereiro","Março","Abril","Maio","Junho","Julho","Agosto","Setembro","Outubro","Novembro","Dezembro"][mes-1]

    try:
        result = supabase.table("financeiro").select("tipo,valor,categoria,data,descricao").gte("data", inicio).lt("data", fim).order("data").execute()
        dados = result.data

        if not dados:
            await update.message.reply_text(f"📭 Nenhum lançamento em {nome_mes}/{ano}.")
            return

        total_receita = sum(float(d["valor"]) for d in dados if d["tipo"] == "receita")
        total_gasto = sum(float(d["valor"]) for d in dados if d["tipo"] == "gasto")
        saldo = total_receita - total_gasto

        gastos = {}
        receitas = {}
        for d in dados:
            cat = d["categoria"]
            val = float(d["valor"])
            if d["tipo"] == "gasto":
                gastos[cat] = gastos.get(cat, 0) + val
            else:
                receitas[cat] = receitas.get(cat, 0) + val

        relatorio = f"📑 *RELATÓRIO FINANCEIRO — {nome_mes.upper()}/{ano}*\n"
        relatorio += "━━━━━━━━━━━━━━━━━━━━\n\n"
        relatorio += f"💰 *RECEITAS TOTAIS:* R$ {total_receita:.2f}\n"
        if receitas:
            for cat, val in sorted(receitas.items(), key=lambda x: -x[1]):
                relatorio += f"  • {cat}: R$ {val:.2f}\n"

        relatorio += f"\n💸 *GASTOS TOTAIS:* R$ {total_gasto:.2f}\n"
        if gastos:
            for cat, val in sorted(gastos.items(), key=lambda x: -x[1]):
                pct = (val / total_gasto * 100) if total_gasto > 0 else 0
                relatorio += f"  • {cat}: R$ {val:.2f} ({pct:.0f}%)\n"

        saldo_emoji = "✅" if saldo >= 0 else "🔴"
        relatorio += f"\n━━━━━━━━━━━━━━━━━━━━\n"
        relatorio += f"{saldo_emoji} *SALDO: R$ {saldo:.2f}*\n"
        relatorio += f"━━━━━━━━━━━━━━━━━━━━"

        await update.message.reply_text(relatorio, parse_mode="Markdown", reply_markup=teclado_principal())
    except Exception as e:
        logger.error(f"Erro relatório: {e}")

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_autorizado(update):
        await update.message.reply_text("❌ Acesso não autorizado.")
        return
    await update.message.reply_text(
        "👋 Olá, Giácomo!\n\n"
        "Fala naturalmente:\n"
        "  _Gastei 100 ontem de gasolina, pix inter_\n"
        "  _Cecília pagou 900 hoje_\n"
        "  _Paguei 50 almoço e 30 café_\n"
        "  _Resumo do mês / semana_\n"
        "  _Editar 5 / apagar 5_\n"
        "  _Limite Alimentação 500_",
        parse_mode="Markdown",
        reply_markup=teclado_principal()
    )

async def ajuda(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_autorizado(update):
        return
    await update.message.reply_text(
        "📖 *Comandos disponíveis:*\n\n"
        "*Registrar:*\n"
        "  `gastei 150 gasolina ontem pix inter`\n"
        "  `Cecília pagou 900`\n"
        "  `50 almoço e 30 café`\n\n"
        "*Consultar:*\n"
        "  `resumo do mês` / `resumo de julho`\n"
        "  `resumo da semana`\n"
        "  `por categoria`\n"
        "  `últimos 10`\n"
        "  `relatório` / `relatório de junho`\n\n"
        "*Editar/Apagar:*\n"
        "  `editar 5`\n"
        "  `apagar 5` / `apagar último`\n\n"
        "*Limites:*\n"
        "  `limite Alimentação 500`\n"
        "  `ver limites`\n"
        "  `remover limite Alimentação`",
        parse_mode="Markdown",
        reply_markup=teclado_principal()
    )

async def processar_mensagem(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_autorizado(update):
        return

    texto_orig = update.message.text.strip()
    texto = texto_orig.lower().strip()

    # Se está no modo edição
    if "editando_id" in context.user_data:
        await processar_edicao(update, context)
        return

    # Se há lançamentos de baixa confiança aguardando confirmação
    if "confirmacao_fila" in context.user_data:
        await processar_confirmacao(update, context)
        return

    # Botões
    if texto in ["💸 gasto", "💰 receita"]:
        tipo = "gasto" if "gasto" in texto else "receita"
        context.user_data["aguardando_tipo"] = tipo
        await update.message.reply_text(f"Digite o {'gasto' if tipo=='gasto' else 'receita'}:\nEx: `{'150 gasolina ontem' if tipo=='gasto' else '650 Antonio hoje'}`", parse_mode="Markdown")
        return

    if texto == "📊 mês":
        await resumo_mes(update)
        return
    if texto == "📅 semana":
        await resumo_semana(update)
        return
    if texto == "🗂 categorias":
        await resumo_categoria(update)
        return
    if texto == "📋 últimos":
        await ultimos_lancamentos(update)
        return
    if texto == "🔔 limites":
        await ver_limites(update)
        return
    if texto == "❓ ajuda":
        await ajuda(update, context)
        return

    # Comandos diretos sem IA
    apagar_grupo_match = re.match(r"apagar\s+grupo\s+([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})", texto)
    if apagar_grupo_match:
        await apagar_grupo(update, apagar_grupo_match.group(1))
        return

    apagar_match = re.match(r"apagar\s+(\d+|[uú]ltimo)", texto)
    if apagar_match:
        param = apagar_match.group(1)
        await apagar_lancamento(update, "ultimo" if "ltimo" in param else int(param))
        return

    editar_match = re.match(r"editar\s+(\d+)", texto)
    if editar_match:
        await iniciar_edicao(update, context, int(editar_match.group(1)))
        return

    remover_limite_match = re.match(r"remover limite\s+(.+)", texto)
    if remover_limite_match:
        cat = remover_limite_match.group(1).strip().capitalize()
        supabase.table("alertas_categoria").update({"ativo": False}).eq("categoria", cat).execute()
        await update.message.reply_text(f"🔕 Limite de *{cat}* removido.", parse_mode="Markdown", reply_markup=teclado_principal())
        return

    limite_match = re.match(r"limite\s+([a-záéíóúâêôãõç\s]+?)\s+(\d+(?:[.,]\d{1,2})?)", texto)
    if limite_match:
        cat = limite_match.group(1).strip().capitalize()
        val = float(limite_match.group(2).replace(",", "."))
        await set_limite(update, f"{cat}|{val}")
        return

    corrigir_match = re.match(r"corrigir\s+(\d+)\s+(.+)", texto)
    if corrigir_match:
        await corrigir_categoria(update, int(corrigir_match.group(1)), corrigir_match.group(2).strip())
        return

    # Aguardando após botão
    if "aguardando_tipo" in context.user_data:
        tipo = context.user_data.pop("aguardando_tipo")
        texto_orig = f"{'gastei' if tipo == 'gasto' else 'recebi'} {texto_orig}"

    # Comandos de consulta em texto livre (resumo, relatório, últimos, etc.)
    cmd, param = detectar_comando_texto(texto_orig)

    if cmd == "resumo_mes":
        mes_num, ano_num = resolver_mes(param)
        await resumo_mes(update, mes=mes_num, ano=ano_num)
        return
    elif cmd == "resumo_semana":
        await resumo_semana(update)
        return
    elif cmd == "resumo_categoria":
        await resumo_categoria(update)
        return
    elif cmd == "ultimos":
        await ultimos_lancamentos(update, n=int(param) if param else 5)
        return
    elif cmd == "ver_limites":
        await ver_limites(update)
        return
    elif cmd == "relatorio":
        mes_num, ano_num = resolver_mes(param)
        await gerar_relatorio(update, mes=mes_num, ano=ano_num)
        return

    # Interpretador local de linguagem natural (sem API externa — ver interpretador_local.py)
    lancamentos = interpretar_local(texto_orig)
    if lancamentos:
        hoje = date.today()
        confiaveis = [item for item in lancamentos if not eh_baixa_confianca(item)]
        baixa_conf = [item for item in lancamentos if eh_baixa_confianca(item)]

        simples = []
        for item in confiaveis:
            if item.get("parcelas"):
                l_parcela = dict(item)
                l_parcela["valor"] = item["valor_parcela"]
                await registrar_parcelado(update, l_parcela, item["parcelas"], hoje)
                await sincronizar_divida(l_parcela, item["parcelas"], hoje)
            else:
                simples.append(item)
        if simples:
            await registrar_lancamentos(update, simples)

        # Lançamentos de baixa confiança (categoria "Outros" ou forma de pagamento
        # não identificada) esperam confirmação do usuário antes de gravar
        if baixa_conf:
            context.user_data["confirmacao_fila"] = baixa_conf
            context.user_data["confirmacao_hoje"] = hoje.isoformat()
            await perguntar_confirmacao(update, baixa_conf[0])
    else:
        await update.message.reply_text(
            "⚠️ Não entendi. Tenta:\n`gastei 150 gasolina`\n`recebi 650 Cecília`",
            parse_mode="Markdown"
        )

async def revisar_outros_semanal(context: ContextTypes.DEFAULT_TYPE):
    """Job semanal: lista os lançamentos com categoria 'Outros' da semana e pede
    revisão. Não envia nada se não houver nenhum."""
    if not ALLOWED_USER:
        return
    try:
        hoje = date.today()
        inicio_semana = hoje - timedelta(days=hoje.weekday())
        result = supabase.table("financeiro").select("id,valor,descricao,data").eq(
            "categoria", "Outros"
        ).gte("data", inicio_semana.isoformat()).lte("data", hoje.isoformat()).order("data").execute()
        dados = result.data or []

        if not dados:
            return

        linhas = "\n".join(
            f"`ID {d['id']}` — {d['descricao'][:40]} — R$ {float(d['valor']):.2f}"
            for d in dados
        )
        texto = (
            f"🗂 *Revisão semanal — categoria \"Outros\"*\n"
            f"({inicio_semana.strftime('%d/%m')} a {hoje.strftime('%d/%m')})\n\n"
            f"{linhas}\n\n"
            f"Revisa e me diz a categoria certa de cada um:\n"
            f"`corrigir <ID> <categoria>`\n"
            f"Ex: `corrigir {dados[0]['id']} Alimentação`"
        )
        await context.bot.send_message(chat_id=int(ALLOWED_USER), text=texto, parse_mode="Markdown")
    except Exception as e:
        logger.error(f"Erro revisão semanal 'Outros': {e}")

def main():
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("ajuda", ajuda))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, processar_mensagem))

    if app.job_queue:
        # Domingo 20h (horário de Fortaleza) — revisão semanal de lançamentos "Outros".
        # PTB usa 0-6 = domingo-sábado para `days`.
        app.job_queue.run_daily(
            revisar_outros_semanal,
            time=dtime(hour=20, minute=0, tzinfo=ZoneInfo("America/Fortaleza")),
            days=(0,),
        )
    else:
        logger.warning(
            "JobQueue indisponível — revisão semanal de 'Outros' desativada. "
            "Instale python-telegram-bot[job-queue]."
        )

    logger.info("Bot GP Financeiro iniciado...")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
