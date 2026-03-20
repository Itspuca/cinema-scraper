"""
Notorious Cinemas Sesto San Giovanni -- Scraper
===============================================
Chiama il modulo `prenoRapido.php` per recuperare la programmazione completa
di un cinema Notorious (Sesto San Giovanni).

Uso:
    python notorious.py --cinema notorious-sesto --giorni 7
    python notorious.py --cinema notorious-sesto --vista giorno --salva
"""

from __future__ import annotations

import os
import argparse
import random
from datetime import datetime, timedelta
from typing import Callable

import requests


BASE_URL = "https://www.notoriouscinemas.it/cvu/modules/prenoRapido.php"
CINEMAS = {
    "notorious-sesto": 5446,
}
DISPLAY_NAMES = {
    "notorious-sesto": "Notorious S.G. Sarca",
}

REQUEST_TIMEOUT   = 30
MAX_GIORNI_TOTALI = 30

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


def _format_time(iso: str) -> str:
    try:
        dt = datetime.fromisoformat(iso)
        return dt.strftime("%H.%M")
    except ValueError:
        try:
            dt = datetime.strptime(iso, "%H:%M")
            return dt.strftime("%H.%M")
        except ValueError:
            return "--"


def _allowed_day(day_iso: str, giorni: int | None, oggi: datetime) -> bool:
    day_dt = datetime.fromisoformat(day_iso.replace("T00:00:00", ""))
    if giorni is None:
        return True
    return 0 <= (day_dt.date() - oggi.date()).days < giorni


# --------------------------------------------------
# Fetch
# --------------------------------------------------

def fetch_lista(
    session:     requests.Session,
    cinema_key:  str,
    giorni:      int | None = None,
    on_progress: Callable[[str, int, int], None] | None = None,
) -> list[tuple[str, str, str, str, str]]:
    cinema_id = CINEMAS[cinema_key]
    oggi      = datetime.today()
    day_limit = giorni if giorni is not None else MAX_GIORNI_TOTALI

    params = {
        "sel":    "getFullSched",
        "idcine": cinema_id,
        "rand":   random.randint(1_000_000_000, 9_999_999_999),
    }

    headers = {
        "Referer":          "https://www.notoriouscinemas.it/sestosangiovanni/index.php",
        "X-Requested-With": "XMLHttpRequest",
        "Accept":           "application/json, text/javascript, */*; q=0.01",
        "User-Agent":       "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
    }
    resp = session.get(
        BASE_URL,
        params=params,
        headers=headers,
        timeout=REQUEST_TIMEOUT,
    )
    resp.raise_for_status()
    try:
        payload = resp.json()
    except ValueError as exc:
        raise RuntimeError(f"Impossibile decodificare la risposta Notorious: {exc}") from exc

    risultati: list[tuple[str, str, str, str, str]] = []
    events = payload.get("DS", {}).get("Scheduling", {}).get("Events", [])
    total  = len(events)
    for idx, event in enumerate(events, start=1):
        title    = event.get("Title", "--")
        director = event.get("Director", "")
        days     = event.get("Days", [])
        for day in days:
            day_iso = day.get("Day", "")
            if not day_iso or not _allowed_day(day_iso, day_limit, oggi=oggi):
                continue
            for perf in day.get("Performances", []):
                start     = perf.get("StartTime") or perf.get("Time") or ""
                time_fmt  = _format_time(start)
                screen    = perf.get("Screen", "")
                data_str  = day_iso.split("T")[0]
                risultati.append((data_str, time_fmt, title, director, screen))
        if on_progress:
            on_progress(f"{title} processato", idx, total)

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
    """Salva la lista in lists/notorious_<cinema>_<data>.txt nella vista scelta."""
    os.makedirs("lists", exist_ok=True)
    oggi = datetime.today().strftime("%Y-%m-%d")
    nome = f"lists/notorious_{cinema_key.lower()}_{oggi}.txt"
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
    parser = argparse.ArgumentParser(description="Notorious Cinemas Scraper")
    parser.add_argument("--cinema",  choices=list(CINEMAS.keys()), default="notorious-sesto",
                        help="Cinema da interrogare")
    parser.add_argument("--giorni",  type=int, default=None,
                        help=f"Numero di giorni (default: {MAX_GIORNI_TOTALI})")
    parser.add_argument("--salva",   action="store_true",
                        help="Salva il risultato in un file .txt nella cartella lists/")
    parser.add_argument("--vista",   choices=["titolo", "giorno"], default="titolo",
                        help="Vista output: 'titolo' (default) o 'giorno'")
    args = parser.parse_args()

    print(f"\nNotorious Cinemas")
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
