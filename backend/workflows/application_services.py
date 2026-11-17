"""Versioned business templates that submit validated inputs to real execution.

Templates do not own an alternative engine. They freeze application provenance
before AssistantServices launches its existing typed execution lifecycle.
"""
import copy
import math
from datetime import date
from decimal import Decimal
from backend.core.geo_services import GeoError
from backend.analysis.production_services import raster_available
from backend.analysis.spectral_indices import available_indices,specification

VERSION='1.5'
OPERATORS=[{'value':'gt','label':'大于'},{'value':'gte','label':'大于等于'},{'value':'lt','label':'小于'},{'value':'lte','label':'小于等于'}]
OP_TEXT={'gt':'大于','gte':'不低于','lt':'小于','lte':'不高于'}
SINGLE={'period':{'start':'2025-11-01','end':'2025-11-30'}}
TWO={'periods':{'before':['2025-10-01','2025-10-31'],'after':['2025-11-01','2025-11-30']}}


def field(key,label,type='number',default=None,options=None,required=False,help=''):
    result={'key':key,'label':label,'type':type,'required':required,'help':help}
    if default is not None:result['default']=default
    if options is not None:result['options']=options
    return result


def source_profiles(index):
    from backend.catalog.algorithm_catalog import contract
    return {source:{'available':True,'reference_resolution_m':c['resolution']['reference_m'],'quality_policy':c['quality_policy'],'quality_note':'QA_PIXEL位0–5与所有非零QA_RADSAT排除；负反射率不clip' if source=='pc_landsat_c2_l2' else 'SCL保留4/5/6/7，原始DN按产品XML定标','required_bands':c['required_bands']} for source in ('pc_sentinel2_l2a','pc_landsat_c2_l2') if (c:=contract(source,index))['available']}


def spectral(id,name,description,index,category='生态与生产',two=False,threshold=None):
    method=specification(index)
    method['source_profiles']=source_profiles(index)
    parameters={'source_id':'auto'};fields=[field('source_id','分析数据源','select','auto',[{'value':'auto','label':'自动选择适用传感器'},{'value':'pc_sentinel2_l2a','label':'Sentinel-2 · 10/20m'},{'value':'pc_landsat_c2_l2','label':'Landsat 8/9 · 30m'}],help='自动模式优先S2；2015年前窗口选择Landsat8/9，不隐式做跨传感器合成。')]
    if threshold is not None:parameters.update(threshold=threshold,operator='gt')
    fields.extend([field('threshold','变化幅度阈值' if two else '指数筛选阈值（可选）',default=threshold,help='阈值面积为投影格网条件面积，不能直接作业务分类面积。'),field('operator','筛选关系','select',default='gt',options=OPERATORS)])
    if two:
        parameters['direction']='decrease';fields.append(field('direction','变化方向','select','decrease',[{'value':'decrease','label':'下降幅度'},{'value':'increase','label':'上升幅度'},{'value':'absolute','label':'绝对变化幅度'}]))
    return {'id':id,'name':name,'category':category,'description':description,'featured':True,'ready':raster_available(),'scope':'selected_aoi','scope_label':'地图明确选择的小范围；单边不超过0.05°','form':{'requires_bbox':True,'max_bbox_span_deg':.05,'period_mode':'two' if two else 'single','dataset_required':False,'fields':fields},'defaults':{'context':copy.deepcopy(TWO if two else SINGLE),'parameters':parameters},'outputs':['配准指数分布','有效覆盖与统计','实际观测日期','GeoTIFF与来源报告'],'method':{'name':method['name'],'index_id':index,'formula':method['formula'],'reference_resolution_m':method['reference_resolution_m'],'source_profiles':method['source_profiles'],'version':method['version']},'limitations':[v for v in method['limitations'] if not v.startswith('SCL')]+['S2采用产品XML定标和SCL；Landsat8/9采用实际scale/offset及QA_PIXEL/RADSAT，不能混用。','请求日期窗口内各选择一景实际影像，不是月平均或全域业务分类。']}


class ApplicationServices:
    def __init__(self,store):self.store=store
    def catalog(self):
        baseline={'id':'land_resources','name':'土地覆盖统计','category':'资源底数','description':'查看已验证的仙桃历史土地覆盖构成与耕作地参考面积。','featured':True,'ready':raster_available(),'scope':'fixed_region','scope_label':'固定：仙桃公开参考市界；2021分类产品 × 2017参考界','form':{'requires_bbox':False,'period_mode':'none','dataset_required':False,'fields':[field('metric','统计内容','select','cropland',[{'value':'cropland','label':'耕作地参考面积'},{'value':'composition','label':'土地覆盖构成'},{'value':'water','label':'永久水体参考'},{'value':'tree','label':'树木覆盖参考'}])]},'defaults':{'context':{},'parameters':{'metric':'cropland'}},'outputs':['面积与构成图','全市配准分布','原生分类GeoTIFF','逐行复算与来源报告'],'method':{'name':'ESA WorldCover2021 v200原生类别统计','source_year':2021,'boundary_year':2017},'limitations':['固定验证区域与历史年度，不承接当前地图选区，不是当前官方耕地调查。']}
        apps=[baseline,spectral('vegetation_status','植被长势观察','查看选区植被光谱状态、有效覆盖和空间分布。','ndvi'),spectral('vegetation_change','植被变化排查','比较两个观测窗口，定位植被指数变化线索。','ndvi',two=True,threshold=.1),spectral('water_clues','水体光谱线索','以绿光与短波红外对比查看水体相关光谱线索。','mndwi',category='生态调查',threshold=0),spectral('vegetation_moisture','植被水分光谱','查看与植被含水相关的光谱差异，辅助筛查。','ndmi',category='生态调查')]
        apps.append({'id':'imagery_search','name':'可用影像查询','category':'数据服务','description':'按选区和日期实际检索已接入卫星影像目录。','featured':True,'ready':True,'scope':'selected_aoi','scope_label':'地图选区；返回候选目录，不执行像素解译','form':{'requires_bbox':True,'max_bbox_span_deg':None,'period_mode':'single','dataset_required':False,'fields':[field('source_id','影像来源','select','all',[{'value':'all','label':'全部已接入目录'},{'value':'sentinel2','label':'Sentinel-2光学'},{'value':'sentinel1','label':'Sentinel-1 RTC雷达目录'},{'value':'landsat','label':'Landsat历史光学目录'}])]},'defaults':{'context':copy.deepcopy(SINGLE),'parameters':{'source_id':'all'}},'outputs':['真实目录候选','获取日期与来源','检索记录JSON'],'method':{'name':'固定STAC集合时空查询'},'limitations':['目录命中不等于像素已经下载、区域完整覆盖或分析完成；S1 RTC像素需提供方授权。']})
        apps.append({'id':'weather','name':'区域天气参考','category':'数据服务','description':'查询选区中心对应的当前天气模型与预报。','featured':True,'ready':True,'scope':'selected_aoi','scope_label':'选区中心的模型请求位置，不是区域气象站实测','form':{'requires_bbox':True,'max_bbox_span_deg':None,'period_mode':'none','dataset_required':False,'fields':[]},'defaults':{'context':{},'parameters':{}},'outputs':['当前模型指标','预报与位置来源记录'],'method':{'name':'Open-Meteo当前天气模型'},'limitations':['不提供历史气象重建，不用天气模型认定灾损。']})
        datasets=[d for d in self.store.datasets() if d.get('source_type')=='user']
        apps.append({'id':'layer_summary','name':'业务图层统计','category':'资源底数','description':'对用户导入的GeoJSON进行要素、范围与几何统计。','featured':True,'ready':True,'scope':'user_dataset','scope_label':'明确选择的用户数据版本；合成教学数据不可作业务结果','form':{'requires_bbox':False,'period_mode':'none','dataset_required':True,'fields':[]},'defaults':{'context':{},'parameters':{}},'outputs':['要素与几何汇总','结果图层','来源与版本记录'],'method':{'name':'用户GeoJSON空间汇总'},'limitations':['导入不证明业务属性或权属真实；面积为算法声明的几何近似。'],'datasets':[{k:d.get(k) for k in ('id','name','sha256','version','feature_count','bbox')} for d in datasets]})
        lab=spectral('spectral_lab','专业光谱分析','按明确指数、日期和选区执行已注册光谱算法。','ndvi',category='专业工具');lab['featured']=False;lab['form']['fields'].insert(0,field('index_id','光谱指数','select','ndvi',[{'value':i['id'],'label':i['label']+' · '+str(i['reference_resolution_m'])+'m参考'} for i in available_indices()]));lab['defaults']['parameters']['index_id']='ndvi'
        lab['method']['index_profiles']={i['id']:source_profiles(i['id']) for i in available_indices()}
        lab['method']['index_specs']={i['id']:specification(i['id']) for i in available_indices()}
        apps.append(lab)
        crop=spectral('crop_spectral','植被与土壤背景分析','按植被覆盖及土壤背景条件选择EVI2、SAVI、MSAVI2或GNDVI，查看选区光谱差异。','evi2',category='农业分析')
        crop['form']['fields'].insert(0,field('index_id','分析算法','select','evi2',[{'value':id,'label':specification(id)['label']} for id in ('evi2','savi','msavi2','gndvi')]))
        crop['defaults']['parameters']['index_id']='evi2';crop['method']['index_profiles']={id:source_profiles(id) for id in ('evi2','savi','msavi2','gndvi')}
        crop['method']['index_specs']={id:specification(id) for id in ('evi2','savi','msavi2','gndvi')}
        apps.extend([crop,spectral('crop_rededge','红边叶绿素线索','使用Sentinel-2 B8A与B05原生20米波段计算NDRE，辅助查看植被红边差异。','ndre',category='农业分析'),spectral('soil_spectral','裸土光谱线索','计算BSI，辅助查看裸土与植被光谱差异，不直接判定土壤类型或肥力。','bsi',category='农业分析')])
        from backend.agriculture.agriculture_catalog import templates as agriculture_templates
        apps.extend(agriculture_templates(self.store))
        from backend.analysis.raster_processing_catalog import templates as processing_templates
        apps.extend(processing_templates(self.store))
        from backend.analysis.cropland_services import product_profile
        profiles={id:product_profile(id) for id in ('worldcover_2021_v200','io_lulc_2024')}
        apps.insert(0,{'id':'cropland_delineation','name':'选区耕作地标注','category':'资源底数','description':'选择年度分类产品，自动提取选区耕作地图斑、面积与GIS成果。','featured':True,'ready':raster_available(),'scope':'selected_aoi','scope_label':'地图选区，单边≤0.05°；按明确年度分类产品提取','form':{'requires_bbox':True,'max_bbox_span_deg':.05,'period_mode':'none','dataset_required':False,'fields':[field('product','分类产品与年度','select','io_lulc_2024',[{'value':'io_lulc_2024','label':'IO Annual LULC · 2024年 · 10米'},{'value':'worldcover_2021_v200','label':'ESA WorldCover · 2021年 · 10米'}],help='不同产品类别和方法不同，不直接作年度增减比较；均不代表当期实况。')]},'defaults':{'context':{},'parameters':{'product':'io_lulc_2024'}},'outputs':['全部耕作地连通图斑','逐图斑面积与选区汇总','有效覆盖与未知区域','完整成果包、GeoJSON、GeoTIFF、CSV及来源报告'],'method':{'name':'公开年度分类产品原生提取','reference_resolution_m':10,'version':'cropland-product-patches-v2','product_profiles':profiles},'limitations':['年度公开分类产品提取，不是当期影像新识别；分类存在漏分和错分，未做本地独立精度验证。','不同产品的类别定义和面积方法分别保留，不能将跨产品面积差直接解释为耕地增减。','连通图斑不是确权地块，不代表法定耕地、永久基本农田或高标准农田。']})
        from backend.agriculture.crop_reference_services import SOURCE_ID as CROP_SOURCE,VERSION as CROP_VERSION,LIMITATIONS as CROP_LIMITS
        apps.append({'id':'crop_reference_analysis','name':'水稻分类产品分析','category':'农业分析','description':'在数据覆盖区内，按地图选区或市县参考边界统计2024年水稻分布与面积，可跨地市复用。','featured':True,'ready':raster_available(),'scope':'selected_aoi','scope_label':'地图选区单边≤0.05°；行政全域最多2000万原生像元，边界来源随成果归档','form':{'requires_bbox':True,'max_bbox_span_deg':.05,'period_mode':'none','dataset_required':False,'fields':[field('analysis_scope','范围方式','select','selected_aoi',[{'value':'selected_aoi','label':'地图选区'},{'value':'administrative','label':'市县参考范围'}]),field('region','市县全名或行政编码','text',help='例如：天门市、潜江市。精确匹配参考边界；同名或缺失时不自动代选。'),field('source_id','数据来源与年度','select',CROP_SOURCE,[{'value':CROP_SOURCE,'label':'GlobalRice20 · 2024年 · ESA公开COG'}],required=True)]},'defaults':{'context':{},'parameters':{'source_id':CROP_SOURCE,'analysis_scope':'selected_aoi','region':''}},'method':{'name':'原生水稻类别1 / 逐行WGS84椭球面积','crop_reference':True,'version':CROP_VERSION},'outputs':['水稻类别参考面积与未知面积','配准分类地图','原生GeoTIFF、CSV及来源包'],'limitations':list(CROP_LIMITS)})
        for app in apps:
            kind,label=('historical_crop_product','年度作物产品分析') if app['id']=='crop_reference_analysis' else ('raster_processing','时序与影像处理') if app['method'].get('processing_family') or app['method'].get('processing_algorithm_id') else ('derived_raster','已有栅格农业分析') if app['method'].get('agriculture_algorithm_id') else ('historical_product','年度分类提取') if app['id']=='cropland_delineation' else ('fixed_historical_region','固定历史验证范围') if app['id']=='land_resources' else ('catalog_only','仅影像目录检索') if app['id']=='imagery_search' else ('weather_model','天气模型预报') if app['id']=='weather' else ('user_vector','用户图层统计') if app['id']=='layer_summary' else ('spectral_analysis','光谱指标分析')
            app['capability_kind']=kind;app['capability_label']=label
            app['readiness_note']='工具已实现，数据覆盖和质量在实际运行时核验。' if app['ready'] else '本机运行依赖尚未就绪。'
        from backend.catalog.business_scenarios import catalog as business_catalog

        return {'version':VERSION,'applications':apps,'business_scenarios':business_catalog(),'workflow':['选择专题','确认范围与日期','运行分析','查看结果与来源','下载或再次运行'],'limitations':['可执行专题与待对接行业系统分开；本地工作空间不代表省域数据已全部接入。']}
    def get(self,id):
        for app in self.catalog()['applications']:
            if app['id']==id:return app
        raise GeoError('APPLICATION_NOT_FOUND','没有此可执行专题',404)
    @staticmethod
    def bbox(value,small):
        if not isinstance(value,list) or len(value)!=4 or any(isinstance(v,bool) or not isinstance(v,(int,float)) or not math.isfinite(v) for v in value):raise GeoError('INVALID_APPLICATION_AREA','请在地图明确选择分析范围',422)
        w,s,e,n=value
        if not (-180<=w<e<=180 and -80<=s<n<=80):raise GeoError('INVALID_APPLICATION_AREA','范围须为合法WGS84矩形，顺序为西、南、东、北',422)
        if small and (e-w>.050001 or n-s>.050001):raise GeoError('APPLICATION_AREA_LIMIT','此遥感专题单边限0.05°；请缩小选区，不能用小窗替代全市/全省分析',422)
        return list(value)
    @staticmethod
    def period(value):
        if not isinstance(value,dict) or set(value)!={'start','end'}:raise GeoError('APPLICATION_PERIOD_REQUIRED','请完整填写开始和结束日期',422)
        try:
            if any(not isinstance(v,str) or len(v)!=10 for v in value.values()):raise ValueError()
            a,b=date.fromisoformat(value['start']),date.fromisoformat(value['end'])
        except (ValueError,TypeError):raise GeoError('INVALID_APPLICATION_PERIOD','日期须为有效YYYY-MM-DD',422)
        if a>b or (b-a).days>183:raise GeoError('INVALID_APPLICATION_PERIOD','日期开始不能晚于结束，每窗口最多183天',422)
        return {'start':a.isoformat(),'end':b.isoformat()}
    def prepare(self,body):
        if not isinstance(body,dict) or set(body)-{'application_id','context','parameters','request_id'}:raise GeoError('INVALID_APPLICATION_RUN','专题运行字段不支持',400)
        from backend.analysis.cropland_services import product_profile
        app=self.get(body.get('application_id'));raw_context=body.get('context',{});raw_parameters=body.get('parameters',{})
        if not isinstance(raw_context,dict) or set(raw_context)-{'bbox','period','periods','dataset_id','observation_id'}:raise GeoError('INVALID_APPLICATION_CONTEXT','专题上下文只接受范围、日期与用户数据',400)
        if not isinstance(raw_parameters,dict):raise GeoError('INVALID_APPLICATION_PARAMETERS','专题参数须为对象',400)
        if app['id']=='cropland_delineation' and 'product' not in raw_parameters:raise GeoError('APPLICATION_PRODUCT_REQUIRED','请明确选择分类产品与年度后运行；不会沿用旧表单并静默切换产品。',422)
        allowed={f['key'] for f in app['form']['fields']}
        if set(raw_parameters)-allowed:raise GeoError('INVALID_APPLICATION_PARAMETERS','参数不属于当前专题',400)
        if not app['ready']:raise GeoError('APPLICATION_UNAVAILABLE','当前环境尚未具备此专题运行依赖',503)
        parameters={**app['defaults']['parameters'],**copy.deepcopy(raw_parameters)};context={};form=app['form']
        if app['id']=='crop_reference_analysis' and parameters.get('analysis_scope')=='administrative':
            if 'bbox' in raw_context:raise GeoError('APPLICATION_SCOPE_CONFLICT','行政参考范围不能同时提交地图矩形',422)
        elif form['requires_bbox']:context['bbox']=self.bbox(raw_context.get('bbox'),form.get('max_bbox_span_deg') is not None)
        elif 'bbox' in raw_context:raise GeoError('APPLICATION_SCOPE_CONFLICT','此专题使用固定验证范围或用户图层，不能继承地图选区',422)
        if form['period_mode']=='single':
            if 'periods' in raw_context:raise GeoError('APPLICATION_PERIOD_CONFLICT','单期专题只接受一个日期窗口',422)
            context['period']=self.period(raw_context.get('period'))
        elif form['period_mode']=='two':
            if 'period' in raw_context:raise GeoError('APPLICATION_PERIOD_CONFLICT','两期专题须分别填写前后观测窗口',422)
            value=raw_context.get('periods')
            if not isinstance(value,dict) or set(value)!={'before','after'}:raise GeoError('APPLICATION_PERIOD_REQUIRED','请完整填写前后两个观测窗口',422)
            periods={}
            for epoch in ('before','after'):
                pair=value[epoch]
                if not isinstance(pair,list) or len(pair)!=2:raise GeoError('INVALID_APPLICATION_PERIOD','每个观测窗口须含开始和结束日期',422)
                period=self.period(dict(zip(('start','end'),pair)));periods[epoch]=[period['start'],period['end']]
            if periods['before'][1]>=periods['after'][0]:raise GeoError('APPLICATION_PERIOD_CONFLICT','前后观测窗口不能重叠或颠倒',422)
            context['periods']=periods
        elif 'period' in raw_context or 'periods' in raw_context:raise GeoError('APPLICATION_PERIOD_CONFLICT','此专题不接收观测日期，固定年度或当前模型口径会明确显示',422)
        if form['dataset_required']:
            id=raw_context.get('dataset_id')
            if not isinstance(id,str) or not id:raise GeoError('APPLICATION_DATASET_REQUIRED','请选择已导入的用户GeoJSON',422)
            dataset=self.store.get_dataset(id)
            if dataset.get('source_type')!='user':raise GeoError('APPLICATION_REQUIRES_USER_DATA','合成教学数据不能作此业务专题输入',422)
            context['dataset_id']=id
        elif 'dataset_id' in raw_context:raise GeoError('APPLICATION_SCOPE_CONFLICT','此专题不接受用户图层替代影像或固定区域',422)
        if 'observation_id' in raw_context:
            if app['method'].get('processing_family') or not app['method'].get('index_id') or form['period_mode']!='single':raise GeoError('PIN_REQUIRES_SINGLE_PERIOD','影像资源绑定仅用于单期光谱专题',422)
            obs=self.store.observations.get(raw_context['observation_id'])
            if not obs['stages']['analysis_ready']:raise GeoError('OBSERVATION_NOT_EXECUTABLE','资源尚不满足可执行波段/传感器条件',422)
            if parameters.get('source_id','auto') not in ('auto',obs['source_id']):raise GeoError('SOURCE_CONFLICT','所选来源与绑定资源不一致',422)
            parameters['source_id']=obs['source_id'];context['observation_id']=obs['id']

        for f in form['fields']:
            key=f['key'];value=parameters.get(key,f.get('default'))
            if f.get('required') and value in (None,''):raise GeoError('APPLICATION_PARAMETER_REQUIRED','请填写或选择'+f['label'],422)
            if f['type']=='select':
                if value not in [o['value'] for o in f['options']]:raise GeoError('INVALID_APPLICATION_PARAMETERS',f['label']+'选择无效',422)
                parameters[key]=value
            elif f['type']=='text':
                if value is None:value=''
                if not isinstance(value,str) or len(value)>30:raise GeoError('INVALID_APPLICATION_PARAMETERS',f['label']+'须为30字以内文本',422)
                parameters[key]=value.strip()
            elif f['type']=='number' and value not in (None,''):
                if isinstance(value,bool) or not isinstance(value,(int,float)) or not math.isfinite(value):raise GeoError('INVALID_APPLICATION_PARAMETERS',f['label']+'须为有限数值',422)
                parameters[key]=value
        id=app['id'];method=copy.deepcopy(app['method'])
        threshold=parameters.get('threshold')
        if threshold in (None,''):
            parameters.pop('threshold',None);parameters.pop('operator',None)
        else:
            if not method.get('agriculture_algorithm_id'):
                spec=specification(parameters.get('index_id',method.get('index_id','ndvi')));limits=spec.get('change_threshold_range',[0,2]) if form['period_mode']=='two' else spec.get('threshold_range',[-1,1])
                if not (limits[0]<threshold<=limits[1] if form['period_mode']=='two' else limits[0]<=threshold<=limits[1]):raise GeoError('INVALID_APPLICATION_THRESHOLD',spec['label']+'阈值须在声明范围内：'+str(limits),422)
        if method.get('processing_family')=='temporal':
            algorithm=parameters['algorithm_id'];index=parameters['index_id'];period=context['period'];inputs={'algorithm_id':algorithm,'source_id':parameters['source_id'],'index_id':index,'bbox':context['bbox'],'start':period['start'],'end':period['end'],'scene_limit':parameters['scene_limit'],'cloud':parameters['cloud']}
            plan=self.store.raster_processing.prepare(inputs);context.update(processing_algorithm=algorithm,index_id=index,source_id=parameters['source_id'],scene_limit=parameters['scene_limit'],cloud=parameters['cloud'])
            method.update(index_id=index,index_spec=specification(index),processing_algorithm_id=algorithm,input_fingerprint=plan['input_fingerprint']);method.pop('index_specs',None)
            question='对所选区域'+period['start']+'至'+period['end']+'的'+specification(index)['label']+'执行'+{'temporal_median':'时段中位数合成','temporal_mean':'时段均值合成','temporal_trend':'指数时序趋势'}[algorithm]
        elif method.get('processing_algorithm_id')=='otsu_segmentation':
            source=method['raster_sources'][parameters['source_ref']];inputs={'algorithm_id':'otsu_segmentation','source_task_id':source['task_id'],'direction':parameters['direction'],'min_patch_pixels':parameters['min_patch_pixels']}
            plan=self.store.raster_processing.prepare(inputs);context.update(processing_algorithm='otsu_segmentation',source_type='production',source_task_id=source['task_id'],direction=parameters['direction'],min_patch_pixels=parameters['min_patch_pixels'])
            method.update(source_ref=copy.deepcopy(plan['source']),input_fingerprint=plan['input_fingerprint']);method.pop('raster_sources',None);question='对所选指数成果进行Otsu自适应阈值分区'
        elif method.get('agriculture_algorithm_id'):
            source=method['raster_sources'][parameters['source_ref']];algorithm=method['agriculture_algorithm_id']
            inputs={'algorithm_id':algorithm,'source_type':source['source_type'],'source_task_id':source['task_id']}
            if algorithm=='fvc':
                inputs.update(soil_ndvi=parameters['soil_ndvi'],vegetation_ndvi=parameters['vegetation_ndvi']);question='估算所选NDVI成果的FVC植被覆盖度'
            else:
                selected_field=parameters.get('field','auto')
                inputs.update(dataset_id=context['dataset_id'],field=source.get('default_field','classification' if source['kind']=='classification' else 'after_index') if selected_field=='auto' else selected_field)
                if 'threshold' in parameters:inputs['threshold']=parameters['threshold']
                question='按所选地块对已有栅格成果进行分区统计'
            plan=self.store.agriculture.prepare(inputs)
            context.update({k:v for k,v in inputs.items() if k!='algorithm_id'})
            method.update(source_ref=copy.deepcopy(plan['source']),input_fingerprint=plan['input_fingerprint'],dataset=copy.deepcopy(plan.get('dataset')))
            method.pop('raster_sources',None)
        elif id=='land_resources':
            question={'cropland':'仙桃全市2021年耕作地参考面积有多少？','composition':'仙桃全市2021年的土地覆盖类型构成是什么？','water':'仙桃2021年永久水体参考面积有多少？','tree':'仙桃2021年树木覆盖参考面积有多少？'}[parameters['metric']]
        elif id=='crop_reference_analysis':
            context['source_id']=parameters['source_id']
            if parameters['analysis_scope']=='administrative':
                self.store.crop_reference.prepare({'source_id':parameters['source_id'],'scope':'administrative','region':parameters.get('region')})
                context['region']=parameters['region'];question='统计'+parameters['region']+'全域2024年水稻分布参考面积并上图'
            else:
                if parameters.get('region'):raise GeoError('APPLICATION_SCOPE_CONFLICT','地图选区模式不同时接受行政地区名称',422)
                question='按选区读取GlobalRice20 2024年水稻分布参考并统计面积'
        elif id=='cropland_delineation':
            product=product_profile(parameters['product']);context['cropland_product']=product['id']
            method.update(product=copy.deepcopy(product),source_year=product['year'],class_code=product['class_code'],area_method=product.get('area_method'))
            question='提取所选区域'+str(product['year'])+'年'+product['name']+'耕作地并全部标注图斑与面积'
        elif id=='imagery_search':
            name={'all':'已接入卫星','sentinel2':'Sentinel-2','sentinel1':'Sentinel-1 RTC','landsat':'Landsat'}[parameters['source_id']];p=context['period'];question='检索所选区域'+p['start']+'至'+p['end']+'可用的'+name+'影像目录'
        elif id=='weather':question='查询所选区域的当前天气模型'
        elif id=='layer_summary':question='统计所选用户数据图层的要素与空间汇总';method['dataset']={k:dataset.get(k) for k in ('id','name','sha256','version')}
        else:
            index=parameters.get('index_id',method['index_id']);spec=specification(index);method.update(name=spec['name'],index_id=index,formula=spec['formula'],reference_resolution_m=spec['reference_resolution_m'],version=spec['version'])
            method['index_spec']=copy.deepcopy(spec);method.pop('index_specs',None)
            if method.get('index_profiles'):method['source_profiles']=method.pop('index_profiles')[index]
            if form['period_mode']=='two':
                t=context['periods'];question='比较所选区域'+t['before'][0]+'至'+t['before'][1]+'与'+t['after'][0]+'至'+t['after'][1]+'的'+spec['label']+'变化'
            else:
                p=context['period'];question='分析所选区域'+p['start']+'至'+p['end']+'的'+spec['label']+'指数'
            if 'threshold' in parameters:
                subject={'decrease':'下降幅度','increase':'上升幅度','absolute':'绝对变化幅度'}.get(parameters.get('direction'),'指数')
                question+='；筛选'+subject+OP_TEXT[parameters['operator']]+format(Decimal(str(parameters['threshold'])),'f')+'的范围'
        if method.get('index_id') and not method.get('processing_family'):
            context['source_id']=parameters['source_id']
            if parameters['source_id']=='pc_landsat_c2_l2':
                from backend.analysis.landsat_reader import specification as landsat_specification
                actual=landsat_specification(method['index_id']);method.update(reference_resolution_m=30,name=actual['name'],quality_policy=actual['quality_policy'])
            if context.get('observation_id'):
                method['observation_ref']={'id':obs['id'],'metadata_sha256':obs['metadata_sha256'],'item_id':obs['item_id']}
        frozen={'id':id,'name':app['name'],'version':VERSION,'context':copy.deepcopy(context),'parameters':copy.deepcopy(parameters),'method':method,'scope_label':app['scope_label']}
        payload={'question':question,'context':context}
        if body.get('request_id') is not None:payload['request_id']=body['request_id']
        return payload,frozen
    def run(self,body):
        payload,frozen=self.prepare(body)
        return self.store.assistant.create(payload,application_ref=frozen)
