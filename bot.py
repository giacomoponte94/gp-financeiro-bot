import os
import re
import logging
from datetime import datetime, date
from telegram import Update, ReplyKeyboardMarkup, KeyboardButton
from telegram.ext import (
    Application, CommandHandler, MessageHandler,
    filters, ContextTypes
)
from supabase import create_client, Client

# --- Config ---
BOT_TOKEN = os.environ.get("BOT_TOKEN")
SUPABASE_URL = os.environ.get("SUPABASE_URL", "https://ggzjrzbsjzhswzffkjfq.supabase.co")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")
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

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_autorizado(update):
        await update.message.reply_text("❌ Acesso não autorizado.")
        return
    await update.message.reply_text(
        "👋 Olá, Giácomo! Sou seu assistente financeiro.\n\n"
        "Você pode usar os botões ou digitar naturalmente:\n\n"
        "  _Gastei 100 de gasolina_\n"
        "  _Recebi 650 do Antonio_\n"
        "  _Resumo do mês_\n"
        "  _Últimos lançamentos_\n"
        "  _Apagar último_\n"
        "  _Apagar 5_ (pelo ID)",
        parse_mode="Markdown",
        reply_markup=teclado_principal()
    )

async def ajuda(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_autorizado(update):
        return
    await update.message.reply_text(
        "📖 *Como usar:*\n\n"
        "*Registrar gasto:*\n"
        "  `gastei 150 gasolina`\n"
        "  `paguei 808 financiamento`\n"
        "  `saiu 1993 xp`\n\n"
        "*Registrar receita:*\n"
        "  `recebi 650 Antonio`\n"
        "  `entrou 900 Cecilia`\n\n"
        "*Consultar:*\n"
        "  `resumo do mês`\n"
        "  `resumo de julho`\n"
        "  `por categoria`\n"
        "  `últimos lançamentos`\n\n"
        "*Apagar/Editar:*\n"
        "  `apagar último`\n"
        "  `apagar 5` (pelo ID)\n"
        "  `últimos 5` (ver IDs)\n\n"
        "*Categorias de gasto:*\n"
        "  Alimentação, Gasolina, Financiamento,\n"
        "  Consórcio, XP, Santander, Nubank,\n"
        "  Internet, Vivo, MEI, Seguro, Prudential, Outros",
        parse_mode="Markdown",
        reply_markup=teclado_principal()
    )

def extrair_lancamento(texto: str):
    texto_orig = texto
    texto = texto.lower().strip()

    tipo = None
    if any(p in texto for p in ["gastei", "paguei", "saiu", "gasto", "pago"]):
        tipo = "gasto"
    elif any(p in texto for p in ["recebi", "entrou", "recebei", "entrada", "receita"]):
        tipo = "receita"

    if not tipo:
        return None

    valor_match = re.search(r"(\d+(?:[.,]\d{1,2})?)", texto)
    if not valor_match:
        return None
    valor = float(valor_match.group(1).replace(",", "."))

    categoria = "Outros"

    if tipo == "gasto":
        mapa = {
            "Alimentação": ["alimenta", "mercado", "comida", "lanche", "restaurante", "ifood"],
            "Gasolina": ["gasolina", "combustivel", "combustível", "posto"],
            "Financiamento": ["financiamento"],
            "Consórcio": ["consorcio", "consórcio"],
            "XP": [" xp", "xp "],
            "Santander": ["santander"],
            "Nubank": ["nubank", " nu "],
            "Internet": ["internet", "wifi"],
            "Vivo": ["vivo"],
            "MEI": ["mei"],
            "Seguro": ["seguro"],
            "Prudential": ["prudential"],
        }
        for cat, palavras in mapa.items():
            if any(p in f" {texto} " for p in palavras):
                categoria = cat
                break
    else:
        categoria = "Aluno"
        if "outros" in texto:
            categoria = "Outros"

    return {"tipo": tipo, "valor": valor, "categoria": categoria, "descricao": texto_orig.strip()}

async def registrar(update: Update, context: ContextTypes.DEFAULT_TYPE, dados=None):
    if not dados:
        return False
    try:
        result = supabase.table("financeiro").insert({
            "tipo": dados["tipo"],
            "valor": dados["valor"],
            "categoria": dados["categoria"],
            "descricao": dados["descricao"],
            "data": date.today().isoformat()
        }).execute()

        id_registrado = result.data[0]["id"] if result.data else "?"
        emoji = "💸" if dados["tipo"] == "gasto" else "💰"
        sinal = "-" if dados["tipo"] == "gasto" else "+"
        await update.message.reply_text(
            f"{emoji} *{dados['tipo'].capitalize()} registrado!*\n"
            f"ID: `{id_registrado}`\n"
            f"Valor: *R$ {sinal}{dados['valor']:.2f}*\n"
            f"Categoria: {dados['categoria']}\n"
            f"Data: {date.today().strftime('%d/%m/%Y')}\n\n"
            f"_Para apagar: `apagar {id_registrado}`_",
            parse_mode="Markdown",
            reply_markup=teclado_principal()
        )
        return True
    except Exception as e:
        logger.error(f"Erro ao salvar: {e}")
        await update.message.reply_text("❌ Erro ao salvar. Tenta de novo.")
        return False

async def ultimos_lancamentos(update: Update, context: ContextTypes.DEFAULT_TYPE, n=5):
    if not is_autorizado(update):
        return
    try:
        result = supabase.table("financeiro")\
            .select("id, tipo, valor, categoria, descricao, data")\
            .order("id", desc=True)\
            .limit(n)\
            .execute()

        dados = result.data
        if not dados:
            await update.message.reply_text("📭 Nenhum lançamento encontrado.", reply_markup=teclado_principal())
            return

        texto = f"📋 *Últimos {len(dados)} lançamentos:*\n\n"
        for d in dados:
            emoji = "💸" if d["tipo"] == "gasto" else "💰"
            sinal = "-" if d["tipo"] == "gasto" else "+"
            texto += f"{emoji} `ID {d['id']}` — *R$ {sinal}{float(d['valor']):.2f}*\n"
            texto += f"  {d['categoria']} | {d['data']}\n"
            texto += f"  _{d['descricao'][:40]}_\n\n"

        texto += "_Para apagar: `apagar <ID>`_"
        await update.message.reply_text(texto, parse_mode="Markdown", reply_markup=teclado_principal())
    except Exception as e:
        logger.error(f"Erro últimos: {e}")

async def apagar_lancamento(update: Update, context: ContextTypes.DEFAULT_TYPE, id_alvo=None):
    if not is_autorizado(update):
        return
    try:
        if id_alvo == "ultimo":
            result = supabase.table("financeiro")\
                .select("id, tipo, valor, categoria")\
                .order("id", desc=True)\
                .limit(1)\
                .execute()
            if not result.data:
                await update.message.reply_text("📭 Nenhum lançamento para apagar.")
                return
            id_alvo = result.data[0]["id"]
            item = result.data[0]
        else:
            result = supabase.table("financeiro")\
                .select("id, tipo, valor, categoria")\
                .eq("id", id_alvo)\
                .execute()
            if not result.data:
                await update.message.reply_text(f"❌ ID {id_alvo} não encontrado.")
                return
            item = result.data[0]

        supabase.table("financeiro").delete().eq("id", id_alvo).execute()

        emoji = "💸" if item["tipo"] == "gasto" else "💰"
        await update.message.reply_text(
            f"🗑 Apagado!\n"
            f"{emoji} ID `{id_alvo}` — R$ {float(item['valor']):.2f} ({item['categoria']})",
            parse_mode="Markdown",
            reply_markup=teclado_principal()
        )
    except Exception as e:
        logger.error(f"Erro apagar: {e}")
        await update.message.reply_text("❌ Erro ao apagar.")

async def resumo_mes(update: Update, context: ContextTypes.DEFAULT_TYPE, mes=None, ano=None):
    if not is_autorizado(update):
        return

    hoje = date.today()
    if not mes:
        mes = hoje.month
    if not ano:
        ano = hoje.year

    inicio = f"{ano}-{mes:02d}-01"
    fim = f"{ano+1}-01-01" if mes == 12 else f"{ano}-{mes+1:02d}-01"

    try:
        result = supabase.table("financeiro")\
            .select("tipo, valor, categoria")\
            .gte("data", inicio)\
            .lt("data", fim)\
            .execute()

        dados = result.data
        if not dados:
            nome_mes = ["Jan","Fev","Mar","Abr","Mai","Jun","Jul","Ago","Set","Out","Nov","Dez"][mes-1]
            await update.message.reply_text(
                f"📭 Nenhum lançamento em {nome_mes}/{ano}.",
                reply_markup=teclado_principal()
            )
            return

        total_receita = sum(float(d["valor"]) for d in dados if d["tipo"] == "receita")
        total_gasto = sum(float(d["valor"]) for d in dados if d["tipo"] == "gasto")
        saldo = total_receita - total_gasto

        categorias = {}
        for d in dados:
            if d["tipo"] == "gasto":
                cat = d["categoria"]
                categorias[cat] = categorias.get(cat, 0) + float(d["valor"])

        cats_texto = "\n".join(
            f"  • {cat}: R$ {val:.2f}"
            for cat, val in sorted(categorias.items(), key=lambda x: -x[1])
        )

        saldo_emoji = "✅" if saldo >= 0 else "🔴"
        nome_mes = ["Jan","Fev","Mar","Abr","Mai","Jun","Jul","Ago","Set","Out","Nov","Dez"][mes-1]

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

async def resumo_categoria(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_autorizado(update):
        return

    hoje = date.today()
    inicio = f"{hoje.year}-{hoje.month:02d}-01"

    try:
        result = supabase.table("financeiro")\
            .select("tipo, valor, categoria")\
            .gte("data", inicio)\
            .execute()

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

async def processar_mensagem(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_autorizado(update):
        return

    texto_orig = update.message.text
    texto = texto_orig.lower().strip()

    # Botões
    if "💸 registrar gasto" in texto:
        context.user_data["aguardando_tipo"] = "gasto"
        await update.message.reply_text("Digite o gasto:\nEx: `150 gasolina`", parse_mode="Markdown")
        return

    if "💰 registrar receita" in texto:
        context.user_data["aguardando_tipo"] = "receita"
        await update.message.reply_text("Digite a receita:\nEx: `650 Antonio`", parse_mode="Markdown")
        return

    if "📊 resumo do mês" in texto or texto == "resumo do mês":
        await resumo_mes(update, context)
        return

    if "🗂 por categoria" in texto or texto == "por categoria":
        await resumo_categoria(update, context)
        return

    if "📋 últimos lançamentos" in texto or "ultimos lancamentos" in texto or texto == "últimos lançamentos":
        await ultimos_lancamentos(update, context)
        return

    if "❓ ajuda" in texto or texto == "ajuda":
        await ajuda(update, context)
        return

    # Apagar
    if "apagar último" in texto or "apagar ultimo" in texto:
        await apagar_lancamento(update, context, "ultimo")
        return

    apagar_match = re.match(r"apagar\s+(\d+)", texto)
    if apagar_match:
        await apagar_lancamento(update, context, int(apagar_match.group(1)))
        return

    # Últimos N
    ultimos_match = re.match(r"[uú]ltimos?\s+(\d+)", texto)
    if ultimos_match:
        await ultimos_lancamentos(update, context, int(ultimos_match.group(1)))
        return

    # Resumo mês específico
    for nome_mes, num_mes in MESES_MAP.items():
        if nome_mes in texto and ("resumo" in texto or "quanto" in texto):
            await resumo_mes(update, context, mes=num_mes, ano=date.today().year)
            return

    # Consultas genéricas
    if any(p in texto for p in ["quanto gastei", "quanto recebi", "resumo", "saldo"]):
        await resumo_mes(update, context)
        return

    # Aguardando após botão
    if "aguardando_tipo" in context.user_data:
        tipo = context.user_data.pop("aguardando_tipo")
        dados = extrair_lancamento(f"{'gastei' if tipo == 'gasto' else 'recebi'} {texto_orig}")
        if dados:
            await registrar(update, context, dados)
        else:
            await update.message.reply_text("⚠️ Não entendi o valor. Tenta: `150 gasolina`", parse_mode="Markdown")
        return

    # Lançamento direto
    dados = extrair_lancamento(texto_orig)
    if dados:
        await registrar(update, context, dados)
    elif any(p in texto for p in ["gastei", "paguei", "recebi", "entrou", "saiu"]):
        await update.message.reply_text(
            "⚠️ Não entendi. Tenta:\n`gastei 150 gasolina`\n`recebi 650 Antonio`",
            parse_mode="Markdown"
        )

def main():
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("ajuda", ajuda))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, processar_mensagem))
    logger.info("Bot iniciado...")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
