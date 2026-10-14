"""Clash controller calls shared by health checks and the CLI scanner."""

import httpx


def api_session(proxy_url):
    from curl_cffi import CurlOpt, requests

    # Explicit empty proxy means direct access, regardless of inherited *_PROXY / NO_PROXY.
    return requests.Session(
        impersonate="chrome136",
        proxies={"http": proxy_url, "https": proxy_url},
        curl_options={CurlOpt.NOPROXY: ""},
    )


def controller_request(settings, path, method="GET", data=None):
    headers = {}
    if settings.get("clash_secret"):
        headers["Authorization"] = f"Bearer {settings['clash_secret']}"
    if settings.get("clash_transport", "unix") == "unix":
        transport = httpx.HTTPTransport(uds=settings["clash_socket"])
        base_url = "http://localhost"
    else:
        transport = httpx.HTTPTransport()
        base_url = settings["clash_url"].rstrip("/")
    with httpx.Client(
        transport=transport,
        trust_env=False,
        timeout=settings.get("request_timeout", 20),
        headers=headers,
    ) as client:
        response = client.request(method, f"{base_url}{path}", json=data)
        response.raise_for_status()
        return response.text
