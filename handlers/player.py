import time
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from telegram.error import BadRequest
import asyncio
import database as db
import tastedive_api
from config import STORAGE_CHANNEL_ID, STORAGE_CHANNEL_ID_SERIES, FSUB_CHANNEL_LINK, FSUB_GROUP_LINK, FSUB_CHANNEL_ID, FSUB_GROUP_ID
from handlers.common import (
    DB_SEMAPHORE, safe_call, delete_message_job, 
    _get_episode_details_message, _get_vip_sales_message
)

async def verificar_inscricao(bot, user_id):
    status_aceitos = ['member', 'administrator', 'creator']
    # O Bot checa usando IDs, não Links!
    chats_para_verificar = [FSUB_CHANNEL_ID, FSUB_GROUP_ID] 
    
    for chat_id in chats_para_verificar:
        try:
            membro = await bot.get_chat_member(chat_id=chat_id, user_id=user_id)
            if membro.status not in status_aceitos:
                return False
        except BadRequest:
            pass 
        except Exception:
            pass
    return True

async def watch_command_handler(update: Update, context: ContextTypes.DEFAULT_TYPE, message_deletada: bool = False) -> None:
    async with DB_SEMAPHORE:
        if update.message and not message_deletada:
            try: await update.message.delete()
            except Exception: pass

        if not context.args: return
        movie_id = context.args[0]
        user_id = update.effective_user.id
        chat_id_to_reply = update.effective_chat.id

        await db.get_or_create_user(user_id=user_id, first_name=update.effective_user.first_name)

        # VIP Check
        if not await db.is_user_vip(user_id):
            config = await db.get_bot_config()
            price = config.get('vip_price', 4.99)
            duration_days = config.get('vip_duration_days', 7)

            if price <= 0:
                await db.set_user_as_vip(user_id, duration_days=duration_days if duration_days > 0 else 9999)
                try: await context.bot.send_message(chat_id=user_id, text="🎉 Bem-vindo! O acesso está gratuito no momento.")
                except: pass
            else:
                context.user_data['update'] = update
                sales_text, reply_markup = await _get_vip_sales_message(context)
                await context.bot.send_message( 
                    chat_id=user_id,
                    text=f"Opa, {update.effective_user.first_name}! 👋\n\n{sales_text}",
                    parse_mode="HTML",
                    reply_markup=reply_markup
                )
                return

        movie = await db.get_movie_by_id(movie_id)

        if movie and movie.get('poster_url'):
            audio_buttons = []
            if movie.get('dubbed_file_id'):
                audio_buttons.append(InlineKeyboardButton("Dublado 🇧🇷", callback_data=f"play_{movie_id}_dub"))
            if movie.get('subtitled_file_id'):
                audio_buttons.append(InlineKeyboardButton("Legendado 🇺🇸", callback_data=f"play_{movie_id}_sub"))

            caption = (
                f"🎬 <b>{movie['title']}</b> ({movie['year']})\n\n"
                f"🎭 <b>Gênero:</b> {movie.get('genre', 'N/A')}\n\n"
                f"📝 <b>Sinopse:</b> {movie.get('description', 'N/A')}\n\n"
                "---\n"
            )

            keyboard = []
            reply_markup = None
            if audio_buttons:
                caption += "Selecione o áudio desejado abaixo:"
                keyboard.append(audio_buttons)
                reply_markup = InlineKeyboardMarkup(keyboard)
            else:
                caption += "😔 <b>Este filme está no catálogo, mas ainda estamos aguardando os arquivos de vídeo.</b>"

            await context.bot.send_photo(
                chat_id=chat_id_to_reply,
                photo=movie['poster_url'],
                caption=caption,
                parse_mode="HTML",
                reply_markup=reply_markup
            )
        else:
            await context.bot.send_message(chat_id=chat_id_to_reply, text="Filme não encontrado ou sem pôster disponível.")

async def player_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Centraliza os callbacks de mídia: play_, ep_nav_, related_, top_, main_top, show_card_
    """
    query = update.callback_query
    callback_data = query.data
    user_id = query.from_user.id
    
    # 1. PLAY MOVIE (play_)
    if callback_data.startswith("play_"):

        esta_inscrito = await verificar_inscricao(context.bot, user_id)
        if not esta_inscrito:
            keyboard_fsub = [
                [InlineKeyboardButton("📢 Entrar no Canal", url=FSUB_CHANNEL_LINK)],
                [InlineKeyboardButton("💬 Entrar no Grupo", url=FSUB_GROUP_LINK)], 
                [InlineKeyboardButton("🔄 Já entrei! Tentar Novamente", callback_data=callback_data)]
            ]
            
            # Se for possível editar a legenda da foto (se a mensagem anterior for foto)
            try:
                await query.answer("🔒 Acesso Restrito!", show_alert=True)
                await safe_call(query, "edit_message_caption", 
                    caption="🚫 <b>Acesso Restrito!</b>\n\n"
                            "Para assistir, você precisa fazer parte da nossa comunidade.\n\n"
                            "1️⃣ Entre no <b>Canal Oficial</b>\n"
                            "2️⃣ Entre no <b>Grupo de Chat</b>\n"
                            "3️⃣ Clique em <b>Tentar Novamente</b>",
                    parse_mode="HTML",
                    reply_markup=InlineKeyboardMarkup(keyboard_fsub)
                )
            except:
                # Se não der pra editar caption (ex: era texto), manda msg nova
                await context.bot.send_message(
                    chat_id=user_id,
                    text="🚫 <b>Acesso Restrito!</b>\nEntre nos canais abaixo para liberar o vídeo.",
                    reply_markup=InlineKeyboardMarkup(keyboard_fsub)
                )
            return
        
        now = time.time()
        last_request = context.user_data.get('last_action_time', 0)
        cooldown = 10

        if now - last_request < cooldown:
            await safe_call(query, "answer", text=f"✋ Calma! Aguarde {int(cooldown - (now - last_request))}s.", show_alert=True)
            return

        async with DB_SEMAPHORE:
            await safe_call(query, "answer")
            _, movie_id_str, audio_choice = callback_data.split('_')
            movie_id = int(movie_id_str)
            await safe_call(query, "edit_message_caption", caption="⏳ Carregando seu filme, por favor aguarde...")

            movie = await db.get_movie_by_id(movie_id)

            if not movie:
                await safe_call(query, "edit_message_caption", caption="Erro: Filme não encontrado.")
                return
            
            file_id_to_send = movie.get('dubbed_file_id') if audio_choice == "dub" else movie.get('subtitled_file_id')
            msg_id_to_copy = movie.get('dubbed_msg_id') if audio_choice == "dub" else movie.get('subtitled_msg_id')

            if file_id_to_send:
                await safe_call(query, "delete_message")
                bot_username = context.bot.username
                video_caption = (
                    f"🎬 <b>{movie['title']}</b> ({movie['year']})\n\n"
                    f"🎭 <b>Gênero:</b> {movie['genre']}\n\n"
                    f"---\n"
                    f"🍿 Assistido com @{bot_username}\n"
                )

                fav_unique_code = f"mov_{movie_id}_{audio_choice}"
                is_fav = await db.is_favorite(user_id, fav_unique_code)
                fav_btn_text = "❌ Remover da Lista" if is_fav else "🔖 Salvar na Lista"

                keyboard = [
                    [InlineKeyboardButton("Compartilhar ❤️", switch_inline_query=f"{movie['title']}"),
                     InlineKeyboardButton("🍿 Relacionados", callback_data=f"related_{movie_id}")],
                    [InlineKeyboardButton(fav_btn_text, callback_data=f"fav_toggle_{fav_unique_code}")]
                ]
                video_reply_markup = InlineKeyboardMarkup(keyboard)

                try:
                    # Plano A
                    sent_message = await context.bot.send_video(
                        chat_id=query.message.chat.id,
                        video=file_id_to_send,
                        caption=video_caption,
                        parse_mode="HTML",
                        reply_markup=video_reply_markup,
                        protect_content=True
                    )
                except BadRequest as e:
                    # Plano B
                    if ("wrong file id" in str(e).lower()) and msg_id_to_copy and STORAGE_CHANNEL_ID:
                        try:
                            copied_message = await context.bot.copy_message(
                                chat_id=query.message.chat.id,
                                from_chat_id=STORAGE_CHANNEL_ID,
                                message_id=msg_id_to_copy,
                                protect_content=True
                            )
                            await context.bot.edit_message_caption(
                                chat_id=query.message.chat.id,
                                message_id=copied_message.message_id,
                                caption=video_caption,
                                parse_mode="HTML",
                                reply_markup=video_reply_markup
                            )
                        except Exception:
                            await context.bot.send_message(chat_id=query.message.chat.id, text="😔 Erro ao recuperar arquivo.")
                    else:
                        await context.bot.send_message(chat_id=query.message.chat.id, text=f"😔 Erro ao enviar: {e}")

                await db.log_movie_view(movie_id=movie_id, user_id=user_id)
                context.user_data['last_action_time'] = time.time()
            else:
                await safe_call(query, "edit_message_caption", caption="😔 Versão indisponível.")

    # 2. RELACIONADOS (related_)
    elif callback_data.startswith("related_"):
        now = time.time()
        last_request = context.user_data.get('last_action_time', 0)
        cooldown = 10
        if now - last_request < cooldown:
            await safe_call(query, "answer", text=f"✋ Aguarde {int(cooldown - (now - last_request))}s.", show_alert=True)
            return

        async with DB_SEMAPHORE:
            await safe_call(query, "answer")
            parts = callback_data.split('_')
            media_id = int(parts[1])
            source_type = 'series' if len(parts) > 2 and parts[2] == 'series' else 'movie'
            
            title_current = ""
            genre_current = ""
            if source_type == 'movie':
                media_obj = await db.get_movie_by_id(media_id)
            else:
                media_obj = await db.get_series_by_id(media_id) # Certifique-se que essa função existe no db
            
            if not media_obj:
                await context.bot.send_message(chat_id=user_id, text="Mídia original não encontrada.")
                return
            
            title_current = media_obj.get('title', 'Desconhecido')
            genre_current = media_obj.get('genre', '')
            
            status_msg = await context.bot.send_message(chat_id=user_id, text=f"🔍 Buscando filmes e séries parecidos com <b>'{title_current}'</b>...", parse_mode="HTML")
            
            # 2. Buscar Recomendações MISTAS no Banco
            recommendations = await db.get_mixed_recommendations(genre_current, limit=6)
            
            if not recommendations:
                await status_msg.edit_text(f"😕 Não encontrei nada do gênero '{genre_current}' no catálogo.")
                return
            
            keyboard = []
            for rec in recommendations:
                # Se for filme, usa o ícone de Claquete 🎬
                if rec['type'] == 'movie':
                    btn_text = f"🎬 {rec['title']}"
                    # Filmes abrem o Card direto
                    callback = f"show_card_{rec['id']}"
                    keyboard.append([InlineKeyboardButton(btn_text, callback_data=callback)])
                
                # Se for série, usa a TV 📺
                else:
                    btn_text = f"📺 {rec['title']}"
                    # Séries jogam para a busca inline (já que não temos show_card de série ainda)
                    # Isso garante que ele ache os episódios
                    keyboard.append([InlineKeyboardButton(btn_text, switch_inline_query_current_chat=rec['title'])])
            
            keyboard.append([InlineKeyboardButton("⬅️ Voltar", callback_data="back_to_main")])
            
            await status_msg.edit_text(
                text=f"🍿 <b>Porque você gosta de {genre_current}:</b>\nAqui estão sugestões do nosso catálogo:",
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode="HTML"
            )
            context.user_data['last_action_time'] = time.time()

    # 3. NAVEGAÇÃO EPISÓDIO (ep_nav_)
    elif callback_data.startswith("ep_nav_"):
        async with DB_SEMAPHORE:
            try: await safe_call(query, "answer")
            except: pass

            new_episode_id = int(callback_data.split('_')[2])
            try: await safe_call(query, "delete_message")
            except: pass

            placeholder_msg = await context.bot.send_message(chat_id=user_id, text="Carregando...")
            message_text, reply_markup = await _get_episode_details_message(
                new_episode_id,
                context.bot.username,
                delete_msg_id=placeholder_msg.message_id
            )

            if reply_markup:
                try:
                    await placeholder_msg.edit_text(text=message_text, reply_markup=reply_markup, parse_mode="HTML")
                except:
                    await placeholder_msg.edit_text(text=message_text, reply_markup=reply_markup, parse_mode="Markdown")
            else:
                await placeholder_msg.edit_text(message_text)

    # 4. RANKING TOP (main_top, top_, show_card_)
    elif callback_data == "main_top":
        async with DB_SEMAPHORE:
            # VIP check (resumido para caber)
            if not await db.is_user_vip(user_id):
                config = await db.get_bot_config()
                if config.get('vip_price', 0) > 0:
                      sales_text, _ = await _get_vip_sales_message(context)
                      
                      keyboard = [
                          [InlineKeyboardButton("✅ Sim, Gerar PIX para Pagar!", callback_data="confirm_pay")],
                          [InlineKeyboardButton("⬅️ Voltar ao Menu", callback_data="back_to_main")]
                      ]
                      
                      await safe_call(query, "edit_message_text", 
                          text=sales_text, 
                          reply_markup=InlineKeyboardMarkup(keyboard), 
                          parse_mode="HTML"
                      )
                      return
            
            await safe_call(query, "answer")
            keyboard = [
                [InlineKeyboardButton("🏆 Top Semana", callback_data="top_7")],
                [InlineKeyboardButton("🗓️ Top Mês", callback_data="top_30")],
                [InlineKeyboardButton("🌎 Top Geral", callback_data="top_0")],
                [InlineKeyboardButton("⬅️ Voltar", callback_data="back_to_main")]
            ]
            await safe_call(query, "edit_message_text", text="Selecione o período:", reply_markup=InlineKeyboardMarkup(keyboard))

    # 5. SISTEMA DE NOTIFICAÇÃO (Sininho)
    elif callback_data.startswith("sub_toggle_"):
        async with DB_SEMAPHORE:
            try:
                tmdb_id = int(callback_data.split('_')[2])
                
                # Chama a função que insere ou deleta no banco
                ativou = await db.toggle_subscription(user_id, tmdb_id)
                
                # O Aviso (Pop-up) vai aparecer não importa se é mensagem normal ou inline
                if ativou:
                    await safe_call(query, "answer", text="🔔 Notificações Ativadas! Você será avisado quando sair episódio novo.", show_alert=True)
                else:
                    await safe_call(query, "answer", text="🔕 Notificações Desativadas para esta série.", show_alert=True)
                    
                # 🛑 AQUI ESTÁ A CORREÇÃO: Verifica se a mensagem existe antes de tentar editar os botões
                if query.message:
                    current_markup = query.message.reply_markup.inline_keyboard
                    new_markup = []
                    for row in current_markup:
                        new_row = []
                        for btn in row:
                            if btn.callback_data == callback_data:
                                novo_texto = "🔔 Avisar Novos Eps (Ativado)" if ativou else "🔕 Avisar Novos Eps"
                                new_row.append(InlineKeyboardButton(novo_texto, callback_data=callback_data))
                            else:
                                new_row.append(btn)
                        new_markup.append(new_row)
                        
                    await safe_call(query, "edit_message_reply_markup", reply_markup=InlineKeyboardMarkup(new_markup))
                
                # Se não tem query.message (Busca Inline), o botão vai se atualizar 
                # sozinho na próxima vez que o usuário digitar graças ao cache_time=0!
            
            except Exception as e:
                print(f"Erro no sininho: {e}")

    elif callback_data.startswith("top_"):
        async with DB_SEMAPHORE:
            await safe_call(query, "answer")
            period_days = int(callback_data.split('_')[1])
            period_text = "Geral"
            if period_days == 7: period_text = "da Semana"
            if period_days == 30: period_text = "do Mês"
            
            await safe_call(query, "edit_message_text", text=f"🏆 Buscando Top 10 {period_text}...")
            trending = await db.get_trending(period_days=period_days)

            if not trending:
                await safe_call(query, "edit_message_text", text="Dados insuficientes.")
                return

            keyboard = []
            for item in trending:
                # Agora o banco retorna uma coluna 'type' ('movie' ou 'series')
                media_type = item.get('type', 'movie') 
                title = item['title']
                year = item['year']
                media_id = item['id'] # O ID agora vem na coluna genérica 'id'
                
                if media_type == 'movie':
                    # Se for FILME: Ícone 🎬 e abre o Card normal
                    btn_text = f"🎬 {title} ({year})"
                    callback = f"show_card_{media_id}"
                    keyboard.append([InlineKeyboardButton(btn_text, callback_data=callback)])
                else:
                    # Se for SÉRIE: Ícone 📺 e faz a busca dos episódios
                    btn_text = f"📺 {title} ({year})"
                    keyboard.append([InlineKeyboardButton(btn_text, switch_inline_query_current_chat=title)])

            keyboard.append([InlineKeyboardButton("⬅️ Voltar", callback_data="main_top")])
            
            await safe_call(query, "edit_message_text", text=f"🏆 <b>Top 10 {period_text}</b>", parse_mode="HTML", reply_markup=InlineKeyboardMarkup(keyboard))

    elif callback_data.startswith("show_card_"):
        async with DB_SEMAPHORE:
            await safe_call(query, "answer")
            movie_id = int(callback_data.split('_')[2])
            movie = await db.get_movie_by_id(movie_id)

            if not movie:
                await safe_call(query, "edit_message_text", text="Filme não encontrado.")
                return

            try: await safe_call(query, "delete_message")
            except: pass

            url = f"https://t.me/{context.bot.username}?start=watch_{movie['movie_id']}"
            keyboard = [[
                InlineKeyboardButton("Assistir ⏯️", url=url),
                InlineKeyboardButton("Compartilhar ❤️", switch_inline_query=movie['title'])
            ]]
            caption = f"🎬 <b>{movie['title']}</b> ({movie['year']})\n🎭 {movie.get('genre', 'N/A')}"
            
            await context.bot.send_photo(chat_id=user_id, photo=movie['poster_url'], caption=caption, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(keyboard))
