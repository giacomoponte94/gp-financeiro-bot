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
ALLOWED_USER = os.environ.get("ALLOWED_USER_ID")  # seu chat_id do Telegram

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

CATEGORIAS_GASTO = [
    "Alimentação", "Gasolina", "Financiamento", "Consórcio",
    "XP", "Santander", "Nubank", "Internet", "Vivo", "MEI",
    "Seguro", "Prudential", "Outros"
]

CATEGORIAS_RECEITA = [
    "Aluno", "Outros"
]

def teclado_principal():
    return ReplyKeyboardMarkup([
        [KeyboardButton("💸 Registrar Gasto"), KeyboardButton("💰 Registrar Receita")],
        [KeyboardButton("📊 Resumo do Mês"), KeyboardButton("📅 Resumo por Período")],
        [KeyboardButton("🗂 Por Categoria"), KeyboardButton("❓ Ajuda")]
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
        "Você pode:\n"
        "• Usar os botões abaixo\n"
        "• Ou digitar naturalmente:\n\n"
        "  _Gastei 100 de gasolina_\n"
        "  _Recebi 650 do Antonio_\n"
        "  _Quanto gastei esse mês?_\n"
        "  _Resumo de junho_",
        parse_mode="Markdown",
        reply_markup=teclado_principal()
    )

async def ajuda(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "📖 *Como usar:*\n\n"
        "*Registrar gasto:*\n"
        "  `gastei 150 de alimentação`\n"
        "  `paguei 808 financiamento`\n"
        "  `saiu 300 seguro`\n\n"
        "*Registrar receita:*\n"
        "  `recebi 650 do Antonio`\n"
        "  `entrou 900 Cecilia`\n\n"
        "*Consultar:*\n"
        "  `resumo do mês`\n"
        "  `resumo de junho`\n"
        "  `quanto gastei esse mês`\n"
        "  `por categoria`\n\n"
        "*Categorias disponíveis:*\n"
        f"  {', '.join(CATEGORIAS_GASTO)}",
        parse_mode="Markdown",
        reply_markup=teclado_principal()
    )

def extrair_lancamento(texto: str):
    """Extrai tipo, valor, categoria e descrição do texto livre."""
    texto = texto.lower().strip()

    # Detectar tipo
    tipo = None
    if any(p in texto for p in ["gastei", "paguei", "saiu", "gasto", "pago"]):
        tipo = "gasto"
    elif any(p in texto for p in ["recebi", "entrou", "recebei", "entrada", "receita"]):
        tipo = "receita"

    if not tipo:
        return None

    # Extrair valor
    valor_match = re.search(r"(\d+(?:[.,]\d{1,2})?)", texto)
    if not valor_match:
        return None
    valor = float(valor_match.group(1).replace(",", "."))

    # Detectar categoria
    categoria = "Outros"
    texto_lower = texto.lower()

    if tipo == "gasto":
        mapa = {
            "alimentação": ["alimenta", "mercado", "comida", "lanche", "restaurante"],
            "gasolina": ["gasolina", "combustivel", "combustível", "posto"],
            "financiamento": ["financiamento", "carro", "banco"],
            "consórcio": ["consorcio", "consórcio"],
            "xp": ["xp"],
            "santander": ["santander"],
            "nubank": ["nubank", "nu"],
            "internet": ["internet", "wifi"],
            "vivo": ["vivo"],
            "mei": ["mei"],
            "seguro": ["seguro"],
            "prudential": ["prudential"],
        }
        for cat, palavras in mapa.items():
            if any(p in texto_lower for p in palavras):
                categoria = cat.capitalize()
                break
    else:
        categoria = "Aluno"
        if "outros" in texto_lower:
            categoria = "Outros"

    # Descrição = texto original resumido
    descricao = texto.strip()

    return {"tipo": tipo, "valor": valor, "categoria": categoria, "descricao": descricao}

async def registrar_lancamento(update: Update, context: ContextTypes.DEFAULT_TYPE, tipo_forcado=None):
    if not is_autorizado(update):
        return

    texto = update.message.text

    # Se veio de botão, pedir input
    if texto in ["💸 Registrar Gasto", "💰 Registrar Receita"]:
        tipo = "gasto" if "Gasto" in texto else "receita"
        context.user_data["aguardando_tipo"] = tipo
        await update.message.reply_text(
            f"Digite o valor e descrição:\n"
            f"Ex: _{'150 de gasolina' if tipo == 'gasto' else '650 do Antonio'}_",
            parse_mode="Markdown"
        )
        return

    # Se estava aguardando após botão
    if "aguardando_tipo" in context.user_data:
        tipo = context.user_data.pop("aguardando_tipo")
        dados = extrair_lancamento(f"{'gastei' if tipo == 'gasto' else 'recebi'} {texto}")
    else:
        dados = extrair_lancamento(texto)

    if not dados:
        return False

    # Salvar no Supabase
    try:
        supabase.table("financeiro").insert({
            "tipo": dados["tipo"],
            "valor": dados["valor"],
            "categoria": dados["categoria"],
            "descricao": dados["descricao"],
            "data": date.today().isoformat()
        }).execute()

        emoji = "💸" if dados["tipo"] == "gasto" else "💰"
        sinal = "-" if dados["tipo"] == "gasto" else "+"
        await update.message.reply_text(
            f"{emoji} *{dados['tipo'].capitalize()} registrado!*\n"
            f"Valor: *R$ {sinal}{dados['valor']:.2f}*\n"
            f"Categoria: {dados['categoria']}\n"
            f"Data: {date.today().strftime('%d/%m/%Y')}",
            parse_mode="Markdown",
            reply_markup=teclado_principal()
        )
        return True
    except Exception as e:
        logger.error(f"Erro ao salvar: {e}")
        await update.message.reply_text("❌ Erro ao salvar. Tenta de novo.")
        return False

async def resumo_mes(update: Update, context: ContextTypes.DEFAULT_TYPE, mes=None, ano=None):
    if not is_autorizado(update):
        return

    hoje = date.today()
    if not mes:
        mes = hoje.month
    if not ano:
        ano = hoje.year

    inicio = f"{ano}-{mes:02d}-01"
    if mes == 12:
        fim = f"{ano+1}-01-01"
    else:
        fim = f"{ano}-{mes+1:02d}-01"

    try:
        result = supabase.table("financeiro")\
            .select("tipo, valor, categoria")\
            .gte("data", inicio)\
            .lt("data", fim)\
            .execute()

        dados = result.data
        if not dados:
            await update.message.reply_text(
                f"📭 Nenhum lançamento em {mes:02d}/{ano}.",
                reply_markup=teclado_principal()
            )
            return

        total_receita = sum(d["valor"] for d in dados if d["tipo"] == "receita")
        total_gasto = sum(d["valor"] for d in dados if d["tipo"] == "gasto")
        saldo = total_receita - total_gasto

        # Por categoria
        categorias = {}
        for d in dados:
            if d["tipo"] == "gasto":
                cat = d["categoria"]
                categorias[cat] = categorias.get(cat, 0) + d["valor"]

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
        await update.message.reply_text("❌ Erro ao buscar dados.")

async def resumo_categoria(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_autorizado(update):
        return

    hoje = date.today()
    inicio = f"{hoje.year}-{hoje.month:02d}-01"

    try:
        result = supabase.table("financeiro")\
            .select("tipo, valor, categoria, descricao, data")\
            .gte("data", inicio)\
            .order("categoria")\
            .execute()

        dados = result.data
        if not dados:
            await update.message.reply_text("📭 Nenhum lançamento este mês.")
            return

        categorias = {}
        for d in dados:
            cat = d["categoria"]
            if cat not in categorias:
                categorias[cat] = {"total": 0, "tipo": d["tipo"]}
            categorias[cat]["total"] += d["valor"]

        texto = f"🗂 *Por categoria — {hoje.strftime('%m/%Y')}*\n\n"
        for cat, info in sorted(categorias.items()):
            emoji = "💸" if info["tipo"] == "gasto" else "💰"
            texto += f"{emoji} *{cat}*: R$ {info['total']:.2f}\n"

        await update.message.reply_text(texto, parse_mode="Markdown", reply_markup=teclado_principal())
    except Exception as e:
        logger.error(f"Erro categoria: {e}")

MESES_MAP = {
    "janeiro": 1, "fevereiro": 2, "março": 3, "marco": 3,
    "abril": 4, "maio": 5, "junho": 6, "julho": 7,
    "agosto": 8, "setembro": 9, "outubro": 10,
    "novembro": 11, "dezembro": 12
}

async def processar_mensagem(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_autorizado(update):
        return

    texto = update.message.text.lower().strip()

    # Botões do teclado
    if "resumo do mês" in texto or "resumo desse mês" in texto or "📊 resumo do mês" in texto:
        await resumo_mes(update, context)
        return

    if "por categoria" in texto or "🗂" in texto:
        await resumo_categoria(update, context)
        return

    if "ajuda" in texto or "❓" in texto:
        await ajuda(update, context)
        return

    # Resumo de mês específico
    for nome_mes, num_mes in MESES_MAP.items():
        if nome_mes in texto and ("resumo" in texto or "quanto" in texto):
            ano = date.today().year
            await resumo_mes(update, context, mes=num_mes, ano=ano)
            return

    # Consultas genéricas
    if any(p in texto for p in ["quanto gastei", "quanto recebi", "resumo", "saldo"]):
        await resumo_mes(update, context)
        return

    # Tentativa de lançamento
    registrado = await registrar_lancamento(update, context)
    if not registrado:
        # Se não entendeu, orienta
        if any(p in texto for p in ["gastei", "paguei", "recebi", "entrou", "saiu"]):
            await update.message.reply_text(
                "⚠️ Não consegui entender. Tenta assim:\n"
                "`gastei 150 gasolina`\n"
                "`recebi 650 Antonio`",
                parse_mode="Markdown"
            )

def main():
    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("ajuda", ajuda))
    app.add_handler(CommandHandler("resumo", resumo_mes))
    app.add_handler(MessageHandler(
        filters.TEXT & ~filters.COMMAND,
        processar_mensagem
    ))

    logger.info("Bot iniciado...")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
