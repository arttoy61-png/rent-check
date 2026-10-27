"""Offline regressions: run separately against each repository."""
import contextlib, importlib.util, io, json, os, sys, tempfile, unittest
from pathlib import Path
from unittest.mock import patch, Mock

REPO=Path(__file__).resolve().parents[1]
SOURCE=REPO/'automation' if REPO.name=='rent-check' else REPO
sys.path.insert(0,str(SOURCE))
from collection_metadata import digest,load_collection,record
from collectors import molit_api as api

def response(total, count):
    return Mock(content=(f'<response><header><resultCode>000</resultCode></header><body><totalCount>{total}</totalCount><items>'+
        '<item><dealYear>2026</dealYear><dealMonth>10</dealMonth><dealDay>26</dealDay></item>'*count+
        '</items></body></response>').encode())

class Evidence(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.csv=Path(self.tmp.name)/'molit.csv';self.csv.write_text('old')
        self.good=dict(complete=True,requested_total=2,successful_keys=[1,2])
    def save(self,meta,accepted=True,now='2026-10-27T06:00:00+00:00'):
        return record(self.csv,load_collection(self.csv),digest(self.csv),meta,accepted,now)
    def test_missing_previous_metadata_never_infers_success(self):
        self.assertEqual(load_collection(self.csv)['status'],'unknown')
        result=self.save(dict(requested_total=2),False)
        self.assertIsNone(result['last_success_at'])
    def test_failure_preserves_last_success(self):
        self.save(self.good,now='2026-10-26T06:00:00+00:00')
        result=self.save(dict(requested_total=2),False)
        self.assertEqual(result['status'],'failed')
        self.assertEqual(result['last_success_at'],'2026-10-26T06:00:00+00:00')
        self.assertEqual(self.csv.read_text(),'old')
    def test_partial_does_not_advance_success(self):
        result=self.save(dict(complete=False,requested_total=2,successful_keys=[1]))
        self.assertEqual(result['status'],'partial');self.assertIsNone(result['last_success_at'])
    def test_success_unchanged_is_check_not_new_transactions(self):
        result=self.save(self.good)
        self.assertEqual(result['status'],'success');self.assertFalse(result['snapshot_changed'])
        self.assertIsNotNone(result['last_success_at'])
    def test_success_new_snapshot(self):
        before=digest(self.csv);self.csv.write_text('new')
        result=record(self.csv,{},before,self.good,True)
        self.assertTrue(result['snapshot_changed']);self.assertEqual(result['status'],'success')
    def test_mismatched_evidence_is_unknown(self):
        self.save(self.good);self.csv.write_text('different')
        self.assertEqual(load_collection(self.csv)['status'],'unknown')
    def test_truncated_metadata_is_unknown(self):
        self.csv.with_suffix('.collection.json').write_text('{')
        self.assertEqual(load_collection(self.csv)['status'],'unknown')
    def test_expected_count_must_match(self):
        result=self.save(dict(complete=True,requested_total=144,successful_keys=[1]))
        self.assertNotEqual(result['status'],'success')

class Paging(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.cache=Path(self.tmp.name)
        self.patcher=patch.object(api,'CACHE_DIR',self.cache);self.patcher.start();self.addCleanup(self.patcher.stop)
    def fetch(self):
        return api.fetch_one_month('synthetic-placeholder','11500','202610','아파트_매매',use_cache=False)
    def test_reads_page_two_over_1000(self):
        with patch.object(api.requests,'get',side_effect=[response(1001,1000),response(1001,1)]) as get,patch.object(api.time,'sleep'):
            self.assertEqual(len(self.fetch()),1001)
            self.assertEqual([c.kwargs['params']['pageNo'] for c in get.call_args_list],[1,2])
    def test_exact_1000_needs_only_one_page(self):
        with patch.object(api.requests,'get',return_value=response(1000,1000)) as get:
            self.assertEqual(len(self.fetch()),1000);self.assertEqual(get.call_count,1)
    def test_empty_page_before_total_fails_and_preserves_cache(self):
        cached=self.cache/f'{api.CACHE_VERSION}_아파트_매매_11500_202610.csv';cached.write_text('previous')
        with patch.object(api.requests,'get',side_effect=[response(1001,1000),response(1001,0)]),patch.object(api.time,'sleep'):
            with self.assertRaisesRegex(RuntimeError,'Incomplete'):self.fetch()
        self.assertEqual(cached.read_text(),'previous')
    def test_short_page_before_total_fails(self):
        with patch.object(api.requests,'get',return_value=response(100,1)),patch.object(api.time,'sleep'):
            with self.assertRaisesRegex(RuntimeError,'Incomplete'):self.fetch()
    def test_missing_total_count_is_not_verified(self):
        xml=b'<response><resultCode>000</resultCode><items/></response>'
        with patch.object(api.requests,'get',return_value=Mock(content=xml)):
            with self.assertRaisesRegex(RuntimeError,'pagination count'):self.fetch()
    def test_total_changes_between_pages_fails(self):
        with patch.object(api.requests,'get',side_effect=[response(1001,1000),response(1002,2)]),patch.object(api.time,'sleep'):
            with self.assertRaisesRegex(RuntimeError,'total changed'):self.fetch()
    def test_valid_zero_is_success(self):
        with patch.object(api.requests,'get',return_value=response(0,0)):
            self.assertEqual(len(self.fetch()),0)
    def test_missing_success_code_is_not_empty_success(self):
        with patch.object(api.requests,'get',return_value=Mock(content=b'<response/>')),patch.object(api.time,'sleep'):
            with self.assertRaises(RuntimeError):self.fetch()


class CollectorEvidence(unittest.TestCase):
    def test_actual_collector_failed_fetch_keeps_csv_and_records_failure(self):
        import types, importlib
        config=types.ModuleType('config')
        config.MOLIT_API_KEY='synthetic-placeholder';config.DEAL_TYPES=['아파트_매매'];config.USE_CACHE=False
        with patch.dict(sys.modules,{'config':config}):
            collector=importlib.import_module('collect_kangseo')
        with tempfile.TemporaryDirectory() as td:
            previous=Path.cwd();os.chdir(td)
            try:
                path=Path('data/molit_kangseo.csv');path.parent.mkdir();path.write_text('previous')
                with patch.object(collector,'fetch_multi',side_effect=RuntimeError('synthetic failure')):
                    with self.assertRaises(RuntimeError):collector.main()
                self.assertEqual(path.read_text(),'previous')
                self.assertEqual(load_collection(path)['status'],'failed')
            finally:os.chdir(previous)

    def test_success_metadata_is_written_only_after_accepted_snapshot(self):
        import types,importlib
        config=types.ModuleType('config')
        config.MOLIT_API_KEY='synthetic-placeholder';config.DEAL_TYPES=['아파트_매매'];config.USE_CACHE=False
        with patch.dict(sys.modules,{'config':config}):
            collector=importlib.import_module('collect_kangseo')
        with tempfile.TemporaryDirectory() as td:
            previous=Path.cwd();os.chdir(td)
            try:
                path=Path('data/molit_kangseo.csv');path.parent.mkdir();path.write_text('previous')
                def collect():
                    collector._collection.update(complete=True,requested_total=1,successful_keys=[1])
                with patch.object(collector,'_collect',side_effect=collect):collector.main()
                self.assertEqual(load_collection(path)['status'],'success')
                self.assertFalse(load_collection(path)['snapshot_changed'])
                def rejected():
                    collect();raise RuntimeError('quality check failed')
                with patch.object(collector,'_collect',side_effect=rejected):
                    with self.assertRaises(RuntimeError):collector.main()
                self.assertEqual(load_collection(path)['status'],'failed')
            finally:os.chdir(previous)

if __name__=='__main__':unittest.main()
