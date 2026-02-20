from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from handlers.common import DB_SEMAPHORE, safe_call, _get_vip_sales_message
import re
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
    """Lógica blindada de pedidos (Agora aceita Link e ID)."""
    user_text = update.message.text.strip()
    
    # [1] EXTRAÇÃO INTELIGENTE (Pega do Link ou usa o Número)
    tmdb_id = None
    media_type = None # 'movie' ou 'tv'

    # Verifica se o usuário mandou um link do TMDB
    if "themoviedb.org" in user_text:
        # Tenta achar /movie/1234 ou /tv/1234 no link
        match = re.search(r'/(movie|tv)/(\d+)', user_text)
        if match:
            media_type = match.group(1) # Pega 'movie' ou 'tv'
            tmdb_id = match.group(2)    # Pega o número
    elif user_text.isdigit():
        # Se ele mandou só o número, a gente aceita, mas avisa que pode dar conflito
        tmdb_id = user_text
    
    if not tmdb_id:
        await update.message.reply_text(
            "⚠️ <b>Erro:</b> Formato inválido.\n\n"
            "Por favor, envie o <b>Link Completo</b> do filme/série no TMDB.\n"
            "Exemplo: <code>https://www.themoviedb.org/tv/1413-american-horror-story</code>",
            parse_mode="HTML"
        )
        return

    msg_wait = await update.message.reply_text("🔍 Verificando conteúdo...")
    
    # [2] Busca na API do TMDB
    # Você vai precisar adaptar a sua função lá no tmdb_api.py para receber o media_type
    # Ex: await tmdb_api.check_tmdb_id(tmdb_id, media_type)
    tmdb_data = await tmdb_api.check_tmdb_id(tmdb_id, media_type)
    
    if not tmdb_data:
        await msg_wait.edit_text(
            "❌ <b>Conteúdo não encontrado!</b>\n"
            "Verifique o link no site do TMDB e tente novamente.",
            parse_mode="HTML"
        )
        return

    # [3] VERIFICAÇÃO DE CATÁLOGO (Mantém igual) 🛡️
    exists_in_catalog = await db.check_content_exists_by_tmdb_id(tmdb_data['tmdb_id'], tmdb_data['type'])
    
    if exists_in_catalog:
        title_found = exists_in_catalog.get('title', tmdb_data['title'])
        await msg_wait.edit_text(
            f"✅ <b>Já temos esse conteúdo!</b> 🍿\n\n"
            f"O título <b>'{title_found}'</b> já está disponível no catálogo.\n\n"
            "🔎 <i>Use o botão de Busca no menu para assistir agora mesmo!</i>",
            parse_mode="HTML"
        )
        if 'state' in context.user_data: del context.user_data['state']
        return

    # [4] VERIFICAÇÃO DE PEDIDOS PENDENTES (Mantém igual)
    try:
        pending_requests = await db.get_pending_requests()
        if pending_requests:
            id_tag = f"[ID: {tmdb_data['tmdb_id']}]"
            for req in pending_requests:
                if id_tag in req['requested_title']:
                    await msg_wait.edit_text(
                        f"⚠️ <b>Pedido já realizado!</b>\n\n"
                        f"O título <b>{tmdb_data['title']}</b> já foi solicitado e está na fila.\n"
                        "Não precisa pedir de novo, é só aguardar! 😉",
                        parse_mode="HTML"
                    )
                    if 'state' in context.user_data: del context.user_data['state']
                    return
    except Exception as e:
        print(f"Erro ao verificar duplicidade pendente: {e}")

    # [5] Sucesso: Salva no Banco (Mantém igual)
    tipo_midia = "Filme" if tmdb_data['type'] == 'movie' else "Série"
    title_final = f"[{tipo_midia}] {tmdb_data['title']} ({tmdb_data['year']}) [ID: {tmdb_data['tmdb_id']}]"
    
    if await db.add_request(update.effective_user.id, title_final):
        if 'state' in context.user_data: del context.user_data['state']
        
        await msg_wait.edit_text(
            f"✅ <b>Pedido Registrado!</b>\n\n"
            f"🎬 <b>Título:</b> {tmdb_data['title']}\n"
            f"📺 <b>Tipo:</b> {tipo_midia}\n"
            f"📅 <b>Ano:</b> {tmdb_data['year']}\n"
            f"🆔 <b>ID:</b> {tmdb_data['tmdb_id']}\n\n"
            "Sua solicitação foi enviada para a equipe e será analisada em breve!",
            parse_mode="HTML"
        )
    else:
        await msg_wait.edit_text("😕 Erro ao salvar o pedido. Tente novamente.", parse_mode="HTML")
        