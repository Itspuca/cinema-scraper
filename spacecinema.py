"""
The Space Cinema -- Scraper Programmazione
==========================================
Estrae la programmazione da www.thespacecinema.it

API:
    GET https://www.thespacecinema.it/api/microservice/showings/cinemas/{id}/films
    Parametri: showingDate (ISO), includesSession=true, includeSessionAttributes=true
    La pagina viene caricata prima per ottenere i cookie di sessione necessari.

Uso:
    python spacecinema.py --cinema space-rozzano --giorni 7
    python spacecinema.py --cinema space-rozzano --vista giorno --salva
"""

from __future__ import annotations

import os
import argparse
from datetime import datetime, timedelta
from typing import Callable

import requests
import cloudscraper


# --------------------------------------------------
# Configurazione
# --------------------------------------------------

BASE_SITE = "https://www.thespacecinema.it"
API_URL   = BASE_SITE + "/api/microservice/showings/cinemas/{cinema_id}/films"

CINEMAS = {
    "space-rozzano": {"id": 1020, "name": "Rozzano", "slug": "rozzano"},
}

MAX_GIORNI_TOTALI = 30
REQUEST_TIMEOUT   = 20

HEADERS_PAGE = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/122.0.0.0 Safari/537.36",
    "Accept":     "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}
HEADERS_API = {
    "User-Agent":       "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/122.0.0.0 Safari/537.36",
    "Accept":           "application/json, text/plain, */*",
    "X-Requested-With": "XMLHttpRequest",
}

GIORNI_IT = ["Lunedi", "Martedi", "Mercoledi", "Giovedi", "Venerdi", "Sabato", "Domenica"]
MESI_IT   = ["gennaio", "febbraio", "marzo", "aprile", "maggio", "giugno",
             "luglio", "agosto", "settembre", "ottobre", "novembre", "dicembre"]


# --------------------------------------------------
# Helpers
# --------------------------------------------------

def data_breve(data: str) -> str:
    """'2026-05-02' -> 'Sabato 2 maggio'"""
    dt = datetime.strptime(data, "%Y-%m-%d")
    return f"{GIORNI_IT[dt.weekday()]} {dt.day} {MESI_IT[dt.month - 1]}"


def _format_time(ts: str) -> str:
    """Converte timestamp ISO o HH:MM:SS in HH.MM"""
    for fmt in ("%Y-%m-%dT%H:%M:%S", "%H:%M:%S", "%H:%M"):
        try:
            return datetime.strptime(ts, fmt).strftime("%H.%M")
        except ValueError:
            continue
    return "--"


def _parse_session_time(session: dict) -> str:
    """Estrae l'orario dalla sessione provando vari nomi di campo."""
    for key in ("startDateTime", "start", "startTime", "sessionDateTime", "time"):
        val = session.get(key)
        if isinstance(val, str) and val:
            return _format_time(val.split("+")[0])
    return "--"


# --------------------------------------------------
# Fetch
# --------------------------------------------------

def fetch_lista(
    session:     requests.Session | cloudscraper.CloudScraper,
    cinema_key:  str,
    giorni:      int | None = None,
    on_progress: Callable[[str, int, int], None] | None = None,
) -> list[tuple[str, str, str, str, str]]:
    """
    Ritorna lista di tuple (data, orario, titolo, regista, sala).

    cinema_key: chiave del dizionario CINEMAS (es. "space-rozzano")
    giorni:     numero di giorni da oggi (None = MAX_GIORNI_TOTALI)
    on_progress(msg, current, total): callback opzionale per il log di avanzamento
    """
    cinema    = CINEMAS[cinema_key]
    cinema_id = cinema["id"]
    slug      = cinema["slug"]
    oggi      = datetime.today()
    tetto     = giorni if giorni is not None else MAX_GIORNI_TOTALI
    risultati: list[tuple[str, str, str, str, str]] = []

    # Carica la pagina del cinema per ottenere i cookie di sessione
    page_url = f"{BASE_SITE}/cinema/{slug}/al-cinema"
    try:
        warmup = session.get(page_url, headers=HEADERS_PAGE, timeout=REQUEST_TIMEOUT)
        if on_progress:
            on_progress(f"[debug] warm-up status={warmup.status_code} content-type={warmup.headers.get('content-type','?')[:40]}", 0, tetto)
    except Exception as exc:
        if on_progress:
            on_progress(f"[debug] warm-up errore: {exc}", 0, tetto)

    headers = {**HEADERS_API, "Referer": page_url}

    for offset in range(tetto):
        target_date = oggi + timedelta(days=offset)
        date_str    = target_date.strftime("%Y-%m-%d")

        params = {
            "showingDate":              target_date.strftime("%Y-%m-%dT00:00:00"),
            "minEmbargoLevel":          3,
            "includesSession":          "true",
            "includeSessionAttributes": "true",
        }

        try:
            resp = session.get(
                API_URL.format(cinema_id=cinema_id),
                params=params,
                headers=headers,
                timeout=REQUEST_TIMEOUT,
            )
            if on_progress and offset == 0:
                on_progress(f"[debug] API status={resp.status_code} content-type={resp.headers.get('content-type','?')[:40]} body={resp.text[:80]}", offset + 1, tetto)
            resp.raise_for_status()
            payload = resp.json()
        except Exception as exc:
            if on_progress:
                on_progress(f"{data_breve(date_str)} — errore: {exc}", offset + 1, tetto)
            continue

        films = payload.get("result") or []

        if not films:
            if on_progress:
                on_progress(f"{data_breve(date_str)} — nessun film", offset + 1, tetto)
            continue

        n_sess = 0
        for film in films:
            title    = film.get("filmTitle") or film.get("title") or "--"
            director = film.get("director", "")

            for group in film.get("showingGroups", []):
                group_date = group.get("date", date_str)
                if "T" in str(group_date):
                    group_date = group_date.split("T")[0]

                for s in group.get("sessions", []):
                    start    = s.get("startTime", "")
                    time_str = _format_time(start) if start else "--"
                    risultati.append((group_date, time_str, title, director, cinema["name"]))
                    n_sess += 1

        if on_progress:
            on_progress(
                f"{data_breve(date_str)} — {len(films)} film, {n_sess} proiezioni",
                offset + 1, tetto,
            )

    return risultati


# --------------------------------------------------
# Output CLI
# --------------------------------------------------

def stampa_lista_per_titolo(risultati: list[tuple]) -> None:
    """Vista per titolo (Esempio A): Titolo, Regista / -- giorno, orario - sala"""
    if not risultati:
        print("  (nessun risultato)")
        return
    per_titolo: dict = {}
    for data, orario, titolo, regista, sala in sorted(risultati, key=lambda x: (x[2].lower(), x[0], x[1])):
        if titolo not in per_titolo:
            per_titolo[titolo] = {"regista": regista, "proiezioni": []}
        per_titolo[titolo]["proiezioni"].append((data, orario, sala))

    for titolo, dati in per_titolo.items():
        reg = f", {dati['regista']}" if dati["regista"] else ""
        print(f"\n  {titolo}{reg}")
        for data, orario, sala in dati["proiezioni"]:
            cin = f" - {sala}" if sala else ""
            print(f"  {data_breve(data)}, {orario}{cin}")

    n_proiezioni = sum(len(d["proiezioni"]) for d in per_titolo.values())
    print(f"\n  Proiezioni: {n_proiezioni}  |  Film unici: {len(per_titolo)}\n")


def stampa_lista_per_giorno(risultati: list[tuple]) -> None:
    """Vista per giorno (Esempio B): Giorno / orario titolo, regista - sala"""
    if not risultati:
        print("  (nessun risultato)")
        return
    giorno_corrente = None
    for data, orario, titolo, regista, sala in sorted(risultati):
        if data != giorno_corrente:
            giorno_corrente = data
            print(f"\n  {data_breve(data)}")
        reg = f", {regista}" if regista else ""
        cin = f" - {sala}" if sala else ""
        print(f"  {orario} {titolo}{reg}{cin}")
    print()


def salva_lista_txt(risultati: list[tuple], cinema_key: str, vista: str = "titolo") -> None:
    """Salva la lista in lists/space_<cinema>_<data>.txt nella vista scelta."""
    os.makedirs("lists", exist_ok=True)
    oggi = datetime.today().strftime("%Y-%m-%d")
    nome = f"lists/space_{cinema_key.lower()}_{oggi}.txt"
    with open(nome, "w", encoding="utf-8") as f:
        if vista == "giorno":
            giorno_corrente = None
            for data, orario, titolo, regista, sala in sorted(risultati):
                if data != giorno_corrente:
                    giorno_corrente = data
                    f.write(f"\n{data_breve(data)}\n")
                reg = f", {regista}" if regista else ""
                cin = f" - {sala}" if sala else ""
                f.write(f"  {orario} {titolo}{reg}{cin}\n")
        else:
            per_titolo: dict = {}
            for data, orario, titolo, regista, sala in sorted(risultati, key=lambda x: (x[2].lower(), x[0], x[1])):
                if titolo not in per_titolo:
                    per_titolo[titolo] = {"regista": regista, "proiezioni": []}
                per_titolo[titolo]["proiezioni"].append((data, orario, sala))
            for titolo, dati in per_titolo.items():
                reg = f", {dati['regista']}" if dati["regista"] else ""
                f.write(f"\n{titolo}{reg}\n")
                for data, orario, sala in dati["proiezioni"]:
                    cin = f" - {sala}" if sala else ""
                    f.write(f"  {data_breve(data)}, {orario}{cin}\n")
    print(f"\n  [ok] Lista salvata in: {nome}")


# --------------------------------------------------
# Main
# --------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="The Space Cinema Scraper")
    parser.add_argument("--cinema",  choices=list(CINEMAS.keys()), default="space-rozzano",
                        help="Cinema da interrogare")
    parser.add_argument("--giorni",  type=int, default=None,
                        help=f"Numero di giorni (default: {MAX_GIORNI_TOTALI})")
    parser.add_argument("--salva",   action="store_true",
                        help="Salva il risultato in un file .txt nella cartella lists/")
    parser.add_argument("--vista",   choices=["titolo", "giorno"], default="titolo",
                        help="Vista output: 'titolo' (default) o 'giorno'")
    args = parser.parse_args()

    info = CINEMAS[args.cinema]
    print(f"\nThe Space Cinema")
    print(f"  Cinema: {info['name']}  (id={info['id']})")
    giorni_info = f"{args.giorni} giorni" if args.giorni else f"{MAX_GIORNI_TOTALI} giorni (default)"
    print(f"  Periodo: {giorni_info}  |  Vista: per {args.vista}\n")

    def on_progress(msg, current, total):
        print(f"  [{current}/{total}] {msg}")

    session   = cloudscraper.create_scraper()
    risultati = fetch_lista(session, args.cinema, giorni=args.giorni, on_progress=on_progress)

    if args.vista == "giorno":
        stampa_lista_per_giorno(risultati)
    else:
        stampa_lista_per_titolo(risultati)

    if args.salva:
        salva_lista_txt(risultati, args.cinema, vista=args.vista)


if __name__ == "__main__":
    main()
