#
# Arquivo para gerenciar toda a interação com o banco de dados Supabase.
#

import os
import sys
import asyncio # <-- 1. IMPORTAMOS ASYNCIO
from supabase import create_client, Client
from config import SUPABASE_URL, SUPABASE_KEY
from datetime import datetime, timedelta # Para manipulação de datas

# Tenta criar a conexão com o Supabase.
try:
    supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
    print("Conexão com o Supabase estabelecida com sucesso!")
except Exception as e:
    print(f"Erro ao conectar com o Supabase: {e}")
    supabase = None

# 2. TODAS as funções que falam com o DB agora são 'async def'
# e usam 'await asyncio.to_thread'
async def get_or_create_user(user_id: int, first_name: str) -> dict | None:
    """
    Verifica se um usuário existe no DB pelo seu ID.
    Se não existir, cria um novo registro.
    Retorna os dados do usuário.
    """
    if not supabase:
        print("Conexão com Supabase não disponível.")
        return None

    # Tenta buscar o usuário na tabela 'users'
    response = await asyncio.to_thread(
        supabase.table('users').select('*').eq('user_id', user_id).execute
    )
    
    # Se a lista 'data' da resposta estiver vazia, o usuário não existe
    if not response.data:
        print(f"Usuário {user_id} não encontrado. Criando novo registro.")
        try:
            insert_response = await asyncio.to_thread(
                supabase.table('users').insert({
                    'user_id': user_id,
                    'first_name': first_name
                    # 'is_vip' já tem 'false' como padrão no banco de dados
                }).execute
            )
            
            if insert_response.data:
                return insert_response.data[0]
        except Exception as e:
            print(f"Erro ao inserir novo usuário: {e}")
            return None
    
    # Se o usuário já existe, retorna os dados dele
    print(f"Usuário {user_id} encontrado no banco de dados.")
    return response.data[0]

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
    
async def set_user_active_payment_id(user_id: int, payment_id: str):
    """Salva o ID do pagamento ativo para um usuário."""
    if not supabase: return
    try:
        await asyncio.to_thread(
            supabase.table('users').update({'active_payment_id': payment_id}).eq('user_id', user_id).execute
        )
    except Exception as e:
        print(f"Erro ao salvar active_payment_id: {e}")

async def clear_user_active_payment_id(user_id: int):
    """Limpa o ID do pagamento ativo de um usuário."""
    if not supabase: return
    try:
        await asyncio.to_thread(
            supabase.table('users').update({'active_payment_id': None}).eq('user_id', user_id).execute
        )
    except Exception as e:
        print(f"Erro ao limpar active_payment_id: {e}")

async def search_movies(query: str, limit: int = 10) -> list[dict]:
    """Busca filmes (lógica do seu handler)."""
    if not supabase: return []
    try:
        response = await asyncio.to_thread(
            supabase.table('movies')
            .select('movie_id, title, year, genre, poster_url')
            .ilike('title', f'%{query}%')
            .limit(limit)
            .execute
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
        response = await asyncio.to_thread(
            supabase.table('movies').select('*').eq('movie_id', movie_id).single().execute
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
    
async def log_movie_view(movie_id: int, user_id: int):
    """Registra um evento de visualização na tabela view_history."""
    if not supabase:
        return
    try:
        await asyncio.to_thread(
            supabase.table('view_history').insert({
                'movie_id': movie_id,
                'user_id': user_id
            }).execute
        )
        print(f"Registrada visualização para o filme ID {movie_id} pelo usuário {user_id}")
    except Exception as e:
        print(f"Erro ao registrar visualização: {e}")

async def get_trending(period_days: int = 0) -> list:
    """
    Busca os filmes mais vistos em um determinado período.
    period_days = 7 para semanal, 30 para mensal, 0 para geral.
    """
    if not supabase:
        return []
    try:
        response = await asyncio.to_thread(
            supabase.rpc('get_trending_movies', {'period_days': period_days}).execute
        )
        return response.data
    except Exception as e:
        print(f"Erro ao buscar trending: {e}")
        return []
    
async def set_user_as_vip(user_id: int, duration_days: int = 30) -> bool:
    """Atualiza o status de um usuário para VIP."""
    if not supabase:
        return False
    
    expiration_date = datetime.utcnow() + timedelta(days=duration_days)
    
    try:
        await asyncio.to_thread(
            supabase.table('users').update({
                'is_vip': True,
                'vip_until': expiration_date.isoformat()
            }).eq('user_id', user_id).execute
        )
        print(f"Usuário {user_id} agora é VIP por {duration_days} dias.")
        return True
    except Exception as e:
        print(f"Erro ao atualizar usuário para VIP: {e}")
        return False
    
async def is_user_vip(user_id: int) -> bool:
    """
    Verifica se o usuário é VIP E se a assinatura não expirou.
    Se a assinatura expirou, remove o status VIP automaticamente.
    """
    if not supabase:
        return False
    try:
        response = await asyncio.to_thread(
            supabase.table('users').select('is_vip, vip_until').eq('user_id', user_id).single().execute
        )
        user_data = response.data
        
        if not user_data or not user_data.get('is_vip'):
            return False

        vip_until_str = user_data.get('vip_until')
        if not vip_until_str:
            await clear_user_active_payment_id(user_id) # Esta função agora é async
            return False

        vip_expiration_date = datetime.fromisoformat(vip_until_str.replace('Z', '+00:00'))

        if datetime.now(vip_expiration_date.tzinfo) < vip_expiration_date:
            return True
        else:
            print(f"Assinatura VIP do usuário {user_id} expirou. Removendo acesso.")
            await asyncio.to_thread(
                supabase.table('users').update({'is_vip': False, 'vip_until': None}).eq('user_id', user_id).execute
            )
            return False

    except Exception as e:
        print(f"Erro ao verificar status VIP: {e}")
        return False
    
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
            supabase.table('series')
            .select('id, tmdb_id, title, description, poster_url, year, genre')
            .ilike('title', f'%{query}%')
            .limit(limit)
            .execute
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

async def get_episodes_for_season(season_id: int) -> (list[dict], dict):
    """
    Busca todos os episódios de uma temporada, ordenados.
    Também retorna os dados da temporada (para o botão "Voltar").
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
            
        # Pega os episódios
        episodes_response = await asyncio.to_thread(
            supabase.table('episodes')
            .select('id, episode_number, title, dubbed_file_id, subtitled_file_id')
            .eq('season_id', season_id)
            .order('episode_number', desc=False)
            .execute
        )
            
        return (episodes_response.data, season_response.data)
    except Exception as e:
        print(f"Erro ao buscar get_episodes_for_season: {e}")
        return ([], {})

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
                'id, episode_number, title, dubbed_file_id, subtitled_file_id, '
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
    