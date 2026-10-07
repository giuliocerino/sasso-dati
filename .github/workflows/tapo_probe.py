#!/usr/bin/env python3
"""
S.A.S.S.O. – prova di lettura Tapo dal cloud (solo diagnostica, solo lettura).

Fa login con l'account Tapo in sola visione, elenca i dispositivi visibili e, per gli hub,
prova a leggere i sensori collegati (temperatura, umidità, perdite) tramite il cloud.
Non accende, spegne o modifica nulla: usa soltanto richieste "get_*".

Il log delle Actions di un repository pubblico è visibile a tutti: per questo lo script
non stampa ID, MAC, IP né altri identificativi, solo modello, nome e letture.
"""
import asyncio
import base64
import json
import os
import sys

from tplinkcloud import TPLinkDeviceManager, TPLinkMFARequiredError, TPLinkAuthError

READ_ONLY = ("get_child_device_list", "get_device_info")


def name(s):
    if not s:
        return "?"
    try:
        d = base64.b64decode(s, validate=True).decode("utf-8")
        if d.isprintable():
            return d
    except Exception:
        pass
    return s


def no_mfa(*_):
    raise TPLinkMFARequiredError("MFA richiesta")


async def ask(dev, method, params=None):
    assert method in READ_ONLY
    req = {"method": method}
    if params is not None:
        req["params"] = params
    return await dev._client.pass_through_request(dev.device_id, req)


async def main():
    user, pwd = os.environ.get("TAPO_ACCOUNT"), os.environ.get("TAPO_PASSWORD")
    if not user or not pwd:
        print("Mancano TAPO_ACCOUNT / TAPO_PASSWORD"); return 1
    try:
        mgr = TPLinkDeviceManager(user, pwd, prefetch=False, include_tapo=True, mfa_callback=no_mfa)
    except TPLinkMFARequiredError:
        print("ESITO: l'account chiede la verifica in due passaggi (MFA): non utilizzabile in automatico."); return 1
    except TPLinkAuthError as e:
        print("ESITO: login rifiutato:", type(e).__name__); return 1
    except Exception as e:
        print("ESITO: login non riuscito:", type(e).__name__, str(e)[:160]); return 1

    try:
        devices = await mgr.get_devices()
    except Exception as e:
        print("ESITO: elenco dispositivi non riuscito:", type(e).__name__, str(e)[:160]); return 1
    tapo = [d for d in devices if getattr(d, "cloud_type", "") == "tapo" or getattr(d.device_info, "cloud_type", "") == "tapo"]
    print(f"Dispositivi visibili: {len(devices)} (Tapo: {len(tapo)})")
    for d in devices:
        i = d.device_info
        print(f"- {i.device_model} · {i.device_type} · '{name(i.alias)}' · online={i.status} · ruolo={i.role}")

    for d in devices:
        i = d.device_info
        if not (i.device_model or "").upper().startswith(("H100", "H200", "H500")):
            continue
        print(f"\nHub {i.device_model} '{name(i.alias)}': lettura dei sensori via cloud…")
        try:
            res = await ask(d, "get_child_device_list", {"start_index": 0})
        except Exception as e:
            print("  ERRORE:", type(e).__name__, str(e)[:160]); continue
        if not res:
            print("  nessuna risposta (passthrough non consentito per questo account/dispositivo?)"); continue
        if res.get("error_code", 0) != 0:
            print("  error_code:", res.get("error_code")); continue
        for c in (res.get("result") or {}).get("child_device_list", []):
            keep = {k: c.get(k) for k in ("model", "category", "status", "current_temp", "current_humidity",
                                          "temp_unit", "in_alarm", "water_leak_status", "battery_percentage",
                                          "at_low_battery", "report_interval", "last_onboarding_timestamp") if k in c}
            print(f"  · {c.get('model')} '{name(c.get('nickname'))}': {json.dumps(keep, ensure_ascii=False)}")

    for d in devices:
        i = d.device_info
        if (i.device_type or "").upper().endswith("IPCAMERA") or (i.device_model or "").upper().startswith(("C4", "C1", "C2", "C5")):
            print(f"\nTelecamera {i.device_model} '{name(i.alias)}': prova get_device_info via cloud…")
            try:
                res = await ask(d, "get_device_info")
                print("  risposta:", "ok" if res and res.get("error_code", 0) == 0 else (res and res.get("error_code")))
            except Exception as e:
                print("  ERRORE:", type(e).__name__, str(e)[:160])
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
