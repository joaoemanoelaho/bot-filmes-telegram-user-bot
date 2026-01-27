from telegram import Update
from telegram.ext import ContextTypes
# CORREÇÃO: Removemos 'get_vip_sales_message' que não existe e não é usado aqui
from handlers.common import DB_SEMAPHORE, safe_call

async def request_start_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Callback para 'main_request'.
    ATUALMENTE EM MANUTENÇÃO.
    """
    query = update.callback_query
    
    async with DB_SEMAPHORE:
        # Mostra o alerta (pop-up)
        await safe_call(query, "answer", text="⚠️ Sistema em Manutenção!", show_alert=True)
        
        texto_manutencao = (
            "⚠️ **SISTEMA DE PEDIDOS EM MANUTENÇÃO** ⚠️\n\n"
            "Estamos atualizando nosso sistema para torná-lo mais preciso!\n\n"
            "🔜 **Novidade:** Em breve, os pedidos serão feitos usando o **Código TMDB**.\n"
            "Isso vai acabar com os erros de filmes com nomes iguais!\n\n"
            "ℹ️ _Aguarde o aviso no canal oficial._"
        )
        
        # Envia a mensagem explicativa
        await safe_call(query, "edit_message_text", text=texto_manutencao, parse_mode="Markdown")

async def request_command_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Comando /pedir.
    ATUALMENTE EM MANUTENÇÃO.
    """
    texto = (
        "⚠️ **SISTEMA DE PEDIDOS EM MANUTENÇÃO** ⚠️\n\n"
        "Estamos atualizando nosso sistema para torná-lo mais preciso!\n\n"
        "🔜 **Novidade:** Em breve, os pedidos serão feitos usando o **Código TMDB**.\n"
        "Isso vai acabar com os erros de filmes com nomes iguais!\n\n"
        "ℹ️ _Aguarde o aviso no canal oficial._"
    )
    await update.message.reply_text(texto, parse_mode="Markdown")
    