"""Persistent Telegram health alerts and daily progress for the story crawl."""
import argparse
import fcntl
import json
from pathlib import Path
import subprocess
import time

from monitor_crawl import assess, worker_alive


def load(path):
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return {}


def resolved(status):
    scan = status.get('id_scan') or {}
    return (sum(n for state, n in scan.get('page_states', {}).items() if state != 'pending')
            + scan.get('skipped_known', 0) + scan.get('skipped_archive', 0))


def observe(status, saved, now, alive):
    kind, _, detail = assess(status, alive, now)
    sample = dict(at=now, resolved=resolved(status), attempts=status.get('attempts', 0))
    samples = [s for s in saved.get('samples', []) if now - 7200 <= s['at'] < now]
    samples.append(sample)
    history = [s for s in samples if s['at'] <= now - 1800]
    baseline = history[-1] if history else samples[0]
    elapsed = now - baseline['at']
    rate = (sample['resolved'] - baseline['resolved']) / elapsed if elapsed > 0 else 0
    scan = status.get('id_scan') or {}
    total = abs(scan.get('end_id', 0) - scan.get('start_id', 1)) + 1
    remaining = max(0, total - sample['resolved'])
    eta = remaining / rate if rate > 0 else None
    limits = status.get('limits', {})
    if kind == 'healthy':
        recent = [s for s in samples if s['at'] <= now - 900]
        if recent and sample['attempts'] <= recent[-1]['attempts']:
            kind, detail = 'no_progress', 'No request progress for 15 minutes'
        elif status.get('free_disk_bytes', float('inf')) < limits.get('min_free_bytes', 0) + 20e9:
            kind, detail = 'low_disk', 'Less than 20 GB remains above the disk reserve'
        elif history and remaining and (eta is None or now + eta > limits.get('deadline', float('inf'))):
            kind, detail = 'slow', 'The recent scan rate would miss the saved crawl deadline'
    return dict(kind=kind, detail=detail, samples=samples, rate=rate, eta=eta,
                resolved=sample['resolved'], total=total)


def format_message(status, observation, event):
    kind = observation['kind']
    counts = (f"IDs {observation['resolved']:,}/{observation['total']:,}; "
              f"{status.get('posts', 0):,} retained posts "
              f"({status.get('long_posts', 0):,} long)")
    if observation['rate'] > 0 and status.get('state') == 'running':
        counts += f"; {observation['rate']:.2f} IDs/s; ETA {observation['eta'] / 86400:.1f} days"
    if 'free_disk_bytes' in status:
        counts += f"; {status['free_disk_bytes'] / 1e9:.1f} GB minimum free"
    if kind == 'scan_complete':
        action = 'Full ID range finished. No action needed.'
    elif kind == 'healthy':
        action = 'Monitoring active. No action needed.'
    elif kind in ('slow', 'low_disk'):
        action = observation['detail'] + '. Review crawl status and capacity.'
    else:
        action = observation['detail'] + '. Inspect crawl status before resuming.'
    return f'forocoches-lm: {event} ({kind}): {counts}. {action}'


def check(root, summary_hours=24):
    with (root / 'telegram-monitor.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        path = root / 'telegram-monitor.json'
        saved, status, now = load(path), load(root / 'status.json'), time.time()
        if saved.get('observed_state') != status.get('state'):
            saved['samples'] = []
        saved['observed_state'] = status.get('state')
        observation = observe(status, saved, now,
                              worker_alive(status.get('pid'), status.get('worker_container')))
        kind = observation['kind']
        previous = saved.get('delivered_kind')
        milestone = status.get('posts', 0) >= 500000 and not saved.get('milestone_sent')
        daily = now - saved.get('summary_at', 0) >= summary_hours * 3600
        event = None
        if previous != kind:
            event = 'monitoring started' if previous is None and kind == 'healthy' else ('recovered' if kind == 'healthy' else 'crawl alert')
        elif milestone:
            event = '500,000 retained-post milestone reached; full scan continues'
        elif daily and status.get('state') == 'running':
            event = 'daily update'
        saved['samples'] = observation['samples']
        delivered = False
        if event:
            text = format_message(status, observation, event)
            try:
                result = subprocess.run([str(Path.home() / '.local/bin/hermes'), 'send', '-q',
                                         '--to', 'telegram', text], capture_output=True, timeout=45)
                delivered = result.returncode == 0
            except (OSError, subprocess.TimeoutExpired):
                delivered = False
            if delivered:
                saved.update(delivered_kind=kind, summary_at=now, last_message=text,
                             milestone_sent=bool(saved.get('milestone_sent') or milestone))
            else:
                saved['delivery_failed_at'] = now
        temp = path.with_suffix('.tmp')
        temp.write_text(json.dumps(saved, indent=2) + '\n')
        temp.replace(path)
        if event and not delivered:
            raise RuntimeError('Telegram delivery failed; next timer check will retry')
        return event


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data', type=Path, required=True)
    parser.add_argument('--summary-hours', type=float, default=24)
    args = parser.parse_args()
    if args.summary_hours <= 0:
        parser.error('Summary interval must be positive')
    check(args.data, args.summary_hours)


if __name__ == '__main__':
    main()
