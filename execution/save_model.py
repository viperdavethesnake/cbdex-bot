"""
Train and save the final AERO/WETH model for live use.

Trains on the full 90-day dataset (no validation holdout) with the
hyperparameters confirmed in walk-forward evaluation.
Saves the fitted Pipeline (StandardScaler + RandomForest) to models/.

Usage:
    python execution/save_model.py
"""

import pickle
from pathlib import Path

from sklearn.ensemble import RandomForestClassifier
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

from research.features import build_features, FEATURE_COLS_AERO, AERO_REGIME_THRESHOLD
from research.labels import attach_labels

MODEL_PATH = Path("models/aero_weth_rf.pkl")
MODEL_PATH.parent.mkdir(exist_ok=True)

RF_PARAMS = {
    "n_estimators":     300,
    "max_depth":        8,
    "min_samples_leaf": 50,
    "class_weight":     "balanced",
    "random_state":     42,
    "n_jobs":           -1,
}

if __name__ == "__main__":
    import polars as pl

    print("Loading AERO/WETH features...")
    df = build_features("AERO_WETH")
    df = attach_labels(df, "AERO_WETH")
    df = df.filter(pl.col("vol_15") >= AERO_REGIME_THRESHOLD)

    X = df[FEATURE_COLS_AERO].to_numpy()
    y = df["label"].to_numpy()

    print(f"Training on {len(df):,} rows  ({(y==1).sum()} LONG  {(y==-1).sum()} SHORT  {(y==0).sum()} HOLD)")

    model = Pipeline([
        ("scaler", StandardScaler()),
        ("rf",     RandomForestClassifier(**RF_PARAMS)),
    ])
    model.fit(X, y)

    with open(MODEL_PATH, "wb") as f:
        pickle.dump(model, f)

    print(f"Model saved to {MODEL_PATH}")
    print(f"Classes: {model.named_steps['rf'].classes_}")
    print("Top 5 features:")
    rf = model.named_steps["rf"]
    for feat, imp in sorted(zip(FEATURE_COLS_AERO, rf.feature_importances_), key=lambda x: -x[1])[:5]:
        print(f"  {feat}: {imp:.4f}")
