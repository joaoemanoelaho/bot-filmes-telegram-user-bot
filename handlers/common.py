import asyncio
import sys
import os
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes
import database as db

# Garante acesso aos módulos raiz (config.py, database.py)
current_dir = os.path.dirname(os.path.abspath(__file__))
parent_dir = os.path.dirname(current_dir)
sys.path.insert(0, parent_dir)

# =================================================================
# === GERENCIADOR DE FILA (SEMAPHORE) ===
# =================================================================
DB_SEMAPHORE = asyncio.Semaphore(20)

async def safe_call(obj, method_name, *args, **kwargs):
    """Evita crash caso o objeto ou método estejam ausentes."""
    if not obj: return None
    method = getattr(obj, method_name, None)
    if not method: return None
    try:
        if asyncio.iscoroutinefunction(method):
            return await method(*args, **kwargs)
        else:
            return method(*args, **kwargs)
    except Exception as e:
        print(f"[WARN] Erro em safe_call({method_name}): {e}")

async def _get_episode_details_message(episode_id: int, bot_username: str, delete_msg_id: int = None) -> tuple[str, InlineKeyboardMarkup]:
    """
    Prepara a mensagem e mostra APENAS os botões de áudio.
    """
    try:
        details = await db.get_full_episode_details(episode_id)
        if not details: 
            return ("Erro: Episódio não encontrado.", None)

        episode = details
        season = details.get('seasons')
        series = season.get('series') if season else None

        if not season or not series: 
            return ("Erro: Dados da temporada ou série ausentes.", None)

        series_title = series.get('title', 'Série')
        ep_title = episode.get('title', f"Episódio {episode['episode_number']}")
        
        message_text = (
            f"📽️ *{series_title}*\n"
            f"🎬 *Temporada:* {season['season_number']}\n"
            f"🎯 *Episódio:* {episode['episode_number']} - {ep_title}\n"
            f"--------------------\n"
            f"Selecione o áudio (o bot irá te chamar no privado):"
        )

        keyboard = []
        audio_row = []
        if episode.get('dubbed_file_id'):
            payload = f"watch_ep_{episode['id']}_dub"
            if delete_msg_id: payload += f"_del_{delete_msg_id}"
            url = f"https://t.me/{bot_username}?start={payload}"
            audio_row.append(InlineKeyboardButton("Dublado 🇧🇷", url=url))
            
        if episode.get('subtitled_file_id'):
            payload = f"watch_ep_{episode['id']}_sub"
            if delete_msg_id: payload += f"_del_{delete_msg_id}"
            url = f"https://t.me/{bot_username}?start={payload}"
            audio_row.append(InlineKeyboardButton("Legendado 🇺🇸", url=url))

        if audio_row: keyboard.append(audio_row)

        return (message_text, InlineKeyboardMarkup(keyboard))

    except Exception as e:
        print(f"❌ Erro em _get_episode_details_message: {e}")
        return (f"Erro ao carregar detalhes do episódio: {e}", None)

async def _get_vip_sales_message(context: ContextTypes.DEFAULT_TYPE) -> tuple[str, InlineKeyboardMarkup]:
    """
    Busca a configuração de venda do DB e formata a mensagem e os botões.
    """
    config = await db.get_bot_config()
    
    price = config.get('vip_price', 4.99)
    anchor_price = config.get('vip_anchor_price', 14.99)
    sales_text = config.get('vip_sales_text', 'Para continuar, assine o VIP!')
    
    formatted_text = sales_text.format(
        PRICE=f"R$ {price:,.2f}",
        ANCHOR_PRICE=f"R$ {anchor_price:,.2f}"
    )
    
    keyboard = [[InlineKeyboardButton("Quero meu Acesso Premium! 🚀", callback_data="main_vip")]]
    
    if 'update' in context.user_data and isinstance(context.user_data['update'], Update) and context.user_data['update'].callback_query:
         keyboard.append([InlineKeyboardButton("⬅️ Voltar ao Menu", callback_data="back_to_main")])
         
    return (formatted_text, InlineKeyboardMarkup(keyboard))

async def delete_message_job(context: ContextTypes.DEFAULT_TYPE):
    try:
        chat_id = context.job.data['chat_id']
        message_id = context.job.data['message_id']
        print(f"[JOB] Deletando msg {message_id} no chat {chat_id} (após 4h).")
        await context.bot.delete_message(chat_id=chat_id, message_id=message_id)
    except Exception as e:
        print(f"[JOB-WARN] Não foi possível deletar msg: {e}")

async def get_fav_keyboard_markup(user_id, unique_code, current_keyboard=None):
    is_fav = await db.is_favorite(user_id, unique_code)
    btn_text = "❌ Remover da Lista" if is_fav else "🔖 Salvar na Lista"
    callback = f"fav_toggle_{unique_code}"
    fav_button = [InlineKeyboardButton(btn_text, callback_data=callback)]
    
    if current_keyboard:
        new_keyboard = []
        replaced = False
        for row in current_keyboard:
            new_row = []
            for btn in row:
                if btn.callback_data and btn.callback_data.startswith("fav_toggle_"):
                    new_row.append(InlineKeyboardButton(btn_text, callback_data=callback))
                    replaced = True
                else:
                    new_row.append(btn)
            new_keyboard.append(new_row)
        if not replaced: new_keyboard.append(fav_button)
        return InlineKeyboardMarkup(new_keyboard)
    else:
        return InlineKeyboardMarkup([fav_button])