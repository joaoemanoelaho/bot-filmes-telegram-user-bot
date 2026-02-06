from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from handlers.common import DB_SEMAPHORE, safe_call, _get_vip_sales_message
import database as db
import tmdb_api 

async def request_start_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Callback para o botão 'main_request'."""
    query = update.callback_query
    user_id = query.from_user.id
    
    async with DB_SEMAPHORE:
        # 1. Verifica VIP
        await db.get_or_create_user(user_id=user_id, first_name=query.from_user.first_name)
        if not await db.is_user_vip(user_id):
            config = await db.get_bot_config()
            price = config.get('vip_price', 4.99)
            if price > 0:
                # --- CORREÇÃO: Botão Direto para o PIX + HTML ---
                sales_text, _ = await _get_vip_sales_message(context)
                
                keyboard = [
                    [InlineKeyboardButton("✅ Sim, Gerar PIX para Pagar!", callback_data="confirm_pay")],
                    [InlineKeyboardButton("⬅️ Voltar ao Menu", callback_data="back_to_main")]
                ]
                
                await safe_call(query, "edit_message_text", 
                    text=f"Opa! 👋\n\n{sales_text}", 
                    parse_mode="HTML", 
                    reply_markup=InlineKeyboardMarkup(keyboard)
                )
                return

        # 2. Inicia o fluxo
        await safe_call(query, "answer")
        context.user_data['state'] = 'awaiting_tmdb_id'
        
        texto_instrucao = (
            "💡 **Faça seu Pedido**\n\n"
            "Para evitar erros e duplicatas, aceitamos apenas o **Código TMDB**.\n\n"
            "1️⃣ Vá no site [themoviedb.org](https://www.themoviedb.org/)\n"
            "2️⃣ Copie o número da URL do filme/série.\n"
            "3️⃣ **Envie APENAS esse número aqui.**\n\n"
            "❌ _Não envie nomes, envie apenas números._\n"
            "Digite /cancelar para sair."
        )
        
        await safe_call(query, "edit_message_text", text=texto_instrucao, parse_mode="Markdown", disable_web_page_preview=True)

async def request_command_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Comando /pedir."""
    context.user_data['state'] = 'awaiting_tmdb_id'
    await update.message.reply_text(
        "Envie o **Código TMDB** (número) do conteúdo.\n"
        "Ex: `550` (para Clube da Luta).\n\n"
        "Digite /cancelar para sair."
    , parse_mode="Markdown")

async def process_tmdb_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Lógica blindada de pedidos."""
    user_text = update.message.text.strip()
    
    # [1] Validação: É número?
    if not user_text.isdigit():
        await update.message.reply_text(
            "⚠️ **Erro:** Você enviou texto.\n\n"
            "Eu preciso do **Código Numérico** do TMDB.\n"
            "Exemplo: `12345`"
        )
        return

    msg_wait = await update.message.reply_text("🔍 Verificando código...")
    
    # [2] Busca na API do TMDB
    tmdb_data = await tmdb_api.check_tmdb_id(user_text)
    
    if not tmdb_data:
        await msg_wait.edit_text(
            "❌ **Código não encontrado!**\n"
            "Esse ID não existe no TMDB. Verifique no site e tente novamente."
        )
        return

    # [3] VERIFICAÇÃO DE CATÁLOGO (NOVA) 🛡️
    # Verifica se esse filme/série JÁ EXISTE na tabela movies ou series
    exists_in_catalog = await db.check_content_exists_by_tmdb_id(tmdb_data['tmdb_id'], tmdb_data['type'])
    
    if exists_in_catalog:
        title_found = exists_in_catalog.get('title', tmdb_data['title'])
        await msg_wait.edit_text(
            f"✅ **Já temos esse conteúdo!** 🍿\n\n"
            f"O título **'{title_found}'** já está disponível no catálogo.\n\n"
            "🔎 __Use o botão de Busca no menu para assistir agora mesmo!__"
        )
        # Limpa o estado para ele não ficar preso no loop de pedir
        if 'state' in context.user_data:
            del context.user_data['state']
        return

    # [4] VERIFICAÇÃO DE PEDIDOS PENDENTES (Anti-Spam)
    try:
        pending_requests = await db.get_pending_requests()
        if pending_requests:
            # Procura a tag [ID: 12345] nos títulos pendentes
            id_tag = f"[ID: {tmdb_data['tmdb_id']}]"
            
            for req in pending_requests:
                if id_tag in req['requested_title']:
                    await msg_wait.edit_text(
                        f"⚠️ **Pedido já realizado!**\n\n"
                        f"O título **{tmdb_data['title']}** já foi solicitado por outro usuário e está na fila de análise.\n\n"
                        "Não precisa pedir de novo, é só aguardar! 😉"
                    )
                    if 'state' in context.user_data:
                        del context.user_data['state']
                    return
    except Exception as e:
        print(f"Erro ao verificar duplicidade pendente: {e}")

    # [5] Sucesso: Salva no Banco
    tipo_midia = "Filme" if tmdb_data['type'] == 'movie' else "Série"
    title_final = f"[{tipo_midia}] {tmdb_data['title']} ({tmdb_data['year']}) [ID: {tmdb_data['tmdb_id']}]"
    
    if await db.add_request(update.effective_user.id, title_final):
        if 'state' in context.user_data:
            del context.user_data['state']
        
        await msg_wait.edit_text(
            f"✅ **Pedido Registrado!**\n\n"
            f"🎬 **Título:** {tmdb_data['title']}\n"
            f"📅 **Ano:** {tmdb_data['year']}\n"
            f"🆔 **ID:** {tmdb_data['tmdb_id']}\n\n"
            "Sua solicitação foi enviada para a equipe e será analisada em breve!"
        )
    else:
        await msg_wait.edit_text("😕 Erro ao salvar o pedido. Tente novamente.")
    