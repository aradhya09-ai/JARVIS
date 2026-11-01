from fastapi import HTTPException, Request

from services import Services


def get_services(request: Request) -> Services:
    services = getattr(request.app.state, "services", None)
    if services is None:
        raise HTTPException(status_code=503, detail="JARVIS services are starting up.")
    return services
