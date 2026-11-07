import importlib

import joblib
import numpy as np
from fastapi.testclient import TestClient
from sklearn.linear_model import LinearRegression
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

from backend.app.main import app


def test_health():
    with TestClient(app) as client:
        response = client.get("/api/health")
        assert response.status_code == 200
        assert response.json() == {"status": "ok"}
        assert client.get("/missing").status_code == 404


def test_ml_imports():
    for name in ("pandas", "numpy", "sklearn", "joblib", "jupyter_core", "matplotlib", "plotly"):
        importlib.import_module(name)


def test_model_persistence(tmp_path):
    # Synthetic plumbing check, not a benchmark or model performance claim.
    x = np.arange(10, dtype=float).reshape(-1, 1)
    model = make_pipeline(StandardScaler(), LinearRegression()).fit(x, 2 * x.ravel())
    path = tmp_path / "smoke.joblib"
    joblib.dump(model, path)
    restored = joblib.load(path)  # Load only artifacts created by trusted code.
    np.testing.assert_allclose(restored.predict([[10.0]]), [20.0])
