"""Local crawl health check. No remote notification credentials are required."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import time


def network_problem(status, now):
    if status.get('route')=='airvpn' and status.get('network_ready') is False:
        return 'VPN tunnel disappeared from the collector network namespace'
    last_response=max(status.get('last_http_response_at') or 0,status.get('run_started_at',now))
    if now-last_response>180:
        return 'No HTTP responses for more than three minutes'
    return None


def assess(status, alive, now, stale_after=180):
    state=status.get('state','unknown')
    if state=='running':
        if not alive:return 'crashed','error','Worker disappeared while status still said running'
        issue=network_problem(status,now)
        if issue:return 'network_unavailable','error',issue
        if now-status.get('updated_at',0)>stale_after:return 'stalled','error','No progress heartbeat for more than three minutes'
        return 'healthy','info','Crawl running'
    if state=='failed':return 'failed','error','Worker crashed: '+status.get('error_type','see crawl.log')
    if state=='paused':return 'paused','warning','Crawl paused for response inspection'
    if state=='network_unavailable':return 'network_unavailable','error','Crawl stopped because its network route is unavailable; check VPN and recreate the collector'
    if state=='disk_limit':return 'disk_limit','warning','Crawl stopped at the free disk space reserve'
    if state in ('queue_exhausted','budget_reached','queue_limit','target_reached','scan_complete','stopped'):
        return state,'info','Crawl stopped: '+state
    return 'unknown','error','Missing or unreadable crawl status'


def worker_alive(pid,container=None):
    if container:
        try:
            result=subprocess.run(['docker','inspect','--format','{{.State.Running}}',container],capture_output=True,text=True,timeout=5)
            return result.returncode==0 and result.stdout.strip()=='true'
        except (OSError,subprocess.TimeoutExpired):return False
    try:
        os.kill(int(pid),0)
        return b'public_crawl.py' in Path(f'/proc/{pid}/cmdline').read_bytes()
    except (OSError,ValueError,TypeError):return False


def check(root, desktop=True):
    try:status=json.loads((root/'status.json').read_text())
    except (OSError,ValueError):status={}
    now=time.time()
    kind,severity,message=assess(status,worker_alive(status.get('pid'),status.get('worker_container')),now)
    health=dict(checked_at=now,kind=kind,severity=severity,message=message,
                worker_state=status.get('state'),worker_pid=status.get('pid'),
                posts=status.get('posts'),long_posts=status.get('long_posts'),
                worker_container=status.get('worker_container'),
                pending=status.get('pages',{}).get('pending',0))
    try:previous=json.loads((root/'health.json').read_text())
    except (OSError,ValueError):previous={}
    signature=(kind,status.get('pid'))
    if kind!='healthy' and signature!=(previous.get('kind'),previous.get('worker_pid')):
        alert=dict(health,desktop_notification='disabled')
        if desktop:
            try:
                result=subprocess.run(['notify-send','--app-name=ForoCoches crawl',
                    '--urgency=critical' if severity=='error' else '--urgency=normal',
                    'ForoCoches crawl needs attention',message],capture_output=True,timeout=5)
                alert['desktop_notification']='submitted' if result.returncode==0 else 'unavailable'
            except (OSError,subprocess.TimeoutExpired):alert['desktop_notification']='unavailable'
        with (root/'alerts.jsonl').open('a') as f:f.write(json.dumps(alert)+'\n')
        print(json.dumps(alert),flush=True)
        health['last_alert']=alert
    elif 'last_alert' in previous:health['last_alert']=previous['last_alert']
    temp=root/'health.tmp';temp.write_text(json.dumps(health,indent=2));temp.replace(root/'health.json')
    return health


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--data',type=Path,default=Path('data/public-2026-10-16'))
    ap.add_argument('--no-desktop',action='store_true')
    args=ap.parse_args();health=check(args.data,not args.no_desktop)
    if health['severity'] in ('error','warning'):raise SystemExit(1)


if __name__=='__main__':main()
