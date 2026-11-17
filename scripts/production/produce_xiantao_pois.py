#!/usr/bin/env python3
"""Bounded public address clues, never a complete licensed-business inventory."""
import argparse,csv,hashlib,json,re,sys
from pathlib import Path
from datetime import datetime,timezone
sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
from backend.integrations.tianditu_services import TiandituServices
from backend.analysis.landcover_services import load_boundary
from backend.core.geo_services import point_in_ring

QUERIES=[('supplies','仙桃市 农资','农资|种子|化肥','农资相关地址线索',['supplies']),('breeding','仙桃市 养殖场','养殖|禽|鸡|猪|牧|渔','养殖相关地址线索',['breeding']),('eel','仙桃市 黄鳝','黄鳝|鳝','黄鳝相关地址线索',['eel']),('coldchain','仙桃市 冷链','冷链|冷库|冷藏|冷冻','冷链相关地址线索',['coldchain']),('machinery','仙桃市 农机','农机|农用机械','农机服务地址线索（非设备轨迹）',['machinery'])]

def csv_text(value):
 """Protect spreadsheet text exports without altering source/GeoJSON values.

 CSV quoting alone does not stop formula execution. Ignore leading whitespace
 or a BOM when checking formula prefixes; preserve the complete original text
 after a leading apostrophe. Coordinates stay numeric and do not use this path.
 """
 text=str(value)
 if re.match(r'^[\s\ufeff]*[=+\-@]',text) or text.startswith(('\t','\r','\n')):return "'"+text
 return text

def write_locations_csv(path,rows):
 with Path(path).open('w',encoding='utf-8-sig',newline='') as stream:
  writer=csv.writer(stream);writer.writerow(['provider_id','name','address','longitude','latitude','verification'])
  for row in rows:writer.writerow([csv_text(row['place_id']),csv_text(row['label']),csv_text(row['address']),*row['center'],'unverified public location clue'])

def within(point,boundary):
 for f in boundary['features']:
  geom=f['geometry'];polygons=[geom['coordinates']] if geom['type']=='Polygon' else geom['coordinates']
  for poly in polygons:
   if point_in_ring(point,poly[0]) and not any(point_in_ring(point,h) for h in poly[1:]):return True
 return False

def produce(root='data/agriculture-atlas'):
 service=TiandituServices(config_path='data/integrations/tianditu.json',timeout=10);boundary,meta=load_boundary();reports=[]
 for category,query,pattern,title,scenes in QUERIES:
  try:rows=service.search(query,20)
  except Exception as e:reports.append({'id':category,'status':'failed','error_code':getattr(e,'code',type(e).__name__)});continue
  accepted=[];seen=set();outside=0;unmatched=0
  for r in rows:
   if not re.search(pattern,r['label']):unmatched+=1;continue
   if not within(r['center'],boundary):outside+=1;continue
   ident=r['place_id']
   if ident in seen:continue
   seen.add(ident);accepted.append(r)
  ident='xiantao-poi-'+category;folder=Path(root)/ident;folder.mkdir(parents=True,exist_ok=True)
  features=[{'type':'Feature','id':r['place_id'],'properties':{'名称':r['label'],'公开地址':r['address'],'线索类别':title,'来源':'天地图地名地址服务','核实状态':'待业务核实，非许可名录'},'geometry':{'type':'Point','coordinates':r['center']}} for r in accepted]
  geo={'type':'FeatureCollection','features':features};(folder/'locations.geojson').write_text(json.dumps(geo,ensure_ascii=False,indent=2)+'\n')
  source={'query':query,'first_page_limit':20,'returned_count':len(rows),'accepted_count':len(features),'outside_reference_boundary_count':outside,'name_unmatched_count':unmatched,'source_url':'https://www.tianditu.gov.cn/','api_documentation':'https://lbs.tianditu.gov.cn/server/search.html','boundary_source':meta,'candidate_records':rows,'fetched_at':datetime.now(timezone.utc).isoformat(),'filter_rule':'公开名称匹配业务关键词且点位在2017参考市界内，按provider place_id去重；只处理首20候选，不保证完整。'}
  (folder/'source.json').write_text(json.dumps(source,ensure_ascii=False,indent=2)+'\n')
  write_locations_csv(folder/'locations.csv',accepted)
  layers=[{'id':ident,'title':title,'kind':'geojson','geojson':geo,'legend':[{'color':'#68cdd4','label':'公开地址线索（非监管对象认定）'}]}] if features else []
  result={'schema_version':'agriculture-atlas-1.0','id':ident,'title':'仙桃'+title,'scene_ids':scenes,'status':'ready','product_type':'public_address_clues','scope_label':'2017公开参考市界内的本次关键词地址候选，非全量','period_label':'检索时间 '+source['fetched_at'][:10],'resolution_m':None,'summary_metrics':[{'label':'本次已定位候选','value':len(features),'unit':'条'},{'label':'原始首屏候选','value':len(rows),'unit':'条'}],'limitations':['仅为天地图地名地址关键词检索首20候选，不是全市机构总数或经营许可名录。','名称相关不等于已核实经营类型、产能、容量、资质或食品安全；餐饮和服务地址也可能命中。','点位保留服务返回经纬度，未作GCJ转换，位置和主体身份需现场/业务核实。','农机地址不是设备实时位置或作业轨迹；不生成轨迹、作业面积或监管结论。'],'layers':layers,'provenance':{'sources':[{'title':'天地图地名地址服务','url':'https://www.tianditu.gov.cn/'}],'source_query':query,'first_page_limit':20,'boundary_year':2017,'fetched_at':source['fetched_at'],'method':source['filter_rule'],'coordinate_reference':'服务返回经纬度，未作GCJ偏移'},'created_at':source['fetched_at'],'artifacts':['result.json','source.json','locations.geojson','locations.csv']}
  (folder/'result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n');reports.append({'id':ident,'status':'ready','returned':len(rows),'accepted':len(features),'outside':outside,'unmatched':unmatched});print(reports[-1],flush=True)
 Path('output/xiantao/poi-production.json').write_text(json.dumps(reports,ensure_ascii=False,indent=2)+'\n');return reports
if __name__=='__main__':
 parser=argparse.ArgumentParser(description=__doc__)
 parser.add_argument('--output',default='data/agriculture-atlas',help='公开地址候选成果的输出目录')
 args=parser.parse_args()
 produce(args.output)
