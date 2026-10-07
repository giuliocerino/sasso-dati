#!/usr/bin/env python3
"""
S.A.S.S.O. – prova: un fotogramma da ciascuna telecamera Tapo, via cloud.

Usa l'account in sola visione e la libreria OnTapo (non ufficiale): per ogni telecamera
prende pochi secondi di video tramite il relay del cloud e ne salva un fotogramma JPEG
in snapshots/. Non cambia impostazioni (il cloud rifiuta comunque ogni comando "set").
Le telecamere a batteria vengono svegliate per pochi secondi, come quando si apre l'app.

Nel log stampa solo nome, modello ed esito: niente ID né credenziali.
"""
import asyncio
import os
import re
import sys
from pathlib import Path

from ontapo import OnTapo

OUT = Path(os.environ.get("SNAP_DIR", "snapshots"))
TIMEOUT = int(os.environ.get("SNAP_TIMEOUT", "90"))


def no_mfa():
    raise RuntimeError("l'account chiede un codice MFA: non utilizzabile in automatico")


def safe(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9_-]+", "_", name).strip("_") or "camera"


async def main() -> int:
    user, pwd = os.environ.get("TAPO_ACCOUNT"), os.environ.get("TAPO_PASSWORD")
    if not user or not pwd:
        print("Mancano TAPO_ACCOUNT / TAPO_PASSWORD"); return 1
    try:
        session = await OnTapo.login(user, pwd, mfa=no_mfa)
    except Exception as e:
        print("ESITO: login non riuscito:", type(e).__name__, str(e).replace(user, "<account>")[:200]); return 1

    OUT.mkdir(parents=True, exist_ok=True)
    ok = 0
    async with session as s:
        devs = await s.devices()
        print(f"Telecamere visibili: {len(devs)}")
        for dev in devs:
            label = getattr(dev, "name", "?")
            try:
                info = await asyncio.wait_for(dev.summary(), 30)
                print(f"- {info.describe()}")
            except Exception as e:
                print(f"- {label}: dati della telecamera non disponibili ({type(e).__name__}: {str(e)[:120]})")
            path = OUT / f"{safe(label)}.jpg"
            try:
                await asyncio.wait_for(dev.snapshot(str(path)), TIMEOUT)
                size = path.stat().st_size if path.exists() else 0
                print(f"  fotogramma: {'OK' if size else 'vuoto'} ({size // 1024} kB)")
                ok += bool(size)
            except asyncio.TimeoutError:
                print(f"  fotogramma: nessuna risposta entro {TIMEOUT} s (telecamera addormentata o non raggiungibile?)")
            except Exception as e:
                print(f"  fotogramma: ERRORE {type(e).__name__}: {str(e)[:160]}")
    print(f"ESITO: {ok} fotogrammi su {len(devs)}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
