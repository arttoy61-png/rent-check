import unittest
import json
from datetime import date
from xml.etree.ElementTree import Element,SubElement,tostring
from seoul_snapshot import *

def page_xml(items,total,page=1,code='000'):
 root=Element('response');header=SubElement(root,'header');SubElement(header,'resultCode').text=code
 body=SubElement(root,'body');SubElement(body,'totalCount').text=str(total);SubElement(body,'pageNo').text=str(page)
 group=SubElement(body,'items')
 for data in items:
  item=SubElement(group,'item')
  for k,v in data.items():SubElement(item,k).text=str(v)
 return tostring(root,encoding='utf-8')

def item(**changes):
 return dict(dealYear='2026',dealMonth='10',dealDay='15',aptNm='동명아파트',umdNm='신사동',jibun='10',excluUseAr='84',floor='7',dealAmount='100,000',**changes)

def make_item(**changes):
 data=item();data.update(changes);return data

class SnapshotTests(unittest.TestCase):
 def collect(self,responses,**kwargs):
  calls=[]
  def request(service,params):calls.append(dict(params));return responses[len(calls)-1]
  result=collect_partition('TEST_ONLY_NOT_A_KEY','11680','202610','아파트_매매',date(2026,10,16),RequestBudget(20),request=request,**kwargs)
  return result,calls
 def test_paginate_over_1000(self):
  records=[make_item(floor=str(i)) for i in range(1000)]
  result,calls=self.collect([page_xml(records,1001),page_xml([make_item(floor='1000')],1001,2)])
  self.assertEqual([c['pageNo'] for c in calls],[1,2]);self.assertEqual(len(result['rows']),1001)
  self.assertNotIn('TEST_ONLY_NOT_A_KEY',json.dumps(result))
 def test_short_page_is_not_complete(self):
  with self.assertRaisesRegex(CollectionError,'short_page'):self.collect([page_xml([item()],1001)])
 def test_missing_page_is_not_complete(self):
  with self.assertRaisesRegex(CollectionError,'short_page'):self.collect([page_xml([item()],2),page_xml([],2,2)],page_size=1)
 def test_changed_total_is_not_complete(self):
  with self.assertRaisesRegex(CollectionError,'total_count_changed'):self.collect([page_xml([item()],2),page_xml([make_item(floor='8')],3,2)],page_size=1)
 def test_repeat_page_is_detected(self):
  with self.assertRaisesRegex(CollectionError,'repeated_page'):self.collect([page_xml([item()],2),page_xml([item()],2,2)],page_size=1)
 def test_zero_rows_is_valid_success(self):
  result,_=self.collect([page_xml([],0)]);self.assertTrue(result['complete']);self.assertEqual(result['rows'],[])
 def test_missing_total_is_failure(self):
  with self.assertRaisesRegex(CollectionError,'invalid_pagination'):self.collect([b'<response><resultCode>00</resultCode></response>'])
 def test_api_error_is_redacted(self):
  with self.assertRaisesRegex(CollectionError,'api_code_22'):self.collect([page_xml([],0,code='22')])
 def test_future_contract_rejected(self):
  with self.assertRaisesRegex(CollectionError,'outside_requested'):self.collect([page_xml([make_item(dealDay='17')],1)])
 def test_different_district_rejected(self):
  with self.assertRaisesRegex(CollectionError,'district_code'):self.collect([page_xml([make_item(sggCd='11500')],1)])
 def test_budget_stops_before_request(self):
  budget=RequestBudget(0)
  with self.assertRaisesRegex(CollectionError,'budget'):collect_partition('x','11680','202610','아파트_매매',date(2026,10,16),budget,request=lambda *_:self.fail('budget must prevent request'))
 def test_cancelled_and_unknown_excluded(self):
  rows=[normalize(make_item(cdealType=x),'11680','202610','아파트_매매',date(2026,10,16)) for x in ['','O','?']]
  self.assertEqual(len(publishable_rows([{'complete':True,'rows':rows}])),1)
  self.assertEqual(cancellation({'cdeal_type':'','cdeal_day':'20261016'}),'cancelled')
 def test_equal_rows_are_reported_not_silently_removed(self):
  result,_=self.collect([page_xml([item(),item()],2)]);self.assertEqual(len(result['rows']),2);self.assertEqual(result['exact_duplicate_extra_rows'],1)
 def test_keys_include_district_lot_and_property_type(self):
  row=normalize(item(),'11680','202610','아파트_매매',date(2026,10,16))
  others=[{**row,'lawd_cd':'11380'},{**row,'jibun':'11'},{**row,'deal_type':'오피스텔_매매'}]
  self.assertEqual(len({complex_id(x) for x in [row,*others]}),4)
  self.assertIn('강남구',full_address(row));self.assertIn('은평구',full_address(others[0]))
  self.assertNotIn('강서구',full_address(row))
 def test_unknown_lot_never_gets_coordinate_key(self):
  for lot in ['',None,'****','10 외 2필지']:self.assertIsNone(address_key('11680','신사동',lot))
 def test_legacy_or_other_district_cache_is_ignored(self):
  row=normalize(item(),'11680','202610','아파트_매매',date(2026,10,16));key=row['address_key']
  point={'lat':37.52,'lng':127.02,'verified':True,'source':'fixture','checked_at':'2026-10-16','lawd_cd':'11680','address_key':key}
  self.assertIsNone(verified_coordinate(row,{'신사동 10':point}))
  self.assertIsNone(verified_coordinate(row,{key:{**point,'lawd_cd':'11380'}}))
  self.assertIsNone(verified_coordinate(row,{key:{**point,'verified':False}}))
  self.assertIsNotNone(verified_coordinate(row,{key:point}))
 def test_all_25_districts_are_required(self):
  parts=[{'lawd_cd':code,'month':'202610','service':service,'complete':True,'total_count':0,'rows':[]} for code in DISTRICTS for service in SERVICES]
  self.assertTrue(assess_complete(parts,['202610'])['complete'])
  self.assertEqual(assess_complete(parts[:-6],['202610'])['complete_districts'],24)
  self.assertFalse(assess_complete(parts[:-6],['202610'])['complete'])
  self.assertFalse(assess_complete(parts+[parts[0]],['202610'])['complete'])
 def test_nearby_uses_only_verified_points_and_non_cancelled(self):
  row=normalize(item(),'11680','202610','아파트_매매',date(2026,10,16));key=row['address_key']
  point={'lat':37.52,'lng':127.02,'verified':True,'source':'fixture','checked_at':'2026-10-16','lawd_cd':'11680','address_key':key}
  rows=[row,{**row,'cdeal_type':'O'},{**row,'jibun':'20'}]
  found=nearby(rows,{key:point},point,500,['아파트_매매'])
  self.assertEqual(len(found['rows']),1);self.assertEqual(found['excluded_cancelled_rows'],1);self.assertEqual(found['coordinate_unlinked_rows'],1)
  self.assertEqual(nearby(rows,{key:point},{**point,'verified':False},500,['아파트_매매'])['status'],'project_location_unverified')

if __name__=='__main__':unittest.main(verbosity=2)
