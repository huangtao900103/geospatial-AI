"""Versioned source catalog, bounded discovery and non-executing source selection plans."""
import copy
import calendar
import hashlib
import json
import re
import uuid
from datetime import date, datetime, timezone
from urllib.parse import urlencode, urlsplit, parse_qs
from geo_services import GeoError,finite
from production_services import SentinelReader,raster_available
from spectral_indices import detect_indices,choose_index,specification
from landcover_services import PRODUCT,load_boundary,available as landcover_available,SOURCES as BOUNDARY_DIR
from map_services import SOURCE_DIR as MAP_SOURCE_DIR
from catalog_adapters import CatalogGateway,PROVIDERS,COLLECTIONS,normalize_item,search_url
from concurrent.futures import ThreadPoolExecutor

SCHEMA='1.0'
METHOD_VERSION='worldcover-native-wgs84-center-v1'
PC_ROOT='https://planetarycomputer.microsoft.com/api/stac/v1/'
GOALS={'cropland_area','vegetation_change','ndvi','weather','flood_extent','general_discovery'}


def now():return datetime.now(timezone.utc).isoformat(timespec='milliseconds')
def uid(prefix):return prefix+'_'+uuid.uuid4().hex[:14]
def digest(value):return hashlib.sha256(json.dumps(value,sort_keys=True,ensure_ascii=False,separators=(',',':')).encode()).hexdigest()


def bbox_value(value,maximum=5):
    if not isinstance(value,list) or len(value)!=4 or not all(finite(v) for v in value) or not (-180<=value[0]<value[2]<=180 and -85<=value[1]<value[3]<=85) or value[2]-value[0]>maximum or value[3]-value[1]>maximum:raise GeoError('INVALID_BBOX','bbox须为合法局部WGS84范围，单边不超过%g°且不跨180°'%maximum,400)
    return list(value)


def period_value(value):
    if not isinstance(value,dict) or set(value)!={'start','end'}:raise GeoError('INVALID_PERIOD','period须包含start/end',400)
    try:
        if any(not isinstance(v,str) or len(v)!=10 for v in value.values()):raise ValueError()
        a,b=date.fromisoformat(value['start']),date.fromisoformat(value['end'])
        if a>b or (b-a).days>366:raise ValueError()
    except (ValueError,TypeError):raise GeoError('INVALID_PERIOD','日期须YYYY-MM-DD，顺序合法且范围不超过366天',400)
    return {'start':a.isoformat(),'end':b.isoformat()}


def source(id,name,type,provider,protocol,bbox=None,start=None,end=None,year=None,bands=None,semantics=None,search=False,pixels=False,analysis=False,operations=None,resolution=None,license_name='以提供方条款为准',license_url=None,auth='none',account=False,url=None,version=None,limitations=None):
    return {'id':id,'name':name,'source_type':type,'provider':provider,'protocol':protocol,'coverage':{'spatial':{'bbox':bbox,'crs':'EPSG:4326','scope_note':'目录集合范围不保证每个位置都有可用观测' if search else '以源文件或返回坐标为准'},'temporal':{'start':start,'end':end,'product_year':year,'time_role':'forecast' if type=='weather' else 'classification_year' if type=='landcover_raster' else 'observations' if search else 'reference_or_unknown'}},'resolution':resolution or {'native':None,'description':'以源数据及具体变量为准'},'bands':bands or [],'semantics':semantics or [],'stages':{'registered':True,'catalog_searchable':search,'pixel_readable':pixels,'pixel_applicable':type in ('optical','sar','landcover_raster','terrain_raster'),'analysis_ready':analysis,'connection_status':'adapter_ready_on_demand' if (search or pixels or analysis) else 'registered_not_connected'},'operations':operations or [],'license':{'name':license_name,'reported_value':license_name,'url':license_url,'verified':bool(license_url),'note':'原样保留提供方声明与链接，不据此推导商业许可或服务免费。'},'cost':{'model':'provider_service_terms','note':'公开入口不等于无限免费或免署名；服务费用与数据许可分别核实。'},'auth':{'kind':auth,'user_key_required':account,'account_required':account},'adapter':{'id':id,'version':'1.0'},'provenance':{'source_url':url,'source_version':version},'verification':{'operation':None,'checked_at':None,'status':'not_checked_in_this_workspace','query_scope':None,'detail':'接入能力标识是实现范围，不保证当前网络或指定时空条件下成功。'},'limitations':limitations or []}


class DataCatalogServices:
    def __init__(self,store,reader=None):self.store=store;self.reader=reader or SentinelReader(store.public_sources);self.gateway=CatalogGateway()
    def sources(self):
        pixels=raster_available();world_ready=landcover_available()
        try:_,b=load_boundary(self.store.landcover.source_dir);citybox=b['bbox']
        except (GeoError,KeyError,ValueError):b={};citybox=None;world_ready=False
        items=[source('worldcover_2021','ESA WorldCover2021 v200','landcover_raster','ESA WorldCover','COG',[-180,-60,180,84],'2021-01-01','2021-12-31',2021,semantics=['40 Cropland','10 Tree cover','80 Permanent water bodies'],pixels=pixels,analysis=pixels or world_ready,operations=['xiantao_class_area_2021','native_class_statistics','bounded_cropland_delineation_2021'],resolution={'native':10,'unit':'nominal_m','description':'名义10m；EPSG4326格网实际面积随纬度变化，不统一乘100m²'},license_name='CC BY 4.0',license_url='https://doi.org/10.5281/zenodo.7254221',url=PRODUCT['source_url'],version='2021-v200',limitations=['仅2021分类参考，非其他年份/法定调查；全域核算固定仙桃，单边≤0.05°选区可提取耕作地连通图斑。']),
          source('pc_sentinel2_l2a','Sentinel-2 L2A公开波段','optical','Microsoft Planetary Computer / ESA','STAC+COG',[-180,-90,180,90],'2015-06-27',None,bands=['B03 green','B04 red','B08 nir','B11 SWIR1','B12 SWIR2','SCL'],search=True,pixels=pixels,analysis=pixels,operations=['catalog_search','bounded_ndvi','bounded_delta_ndvi','bounded_ndwi','bounded_mndwi','bounded_ndmi','bounded_ndbi','bounded_nbr','bounded_delta_ndwi','bounded_delta_mndwi','bounded_delta_ndmi','bounded_delta_ndbi','bounded_delta_nbr'],resolution={'native':[10,20,60],'unit':'m_by_band','description':'产品波段10/20/60m；客户按指数使用10m或20m原生波段为参考，其余所需波段/前期/SCL最近邻对齐；预览降采样不参与统计'},license_name='proprietary',license_url='https://scihub.copernicus.eu/twiki/pub/SciHubWebPortal/TermsConditions/Sentinel_Data_Terms_and_Conditions.pdf',auth='public_provider_managed_sas',url=PC_ROOT+'collections/sentinel-2-l2a',version='sentinel-2-l2a',limitations=['当前只支持≤0.05°有界所需光谱波段/SCL窗口；不提供全市耕地分类器。','公开SAS仅服务端内存使用，未向客户要求密钥；并非所有时空范围都有合格像素。']),
          source('pc_sentinel1_rtc','Sentinel-1 RTC雷达目录','sar','Microsoft Planetary Computer','STAC+COG',[-180,-90,180,90],'2014-10-10',None,bands=['VV','VH（按条目极化）'],search=True,pixels=False,analysis=False,operations=['catalog_search'],resolution={'native':{'pixel_spacing_m':10,'range_resolution_m':20,'azimuth_resolution_m':22},'description':'10m像元间隔不等于10m独立地面分辨率；具体依条目/产品'},license_name='CC-BY-4.0',license_url='https://creativecommons.org/licenses/by/4.0/',auth='provider_account_required',account=True,url=PC_ROOT+'collections/sentinel-1-rtc',version='sentinel-1-rtc',limitations=['官方msft:requires_account=true，像素访问需要提供方账户；本平台本轮只公开目录检索。','SAR无red/nir波段，不能充当NDVI光学输入；RTC淹水/变化算法尚未接。']),
          source('pc_landsat_c2_l2','Landsat Collection2 L2 / L8·9分析','optical','Microsoft Planetary Computer / USGS','STAC+COG',[-180,-90,180,90],'1982-08-22',None,bands=['red','nir08','swir16','swir22','热红外/质量波段（依传感器）'],search=True,pixels=pixels,analysis=pixels,operations=['catalog_search','landsat89_native_indices'],resolution={'native':[30,60,100,120],'unit':'m_by_sensor_band','description':'目录gsd随传感器/波段变化；不把全源统一为30m'},license_name='proprietary',license_url='https://www.usgs.gov/core-science-systems/hdds/data-policy',url=PC_ROOT+'collections/landsat-c2-l2',version='landsat-c2-l2',limitations=['提供方license原字段为proprietary，license链接指USGS Public Domain，保留两者不自行重写授权。','Landsat8/9已接入30m原生反射率/QA与六指数；L4/5/7仍仅目录，不自动套用新传感器处理。']),
          source('open_meteo','Open-Meteo天气模型','weather','Open-Meteo','fixed REST',[-180,-85,180,85],analysis=True,operations=['current_model_forecast'],resolution={'native':None,'description':'返回模型格点依位置/模型，非请求点实测站'},license_name='CC BY 4.0; service terms apply',license_url='https://open-meteo.com/en/terms',url='https://open-meteo.com/en/docs',version='forecast-api',limitations=['当前与5日预报模型，非田间实测；本适配器不提供历史气象重建。','免费API与商业服务使用条件不同，正式使用须核实提供方条款。']),
          source('xiantao_boundary_2017','仙桃公开参考市界','vector','geoBoundaries gbOpen','GeoJSON',citybox,year=b.get('boundary_year_represented',2017),semantics=['行政参考Polygon/MultiPolygon'],analysis=bool(b),operations=['city_mask'],license_name=b.get('license','以源元信息为准'),license_url=b.get('license_url'),url=b.get('source_url'),version=b.get('sha256'),limitations=['2017概化参考界，非当前权威界；不得缩放匹配统计年鉴面积。']),
          source('natural_earth_rivers','Natural Earth公开概化水系','vector','Natural Earth','GeoJSON',[112.8,29.8,114.1,30.85],semantics=['概化水系线'],analysis=True,operations=['reference_overlay'],resolution={'native':'1:10000000','unit':'map_scale','description':'1:1000万比例尺，不是10m空间分辨率'},license_name='Public domain',license_url='https://www.naturalearthdata.com/about/terms-of-use/',url='https://www.naturalearthdata.com/',version='local_snapshot',limitations=['宏观参考，非村级沟渠/权威水利边界/现状变化。'])]
        items.extend([
          source('pc_cop_dem_glo30','PC Copernicus GLO-30地表高程参考','terrain_raster','Microsoft Planetary Computer / Copernicus','STAC+COG',[-180,-90,180,90],search=True,operations=['catalog_search'],resolution={'native':30,'unit':'nominal_m','description':'名义30m DSM，含建筑与植被，不等于裸地DTM'},url=PROVIDERS['pc_cop_dem_glo30']['root']+'collections/cop-dem-glo-30',license_name='proprietary (provider field); Copernicus DEM license',license_url='https://spacedata.copernicus.eu/documents/20126/0/CSCDA_ESA_Mission-specific+Annex.pdf',limitations=['可检索并入库高程资产元数据；地形算法尚未注册，不能用于光谱指数。','按空间检索，2021目录参考时刻不是请求年度实况。']),
          source('earthsearch_sentinel2_l2a','Earth Search Sentinel-2 L2A','optical','Element 84 / ESA','STAC+COG',[-180,-90,180,90],'2015-06-27',search=True,operations=['catalog_search'],bands=['green','red','nir','swir16','swir22','scl'],url=PROVIDERS['earthsearch_sentinel2_l2a']['root']+'collections/sentinel-2-l2a',license_url='https://registry.opendata.aws/sentinel-2-l2a-cogs/',limitations=['已实现独立目录和资产元数据；本轮尚未接此源的像元定标执行器。','不同基线COG可能已经应用偏移，不能把PC XML定标重复应用到此源。']),
          source('earthsearch_cop_dem_glo30','Copernicus GLO-30地表高程参考','terrain_raster','Element 84 / Copernicus','STAC+COG',[-180,-90,180,90],year=None,search=True,operations=['catalog_search'],resolution={'native':30,'unit':'nominal_m','description':'名义30m DSM，含建筑与植被，不等于裸地DTM或测绘精度'},license_name='proprietary (provider field); Copernicus DEM license',license_url='https://spacedata.copernicus.eu/documents/20126/0/CSCDA_ESA_Mission-specific+Annex.pdf',url=PROVIDERS['earthsearch_cop_dem_glo30']['root']+'collections/cop-dem-glo-30',limitations=['地形参考产品按空间检索；目录参考时刻不是任意请求年度的实时观测。','本轮资产元数据已入库，尚未提供坡度/高程窗口执行算子；不作为光谱指数输入。']),
          source('cdse_sentinel2_l2a','Copernicus Data Space Sentinel-2目录','optical','Copernicus Data Space / ESA','STAC 1.0/1.1',[-180,-90,180,90],'2015-06-27',search=True,operations=['catalog_search'],auth='provider_account_required',account=True,url=PROVIDERS['cdse_sentinel2_l2a']['root']+'collections/sentinel-2-l2a',license_url='https://dataspace.copernicus.eu/terms-and-conditions',limitations=['新版公开STAC目录可检索；S3/产品像元另需提供方账户，未将公开目录当作已授权像素。','保留STAC1.1 bands与auth元信息，不套用其他供应商资产读取规则。'])])
        items.append(source('io_lulc_2024','IO Annual LULC 2024','landcover_raster','Impact Observatory / Microsoft / Esri','STAC+COG',[-180,-60,180,85],'2024-01-01','2024-12-31',2024,semantics=['5 Crops','1 Water','10 Clouds (unknown)','0 NoData'],pixels=pixels,analysis=pixels,operations=['bounded_cropland_delineation_2024'],resolution={'native':10,'unit':'m','description':'原生UTM10米投影格网；统计为投影像元面积，与WorldCover椭球面积口径不同'},license_name='CC BY 4.0',license_url='https://registry.opendata.aws/io-lulc/',url='https://api.impactobservatory.com/stac-aws/collections/io-10m-annual-lulc',version='io-10m-annual-lulc-2024',limitations=['2024年度分类参考，不是当期识别或法定耕地；0缺测和10云均不当作非农田。','当前仅单瓦片完整覆盖的小选区；不跨投影拼接，不用另一年份回退。','与WorldCover类别定义和方法不同，不能把面积差直接作为耕地增减。']))
        tianditu = getattr(self.store, 'tianditu', None)
        if tianditu:
            state = tianditu.status()
            for ident,name,kind,protocol,service,operations,url,notes in (
                ('tianditu_maps','天地图底图与中文注记','basemap','OGC WMTS','tiles',['map_display'],'https://lbs.tianditu.gov.cn/server/MapService.html',['影像与标准地图为在线制图服务，不提供可计算的遥感光谱波段或观测日期。','仅当前视窗按需浏览，不导出或批量下载底图瓦片。']),
                ('tianditu_places','天地图地名地址服务','geocoder','fixed REST','search',['place_search','reverse_geocode'],'https://lbs.tianditu.gov.cn/server/search2.html',['地名点与返回视野仅用于导航，不能当行政区界、地块或分析掩膜。','显式搜索才查询外网，输入建议优先本地检索。']),
                ('tianditu_administrative','天地图行政区划参考范围','vector','REST v2 / WKT','administrative',['admin_reference_boundary'],'https://lbs.tianditu.gov.cn/server/administrative2.html',['返回边界保留来源、区划代码与查询时间，不代替法定确权或农业业务边界。','多匹配需选择，服务未返回有效几何时不从中心点或bbox生成边界。','获取边界不等于已实现该行政区全域遥感分类。']),
            ):
                item = source(ident,name,kind,'天地图 · 国家地理信息公共服务平台',protocol,operations=operations,semantics=[name],auth='server_api_key',account=True,url=url,version='v2' if ident.endswith('administrative') else 'official-wmts-rest',license_url='https://lbs.tianditu.gov.cn/authorization/authorization.html',limitations=notes)
                item['stages'].update(connection_status=state['status'],service_adapter_implemented=True,service_queryable=state['configured'])
                service_state = state.get('services',{}).get(service,{})
                item['verification']={'operation':'connection_check','checked_at':state.get('checked_at'),'status':service_state.get('status','not_checked'),'detail':service_state.get('message',state['message']),'query_scope':None}
                items.append(item)
        for id,name,type,protocol in [('ogc_external','外部OGC地图与要素服务','ogc','WMS/WMTS/WFS'),('business_systems','农业业务系统','business','REST'),('iot_observations','物联网观测','iot','REST/MQTT')]:items.append(source(id,name,type,'待配置',protocol,limitations=['仅登记接入规划，无真实连接、授权或数据；保存endpoint不代表已接入。']))
        for id,name,kind in [('domestic_highres_planned','国内高分与商业影像（规划）','optical'),('uav_ortho_planned','无人机正射影像（规划）','optical'),('dem_terrain_planned','DEM与地形数据（规划）','terrain_raster')]:
            planned=source(id,name,kind,'待明确','待约定',auth='not_configured',version='planned',limitations=['仅登记后续接入方向，没有数据、许可、认证、检索、像素读取或算法适配器。'])
            planned['stages']['pixel_applicable']=True;planned['auth'].update(user_key_required=None,account_required=None);planned['cost']={'model':'unknown','note':'费用及服务条件尚未确定。'};items.append(planned)
        try:
            meta=json.loads((MAP_SOURCE_DIR/'natural_earth_rivers_xiantao.metadata.json').read_text('utf-8'));river=next(s for s in items if s['id']=='natural_earth_rivers');river['provenance'].update(source_url=meta['source_url'],source_version=meta['snapshot_sha256'],fetched_at=meta['fetched_at'])
        except (OSError,ValueError,KeyError):
            river=next(s for s in items if s['id']=='natural_earth_rivers');river['stages']['analysis_ready']=False;river['stages']['connection_status']='registered_not_connected'
        for dataset in self.store.datasets():
            if dataset.get('source_type')!='user':continue
            s=source('user_geojson:'+dataset['id'],dataset['name'],'user_vector','用户提供','GeoJSON',dataset.get('bbox'),semantics=list(dataset.get('geometry_types',{})),analysis=True,operations=['spatial_summary','nearest_if_two_points','attribute_ndvi_if_red_nir'],license_name='以提供方授权为准',auth='workspace_local',url='/api/datasets/'+dataset['id'],version=dataset['sha256'],limitations=['用户导入不证明数据真实或已获得权属；导入时间不是观测日期。'])
            s['provenance']['imported_at']=dataset['created_at'];items.append(s)
        business_semantics={
            'pc_sentinel2_l2a':['NDVI/NDWI/MNDWI/NDMI/NDBI/NBR','两期指数变化','光学原生窗口分析'],
            'pc_sentinel1_rtc':['SAR雷达观测目录','淹水与变化研究候选','待授权及算法接入'],
            'pc_landsat_c2_l2':['Landsat8/9原生30m六指数','QA_PIXEL与QA_RADSAT质量','其他传感器仍仅目录'],
            'open_meteo':['当前天气模型','5日预报','辅助气象条件'],
            'ogc_external':['地图显示与矢量要素','待受控接口对接'],
            'business_systems':['农业主体与工程台账','待授权及字段对接'],
            'iot_observations':['设备与时序观测','待事件和单位对接'],
            'domestic_highres_planned':['高分辨率观测候选','待产品许可与接口'],
            'uav_ortho_planned':['局部精细观测候选','待配准成果与授权'],
            'dem_terrain_planned':['高程与地形辅助候选','待高程基准与精度确认'],
        }
        for item in items:
            if not item['semantics']:item['semantics']=business_semantics.get(item['id'],[])
        checks={}
        for check in self.store.list('data_source_check'):checks.setdefault(check.get('source_id',check['id']),check)
        for item in items:
            if item['id'] in checks:item['verification']=checks[item['id']]['verification']
        items.extend(self.store.list('data_source_draft'))
        for item in items:
            item['metadata_fingerprint']=digest({k:v for k,v in item.items() if k not in ('verification','metadata_fingerprint','created_at')})
        return {'schema_version':SCHEMA,'catalog_fingerprint':digest([{'id':s['id'],'fingerprint':s['metadata_fingerprint']} for s in items]),'sources':items,'stage_semantics':'四能力是实现/依赖维度，最近请求实测独立记录；目录命中不等于像素读取或分析完成。'}
    def get_source(self,id):
        for source in self.sources()['sources']:
            if source['id']==id:return source
        raise GeoError('SOURCE_NOT_FOUND','没有此数据源',404)
    def search(self,body):
        if not isinstance(body,dict) or set(body)-{'source_ids','bbox','start','end','limit'}:raise GeoError('INVALID_DISCOVERY','检索字段不支持',400)
        ids=body.get('source_ids',['pc_sentinel2_l2a'])
        if not isinstance(ids,list) or not 1<=len(ids)<=7 or any(not isinstance(i,str) or i not in COLLECTIONS for i in ids) or len(set(ids))!=len(ids):raise GeoError('SOURCE_NOT_SEARCHABLE','只支持已登记固定STAC集合，不能访问任意URL或草案endpoint',400)
        box=bbox_value(body.get('bbox'));period=period_value({'start':body.get('start'),'end':body.get('end')});limit=body.get('limit',3)
        if isinstance(limit,bool) or not isinstance(limit,int) or not 1<=limit<=6:raise GeoError('INVALID_LIMIT','每源结果数限制为1–6',400)
        def discover(id):
            url=search_url(id,box,period,limit);fetched=now()
            try:
                data=(self.reader if id.startswith('pc_') else self.gateway).fetch_json(url,lambda:None)
                if not isinstance(data,dict) or not isinstance(data.get('features'),list):raise GeoError('CATALOG_SCHEMA','公开目录未返回有效FeatureCollection',502)
                items=[normalize_item(id,f,box,period) for f in data['features'][:limit]]
                status='succeeded' if items else 'empty';detail='仅查询目录元数据；未读取像素或执行分析。'
                if not PROVIDERS[id]['temporal']:detail+='地形产品按空间检索，目录参考时刻不是请求年度观测；日期过滤不适用。'
                result={'source_id':id,'status':status,'items':items,'message':detail,'provenance':{'source_url':url,'fetched_at':fetched,'operation':'catalog_search','catalog_only':True,'collection':COLLECTIONS[id],'temporal_filter_applied':PROVIDERS[id]['temporal'],'response_sha256':digest(items)}}
            except Exception as e:
                result={'source_id':id,'status':'failed','items':[],'message':e.message if isinstance(e,GeoError) else '公开目录查询失败；未生成替代候选','provenance':{'source_url':url,'fetched_at':fetched,'operation':'catalog_search','catalog_only':True}}
            self.store.save('data_source_check',{'id':uid('sourcecheck'),'source_id':id,'verification':{'operation':'catalog_search','checked_at':fetched,'status':result['status'],'query_scope':{'bbox':box,'period':period,'limit':limit},'detail':result['message']},'created_at':now()})
            return result
        with ThreadPoolExecutor(max_workers=min(3,len(ids))) as executor:results=list(executor.map(discover,ids))
        count=sum(r['status']!='failed' for r in results);record={'id':uid('discovery'),'schema_version':SCHEMA,'query':{'source_ids':ids,'bbox':box,**period,'limit':limit},'status':'completed' if count==len(results) else 'partial' if count else 'failed','results':results,'discovery_only':True,'created_at':now()}
        return self.store.save('data_search',record)
    def create_draft(self,body):
        if not isinstance(body,dict) or set(body)-{'name','source_type','endpoint','protocol'}:raise GeoError('INVALID_DRAFT','只接受名称、类型、地址、协议；不接受凭据',400)
        name,kind,url,protocol=(body.get(k) for k in ('name','source_type','endpoint','protocol'))
        if not isinstance(name,str) or not name.strip() or len(name)>80 or kind not in ('ogc','business','iot','catalog') or not isinstance(url,str) or len(url)>1000 or not isinstance(protocol,str) or not protocol.strip() or len(protocol)>50:raise GeoError('INVALID_DRAFT','接入草案字段格式无效',400)
        try:
            p=urlsplit(url)
            if p.scheme not in ('https','http') or not p.hostname or p.username or p.password or p.fragment or any(re.search(r'key|token|secret|auth|credential|password|^sig$|signature|^sas$|^x-amz-|^x-goog-',k,re.I) for k in parse_qs(p.query,keep_blank_values=True)):raise ValueError()
            p.port
        except ValueError:raise GeoError('INVALID_ENDPOINT','地址不能包含凭据、密钥或非HTTP(S)协议',400)
        if len(self.store.list('data_source_draft'))>=100:raise GeoError('DRAFT_LIMIT','最多登记100个接入草案',422)
        item=source(uid('draft'),name.strip(),kind,'待配置',protocol,auth='not_configured',url=url,limitations=['仅保存接入意向，未访问URL/测试连通性/读取数据/配置凭据。'])
        item['endpoint']=url;item['created_at']=now();return self.store.save('data_source_draft',item)
    def list_searches(self):return self.store.list('data_search')
    def get_search(self,id):return self.store.get('data_search',id)
    def list_plans(self):return self.store.list('data_plan')
    def get_plan(self,id):return self.store.get('data_plan',id)
    def create_plan(self,body):
        if not isinstance(body,dict) or set(body)-{'question','region','bbox','period','goal','source_ids','context'}:raise GeoError('INVALID_DATA_PLAN','数据计划字段不支持',400)
        question=body.get('question','')
        if not isinstance(question,str) or len(question)>4000:raise GeoError('INVALID_DATA_PLAN','问题必须为受限文本',400)
        spectral=detect_indices(question)
        goal=body.get('goal') or ('vegetation_change' if spectral and any(w in question for w in ('对比','比较','变化','两期')) else 'ndvi' if spectral else 'vegetation_change' if any(w in question.lower() for w in ('ndvi','植被','光谱')) and any(w in question for w in ('对比','比较','变化','两期')) else 'ndvi' if any(w in question.lower() for w in ('ndvi','植被','长势')) else 'weather' if any(w in question for w in ('天气','气象','降雨')) else 'flood_extent' if any(w in question for w in ('淹水','洪涝','积水')) else 'cropland_area' if any(w in question for w in ('耕地','农田','耕作地')) else 'general_discovery')
        if not isinstance(goal,str) or goal not in GOALS:raise GeoError('INVALID_GOAL','不支持此选源目标',400)
        semantic_issues=[]
        unsupported_metric=any(word in question for word in ('官方','法定','高标准','播种','水田','旱地','水稻','小麦','玉米','油菜','棉花','作物细分','作物分类'))
        if unsupported_metric and any(word in question for word in ('面积','多少亩','公顷')) and (any(word in question for word in ('统计','核算','计算','多少','几亩','几公顷')) or question.strip().endswith('面积')):semantic_issues.append({'code':'METRIC_UNSUPPORTED','severity':'blocking','message':'该业务定义没有面积执行算子，不能用WorldCover类别统计或目录检索冒充。'})
        text_cities=[]
        for name in ('仙桃','武汉','北京','上海','深圳','广州','宜昌','黄石','十堰','荆州','荆门','襄阳','孝感','黄冈','鄂州','咸宁','随州','恩施','潜江','天门'):
            if name in question:text_cities.append(name+'市')
        for mention in re.findall(r'((?:(?!市|县)[\u4e00-\u9fff]){1,10}(?:市(?!界)|县(?!界)))',question):
            clean=re.sub(r'统计|核算|计算|分析|请|我要|参考|全部|完整|的|目前|当前|并|展示|显示|查看|分布|覆盖|范围|情况|土地|耕地|农田|在|内|于|和|与|用|遥感|年','',mention)
            if clean.endswith('全市') and clean!='全市':clean=clean[:-2]+'市'
            if clean and clean not in ('全市','全县','市','县','本市','我市') and clean not in text_cities:text_cities.append(clean)
        scope_region='全国' if '全国' in question else '湖北省' if ('全湖北' in question or '全省' in question or ('湖北省' in question and '仙桃' not in question)) else None
        region=body.get('region',scope_region or (text_cities[0] if len(text_cities)==1 else '仙桃市'))
        if len(set(text_cities))>1:semantic_issues.append({'code':'MULTI_REGION_UNSUPPORTED','severity':'blocking','message':'请求包含多个地区，不能用单个仙桃执行合同替代。'})
        if text_cities and 'region' in body and body['region'] not in text_cities and not (body['region'] in ('仙桃','仙桃市','湖北省仙桃市','湖北省 · 仙桃市') and text_cities==['仙桃市']):semantic_issues.append({'code':'REGION_CONFLICT','severity':'blocking','message':'结构化区域不能覆盖原文中的明确地区。'})
        if any(w in question for w in ('耕地面积','农田面积','多少亩','农田有多少','耕作地面积')) and goal not in ('cropland_area','general_discovery'):semantic_issues.append({'code':'GOAL_CONFLICT','severity':'blocking','message':'原文请求农田/耕地面积，不能通过goal参数改跑NDVI或其他算子。'})
        if any(w in question.lower() for w in ('ndvi','光谱变化')) and goal not in ('ndvi','vegetation_change','general_discovery'):semantic_issues.append({'code':'GOAL_CONFLICT','severity':'blocking','message':'原文光谱目标与指定goal不一致，不能用其他分析替代。'})
        if not isinstance(region,str) or len(region)>100:raise GeoError('INVALID_REGION','区域名称无效',400)
        catalog=self.sources()['sources'];by_id={s['id']:s for s in catalog};ids=body.get('source_ids')
        if ids is not None and (not isinstance(ids,list) or not ids or len(ids)>20 or any(not isinstance(i,str) or i not in by_id for i in ids) or len(set(ids))!=len(ids)):raise GeoError('INVALID_SOURCE_SELECTION','源约束须为已登记且不重复的源ID',400)
        bbox=bbox_value(body['bbox']) if 'bbox' in body else None
        period=period_value(body['period']) if 'period' in body else None
        explicit_period=None
        iso_dates=re.findall(r'(?<!\d)\d{4}-\d{2}-\d{2}(?!\d)',question)
        month_matches=re.findall(r'((?:19|20|21)\d{2})年\s*(\d{1,2})月',question)
        try:
            if 1<=len(iso_dates)<=2:
                parsed=[date.fromisoformat(d) for d in iso_dates]
                if parsed[0]>parsed[-1]:raise ValueError()
                explicit_period={'start':parsed[0].isoformat(),'end':parsed[-1].isoformat()}
            elif len(iso_dates)>2 and 'period' in body:raise ValueError()
            elif len(month_matches)==1 and len(re.findall(r'\d{1,2}月',question))==1:
                y,m=map(int,month_matches[0]);explicit_period={'start':date(y,m,1).isoformat(),'end':date(y,m,calendar.monthrange(y,m)[1]).isoformat()}
            elif month_matches and 'period' in body:raise ValueError()
        except (ValueError,TypeError):semantic_issues.append({'code':'PERIOD_CONFLICT','severity':'blocking','message':'原文日期/月份无效或不能明确桥接，不能用period参数悄悄替换。'})
        if explicit_period is not None:
            if period is not None and period!=explicit_period:semantic_issues.append({'code':'PERIOD_CONFLICT','severity':'blocking','message':'结构化period与原文明示的日期或月份不一致。'})
            elif period is None:period=explicit_period
        years=re.findall(r'(?<!\d)(?:(?:19|20|21)\d{2}|\d{4}(?=年))(?!\d)',question)
        if ('边界' in question or '市界' in question) and ('2017' in question or any(w in question for w in ('来源','哪年','年份','为什么','质量','准','来自哪里'))) and not any(w in question for w in ('统计','核算','计算','农田面积','耕地面积')):years=[y for y in years if y!='2017']
        if period is None and years:
            if len(set(years))==1:period={'start':years[0]+'-01-01','end':years[0]+'-12-31'}
        temporal_question=re.sub(r'当前范围|当前选区|当前区域|当前地图','',question) if isinstance(body.get('context'),dict) and body['context'].get('task_type')=='cropland' else question
        relative=any(w in temporal_question for w in ('今年','当前','现在','最新','现状','去年','本年度','上年度','本年','今日','上年','实时','目前'))
        if period is None and relative:
            year=datetime.now(timezone.utc).year-(1 if any(w in question for w in ('去年','上年度','上年')) else 0);period={'start':str(year)+'-01-01','end':str(year)+'-12-31'}
        if 'period' in body and years and any(not period['start'][:4]<=year<=period['end'][:4] for year in years):semantic_issues.append({'code':'PERIOD_CONFLICT','severity':'blocking','message':'结构化period不能覆盖原文明示的年份。'})
        if 'period' in body and relative:
            expected=datetime.now(timezone.utc).year-(1 if any(w in question for w in ('去年','上年度','上年')) else 0)
            if period['start'][:4]!=str(expected) or period['end'][:4]!=str(expected):semantic_issues.append({'code':'PERIOD_CONFLICT','severity':'blocking','message':'结构化period与原文相对年度不一致。'})
        annual_lc_context=isinstance(body.get('context'),dict) and body['context'].get('task_type') in ('landcover','cropland')
        if (goal=='cropland_area' or annual_lc_context) and period is not None:
            year=period['start'][:4]
            if year in ('2021','2024') and period['end'][:4]==year and period!={'start':year+'-01-01','end':year+'-12-31'}:
                semantic_issues.append({'code':'ANNUAL_PRODUCT_ONLY','severity':'blocking','message':'年度分类产品不能声称已分析指定月/日的耕地面积；可另行查看对应年度参考。'})
        def gap(code,message,severity='blocking'):gaps.append({'code':code,'severity':severity,'message':message})
        gaps=semantic_issues;selection=[];steps=[];candidates=[];contract=None
        lineage_only=body.get('context') is not None and not any(k in body for k in ('goal','source_ids','bbox','period'))
        if lineage_only:
            # Context-only attachment describes a completed answer, not a newly proposed search.
            source_words=any(w in question for w in ('来源','依据','准吗','准不准','质量','精度','下载','导出','可用','哪年','年份'))
            numeric_current=relative and (any(w in question for w in ('多少','占比','统计','核算','计算')) or not source_words)
            if not years and not numeric_current:period=None
            kind=body['context'].get('task_type') if isinstance(body['context'],dict) else None
            evidence_goal=goal if goal!='general_discovery' else ('cropland_area' if kind=='landcover' else 'general_discovery')
            evidence,used,related=self.execution_evidence(body['context'],evidence_goal,region,bbox,period,question)
            if kind=='cropland' and evidence:
                if bbox is None:bbox=evidence.get('bbox')
                if not text_cities and 'region' not in body:region='地图选区'
            if semantic_issues:evidence=None;used=[];related=related or {'task_id':body['context'].get('task_id'),'reason':'原文范围/目标冲突，不声明本次使用'}
            if related:gaps.append({'code':'EVIDENCE_SCOPE_MISMATCH','severity':'blocking','message':'历史结果与本次问题范围/年度不一致，仅列为相关历史，不作为实际使用。'})
            snapshot=[copy.deepcopy(by_id[u['source_id']]) for u in used if u['source_id'] in by_id]
            record={'id':uid('data_plan'),'schema_version':SCHEMA,'status':'needs_data' if gaps else 'planned','request':copy.deepcopy(body),'interpreted':{'goal':goal,'region':region,'bbox':bbox,'period':period},'candidates':[],'selection':[],'used':used,'execution_evidence':evidence,'related_existing_result':related,'source_snapshot':snapshot,'catalog_fingerprint':self.sources()['catalog_fingerprint'],'steps':[],'gaps':gaps,'executable':False,'not_executed':True,'execution_contract':None,'provenance':{'planner_mode':'bounded_rules_no_general_llm','purpose':'lineage_only','note':'本记录只描述已成功任务的来源，不启动新选源或执行。'},'created_at':now()}
            return self.store.save('data_plan',record)
        def choose(id,role,reason,operation):
            s=by_id[id];selection.append({'source_id':id,'role':role,'reason':reason,'stages':s['stages'],'operations':[operation]});steps.append({'id':'step_'+str(len(steps)+1),'operation':operation,'source_id':id,'depends_on':[],'status':'planned' if s['stages']['analysis_ready'] else 'blocked'})
        eligible=set(ids) if ids is not None else set(by_id)
        if goal=='cropland_area':
            metric_ok=not any(word in question for word in ('官方','法定','高标准','播种','水田','旱地','水稻','小麦','玉米','油菜','棉花','作物细分','作物分类'))
            if not metric_ok and not any(g['code']=='METRIC_UNSUPPORTED' for g in gaps):gap('METRIC_UNSUPPORTED','WorldCover类别面积不能代替官方/法定耕地、高标准农田、播种面积或作物/水旱地细分；当前没有该口径算子。')
            year_ok=(period is None or (period['start']>='2021-01-01' and period['end']<='2021-12-31')) and all(y=='2021' for y in years)
            if not year_ok:gap('YEAR_MISMATCH','WorldCover2021不能填补其他年度耕地面积；S2可发现影像但当前没有全市耕地分类器。')
            try:_,boundary=load_boundary(self.store.landcover.source_dir);citybox=boundary['bbox']
            except GeoError:citybox=None;gap('BOUNDARY_UNAVAILABLE','没有可核验的市界。')
            scope_ok=region in ('仙桃市','仙桃','湖北省仙桃市','湖北省 · 仙桃市') and not any(w in question for w in ('全省','省域','全国','全湖北')) and (bbox is None or citybox and all(abs(a-b)<1e-6 for a,b in zip(bbox,citybox)))
            if not scope_ok:gap('REGION_UNSUPPORTED','全域面积适配器仅支持仙桃参考市界，不能以此替换省域、异地或选中地块。')
            for id in ('worldcover_2021','xiantao_boundary_2017'):
                if id not in eligible:gap('REQUIRED_SOURCE_EXCLUDED','源约束排除了必要输入：'+id)
            if metric_ok and year_ok and scope_ok and {'worldcover_2021','xiantao_boundary_2017'}<=eligible:
                choose('worldcover_2021','primary','2021分类产品可执行原生类别统计','xiantao_class_area_2021');choose('xiantao_boundary_2017','mask','明确2017公开参考市界，不声称权威现状界','city_mask')
                if not by_id['worldcover_2021']['stages']['analysis_ready']:gap('ENGINE_UNAVAILABLE','当前环境或边界条件尚未达到面积执行要求。')
                contract={'service':'/api/landcover/tasks','method':'POST','body':{'region':'xiantao','product':'worldcover_2021_v200','requirement':'核算仙桃全市2021公开分类基准'}}
                if period is None:period={'start':'2021-01-01','end':'2021-12-31'};gap('DEFAULT_BASELINE','未指定年度，计划明确使用2021历史分类基准，非当前统计。','warning')
            gap('NO_LOCAL_ACCURACY','尚无仙桃独立精度样本与误差校正，不能产生本地面积置信区间。','warning')
        elif goal in ('ndvi','vegetation_change'):
            explicit_source='pc_landsat_c2_l2' if 'landsat' in question.lower() else 'pc_sentinel2_l2a' if re.search(r'sentinel[- ]?2',question,re.I) else None
            preferred=explicit_source or ('pc_landsat_c2_l2' if ('pc_sentinel2_l2a' not in eligible or period and period['start']<'2015-06-27') else 'pc_sentinel2_l2a')
            if preferred not in eligible:gap('OPTICAL_BANDS_REQUIRED','指定或适用的光学来源未包含于源约束；SAR、分类图或仅目录源不能替代。')
            else:
                algorithm=choose_index(question)
                if preferred=='pc_landsat_c2_l2':
                    from landsat_reader import specification as landsat_specification
                    algorithm=landsat_specification(algorithm['id'])
                choose(preferred,'primary','按'+algorithm['label']+'读取所需光谱与'+('QA_PIXEL/RADSAT' if preferred=='pc_landsat_c2_l2' else 'SCL')+'，使用'+str(algorithm['reference_resolution_m'])+'m原生参考；需满足覆盖/质量','landsat89_native_indices' if preferred=='pc_landsat_c2_l2' else 'bounded_delta_'+algorithm['id'] if goal=='vegetation_change' else 'bounded_'+algorithm['id'])
                if not by_id[preferred]['stages']['analysis_ready']:gap('PIXEL_ENGINE_UNAVAILABLE','当前Python不具备真实COG像素运行依赖。')
            if bbox is None or bbox[2]-bbox[0]>.050001 or bbox[3]-bbox[1]>.050001:gap('SMALL_AOI_REQUIRED','请明确≤0.05°的小范围，不能把默认小窗当全市。')
            if period is None:gap('PERIOD_REQUIRED','需要明确观测时期；两期变化还需前后两个不重叠窗口，当前计划不会猜测。')
            if goal=='vegetation_change':gap('TWO_EPOCH_WINDOWS_REQUIRED','两期变化需完整before/after两个窗口，统一选源计划当前period仅描述总观察期。')
            gap('NOT_CROPLAND_CLASSIFICATION','NDVI/光谱变化不是耕地分类或农田面积认定。','warning')
            if bbox and period and goal=='ndvi':
                from datetime import timedelta
                from production_services import make_plan
                first=date.fromisoformat(period['start']);previous=(first-timedelta(days=1)).isoformat()
                payload={'source_id':preferred,'mode':'sentinel_cog','sampling':'native','index_id':choose_index(question)['id'],'bbox':bbox,'goal':'ndvi','requirement':question or 'NDVI小范围分析','periods':{'before':[previous,previous],'after':[period['start'],period['end']]}}
                try:
                    native_plan=make_plan(payload)
                    if native_plan['blocking_gaps']:gap('EXECUTOR_CONDITIONS','；'.join(native_plan['blocking_gaps']))
                    contract={'service':'/api/production/tasks','method':'POST','body':payload}
                except GeoError as e:gap('EXECUTOR_CONDITIONS',e.message)
        elif goal=='weather':
            if 'open_meteo' not in eligible:gap('WEATHER_SOURCE_REQUIRED','源约束没有包含已适配天气模型。')
            else:choose('open_meteo','primary','固定模型预报服务，不是田间实测','current_model_forecast')
            if period is not None:gap('HISTORICAL_WEATHER_UNSUPPORTED','现有适配器为当前/预报，不提供任意历史时期天气。')
            if bbox:
                lat=(bbox[1]+bbox[3])/2;lon=(bbox[0]+bbox[2])/2
            elif region in ('仙桃','仙桃市'):lat,lon=30.36,113.45
            else:lat=lon=None;gap('LOCATION_REQUIRED','需要可定位地点，不能把仙桃天气贴到其他地区。')
            if lat is not None:contract={'service':'/api/public/weather?lat=%s&lon=%s'%(lat,lon),'method':'GET','body':None}
            gap('MODEL_NOT_OBSERVATION','请求点与模型格点可能不同；预报不能直接认定灾情。','warning')
        elif goal=='flood_extent':
            if 'pc_sentinel1_rtc' in eligible:choose('pc_sentinel1_rtc','candidate','雷达可作为后续淹水研究候选；当前只目录','catalog_search')
            gap('SAR_AUTH_REQUIRED','PC S1RTC像素需提供方账户，公开目录不等于免账户读取。');gap('FLOOD_OPERATOR_MISSING','本平台未接入SAR定标/淹水算法与验证，不能宣布多源分析已就绪。')
        else:
            for id in COLLECTIONS:
                if id in eligible:selection.append({'source_id':id,'role':'candidate','reason':'可按时空条件独立目录检索；没有执行读取或分析','stages':by_id[id]['stages'],'operations':['catalog_search']})
            if not selection:gap('NO_SEARCHABLE_SOURCE','所选数据源尚无已实现的公开目录检索适配器，不能生成空源的可执行检索。')
            if bbox is None:gap('BBOX_REQUIRED','目录查询需要明确范围。')
            if period is None:gap('PERIOD_REQUIRED','目录查询需要明确日期范围。')
            if bbox and period and selection:contract={'service':'/api/data-sources/search','method':'POST','body':{'source_ids':[s['source_id'] for s in selection],'bbox':bbox,**period,'limit':3}}
        for s in catalog:
            if s['id'] not in eligible:continue
            selected=next((x for x in selection if x['source_id']==s['id']),None)
            reason=selected['reason'] if selected else '当前目标没有可执行适配组合，仅作为目录候选或其他业务来源。'
            suitability='selected_for_plan' if selected else 'not_selected'
            if goal=='cropland_area' and s['id']=='worldcover_2021' and any(g['code']=='YEAR_MISMATCH' for g in gaps):suitability='rejected';reason='YEAR_MISMATCH：2021产品不能代替请求年度'
            if goal=='cropland_area' and s['id']=='worldcover_2021' and any(g['code']=='METRIC_UNSUPPORTED' for g in gaps):suitability='rejected';reason='METRIC_UNSUPPORTED：现有年度分类不能替代指定业务口径'
            if goal in ('ndvi','vegetation_change') and s['source_type']=='sar':suitability='rejected';reason='MODALITY_MISMATCH：SAR没有光学red/nir'
            if goal in ('ndvi','vegetation_change') and s['id']=='pc_landsat_c2_l2':reason='Landsat8/9具有原生定标与质量执行；L4/5/7仍仅目录'
            candidates.append({'source_id':s['id'],'suitability':suitability,'reason':reason})
        evidence,used,related=self.execution_evidence(body.get('context'),goal,region,bbox,period,question)
        if related:gap('EVIDENCE_SCOPE_MISMATCH','关联历史成果与本次目标/时空条件不同，仅列相关历史结果，不计本次实际使用。')
        blocking=any(g['severity']=='blocking' for g in gaps)
        record={'id':uid('data_plan'),'schema_version':SCHEMA,'status':'needs_data' if blocking else 'planned','request':copy.deepcopy(body),'interpreted':{'goal':goal,'region':region,'bbox':bbox,'period':period},'candidates':candidates,'selection':selection,'source_snapshot':[copy.deepcopy(by_id[s['source_id']]) for s in selection],'catalog_fingerprint':self.sources()['catalog_fingerprint'],'used':used,'execution_evidence':evidence,'related_existing_result':related,'steps':steps,'gaps':gaps,'executable':not blocking and contract is not None,'not_executed':True,'execution_contract':None if blocking else contract,'provenance':{'planner_mode':'bounded_rules_no_general_llm','source_catalog_schema':SCHEMA,'note':'计划不隐式查询网络或执行分析；used只能来自另一个已成功任务。'},'created_at':now()}
        return self.store.save('data_plan',record)
    def execution_evidence(self,context,goal,region,bbox,period,question):
        if context is None:return None,[],None
        if not isinstance(context,dict) or set(context)!={'task_type','task_id'} or context.get('task_type') not in ('landcover','production','cropland') or not isinstance(context.get('task_id'),str) or not 1<=len(context['task_id'])<=100:raise GeoError('INVALID_EVIDENCE_CONTEXT','只接受真实task_type/task_id引用',400)
        literal_years=re.findall(r'(?<!\d)(?:19|20|21)\d{2}(?!\d)',question)
        if ('边界' in question or '市界' in question) and ('2017' in question or any(w in question for w in ('来源','哪年','年份','为什么','质量','准','来自哪里'))) and not any(w in question for w in ('统计','核算','计算','农田面积','耕地面积')):literal_years=[year for year in literal_years if year!='2017']
        kind,id=context['task_type'],context['task_id'];task=getattr(self.store,kind).get(id)
        if task['status']!='succeeded' or not isinstance(task.get('result'),dict):return None,[],{'task_type':kind,'task_id':id,'status':task['status'],'reason':'任务没有成功成果'}
        p=task['result']['provenance'];evidence={'task_type':kind,'task_id':id,'status':'succeeded','result_created_at':task.get('finished_at')};mismatch=False;used=[]
        if kind=='cropland':
            product_id=p.get('product',{}).get('id');expected={'worldcover_2021_v200':(2021,'worldcover_2021'),'io_lulc_2024':(2024,'io_lulc_2024')}.get(product_id)
            year,source_id=expected if expected else (None,None);box=p.get('selected_bbox')
            mismatch=not expected or p.get('year')!=year or p.get('operation')!='published_classification_extraction' or goal not in ('cropland_area','general_discovery') or any(y!=str(year) for y in literal_years) or (period is not None and period!={'start':str(year)+'-01-01','end':str(year)+'-12-31'}) or (bbox is not None and bbox!=box) or any(w in question for w in ('全省','全国','全市','全县'))
            evidence.update(source_year=year,boundary_year=None,bbox=box,selection_sha256=p.get('selection_sha256'),area_method=p.get('area_method'))
            if not mismatch:used=[{'source_id':source_id,'role':'actual_input','task_ref':context,'evidence':{'product':p['product'],'year':year,'classified_window_sha256':p['classified_window_sha256'],'bbox':box,'selection_sha256':p['selection_sha256'],'operation':p['operation'],'area_method':p.get('area_method')}}]
        elif kind=='landcover':
            b=p['boundary_source'];box=b.get('bbox');mismatch=p.get('year')!=2021 or p.get('product',{}).get('id')!=PRODUCT['id'] or any(year!='2021' for year in literal_years) or goal not in ('cropland_area','general_discovery') or region not in ('仙桃','仙桃市','湖北省仙桃市','湖北省 · 仙桃市') or any(w in question for w in ('全省','省域','全国','全湖北')) or ('湖北省' in question and '仙桃' not in question) or (period is not None and period!={'start':'2021-01-01','end':'2021-12-31'}) or (bbox is not None and (not box or any(abs(a-c)>1e-6 for a,c in zip(bbox,box))))
            evidence.update(source_year=p['year'],boundary_year=b.get('boundary_year_represented'),boundary_sha256=p['boundary_sha256'])
            if not mismatch:used=[{'source_id':'worldcover_2021','role':'actual_input','task_ref':context,'evidence':{'product':p['product'],'year':p['year'],'classified_window_sha256':p['classified_window_sha256']}},{'source_id':'xiantao_boundary_2017','role':'actual_mask','task_ref':context,'evidence':{'year':b.get('boundary_year_represented'),'sha256':p['boundary_sha256'],'source_url':b.get('source_url')}}]
        else:
            box=p.get('bbox');dates=[s.get('datetime','')[:10] for s in p.get('source_details',{}).get('scenes',{}).values()]
            mismatch=not dates or goal not in ('ndvi','vegetation_change','general_discovery') or p.get('source_type')!='public_satellite_pixels' or (bbox is not None and (not box or any(abs(a-c)>1e-6 for a,c in zip(bbox,box)))) or (period is not None and any(not period['start']<=d<=period['end'] for d in dates))
            if not mismatch:used=[{'source_id':p.get('source_id','pc_sentinel2_l2a'),'role':'actual_input','task_ref':context,'evidence':{'input_sha256':p['input_sha256'],'scenes':p.get('source_details',{}).get('scenes',{}),'bbox':box,'algorithm':p.get('algorithm'),'grid':p.get('grid')}}]
        if mismatch:return None,[],{**evidence,'reason':'目标、范围、时期或真实来源类型不匹配，不能声明本次已使用'}
        return evidence,used,None
