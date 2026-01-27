from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import ContextTypes

async def manutencao_pedidos_comando(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Responde ao comando /pedir com mensagem de manutenção."""
    texto = (
        "⚠️ **SISTEMA DE PEDIDOS EM MANUTENÇÃO** ⚠️\n\n"
        "Estamos atualizando nosso sistema para torná-lo mais preciso!\n\n"
        "🔜 **Novidade:** Em breve, os pedidos serão feitos usando o **Código TMDB**.\n"
        "Isso vai acabar com os erros de filmes com nomes iguais!\n\n"
        "ℹ️ _Aguarde o aviso no canal oficial._"
    )
    await update.message.reply_text(texto, parse_mode="Markdown")

async def manutencao_pedidos_botao(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Intercepta o clique no botão 'Pedir Filme/Série'."""
    query = update.callback_query
    await query.answer("⚠️ Em manutenção!", show_alert=True)
    
    texto = (
        "⚠️ **MANUTENÇÃO** ⚠️\n\n"
        "O sistema de pedidos está sendo reformulado para usar o **Código TMDB**.\n"
        "Isso evitará erros como baixar 'Private Lesson' antigo em vez do novo.\n\n"
        "Voltaremos em breve! 🔧"
    )
    
    # Envia uma nova mensagem explicativa ou edita (opcional)
    # Aqui apenas enviamos o alerta (acima) e uma mensagem efêmera
    await query.message.reply_text(texto, parse_mode="Markdown")
    