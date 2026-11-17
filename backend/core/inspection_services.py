"""Read one native-grid cell from a successful local raster result only."""
import importlib.util
import math
import re
from pathlib import Path
from backend.core.geo_services import GeoError
from backend.analysis.landcover_services import CLASSES

class InspectionServices:
    def __init__(self,store):self.store=store
    def inspect(self,kind,task_id,lon,lat,expected_version=None):
        if kind not in ('production','landcover','cropland','agriculture','raster_processing','agriculture_atlas'):raise GeoError('INSPECTION_UNSUPPORTED','只能核查已完成的原生光谱、分类或影像处理成果',422)
        if not isinstance(task_id,str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,100}',task_id):raise GeoError('INVALID_TASK_ID','成果编号无效',400)
        try:
            if isinstance(lon,bool) or isinstance(lat,bool):raise ValueError()
            lon,lat=float(lon),float(lat)
            if not math.isfinite(lon) or not math.isfinite(lat) or not -180<=lon<=180 or not -85<=lat<=85:raise ValueError()
        except (ValueError,TypeError,OverflowError):raise GeoError('INVALID_COORDINATES','经纬度须为合法WGS84有限数值，经度在前、纬度在后',400)
        if not all(importlib.util.find_spec(m) for m in ('rasterio','numpy')):raise GeoError('INSPECTION_UNAVAILABLE','本机缺少栅格读取运行环境',503)
        if kind=='agriculture_atlas':return self._atlas(task_id,lon,lat,expected_version)
        import numpy as np
        import rasterio
        from rasterio.features import geometry_mask
        from rasterio.warp import transform,transform_geom,transform_bounds
        from rasterio.windows import Window
        service=getattr(self.store,kind);task=service.get(task_id)
        if task.get('status')!='succeeded':raise GeoError('INSPECTION_NOT_READY','该成果尚未成功，不能返回像元值',409)
        result=task.get('result') or {};p=result.get('provenance') or {};summary=result.get('summary') or {};plan=task.get('plan') or {}
        if kind=='production' and plan.get('sampling')!='native':raise GeoError('INSPECTION_UNSUPPORTED','旧样本或代表网格成果不具备原生像元核查文件',422)
        if kind=='agriculture' and plan.get('algorithm_id')!='fvc':raise GeoError('INSPECTION_UNSUPPORTED','逐地块统计是矢量汇总，只有FVC农业成果提供原生栅格点查',422)
        path=Path(service.artifact(task_id,'analysis.tif' if kind in ('production','agriculture','raster_processing') else 'classification.tif'))
        if not path.is_file() or path.suffix.lower() not in ('.tif','.tiff'):raise GeoError('INSPECTION_UNAVAILABLE','本地原生栅格文件不可用，未用预览图替代',404)
        base={'task_id':task_id,'kind':kind,'coordinates':[lon,lat],'inside_aoi':False,'valid':False,'value':None,'bands':{},'row':None,'column':None,'status':'outside_raster','message':'该位置在成果栅格范围之外','limitations':['核查值来自导出的原生参考格网，不从PNG或底图取值。','单个像元数值不构成地块、灾损或法定业务认定。']}
        scenes=p.get('source_details',{}).get('scenes',{})
        dates=[str(scene.get('datetime',''))[:10] for scene in (scenes.values() if isinstance(scenes,dict) else scenes if isinstance(scenes,list) else []) if isinstance(scene,dict)]
        if dates:base['actual_dates']=dates
        if kind=='raster_processing':
            algorithm=plan.get('algorithm_id');actual=p.get('actual_dates') or summary.get('actual_dates') or (p.get('source_ref') or {}).get('actual_dates') or []
            base['actual_dates']=[str(value)[:10] for value in (actual.values() if isinstance(actual,dict) else actual if isinstance(actual,list) else [actual]) if value]
            base.update(algorithm_id=algorithm,index_id=summary.get('index_id') or plan.get('index_id') or (plan.get('source') or {}).get('index_id'),units=summary.get('slope_unit') or summary.get('index_unit') or summary.get('unit'),minimum_observations=summary.get('min_observations',summary.get('minimum_observations',plan.get('minimum_observations'))),support_count=None)
            base['limitations'].append('合成/趋势使用实际观测及有效次数；阈值分区是指数条件，不是作物、水体或灾损认定。')
        if kind=='agriculture':
            actual=p.get('actual_dates') or (p.get('source_ref') or {}).get('actual_dates') or {}
            if isinstance(actual,dict):base['actual_dates']=[str(value)[:10] for value in actual.values() if value]
            base.update(index_id='fvc',units='fraction',soil_ndvi=plan.get('soil_ndvi'),vegetation_ndvi=plan.get('vegetation_ndvi'),endmember_source=plan.get('endmember_source'),source_ref=p.get('source_ref'))
            base['limitations'].append('FVC按用户NDVI端元估算并裁至[0,1]，端元未做本地校准；不是实测覆盖率或作物分类。')
        if kind=='production':
            index_id=summary.get('index_id') or p.get('index_id') or plan.get('index_id') or 'ndvi';base['index_id']=index_id
            if p.get('grid',{}).get('reference_resolution_m') is not None:base['reference_resolution_m']=p['grid']['reference_resolution_m']
            elif index_id=='ndvi':base['reference_resolution_m']=10
        elif kind not in ('agriculture','raster_processing'):
            base.update(source_year=p.get('year',summary.get('reference_year')),class_code=None,class_label=None)
            if summary.get('nominal_resolution_m') is not None:base['reference_resolution_m']=summary['nominal_resolution_m']
            base['limitations'].append('类别来自历史分类产品与'+('用户选区' if kind=='cropland' else '参考市界')+'；不是当前权威调查或现场核验。')
        try:
            with rasterio.open(path,'r') as ds:
                if ds.driver!='GTiff':raise GeoError('INSPECTION_UNSUPPORTED','仅支持归档本地GeoTIFF，不能核查任意虚拟栅格',422)
                if not ds.crs:raise GeoError('INSPECTION_UNSUPPORTED','成果缺少坐标参考，不能猜测像元位置',422)
                base['crs']=ds.crs.to_string()
                if kind in ('agriculture','raster_processing') and ds.crs.is_projected and ds.crs.linear_units in ('metre','meter','metres','meters'):
                    base['reference_resolution_m']=max(abs(ds.transform.a),abs(ds.transform.e))
                west,south,east,north=transform_bounds(ds.crs,'EPSG:4326',*ds.bounds,densify_pts=21)
                in_longitude=(west<=lon<=east) if west<=east else (lon>=west or lon<=east)
                if not in_longitude or not south<=lat<=north:return base
                xs,ys=transform('EPSG:4326',ds.crs,[lon],[lat])
                if not math.isfinite(xs[0]) or not math.isfinite(ys[0]):return base
                row,col=ds.index(xs[0],ys[0])
                if not (0<=row<ds.height and 0<=col<ds.width):return base
                base.update(row=int(row),column=int(col));window=Window(col,row,1,1)
                values=ds.read(window=window,masked=False)[:,0,0]
                names=[name or 'band_'+str(i+1) for i,name in enumerate(ds.descriptions)]
                base['bands']={name:float(value) if math.isfinite(float(value)) and not (ds.nodata is not None and math.isfinite(float(ds.nodata)) and float(value)==float(ds.nodata)) else None for name,value in zip(names,values)}
                if kind=='raster_processing':
                    bands=base['bands'];otsu=plan.get('algorithm_id')=='otsu_segmentation';valid_band='common_valid_mask' if otsu else 'valid_mask';value_band='selected_mask' if otsu else 'value'
                    if not all(key in bands for key in ('aoi_mask',valid_band,value_band)):raise GeoError('INSPECTION_UNSUPPORTED','影像处理成果缺少主值或明确掩膜，不换用其他波段',422)
                    inside=bands['aoi_mask']==1;valid=inside and bands[valid_band]==1 and bands[value_band] is not None
                    support=bands.get('valid_observation_count');base['support_count']=int(support) if support is not None else None
                    unit='指数/天' if plan.get('algorithm_id')=='temporal_trend' else '条件掩膜（1命中、0未命中）' if otsu else '指数值'
                    base.update(inside_aoi=inside,valid=bool(valid),value=bands[value_band] if valid else None,value_band=value_band,metric_label=(base.get('index_id') or '光谱').upper()+' · '+unit,threshold=summary.get('threshold'),threshold_operator=summary.get('threshold_operator'))
                    minimum=base.get('minimum_observations')
                    if inside and not valid and support is not None and minimum is not None and support<minimum:base['quality_reason']='insufficient_observation_support'
                elif kind in ('production','agriculture'):
                    bands=base['bands']
                    if not all(key in bands for key in ('aoi_mask','common_valid_mask')):raise GeoError('INSPECTION_UNSUPPORTED','该栅格没有可核查的选区和共同有效掩膜',422)
                    inside=bands['aoi_mask']==1;valid=inside and bands['common_valid_mask']==1
                    value_band='fvc' if kind=='agriculture' else ('delta_' if plan.get('goal')=='change' else 'after_')+index_id
                    if value_band not in bands:raise GeoError('INSPECTION_UNSUPPORTED','栅格缺少该指数对应波段，未换用其他值',422)
                    valid=valid and bands[value_band] is not None
                    base.update(inside_aoi=inside,valid=bool(valid),value=bands[value_band] if valid else None,value_band=value_band,metric_label='FVC植被覆盖度估算' if kind=='agriculture' else ('Δ' if plan.get('goal')=='change' else '')+index_id.upper())
                else:
                    boundary=result.get('boundary') or result.get('selection_boundary') or {};geoms=[f.get('geometry') for f in boundary.get('features',[]) if (f.get('geometry') or {}).get('type') in ('Polygon','MultiPolygon')]
                    if not geoms:raise GeoError('INSPECTION_UNSUPPORTED','分类栅格没有归档市界，不能把外包矩形当作统计范围',422)
                    if ds.crs.to_epsg()!=4326:geoms=[transform_geom('EPSG:4326',ds.crs,geometry) for geometry in geoms]
                    io_product=kind=='cropland' and p.get('product',{}).get('id')=='io_lulc_2024'
                    if io_product:
                        if 'aoi_mask' not in base['bands']:raise GeoError('INSPECTION_UNSUPPORTED','该产品缺少归档选区掩膜，不能用投影外包矩形代替选区',422)
                        inside=base['bands']['aoi_mask']==1
                    else:inside=bool(geometry_mask(geoms,out_shape=(1,1),transform=ds.window_transform(window),invert=True,all_touched=False)[0,0])
                    raw=float(values[0]);classes={int(c['code']):c['name'] for c in result.get('classes',[]) if c.get('code') not in (0,10)} if io_product else {code:label for code,label,_ in CLASSES};valid=inside and math.isfinite(raw) and raw in classes and (ds.nodata is None or raw!=ds.nodata)
                    if io_product:base.update(product_id='io_lulc_2024',area_method=p.get('area_method'),quality_reason='cloud_unknown' if inside and raw==10 else 'nodata_unknown' if inside and raw==0 else None)
                    base.update(inside_aoi=inside,valid=bool(valid),class_code=int(raw) if valid else None,class_label=classes.get(int(raw)) if valid else None,value=int(raw) if valid else None,value_band=names[0],metric_label='土地覆盖类别')
                if not base['inside_aoi']:base.update(status='outside_aoi',message='该像元中心在归档分析选区或参考市界之外')
                elif not base['valid']:base.update(status='invalid',message='该像元有效观测次数不足，未计算合成或趋势；不能当作指数或变化等于0。' if base.get('quality_reason')=='insufficient_observation_support' else '该像元为云覆盖或缺测，未计入有效分类；不能当作非农田。' if base.get('quality_reason') else '该像元未参与统计：无效、缺测或未满足共同质量规则')
                else:base.update(status='valid',message='已读取原生格网像元值')
                return base
        except GeoError:raise
        except Exception:raise GeoError('INSPECTION_UNAVAILABLE','本地成果栅格读取或坐标转换失败，未生成替代值',503)

    def _atlas(self,ident,lon,lat,expected_version):
        import numpy as np
        import rasterio
        from rasterio.warp import transform
        from rasterio.windows import Window
        from backend.analysis.regional_raster import descriptor,validate_grid
        atlas=self.store.agriculture_atlas
        if expected_version is not None:atlas.require_version(ident,expected_version)
        if atlas.get(ident).get('product_type')=='crop_reference':return self._crop_reference(ident,lon,lat)
        task,path=descriptor(atlas,ident,for_analysis=False);product=task['atlas_product'];native=task['plan']['native'];summary=task['result']['summary']
        base={'task_id':ident,'kind':'agriculture_atlas','product_sha256':product['product_sha256'],'coordinates':[lon,lat],'inside_aoi':False,'valid':False,'value':None,'bands':{},'row':None,'column':None,'status':'outside_raster','message':'该位置在成果栅格范围之外','reference_resolution_m':native['resolution_m'],'actual_dates':[v[:10] for v in summary.get('actual_dates',[])],'source_year':summary.get('reference_year'),'limitations':['数值直接读取归档原生GeoTIFF；不是从预览PNG或底图估计。']+product.get('limitations',[])}
        try:
            with rasterio.open(path) as ds:
                names=validate_grid(ds,native);base['crs']=ds.crs.to_string();x,y=transform('EPSG:4326',ds.crs,[lon],[lat]);row,col=ds.index(x[0],y[0])
                if not(0<=row<ds.height and 0<=col<ds.width):return base
                values=ds.read(window=Window(col,row,1,1))[:,0,0];bands={name:float(v) if np.isfinite(v) else None for name,v in zip(names,values)};base.update(row=int(row),column=int(col),bands=bands)
                inside=bands[native['inside_band']]==1;value=bands[native['value_band']];valid=inside and value is not None
                if native.get('valid_band'):valid=valid and bands[native['valid_band']]==1
                if native['category'] in ('classification','review'):
                    classes={int(c['code']):c.get('name',c.get('label')) for c in product.get('classes',[]) if c.get('code') not in ((0,10) if native['category']=='classification' else (0,))};valid=valid and value in classes
                    base.update(class_code=int(value) if valid else None,class_label=classes.get(int(value)) if valid else None,metric_label='跨产品类别核查（非增减）' if native['category']=='review' else '2024年度土地覆盖类别')
                else:base.update(index_id=summary['index_id'],metric_label=('Δ' if task['plan']['goal']=='change' else '')+summary['index_id'].upper())
                base.update(inside_aoi=inside,valid=bool(valid),value=value if valid else None,value_band=native['value_band'],status='valid' if valid else 'invalid' if inside else 'outside_aoi',message='已读取原生格网像元值' if valid else '该像元为未知或未通过质量规则，不当作0或非农田' if inside else '该像元中心在参考市界之外')
                return base
        except GeoError:raise
        except Exception:raise GeoError('INSPECTION_UNAVAILABLE','区域原生像元读取或坐标转换失败，未生成替代值',503)

    def _crop_reference(self,ident,lon,lat):
        import rasterio
        from rasterio.windows import Window
        product=self.store.agriculture_atlas.get(ident)
        base={'task_id':ident,'kind':'agriculture_atlas','product_sha256':product['product_sha256'],'coordinates':[lon,lat],'inside_aoi':False,'valid':False,'value':None,'bands':{},'row':None,'column':None,'status':'outside_raster','source_year':2024,'metric_label':'2024水稻产品类别','reference_notice':'名义20米，实际为EPSG4326角度格网；非当期作物实测','limitations':product['limitations']}
        with rasterio.open(self.store.agriculture_atlas.artifact(ident,'classification.tif')) as ds:
            if ds.crs.to_epsg()!=4326 or ds.count!=2 or ds.descriptions!=('classification','aoi_mask') or ds.nodata!=255:raise GeoError('CROP_NATIVE_CONTRACT','作物原生归档格网不符合声明',409)
            row,col=ds.index(lon,lat);base.update(crs=str(ds.crs),row=row,column=col)
            if not (0<=row<ds.height and 0<=col<ds.width):return base
            values=ds.read(window=Window(col,row,1,1))[:,0,0];code=int(values[0]);inside=int(values[1])==1;valid=inside and code in (0,1)
            base.update(inside_aoi=inside,valid=valid,value=code if valid else None,class_code=code if valid else None,class_label={0:'产品非水稻类别',1:'水稻类别参考'}.get(code) if valid else None,bands={'classification':code,'aoi_mask':int(values[1])},status='valid' if valid else 'invalid' if inside else 'outside_aoi')
            return base
