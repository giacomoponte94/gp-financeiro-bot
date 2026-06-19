import os
import re
import json
import logging
import httpx
from datetime import datetime, date, timedelta
from telegram import Update, ReplyKeyboardMarkup, KeyboardButton
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
from supabase import create_client, Client

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

SYSTEM_PROMPT = """Você é um assistente financeiro pessoal do Giácomo, personal trainer em Fortaleza-CE.
Sua função é interpretar mensagens sobre gastos e receitas e retornar JSON estruturado.

Data de hoje: {hoje}

Categorias válidas para GASTO:
Alimentação, Gasolina, Financiamento, Consórcio, XP, Santander, Nubank, 
Internet, Vivo, MEI, Seguro, Prudential, Saúde, Lazer, Compras, Negócio, Outros

Categorias válidas para RECEITA:
Aluno, Outros

Formas de pagamento possíveis: pix, crédito, débito, dinheiro, transferência
Bancos/cartões: nubank, inter, c6, santander, xp, caixa, bradesco, itaú

Regras de data:
- "ontem" = {ontem}
- "hoje" = {hoje}
- "anteontem" = {anteontem}
- Dias da semana: calcule relativo ao hoje
- "dia 10" = dia 10 do mês atual
- Se não mencionar data, use hoje

Retorne APENAS JSON válido, sem texto adicional, neste formato:
{{
  "lancamentos": [
    {{
      "tipo": "gasto" ou "receita",
      "valor": número,
      "categoria": string,
      "descricao": string resumida,
      "data": "YYYY-MM-DD",
      "forma_pagamento": string ou null,
      "local": string ou null
    }}
  ],
  "comando": null ou "resumo_mes" ou "resumo_categoria" ou "ultimos" ou "apagar_ultimo" ou "apagar_id",
  "comando_param": null ou número (para apagar_id) ou nome_mes (para resumo_mes)
}}

Se a mensagem for um comando (resumo, apagar, últimos), retorne lancamentos vazio e preencha comando.
Se não entender, retorne {{"erro": "mensagem não compreendida"}}."""

def teclado_principal():
    return ReplyKeyboardMarkup([
        [KeyboardButton("💸 Registrar Gasto"), KeyboardButton("💰 Registrar Receita")],
        [KeyboardButton("📊 Resumo do Mês"), KeyboardButton("🗂 Por Categoria")],
        [KeyboardButton("📋 Últimos Lançamentos"), KeyboardButton("❓ Ajuda")]
    ], resize_keyboard=True)

def is_autorizado(update: Update) -> bool:
    if not ALLOWED_USER:
        return True
    return str(update.effective_user.id) == str(ALLOWED_USER)

async def interpretar_com_claude(texto: str) -> dict:
    hoje = date.today()
    ontem = hoje - timedelta(days=1)
    anteontem = hoje - timedelta(days=2)

    system = SYSTEM_PROMPT.format(
        hoje=hoje.isoformat(),
        ontem=ontem.isoformat(),
        anteontem=anteontem.isoformat()
    )

    try:
        logger.info(f"Chamando Claude API. ANTHROPIC_KEY presente: {bool(ANTHROPIC_KEY)}, tamanho: {len(ANTHROPIC_KEY) if ANTHROPIC_KEY else 0}")
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(
                "https://api.anthropic.com/v1/messages",
                headers={
                    "x-api-key": ANTHROPIC_KEY,
                    "anthropic-version": "2023-06-01",
                    "content-type": "application/json"
                },
                json={
                    "model": "claude-sonnet-4-6",
                    "max_tokens": 500,
                    "system": system,
                    "messages": [{"role": "user", "content": texto}]
                }
            )
            logger.info(f"Claude API status: {resp.status_code}")
            logger.info(f"Claude API response: {resp.text[:300]}")
            data = resp.json()
            raw = data["content"][0]["text"].strip()
            raw = re.sub(r"```json|```", "", raw).strip()
            return json.loads(raw)
    except Exception as e:
        logger.error(f"Erro Claude API: {e}")
        return {"erro": str(e)}

async def registrar_lancamentos(update: Update, lancamentos: list):
    if not lancamentos:
        return
    for l in lancamentos:
        try:
            result = supabase.table("financeiro").insert({
                "tipo": l["tipo"],
                "valor": l["valor"],
                "categoria": l.get("categoria", "Outros"),
                "descricao": l.get("descricao", ""),
                "data": l.get("data", date.today().isoformat()),
                "forma_pagamento": l.get("forma_pagamento"),
                "local": l.get("local")
            }).execute()

            id_reg = result.data[0]["id"] if result.data else "?"
            emoji = "💸" if l["tipo"] == "gasto" else "💰"
            sinal = "-" if l["tipo"] == "gasto" else "+"
            forma = f" • {l['forma_pagamento']}" if l.get("forma_pagamento") else ""
            local = f" • {l['local']}" if l.get("local") else ""

            await update.message.reply_text(
                f"{emoji} *{l['tipo'].capitalize()} registrado!*\n"
                f"ID: `{id_reg}`\n"
                f"Valor: *R$ {sinal}{float(l['valor']):.2f}*\n"
                f"Categoria: {l.get('categoria', 'Outros')}{forma}{local}\n"
                f"Data: {datetime.strptime(l['data'], '%Y-%m-%d').strftime('%d/%m/%Y')}\n\n"
                f"_Para apagar: `apagar {id_reg}`_",
                parse_mode="Markdown",
                reply_markup=teclado_principal()
            )
        except Exception as e:
            logger.error(f"Erro ao salvar lançamento: {e}")
            await update.message.reply_text("❌ Erro ao salvar. Tenta de novo.")

async def ultimos_lancamentos(update: Update, n=5):
    try:
        result = supabase.table("financeiro")\
            .select("id, tipo, valor, categoria, descricao, data, forma_pagamento")\
            .order("id", desc=True)\
            .limit(n)\
            .execute()

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

        texto += "_Para apagar: `apagar <ID>`_"
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
            parse_mode="Markdown",
            reply_markup=teclado_principal()
        )
    except Exception as e:
        logger.error(f"Erro apagar: {e}")

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
            f"*Gastos por categoria:*\n{cats_texto}",
            parse_mode="Markdown",
            reply_markup=teclado_principal()
        )
    except Exception as e:
        logger.error(f"Erro resumo: {e}")

async def resumo_categoria(update: Update):
    hoje = date.today()
    inicio = f"{hoje.year}-{hoje.month:02d}-01"
    try:
        result = supabase.table("financeiro").select("tipo,valor,categoria").gte("data", inicio).execute()
        dados = result.data
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
                texto += f"  💸 {cat}: R$ {val:.2f}\n"

        await update.message.reply_text(texto, parse_mode="Markdown", reply_markup=teclado_principal())
    except Exception as e:
        logger.error(f"Erro categoria: {e}")

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_autorizado(update):
        await update.message.reply_text("❌ Acesso não autorizado.")
        return
    await update.message.reply_text(
        "👋 Olá, Giácomo! Sou seu assistente financeiro com IA.\n\n"
        "Fala naturalmente:\n"
        "  _Gastei 100 ontem de gasolina no posto, pix inter_\n"
        "  _Recebi 650 da Cecília hoje_\n"
        "  _Paguei 50 de almoço e 30 de café_\n"
        "  _Resumo de junho_\n"
        "  _Apagar último_",
        parse_mode="Markdown",
        reply_markup=teclado_principal()
    )

async def ajuda(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_autorizado(update):
        return
    await update.message.reply_text(
        "📖 *Como usar:*\n\n"
        "*Fala naturalmente — exemplos:*\n"
        "  `gastei 150 de gasolina ontem no posto shell, débito inter`\n"
        "  `recebi 900 da Cecília hoje`\n"
        "  `paguei 808 do financiamento dia 15`\n"
        "  `50 de lanche e 30 de estacionamento`\n\n"
        "*Consultas:*\n"
        "  `resumo do mês`\n"
        "  `resumo de julho`\n"
        "  `por categoria`\n"
        "  `últimos 5`\n\n"
        "*Apagar:*\n"
        "  `apagar último`\n"
        "  `apagar 5`",
        parse_mode="Markdown",
        reply_markup=teclado_principal()
    )

async def processar_mensagem(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_autorizado(update):
        return

    texto = update.message.text.strip()
    texto_lower = texto.lower()

    # Botões fixos
    if "📊 resumo do mês" in texto_lower:
        await resumo_mes(update)
        return
    if "🗂 por categoria" in texto_lower:
        await resumo_categoria(update)
        return
    if "📋 últimos lançamentos" in texto_lower:
        await ultimos_lancamentos(update)
        return
    if "❓ ajuda" in texto_lower:
        await ajuda(update, context)
        return
    if "💸 registrar gasto" in texto_lower:
        context.user_data["aguardando_tipo"] = "gasto"
        await update.message.reply_text("Digite o gasto:\nEx: `150 gasolina ontem, pix inter`", parse_mode="Markdown")
        return
    if "💰 registrar receita" in texto_lower:
        context.user_data["aguardando_tipo"] = "receita"
        await update.message.reply_text("Digite a receita:\nEx: `650 Cecília hoje`", parse_mode="Markdown")
        return

    # Se aguardando após botão, prefixar
    if "aguardando_tipo" in context.user_data:
        tipo = context.user_data.pop("aguardando_tipo")
        texto = f"{'gastei' if tipo == 'gasto' else 'recebi'} {texto}"

    # Envia para Claude interpretar
    await update.message.reply_chat_action("typing")
    resultado = await interpretar_com_claude(texto)

    if "erro" in resultado:
        await update.message.reply_text(
            "⚠️ Não entendi. Tenta ser mais específico:\n`gastei 150 gasolina`\n`recebi 650 Cecília`",
            parse_mode="Markdown"
        )
        return

    # Executar comando se houver
    cmd = resultado.get("comando")
    param = resultado.get("comando_param")

    if cmd == "resumo_mes":
        mes_num = MESES_MAP.get(str(param).lower()) if param else None
        await resumo_mes(update, mes=mes_num)
        return
    if cmd == "resumo_categoria":
        await resumo_categoria(update)
        return
    if cmd == "ultimos":
        await ultimos_lancamentos(update, n=int(param) if param else 5)
        return
    if cmd == "apagar_ultimo":
        await apagar_lancamento(update, "ultimo")
        return
    if cmd == "apagar_id":
        await apagar_lancamento(update, int(param))
        return

    # Registrar lançamentos
    lancamentos = resultado.get("lancamentos", [])
    if lancamentos:
        await registrar_lancamentos(update, lancamentos)
    else:
        await update.message.reply_text(
            "⚠️ Não entendi. Tenta:\n`gastei 150 gasolina`\n`recebi 650 Cecília`",
            parse_mode="Markdown"
        )

def main():
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("ajuda", ajuda))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, processar_mensagem))
    logger.info("Bot com IA iniciado...")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
