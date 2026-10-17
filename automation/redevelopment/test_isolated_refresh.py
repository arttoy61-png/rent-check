"""Offline regression tests. Never contacts GitHub, Kakao, or MOLIT."""
import copy
import unittest
from unittest.mock import patch, Mock
import isolated_refresh as m


def ledger():
    return {'epoch':m.EPOCH,'maximum_total':600,'prior_probes':[m.fingerprint(k) for k in m.PRIOR], 'reserved_total':2,'attempts':{}}


class FakeState:
    reserve=m.RemoteState.reserve
    result=m.RemoteState.result
    def __init__(self, data=None):
        from pathlib import Path
        self.root=Path('/fake-unused-state')
        self.ledger=copy.deepcopy(data if data is not None else ledger())
        self.coordinates={}
        self.events=[]
    def persist(self,paths):
        self.events.append(tuple(paths))


class IsolatedTests(unittest.TestCase):
    def setUp(self):
        self.keys=['11500|마곡동|'+str(n) for n in range(742,748)]
        self.patches=[patch.object(m,'atomic_write'),patch.object(m.time,'sleep'),patch.object(m,'candidates',return_value=self.keys)]
        for p in self.patches:p.start();self.addCleanup(p.stop)
    def test_ledger_scope_count_corruption(self):
        self.assertEqual(m.validate_ledger(ledger())['reserved_total'],2)
        for field,value in [('epoch','other'),('maximum_total',601),('reserved_total',True),('reserved_total',3),('attempts',[])]:
            d=ledger();d[field]=value
            with self.subTest(field=field),self.assertRaises(m.GateError):m.validate_ledger(d)
        d=ledger();d['attempts']={m.fingerprint(self.keys[0]):{'state':'unknown'}};d['reserved_total']=3
        with self.assertRaises(m.GateError):m.validate_ledger(d)
    def test_reservation_ceiling_and_prior_skip(self):
        s=FakeState()
        for k in m.PRIOR:self.assertFalse(s.reserve(k))
        for n in range(598):self.assertTrue(s.reserve('11500|마곡동|'+str(1000+n)))
        self.assertEqual(s.ledger['reserved_total'],600)
        self.assertFalse(s.reserve(self.keys[0]))
        request=Mock()
        m.geocode({},s,'fake-test-binding',request=request)
        request.assert_not_called()
    def test_reserve_conflict_prevents_request(self):
        s=FakeState();s.persist=Mock(side_effect=m.GateError('remote_state_changed_stop'));request=Mock()
        with self.assertRaises(m.GateError):m.geocode({},s,'fake-test-binding',request=request)
        request.assert_not_called()
    def test_restart_skips_durable_reserved(self):
        s=FakeState();s.reserve(self.keys[0]);restarted=FakeState(s.ledger)
        request=Mock(return_value=(None,'ZERO_RESULT'))
        m.geocode({},restarted,'fake-test-binding',limit=1,request=request)
        self.assertEqual(request.call_args.args[0],self.keys[1])
        self.assertEqual(restarted.ledger['reserved_total'],4)
    def test_first_error_is_consumed_after_persist(self):
        s=FakeState()
        def request(key,binding):
            self.assertEqual(s.events,[('state/ledger.json',)])
            self.assertEqual(s.ledger['attempts'][m.fingerprint(key)]['state'],'reserved')
            raise TimeoutError()
        _,report=m.geocode({},s,'fake-test-binding',limit=1,request=request)
        self.assertEqual(report['requests_this_run'],1)
        self.assertEqual(s.ledger['attempts'][m.fingerprint(self.keys[0])]['state'],'service_error')
        self.assertFalse(s.reserve(self.keys[0]))
    def test_three_errors_stop(self):
        s=FakeState();request=Mock(return_value=(None,'ERROR'))
        _,report=m.geocode({},s,'fake-test-binding',request=request)
        self.assertEqual(request.call_count,3)
        self.assertEqual(report['reserved_total'],5)
        self.assertEqual(report['stopped'],'three_consecutive_service_errors')
    def test_missing_binding_never_reserves(self):
        s=FakeState();request=Mock()
        m.geocode({},s,'',request=request)
        self.assertEqual(s.ledger['reserved_total'],2);request.assert_not_called()
    def test_remote_reservation_requires_confirmed_head(self):
        from pathlib import Path
        for scenario in ['conflict','push_failure','unconfirmed','success']:
            with self.subTest(scenario=scenario):
                state=m.RemoteState.__new__(m.RemoteState)
                state.root=Path('/fake-unused-state');state.head='old';state.ledger=ledger();state.coordinates={}
                seen=[];rev_calls=0;ls_calls=0
                def fake_git(root,*args):
                    nonlocal rev_calls,ls_calls
                    seen.append(args)
                    if args[0]=='rev-parse':
                        rev_calls+=1
                        return 'old' if rev_calls==1 else 'new'
                    if args[0]=='ls-remote':
                        ls_calls+=1
                        head='changed' if scenario=='conflict' or (ls_calls==2 and scenario=='unconfirmed') else ('old' if ls_calls==1 else 'new')
                        return head+' refs/heads/'+m.BRANCH
                    if args[0]=='diff':return 'state/ledger.json'
                    if args[0]=='push' and scenario=='push_failure':raise m.GateError('state_git_operation_failed')
                    return ''
                request=Mock(return_value=(None,'ZERO_RESULT'))
                with patch.object(m,'git',side_effect=fake_git):
                    if scenario=='success':
                        # Reservation itself must succeed only after the confirmation read.
                        self.assertTrue(state.reserve(self.keys[0]))
                        self.assertEqual(state.head,'new');self.assertEqual(ls_calls,2)
                    else:
                        with self.assertRaises(m.GateError):m.geocode({},state,'fake-test-binding',limit=1,request=request)
                        request.assert_not_called()
                pushes=[a for a in seen if a[0]=='push']
                for args in pushes:self.assertEqual(args,('push','origin','HEAD:refs/heads/'+m.BRANCH))

    def test_match_strictness(self):
        key=self.keys[0]
        doc={'address':{'b_code':'1150010500','region_1depth_name':'서울','region_2depth_name':'강서구','region_3depth_name':'마곡동','mountain_yn':'N','main_address_no':'742','sub_address_no':'0','y':'37.56','x':'126.83'}}
        self.assertIsNotNone(m.validate_match(key,[doc]))
        for field,value in [('b_code','1168010500'),('region_2depth_name','강남구'),('region_3depth_name','대치동'),('main_address_no','743'),('mountain_yn','Y'),('x','nan'),('y','35')]:
            bad=copy.deepcopy(doc);bad['address'][field]=value
            with self.subTest(field=field):self.assertIsNone(m.validate_match(key,[bad]))
        other=copy.deepcopy(doc);other['address']['main_address_no']='743'
        self.assertIsNone(m.validate_match(key,[doc,other]))
        self.assertIsNone(m.validate_match(key,[doc,doc]))

if __name__=='__main__':unittest.main()
