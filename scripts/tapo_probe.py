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

    print("Login cloud Kasa:", "ok" if mgr._kasa_token else "no", "· login cloud Tapo:", "ok" if mgr._tapo_token else "no")
    if not mgr._tapo_token:
        # il login Tapo fallito viene ignorato in silenzio dalla libreria: lo ripetiamo per vedere il motivo
        try:
            from tplinkcloud.client import TPLinkApi
            r = TPLinkApi(cloud_type="tapo").login(user, pwd, mfa_callback=no_mfa)
            print("Secondo tentativo login Tapo:", "ok" if r and r.get("token") else "nessun token",
                  "· campi ricevuti:", sorted(r.keys()) if isinstance(r, dict) else type(r).__name__)
            if isinstance(r, dict):
                for k in ("errorCode", "errorMsg", "msg", "mfaType", "lockedMinutes", "remainAttemptTimes"):
                    if k in r: print(f"  {k}: {str(r[k])[:120]}")
            if r and r.get("token"):
                mgr._tapo_token, mgr._tapo_refresh_token = r.get("token"), r.get("refreshToken")
        except Exception as e:
            print("Login Tapo non riuscito:", type(e).__name__, str(e).replace(user, "<account>")[:200])
    # Il cloud Tapo elenca hub e sensori con un metodo diverso da getDeviceList: proviamo le varianti note
    if mgr._tapo_token:
        api = mgr._tapo_api
        types = ["SMART.TAPOHUB", "SMART.TAPOSENSOR", "SMART.IPCAMERA", "SMART.TAPOPLUG", "SMART.TAPOBULB",
                 "SMART.KASAHUB", "SMART.TAPOSWITCH", "SMART.TAPOROBOVAC"]
        for body in ({"method": "getDeviceList"},
                     {"method": "getDeviceListByPage", "params": {"deviceTypeList": types, "index": 0, "limit": 50}},
                     {"method": "getDeviceListByPage", "params": {"index": 0, "limit": 50}}):
            try:
                r = api._request_post_v1(body, mgr._tapo_token)
                lst = (r.result or {}).get("deviceList") if r.successful else None
                print(f"[Tapo {body['method']}{' con tipi' if 'deviceTypeList' in body.get('params', {}) else ''}]",
                      f"errore {r.error_code} {str(r.msg)[:80]}" if not r.successful else f"{len(lst or [])} dispositivi")
                for i in lst or []:
                    print(f"   - {i.get('deviceModel')} · {i.get('deviceType')} · '{name(i.get('alias'))}' · online={i.get('status')} · ruolo={i.get('role')}")
                    if (i.get("deviceType") or "").endswith("HUB") and r.successful:
                        from tplinkcloud.device_client import TPLinkDeviceClient
                        c = TPLinkDeviceClient(i.get("appServerUrl"), mgr._tapo_token, term_id=api._term_id,
                                               access_key=api.access_key, secret_key=api.secret_key,
                                               app_name=api._app_name, cloud_type="tapo")
                        try:
                            res = await c.pass_through_request(i.get("deviceId"), {"method": "get_child_device_list", "params": {"start_index": 0}})
                            if not res:
                                print("     sensori: nessuna risposta dal passthrough")
                            elif res.get("error_code", 0) != 0:
                                print("     sensori: error_code", res.get("error_code"))
                            for ch in ((res or {}).get("result") or {}).get("child_device_list", []):
                                keep = {k: ch.get(k) for k in ("model", "status", "current_temp", "current_humidity", "temp_unit",
                                                               "in_alarm", "water_leak_status", "battery_percentage", "at_low_battery") if k in ch}
                                print(f"     · {ch.get('model')} '{name(ch.get('nickname'))}': {json.dumps(keep, ensure_ascii=False)}")
                        except Exception as e:
                            print("     sensori: ERRORE", type(e).__name__, str(e)[:160])
            except Exception as e:
                print(f"[Tapo {body['method']}] ERRORE", type(e).__name__, str(e)[:160])
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
