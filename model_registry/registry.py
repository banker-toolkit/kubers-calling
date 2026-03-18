"""
KUBER'S CALLING — model_registry/registry.py
=============================================
Layer 7: ML model registry.
"""
import os, sys, json, logging
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import MODEL_STORE_PATH, ENABLE_ML_STRATEGY

log = logging.getLogger("registry")

_REGISTRY_FILE = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "model_registry.json"
)

def get_live_model():
    if not ENABLE_ML_STRATEGY:
        return None
    if not os.path.exists(_REGISTRY_FILE):
        return None
    try:
        with open(_REGISTRY_FILE) as f:
            meta = json.load(f)
        model_path = meta.get("live_model_path", "")
        if not model_path or not os.path.exists(model_path):
            return None
        import pickle
        with open(model_path, "rb") as f:
            model = pickle.load(f)
        log.info("[registry] Loaded live model: %s", meta.get("model_name"))
        return model
    except Exception as e:
        log.error("[registry] Model load failed: %s", e)
        return None
