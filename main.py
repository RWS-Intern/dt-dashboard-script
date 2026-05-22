import os
import requests
from datetime import datetime
from supabase import create_client

# ─────────────────────────────────────────
# CONFIGURATION
# ─────────────────────────────────────────
API_URL      = os.environ.get("IIOT_API_URL")
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")

# ─────────────────────────────────────────
# DT CONSTANTS — only DT002 (TRANSFORMER1)
# ─────────────────────────────────────────
DT_CONSTANTS = {
    "939": {
        "dt_id":         "DT002",
        "i_rated":       133.33,
        "offset_val":    10,
        "tank_height":   60,
        "initial_level": 60,
        "initial_oti":   50.2
    }
}

# ─────────────────────────────────────────
# DATA VALIDATION
# ─────────────────────────────────────────
def is_valid(raw):
    t1 = float(raw["Temp1"])
    t2 = float(raw["Temp2"])
    r  = float(raw["CURRENT_R"])
    y  = float(raw["CURRENT_Y"])
    b  = float(raw["CURRENT_B"])

    if t1 < 5 or t1 > 120:
        print(f"❌ Invalid Temp1: {t1} — skipping")
        return False
    if t2 < 5 or t2 > 120:
        print(f"❌ Invalid Temp2: {t2} — skipping")
        return False
    if r < 0 or y < 0 or b < 0:
        print(f"❌ Negative current — skipping")
        return False
    if r > 200 or y > 200 or b > 200:
        print(f"❌ Unrealistic current (>200A) — skipping")
        return False
    return True

# ─────────────────────────────────────────
# THRESHOLD FUNCTIONS
# ─────────────────────────────────────────
def oti_status(v):
    return "CRITICAL" if v > 95 else ("WARNING" if v > 85 else "NORMAL")

def wti_status(v):
    return "CRITICAL" if v > 110 else ("WARNING" if v > 95 else "NORMAL")

def k_status(v):
    return "CRITICAL" if v > 1.0 else ("WARNING" if v > 0.8 else "NORMAL")

def imb_status(v):
    return "CRITICAL" if v > 80 else ("WARNING" if v > 40 else "NORMAL")

def oil_status(dT):
    return "CRITICAL" if dT > 10 else ("WARNING" if dT > 5 else "NORMAL")

# ─────────────────────────────────────────
# FORMULA ENGINE
# ─────────────────────────────────────────
def calculate(raw, const):
    t1  = float(raw["Temp1"])
    t2  = float(raw["Temp2"])
    r   = float(raw["CURRENT_R"])
    y   = float(raw["CURRENT_Y"])
    b   = float(raw["CURRENT_B"])
    pf  = float(raw["AVG_PF"])

    I_max  = max(r, y, b)
    I_min  = min(r, y, b)
    I_avg  = (r + y + b) / 3
    K      = I_max / const["i_rated"]

    OTI       = t1 + const["offset_val"] + (K * K * 5)
    WTI       = OTI + 26 * pow(K, 1.6)
    imbalance = ((I_max - I_min) / I_avg * 100) if I_avg > 0 else 0
    dT        = t1 - t2

    expected_level = const["initial_level"] * (
        1 + 0.00075 * (OTI - const["initial_oti"])
    )
    oil_loss_pct = max(0, (const["initial_level"] - expected_level)
                       / const["initial_level"] * 100)
    theft = "ALERT" if oil_loss_pct > 1 else "OK"

    from_top  = const["tank_height"] - expected_level
    level_pct = ((const["tank_height"] - from_top) / const["tank_height"] * 100)
    level_pct = max(0, min(100, level_pct))

    oil_s = oil_status(dT)

    statuses = [oti_status(OTI), wti_status(WTI), k_status(K), imb_status(imbalance), oil_s]
    overall  = "CRITICAL" if "CRITICAL" in statuses else ("WARNING" if "WARNING" in statuses else "NORMAL")

    return {
        "dt_id":         const["dt_id"],
        "timestamp": raw["DATA_STAMP"] + "+05:30",
        "temp1":         round(t1, 3),
        "temp2":         round(t2, 3),
        "current_r":     round(r, 3),
        "current_y":     round(y, 3),
        "current_b":     round(b, 3),
        "k":             round(K, 4),
        "load_pct":      round(K * 100, 2),
        "oti":           round(OTI, 2),
        "wti":           round(WTI, 2),
        "imbalance_pct": round(imbalance, 2),
        "dt_diff":       round(dT, 2),
        "oil_status":    oil_s,
        "level_pct":     round(level_pct, 2),
        "oil_theft":     theft,
        "overall":       overall,
        "avg_pf":        round(pf, 3)
    }

# ─────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────
def run():
    print(f"[{datetime.now()}] Fetching API data...")

    try:
        response = requests.get(API_URL, timeout=30)
        data     = response.json()
    except Exception as e:
        print(f"API error: {e}")
        return

    db = create_client(SUPABASE_URL, SUPABASE_KEY)

    for device in data:
        location_id = device.get("LOCATION_ID")

        if location_id not in DT_CONSTANTS:
            print(f"Skipping LOCATION_ID {location_id} ({device.get('LOCATION_NAME', 'Unknown')})")
            continue

        # Validate reading before saving
        if not is_valid(device):
            print(f"⚠️ Bad reading from {device.get('LOCATION_NAME')} — not saved")
            continue

        const = DT_CONSTANTS[location_id]

        try:
            row = calculate(device, const)
            db.table("dt_readings").insert(row).execute()
            print(f"✅ {const['dt_id']} ({device['LOCATION_NAME']}) saved — "
                  f"OTI:{row['oti']} WTI:{row['wti']} "
                  f"Load:{row['load_pct']}% Imb:{row['imbalance_pct']}% "
                  f"Overall:{row['overall']}")
        except Exception as e:
            print(f"❌ Error saving {const['dt_id']}: {e}")

    print(f"[{datetime.now()}] Done\n")


if __name__ == "__main__":
    run()
