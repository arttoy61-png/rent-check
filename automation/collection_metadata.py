"""Collection evidence is bound to CSV bytes; generation time is never evidence."""
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

def digest(path):
    path = Path(path)
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None

def load_collection(path):
    path = Path(path)
    try:
        value = json.loads(path.with_suffix('.collection.json').read_text(encoding='utf-8'))
        if path.is_file() and value.get('snapshot_sha256') == digest(path) and value.get('schema_version') == 1:
            return value
    except (OSError, ValueError, TypeError, AttributeError):
        pass
    return {'status': 'unknown', 'last_success_at': None}

def record(path, previous, before, metadata, accepted, now=None):
    path = Path(path)
    now = now or datetime.now(timezone.utc).isoformat(timespec='seconds')
    expected = metadata.get('requested_total', 0)
    succeeded = len(metadata.get('successful_keys', []))
    after = digest(path)
    complete = after is not None and accepted and metadata.get('complete') is True and expected > 0 and succeeded == expected
    status = 'success' if complete else ('partial' if succeeded and not metadata.get('complete') else 'failed')
    value = dict(schema_version=1, status=status, attempted_at=now,
                 last_success_at=now if complete else previous.get('last_success_at'),
                 snapshot_sha256=after, snapshot_changed=before != after,
                 expected_partitions=expected, succeeded_partitions=succeeded,
                 cache_policy='recent_two_months_live; older_months_may_use_cache')
    target = path.with_suffix('.collection.json')
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix('.tmp')
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    temporary.replace(target)
    return value
