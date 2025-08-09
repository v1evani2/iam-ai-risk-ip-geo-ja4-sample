import argparse
import csv
import random
from datetime import datetime, timedelta, timezone

USERS = [f"u{i}" for i in range(1, 2001)]
DEVICES = [f"dev-{i}" for i in range(1, 1501)]
IPS_GOOD = [f"198.51.100.{i}" for i in range(1, 200)]
IPS_BAD = [f"203.0.113.{i}" for i in range(1, 50)] + ["203.0.113.200","203.0.113.201"]
UAS = [
    "Mozilla/5.0 (Macintosh; Intel Mac OS X)",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
    "Mozilla/5.0 (X11; Linux x86_64)",
    "Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X)",
]

COUNTRIES = ["US", "US", "US", "US", "CA", "GB", "IN", "DE", "AU"]
REGIONS = ["CA", "WA", "NY", "TX", "MA", "ON", "BC", "MH", "BE"]
CITIES = ["San Jose", "Seattle", "New York", "Austin", "Boston", "Toronto", "Vancouver", "Mumbai", "Berlin"]


def random_ts(base: datetime, days_back=30):
    offset = timedelta(days=random.randint(0, days_back), hours=random.randint(0,23), minutes=random.randint(0,59))
    return (base - offset).replace(tzinfo=timezone.utc).isoformat()


def main(out_path: str, rows: int):
    now = datetime.now(timezone.utc)
    with open(out_path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["timestamp","user_id","ip","user_agent","device_id","success","country","region","city"])

        for _ in range(rows):
            user = random.choice(USERS)
            # Normal traffic: good IPs mostly
            if random.random() < 0.9:
                ip = random.choice(IPS_GOOD)
                success = random.random() < 0.92
            else:
                ip = random.choice(IPS_BAD)
                success = random.random() < 0.6

            ua = random.choice(UAS)
            dev = random.choice(DEVICES)
            country = random.choice(COUNTRIES)
            region = random.choice(REGIONS)
            city = random.choice(CITIES)
            ts = random_ts(now, days_back=45)

            # Inject some anomalies
            if random.random() < 0.02:
                ip = f"203.0.113.{random.randint(200, 250)}"
                country = "RU"
                region = "XX"
                city = "??"
                success = random.random() < 0.3

            ja4 = f"t13d301000_{random.randint(1000,9999)}_{random.randint(1000,9999)}"
            ja4h = f"ge11nn100000_{random.randint(1000,9999)}"
            ja4s = f"{random.randint(100000,999999)}"
            w.writerow([ts, user, ip, ua, dev, int(success), country, region, city])  # legacy columns
            # NOTE: JA4 fields are not in CSV columns by default; used in JSON scoring & Kafka paths


    print(f"Wrote {rows} rows to {out_path}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="data/sample_logins.csv")
    ap.add_argument("--rows", type=int, default=20000)
    args = ap.parse_args()
    main(args.out, args.rows)
