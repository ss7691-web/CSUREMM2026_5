import logging
from urllib.parse import urlparse
import requests
import config
import time
from auth import Auth
import random

logger = logging.getLogger(__name__)


class RestError(Exception):
    def __init__(self, method, url, status_code, body):
        self.method = method
        self.url = url
        self.status_code = status_code
        self.body = body
        super().__init__(f"{method} {url} -> HTTP {status_code}: {body}")

    @property
    def is_transient(self):
        return (self.status_code is None) or (self.status_code == 429) or (self.status_code >= 500)

class RestConn:
    def __init__(self, min_interval=0.1, max_retries=8, base_backoff=0.5, max_backoff=30.0):
        self.auth = Auth(config)
        self.base_url = config.BASE_URL.rstrip("/")
        self.sign_prefix = urlparse(config.BASE_URL).path.rstrip("/")
        self.min_interval = min_interval
        self._last_request_ts = 0.0
        self.max_retries = max_retries
        self.base_backoff = base_backoff
        self.max_backoff = max_backoff

    def _resolve(self, path):
        path = "/" + path.lstrip("/")
        request_url = self.base_url + path
        sign_path = self.sign_prefix + path
        return request_url, sign_path

    def _throttle(self):
        now = time.monotonic()
        wait = self.min_interval - (now - self._last_request_ts)
        if wait > 0:
            time.sleep(wait)
        self._last_request_ts = time.monotonic()

    def _send_once(self, path, params=None):
        self._throttle()
        request_url, sign_path = self._resolve(path)
        headers = self.auth.get_headers("GET", sign_path)
        try:
            resp = requests.get(request_url, headers=headers, params=params, timeout=10)
        except (requests.Timeout, requests.ConnectionError) as e:
            logger.warning("request failed: GET %s -> network error: %s", request_url, e)
            raise RestError("GET", request_url, None, f"network error: {e}")
        if not resp.ok:
            logger.warning("request failed: GET %s -> HTTP %s: %.200s",
                           resp.url, resp.status_code, resp.text)
            raise RestError("GET", resp.url, resp.status_code, resp.text)
        return resp.json()
    
    def get_with_auth(self, path, params=None):
        attempt = 0
        while True:
            try:
                return self._send_once(path, params)
            except RestError as e:
                if not e.is_transient or attempt >= self.max_retries:
                    raise
                delay = min(self.base_backoff * (2 ** attempt), self.max_backoff) \
                        + random.uniform(0, self.base_backoff)
                logger.info("retrying GET %s in %.2fs (attempt %d/%d, last status %s)",
                            path, delay, attempt + 1, self.max_retries, e.status_code)
                time.sleep(delay)
                attempt += 1
    

    def paginated_get(self, path, params=None):
        params = dict(params or {})   # copy: don't mutate the caller's dict
        cursor = None
        while True:
            if cursor:
                params["cursor"] = cursor
            page = self.get_with_auth(path, params=params)
            yield page
            cursor = page.get("cursor")
            if not cursor:            # "" or None -> no more pages
                break
