"""Landsat 8/9 Collection 2 L2 SR: bounded real COG reads, not catalog-only.

Calibration comes from the actual selected STAC assets. QA_PIXEL fill, dilated
cloud, cirrus, cloud, shadow and snow are excluded; QA_RADSAT nonzero is excluded
conservatively across all bands/terrain. Water is retained. Unsigned immutable
asset URLs only are persisted; provider SAS remains in memory.
"""
import copy
import hashlib
import json
import math
import queue
import re
import threading
import time
from urllib.parse import urlencode,urlsplit
from urllib.request import Request,build_opener,HTTPRedirectHandler
from geo_services import GeoError,finite
from spectral_indices import specification as index_specification

VERSION='landsat-c2-l2-oli-1.0'
SOURCE_ID='pc_landsat_c2_l2'
COLLECTION='landsat-c2-l2'
STAC_ROOT='https://planetarycomputer.microsoft.com/api/stac/v1/'
HOST='landsateuwest.blob.core.windows.net'
BANDS={'blue':('blue','SR_B2'),'green':('green','SR_B3'),'red':('red','SR_B4'),'nir':('nir08','SR_B5'),'swir1':('swir16','SR_B6'),'swir2':('swir22','SR_B7')}
QUALITY_POLICY={'id':'landsat-oli-qa-pixel-radsat-v1','qa_pixel_excluded_bits':[0,1,2,3,4,5],'qa_radsat_rule':'all nonzero excluded (conservative all-band saturation and terrain occlusion)','water_bit_7_retained':True,'reflectance_rule':'no clipping; negative inputs and nonpositive denominator excluded by index engine','source_url':'https://www.usgs.gov/landsat-missions/landsat-collection-2-quality-assessment-bands'}


def specification(index_id='ndvi'):
    value=index_specification(index_id)
    if SOURCE_ID not in value['supported_source_ids']:raise GeoError('SOURCE_INDEX_UNSUPPORTED','Landsat8/9没有本NDRE定义所需的红边与窄NIR波段，不能用其他波段替代',422)
    value['required_bands']=[{'id':BANDS[b['name']][1],'name':b['name'],'asset_key':BANDS[b['name']][0],'native_resolution_m':30} for b in value['required_bands']]
    value['reference_band']=BANDS[value['reference_key']][1]
    value['reference_resolution_m']=30
    value['sensor']='landsat-8/9-oli'
    value['quality_policy']=copy.deepcopy(QUALITY_POLICY)
    value['limitations']=[s for s in value['limitations'] if not s.startswith('SCL')]+['Landsat8/9 L2 SR：按实际STAC scale/offset定标；QA_PIXEL bits0–5与所有非零QA_RADSAT排除；不clip负值，负反射率/非正分母不参与指数统计。']
    return value


def asset_url(value):
    if not isinstance(value,str) or len(value)>2000 or any(ord(c)<33 for c in value):raise GeoError('LANDSAT_ASSET_DENIED','Landsat资产地址无效',502)
    try:p=urlsplit(value);port=p.port
    except ValueError:raise GeoError('LANDSAT_ASSET_DENIED','Landsat资产地址无效',502)
    if p.scheme!='https' or p.hostname!=HOST or p.username or p.password or port is not None or p.query or p.fragment or not p.path.startswith('/landsat-c2/level-2/standard/oli-tirs/') or '..' in p.path or '%' in p.path or not p.path.endswith('.TIF'):
        raise GeoError('LANDSAT_ASSET_DENIED','只允许固定Landsat8/9公开COG资产，不接受任意URL或持久化签名',502)
    return value


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self,req,fp,code,msg,headers,newurl):
        raise GeoError('LANDSAT_REDIRECT_DENIED','Landsat数据请求不跟随跳转',502)


class LandsatReader:
    sensor_id='landsat-8/9-oli'
    quality_kind='landsat_qa'
    provider='USGS/NASA Landsat 8/9 Collection 2 L2 SR / Microsoft Planetary Computer COG'
    reader_version=VERSION
    quality_policy=QUALITY_POLICY
    def __init__(self,public_sources=None):
        self.public_sources=public_sources;self.tokens={};self.network_slots=threading.BoundedSemaphore(4);self.opener=build_opener(NoRedirect())
    def fetch_bytes(self,url,check,max_bytes=4*1024*1024):
        p=urlsplit(url)
        if p.scheme!='https' or p.hostname!='planetarycomputer.microsoft.com' or p.username or p.password or p.port is not None or not (p.path=='/api/stac/v1/search' or p.path=='/api/sas/v1/token/landsateuwest/landsat-c2'):
            raise GeoError('LANDSAT_SOURCE_DENIED','只允许固定Landsat STAC与公开签名服务',502)
        if not self.network_slots.acquire(blocking=False):raise GeoError('PUBLIC_SOURCE_BUSY','公开Landsat请求仍在完成，请稍后重试',503)
        channel=queue.Queue(maxsize=1)
        def fetch():
            try:
                with self.opener.open(Request(url,headers={'Accept':'application/json','User-Agent':'GeoAI-Landsat/1.0'}),timeout=12) as response:
                    data=response.read(max_bytes+1)
                    if len(data)>max_bytes:raise ValueError()
                    channel.put((True,data))
            except Exception:channel.put((False,None))
            finally:self.network_slots.release()
        threading.Thread(target=fetch,daemon=True).start();deadline=time.monotonic()+20
        while time.monotonic()<deadline:
            check()
            try:
                good,data=channel.get(timeout=.1)
                if not good:raise GeoError('LANDSAT_SOURCE_UNAVAILABLE','公开Landsat目录或访问令牌请求失败，未切换样本',502)
                return data
            except queue.Empty:pass
        raise GeoError('PUBLIC_SOURCE_TIMEOUT','公开Landsat请求等待超时，未切换样本',502)
    def fetch_json(self,url,check):
        try:
            doc=json.loads(self.fetch_bytes(url,check).decode('utf-8'),parse_constant=lambda _:(_ for _ in ()).throw(ValueError()))
            if not isinstance(doc,dict):raise ValueError()
            return doc
        except (ValueError,UnicodeError,RecursionError):raise GeoError('CATALOG_SCHEMA','公开Landsat服务返回无效JSON',502)
    def signed_url(self,url,check):
        asset_url(url);hit=self.tokens.get('landsat-c2')
        if hit and time.monotonic()-hit[0]<300:token=hit[1]
        else:
            doc=self.fetch_json('https://planetarycomputer.microsoft.com/api/sas/v1/token/landsateuwest/landsat-c2',check)
            token=doc.get('token')
            if not isinstance(token,str) or not token or len(token)>5000:raise GeoError('PUBLIC_SIGNING_FAILED','Landsat公开访问令牌不可用，未保存或回显凭据',502)
            self.tokens['landsat-c2']=(time.monotonic(),token)
        return url+'?'+token.lstrip('?')
    def discover(self,plan,check):
        scenes={};algorithm=specification(plan.get('index_id','ndvi'));needed=[b['name'] for b in algorithm['required_bands']]
        pinned=plan.get('scene_id')
        if pinned is not None and (not isinstance(pinned,str) or not re.fullmatch(r'LC0[89]_[A-Z0-9_]+',pinned)):raise GeoError('INVALID_SCENE_ID','锁定场景须为实际Landsat8/9 ID，不能是URL',400)
        if pinned and plan['goal']=='change':raise GeoError('PINNED_CHANGE_UNSUPPORTED','单景锁定不能用于两期变化；请分别明确两期来源',422)
        for epoch in (['before','after'] if plan['goal']=='change' else ['after']):
            check();start,end=plan['periods'][epoch]
            params={'collections':COLLECTION,'bbox':','.join(str(v) for v in plan['aoi']['bbox']),'datetime':start+'T00:00:00Z/'+end+'T23:59:59Z','query':json.dumps({'platform':{'in':['landsat-8','landsat-9']},'eo:cloud_cover':{'lte':plan['cloud']}},separators=(',',':')),'limit':30}
            if pinned:params['ids']=pinned
            url=STAC_ROOT+'search?'+urlencode(params)
            doc=self.fetch_json(url,check);check();candidates=[]
            if not isinstance(doc.get('features'),list):raise GeoError('CATALOG_SCHEMA','Landsat目录缺少场景列表',502)
            for scene in doc['features']:
                if not isinstance(scene,dict):continue
                props=scene.get('properties',{});cloud=props.get('eo:cloud_cover');dt=props.get('datetime','');box=scene.get('bbox',[])
                if props.get('platform') not in ('landsat-8','landsat-9') or not finite(cloud) or not 0<=cloud<=plan['cloud'] or not isinstance(dt,str) or not start<=dt[:10]<=end or not isinstance(box,list) or len(box)<4 or not all(finite(v) for v in box[:4]):continue
                aoi=plan['aoi']['bbox']
                if not (box[0]<=aoi[0] and box[1]<=aoi[1] and box[2]>=aoi[2] and box[3]>=aoi[3]):continue
                if scene.get('collection') not in (None,COLLECTION) or not isinstance(scene.get('id'),str) or not re.fullmatch(r'LC0[89]_[A-Z0-9_]+',scene['id']):continue
                if pinned and scene['id']!=pinned:continue
                candidates.append(scene)
            if not candidates:raise GeoError('NO_SUITABLE_SCENE',epoch+'窗口未找到完整覆盖、日期与云量合格的Landsat8/9场景，未换源或使用样本',422)
            scene=min(candidates,key=lambda f:(f['properties']['eo:cloud_cover'],f['properties']['datetime']));assets={};calibration={}
            for common in needed:
                key,band_id=BANDS[common];asset=scene.get('assets',{}).get(key)
                if not isinstance(asset,dict):raise GeoError('MISSING_BAND','Landsat场景缺少'+band_id,502)
                metadata=asset.get('raster:bands')
                if not isinstance(metadata,list) or len(metadata)!=1 or not isinstance(metadata[0],dict):raise GeoError('MISSING_CALIBRATION','实际Landsat资产缺少明确raster:bands定标参数，未猜测DN尺度',502)
                meta=metadata[0];scale=meta.get('scale');offset=meta.get('offset');nodata=meta.get('nodata')
                if not finite(scale) or not finite(offset) or not math.isclose(scale,.0000275,rel_tol=1e-9) or not math.isclose(offset,-.2,abs_tol=1e-12) or nodata!=0 or meta.get('spatial_resolution')!=30:
                    raise GeoError('UNSUPPORTED_LANDSAT_CALIBRATION','Landsat资产定标或分辨率不符合本版L8/9 C2 L2 SR契约，未套用S2规则',502)
                assets[common]={'href':asset_url(asset.get('href')),'scale':scale,'offset':offset,'nodata':nodata,'source_band':band_id,'asset_key':key}
                calibration[band_id]={'scale':scale,'offset':offset,'nodata':nodata,'spatial_resolution_m':30,'source':'selected STAC asset raster:bands'}
            for key in ('qa_pixel','qa_radsat'):
                asset=scene.get('assets',{}).get(key)
                if not isinstance(asset,dict):raise GeoError('MISSING_QUALITY_BAND','实际Landsat资产缺少'+key,502)
                assets[key]={'href':asset_url(asset.get('href')),'scale':1,'offset':0,'source_band':key.upper(),'quality_kind':key,'nodata':None,'raw_quality':True}
            scenes[epoch]={'id':scene['id'],'datetime':scene['properties']['datetime'],'cloud_cover':scene['properties']['eo:cloud_cover'],'platform':scene['properties']['platform'],'assets':assets,'catalog_url':url,'collection':COLLECTION,'source_id':SOURCE_ID,'calibration':{'method':'surface_reflectance=DN*scale+offset (actual selected STAC raster:bands), no clipping','per_band':calibration,'stac_item_sha256':hashlib.sha256(json.dumps(scene,sort_keys=True,ensure_ascii=False,separators=(',',':')).encode()).hexdigest(),'source_url':'https://www.usgs.gov/faqs/how-do-i-use-a-scale-factor-landsat-level-2-science-products'},'quality_policy':copy.deepcopy(QUALITY_POLICY),'provider':self.provider}
        if plan['goal']=='change' and scenes['before']['id']==scenes['after']['id']:raise GeoError('DUPLICATE_SCENE','Landsat两期不能使用同一场景',422)
        return scenes
    def read_native(self,plan,scenes,check):
        from native_raster import read_native
        actual=copy.deepcopy(plan);actual['index']=specification(plan.get('index_id','ndvi'))
        return read_native(actual,scenes,self,check)
    def read(self,plan,scenes,check):
        raise GeoError('LANDSAT_NATIVE_REQUIRED','Landsat8/9仅支持30m原生窗口分析，不提供代表网格替代',422)
