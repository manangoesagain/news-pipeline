"""
Shared, fenced file access for the self-learning loop.

The learning step may only write inside LEARN_DIR (data files, never code). Every write goes through
safe_write_text / safe_write_json, which refuse any path outside the folder (symlinks and junctions
included), refuse code-like file types, redact anything that looks like a secret or an email address,
and write atomically (temp file + os.replace) so a crash never leaves a truncated file. Nothing here
executes generated content.
"""

import json
import os
import re
import tempfile
from datetime import datetime, timezone

LEARN_DIR = os.path.join("_data", "_learning")
ALLOWED_EXT = {".json", ".md", ".txt"}
MAX_FILE_BYTES = 400_000

_EMAIL = re.compile(r"(?<![A-Za-z0-9._%+-])[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_KEYLIKE = re.compile(r"\b(?:sk|pk|nvapi|gsk|AIza|ghp|gho|xox[bp])[-_A-Za-z0-9]{16,}\b")
_SPECIFIC = [
    re.compile(r"\b\d{6,12}:[A-Za-z0-9_-]{30,}"),                 # Telegram bot token
    re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}"),                 # GitHub fine-grained PAT
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}"),                   # GitHub ghp_/gho_/ghu_/ghs_/ghr_
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),                           # AWS access key id
    re.compile(r"\bre_[A-Za-z0-9_]{16,}"),                         # Resend
    re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]{8,}"),         # Authorization header
]
_HEX = re.compile(r"(?<![A-Za-z0-9])[0-9A-Fa-f]{32,}(?![A-Za-z0-9])")
_B64 = re.compile(r"(?<![A-Za-z0-9+_=-])[A-Za-z0-9+_-]{32,}={0,2}(?![A-Za-z0-9+_=-])")
_SECRET_NAME = ("KEY", "TOKEN", "SECRET", "PASSWORD", "CHAT_ID")


class FenceError(Exception):
    """Raised when something tries to write outside the learning folder."""


def _env_secrets():
    vals = {v.strip() for k, v in os.environ.items()
            if any(w in k.upper() for w in _SECRET_NAME) and len(v.strip()) >= 6}
    return sorted(vals, key=len, reverse=True)


def _b64_repl(m):
    s = m.group(0)
    # long slugs / plain words stay; random-looking strings (a digit, or mixed case) go
    if re.search(r"\d", s) or (re.search(r"[a-z]", s) and re.search(r"[A-Z]", s)):
        return "[token removed]"
    return s


def redact(text, _secrets=None) -> str:
    text = str(text or "")
    for pat in _SPECIFIC:
        text = pat.sub("[key removed]", text)
    for v in (_env_secrets() if _secrets is None else _secrets):
        text = text.replace(v, "[secret removed]")
    text = _EMAIL.sub("[address removed]", text)
    text = _KEYLIKE.sub("[key removed]", text)
    text = _HEX.sub("[token removed]", text)
    return _B64.sub(_b64_repl, text)


def _redact_obj(o, _secrets=None):
    """Redact every string in a JSON-like object (so the result is always valid JSON).
    The environment is scanned once per write, not once per string (that was the slow part)."""
    if _secrets is None:
        _secrets = _env_secrets()
    if isinstance(o, str):
        return redact(o, _secrets)
    if isinstance(o, dict):
        return {(redact(k, _secrets) if isinstance(k, str) else k): _redact_obj(v, _secrets) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_redact_obj(v, _secrets) for v in o]
    return o


def _norm(p):
    return os.path.normcase(os.path.normpath(p))


def _common(a, b):
    try:
        return os.path.commonpath([_norm(a), _norm(b)]) == _norm(a)
    except ValueError:
        return False


def _inside(path: str) -> str:
    """Return the absolute path if it is a plain (non-symlinked) data file path inside LEARN_DIR."""
    abs_base = os.path.abspath(LEARN_DIR)
    full = os.path.abspath(path)
    if not _common(abs_base, full):
        raise FenceError(f"refusing to write outside {LEARN_DIR}: {path}")
    if os.path.splitext(full)[1].lower() not in ALLOWED_EXT:
        raise FenceError(f"refusing to write a non-data file type: {path}")
    rel = os.path.relpath(full, abs_base)
    if rel == "." or rel.startswith(".."):
        raise FenceError(f"refusing to write outside {LEARN_DIR}: {path}")
    # any existing symlink on the way down inside the learning dir is refused
    cur = abs_base
    for part in rel.split(os.sep):
        cur = os.path.join(cur, part)
        if os.path.islink(cur):
            raise FenceError(f"refusing a symlink inside {LEARN_DIR}: {path}")
    # the resolved target must be exactly where it lexically claims to be (catches junctions etc.)
    real_base = os.path.realpath(abs_base)
    real_full = os.path.realpath(full)
    if not _common(real_base, real_full) or _norm(real_full) != _norm(os.path.join(real_base, rel)):
        raise FenceError(f"refusing path that resolves outside {LEARN_DIR}: {path}")
    return full


def _atomic_write(full: str, data: str) -> None:
    d = os.path.dirname(full)
    os.makedirs(d, exist_ok=True)
    _inside(full)  # re-check now that the directory exists
    fd, tmp = tempfile.mkstemp(dir=d, prefix=".tmp-", suffix=".part")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as f:
            f.write(data)
            f.flush()
            try:
                os.fsync(f.fileno())
            except OSError:
                pass
        _inside(full)  # target still fenced right before the swap
        os.replace(tmp, full)
    except BaseException:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise


def _cap_text(data: str) -> str:
    b = data.encode("utf-8")
    if len(b) <= MAX_FILE_BYTES:
        return data
    cut = b[:MAX_FILE_BYTES].decode("utf-8", "ignore")
    nl = cut.rfind("\n")
    return cut[:nl + 1] if nl > 0 else cut


def safe_write_text(path: str, text: str) -> None:
    full = _inside(path)
    _atomic_write(full, _cap_text(redact(text)))


def safe_write_json(path: str, obj) -> None:
    """Never truncates: raises FenceError('too large') if the JSON would exceed MAX_FILE_BYTES."""
    full = _inside(path)
    data = json.dumps(_redact_obj(obj), ensure_ascii=False, separators=(",", ":"))
    if len(data.encode("utf-8")) > MAX_FILE_BYTES:
        raise FenceError("too large")
    _atomic_write(full, data)


_BAD = (OSError, ValueError, UnicodeDecodeError, RecursionError)


def load_json(path: str, default=None):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except _BAD:
        return default


def load_text(path: str, default: str = "") -> str:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return f.read()
    except _BAD:
        return default


def backup_corrupt(path: str) -> bool:
    """If `path` exists but is not valid JSON, copy it to <path>.corrupt-<timestamp>.txt (redacted,
    inside LEARN_DIR) and return True. Returns False when the file is missing or valid. Fence and
    I/O errors propagate so a caller never overwrites a damaged file it failed to back up."""
    src = _inside(path)
    if not os.path.isfile(src):
        return False
    try:
        with open(src, "rb") as f:
            raw = f.read()
    except FileNotFoundError:
        return False
    try:
        json.loads(raw.decode("utf-8"))
        return False
    except (ValueError, RecursionError):  # includes UnicodeDecodeError
        pass
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    dest = _inside(f"{src}.corrupt-{stamp}.txt")
    n = 1
    while os.path.exists(dest):
        n += 1
        dest = _inside(f"{src}.corrupt-{stamp}-{n}.txt")
    _atomic_write(dest, redact(raw.decode("utf-8", "replace")))
    return True


_SLUG = re.compile(r"[a-z0-9-]{3,40}")


def safe_slug(s):
    """The slug if it fully matches ^[a-z0-9-]{3,40}$ (no trailing-newline loophole), else None."""
    return s if isinstance(s, str) and _SLUG.fullmatch(s) else None
