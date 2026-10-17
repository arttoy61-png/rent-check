"""Isolated Seoul preparation. No publishing, secret changes or legacy writes.

Network collection is opt-in through collect(); tests inject a transport.
Every partition is district x contract month x property/transaction type.
"""
from __future__ import annotations
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone, date
from hashlib import sha256
from pathlib import Path
import json
import math
import re
import unicodedata
import urllib.request
import urllib.parse
import urllib.error
import xml.etree.ElementTree as ET

DISTRICTS=dict(zip(
 ['11110','11140','11170','11200','11215','11230','11260','11290','11305','11320','11350','11380','11410','11440','11470','11500','11530','11545','11560','11590','11620','11650','11680','11710','11740'],
 ['종로구','중구','용산구','성동구','광진구','동대문구','중랑구','성북구','강북구','도봉구','노원구','은평구','서대문구','마포구','양천구','강서구','구로구','금천구','영등포구','동작구','관악구','서초구','강남구','송파구','강동구']))
SERVICES={
 '아파트_매매':'RTMSDataSvcAptTrade/getRTMSDataSvcAptTrade',
 '아파트_전월세':'RTMSDataSvcAptRent/getRTMSDataSvcAptRent',
 '연립다세대_매매':'RTMSDataSvcRHTrade/getRTMSDataSvcRHTrade',
 '연립다세대_전월세':'RTMSDataSvcRHRent/getRTMSDataSvcRHRent',
 '오피스텔_매매':'RTMSDataSvcOffiTrade/getRTMSDataSvcOffiTrade',
 '오피스텔_전월세':'RTMSDataSvcOffiRent/getRTMSDataSvcOffiRent',
}
API_ROOT='https://apis.data.go.kr/1613000/'

class CollectionError(RuntimeError):pass

@dataclass
class RequestBudget:
 limit:int=600
 used:int=0
 def take(self):
  if self.used>=self.limit:raise CollectionError('request_budget_exhausted')
  self.used+=1

def norm(value):
 return re.sub(r'\s+',' ',unicodedata.normalize('NFKC',str(value or ''))).strip()

def address_key(lawd,dong,jibun):
 lawd=str(lawd);dong=norm(dong);jibun=norm(jibun)
 if lawd not in DISTRICTS or not dong or not re.fullmatch(r'(?:산\s*)?\d+(?:-\d+)?',jibun):return None
 return '|'.join((lawd,dong,jibun))

def full_address(row):
 key=address_key(row.get('lawd_cd'),row.get('umd_name'),row.get('jibun'))
 if not key:return None
 code,dong,jibun=key.split('|')
 return f'서울특별시 {DISTRICTS[code]} {dong} {jibun}'

def complex_id(row):
 key=address_key(row.get('lawd_cd'),row.get('umd_name'),row.get('jibun'))
 if not key:return None
 kind=norm(row.get('deal_type')).split('_')[0]
 identity=[key,kind,norm(row.get('building_name'))]
 return f"{row['lawd_cd']}-"+sha256(json.dumps(identity,ensure_ascii=False,separators=(',',':')).encode()).hexdigest()[:20]

def cancellation(row):
 flag=norm(row.get('cdeal_type')).upper()
 if flag=='O' or norm(row.get('cdeal_day')):return 'cancelled'
 if flag not in ('','N'):return 'unknown_flag'
 return 'not_marked'

def verified_coordinate(row,cache):
 key=address_key(row.get('lawd_cd'),row.get('umd_name'),row.get('jibun'))
 item=cache.get(key) if key else None
 if not item or item.get('address_key')!=key or item.get('lawd_cd')!=str(row.get('lawd_cd')):return None
 if not item.get('verified') or not item.get('source') or not item.get('checked_at'):return None
 try:lat=float(item['lat']);lng=float(item['lng'])
 except (KeyError,TypeError,ValueError):return None
 if not all(math.isfinite(x) for x in (lat,lng)) or not (37.4<=lat<=37.75 and 126.7<=lng<=127.25):return None
 return {'lat':lat,'lng':lng,'source':item['source'],'checked_at':item['checked_at'],'address_key':key}

def transport(service,params):
 # Never include a request URL, response body or credential in raised errors.
 url=API_ROOT+SERVICES[service]+'?'+urllib.parse.urlencode(params)
 try:
  with urllib.request.urlopen(url,timeout=20) as response:return response.read()
 except urllib.error.HTTPError as exc:raise CollectionError(f'http_{exc.code}') from None
 except Exception as exc:raise CollectionError('transport_'+type(exc).__name__) from None

def parse_page(content,expected_page):
 try:root=ET.fromstring(content)
 except ET.ParseError:raise CollectionError('invalid_xml') from None
 code=norm(root.findtext('.//resultCode'))
 if code not in ('00','000'):raise CollectionError('api_code_'+re.sub(r'[^A-Za-z0-9_-]','',code)[:20])
 try:total=int(root.findtext('.//totalCount',''));page=int(root.findtext('.//pageNo',str(expected_page)))
 except ValueError:raise CollectionError('invalid_pagination_metadata') from None
 if total<0 or page!=expected_page:raise CollectionError('pagination_metadata_mismatch')
 items=[{child.tag:norm(child.text) for child in item} for item in root.findall('.//item')]
 return total,items

def normalize(item,lawd,month,service,cutoff):
 try:contract=date(int(item['dealYear']),int(item['dealMonth']),int(item['dealDay']))
 except (KeyError,ValueError):raise CollectionError('invalid_contract_date') from None
 if contract.strftime('%Y%m')!=month or contract>cutoff:raise CollectionError('contract_outside_requested_period')
 returned_lawd=norm(item.get('sggCd') or item.get('lawdCd'))
 if returned_lawd and returned_lawd!=lawd:raise CollectionError('district_code_mismatch')
 row={'gu_name':DISTRICTS[lawd],'lawd_cd':lawd,'deal_ym':month,'deal_type':service,
  'contract_date':contract.isoformat(),'building_name':item.get('aptNm') or item.get('mhouseNm') or item.get('offiNm') or '',
  'umd_name':item.get('umdNm',''),'jibun':item.get('jibun',''),'area_m2':item.get('excluUseAr',''),
  'floor':item.get('floor',''),'build_year':item.get('buildYear',''),'deal_amount':item.get('dealAmount',''),
  'deposit':item.get('deposit',''),'monthly_rent':item.get('monthlyRent',''),
  'cdeal_type':item.get('cdealType',''),'cdeal_day':item.get('cdealDay',''),
  'contract_type':item.get('contractType',''),'contract_term':item.get('contractTerm',''),
  'use_rr_right':item.get('useRRRight',''),'dealing_gbn':item.get('dealingGbn','')}
 row['address_key']=address_key(lawd,row['umd_name'],row['jibun']);row['address']=full_address(row)
 row['complex_id']=complex_id(row);row['cancellation_status']=cancellation(row)
 return row

def collect_partition(key,lawd,month,service,cutoff,budget,request=transport,page_size=1000):
 if lawd not in DISTRICTS or service not in SERVICES or not re.fullmatch(r'\d{6}',month):raise ValueError('invalid_partition')
 total=None;raw=[];page_hashes=set();pages=[];page=1
 while True:
  budget.take()
  payload=request(service,{'serviceKey':key,'LAWD_CD':lawd,'DEAL_YMD':month,'pageNo':page,'numOfRows':page_size})
  count,items=parse_page(payload,page)
  if total is None:total=count
  elif total!=count:raise CollectionError('total_count_changed_during_pagination')
  digest=sha256(json.dumps(items,sort_keys=True,ensure_ascii=False).encode()).hexdigest()
  if items and digest in page_hashes:raise CollectionError('repeated_page')
  page_hashes.add(digest);pages.append({'page':page,'rows':len(items),'total_count':count,'sha256':digest})
  raw.extend(items)
  if len(raw)>total:raise CollectionError('more_rows_than_total')
  if len(raw)==total:break
  if not items or len(items)<page_size:raise CollectionError('short_page_before_total')
  page+=1
 rows=[normalize(item,lawd,month,service,cutoff) for item in raw]
 # Equal records may be real distinct transactions; report, do not silently dedupe.
 fingerprints=Counter(json.dumps(item,sort_keys=True,ensure_ascii=False) for item in raw)
 return {'lawd_cd':lawd,'gu_name':DISTRICTS[lawd],'month':month,'service':service,'complete':True,
  'collected_at':datetime.now(timezone.utc).isoformat(),'total_count':total,'pages':pages,
  'exact_duplicate_extra_rows':sum(n-1 for n in fingerprints.values()),'raw_items':raw,'rows':rows}

def assess_complete(partitions,months):
 expected={(code,month,service) for code in DISTRICTS for month in months for service in SERVICES}
 keys=[(p['lawd_cd'],p['month'],p['service']) for p in partitions]
 duplicate_keys=[k for k,n in Counter(keys).items() if n>1]
 successful={k for p,k in zip(partitions,keys) if p.get('complete') and p.get('total_count')==len(p.get('rows',[]))}
 missing=sorted(expected-successful);unexpected=sorted(set(keys)-expected)
 return {'complete':not missing and not unexpected and not duplicate_keys,'expected_partitions':len(expected),
  'successful_partitions':len(successful&expected),'missing_or_failed':missing,'unexpected':unexpected,'duplicate_partition_keys':duplicate_keys,
  'complete_districts':sum(all((code,m,s) in successful for m in months for s in SERVICES) for code in DISTRICTS)}

def publishable_rows(partitions):
 """Rows eligible for downstream summaries; unknown cancellation flags stay out."""
 return [r for p in partitions if p.get('complete') for r in p.get('rows',[]) if r['cancellation_status']=='not_marked']

def distance_m(a,b):
 lat1,lat2=map(math.radians,[a['lat'],b['lat']]);dlat=lat2-lat1;dlng=math.radians(b['lng']-a['lng'])
 h=math.sin(dlat/2)**2+math.cos(lat1)*math.cos(lat2)*math.sin(dlng/2)**2
 return 6371008.8*2*math.asin(min(1,math.sqrt(h)))

def nearby(rows,cache,center,radius_m,types):
 if not center or not center.get('verified') or not center.get('source'):return {'status':'project_location_unverified','rows':[]}
 if not 100<=radius_m<=2000:raise ValueError('radius outside supported range')
 result=[];unlinked=0;excluded_cancelled=0
 for row in rows:
  if row['deal_type'] not in types:continue
  if cancellation(row)!='not_marked':excluded_cancelled+=1;continue
  point=verified_coordinate(row,cache)
  if not point:unlinked+=1;continue
  distance=distance_m(center,point)
  if distance<=radius_m:result.append({**row,'distance_m':round(distance),'coordinate':point})
 return {'status':'ready','radius_m':radius_m,'coordinate_unlinked_rows':unlinked,'excluded_cancelled_rows':excluded_cancelled,'rows':result}
