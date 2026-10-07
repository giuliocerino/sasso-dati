#!/usr/bin/env python3
"""
S.A.S.S.O. – contatore di muoni (Glacier Muon Telescope).

Legge in sola lettura le ultime 24 ore dall'API pubblica PostgREST del gruppo
(https://api.glacier.stefa9.it, tabella muon_data) e scrive data/muon.json:

  mean_rate_hz   rateo medio delle ultime 24 ore (eventi al secondo)
  hourly         rateo medio ora per ora, per il grafico
  daily          rateo medio di ogni giorno: si accumula a ogni lettura e resta come archivio

Ogni riga di muon_data è un conteggio di eventi (event_count) in un intervallo di circa un
minuto. Il rateo è eventi / secondi trascorsi dalla riga precedente; gli intervalli anomali
(più corti di 20 s o più lunghi di 5 min, ad esempio dopo un'interruzione) vengono scartati.
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import requests

API = os.environ.get("MUON_API", "https://api.glacier.stefa9.it/muon_data")
DEVICES = os.environ.get("MUON_DEVICES", "sas,SAS")      # il bivacco compare come "sas" (prima "SAS")
TZ = ZoneInfo("Europe/Rome")
OUT = Path(os.environ.get("OUTPUT_DIR", "data"))
MIN_DT, MAX_DT = 20, 300
PAGE = 5000


def fetch(since: datetime) -> list[dict]:
    rows, offset = [], 0
    while True:
        r = requests.get(API, params={
            "select": "timestamp,event_count",
            "device_id": f"in.({DEVICES})",
            "timestamp": f"gte.{since.isoformat()}",
            "order": "timestamp.asc",
            "limit": PAGE, "offset": offset,
        }, timeout=60, headers={"User-Agent": "SASSO-data-bot/1.0 (+Politecnico di Torino)"})
        r.raise_for_status()
        batch = r.json()
        rows += batch
        if len(batch) < PAGE:
            return rows
        offset += PAGE


def intervals(rows: list[dict]) -> list[tuple[datetime, int, float]]:
    """(fine intervallo, eventi, secondi) per ogni intervallo valido."""
    out, prev = [], None
    for row in rows:
        if not row.get("timestamp") or row.get("event_count") is None:
            continue
        t = datetime.fromisoformat(row["timestamp"]).astimezone(TZ)
        if prev is not None:
            dt = (t - prev).total_seconds()
            if MIN_DT <= dt <= MAX_DT:
                out.append((t, int(row["event_count"]), dt))
        prev = t
    return out


def r3(x):
    return None if x is None else round(x, 3)


def main() -> int:
    now = datetime.now(TZ)
    since = now - timedelta(hours=24)
    rows = fetch(since - timedelta(minutes=5))          # una riga in più per il primo intervallo
    iv = [x for x in intervals(rows) if x[0] >= since]
    if not iv:
        print("::warning::nessun dato del contatore di muoni nelle ultime 24 ore")
        return 0

    events, secs = sum(e for _, e, _ in iv), sum(d for _, _, d in iv)
    hours: dict[datetime, list[float]] = {}
    for t, e, d in iv:
        h = t.replace(minute=0, second=0, microsecond=0)
        acc = hours.setdefault(h, [0, 0.0, 0])
        acc[0] += e; acc[1] += d; acc[2] += 1
    hourly = [{"time": h.isoformat(), "rate_hz": r3(a[0] / a[1]), "samples": a[2]}
              for h, a in sorted(hours.items())]

    # archivio dei ratei giornalieri: si conserva quello che c'era e si aggiornano i giorni letti ora
    path = OUT / "muon.json"
    try:
        daily = {d["date"]: d for d in json.loads(path.read_text()).get("daily", [])}
    except Exception:
        daily = {}
    per_day: dict[str, list[float]] = {}
    for t, e, d in iv:
        acc = per_day.setdefault(t.date().isoformat(), [0, 0.0])
        acc[0] += e; acc[1] += d
    for day, (e, d) in per_day.items():
        if d >= 6 * 3600 or day not in daily:            # un giorno parziale non sovrascrive uno più completo
            daily[day] = {"date": day, "rate_hz": r3(e / d), "hours_covered": round(d / 3600, 1)}

    out = {
        "source": "Glacier Muon Telescope · api.glacier.stefa9.it",
        "device": DEVICES.split(",")[0],
        "updated": now.isoformat(timespec="seconds"),
        "from": iv[0][0].isoformat(), "to": iv[-1][0].isoformat(),
        "samples": len(iv),
        "events_24h": events,
        "mean_rate_hz": r3(events / secs),
        "mean_rate_per_min": round(60 * events / secs, 1),
        "hourly": hourly,
        "daily": [daily[k] for k in sorted(daily)][-400:],
    }
    OUT.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(out, ensure_ascii=False, indent=1))
    print(f"ok – rateo medio 24 h {out['mean_rate_hz']} eventi/s su {len(iv)} intervalli")
    return 0


if __name__ == "__main__":
    sys.exit(main())
