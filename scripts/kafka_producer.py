#!/usr/bin/env python3
"""
Send login CSV rows as JSON to Kafka topic.
Usage:
  export KAFKA_BROKER=localhost:9092
  python scripts/kafka_producer.py --csv data/sample_logins.csv --topic login-events
"""
import argparse, csv, json, os, time
from kafka import KafkaProducer

def main(csv_path: str, topic: str, broker: str):
    prod = KafkaProducer(
        bootstrap_servers=[broker],
        value_serializer=lambda v: json.dumps(v).encode('utf-8'),
        linger_ms=50,
        acks='all',
    )
    with open(csv_path, "r") as f:
        r = csv.DictReader(f)
        count = 0
        for row in r:
            # normalize types
            row["success"] = int(row.get("success", "0")) in (1, "1", True, "true", "True")
            prod.send(topic, row)
            count += 1
            if count % 1000 == 0:
                print(f"Sent {count} messages...")
                prod.flush()
            time.sleep(0.001)  # small pacing for demo
    prod.flush()
    print("Done. Total sent:", count)

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", default="data/sample_logins.csv")
    ap.add_argument("--topic", default="login-events")
    args = ap.parse_args()
    broker = os.environ.get("KAFKA_BROKER", "localhost:9092")
    main(args.csv, args.topic, broker)
