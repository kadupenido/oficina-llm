import json

import httpx
import structlog

from app.config import Settings

log = structlog.get_logger(__name__)

TAVILY_URL = "https://api.tavily.com/search"

WEB_SEARCH_TOOL = {
    "type": "function",
    "function": {
        "name": "web_search",
        "description": (
            "Busca na internet informações recentes ou factos que não estão no histórico. "
            "Use para notícias, preços, eventos, datas atuais e dados que mudam com o tempo."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Consulta de busca em linguagem natural.",
                },
            },
            "required": ["query"],
        },
    },
}


def _truncate_payload(text: str, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 3] + "..."


async def run_web_search(
    query: str,
    settings: Settings,
) -> tuple[str, list[dict[str, str]]]:
    """Chama a Tavily e devolve (conteúdo para role tool, fontes para a UI)."""
    query = query.strip()
    if not query:
        payload = json.dumps({"error": "Consulta de busca vazia."}, ensure_ascii=False)
        return payload, []

    body = {
        "api_key": settings.tavily_api_key,
        "query": query,
        "max_results": settings.web_search_max_results,
        "include_answer": False,
    }
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(15.0, connect=5.0)) as client:
            response = await client.post(TAVILY_URL, json=body)
            response.raise_for_status()
            data = response.json()
    except httpx.HTTPStatusError as exc:
        log.warning("tavily_http_error", status=exc.response.status_code)
        payload = json.dumps(
            {"error": "A busca na web falhou (HTTP)."},
            ensure_ascii=False,
        )
        return payload, []
    except httpx.HTTPError as exc:
        log.warning("tavily_unreachable", error=str(exc))
        payload = json.dumps(
            {"error": "Não foi possível contactar o serviço de busca."},
            ensure_ascii=False,
        )
        return payload, []

    raw_results = data.get("results") if isinstance(data, dict) else None
    if not isinstance(raw_results, list):
        payload = json.dumps({"error": "Resposta de busca inválida."}, ensure_ascii=False)
        return payload, []

    compact: list[dict[str, str]] = []
    sources: list[dict[str, str]] = []
    for item in raw_results:
        if not isinstance(item, dict):
            continue
        title = str(item.get("title") or "").strip()
        url = str(item.get("url") or "").strip()
        snippet = str(item.get("content") or item.get("snippet") or "").strip()
        if not url and not snippet:
            continue
        compact.append(
            {
                "title": title or url,
                "url": url,
                "snippet": snippet,
            }
        )
        sources.append({"title": title or url, "url": url})

    if not compact:
        payload = json.dumps(
            {"query": query, "results": [], "message": "Nenhum resultado encontrado."},
            ensure_ascii=False,
        )
        return _truncate_payload(payload, settings.web_search_max_chars), sources

    payload = json.dumps({"query": query, "results": compact}, ensure_ascii=False)
    return _truncate_payload(payload, settings.web_search_max_chars), sources
