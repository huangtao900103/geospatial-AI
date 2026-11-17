"""Fixed-provider IO Annual LULC 2024 native-window classification extraction.

Crops=5. Clouds=10 and NoData=0 are unknown, never non-cropland evidence.
The result is a historical annual product, not current-image classification.
"""
import copy
import csv
import hashlib
import json
import math
import re
import time
from pathlib import Path
from urllib.parse import urlencode, urlsplit
from urllib.request import Request, build_opener, HTTPRedirectHandler
from urllib.error import HTTPError, URLError

from backend.core.geo_services import GeoError

COLLECTION = 'io-10m-annual-lulc'
CATALOG = 'https://api.impactobservatory.com/stac-aws/collections/' + COLLECTION + '/items'
DATA_HOST = 'io-10m-annual-lulc.s3.us-west-2.amazonaws.com'
AREA_METHOD = 'native_utm_projected_grid'
CLASSES = [(1,'水体','#419bdf'),(2,'树木覆盖','#397d49'),(4,'淹水植被','#7a87c6'),
           (5,'作物覆盖（Crops）','#e49635'),(7,'建设用地','#c4281b'),
           (8,'裸地','#a59b8f'),(9,'冰雪','#a8ebff'),(11,'草灌地','#e3e2c3')]
CLOUD_CLASS = (10,'云（未知分类）','#616161')
LIMITATIONS = [
    '提取的是IO Annual LULC 2024年度Crops类别5，不是2026实况、当期影像新分类或官方耕地调查。',
    'Crops是年度土地覆盖模型类别，不代表确权地块、永久基本农田、高标准农田或水稻等作物细分。',
    '0缺测、10云及未识别类别均为未知区域，不计入有效分类覆盖，也不能当作非农田。',
    '面积为原生10米UTM投影格网面积，单像元100平方米；不是WGS84椭球面积或法定调查面积。',
    '按像元中心纳入选区，保留完整像元轮廓；边缘可越选区约半像元。图斑为四邻域连通范围，不简化、不滤小斑。',
    '未经本地独立样本精度验证或面积误差校正；有效覆盖率不等于分类精度。底图时相未知时仅供定位参考。',
    '当前只接受单个IO瓦片完整覆盖选区；多瓦片、多投影请求明确拒绝，不隐式拼接或回退到2021产品。',
    '不同产品的类别定义和统计方法不同，不能把IO2024与WorldCover2021面积差直接解释为耕地增减。',
    '不同分类产品的模型和类别口径不同，不直接将WorldCover2021与IO2024面积差解释为耕地增减。',
]
PRODUCT = {'id':'io_lulc_2024','source_id':'io_lulc_2024','year':2024,
           'name':'Impact Observatory / Esri Annual LULC 2024','nominal_resolution_m':10,'class_code':5,
           'license':'CC BY 4.0','license_url':'https://creativecommons.org/licenses/by/4.0/',
           'source_url':'https://registry.opendata.aws/io-lulc/',
           'catalog_url':CATALOG,'collection':COLLECTION,'area_method':AREA_METHOD,
           'attribution':'Impact Observatory / Microsoft / Esri 10m Annual LULC 2024 · CC BY 4.0',
           'limitations':LIMITATIONS}


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self,*args,**kwargs):
        raise GeoError('SOURCE_REDIRECT_BLOCKED','分类目录发生重定向；停止而非访问未登记来源',502)


def fetch_catalog(bbox):
    query={'bbox':','.join(format(v,'.10f') for v in bbox),
           'datetime':'2024-01-01T00:00:00Z/2024-12-31T23:59:59Z','limit':'6'}
    url=CATALOG+'?'+urlencode(query)
    for attempt in range(2):
        try:
            opener=build_opener(NoRedirect())
            with opener.open(Request(url,headers={'Accept':'application/geo+json, application/json','User-Agent':'GeoAI-IO-LULC/1.0'}),timeout=20) as response:
                raw=response.read(3_000_001)
            if len(raw)>3_000_000:raise GeoError('CATALOG_LIMIT','分类目录响应过大，停止处理',502)
            payload=json.loads(raw)
            if not isinstance(payload,dict):raise ValueError()
            payload['_geoai_transport']={'attempts':attempt+1,'max_attempts':2}
            return payload
        except GeoError:raise
        except HTTPError as error:
            if attempt==0 and error.code in (408,429,500,502,503,504):time.sleep(.3);continue
            raise GeoError('IO_CATALOG_UNAVAILABLE','IO2024公开分类目录查询失败（HTTP '+str(error.code)+'），未切换其他年度或模拟数据',502)
        except (URLError,TimeoutError,OSError):
            if attempt==0:time.sleep(.3);continue
            raise GeoError('IO_CATALOG_UNAVAILABLE','IO2024公开分类目录网络暂不可用，同源重试后仍未成功；未切换其他年度或模拟数据',502)
        except Exception:raise GeoError('IO_CATALOG_INVALID','IO2024公开分类目录格式无效，未生成替代结果',502)


def validate_item(payload):
    items=payload.get('features') if isinstance(payload,dict) else None
    if not isinstance(items,list):raise GeoError('IO_CATALOG_INVALID','IO分类目录返回格式无效',502)
    if len(items)==0:raise GeoError('IO_NO_COVERAGE','IO2024目录未命中此选区',422)
    if len(items)!=1 or any(l.get('rel')=='next' for l in payload.get('links',[]) if isinstance(l,dict)):
        raise GeoError('MULTI_TILE_NOT_SUPPORTED','选区涉及多个IO瓦片；当前不支持跨瓦片/跨投影统计，请缩小或拆分选区',422)
    item=items[0]
    if not isinstance(item,dict) or item.get('collection')!=COLLECTION:raise GeoError('IO_CATALOG_INVALID','IO目录集合不匹配',502)
    match=re.fullmatch(r'(\d{2}[C-HJ-NP-X])-2024',str(item.get('id','')))
    if not isinstance(item.get('properties'),dict) or not isinstance(item.get('assets'),dict) or not isinstance(item['assets'].get('supercell'),dict):
        raise GeoError('IO_CATALOG_INVALID','IO目录缺少有效属性或分类资产',502)
    prop=item['properties'];asset=item['assets']['supercell']
    href=asset.get('href');parts=urlsplit(href) if isinstance(href,str) else None
    if not match or prop.get('datetime')!='2024-01-01T00:00:00Z' or not str(prop.get('end_datetime','')).startswith('2024-12-31T23:59:59'):
        raise GeoError('IO_VERSION_MISMATCH','IO目录未返回2024年度预期版本，停止而非换年',502)
    expected='/'+match.group(1)+'_2024.tif'
    if not parts or parts.scheme!='https' or parts.hostname!=DATA_HOST or parts.netloc!=DATA_HOST or parts.path!=expected or parts.query or parts.fragment:
        raise GeoError('IO_ASSET_BLOCKED','IO分类资产地址不在已登记官方COG范围',502)
    epsg=asset.get('proj:epsg')
    if epsg is None:
        code=prop.get('proj:code','');epsg=int(code[5:]) if re.fullmatch(r'EPSG:\d+',str(code)) else None
    if isinstance(epsg,bool) or not isinstance(epsg,int) or not (32601<=epsg<=32660 or 32701<=epsg<=32760):
        raise GeoError('IO_GRID_UNSUPPORTED','IO分类资产不是已支持的WGS84 UTM投影',422)
    class_values=asset.get('classification:classes')
    if not isinstance(class_values,list):raise GeoError('IO_CLASS_MISMATCH','IO目录缺少类别契约声明',502)
    declared={v.get('value'):v.get('name') for v in class_values if isinstance(v,dict)}
    if declared.get(5)!='crops' or declared.get(10)!='clouds':
        raise GeoError('IO_CLASS_MISMATCH','IO分类类别声明不符合Crops5/Clouds10契约',502)
    return {'item_id':item['id'],'href':href,'epsg':epsg,
            'catalog_attempts':payload.get('_geoai_transport',{}).get('attempts',1),
            'metadata_sha256':hashlib.sha256(json.dumps(item,sort_keys=True,separators=(',',':')).encode()).hexdigest(),
            'start_datetime':prop.get('start_datetime',prop['datetime']),'end_datetime':prop['end_datetime']}


def densified_rectangle(bbox,segments=32):
    w,s,e,n=bbox;points=[]
    for first,last in [((w,s),(e,s)),((e,s),(e,n)),((e,n),(w,n)),((w,n),(w,s))]:
        for k in range(segments):
            f=k/segments;points.append([first[0]+f*(last[0]-first[0]),first[1]+f*(last[1]-first[1])])
    points.append(points[0]);return {'type':'Polygon','coordinates':[points]}


class IOLulcReader:
    """Constructor injections are test-only; API bodies never accept paths/URLs."""
    def __init__(self,catalog_fetch=None,source_path=None):
        self.catalog_fetch=catalog_fetch or fetch_catalog;self.source_path=source_path

    def account(self,bbox,metadata,output_dir,check=lambda:None,progress=lambda a,b:None,max_cells=1_048_576):
        import numpy as np
        import rasterio
        from rasterio.features import geometry_mask
        from rasterio.warp import transform_geom
        from rasterio.windows import Window
        check();binding=validate_item(self.catalog_fetch(bbox));directory=Path(output_dir);directory.mkdir(parents=True,exist_ok=True)
        environment={'GDAL_DISABLE_READDIR_ON_OPEN':'EMPTY_DIR','CPL_VSIL_CURL_ALLOWED_EXTENSIONS':'.tif',
                     'GDAL_HTTP_TIMEOUT':'20','GDAL_HTTP_CONNECTTIMEOUT':'10','GDAL_HTTP_MAX_RETRY':'0',
                     'GDAL_CACHEMAX':64*1024*1024,'CPL_VSIL_CURL_USE_HEAD':'YES'}
        try:
            with rasterio.Env(**environment),rasterio.open(self.source_path or binding['href']) as src:
                epsg=src.crs.to_epsg() if src.crs else None;t=src.transform
                if epsg!=binding['epsg'] or src.count!=1 or abs(t.a-10)>1e-8 or abs(t.e+10)>1e-8 or t.b!=0 or t.d!=0 or src.dtypes[0]!='uint8' or src.nodata!=0:
                    raise GeoError('IO_GRID_MISMATCH','IO原生栅格的CRS、10米格网、类别波段或缺测值不符合声明',502)
                geom=transform_geom('EPSG:4326',src.crs,densified_rectangle(bbox))
                points=geom['coordinates'][0];xs=[p[0] for p in points];ys=[p[1] for p in points]
                left,bottom,right,top=min(xs),min(ys),max(xs),max(ys)
                if left<src.bounds.left-1e-6 or right>src.bounds.right+1e-6 or bottom<src.bounds.bottom-1e-6 or top>src.bounds.top+1e-6:
                    raise GeoError('SOURCE_COVERAGE_GAP','单个IO原生瓦片未完整覆盖选区，不能报告全区结果',422)
                c0=math.floor((left-t.c)/t.a);c1=math.ceil((right-t.c)/t.a)
                r0=math.floor((t.f-top)/(-t.e));r1=math.ceil((t.f-bottom)/(-t.e));width,height=c1-c0,r1-r0
                if width<=0 or height<=0 or width*height>max_cells:raise GeoError('AOI_GRID_LIMIT','IO原生窗口为空或超过处理上限',422)
                window=Window(c0,r0,width,height);native_transform=src.window_transform(window)
                check();data=src.read(1,window=window,masked=False)
                if data.shape!=(height,width):raise GeoError('SOURCE_COVERAGE_GAP','IO原生窗口读取不完整',502)
                inside=geometry_mask([geom],out_shape=data.shape,transform=native_transform,invert=True,all_touched=False)
                inside_count=int(inside.sum())
                if inside_count==0:raise GeoError('EMPTY_AOI_MASK','选区内没有原生像元中心，请扩大范围',422)
                codes=[c[0] for c in CLASSES];valid=inside&np.isin(data,codes);crop=inside&(data==5)
                cloud=inside&(data==10);unknown=inside&~valid
                clipped=np.where(inside,data,0).astype('uint8')
                # Preserve unknown class codes in the raster, but never count them as valid.
                partial=directory/'classification.partial.tif';native=directory/'classification.tif'
                with rasterio.open(partial,'w',driver='GTiff',width=width,height=height,count=2,dtype='uint8',
                                   crs=src.crs,transform=native_transform,nodata=0,compress='deflate') as dst:
                    dst.write(clipped,1);dst.write(inside.astype('uint8'),2)
                    dst.set_band_description(1,'classification');dst.set_band_description(2,'aoi_mask')
                    dst.update_tags(source_product=PRODUCT['name'],product_year='2024',crop_class='5',
                                    source_id=PRODUCT['id'],area_method=AREA_METHOD,class_10='Clouds: unknown',
                                    mask_rule='pixel center in projected densified WGS84 rectangle; all_touched=False',
                                    license=PRODUCT['license'],attribution=PRODUCT['attribution'])
                partial.replace(native)
                digest=hashlib.sha256();digest.update(clipped.tobytes());digest.update(inside.astype('uint8').tobytes());digest.update(str(native_transform).encode());digest.update(src.crs.to_string().encode())
                native_bounds=list(rasterio.windows.bounds(window,src.transform))
                classes=[]
                for code,name,color in CLASSES:
                    count=int((inside&(data==code)).sum());classes.append({'code':code,'name':name,'color':color,'pixel_count':count,
                        'area_ha':count*.01,'area_mu':count*.15,'share':count/inside_count})
                with (directory/'row_counts.csv').open('w',encoding='utf-8-sig',newline='') as stream:
                    writer=csv.writer(stream);writer.writerow(['row','northing','southing','pixel_area_m2','inside_pixels','unknown_pixels','cloud_pixels']+['class_'+str(c) for c in codes])
                    for row in range(height):
                        counts=[int((inside[row]&(data[row]==c)).sum()) for c in codes]
                        writer.writerow([row,native_transform.f-row*10,native_transform.f-(row+1)*10,100,int(inside[row].sum()),int(unknown[row].sum()),int(cloud[row].sum())]+counts)
                valid_count=int(valid.sum());unknown_count=int(unknown.sum());crop_count=int(crop.sum())
                summary={'cropland_ha':crop_count*.01,'cropland_mu':crop_count*.15,'total_area_ha':inside_count*.01,
                         'valid_area_ha':valid_count*.01,'nodata_area_ha':unknown_count*.01,'coverage_ratio':valid_count/inside_count,
                         'cropland_share':crop_count/inside_count,'inside_pixel_count':inside_count,'valid_pixel_count':valid_count,
                         'nodata_pixel_count':unknown_count,'unknown_pixel_count':unknown_count,'unknown_area_ha':unknown_count*.01,
                         'cloud_pixel_count':int(cloud.sum()),'cloud_area_ha':int(cloud.sum())*.01,'cropland_pixel_count':crop_count,
                         'cropland_area_observed_only':True,'full_classified_coverage':unknown_count==0,
                         'reference_year':2024,'class_code':5,'nominal_resolution_m':10,'source_id':PRODUCT['id'],
                         'method':'IO2024类别5；UTM10米原生格网投影面积；选区像元中心掩膜',
                         'area_method':AREA_METHOD,'area_unit':'m2_native_projected_grid','pixel_area_m2':100,
                         'native_crs':src.crs.to_string(),'grid_resolution_m':10,'ci95':None,'is_official':False,
                         'raster_dimensions':[height,width],'processed_bbox_pixel_count':width*height,'strips_read':1,
                         'accuracy_note':'未经该选区独立样本验证或面积误差校正，非官方调查；全量仅指产品类别5像元',
                         'bbox':list(bbox),'boundary_year':None,'boundary_reference_area_km2':None}
                if valid_count+unknown_count!=inside_count:raise GeoError('AREA_BALANCE_ERROR','IO有效和未知面积不守恒',502)
                record={'tile':binding['item_id'],'url':binding['href'],'native_shape':[src.height,src.width],
                        'crs':src.crs.to_string(),'pixel_size_m':[10,10],'window_shape':[height,width],
                        'window':[c0,r0,width,height],'source_nodata':0,'metadata_sha256':binding['metadata_sha256'],
                        'method':'原生10米窗口完整读取，未读取overview或重采样'}
                check();progress(.98,{'processed_bbox_pixels':width*height,'inside_pixels':inside_count,'crop_pixels':crop_count})
                return {'summary':summary,'classes':classes,'native_path':str(native),'bounds':native_bounds,
                        'source_tiles':[record],'source_binding':binding,'classified_window_sha256':digest.hexdigest(),
                        'coverage_warning':'存在云或缺测；未知区域不能当作非农田' if unknown_count else ''}
        except GeoError:raise
        except Exception:raise GeoError('IO_PIXEL_READ_FAILED','IO2024官方原生COG读取失败；未用2021产品或模拟数据代替',502)


def vectorize_crops(native_path,output_dir,check=lambda:None,max_cells=1_048_576,max_patches=20_000):
    import numpy as np
    import rasterio
    from rasterio.features import shapes,geometry_mask,bounds as geometry_bounds
    from rasterio.windows import Window
    from rasterio.warp import transform_geom
    features=[];total_count=0
    with rasterio.open(native_path) as src:
        if src.width*src.height>max_cells or src.count!=2 or src.descriptions!=('classification','aoi_mask'):
            raise GeoError('IO_VECTOR_GRID_INVALID','IO矢量化需要原生分类和选区掩膜',502)
        data=src.read(1);inside=src.read(2)==1;crop=(data==5)&inside;expected=int(crop.sum())
        for index,(geometry,value) in enumerate(shapes(data,mask=crop,connectivity=4,transform=src.transform)):
            check()
            if index>=max_patches:raise GeoError('PATCH_LIMIT','选区碎斑超过20000个；请缩小选区，系统不会过滤小图斑',422)
            if int(value)!=5:raise GeoError('CLASS_CODE_MISMATCH','IO图斑出现非作物类别',502)
            left,bottom,right,top=geometry_bounds(geometry);t=src.transform
            c0=max(0,round((left-t.c)/t.a));c1=min(src.width,round((right-t.c)/t.a))
            r0=max(0,round((t.f-top)/(-t.e)));r1=min(src.height,round((t.f-bottom)/(-t.e)))
            mask=geometry_mask([geometry],out_shape=(r1-r0,c1-c0),transform=src.window_transform(Window(c0,r0,c1-c0,r1-r0)),invert=True,all_touched=False)
            if np.any(mask&~crop[r0:r1,c0:c1]):raise GeoError('PATCH_MASK_MISMATCH','IO图斑包含非作物或选区外像元',502)
            count=int(mask.sum());total_count+=count;patch_id='crop-'+str(index+1).zfill(6)
            feature={'type':'Feature','id':patch_id,'geometry':transform_geom(src.crs,'EPSG:4326',geometry),
                     'properties':{'patch_id':patch_id,'class_code':5,'class_name':'作物覆盖参考图斑','reference_year':2024,
                                   'pixel_count':count,'area_m2':count*100.,'area_ha':count*.01,'area_mu':count*.15,
                                   'area_method':AREA_METHOD,'native_crs':src.crs.to_string(),'connectivity':4,'is_cadastral_parcel':False}}
            features.append(feature)
    if total_count!=expected:raise GeoError('PATCH_AREA_BALANCE_ERROR','IO图斑与原生作物像元数不守恒',502)
    result={'type':'FeatureCollection','features':features,'metadata':{'product':PRODUCT['name'],'year':2024,'class_code':5,
            'crs':'EPSG:4326','native_crs':src.crs.to_string(),'area_method':AREA_METHOD,'connectivity':4,
            'geometry_simplified':False,'is_cadastral_parcel':False,'limitations':LIMITATIONS}}
    folder=Path(output_dir);folder.mkdir(parents=True,exist_ok=True)
    (folder/'cropland.geojson').write_text(json.dumps(result,ensure_ascii=False,separators=(',',':')),'utf-8')
    with (folder/'patches.csv').open('w',encoding='utf-8-sig',newline='') as stream:
        fields=['patch_id','class_code','reference_year','pixel_count','area_m2','area_ha','area_mu','area_method','native_crs','connectivity','is_cadastral_parcel']
        writer=csv.DictWriter(stream,fieldnames=fields,extrasaction='ignore');writer.writeheader()
        for feature in features:writer.writerow(feature['properties'])
    return {'geojson':result,'patch_count':len(features),'pixel_count':total_count,'area_m2':total_count*100.,'balance_verified':True}


def make_preview(native_path,output_dir):
    import numpy as np
    import rasterio
    from rasterio.warp import transform_bounds,reproject,Resampling
    from rasterio.transform import from_bounds
    from PIL import Image
    with rasterio.open(native_path) as src:
        bb=transform_bounds(src.crs,'EPSG:3857',*src.bounds,densify_pts=21)
        width=min(1536,src.width);height=max(1,round(width*(bb[3]-bb[1])/(bb[2]-bb[0])))
        if height>1536:width=max(1,round(width*1536/height));height=1536
        dst=np.zeros((height,width),dtype='uint8')
        reproject(source=rasterio.band(src,1),destination=dst,src_transform=src.transform,src_crs=src.crs,
                  dst_transform=from_bounds(*bb,width,height),dst_crs='EPSG:3857',src_nodata=0,dst_nodata=0,resampling=Resampling.nearest)
        crop=np.zeros((height,width,4),dtype='uint8');crop[dst==5]=[60,200,137,210]
        Image.fromarray(crop).save(Path(output_dir)/'overlay.png');all_classes=np.zeros_like(crop)
        for code,name,color in CLASSES+[CLOUD_CLASS]:
            all_classes[dst==code]=[int(color[i:i+2],16) for i in (1,3,5)]+[180]
        Image.fromarray(all_classes).save(Path(output_dir)/'classes.png')
        bounds=list(transform_bounds('EPSG:3857','EPSG:4326',*bb,densify_pts=21))
        return {'bounds':bounds,'width':width,'height':height,'display_crs':'EPSG:3857',
                'statistics_resampled':False,'display_resampling':'nearest; preview only','attribution':PRODUCT['attribution']}
