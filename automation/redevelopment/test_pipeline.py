import copy,gzip,json,os,tempfile,unittest
from pathlib import Path
from datetime import datetime,date,timezone,timedelta
from unittest.mock import patch
from pipeline import *

NOW=datetime(2026,10,16,16,0,tzinfo=timezone.utc)

def fixtures(cancelled=False):
    parts=[]
    for code in DISTRICTS:
        for service in SERVICES:
            raw=[]
            if code=='11500' and service=='아파트_매매':
                raw=[{'dealYear':'2026','dealMonth':'10','dealDay':'15','sggCd':code,'umdNm':'마곡동','jibun':'741','aptNm':'마곡엠밸리8단지','excluUseAr':'84.9','floor':'3','dealAmount':'100,000','cdealType':''}]
                if cancelled:raw.append({**raw[0],'cdealType':'O','cdealDay':'2026.10.16'})
            rows=[normalize(item,code,'202610',service,date(2026,10,16)) for item in raw]
            parts.append({'lawd_cd':code,'month':'202610','service':service,'complete':True,'collected_at':NOW.isoformat(),'total_count':len(raw),'raw_items':raw,'rows':rows,'pages':[{'page':1,'rows':len(raw),'total_count':len(raw),'sha256':digest(raw)}]})
    return parts

class PipelineTests(unittest.TestCase):
    def setUp(self):
        # Fixtures are local replays, even when their parent CI was scheduled.
        # Restore the runner environment after each test; do not alter production
        # status_record or suppress the real scheduled_run flag.
        execution_env=patch.dict(os.environ,{'GITHUB_ACTIONS':'false','GITHUB_EVENT_NAME':''})
        execution_env.start();self.addCleanup(execution_env.stop)
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
        self.feed=self.root/'feed';self.map=self.root/'map';self.source_state=self.root/'source-state';self.map_state=self.root/'map-state'
        self.parts=fixtures();self.cache=self.root/'cache.json';self.projects=self.root/'projects.json'
        self.cache.write_bytes(encoded({'11500|마곡동|741':{'address_key':'11500|마곡동|741','lawd_cd':'11500','lat':37.563954,'lng':126.819605,'verified':True,'source':'fixture://address-proof','checked_at':NOW.isoformat()}}))
        self.projects.write_bytes(encoded({'fixture-project':{'verified':True,'source':'fixture://official-boundary','point':[37.565231,126.815685],'basis':'verified_official_boundary_centroid'}}))
    def tearDown(self):self.tmp.cleanup()
    def source(self,parts=None,**kw):return produce_feed(lambda run:parts if parts is not None else self.parts,['202610'],date(2026,10,16),self.source_state,self.feed,now=kw.pop('now',NOW),**kw)
    def promote(self,**kw):return promote_map(self.feed/'source.json.gz',self.feed/'status.json',self.cache,self.projects,self.map,self.map_state,now=kw.pop('now',NOW),**kw)
    def good(self):self.assertEqual(self.source()['state'],'ready');self.assertEqual(self.promote()['state'],'ready');return (self.map/MAP_FILE).read_bytes()
    def test_complete_round_trip_and_no_scheduling(self):
        self.good();status=read_json(self.map/STATUS_FILE);snapshot=read_json(self.map/MAP_FILE)
        self.assertEqual(snapshot['meta']['complete_districts'],25);self.assertEqual(snapshot['meta']['raw_rows'],1)
        self.assertFalse(status['scheduled_run']);self.assertFalse(status['scheduler_registered_locally']);self.assertEqual(status['network_requests'],0)
    def test_round_trip_records_execution_context_without_registering_scheduler(self):
        cases=[('false','schedule','local',False),('true','schedule','github_actions',True),('true','workflow_dispatch','github_actions',False),('true','push','github_actions',False),('true','pull_request','github_actions',False)]
        for actions,event,environment,scheduled in cases:
            with self.subTest(actions=actions,event=event):
                with patch.dict(os.environ,{'GITHUB_ACTIONS':actions,'GITHUB_EVENT_NAME':event}):
                    self.good()
                    for path in [self.feed/'status.json',self.map/STATUS_FILE]:
                        status=read_json(path)
                        self.assertEqual(status['execution_environment'],environment)
                        self.assertEqual(status['scheduled_run'],scheduled)
                        self.assertFalse(status['scheduler_registered_locally'])
                        self.assertEqual(status['network_requests'],0)
                self.assertEqual(os.environ['GITHUB_ACTIONS'],'false')
                self.assertEqual(os.environ['GITHUB_EVENT_NAME'],'')
    def test_rolling_month_year_boundary(self):self.assertEqual(rolling_months(date(2027,1,5)),['202611','202612','202701'])
    def test_future_cutoff_rejected(self):
        with self.assertRaisesRegex(GateError,'future_collection_cutoff'):validate_partitions(self.parts,['202610'],date(2026,10,18),NOW)
    def test_process_failure_fallback_keeps_snapshot(self):
        old=self.good();result=record_failed_step(self.map/STATUS_FILE)
        self.assertEqual(result['state'],'failed');self.assertTrue(result['kept_previous']);self.assertEqual((self.map/MAP_FILE).read_bytes(),old)
    def test_24_districts_preserve_feed_and_map(self):
        original=self.good();source_before=(self.feed/'source.json.gz').read_bytes()
        result=self.source([p for p in self.parts if p['lawd_cd']!='11740'])
        self.assertEqual(result['state'],'failed');self.assertEqual((self.feed/'source.json.gz').read_bytes(),source_before)
        self.assertEqual(self.promote()['state'],'failed');self.assertEqual((self.map/MAP_FILE).read_bytes(),original)
    def test_missing_service_is_not_complete(self):self.assertEqual(self.source(self.parts[:-1])['state'],'failed')
    def test_duplicate_partition_rejected(self):self.assertEqual(self.source(self.parts+[self.parts[0]])['state'],'failed')
    def test_page_total_tamper_preserves_source(self):
        self.source();old=(self.feed/'source.json.gz').read_bytes();parts=copy.deepcopy(self.parts);parts[0]['pages'][0]['total_count']=1
        self.assertEqual(self.source(parts)['reason'],'page_total_mismatch');self.assertEqual((self.feed/'source.json.gz').read_bytes(),old)
    def test_page_hash_tamper_rejected(self):
        parts=copy.deepcopy(self.parts);parts[0]['pages'][0]['sha256']='bad'
        self.assertEqual(self.source(parts)['reason'],'page_hash_mismatch')
    def test_cancel_normalization_tamper_rejected(self):
        parts=fixtures(True);part=next(p for p in parts if len(p['rows'])>0);part['rows'][1]['cancellation_status']='not_marked'
        self.assertEqual(self.source(parts)['reason'],'normalized_row_or_cancellation_mismatch')
    def test_cancelled_source_retained_but_map_excludes(self):
        self.source(fixtures(True));self.promote();out=read_json(self.map/MAP_FILE)
        self.assertEqual(out['meta']['raw_rows'],2);self.assertEqual(out['meta']['excluded_cancelled'],1);self.assertEqual(len(out['rows']),1)
    def test_empty_city_does_not_replace_good(self):
        self.good();parts=copy.deepcopy(self.parts)
        for p in parts:p.update(raw_items=[],rows=[],total_count=0,pages=[{'page':1,'rows':0,'total_count':0,'sha256':digest([])}])
        self.assertEqual(self.source(parts)['reason'],'empty_city_snapshot_requires_review')
    def test_stale_source_rejected(self):self.assertEqual(self.source(now=NOW+timedelta(days=3))['reason'],'source_older_than_48_hours')
    def test_future_timestamp_rejected(self):self.assertEqual(self.source(now=NOW-timedelta(days=1))['reason'],'future_source_timestamp')
    def test_exact_dong_and_gu_required_for_coordinates(self):
        old=self.good();self.cache.write_bytes(encoded({'마곡동|741':{'lat':37.563954,'lng':126.819605,'verified':True}}))
        self.assertEqual(self.promote()['reason'],'no_verified_coordinates_linked_keep_last_good');self.assertEqual((self.map/MAP_FILE).read_bytes(),old)
    def test_partial_coordinates_are_explicit(self):
        parts=copy.deepcopy(self.parts);p=next(p for p in parts if p['rows']);raw={**p['raw_items'][0],'jibun':'999'};p['raw_items'].append(raw);p['rows'].append(normalize(raw,p['lawd_cd'],p['month'],p['service'],date(2026,10,16)));p['total_count']=2;p['pages']=[{'page':1,'rows':2,'total_count':2,'sha256':digest(p['raw_items'])}]
        self.source(parts);self.promote();out=read_json(self.map/MAP_FILE)
        self.assertEqual(out['meta']['coordinate_unlinked_rows'],1);self.assertFalse(out['meta']['coordinate_coverage_complete'])
    def test_bad_feed_digest_keeps_map(self):
        old=self.good();(self.feed/'source.json.gz').write_bytes(b'bad')
        self.assertEqual(self.promote()['reason'],'feed_digest_mismatch');self.assertEqual((self.map/MAP_FILE).read_bytes(),old)
    def test_interruption_before_source_swap_keeps_feed(self):
        self.source();old=(self.feed/'source.json.gz').read_bytes()
        def failure():raise OSError('simulated power loss before replace')
        self.assertEqual(self.source(before_replace=failure)['state'],'failed');self.assertEqual((self.feed/'source.json.gz').read_bytes(),old)
    def test_interruption_before_map_swap_keeps_map(self):
        old=self.good()
        def failure():raise OSError('simulated interrupted write')
        self.assertEqual(self.promote(before_replace=failure)['state'],'failed');self.assertEqual((self.map/MAP_FILE).read_bytes(),old)
    def test_older_source_cannot_roll_back_map(self):
        old=self.good();parts=copy.deepcopy(self.parts)
        for p in parts:p['collected_at']=(NOW-timedelta(hours=1)).isoformat()
        self.source(parts);self.assertEqual(self.promote()['reason'],'older_source_would_replace_newer_snapshot');self.assertEqual((self.map/MAP_FILE).read_bytes(),old)
    def test_lock_blocks_second_writer(self):
        self.good()
        with locked(self.map_state):
            with self.assertRaisesRegex(GateError,'another_update'):self.promote()
    def test_secret_not_in_failure_report(self):
        def loader(run):raise ValueError('secret-value-should-not-appear')
        result=produce_feed(loader,['202610'],date(2026,10,16),self.source_state,self.feed,now=NOW)
        self.assertNotIn('secret-value',json.dumps(result));self.assertEqual(result['reason'],'ValueError')
    def test_budget_exhaustion_cannot_publish_partial(self):
        saved={(p['lawd_cd'],p['service']):p for p in self.parts};budget=RequestBudget(149)
        def collector(key,code,month,service,cutoff,b):b.take();return saved[(code,service)]
        def loader(run):return collect_all('fixture', ['202610'],date(2026,10,16),run,budget,collector,0)
        result=produce_feed(loader,['202610'],date(2026,10,16),self.source_state,self.feed,now=NOW,budget=budget)
        self.assertEqual(result['state'],'failed');self.assertEqual(budget.used,149);self.assertFalse((self.feed/'source.json.gz').exists())

if __name__=='__main__':unittest.main()
