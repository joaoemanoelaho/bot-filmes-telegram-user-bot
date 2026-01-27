import re
import asyncio 
from tmdbv3api import TMDb, Movie, TV # <--- ADICIONEI TV
from config import TMDB_API_KEY

# Configuração da API
tmdb = TMDb()
tmdb.api_key = TMDB_API_KEY
tmdb.language = 'pt-BR' 
movie_search = Movie()
tv_search = TV() # <--- INICIALIZA BUSCA DE SÉRIES

async def check_tmdb_id(tmdb_id: str) -> dict | None:
    """
    Verifica se um ID existe no TMDB (primeiro tenta Filme, depois Série).
    Retorna os dados formatados ou None.
    """
    try:
        # Converter para int, se falhar não é ID válido
        t_id = int(tmdb_id)
        
        # 1. Tenta buscar como FILME
        try:
            details = await asyncio.to_thread(movie_search.details, t_id)
            if hasattr(details, 'title'):
                return {
                    "title": details.title,
                    "year": details.release_date.split('-')[0] if hasattr(details, 'release_date') and details.release_date else "N/A",
                    "type": "movie",
                    "tmdb_id": t_id,
                    "overview": getattr(details, 'overview', 'Sem sinopse.')
                }
        except Exception:
            pass # Não é filme, continua para tentar série...

        # 2. Tenta buscar como SÉRIE
        try:
            details = await asyncio.to_thread(tv_search.details, t_id)
            if hasattr(details, 'name'):
                return {
                    "title": details.name,
                    "year": details.first_air_date.split('-')[0] if hasattr(details, 'first_air_date') and details.first_air_date else "N/A",
                    "type": "tv",
                    "tmdb_id": t_id,
                    "overview": getattr(details, 'overview', 'Sem sinopse.')
                }
        except Exception:
            pass # Não é série também

    except ValueError:
        return None # Não enviou número
    except Exception as e:
        print(f"Erro ao validar ID TMDB: {e}")
        return None

    return None

# MANTENHA A SUA FUNÇÃO search_movie_options ABAIXO IGUAL JÁ ESTAVA...
async def search_movie_options(query: str) -> list:
    """
    Busca um filme e retorna os 3 melhores resultados encontrados pela API.
    """
    try:
        clean_query = query.replace('&', 'and')
        year_match = re.search(r'\((\d{4})\)', clean_query)
        year = int(year_match.group(1)) if year_match else None
        if year:
            clean_query = re.sub(r'\s*\(\d{4}\)\s*', '', clean_query).strip()
        
        search_results = await asyncio.to_thread(movie_search.search, clean_query)
        
        filtered_results = [r for r in search_results if str(year) in r.release_date] if year else search_results

        options = []
        for result in filtered_results[:3]:
            details = await asyncio.to_thread(movie_search.details, result.id, append_to_response='translations')
            
            title_pt = details.title
            original_title = details.original_title
            english_title = None
            translations_data = details.translations.get('translations', [])
            english_translation = next((t['data']['title'] for t in translations_data if t['iso_639_1'] == 'en' and t['data']['title']), None)
            if english_translation:
                english_title = english_translation
            display_title = title_pt
            if title_pt == original_title and english_title:
                display_title = english_title
            if display_title != original_title:
                final_button_text = f"{display_title} ({original_title})"
            else:
                final_button_text = display_title

            options.append({
                'tmdb_id': details.id,
                'title': display_title,
                'button_text': final_button_text,
                'year': int(details.release_date.split('-')[0]) if hasattr(details, 'release_date') and details.release_date else 'N/A',
                'description': getattr(details, 'overview', ''),
                'poster_url': f"https://image.tmdb.org/t/p/w500{details.poster_path}" if getattr(details, 'poster_path', None) else None
            })
            
        return options
    
    except Exception as e:
        print(f"Erro ao buscar opções no TMDb: {e}")
        return []
    