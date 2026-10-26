"""Environment-driven configuration. No secrets in code."""
import os


def _load_env_file() -> None:
    """Optional .env next to the repo root (git-ignored). Real env vars win."""
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    path = os.path.join(root, ".env")
    try:
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    os.environ.setdefault(k.strip(), v.strip())
    except FileNotFoundError:
        pass


_load_env_file()

RECEIPT_SECRET = os.environ.get("LEASH_RECEIPT_SECRET", "dev-secret-change-me")
DATA_DIR = os.environ.get("LEASH_DATA_DIR", os.path.join(os.getcwd(), "data"))
ZAPIER_HOOK_URL = os.environ.get("LEASH_ZAPIER_HOOK_URL", "")

# PayPal: "live" hits the sandbox/live API; "dry" fakes it (tests, offline dev)
PAYPAL_MODE = os.environ.get("LEASH_PAYPAL_MODE", "live" if os.environ.get(
    "LEASH_PAYPAL_CLIENT_ID") else "dry")
PAYPAL_CLIENT_ID = os.environ.get("LEASH_PAYPAL_CLIENT_ID", "")
PAYPAL_SECRET = os.environ.get("LEASH_PAYPAL_SECRET", "")
PAYPAL_ENV = os.environ.get("LEASH_PAYPAL_ENV", "sandbox")  # sandbox | live

# Split-evasion detection: N attempts to the same payee within WINDOW seconds
# whose total exceeds this fraction of the per-transaction limit.
SPLIT_WINDOW_SECONDS = 15 * 60
SPLIT_MIN_ATTEMPTS = 3
SPLIT_SUM_FRACTION = 0.8

# Per-transaction overshoot tolerance: above the limit but <= this multiple is
# a HOLD (human decides); beyond it is an outright DENY.
OVERSHOOT_HOLD_FACTOR = 2.0
