"""Local Otsu segmentation of a frozen, calibrated native spectral raster.

Otsu maximizes between-class histogram variance. It is a spectral partition,
not a trained water/crop classifier and not a Google Earth Engine cloud call.
"""
import copy,csv,json,math
from pathlib import Path
from backend.core.geo_services import GeoError

VERSION='otsu-native-1.0'
FILES=('analysis.tif','overlay.png','result.geojson','patches.csv','result.json')
MAX_CELLS=1048576
MAX_PATCHES=2500
LIMITATIONS=['Otsu仅将指数分布自动分成两组，不直接认定水体、农田、作物或灾损。','单景定标、质量掩膜、混合像元和直方图分箱影响阈值；有效覆盖不是分类精度。','斑块按四邻域连通，最小像元数筛选会移除细小信号；原始命中和移除数单独报告。','面积为原生投影格网条件面积，连通斑块不是权属地块；缺测不当作未命中。']


def otsu(values,bins=128):
 import numpy as np
 values=np.asarray(values,dtype='float64');values=values[np.isfinite(values)]
 if values.size<2:raise GeoError('OTSU_INSUFFICIENT_VALUES','有效像元不足，不能自动确定阈值',422)
 low,high=float(values.min()),float(values.max())
 if high-low<=1e-12:raise GeoError('OTSU_NO_VARIANCE','有效指数没有足够差异，无法自动分区；不会人为指定阈值',422)
 counts,edges=np.histogram(values,bins=bins,range=(low,high));centres=(edges[:-1]+edges[1:])/2
 weights=counts.astype('float64');left=np.cumsum(weights);right=weights.sum()-left
 totals=np.cumsum(weights*centres);overall=totals[-1];valid=(left>0)&(right>0)
 scores=np.full(bins,-np.inf);scores[valid]=(overall*left[valid]-totals[valid]*weights.sum())**2/(left[valid]*right[valid]*weights.sum()**2)
 index=int(np.argmax(scores));threshold=float(edges[index+1]);total_variance=float(np.sum(weights*(centres-overall/weights.sum())**2)/weights.sum())
 return threshold,{'bins':bins,'counts':counts.tolist(),'edges':edges.tolist(),'between_class_variance':float(scores[index]),'separability':float(scores[index]/total_variance) if total_variance>0 else None,'tie_policy':'first maximum, lowest histogram split boundary','selection_boundary':'upper edge of lower histogram group; above is >=; below is <'}


def _polygon_area(geometry):
 import numpy as np
 total=0.
 for i,ring in enumerate(geometry['coordinates']):
  points=np.asarray(ring,dtype='float64');points=points-points[0]
  area=abs(float(np.sum(points[:-1,0]*points[1:,1]-points[1:,0]*points[:-1,1])))/2
  total+=area if i==0 else -area
 return total


def run_segmentation(plan,source_path,directory,check=lambda:None):
 import numpy as np
 import rasterio
 from rasterio.features import shapes,rasterize
 from rasterio.warp import transform_geom,transform_bounds,reproject,Resampling
 from PIL import Image
 directory=Path(directory);directory.mkdir(parents=True,exist_ok=True);check();source=plan['source'];index=source['index_id']
 with rasterio.open(source_path) as ds:
  if ds.width*ds.height>MAX_CELLS:raise GeoError('PROCESSING_GRID_LIMIT','原生栅格超出本机处理上限，不降采样替代统计',422)
  if not ds.crs or not ds.crs.is_projected or ds.crs.linear_units not in ('metre','meter','metres','meters') or ds.transform.b or ds.transform.d or ds.transform.a<=0 or ds.transform.e>=0:raise GeoError('PROCESSING_GRID_UNSUPPORTED','指数分区只接受已归档米制北向上原生格网',422)
  names=list(ds.descriptions);required=['after_'+index,'aoi_mask','common_valid_mask']
  if any(name not in names for name in required):raise GeoError('PROCESSING_MASK_REQUIRED','源缺少指数、选区或质量掩膜，不能从预览图推算',422)
  data=ds.read(names.index('after_'+index)+1);inside=ds.read(names.index('aoi_mask')+1)==1;valid=inside&(ds.read(names.index('common_valid_mask')+1)==1)&np.isfinite(data)
  transform,crs,height,width=ds.transform,ds.crs,ds.height,ds.width;bounds=ds.bounds;cell_area=abs(transform.a*transform.e)
 threshold,histogram=otsu(data[valid]);check();raw=valid&((data>=threshold) if plan['direction']=='above' else (data<threshold))
 features=[];kept=[];raw_patch_count=0;raw_patch_pixels=0;removed_pixels=0;removed_patches=0
 for geometry,value in shapes(raw.astype('uint8'),mask=raw,transform=transform,connectivity=4):
  check();raw_patch_count+=1;pixel_count=int(round(_polygon_area(geometry)/cell_area));raw_patch_pixels+=pixel_count
  if pixel_count<plan['min_patch_pixels']:removed_pixels+=pixel_count;removed_patches+=1;continue
  if len(features)>=MAX_PATCHES:raise GeoError('PROCESSING_PATCH_LIMIT','连通斑块超过2500个，请提高最小斑块像元数后再运行；不会截断结果',422)
  kept.append((geometry,1));identity=len(features)+1;props={'patch_id':identity,'name':'指数分区 '+str(identity),'pixel_count':pixel_count,'area_ha':pixel_count*cell_area/10000,'threshold':threshold,'operator':'gte' if plan['direction']=='above' else 'lt','index_id':index,'source_task_id':source['task_id'],'min_patch_pixels':plan['min_patch_pixels']}
  features.append({'type':'Feature','id':'spectral-partition-'+str(identity),'geometry':transform_geom(crs,'EPSG:4326',geometry,precision=9),'properties':props})
 if raw_patch_pixels!=int(raw.sum()):raise GeoError('PROCESSING_PIXEL_CHECK_FAILED','连通图斑像元计数不一致，未发布面积结果',500)
 selected=rasterize(kept,out_shape=(height,width),transform=transform,fill=0,dtype='uint8').astype('bool') if kept else np.zeros((height,width),dtype='bool')
 if int(selected.sum())!=int(raw.sum())-removed_pixels:raise GeoError('PROCESSING_PIXEL_CHECK_FAILED','图斑回烧与原生筛选像元不一致',500)
 count=int(inside.sum());valid_count=int(valid.sum());selected_count=int(selected.sum());label=source.get('index_label',index.upper());actual_dates=copy.deepcopy(source.get('actual_dates',{}))
 summary={'algorithm_id':'otsu_segmentation','index_id':index,'index_label':label,'mean_value':float(data[valid].mean(dtype='float64')),'index_unit':'index','native_pixel_count':count,'valid_pixel_count':valid_count,'valid_count':valid_count,'invalid_pixel_count':count-valid_count,'valid_ratio':valid_count/count if count else 0.,'threshold':threshold,'direction':plan['direction'],'threshold_operator':'gte' if plan['direction']=='above' else 'lt','patch_count':len(features),'raw_patch_count':raw_patch_count,'selected_pixel_count':selected_count,'selected_area_ha':selected_count*cell_area/10000,'raw_selected_pixel_count':int(raw.sum()),'removed_patch_count':removed_patches,'removed_pixel_count':removed_pixels,'min_patch_pixels':plan['min_patch_pixels'],'valid_area_ha':valid_count*cell_area/10000,'unknown_area_ha':(count-valid_count)*cell_area/10000,'area_method':'native_projected_grid','area_label':'自动阈值命中的原生格网条件面积，非水体/作物/地块面积','actual_dates':actual_dates,'histogram':histogram}
 tif_names=['selected_mask','raw_selected_mask','common_valid_mask','aoi_mask','source_'+index];arrays=[np.where(valid,selected.astype('float32'),np.nan),np.where(valid,raw.astype('float32'),np.nan),valid.astype('float32'),inside.astype('float32'),np.where(valid,data,np.nan)]
 with rasterio.open(directory/'analysis.tif','w',driver='GTiff',width=width,height=height,count=len(arrays),dtype='float32',crs=crs,transform=transform,nodata=np.nan,compress='deflate') as dst:
  for number,(name,array) in enumerate(zip(tif_names,arrays),1):check();dst.write(array,number);dst.set_band_description(number,name)
 # Display reprojects only the completed binary raster; no preview pixels enter statistics.
 merc=transform_bounds(crs,'EPSG:3857',*bounds,densify_pts=21);spanx,spany=merc[2]-merc[0],merc[3]-merc[1];scale=1024/max(width,height);pw=max(1,int(round(width*min(scale,1))));ph=max(1,int(round(pw*spany/spanx)))
 if max(pw,ph)>1024:
  factor=1024/max(pw,ph);pw=max(1,int(pw*factor));ph=max(1,int(ph*factor))
 preview_transform=rasterio.transform.from_bounds(*merc,pw,ph);target=np.full((ph,pw),np.nan,dtype='float32')
 reproject(np.where(selected,1,np.nan).astype('float32'),target,src_transform=transform,src_crs=crs,src_nodata=np.nan,dst_transform=preview_transform,dst_crs='EPSG:3857',dst_nodata=np.nan,resampling=Resampling.nearest)
 rgba=np.zeros((ph,pw,4),dtype='uint8');rgba[np.isfinite(target)]=[54,151,192,210];Image.fromarray(rgba).save(directory/'overlay.png')
 geo={'type':'FeatureCollection','features':features};base='/api/raster-processing/tasks/'+directory.name+'/'
 (directory/'result.geojson').write_text(json.dumps(geo,ensure_ascii=False,allow_nan=False),'utf-8')
 with (directory/'patches.csv').open('w',encoding='utf-8-sig',newline='') as stream:
  fields=['patch_id','name','pixel_count','area_ha','threshold','operator','index_id','source_task_id','min_patch_pixels'];writer=csv.DictWriter(stream,fieldnames=fields);writer.writeheader();writer.writerows(f['properties'] for f in features)
 attribution='USGS Landsat / Microsoft Planetary Computer' if source.get('source_id')=='pc_landsat_c2_l2' else 'ESA / Copernicus Sentinel-2 / Microsoft Planetary Computer'
 provenance={'algorithm_id':'otsu_segmentation','algorithm_version':VERSION,'source_type':'derived_public_satellite_pixels','source_ref':copy.deepcopy(source),'bbox':source['bbox'],'actual_dates':actual_dates,'native_crs':str(crs),'grid':{'rows':height,'cols':width,'transform':list(transform)[:6],'reference_resolution_m':abs(transform.a),'statistics_resampled':False},'area_method':'native_projected_grid','input_fingerprint':plan['input_fingerprint'],'attribution':attribution,'method':'128-bin Otsu between-class variance; four-connected native masks; optional positive-component size filter','reference_urls':['https://developers.google.com/earth-engine/apidocs/ee-reducer-histogram','https://doi.org/10.1109/TSMC.1979.4310076'],'not_external_engine_call':True}
 result={'summary':summary,'geojson':geo,'image':{'kind':'otsu_segmentation','url':base+'overlay.png','bounds':list(transform_bounds('EPSG:3857','EPSG:4326',*merc,densify_pts=21)),'width':pw,'height':ph,'crs':'EPSG:3857','legend':[{'label':'指数阈值命中分区','color':'#3697c0'},{'label':'未命中或未知：透明','color':'#ffffff'}],'statistics_from_preview':False,'attribution':attribution},'artifacts':[{'name':name,'url':base+name,'type':{'tif':'geotiff','geojson':'geojson','png':'png','csv':'csv','json':'json'}[name.rsplit('.',1)[1]]} for name in FILES],'provenance':provenance,'report':{'limitations':LIMITATIONS,'findings':['阈值%.8f；128分箱；分离度%s。'%(threshold,histogram['separability']),'原始命中%d像元，按最小斑块条件移除%d像元；保留%d个连通分区。'%(int(raw.sum()),removed_pixels,len(features))],'method':provenance['method']}}
 (directory/'result.json').write_text(json.dumps(result,ensure_ascii=False,allow_nan=False,indent=2),'utf-8');check();return result
