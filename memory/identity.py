from __future__ import annotations

import hashlib
import urllib.parse


TRACKING_QUERY_PARAM_NAMES = {
    "ref",
    "source",
    "src",
    "campaign",
    "cmp",
    "cid",
    "mc_cid",
    "mc_eid",
    "fbclid",
    "gclid",
    "dclid",
    "gbraid",
    "wbraid",
    "igshid",
    "mkt_tok",
}


def canonicalize_url(url: str) -> str:
    raw = (url or "").strip()
    if not raw:
        return ""
    try:
        parts = urllib.parse.urlsplit(raw)
    except Exception:
        return raw

    scheme = (parts.scheme or "").lower() or "https"
    netloc = (parts.netloc or "").lower()
    path = parts.path or ""
    if path != "/" and path.endswith("/"):
        path = path.rstrip("/")
    if scheme == "http" and netloc.endswith(":80"):
        netloc = netloc[:-3]
    if scheme == "https" and netloc.endswith(":443"):
        netloc = netloc[:-4]

    filtered_query: list[tuple[str, str]] = []
    for k, v in urllib.parse.parse_qsl(parts.query, keep_blank_values=True):
        key = (k or "").strip()
        key_l = key.lower()
        if key_l.startswith("utm_") or key_l in TRACKING_QUERY_PARAM_NAMES:
            continue
        filtered_query.append((key, v))

    filtered_query.sort(key=lambda kv: (kv[0].lower(), kv[0], kv[1]))
    query = urllib.parse.urlencode(filtered_query, doseq=True)
    return urllib.parse.urlunsplit((scheme, netloc, path, query, ""))


def make_item_id(item: dict) -> str:
    canonical_url = canonicalize_url(str(item.get("canonical_url") or item.get("url") or ""))
    if canonical_url:
        payload = canonical_url
    else:
        source = str(item.get("source") or "").strip().lower()
        title = str(item.get("title") or "").strip().lower()
        published_at = str(item.get("published_at") or "").strip()
        payload = f"{source}\x1f{title}\x1f{published_at}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()

