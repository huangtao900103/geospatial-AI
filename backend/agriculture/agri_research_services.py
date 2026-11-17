"""Immutable real observation series, sourced sample features and review records."""
import copy
import hashlib
import json
import math
import re
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path

from backend.core.geo_services import GeoError

VERSION='agri-research-1.0'
SLUG=re.compile(r'[a-z0-9][a-z0-9-]{2,95}')


def now():return datetime.now(timezone.utc).isoformat(timespec='milliseconds')
def canonical(value):return json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False).encode()
def digest(value):return hashlib.sha256(canonical(value)).hexdigest()
def sha_file(path):
    h=hashlib.sha256()
    with Path(path).open('rb') as f:
        for part in iter(lambda:f.read(1024*1024),b''):h.update(part)
    return h.hexdigest()
def text(value,name,maximum=300):
    if not isinstance(value,str) or not value.strip() or len(value)>maximum:raise GeoError('INVALID_RESEARCH_INPUT',name+'须为有效文本',422)
    return value.strip()
def finite(value):return isinstance(value,(int,float)) and not isinstance(value,bool) and math.isfinite(value)


class AgriResearchServices:
    def __init__(self,store):
        self.store=store
        self.root=Path(store.db_path).parent/'agri-research'/'series'
        self.hashes={};self.lock=threading.RLock()

    def _hash(self,path):
        stat=path.stat();key=(str(path),stat.st_mtime_ns,stat.st_size)
        if key not in self.hashes:self.hashes[key]=sha_file(path)
        return self.hashes[key]

    def _series(self,ident):
        if not isinstance(ident,str) or not SLUG.fullmatch(ident):raise GeoError('INVALID_SERIES_ID','时序标识无效',422)
        root=self.root/ident;path=root/'result.json'
        if root.is_symlink() or not path.is_file() or path.is_symlink() or path.stat().st_size>8*1024*1024:raise GeoError('SERIES_NOT_FOUND','时序尚未完成或未发布',404)
        raw=json.loads(path.read_text('utf-8'))
        if raw.get('id')!=ident or raw.get('status') not in ('ready','insufficient_dates'):raise GeoError('SERIES_UNAVAILABLE','时序尚未通过发布检查',422)
        return root,raw

    def _artifact(self,root,raw,name):
        if not isinstance(name,str) or len(name)>240 or name.startswith('/') or any(p in ('','..','.') for p in name.split('/')):raise GeoError('SERIES_FILE_INVALID','时序文件路径无效',400)
        declared={'result.json':None,'publication.json':None}
        for item in raw.get('artifacts',[]):
            if isinstance(item,str):declared[item]=None
            elif isinstance(item,dict):declared[item.get('file',item.get('path',item.get('name')))]=item.get('sha256')
        # Observation/change paths are declared in the immutable series itself.
        for item in raw.get('observations',[])+raw.get('changes',[]):
            for key in ('native_file','overlay_file'):
                if item.get(key):declared[item[key]]=item.get('native_sha256',item.get('sha256')) if key=='native_file' else item.get('overlay_sha256')
        if name not in declared:raise GeoError('SERIES_FILE_NOT_FOUND','时序未声明该文件',404)
        path=root/name
        parts=name.split('/')
        if not path.is_file() or any(root.joinpath(*parts[:i]).is_symlink() for i in range(1,len(parts)+1)) or root.resolve() not in path.resolve().parents:raise GeoError('SERIES_FILE_UNAVAILABLE','时序文件不可用',422)
        if declared[name] and self._hash(path)!=declared[name]:raise GeoError('SERIES_CONTENT_CHANGED','时序文件与生产记录不一致',409)
        return path

    def get(self,ident,expected=None):
        root,raw=self._series(ident)
        if (root/'publication.json').is_symlink():raise GeoError('SERIES_FILE_UNAVAILABLE','时序发布记录不可用',422)
        publication=json.loads((root/'publication.json').read_text()) if (root/'publication.json').is_file() else {}
        artifacts=[]
        for entry in raw.get('artifacts',[]):
            name=entry if isinstance(entry,str) else entry.get('file',entry.get('path',entry.get('name')))
            path=self._artifact(root,raw,name);artifacts.append({'file':name,'sha256':self._hash(path)})
        version=digest({'result_sha256':self._hash(root/'result.json'),'artifacts':artifacts,'publication':publication})
        if expected and expected!=version:raise GeoError('SERIES_VERSION_CHANGED','该时序版本已改变，请重新查看',409)
        summary=raw.get('sequence_summary',{});scope=raw.get('scope',{});period=raw.get('period') or {'start':raw.get('request',{}).get('start'),'end':raw.get('request',{}).get('end')}
        result=copy.deepcopy(raw);result.update(id=ident,title=raw.get('title','仙桃长势与水分时序'),series_sha256=version,period_label=str(period.get('start',''))+' — '+str(period.get('end','')),scope_label='仙桃全市 · '+str(scope.get('boundary_year',2017))+'公开参考市界')
        comparison=raw.get('quality_comparison') or {}
        result['summary']={'index_id':'ndvi','common_support_pixel_count':comparison.get('common_support_pixels',summary.get('common_support_pixels',0)),'support_scope':'质量合格观测共同范围' if comparison else '全部观测共同范围','note':('完整序列全部%d次观测共同支持为%d个像元；主曲线仅比较质量合格的实际日期，不插值，低覆盖及全云日期保留缺测。'%(len(raw.get('observations',[])),summary.get('common_support_pixels',0))) if comparison else '逐期有效范围不等同固定共同范围；缺测不补零。'}
        result['summary']['support_note']=result['summary']['note']
        for item in result.get('observations',[]):
            item.update(mean_value=item.get('ndvi_mean'),mean_common_support=item.get('quality_common_mean_ndvi') if comparison else item.get('common_mean'),date=str(item.get('date',item.get('datetime','')))[:10])
            link=publication.get('observations',{}).get(item.get('scene_id'),{})
            item.update(link)
        for index,item in enumerate(result.get('changes',[])):
            item.setdefault('id','change-'+str(index+1));item.update(publication.get('changes',{}).get(item['id'],{}))
        result['downloads']=[{'label':'观测与来源记录','url':'/api/agri-research/series/'+ident+'/files/result.json?series_sha256='+version,'type':'json'}]
        if any(isinstance(entry,dict) and entry.get('file')=='quality-comparison.csv' for entry in raw.get('artifacts',[])):
            result['downloads'].append({'label':'质量合格共同范围时序CSV','url':'/api/agri-research/series/'+ident+'/files/quality-comparison.csv?series_sha256='+version,'type':'csv'})
        result['provenance']={**result.get('provenance',{}),'series_sha256':version,'publication':publication}
        return result

    def artifact(self,ident,name,expected=None):
        if expected:self.get(ident,expected)
        root,raw=self._series(ident);return self._artifact(root,raw,name)

    def catalog(self):
        series=[];errors=[]
        if self.root.exists():
            for folder in sorted(self.root.iterdir()):
                if folder.is_dir() and not folder.is_symlink() and SLUG.fullmatch(folder.name):
                    try:
                        item=self.get(folder.name);series.append({k:item.get(k) for k in ('id','title','status','period_label','scope_label','series_sha256','sequence_summary','limitations')})
                        series[-1]['observation_count']=len(item.get('observations',[]))
                    except (GeoError,OSError,ValueError,KeyError):errors.append(folder.name)
        sets=self.store.list('crop_sample_set');models=self.store.list('crop_model')
        from backend.agriculture.crop_model import DEFAULT_REQUIREMENTS
        classifier={'status':'needs_samples','counts':{'rice':0,'rapeseed':0,'other':0},'requirements':copy.deepcopy(DEFAULT_REQUIREMENTS),'issues':['尚无适用于本地、所声明作物季节的独立标注样本。','5—10月观察未覆盖油菜完整越冬物候，不能据此发布油菜分类。'],'sample_count':0}
        if sets:
            latest=sets[0];classifier.update(status=latest['status'],counts=latest.get('counts',{}),sample_count=latest['count'],issues=latest.get('issues',[]))
        if models:classifier.update(status=models[0]['status'],validation=models[0].get('validation'),model_id=models[0]['id'])
        public=lambda values:[{k:v for k,v in row.items() if k not in ('samples','model','geojson')} for row in values]
        return {'version':VERSION,'series':series,'unavailable_series_ids':errors,'classifier':classifier,'sample_sets':public(sets),'models':public(models),'reviews':public(self.store.list('agri_review')),'capabilities':{'temporal':'actual_representative_observations','classification':'supervised_baseline_requires_independent_samples','change':'same_source_common_support_candidates_and_review'}}

    def feature_schema(self,series):
        observations=series.get('observations',[]);year=int(str(observations[-1]['date'])[:4]) if observations else 2026
        if series.get('quality_comparison'):
            eligible=set(series['quality_comparison'].get('dates',[]))
            observations=[item for item in observations if item.get('comparison_eligible') and item.get('date') in eligible]
        features=[]
        for observation in observations:
            day=observation['date']
            for index in ('ndvi','ndmi'):
                features.append({'name':index+'_'+day.replace('-','_'),'index_id':index,'start':day,'end':day,'aggregation':'single_observation','source_id':'pc_landsat_c2_l2','resolution_m':30})
        period=series.get('period') or {};season='summer' if str(period.get('start',''))[5:7] in ('04','05','06') else 'custom'
        return {'schema_version':'crop-features-1.0','crop_year':year,'features':features,'period':period,'season':season}

    def import_samples(self,body):
        if not isinstance(body,dict) or set(body)-{'name','series_id','geojson','label_source'}:raise GeoError('INVALID_SAMPLE_INPUT','样本输入字段无效',400)
        series=self.get(body.get('series_id'));name=text(body.get('name'),'样本集名称',100);geo=body.get('geojson')
        if not isinstance(geo,dict) or geo.get('type')!='FeatureCollection' or not isinstance(geo.get('features'),list) or not 1<=len(geo['features'])<=500:raise GeoError('INVALID_SAMPLE_INPUT','请导入1—500个真实GeoJSON样本点',422)
        source=body.get('label_source')
        if not isinstance(source,dict) or set(source)!={'method','reference','license','verified_by'} or source.get('method') not in ('field_survey','independent_photo_interpretation'):raise GeoError('SAMPLE_SOURCE_REQUIRED','样本须声明实地调查或独立影像解译来源',422)
        source={k:text(v,'样本来源',1000) for k,v in source.items()}
        samples=[];seen=set()
        for i,feature in enumerate(geo['features']):
            if not isinstance(feature,dict) or feature.get('type')!='Feature' or not isinstance(feature.get('geometry'),dict) or feature['geometry'].get('type')!='Point':raise GeoError('INVALID_SAMPLE_GEOMETRY','当前训练入口只接受已核实的样本点',422)
            coordinates=feature['geometry'].get('coordinates');props=feature.get('properties')or{}
            if not isinstance(props,dict):raise GeoError('SAMPLE_LABEL_REQUIRED','样本属性须为包含调查标签的对象',422)
            if not isinstance(coordinates,list) or len(coordinates)!=2 or not all(finite(v) for v in coordinates) or not -180<=coordinates[0]<=180 or not -80<=coordinates[1]<=80:raise GeoError('INVALID_SAMPLE_GEOMETRY','样本须为WGS84经纬度点',422)
            if props.get('label') not in ('rice','rapeseed','other') or props.get('split') not in ('train','validation') or type(props.get('crop_year')) is not int:raise GeoError('SAMPLE_LABEL_REQUIRED','样本须有label、crop_year、field_id及train/validation划分',422)
            field=text(props.get('field_id'),'调查地块编号',100);ident=str(feature.get('id',i))
            if ident in seen:raise GeoError('SAMPLE_DUPLICATE','样本ID不能重复',422)
            seen.add(ident);samples.append({'id':ident,'label':props['label'],'crop_year':props['crop_year'],'field_id':field,'split':props['split'],'lon':coordinates[0],'lat':coordinates[1],'label_source':copy.deepcopy(source),'features':{}})
        schema=self.feature_schema(series)
        if len(schema['features'])<4:raise GeoError('SERIES_NOT_READY','至少需要两个实际日期才能提取样本时序特征',422)
        import rasterio
        from rasterio.warp import transform
        feature_dates={item['start'] for item in schema['features']}
        for observation in series['observations']:
            if observation['date'] not in feature_dates:continue
            path=self.artifact(series['id'],observation['native_file'],series['series_sha256'])
            with rasterio.open(path) as ds:
                names=list(ds.descriptions);required=['value','valid_mask','city_mask','ndmi','ndmi_valid']
                if any(key not in names for key in required) or not ds.crs or not ds.crs.is_projected or ds.crs.linear_units not in ('metre','meter','metres','meters') or ds.transform.b or ds.transform.d or abs(ds.transform.a-30)>1e-8 or abs(ds.transform.e+30)>1e-8:raise GeoError('SAMPLE_FEATURE_CONTRACT','取样必须使用所声明的Landsat 30米原生米制格网及质量波段',422)
                xs,ys=transform('EPSG:4326',ds.crs,[s['lon'] for s in samples],[s['lat'] for s in samples])
                rows=ds.sample(zip(xs,ys),indexes=[names.index(k)+1 for k in required],masked=True)
                for sample,values in zip(samples,rows):
                    inside=not bool(getattr(values[2],'mask',False)) and float(values[2])==1
                    for index,v,m in [('ndvi',0,1),('ndmi',3,4)]:
                        good=inside and not bool(getattr(values[m],'mask',False)) and float(values[m])==1 and not bool(getattr(values[v],'mask',False)) and math.isfinite(float(values[v]))
                        sample['features'][index+'_'+observation['date'].replace('-','_')]=float(values[v]) if good else None
        feature_hash=digest({'series':series['series_sha256'],'schema':schema})
        for sample in samples:sample['feature_source_sha256']=feature_hash
        from backend.agriculture.crop_model import audit_samples
        audit=audit_samples(samples,schema,crop_year=schema['crop_year'])
        counts={key:{label:sum(s['split']==key and s['label']==label for s in samples) for label in ('rice','rapeseed','other')} for key in ('train','validation')}
        row={'id':'samples_'+uuid.uuid4().hex[:14],'name':name,'created_at':now(),'series_id':series['id'],'series_sha256':series['series_sha256'],'count':len(samples),'counts':counts,'samples':samples,'feature_schema':schema,'feature_source_sha256':feature_hash,'status':'ready_for_training' if audit.get('status')=='ready_to_train' else 'needs_samples','issues':audit.get('issues',[]),'audit':audit,'provenance':{'label_source':source,'label_truth_status':'provider_declared_not_platform_verified','features':'actual native observation pixels','feature_dates':sorted(feature_dates),'excluded_observation_dates':[item['date'] for item in series['observations'] if item['date'] not in feature_dates],'missing_value_imputation':False,'season':schema['season'],'rapeseed_season_complete':False}}
        return self.store.save('crop_sample_set',row)

    def train(self,body):
        if not isinstance(body,dict) or set(body)!={'sample_set_id'}:raise GeoError('INVALID_TRAIN_INPUT','请指定已导入的真实样本集',400)
        sample_set=self.store.get('crop_sample_set',text(body['sample_set_id'],'样本集ID',100));series=self.get(sample_set['series_id'],sample_set['series_sha256'])
        from backend.agriculture.crop_model import train_model
        result=train_model(sample_set['samples'],sample_set['feature_schema'],crop_year=sample_set['feature_schema']['crop_year'])
        if result.get('status')=='trained_baseline' and not sample_set.get('provenance',{}).get('rapeseed_season_complete'):
            result={'status':'needs_seasonal_data','validation':None,'issues':['当前时序未覆盖油菜完整越冬物候，请补充与样本季节对应的观测后再训练三类作物模型。']}
        row={'id':'model_'+uuid.uuid4().hex[:14],'sample_set_id':sample_set['id'],'series_id':series['id'],'series_sha256':series['series_sha256'],'created_at':now(),**result}
        return self.store.save('crop_model',row)

    def review(self,ident):
        value=self.store.get('agri_review',text(ident,'核查记录ID',100));result=copy.deepcopy(value)
        # Display current audit state without changing the frozen source geometry.
        for feature in result['geojson']['features']:
            state=result.get('candidate_statuses',{}).get(feature['id'],{})
            feature['properties'].update(review_status=state.get('status','pending'),review_note=state.get('note',''))
        return result

    def create_review(self,body):
        allowed={'series_id','change_id','threshold','min_pixels','direction'}
        if not isinstance(body,dict) or set(body)-allowed:raise GeoError('INVALID_REVIEW_INPUT','核查条件字段无效',400)
        threshold=body.get('threshold');minimum=body.get('min_pixels',9);direction=body.get('direction','decrease')
        if not finite(threshold) or not 0<threshold<=2 or type(minimum)is not int or not 1<=minimum<=10000 or direction not in ('decrease','increase','both'):raise GeoError('INVALID_REVIEW_CONDITION','请明确指数差值阈值(0,2]、最小像元数和变化方向',422)
        series=self.get(body.get('series_id'));change=next((v for v in series.get('changes',[]) if v.get('id')==body.get('change_id')),None)
        if not change:raise GeoError('CHANGE_NOT_FOUND','未找到该同源变化结果',404)
        path=self.artifact(series['id'],change['native_file'],series['series_sha256'])
        import numpy as np
        import rasterio
        from rasterio.features import shapes
        from rasterio.warp import transform_geom
        features=[];all_count=0;retained_pixels=0;removed_pixels=0;overflow_count=0
        with rasterio.open(path) as ds:
            names=list(ds.descriptions)
            if any(n not in names for n in ('value','valid_mask','city_mask')) or ds.width*ds.height>7000000 or not ds.crs or not ds.crs.is_projected or ds.crs.linear_units not in ('metre','meter','metres','meters') or ds.transform.b or ds.transform.d or ds.transform.a<=0 or ds.transform.e>=0:raise GeoError('REVIEW_GRID_INVALID','变化源必须为有质量掩膜的受控原生米制格网',422)
            value=ds.read(names.index('value')+1);valid=(ds.read(names.index('valid_mask')+1)==1)&(ds.read(names.index('city_mask')+1)==1)&np.isfinite(value)
            if not valid.any():raise GeoError('CHANGE_NO_COMMON_SUPPORT','这两个日期没有共同有效像元，不能生成变化核查；请选择有共同覆盖的日期组合',422)
            for sign,condition in [('decrease',value<=-threshold),('increase',value>=threshold)]:
                if direction not in (sign,'both'):continue
                mask=valid&condition
                for geometry,code in shapes(mask.astype('uint8'),mask=mask,transform=ds.transform,connectivity=4):
                    if code!=1:continue
                    def ring_area(ring):return abs(sum(a[0]*b[1]-b[0]*a[1] for a,b in zip(ring,ring[1:])))/2
                    area=ring_area(geometry['coordinates'][0])-sum(ring_area(r) for r in geometry['coordinates'][1:]);pixels=int(round(area/abs(ds.transform.a*ds.transform.e)))
                    if pixels<minimum:removed_pixels+=pixels;continue
                    all_count+=1;retained_pixels+=pixels
                    if len(features)>=1500:overflow_count+=1;continue
                    ident=digest({'source':change['native_file'],'series':series['series_sha256'],'sign':sign,'threshold':threshold,'minimum':minimum,'geometry':geometry})[:24]
                    features.append({'type':'Feature','id':ident,'properties':{'candidate_id':ident,'signal':'指数下降线索' if sign=='decrease' else '指数上升线索','direction':sign,'area_ha':area/10000,'pixel_count':pixels,'threshold':threshold,'before_date':change['before_date'],'after_date':change['after_date']},'geometry':transform_geom(ds.crs,'EPSG:4326',geometry)})
        ident='review_'+uuid.uuid4().hex[:14];summary={'scene_id':'crop_research','scene_name':'变化核查','feature_count':len(features),'polygon_feature_count':len(features),'area_sum_ha':sum(f['properties']['area_ha'] for f in features),'candidate_count_before_display_limit':all_count,'candidate_pixels_all':retained_pixels,'removed_small_pixels':removed_pixels,'omitted_candidate_count':overflow_count,'truncated':bool(overflow_count),'common_valid_ratio':change.get('common_valid_ratio'),'options':{'threshold':threshold,'min_pixels':minimum,'direction':direction},'limitations':['指数差异线索，不能直接认定灾害、病虫害、作物或耕地变化。','同源共同有效范围内计算；云缺测不补零。','最多显示1500个线索，超出数量单列，显示面积不冒称全体面积。']}
        summary['limitations'].append('全市指数变化线索，尚未限定耕地范围；跨季节变化需结合物候及现场资料核查。')
        row={'id':ident,'algorithm_id':'agri_change_review','status':'pending','feature_count':len(features),'name':'仙桃变化核查 · '+change['before_date']+'至'+change['after_date'],'created_at':now(),'series_id':series['id'],'change_id':change['id'],'before_date':change['before_date'],'after_date':change['after_date'],'geojson':{'type':'FeatureCollection','features':features},'summary':summary,'candidate_statuses':{},'history':[],'provenance':{'source_type':'analysis','source':'同平台Landsat原生指数共同支持变化','series_sha256':series['series_sha256'],'change_sha256':self._hash(path),'algorithm_version':VERSION,'review_status':'pending','boundary_year':2017}}
        return self.store.save('agri_review',row)

    def update_review(self,ident,body):
        if not isinstance(body,dict) or set(body)-{'candidate_id','status','note'} or body.get('status') not in ('pending','verified','dismissed'):raise GeoError('INVALID_REVIEW_STATUS','核查状态无效',400)
        with self.lock:
            row=self.store.get('agri_review',text(ident,'核查记录ID',100));candidate=body.get('candidate_id')
            if not any(f['id']==candidate for f in row['geojson']['features']):raise GeoError('CANDIDATE_NOT_FOUND','核查图斑不存在',404)
            note=body.get('note','')
            if not isinstance(note,str) or len(note)>500:raise GeoError('INVALID_REVIEW_NOTE','核查说明限500字',422)
            state={'status':body['status'],'note':note.strip(),'updated_at':now()};row['candidate_statuses'][candidate]=state;row['history'].append({'candidate_id':candidate,**state});self.store.save('agri_review',row)
            return self.review(ident)
