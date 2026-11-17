"""Versioned city observations on one native Landsat platform and grid.

Windows select actual representative observations, never invented daily values
or single-scene "monthly composites".  Existing published products are read-only.
"""
import calendar
import copy
import csv
import hashlib
import json
import math
import shutil
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlencode

from backend.core.geo_services import GeoError
from backend.analysis.landcover_services import load_boundary
from backend.analysis.landsat_reader import LandsatReader
from backend.agriculture.city_classification import densify_geometry
from backend.agriculture.city_spectral import (ASSETS, CityReader, MAX_CELLS, STAC_SEARCH, canonical,
                           covers_city_geometry, index_array, preview, quality_mask,
                           scene_contract, sha_file, stamp, write_json)

VERSION = 'agri-series-1.1'
LIMITATIONS = [
    '2017年公开参考仙桃市界；不是现行权威行政界、地籍或工程边界。',
    '每个窗口选取一景实际观测，明确缺测；不是月均、多景合成或连续逐日监测。',
    '同一Landsat平台、30米格网及QA规则；不混合Landsat8/9或Sentinel-2时间值。',
    'NDVI/NDMI及变化是光谱线索，不单独判定作物种类、病虫害、灾损或产量。',
    '相邻变化只统计共同有效像元；观测日期间隔不同，不能把差值直接当相同时间速率。',
    '有效覆盖率是数据支持，不是分类准确率；缺测保留未知，不补零或插值。',
    '本轮5至10月序列不覆盖油菜完整越冬季，不能据此声称完成油菜识别。',
]


def request(start='2026-05-01', end='2026-10-31', platform='landsat-8', interval='month',
            max_candidates=2, minimum_qa=.7, cloud_max=100, today=None):
    try:
        first, last = date.fromisoformat(start), date.fromisoformat(end)
        latest = (today or datetime.now(timezone.utc).date()) - timedelta(days=1)
        if first.isoformat() != start or last.isoformat() != end or first > last or first < date(2013, 4, 11) or last > latest or (last-first).days > 365: raise ValueError()
        if platform not in ('landsat-8', 'landsat-9') or interval not in ('month', 'dekad'): raise ValueError()
        if type(max_candidates) is not int or not 1 <= max_candidates <= 3: raise ValueError()
        if isinstance(minimum_qa, bool) or not isinstance(minimum_qa, (int,float)) or not math.isfinite(minimum_qa) or not 0 < minimum_qa <= 1: raise ValueError()
        if isinstance(cloud_max, bool) or not isinstance(cloud_max,(int,float)) or not math.isfinite(cloud_max) or not 0 <= cloud_max <= 100: raise ValueError()
    except (ValueError, TypeError, OverflowError):
        raise GeoError('SERIES_REQUEST_INVALID','时序须为历史日期、单一Landsat平台，最多366天；候选1至3，QA门槛(0,1]。',400)
    windows=[]; current=first
    while current <= last:
        month_end=date(current.year,current.month,calendar.monthrange(current.year,current.month)[1])
        window_end=month_end if interval=='month' or current.day>=21 else date(current.year,current.month,10 if current.day<=10 else 20)
        stop=min(last,window_end)
        label=current.strftime('%Y-%m') if interval=='month' else current.strftime('%Y-%m')+'-'+('01' if current.day<=10 else '02' if current.day<=20 else '03')
        windows.append({'label':label,'start':current.isoformat(),'end':stop.isoformat()})
        current=stop+timedelta(days=1)
    if len(windows)>24: raise GeoError('SERIES_WINDOW_LIMIT','本次最多24个时间窗，请缩短时段。',422)
    return {'version':VERSION,'start':start,'end':end,'platform':platform,'required_tier':'T1','source_id':'pc_landsat_c2_l2',
            'collection':'landsat-c2-l2','interval':interval,'windows':windows,'max_candidates':max_candidates,
            'minimum_qa':float(minimum_qa),'cloud_max':float(cloud_max),'catalog_limit':120,
            'minimum_sequence_dates':4,'max_native_cells':MAX_CELLS,'indices':['ndvi','ndmi'],
            'selection_rule':'one highest actual AOI QA candidate per window; scene-cloud then ID tie-break; bounded candidate set',
            'change_threshold':.1}


def _require(condition, code='SERIES_CACHE_CHANGED', message='冻结输入或缓存文件已变化，停止处理。'):
    if not condition: raise GeoError(code,message,409)


def _digest(value): return hashlib.sha256(canonical(value)).hexdigest()


def _read(reader,scene,band,**kwargs):
    for attempt in range(2):
        try:return reader.read(scene,band,**kwargs)
        except GeoError as error:
            if attempt or error.code not in ('PUBLIC_SOURCE_TIMEOUT','LANDSAT_SOURCE_UNAVAILABLE','PUBLIC_SIGNING_FAILED','CITY_SOURCE_READ_FAILED'):raise
            time.sleep(.5)


def discover(plan, boundary, directory, reader=None, frozen_dir=None):
    """Freeze bounded official catalogues; repeat calls verify without network."""
    from rasterio.features import bounds
    root=Path(directory);root.mkdir(parents=True,exist_ok=True); manifest_path=root/'catalog-manifest.json'
    fingerprint=_digest({'request':plan,'boundary':boundary})
    if manifest_path.is_file():
        saved=json.loads(manifest_path.read_text());_require(saved.get('request_sha256')==fingerprint)
        for entry in saved['catalogs']:
            _require(sha_file(root/entry['file'])==entry['sha256'])
            if entry.get('input_file'):_require(sha_file(root/entry['input_file'])==entry['input_sha256'])
        return saved
    reader=reader or LandsatReader(); boxes=[bounds(f['geometry']) for f in boundary['features']]
    bbox=[min(v[0] for v in boxes),min(v[1] for v in boxes),max(v[2] for v in boxes),max(v[3] for v in boxes)]
    catalogs=[]; windows=[]
    for window in plan['windows']:
        params={'collections':'landsat-c2-l2','bbox':','.join(map(str,bbox)),
                'datetime':window['start']+'T00:00:00Z/'+window['end']+'T23:59:59Z',
                'query':json.dumps({'platform':{'eq':plan['platform']},'eo:cloud_cover':{'lte':plan['cloud_max']}},separators=(',',':')),
                'limit':plan['catalog_limit'],'sortby':'datetime'}
        url=STAC_SEARCH+'?'+urlencode(params);input_record={}
        if frozen_dir:
            source_root=Path(frozen_dir);source=source_root/('catalog-'+window['label']+'.json');source_manifest=source_root/'catalog-manifest.json'
            original=json.loads(source_manifest.read_text());record=next((entry for entry in original['catalogs'] if entry['file']==source.name),None)
            _require(record and source.is_file() and sha_file(source)==record['sha256'] and original['request']['platform']==plan['platform'])
            doc=json.loads(source.read_text());input_file='input-'+source.name;shutil.copy2(source,root/input_file)
            input_record={'mode':'frozen_verified','input_file':input_file,'input_sha256':sha_file(source),'input_manifest_sha256':sha_file(source_manifest)}
            if not (root/'input-catalog-manifest.json').exists():shutil.copy2(source_manifest,root/'input-catalog-manifest.json')
        else:doc=reader.fetch_json(url,lambda:None);input_record={'mode':'live'}
        if not isinstance(doc,dict) or not isinstance(doc.get('features'),list):raise GeoError('SERIES_CATALOG_INVALID','官方目录未返回有效场景列表。',502)
        accepted=[]; rejected=[]
        for raw in doc['features'][:plan['catalog_limit']]:
            try:
                _require(isinstance(raw,dict),'SERIES_CATALOG_INVALID')
                p=raw.get('properties',{});cloud=p.get('eo:cloud_cover')
                if p.get('platform')!=plan['platform'] or isinstance(cloud,bool) or not isinstance(cloud,(int,float)) or not math.isfinite(cloud) or not 0<=cloud<=plan['cloud_max']:continue
                if not covers_city_geometry(raw.get('geometry',{}),boundary):
                    rejected.append({'id':raw.get('id'),'reason':'partial_or_unsupported_footprint'});continue
                if not str(raw.get('id','')).endswith('_T1'):
                    rejected.append({'id':raw.get('id'),'reason':'tier2_geometry_quality_excluded'});continue
                feature={'type':'Feature','id':raw['id'],'collection':raw.get('collection','landsat-c2-l2'),'geometry':raw['geometry'],
                         'properties':{k:p.get(k) for k in ('datetime','platform','eo:cloud_cover')},'assets':{}}
                for asset_name in ASSETS.values():
                    asset=raw.get('assets',{}).get(asset_name)
                    feature['assets'][asset_name]={k:v for k,v in (asset or {}).items() if k in ('href','raster:bands','proj:epsg','proj:shape','proj:transform')}
                scene=scene_contract(feature,[window['start'],window['end']]);accepted.append((scene,feature))
            except (GeoError,KeyError,TypeError):rejected.append({'id':raw.get('id') if isinstance(raw,dict) else None,'reason':'scene_contract'})
        unique={}
        for scene,feature in sorted(accepted,key=lambda pair:(pair[0]['scene_cloud_cover'],pair[0]['scene_id'])):
            unique.setdefault(scene['datetime'][:10],(scene,feature))
        selected=sorted(unique.values(),key=lambda pair:(pair[0]['scene_cloud_cover'],pair[0]['scene_id']))[:plan['max_candidates']]
        name='catalog-'+window['label']+'.json';snapshot={'type':'FeatureCollection','features':[f for _,f in accepted],'links':[]}
        write_json(root/name,snapshot);catalogs.append({'file':name,'sha256':sha_file(root/name),'request_url':url,'retrieved_at':stamp() if not frozen_dir else None,**input_record})
        windows.append({**window,'candidates':[s for s,_ in selected],'returned':len(doc['features']),'eligible':len(unique),'rejected':rejected,
                        'pagination_not_followed':any(isinstance(link,dict) and link.get('rel')=='next' for link in doc.get('links',[])),
                        'bounded_candidates':len(unique)>len(selected)})
    result={'version':VERSION,'request_sha256':fingerprint,'request':plan,'bbox':bbox,'catalogs':catalogs,'windows':windows,
            'selection_scope':'bounded fixed official search and actual-footprint candidates; unqueried pages not claimed exhaustive'}
    write_json(manifest_path,result);return result


def _grid_document(grid):
    from rasterio.transform import array_bounds
    from rasterio.warp import transform_bounds
    b=list(transform_bounds(grid['crs'],'EPSG:4326',*array_bounds(*grid['shape'],grid['transform']),densify_pts=21))
    return {'crs':str(grid['crs']),'transform':list(grid['transform'])[:6],'shape':list(grid['shape']),
            'resolution_m':30,'cell_area_m2':900,'bounds':[[b[1],b[0]],[b[3],b[2]]]}


def _grid_from_document(document,boundary):
    import numpy as np
    from affine import Affine
    from rasterio.crs import CRS
    from rasterio.features import geometry_mask
    from rasterio.warp import transform_geom
    shape=tuple(document['shape']);transform=Affine(*document['transform']);crs=CRS.from_string(document['crs'])
    _require(len(shape)==2 and all(type(v)is int and v>0 for v in shape) and math.prod(shape)<=MAX_CELLS)
    _require(crs.is_projected and crs.linear_units in ('metre','meter') and tuple(transform)[:2]==(30.,0.) and transform.d==0 and transform.e==-30.)
    geoms=[transform_geom('EPSG:4326',crs,densify_geometry(f['geometry'])) for f in boundary['features']]
    inside=geometry_mask(geoms,out_shape=shape,transform=transform,invert=True,all_touched=False)
    _require(inside.any());return {'shape':shape,'transform':transform,'crs':crs,'inside':inside,'cell_area_m2':900}


def _write_raster(path,arrays,names,grid):
    import numpy as np
    import rasterio
    from rasterio.windows import Window
    path=Path(path);temporary=path.with_suffix('.partial.tif')
    profile={'driver':'GTiff','height':grid['shape'][0],'width':grid['shape'][1],'count':len(names),'dtype':'float32','crs':grid['crs'],'transform':grid['transform'],'nodata':float('nan'),'compress':'deflate','tiled':True,'blockxsize':256,'blockysize':256}
    with rasterio.open(temporary,'w',**profile) as dst:
        for index,name in enumerate(names,1):
            dst.set_band_description(index,name)
            for row in range(0,grid['shape'][0],256):
                height=min(256,grid['shape'][0]-row);dst.write(np.asarray(arrays[name][row:row+height],dtype='float32'),index,window=Window(0,row,grid['shape'][1],height))
    temporary.replace(path)


def _archive_for_scene(scene,archive_root,grid):
    """Re-use one verified original scene, never borrow values from another date."""
    import numpy as np
    import rasterio
    root=Path(archive_root);sources=[];arrays={}
    for index in ('ndvi','ndmi'):
        folder=root/('xiantao-'+scene['datetime'][:4]+'-spectral-'+index);manifest=folder/'result.json';native=folder/'analysis.tif'
        if not manifest.is_file() or not native.is_file():return None
        result=json.loads(manifest.read_text());selected=result.get('provenance',{}).get('scenes',{}).get('after',{}).get('scene',{})
        if selected.get('scene_id')!=scene['scene_id'] or selected.get('platform')!=scene['platform']:return None
        _require(sha_file(native)==result.get('native_sha256'))
        with rasterio.open(native) as ds:
            document={'shape':list(ds.shape),'transform':list(ds.transform)[:6],'crs':ds.crs.to_string()}
            if grid is not None:_require(document['shape']==list(grid['shape']) and document['transform']==list(grid['transform'])[:6] and ds.crs==grid['crs'])
            for key in ('red','nir','swir1','qa_pixel','qa_radsat'):
                band=('after_'+key+'_reflectance') if key not in ('qa_pixel','qa_radsat') else 'after_'+key
                if band in ds.descriptions:
                    value=ds.read(ds.descriptions.index(band)+1)
                    if key in arrays:_require(np.array_equal(arrays[key],value,equal_nan=True))
                    arrays[key]=value
            sources.append({'product_id':result['id'],'native_sha256':result['native_sha256']})
    if set(arrays)!= {'red','nir','swir1','qa_pixel','qa_radsat'}:return None
    return arrays,document,sources


def _candidate_qa(scene,boundary,grid,reader,cache):
    import numpy as np
    import rasterio
    folder=Path(cache)/scene['scene_id'];folder.mkdir(parents=True,exist_ok=True);meta=folder/'qa.json';path=folder/'qa.tif'
    token=_digest({'scene':scene,'grid':_grid_document(grid) if grid else None,'boundary':boundary})
    if meta.is_file():
        saved=json.loads(meta.read_text())
        if grid is None and saved.get('grid'):grid=_grid_from_document(saved['grid'],boundary);token=_digest({'scene':scene,'grid':_grid_document(grid),'boundary':boundary})
        _require(saved['input_sha256']==token and path.is_file() and sha_file(path)==saved['sha256'])
        with rasterio.open(path) as ds:qa,rad=ds.read()
        return qa,rad,grid,saved['evidence'],saved['assessment']
    qa,own,qa_evidence=_read(reader,scene,'qa_pixel',grid=grid,boundary=boundary)
    rad,_,rad_evidence=_read(reader,scene,'qa_radsat',grid=own)
    qa=np.where(own['inside'],qa,np.nan);rad=np.where(own['inside'],rad,np.nan)
    count=int(own['inside'].sum());valid=quality_mask(qa,rad)&own['inside']
    assessment={'scene_id':scene['scene_id'],'date':scene['datetime'][:10],'qa_valid_pixel_count':int(valid.sum()),'qa_valid_ratio':float(valid.sum()/count),'city_pixel_count':count}
    evidence={'qa_pixel':qa_evidence,'qa_radsat':rad_evidence};_write_raster(path,{'qa_pixel':qa,'qa_radsat':rad},['qa_pixel','qa_radsat'],own)
    token=_digest({'scene':scene,'grid':_grid_document(own),'boundary':boundary})
    write_json(meta,{'input_sha256':token,'sha256':sha_file(path),'grid':_grid_document(own),'assessment':assessment,'evidence':evidence})
    return qa,rad,own,evidence,assessment


def reduce_support(observations,inside):
    """Block-friendly pure summary. Zero observations remain unknown, not zero."""
    import numpy as np
    if len(observations)==0:raise GeoError('SERIES_NO_OBSERVATIONS','没有实际可用观测，不能计算时序。',422)
    stack=np.asarray(observations,dtype='float32')
    _require(stack.ndim==3 and stack.shape[1:]==inside.shape,'SERIES_GRID_INVALID')
    good=np.isfinite(stack)&inside[None,:,:];count=good.sum(axis=0).astype('uint8');common=inside&good.all(axis=0)
    common_means=[float(row[common].mean(dtype='float64')) if common.any() else None for row in stack]
    return count,common,common_means


def _quality_eligible(item,minimum=.7):
    return not _quality_reasons(item,minimum)


def _quality_reasons(item,minimum=.7):
    return [{'field':key,'value':item.get(key),'minimum':minimum} for key in ('qa_valid_ratio','ndvi_valid_ratio','ndmi_valid_ratio')
            if isinstance(item.get(key),bool) or not isinstance(item.get(key),(int,float)) or not math.isfinite(item[key]) or item[key]<minimum]


def quality_comparison(root,observations,grid,minimum=.7):
    """Explicit quality subset; never overwrite the all-date support statistic."""
    import numpy as np
    import rasterio
    from rasterio.windows import Window
    root=Path(root);eligible=[i for i,v in enumerate(observations) if _quality_eligible(v,minimum)]
    sources=[rasterio.open(root/v['native_file']) for v in observations]
    common=np.zeros(grid['shape'],dtype=bool);support=np.zeros(grid['shape'],dtype='uint8');ndvi_sums=[0.]*len(observations);ndmi_sums=[0.]*len(observations)
    try:
        for row in range(0,grid['shape'][0],256):
            h=min(256,grid['shape'][0]-row);window=Window(0,row,grid['shape'][1],h);city=grid['inside'][row:row+h]
            ndvi={i:sources[i].read(1,window=window) for i in eligible};ndmi={i:sources[i].read(sources[i].descriptions.index('ndmi')+1,window=window) for i in eligible}
            good=[np.isfinite(ndvi[i])&np.isfinite(ndmi[i])&city for i in eligible]
            valid=city&np.logical_and.reduce(good) if len(eligible)>=2 else np.zeros(city.shape,dtype=bool)
            common[row:row+h]=valid
            if good:support[row:row+h]=np.asarray(good).sum(axis=0)
            for i in eligible:
                ndvi_sums[i]+=float(ndvi[i][valid].sum(dtype='float64'));ndmi_sums[i]+=float(ndmi[i][valid].sum(dtype='float64'))
        n=int(common.sum());excluded=[]
        for i,item in enumerate(observations):
            item['comparison_eligible']=i in eligible
            item['quality_common_mean_ndvi']=ndvi_sums[i]/n if i in eligible and n else None
            item['quality_common_mean_ndmi']=ndmi_sums[i]/n if i in eligible and n else None
            if i not in eligible:
                excluded.append({'date':item['date'],'reasons':_quality_reasons(item,minimum)})
        native=root/'quality-comparison-support.tif';arrays={'common_valid_mask':np.where(grid['inside'],common,np.nan),'city_mask':grid['inside'],'eligible_valid_observation_count':np.where(grid['inside'],support,np.nan)}
        _write_raster(native,arrays,list(arrays),grid)
        result={'status':'ready' if len(eligible)>=2 and n else 'insufficient_common_support','rule':{'qa_valid_ratio_minimum':minimum,'ndvi_valid_ratio_minimum':minimum,'ndmi_valid_ratio_minimum':minimum,'same_spatial_mask_for_both_indices':True,'minimum_eligible_dates':2},'dates':[observations[i]['date'] for i in eligible],'source_sha256s':[{'date':item['date'],'sha256':item['sha256']} for item in observations],'common_support_pixels':n,'common_support_ratio':float(n/grid['inside'].sum()),'common_area_ha_30m':n*.09,'excluded_dates_with_reasons':excluded,'native_file':native.name,'sha256':sha_file(native),'interpretation':'仅比较质量合格日期在同一共同有效空间范围的NDVI/NDMI均值；不补齐缺月，不插值，不改变全序列共同支持。'}
        write_json(root/'quality-comparison.json',{**result,'observations':[{key:item.get(key) for key in ('date','comparison_eligible','quality_common_mean_ndvi','quality_common_mean_ndmi')} for item in observations]})
        with (root/'quality-comparison.csv').open('w',encoding='utf-8-sig',newline='') as stream:
            fields=['date','comparison_eligible','quality_common_mean_ndvi','quality_common_mean_ndmi','qa_valid_ratio','ndvi_valid_ratio','ndmi_valid_ratio'];writer=csv.DictWriter(stream,fields);writer.writeheader();writer.writerows({key:item.get(key) for key in fields} for item in observations)
        return result
    finally:
        for source in sources:source.close()


def _artifact_manifest(root):
    return [{'file':str(p.relative_to(root)),'sha256':sha_file(p),'bytes':p.stat().st_size} for p in sorted(root.rglob('*')) if p.is_file() and 'cache' not in p.parts and p.name!='result.json']


def _add_quality_changes(root,result,grid,plan):
    """Pair actual qualified dates; original adjacent native products stay intact."""
    import numpy as np
    import rasterio
    originals=[change for change in result['changes'] if not change.get('comparison_eligible')]
    for change in originals:
        change['comparison_eligible']=False
        change['support_status']='supported' if change['common_valid_pixel_count'] else 'no_support'
        if not change['common_valid_pixel_count']:
            change['positive_condition_area_ha']=None;change['negative_condition_area_ha']=None
    qualified=[item for item in result['observations'] if item['comparison_eligible']];derived=[]
    for before,after in zip(qualified,qualified[1:]):
        with rasterio.open(root/before['native_file']) as first,rasterio.open(root/after['native_file']) as last:
            a=first.read(1);b=last.read(1)
        valid=grid['inside']&np.isfinite(a)&np.isfinite(b);delta=np.where(valid,b-a,np.nan).astype('float32');n=int(valid.sum())
        folder=root/'quality-changes'/(before['date']+'_'+after['date']);folder.mkdir(parents=True,exist_ok=True)
        native=folder/'analysis.tif';_write_raster(native,{'value':delta,'valid_mask':np.where(grid['inside'],valid,np.nan),'city_mask':grid['inside']},['value','valid_mask','city_mask'],grid)
        image=preview(delta,valid,grid,'change',folder);threshold=plan['change_threshold']
        derived.append({'id':'quality-change-'+before['date'].replace('-','')+'-'+after['date'].replace('-',''),'comparison_eligible':True,'support_status':'supported' if n else 'no_support','support_rule':'pairwise common valid NDVI within the same reference city mask','before_date':before['date'],'after_date':after['date'],'actual_dates':[before['datetime'],after['datetime']],'operation':'after_minus_before','before_sha256':before['sha256'],'after_sha256':after['sha256'],'day_gap':(date.fromisoformat(after['date'])-date.fromisoformat(before['date'])).days,'common_valid_ratio':float(n/grid['inside'].sum()),'common_valid_pixel_count':n,'mean_delta_ndvi':float(delta[valid].mean(dtype='float64')) if n else None,'threshold':threshold,'positive_condition_area_ha':int((valid&(delta>=threshold)).sum())*.09 if n else None,'negative_condition_area_ha':int((valid&(delta<=-threshold)).sum())*.09 if n else None,'native_file':str(native.relative_to(root)),'overlay_file':str((folder/'overlay.png').relative_to(root)),'image':image,'sha256':sha_file(native),'interpretation':'质量合格日期实际配对NDVI后减前，仅在两日期共同有效像元比较；保留真实跨日间隔，非月均、非插值，条件面积不是灾损或作物认定。'})
    result['changes']=originals+derived


def add_quality_comparison(root):
    """Add the explicit derived subset to a verified frozen series, offline.

    Source observations, their metadata, full-sequence support and all original
    differences are immutable. Only new derived artifacts and result.json change.
    """
    root=Path(root);result=json.loads((root/'result.json').read_text());validate(root,result)
    original={item['file']:item['sha256'] for item in result['artifacts'] if item['file'] not in ('quality-comparison-support.tif','quality-comparison.json','quality-comparison.csv') and not item['file'].startswith('quality-changes/')}
    boundary=json.loads((root/'boundary.geojson').read_text());grid=_grid_from_document(result['grid'],boundary)
    plan=json.loads((root/'request.json').read_text())['request']
    result['quality_comparison']=quality_comparison(root,result['observations'],grid,plan['minimum_qa'])
    _add_quality_changes(root,result,grid,plan)
    for path,digest in original.items():_require(sha_file(root/path)==digest)
    result['artifacts']=_artifact_manifest(root);result['validation']=validate(root,result)
    write_json(root/'result.json',result)
    return result


def produce(output, plan=None, archive_root='data/agriculture-atlas', source_dir=None,
            catalog_reader=None, city_reader=None, progress=print, frozen_catalog_dir=None, reuse_from=None):
    import numpy as np
    import rasterio
    root=Path(output);root.mkdir(parents=True,exist_ok=True);plan=plan or request();boundary,metadata=load_boundary(source_dir)
    token=_digest({'request':plan,'boundary':boundary});request_path=root/'request.json'
    if request_path.is_file():_require(json.loads(request_path.read_text())['request_sha256']==token)
    else:write_json(request_path,{'request':plan,'request_sha256':token,'boundary_source_sha256':metadata['sha256']})
    if (root/'result.json').is_file():
        result=json.loads((root/'result.json').read_text());validate(root,result);return result
    write_json(root/'boundary.geojson',boundary)
    discovery=discover(plan,boundary,root/'catalogs',catalog_reader,frozen_catalog_dir)
    reader=city_reader or CityReader();grid=None;grid_path=root/'grid.json'
    if grid_path.is_file():grid=_grid_from_document(json.loads(grid_path.read_text()),boundary)
    # A verified archived scene anchors the grid before any new source read.
    if grid is None and archive_root:
        for window in discovery['windows']:
            for scene in window['candidates']:
                cached=_archive_for_scene(scene,archive_root,None)
                if cached:
                    grid=_grid_from_document(cached[1],boundary);break
            if grid is not None:break
    if grid is not None and not grid_path.is_file():write_json(grid_path,_grid_document(grid))
    if reuse_from:
        reuse_root=Path(reuse_from);prior=json.loads((reuse_root/'result.json').read_text())
        _require(prior['platform']==plan['platform'] and prior['scope']['boundary_source_sha256']==metadata['sha256'])
        if grid is None:grid=_grid_from_document(prior['grid'],boundary);write_json(grid_path,_grid_document(grid))
        _require(prior['grid']==_grid_document(grid))
        reuse_manifest_sha=sha_file(reuse_root/'result.json')
        for window in discovery['windows']:
            for scene in window['candidates']:
                for category in ('cache','observations'):
                    source=reuse_root/category/scene['scene_id'];destination=root/category/scene['scene_id']
                    if source.is_dir() and not destination.exists():shutil.copytree(source,destination)
                saved=root/'observations'/scene['scene_id']/'observation.json'
                if saved.is_file():
                    item=json.loads(saved.read_text());native=saved.parent/'analysis.tif';_require(item['scene']==scene and sha_file(native)==item['sha256'])
                    item['input_sha256']=_digest({'scene':scene,'grid':_grid_document(grid),'version':VERSION})
                    item.setdefault('evidence',{})['reused_series']={'source_result_sha256':reuse_manifest_sha,'source_observation_sha256':sha_file(reuse_root/'observations'/scene['scene_id']/'observation.json'),'native_sha256':item['sha256'],'pixels_recomputed_from_original_reflectance_during_validation':True}
                    write_json(saved,item)
    observations=[];window_status=[]
    for window in discovery['windows']:
        candidates=[];failures=[]
        for scene in window['candidates']:
            progress('QA '+scene['scene_id']);reused=_archive_for_scene(scene,archive_root,grid) if archive_root else None
            try:
                if reused:
                    arrays,document,sources=reused;own=grid or _grid_from_document(document,boundary);qa=arrays['qa_pixel'];rad=arrays['qa_radsat'];mask=quality_mask(qa,rad)&own['inside']
                    assessment={'scene_id':scene['scene_id'],'date':scene['datetime'][:10],'qa_valid_pixel_count':int(mask.sum()),'qa_valid_ratio':float(mask.sum()/own['inside'].sum()),'city_pixel_count':int(own['inside'].sum())};evidence={'reused_archives':sources}
                else:qa,rad,own,evidence,assessment=_candidate_qa(scene,boundary,grid,reader,root/'cache')
                if grid is None:grid=own;write_json(grid_path,_grid_document(grid))
                candidates.append({'scene':scene,'qa':qa,'rad':rad,'assessment':assessment,'evidence':evidence,'reused':reused})
            except GeoError as error:failures.append({'scene_id':scene['scene_id'],'code':error.code})
        if not candidates:
            reason='仅有Tier2几何质量候选，未用于本次同源比较' if any(v.get('reason')=='tier2_geometry_quality_excluded' for v in window['rejected']) else '没有通过完整覆盖、平台、几何质量及实际读取条件的观测'
            window_status.append({**{k:window[k] for k in ('label','start','end')},'status':'missing','reason':reason,'read_failures':failures,'assessments':[],'rejected':window['rejected']});continue
        selected=min(candidates,key=lambda v:(-v['assessment']['qa_valid_ratio'],v['scene']['scene_cloud_cover'],v['scene']['scene_id']))
        scene=selected['scene'];folder=root/'observations'/scene['scene_id'];folder.mkdir(parents=True,exist_ok=True);native=folder/'analysis.tif';saved=folder/'observation.json'
        observation_token=_digest({'scene':scene,'grid':_grid_document(grid),'version':VERSION})
        if saved.is_file():
            item=json.loads(saved.read_text());_require(item['input_sha256']==observation_token and native.is_file() and sha_file(native)==item['sha256']);observations.append(item)
        else:
            arrays=selected['reused'][0] if selected['reused'] else {'qa_pixel':selected['qa'],'qa_radsat':selected['rad']}
            evidence=copy.deepcopy(selected['evidence'])
            for band in ('red','nir','swir1'):
                if band not in arrays:
                    progress('Read '+scene['scene_id']+' '+band);arrays[band],_,evidence[band]=_read(reader,scene,band,grid=grid)
            ndvi,nvalid=index_array('ndvi',arrays,arrays['qa_pixel'],arrays['qa_radsat'],grid['inside'])
            ndmi,mvalid=index_array('ndmi',arrays,arrays['qa_pixel'],arrays['qa_radsat'],grid['inside'])
            export={'value':ndvi,'valid_mask':np.where(grid['inside'],nvalid,np.nan),'city_mask':grid['inside'].astype('uint8'),'ndmi':ndmi,'ndmi_valid':np.where(grid['inside'],mvalid,np.nan)}
            export.update({key+'_reflectance' if key in ('red','nir','swir1') else key:np.where(grid['inside'],arrays[key],np.nan) for key in ('red','nir','swir1','qa_pixel','qa_radsat')})
            _write_raster(native,export,list(export),grid);image=preview(ndvi,nvalid,grid,'ndvi',folder)
            item={'date':scene['datetime'][:10],'datetime':scene['datetime'],'scene_id':scene['scene_id'],'platform':scene['platform'],'window':{k:window[k] for k in ('label','start','end')},'input_sha256':observation_token,'sha256':sha_file(native),'native_file':str(native.relative_to(root)),'overlay_file':str((folder/'overlay.png').relative_to(root)),'image':image,'ndvi_mean':float(ndvi[nvalid].mean(dtype='float64')) if nvalid.any() else None,'ndmi_mean':float(ndmi[mvalid].mean(dtype='float64')) if mvalid.any() else None,'ndvi_valid_pixel_count':int(nvalid.sum()),'ndmi_valid_pixel_count':int(mvalid.sum()),'ndvi_valid_ratio':float(nvalid.sum()/grid['inside'].sum()),'ndmi_valid_ratio':float(mvalid.sum()/grid['inside'].sum()),'valid_ratio':float((nvalid&mvalid).sum()/grid['inside'].sum()),'qa_valid_ratio':selected['assessment']['qa_valid_ratio'],'quality_status':'passed' if selected['assessment']['qa_valid_ratio']>=plan['minimum_qa'] else 'below_target','evidence':evidence,'scene':scene}
            write_json(saved,item);observations.append(item)
        window_status.append({**{k:window[k] for k in ('label','start','end')},'status':'observed','selected_scene_id':scene['scene_id'],'read_failures':failures,'assessments':[v['assessment'] for v in candidates]})
        del candidates
    if grid is None or not observations:raise GeoError('SERIES_NO_OBSERVATIONS','所有窗口没有可读取的真实全市观测，未生成替代结果。',422)
    observations.sort(key=lambda v:v['date']);_require(len({v['date'] for v in observations})==len(observations),'SERIES_DUPLICATE_DATE')
    series_summary,changes=_summarize(root,observations,grid,plan,progress)
    for item,common_mean in zip(observations,series_summary.pop('common_means')):item['common_mean']=common_mean
    comparison=quality_comparison(root,observations,grid,plan['minimum_qa'])
    with (root/'series.csv').open('w',encoding='utf-8-sig',newline='') as stream:
        fields=['date','scene_id','ndvi_mean','ndmi_mean','valid_ratio','qa_valid_ratio','common_mean','quality_status'];writer=csv.DictWriter(stream,fields);writer.writeheader();writer.writerows({k:v.get(k) for k in fields} for v in observations)
    status='ready' if sum(v['ndvi_valid_pixel_count']>0 for v in observations)>=plan['minimum_sequence_dates'] else 'insufficient_dates'
    result={'schema_version':VERSION,'id':'xiantao-'+plan['platform'].replace('landsat-','l')+'-tier1-'+plan['start'].replace('-','')+'-'+plan['end'].replace('-',''),'status':status,'title':'仙桃市Landsat '+plan['platform'][-1]+' Tier1月窗口光谱序列','platform':plan['platform'],'source_id':plan['source_id'],'period':{'start':plan['start'],'end':plan['end']},'scope':{'region':'仙桃市','boundary_year':metadata.get('boundary_year_represented'),'boundary_source_sha256':metadata['sha256']},'grid':_grid_document(grid),'bounds':_grid_document(grid)['bounds'],'observations':observations,'window_status':window_status,'sequence_summary':{**series_summary,'observed_dates':len(observations),'usable_dates':sum(v['quality_status']=='passed' for v in observations),'requested_windows':len(plan['windows']),'missing_windows':[v['label'] for v in window_status if v['status']=='missing']},'changes':changes,'limitations':LIMITATIONS,'provenance':{'created_at':stamp(),'request_sha256':token,'algorithm_version':VERSION,'catalog_manifest_sha256':sha_file(root/'catalogs/catalog-manifest.json'),'selection_rule':plan['selection_rule'],'single_platform':True,'required_tier':'T1','composite':False,'native_grid_statistics':True,'row_block_size':256}}
    result['quality_comparison']=comparison
    _add_quality_changes(root,result,grid,plan)
    result['artifacts']=_artifact_manifest(root)
    result['validation']=validate(root,result);write_json(root/'result.json',result);return result


def _summarize(root,observations,grid,plan,progress):
    import numpy as np
    import rasterio
    from rasterio.windows import Window
    rows=[rasterio.open(root/item['native_file']) for item in observations];common_sum=[0.]*len(rows);common_count=0;support=np.zeros(grid['shape'],dtype='uint8');inside=grid['inside'];changes=[]
    try:
        for row in range(0,grid['shape'][0],256):
            h=min(256,grid['shape'][0]-row);window=Window(0,row,grid['shape'][1],h);stack=np.array([src.read(1,window=window) for src in rows]);count,common,means=reduce_support(stack,inside[row:row+h]);support[row:row+h]=count;n=int(common.sum());common_count+=n
            for i in range(len(rows)):
                if n:common_sum[i]+=float(stack[i][common].sum(dtype='float64'))
        common_means=[v/common_count if common_count else None for v in common_sum]
        support_values={'valid_observation_count':np.where(inside,support,np.nan),'city_mask':inside.astype('float32')};_write_raster(root/'observation-support.tif',support_values,list(support_values),grid)
        for i in range(1,len(rows)):
            progress('Change '+observations[i-1]['date']+' '+observations[i]['date']);first=rows[i-1].read(1);last=rows[i].read(1);valid=inside&np.isfinite(first)&np.isfinite(last);delta=np.where(valid,last-first,np.nan).astype('float32');folder=root/'changes'/(observations[i-1]['date']+'_'+observations[i]['date']);folder.mkdir(parents=True,exist_ok=True)
            _write_raster(folder/'analysis.tif',{'value':delta,'valid_mask':np.where(inside,valid,np.nan),'city_mask':inside},['value','valid_mask','city_mask'],grid);image=preview(delta,valid,grid,'change',folder);threshold=plan['change_threshold']
            changes.append({'before_date':observations[i-1]['date'],'after_date':observations[i]['date'],'actual_dates':[observations[i-1]['datetime'],observations[i]['datetime']],'operation':'after_minus_before','before_sha256':observations[i-1]['sha256'],'after_sha256':observations[i]['sha256'],'day_gap':(date.fromisoformat(observations[i]['date'])-date.fromisoformat(observations[i-1]['date'])).days,'common_valid_ratio':float(valid.sum()/inside.sum()),'common_valid_pixel_count':int(valid.sum()),'mean_delta_ndvi':float(delta[valid].mean(dtype='float64')) if valid.any() else None,'threshold':threshold,'positive_condition_area_ha':int((valid&(delta>=threshold)).sum())*.09,'negative_condition_area_ha':int((valid&(delta<=-threshold)).sum())*.09,'native_file':str((folder/'analysis.tif').relative_to(root)),'overlay_file':str((folder/'overlay.png').relative_to(root)),'image':image,'sha256':sha_file(folder/'analysis.tif'),'interpretation':'同一平台相邻实际日期NDVI后减前；条件面积是筛查线索，不是作物变化或灾损认定。'})
        return {'common_support_pixels':common_count,'common_support_ratio':float(common_count/inside.sum()),'common_means':common_means,'mean_delta_ndvi_first_last':common_means[-1]-common_means[0] if common_count else None,'pixels_with_at_least_4_observations':int((inside&(support>=4)).sum()),'city_pixel_count':int(inside.sum()),'support_histogram':{str(v):int((inside&(support==v)).sum()) for v in range(len(observations)+1)}},changes
    finally:
        for src in rows:src.close()


def validate(root,result=None):
    """Independent block recomputation of exported formulas, masks and means."""
    import numpy as np
    import rasterio
    from rasterio.features import geometry_mask
    from rasterio.warp import transform_geom
    from rasterio.windows import Window
    root=Path(root);result=result or json.loads((root/'result.json').read_text());checked=0;maximum=0.;dates=[v['date'] for v in result['observations']]
    _require(len(set(dates))==len(dates) and dates==sorted(dates),'SERIES_VALIDATION_FAILED','实际观测日期不独立或无序。')
    _require(all(v['platform']==result['platform'] and v['scene_id'].endswith('_T1') and v['window']['start']<=v['date']<=v['window']['end'] for v in result['observations']),'SERIES_VALIDATION_FAILED','观测平台、Tier1几何质量或实际日期不符合冻结窗口。')
    boundary=json.loads((root/'boundary.geojson').read_text());expected_grid=_grid_from_document(result['grid'],boundary);expected_city=expected_grid['inside'];independent_ratios=[]
    for item in result['observations']:
        path=root/item['native_file'];_require(path.is_file() and sha_file(path)==item['sha256'])
        total=0;good_counts=[0,0];sums=[0.,0.];qa_count=0;combined_count=0
        with rasterio.open(path) as src:
            names={name:i+1 for i,name in enumerate(src.descriptions)}
            _require(src.res==(30.,30.) and src.crs==expected_grid['crs'] and src.shape==tuple(result['grid']['shape']) and list(src.transform)[:6]==result['grid']['transform'])
            for row in range(0,src.height,256):
                h=min(256,src.height-row);data=src.read(window=Window(0,row,src.width,h));get=lambda name:data[names[name]-1];city=get('city_mask')==1
                _require(np.array_equal(city,expected_city[row:row+h]),'SERIES_VALIDATION_FAILED','市界mask与归档参考多边形独立重算不一致。')
                total+=int(city.sum());qa=get('qa_pixel');rad=get('qa_radsat')
                finite=np.isfinite(qa)&np.isfinite(rad)&(qa>=0)&(qa<=65535)&(qa==np.floor(qa))&(rad==0)
                clean=finite&((np.nan_to_num(qa,nan=65535).astype('uint16')&63)==0)
                qa_count+=int((city&clean).sum());masks=[]
                nir=get('nir_reflectance');pairs=[(get('red_reflectance'),'value','valid_mask'),(get('swir1_reflectance'),'ndmi','ndmi_valid')]
                for i,(other,value_name,valid_name) in enumerate(pairs):
                    valid=city&clean&np.isfinite(nir)&np.isfinite(other)&(nir>=0)&(other>=0)&((nir+other)>0);expected=np.full(nir.shape,np.nan,dtype='float32');np.divide(nir-other,nir+other,out=expected,where=valid);observed=get(value_name)
                    _require(np.array_equal(np.isfinite(observed),valid) and np.array_equal(get(valid_name)[city],valid[city]),'SERIES_VALIDATION_FAILED','导出有效掩膜与独立QA重算不一致。')
                    error=float(np.max(np.abs(observed[valid]-expected[valid]))) if valid.any() else 0.;maximum=max(maximum,error);_require(error<=1e-7,'SERIES_VALIDATION_FAILED','导出指数与独立公式重算不一致。')
                    good_counts[i]+=int(valid.sum());sums[i]+=float(observed[valid].sum(dtype='float64'))
                    masks.append(valid)
                combined_count+=int((masks[0]&masks[1]).sum())
                checked+=int(city.sum())
        for i,name in enumerate(('ndvi','ndmi')):
            _require(good_counts[i]==item[name+'_valid_pixel_count'] and (abs(sums[i]/good_counts[i]-item[name+'_mean'])<1e-10 if good_counts[i] else item[name+'_mean'] is None),'SERIES_VALIDATION_FAILED','序列均值或像元数与实际栅格不一致。')
            _require(abs(good_counts[i]/total-item[name+'_valid_ratio'])<1e-12,'SERIES_VALIDATION_FAILED','公式有效率与像元计数不一致。')
        _require(abs(qa_count/total-item['qa_valid_ratio'])<1e-12 and abs(combined_count/total-item['valid_ratio'])<1e-12,'SERIES_VALIDATION_FAILED','QA或双指数共同有效率不一致。')
        independent_ratios.append({'qa_valid_ratio':qa_count/total,'ndvi_valid_ratio':good_counts[0]/total,'ndmi_valid_ratio':good_counts[1]/total})
    sources=[rasterio.open(root/v['native_file']) for v in result['observations']];hist={str(v):0 for v in range(len(sources)+1)};common_count=0;common_sums=[0.]*len(sources)
    try:
        with rasterio.open(root/'observation-support.tif') as support:
            for row in range(0,expected_city.shape[0],256):
                h=min(256,expected_city.shape[0]-row);window=Window(0,row,expected_city.shape[1],h);city=expected_city[row:row+h]
                stack=np.asarray([src.read(1,window=window) for src in sources]);good=np.isfinite(stack)&city[None,:,:];counts=good.sum(axis=0)
                _require(np.array_equal(support.read(1,window=window)[city],counts[city]),'SERIES_VALIDATION_FAILED','逐像元独立有效日期数量不一致。')
                common=city&good.all(axis=0);common_count+=int(common.sum())
                for count in range(len(sources)+1):hist[str(count)]+=int((city&(counts==count)).sum())
                for i,values in enumerate(stack):common_sums[i]+=float(values[common].sum(dtype='float64'))
        summary=result['sequence_summary'];_require(hist==summary['support_histogram'] and common_count==summary['common_support_pixels'],'SERIES_VALIDATION_FAILED','时序支持直方图或共同有效范围不一致。')
        for i,item in enumerate(result['observations']):
            _require((abs(common_sums[i]/common_count-item['common_mean'])<1e-10 if common_count else item['common_mean'] is None),'SERIES_VALIDATION_FAILED','共同比较范围均值不一致。')
        comparison=result.get('quality_comparison')
        if comparison is not None:
            minimum=json.loads((root/'request.json').read_text())['request']['minimum_qa']
            rule={'qa_valid_ratio_minimum':minimum,'ndvi_valid_ratio_minimum':minimum,'ndmi_valid_ratio_minimum':minimum,'same_spatial_mask_for_both_indices':True,'minimum_eligible_dates':2}
            eligible=[i for i,ratios in enumerate(independent_ratios) if all(ratios[key]>=minimum for key in ratios)]
            expected_dates=[result['observations'][i]['date'] for i in eligible]
            _require(comparison['rule']==rule and comparison['dates']==expected_dates,'SERIES_VALIDATION_FAILED','质量比较子集与冻结门槛及实际公式有效率不一致。')
            _require(comparison['source_sha256s']==[{'date':item['date'],'sha256':item['sha256']} for item in result['observations']],'SERIES_VALIDATION_FAILED','质量比较子集源像元SHA不一致。')
            rejected=[{'date':item['date'],'reasons':[{'field':key,'value':independent_ratios[i][key],'minimum':minimum} for key in ('qa_valid_ratio','ndvi_valid_ratio','ndmi_valid_ratio') if independent_ratios[i][key]<minimum]} for i,item in enumerate(result['observations']) if i not in eligible]
            _require(comparison['excluded_dates_with_reasons']==rejected,'SERIES_VALIDATION_FAILED','不合格日期及排除理由不一致。')
            quality_path=root/comparison['native_file'];_require(quality_path.is_file() and sha_file(quality_path)==comparison['sha256'])
            quality_count=0;quality_sums=[[0.,0.] for _ in sources]
            with rasterio.open(quality_path) as delivered:
                _require(delivered.descriptions==('common_valid_mask','city_mask','eligible_valid_observation_count') and delivered.shape==expected_city.shape and list(delivered.transform)[:6]==result['grid']['transform'],'SERIES_VALIDATION_FAILED','质量共同范围栅格结构不一致。')
                for row in range(0,expected_city.shape[0],256):
                    h=min(256,expected_city.shape[0]-row);window=Window(0,row,expected_city.shape[1],h);city=expected_city[row:row+h]
                    blocks={i:(sources[i].read(1,window=window),sources[i].read(sources[i].descriptions.index('ndmi')+1,window=window)) for i in eligible}
                    good=[city&np.isfinite(blocks[i][0])&np.isfinite(blocks[i][1]) for i in eligible]
                    valid=city&np.logical_and.reduce(good) if len(good)>=2 else np.zeros(city.shape,dtype=bool)
                    support=np.asarray(good).sum(axis=0) if good else np.zeros(city.shape,dtype='uint8')
                    mask=delivered.read(1,window=window);counts=delivered.read(3,window=window)
                    _require(np.array_equal(delivered.read(2,window=window)==1,city) and np.array_equal(mask[city],valid[city]) and np.array_equal(counts[city],support[city]) and np.isnan(mask[~city]).all() and np.isnan(counts[~city]).all(),'SERIES_VALIDATION_FAILED','质量合格子集共同mask或支持计数不一致。')
                    quality_count+=int(valid.sum())
                    for i,(ndvi,ndmi) in blocks.items():
                        quality_sums[i][0]+=float(ndvi[valid].sum(dtype='float64'));quality_sums[i][1]+=float(ndmi[valid].sum(dtype='float64'))
            _require(quality_count==comparison['common_support_pixels'] and abs(quality_count/expected_city.sum()-comparison['common_support_ratio'])<1e-12 and abs(quality_count*.09-comparison['common_area_ha_30m'])<1e-8,'SERIES_VALIDATION_FAILED','质量合格子集共同面积不一致。')
            _require(comparison['status']==('ready' if len(eligible)>=2 and quality_count else 'insufficient_common_support'),'SERIES_VALIDATION_FAILED','质量合格子集状态不一致。')
            for i,item in enumerate(result['observations']):
                _require(item['comparison_eligible']==(i in eligible),'SERIES_VALIDATION_FAILED','质量比较准入标识不一致。')
                for j,index in enumerate(('ndvi','ndmi')):
                    value=item['quality_common_mean_'+index]
                    _require(abs(value-quality_sums[i][j]/quality_count)<1e-10 if i in eligible and quality_count else value is None,'SERIES_VALIDATION_FAILED','质量合格子集在固定共同范围均值不一致。')
            exported=json.loads((root/'quality-comparison.json').read_text())
            expected_export={**comparison,'observations':[{key:item.get(key) for key in ('date','comparison_eligible','quality_common_mean_ndvi','quality_common_mean_ndmi')} for item in result['observations']]}
            _require(exported==expected_export,'SERIES_VALIDATION_FAILED','质量比较JSON与独立检查后的结果不一致。')
            with (root/'quality-comparison.csv').open(encoding='utf-8-sig',newline='') as stream:
                rows=list(csv.DictReader(stream));fields=['date','comparison_eligible','quality_common_mean_ndvi','quality_common_mean_ndmi','qa_valid_ratio','ndvi_valid_ratio','ndmi_valid_ratio']
            expected_rows=[{key:str(item[key]) if item.get(key) is not None else '' for key in fields} for item in result['observations']]
            _require(rows==expected_rows,'SERIES_VALIDATION_FAILED','质量比较CSV与独立检查后的结果不一致。')
        by_date={v['date']:(v,sources[i]) for i,v in enumerate(result['observations'])}
        for change in result.get('changes',[]):
            before,src_before=by_date[change['before_date']];after,src_after=by_date[change['after_date']]
            _require(change['before_sha256']==before['sha256'] and change['after_sha256']==after['sha256'] and sha_file(root/change['native_file'])==change['sha256'])
            _require(change['operation']=='after_minus_before' and change['actual_dates']==[before['datetime'],after['datetime']] and change['day_gap']==(date.fromisoformat(after['date'])-date.fromisoformat(before['date'])).days,'SERIES_VALIDATION_FAILED','实际跨日期差值的日期或运算定义不一致。')
            n=0;sum_delta=0.;positive=0;negative=0
            with rasterio.open(root/change['native_file']) as dst:
                for row in range(0,expected_city.shape[0],256):
                    h=min(256,expected_city.shape[0]-row);window=Window(0,row,expected_city.shape[1],h);a=src_before.read(1,window=window);b=src_after.read(1,window=window);city=expected_city[row:row+h];valid=city&np.isfinite(a)&np.isfinite(b);delivered=dst.read(1,window=window);expected=np.where(valid,b-a,np.nan).astype('float32')
                    _require(np.array_equal(np.isfinite(delivered),valid) and np.allclose(delivered[valid],expected[valid],atol=1e-7,rtol=0),'SERIES_VALIDATION_FAILED','相邻变化非真实共同范围后减前。')
                    _require(np.array_equal(dst.read(2,window=window)[city],valid[city]),'SERIES_VALIDATION_FAILED','相邻变化共同有效mask不一致。')
                    n+=int(valid.sum());sum_delta+=float(delivered[valid].sum(dtype='float64'));positive+=int((valid&(delivered>=change['threshold'])).sum());negative+=int((valid&(delivered<=-change['threshold'])).sum())
            areas_match=abs(positive*.09-change['positive_condition_area_ha'])<1e-8 and abs(negative*.09-change['negative_condition_area_ha'])<1e-8 if n else change['positive_condition_area_ha'] is None and change['negative_condition_area_ha'] is None
            # Older frozen series used numerical zero for unsupported condition
            # areas. The explicitly augmented format records unknown as null.
            if not n and not comparison:areas_match=change['positive_condition_area_ha'] in (None,0) and change['negative_condition_area_ha'] in (None,0)
            _require(n==change['common_valid_pixel_count'] and areas_match,'SERIES_VALIDATION_FAILED','相邻变化像元数或阈值条件面积不一致。')
            _require(abs(n/expected_city.sum()-change['common_valid_ratio'])<1e-12,'SERIES_VALIDATION_FAILED','实际跨日期差值共同有效率不一致。')
            _require(abs(sum_delta/n-change['mean_delta_ndvi'])<1e-10 if n else change['mean_delta_ndvi'] is None,'SERIES_VALIDATION_FAILED','相邻变化均值不一致。')
            if comparison:
                _require(change['support_status']==('supported' if n else 'no_support'),'SERIES_VALIDATION_FAILED','差值支持状态与共同有效像元不一致。')
        if comparison:
            expected_pairs=list(zip(comparison['dates'],comparison['dates'][1:]));actual_pairs=[(v['before_date'],v['after_date']) for v in result['changes'] if v.get('comparison_eligible')]
            _require(actual_pairs==expected_pairs,'SERIES_VALIDATION_FAILED','质量合格日期实际配对不完整或顺序不一致。')
    finally:
        for src in sources:src.close()
    for entry in result.get('artifacts',[]):
        path=root/entry['file'];_require(path.is_file() and sha_file(path)==entry['sha256'])
    return {'status':'passed','native_city_pixels_checked':checked,'max_formula_absolute_error':maximum,'method':'independent reopened native QA, reflectance ratio, polygon masks, support counts/common means, quality-subset common NDVI/NDMI and adjacent change/threshold areas','network_access':False,'classification_accuracy_validated':False,'changes_checked':len(result.get('changes',[])),'quality_comparison_checked':comparison is not None}
