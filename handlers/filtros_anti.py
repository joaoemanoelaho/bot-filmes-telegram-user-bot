import time
from telegram import Update
from telegram.ext import ContextTypes, MessageHandler, filters
from telegram.ext import ApplicationHandlerStop

# ==========================================
# ⚙️ CONFIGURAÇÕES DOS FILTROS
# ==========================================
TEMPO_FLOOD_SEGUNDOS = 1.5  # Tempo mínimo entre mensagens (1.5 segundos)

# Palavras de spam clássicas (Se quiser, pode adicionar mais)
PALAVRAS_PROIBIDAS = [
    "tigrinho", "cassino", "plataforma pagando", "fortune tiger", 
    "onlyfans", "🔞", "slots", "aposta"
]

async def master_filter(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    O Cão de Guarda do Bot (Versão Chat Privado).
    Lê a mensagem antes de todo mundo. Se tiver infração, deleta e encerra o processo.
    """
    if not update.effective_message or not update.effective_user or not update.effective_chat:
        return

    msg = update.effective_message
    user = update.effective_user
    chat = update.effective_chat

    # ==========================================
    # 👑 REGRA 1: ATUAR APENAS NO PRIVADO DO BOT
    # Se for grupo ou canal, o bot ignora o filtro e deixa rolar
    # ==========================================
    if chat.type != 'private':
        return

    texto_msg = (msg.text or msg.caption or "").lower()

    # ==========================================
    # 🛡️ 2. SISTEMA ANTI-FLOOD (Metralhadora)
    # ==========================================
    agora = time.time()
    ultima_msg_tempo = context.user_data.get('ultima_msg_tempo', 0)
    
    if agora - ultima_msg_tempo < TEMPO_FLOOD_SEGUNDOS:
        try:
            await msg.delete()
        except: pass
        raise ApplicationHandlerStop() # 🛑 Mata a execução do bot aqui!
    
    # Atualiza o relógio do usuário
    context.user_data['ultima_msg_tempo'] = agora

    # ==========================================
    # 🔗 3. SISTEMA ANTI-LINKS (Com exceção do TMDB)
    # ==========================================
    tem_link = False
    if msg.entities:
        for ent in msg.entities:
            if ent.type in ['url', 'text_link']:
                tem_link = True
                break
                
    if tem_link:
        # Verifica se o link é o do TMDB (o que usamos pros pedidos)
        if "themoviedb.org" in texto_msg or "tmdb.org" in texto_msg:
            pass # É do TMDB, o guarda libera a catraca!
        else:
            try:
                await msg.delete()
                await msg.reply_text(f"🚫 <b>{user.first_name}</b>, apenas links do TMDB são permitidos para fazer pedidos!", parse_mode="HTML")
            except: pass
            raise ApplicationHandlerStop()

    # ==========================================
    # 🔄 4. SISTEMA ANTI-ENCAMINHAMENTO (Impede correntes)
    # ==========================================
    if msg.forward_origin:
        try:
            await msg.delete()
            await msg.reply_text(f"🚫 <b>{user.first_name}</b>, não aceitamos mensagens encaminhadas por aqui.", parse_mode="HTML")
        except: pass
        raise ApplicationHandlerStop()

    # ==========================================
    # 🤬 5. SISTEMA ANTI-SPAM TIGRINHO / PALAVRÕES
    # ==========================================
    if any(palavra in texto_msg for palavra in PALAVRAS_PROIBIDAS):
        try:
            await msg.delete() # Apaga silenciosamente
        except: pass
        raise ApplicationHandlerStop()

# ==========================================
# 🔌 EXPORTA O HANDLER PRONTO
# ==========================================
filtro_handler = MessageHandler(filters.ALL, master_filter)
