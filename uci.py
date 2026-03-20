"""
UCI Cinemas -- Scraper Programmazione
=====================================
Ricava la programmazione giornaliera dall'API pubblica UCI.

Endpoint:
    GET https://uci-backend-high-load-1042268733238.europe-west8.run.app/api/theatres/{theatre-slug}/programming/{YYYY-MM-DD}

Uso:
    python uci.py --cinema uci-bicocca --giorni 7
    python uci.py --cinema uci-lissone --vista giorno --salva
"""

import os
import argparse
from datetime import datetime, timedelta
from typing import Callable

import requests


BASE_URL = "https://uci-backend-high-load-1042268733238.europe-west8.run.app/api/theatres"

# Mappa chiave -> (slug API, nome visualizzato)
THEATRES = {
    "uci-bicocca": "uci-cinemas-bicocca-milano",
    "uci-lissone": "uci-cinemas-lissone-milano",
}
DISPLAY_NAMES = {
    "uci-bicocca": "UCI Bicocca",
    "uci-lissone": "UCI Lissone",
}

MAX_GIORNI_TOTALI = 30
MAX_GIORNI_VUOTI  = 5
REQUEST_TIMEOUT   = 30

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
    for fmt in ("%Y-%m-%d %H:%M:%S", "%H:%M:%S"):
        try:
            return datetime.strptime(ts, fmt).strftime("%H.%M")
        except ValueError:
            continue
    return "--"


def _fetch_day(session: requests.Session, theatre_slug: str, date_str: str) -> list[dict]:
    url = f"{BASE_URL}/{theatre_slug}/programming/{date_str}"
    resp = session.get(url, timeout=REQUEST_TIMEOUT)
    resp.raise_for_status()
    return resp.json().get("data", [])


# --------------------------------------------------
# Fetch
# --------------------------------------------------

def fetch_lista(
    session:     requests.Session,
    theatre_key: str,
    giorni:      int | None = None,
    on_progress: Callable[[str, int, int], None] | None = None,
) -> list[tuple[str, str, str, str, str]]:
    """
    Ritorna lista di tuple (data, orario, titolo, regista, sala).

    theatre_key: chiave del dizionario THEATRES (es. "uci-bicocca")
    giorni:      numero di giorni da oggi (None = MAX_GIORNI_TOTALI)
    """
    slug         = THEATRES[theatre_key]
    display_name = DISPLAY_NAMES[theatre_key]
    risultati: list[tuple[str, str, str, str, str]] = []
    oggi         = datetime.today()
    limite       = giorni if giorni is not None else MAX_GIORNI_TOTALI
    giorni_vuoti = 0

    for i in range(limite):
        date_str = (oggi + timedelta(days=i)).strftime("%Y-%m-%d")

        data     = _fetch_day(session, slug, date_str)
        giornate = 0

        for film in data:
            titolo  = film.get("title", "--")
            regista = ""
            for screen_group in film.get("screens", []):
                for screen_data in screen_group.values():
                    for screening in screen_data:
                        for perf in screening.get("performances", []):
                            start = perf.get("starts_at", "")
                            if not start:
                                continue
                            sala = display_name
                            risultati.append((
                                perf.get("day", date_str),
                                _format_time(start),
                                titolo,
                                regista,
                                sala,
                            ))
                            giornate += 1

        if giornate == 0:
            giorni_vuoti += 1
            if on_progress:
                on_progress(f"{data_breve(date_str)} — nessuna proiezione", i + 1, limite)
            if giorni is None and giorni_vuoti >= MAX_GIORNI_VUOTI:
                break
            continue

        giorni_vuoti = 0
        if on_progress:
            on_progress(f"{data_breve(date_str)} — {giornate} proiezioni", i + 1, limite)

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


def salva_lista_txt(risultati: list[tuple], theatre_key: str, vista: str = "titolo") -> None:
    """Salva la lista in lists/uci_<cinema>_<data>.txt nella vista scelta."""
    os.makedirs("lists", exist_ok=True)
    oggi = datetime.today().strftime("%Y-%m-%d")
    nome = f"lists/uci_{theatre_key.lower()}_{oggi}.txt"
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
    parser = argparse.ArgumentParser(description="UCI Cinemas Scraper")
    parser.add_argument("--cinema",  choices=list(THEATRES.keys()), default="uci-bicocca",
                        help="Cinema da interrogare")
    parser.add_argument("--giorni",  type=int, default=None,
                        help=f"Numero di giorni (default: {MAX_GIORNI_TOTALI})")
    parser.add_argument("--salva",   action="store_true",
                        help="Salva il risultato in un file .txt nella cartella lists/")
    parser.add_argument("--vista",   choices=["titolo", "giorno"], default="titolo",
                        help="Vista output: 'titolo' (default) o 'giorno'")
    args = parser.parse_args()

    print(f"\nUCI Cinemas")
    print(f"  Cinema: {DISPLAY_NAMES[args.cinema]}")
    giorni_info = f"{args.giorni} giorni" if args.giorni else f"{MAX_GIORNI_TOTALI} giorni (default)"
    print(f"  Periodo: {giorni_info}  |  Vista: per {args.vista}\n")

    def on_progress(msg, current, total):
        print(f"  [{current}/{total}] {msg}")

    session   = requests.Session()
    risultati = fetch_lista(session, args.cinema, giorni=args.giorni, on_progress=on_progress)

    if args.vista == "giorno":
        stampa_lista_per_giorno(risultati)
    else:
        stampa_lista_per_titolo(risultati)

    if args.salva:
        salva_lista_txt(risultati, args.cinema, vista=args.vista)


if __name__ == "__main__":
    main()
