import base64
import logging
import os
import time

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.hazmat.primitives.asymmetric.rsa import RSAPrivateKey
from cryptography.hazmat.primitives.serialization import load_pem_private_key

logger = logging.getLogger(__name__)


class Auth:
    def __init__(self, config):
        self.config = config
        self.private_key = None
        # Fail fast: load + validate the key as soon as Auth exists.
        self._with_error_handling(self.load_private_key, config)

    # 1. load_private_key
    def load_private_key(self, config):
        path = config.private_key_path
        with open(path, "rb") as f:
            key_bytes = f.read()
        key_object = load_pem_private_key(key_bytes, password=None)
        self.validate_private_key(key_object)  
        self.private_key = key_object
        return key_object

    # 2. validate_private_key
    def validate_private_key(self, key_object):
        path = self.config.private_key_path
        if not os.path.exists(path):
            logger.critical(f"private key file not found at {path}")
            raise FileNotFoundError(f"private key file not found at {path}")
        if not isinstance(key_object, RSAPrivateKey):
            logger.critical("private key is not RSA type")
            raise TypeError("private key is not RSA type")
        return True

    # 3. generate_timestamp
    def generate_timestamp(self):
        return str(int(time.time() * 1000))

    # 4. build_message_string
    def build_message_string(self, timestamp, method, path):
        method = method.upper()
        if "?" in path:
            path = path.split("?")[0]
        return timestamp + method + path

    # 5. sign_message
    def sign_message(self, message_string):
        message_bytes = message_string.encode("utf-8")
        signature_bytes = self.private_key.sign(
            message_bytes,
            padding.PSS(
                mgf=padding.MGF1(hashes.SHA256()),
                salt_length=hashes.SHA256().digest_size,
            ),
            hashes.SHA256(),
        )
        return base64.b64encode(signature_bytes).decode("utf-8")

    # 6. get_headers
    def get_headers(self, method, path):
        timestamp = self.generate_timestamp()
        message_string = self.build_message_string(timestamp, method, path)
        signature = self._with_error_handling(self.sign_message, message_string)
        return {
            "KALSHI-ACCESS-KEY": self.config.key_id,
            "KALSHI-ACCESS-SIGNATURE": signature,
            "KALSHI-ACCESS-TIMESTAMP": timestamp,
            "Content-Type": "application/json",
        }

    # 7. shared error-handling wrapper, used by both __init__ and get_headers
    def _with_error_handling(self, func, *args):
        try:
            return func(*args)
        except FileNotFoundError:
            logger.critical("auth: key file not found")
            raise
        except (ValueError, TypeError):
            logger.critical("auth: key validation failed")
            raise
        except Exception:
            logger.error("auth: message signing failed")
            raise


