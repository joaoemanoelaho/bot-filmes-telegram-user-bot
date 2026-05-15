#
# Arquivo para gerenciar toda a interação com o banco de dados Supabase.
#

import os
import sys
import time
import asyncio # <-- 1. IMPORTAMOS ASYNCIO

current_dir = os.path.dirname(os.path.abspath(__file__))
parent_dir = os.path.dirname(current_dir)
sys.path.insert(0, parent_dir)

from supabase import create_client, Client
from config import SUPABASE_URL, SUPABASE_KEY
from datetime import datetime, timedelta, timezone # Para manipulação de datas

_bot_config_cache = None
_config_cache_time = 0

VIP_CACHE = {}
CACHE_TTL = 300 

# Tenta criar a conexão com o Supabase.
try:
    supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
    print("Conexão com o Supabase estabelecida com sucesso!")
except Exception as e:
    print(f"Erro ao conectar com o Supabase: {e}")
    supabase = None

async def get_bot_config() -> dict:
    """
    Busca as configurações do bot (preço, texto, etc.) do Supabase.
    Usa um cache de 60 segundos para evitar sobrecarga.
    """
    global _bot_config_cache, _config_cache_time
    
    # Cache de 60 segundos
    now = time.time()
    if _bot_config_cache and (now - _config_cache_time < 60):
        return _bot_config_cache

    if not supabase: 
        # Retorna defaults se o DB falhar
        return {
            'vip_price': 4.99, 'vip_anchor_price': 14.99,
            'vip_duration_days': 7, 'vip_sales_text': 'Seja VIP! (Erro de DB)'
        }
        
    try:
        response = await asyncio.to_thread(
            supabase.table('bot_config').select('*').eq('id', 1).single().execute
        )
        if response.data:
            _bot_config_cache = response.data
            _config_cache_time = now
            return response.data
        else:
            # Se a tabela estiver vazia, retorna defaults
             return {
                'vip_price': 4.99, 'vip_anchor_price': 14.99,
                'vip_duration_days': 7, 'vip_sales_text': 'Seja VIP! (Tabela Vazia)'
            }
    except Exception as e:
        print(f"Erro ao buscar config do bot: {e}")
        return {} # Falha segura

async def set_bot_config_value(key: str, value) -> bool:
    """Atualiza um valor específico na tabela de configuração."""
    global _bot_config_cache, _config_cache_time
    if not supabase: return False
    try:
        await asyncio.to_thread(
            supabase.table('bot_config').update({key: value}).eq('id', 1).execute
        )
        _bot_config_cache = None # Limpa o cache
        _config_cache_time = 0
        return True
    except Exception as e:
        print(f"Erro ao salvar config do bot: {e}")
        return False

# 2. TODAS as funções que falam com o DB agora são 'async def'
# e usam 'await asyncio.to_thread'
async def get_or_create_user(user_id: int, first_name: str) -> tuple[dict, bool]:
    """
    Verifica se um usuário existe no DB pelo seu ID.
    Se não existir, cria um novo registro.
    Se existir mas estiver inativo, REATIVA O USUÁRIO.
    Retorna os dados do usuário.
    """
    if not supabase:
        print("Conexão com Supabase não disponível.")
        return None, False

    # Tenta buscar o usuário na tabela 'users'
    response = await asyncio.to_thread(
        supabase.table('users').select('*').eq('user_id', user_id).execute
    )
    
    # Se a lista 'data' da resposta estiver vazia, o usuário não existe
    if not response.data:
        print(f"🎉 Novo usuário detectado: {user_id}. Aplicando Trial de 4h...")
        
        # Define 4 horas a partir de agora (UTC)
        trial_end = datetime.utcnow() + timedelta(hours=4)
        
        try:
            insert_response = await asyncio.to_thread(
                supabase.table('users').insert({
                    'user_id': user_id,
                    'first_name': first_name,
                    'is_vip': True,                     # <--- JÁ NASCE VIP
                    'vip_until': trial_end.isoformat(), # <--- VALIDADE DE 4H
                    'is_active': True
                }).execute
            )
            
            if insert_response.data:
                # Atualiza o Cache IMEDIATAMENTE para ele não ser bloqueado
                current_time = time.time()
                if 'VIP_CACHE' in globals():
                    globals()['VIP_CACHE'][user_id] = {
                        'status': True,
                        'expires_at': current_time + (4 * 3600) # Cache de 4h
                    }
                
                return insert_response.data[0], True # True indica que é NOVO
        except Exception as e:
            print(f"Erro ao inserir novo usuário trial: {e}")
            return None, False
    
    # Se o usuário já existe, verifica se está ativo
    print(f"Usuário {user_id} encontrado no banco de dados.")
    user_data = response.data[0]
    
    # --- LÓGICA DE REATIVAÇÃO ---
    # Se ele estava inativo (False), reativa ele (True)
    # Usamos .get('is_active', True) para ser seguro caso a coluna ainda não exista (ela vai defaultar para True)
    if not user_data.get('is_active', True):
        print(f"Usuário {user_id} estava inativo. Reativando...")
        try:
            # Atualiza o status no banco para True
            await asyncio.to_thread(
                supabase.table('users').update({'is_active': True})
                .eq('user_id', user_id).execute
            )
            # Atualiza o dict local que vamos retornar
            user_data['is_active'] = True
        except Exception as e:
            print(f"Erro ao REATIVAR usuário {user_id}: {e}")
    # --- FIM DA LÓGICA ---
    
    return user_data, False

async def get_user_details(user_id: int):
    """Busca todos os detalhes de um usuário."""
    if not supabase: return None
    try:
        response = await asyncio.to_thread(
            supabase.table('users').select('*').eq('user_id', user_id).single().execute
        )
        return response.data
    except Exception as e:
        print(f"Erro ao buscar detalhes do usuário: {e}")
        return None
    
async def set_user_active_payment_id(user_id: int, payment_id: str, qr_message_id: int):
    """Salva o ID do pagamento ativo para um usuário."""
    if not supabase: return
    try:
        await asyncio.to_thread(
            supabase.table('users').update({'active_payment_id': payment_id, "qr_message_id": qr_message_id}).eq('user_id', user_id).execute
        )
    except Exception as e:
        print(f"Erro ao salvar active_payment_id: {e}")

async def clear_user_active_payment_id(user_id: int):
    """Limpa o ID do pagamento ativo de um usuário."""
    if not supabase: return
    try:
        await asyncio.to_thread(
            supabase.table('users').update({'active_payment_id': None, 'qr_message_id': None}).eq('user_id', user_id).execute
        )
    except Exception as e:
        print(f"Erro ao limpar active_payment_id: {e}")

async def search_movies(query: str, limit: int = 10) -> list[dict]:
    """Busca filmes (lógica do seu handler)."""
    if not supabase: return []
    try:
        response = await asyncio.to_thread(
            supabase.rpc('search_movies_fts', {
                'query': query,
                'result_limit': limit
            }).execute
        )
        return response.data
    except Exception as e:
        print(f"Erro search_movies: {e}")
        return []
    
async def get_movie_by_id(movie_id: int) -> dict | None:
    """Busca um filme específico no banco de dados pelo seu ID."""
    if not supabase:
        return None
    try:
        query = "*, dubbed_msg_id, subtitled_msg_id"
        response = await asyncio.to_thread(
            supabase.table('movies').select(query).eq('movie_id', movie_id).single().execute
        )
        return response.data
    except Exception as e:
        print(f"Erro ao buscar filme por ID: {e}")
        return None
    
async def add_movie(movie_data: dict) -> bool:
    """
    Adiciona um novo filme ao banco de dados.
    'movie_data' deve ser um dicionário com todas as colunas da tabela 'movies'.
    Retorna True se foi bem-sucedido, False caso contrário.
    """
    if not supabase:
        return False
    
    try:
        await asyncio.to_thread(
            supabase.table('movies').insert(movie_data).execute
        )
        print(f"Filme '{movie_data['title']}' adicionado ao banco de dados.")
        return True
    except Exception as e:
        print(f"Erro ao adicionar filme no Supabase: {e}")
        return False
    
async def add_request(user_id: int, title: str) -> bool:
    """Adiciona um novo pedido de filme/série ao banco de dados."""
    if not supabase:
        return False
    
    try:
        await asyncio.to_thread(
            supabase.table('requests').insert({
                'user_id': user_id,
                'requested_title': title
            }).execute
        )
        print(f"Pedido '{title}' do usuário {user_id} salvo no banco de dados.")
        return True
    except Exception as e:
        print(f"Erro ao salvar pedido no Supabase: {e}")
        return False
    
async def log_movie_view(user_id: int, movie_id: int = None, series_id: int = None):
    """
    Registra visualização de Filme OU Série de forma inteligente.
    Impede que maratonas gerem dezenas de visualizações no mesmo dia,
    nivelando Séries e Filmes no Top 10.
    """
    if not supabase: return
    try:
        data = {'user_id': user_id}
        
        # Define qual ID estamos procurando para checar o anti-flood
        coluna_id = ""
        valor_id = 0
        
        if movie_id:
            data['movie_id'] = movie_id
            coluna_id = 'movie_id'
            valor_id = movie_id
        elif series_id:
            data['series_id'] = series_id
            coluna_id = 'series_id'
            valor_id = series_id
        else:
            return # Se não mandou nenhum dos dois, aborta.

        # --- SISTEMA ANTI-FLOOD / MARATONA (6 HORAS) ---
        # Se o usuário tentar registrar o mesmo filme ou episódios da MESMA SÉRIE
        # dentro de 6 horas, o banco ignora. Só conta o primeiro episódio que ele deu play.
        
        tempo_limite = datetime.utcnow() - timedelta(hours=6)
        limite_str = tempo_limite.isoformat()

        # Verifica se ele já assistiu isso recentemente
        ja_assistiu_recente = await asyncio.to_thread(
            supabase.table('view_history')
            .select('id', count='exact')
            .eq('user_id', user_id)
            .eq(coluna_id, valor_id)
            .gte('created_at', limite_str) # gte = Greater Than or Equal (Maior ou igual à data limite)
            .execute
        )
        
        count = ja_assistiu_recente.count if ja_assistiu_recente.count is not None else len(ja_assistiu_recente.data)
        
        if count > 0:
            # print(f"🚫 View de {coluna_id} {valor_id} ignorada pelo Anti-Flood (Maratona).")
            return # Já assistiu hoje, não conta mais pontos pro Top 10.
        # -----------------------------------------------

        # Se passou no Anti-Flood, registra a view normalmente
        await asyncio.to_thread(
            supabase.table('view_history').insert(data).execute
        )
        # print(f"✅ Visualização registrada para {data}")
        
    except Exception as e:
        print(f"Erro ao registrar view: {e}")

async def get_trending(period_days: int = 0) -> list:
    """Busca o Top 10 Misto (Filmes e Séries)."""
    if not supabase: return []
    try:
        response = await asyncio.to_thread(
            supabase.rpc('get_trending_mixed', {'period_days': period_days}).execute
        )
        return response.data
    except Exception as e:
        print(f"Erro ao buscar trending misto: {e}")
        return []
    
async def set_user_as_vip(user_id: int, duration_days: int = 30) -> bool:
    """Atualiza o status de um usuário para VIP e JÁ ATUALIZA O CACHE."""
    if not supabase:
        return False
    
    expiration_date = datetime.utcnow() + timedelta(days=duration_days)
    
    try:
        # 1. Atualiza no Banco de Dados (Supabase)
        await asyncio.to_thread(
            supabase.table('users').update({
                'is_vip': True,
                'vip_until': expiration_date.isoformat()
            }).eq('user_id', user_id).execute
        )
        
        # 2. Atualiza na Memória RAM (Cache) IMEDIATAMENTE 🚀
        # Assim o usuário não precisa esperar o tempo do cache vencer!
        VIP_CACHE[user_id] = {
            'status': True,
            'expires_at': time.time() + CACHE_TTL
        }
        return True
    except Exception as e:
        print(f"Erro ao atualizar usuário para VIP: {e}")
        return False
    
async def is_user_vip(user_id: int) -> bool:
    """
    Verifica se o usuário é VIP com Cache para velocidade máxima.
    Só consulta o banco de dados se o cache expirar ou não existir.
    """
    
    # 1. VERIFICAÇÃO RÁPIDA (MEMÓRIA RAM) 🚀
    current_time = time.time()
    
    if user_id in VIP_CACHE:
        cached_data = VIP_CACHE[user_id]
        # Se o cache ainda é válido (não passou de 5 min), retorna o valor salvo
        if current_time < cached_data['expires_at']:
            return cached_data['status']

    # 2. CONSULTA AO BANCO (LENTA - Só acontece a cada 5 min) 🐢
    if not supabase:
        return False

    is_vip_result = False # Assume falso até provar o contrário

    try:
        # Busca no banco
        response = await asyncio.to_thread(
            supabase.table('users').select('is_vip, vip_until').eq('user_id', user_id).execute
        )
        
        # Verifica se retornou dados
        if response.data and len(response.data) > 0:
            user_data = response.data[0]
            
            # Se no banco diz que é VIP, vamos conferir a data
            if user_data.get('is_vip'):
                vip_until_str = user_data.get('vip_until')
                
                if vip_until_str:
                    # Converte string ISO para objeto de data
                    vip_expiration_date = datetime.fromisoformat(vip_until_str.replace('Z', '+00:00'))
                    now = datetime.now(vip_expiration_date.tzinfo)

                    if now < vip_expiration_date:
                        # ✅ É VIP e a data está válida
                        is_vip_result = True
                    else:
                        # ❌ Expirou! Atualiza o banco para remover VIP
                        print(f"📉 Assinatura de {user_id} expirou. Removendo...")
                        asyncio.create_task(asyncio.to_thread(
                            supabase.table('users').update({'is_vip': False, 'vip_until': None}).eq('user_id', user_id).execute
                        ))
                        is_vip_result = False
                else:
                    # É VIP mas não tem data?? Removemos por segurança (lógica original)
                    if 'clear_user_active_payment_id' in globals():
                        await clear_user_active_payment_id(user_id)
                    is_vip_result = False
            else:
                is_vip_result = False
        else:
            is_vip_result = False

    except Exception as e:
        print(f"⚠️ Erro ao verificar VIP no banco: {e}")
        # Em caso de erro de conexão, se tivermos um cache antigo, usamos ele por segurança?
        # Ou retornamos False. Vamos retornar False para evitar liberar acesso indevido.
        is_vip_result = False

    # 3. SALVA NO CACHE PARA A PRÓXIMA VEZ 💾
    VIP_CACHE[user_id] = {
        'status': is_vip_result,
        'expires_at': current_time + CACHE_TTL
    }

    return is_vip_result

async def find_movie_by_title_and_year(title: str, year: int) -> dict | None:
    """Procura por um filme no banco de dados pelo título e ano."""
    if not supabase:
        return None
    try:
        response = await asyncio.to_thread(
            supabase.table('movies').select('*').eq('title', title).eq('year', year).single().execute
        )
        return response.data
    except Exception:
        return None

async def update_movie_file_id(movie_id: int, file_id: str, audio_type: str):
    """Atualiza o file_id de um filme existente (dublado ou legendado)."""
    if not supabase:
        return False
    
    column_to_update = 'dubbed_file_id' if audio_type.upper() == 'DUB' else 'subtitled_file_id'
    
    try:
        await asyncio.to_thread(
            supabase.table('movies').update({column_to_update: file_id}).eq('movie_id', movie_id).execute
        )
        print(f"Atualizado {column_to_update} para o filme ID: {movie_id}")
        return True
    except Exception as e:
        print(f"Erro ao atualizar file_id: {e}")
        return False
    
async def filter_existing_titles(titles: list[str]) -> list[str]:
    """
    Recebe uma lista de títulos de filmes e retorna uma sub-lista 
    contendo apenas os títulos que já existem no banco de dados.
    """
    if not titles:
        return []
    try:
        response = await asyncio.to_thread(
            supabase.table('movies').select('title').in_('title', titles).execute
        )
        existing_titles = [movie['title'] for movie in response.data]
        return existing_titles
    except Exception as e:
        print(f"Erro ao filtrar títulos existentes no Supabase: {e}")
        return []

async def search_series_by_title(query: str, limit: int = 10) -> list[dict]:
    """
    Busca séries no banco de dados local pelo título (tabela 'series').
    """
    if not supabase:
        print("Conexão com Supabase não disponível.")
        return []
    try:
        response = await asyncio.to_thread(
            supabase.rpc('search_series_fts', {
                'query': query,
                'result_limit': limit
            }).execute
        )
        return [{'series_id': s['id'], **{k: v for k, v in s.items() if k != 'id'}} for s in response.data]
        
    except Exception as e:
        print(f"Erro ao buscar séries no Supabase: {e}")
    return []

async def get_series_by_id(series_id: int) -> dict | None:
    """Busca UMA série pelo ID interno do nosso banco."""
    if not supabase: return None
    try:
        response = await asyncio.to_thread(
            supabase.table('series')
            .select('*')
            .eq('id', series_id)
            .single()
            .execute
        )
        return response.data
    except Exception as e:
        print(f"Erro ao buscar get_series_by_id: {e}")
        return None

async def get_seasons_for_series(series_id: int) -> list[dict]:
    """Busca todas as temporadas de uma série, ordenadas."""
    if not supabase: return []
    try:
        response = await asyncio.to_thread(
            supabase.table('seasons')
            .select('id, season_number, name')
            .eq('series_id', series_id)
            .order('season_number', desc=False)
            .execute
        )
        return response.data
    except Exception as e:
        print(f"Erro ao buscar get_seasons_for_series: {e}")
        return []

#
# --- CORREÇÃO (v5.13): Função agora aceita limit e offset ---
#
async def get_episodes_for_season(season_id: int, limit: int = 48, offset: int = 0) -> (list[dict], dict):
    """
    Busca uma 'página' de episódios de uma temporada, ordenados.
    Também retorna os dados da temporada.
    """
    if not supabase: return ([], {})
    try:
        # Pega a temporada (para saber o series_id e voltar)
        season_response = await asyncio.to_thread(
            supabase.table('seasons')
            .select('id, season_number, series_id')
            .eq('id', season_id)
            .single()
            .execute
        )
        
        if not season_response.data:
            return ([], {})
            
        # Pega os episódios com paginação
        episodes_response = await asyncio.to_thread(
            supabase.table('episodes')
            .select('id, episode_number, title, dubbed_file_id, subtitled_file_id')
            .eq('season_id', season_id)
            .order('episode_number', desc=False)
            .range(offset, offset + limit - 1)  # <-- A MÁGICA DA PAGINAÇÃO
            .execute
        )
            
        return (episodes_response.data, season_response.data)
    except Exception as e:
        print(f"Erro ao buscar get_episodes_for_season: {e}")
        return ([], {})
# --- FIM DA CORREÇÃO ---

async def get_episode_by_id(episode_id: int) -> dict | None:
    """Busca UM episódio pelo ID interno do nosso banco."""
    if not supabase: return None
    try:
        response = await asyncio.to_thread(
            supabase.table('episodes')
            .select('id, season_id, dubbed_file_id, subtitled_file_id, title, episode_number')
            .eq('id', episode_id)
            .single()
            .execute
        )
        return response.data
    except Exception as e:
        print(f"Erro ao buscar get_episode_by_id: {e}")
        return None
    
async def get_full_episode_details(episode_id: int) -> dict | None:
    """
    Busca todos os detalhes de um episódio, temporada e série
    de uma só vez usando JOINs implícitos do Supabase.
    Substitui 3-4 chamadas de DB por apenas 1.
    """
    if not supabase: return None
    try:
        response = await asyncio.to_thread(
            supabase.table('episodes')
            .select(
                'id, episode_number, title, dubbed_file_id, subtitled_file_id, dubbed_msg_id, subtitled_msg_id, '
                'seasons ( '
                '   id, season_number, series_id, '
                '   series ( '
                '       id, title'
                '   )'
                ')'
            )
            .eq('id', episode_id)
            .single()
            .execute
        )
        return response.data
    
    except Exception as e:
        print(f"Erro ao buscar get_full_episode_details: {e}")
        return None
    
#
# --- CORREÇÃO (v5.13): Funções rápidas para navegação de episódios ---
#
async def get_neighbor_episode(season_id, current_number, direction='next'):
    """
    Busca o episódio vizinho (anterior ou próximo) sem gerar erro se não existir.
    """
    try:
        query = supabase.table("episodes").select("id, season_id, episode_number")\
            .eq("season_id", season_id)
        
        if direction == 'next':
            # Busca o próximo (número maior)
            query = query.gt("episode_number", current_number)\
                .order("episode_number", desc=False)\
                .limit(1)
        else:
            # Busca o anterior (número menor)
            query = query.lt("episode_number", current_number)\
                .order("episode_number", desc=True)\
                .limit(1)
        
        # Executa a query
        # O uso de to_thread evita bloquear o bot enquanto espera o banco
        response = await asyncio.to_thread(query.execute)
        
        # Se tiver dados na lista, retorna o primeiro. Se não, retorna None.
        if response.data and len(response.data) > 0:
            return response.data[0]
            
        return None

    except Exception as e:
        # Se for um erro real de conexão, ele avisa.
        # Mas "não encontrado" não vai mais cair aqui.
        print(f"⚠️ Erro ao buscar vizinho ({direction}): {e}")
        return None
    
async def set_user_inactive(user_id: int):
    """Marca um usuário como inativo (ex: bloqueou o bot)."""
    if not supabase: 
        return
    try:
        await asyncio.to_thread(
            supabase.table('users').update({'is_active': False})
            .eq('user_id', user_id).execute
        )
        print(f"Usuário {user_id} marcado como INATIVO.")
    except Exception as e:
        print(f"Erro ao marcar usuário {user_id} como inativo: {e}")

async def get_active_users() -> list[dict]:
    """Retorna uma lista de user_ids de todos os usuários ATIVOS."""
    if not supabase: 
        return []
    try:
        response = await asyncio.to_thread(
            supabase.table('users').select('user_id')
            .eq('is_active', True).execute
        )
        return response.data
    except Exception as e:
        print(f"Erro ao buscar usuários ativos: {e}")
        return []
    
async def get_all_movie_file_ids() -> list[dict]:
    """Busca todos os file_ids da tabela 'movies'."""
    if not supabase: 
        return []
    try:
        response = await asyncio.to_thread(
            supabase.table('movies')
            .select('movie_id, title, dubbed_file_id, subtitled_file_id')
            .execute
        )
        return response.data
    except Exception as e:
        print(f"Erro ao buscar todos os filmes: {e}")
        return []

async def get_all_episode_file_ids() -> list[dict]:
    """Busca todos os file_ids da tabela 'episodes'."""
    if not supabase: 
        return []
    try:
        response = await asyncio.to_thread(
            supabase.table('episodes')
            .select('id, episode_number, dubbed_file_id, subtitled_file_id')
            .execute
        )
        return response.data
    except Exception as e:
        print(f"Erro ao buscar todos os episódios: {e}")
        return []
    
async def update_movie_file_id_only(movie_id: int, new_file_id: str, audio_type: str) -> bool:
    """
    Atualiza APENAS o file_id de um filme (usado pelo bot de usuário).
    """
    if not supabase:
        return False
    
    col = 'dubbed_file_id' if audio_type.upper() == 'DUB' else 'subtitled_file_id'
    
    try:
        await asyncio.to_thread(
            supabase.table('movies').update({col: new_file_id})
            .eq('movie_id', movie_id)
            .execute
        )
        print(f"✅ [Auto-Cura] File ID do filme {movie_id} ({audio_type}) atualizado.")
        return True
    except Exception as e:
        print(f"❌ [Auto-Cura] Erro ao atualizar file_id do filme {movie_id}: {e}")
        return False

async def update_episode_file_id_only(episode_id: int, new_file_id: str, audio_type: str) -> bool:
    """
    Atualiza APENAS o file_id de um episódio (usado pelo bot de usuário).
    """
    if not supabase:
        return False
    
    col = 'dubbed_file_id' if audio_type.upper() == 'DUB' else 'subtitled_file_id'
    
    try:
        await asyncio.to_thread(
            supabase.table('episodes').update({col: new_file_id})
            .eq('id', episode_id) # O ID da tabela episodes é 'id'
            .execute
        )
        print(f"✅ [Auto-Cura] File ID do episódio {episode_id} ({audio_type}) atualizado.")
        return True
    except Exception as e:
        print(f"❌ [Auto-Cura] Erro ao atualizar file_id do episódio {episode_id}: {e}")
        return False
    
# =================================================================
# === SISTEMA DE FAVORITOS (SUPABASE) ===
# =================================================================

async def add_favorite(user_id: int, data: dict) -> str:
    """
    Adiciona aos favoritos respeitando o limite de 10.
    Retorna: 'success', 'limit_reached', 'exists' ou 'error'
    """
    if not supabase: return "error"

    try:
        # 1. Checa o limite (Count)
        count_response = await asyncio.to_thread(
            supabase.table('favorites')
            .select('unique_code', count='exact')
            .eq('user_id', user_id)
            .execute
        )
        count = count_response.count if count_response.count is not None else len(count_response.data)
        
        if count >= 10:
            # Verifica se JÁ existe antes de dar erro de limite (para não bloquear remoção/toggle)
            exists_response = await asyncio.to_thread(
                supabase.table('favorites').select('unique_code').eq('user_id', user_id).eq('unique_code', data['unique_code']).execute
            )
            if exists_response.data:
                return "exists" # Já existe, ok
            return "limit_reached"

        # 2. Adiciona (upsert=False para falhar se existir, ou ignoramos erro de PK)
        await asyncio.to_thread(
            supabase.table('favorites').upsert({
                'user_id': user_id,
                'unique_code': data['unique_code'],
                'media_type': data['media_type'],
                'title': data['title'],
                'file_id': data['file_id'],
                'message_id': data['message_id'],
                'channel_id': data['channel_id']
            }, on_conflict='user_id, unique_code').execute
        )
        return "success"

    except Exception as e:
        print(f"Erro ao salvar favorito no Supabase: {e}")
        return "error"

async def remove_favorite(user_id: int, unique_code: str):
    """Remove um item da lista."""
    if not supabase: return
    try:
        await asyncio.to_thread(
            supabase.table('favorites')
            .delete()
            .eq('user_id', user_id)
            .eq('unique_code', unique_code)
            .execute
        )
    except Exception as e:
        print(f"Erro ao remover favorito: {e}")

async def get_user_favorites(user_id: int) -> list[dict]:
    """Retorna a lista de favoritos do usuário."""
    if not supabase: return []
    try:
        response = await asyncio.to_thread(
            supabase.table('favorites')
            .select('*')
            .eq('user_id', user_id)
            .order('added_at', desc=True)
            .execute
        )
        return response.data
    except Exception as e:
        print(f"Erro ao buscar favoritos: {e}")
        return []

async def get_favorite_item(user_id: int, unique_code: str) -> dict | None:
    """Retorna os detalhes de um item específico."""
    if not supabase: return None
    try:
        response = await asyncio.to_thread(
            supabase.table('favorites')
            .select('*')
            .eq('user_id', user_id)
            .eq('unique_code', unique_code)
            .single()
            .execute
        )
        return response.data
    except Exception as e:
        # print(f"Erro get_favorite_item: {e}")
        return None

async def is_favorite(user_id: int, unique_code: str) -> bool:
    """Retorna True se já for favorito."""
    if not supabase: return False
    try:
        response = await asyncio.to_thread(
            supabase.table('favorites')
            .select('unique_code')
            .eq('user_id', user_id)
            .eq('unique_code', unique_code)
            .execute
        )
        return len(response.data) > 0
    except Exception:
        return False

async def get_pending_requests():
    """Busca todos os pedidos com status 'pending'."""
    try:
        response = supabase.table("requests").select("*").eq("status", "pending").execute()
        return response.data if response.data else []
    except Exception as e:
        print(f"Erro ao buscar pedidos pendentes: {e}")
        return []

async def update_request_status(req_id, new_status):
    """
    Atualiza o status usando a coluna 'request_id' (baseado nos seus logs).
    """
    try:
        # PASSO 1: Busca os dados atuais (Título e User ID)
        # Atenção: Usando 'request_id' pois foi o que apareceu no seu log de erro anterior
        data_res = supabase.table("requests").select("*").eq("request_id", req_id).execute()
        
        if not data_res.data:
            print(f"Pedido {req_id} não encontrado no banco.")
            return None

        current_data = data_res.data[0]

        # PASSO 2: Atualiza o status
        supabase.table("requests").update({"status": new_status}).eq("request_id", req_id).execute()
        
        return current_data

    except Exception as e:
        print(f"Erro database update: {e}")
        return None
    
async def get_request_by_id(req_id: int) -> dict | None:
    """
    Busca os dados de um pedido pelo ID para podermos notificar o usuário
    antes de excluir a linha.
    """
    if not supabase: return None
    try:
        response = await asyncio.to_thread(
            supabase.table('requests')
            .select('*')
            .eq('request_id', req_id)
            .single()
            .execute
        )
        return response.data
    except Exception as e:
        print(f"Erro ao buscar pedido {req_id}: {e}")
        return None

async def delete_request(req_id: int) -> bool:
    """
    Deleta fisicamente a linha do pedido na tabela 'requests'.
    """
    if not supabase: return False
    try:
        await asyncio.to_thread(
            supabase.table('requests')
            .delete()
            .eq('request_id', req_id)
            .execute
        )
        print(f"Pedido {req_id} deletado do banco com sucesso.")
        return True
    except Exception as e:
        print(f"Erro ao deletar pedido {req_id}: {e}")
        return False

async def check_content_exists_by_tmdb_id(tmdb_id: int, media_type: str) -> dict | None:
    """
    Verifica se um filme ou série já existe no catálogo pelo TMDB ID.
    Retorna o título se existir, ou None se não existir.
    """
    if not supabase: return None
    
    # Define qual tabela olhar baseada no tipo retornado pela API
    table_name = 'movies' if media_type == 'movie' else 'series'
    
    try:
        # Busca apenas o título para confirmar e mostrar ao usuário
        # .limit(1) garante que a busca pare assim que encontrar o primeiro
        response = await asyncio.to_thread(
            supabase.table(table_name)
            .select('title, is_complete')
            .eq('tmdb_id', tmdb_id)
            .execute
        )
        
        # Se retornou alguma linha, significa que já temos!
        if response.data and len(response.data) > 0:
            return response.data[0] # Retorna {'title': 'Nome do Filme'}
            
        return None
    except Exception as e:
        # Se a coluna 'tmdb_id' não existir ou der outro erro, apenas loga e segue
        print(f"⚠️ Erro ao verificar existência no catálogo ({table_name}): {e}")
        return None

# Adicione isso no database.py
async def delete_user(user_id: int):
    """
    Remove definitivamente um usuário do banco (para quem bloqueou o bot).
    """
    if not supabase: return
    try:
        await asyncio.to_thread(
            supabase.table('users').delete().eq('user_id', user_id).execute
        )
        print(f"💀 Usuário {user_id} removido do banco (Bloqueou o bot).")
    except Exception as e:
        print(f"Erro ao deletar usuário {user_id}: {e}")
    
async def get_mixed_recommendations(genre: str, limit: int = 5) -> list[dict]:
    """Busca Filmes E Séries do banco com gênero similar."""
    if not supabase or not genre: return []
    try:
        # Pega o primeiro gênero (ex: "Ação" de "Ação, Aventura")
        main_genre = genre.split(',')[0].strip()
        
        response = await asyncio.to_thread(
            supabase.rpc('get_mixed_recommendations', {
                'target_genre': main_genre,
                'limit_count': limit
            }).execute
        )
        return response.data
    except Exception as e:
        print(f"Erro ao buscar recomendações mistas: {e}")
        return []
    
async def buscar_usuarios_vencendo_em(dias: int):
    """
    Busca usuários cujo 'vip_until' expira em exatamente X dias.
    """
    try:
        # Pega a data e hora atual em UTC (já que o banco usa timestamptz)
        hoje = datetime.now(timezone.utc)
        
        # Calcula a janela de 24 horas para o dia alvo
        data_alvo_inicio = hoje + timedelta(days=dias)
        data_alvo_fim = data_alvo_inicio + timedelta(days=1)
        
        str_inicio = data_alvo_inicio.isoformat()
        str_fim = data_alvo_fim.isoformat()

        # Vai no Supabase e pega quem é VIP e vence nessa janela de tempo
        resposta = await asyncio.to_thread(
            supabase.table("users").select("user_id, vip_until, first_name")
            .eq("is_vip", True)
            .gte("vip_until", str_inicio)
            .lt("vip_until", str_fim)
            .execute
        )
        return resposta.data
        
    except Exception as e:
        print(f"❌ Erro ao buscar vencimentos de {dias} dias: {e}")
        return []

def _limpar_cache_usuario(user_id: int):
    """
    Limpa a memória (cache) do usuário.
    Força o bot a ir no banco de dados no próximo clique.
    """
    global VIP_CACHE
    if user_id in VIP_CACHE:
        del VIP_CACHE[user_id]
        print(f"🧹 [Cache] Memória do usuário {user_id} limpa com sucesso!")
        
async def obter_episodios_baixados(series_id):
    """Retorna uma lista de tuplas (temporada, episodio) que já estão no banco."""
    try:
        # 1. Primeiro, acha todas as temporadas que pertencem a essa série
        seasons_resp = await asyncio.to_thread(
            supabase.table('seasons')
            .select('id, season_number')
            .eq('series_id', series_id)
            .execute
        )
        
        if not seasons_resp.data:
            return []
            
        # Cria um "mapa" para saber qual ID pertence a qual número de temporada
        season_map = {s['id']: s['season_number'] for s in seasons_resp.data}
        season_ids = list(season_map.keys())
        
        # 2. Agora sim, busca os episódios que pertencem a essas temporadas
        episodes_resp = await asyncio.to_thread(
            supabase.table('episodes')
            .select('season_id, episode_number')
            .in_('season_id', season_ids)
            .execute
        )
        
        # 3. Junta as peças e devolve a lista de tuplas (temporada, episodio)
        baixados = []
        if episodes_resp.data:
            for ep in episodes_resp.data:
                temp_num = season_map.get(ep['season_id'])
                ep_num = ep['episode_number']
                if temp_num is not None and ep_num is not None:
                    baixados.append((temp_num, ep_num))
                    
        return baixados
    except Exception as e:
        print(f"Erro ao obter episódios baixados: {e}")
        return []
    
async def toggle_subscription(user_id: int, tmdb_id: int):
    """Inscreve ou desinscreve o usuário das notificações de uma série."""
    # Verifica se já está inscrito
    resp = await asyncio.to_thread(
        supabase.table('series_subscriptions')
        .select('*')
        .eq('user_id', user_id)
        .eq('tmdb_id', tmdb_id)
        .execute
    )
    
    if resp.data:
        # Se achou, deleta (Desinscreve)
        await asyncio.to_thread(
            supabase.table('series_subscriptions')
            .delete()
            .eq('user_id', user_id)
            .eq('tmdb_id', tmdb_id)
            .execute
        )
        return False # Retorna falso para sabermos que ele tirou a inscrição
    else:
        # Se não achou, insere (Inscreve)
        await asyncio.to_thread(
            supabase.table('series_subscriptions')
            .insert({'user_id': user_id, 'tmdb_id': tmdb_id})
            .execute
        )
        return True # Retorna verdadeiro para sabermos que ele ativou

async def is_subscribed(user_id: int, tmdb_id: int):
    """Verifica se o botão deve mostrar '🔔 Ativado' ou '🔕 Desativado'"""
    resp = await asyncio.to_thread(
        supabase.table('series_subscriptions')
        .select('*')
        .eq('user_id', user_id)
        .eq('tmdb_id', tmdb_id)
        .execute
    )
    return len(resp.data) > 0

async def get_subscribers(tmdb_id: int):
    """Puxa a lista de todo mundo para o Downloader mandar a mensagem"""
    resp = await asyncio.to_thread(
        supabase.table('series_subscriptions')
        .select('user_id')
        .eq('tmdb_id', tmdb_id)
        .execute
    )
    return [row['user_id'] for row in resp.data] if resp.data else []

async def obter_usuarios_broadcast(alvo="all"):
    """Busca usuários no Supabase filtrando por VIP, FREE ou TODOS."""
    if not supabase: return []
    try:
        query = supabase.table('users').select('user_id').eq('is_active', True)
        
        if alvo == "vip":
            query = query.eq('is_vip', True)
        elif alvo == "free":
            query = query.eq('is_vip', False) # Ou is_vip is null dependendo do seu banco
            
        response = await asyncio.to_thread(query.execute)
        
        if response.data:
            return [int(user['user_id']) for user in response.data]
        return []
    except Exception as e:
        print(f"❌ Erro ao buscar usuários para broadcast: {e}")
        return []