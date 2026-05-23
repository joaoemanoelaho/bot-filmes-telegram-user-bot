import asyncio
import time
import database as db # Importação correta em formato de alias
from pyrogram.errors import FloodWait

async def iniciar_worker_notificacoes(app):
    """
    Worker em background que roda eternamente no Bot de Usuário,
    caçando notificações pendentes no Supabase.
    """
    print("🚀 [Worker] Motor de Notificações Assíncronas Ligado!")
    
    while True:
        try:
            # Chama o supabase através do db.
            if not db.supabase:
                await asyncio.sleep(10)
                continue

            # 1. Busca se tem alguma notificação 'pending' (Ordem da mais antiga para a mais nova)
            res = await asyncio.to_thread(
                db.supabase.table('notification_queue')
                .select('*')
                .eq('status', 'pending')
                .order('created_at', desc=False)
                .limit(1)
                .execute
            )

            if not res.data:
                # Sem trabalho? Dorme 10 segundos e checa de novo
                await asyncio.sleep(10)
                continue

            notificacao = res.data[0]
            notif_id = notificacao['id']
            tmdb_id = notificacao['tmdb_id']
            nome_conteudo = notificacao['content_name']

            # 2. A TRAVA (Lock): Muda imediatamente para 'processing' para nenhum outro processo clonar
            await asyncio.to_thread(
                db.supabase.table('notification_queue')
                .update({'status': 'processing'})
                .eq('id', notif_id)
                .execute
            )

            print(f"⚙️ [Worker] Processando pacote de notificações para: {nome_conteudo}")

            # 3. Busca os assinantes daquela série específica (usando o db.)
            inscritos = await db.get_subscribers(tmdb_id)
            
            if inscritos:
                mensagem_texto = f"🍿 **Nova atualização no catálogo!**\n\nO conteúdo **{nome_conteudo}** acabou de chegar! Acesse o bot para assistir agora mesmo. 🏃‍♂️💨"
                
                for uid in inscritos:
                    # O LOOP DE RETENTATIVA DO CTO
                    while True:
                        try:
                            await app.send_message(chat_id=uid, text=mensagem_texto)
                            await asyncio.sleep(0.05) # Ritmo normal
                            break # Deu certo, sai do while e vai pro próximo usuário
                            
                        except FloodWait as e:
                            # 🚦 O Telegram pediu arrego! Pausando...
                            tempo_espera = e.value
                            print(f"🚦 [FloodWait] Telegram mandou esperar {tempo_espera}s. Pausando...")
                            await asyncio.sleep(tempo_espera)
                            # Não tem 'break' aqui! Ele volta pro topo do 'while' e tenta o mesmo usuário de novo!
                            
                        except Exception as e:
                            # Erro fatal (usuário deletou a conta, bloqueou, etc)
                            erro_str = str(e).lower()
                            if "blocked" in erro_str or "deactivated" in erro_str:
                                await db.set_user_inactive(uid)
                            else:
                                print(f"⚠️ Erro ao enviar para {uid}: {e}")
                            break # Sai do while e desiste desse usuário

            # 4. CHECK-OUT: Finalizou o lote? Deleta da fila para poupar banco (ou marca como concluído)
            await asyncio.to_thread(
                db.supabase.table('notification_queue')
                .delete()
                .eq('id', notif_id)
                .execute
            )
            print(f"✅ [Worker] Todas as notificações de '{nome_conteudo}' foram entregues!")

        except Exception as e:
            print(f"❌ [Worker] Erro crítico no loop do Worker: {e}")
            await asyncio.sleep(10) # Evita loop infinito de erro travando a CPU
