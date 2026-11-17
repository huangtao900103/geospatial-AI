"""Fixed public STAC providers and credential-free observation metadata."""
import json,math,re,threading
from urllib.parse import urlencode,urlsplit,urlunsplit
from urllib.request import Request,build_opener,HTTPRedirectHandler
from backend.core.geo_services import GeoError,finite

PC='https://planetarycomputer.microsoft.com/api/stac/v1/'
EARTH='https://earth-search.aws.element84.com/v1/'
CDSE='https://stac.dataspace.copernicus.eu/v1/'
PROVIDERS={
 'pc_sentinel2_l2a':{'root':PC,'collection':'sentinel-2-l2a','provider':'Microsoft Planetary Computer / ESA','temporal':True},
 'pc_sentinel1_rtc':{'root':PC,'collection':'sentinel-1-rtc','provider':'Microsoft Planetary Computer / ESA','temporal':True},
 'pc_cop_dem_glo30':{'root':PC,'collection':'cop-dem-glo-30','provider':'Microsoft Planetary Computer / Copernicus','temporal':False},
 'pc_landsat_c2_l2':{'root':PC,'collection':'landsat-c2-l2','provider':'Microsoft Planetary Computer / USGS','temporal':True},
 'earthsearch_sentinel2_l2a':{'root':EARTH,'collection':'sentinel-2-l2a','provider':'Element 84 Earth Search / ESA','temporal':True},
 'earthsearch_cop_dem_glo30':{'root':EARTH,'collection':'cop-dem-glo-30','provider':'Element 84 Earth Search / Copernicus','temporal':False},
 'cdse_sentinel2_l2a':{'root':CDSE,'collection':'sentinel-2-l2a','provider':'Copernicus Data Space / ESA','temporal':True},
}
COLLECTIONS={key:value['collection'] for key,value in PROVIDERS.items()}
class NoRedirect(HTTPRedirectHandler):
 def redirect_request(self,*args,**kwargs):raise GeoError('CATALOG_REDIRECT_DENIED','目录服务发生跳转，未访问其他地址',502)
class CatalogGateway:
 def __init__(self):self.opener=build_opener(NoRedirect());self.slots=threading.BoundedSemaphore(4)
 def fetch_json(self,url,check=lambda:None):
  parsed=urlsplit(url)
  if parsed.scheme!='https' or parsed.hostname not in {urlsplit(v['root']).hostname for v in PROVIDERS.values()} or parsed.username or parsed.password:raise GeoError('CATALOG_PROVIDER_DENIED','只允许已登记的固定STAC提供方',400)
  if not self.slots.acquire(timeout=.2):raise GeoError('CATALOG_BUSY','目录请求较多，请稍后重试',503)
  try:
   check()
   with self.opener.open(Request(url,headers={'Accept':'application/geo+json,application/json','User-Agent':'GeoAI-DataLibrary/0.13'}),timeout=12) as r:data=r.read(6*1024*1024+1)
   check()
   if len(data)>6*1024*1024:raise ValueError()
   result=json.loads(data.decode('utf-8'),parse_constant=lambda x:(_ for _ in ()).throw(ValueError()))
   if not isinstance(result,dict):raise ValueError()
   return result
  except GeoError:raise
  except Exception:raise GeoError('CATALOG_UNAVAILABLE','真实STAC目录请求失败、超时或响应无效，未生成替代条目',502)
  finally:self.slots.release()

def search_url(source_id,box,period,limit):
 source=PROVIDERS[source_id];q={'collections':source['collection'],'bbox':','.join(str(v) for v in box),'limit':limit}
 if source['temporal']:q['datetime']=period['start']+'T00:00:00Z/'+period['end']+'T23:59:59Z'
 return source['root']+'search?'+urlencode(q)

def locator(value):
 if not isinstance(value,str) or len(value)>3000:return None
 try:
  p=urlsplit(value)
  if p.scheme not in ('https','s3') or not p.hostname or p.username or p.password or p.port not in (None,443):return None
  return urlunsplit((p.scheme,p.netloc,p.path,'',''))
 except ValueError:return None

def bbox4(value):
 if not isinstance(value,list) or len(value) not in (4,6) or not all(finite(v) for v in value):return None
 b=value[:4] if len(value)==4 else [value[0],value[1],value[3],value[4]]
 return b if -180<=b[0]<b[2]<=180 and -90<=b[1]<b[3]<=90 else None

def assets_metadata(source_id,assets,properties):
 result=[]
 if not isinstance(assets,dict):return result
 landsat=source_id=='pc_landsat_c2_l2'
 aliases={'B02':'blue','B05':'rededge','B8A':'nir_narrow','blue':'blue','B03':'green','B04':'red','B08':'nir','B11':'swir1','B12':'swir2','SCL':'scl','green':'green','red':'red','nir':'nir','nir08':'nir' if landsat else 'nir08','swir16':'swir1','swir22':'swir2','qa_pixel':'qa_pixel','qa_radsat':'qa_radsat','data':'elevation' if source_id in ('earthsearch_cop_dem_glo30','pc_cop_dem_glo30') else None}
 for key,a in list(assets.items())[:100]:
  if not isinstance(a,dict) or not isinstance(key,str):continue
  eo=a.get('eo:bands') or [];raster=a.get('raster:bands') or [];unified=a.get('bands') or []
  band=unified[0] if unified and isinstance(unified[0],dict) else {};r=raster[0] if raster and isinstance(raster[0],dict) else band
  common=aliases.get(key)
  if not common:
   b=eo[0] if eo and isinstance(eo[0],dict) else band
   common=b.get('common_name') or b.get('eo:common_name')
   common={'swir16':'swir1','swir22':'swir2','nir08':'nir' if landsat else 'nir08'}.get(common,common)
  if key in ('visual','rendered_preview','thumbnail') and not aliases.get(key):common='rgb_visualization'
  transform=a.get('proj:transform') or properties.get('proj:transform')
  source_res={'B02':10,'B05':20,'B8A':20,'B03':10,'B04':10,'B08':10,'B11':20,'B12':20,'SCL':20}.get(key) if source_id=='pc_sentinel2_l2a' else None
  resolution=r.get('spatial_resolution') or a.get('gsd') or source_res or properties.get('gsd')
  if resolution is None and isinstance(transform,list) and len(transform)>=6 and finite(transform[0]):resolution=abs(transform[0])
  unit=r.get('unit') or band.get('unit')
  if not unit and common in ('green','red','nir','swir1','swir2'):unit='reflectance_after_calibration'
  result.append({'key':key[:100],'common_name':common,'roles':a.get('roles',[])[:10] if isinstance(a.get('roles'),list) else [],'title':str(a.get('title',key))[:200],'href':locator(a.get('href')),'media_type':a.get('type'),'scale':r.get('scale') if finite(r.get('scale')) else None,'offset':r.get('offset') if finite(r.get('offset')) else None,'nodata':r.get('nodata') if finite(r.get('nodata')) else None,'unit':unit,'resolution':resolution if finite(resolution) else None,'crs':a.get('proj:code') or a.get('proj:epsg') or properties.get('proj:code') or properties.get('proj:epsg'),'bands':[{k:b[k] for k in ('name','common_name','eo:common_name','unit','scale','offset','nodata') if k in b and isinstance(b[k],(str,int,float)) and not isinstance(b[k],bool)} for b in (unified or eo or raster)[:10] if isinstance(b,dict)],'calibration_policy':'product_xml_before_read' if source_id=='pc_sentinel2_l2a' else 'asset_metadata_before_read','auth_refs':[v for v in a.get('auth:refs',[])[:10] if isinstance(v,str)] if isinstance(a.get('auth:refs'),list) else [],'auth_required':source_id in ('cdse_sentinel2_l2a','pc_sentinel1_rtc')})
 return result

def normalize_item(source_id,feature,box,period):
 if not isinstance(feature,dict) or not isinstance(feature.get('id'),str) or not isinstance(feature.get('properties'),dict):raise GeoError('CATALOG_SCHEMA','目录条目格式无效',502)
 source=PROVIDERS[source_id];p=feature['properties'];dt=p.get('datetime') or p.get('start_datetime');b=bbox4(feature.get('bbox'))
 if source['temporal'] and (not isinstance(dt,str) or len(dt)<10 or not period['start']<=dt[:10]<=period['end']):raise GeoError('CATALOG_SCOPE_MISMATCH','观测日期不符合本次条件',502)
 if b is None or b[0]>box[2] or b[2]<box[0] or b[1]>box[3] or b[3]<box[1]:raise GeoError('CATALOG_SCOPE_MISMATCH','条目覆盖范围不符合本次条件',502)
 if feature.get('collection',source['collection'])!=source['collection']:raise GeoError('CATALOG_SCOPE_MISMATCH','目录集合与请求不一致',502)
 assets=assets_metadata(source_id,feature.get('assets',{}),p)
 return {'id':feature['id'],'datetime':dt,'bbox':b,'cloud_cover':p.get('eo:cloud_cover') if finite(p.get('eo:cloud_cover')) else None,'collection':source['collection'],'platform':p.get('platform'),'provider':source['provider'],'asset_roles':list(feature.get('assets',{}))[:100] if isinstance(feature.get('assets'),dict) else [],'assets':assets,'temporal_role':'observation' if source['temporal'] else 'reference_epoch','pixel_verified':False,'geometry':feature.get('geometry') if isinstance(feature.get('geometry'),dict) else None}
