"""
API key encryption using AES-256-GCM.
Keys are encrypted at rest, decrypted only at runtime.
Master key from explicit protected runtime configuration.
"""

import os
import json
import base64
import hashlib
import logging
from typing import Dict, Optional
from pathlib import Path

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
from runtime_paths import data_root

log = logging.getLogger(__name__)

SALT = b"trading-agent-v1"  # Fixed salt for key derivation


def keys_file() -> Path:
    configured = os.environ.get("TRADING_KEYS_FILE", "").strip()
    return Path(configured).expanduser().resolve() if configured else data_root() / ".keys.enc"


def _derive_key(master_password: str) -> bytes:
    """Derive 256-bit AES key from master password."""
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=32,
        salt=SALT,
        iterations=600_000,
    )
    return kdf.derive(master_password.encode())


def _get_master_key() -> str:
    """Get master password from environment."""
    key = os.environ.get("TRADING_MASTER_KEY")
    if not key:
        configured = os.environ.get("TRADING_MASTER_KEY_FILE", "").strip()
        if configured:
            keyfile = Path(configured).expanduser().resolve()
            if keyfile.exists():
                key = keyfile.read_text().strip()
    if not key:
        raise RuntimeError(
            "TRADING_MASTER_KEY not set. Run: export TRADING_MASTER_KEY='your-secure-password'"
        )
    return key


def encrypt(plaintext: str) -> str:
    """Encrypt a string. Returns base64-encoded ciphertext."""
    key = _derive_key(_get_master_key())
    aesgcm = AESGCM(key)
    nonce = os.urandom(12)
    ciphertext = aesgcm.encrypt(nonce, plaintext.encode(), None)
    return base64.b64encode(nonce + ciphertext).decode()


def decrypt(encrypted: str) -> str:
    """Decrypt a base64-encoded ciphertext."""
    key = _derive_key(_get_master_key())
    aesgcm = AESGCM(key)
    raw = base64.b64decode(encrypted)
    nonce, ciphertext = raw[:12], raw[12:]
    return aesgcm.decrypt(nonce, ciphertext, None).decode()


# ── Key Store ─────────────────────────────────────────────────

def load_keys() -> Dict[str, Dict[str, str]]:
    """
    Load encrypted exchange API keys.
    Returns: {"binance": {"api_key": "...", "secret": "...", "password": ""}, ...}
    """
    target = keys_file()
    if not target.exists():
        log.warning("No keys file found at %s. Create with: python -m exchange.secrets add <exchange>",
                    target)
        return {}

    try:
        encrypted_data = target.read_text().strip()
        if not encrypted_data:
            return {}
        plaintext = decrypt(encrypted_data)
        return json.loads(plaintext)
    except Exception as e:
        log.error("Failed to decrypt keys: %s", e)
        return {}


def save_keys(keys: Dict[str, Dict[str, str]]):
    """Encrypt and save exchange API keys to disk."""
    plaintext = json.dumps(keys, indent=2)
    encrypted = encrypt(plaintext)
    target = keys_file()
    target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    target.write_text(encrypted)
    target.chmod(0o600)  # Owner read/write only
    log.info("Keys saved to %s", target)


def add_key(exchange: str, api_key: str, secret: str, password: str = ""):
    """Add or update an exchange's API keys."""
    keys = load_keys()
    keys[exchange] = {
        "api_key": api_key,
        "secret": secret,
        "password": password,
    }
    save_keys(keys)


def remove_key(exchange: str):
    """Remove an exchange's API keys."""
    keys = load_keys()
    keys.pop(exchange, None)
    save_keys(keys)


def get_exchange_credentials(exchange: str) -> Optional[Dict[str, str]]:
    """Get decrypted credentials for an exchange."""
    keys = load_keys()
    return keys.get(exchange)


def load_secrets_into_env():
    """Load environment variables from encrypted keystore into os.environ.
    
    Call this AFTER load_dotenv() to overlay encrypted secrets.
    Does NOT overwrite values already set in the environment.
    """
    keys = load_keys()
    env_vars = keys.get("_env", {})
    loaded = 0
    for key, value in env_vars.items():
        if key not in os.environ:
            os.environ[key] = str(value)
            loaded += 1
    if loaded:
        log.info("Loaded %d secrets from keystore", loaded)


def set_env_secrets(env_vars: Dict[str, str]):
    """Store environment variables in the encrypted keystore."""
    keys = load_keys()
    keys["_env"] = env_vars
    save_keys(keys)
    log.info("Stored %d env secrets to keystore", len(env_vars))


# ── CLI ───────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys
    if len(sys.argv) < 2:
        print("Usage: python -m exchange.secrets <add|remove|list|test> [exchange]")
        sys.exit(1)

    cmd = sys.argv[1]

    if cmd == "add":
        exchange = sys.argv[2] if len(sys.argv) > 2 else input("Exchange (binance/coinbase/bybit/kraken): ")
        api_key = input("API Key: ")
        secret = input("API Secret: ")
        password = input("Password (optional): ")
        add_key(exchange.strip(), api_key.strip(), secret.strip(), password.strip())
        print(f"✅ Keys saved for {exchange}")

    elif cmd == "remove":
        exchange = sys.argv[2] if len(sys.argv) > 2 else input("Exchange to remove: ")
        remove_key(exchange.strip())
        print(f"✅ Keys removed for {exchange}")

    elif cmd == "list":
        keys = load_keys()
        if not keys:
            print("No keys configured.")
        for ex in keys:
            print(f"  {ex}: configured ✓")

    elif cmd == "test":
        from .adapter import ExchangeAdapter, ExchangeID
        exchange = sys.argv[2] if len(sys.argv) > 2 else "binance"
        creds = get_exchange_credentials(exchange)
        if not creds:
            print(f"❌ No keys for {exchange}")
            sys.exit(1)
        try:
            adapter = ExchangeAdapter(
                ExchangeID(exchange),
                api_key=creds["api_key"],
                secret=creds["secret"],
                password=creds.get("password", ""),
            )
            if adapter.test_connection():
                balance = adapter.fetch_balance()
                print(f"✅ Connected to {exchange}")
                for asset, total in sorted(balance.total.items()):
                    if total > 0:
                        print(f"  {asset}: {total:.4f}")
            else:
                print(f"❌ Connection test failed")
        except Exception as e:
            print(f"❌ Error: {e}")
