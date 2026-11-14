"""Exercise real catalogue -> calibrated rasters -> typed jobs -> GIS/ZIP over HTTP."""
import argparse,hashlib,io,json,time,zipfile
from pathlib import Path
from urllib.request import Request,urlopen
from urllib.error import HTTPError
parser=argparse.ArgumentParser();parser.add_argument('--port',type=int,default=8017);parser.add_argument('--label',default='preview');args=parser.parse_args();base='http://127.0.0.1:'+str(args.port);out=Path('output/v18-review');out.mkdir(exist_ok=True);proof={'url':base,'notice':'真实公开影像流程验证，ROI不是仙桃行政范围/实测农业地块，不产生作物或灾损认定。','cases':[]}
def save():(out/(args.label+'-processing-proof.json')).write_text(json.dumps(proof,ensure_ascii=False,indent=2))
def request(path,body=None,raw=False):
 try:
  with urlopen(Request(base+path,data=json.dumps(body).encode() if body is not None else None,headers={'Content-Type':'application/json'} if body is not None else {}),timeout=30) as response:content=response.read()
 except HTTPError as error:raise RuntimeError(error.read().decode()) from error
 return content if raw else json.loads(content)
def wait(question):
 for _ in range(1200):
  q=request('/api/assistant/questions/'+question['id'])
  if q['status']!='processing':
   assert q['status']=='answered',json.dumps(q,ensure_ascii=False);return q
  time.sleep(.4)
 raise TimeoutError(question['id'])
for algorithm in ('temporal_median','temporal_mean','temporal_trend','otsu_segmentation'):
 if algorithm=='otsu_segmentation':body={'application_id':'adaptive_segmentation','context':{},'parameters':{'source_ref':'production:production_e4981e20877640','direction':'above','min_patch_pixels':2}}
 else:body={'application_id':'temporal_analysis','context':{'bbox':[114.2,30.5,114.21,30.51],'period':{'start':'2025-10-01','end':'2025-11-30'}},'parameters':{'algorithm_id':algorithm,'source_id':'pc_sentinel2_l2a','index_id':'ndvi','scene_limit':4,'cloud':30}}
 body['request_id']='v18-'+args.label+'-'+algorithm;q=wait(request('/api/application-runs',body));ref=q['answer']['result_ref'];assert ref['type']=='raster_processing';assert ref['url'].startswith('/api/raster-processing/tasks/');task=request(ref['url']);result=task['result'];assert result['summary']['algorithm_id']==algorithm
 bundle=next(item for item in q['answer']['downloads'] if item['url'].endswith('bundle.zip'));data=request(bundle['url'],raw=True)
 with zipfile.ZipFile(io.BytesIO(data)) as z:
  assert z.testzip() is None;m=json.loads(z.read('manifest.json'))
  for item in m['entries']:assert hashlib.sha256(z.read(item['path'])).hexdigest()==item['sha256']
 for artifact in result['artifacts']:
  assert request(artifact['url'],raw=True)
 # Source follow-up must retain the original result, not create another computation.
 follow=wait(request('/api/assistant/questions',{'question':'这些结果来自哪里？','context':{'reference_question_id':q['id']},'request_id':'v18-'+args.label+'-'+algorithm+'-source'}));assert follow['binding']['task_id']==task['id'];assert follow['answer']['map_effect']=='none'
 proof['cases'].append({'algorithm_id':algorithm,'question_id':q['id'],'task_id':task['id'],'summary':result['summary'],'provenance':result['provenance'],'bundle_bytes':len(data),'bundle_sha256':hashlib.sha256(data).hexdigest(),'manifest_entries_verified':len(m['entries']),'followup_question_id':follow['id']});save();print(algorithm,task['id'],result['summary'].get('mean_value'),flush=True)
proof['health']=request('/api/health');proof['map']=request('/api/map');assert proof['map']['features']==[]
datasets=request('/api/datasets');assert not any(d['id']=='xt-fixture' or d.get('source_type')=='synthetic' for d in datasets);proof['datasets']=[{k:d.get(k) for k in ('id','name','source_type','feature_count')} for d in datasets];save();print('Completed 4 real algorithms with source follow-ups, GIS and verified ZIPs',flush=True)
