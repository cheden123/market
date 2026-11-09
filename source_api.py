"""Opt-in API.SH Avito search adapter. No scraping, CAPTCHA bypass, or login."""
import json
import urllib.parse
import urllib.request

ENDPOINT = "https://api.api.sh/avito/v1/search"

def convert_listing(entry):
    if not isinstance(entry, dict):
        return None
    title = str(entry.get("title") or "").strip()
    link = str(entry.get("url") or "")
    raw_id = entry.get("id")
    price = entry.get("price")
    if not title or raw_id is None or not link.startswith("https://www.avito.ru/"):
        return None
    try:
        price = float(price)
        if not 0 < price < 1e12:
            return None
    except (TypeError, ValueError):
        return None
    return {"source": "avito-api-sh", "external_id": str(raw_id),
            "title": title, "price": price, "url": link,
            "description": str(entry.get("description") or "")[:10000],
            "region": str(entry.get("location") or entry.get("address") or "")[:300]}

def search(api_key, search_url, timeout=120):
    """One page, one billable request. No pagination or automatic retry."""
    if not api_key or not api_key.strip():
        raise ValueError("Не указан API.SH API Key.")
    parsed = urllib.parse.urlsplit(search_url)
    if parsed.scheme != "https" or parsed.hostname not in {"avito.ru", "www.avito.ru"}:
        raise ValueError("Используйте поисковую HTTPS-ссылку avito.ru.")
    parameters = urllib.parse.urlencode({"url": search_url, "sort": "newest", "page": "1"})
    req = urllib.request.Request(ENDPOINT + "?" + parameters,
            headers={"x-api-key": api_key.strip(), "Accept": "application/json",
                     "User-Agent": "MarketValueHunter/0.2"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            if response.status != 200:
                raise RuntimeError("API.SH HTTP " + str(response.status))
            payload = json.load(response)
    except urllib.error.HTTPError as e:
        raise RuntimeError("Ошибка API.SH (HTTP " + str(e.code) + "). Проверьте ключ и баланс.") from e
    if not isinstance(payload, dict) or payload.get("success") is False:
        raise RuntimeError("Ошибка ответа API.SH: " + str(payload.get("error") if isinstance(payload, dict) else "формат"))
    if not isinstance(payload.get("listings"), list):
        raise RuntimeError("API.SH вернул ответ без массива listings; проверка схемы обязательна.")
    return [x for row in payload["listings"] if (x := convert_listing(row)) is not None], float(payload.get("credits_used") or 0)

