import json
import os
import tempfile
import threading

_locks = {}
_registry_lock = threading.Lock()


def _get_lock(filename):
    with _registry_lock:
        if filename not in _locks:
            _locks[filename] = threading.RLock()
        return _locks[filename]


def _read(filename):
    try:
        with open(filename, "r", encoding="utf-8") as f:
            content = f.read()
    except FileNotFoundError:
        return {}
    return json.loads(content) if content.strip() else {}


def _write(filename, data):
    directory = os.path.dirname(os.path.abspath(filename))
    os.makedirs(directory, exist_ok=True)
    fd, temp_path = tempfile.mkstemp(dir=directory, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(temp_path, filename)
    except Exception:
        if os.path.exists(temp_path):
            os.remove(temp_path)
        raise


def load_data(filename):
    with _get_lock(filename):
        return _read(filename)


def update_data(filename, updater):
    with _get_lock(filename):
        data = _read(filename)
        result = updater(data)
        _write(filename, data)
        return result
