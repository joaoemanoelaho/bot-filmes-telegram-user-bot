import re
import asyncio # <-- 1. IMPORTAMOS ASYNCIO
from tmdbv3api import TMDb, Movie
from config import TMDB_API_KEY

# Configuração da API
tmdb = TMDb()
tmdb.api_key = TMDB_API_KEY
tmdb.language = 'pt-BR' 
movie_search = Movie()

# 2. Função agora é 'async def'
async def search_movie_options(query: str) -> list:
    """
    Busca um filme e retorna os 3 melhores resultados encontrados pela API,
    sem nenhum filtro de idioma, para o usuário escolher.
    """
    try:
        # 1. Limpeza da query e extração do ano (seu código original)
        clean_query = query.replace('&', 'and')
        year_match = re.search(r'\((\d{4})\)', clean_query)
        year = int(year_match.group(1)) if year_match else None
        if year:
            clean_query = re.sub(r'\s*\(\d{4}\)\s*', '', clean_query).strip()
        
        # 3. Busca inicial (AGORA EM UMA THREAD)
        search_results = await asyncio.to_thread(
            movie_search.search, clean_query
        )
        
        # 4. Filtro inicial por ano
        filtered_results = [r for r in search_results if str(year) in r.release_date] if year else search_results

        options = []
        # O loop agora pega os 3 primeiros resultados
        for result in filtered_results[:3]:
            # 5. Busca de detalhes (AGORA EM UMA THREAD)
            details = await asyncio.to_thread(
                movie_search.details, result.id, append_to_response='translations'
            )
            
            # --- LÓGICA DO TÍTULO INTELIGENTE (Sem alteração) ---
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
            # --- FIM DA LÓGICA ---

            options.append({
                'tmdb_id': details.id,
                'title': display_title,
                'button_text': final_button_text,
                'year': int(details.release_date.split('-')[0]) if details.release_date else 'N/A',
                'description': details.overview,
                'poster_url': f"https://image.tmdb.org/t/p/w500{details.poster_path}" if details.poster_path else None
            })
            
        return options
    
    except Exception as e:
        print(f"Erro ao buscar opções no TMDb: {e}")
        return []
    