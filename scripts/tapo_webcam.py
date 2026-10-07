#!/usr/bin/env python3
"""
S.A.S.S.O. – un fotogramma al giorno dalle 4 webcam Tapo, via cloud (account in sola visione).

Per ogni telecamera prende pochi secondi di video dal relay del cloud Tapo (libreria OnTapo,
non ufficiale), ne estrae un fotogramma, lo ridimensiona e lo salva in webcam/<chiave>.jpg.
Scrive anche webcam/webcam.json con data, ora ed esito di ogni telecamera.
Le immagini vengono SOVRASCRITTE ogni giorno: non si accumulano.

Le telecamere a batteria vengono svegliate per pochi secondi, come aprendo la diretta nell'app.
Se una telecamera non risponde, si riprova una volta alla fine del giro.

La sessione Tapo viene salvata (cifrata) in .state/ e riusata: così il cloud vede sempre lo stesso
"dispositivo" e non arriva una notifica di nuovo accesso a ogni esecuzione. Il login completo
si rifà solo quando la sessione salvata non vale più.
Nel log non compaiono ID né credenziali.
"""
import asyncio
import base64
import hashlib
import io
import json
import os
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from ontapo import OnTapo, Unauthorized
from PIL import Image

TZ = ZoneInfo("Europe/Rome")
OUT = Path(os.environ.get("WEBCAM_DIR", "webcam"))
TIMEOUT = int(os.environ.get("SNAP_TIMEOUT", "90"))
WIDTH = int(os.environ.get("WEBCAM_WIDTH", "1280"))
SESSION_FILE = Path(os.environ.get("STATE_DIR", ".state")) / "tapo_session.enc"

# nome nell'app Tapo -> (chiave del file, descrizione)
CAMERAS = {
    "monte1": ("esterna-monte", "Esterna lato monte"),
    "Tapo_C410_FEE7_valle": ("esterna-valle", "Esterna lato valle"),
    "Tapo_C410_5777_monte": ("interna-finestra", "Interna, dal tavolo verso la finestra"),
    "camera3": ("interna-volume", "Interna, nel volume in lamiera"),
}


def no_mfa():
    raise RuntimeError("l'account chiede un codice MFA: non utilizzabile in automatico")


def _cipher():
    """La sessione salvata è cifrata con una chiave derivata dalle credenziali (che sono secret)."""
    from cryptography.fernet import Fernet
    secret = (os.environ.get("TAPO_PASSWORD", "") + "|" + os.environ.get("TAPO_ACCOUNT", "")).encode()
    key = hashlib.pbkdf2_hmac("sha256", secret, b"sasso-tapo-session", 200_000)
    return Fernet(base64.urlsafe_b64encode(key))


def load_session():
    try:
        return json.loads(_cipher().decrypt(SESSION_FILE.read_bytes()))
    except Exception:
        return None


def save_session(session) -> None:
    try:
        SESSION_FILE.parent.mkdir(parents=True, exist_ok=True)
        SESSION_FILE.write_bytes(_cipher().encrypt(json.dumps(session.to_dict()).encode()))
        SESSION_FILE.chmod(0o600)
    except Exception as e:
        print("(sessione non salvata:", type(e).__name__, ")")


async def open_session(user: str, pwd: str):
    """Restituisce (sessione, elenco telecamere). Prima prova la sessione salvata, poi il refresh,
    e solo alla fine un login completo (che riusa comunque lo stesso identificativo di dispositivo)."""
    saved = load_session()
    if saved:
        s = OnTapo.from_dict(saved)
        for step in ("sessione salvata", "rinnovo"):
            try:
                if step == "rinnovo":
                    await s.refresh()
                devs = await s.devices()
                print(f"Accesso: {step}, nessun nuovo login")
                return s, devs
            except Unauthorized:
                continue
            except Exception as e:
                print(f"Accesso con {step} non riuscito ({type(e).__name__}), provo il login")
                break
        await s.aclose()
    s = OnTapo(email=user, terminal_uuid=(saved or {}).get("terminalUUID"))
    try:
        proc, _ = await s._do_login(user, pwd)       # stesso terminalUUID di prima: per Tapo è lo stesso dispositivo
        if proc is not None:
            raise RuntimeError("l'account chiede un codice MFA: non utilizzabile in automatico")
    except AttributeError:                          # libreria cambiata: login standard
        await s.aclose()
        s = await OnTapo.login(user, pwd, mfa=no_mfa)
    except Exception:
        await s.aclose()
        raise
    print("Accesso: login completo")
    return s, await s.devices()


async def snap(dev, key: str):
    with tempfile.TemporaryDirectory() as tmp:
        raw = Path(tmp) / "frame.jpg"
        await asyncio.wait_for(dev.snapshot(str(raw)), TIMEOUT)
        img = Image.open(raw).convert("RGB")
        if img.width > WIDTH:
            img = img.resize((WIDTH, round(img.height * WIDTH / img.width)), Image.LANCZOS)
        buf = io.BytesIO(); img.save(buf, "JPEG", quality=72, optimize=True, progressive=True)
        (OUT / f"{key}.jpg").write_bytes(buf.getvalue())
        return img, len(buf.getvalue())


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
        session, devices = await open_session(user, pwd)
    except Exception as e:
        print("ESITO: accesso non riuscito:", type(e).__name__, str(e).replace(user, "<account>")[:200]); return 1

    ok, failed = 0, []
    async with session:
        todo = []
        for dev in devices:
            name = getattr(dev, "name", "")
            if name in CAMERAS:
                todo.append(dev)
            else:
                print(f"- '{name}': non in elenco, salto")
        for attempt in (1, 2):
            if attempt == 2:
                if not failed:
                    break
                print(f"Secondo tentativo per {len(failed)} telecamera/e tra 20 s")
                await asyncio.sleep(20)
                todo, failed = failed, []
            for dev in todo:
                name = dev.name
                key, label = CAMERAS[name]
                try:
                    img, size = await snap(dev, key)
                    result["cameras"][key] = {"name": name, "label": label, "time": datetime.now(TZ).isoformat(timespec="minutes"),
                                              "width": img.width, "height": img.height, "ok": True}
                    print(f"- {label}: OK ({size // 1024} kB, {img.width}×{img.height})"); ok += 1
                except Exception as e:
                    msg = "nessuna risposta" if isinstance(e, asyncio.TimeoutError) else f"{type(e).__name__}: {str(e)[:120]}"
                    old = result["cameras"].get(key, {})
                    result["cameras"][key] = {**old, "name": name, "label": label, "ok": False, "error": msg}
                    print(f"- {label}: non riuscito ({msg})" + ("; riprovo dopo" if attempt == 1 else "; resta l'immagine precedente, se c'è"))
                    failed.append(dev)
        save_session(session)
    (OUT / "webcam.json").write_text(json.dumps(result, ensure_ascii=False, indent=1))
    print(f"ESITO: {ok} fotogrammi su {len(CAMERAS)}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
