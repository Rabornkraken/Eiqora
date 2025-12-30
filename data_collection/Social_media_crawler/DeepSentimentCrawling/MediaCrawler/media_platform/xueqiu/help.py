from typing import Dict, Optional

def filter_search_result_card(cards: Optional[list]) -> list:
    """
    Filter valid note cards from search results
    """
    if not cards:
        return []
    # Xueqiu search results are usually a list of dicts directly in 'list' key
    return cards
