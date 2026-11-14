"""Business answer cards built only from completed typed execution outputs."""
import copy
from spectral_indices import specification


def build_answer(question,job):
    r=job['result'];binding=job['binding'];kind=binding['type'];intent=job.get('intent');ref=job.get('result_ref');prefix=ref.get('url') if ref else '/api/executions/'+job['id']
    answer={'question_id':question['id'],'title':'真实执行结果','summary':job['summary'],'metrics':[],'source_year':None,'boundary_year':None,'warnings':[],'chart':None,'map_effect':'show','result_ref':copy.deepcopy(ref),'downloads':[],'detail_ref':prefix,'execution_ref':{'id':job['id'],'url':'/api/executions/'+job['id']},'provenance':{'execution_id':job['id'],'binding':copy.deepcopy(binding),'planner_mode':'bounded_rules_no_general_llm'}}
    if job.get('plan_ref'):answer['data_plan_ref']=copy.deepcopy(job['plan_ref'])
    def metric(id,label,value,unit='',precision=2):return {'id':id,'label':label,'value':value,'unit':unit,'precision':precision}
    read_only=intent in ('source_quality','download_results')
    if kind=='cropland':
        from cropland_services import product_profile
        s=r['summary'];p=r['provenance']
        profile=product_profile(p.get('product',{}).get('id','worldcover_2021_v200'));year=profile['year'];code=profile['class_code']
        answer.update(title='选区耕作地标注 · '+str(year)+'参考',source_year=year,boundary_year=None,source_description=profile['name']+' · 地图选区 · 原生分类产品',provenance={**p,'execution_id':job['id'],'binding':binding})
        answer['metrics']=[metric('cropland_ha','耕作地参考面积',s['cropland_ha'],'公顷',4),metric('cropland_mu','折合面积',s['cropland_mu'],'亩',3),metric('patch_count','连通分类图斑',s['patch_count'],'个',0),metric('coverage_ratio','有效分类覆盖',s['coverage_ratio']*100,'%',2)]
        answer['summary']='已读取选区全部原生分类像元，将%d年产品中的耕作地类别%d提取为%d个连通图斑，共%.4f公顷（%.3f亩）。图斑可在地图点击核查并下载；产品分类存在错分和漏分，不保证所有真实农田均被识别。'%(year,code,s['patch_count'],s['cropland_ha'],s['cropland_mu'])
        answer['warnings']=copy.deepcopy(r.get('report',{}).get('limitations',r.get('limitations',p.get('limitations',[])))) or [str(year)+'年分类产品提取，不是当期遥感影像新分类；连通图斑不是确权地块。','有效覆盖率不是分类精度；未经本地独立样本验证。']
        if s.get('nodata_pixel_count',0):answer['warnings'].append('选区存在%d个缺测或云遮挡等未知像元；未知区域不能当作非农田。'%s['nodata_pixel_count'])
        formats={'geojson':'geojson','tif':'geotiff','csv':'csv','png':'png','json':'json'}
        answer['downloads']=[{'label':a['name'],'url':a['url'],'type':a.get('type') or formats.get(a['url'].rsplit('.',1)[-1],'json')} for a in r.get('artifacts',[]) if a.get('url')]
    elif kind=='raster_processing':
        s=r['summary'];p=r['provenance'];algorithm=s['algorithm_id'];dates=s.get('actual_dates',p.get('actual_dates',[]));dates=list(dates.values()) if isinstance(dates,dict) else dates;index=s.get('index_label') or (s.get('index') or {}).get('label') or str(s.get('index_id','')).upper()
        answer.update(title={'temporal_median':'时段中位数合成','temporal_mean':'时段均值合成','temporal_trend':'指数时序趋势','otsu_segmentation':'自适应阈值分区'}[algorithm],source_year=int(str(dates[-1])[:4]) if dates else None,source_description='实际观测：'+' → '.join(str(value)[:10] for value in dates)+' · 明确选区',provenance={**p,'execution_id':job['id'],'binding':binding})
        valid=s.get('valid_pixel_count',s.get('valid_count',0));mean=s.get('mean_value',(s.get('statistics') or {}).get('mean'));ratio=s.get('valid_ratio',0)
        if algorithm=='otsu_segmentation':
            answer['metrics']=[metric('selected_area_ha','指数分区格网条件面积',s['selected_area_ha'],'公顷',4),metric('threshold','自动阈值',s['threshold'],'',6),metric('patch_count','连通分区',s['patch_count'],'个',0),metric('valid_ratio','源有效覆盖',ratio*100,'%',2)]
            answer['summary']='已对所选'+index+'计算Otsu自动阈值%.6f，保留%d个连通指数分区，共%.4f公顷原生格网条件面积；这是光谱分组，不是水体、农田或灾损认定。'%(s['threshold'],s['patch_count'],s['selected_area_ha'])
        else:
            trend=algorithm=='temporal_trend';minimum=s.get('min_observations',s.get('minimum_observations',3 if trend else 2))
            answer['metrics']=[metric('mean_value','平均指数变化斜率' if trend else '合成平均'+index,mean,'指数/日' if trend else '',7 if trend else 4),metric('scene_count','实际观测日期',s['scene_count'],'个',0),metric('valid_ratio','满足观测支持的覆盖',ratio*100,'%',2),metric('valid_count','有效输出像元',valid,'个',0)]
            answer['summary']='已按同一传感器的%d个实际观测日期完成%s；每个输出像元至少需要%d次独立有效观测，满足支持的覆盖为%.2f%%。不足支持保持未知，逐景与共同支持统计分别保留。'%(s['scene_count'],answer['title'],minimum,ratio*100)
        answer['warnings']=copy.deepcopy(r.get('report',{}).get('limitations',[]))+copy.deepcopy(r.get('report',{}).get('findings',[]))
        if s.get('quality_warning'):answer['warnings'].append(s['quality_warning'])
        answer['downloads']=[{'label':a['name'],'url':a['url'],'type':a.get('type','json')} for a in r.get('artifacts',[]) if a.get('url')]
    elif kind=='agriculture':
        s=r['summary'];p=r['provenance'];algorithm=s['algorithm_id'];source=p.get('source_ref') or {}
        dates=p.get('actual_dates') or {};date_values=list(dates.values()) if isinstance(dates,dict) else dates
        date_label=' → '.join(str(value)[:10] for value in date_values) if date_values else str(p.get('source_year') or '见来源报告')
        answer.update(title='植被覆盖度估算' if algorithm=='fvc' else '地块遥感分区统计',source_year=p.get('source_year'),source_description='源成果：'+source.get('task_id','')+' · '+date_label,provenance={**p,'execution_id':job['id'],'binding':binding})
        answer['warnings']=copy.deepcopy(r.get('report',{}).get('limitations',[]))
        if algorithm=='fvc':
            mean=s['mean_fvc'];answer['metrics']=[metric('mean_fvc','平均植被覆盖度估算',mean*100 if mean is not None else None,'%',2),metric('valid_pixel_count','有效像元',s['valid_pixel_count'],'个',0),metric('valid_ratio','有效覆盖',s['valid_ratio']*100,'%',2),metric('clamped_pixel_count','端元外截断像元',s['clamped_low_count']+s['clamped_high_count'],'个',0)]
            answer['summary']='已对所选单期NDVI原生有效像元完成FVC估算，使用明确端元：裸土%.4f、高覆盖植被%.4f。%d个有效像元，%d个值截断到[0,1]；此结果是端元假设下的估算，端元未经本地校准。'%(s['soil_ndvi'],s['vegetation_ndvi'],s['valid_pixel_count'],s['clamped_low_count']+s['clamped_high_count'])
        else:
            answer['metrics']=[metric('zone_count','统计地块',s['zone_count'],'个',0),metric('unique_valid_area_ha','去重有效覆盖面积',s['unique_valid_area_ha'],'公顷',4),metric('partial_zone_count','部分覆盖地块',s['partial_zone_count'],'个',0),metric('outside_zone_count','未覆盖地块',s['outside_zone_count'],'个',0)]
            answer['summary']='已统计%d个地块：%d个完全位于源选区内，%d个部分覆盖，%d个未覆盖。逐地块统计可在地图点击或下载CSV查看；重叠地块合计可能重复，去重面积单独给出，未覆盖部分不外推。'%(s['zone_count'],s['full_zone_count'],s['partial_zone_count'],s['outside_zone_count'])
        formats={'geojson':'geojson','tif':'geotiff','csv':'csv','png':'png','json':'json'}
        answer['downloads']=[{'label':a['name'],'url':a['url'],'type':a.get('type') or formats.get(a['url'].rsplit('.',1)[-1],'json')} for a in r.get('artifacts',[]) if a.get('url')]
    elif kind=='production':
        s=r['summary'];p=r['provenance'];algorithm=s.get('index') or p.get('algorithm') or specification();index_id=algorithm['id'];index_label=algorithm['label'];mean=s.get('mean_index',s.get('mean_ndvi'));scenes=p.get('source_details',{}).get('scenes',{});dates={epoch:scene.get('datetime','')[:10] for epoch,scene in scenes.items()};change=s.get('mean_delta') is not None
        answer.update(title='选区两期'+index_label+'变化' if change and index_id!='ndvi' else '选区两期光谱变化' if change else '选区'+index_label+'分析',provenance={**p,'execution_id':job['id'],'binding':binding},source_year=int(dates['after'][:4]) if dates.get('after') else None,source_description='实际观测：'+(' → '.join(dates[k] for k in ('before','after') if k in dates))+' · 指定选区')
        if change:
            answer['metrics']=[metric('mean_delta','平均'+index_label+'变化',s['mean_delta'],'',4),metric('mean_ndvi' if index_id=='ndvi' else 'mean_index','末期平均'+index_label,mean,'',4),metric('valid_count','共同有效像元',s['valid_count'],'个',0),metric('selected_area_ha','阈值命中投影面积',s['selected_area_ha'],'公顷')]
        else:answer['metrics']=[metric('mean_ndvi' if index_id=='ndvi' else 'mean_index','平均'+index_label,mean,'',4),metric('valid_count','有效像元',s['valid_count'],'个',0),metric('valid_ratio','有效比例',s['valid_ratio']*100,'%'),metric('selected_area_ha','阈值命中投影面积',s['selected_area_ha'],'公顷')]
        calibration='实际STAC定标与QA位检查' if p.get('source_id')=='pc_landsat_c2_l2' or p.get('source_details',{}).get('quality_kind')=='landsat_qa' else 'XML定标与SCL检查'
        answer['summary']='系统已自动完成影像检索、'+calibration+'、所需波段与质量数据读取、共配准和'+index_label+'计算。选区内统计%d个原生参考格网像元，%d个%s有效；%d个命中条件，对应%.4f公顷投影网格面积。'%(s['native_pixel_count'],s['valid_count'],'共同' if change else '',s['selected_count'],s['selected_area_ha'])
        answer['warnings']=copy.deepcopy(r['report']['limitations'])+r['report']['findings']
        if s.get('quality_warning'):answer['warnings'].append(s['quality_warning'])
        answer['chart']={'title':'末期有效像元'+index_label+'分布','unit':'个','data':[{'label':'%.2f–%.2f'%(h['low'],h['high']),'value':h['count'],'color':'#78b7b1'} for h in s.get('index_histogram',s.get('ndvi_histogram',[]))]}
        answer['downloads']=[{'label':a['name'],'url':a['url'],'type':a['type']} for a in r.get('artifacts',[]) if a.get('url')]
    elif kind=='weather':
        c=r.get('current',{});units=r.get('units',{}).get('current',{})
        answer.update(title='指定位置天气模型',map_effect='none',result_ref=None,source_description='模型时间：'+str(c.get('time','以来源报告为准')))
        for key,label in [('temperature_2m','气温'),('precipitation','降水'),('wind_speed_10m','风速')]:
            if isinstance(c.get(key),(int,float)):answer['metrics'].append(metric(key,label,c[key],units.get(key,'')))
        answer['warnings']=['这是提供方实际返回的模型预报，不是田间站点实测，也不能直接认定灾损。'];answer['downloads']=[{'label':'真实预报与位置来源记录','url':'/api/executions/'+job['id'],'type':'json'}];answer['provenance']=copy.deepcopy(r.get('provenance',{'provider':'Open-Meteo','request':job.get('plan_snapshot',{}).get('tool',{}).get('inputs')}))
    elif kind=='discovery':
        entries=[item for source in r['results'] for item in source['items']]
        answer.update(title='真实影像目录检索',map_effect='none',result_ref=None,metrics=[metric('scene_count','实际命中条目',len(entries),'条',0)],source_description='仅目录发现 · 未读取像素或执行解译',warnings=['目录命中不代表选区完整覆盖、影像无云或已经分析。'],downloads=[{'label':'真实检索元数据与来源','url':'/api/data-searches/'+binding['task_id'],'type':'json'}])
        answer['chart']={'title':'各来源实际命中','unit':'条','data':[{'label':s['source_id'],'value':len(s['items']),'color':'#7fa7c6'} for s in r['results']]}
    elif kind=='analysis':
        answer.update(title='用户空间数据分析',metrics=[metric('feature_count','输出要素',r.get('feature_count',0),'个',0)],provenance=copy.deepcopy(r.get('provenance',{})),warnings=['按所绑定用户数据版本实际计算；输入真实性与业务属性由提供方负责。'],downloads=[{'label':'结果图层 GeoJSON','url':'/api/analysis/'+binding['task_id']+'/result.geojson','type':'geojson'},{'label':'统计与来源报告','url':'/api/analysis/'+binding['task_id'],'type':'json'}])
        s=r.get('summary') or {};counts=s.get('geometry_counts',{})
        if counts:
            names={'Point':'点','MultiPoint':'多点','LineString':'线','MultiLineString':'多线','Polygon':'面','MultiPolygon':'多面'}
            parts=[str(value)+'个'+names.get(kind,kind)+'要素' for kind,value in counts.items()]
            answer['summary']='已对“'+str(r.get('provenance',{}).get('dataset_name','所选图层'))+'”完成统计，包含'+'、'.join(parts)+'。结果图层、统计和输入版本记录可下载。'
            answer['chart']={'title':'几何类型构成','unit':'个','data':[{'label':names.get(kind,kind)+'要素','value':value,'color':'#577c99'} for kind,value in counts.items()]}
            if s.get('polygon_feature_count',0)>0:
                answer['metrics'].append(metric('area_sum_ha','面要素面积合计',s['area_sum_ha'],'公顷'))
                answer['warnings'].append(s.get('area_method','逐要素几何面积汇总，不作法定认定。'))
    if read_only:
        answer['title']='原执行来源与质量' if intent=='source_quality' else '下载原执行真实成果'
        answer['summary']=job['summary'];answer['map_effect']='none'
    if kind in ('cropland','production','analysis','agriculture','raster_processing'):
        from result_bundle_services import bundle_link
        answer['downloads'].append(bundle_link(kind,binding['task_id']))
    return answer
