# GP Financeiro Bot

Bot do Telegram para controle de gastos e receitas, integrado ao Supabase.

## Variáveis de ambiente (configurar no Railway)

| Variável | Valor |
|---|---|
| `BOT_TOKEN` | Token do BotFather |
| `SUPABASE_URL` | https://qxiybqizslwwfsersbjg.supabase.co |
| `SUPABASE_KEY` | chave anon do Supabase |
| `ALLOWED_USER_ID` | seu chat_id do Telegram |

## Como encontrar seu ALLOWED_USER_ID

1. Inicia o bot
2. Manda qualquer mensagem
3. Acessa: https://api.telegram.org/bot<TOKEN>/getUpdates
4. Pega o campo `message.from.id`

## Deploy no Railway

1. Sobe esse repositório no GitHub
2. Entra em railway.app → New Project → Deploy from GitHub
3. Seleciona o repositório
4. Vai em Variables e adiciona as 4 variáveis acima
5. Railway detecta o Procfile e sobe automaticamente

## Comandos do bot

- `gastei 150 de gasolina` → registra gasto
- `recebi 650 do Antonio` → registra receita
- `resumo do mês` → resumo de junho
- `resumo de julho` → resumo de mês específico
- `por categoria` → gastos agrupados
