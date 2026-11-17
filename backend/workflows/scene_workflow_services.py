"""Executable, versioned business-layer workflows; no invented business inputs."""
import copy
import uuid
from datetime import datetime, timezone
from backend.core.geo_services import GeoError, analyse, finite, haversine

NAMES=dict(zip(
 'farmland high_standard tailwater grain disaster fishing machinery breeding supplies coldchain tenure eel sowing forest forest_change forest_risk rivers flood water_projects roads road_work logistics'.split(),
 ['耕地与资源底数','高标准农田','尾水治理','粮食与重要农产品','防灾减灾','长江禁捕','北斗农机监管','养殖场所','农资经销门店','冷链物流与加工','土地确权与二轮延包','黄鳝产业链','秋冬播生产','森林资源','林地变化核查','林草灾害巡查','河湖水域','防汛与积水核查','水利工程','公路与交通设施','工程与沿线巡查','运输与物流节点']))
VERSION='scene-ledger-1.1'

def scene_id(value):
 if value not in NAMES:raise GeoError('UNKNOWN_SCENE','未找到场景',404)
 return value

class SceneWorkflowServices:
 def __init__(self,store):self.store=store
 def catalog(self):
  return {'version':VERSION,'bindings':self.store.list('scene_binding'),'runs':[{k:a.get(k) for k in ('id','name','created_at','dataset_id','summary','provenance')} for a in self.store.list('analysis') if a.get('summary',{}).get('scene_id') in NAMES]}
 def _selection(self,sid,body,allowed):
  scene_id(sid)
  if not isinstance(body,dict) or set(body)-allowed:raise GeoError('INVALID_SCENE_INPUT','场景请求包含未知字段',400)
  ident=body.get('dataset_id')
  if not isinstance(ident,str) or not ident.strip() or len(ident)>100:raise GeoError('INVALID_SCENE_DATASET','数据集ID必须为不超过100字的非空字符串',422)
  dataset=self.store.get_dataset(ident)
  if dataset.get('source_type')!='user':raise GeoError('SCENE_DATA_REQUIRED','请选择用户上传的业务数据，不能以合成数据或遥感参考结果冒充业务台账',422)
  query=body.get('query','')
  if not isinstance(query,str) or len(query)>100:raise GeoError('INVALID_SCENE_INPUT','筛选词必须为不超过100字的字符串',422)
  query=query.strip();features=dataset['geojson']['features']
  if sid in ('high_standard','farmland','tenure') and any(f['geometry']['type'] not in ('Polygon','MultiPolygon') for f in features):raise GeoError('SCENE_GEOMETRY_REQUIRED','该场景须选择地块/工程面数据；点或线路不能计算项目面积',422)
  if sid=='machinery' and any(f['geometry']['type']!='LineString' for f in features):raise GeoError('TRACK_LINES_REQUIRED','农机轨迹统计须上传LineString轨迹；离散定位点须先按设备与时间整理为轨迹，不自动连线或推算作业面积',422)
  selected=[f for f in features if not query or query.casefold() in ' '.join(str(v) for v in f['properties'].values()).casefold()]
  if not selected:raise GeoError('SCENE_EMPTY_FILTER','没有匹配的记录，请调整筛选词；未生成空成果冒充完成',422)
  return dataset,features,selected,query
 def scope(self,sid,body):
  dataset,features,selected,query=self._selection(sid,body,{'dataset_id','query'})
  positions=[]
  def walk(value):
   if isinstance(value,list) and len(value)==2 and all(finite(v) for v in value):positions.append(value)
   else:
    for child in value:walk(child)
  for feature in selected:walk(feature['geometry']['coordinates'])
  bbox=[min(p[0] for p in positions),min(p[1] for p in positions),max(p[0] for p in positions),max(p[1] for p in positions)]
  return {'bbox':bbox,'selected_count':len(selected),'input_count':len(features),'dataset_id':dataset['id'],'version':dataset['version'],'sha256':dataset['sha256'],'query':query}
 def run(self,sid,body):
  allowed={'dataset_id','group_by','measure_field','measure_unit','entity_field','query'}
  dataset,features,selected,query=self._selection(sid,body,allowed)
  fields={k for f in features for k in f['properties']}
  options={}
  for key in ('group_by','measure_field','entity_field'):
   value=body.get(key,'')
   if not isinstance(value,str) or (value and value not in fields):raise GeoError('SCENE_FIELD_MISSING','所选业务字段不存在：'+key,422)
   options[key]=value
  unit=body.get('measure_unit','')
  if not isinstance(unit,str) or len(unit)>30:raise GeoError('INVALID_SCENE_INPUT','单位必须为不超过30字的字符串',422)
  if options['measure_field'] and not unit.strip():raise GeoError('MEASURE_UNIT_REQUIRED','数值汇总必须明确单位，避免将不同口径混算',422)
  options.update(measure_unit=unit.strip(),query=query)
  def unused_field(base):
   name=base;index=2
   while name in fields:name=base+'_'+str(index);index+=1
   fields.add(name);return name
  group=options['group_by'] or unused_field('__scene_no_group__')
  calculated,summary=analyse({'type':'FeatureCollection','features':selected},'spatial_summary',{'group_by':group})
  output={'type':'FeatureCollection','features':copy.deepcopy(selected)}
  computed_fields={}
  if summary['polygon_feature_count']:
   computed_fields['area_ha']=unused_field('scene_geometry_area_ha')
   for original,calculation in zip(output['features'],calculated['features']):
    if original['geometry']['type'] in ('Polygon','MultiPolygon'):original['properties'][computed_fields['area_ha']]=calculation['properties']['computed_area_ha']
  numeric=options['measure_field'];entity=options['entity_field']
  if entity:
   values=[f['properties'].get(entity) for f in selected];valid=[str(v) for v in values if v is not None and str(v).strip()]
   summary.update(entity_field=entity,entity_count=len(set(valid)),entity_missing_count=len(values)-len(valid))
  if numeric:
   vals=[f['properties'].get(numeric) for f in selected];valid=[v for v in vals if finite(v)]
   summary.update(measure_field=numeric,measure_unit=options['measure_unit'],measure_sum=sum(valid) if valid else None,measure_valid_count=len(valid),measure_missing_count=sum(v is None or v=='' for v in vals),measure_invalid_count=sum(not finite(v) and v is not None and v!='' for v in vals))
   if summary['measure_sum'] is not None and not finite(summary['measure_sum']):raise GeoError('NUMERIC_OVERFLOW','汇总超出有限数值范围',422)
  lengths=[]
  if any(f['geometry']['type']=='LineString' for f in selected):computed_fields['length_m']=unused_field('scene_geometry_length_m')
  for f in output['features']:
   if f['geometry']['type']=='LineString':
    coords=f['geometry']['coordinates'];length=sum(haversine(a,b) for a,b in zip(coords,coords[1:]));f['properties'][computed_fields['length_m']]=round(length,3);lengths.append(length)
  feature_groups={str(f['id']):'(未分组)' if f['properties'].get(group) is None else str(f['properties'][group]) for f in selected} if options['group_by'] else {}
  summary.update(feature_groups=feature_groups,computed_fields=computed_fields,scene_id=sid,scene_name=NAMES[sid],input_feature_count=len(features),filtered_out_count=len(features)-len(selected),line_count=len(lengths),line_length_m=round(sum(lengths),3) if lengths else None,group_by=options['group_by'] or None,options=options,
   limitations=['仅统计明确上传的业务台账；原始真实性和时间口径由提供方负责。','面积逐面相加，未做跨要素去重，不是法定面积或工程验收认定。','线路长度为球面折线长度，不推算作业面积、作业合规率或实时定位。','遥感参考与业务监管结论分别保留；平台未接通实时设备或监管专网。'])
  created=datetime.now(timezone.utc).isoformat(timespec='milliseconds');ident='analysis_'+uuid.uuid4().hex[:14]
  result={'id':ident,'name':NAMES[sid]+' · 台账分析','algorithm_id':'scene_summary','dataset_id':dataset['id'],'status':'succeeded','feature_count':len(selected),'created_at':created,'geojson':output,'summary':summary,
   'provenance':{'source_type':'user','source':'用户上传业务台账','dataset_id':dataset['id'],'dataset_name':dataset['name'],'dataset_version':dataset['version'],'sha256':dataset['sha256'],'algorithm_id':'scene_summary','algorithm_version':VERSION,'parameters':copy.deepcopy(options),'crs':'EPSG:4326','created_at':created,'review_status':'待业务核查','external_calls':False}}
  self.store.save('analysis',result)
  self.store.save('scene_binding',{'id':sid,'created_at':created,'dataset_id':dataset['id'],'dataset_sha256':dataset['sha256'],'options':options,'latest_analysis_id':ident})
  return result
