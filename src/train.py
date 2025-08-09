import argparse
import os
import joblib
import pandas as pd
from sklearn.ensemble import IsolationForest

from feature import fit_encoders, save_encoders, df_to_features
from geoutils import lookup_city_coords


def train(input_csv: str, model_dir: str, n_estimators: int = 200, random_state: int = 42):
    os.makedirs(model_dir, exist_ok=True)
    df = pd.read_csv(input_csv)

    enc = fit_encoders(df)
    X, _ = df_to_features(df, enc)

    # IsolationForest (unsupervised)
    clf = IsolationForest(
        n_estimators=n_estimators,
        contamination=.01, #"auto"
        random_state=random_state,
        n_jobs=-1
    )
    clf.fit(X)

    # Build per-user mean coordinates (baseline)
    user_geo = {}
    if 'user_id' in df.columns and 'city' in df.columns:
        tmp = {}
        for _, r in df[['user_id','city']].fillna('').iterrows():
            u = str(r['user_id'])
            city = r['city'] if r['city'] else None
            coords = lookup_city_coords(city)
            if not coords:
                continue
            tmp.setdefault(u, {'sum_lat':0.0, 'sum_lon':0.0, 'cnt':0})
            tmp[u]['sum_lat'] += coords[0]
            tmp[u]['sum_lon'] += coords[1]
            tmp[u]['cnt'] += 1
        for u, agg in tmp.items():
            if agg['cnt'] > 0:
                user_geo[u] = {
                    'lat': agg['sum_lat']/agg['cnt'],
                    'lon': agg['sum_lon']/agg['cnt'],
                    'n': agg['cnt']
                }

    joblib.dump(clf, os.path.join(model_dir, "model.joblib"))
    save_encoders(enc, os.path.join(model_dir, "encoders.json"))
    # Save user geo baselines
    import json
    with open(os.path.join(model_dir, 'user_geo.json'), 'w') as gf:
        json.dump(user_geo, gf)
    print(f"Saved model, encoders, and user_geo to: {model_dir}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True, help="Path to training CSV")
    parser.add_argument("--model_dir", required=True, help="Output directory for model artifacts")
    parser.add_argument("--n_estimators", type=int, default=200)
    parser.add_argument("--random_state", type=int, default=42)
    args = parser.parse_args()

    train(args.input, args.model_dir, args.n_estimators, args.random_state)
