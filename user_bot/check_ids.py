import asyncio
from telegram import Bot
from telegram.error import BadRequest
import database as db
from config import BOT_TOKEN
import datetime

# Uma lista para guardar os IDs quebrados
broken_files = []

async def check_file_id(bot: Bot, file_id: str, context_message: str):
    """
    Tenta acessar um file_id. Se falhar, adiciona na lista de quebrados.
    (VERSÃO CORRIGIDA)
    """
    if not file_id:
        return # Ignora se o campo estiver vazio
        
    try:
        # bot.get_file é um "ping" leve para o Telegram.
        await bot.get_file(file_id)
        print(f"  [OK] {context_message}")
        
    except BadRequest as e:
        error_message = str(e)
        
        # --- ESTA É A MUDANÇA ---
        # Agora ele procura por AMBAS as mensagens de erro
        if "Wrong file identifier" in error_message or "Wrong file_id or the file is temporarily unavailable" in error_message:
        # --- FIM DA MUDANÇA ---
            print(f"  [FALHA] {context_message} -> ID QUEBRADO: {file_id}")
            # Adiciona o contexto e o ID quebrado na lista
            broken_files.append(f"{context_message} (ID: {file_id})")
        else:
            print(f"  [ERRO] {context_message} -> Erro: {e}")
            
    except Exception as e:
        print(f"  [ERRO GRAVE] {context_message} -> {e}")

    # Pausa de 1 segundo para não sobrecarregar o Telegram
    await asyncio.sleep(1)


async def main():
    print("Iniciando verificação de saúde dos File IDs...")
    print("Isso pode demorar muitos minutos. Pegue um café ☕\n")
    
    if not BOT_TOKEN:
        print("ERRO: BOT_TOKEN não encontrado no config.py")
        return
        
    bot = Bot(BOT_TOKEN)
    
    # 1. Verificando Filmes
    print("--- Verificando Tabela [MOVIES] ---")
    movies = await db.get_all_movie_file_ids()
    print(f"Encontrados {len(movies)} filmes para checar...")
    
    for movie in movies:
        movie_ctx = f"Filme ID {movie['movie_id']} ({movie['title']})"
        await check_file_id(bot, movie.get('dubbed_file_id'), f"{movie_ctx} [DUB]")
        await check_file_id(bot, movie.get('subtitled_file_id'), f"{movie_ctx} [SUB]")

    # 2. Verificando Episódios
    print("\n--- Verificando Tabela [EPISODES] ---")
    episodes = await db.get_all_episode_file_ids()
    print(f"Encontrados {len(episodes)} episódios para checar...")
    
    for ep in episodes:
        # Usando .get() para evitar erro se 'episode_number' não existir
        ep_num = ep.get('episode_number', '?')
        ep_ctx = f"Episódio ID {ep['id']} (Ep {ep_num})"
        await check_file_id(bot, ep.get('dubbed_file_id'), f"{ep_ctx} [DUB]")
        await check_file_id(bot, ep.get('subtitled_file_id'), f"{ep_ctx} [SUB]")

    # 3. Relatório Final
    print("\n\n--- CHECK-UP CONCLUÍDO! ---")
    if not broken_files:
        print("🎉 BOAS NOTÍCIAS! Nenhum file_id quebrado foi encontrado.")
    else:
        print(f"🚨 ATENÇÃO! Foram encontrados {len(broken_files)} file_ids quebrados:")
        
        # --- MELHORIA: Salvar em um arquivo ---
        filename = f"quebrados_{datetime.datetime.now().strftime('%Y%m%d_%H%M')}.txt"
        with open(filename, 'w', encoding='utf-8') as f:
            f.write(f"Relatório de File IDs Quebrados - {datetime.datetime.now().strftime('%Y-%m-%d %H:%M')}\n")
            f.write(f"Total: {len(broken_files)}\n")
            f.write("--------------------------------------------------\n\n")
            
            for item in broken_files:
                print(f"  - {item}")
                f.write(f"- {item}\n")
        
        print(f"\n✅ Relatório salvo em: {filename}")
        print("\nSua tarefa: Encontre esses arquivos no seu canal de armazenamento,")
        print("envie-os novamente, pegue o novo file_id e atualize no Supabase.")
        # --- FIM DA MELHORIA ---

if __name__ == "__main__":
    asyncio.run(main())