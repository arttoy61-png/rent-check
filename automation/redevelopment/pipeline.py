"""Isolated Seoul source -> validated feed -> redevelopment map update.

This module has no Git, deployment, scheduling, geocoding, or HOME writes.
Only a fully validated source replaces source.json.gz or the map snapshot.
"""
from __future__ import annotations
from pathlib import Path
from datetime import datetime,date,timedelta,timezone
from collections import Counter
from contextlib import contextmanager
from hashlib import sha256
from uuid import uuid4
import argparse,ast,gzip,json,math,os,sys,time,urllib.request

from seoul_snapshot import DISTRICTS,SERVICES,RequestBudget,CollectionError,collect_partition,assess_complete,normalize,verified_coordinate,cancellation

MAP_FILE='redevelopment-nearby-seoul.json'
STATUS_FILE='redevelopment-nearby-status.json'
MAX_FEED_BYTES=400*1024*1024
MAX_SOURCE_AGE_HOURS=48
KST=timezone(timedelta(hours=9))

class GateError(RuntimeError):pass
def utcnow():return datetime.now(timezone.utc)
def encoded(obj):return json.dumps(obj,ensure_ascii=False,separators=(',',':')).encode('utf8')
def digest(obj):return sha256(json.dumps(obj,sort_keys=True,ensure_ascii=False).encode()).hexdigest()
def read_json(path):return json.loads(Path(path).read_text(encoding='utf8'))
def parse_time(value):
    try:dt=datetime.fromisoformat(value)
    except (TypeError,ValueError):raise GateError('invalid_source_timestamp') from None
    if not dt.tzinfo:raise GateError('source_timestamp_without_timezone')
    return dt

def rolling_months(cutoff,months_back=3):
    if not 1<=months_back<=3:raise GateError('months_back_outside_1_to_3')
    serial=cutoff.year*12+cutoff.month-1
    return [f'{(serial-i)//12:04d}{(serial-i)%12+1:02d}' for i in reversed(range(months_back))]

def atomic_write(path,content,before_replace=None):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    temporary=path.with_name(path.name+'.'+uuid4().hex+'.tmp')
    try:
        with temporary.open('xb') as stream:stream.write(content);stream.flush();os.fsync(stream.fileno())
        if before_replace:before_replace()
        os.replace(temporary,path)
    finally:
        if temporary.exists():temporary.unlink()

@contextmanager
def locked(directory):
    directory=Path(directory);directory.mkdir(parents=True,exist_ok=True);path=directory/'update.lock'
    try:fd=os.open(path,os.O_CREAT|os.O_EXCL|os.O_WRONLY)
    except FileExistsError:raise GateError('another_update_or_stale_lock') from None
    try:
        with os.fdopen(fd,'w') as f:json.dump({'pid':os.getpid(),'started_at':utcnow().isoformat()},f)
        yield
    finally:path.unlink(missing_ok=True)

def validate_partitions(parts,months,cutoff,now=None):
    now=now or utcnow()
    if cutoff>now.astimezone(KST).date():raise GateError('future_collection_cutoff')
    if months!=rolling_months(cutoff,len(months)):raise GateError('nonconsecutive_or_wrong_month_window')
    result=assess_complete(parts,months)
    if not result['complete']:raise GateError('incomplete_25_district_partition_set')
    total=0;seen_hashes=set();latest=[]
    for part in parts:
        lawd,month,service=part['lawd_cd'],part['month'],part['service']
        rows,raw,pages=part.get('rows',[]),part.get('raw_items',[]),part.get('pages',[])
        if part.get('total_count')!=len(raw) or len(raw)!=len(rows):raise GateError('source_row_count_mismatch')
        if not pages or [p['page'] for p in pages]!=list(range(1,len(pages)+1)):raise GateError('nonconsecutive_pages')
        start=0;seen_hashes.clear()
        for i,page in enumerate(pages):
            if page['total_count']!=len(raw) or page['rows']<0:raise GateError('page_total_mismatch')
            if i<len(pages)-1 and page['rows']!=1000:raise GateError('short_page_before_total')
            if page['rows']>1000:raise GateError('page_size_exceeded')
            chunk=raw[start:start+page['rows']];page_hash=digest(chunk)
            if page_hash!=page['sha256']:raise GateError('page_hash_mismatch')
            if chunk and page_hash in seen_hashes:raise GateError('repeated_page')
            seen_hashes.add(page_hash);start+=page['rows']
        if start!=len(raw):raise GateError('page_sum_mismatch')
        for source,row in zip(raw,rows):
            try:expected=normalize(source,lawd,month,service,cutoff)
            except CollectionError as exc:raise GateError(str(exc)) from None
            if expected!=row:raise GateError('normalized_row_or_cancellation_mismatch')
        collected=parse_time(part.get('collected_at'))
        if collected>now+timedelta(minutes=5):raise GateError('future_source_timestamp')
        if now-collected>timedelta(hours=MAX_SOURCE_AGE_HOURS):raise GateError('source_older_than_48_hours')
        latest.append(collected);total+=len(rows)
    if total==0:raise GateError('empty_city_snapshot_requires_review')
    return {**result,'raw_rows':total,'source_checked_at':max(latest).isoformat(),'source_started_at':min(latest).isoformat()}

def existing_key(config_path=None):
    key=os.environ.get('MOLIT_API_KEY')
    if key:return key
    if config_path:
        tree=ast.parse(Path(config_path).read_text(encoding='utf-8-sig'))
        for item in tree.body:
            if isinstance(item,ast.Assign) and any(isinstance(t,ast.Name) and t.id=='MOLIT_API_KEY' for t in item.targets):
                if isinstance(item.value,ast.Constant) and isinstance(item.value.value,str) and item.value.value:return item.value.value
    raise GateError('existing_credential_unavailable')

def collect_all(key,months,cutoff,run_dir,budget,collector=collect_partition,pause=.3):
    parts=[];failures=0
    for code in DISTRICTS:
        for month in months:
            for service in SERVICES:
                part={'lawd_cd':code,'month':month,'service':service,'complete':False}
                try:part=collector(key,code,month,service,cutoff,budget);failures=0
                except CollectionError as exc:
                    part['error']=str(exc);failures+=1
                    atomic_write(Path(run_dir)/f'{code}_{month}_{list(SERVICES).index(service)}.json',encoded(part))
                    parts.append(part)
                    if failures>=3 or 'budget' in str(exc) or 'api_code_22' in str(exc):return parts
                    continue
                parts.append(part)
                atomic_write(Path(run_dir)/f'{code}_{month}_{list(SERVICES).index(service)}.json',encoded(part))
                if pause:time.sleep(pause)
    return parts

def prior_status(path):
    try:return read_json(path)
    except (OSError,ValueError):return {}
def status_record(state,mode,now,previous,**extra):
    return {'version':1,'state':state,'mode':mode,'attempted_at':now.isoformat(),
        'last_good_source_at':previous.get('last_good_source_at'),
        'execution_environment':'github_actions' if os.environ.get('GITHUB_ACTIONS')=='true' else 'local',
        'scheduled_run':os.environ.get('GITHUB_ACTIONS')=='true' and os.environ.get('GITHUB_EVENT_NAME')=='schedule',
        'scheduler_registered_locally':False,**extra}

def record_failed_step(status_path):
    path=Path(status_path)
    if path.name not in ['status.json',STATUS_FILE]:raise GateError('unsupported_status_filename')
    previous=prior_status(path)
    data_file=path.with_name('source.json.gz' if path.name=='status.json' else MAP_FILE)
    result=status_record('failed','workflow_step_fallback',utcnow(),previous,reason=previous.get('reason','workflow_step_failed'),kept_previous=data_file.exists(),network_requests=None)
    atomic_write(path,encoded(result));return result

def produce_feed(loader,months,cutoff,state_dir,feed_dir,mode='replay',now=None,budget=None,before_replace=None):
    now=now or utcnow();state_dir=Path(state_dir);feed_dir=Path(feed_dir);status_path=feed_dir/'status.json'
    with locked(state_dir):
        previous=prior_status(status_path);run=state_dir/('run-'+now.strftime('%Y%m%dT%H%M%SZ')+'-'+uuid4().hex[:8]);run.mkdir()
        try:
            parts=loader(run);validation=validate_partitions(parts,months,cutoff,utcnow() if mode=='network_collection' else now)
            bundle={'feed_version':1,'cutoff':cutoff.isoformat(),'months':months,'validation':validation,'partitions':parts,'producer_mode':mode,'network_requests':budget.used if budget else 0}
            packed=gzip.compress(encoded(bundle),mtime=0)
            atomic_write(run/'source.json.gz',packed)
            atomic_write(feed_dir/'source.json.gz',packed,before_replace)
            status=status_record('ready',mode,now,previous,last_good_source_at=validation['source_checked_at'],feed_sha256=sha256(packed).hexdigest(),network_requests=budget.used if budget else 0,validation=validation)
        except Exception as exc:
            reason=str(exc) if isinstance(exc,(GateError,CollectionError)) else type(exc).__name__
            status=status_record('failed',mode,now,previous,reason=reason,kept_previous=(feed_dir/'source.json.gz').exists(),network_requests=budget.used if budget else 0)
        atomic_write(status_path,encoded(status));atomic_write(run/'result.json',encoded(status));return status

def unpack_feed(path):
    # Limit decompression and never deserialize executable formats.
    with gzip.open(path,'rb') as stream:content=stream.read(MAX_FEED_BYTES+1)
    if len(content)>MAX_FEED_BYTES:raise GateError('feed_too_large')
    result=json.loads(content)
    if result.get('feed_version')!=1:raise GateError('unsupported_feed_version')
    return result

def number(value):
    try:n=float(str(value).replace(',','').strip())
    except (ValueError,TypeError):return None
    return n if math.isfinite(n) else None

def map_snapshot(bundle,cache,projects,now):
    cutoff=date.fromisoformat(bundle['cutoff']);months=bundle['months'];validation=validate_partitions(bundle['partitions'],months,cutoff,now)
    rows=[r for p in bundle['partitions'] for r in p['rows']];counts={s:Counter() for s in SERVICES};district_counts={code:{s:Counter() for s in SERVICES} for code in DISTRICTS}
    linked=[]
    for i,row in enumerate(rows):
        s=row['deal_type'];targets=[counts[s],district_counts[row['lawd_cd']][s]]
        for count in targets:count['raw']+=1
        state=cancellation(row)
        if state!='not_marked':
            for count in targets:count['excluded_cancelled' if state=='cancelled' else 'excluded_unknown_cancel']+=1
            continue
        for count in targets:count['eligible']+=1
        point=verified_coordinate(row,cache)
        if not point:
            for count in targets:count['coordinate_unlinked']+=1
            continue
        for count in targets:count['coordinate_linked']+=1
        values={k:number(row[k]) for k in ['area_m2','floor','deal_amount','deposit','monthly_rent']}
        required=['area_m2','floor','deal_amount'] if s.endswith('_매매') else ['area_m2','floor','deposit','monthly_rent']
        if any(values[k] is None for k in required) or values['area_m2']<=0 or any(values[k]<0 for k in required if k!='floor'):
            for count in targets:count['invalid_value']+=1
            continue
        linked.append({'id':'source-'+str(i),'complex_id':row['complex_id'],'lawd_cd':row['lawd_cd'],'gu_name':row['gu_name'],'name':row['building_name'],'type':s,'date':row['contract_date'],'address':row['address'],'lat':point['lat'],'lng':point['lng'],'coordinate_source':point['source'],'coordinate_checked_at':point['checked_at'],'coordinate_basis':cache[row['address_key']].get('basis','exact_saved_address_match'),**values})
    if not linked:raise GateError('no_verified_coordinates_linked_keep_last_good')
    valid_projects={k:v for k,v in projects.items() if v.get('verified') and v.get('source') and v.get('basis')=='verified_official_boundary_centroid' and len(v.get('point',[]))==2 and all(isinstance(x,(int,float)) and math.isfinite(x) for x in v['point']) and 37.4<=v['point'][0]<=37.75 and 126.7<=v['point'][1]<=127.25}
    if not valid_projects:raise GateError('no_verified_project_centers')
    eligible=sum(v['eligible'] for v in counts.values());unlinked=sum(v['coordinate_unlinked'] for v in counts.values());invalid=sum(v['invalid_value'] for v in counts.values())
    if eligible!=len(linked)+unlinked+invalid:raise GateError('map_count_reconciliation_failed')
    meta={'generated_at':now.isoformat(),'source_checked_at':validation['source_checked_at'],'period':[months[0][:4]+'-'+months[0][4:]+'-01',cutoff.isoformat()],'latest_contract_date':max(r['contract_date'] for r in rows),'period_basis':'계약일','collection_complete':True,'complete_districts':25,'successful_partitions':validation['successful_partitions'],'expected_partitions':validation['expected_partitions'],'raw_rows':len(rows),'eligible_rows':eligible,'excluded_cancelled':sum(v['excluded_cancelled'] for v in counts.values()),'excluded_unknown_cancel':sum(v['excluded_unknown_cancel'] for v in counts.values()),'coordinate_addresses':len({r['address'] for r in linked}),'coordinate_linked_rows':len(linked),'coordinate_unlinked_rows':unlinked,'invalid_value_rows':invalid,'coordinate_coverage_complete':unlinked==0 and invalid==0,'raw_source':'https://rt.molit.go.kr/','source_basis':'국토교통부 공공데이터 실거래 API 6종','cancellation_limit':'매매 취소표시·해제일 제외. 전월세 취소 여부와 이후 신고·취소는 미확인.','coordinate_limit':f'로컬에 보존된 주소 대조 좌표 {len({r["address"] for r in linked})}곳만 연결. 전체 서울 좌표 검증은 미완료.','pipeline_mode':bundle.get('producer_mode'),'network_requests':bundle.get('network_requests',0)}
    return {'version':2,'meta':meta,'districts':DISTRICTS,'services':list(SERVICES),'counts':counts,'district_counts':district_counts,'projects':valid_projects,'rows':linked}

def promote_map(feed_file,feed_status,cache_file,project_file,map_dir,state_dir,now=None,before_replace=None):
    now=now or utcnow();map_dir=Path(map_dir);status_path=map_dir/STATUS_FILE
    with locked(state_dir):
        previous=prior_status(status_path)
        try:
            upstream=read_json(feed_status)
            if upstream.get('state')!='ready':raise GateError('upstream_update_failed_keep_last_good')
            packed=Path(feed_file).read_bytes()
            if sha256(packed).hexdigest()!=upstream.get('feed_sha256'):raise GateError('feed_digest_mismatch')
            bundle=unpack_feed(feed_file);cache=read_json(cache_file);projects=read_json(project_file)
            snapshot=map_snapshot(bundle,cache,projects,now)
            old_path=map_dir/MAP_FILE
            if old_path.exists():
                old=read_json(old_path);old_time=parse_time(old['meta']['source_checked_at'])
                if parse_time(snapshot['meta']['source_checked_at'])<old_time:raise GateError('older_source_would_replace_newer_snapshot')
                old_digest=sha256(old_path.read_bytes()).hexdigest()
                atomic_write(Path(state_dir)/'last-good'/f'{old_digest}.json',old_path.read_bytes())
            atomic_write(old_path,encoded(snapshot),before_replace)
            status=status_record('ready','local_feed_import',now,previous,last_good_source_at=snapshot['meta']['source_checked_at'],source_feed_sha256=upstream['feed_sha256'],source_mode=upstream.get('mode'),network_requests=0,coordinate_coverage_complete=snapshot['meta']['coordinate_coverage_complete'])
        except Exception as exc:
            reason=str(exc) if isinstance(exc,(GateError,CollectionError)) else type(exc).__name__
            status=status_record('failed','local_feed_import',now,previous,reason=reason,kept_previous=(map_dir/MAP_FILE).exists(),network_requests=0)
        atomic_write(status_path,encoded(status));return status

def fetch_public_feed(destination):
    # Fixed, proposed feed endpoint; used only by an explicitly activated import command.
    root='https://raw.githubusercontent.com/arttoy61-png/rent-check/seoul-redevelopment-feed/feed/'
    destination=Path(destination);destination.mkdir(parents=True,exist_ok=True)
    for name,limit in [('status.json',1024*1024),('source.json.gz',40*1024*1024)]:
        try:
            with urllib.request.urlopen(root+name,timeout=60) as response:content=response.read(limit+1)
        except Exception as exc:raise GateError('public_feed_unavailable_'+type(exc).__name__) from None
        if len(content)>limit:raise GateError('compressed_feed_too_large')
        atomic_write(destination/name,content)
    return destination

def main():
    parser=argparse.ArgumentParser();sub=parser.add_subparsers(dest='command',required=True)
    source=sub.add_parser('source');source.add_argument('--replay',nargs='+');source.add_argument('--collect',action='store_true');source.add_argument('--credential-config');source.add_argument('--cutoff');source.add_argument('--months-back',type=int,default=3);source.add_argument('--request-budget',type=int,default=600);source.add_argument('--state-dir',required=True);source.add_argument('--feed-dir',required=True)
    target=sub.add_parser('map');target.add_argument('--feed-dir',required=True);target.add_argument('--fetch-public',action='store_true');target.add_argument('--cache',required=True);target.add_argument('--projects',required=True);target.add_argument('--map-dir',required=True);target.add_argument('--state-dir',required=True)
    failed=sub.add_parser('record-failed-step');failed.add_argument('--status-file',required=True)
    args=parser.parse_args();now=utcnow()
    if args.command=='record-failed-step':print(json.dumps(record_failed_step(args.status_file),ensure_ascii=True));return 0
    if args.command=='source':
        cutoff=date.fromisoformat(args.cutoff) if args.cutoff else now.astimezone(KST).date();months=rolling_months(cutoff,args.months_back);budget=RequestBudget(args.request_budget)
        if bool(args.collect)==bool(args.replay):parser.error('Choose exactly one of --collect or --replay; no network by default.')
        if args.collect:
            # Credential lookup occurs inside the guarded attempt so failure preserves status/data.
            def loader(run):return collect_all(existing_key(args.credential_config),months,cutoff,run,budget)
        else:
            def loader(run):return [read_json(p) for directory in args.replay for p in sorted(Path(directory).glob('[0-9]*_*.json'))]
        result=produce_feed(loader,months,cutoff,args.state_dir,args.feed_dir,'network_collection' if args.collect else 'saved_source_replay',now,budget)
    else:
        if args.fetch_public:
            try:fetch_public_feed(args.feed_dir)
            except GateError as exc:
                directory=Path(args.map_dir);previous=prior_status(directory/STATUS_FILE)
                result=status_record('failed','public_feed_fetch',now,previous,reason=str(exc),kept_previous=(directory/MAP_FILE).exists(),network_requests=1)
                with locked(args.state_dir):atomic_write(directory/STATUS_FILE,encoded(result))
                print(json.dumps(result,ensure_ascii=True));return 1
        result=promote_map(Path(args.feed_dir)/'source.json.gz',Path(args.feed_dir)/'status.json',args.cache,args.projects,args.map_dir,args.state_dir,now)
    print(json.dumps(result,ensure_ascii=True));return 0 if result['state']=='ready' else 1

if __name__=='__main__':raise SystemExit(main())
