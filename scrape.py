"""Vinted Searcher - runs every saved search and merges the results.

Route 1 (free): the open-source vinted_scraper library, straight from GitHub Actions.
Route 2 (paid fallback): an Apify Vinted actor, used only for searches the free
route could not fetch, and only if APIFY_TOKEN is set.

Reads  config/searches.json
Writes data/items.json
"""

import json
import os
import sys
import time
import traceback
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlparse

ROOT = Path(__file__).resolve().parent.parent
SEARCHES_FILE = ROOT / "config" / "searches.json"
ITEMS_FILE = ROOT / "data" / "items.json"

BASE_URL = os.environ.get("VINTED_BASE_URL", "https://www.vinted.co.uk")
APIFY_TOKEN = os.environ.get("APIFY_TOKEN", "").strip()
APIFY_ACTOR = os.environ.get("APIFY_ACTOR", "louisdeconinck/vinted-scraper")
# Don't use Apify more often than this, so a long block can't drain the credit.
APIFY_MIN_HOURS = float(os.environ.get("APIFY_MIN_HOURS", "2"))
FORCE_APIFY = os.environ.get("FORCE_APIFY", "") == "1"

PER_SEARCH = 48          # newest listings fetched per search per run
KEEP_DAYS = 14           # drop items not seen for this long
MAX_ITEMS = 3000         # hard cap on stored items
MAX_RUN_LOG = 48         # run history entries kept for the status screen

# Website URL parameters -> the catalogue API's parameter names.
PARAM_MAP = {
    "catalog[]": "attribute_ids[catalog]",
    "brand_ids[]": "attribute_ids[brand]",
    "size_ids[]": "attribute_ids[size]",
    "status_ids[]": "attribute_ids[status]",
    "color_ids[]": "attribute_ids[color]",
    "material_ids[]": "attribute_ids[material]",
}
DROP_PARAMS = {"time", "page", "search_id", "disabled_personalization", "currency"}


def now_iso():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def load_json(path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return default


# ---------------------------------------------------------------- searches

def search_url(s):
    """The Vinted catalogue URL for a search (pasted URL or built from fields)."""
    if s.get("url"):
        u = urlparse(s["url"].strip())
        q = parse_qs(u.query, keep_blank_values=False)
    else:
        q = {}
        if s.get("query"):
            q["search_text"] = [s["query"]]
    if s.get("min_price") not in (None, ""):
        q["price_from"] = [str(s["min_price"])]
    if s.get("max_price") not in (None, ""):
        q["price_to"] = [str(s["max_price"])]
    q.setdefault("order", ["newest_first"])
    for k in DROP_PARAMS:
        q.pop(k, None)
    return f"{BASE_URL}/catalog?" + urlencode(q, doseq=True)


def api_params(url):
    """Turn a catalogue URL into parameters for the free route."""
    q = parse_qs(urlparse(url).query)
    params = {}
    for key, values in q.items():
        if key in DROP_PARAMS:
            continue
        target = PARAM_MAP.get(key, key.rstrip("[]") if key.endswith("[]") else key)
        params[target] = ",".join(values) if len(values) > 1 or key in PARAM_MAP else values[0]
    params["page"] = 1
    params["per_page"] = PER_SEARCH
    return params


# ---------------------------------------------------------------- normalise

def _price(v):
    if isinstance(v, dict):
        try:
            return float(v.get("amount")), v.get("currency_code") or v.get("currency")
        except (TypeError, ValueError):
            return None, None
    try:
        return float(v), None
    except (TypeError, ValueError):
        return None, None


def _photo(raw):
    p = raw.get("photo") or (raw.get("photos") or [None])[0]
    if isinstance(p, dict):
        thumbs = {t.get("type"): t.get("url") for t in p.get("thumbnails") or [] if isinstance(t, dict)}
        return thumbs.get("thumb310x430") or thumbs.get("thumb364x428") or p.get("url")
    return raw.get("imageUrl") or raw.get("image")


def normalise(raw):
    """One item from either route -> our stored shape. Returns None if unusable."""
    item_id = raw.get("id")
    if item_id is None:
        return None
    price, cur = _price(raw.get("price"))
    if cur is None:
        cur = raw.get("currency")
    total, _ = _price(raw.get("total_item_price"))
    brand = raw.get("brand_title") or raw.get("brand")
    if isinstance(brand, dict):
        brand = brand.get("title")
    size = raw.get("size_title") or raw.get("size")
    cond = raw.get("status") or raw.get("condition")
    url = raw.get("url") or (f"{BASE_URL}{raw['path']}" if raw.get("path") else f"{BASE_URL}/items/{item_id}")
    if url.startswith("/"):
        url = BASE_URL + url
    favs = raw.get("favourite_count")
    if favs is None and isinstance(raw.get("stats"), dict):
        favs = raw["stats"].get("favorites")
    return {
        "id": str(item_id),
        "title": (raw.get("title") or "").strip(),
        "price": price,
        "total": total,
        "currency": cur or "GBP",
        "brand": brand or "",
        "size": size or "",
        "condition": cond or "",
        "url": url,
        "image": _photo(raw),
        "favourites": favs,
    }


def passes(item, s):
    """Per-search filters the website can't do: excluded words and must-have words."""
    text = f"{item['title']} {item['brand']}".lower()
    for word in s.get("exclude") or []:
        if word and word.lower() in text:
            return False
    must = [w for w in (s.get("include") or []) if w]
    if must and not any(w.lower() in text for w in must):
        return False
    if s.get("max_price") not in (None, "") and item["price"] is not None:
        if item["price"] > float(s["max_price"]):
            return False
    return True


# ---------------------------------------------------------------- route 1

def fetch_free(searches):
    """Returns ({search_id: [raw items]}, {search_id: error})."""
    results, errors = {}, {}
    try:
        from vinted_scraper import VintedWrapper
        client = VintedWrapper(BASE_URL)
    except Exception as e:  # session couldn't even start: everything failed
        msg = f"free route could not start: {e}"
        print(msg)
        return {}, {s["id"]: msg for s in searches}

    for s in searches:
        try:
            data = client.search(api_params(search_url(s)))
            results[s["id"]] = data.get("items") or []
            print(f"[free] {s['name']}: {len(results[s['id']])} items")
        except Exception as e:
            errors[s["id"]] = str(e)[:200]
            print(f"[free] {s['name']}: FAILED {e}")
        time.sleep(2)  # be gentle
    return results, errors


# ---------------------------------------------------------------- route 2

def fetch_apify(searches):
    results, errors = {}, {}
    from apify_client import ApifyClient
    client = ApifyClient(APIFY_TOKEN)
    for s in searches:
        try:
            run = client.actor(APIFY_ACTOR).call(
                run_input={
                    "startUrls": [{"url": search_url(s)}],
                    "maxResultsPerUrl": PER_SEARCH,
                },
                timeout_secs=300,
            )
            if not run or run.get("status") != "SUCCEEDED":
                raise RuntimeError(f"run status {run and run.get('status')}")
            items = list(client.dataset(run["defaultDatasetId"]).iterate_items())
            results[s["id"]] = items[:PER_SEARCH]
            print(f"[apify] {s['name']}: {len(items)} items")
        except Exception as e:
            errors[s["id"]] = str(e)[:200]
            print(f"[apify] {s['name']}: FAILED {e}")
    return results, errors


def apify_allowed(store):
    if not APIFY_TOKEN:
        return False, "no APIFY_TOKEN secret"
    if FORCE_APIFY:
        return True, ""
    last = store.get("last_apify_at")
    if last:
        since = datetime.now(timezone.utc) - datetime.fromisoformat(last)
        if since < timedelta(hours=APIFY_MIN_HOURS):
            return False, f"Apify used {since.seconds // 60} min ago (limit every {APIFY_MIN_HOURS:g} h)"
    return True, ""


# ---------------------------------------------------------------- main

def main():
    searches = [s for s in load_json(SEARCHES_FILE, {"searches": []}).get("searches", []) if s.get("enabled", True)]
    store = load_json(ITEMS_FILE, {"items": [], "runs": []})
    ts = now_iso()

    if not searches:
        print("No enabled searches.")
        store.setdefault("runs", []).insert(0, {"at": ts, "note": "no searches"})
        store["runs"] = store["runs"][:MAX_RUN_LOG]
        ITEMS_FILE.write_text(json.dumps(store, ensure_ascii=False, indent=1), encoding="utf-8")
        return

    status = {s["id"]: {"id": s["id"], "name": s["name"], "source": None, "count": 0, "new": 0, "error": None} for s in searches}

    if FORCE_APIFY:
        results, errors = {}, {s["id"]: "forced" for s in searches}
    else:
        results, errors = fetch_free(searches)
    for sid in results:
        status[sid]["source"] = "free"

    if errors:
        failed = [s for s in searches if s["id"] in errors]
        ok, why = apify_allowed(store)
        if ok:
            store["last_apify_at"] = ts
            a_res, a_err = fetch_apify(failed)
            for sid, items in a_res.items():
                results[sid] = items
                status[sid]["source"] = "apify"
                errors.pop(sid, None)
            errors.update(a_err)
        else:
            print(f"Apify skipped: {why}")
            for sid in errors:
                errors[sid] = f"{errors[sid]} | Apify skipped: {why}"

    for sid, err in errors.items():
        status[sid]["error"] = err

    # merge
    by_id = {i["id"]: i for i in store.get("items", [])}
    for s in searches:
        for raw in results.get(s["id"], []):
            item = normalise(raw)
            if not item or not passes(item, s):
                continue
            status[s["id"]]["count"] += 1
            old = by_id.get(item["id"])
            if old:
                old.update({k: v for k, v in item.items() if v not in (None, "")})
                old["last_seen"] = ts
                if s["id"] not in old["searches"]:
                    old["searches"].append(s["id"])
            else:
                item.update({"first_seen": ts, "last_seen": ts, "searches": [s["id"]]})
                by_id[item["id"]] = item
                status[s["id"]]["new"] += 1

    cutoff = (datetime.now(timezone.utc) - timedelta(days=KEEP_DAYS)).isoformat()
    items = [i for i in by_id.values() if i.get("last_seen", "") >= cutoff]
    items.sort(key=lambda i: i["first_seen"], reverse=True)
    store["items"] = items[:MAX_ITEMS]
    store["updated_at"] = ts
    store.setdefault("runs", []).insert(0, {"at": ts, "searches": list(status.values())})
    store["runs"] = store["runs"][:MAX_RUN_LOG]

    ITEMS_FILE.parent.mkdir(parents=True, exist_ok=True)
    ITEMS_FILE.write_text(json.dumps(store, ensure_ascii=False, indent=1), encoding="utf-8")
    new_total = sum(v["new"] for v in status.values())
    print(f"Done: {len(store['items'])} stored, {new_total} new, {len(errors)} failed")


if __name__ == "__main__":
    try:
        main()
    except Exception:
        traceback.print_exc()
        sys.exit(1)
