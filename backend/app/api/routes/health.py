from fastapi import APIRouter, Depends

from app.api.deps import get_llm

router = APIRouter(tags=["health"])


@router.get(
    "/health",
    summary="Saúde da API e do serviço de inferência",
    responses={
        200: {"description": "A API está no ar. status=degraded se o upstream não responde."}
    },
)
async def health(llm=Depends(get_llm)) -> dict:
    upstream = await llm.check_health()
    ready = upstream.reachable and upstream.status in {"ok", "healthy"}
    return {
        "status": "ok" if ready else "degraded",
        "upstream": {"reachable": upstream.reachable, "status": upstream.status},
    }
