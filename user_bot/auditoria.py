import asyncio
import requests
import database as db
from config import TMDB_API_KEY # Puxa a chave direto do seu config

async def auditar_acervo_series():
    print("🕵️ Iniciando Auditoria Geral do Acervo de Séries...")
    
    # 1. Puxa TODAS as séries do banco
    try:
        response = await asyncio.to_thread(
            db.supabase.table('series').select('id, tmdb_id, title').execute
        )
        todas_as_series = response.data if response.data else []
    except Exception as e:
        print(f"Erro ao buscar séries no banco: {e}")
        return

    print(f"📊 Encontradas {len(todas_as_series)} séries para auditar.\n")

    atualizadas_para_false = 0

    for serie in todas_as_series:
        serie_id = serie['id']
        tmdb_id = serie['tmdb_id']
        titulo = serie['title']
        
        if not tmdb_id:
            continue
            
        print(f"🔄 Analisando: {titulo} (TMDB: {tmdb_id})")
        
        # Fazendo a busca DIRETAMENTE na API do TMDB
        url = f"https://api.themoviedb.org/3/tv/{tmdb_id}?api_key={TMDB_API_KEY}&language=pt-BR"
        
        try:
            # Pede pro requests buscar os dados no TMDB
            resposta = await asyncio.to_thread(requests.get, url)
            dados_tmdb = resposta.json()
            
            # Checa se a chave da API deu erro (Ex: falta de .env local)
            if dados_tmdb.get('success') is False:
                print(f"   ⚠️ Erro de API TMDB: {dados_tmdb.get('status_message')}")
                continue

            if 'seasons' not in dados_tmdb:
                print(f"   ⚠️ TMDB não retornou os dados de temporadas. Pulando...")
                continue
                
            total_eps_tmdb = 0
            for season in dados_tmdb['seasons']:
                # Ignora a temporada 0 (Especiais/Bastidores)
                if season['season_number'] > 0: 
                    total_eps_tmdb += season.get('episode_count', 0)
                    
        except Exception as e:
            print(f"   ⚠️ Falha na requisição HTTP: {e}")
            continue
                
        # 3. Conta quantos episódios tem no Banco
        eps_baixados = await db.obter_episodios_baixados(serie_id)
        total_eps_banco = len(eps_baixados)
        
        # 4. O Veredito!
        if total_eps_banco < total_eps_tmdb:
            print(f"   ❌ INCOMPLETA! Temos {total_eps_banco} de {total_eps_tmdb}. Marcando como FALSE...")
            try:
                await asyncio.to_thread(
                    db.supabase.table('series').update({'is_complete': False}).eq('id', serie_id).execute
                )
                atualizadas_para_false += 1
            except Exception as e:
                print(f"   ⚠️ Erro ao atualizar o Supabase: {e}")
        else:
            print(f"   ✅ COMPLETA! Temos {total_eps_banco} de {total_eps_tmdb}. Tudo certo.")

        # Pausa de 0.3 segundos para não tomar block por excesso de requisições
        await asyncio.sleep(0.3)

    print("\n==================================================")
    print(f"🏁 Auditoria Finalizada! {atualizadas_para_false} séries marcadas como INCOMPLETAS.")
    print("==================================================")

if __name__ == "__main__":
    asyncio.run(auditar_acervo_series())