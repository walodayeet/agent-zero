from helpers.api import ApiHandler, Request, Response
from helpers import dotenv, runtime
from helpers.tunnel_manager import TunnelManager
import httpx


class TunnelProxy(ApiHandler):
    async def process(self, input: dict, request: Request) -> dict | Response:
        return await process(input)

async def process(input: dict) -> dict | Response:
    # Bare metal has no separate tunnel service: the manager lives in this process.
    # Probing the port anyway costs up to 2s per address on Windows (a closed
    # loopback port is not refused there) and blocks the event loop.
    if runtime.is_local_runtime():
        from api.tunnel import process as local_process

        return await local_process(input)

    # Get configuration from environment
    tunnel_api_port = (
        runtime.get_arg("tunnel_api_port")
        or int(dotenv.get_dotenv_value("TUNNEL_API_PORT", 0))
        or 55520
    )
    url = f"http://127.0.0.1:{tunnel_api_port}/"

    async def _post(payload: dict):
        async with httpx.AsyncClient(timeout=0.75) as client:
            return await client.post(url, json=payload)

    # first verify the service is running:
    service_ok = False
    try:
        response = await _post({"action": "health"})
        service_ok = response.status_code == 200
    except Exception:
        service_ok = False

    # forward this request to the tunnel service if OK
    if service_ok:
        try:
            response = await _post(input)
            return response.json()
        except Exception as e:
            return {"error": str(e)}

    # forward to API handler directly
    from api.tunnel import process as local_process

    return await local_process(input)
