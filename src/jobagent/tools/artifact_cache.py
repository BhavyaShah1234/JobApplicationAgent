"""Private, atomic, versioned artifacts for resumable document and form work."""
import hashlib
import json
import os
from pathlib import Path
import tempfile
import threading


def digest(value):
    """Hash JSON-compatible content; callers include prompt, schema and source versions."""
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                     separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def write_private(path, value):
    """Publish complete JSON with owner-only permissions, including after interruption."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, name = tempfile.mkstemp(prefix='.' + path.name, dir=path.parent)
    try:
        with os.fdopen(fd, 'w') as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
    finally:
        Path(name).unlink(missing_ok=True)


class ArtifactCache:
    """Keep successful serializable stage results; corrupt/stale entries are misses."""
    VERSION = 1

    def __init__(self, root):
        self.root = Path(root)
        self.events = []
        self._lock = threading.Lock()

    def get(self, stage, inputs):
        """Return an intact matching result, or None for a missing/stale artifact."""
        fingerprint = digest({'stage': stage, 'version': self.VERSION, 'inputs': inputs})
        path = self.root / (fingerprint + '.json')
        try:
            record = json.loads(path.read_text())
            if (record['version'] == self.VERSION and record['fingerprint'] == fingerprint
                    and digest(record['result']) == record['result_sha256']):
                self._event(stage, True)
                return record['result']
        except (OSError, ValueError, KeyError, TypeError):
            pass
        self._event(stage, False)
        return None

    def put(self, stage, inputs, result):
        """Persist a successful result with a checksum and its complete input identity."""
        fingerprint = digest({'stage': stage, 'version': self.VERSION, 'inputs': inputs})
        write_private(self.root / (fingerprint + '.json'), {
            'version': self.VERSION, 'fingerprint': fingerprint,
            'result_sha256': digest(result), 'result': result})

    def _event(self, stage, hit):
        with self._lock:
            self.events.append({'stage': stage, 'cache_hit': hit})

    def record(self, event):
        """Append timing metadata safely when section workers finish concurrently."""
        with self._lock:
            self.events.append(event)
