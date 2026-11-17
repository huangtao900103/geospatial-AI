"""City-wide native-resolution ESA WorldCover area accounting; never NDVI extrapolation."""
import copy
import csv
import hashlib
import importlib.util
import json
import math
import re
import threading
import uuid
from datetime import datetime, timezone
from backend.paths import PROJECT_ROOT
from pathlib import Path
from backend.core.geo_services import GeoError, finite

ROOT=PROJECT_ROOT
SOURCES=ROOT/'data_sources'
SOURCE_TEMPLATE='https://esa-worldcover.s3.eu-central-1.amazonaws.com/v200/2021/map/ESA_WorldCover_10m_2021_v200_{tile}_Map.tif'
PRODUCT={'id':'worldcover_2021_v200','year':2021,'name':'ESA WorldCover 2021 v200','nominal_resolution_m':10,'class_code':40,'license':'CC BY 4.0','source_url':'https://esa-worldcover.org/en/data-access','doi':'https://doi.org/10.5281/zenodo.7254221'}
CLASSES=[(10,'树木覆盖','#006400'),(20,'灌丛','#ffbb22'),(30,'草地','#ffff4c'),(40,'耕作地（Cropland）','#f096ff'),(50,'建设用地','#fa0000'),(60,'裸地/稀疏植被','#b4b4b4'),(70,'冰雪','#f0f0f0'),(80,'永久水体','#0064c8'),(90,'草本湿地','#0096a0'),(95,'红树林','#00cf75'),(100,'苔藓地衣','#fae6a0')]
LIMITATIONS=['2021年公开土地覆盖分类产品统计，不是2026年实况、官方法定耕地保有量、播种面积或高标准农田面积。','使用2017年公开概化参考市界，不声称2021或当前权威市界；不把结果缩放到官方公布面积。','统计使用全原生分类像元；中心落入市界即计入，边界栅格化有像元级近似误差。','未完成仙桃本地独立样本精度与面积误差校正，不能给出当地95%置信区间；全球精度不等于仙桃误差。','地图PNG为重投影降采样显示，不能从显示像素重新计算面积。']
STEPS=[('boundary','核验仙桃市界与口径'),('sources','定位2021分类产品覆盖块'),('account','原生像元全域掩膜与椭球面积'),('visualize','生成配准分布与原生GIS栅格'),('report','校验覆盖并归档分类面积报告')]


def now():return datetime.now(timezone.utc).isoformat(timespec='milliseconds')
def available():return importlib.util.find_spec('rasterio') is not None and importlib.util.find_spec('numpy') is not None and importlib.util.find_spec('PIL') is not None


def load_boundary(source_dir=None):
    directory=Path(source_dir or SOURCES);path=directory/'xiantao_boundary.geojson';meta_path=directory/'xiantao_boundary.metadata.json'
    if not path.is_file() or not meta_path.is_file():raise GeoError('BOUNDARY_UNAVAILABLE','真实仙桃参考市界尚未准备，不能以矩形替代全市边界',422)
    raw=path.read_bytes();geo=json.loads(raw);meta=json.loads(meta_path.read_text('utf-8'))
    if hashlib.sha256(raw).hexdigest()!=meta.get('sha256'):raise GeoError('BOUNDARY_HASH_MISMATCH','市界内容指纹不一致，停止统计')
    if geo.get('type')!='FeatureCollection' or not geo.get('features') or any(f.get('geometry',{}).get('type') not in ('Polygon','MultiPolygon') for f in geo['features']):raise GeoError('BOUNDARY_INVALID','市界必须为有效Polygon/MultiPolygon')
    return geo,meta


def capabilities(source_dir=None):
    ready=available()
    try:
        _,meta=load_boundary(source_dir);region={'id':'xiantao','name':'仙桃市','boundary_available':True,'bbox':meta['bbox'],'boundary_year':meta.get('boundary_year_represented'),'source':meta['provider']}
    except (GeoError,ValueError,KeyError):region={'id':'xiantao','name':'仙桃市','boundary_available':False}
    return {'available':ready and region['boundary_available'],'product':PRODUCT,'region':region,'method':'全原生EPSG:4326分类像元，中心市界掩膜，逐行WGS84椭球面积','limitations':LIMITATIONS}


def ellipsoid_row_pixel_area(north,south,longitude_width):
    """Exact WGS84 ellipsoid lat/lon rectangle area, m² (not geodesic-chord polygon)."""
    if not all(finite(v) for v in (north,south,longitude_width)) or not -90<=south<north<=90 or not 0<longitude_width<=360:raise GeoError('INVALID_AREA_GRID','椭球像元面积参数无效')
    a=6378137.0;f=1/298.257223563;e2=f*(2-f);e=math.sqrt(e2)
    def q(latitude):
        s=math.sin(math.radians(latitude));return (1-e2)*(s/(1-e2*s*s)+math.atanh(e*s)/e)
    return a*a/2*math.radians(longitude_width)*(q(north)-q(south))


def covering_tiles(bbox):
    west,south,east,north=bbox;out=[]
    for lat in range(math.floor(south/3)*3,math.ceil(north/3)*3,3):
        for lon in range(math.floor(west/3)*3,math.ceil(east/3)*3,3):out.append(('N' if lat>=0 else 'S')+f'{abs(lat):02d}'+('E' if lon>=0 else 'W')+f'{abs(lon):03d}')
    return out


def account_native(boundary,metadata,output_dir,check=lambda:None,progress=lambda a,b:None,source_urls=None):
    """Read all native cells in bounded city bbox in strips, with no area resampling."""
    import numpy as np
    import rasterio
    from rasterio.features import geometry_mask
    from rasterio.transform import from_origin
    from rasterio.windows import Window
    directory=Path(output_dir);directory.mkdir(parents=True,exist_ok=True)
    bbox=metadata['bbox'];tiles=covering_tiles(bbox);urls=source_urls or [SOURCE_TEMPLATE.format(tile=t) for t in tiles]
    if len(urls)!=len(tiles):raise GeoError('SOURCE_MISMATCH','覆盖块与数据源数量不一致')
    geoms=[f['geometry'] for f in boundary['features']];class_counts={c:0 for c,_,_ in CLASSES};class_areas={c:0.0 for c,_,_ in CLASSES};inside_count=0;total_area=0.;invalid_count=0;invalid_area=0.;tile_records=[];hasher=hashlib.sha256();processed=0
    env={'GDAL_DISABLE_READDIR_ON_OPEN':'EMPTY_DIR','CPL_VSIL_CURL_ALLOWED_EXTENSIONS':'.tif','GDAL_HTTP_TIMEOUT':'20','GDAL_HTTP_CONNECTTIMEOUT':'10','GDAL_HTTP_MAX_RETRY':'0','GDAL_CACHEMAX':64*1024*1024}
    partial=directory/'classification.partial.tif';native=directory/'classification.tif'
    with rasterio.Env(**env):
        with rasterio.open(urls[0]) as first:
            if first.crs!=rasterio.crs.CRS.from_epsg(4326) or first.count!=1 or first.transform.b!=0 or first.transform.d!=0 or first.transform.a<=0 or first.transform.e>=0:raise GeoError('UNSUPPORTED_GRID','分类产品须为单波段北向上EPSG4326原生格网')
            rx,ry=first.transform.a,-first.transform.e
        west=-180+math.floor((bbox[0]+180)/rx+1e-7)*rx;north=90-math.floor((90-bbox[3])/ry+1e-7)*ry;width=math.ceil((bbox[2]-west)/rx-1e-7);height=math.ceil((north-bbox[1])/ry-1e-7)
        if width*height>180_000_000 or width>20000 or height>20000:raise GeoError('CITY_GRID_LIMIT','市级原生格网超过本地处理上限，不能自动降采样替代统计')
        transform=from_origin(west,north,rx,ry);bounds=[west,north-height*ry,west+width*rx,north];row_counts=np.zeros((height,len(CLASSES)+1),dtype='int64');strips=0
        profile={'driver':'GTiff','height':height,'width':width,'count':1,'dtype':'uint8','crs':'EPSG:4326','transform':transform,'nodata':0,'compress':'deflate','tiled':True,'blockxsize':512,'blockysize':512}
        with rasterio.open(partial,'w',**profile) as target:
            for tile_index,url in enumerate(urls):
                check()
                with rasterio.open(url) as source:
                    if source.crs!=rasterio.crs.CRS.from_epsg(4326) or abs(source.transform.a-rx)>1e-12 or abs(source.transform.e+ry)>1e-12:raise GeoError('GRID_MISMATCH','覆盖块格网不一致，停止而非猜测重采样')
                    c0=max(0,round((source.bounds.left-west)/rx));c1=min(width,round((source.bounds.right-west)/rx));r0=max(0,round((north-source.bounds.top)/ry));r1=min(height,round((north-source.bounds.bottom)/ry))
                    if c1<=c0 or r1<=r0:continue
                    sc=round((west+c0*rx-source.transform.c)/rx);sr=round((source.transform.f-(north-r0*ry))/ry)
                    tile_records.append({'tile':tiles[tile_index],'url':url if source_urls is None else 'test-local-source','crs':'EPSG:4326','native_shape':[source.height,source.width],'pixel_size_degrees':[rx,ry],'source_nodata':source.nodata,'window_shape':[r1-r0,c1-c0],'method':'原生格网完整覆盖窗口，512行条带；未读取overview或重采样'})
                    for dr in range(r0,r1,512):
                        check();rows=min(512,r1-dr);source_window=Window(sc,sr+dr-r0,c1-c0,rows);destination_window=Window(c0,dr,c1-c0,rows)
                        data=source.read(1,window=source_window,masked=False)
                        inside=geometry_mask(geoms,out_shape=data.shape,transform=source.window_transform(source_window),invert=True,all_touched=False)
                        areas=np.asarray([ellipsoid_row_pixel_area(north-(dr+j)*ry,north-(dr+j+1)*ry,rx) for j in range(rows)])
                        per_row_inside=inside.sum(axis=1,dtype='int64');inside_count+=int(per_row_inside.sum());total_area+=float(np.dot(per_row_inside,areas))
                        known=np.zeros(data.shape,dtype='bool')
                        for class_index,(code,_,_) in enumerate(CLASSES):
                            selected=(data==code)&inside;counts=selected.sum(axis=1,dtype='int64');known|=selected;class_counts[code]+=int(counts.sum());class_areas[code]+=float(np.dot(counts,areas));row_counts[dr:dr+rows,class_index]+=counts
                        missing=inside&~known;missing_rows=missing.sum(axis=1,dtype='int64');invalid_count+=int(missing_rows.sum());invalid_area+=float(np.dot(missing_rows,areas));row_counts[dr:dr+rows,-1]+=missing_rows;strips+=1
                        clipped=np.where(known,data,0).astype('uint8');target.write(clipped,1,window=destination_window)
                        hasher.update(f'{c0},{dr},{c1-c0},{rows};'.encode());hasher.update(clipped.tobytes(order='C'))
                        processed+=rows*(c1-c0);progress(min(.98,processed/(width*height)),{'tile':tiles[tile_index],'processed_bbox_pixels':processed,'inside_pixels':inside_count,'crop_pixels':class_counts[40]})
            check()
            target.update_tags(source_product=PRODUCT['name'],source_doi=PRODUCT['doi'],product_year='2021',class_40='Cropland',boundary_provider=metadata.get('provider',''),boundary_year=str(metadata.get('boundary_year_represented','unknown')),boundary_sha256=metadata.get('sha256',''),mask_rule='pixel center; all_touched=False',area_method='WGS84 ellipsoid latitude-band row weighting',license='ESA WorldCover CC BY 4.0; retain boundary attribution separately')
        partial.replace(native)
    if processed!=width*height:raise GeoError('SOURCE_COVERAGE_GAP','源覆盖窗口未完整覆盖市界包络格网，不能报告全市覆盖率')
    with (directory/'row_counts.csv').open('w',newline='') as row_file:
        writer=csv.writer(row_file);writer.writerow(['row','north','south','pixel_width_deg','total_pixels','nodata_pixels']+['class_'+str(c) for c,_,_ in CLASSES])
        for row_index,counts in enumerate(row_counts):writer.writerow([row_index,north-row_index*ry,north-(row_index+1)*ry,rx,int(counts.sum()),int(counts[-1])]+[int(v) for v in counts[:-1]])
    if inside_count==0:raise GeoError('EMPTY_CITY_MASK','市界内没有原生像元，停止统计')
    valid_area=sum(class_areas.values());valid_count=sum(class_counts.values());coverage=min(1.0,max(0.0,valid_area/total_area)) if total_area else 0
    if abs(valid_area+invalid_area-total_area)>max(.01,total_area*1e-9):raise GeoError('AREA_BALANCE_ERROR','分类面积加缺测面积未守恒')
    summary={'cropland_ha':class_areas[40]/10000,'cropland_mu':class_areas[40]/10000*15,'total_area_ha':total_area/10000,'valid_area_ha':valid_area/10000,'nodata_area_ha':invalid_area/10000,'coverage_ratio':coverage,'cropland_share':class_areas[40]/total_area,'inside_pixel_count':inside_count,'valid_pixel_count':valid_count,'nodata_pixel_count':invalid_count,'cropland_pixel_count':class_counts[40],'reference_year':2021,'nominal_resolution_m':10,'method':'WGS84椭球纬带积分；逐原生行面积权重；市界像元中心掩膜','accuracy_note':'未经仙桃独立样本验证或面积误差校正，不提供当地置信区间；非官方耕地数','ci95':None,'is_official':False,'raster_dimensions':[height,width],'native_pixel_size_degrees':[rx,ry],'processed_bbox_pixel_count':processed,'strips_read':strips,'rastermask_vs_vector_percent':(total_area/1000000/metadata.get('validation',{}).get('boundary_area_km2')-1)*100 if metadata.get('validation',{}).get('boundary_area_km2') else None,'boundary_year':metadata.get('boundary_year_represented'),'boundary_reference_area_km2':metadata.get('validation',{}).get('boundary_area_km2'),'official_reference_area_km2':metadata.get('validation',{}).get('official_reference_area_km2'),'boundary_difference_percent':metadata.get('validation',{}).get('difference_percent')}
    classes=[{'code':code,'name':name,'area_ha':class_areas[code]/10000,'area_mu':class_areas[code]/10000*15,'pixel_count':class_counts[code],'share':class_areas[code]/total_area,'color':color} for code,name,color in CLASSES]
    return {'summary':summary,'classes':classes,'native_path':str(native),'bounds':bounds,'source_tiles':tile_records,'classified_window_sha256':hasher.hexdigest(),'coverage_warning':'存在未分类/缺测，分类面积不是全覆盖估计' if coverage<.9999 else ''}


def make_preview(native_path,output_dir):
    import numpy as np
    import rasterio
    from rasterio.warp import reproject,transform_bounds,Resampling
    from rasterio.transform import from_bounds
    from PIL import Image
    with rasterio.open(native_path) as src:
        mercator_bounds=transform_bounds(src.crs,'EPSG:3857',*src.bounds,densify_pts=21)
        mw,mh=mercator_bounds[2]-mercator_bounds[0],mercator_bounds[3]-mercator_bounds[1];width=max(1,min(1536,src.width));height=max(1,round(width*mh/mw))
        if height>1536:width=max(1,round(width*1536/height));height=1536
        destination=np.zeros((height,width),dtype='uint8');transform=from_bounds(*mercator_bounds,width,height)
        reproject(source=rasterio.band(src,1),destination=destination,src_transform=src.transform,src_crs=src.crs,dst_transform=transform,dst_crs='EPSG:3857',src_nodata=0,dst_nodata=0,resampling=Resampling.nearest)
        rgba=np.zeros((height,width,4),dtype='uint8');crop=destination==40;rgba[crop]=[240,150,255,210];Image.fromarray(rgba).save(Path(output_dir)/'overlay.png')
        rgba2=np.zeros_like(rgba)
        for code,_,color in CLASSES:
            selected=destination==code;rgba2[selected]=[int(color[i:i+2],16) for i in (1,3,5)]+[185]
        Image.fromarray(rgba2).save(Path(output_dir)/'classes.png')
        return {'bounds':[src.bounds.left,src.bounds.bottom,src.bounds.right,src.bounds.top],'width':width,'height':height,'display_crs':'EPSG:3857','description':'仅code40耕作地的透明分布；重投影降采样仅用于显示，统计使用全部原生像元'}


class LandcoverServices:
    def __init__(self,store,output_dir=None,source_dir=None):
        self.store=store;self.output_dir=Path(output_dir or Path(store.db_path).parent/'landcover');self.source_dir=source_dir;self.stopping=False;self.threads=[]
        for task in store.list('landcover_task'):
            if task['status'] in ('queued','running'):
                task.update(status='failed',error={'code':'INTERRUPTED','message':'服务重启，面积核算中断；请重试'},finished_at=now())
                for node in task.get('nodes',[]):
                    if node.get('status')=='running':node.update(status='failed',message='服务重启中断',finished_at=now())
                store.save('landcover_task',task)
    def get(self,task_id):return self.store.get('landcover_task',task_id)
    def list(self):return self.store.list('landcover_task')
    def create(self,body):
        if not isinstance(body,dict) or set(body)-{'region','product','requirement'}:raise GeoError('INVALID_LANDCOVER_REQUEST','面积请求包含不支持字段',400)
        if body.get('region','xiantao') not in ('xiantao','仙桃市'):raise GeoError('REGION_UNSUPPORTED','当前仅支持仙桃全市，不能将其他地区默默套用仙桃市界',422)
        if body.get('product','worldcover_2021_v200')!='worldcover_2021_v200':raise GeoError('YEAR_MISMATCH','当前仅支持ESA WorldCover2021分类基准，不支持指定产品年份',422)
        requirement=body.get('requirement','核算仙桃全市2021年公开分类产品耕作地面积')
        if not isinstance(requirement,str) or not requirement.strip() or len(requirement)>2000:raise GeoError('INVALID_REQUIREMENT','需求须为受限非空文本',400)
        years=re.findall(r'(?<!\d)20\d{2}(?!\d)',requirement)
        if any(year!='2021' for year in years):raise GeoError('YEAR_MISMATCH','当前只有2021公开分类产品基准；请求包含其他年份，不能自动改成2021。请明确请求2021参考基准。',422)
        if any(word in requirement for word in ('全省','省域','全国','全湖北')) or ('湖北省' in requirement and '仙桃' not in requirement):raise GeoError('REGION_UNSUPPORTED','当前仅核算仙桃全市，省域或全国请求不能用仙桃市界替代',422)
        mentions=re.findall(r'((?:(?!市|县)[\u4e00-\u9fff]){1,10}(?:市|县))',requirement)
        for mention in mentions:
            cleaned=re.sub(r'统计|核算|计算|分析|请|我要|帮我|参考|全部|完整|展示|显示|查看|呈现|同时|并且|以及|并|及|要|的','',mention)
            if '仙桃' not in mention and cleaned not in ('全市','全县','市','县'):raise GeoError('REGION_UNSUPPORTED','检测到其他地区表述，当前仅支持仙桃全市，不自动替换请求范围',422)
        plan={'region':'仙桃市','reference_year':2021,'product':PRODUCT,'metric_definition':'2017公开参考仙桃市界内，ESA WorldCover2021 v200 code40分类面积','method':'全原生像元中心掩膜+WGS84椭球逐行面积，不用NDVI推断、不作面积外推','requirement':requirement,'limitations':LIMITATIONS}
        task={'id':'landcover_'+uuid.uuid4().hex[:14],'name':'仙桃全市2021耕作地分类面积基准','status':'queued','progress':0,'plan':plan,'snapshot':copy.deepcopy(plan),'nodes':[{'id':id,'name':name,'status':'pending','message':'等待执行'} for id,name in STEPS],'logs':[{'time':now(),'message':'创建全市分类产品统计任务；非当前官方耕地调查'}],'result':None,'created_at':now()}
        with self.store.lock:
            if self.stopping:raise GeoError('SERVICE_STOPPING','服务正在关闭',503)
            if any(t['status'] in ('queued','running') for t in self.list()):raise GeoError('LANDCOVER_BUSY','本机已有全市面积任务正在执行',429)
            self.store.save('landcover_task',task);thread=threading.Thread(target=self.execute,args=(task['id'],),daemon=True);self.threads.append(thread);thread.start()
        return task
    def check(self,task_id):
        if self.stopping:raise GeoError('INTERRUPTED','服务关闭，停止处理')
        if self.get(task_id)['status']=='cancelled':raise GeoError('CANCELLED','用户取消')
    def change(self,task_id,fn):
        with self.store.lock:
            task=self.get(task_id)
            if task['status'] in ('succeeded','failed','cancelled'):return False
            fn(task);self.store.save('landcover_task',task);return True
    def execute(self,task_id):
        try:
            directory=self.output_dir/task_id;boundary=meta=statistics=preview=None
            for index,(id,name) in enumerate(STEPS):
                self.check(task_id)
                def start(t):t.update(status='running');t['nodes'][index].update(status='running',started_at=now(),message=name);t['logs'].append({'time':now(),'message':'开始：'+name})
                if not self.change(task_id,start):return
                if id=='boundary':
                    if not available():raise GeoError('RASTER_ENGINE_UNAVAILABLE','当前Python缺少rasterio/numpy/Pillow')
                    boundary,meta=load_boundary(self.source_dir);summary={'真实市界来源':meta['provider'],'边界年份':meta.get('boundary_year_represented'),'bbox':meta['bbox'],'边界哈希':meta['sha256']}
                elif id=='sources':summary={'产品':PRODUCT,'覆盖块':covering_tiles(meta['bbox']),'数据年份':2021,'分类代码':40,'原生统计':'所有市界内像元，不重采样'}
                elif id=='account':
                    def progress(fraction,detail):
                        self.check(task_id)
                        def update(t):t['progress']=15+round(fraction*65);t['nodes'][index]['output_summary']=detail;t['nodes'][index]['message']='全原生格网已扫描%.1f%%'%(fraction*100)
                        self.change(task_id,update)
                    statistics=account_native(boundary,meta,directory,lambda:self.check(task_id),progress);summary=statistics['summary']
                elif id=='visualize':preview=make_preview(statistics['native_path'],directory);summary={'显示基准':preview['display_crs'],'图片尺寸':[preview['width'],preview['height']],'统计未降采样':True,'原生GeoTIFF字节':(directory/'classification.tif').stat().st_size}
                else:
                    summary={'面积守恒':abs(sum(c['area_ha'] for c in statistics['classes'])+statistics['summary']['nodata_area_ha']-statistics['summary']['total_area_ha'])<1e-5,'有效覆盖率':statistics['summary']['coverage_ratio'],'分类数':len(CLASSES),'不是当前官方耕地数':True}
                self.check(task_id)
                def finish(t):
                    t['nodes'][index].update(status='succeeded',finished_at=now(),message='实际处理完成',output_summary=summary);t['progress']=[5,15,80,95,100][index];t['logs'].append({'time':now(),'message':'完成：'+name})
                    if id=='report':
                        image={**preview,'url':'/api/landcover/tasks/'+task_id+'/overlay.png','classes_url':'/api/landcover/tasks/'+task_id+'/classes.png'}
                        provenance={'product':PRODUCT,'year':2021,'class_code':40,'boundary_source':meta,'boundary_sha256':meta['sha256'],'source_tiles':statistics['source_tiles'],'classified_window_sha256':statistics['classified_window_sha256'],'method':statistics['summary']['method'],'mask_rule':'pixel center inside polygon, all_touched=False','statistics_resampled':False,'display_crs':'EPSG:3857','source_type':'public_classification_product','local_accuracy_ci95':None,'created_at':now()}
                        findings=['2021产品code40分类面积 %.3f公顷（%.3f亩）。'%(statistics['summary']['cropland_ha'],statistics['summary']['cropland_mu']),'全原生有效覆盖率%.6f%%；未分类/缺测%.3f公顷。'%(statistics['summary']['coverage_ratio']*100,statistics['summary']['nodata_area_ha']),'边界为2017概化公开参考，不能当作2021或当前权威边界；不强制匹配统计年鉴面积。']
                        result={'summary':statistics['summary'],'classes':statistics['classes'],'boundary':boundary,'image':image,'report':{'title':'仙桃市2021年公开土地覆盖分类面积基准报告','findings':findings,'limitations':LIMITATIONS+meta.get('limitations',[]),'steps':copy.deepcopy(t['nodes'])},'provenance':provenance,'artifacts':[{'name':'原生分类GeoTIFF','url':'/api/landcover/tasks/'+task_id+'/classification.tif'},{'name':'逐行分类计数CSV','url':'/api/landcover/tasks/'+task_id+'/row_counts.csv'},{'name':'完整结果JSON','url':'/api/landcover/tasks/'+task_id+'/result.json'}]}
                        directory.mkdir(parents=True,exist_ok=True);(directory/'result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),'utf-8');t.update(status='succeeded',result=result,finished_at=now())
                if not self.change(task_id,finish):return
        except Exception as e:
            code=e.code if isinstance(e,GeoError) else 'LANDCOVER_FAILED';message=e.message if isinstance(e,GeoError) else '全市分类面积处理失败；未用抽样或伪造数字替代，请检查源网络与引擎'
            def fail(t):
                t.update(status='failed',error={'code':code,'message':message},finished_at=now());t['logs'].append({'time':now(),'message':message})
                for node in t['nodes']:
                    if node['status']=='running':node.update(status='failed',message=message,finished_at=now())
            self.change(task_id,fail)
    def artifact(self,task_id,name):
        task=self.get(task_id)
        if task['status']!='succeeded' or name not in ('overlay.png','classes.png','classification.tif','result.json','row_counts.csv'):raise GeoError('ARTIFACT_UNAVAILABLE','成果尚未完成或不存在',404)
        path=self.output_dir/task_id/name
        if not path.is_file():raise GeoError('ARTIFACT_UNAVAILABLE','成果文件不可用',404)
        return path
    def cancel(self,task_id):
        with self.store.lock:
            task=self.get(task_id)
            if task['status'] not in ('queued','running'):raise GeoError('INVALID_STATE','只有待处理或运行任务可取消',409)
            task.update(status='cancelled',finished_at=now());task['logs'].append({'time':now(),'message':'用户取消，不发布未完成结果'})
            for n in task['nodes']:
                if n['status'] in ('pending','running'):n.update(status='cancelled',finished_at=now(),message='用户取消')
            return self.store.save('landcover_task',task)
    def retry(self,task_id):
        old=self.get(task_id)
        if old['status'] not in ('failed','cancelled'):raise GeoError('INVALID_STATE','仅失败/取消任务可重试',409)
        return self.create({'requirement':old['plan']['requirement']})
    def close(self):
        self.stopping=True
        for thread in self.threads:thread.join(timeout=1)
