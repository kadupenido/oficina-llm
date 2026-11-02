from fastapi import APIRouter, Depends

from app.api.deps import get_llm

router = APIRouter(tags=["health"])


@router.get(
    "/health",
    summary="Saúde da API e do llama-server",
    responses={200: {"description": "A API está no ar. status=degraded se o modelo não responde."}},
)
async def health(llm=Depends(get_llm)) -> dict:
    llama = await llm.check_health()
    ready = llama.reachable and llama.status in {"ok", "healthy"}
    return {
        "status": "ok" if ready else "degraded",
        "llama_server": {"reachable": llama.reachable, "status": llama.status},
    }
