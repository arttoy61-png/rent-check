"""Isolated engine -> minimal public map feed. Existing environment keys only.
Raw source stays in runner temp. All address reservations are remotely durable first.
"""
from pathlib import Path
from datetime import datetime, timezone
from collections import defaultdict
from hashlib import sha256
import argparse, json, math, os, re, subprocess, sys, time, urllib.request, urllib.parse
from pipeline import (read_json, encoded, atomic_write, utcnow, KST, rolling_months,
                      RequestBudget, existing_key, collect_all, produce_feed, unpack_feed,
                      map_snapshot, validate_partitions, parse_time, GateError)
from seoul_snapshot import DISTRICTS, SERVICES, address_key, norm, verified_coordinate

EPOCH='redevelopment-20261016-total600-v1'
BRANCH='redevelopment-map-state'
TARGET='arttoy61-png/rent-check'
MAXIMUM=600
PRIOR=['11500|마곡동|741','11680|대치동|316']
FILES=['state/ledger.json','state/coordinates.json','feed/redevelopment-nearby-seoul.json','feed/redevelopment-nearby-status.json','feed/manifest.json']
HERE=Path(__file__).resolve().parent

def fingerprint(key):return sha256(key.encode('utf8')).hexdigest()
def git(root,*args):
    try:return subprocess.run(['git','-C',str(root),*args],check=True,capture_output=True,text=True).stdout.strip()
    except subprocess.SubprocessError:raise GateError('state_git_operation_failed') from None

def validate_ledger(d):
    if d.get('epoch')!=EPOCH or d.get('maximum_total')!=600 or d.get('prior_probes')!=[fingerprint(k) for k in PRIOR]:raise GateError('budget_scope_or_epoch_mismatch')
    a=d.get('attempts');n=d.get('reserved_total')
    if not isinstance(a,dict) or type(n) is not int or n!=2+len(a) or n>600:raise GateError('budget_count_mismatch')
    if any(not re.fullmatch('[a-f0-9]{64}',k) or k in d['prior_probes'] or not isinstance(v,dict) or v.get('state') not in ['reserved','verified','unmatched','service_error'] for k,v in a.items()):raise GateError('invalid_attempt_ledger')
    return d

class RemoteState:
    def __init__(self,repo,root):
        self.repo=Path(repo);self.root=Path(root)
        # Parent seeds a single approval epoch once. Missing branch/file never resets it.
        remote=git(self.repo,'remote','get-url','origin')
        if remote not in ['https://github.com/'+TARGET+'.git','https://github.com/'+TARGET,'git@github.com:'+TARGET+'.git']:raise GateError('unexpected_engine_repository')
        ref=git(self.repo,'ls-remote','origin','refs/heads/'+BRANCH).split()
        if len(ref)!=2:raise GateError('durable_state_branch_missing_no_reset')
        git(self.repo,'fetch','--no-tags','origin','refs/heads/'+BRANCH)
        if git(self.repo,'rev-parse','FETCH_HEAD')!=ref[0]:raise GateError('state_changed_during_fetch')
        git(self.repo,'worktree','add','--detach',str(self.root),ref[0])
        self.head=ref[0]
        files=git(self.root,'ls-files').splitlines()
        if set(files)!=set(FILES):raise GateError('unexpected_state_branch_contents')
        self.ledger=validate_ledger(read_json(self.root/'state/ledger.json'))
        self.coordinates=read_json(self.root/'state/coordinates.json')
        if not isinstance(self.coordinates,dict):raise GateError('invalid_coordinate_cache')
    def persist(self,paths):
        if not paths or any(p not in FILES for p in paths):raise GateError('unexpected_state_write')
        if git(self.root,'rev-parse','HEAD')!=self.head:raise GateError('local_state_changed')
        ref=git(self.root,'ls-remote','origin','refs/heads/'+BRANCH).split()
        if len(ref)!=2 or ref[0]!=self.head:raise GateError('remote_state_changed_stop')
        git(self.root,'add','--',*paths)
        staged=git(self.root,'diff','--cached','--name-only').splitlines()
        if any(p not in paths for p in staged):raise GateError('unexpected_staged_state_write')
        if not staged:return
        git(self.root,'-c','user.name=Redevelopment isolated updater','-c','user.email=redevelopment-updater@users.noreply.github.com','commit','-m','data: isolated redevelopment state')
        commit=git(self.root,'rev-parse','HEAD')
        # Non-force push rejects divergence; no merge/reset/rebase or retry.
        git(self.root,'push','origin','HEAD:refs/heads/'+BRANCH)
        ref=git(self.root,'ls-remote','origin','refs/heads/'+BRANCH).split()
        if len(ref)!=2 or ref[0]!=commit:raise GateError('state_push_unconfirmed_no_request')
        self.head=commit
    def reserve(self,key):
        h=fingerprint(key)
        if h in self.ledger['prior_probes'] or h in self.ledger['attempts']:return False
        if self.ledger['reserved_total']>=MAXIMUM:return False
        self.ledger['attempts'][h]={'state':'reserved','reserved_at':utcnow().isoformat()}
        self.ledger['reserved_total']+=1
        atomic_write(self.root/'state/ledger.json',encoded(validate_ledger(self.ledger)))
        self.persist(['state/ledger.json'])
        return True
    def result(self,key,point,status):
        h=fingerprint(key)
        if h not in self.ledger['attempts']:raise GateError('result_without_reservation')
        self.ledger['attempts'][h].update(state='verified' if point else ('unmatched' if status in ['OK','ZERO_RESULT'] else 'service_error'),completed_at=utcnow().isoformat())
        if point:
            # Cache has no plain address, building, price, party or raw response fields.
            self.coordinates[h]={k:point[k] for k in ['lawd_cd','lat','lng','verified','source','checked_at','basis']}
        atomic_write(self.root/'state/ledger.json',encoded(self.ledger))
        atomic_write(self.root/'state/coordinates.json',encoded(self.coordinates))
        self.persist(['state/ledger.json','state/coordinates.json'])

def validate_match(key,documents):
    if not isinstance(documents,list) or len(documents)!=1:return None
    lawd,dong,lot=key.split('|');matches=[]
    for item in documents:
        a=item.get('address') or {}
        actual=('산 ' if a.get('mountain_yn')=='Y' else '')+str(a.get('main_address_no') or '')
        if a.get('sub_address_no') and a['sub_address_no']!='0':actual+='-'+a['sub_address_no']
        if a.get('region_1depth_name')!='서울' or a.get('b_code','')[:5]!=lawd or a.get('region_2depth_name')!=DISTRICTS[lawd] or norm(a.get('region_3depth_name'))!=dong or norm(actual)!=lot:continue
        try:lat=float(a['y']);lng=float(a['x'])
        except (TypeError,ValueError,KeyError):continue
        if not(math.isfinite(lat) and math.isfinite(lng) and 37.4<=lat<=37.75 and 126.7<=lng<=127.25):continue
        matches.append({'address_key':key,'lawd_cd':lawd,'lat':lat,'lng':lng,'verified':True,'source':'https://developers.kakao.com/docs/latest/ko/local/dev-guide#address-coord','checked_at':utcnow().isoformat(),'basis':'exact_district_legal_dong_lot_address_match'})
    return matches[0] if len(matches)==1 else None

def search_once(key,rest_key):
    lawd,dong,lot=key.split('|')
    query='서울특별시 '+DISTRICTS[lawd]+' '+dong+' '+lot
    url='https://dapi.kakao.com/v2/local/search/address.json?'+urllib.parse.urlencode({'query':query,'analyze_type':'exact','size':30})
    request=urllib.request.Request(url,headers={'Authorization':'KakaoAK '+rest_key})
    try:
        # No redirects: never transmit the credential to another destination. No retries.
        class NoRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self,*args,**kwargs):return None
        with urllib.request.build_opener(NoRedirect).open(request,timeout=20) as r:body=r.read(1024*1024+1)
        if len(body)>1024*1024:return None,'ERROR'
        data=json.loads(body);docs=data.get('documents')
        if not isinstance(docs,list):return None,'ERROR'
        if docs and (len(docs)!=1 or data.get('meta',{}).get('total_count')!=1):return None,'OK'
        return validate_match(key,docs),'OK' if docs else 'ZERO_RESULT'
    except Exception:return None,'ERROR'

def candidates(bundle):
    groups=defaultdict(int)
    for p in bundle['partitions']:
        for r in p['rows']:
            if r['cancellation_status']=='not_marked' and r['address_key']:groups[r['address_key']]+=1
    # Round-robin25districts; most rows first within district. Deterministic no duplicate.
    pools={d:sorted((k for k in groups if k.startswith(d+'|')),key=lambda k:(-groups[k],k)) for d in DISTRICTS}
    return [pool[i] for i in range(max(map(len,pools.values()),default=0)) for pool in pools.values() if i<len(pool)]

def restored_cache(state,keys):
    out={}
    for k in keys:
        p=state.coordinates.get(fingerprint(k))
        if p:
            allowed=set(['lawd_cd','lat','lng','verified','source','checked_at','basis'])
            if set(p)!=allowed or p.get('verified') is not True:raise GateError('invalid_cached_coordinate_fields')
            if fingerprint(k) not in state.ledger['prior_probes'] and k!='11500|마곡동|1589' and state.ledger['attempts'].get(fingerprint(k),{}).get('state')!='verified':raise GateError('coordinate_without_verified_attempt')
            candidate={**p,'address_key':k};lawd,dong,lot=k.split('|')
            if verified_coordinate({'lawd_cd':lawd,'umd_name':dong,'jibun':lot},{k:candidate}):out[k]=candidate
    return out

def geocode(bundle,state,rest_key,limit=598,request=search_once):
    if type(limit) is not int or not 0<=limit<=598:raise GateError('invalid_address_limit')
    keys=candidates(bundle);cache=restored_cache(state,keys);sent=errors=0;stop=None
    for key in keys:
        if key in cache:continue
        if sent>=limit or state.ledger['reserved_total']>=MAXIMUM:break
        if not rest_key:stop='existing_kakao_binding_unavailable';break
        if not state.reserve(key):continue
        # Reserve success precedes every possible transmission, including failed first request.
        try:point,status=request(key,rest_key)
        except Exception:point,status=None,'ERROR'
        sent+=1;state.result(key,point,status)
        if point:cache[key]=point
        errors=errors+1 if status not in ['OK','ZERO_RESULT'] else 0
        if errors>=3:stop='three_consecutive_service_errors';break
        time.sleep(.25)
    return cache,{'requests_this_run':sent,'reserved_total':state.ledger['reserved_total'],'maximum_total':600,'stopped':stop}

def publish(state,snapshot,status):
    if snapshot is not None:
        old=read_json(state.root/'feed/redevelopment-nearby-seoul.json')
        if parse_time(snapshot['meta']['source_checked_at'])<parse_time(old['meta']['source_checked_at']):raise GateError('source_rollback_rejected')
        atomic_write(state.root/'feed/redevelopment-nearby-seoul.json',encoded(snapshot))
    atomic_write(state.root/'feed/redevelopment-nearby-status.json',encoded(status))
    manifest={'version':1,'approval_epoch':EPOCH,'files':{n:sha256((state.root/'feed'/n).read_bytes()).hexdigest() for n in ['redevelopment-nearby-seoul.json','redevelopment-nearby-status.json']}}
    atomic_write(state.root/'feed/manifest.json',encoded(manifest))
    state.persist([p for p in FILES if p.startswith('feed/')])

def run(repo,temp,address_limit=598,replay=None):
    temp=Path(temp).resolve();temp.mkdir(parents=True,exist_ok=True)
    state=RemoteState(repo,temp/'state-tree');now=utcnow();old_status=read_json(state.root/'feed/redevelopment-nearby-status.json');snapshot=None
    status={'version':1,'state':'failed','mode':'isolated_engine','attempted_at':now.isoformat(),'last_good_source_at':old_status.get('last_good_source_at'),'execution_environment':'github_actions','scheduled_run':os.environ.get('GITHUB_EVENT_NAME')=='schedule','scheduler_registered_locally':False,'kept_previous':True}
    try:
        if replay:
            # Only explicit local saved bundle; never fall back to a normalized legacy CSV.
            bundle=unpack_feed(replay);validate_partitions(bundle['partitions'],bundle['months'],datetime.fromisoformat(bundle['cutoff']).date(),now)
            if len(bundle['partitions'])!=450:raise GateError('three_month_450_partitions_required')
        else:
            cutoff=now.astimezone(KST).date();months=rolling_months(cutoff);budget=RequestBudget(600)
            def loader(folder):return collect_all(existing_key(),months,cutoff,folder,budget)
            result=produce_feed(loader,months,cutoff,temp/'source',temp/'raw-feed','network_collection',now,budget)
            if result['state']!='ready':raise GateError(result.get('reason','collection_failed'))
            bundle=unpack_feed(temp/'raw-feed/source.json.gz')
        cache,geo=geocode(bundle,state,os.environ.get('KAKAO_REST_KEY',''),address_limit)
        atomic_write(temp/'geocode-summary.json',encoded(geo))
        snapshot=map_snapshot(bundle,cache,read_json(HERE/'project-centers.json'),utcnow())
        # Original source/raw_items are NEVER staged, committed, uploaded or printed.
        status.update(state='ready',last_good_source_at=snapshot['meta']['source_checked_at'],coordinate_coverage_complete=snapshot['meta']['coordinate_coverage_complete'],geocoding=geo,kept_previous=False,network_requests=bundle.get('network_requests',0))
    except Exception as exc:
        status['reason']=str(exc) if isinstance(exc,GateError) else type(exc).__name__
        snapshot=None
    publish(state,snapshot,status)
    return status

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--repo',required=True);p.add_argument('--temp',required=True);p.add_argument('--address-limit',type=int,default=598);p.add_argument('--replay')
    a=p.parse_args()
    try:r=run(a.repo,a.temp,a.address_limit,a.replay);print(json.dumps(r));sys.exit(0 if r['state']=='ready' else 1)
    except Exception as exc:print(json.dumps({'state':'failed','reason':str(exc) if isinstance(exc,GateError) else type(exc).__name__}));sys.exit(1)
