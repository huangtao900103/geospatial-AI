"""Business forms for agricultural analyses of explicit archived raster results."""
import copy
from backend.agriculture.agriculture_services import VERSION as AGRICULTURE_VERSION

SOURCE_NAMES={'pc_sentinel2_l2a':'Sentinel-2','pc_landsat_c2_l2':'Landsat 8/9'}

def raster_sources(store,algorithm_id):
    result={}
    for kind in ('production','cropland'):
        if algorithm_id=='fvc' and kind!='production':continue
        service=getattr(store,kind)
        for task in service.list():
            if task.get('status')!='succeeded' or not task.get('result'):continue
            plan=task.get('plan') or {};data=task['result'];s=data.get('summary') or {};p=data.get('provenance') or {}
            index_id=s.get('index_id') or plan.get('index_id') or 'ndvi'
            if kind=='production':
                if plan.get('sampling')!='native' or plan.get('mode')!='sentinel_cog':continue
                if algorithm_id=='fvc' and (index_id!='ndvi' or plan.get('goal')!='ndvi'):continue
            try:service.artifact(task['id'],'analysis.tif' if kind=='production' else 'classification.tif')
            except Exception:continue
            dates=[str(v.get('datetime',''))[:10] for v in p.get('source_details',{}).get('scenes',{}).values() if isinstance(v,dict)]
            year=s.get('reference_year') or p.get('year');algorithm=s.get('index') or p.get('algorithm') or {}
            label=(algorithm.get('label') or index_id.upper())+' · '+SOURCE_NAMES.get(p.get('source_id',plan.get('source_id')), '公开影像') if kind=='production' else str((p.get('product') or {}).get('name','年度分类产品'))
            label+=' · '+(' → '.join(dates) if dates else str(year or '见来源日期'))+' · '+task['id'][-6:]
            key=kind+':'+task['id']
            result[key]={'source_ref':key,'source_type':kind,'task_id':task['id'],'kind':'spectral' if kind=='production' else 'classification','label':label,'index_id':index_id if kind=='production' else None,'goal':plan.get('goal'),'bbox':p.get('bbox') or p.get('selected_bbox') or (plan.get('aoi') or {}).get('bbox') or plan.get('bbox'),'year':year,'actual_dates':dates,'supported_fields':['classification'] if kind=='cropland' else ['after_index','delta_index'] if plan.get('goal')=='change' else ['after_index'],'threshold_range':algorithm.get('threshold_range',[-1,1]),'reference_resolution_m':algorithm.get('reference_resolution_m',s.get('nominal_resolution_m')),'input_sha256':p.get('input_sha256') or p.get('classified_window_sha256')}
    atlas=getattr(store,'agriculture_atlas',None)
    if atlas:
        from backend.analysis.regional_raster import descriptor
        seen=set()
        for product in atlas.list()['products']:
            if product.get('product_type') not in ('annual_classification','city_spectral_reference'):continue
            if product.get('product_type')=='annual_classification' and not product['id'].endswith('-cropland'):continue
            try:task,path=descriptor(atlas,product['id'])
            except Exception:continue
            plan=task['plan'];native=plan['native'];index=plan.get('index_id');goal=plan.get('goal')
            if algorithm_id=='fvc' and (native['category']!='spectral' or index!='ndvi' or goal!='ndvi'):continue
            physical=(product['source_product_id'],goal)
            if physical in seen:continue
            seen.add(physical);key='agriculture_atlas:'+product['id'];s=task['result']['summary'];p=task['result']['provenance'];default='classification' if native['category']=='classification' else 'delta_index' if goal=='change' else 'after_index'
            result[key]={'source_ref':key,'source_type':'agriculture_atlas','task_id':product['id'],'kind':native['category'],'label':product['title']+' · '+product['period_label'],'index_id':index,'goal':goal,'bbox':p['bbox'],'year':s.get('reference_year'),'actual_dates':s.get('actual_dates',[]),'supported_fields':[default],'default_field':default,'threshold_range':[-1,1],'reference_resolution_m':product.get('resolution_m'),'product_sha256':product['product_sha256'],'quality':product.get('quality')}
    return result

def templates(store):
    from backend.analysis.production_services import raster_available
    sources_fvc=raster_sources(store,'fvc');sources_zonal=raster_sources(store,'zonal_statistics')
    fields=[]
    for d in store.datasets():
        if d.get('source_type')!='user':continue
        full=store.get_dataset(d['id']);features=full.get('geojson',{}).get('features',[])
        if not features or any((f.get('geometry') or {}).get('type') not in ('Polygon','MultiPolygon') for f in features):continue
        fields.append({k:d.get(k) for k in ('id','name','sha256','version','feature_count','bbox')})
    def field(key,label,type='number',default=None,options=None,required=False,help=''):
        obj={'key':key,'label':label,'type':type,'required':required,'help':help}
        if default is not None:obj['default']=default
        if options is not None:obj['options']=options
        return obj
    def base(id,name,description,algorithm,sources):
        return {'id':id,'name':name,'category':'农业分析','description':description,'featured':True,'ready':raster_available(),'scope':'archived_raster','scope_label':'沿用明确选择的成功栅格成果、实际观测与原始范围；不继承新的地图框选','form':{'requires_bbox':False,'period_mode':'none','dataset_required':algorithm=='zonal_statistics','fields':[field('source_ref','已有栅格成果','select',options=[{'value':ref,'label':m['label']} for ref,m in sources.items()],required=True,help='只列真实成功且原生栅格文件可用的成果。')]},'defaults':{'context':{},'parameters':{}},'method':{'name':name,'agriculture_algorithm_id':algorithm,'version':AGRICULTURE_VERSION,'raster_sources':copy.deepcopy(sources)},'limitations':['本次计算使用所选成果的原始范围和日期，不是重新获取当期影像。','源数据质量与业务属性需核查；不能据此认定法定面积、作物产量或灾损。']}
    fvc=base('vegetation_cover','植被覆盖度估算','基于单期NDVI与明确端元，估算像元二分模型植被覆盖度。','fvc',sources_fvc)
    fvc['form']['fields'].extend([field('soil_ndvi','裸土NDVI端元',required=True,help='须明确填写[-1,1]；应按地区、季节和参考样本校准，平台不替您假定端元。'),field('vegetation_ndvi','高覆盖植被NDVI端元',required=True,help='须大于裸土端元，且在[-1,1]；结果对端元选择敏感。')])
    fvc['method'].update(formula='clip((NDVI - soil_ndvi) / (vegetation_ndvi - soil_ndvi), 0, 1)',unit='fraction')
    fvc['outputs']=['FVC估算分布与均值','端元与截断像元说明','原生GeoTIFF、CSV及完整来源包']
    fvc['limitations'].append('FVC为给定端元假设下的估算，不是地面实测、LAI或独立精度验证；超端元值会截断并单独报告。')
    zonal=base('field_zonal_statistics','地块分区统计','按用户地块汇总指数或分类面积，区分有效、未知与部分覆盖。','zonal_statistics',sources_zonal)
    zonal['form']['fields'].extend([field('field','统计内容','select','auto',[{'value':'auto','label':'按源成果自动选择'},{'value':'after_index','label':'单期 / 后期指数'},{'value':'delta_index','label':'两期差值（后减前）'}],help='年度分类源自动统计作物/耕作地类别；只有两期指数源可统计差值。'),field('threshold','指数命中阈值（≥，可选）',help='仅连续指数使用；命中面积为格网条件面积，不是作物分类或灾损认定。')])
    zonal['defaults']['parameters']['field']='auto';zonal['datasets']=fields
    zonal['outputs']=['逐地块均值、分位数与有效覆盖','分类参考面积或指数阈值面积','含统计属性的GeoJSON、CSV及来源包']
    zonal['limitations'].extend(['超出源选区的地块只统计交集，不用局部有效比例代表整块地完整覆盖。','重叠地块逐块统计可能重复计量；唯一覆盖统计与逐地块合计分别解释。'])
    return [fvc,zonal]
