"""
Cineteca Milano -- Scraper Programmazione
==========================================
Estrae la programmazione da www.cinetecamilano.it

Requisiti:
    pip install requests beautifulsoup4

Uso:
    python cineteca_milano.py --lista
    python cineteca_milano.py --lista --sala Arlecchino --giorni 14 --salva
    python cineteca_milano.py --avanzato --tipo film --sala MIC --data 2026-03-20 --giorni 7
"""

import os
import re
import json
import csv
import argparse
import requests
from datetime import datetime, timedelta
from bs4 import BeautifulSoup


# --------------------------------------------------
# Configurazione
# --------------------------------------------------

BASE_URL     = "https://www.cinetecamilano.it"
CALENDAR_URL = f"{BASE_URL}/calendario/"
AJAX_URL     = f"{BASE_URL}/wp-admin/admin-ajax.php"

LUOGHI = {
    "Arlecchino": 4,
    "Biblioteca": 37,
    "Metropolis": 36,
    "MIC":        15,
}

LUOGHI_INV = {v: k for k, v in LUOGHI.items()}  # {4: "Arlecchino", ...}

HEADERS = {
    "User-Agent":       "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "X-Requested-With": "XMLHttpRequest",
    "Referer":          CALENDAR_URL,
}

MAX_GIORNI_VUOTI  = 5
MAX_GIORNI_TOTALI = 180


# --------------------------------------------------
# Core: nonce + fetch
# --------------------------------------------------

def get_nonce(session: requests.Session) -> str:
    resp = session.get(CALENDAR_URL, headers=HEADERS, timeout=15)
    resp.raise_for_status()

    match = re.search(r'dkrcmc_ajax_nonce["\s:]+(["\'])([a-f0-9]+)\1', resp.text)
    if not match:
        soup = BeautifulSoup(resp.text, "html.parser")
        for script in soup.find_all("script"):
            if script.string and "dkrcmc_ajax_nonce" in (script.string or ""):
                m = re.search(r'dkrcmc_ajax_nonce["\s:]+(["\'])([a-f0-9]+)\1', script.string)
                if m:
                    return m.group(2)
        raise RuntimeError("Nonce non trovato. Il sito potrebbe aver cambiato struttura.")

    print(f"  [ok] Nonce: {match.group(2)}")
    return match.group(2)


def fetch_events(
    session:   requests.Session,
    nonce:     str,
    date:      str,
    tipi:      list[str] | None = None,
    luoghi_id: list[int] | None = None,
) -> list[dict]:
    if tipi is None:
        tipi = ["film", "evento", "spettacolo"]
    if luoghi_id is None:
        luoghi_id = list(LUOGHI.values())

    passed_values = [json.dumps({"dkrcmc_date": date})]
    for tipo in tipi:
        passed_values.append(json.dumps({"dkrcmc_post_type": tipo}))
    for tid in luoghi_id:
        passed_values.append(json.dumps({"dkrcmc_taxonomy_slug": "luogo", "dkrcmc_term_id": tid}))
    passed_values.append(json.dumps({"dkrcmc_transient": str(int(datetime.now().timestamp() * 1000))}))

    data = {
        "action":                 "dkrcmc_ajax_filter",
        "dkrcmc_passed_values[]": passed_values,
        "dkrcmc_ajax_nonce":      nonce,
    }

    resp = session.post(AJAX_URL, data=data, headers=HEADERS, timeout=15)
    resp.raise_for_status()
    return resp.json().get("dkrcmc-data", [])


# --------------------------------------------------
# Estrazione campi
# --------------------------------------------------

def estrai_regista(ev: dict) -> str:
    """Prima parte del subtitle prima della virgola."""
    subtitle = ev.get("subtitle", "") or ""
    if subtitle:
        testo = BeautifulSoup(subtitle, "html.parser").get_text(separator=" ").strip()
        if "," in testo:
            return testo.split(",")[0].strip()
    return ""


def estrai_orario(ev: dict) -> str:
    """HH.MM dal campo 'date' (es. 'Lunedi 16 Marzo 2026, 18:00' -> '18.00')."""
    date_val = ev.get("date", "") or ""
    match = re.search(r',\s*(([01]?\d|2[0-3]):([0-5]\d))', date_val)
    if not match:
        match = re.search(r'\b(([01]?\d|2[0-3]):([0-5]\d))\b', date_val)
    if match:
        return match.group(1).replace(":", ".")
    return "--"


def estrai_sala(ev: dict) -> str:
    """
    Estrae il nome della sala cercando il term_id di 'luogo'
    nei campi taxonomy/terms dell'evento, incluso il campo 'place'.
    Ritorna stringa vuota se non trovato.
    """
    for campo in ("place", "terms", "taxonomies", "luoghi", "luogo", "locations", "tags"):
        val = ev.get(campo)
        if not val:
            continue

        # Se è una stringa, confronto diretto col nome della sala
        if isinstance(val, str):
            for sala in LUOGHI:
                if sala.lower() in val.lower():
                    return sala
            continue

        items = val if isinstance(val, list) else [val]
        for item in items:
            if isinstance(item, str):
                for sala in LUOGHI:
                    if sala.lower() in item.lower():
                        return sala
                continue
            if not isinstance(item, dict):
                continue
            tid = item.get("term_id") or item.get("id") or item.get("ID")
            if tid:
                try:
                    nome = LUOGHI_INV.get(int(tid))
                    if nome:
                        return nome
                except (ValueError, TypeError):
                    pass
            # Fallback: cerca per nome o slug
            for sala in LUOGHI:
                if sala.lower() in str(item.get("name", "")).lower() or \
                   sala.lower() in str(item.get("slug", "")).lower():
                    return sala
    return ""


def estrai_anno(ev: dict) -> str:
    """Anno dal subtitle (es. '..., 2025, 160\'' -> '2025')."""
    subtitle = ev.get("subtitle", "") or ""
    if subtitle:
        testo = BeautifulSoup(subtitle, "html.parser").get_text(separator=" ")
        m = re.search(r'\b(19[0-9]{2}|20[0-9]{2})\b', testo)
        if m:
            return m.group(0)
    return ""


# --------------------------------------------------
# Modalita LISTA
# --------------------------------------------------

GIORNI_IT = ["Lunedi", "Martedi", "Mercoledi", "Giovedi", "Venerdi", "Sabato", "Domenica"]
MESI_IT   = ["gennaio", "febbraio", "marzo", "aprile", "maggio", "giugno",
             "luglio", "agosto", "settembre", "ottobre", "novembre", "dicembre"]


def data_breve(data: str) -> str:
    """'2026-05-02' -> 'Sabato 2 maggio'"""
    dt = datetime.strptime(data, "%Y-%m-%d")
    return f"{GIORNI_IT[dt.weekday()]} {dt.day} {MESI_IT[dt.month - 1]}"


def fetch_lista(
    session:    requests.Session,
    nonce:      str,
    luoghi_id:  list[int] | None = None,
    giorni:     int | None = None,
    on_progress = None,
) -> list[tuple[str, str, str, str]]:
    """
    Ritorna lista di tuple (data, orario, titolo, regista) per tutti i film
    in programmazione, partendo da oggi.

    on_progress(msg, current, total) -- callback opzionale per il log di avanzamento.
    """
    risultati = []
    giorni_vuoti = 0
    oggi = datetime.today()
    tetto = giorni if giorni is not None else MAX_GIORNI_TOTALI

    for i in range(tetto):
        date_str = (oggi + timedelta(days=i)).strftime("%Y-%m-%d")
        print(f"  Recupero {date_str}...", end="\r")

        events = fetch_events(session, nonce, date_str, tipi=["film"], luoghi_id=luoghi_id)
        film   = [ev for ev in events if ev.get("type", "").lower() == "film"]

        if not film:
            if on_progress:
                on_progress(f"{data_breve(date_str)} — nessun film", i + 1, tetto)
            if giorni is None:
                giorni_vuoti += 1
                if giorni_vuoti >= MAX_GIORNI_VUOTI:
                    print(f"\n  Fine: {MAX_GIORNI_VUOTI} giorni vuoti consecutivi.")
                    break
            continue

        giorni_vuoti = 0
        for ev in film:
            risultati.append((
                date_str,
                estrai_orario(ev),
                ev.get("title", "--"),
                estrai_regista(ev),
                estrai_sala(ev),
            ))

        if on_progress:
            on_progress(f"{data_breve(date_str)} — {len(film)} film", i + 1, tetto)

    return risultati


def _cartella_lists() -> str:
    base = os.path.dirname(os.path.abspath(__file__))
    cartella = os.path.join(base, "lists")
    os.makedirs(cartella, exist_ok=True)
    return cartella


def _raggruppa_per_titolo(risultati) -> dict:
    """Raggruppa le proiezioni per titolo, ordinate per data e orario."""
    righe = sorted(set(risultati), key=lambda x: (x[2].lower(), x[0], x[1]))
    per_titolo = {}
    for data, orario, titolo, regista, sala in righe:
        if titolo not in per_titolo:
            per_titolo[titolo] = {"regista": regista, "proiezioni": []}
        per_titolo[titolo]["proiezioni"].append((data, orario, sala))
    return per_titolo


def stampa_lista(risultati) -> None:
    """Vista per titolo (Esempio A): Titolo, Regista / data, orario - sala"""
    per_titolo = _raggruppa_per_titolo(risultati)
    print(f"\n{'='*60}")
    for titolo, dati in per_titolo.items():
        reg = f", {dati['regista']}" if dati["regista"] else ""
        print(f"\n  {titolo}{reg}")
        for data, orario, sala in dati["proiezioni"]:
            cin = f" - {sala}" if sala else ""
            print(f"  {data_breve(data)}, {orario}{cin}")
    print(f"\n{'='*60}")
    n_proiezioni = sum(len(d["proiezioni"]) for d in per_titolo.values())
    print(f"  Proiezioni: {n_proiezioni}  |  Film unici: {len(per_titolo)}\n")


def stampa_lista_per_giorno(risultati) -> None:
    """Vista per giorno (Esempio B): Giorno / orario titolo, regista - sala"""
    righe = sorted(set(risultati), key=lambda x: (x[0], x[1]))
    giorno_corrente = None
    print(f"\n{'='*60}")
    for data, orario, titolo, regista, sala in righe:
        if data != giorno_corrente:
            giorno_corrente = data
            print(f"\n  {data_breve(data)}")
        reg = f", {regista}" if regista else ""
        cin = f" - {sala}" if sala else ""
        print(f"  {orario} {titolo}{reg}{cin}")
    print(f"\n{'='*60}")
    film_unici = len({t for _, _, t, _, _ in righe})
    print(f"  Proiezioni: {len(righe)}  |  Film unici: {film_unici}\n")


def salva_lista_txt(risultati) -> None:
    per_titolo = _raggruppa_per_titolo(risultati)
    filename   = os.path.join(
        _cartella_lists(),
        f"lista_{datetime.today().strftime('%Y-%m-%d')}.txt"
    )
    with open(filename, "w", encoding="utf-8") as f:
        f.write(f"Cineteca Milano - Programmazione\n")
        f.write(f"Generato il: {datetime.today().strftime('%d/%m/%Y %H:%M')}\n\n")
        for titolo, dati in per_titolo.items():
            reg = f", {dati['regista']}" if dati["regista"] else ""
            f.write(f"{titolo}{reg}\n")
            for data, orario, sala in dati["proiezioni"]:
                cin = f" - {sala}" if sala else ""
                f.write(f"  {data_breve(data)}, {orario}{cin}\n")
            f.write("\n")
        n_proiezioni = sum(len(d["proiezioni"]) for d in per_titolo.values())
        f.write(f"Proiezioni: {n_proiezioni}  |  Film unici: {len(per_titolo)}\n")
    print(f"  [ok] Salvato: {filename}")


# --------------------------------------------------
# Modalita AVANZATO
# --------------------------------------------------

def stampa_eventi(events: list[dict], date: str) -> None:
    if not events:
        print(f"\n  Nessun evento per {date}.\n")
        return

    by_type: dict[str, list] = {}
    for ev in events:
        by_type.setdefault(ev.get("type", "Altro"), []).append(ev)

    print(f"\n{'='*60}  {date}  {'='*10}")
    for tipo, items in sorted(by_type.items()):
        print(f"\n  {tipo.upper()} ({len(items)})\n  {'-'*40}")
        for ev in items:
            titolo   = ev.get("title", "--")
            orario   = estrai_orario(ev)
            regista  = estrai_regista(ev)
            link     = ev.get("permalink", "")
            dettagli = f"  {orario}" if orario != "--" else ""
            reg_str  = f"  ({regista})" if regista else ""
            print(f"  * {titolo}{dettagli}{reg_str}")
            if link:
                print(f"    {link}")
    print()


# --------------------------------------------------
# Main
# --------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Cineteca Milano Scraper")

    parser.add_argument("--lista",    action="store_true", help="Modalita lista programmazione")
    parser.add_argument("--avanzato", action="store_true", help="Modalita ricerca avanzata")

    parser.add_argument("--sala",    choices=list(LUOGHI.keys()) + ["tutte"], default="tutte")
    parser.add_argument("--giorni",  type=int, default=None, help="Numero di giorni (lista: default infinito, avanzato: default 1)")
    parser.add_argument("--salva",   action="store_true",    help="Salva file .txt nella cartella lists/")
    parser.add_argument("--vista",   choices=["titolo", "giorno"], default="titolo",
                        help="Vista output: 'titolo' (default) o 'giorno'")

    # Solo modalita avanzata
    parser.add_argument("--tipo",    choices=["film", "evento", "spettacolo", "tutti"], default="tutti")
    parser.add_argument("--data",    default=None, help="Data YYYY-MM-DD (default: oggi)")
    parser.add_argument("--json",    action="store_true", help="Salva JSON per ogni giorno")

    args      = parser.parse_args()
    luoghi_id = None if args.sala == "tutte" else [LUOGHI[args.sala]]
    session   = requests.Session()

    print(f"\nCineteca Milano")
    print(f"  Sala: {args.sala}")
    print(f"\n[1/2] Estrazione nonce...")
    nonce = get_nonce(session)
    print(f"[2/2] Recupero dati...\n")

    # ── Lista ────────────────────────────────────────────
    if args.lista:
        giorni_info = f"{args.giorni} giorni" if args.giorni else "tutta la programmazione"
        print(f"  Modalita: lista  ({giorni_info})  |  Vista: per {args.vista}\n")
        risultati = fetch_lista(session, nonce, luoghi_id=luoghi_id, giorni=args.giorni)
        if args.vista == "giorno":
            stampa_lista_per_giorno(risultati)
        else:
            stampa_lista(risultati)
        if args.salva:
            salva_lista_txt(risultati)
        return

    # ── Avanzato ─────────────────────────────────────────
    if args.avanzato:
        start  = datetime.strptime(args.data, "%Y-%m-%d") if args.data else datetime.today()
        tipi   = None if args.tipo == "tutti" else [args.tipo]
        n_giorni = args.giorni if args.giorni is not None else 1
        print(f"  Modalita: avanzato  |  Tipo: {args.tipo}  |  Da: {start.strftime('%Y-%m-%d')}  ({n_giorni} giorno/i)\n")

        for i in range(n_giorni):
            date_str = (start + timedelta(days=i)).strftime("%Y-%m-%d")
            events   = fetch_events(session, nonce, date_str, tipi, luoghi_id)
            stampa_eventi(events, date_str)
            if args.json:
                fname = f"cineteca_{date_str}.json"
                with open(fname, "w", encoding="utf-8") as f:
                    json.dump(events, f, ensure_ascii=False, indent=2)
                print(f"  [ok] {fname}")
        return

    print("  Usa --lista oppure --avanzato. Per aiuto: python cineteca_milano.py --help")


if __name__ == "__main__":
    main()
