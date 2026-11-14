"""Real HTTP acceptance checks. Selected test AOIs are not surveyed agricultural parcels."""
import argparse,hashlib,io,json,time,zipfile
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request,urlopen
import numpy as np
from rasterio.io import MemoryFile

parser=argparse.ArgumentParser();parser.add_argument('--port',type=int,default=8017);parser.add_argument('--label',default='preview');args=parser.parse_args()
base='http://127.0.0.1:'+str(args.port);output=Path('output/v16-agriculture')/(args.label+'-http-proof.json');evidence={'url':base,'notice':'公开遥感真实像元试验；框选测试区不是实测农田，FVC端元0.2/0.8为显式测试假设。','spectral':[]}
def save():output.write_text(json.dumps(evidence,ensure_ascii=False,indent=2))
def request(path,body=None,raw=False):
 try:
  with urlopen(Request(base+path,data=json.dumps(body).encode() if body is not None else None,headers={'Content-Type':'application/json'} if body is not None else {}),timeout=30) as response:data=response.read()
 except HTTPError as error:raise RuntimeError(error.read().decode()) from error
 return data if raw else json.loads(data)
def await_question(question):
 for _ in range(700):
  q=request('/api/assistant/questions/'+question['id'])
  if q['status']!='processing':
   if q['status']!='answered':raise RuntimeError(json.dumps(q,ensure_ascii=False))
   return q
  time.sleep(.4)
 raise TimeoutError(question['id'])
def bundle(q):
 link=next(x for x in q['answer']['downloads'] if x['url'].endswith('bundle.zip'));data=request(link['url'],raw=True)
 with zipfile.ZipFile(io.BytesIO(data)) as z:
  names=z.namelist();assert len(names)==len(set(names));assert 'manifest.json' in names
 return {'bytes':len(data),'sha256':hashlib.sha256(data).hexdigest(),'files':names}
context={'bbox':[114.2,30.5,114.21,30.51],'period':{'start':'2025-11-01','end':'2025-11-30'}}
forms={'evi2':'crop_spectral','savi':'crop_spectral','msavi2':'crop_spectral','gndvi':'crop_spectral','ndre':'crop_rededge','bsi':'soil_spectral'}
formulas={'evi2':lambda b:2.5*(b['nir']-b['red'])/(b['nir']+2.4*b['red']+1),'savi':lambda b:1.5*(b['nir']-b['red'])/(b['nir']+b['red']+.5),'msavi2':lambda b:(2*b['nir']+1-np.sqrt((2*b['nir']+1)**2-8*(b['nir']-b['red'])))/2,'gndvi':lambda b:(b['nir']-b['green'])/(b['nir']+b['green']),'ndre':lambda b:(b['nir_narrow']-b['rededge'])/(b['nir_narrow']+b['rededge']),'bsi':lambda b:((b['swir1']+b['red'])-(b['nir']+b['blue']))/(b['swir1']+b['red']+b['nir']+b['blue'])}
for source,index in [('pc_sentinel2_l2a',id) for id in forms]+[('pc_landsat_c2_l2','evi2')]:
 parameters={'source_id':source,'threshold':0,'operator':'gt'}
 if forms[index]=='crop_spectral':parameters['index_id']=index
 question=await_question(request('/api/application-runs',{'application_id':forms[index],'context':context,'parameters':parameters,'request_id':'v16-'+args.label+'-'+source+'-'+index}))
 task=request(question['answer']['result_ref']['url']);r=task['result'];data=request(question['answer']['result_ref']['url']+'/analysis.tif',raw=True)
 with MemoryFile(data) as mem,mem.open() as ds:
  names=ds.descriptions;b={name[6:-12]:ds.read(i+1).astype('float64') for i,name in enumerate(names) if name.startswith('after_') and name.endswith('_reflectance')}
  actual=ds.read(names.index('after_'+index)+1);valid=ds.read(names.index('common_valid_mask')+1)==1
  expected=formulas[index](b).astype('float32');np.testing.assert_allclose(actual[valid],expected[valid],rtol=0,atol=1e-6);assert int(valid.sum())==r['summary']['valid_count']
  proof={'all_valid_cells_checked':int(valid.sum()),'max_abs_formula_error':float(np.max(np.abs(actual[valid]-expected[valid]))) if valid.any() else None,'bands':list(names),'crs':str(ds.crs),'resolution':list(ds.res)}
 evidence['spectral'].append({'index_id':index,'source_id':source,'question_id':question['id'],'task_id':task['id'],'mean_index':r['summary']['mean_index'],'valid_count':r['summary']['valid_count'],'source_details':r['provenance'].get('source_details',{}),'verification':proof,'bundle':bundle(question)});save();print(source,index,task['id'],r['summary']['mean_index'],flush=True)
# Reuse a real archived single-period NDVI, never create a guessed source or teaching raster.
apps=request('/api/applications')['applications'];cover=next(a for a in apps if a['id']=='vegetation_cover');sources=cover['method']['raster_sources'];ref=next((k for k,v in sources.items() if v['task_id']=='production_e4981e20877640'),None)
if not ref:raise RuntimeError('Expected verified NDVI task unavailable')
q=await_question(request('/api/application-runs',{'application_id':'vegetation_cover','context':{},'parameters':{'source_ref':ref,'soil_ndvi':.2,'vegetation_ndvi':.8},'request_id':'v16-'+args.label+'-fvc'}));fvc=request(q['answer']['result_ref']['url']);evidence['fvc']={'question_id':q['id'],'task_id':fvc['id'],'summary':fvc['result']['summary'],'source':fvc['plan']['source'],'bundle':bundle(q)};save()
def polygon(b):
 w,s,e,n=b;return {'type':'Polygon','coordinates':[[[w,s],[e,s],[e,n],[w,n],[w,s]]]}
zones=[('源范围内测试区',[114.201,30.501,114.205,30.505]),('部分覆盖测试区',[114.208,30.505,114.213,30.51]),('源范围外测试区',[114.22,30.52,114.225,30.525])]
geo={'type':'FeatureCollection','features':[{'type':'Feature','id':'verification-'+str(i+1),'properties':{'name':name,'purpose':'算法核验选区，非实测农田'},'geometry':polygon(b)} for i,(name,b) in enumerate(zones)]}
dataset=request('/api/datasets',{'name':'农业算法核验选区（非实测地块）','geojson':geo});evidence['dataset_id']=dataset['id'];save()
q=await_question(request('/api/application-runs',{'application_id':'field_zonal_statistics','context':{'dataset_id':dataset['id']},'parameters':{'source_ref':ref,'field':'auto','threshold':.3},'request_id':'v16-'+args.label+'-zonal'}));t=request(q['answer']['result_ref']['url']);s=t['result']['summary'];assert s['zone_count']==3 and s['full_zone_count']==1 and s['partial_zone_count']==1 and s['outside_zone_count']==1
evidence['zonal']={'question_id':q['id'],'task_id':t['id'],'summary':s,'rows':[f['properties'] for f in t['result']['geojson']['features']],'bundle':bundle(q)}
evidence['health']=request('/api/health');save();print('FVC and zonal complete',evidence['dataset_id'],flush=True)
