"""
Anteo Spazio Cinema -- Scraper Programmazione
==============================================
Estrae la programmazione da www.spaziocinema.info

API:
    GET https://www.spaziocinema.info/@@get-calendario?dt=YYYY-MM-DD&cinema=NOME+CINEMA
    Risposta: array JSON di film, ciascuno con 'orari' (proiezioni del giorno)
    e 'film_occupations' (tutte le proiezioni future della rete).
"""

import re
import os
import sys
import time
import argparse
import requests
from datetime import datetime, timedelta


# --------------------------------------------------
# Configurazione
# --------------------------------------------------

BASE_URL = "https://www.spaziocinema.info/@@get-calendario"

# Mappa nome visualizzato → nome esatto da passare al parametro ?cinema=
CINEMAS = {
    "Anteo":    "Anteo palazzo del cinema",
    "CityLife": "Citylife anteo",
    "Ariosto":  "Ariosto",
    "Capitol":  "Capitol anteo spaziocinema",
}

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept":     "application/json",
}

MAX_GIORNI_VUOTI  = 5
MAX_GIORNI_TOTALI = 180

REQUEST_TIMEOUT = 30    # secondi per singola richiesta
MAX_RETRIES     = 3     # tentativi in caso di timeout/errore di rete
RETRY_DELAY     = 4     # secondi di pausa tra un tentativo e il successivo

GIORNI_IT = ["Lunedi", "Martedi", "Mercoledi", "Giovedi", "Venerdi", "Sabato", "Domenica"]
MESI_IT   = ["gennaio", "febbraio", "marzo", "aprile", "maggio", "giugno",
             "luglio", "agosto", "settembre", "ottobre", "novembre", "dicembre"]


# --------------------------------------------------
# Helpers
# --------------------------------------------------

def data_breve(data: str) -> str:
    """'2026-05-02' → 'Sabato 2 maggio'"""
    dt = datetime.strptime(data, "%Y-%m-%d")
    return f"{GIORNI_IT[dt.weekday()]} {dt.day} {MESI_IT[dt.month - 1]}"


def _cinema_name_da_orario(film: dict, orario: dict) -> str:
    """
    Trova il cinema_name corrispondente a un orario cercando in film_occupations.
    Il collegamento è la projection_url, univoca per ogni proiezione,
    presente sia in 'orari' che in 'film_occupations'.
    """
    proj_url = orario.get("projection_url", "")
    if not proj_url:
        return ""
    # Costruisce un indice projection_url → cinema_name da film_occupations
    for occ in film.get("film_occupations", []):
        if occ.get("projection_url") == proj_url:
            return occ.get("cinema_name", "")
    return ""


# --------------------------------------------------
# Fetch
# --------------------------------------------------

def fetch_giorno(session: requests.Session, date_str: str, cinema_name: str) -> list[dict]:
    """
    Recupera tutti i film per un giorno e un cinema specifici.
    Ritorna l'array JSON grezzo (lista di film).
    """
    resp = session.get(
        BASE_URL,
        params={"dt": date_str, "cinema": cinema_name},
        headers=HEADERS,
        timeout=REQUEST_TIMEOUT,
    )
    resp.raise_for_status()
    data = resp.json()
    return data if isinstance(data, list) else []


def fetch_lista(
    session:      requests.Session,
    cinema_key:   str,
    giorni:       int | None = None,
    on_progress=  None,
) -> list[tuple[str, str, str, str, str]]:
    """
    Ritorna lista di tuple (data, orario, titolo, regista, cinema_name).

    cinema_key: chiave del dizionario CINEMAS (es. "Anteo")
    giorni:     numero di giorni da oggi (None = infinito fino a MAX_GIORNI_VUOTI vuoti)
    on_progress(msg, current, total): callback opzionale per il log di avanzamento
    """
    cinema_name = CINEMAS[cinema_key]
    risultati   = []
    giorni_vuoti = 0
    oggi  = datetime.today()
    tetto = giorni if giorni is not None else MAX_GIORNI_TOTALI

    for i in range(tetto):
        date_str = (oggi + timedelta(days=i)).strftime("%Y-%m-%d")

        # Fetch con retry automatico su errori di rete/timeout
        films = None
        for tentativo in range(1, MAX_RETRIES + 1):
            try:
                films = fetch_giorno(session, date_str, cinema_name)
                break   # successo, esci dal loop
            except (requests.exceptions.Timeout,
                    requests.exceptions.ConnectionError) as err:
                if tentativo < MAX_RETRIES:
                    if on_progress:
                        on_progress(
                            f"{data_breve(date_str)} — timeout, riprovo ({tentativo}/{MAX_RETRIES})...",
                            i + 1, tetto,
                        )
                    time.sleep(RETRY_DELAY)
                else:
                    # Esauriti i tentativi: salta il giorno e prosegui
                    if on_progress:
                        on_progress(
                            f"{data_breve(date_str)} — skip (timeout dopo {MAX_RETRIES} tentativi)",
                            i + 1, tetto,
                        )
                    films = []

        # Tieni solo i film che hanno proiezioni in questo giorno
        films_oggi = [f for f in films if f.get("orari")]

        if not films_oggi:
            if on_progress:
                on_progress(f"{data_breve(date_str)} — nessun film", i + 1, tetto)
            if giorni is None:
                giorni_vuoti += 1
                if giorni_vuoti >= MAX_GIORNI_VUOTI:
                    break
            continue

        giorni_vuoti = 0
        for film in films_oggi:
            titolo  = film.get("title", "--")
            regista = film.get("director", "")
            for orario in film.get("orari", []):
                hour   = orario.get("hour", "--").replace(":", ".")
                cinema = _cinema_name_da_orario(film, orario)
                risultati.append((date_str, hour, titolo, regista, cinema))

        if on_progress:
            on_progress(f"{data_breve(date_str)} — {len(films_oggi)} film", i + 1, tetto)

    return risultati


# --------------------------------------------------
# Output CLI
# --------------------------------------------------

def stampa_lista_per_giorno(risultati: list[tuple]) -> None:
    """Vista per giorno (Esempio B): Giorno / orario titolo, regista - cinema"""
    if not risultati:
        print("  (nessun risultato)")
        return
    giorno_corrente = None
    for data, orario, titolo, regista, cinema in sorted(risultati):
        if data != giorno_corrente:
            giorno_corrente = data
            print(f"\n  {data_breve(data)}")
        reg = f", {regista}" if regista else ""
        cin = f" - {cinema}" if cinema else ""
        print(f"  {orario} {titolo}{reg}{cin}")
    print()


def stampa_lista_per_titolo(risultati: list[tuple]) -> None:
    """Vista per titolo (Esempio A): Titolo, Regista / data, orario - cinema"""
    if not risultati:
        print("  (nessun risultato)")
        return
    per_titolo: dict = {}
    for data, orario, titolo, regista, cinema in sorted(risultati, key=lambda x: (x[2].lower(), x[0], x[1])):
        if titolo not in per_titolo:
            per_titolo[titolo] = {"regista": regista, "proiezioni": []}
        per_titolo[titolo]["proiezioni"].append((data, orario, cinema))
    for titolo, dati in per_titolo.items():
        reg = f", {dati['regista']}" if dati["regista"] else ""
        print(f"\n  {titolo}{reg}")
        for data, orario, cinema in dati["proiezioni"]:
            cin = f" - {cinema}" if cinema else ""
            print(f"  {data_breve(data)}, {orario}{cin}")
    n_proiezioni = sum(len(d["proiezioni"]) for d in per_titolo.values())
    print(f"\n  Proiezioni: {n_proiezioni}  |  Film unici: {len(per_titolo)}\n")


def salva_lista_txt(risultati: list[tuple], cinema_key: str, vista: str = "titolo") -> None:
    """Salva la lista in lists/anteo_<cinema>_<data>.txt nella vista scelta."""
    os.makedirs("lists", exist_ok=True)
    oggi = datetime.today().strftime("%Y-%m-%d")
    nome = f"lists/anteo_{cinema_key.lower()}_{oggi}.txt"
    with open(nome, "w", encoding="utf-8") as f:
        if vista == "giorno":
            giorno_corrente = None
            for data, orario, titolo, regista, cinema in sorted(risultati):
                if data != giorno_corrente:
                    giorno_corrente = data
                    f.write(f"\n{data_breve(data)}\n")
                reg = f", {regista}" if regista else ""
                cin = f" - {cinema}" if cinema else ""
                f.write(f"  {orario} {titolo}{reg}{cin}\n")
        else:
            per_titolo: dict = {}
            for data, orario, titolo, regista, cinema in sorted(risultati, key=lambda x: (x[2].lower(), x[0], x[1])):
                if titolo not in per_titolo:
                    per_titolo[titolo] = {"regista": regista, "proiezioni": []}
                per_titolo[titolo]["proiezioni"].append((data, orario, cinema))
            for titolo, dati in per_titolo.items():
                reg = f", {dati['regista']}" if dati["regista"] else ""
                f.write(f"\n{titolo}{reg}\n")
                for data, orario, cinema in dati["proiezioni"]:
                    cin = f" - {cinema}" if cinema else ""
                    f.write(f"  {data_breve(data)}, {orario}{cin}\n")
    print(f"\n  [ok] Lista salvata in: {nome}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Spazio Cinema Anteo Scraper")
    parser.add_argument("--cinema", choices=list(CINEMAS.keys()), default="Anteo",
                        help="Cinema da interrogare")
    parser.add_argument("--giorni", type=int, default=None,
                        help="Numero di giorni (default: tutta la programmazione)")
    parser.add_argument("--salva", action="store_true",
                        help="Salva il risultato in un file .txt nella cartella lists/")
    parser.add_argument("--vista", choices=["titolo", "giorno"], default="titolo",
                        help="Vista output: 'titolo' (default) o 'giorno'")
    args = parser.parse_args()

    print(f"\nSpazio Cinema Anteo")
    print(f"  Cinema: {args.cinema}  ({CINEMAS[args.cinema]})")
    giorni_info = f"{args.giorni} giorni" if args.giorni else "tutta la programmazione"
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
