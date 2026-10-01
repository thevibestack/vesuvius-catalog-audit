"""Anonymous, metadata-only access to the Vesuvius open-data S3 bucket.

Standard library only: the audit must be runnable on any machine that has Python,
without a virtualenv, an SDK, credentials, or an install step.

What "metadata-only" means here, concretely:

* ``get`` performs a plain GET of small keys (``meta.json``, ``.zattrs``,
  ``.zarray``) and *ranged* GETs for anything larger -- a 2 KB range on a
  multi-megabyte ``x.tif`` is enough to read its TIFF header.
* ``list_objects`` is ``ListObjectsV2``: object *names*, never bodies.
* ``head`` is a HEAD request.

No chunk of voxel data is ever downloaded. The bucket is public-read; no
credentials are used and none are needed.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

S3_NS = "{http://s3.amazonaws.com/doc/2006-03-01/}"
DEFAULT_BUCKET_URL = "https://vesuvius-challenge-open-data.s3.amazonaws.com/"
DEFAULT_BUCKET = "vesuvius-challenge-open-data"

#: Errors worth retrying: throttling and transient 5xx.
RETRY_STATUS = {429, 500, 502, 503, 504}


class S3Error(RuntimeError):
    """Any failure talking to the bucket."""


class NotFound(S3Error):
    """HTTP 404 -- the key or prefix does not exist."""

    def __init__(self, key: str):
        super().__init__(f"not found: {key}")
        self.key = key


class AnonymousS3:
    """Minimal anonymous S3 client over HTTPS.

    Thread-safe: each call builds its own request; the shared state is read-only.
    """

    def __init__(
        self,
        base_url: str = DEFAULT_BUCKET_URL,
        bucket: str = DEFAULT_BUCKET,
        retries: int = 4,
        timeout: float = 60.0,
        user_agent: str = "vesuvius-catalog-audit/1.0 (metadata-only)",
    ) -> None:
        self.base_url = base_url if base_url.endswith("/") else base_url + "/"
        self.bucket = bucket
        self.retries = retries
        self.timeout = timeout
        self.user_agent = user_agent
        self.requests = 0
        self.bytes_read = 0

    # ---------------------------------------------------------------- transport

    def _raw(self, url: str, byte_range: tuple[int, int] | None = None) -> bytes:
        last: Exception | None = None
        for attempt in range(self.retries):
            req = urllib.request.Request(url)
            req.add_header("User-Agent", self.user_agent)
            if byte_range is not None:
                req.add_header("Range", f"bytes={byte_range[0]}-{byte_range[1]}")
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                    body = resp.read()
                self.requests += 1
                self.bytes_read += len(body)
                return body
            except urllib.error.HTTPError as exc:
                if exc.code == 404:
                    raise NotFound(url) from exc
                if exc.code not in RETRY_STATUS or attempt == self.retries - 1:
                    raise S3Error(f"HTTP {exc.code} for {url}") from exc
                last = exc
            except (urllib.error.URLError, TimeoutError, OSError) as exc:
                if attempt == self.retries - 1:
                    raise S3Error(f"{type(exc).__name__} for {url}: {exc}") from exc
                last = exc
            time.sleep(min(2.0 ** attempt * 0.5, 8.0))
        raise S3Error(f"exhausted retries for {url}: {last}")

    # ------------------------------------------------------------------- reads

    def get(self, key: str, byte_range: tuple[int, int] | None = None) -> bytes:
        """GET ``key`` (optionally a byte range). Raises :class:`NotFound` on 404."""
        key = key.lstrip("/")
        return self._raw(self.base_url + urllib.parse.quote(key, safe="/"), byte_range)

    def get_json(self, key: str):
        try:
            return json.loads(self.get(key))
        except json.JSONDecodeError as exc:
            raise S3Error(f"{key} is not JSON: {exc}") from exc

    def get_json_gz_or_plain(self, key: str):
        """The catalogue ``metadata.json`` is served gzipped to anonymous readers."""
        import gzip

        raw = self.get(key)
        if raw[:2] == b"\x1f\x8b":
            raw = gzip.decompress(raw)
        return json.loads(raw)

    def head(self, key: str) -> dict:
        key = key.lstrip("/")
        url = self.base_url + urllib.parse.quote(key, safe="/")
        req = urllib.request.Request(url, method="HEAD")
        req.add_header("User-Agent", self.user_agent)
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                self.requests += 1
                return {
                    "size": int(resp.headers.get("Content-Length", -1)),
                    "etag": (resp.headers.get("ETag") or "").strip('"'),
                }
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                raise NotFound(key) from exc
            raise S3Error(f"HTTP {exc.code} on HEAD {key}") from exc

    # ------------------------------------------------------------------ listing

    def list_objects(
        self,
        prefix: str,
        max_keys: int = 1000,
        delimiter: str | None = None,
        stop_after: int | None = None,
    ) -> tuple[list[str], list[str], bool]:
        """``ListObjectsV2`` under ``prefix``.

        Returns ``(keys, sub_prefixes, truncated)``. ``truncated`` is True when
        the caller's ``stop_after`` cut the walk short (i.e. the listing was
        *not* exhausted) -- never silently: the caller must know its evidence
        is partial.
        """
        keys: list[str] = []
        prefixes: list[str] = []
        token: str | None = None
        truncated = False
        while True:
            params = {"list-type": "2", "prefix": prefix, "max-keys": str(max_keys)}
            if delimiter:
                params["delimiter"] = delimiter
            if token:
                params["continuation-token"] = token
            body = self._raw(self.base_url + "?" + urllib.parse.urlencode(params))
            root = ET.fromstring(body)
            for el in root.iter(S3_NS + "Contents"):
                name = el.findtext(S3_NS + "Key")
                if name:
                    keys.append(name)
            for el in root.iter(S3_NS + "CommonPrefixes"):
                name = el.findtext(S3_NS + "Prefix")
                if name:
                    prefixes.append(name)
            if root.findtext(S3_NS + "IsTruncated") != "true":
                break
            if stop_after is not None and len(keys) >= stop_after:
                truncated = True
                break
            token = root.findtext(S3_NS + "NextContinuationToken")
            if not token:
                break
        if stop_after is not None and len(keys) > stop_after:
            keys = keys[:stop_after]
        return keys, prefixes, truncated

    def list_keys(self, prefix: str, stop_after: int | None = None) -> list[str]:
        keys, _, _ = self.list_objects(prefix, stop_after=stop_after)
        return keys

    # ------------------------------------------------------------------ helpers

    def probe_data_keys(self, prefix: str, probe: int = 8) -> tuple[int, bool, list[str]]:
        """Is there any data object under ``prefix``?

        Returns ``(n_data_keys_seen, conclusive, keys_seen)``.

        A Zarr group holds ``.zarray``/``.zattrs``/``.zgroup`` next to its chunk
        objects; only those three names start with a dot, and ``.`` (0x2E) sorts
        before every digit and letter in the key encoding S3 uses, so metadata
        keys are always listed first. With ``probe=8`` the result is airtight:

        * a non-dot key in the window -> data exists, conclusive;
        * no non-dot key and the listing is exhausted -> the prefix is empty of
          data, conclusive (at most 3 dot-keys can exist, so <=8 total keys and
          no truncation means we saw everything);
        * no non-dot key and the listing is *truncated* -> impossible with <=3
          dot-keys; reported as inconclusive rather than guessed at.
        """
        keys, _, truncated = self.list_objects(prefix, max_keys=probe, stop_after=probe)
        keys = [k for k in keys if not k.endswith("/")]
        data = [k for k in keys if not k.rsplit("/", 1)[-1].startswith(".")]
        conclusive = bool(data) or not truncated
        return len(data), conclusive, keys
