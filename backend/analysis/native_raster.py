"""Bounded calibrated optical analysis on the selected native reference grid.

Only previews are downsampled. Before/SCL are nearest-neighbour aligned to the
reference grid; statistics use every AOI pixel centre, never the preview.
"""
import copy
import hashlib
import json
import math
import time
from pathlib import Path
from backend.core.geo_services import GeoError
from backend.analysis.spectral_indices import specification, evaluate_index, FORMULAS

VERSION='3.2-native'
MAX_CELLS=1048576


def read_native(plan,scenes,reader,check):
    import numpy as np
    import rasterio
    from rasterio.features import geometry_mask
    from rasterio.windows import from_bounds,Window
    from rasterio.warp import transform_bounds,transform_geom,reproject,Resampling
    from backend.analysis.raster_cache import WINDOW_CACHE
    algorithm=plan.get('index') or specification(plan.get('index_id','ndvi'));reference_key=algorithm['reference_key'];reference_label='after-'+algorithm['reference_band']+' native grid'
    box=plan['aoi']['bbox'];reference=scenes['after']['assets'][reference_key];started=time.monotonic();cache_hits=0;source_bytes=0;hit_bytes=0;window_count=0;cache_observations={}
    env=dict(GDAL_DISABLE_READDIR_ON_OPEN='EMPTY_DIR',CPL_VSIL_CURL_ALLOWED_EXTENSIONS='.tif,.tiff',GDAL_HTTP_TIMEOUT='15',GDAL_HTTP_MAX_RETRY='0',GDAL_CACHEMAX=32*1024*1024)
    check()
    try:
        with rasterio.Env(**env),rasterio.open(reader.signed_url(reference['href'],check)) as ds:
            if not ds.crs or not ds.crs.is_projected or ds.crs.linear_units not in ('metre','meter'):
                raise GeoError('NATIVE_GRID_UNSUPPORTED','当前原生统计需要米制投影的光谱参考波段格网')
            if getattr(reader,'quality_kind',None)=='landsat_qa' and (not math.isclose(abs(ds.transform.a),30,abs_tol=1e-7) or not math.isclose(abs(ds.transform.e),30,abs_tol=1e-7) or abs(ds.transform.b)>1e-10 or abs(ds.transform.d)>1e-10):
                raise GeoError('LANDSAT_GRID_UNSUPPORTED','Landsat实际COG参考格网不是本版支持的30m北向格网，未按标称分辨率冒充统计',502)
            if algorithm['id'] in FORMULAS and (not math.isclose(abs(ds.transform.a),algorithm['reference_resolution_m'],abs_tol=1e-7) or not math.isclose(abs(ds.transform.e),algorithm['reference_resolution_m'],abs_tol=1e-7) or abs(ds.transform.b)>1e-10 or abs(ds.transform.d)>1e-10):
                raise GeoError('REFERENCE_GRID_MISMATCH','实际参考COG格网与该指数声明的原生分辨率不一致，未用上采样冒充原生分析',502)
            bounds=transform_bounds('EPSG:4326',ds.crs,*box,densify_pts=21)
            w=from_bounds(*bounds,ds.transform);col,row=math.floor(w.col_off),math.floor(w.row_off)
            window=Window(col,row,math.ceil(w.col_off+w.width)-col,math.ceil(w.row_off+w.height)-row)
            rows,cols=int(window.height),int(window.width)
            if min(rows,cols)<1 or max(rows,cols)>2048 or rows*cols>MAX_CELLS:raise GeoError('NATIVE_WINDOW_LIMIT','原生分析窗口超出本机像元限额，请缩小地图选区')
            crs=ds.crs;transform=ds.window_transform(window)
        # Densified WGS84 rectangle: target pixel centres define the AOI mask.
        west,south,east,north=box;ring=[]
        for a,b in [((west,south),(east,south)),((east,south),(east,north)),((east,north),(west,north)),((west,north),(west,south))]:
            ring.extend([[a[0]+(b[0]-a[0])*i/20,a[1]+(b[1]-a[1])*i/20] for i in range(20)])
        ring.append(ring[0]);geom=transform_geom('EPSG:4326',crs,{'type':'Polygon','coordinates':[ring]})
        mask=geometry_mask([geom],out_shape=(rows,cols),transform=transform,invert=True,all_touched=False)
        if not mask.any():raise GeoError('EMPTY_NATIVE_AOI','选区没有包含原生像元中心，请扩大选区')
        result={};asset_evidence={}
        with rasterio.Env(**env):
            for epoch,scene in scenes.items():
                result[epoch]={};asset_evidence[epoch]={}
                for band,asset in sorted(scene['assets'].items(),key=lambda pair:0 if pair[0] in ('scl','qa_pixel','qa_radsat') else 1):
                    check()
                    with rasterio.open(reader.signed_url(asset['href'],check)) as ds:
                        if not ds.crs:raise GeoError('MISSING_CRS','源资产缺少坐标参考')
                        if getattr(reader,'quality_kind',None)=='landsat_qa' and (not ds.crs.is_projected or ds.crs.linear_units not in ('metre','meter') or not math.isclose(abs(ds.transform.a),30,abs_tol=1e-7) or not math.isclose(abs(ds.transform.e),30,abs_tol=1e-7) or abs(ds.transform.b)>1e-10 or abs(ds.transform.d)>1e-10):
                            raise GeoError('LANDSAT_GRID_UNSUPPORTED','Landsat实际资产不是本版支持的30m米制北向格网',502)
                        if algorithm['id'] in FORMULAS and band in algorithm['band_keys']:
                            expected=next(b['native_resolution_m'] for b in algorithm['required_bands'] if b['name']==band)
                            if not ds.crs.is_projected or ds.crs.linear_units not in ('metre','meter') or not math.isclose(abs(ds.transform.a),expected,abs_tol=1e-7) or not math.isclose(abs(ds.transform.e),expected,abs_tol=1e-7) or abs(ds.transform.b)>1e-10 or abs(ds.transform.d)>1e-10:
                                raise GeoError('INPUT_GRID_MISMATCH','实际光谱输入格网与所需波段原生分辨率不一致：'+band,502)
                        b=transform_bounds('EPSG:4326',ds.crs,*box,densify_pts=21)
                        w=from_bounds(*b,ds.transform);c,r=math.floor(w.col_off),math.floor(w.row_off)
                        win=Window(c,r,math.ceil(w.col_off+w.width)-c,math.ceil(w.row_off+w.height)-r)
                        if min(win.width,win.height)<1 or max(win.width,win.height)>2048:raise GeoError('NATIVE_WINDOW_LIMIT','原生资产窗口超过2048像素')
                        source_transform=ds.window_transform(win)
                        key={'href':asset['href'],'window':[float(win.col_off),float(win.row_off),int(win.width),int(win.height)],'crs':ds.crs.to_string(),'transform':list(source_transform)[:6],'nodata':float(ds.nodata) if ds.nodata is not None and math.isfinite(float(ds.nodata)) else None,'asset_nodata':asset.get('nodata'),'source_dtype':ds.dtypes[0],'read_dtype':'float32','masked':True,'boundless':True}
                        if asset.get('raw_quality'):key.update(masked=False,quality_fill=65535)
                        def load_window():
                            # Zero is a valid unsaturated QA_RADSAT value. Read
                            # QA bitfields without rasterio's nodata mask; an
                            # out-of-source pixel is explicitly invalid 65535.
                            raw=ds.read(1,window=win,boundless=True,masked=False,fill_value=65535).astype('float32') if asset.get('raw_quality') else ds.read(1,window=win,boundless=True,masked=True).astype('float32').filled(np.nan)
                            if asset.get('nodata') is not None:raw[raw==asset['nodata']]=np.nan
                            return {'raw':raw,'evidence':{'window_sha256':hashlib.sha256(raw.tobytes(order='C')).hexdigest()}}
                        cached,hit=WINDOW_CACHE.get_or_load(key,load_window,check=check)
                        cache_observations[epoch+':'+band]={'hit':hit,**cached.get('cache',{})}
                        raw=cached['raw'].copy();raw_hash=cached['evidence']['window_sha256'];cache_hits+=int(hit);source_bytes+=raw.nbytes;hit_bytes+=raw.nbytes if hit else 0;window_count+=1
                        if band not in ('scl','qa_pixel','qa_radsat'):raw=raw*asset['scale']+asset['offset']
                        target=np.full((rows,cols),np.nan,dtype='float32')
                        reproject(raw,target,src_transform=ds.window_transform(win),src_crs=ds.crs,src_nodata=np.nan,dst_transform=transform,dst_crs=crs,dst_nodata=np.nan,resampling=Resampling.nearest)
                        target[~mask]=np.nan;result[epoch][band]=target
                        if not plan.get('allow_sparse_quality') and band=='scl' and int((mask&np.isfinite(target)&np.isin(target,[4,5,6,7])).sum())/int(mask.sum())<.1:raise GeoError('INSUFFICIENT_VALID_PIXELS','SCL质量预检可用覆盖不足10%，已提前停止，未读取额外光谱波段或切换样本')
                        if band in ('qa_pixel','qa_radsat') and all(k in result[epoch] for k in ('qa_pixel','qa_radsat')):
                            finite_qa,quality_qa=landsat_quality(result[epoch])
                            if not plan.get('allow_sparse_quality') and int((mask&finite_qa&quality_qa).sum())/int(mask.sum())<.1:raise GeoError('INSUFFICIENT_VALID_PIXELS','Landsat QA质量预检可用覆盖不足10%，未读取额外光谱波段或切换样本')
                        asset_evidence[epoch][band]={'href':asset['href'],'crs':ds.crs.to_string(),'transform':list(ds.window_transform(win))[:6],'window':[float(win.col_off),float(win.row_off),int(win.width),int(win.height)],'window_sha256':raw_hash,'scale':asset.get('scale'),'offset':asset.get('offset'),'resampling':'nearest to '+reference_label}
                    check()
        cell_area=abs(transform.a*transform.e-transform.b*transform.d)
        metadata={'sampling':'native','algorithm_version':VERSION,'source_type':'public_satellite_pixels','provider':'ESA Sentinel-2 L2A / Microsoft Planetary Computer COG','bbox':box,'rows':rows,'cols':cols,'crs':crs.to_string(),'transform':list(transform)[:6],'cell_area_m2':cell_area,'native_pixel_count':int(mask.sum()),'scenes':copy.deepcopy(scenes),'asset_evidence':asset_evidence,'reference_band':algorithm['reference_band'],'reference_resolution_m':algorithm['reference_resolution_m'],'required_bands':algorithm['required_bands'],'resampling':reference_label+'; required bands/before/SCL nearest aligned; preview resampling excluded from statistics','mask_rule':'WGS84 AOI rectangle, densified edges, pixel centres inside, all_touched=False','scl_retained':[4,5,6,7]}
        if getattr(reader,'quality_kind',None)=='landsat_qa':
            metadata.pop('scl_retained',None)
            metadata.update(provider=reader.provider,sensor=reader.sensor_id,reader_version=reader.reader_version,quality_kind='landsat_qa',quality_policy=copy.deepcopy(reader.quality_policy),index=copy.deepcopy(algorithm),resampling=reference_label+'; required bands/before/QA nearest aligned; preview excluded from statistics')
        h=hashlib.sha256(json.dumps(metadata,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode())
        h.update(mask.tobytes(order='C'))
        for epoch in sorted(result):
            for band in sorted(result[epoch]):h.update(result[epoch][band].tobytes(order='C'))
        metadata['input_sha256']=h.hexdigest();metadata['efficiency']={'source_window_cache_hits':cache_hits,'source_windows':window_count,'uncompressed_source_window_bytes':source_bytes,'cache_hit_source_window_bytes':hit_bytes,'read_seconds':round(time.monotonic()-started,4),'cache_policy':'process memory 30min/64MiB; unsigned immutable scene assets; no provider content-version guarantee','only_required_bands':True,'windows':cache_observations};result['metadata']=metadata;result['_mask']=mask
        return result
    except GeoError:raise
    except Exception:raise GeoError('NATIVE_COG_READ_FAILED','真实COG原生窗口读取或配准失败，未使用代表网格或人工样本替代',502)


def landsat_quality(epoch):
    """Return finite/integer QA mask and the explicit L8/9 policy mask."""
    import numpy as np
    qa,rad=epoch['qa_pixel'],epoch['qa_radsat']
    finite=np.isfinite(qa)&np.isfinite(rad)&(qa>=0)&(qa<=65535)&(rad>=0)&(rad<=65535)&(qa==np.floor(qa))&(rad==np.floor(rad))
    q=np.where(finite,qa,65535).astype('uint16');r=np.where(finite,rad,65535).astype('uint16')
    return finite,((q&63)==0)&(r==0)


def grids(bands,index_id="ndvi"):
    import numpy as np
    output={};reasons={};mask=bands['_mask'];algorithm=specification(index_id)
    for epoch in ('before','after'):
        if epoch not in bands:continue
        required=[bands[epoch][key] for key in algorithm['band_keys']]
        finite_bands=np.logical_and.reduce([np.isfinite(value) for value in required])
        if bands['metadata'].get('quality_kind')=='landsat_qa':
            finite_qa,quality=landsat_quality(bands[epoch]);finite=finite_bands&finite_qa;quality_key='qa_excluded'
        else:
            scl=bands[epoch]['scl'];finite=finite_bands&np.isfinite(scl);quality=np.isin(scl,[4,5,6,7]);quality_key='scl_excluded'
        reflectance=np.logical_and.reduce([value>=0 for value in required])
        computed,domain=evaluate_index(index_id,bands[epoch]);positive=reflectance&domain['valid']
        valid=mask&finite&quality&positive
        ndvi=np.where(valid,computed,np.nan).astype('float32')
        output[epoch]=(ndvi,valid)
        reasons[epoch]={'nodata':int((mask&~finite).sum()),quality_key:int((mask&finite&~quality).sum()),'invalid_reflectance':int((mask&finite&quality&~positive).sum())}
        if index_id in FORMULAS:
            # Domain diagnostics can overlap a negative-reflectance rejection;
            # report them rather than hide an invalid root behind another mask.
            base=mask&finite&quality
            reasons[epoch].update(invalid_denominator=int((base&domain['invalid_denominator']).sum()),invalid_radicand=int((base&domain['invalid_radicand']).sum()),nonfinite_formula_result=int((base&domain['nonfinite_result']).sum()))
    return output,reasons


def quality_summary(bands,index_id="ndvi"):
    data,reasons=grids(bands,index_id)
    if bands['metadata'].get('quality_kind')=='landsat_qa':return {epoch:{'valid_count':int(mask.sum()),'quality_kind':'landsat_qa','quality_policy':copy.deepcopy(bands['metadata']['quality_policy']),'mask_reasons':reasons[epoch]} for epoch,(_,mask) in data.items()}
    return {epoch:{'valid_count':int(mask.sum()),'scl7_retained_count':int((mask&(bands[epoch]['scl']==7)).sum()),'mask_reasons':reasons[epoch]} for epoch,(_,mask) in data.items()}


def compute_native(plan,bands,task_id,artifact_dir,check):
    import numpy as np
    import rasterio
    from affine import Affine
    from rasterio.transform import array_bounds
    from rasterio.warp import calculate_default_transform,reproject,Resampling,transform_bounds
    from PIL import Image
    check();meta=bands['metadata'];algorithm=meta.get('index') or plan.get('index') or specification(plan.get('index_id','ndvi'));index_id=algorithm['id'];label=algorithm['label'];data,reasons=grids(bands,index_id);after,av=data['after'];change=plan['goal']=='change'
    before,bv=data['before'] if change else (None,None);valid=av&bv if change else av
    count=int(bands['_mask'].sum());n=int(valid.sum())
    if not count:raise GeoError('EMPTY_NATIVE_AOI','选区没有参考格网像元中心')
    ratio=n/count
    if not n or ratio<.1:raise GeoError('INSUFFICIENT_VALID_PIXELS','原生选区共同有效像元不足10%或全部无效，未发布结果或替换数据')
    delta=np.where(valid,after-before,np.nan).astype('float32') if change else None
    value=(-delta if plan['direction']=='decrease' else delta if plan['direction']=='increase' else np.abs(delta)) if change else after
    operator=plan.get('selection',{}).get('operator','gte');threshold=plan['threshold']
    operations={'lt':np.less,'lte':np.less_equal,'gt':np.greater,'gte':np.greater_equal,'eq':np.equal}
    if operator not in operations:raise GeoError('INVALID_SELECTION','不支持此阈值比较')
    selected=valid&operations[operator](value,threshold)
    if change and plan['direction'] in ('increase','decrease'):selected&=value>=0
    selected_count=int(selected.sum());cell_ha=meta['cell_area_m2']/10000
    summary={'sampling':'native','native_pixel_count':count,'pixel_count':count,'valid_count':n,'invalid_count':count-n,'valid_ratio':ratio,'after_valid_count':int(av.sum()),'before_valid_count':int(bv.sum()) if change else None,'index':copy.deepcopy(algorithm),'index_id':index_id,'mean_index':float(after[valid].mean(dtype='float64')),'mean_before_index':float(before[valid].mean(dtype='float64')) if change else None,'mean_delta':float(delta[valid].mean(dtype='float64')) if change else None,'min_index':float(after[valid].min()),'max_index':float(after[valid].max()),'selected_count':selected_count,'changed_count':selected_count if change else None,'selected_area_ha':selected_count*cell_ha,'selected_grid_area_ha':selected_count*cell_ha,'area_ha':selected_count*cell_ha,'analyzed_area_ha':count*cell_ha,'valid_area_ha':n*cell_ha,'threshold':threshold,'selection':copy.deepcopy(plan.get('selection',{})),'direction':plan['direction'],'mask_reasons':reasons,'formula':algorithm['formula'],'change_formula':'after_'+index_id+'-before_'+index_id,'method':'all AOI pixel centres on after-'+algorithm['reference_band']+' native projected grid; cell area = affine determinant','area_label':'阈值命中原生参考格网投影面积，非作物/地块面积','quality_warning':'共同有效比例低于50%，结果仅供质量检查' if ratio<.5 else ''}
    if index_id in ('evi2','savi','msavi2'):
        low,high=float(after[valid].min()),float(after[valid].max())
        if low==high:low-=max(1e-6,abs(low)*1e-6);high+=max(1e-6,abs(high)*1e-6)
        histogram,edges=np.histogram(after[valid],bins=np.linspace(low,high,9))
    else:histogram,edges=np.histogram(after[valid],bins=[-1,-.2,0,.2,.4,.6,.8,1.000001])
    summary['index_histogram']=[{'low':float(edges[i]),'high':float(edges[i+1]),'count':int(c)} for i,c in enumerate(histogram)]
    if index_id in FORMULAS:
        summary.update(histogram_policy='all_valid_values; no clipping',outside_normalized_range_count=int(((after<-1)|(after>1))[valid].sum()),output_values_clipped=False)
        if sum(int(value) for value in histogram)!=n:raise GeoError('HISTOGRAM_BALANCE_ERROR','指数直方图未包含全部有效像元，停止发布')
    if index_id=='ndvi':
        for old,new in [('mean_ndvi','mean_index'),('mean_before_ndvi','mean_before_index'),('min_ndvi','min_index'),('max_ndvi','max_index'),('ndvi_histogram','index_histogram')]:summary[old]=copy.deepcopy(summary[new])
    landsat=meta.get('quality_kind')=='landsat_qa'
    unclassified=np.zeros(valid.shape,dtype='bool') if landsat else valid&(bands['after']['scl']==7)
    if change and not landsat:unclassified|=valid&(bands['before']['scl']==7)
    summary['unclassified_retained_count']=int(unclassified.sum())
    if summary['unclassified_retained_count']:
        if summary['quality_warning']:summary['quality_warning']+='；'
        summary['quality_warning']+='共同有效中含%d个保留的SCL7未分类像元；有效覆盖不能等同严格无云或分类准确率。'%summary['unclassified_retained_count']
    summary['efficiency']=copy.deepcopy(meta.get('efficiency',{}))
    if change:legend=[{'label':'下降 ≥ '+str(threshold),'color':'#dc8d78'},{'label':'差值介于阈值之间','color':'#d8c994'},{'label':'上升 ≥ '+str(threshold),'color':'#65c4a1'}]
    elif index_id=='ndvi':legend=[{'label':'NDVI < 0','color':'#dc8d78'},{'label':'0 ≤ NDVI < 0.2','color':'#e0c687'},{'label':'0.2 ≤ NDVI < 0.5','color':'#afc98a'},{'label':'NDVI ≥ 0.5','color':'#65c4a1'}]
    elif algorithm.get('visualization'):
        breaks=algorithm['visualization']['breaks'];colors=algorithm['visualization']['colors']
        legend=[{'label':label+' < '+str(breaks[0]),'color':colors[0]},*[{'label':str(breaks[i-1])+' ≤ '+label+' < '+str(breaks[i]),'color':colors[i]} for i in range(1,len(breaks))],{'label':label+' ≥ '+str(breaks[-1]),'color':colors[-1]}]
    else:legend=[{'label':label+' < -0.2','color':'#c5a583'},{'label':'-0.2 ≤ '+label+' < 0','color':'#d8ccae'},{'label':'0 ≤ '+label+' < 0.2','color':'#a7d6d4'},{'label':label+' ≥ 0.2','color':'#488cbd'}]
    folder=Path(artifact_dir);folder.mkdir(parents=True,exist_ok=True);transform=Affine(*meta['transform']);crs=meta['crs']
    arrays=[after];names=['after_'+index_id]
    if change:arrays.extend([before,delta]);names.extend(['before_'+index_id,'delta_'+index_id])
    arrays.extend([np.where(bands['_mask'],valid.astype('float32'),np.nan),bands['_mask'].astype('float32')]);names.extend(['common_valid_mask','aoi_mask'])
    evidence_band_details={}
    if index_id!='ndvi' or landsat:
        for epoch in (['after','before'] if change else ['after']):
            for band in algorithm['required_bands']:
                name=epoch+'_'+band['name']+'_reflectance';arrays.append(bands[epoch][band['name']]);names.append(name);evidence_band_details[name]={'source_band':band['id'],'units':'unitless_surface_reflectance','calibration_already_applied':True}
            if landsat:
                for quality_band in ('qa_pixel','qa_radsat'):
                    name=epoch+'_'+quality_band;arrays.append(bands[epoch][quality_band]);names.append(name);evidence_band_details[name]={'source_band':quality_band.upper(),'units':'uint16_bitfield_stored_exactly_in_float32','quality_policy':meta['quality_policy']}
            else:
                name=epoch+'_scl';arrays.append(bands[epoch]['scl']);names.append(name);evidence_band_details[name]={'source_band':'SCL','units':'category_code','retained':[4,5,6,7]}
    with rasterio.open(folder/'analysis.tif','w',driver='GTiff',height=after.shape[0],width=after.shape[1],count=len(arrays),dtype='float32',crs=crs,transform=transform,nodata=float('nan'),compress='deflate',predictor=3,tiled=True,blockxsize=256,blockysize=256) as dst:
        for i,(array,name) in enumerate(zip(arrays,names),1):dst.write(array.astype('float32'),i);dst.set_band_description(i,name)
        for i,name in enumerate(names,1):
            if name in evidence_band_details:dst.update_tags(i,**{k:str(v) for k,v in evidence_band_details[name].items()})
        dst.update_tags(algorithm_version=VERSION,sampling='native',reference='after '+algorithm['reference_band']+' native grid',index_id=index_id,index_formula=algorithm['formula'],input_sha256=meta['input_sha256'],task_id=task_id)
    check()
    rows,cols=after.shape;bounds=array_bounds(rows,cols,transform)
    preview_transform,width,height=calculate_default_transform(crs,'EPSG:3857',cols,rows,*bounds)
    scale=max(width/1536,height/1536,1);width=max(1,int(width/scale));height=max(1,int(height/scale))
    from rasterio.transform import from_bounds
    mercator_bounds=transform_bounds(crs,'EPSG:3857',*bounds,densify_pts=21);preview_transform=from_bounds(*mercator_bounds,width,height)
    target=np.full((height,width),np.nan,dtype='float32');source=np.where(valid,delta if change else after,np.nan).astype('float32')
    reproject(source,target,src_transform=transform,src_crs=crs,src_nodata=np.nan,dst_transform=preview_transform,dst_crs='EPSG:3857',dst_nodata=np.nan,resampling=Resampling.nearest)
    rgba=np.zeros((height,width,4),dtype='uint8');usable=np.isfinite(target)
    if change:categories=[target<=-threshold,(target>-threshold)&(target<threshold),target>=threshold]
    elif algorithm.get('visualization'):
        breaks=algorithm['visualization']['breaks'];categories=[target<breaks[0],*[(target>=breaks[i-1])&(target<breaks[i]) for i in range(1,len(breaks))],target>=breaks[-1]]
    else:categories=[target<0,(target>=0)&(target<.2),(target>=.2)&(target<.5),target>=.5] if index_id=='ndvi' else [target<-.2,(target>=-.2)&(target<0),(target>=0)&(target<.2),target>=.2]
    for category,entry in zip(categories,legend):
        color=entry['color'].lstrip('#');rgba[usable&category]=[*[int(color[i:i+2],16) for i in (0,2,4)],220]
    Image.fromarray(rgba).save(folder/'overlay.png')
    geo_bounds=transform_bounds('EPSG:3857','EPSG:4326',*mercator_bounds,densify_pts=21);box=plan['aoi']['bbox'];w,s,e,north=box;ring=[[w,s],[e,s],[e,north],[w,north],[w,s]]
    prefix='/api/production/tasks/'+task_id
    provenance={'mode':plan['mode'],'sampling':'native','source_type':meta['source_type'],'source':meta['provider'],'algorithm_version':VERSION,'algorithm':copy.deepcopy(algorithm),'index_id':index_id,'run_id':task_id,'requirement':plan['requirement'],'bbox':box,'requested_periods':plan['periods'],'input_sha256':meta['input_sha256'],'source_details':meta,'crs':crs,'grid':{'rows':rows,'cols':cols,'transform':meta['transform'],'cell_area_m2':meta['cell_area_m2'],'resampling':meta['resampling'],'reference':'after-'+algorithm['reference_band']+' native grid','reference_band':algorithm['reference_band'],'reference_resolution_m':algorithm['reference_resolution_m']},'raster_evidence':{'band_descriptions':names,'source_bands':evidence_band_details,'note':'New index exports preserve calibrated aligned inputs and SCL for independent formula/mask checks; not original DN or unresampled source assets.'},'quality':{'scl_retained':[4,5,6,7],'valid_ratio':ratio,'unclassified_retained_count':summary['unclassified_retained_count'],'policy_note':'保留SCL7未分类；非严格无云保证'},'review_status':'待业务核查'}
    if landsat:
        provenance.update(source_id='pc_landsat_c2_l2',sensor=meta['sensor'],reader_version=meta['reader_version'])
        provenance['quality']={'kind':'landsat_qa','valid_ratio':ratio,'policy':copy.deepcopy(meta['quality_policy']),'policy_note':'按L8/9 QA_PIXEL bits0–5及所有非零QA_RADSAT保守剔除；水体bit7保留；质量有效不等于分类准确率'}
        provenance['raster_evidence']['note']='Exports preserve calibrated aligned reflectance and original aligned QA bitfield values for formula/mask recomputation; no SCL is invented and no raw DN is claimed.'
    findings=['读取原生窗口全部选区像元中心并统计：%d个，%d个共同有效。'%(count,summary['valid_count']),'每个观测窗口自动选择一景满足覆盖/云量条件的影像，不代表月平均或多景合成。',summary['area_label']+'；不认定作物、法定耕地、灾损或真实地块变化。']
    if summary['quality_warning']:findings.append(summary['quality_warning'])
    report={'title':('真实Landsat8/9原生30m格网' if landsat else '真实Sentinel-2原生参考格网')+('两期光谱变化' if change else label)+'分析报告','input':{'requirement':plan['requirement'],'bbox':box,'requested_periods':plan['periods'],'goal':plan['goal']},'findings':findings,'limitations':plan['limitations'],'provenance':provenance,'steps':[]}
    product={'summary':summary,'provenance':provenance,'report':report,'geojson':{'type':'FeatureCollection','features':[{'type':'Feature','id':task_id+'-aoi','geometry':{'type':'Polygon','coordinates':[ring]},'properties':{'name':'实际分析选区','source_type':'public_satellite_pixels','task_id':task_id}}]},'image':{'url':prefix+'/overlay.png','bounds':list(geo_bounds),'crs':'EPSG:3857','width':width,'height':height,'legend':legend,'statistics_from_preview':False},'artifacts':[{'type':'geotiff','name':'完整原生参考格网分析','url':prefix+'/analysis.tif'},{'type':'png','name':'配准分析预览','url':prefix+'/overlay.png'},{'type':'json','name':'结果与来源报告','url':prefix+'/result.json'}]}
    (folder/'result.json').write_text(json.dumps(product,ensure_ascii=False,indent=2,allow_nan=False),'utf-8');check();return product
