#!/usr/bin/env python3
"""
S.A.S.S.O. – lettura oraria dei dati fotovoltaici da EPEVER Solar Guardian.

Legge SOLO in lettura dal cloud Solar Guardian (server hncloud.epsolarpv.com)
e scrive tre file JSON per la pagina del progetto:

  data/latest.json   valori correnti + produzione ora per ora di oggi
  data/daily.json    produzione giornaliera, ultimi ~60 giorni
  data/monthly.json  produzione mensile dell'anno in corso e del precedente

Le chiamate non sono un'API ufficiale: sono quelle che usa il portale web.
Per prudenza lo script può chiamare solo i percorsi elencati in READ_ONLY_PATHS:
nessuna chiamata può cambiare impostazioni o accendere/spegnere i carichi.

Variabili d'ambiente:
  EPEVER_ACCOUNT        email dell'account Solar Guardian      (secret)
  EPEVER_PASSWORD       password dell'account                  (secret)
  EPEVER_STATION_ID     id dell'impianto (default 67797 = Sasso_PoliTo)
  EPEVER_TOKEN_FILE     dove conservare il token tra un'esecuzione e l'altra
  EPEVER_DEVICE_DATA    "1" per provare anche i valori dei 4 regolatori (sperimentale)
  OUTPUT_DIR            cartella dei JSON (default: data)
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import requests

API = "https://hncloudapi.epsolarpv.com/usrCloud"
DISPATCH = "https://hncomm.epsolarpv.com:7100"
TZ = ZoneInfo("Europe/Rome")
STATION_ID = int(os.environ.get("EPEVER_STATION_ID", "67797"))
OUT = Path(os.environ.get("OUTPUT_DIR", "data"))
TOKEN_FILE = Path(os.environ.get("EPEVER_TOKEN_FILE", ".state/epever_token.json"))
REFRESH_AFTER_DAYS = 7          # il token dura 30 giorni: lo rinnoviamo ben prima
TIMEOUT = 30

# Unici percorsi ammessi. Tutti in sola lettura (più login e rinnovo del token).
READ_ONLY_PATHS = {
    "/user/login",
    "/user/refreshToken",
    "/vn/userView/getGeneratedEnergy",
    "/vn/userView/getPowerStationStatistics",
    "/vn/powerStation/getPowerStationList",
    "/dev/getDataDevs",
    "/vn/equipment/getEquipmentListLessPage",
    "/vn/equipment/getEquipment",
    # server dati dei dispositivi (sperimentale)
    "/device/getDevServerAddr",
    "/history/lastDatapoint",
}

# Variabili dei regolatori da pubblicare (dataIdentifier o nome inglese del portale)
DEVICE_VARS = {
    "PV Voltage": "pv_v", "PV Current": "pv_a", "PV Power": "pv_w",
    "Battery Voltage": "batt_v", "Battery Current": "batt_a", "Battery SOC": "batt_soc",
    "Battery Temperature": "batt_temp", "Charging Status": "charging_status",
    "Daily Generation": "day_kwh", "Total Generation": "total_kwh",
    "Load Power": "load_w", "Device Temperature": "device_temp",
}


class CaptchaRequired(RuntimeError):
    """Il server chiede il captcha (stato 1756): serve un login manuale dal portale."""


class Api:
    def __init__(self) -> None:
        self.s = requests.Session()
        self.s.headers.update({
            "Content-Type": "application/json;charset=UTF-8",
            "User-Agent": "SASSO-data-bot/1.0 (+Politecnico di Torino)",
        })
        self.token: str | None = None

    def post(self, path: str, body: dict | None = None, base: str = API, headers: dict | None = None) -> dict:
        if path not in READ_ONLY_PATHS:
            raise RuntimeError(f"percorso non ammesso (sola lettura): {path}")
        body = dict(body or {})
        if self.token and path != "/user/login":
            body["token"] = self.token
        r = self.s.post(base.rstrip("/") + path, data=json.dumps(body), headers=headers, timeout=TIMEOUT)
        r.raise_for_status()
        return r.json()


# ---------- token ----------

def _jwt_exp(token: str) -> float:
    try:
        payload = token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        return float(json.loads(base64.urlsafe_b64decode(payload))["exp"])
    except Exception:
        return 0.0


def load_token() -> dict | None:
    try:
        return json.loads(TOKEN_FILE.read_text())
    except Exception:
        return None


def save_token(token: str) -> None:
    TOKEN_FILE.parent.mkdir(parents=True, exist_ok=True)
    TOKEN_FILE.write_text(json.dumps({"token": token, "saved": time.time()}))
    try:
        TOKEN_FILE.chmod(0o600)
    except OSError:
        pass


def login(api: Api) -> None:
    account = os.environ.get("EPEVER_ACCOUNT")
    password = os.environ.get("EPEVER_PASSWORD")
    if not account or not password:
        raise SystemExit("Mancano EPEVER_ACCOUNT / EPEVER_PASSWORD")
    res = api.post("/user/login", {
        "account": account,
        "password": hashlib.md5(password.encode()).hexdigest(),   # come il portale
        "isPhone": 0,
    })
    if res.get("status") == 1756:
        raise CaptchaRequired("Solar Guardian chiede il captcha: accedi una volta dal portale e riprova più tardi.")
    if res.get("status") != 0:
        raise RuntimeError(f"login non riuscito: status={res.get('status')} info={res.get('info')}")
    data = res.get("data") or {}
    token = data.get("token") if isinstance(data, dict) else data
    if not token:
        raise RuntimeError("login riuscito ma senza token nella risposta")
    api.token = token
    save_token(token)
    print("login eseguito, token salvato")


def ensure_token(api: Api) -> None:
    saved = load_token()
    now = time.time()
    if saved and _jwt_exp(saved["token"]) - now > 2 * 86400:
        api.token = saved["token"]
        if now - saved.get("saved", 0) > REFRESH_AFTER_DAYS * 86400:
            try:
                res = api.post("/user/refreshToken")
                if res.get("status") == 0 and res.get("data"):
                    api.token = res["data"] if isinstance(res["data"], str) else res["data"].get("token")
                    save_token(api.token)
                    print("token rinnovato")
            except Exception as e:              # se il rinnovo fallisce usiamo quello vecchio
                print("rinnovo token non riuscito:", e)
        return
    login(api)


def call(api: Api, path: str, body: dict | None = None) -> dict:
    """Chiamata con un solo nuovo login se il token non è più valido."""
    res = api.post(path, body)
    if res.get("status") not in (0, None) and str(res.get("status")) in {"11", "12", "1702", "4010", "4011", "4012", "4013", "4014", "4017", "23000"}:
        print("token non valido (status", res.get("status"), ") – nuovo login")
        login(api)
        res = api.post(path, body)
    if res.get("status") != 0:
        raise RuntimeError(f"{path}: status={res.get('status')} info={res.get('info')}")
    return res


# ---------- dati impianto (verificati) ----------

def ms(dt: datetime) -> int:
    return int(dt.timestamp() * 1000)


def station_stats(api: Api, flag: int, start: datetime, end: datetime) -> list[dict]:
    """flag 1 = giorno (passo orario), 2 = mese (passo giornaliero), 3 = anno (passo mensile)."""
    res = call(api, "/vn/userView/getPowerStationStatistics", {
        "powerStationId": STATION_ID, "flag": flag,
        "startTimestamp": ms(start), "endTimestamp": ms(end) - 1,
    })
    return res.get("data") or []


def r2(x):
    return None if x is None else round(float(x), 2)


def hourly_today(api: Api, now: datetime) -> list[dict]:
    day0 = now.replace(hour=0, minute=0, second=0, microsecond=0)
    rows = station_stats(api, 1, day0, day0 + timedelta(days=1))
    out, prev_total = [], None
    for row in rows:
        total = row.get("generatingCapacitySum")
        t = datetime.fromtimestamp(row["timeStamp"], TZ)
        if total is None:
            continue
        # Energia dell'ora = differenza del contatore totale (più affidabile del
        # contatore "oggi", che ogni regolatore azzera quando si sveglia al mattino).
        kwh = None if prev_total is None else max(0.0, total - prev_total)
        out.append({"time": t.isoformat(), "total_kwh": r2(total), "hour_kwh": r2(kwh)})
        prev_total = total
    return out


def daily_series(api: Api, now: datetime, months: int = 2) -> list[dict]:
    rows = []
    first = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    starts = []
    for _ in range(months):
        starts.append(first)
        first = (first - timedelta(days=1)).replace(day=1)
    for start in reversed(starts):
        end = (start + timedelta(days=32)).replace(day=1)
        rows += station_stats(api, 2, start, end)
    out, seen = [], set()
    for row in sorted(rows, key=lambda r: r["timeStamp"]):
        d = datetime.fromtimestamp(row["timeStamp"], TZ).date().isoformat()
        if d in seen or row.get("generatingCapacitySum") is None:
            continue
        seen.add(d)
        out.append({"date": d, "kwh": r2(row.get("toDayGeneratingCapacity")),
                    "total_kwh": r2(row.get("generatingCapacitySum"))})
    # ricalcolo la produzione del giorno come differenza dei totali, quando possibile
    for a, b in zip(out, out[1:]):
        if a["total_kwh"] is not None and b["total_kwh"] is not None:
            b["kwh_from_total"] = r2(max(0.0, b["total_kwh"] - a["total_kwh"]))
    return out


def monthly_series(api: Api, now: datetime) -> list[dict]:
    out = []
    for year in (now.year - 1, now.year):
        start = datetime(year, 1, 1, tzinfo=TZ)
        for row in station_stats(api, 3, start, datetime(year + 1, 1, 1, tzinfo=TZ)):
            if row.get("generatingCapacitySum") is None:
                continue
            m = datetime.fromtimestamp(row["timeStamp"], TZ)
            out.append({"month": m.strftime("%Y-%m"), "kwh": r2(row.get("toDayGeneratingCapacity")),
                        "total_kwh": r2(row.get("generatingCapacitySum"))})
    return out


# ---------- dati dei regolatori (sperimentale) ----------

def device_values(api: Api) -> list[dict]:
    devs = call(api, "/dev/getDataDevs", {"powerStationId": STATION_ID, "page_param": {"offset": 0, "limit": 100}})
    gateways = [g["devid"] for g in (devs["data"].get("hnDevS") or [])]
    points, meta = [], {}
    for gw in gateways:
        lst = call(api, "/vn/equipment/getEquipmentListLessPage", {"gatewayId": gw, "pageNo": 1, "pageSize": 100})
        for eq in lst["data"]["list"]:
            det = call(api, "/vn/equipment/getEquipment", {
                "id": eq["id"], "pageShowConfigFlag": 12, "platformShowConfigFlag": 0, "infoFlag": None, "type": 0})
            for grp in det["data"].get("variableGroupList") or []:
                for v in grp.get("variableList") or []:
                    key = DEVICE_VARS.get(v.get("variableNameE", "").strip())
                    if not key:
                        continue
                    meta[v["dataPointId"]] = (eq["equipmentName"], key, v.get("unit") or "")
                    points.append({"deviceNo": v["deviceNo"], "slaveIndex": v["slaveIndex"],
                                   "itemId": v["itemId"], "dataPointId": v["dataPointId"]})
    if not points:
        return []
    hdr = {"token": api.token, "languageType": "1", "Content-type": "application/json"}
    addr = api.post("/device/getDevServerAddr", {"type": 3, "deviceNos": sorted({p["deviceNo"] for p in points})},
                    base=DISPATCH, headers=hdr)
    servers = (addr.get("data") or addr).get("devServer") or []
    by_dev: dict[str, dict] = {}
    for srv in servers:
        sel = [p for p in points if p["deviceNo"] in srv.get("deviceNos", [])]
        res = api.post("/history/lastDatapoint", {"devDatapoints": sel}, base=srv["url"], headers=hdr)
        for row in ((res.get("data") or {}).get("list") or []):
            name, key, unit = meta.get(row.get("dataPointId"), (None, None, None))
            if not name:
                continue
            d = by_dev.setdefault(name, {"name": name, "values": {}, "time": None})
            d["values"][key] = {"value": row.get("value"), "unit": unit}
            if row.get("time"):
                d["time"] = datetime.fromtimestamp(row["time"] / 1000, TZ).isoformat()
    return sorted(by_dev.values(), key=lambda d: d["name"])


def summarize_devices(devs: list[dict]) -> dict:
    def num(d, k):
        try:
            return float(d["values"][k]["value"])
        except Exception:
            return None
    pv = [num(d, "pv_w") for d in devs]
    batt_v = [num(d, "batt_v") for d in devs if num(d, "batt_v") is not None]
    soc = [num(d, "batt_soc") for d in devs if num(d, "batt_soc") is not None]
    return {
        "pv_power_w": r2(sum(x for x in pv if x is not None)) if any(x is not None for x in pv) else None,
        # batteria comune ai 4 regolatori: media delle letture
        "battery_v": r2(sum(batt_v) / len(batt_v)) if batt_v else None,
        "battery_soc": r2(sum(soc) / len(soc)) if soc else None,
    }


# ---------- main ----------

def write_json(name: str, obj) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    tmp = OUT / (name + ".tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=1))
    tmp.replace(OUT / name)


def main() -> int:
    now = datetime.now(TZ)
    api = Api()
    try:
        ensure_token(api)
    except CaptchaRequired as e:
        print("::error::" + str(e))
        return 2

    gen = call(api, "/vn/userView/getGeneratedEnergy")["data"]
    hours = hourly_today(api, now)
    today_kwh = None
    if hours:
        # produzione di oggi = totale ora - totale a mezzanotte
        today_kwh = r2(max(0.0, hours[-1]["total_kwh"] - hours[0]["total_kwh"]))

    latest = {
        "source": "EPEVER Solar Guardian (hncloud.epsolarpv.com)",
        "station": "Sasso_PoliTo",
        "updated": now.isoformat(timespec="seconds"),
        "data_time": hours[-1]["time"] if hours else None,
        "energy": {
            "today_kwh": today_kwh,
            "today_kwh_portal": r2(gen.get("toDayGeneratingCapacity")),
            "month_kwh": r2(gen.get("monthGeneratedEnergy")),
            "year_kwh": r2(gen.get("yearGeneratedEnergy")),
            "total_kwh": r2(gen.get("generatingCapacitySum")),
        },
        "alarms": gen.get("alarmSum"),
        "hourly_today": hours,
    }

    if os.environ.get("EPEVER_DEVICE_DATA") == "1":
        try:
            devs = device_values(api)
            latest["controllers"] = devs
            latest["now"] = summarize_devices(devs)
        except Exception as e:
            print("::warning::valori dei regolatori non disponibili:", e)
            latest["controllers_error"] = str(e)[:200]

    write_json("latest.json", latest)
    write_json("daily.json", {"updated": latest["updated"], "days": daily_series(api, now)})
    write_json("monthly.json", {"updated": latest["updated"], "months": monthly_series(api, now)})
    print(f"ok – oggi {today_kwh} kWh, totale {latest['energy']['total_kwh']} kWh")
    return 0


if __name__ == "__main__":
    sys.exit(main())
