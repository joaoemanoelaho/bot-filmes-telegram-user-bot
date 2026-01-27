import asyncio
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes
from .. import database as db
import os
import sys

# Garante acesso aos módulos raiz
current_dir = os.path.dirname(os.path.abspath(__file__))
parent_dir = os.path.dirname(current_dir)
sys.path.insert(0, parent_dir)

# Semáforo global
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

async def delete_message_job(context: ContextTypes.DEFAULT_TYPE):
    """Job para deletar mensagens após X tempo."""
    try:
        chat_id = context.job.data['chat_id']
        message_id = context.job.data['message_id']
        await context.bot.delete_message(chat_id=chat_id, message_id=message_id)
    except Exception as e:
        print(f"[JOB-WARN] Falha ao deletar msg {message_id}: {e}")

async def get_vip_sales_message() -> tuple[str, InlineKeyboardMarkup]:
    """Gera o texto e botões de venda VIP."""
    config = await db.get_bot_config()
    price = config.get('vip_price', 4.99)
    anchor_price = config.get('vip_anchor_price', 14.99)
    sales_text = config.get('vip_sales_text', 'Para continuar, assine o VIP!')
    
    formatted_text = sales_text.format(
        PRICE=f"R$ {price:,.2f}",
        ANCHOR_PRICE=f"R$ {anchor_price:,.2f}"
    )
    
    keyboard = [
        [InlineKeyboardButton("Quero meu Acesso Premium! 🚀", callback_data="main_vip")],
        [InlineKeyboardButton("⬅️ Voltar ao Menu", callback_data="back_to_main")]
    ]
    return (formatted_text, InlineKeyboardMarkup(keyboard))
