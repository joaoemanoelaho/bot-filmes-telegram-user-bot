import sys
import os
import logging
import asyncio
import json
import secrets
from starlette.applications import Starlette
from starlette.routing import Route
from starlette.requests import Request
from starlette.responses import Response, JSONResponse
from telegram import Update
from telegram.ext import Application, PicklePersistence
from ptbcontrib.aiohttp_request import AiohttpRequest
import aiohttp
from aiohttp_socks import ProxyConnector
from telegram.request import HTTPXRequest
from telegram.error import NetworkError
from telegram import InlineKeyboardMarkup, InlineKeyboardButton
from zoneinfo import ZoneInfo

import handlers_user as handlers
import database as db
from handlers import (
    manutencao, 
    start, 
    vip, 
    pedidos, 
    admin, 
    player, 
    busca, 
    utils_fav,
    filtros_anti
)
from handlers.start import adicionar_horas_vip
from telegram.ext import CommandHandler, CallbackQueryHandler, MessageHandler, filters, InlineQueryHandler
# CERTIFIQUE-SE QUE ESTAS VARIÁVEIS ESTÃO NO SEU CONFIG.PY
from config import BOT_TOKEN, PROXY_URL, WEBHOOK_DOMAIN, TELEGRAM_WEBHOOK_PATH, WEBHOOK_SECRET

# --- DEBUG PRINT ---
print("[DEBUG] Versão do código: 2.0 (SyncPay Integration)")
# ---------------------

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)

application: Application = None
APP_INITIALIZED = asyncio.Event()
session: aiohttp.ClientSession | None = None

# ==========================================================
# 🔁 ERROR HANDLER
# ==========================================================
async def error_handler(update: object, context):
    e = context.error
    print(f"[DEBUG] ERROR_HANDLER ATIVADO! Erro: {e}")

    if isinstance(e, (NetworkError, aiohttp.ClientError)):
        print(f"❌ ERRO DE REDE (Captura Global): {e}")
        return

    print(f"❌ Erro não-rede no handler: {e}")
    import traceback
    traceback.print_exc()

# ==========================================================
# ⏰ CRON JOB: LEMBRETE DE VENCIMENTO VIP (11:00 BRT)
# ==========================================================
async def rotina_lembretes_vencimento(app):
    print("⏰ [CRON] Rotina de Lembretes de Vencimento iniciada!")
    fuso_br = ZoneInfo('America/Sao_Paulo') # Define o horário exato do Brasil
    
    while True:
        try:
            agora = datetime.now(fuso_br)
            # Define o alvo: Hoje às 11:00:00 da manhã
            alvo = agora.replace(hour=11, minute=0, second=0, microsecond=0)
            
            # Se hoje já passou das 11:00, ele agenda para as 11:00 de amanhã!
            if agora >= alvo:
                alvo += timedelta(days=1)
            
            # Calcula exatamente quantos segundos faltam até as 11:00
            segundos_espera = (alvo - agora).total_seconds()
            horas_espera = segundos_espera / 3600
            print(f"⏰ [CRON] Lembretes VIP agendados para disparar em {horas_espera:.1f} horas (às 11:00 BRT).")
            
            # O bot "dorme" e só acorda nesse horário exato!
            await asyncio.sleep(segundos_espera)
            
            # ======== ACORDOU! HORA DE COBRAR ========
            print("🔔 [CRON] São 11:00! Disparando cobranças VIP...")
            
            dias_aviso = [3, 2, 1, 0] 
            
            for dias in dias_aviso:
                usuarios = await db.buscar_usuarios_vencendo_em(dias)
                
                if usuarios:
                    print(f"🔔 Disparando avisos para {len(usuarios)} usuários vencendo em {dias} dias.")
                
                for user in usuarios:
                    user_id = user.get('user_id')
                    nome = user.get('first_name', 'Amigo(a)')
                    if not user_id: continue
                    
                    if dias == 0:
                        alerta = "🚨 **SEU VIP ACABA HOJE!** 🚨"
                        texto_dias = "nas próximas horas"
                    elif dias == 1:
                        alerta = "⏳ **SEU VIP ACABA AMANHÃ!** ⏳"
                        texto_dias = "amanhã"
                    else:
                        alerta = f"⚠️ **Atenção, {nome}!**"
                        texto_dias = f"em **{dias} dias**"

                    mensagem = (
                        f"{alerta}\n\n"
                        f"Sua assinatura Pipoca Premium vai expirar {texto_dias}.\n\n"
                        f"Não fique sem os seus filmes e séries favoritos! 🍿\n"
                        f"Renove o seu plano agora mesmo para não perder o acesso."
                    )
                    
                    # Botão mágico que já abre a aba de pagamento de PIX na mesma hora!
                    keyboard = [[InlineKeyboardButton("💎 Renovar VIP Agora", callback_data="main_vip")]]
                    
                    try:
                        await app.bot.send_message(
                            chat_id=user_id, 
                            text=mensagem,
                            reply_markup=InlineKeyboardMarkup(keyboard),
                            parse_mode="Markdown"
                        )
                        print(f"   ✅ Aviso ({dias} dias) enviado para {user_id}")
                    except Exception as e:
                        print(f"   ❌ Erro ao enviar aviso para {user_id}: {e}")
                        
                    await asyncio.sleep(2) # Pausa de 2 segundos para evitar Flood no Telegram
                    
        except Exception as e:
            print(f"❌ Erro geral na rotina de lembretes: {e}")
            await asyncio.sleep(60) # Se der algum erro, espera 1 minuto e tenta rodar de novo

# ==========================================================
# 🚀 STARTUP DO BOT
# ==========================================================
async def startup():
    global application, session
    print("[DEBUG] Função startup() iniciada.")
    final_proxy_url: str | None = PROXY_URL

    try:
        timeout = aiohttp.ClientTimeout(total=600, connect=60, sock_read=600, sock_connect=60)
    
        request_motor = AiohttpRequest(
            client_timeout=timeout, 
            connection_pool_size=256, 
            socks_url=final_proxy_url
        )

        persistence = PicklePersistence(filepath="bot_persistence.pkl")

        print("🔵 Criando Application do Bot com Persistência...")
        application = Application.builder().token(BOT_TOKEN).request(request_motor).get_updates_request(request_motor).persistence(persistence).build()

        application.add_handler(filtros_anti.filtro_handler, group=-2)
        
        # 1. START & MENU
        application.add_handler(CommandHandler("start", start.start))
        application.add_handler(CommandHandler("help", start.help_handler))
        application.add_handler(CommandHandler("cancelar", start.cancel_handler))
        application.add_handler(CallbackQueryHandler(start.back_to_main_handler, pattern="^back_to_main$"))

        # 2. VIP & PIX
        application.add_handler(CallbackQueryHandler(vip.vip_menu_callback, pattern="^main_vip$"))
        application.add_handler(CallbackQueryHandler(vip.confirm_pay_callback, pattern="^confirm_pay$"))

        # 3. PLAYER & NAVEGAÇÃO
        application.add_handler(CommandHandler("watch", player.watch_command_handler))
        application.add_handler(CallbackQueryHandler(player.player_callback, pattern="^(play_|ep_nav_|related_|show_card_|main_top|top_)"))

        # 4. FAVORITOS
        application.add_handler(CallbackQueryHandler(utils_fav.fav_menu_callback, pattern="^fav_menu$"))
        application.add_handler(CallbackQueryHandler(utils_fav.fav_toggle_callback, pattern="^fav_toggle_"))
        application.add_handler(CallbackQueryHandler(utils_fav.fav_watch_callback, pattern="^fav_watch_"))

        # 5. ADMIN
        application.add_handler(CommandHandler("setmenu", admin.set_menu_command))
        application.add_handler(CommandHandler("fakepay", admin.fake_pay_command))
        application.add_handler(CommandHandler("setconfig", admin.set_config_command))
        application.add_handler(CommandHandler("settext", admin.set_text_command))
        application.add_handler(CommandHandler("showconfig", admin.show_config_command))
        application.add_handler(CommandHandler("pedidos", admin.pedidos_command_handler))
        application.add_handler(CommandHandler("transmissao", admin.broadcast_command_handler))
        application.add_handler(MessageHandler(filters.ALL & ~filters.COMMAND, admin.text_message_handler))
        application.add_handler(CallbackQueryHandler(admin.admin_callback, pattern="^adm_"))

        # 6. PEDIDOS
        application.add_handler(CommandHandler("pedir", pedidos.request_command_handler))
        application.add_handler(CallbackQueryHandler(pedidos.request_start_callback, pattern="^main_request$"))
        
        # 7. BUSCA
        application.add_handler(InlineQueryHandler(busca.inline_query_handler))
        application.add_error_handler(error_handler)

        await application.initialize()
        await application.start()
        
        webhook_url = f"{WEBHOOK_DOMAIN}{TELEGRAM_WEBHOOK_PATH}"
        print(f"ℹ️ Configurando webhook do Telegram para: {webhook_url}")
        # 🛡️ SISTEMA BLINDADO: Tenta conectar ao Telegram até 5 vezes
        for tentativa in range(5):
            try:
                # drop_pending_updates=True ajuda a limpar mensagens velhas presas
                await application.bot.set_webhook(url=webhook_url, allowed_updates=Update.ALL_TYPES, drop_pending_updates=True)
                print("✅ Webhook do Telegram configurado com sucesso!")
                break # Deu certo, sai do loop!
            except Exception as e:
                print(f"⚠️ Falha de rede ao conectar com Telegram (Tentativa {tentativa + 1}/5): {e}")
                if tentativa == 4:
                    print("💀 O Proxy ou a Rede caíram de vez. Desistindo...")
                    raise # Se falhou 5 vezes, aí sim a gente desiste
                await asyncio.sleep(3) # Espera 3 segundos antes de tentar de novo

        print("✅ Bot inicializado com sucesso.")

        asyncio.create_task(rotina_lembretes_vencimento(application))

        APP_INITIALIZED.set()
    except Exception as e:
        print(f"❌ ERRO CRÍTICO NO STARTUP: {e}")
        import traceback
        traceback.print_exc()
        raise

# ==========================================================
# 🧹 SHUTDOWN
# ==========================================================
async def shutdown():
    global session, application
    print("[DEBUG] Encerrando...")
    try:
        if application:
            await application.shutdown()
            await application.stop()
            print("✅ Application encerrada.")
        if session and not session.closed:
            await session.close()
            print("✅ Sessão aiohttp fechada.")
    except Exception as e:
        print(f"⚠️ Erro durante shutdown: {e}")

# ==========================================================
# 📩 WEBHOOK TELEGRAM
# ==========================================================
async def telegram_webhook(request: Request) -> Response:
    await APP_INITIALIZED.wait()
    try:
        data = await request.json()
        if not isinstance(data, dict) or "update_id" not in data:
            return Response("ok", status_code=200)
        update = Update.de_json(data, application.bot)
        asyncio.create_task(application.process_update(update))
    except Exception as e:
        print(f"❌ Erro webhook Telegram: {e}")
    return Response("ok", status_code=200)

# ==========================================================
# 💸 WEBHOOK SYNCPAY (NOVO)
# ==========================================================
async def syncpay_webhook(request: Request) -> Response:
    await APP_INITIALIZED.wait()

    # === 🔒 BLINDAGEM DE SEGURANÇA ===
    # Pega a senha que veio na URL
    secret_token = request.query_params.get('secret')
    
    # Verifica se a senha é EXATAMENTE igual a que você colocou no payments.py
    if not secret_token or not secrets.compare_digest(secret_token, WEBHOOK_SECRET):
        print(f"[ALERTA DE SEGURANÇA] 🚨 Tentativa de invasão bloqueada! IP tentou acessar sem senha.")
        return JSONResponse({"status": "error", "message": "Acesso Negado. Senha incorreta."}, status_code=403)
    # =================================
    
    # 1. Captura e validação do User ID na URL
    raw_user_id = request.path_params.get('user_id')
    try:
        user_id = int(str(raw_user_id).strip())
    except (ValueError, TypeError):
        print(f"[Webhook SyncPay] ❌ ID inválido recebido: '{raw_user_id}'")
        return JSONResponse({"status": "error", "message": "Invalid user_id"}, status_code=400)

    # 2. Leitura do JSON
    try:
        payload = await request.json()
        # A SyncPay manda tudo dentro de um objeto 'data'
        data = payload.get('data', {}) 
    except Exception as e:
        print(f"[Webhook SyncPay] ❌ Erro ao ler JSON: {e}")
        return JSONResponse({"status": "error"}, status_code=400)

    if not data:
         print(f"[Webhook SyncPay] ❌ Payload vazio ou sem 'data'.")
         return JSONResponse({"status": "ignored"})

    # 3. Processamento do status
    # Status possíveis: 'completed', 'pending', 'failed'
    payment_status = data.get("status")
    print(f"[Webhook SyncPay] 🔔 UserID: {user_id} | Status: {payment_status}")
    
    if payment_status in ["completed", "PAID_OUT", "APPROVED", "paid", "SETTLED"]:
        try:
            # Apaga a mensagem do QR Code antigo se existir
            try:
                user_data = await db.get_user_details(user_id)
                qr_msg_id = user_data.get('qr_message_id') if user_data else None
                if qr_msg_id:
                    await application.bot.delete_message(chat_id=user_id, message_id=qr_msg_id)
            except Exception: pass
            
            # ====================================================
            # 👉 ATIVA OU RENOVA O VIP (SOMANDO AS HORAS)
            # ====================================================
            config = await db.get_bot_config()
            duration = config.get('vip_duration_days', 30)
            
            # Chama a função que soma o tempo (30 dias * 24 horas = 720 horas)
            await adicionar_horas_vip(user_id, duration * 24)
            await db.clear_user_active_payment_id(user_id)
            print(f"✅ VIP ATIVADO/RENOVADO (SyncPay) para UserID: {user_id}")

            try:
                user_info = await db.get_user_details(user_id)
                referrer_id = user_info.get('referred_by') if user_info else None
                
                if referrer_id:
                    referrer_info = await db.get_user_details(referrer_id)
                    if referrer_info:
                        pontos_atuais = referrer_info.get('points', 0)
                        novos_pontos = pontos_atuais + 1
                        
                        if novos_pontos >= 5:
                            # BATEU 5 PONTOS! Dá 30 dias e zera os pontos
                            await adicionar_horas_vip(referrer_id, 720)
                            await asyncio.to_thread(db.supabase.table('users').update({'points': 0}).eq('user_id', referrer_id).execute)
                            
                            try:
                                await application.bot.send_message(
                                    chat_id=referrer_id,
                                    text="🎉 <b>VOCÊ BATEU 5 PONTOS!</b> 🏆\n\nUm amigo que você indicou acabou de assinar o VIP. Com isso você completou 5 pontos e ganhou <b>1 MÊS DE VIP TOTALMENTE GRÁTIS!</b> 🎁🚀",
                                    parse_mode="HTML"
                                )
                            except: pass
                        else:
                            # SÓ SOMA 1 PONTO
                            await asyncio.to_thread(db.supabase.table('users').update({'points': novos_pontos}).eq('user_id', referrer_id).execute)
                            try:
                                await application.bot.send_message(
                                    chat_id=referrer_id,
                                    text=f"🪙 <b>VOCÊ GANHOU 1 PONTO!</b>\n\nUm amigo que você indicou acabou de assinar o VIP! Você agora tem <b>{novos_pontos}/5 pontos</b>. Junte 5 e ganhe 1 Mês Grátis! 🎁",
                                    parse_mode="HTML"
                                )
                            except: pass
            except Exception as e:
                print(f"Erro ao processar pontos do padrinho: {e}")
            
            # Manda mensagem de sucesso
            try:
                await application.bot.send_message(
                    chat_id=user_id,
                    text="✅ **Pagamento Confirmado!** 🚀\n\nSeu acesso VIP foi liberado ou estendido com sucesso.\nObrigado por apoiar o Cine Pipoca! 🍿",
                    parse_mode="Markdown"
                )
            except Exception: pass
            
        except Exception as e_db:
            print(f"❌ ERRO AO SALVAR VIP (SyncPay): {e_db}")
            return JSONResponse({"status": "error"}, status_code=500)

    return JSONResponse({"status": "received"})

# ==========================================================
# 🔔 WEBHOOK SUPABASE
# ==========================================================
async def supabase_webhook(request: Request) -> Response:
    await APP_INITIALIZED.wait()
    try:
        data = await request.json()
        user_id = data.get('user_id')
        title = data.get('title')
        new_status = data.get('new_status')
        
        if user_id and new_status:
            message = ""
            if new_status == 'added':
                message = f"🎉 **Boas notícias!**\n\nO título **'{title}'** foi adicionado ao catálogo! 🍿"
            elif new_status == 'denied':
                message = f"😔 **Olá!**\n\nSobre o pedido **'{title}'**: infelizmente não conseguimos adicionar no momento."
            
            if message:
                try:
                    await application.bot.send_message(chat_id=user_id, text=message, parse_mode="Markdown")
                except Exception: pass
        return Response(status_code=200)
    except Exception:
        return Response(status_code=500)

# ==========================================================
# 🩺 HEALTH CHECK
# ==========================================================
async def health_check(request: Request) -> Response:
    return Response("Online!", status_code=200)

# ==========================================================
# 🛠️ ROTAS
# ==========================================================
routes = [
    Route(TELEGRAM_WEBHOOK_PATH, endpoint=telegram_webhook, methods=["POST"]),
    # ROTA NOVA SYNCPAY
    Route("/webhook/syncpay/{user_id}", endpoint=syncpay_webhook, methods=["POST"]),
    Route("/webhook/supabase", endpoint=supabase_webhook, methods=["POST"]),
    Route("/health", endpoint=health_check, methods=["GET"]),
]

app = Starlette(routes=routes, on_startup=[startup], on_shutdown=[shutdown])

if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8000))
    print(f"[WEB] Iniciando na porta {port}...")
    uvicorn.run(app, host="0.0.0.0", port=port)