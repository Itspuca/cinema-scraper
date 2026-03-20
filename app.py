"""
Cinema Scraper -- Backend
=========================
API FastAPI che espone la programmazione di tutti i cinema.
Cache in memoria: i risultati vengono conservati per CACHE_TTL secondi
per non sovraccaricare i siti sorgente.

Avvio locale:
    uvicorn app:app --reload

Deploy Render:
    build:  pip install -r requirements.txt
    start:  uvicorn app:app --host 0.0.0.0 --port $PORT
"""

import os
import sys
import json
import time
import queue
import asyncio
import threading
import requests as req_lib

from fastapi import FastAPI, Query, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from cineteca_milano import get_nonce, fetch_lista, LUOGHI
import anteo      as anteo_mod
import uci        as uci_mod
import notorious  as notorious_mod

# Chiavi per le reti complete
GLOBAL_ALL        = "tutti-cinema"
ANTEO_NETWORK_ALL = "tutti-anteo"
UCI_NETWORK_ALL   = "tutti-uci"
NOTORIOUS_NETWORK = "tutti-notorious"

# Mappe cinema → nome visualizzato
UCI_THEATRES      = {"uci-bicocca": "UCI Bicocca", "uci-lissone": "UCI Lissone"}
NOTORIOUS_CINEMAS = {"notorious-sesto": "Notorious S.G. Sarca"}

app = FastAPI(title="Cinema Scraper API")

# TMDB (poster)
TMDB_KEY    = os.getenv("TMDB_API_KEY", "")
TMDB_SEARCH = "https://api.themoviedb.org/3/search/movie"
TMDB_IMG    = "https://image.tmdb.org/t/p/w342"

# --------------------------------------------------
# Cache in memoria
# --------------------------------------------------

_cache: dict = {}
CACHE_TTL = 60 * 60 * 2  # 2 ore


def cache_get(key: str):
    entry = _cache.get(key)
    if entry and (time.time() - entry["ts"]) < CACHE_TTL:
        return entry["data"]
    return None


def cache_set(key: str, data):
    _cache[key] = {"data": data, "ts": time.time()}


def raw_to_films(raw):
    return [
        {"data": d, "orario": o, "titolo": t, "regista": r, "sala": s}
        for d, o, t, r, s in sorted(set(raw), key=lambda x: (x[0], x[1], x[2]))
    ]


def sse(payload: dict) -> str:
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


def _wrap_progress(prefix: str, callback):
    if not callback:
        return None
    def _wrapped(msg, current, total):
        callback(f"{prefix} — {msg}", current, total)
    return _wrapped


# --------------------------------------------------
# Routes
# --------------------------------------------------

def _serve_html(filename: str) -> str:
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), filename)
    with open(path, encoding="utf-8") as f:
        return f.read()


@app.get("/", response_class=HTMLResponse)
def index():
    return _serve_html("index.html")


@app.get("/poster", response_class=HTMLResponse)
def index_poster():
    return _serve_html("index_poster.html")


@app.get("/api/sale")
def sale():
    return {
        "cineteca":  ["tutte"] + list(LUOGHI.keys()),
        "anteo":     [ANTEO_NETWORK_ALL] + list(anteo_mod.CINEMAS.keys()),
        "uci":       [UCI_NETWORK_ALL]   + list(UCI_THEATRES.keys()),
        "notorious": [NOTORIOUS_NETWORK] + list(NOTORIOUS_CINEMAS.keys()),
        "all":       [GLOBAL_ALL],
    }


@app.get("/api/stream")
async def stream(
    sala:   str = Query("tutte"),
    giorni: int = Query(None),
):
    """
    Endpoint SSE: trasmette il progresso dello scraping in tempo reale,
    poi invia i risultati finali come ultimo evento.

    Valori validi per 'sala':
      - "tutte" / "Arlecchino" / "Biblioteca" / "Metropolis" / "MIC"
      - "tutti-anteo" / "Anteo" / "CityLife" / "Ariosto" / "Capitol"
      - "tutti-uci" / "uci-bicocca" / "uci-lissone"
      - "tutti-notorious" / "notorious-sesto"
      - "tutti-cinema"  (tutti i cinema)
    """
    valid_sala = (
        {"tutte", GLOBAL_ALL, ANTEO_NETWORK_ALL, UCI_NETWORK_ALL, NOTORIOUS_NETWORK}
        | set(LUOGHI.keys())
        | set(anteo_mod.CINEMAS.keys())
        | set(UCI_THEATRES.keys())
        | set(NOTORIOUS_CINEMAS.keys())
    )

    if sala not in valid_sala:
        raise HTTPException(status_code=400, detail=f"Sala non valida: {sala}")

    cache_key = f"{sala}_{giorni}"

    async def generator():
        cached = cache_get(cache_key)
        if cached is not None:
            yield sse({"type": "log",  "msg": "Dati dalla cache."})
            yield sse({"type": "done", "films": cached, "cached": True})
            return

        q: queue.Queue = queue.Queue()

        def run():
            def on_progress(msg, current, total):
                q.put({"type": "progress", "msg": msg, "current": current, "total": total})
            try:
                session = req_lib.Session()

                if sala == GLOBAL_ALL:
                    q.put({"type": "log", "msg": "Avvio ricerca Cineteca Milano..."})
                    nonce = get_nonce(session)
                    raw   = fetch_lista(session, nonce, luoghi_id=None, giorni=giorni,
                                        on_progress=_wrap_progress("Cineteca", on_progress))
                    q.put({"type": "log", "msg": "Ricerca rete Anteo..."})
                    for c in anteo_mod.CINEMAS:
                        raw += anteo_mod.fetch_lista(session, c, giorni=giorni,
                                                     on_progress=_wrap_progress(c, on_progress))
                    q.put({"type": "log", "msg": "Ricerca rete UCI..."})
                    for c in UCI_THEATRES:
                        raw += uci_mod.fetch_lista(session, c, giorni=giorni,
                                                   on_progress=_wrap_progress(UCI_THEATRES[c], on_progress))
                    q.put({"type": "log", "msg": "Ricerca rete Notorious..."})
                    for c in NOTORIOUS_CINEMAS:
                        raw += notorious_mod.fetch_lista(session, c, giorni=giorni,
                                                         on_progress=_wrap_progress(NOTORIOUS_CINEMAS[c], on_progress))

                elif sala == ANTEO_NETWORK_ALL:
                    q.put({"type": "log", "msg": "Avvio ricerca rete Anteo..."})
                    raw = []
                    for c in anteo_mod.CINEMAS:
                        raw += anteo_mod.fetch_lista(session, c, giorni=giorni,
                                                     on_progress=_wrap_progress(c, on_progress))

                elif sala in anteo_mod.CINEMAS:
                    q.put({"type": "log", "msg": f"Avvio ricerca {sala}..."})
                    raw = anteo_mod.fetch_lista(session, sala, giorni=giorni, on_progress=on_progress)

                elif sala == UCI_NETWORK_ALL:
                    q.put({"type": "log", "msg": "Avvio ricerca rete UCI..."})
                    raw = []
                    for c in UCI_THEATRES:
                        raw += uci_mod.fetch_lista(session, c, giorni=giorni,
                                                   on_progress=_wrap_progress(UCI_THEATRES[c], on_progress))

                elif sala in UCI_THEATRES:
                    q.put({"type": "log", "msg": f"Avvio ricerca {UCI_THEATRES[sala]}..."})
                    raw = uci_mod.fetch_lista(session, sala, giorni=giorni, on_progress=on_progress)

                elif sala == NOTORIOUS_NETWORK:
                    q.put({"type": "log", "msg": "Avvio ricerca rete Notorious..."})
                    raw = []
                    for c in NOTORIOUS_CINEMAS:
                        raw += notorious_mod.fetch_lista(session, c, giorni=giorni,
                                                         on_progress=_wrap_progress(NOTORIOUS_CINEMAS[c], on_progress))

                elif sala in NOTORIOUS_CINEMAS:
                    q.put({"type": "log", "msg": f"Avvio ricerca {NOTORIOUS_CINEMAS[sala]}..."})
                    raw = notorious_mod.fetch_lista(session, sala, giorni=giorni, on_progress=on_progress)

                else:
                    # Cineteca Milano: serve il nonce WordPress
                    luoghi_id = None if sala == "tutte" else [LUOGHI[sala]]
                    q.put({"type": "log", "msg": "Avvio ricerca Cineteca Milano..."})
                    nonce = get_nonce(session)
                    raw   = fetch_lista(session, nonce, luoghi_id=luoghi_id,
                                        giorni=giorni, on_progress=on_progress)

                films = raw_to_films(raw)
                cache_set(cache_key, films)
                q.put({"type": "done", "films": films, "cached": False})
            except Exception as e:
                q.put({"type": "error", "msg": str(e)})

        t = threading.Thread(target=run, daemon=True)
        t.start()

        while True:
            await asyncio.sleep(0.15)
            while True:
                try:
                    event = q.get_nowait()
                    yield sse(event)
                    if event["type"] in ("done", "error"):
                        return
                except queue.Empty:
                    break
            if not t.is_alive() and q.empty():
                break

    return StreamingResponse(
        generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.get("/api/poster")
def poster(titolo: str = Query(...), anno: str = Query("")):
    """
    Cerca il poster del film su TMDB e ritorna l'URL dell'immagine.
    Richiede la variabile d'ambiente TMDB_API_KEY.
    """
    if not TMDB_KEY:
        return JSONResponse({"url": None})

    cache_key = f"poster_{titolo}_{anno}"
    cached    = cache_get(cache_key)
    if cached is not None:
        return JSONResponse({"url": cached})

    params: dict = {"api_key": TMDB_KEY, "query": titolo, "language": "it-IT"}
    if anno:
        params["year"] = anno

    try:
        r       = req_lib.get(TMDB_SEARCH, params=params, timeout=10)
        results = r.json().get("results", [])
        url     = None
        if results and results[0].get("poster_path"):
            url = TMDB_IMG + results[0]["poster_path"]
        if not url and anno:
            params.pop("year")
            r2      = req_lib.get(TMDB_SEARCH, params=params, timeout=10)
            results = r2.json().get("results", [])
            if results and results[0].get("poster_path"):
                url = TMDB_IMG + results[0]["poster_path"]
        cache_set(cache_key, url)
        return JSONResponse({"url": url})
    except Exception:
        return JSONResponse({"url": None})


@app.delete("/api/cache")
def svuota_cache():
    _cache.clear()
    return {"ok": True, "message": "Cache svuotata"}
