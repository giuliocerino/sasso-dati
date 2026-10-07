#!/usr/bin/env python3
"""
S.A.S.S.O. – un fotogramma al giorno dalle 4 webcam Tapo, via cloud (account in sola visione).

Per ogni telecamera prende pochi secondi di video dal relay del cloud Tapo (libreria OnTapo,
non ufficiale), ne estrae un fotogramma, lo ridimensiona e lo salva in webcam/<chiave>.jpg.
Scrive anche webcam/webcam.json con data, ora ed esito di ogni telecamera.
Le immagini vengono SOVRASCRITTE ogni giorno: non si accumulano.

Le telecamere a batteria vengono svegliate per pochi secondi, come aprendo la diretta nell'app.
Nel log non compaiono ID né credenziali.
"""
import asyncio
import io
import json
import os
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from ontapo import OnTapo
from PIL import Image

TZ = ZoneInfo("Europe/Rome")
OUT = Path(os.environ.get("WEBCAM_DIR", "webcam"))
TIMEOUT = int(os.environ.get("SNAP_TIMEOUT", "90"))
WIDTH = int(os.environ.get("WEBCAM_WIDTH", "1280"))

# nome nell'app Tapo -> (chiave del file, descrizione)
CAMERAS = {
    "monte1": ("esterna-monte", "Esterna lato monte"),
    "Tapo_C410_FEE7_valle": ("esterna-valle", "Esterna lato valle"),
    "Tapo_C410_5777_monte": ("interna-finestra", "Interna, dal tavolo verso la finestra"),
    "camera3": ("interna-volume", "Interna, nel volume in lamiera"),
}


def no_mfa():
    raise RuntimeError("l'account chiede un codice MFA: non utilizzabile in automatico")


async def main() -> int:
    user, pwd = os.environ.get("TAPO_ACCOUNT"), os.environ.get("TAPO_PASSWORD")
    if not user or not pwd:
        print("Mancano TAPO_ACCOUNT / TAPO_PASSWORD"); return 1
    OUT.mkdir(parents=True, exist_ok=True)
    try:
        prev = json.loads((OUT / "webcam.json").read_text())
    except Exception:
        prev = {"cameras": {}}
    result = {"updated": datetime.now(TZ).isoformat(timespec="seconds"), "cameras": dict(prev.get("cameras", {}))}

    try:
        session = await OnTapo.login(user, pwd, mfa=no_mfa)
    except Exception as e:
        print("ESITO: login non riuscito:", type(e).__name__, str(e).replace(user, "<account>")[:200]); return 1

    ok = 0
    async with session as s:
        for dev in await s.devices():
            name = getattr(dev, "name", "")
            if name not in CAMERAS:
                print(f"- '{name}': non in elenco, salto"); continue
            key, label = CAMERAS[name]
            with tempfile.TemporaryDirectory() as tmp:
                raw = Path(tmp) / "frame.jpg"
                try:
                    await asyncio.wait_for(dev.snapshot(str(raw)), TIMEOUT)
                    img = Image.open(raw).convert("RGB")
                    if img.width > WIDTH:
                        img = img.resize((WIDTH, round(img.height * WIDTH / img.width)), Image.LANCZOS)
                    buf = io.BytesIO(); img.save(buf, "JPEG", quality=72, optimize=True, progressive=True)
                    (OUT / f"{key}.jpg").write_bytes(buf.getvalue())
                    result["cameras"][key] = {"name": name, "label": label, "time": datetime.now(TZ).isoformat(timespec="minutes"),
                                              "width": img.width, "height": img.height, "ok": True}
                    print(f"- {label}: OK ({len(buf.getvalue()) // 1024} kB, {img.width}×{img.height})"); ok += 1
                except Exception as e:
                    msg = "nessuna risposta" if isinstance(e, asyncio.TimeoutError) else f"{type(e).__name__}: {str(e)[:120]}"
                    old = result["cameras"].get(key, {})
                    result["cameras"][key] = {**old, "name": name, "label": label, "ok": False, "error": msg}
                    print(f"- {label}: non riuscito ({msg}); resta l'immagine precedente, se c'è")
    (OUT / "webcam.json").write_text(json.dumps(result, ensure_ascii=False, indent=1))
    print(f"ESITO: {ok} fotogrammi su {len(CAMERAS)}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
