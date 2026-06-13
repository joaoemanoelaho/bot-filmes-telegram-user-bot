from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from handlers.common import DB_SEMAPHORE, safe_call, _get_vip_sales_message
import re
import database as db
import tmdb_api 
import asyncio

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
            "💡 <b>Faça seu Pedido</b>\n\n"
            "Para garantir que vamos achar o conteúdo exato, precisamos do <b>Link Completo</b> do TMDB.\n\n"
            "1️⃣ Vá no site <a href='https://www.themoviedb.org/'>themoviedb.org</a>\n"
            "2️⃣ Pesquise o filme ou série desejada.\n"
            "3️⃣ Copie o <b>link (URL) inteiro</b> da página e envie aqui.\n\n"
            "✅ <i>Exemplo:</i> <code>https://www.themoviedb.org/tv/1413-american-horror-story</code>\n\n"
            "❌ <i>Não envie o nome do filme, apenas o link!</i>\n"
            "Digite /cancelar para sair."
        )
        
        await safe_call(query, "edit_message_text", text=texto_instrucao, parse_mode="HTML", disable_web_page_preview=True)

async def request_command_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Comando /pedir."""
    context.user_data['state'] = 'awaiting_tmdb_id'
    await update.message.reply_text(
        "Envie o <b>Link Completo</b> do conteúdo no TMDB.\n\n"
        "✅ <i>Exemplo:</i>\n<code>https://www.themoviedb.org/movie/550-fight-club</code>\n\n"
        "Digite /cancelar para sair."
    , parse_mode="HTML", disable_web_page_preview=True)

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
        # Verifica se o conteúdo está marcado como completo.
        # Se a chave 'is_complete' não existir, assumimos 'True' por padrão para não quebrar os filmes antigos.
        esta_completo = exists_in_catalog.get('is_complete', True)
        
        if esta_completo:
            title_found = exists_in_catalog.get('title', tmdb_data['title'])
            await msg_wait.edit_text(
                f"✅ <b>Já temos esse conteúdo!</b> 🍿\n\n"
                f"O título <b>'{title_found}'</b> já está 100% completo no catálogo.\n\n"
                "🔎 <i>Use o botão de Busca no menu para assistir agora mesmo!</i>",
                parse_mode="HTML"
            )
            if 'state' in context.user_data: del context.user_data['state']
            return
        else:
            # Se NÃO estiver completo (is_complete = False), o bot avisa e CONTINUA o fluxo!
            await msg_wait.edit_text(
                f"⚠️ <b>Atualização Necessária!</b>\n\n"
                f"O título <b>'{tmdb_data['title']}'</b> já está no catálogo, mas consta como <b>INCOMPLETO</b>.\n"
                "🔄 Registrando seu pedido de atualização...",
                parse_mode="HTML"
            )
            # Uma pequena pausa para o usuário ler a mensagem antes do bot processar as Etapas 4 e 5
            await asyncio.sleep(2.5)

    # [4] VERIFICAÇÃO DE PEDIDOS PENDENTES E EM PROCESSAMENTO 🛡️
    try:
        # A MÁGICA AQUI: Puxa direto do banco pedidos 'pending' OU 'processing'
        response = await asyncio.to_thread(
            db.supabase.table("requests")
            .select("requested_title")
            .in_("status", ["pending", "processing"])
            .execute
        )
        
        pedidos_ativos = response.data if response.data else []
        
        if pedidos_ativos:
            id_tag = f"[ID: {tmdb_data['tmdb_id']}]"
            for req in pedidos_ativos:
                if id_tag in req['requested_title']:
                    await msg_wait.edit_text(
                        f"⚠️ <b>Pedido já realizado!</b>\n\n"
                        f"O título <b>{tmdb_data['title']}</b> já foi solicitado e está na fila trabalhando.\n"
                        "Não precisa pedir de novo, é só aguardar! 😉",
                        parse_mode="HTML"
                    )
                    if 'state' in context.user_data: del context.user_data['state']
                    return
    except Exception as e:
        print(f"Erro ao verificar duplicidade na fila: {e}")

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
        