"""Small business forms backed by actual collection and threshold operators."""
import copy
from spectral_indices import available_indices,specification
from agriculture_catalog import raster_sources
from agriculture_services import ready
from raster_processing_services import VERSION

def field(key,label,type='number',default=None,options=None,required=False,help=''):
 value={'key':key,'label':label,'type':type,'required':required,'help':help}
 if default is not None:value['default']=default
 if options is not None:value['options']=options
 return value

def templates(store):
 from application_services import source_profiles
 index_specs={item['id']:specification(item['id']) for item in available_indices()}
 temporal={'id':'temporal_analysis','name':'时段影像分析','category':'时序与影像处理','description':'按同一传感器的多日期有效观测生成指数合成，或查看所选时段的线性趋势。','featured':True,'ready':ready(),'scope':'selected_aoi','scope_label':'明确地图小范围，单边≤0.05°；最多183天、6个独立观测日期','form':{'requires_bbox':True,'max_bbox_span_deg':.05,'period_mode':'single','dataset_required':False,'fields':[field('algorithm_id','分析方法','select','temporal_median',[{'value':'temporal_median','label':'时段中位数合成'},{'value':'temporal_mean','label':'时段均值合成'},{'value':'temporal_trend','label':'指数时序趋势（至少3日期）'}]),field('index_id','分析指标','select','ndvi',[{'value':id,'label':spec['label']} for id,spec in index_specs.items()]),field('source_id','影像来源','select','auto',[{'value':'auto','label':'自动选择适用传感器'},{'value':'pc_sentinel2_l2a','label':'Sentinel-2'},{'value':'pc_landsat_c2_l2','label':'Landsat 8/9'}],help='一次只使用一种传感器；不混合S2与Landsat，红边NDRE仅适用S2。'),field('scene_limit','最多观测日期','number',4,help='2至6个独立UTC日期；趋势至少3日期。候选超限或获取失败会说明，不用单景代替。'),field('cloud','场景云量上限（%）','number',40,help='0至100，逐像元仍独立进行SCL或QA检查；不是无云保证。')]},'defaults':{'context':{'period':{'start':'2025-10-01','end':'2025-11-30'}},'parameters':{'algorithm_id':'temporal_median','index_id':'ndvi','source_id':'auto','scene_limit':4,'cloud':40}},'method':{'name':'多日期原生指数集合统计','processing_family':'temporal','processing_algorithm_ids':['temporal_median','temporal_mean','temporal_trend'],'index_id':'ndvi','index_specs':index_specs,'index_profiles':{id:source_profiles(id) for id in index_specs},'version':VERSION},'outputs':['合成值或每天指数变化斜率','逐像元有效观测次数','实际观测日期和逐景统计CSV','GeoTIFF、地图及来源包'],'limitations':['合成是所选有效观测的统计，不是逐日连续监测或独立物候/产量模型。','每个合成像元至少2次有效观测；趋势至少3个独立日期，不足支持保持未知。','逐景变化可能受观测空间支持变化影响，共同支持统计单独给出。']}
 sources={key:value for key,value in raster_sources(store,'zonal_statistics').items() if value['source_type']=='production' and value['goal']=='ndvi'}
 adaptive={'id':'adaptive_segmentation','name':'自适应阈值分区','category':'时序与影像处理','description':'对已有真实指数成果计算Otsu阈值，输出连通分区与格网条件面积。','featured':True,'ready':ready(),'scope':'archived_raster','scope_label':'沿用明确所选的成功单期原生光谱成果、范围和实际日期','form':{'requires_bbox':False,'period_mode':'none','dataset_required':False,'fields':[field('source_ref','已有指数成果','select',options=[{'value':key,'label':value['label']} for key,value in sources.items()],required=True),field('direction','保留的指数组','select','above',[{'value':'above','label':'不低于自动阈值'},{'value':'below','label':'低于自动阈值'}]),field('min_patch_pixels','最小连通斑块像元数','number',1,help='1至1000；为1时保留所有斑块，提高会移除细小信号并单独报告。')]},'defaults':{'context':{},'parameters':{'direction':'above','min_patch_pixels':1}},'method':{'name':'128分箱Otsu阈值与四邻域连通分区','processing_algorithm_id':'otsu_segmentation','raster_sources':copy.deepcopy(sources),'version':VERSION},'outputs':['自动阈值和直方图分离度','全部保留连通分区及面积','原始命中/移除像元数','GeoJSON、GeoTIFF、CSV和来源包'],'limitations':['指数分区不是已训练的水体、农田、作物或灾损分类。','无有效数据或指数无差异时不能自动给出阈值；不人为填充阈值。','面积为原生投影格网条件面积，斑块不是权属地块。']}
 return [temporal,adaptive]
