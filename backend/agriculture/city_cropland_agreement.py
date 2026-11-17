"""Cross-year/model classification screening, explicitly not cropland change.

WorldCover2021 nearest-neighbour alignment to IO2024's native 10m reference;
all comparison areas belong to that common projected grid, not original areas.
"""
import csv
import json
from backend.paths import PROJECT_ROOT
from pathlib import Path
from datetime import datetime,timezone
from backend.core.geo_services import GeoError
from backend.agriculture.city_classification import file_sha,canonical
from backend.analysis.io_lulc_reader import CLASSES as IO_CLASSES
from backend.analysis.landcover_services import CLASSES as WC_CLASSES

ROOT=PROJECT_ROOT
VERSION='city-cross-product-review-1.1'
CLASSES=[(1,'两产品均为作物/耕作地类别','#49c99a'),(2,'仅IO2024为作物类别','#e3ac58'),(3,'仅WorldCover2021为耕作地类别','#8f93df'),(4,'两产品均为其他已知类别','#78939d')]
LIMITATIONS=[
 '跨年、跨模型和跨类别定义的一致性核查参考，不是耕地增减、土地变化、准确率或官方面积。',
 '交集只表示两种产品均给出对应类别，不等于真实农田面积；差异只作为需核验线索。',
 'IO2024原生UTM10米格网与2017公开参考市界中心掩膜作为比较基准；WorldCover2021以最近邻对齐，比较格网发生重采样。',
 '本结果所有面积为同一10米投影格网计数，每像元100平方米；不替换WorldCover2021原生椭球面积或IO2024原生统计。',
 '任一源为缺测、云或未知类别时保留未知，不并入两者均非或差异范围。',
 '共同格网未知也可能来自两种像元中心边界掩膜与对齐边缘，不等于实际云量或未调查。',
 '2017广义参考市界并非当期权威边界，类别和边界误差都可能产生差异；不认定高标准农田、确权或种植品种。',
 'PNG只作显示，统计来自所有参考格网像元；未做本地独立精度与面积误差校正。',
]
def stamp():return datetime.now(timezone.utc).isoformat(timespec='seconds')
def classify(io,wc,inside):
 import numpy as np
 valid=inside&np.isin(io,[c[0] for c in IO_CLASSES])&np.isin(wc,[c[0] for c in WC_CLASSES]);result=np.zeros(io.shape,dtype='uint8')
 crop_io=io==5;crop_wc=wc==40
 result[valid&crop_io&crop_wc]=1;result[valid&crop_io&~crop_wc]=2;result[valid&~crop_io&crop_wc]=3;result[valid&~crop_io&~crop_wc]=4
 return result,valid

def account(io_path,wc_path,directory,progress=lambda detail:None):
 import numpy as np
 import rasterio
 from rasterio.windows import Window
 from rasterio.vrt import WarpedVRT
 from rasterio.enums import Resampling
 io_path,wc_path,directory=Path(io_path),Path(wc_path),Path(directory);directory.mkdir(parents=True,exist_ok=True)
 fingerprints={'io2024':file_sha(io_path),'worldcover2021':file_sha(wc_path)};counts={c:0 for c,_,_ in CLASSES};inside_count=unknown_count=io_unknown=wc_unknown=0;row_records=[];strips=0
 with rasterio.open(io_path) as io,rasterio.open(wc_path) as wc:
  if io.count!=2 or io.descriptions!=('classification','aoi_mask') or io.crs.to_epsg()!=32649 or abs(io.transform.a-10)>1e-8 or abs(io.transform.e+10)>1e-8 or io.transform.b or io.transform.d:raise GeoError('REVIEW_GRID_INVALID','核查需要已生产的IO2024原生UTM49N10米格网与市界掩膜',422)
  if wc.count!=1 or wc.crs.to_epsg()!=4326 or wc.nodata!=0 or wc.dtypes[0]!='uint8':raise GeoError('REVIEW_SOURCE_INVALID','WC2021输入必须为原生EPSG4326分类格网',422)
  reference={'width':io.width,'height':io.height,'transform':io.transform,'crs':io.crs};profile={**io.profile,'count':5,'compress':'deflate','tiled':True,'blockxsize':512,'blockysize':512};partial=directory/'analysis.partial.tif'
  with WarpedVRT(wc,crs=io.crs,transform=io.transform,width=io.width,height=io.height,resampling=Resampling.nearest,src_nodata=0,nodata=0,warp_mem_limit=64,tolerance=1e-9) as aligned,rasterio.open(partial,'w',**profile) as dst:
   for offset in range(0,io.height,512):
    rows=min(512,io.height-offset);window=Window(0,offset,io.width,rows);raw_io=io.read(1,window=window);inside=io.read(2,window=window)==1;raw_wc=aligned.read(1,window=window)
    data,valid=classify(raw_io,raw_wc,inside);unknown=inside&~valid;per_inside=inside.sum(axis=1,dtype='int64');per_unknown=unknown.sum(axis=1,dtype='int64');inside_count+=int(per_inside.sum());unknown_count+=int(per_unknown.sum());io_unknown+=int((inside&~np.isin(raw_io,[c[0] for c in IO_CLASSES])).sum());wc_unknown+=int((inside&~np.isin(raw_wc,[c[0] for c in WC_CLASSES])).sum())
    per_classes=[]
    for code,_,_ in CLASSES:
     per=(data==code).sum(axis=1,dtype='int64');counts[code]+=int(per.sum());per_classes.append(per)
    dst.write(data,1,window=window);dst.write(inside.astype('uint8'),2,window=window);dst.write(np.where(inside,raw_io,0).astype('uint8'),3,window=window);dst.write(np.where(inside,raw_wc,0).astype('uint8'),4,window=window);dst.write(valid.astype('uint8'),5,window=window)
    for j in range(rows):row_records.append([offset+j,int(per_inside[j]),int(per_unknown[j])]+[int(v[j]) for v in per_classes])
    strips+=1;progress({'stage':'compare','strip':strips,'processed_bbox_pixels':(offset+rows)*io.width,'inside_pixels':inside_count,'jointly_valid_pixels':inside_count-unknown_count})
   for band,name in enumerate(('cross_product_review_class','aoi_mask','io2024_original_class','wc2021_aligned_class','jointly_valid_mask'),1):dst.set_band_description(band,name)
   dst.update_tags(algorithm_version=VERSION,purpose='cross-year cross-model review only; not change or accuracy',reference_grid='IO2024 native UTM49N10m',worldcover_alignment='nearest neighbour; transformer tolerance=1e-9 pixel',io2024_sha256=fingerprints['io2024'],worldcover2021_sha256=fingerprints['worldcover2021'],area_method='common projected grid, 100 m2 per pixel')
  partial.replace(directory/'analysis.tif');bounds=list(io.bounds);shape=[io.height,io.width];transform=list(io.transform)[:6]
 if sum(counts.values())+unknown_count!=inside_count:raise GeoError('REVIEW_BALANCE_ERROR','核查类别与未知计数不守恒',502)
 if fingerprints['io2024']!=file_sha(io_path) or fingerprints['worldcover2021']!=file_sha(wc_path):raise GeoError('REVIEW_SOURCE_CHANGED','原始分类文件在核查中发生变化',409)
 classes=[{'code':c,'name':name,'color':color,'pixel_count':counts[c],'area_ha':counts[c]*.01,'area_mu':counts[c]*.15,'share_of_city_grid':counts[c]/inside_count if inside_count else None} for c,name,color in CLASSES]
 summary={'inside_pixel_count':inside_count,'jointly_valid_pixel_count':inside_count-unknown_count,'unknown_pixel_count':unknown_count,'io_unknown_pixel_count':io_unknown,'worldcover_unknown_pixel_count':wc_unknown,'jointly_valid_fraction':(inside_count-unknown_count)/inside_count if inside_count else None,'both_crop_pixel_count':counts[1],'only_io_pixel_count':counts[2],'only_worldcover_pixel_count':counts[3],'neither_crop_pixel_count':counts[4],'both_crop_ha':counts[1]*.01,'only_io_ha':counts[2]*.01,'only_worldcover_ha':counts[3]*.01,'neither_crop_ha':counts[4]*.01,'unknown_area_ha':unknown_count*.01,'total_grid_area_ha':inside_count*.01,'reference_crs':'EPSG:32649','grid_resolution_m':10,'pixel_area_m2':100,'raster_dimensions':shape,'strips_read':strips,'classification_years':[2021,2024],'boundary_year':2017,'is_change_detection':False,'is_accuracy_assessment':False,'is_official':False,'area_method':'common_io2024_projected_grid','worldcover_resampled_for_comparison':True,'reference_grid_resampled':False,'transformer_tolerance_pixels':1e-9}
 with (directory/'classes.csv').open('w',encoding='utf-8-sig',newline='') as f:
  writer=csv.DictWriter(f,fieldnames=list(classes[0]));writer.writeheader();writer.writerows(classes);writer.writerow({'code':0,'name':'未知（任一源无有效分类）','color':'#9ba5ae','pixel_count':unknown_count,'area_ha':unknown_count*.01,'area_mu':unknown_count*.15,'share_of_city_grid':unknown_count/inside_count if inside_count else None})
 with (directory/'row_counts.csv').open('w',encoding='utf-8-sig',newline='') as f:writer=csv.writer(f);writer.writerow(['row','inside_pixels','unknown_pixels']+['class_'+str(c) for c,_,_ in CLASSES]);writer.writerows(row_records)
 return {'summary':summary,'classes':classes,'source_sha256':fingerprints,'native_bounds':bounds,'native_transform':transform,'native_sha256':file_sha(directory/'analysis.tif')}

def make_preview(directory):
 import numpy as np
 import rasterio
 from rasterio.warp import reproject,transform_bounds,Resampling
 from rasterio.transform import from_bounds
 from PIL import Image
 directory=Path(directory)
 with rasterio.open(directory/'analysis.tif') as src:
  bb=transform_bounds(src.crs,'EPSG:3857',*src.bounds,densify_pts=21);width=min(1800,src.width);height=max(1,round(width*(bb[3]-bb[1])/(bb[2]-bb[0])))
  if height>1800:width=max(1,round(width*1800/height));height=1800
  dest=np.zeros((height,width),dtype='uint8');inside=np.zeros((height,width),dtype='uint8');dt=from_bounds(*bb,width,height)
  for band,arr in [(1,dest),(2,inside)]:reproject(source=rasterio.band(src,band),destination=arr,src_transform=src.transform,src_crs=src.crs,dst_transform=dt,dst_crs='EPSG:3857',src_nodata=0,dst_nodata=0,resampling=Resampling.nearest)
  image=np.zeros((height,width,4),dtype='uint8')
  for code,name,color in CLASSES:image[dest==code]=[int(color[i:i+2],16) for i in (1,3,5)]+[205 if code!=4 else 65]
  image[(inside==1)&(dest==0)]=[155,165,174,120];Image.fromarray(image).save(directory/'overlay.png')
  candidate=image.copy();candidate[~np.isin(dest,[2,3])]=0;Image.fromarray(candidate).save(directory/'review-candidates.png')
  return {'bounds':list(transform_bounds('EPSG:3857','EPSG:4326',*bb,densify_pts=21)),'width':width,'height':height,'display_crs':'EPSG:3857','display_resampling':'nearest; display only','statistics_resampled':True,'reference_grid_resampled':False,'worldcover_resampled_for_comparison':True,'legend':[{'code':c,'label':name,'color':color} for c,name,color in CLASSES]+[{'code':0,'label':'未知：任一源无有效分类','color':'#9ba5ae'}]}

def atlas_fields(result,boundary,image):
 s=result['summary'];w,south,e,n=image['bounds'];legend=image['legend']
 result.update({'schema_version':'agriculture-atlas-1.0','title':'仙桃跨产品耕作地核查参考','scene_ids':['farmland','high_standard','grain'],'product_type':'cross_product_review','scope_label':'仙桃全市 · 2017公开参考市界','period_label':'WorldCover2021 × IO2024（跨源参考）','resolution_m':10,
  'summary_metrics':[{'id':'both_crop','label':'两产品均为作物/耕作地类别','value':s['both_crop_ha'],'unit':'公顷','precision':2,'note':'共同格网交集，不是真实农田面积认定'},{'id':'only_io','label':'仅IO2024为作物类别','value':s['only_io_ha'],'unit':'公顷','precision':2,'note':'跨源差异参考，不是新增耕地'},{'id':'only_wc','label':'仅WorldCover2021为耕作地类别','value':s['only_worldcover_ha'],'unit':'公顷','precision':2,'note':'跨源差异参考，不是减少耕地'},{'id':'unknown','label':'任一源未知或对齐边缘','value':s['unknown_area_ha'],'unit':'公顷','precision':2}],
  'layers':[{'id':'xiantao-cropland-review-raster','title':'两年度产品类别核查参考','kind':'raster','image':{'url':'overlay.png','bounds':[[south,w],[n,e]]},'legend':legend},{'id':'xiantao-cropland-review-boundary','title':'2017公开参考市界','kind':'geojson','geojson':boundary,'boundary':True}]})
 return result

def audit_files(directory,io_path,wc_path,summary,source_hashes):
 """Independently recount all five bands and check original WC pixel centres."""
 import numpy as np
 import rasterio
 from rasterio.windows import Window
 from rasterio.warp import transform
 directory=Path(directory);totals={c:0 for c,_,_ in CLASSES};inside_count=unknown_count=blocks=sampled=0
 with rasterio.open(directory/'analysis.tif') as result,rasterio.open(io_path) as io:
  for _,win in result.block_windows(1):
   data=result.read(window=win);inside=data[1]==1;known=inside&np.isin(data[2],[c[0] for c in IO_CLASSES])&np.isin(data[3],[c[0] for c in WC_CLASSES]);expected=np.where(known,np.where(data[2]==5,1,3)+np.where(data[3]==40,0,1),0).astype('uint8')
   if not np.array_equal(expected,data[0]) or not np.array_equal(known,data[4]==1) or not np.array_equal(np.where(inside,io.read(1,window=win),0),data[2]) or not np.array_equal(io.read(2,window=win),data[1]):raise GeoError('REVIEW_RECOMPUTE_MISMATCH','输出格网逐像元独立复算不一致',502)
   inside_count+=int(inside.sum());unknown_count+=int((inside&~known).sum());blocks+=1
   for code in totals:totals[code]+=int((data[0]==code).sum())
  with rasterio.open(wc_path) as wc:
   seen=set()
   for ry,rx in [(.10,.06),(.42,.46),(.73,.74),(.80,.49)]:
    height=min(64,result.height);width=min(64,result.width);row=min(result.height-height,int(result.height*ry));col=min(result.width-width,int(result.width*rx))
    if (row,col) in seen:continue
    seen.add((row,col));data=result.read(window=Window(col,row,width,height));mask=data[1]==1;rr,cc=np.indices(mask.shape);t=result.transform;xs=t.c+(cc+col+.5)*t.a;ys=t.f+(rr+row+.5)*t.e;lon,lat=transform(result.crs,wc.crs,xs.ravel().tolist(),ys.ravel().tolist());columns=np.floor((np.array(lon)-wc.transform.c)/wc.transform.a).astype('int64');rows=np.floor((np.array(lat)-wc.transform.f)/wc.transform.e).astype('int64');c0,c1,r0,r1=int(columns.min()),int(columns.max())+1,int(rows.min()),int(rows.max())+1
    source=wc.read(1,window=Window(c0,r0,c1-c0,r1-r0),boundless=True,fill_value=0);values=source[rows-r0,columns-c0].reshape(mask.shape)
    if not np.array_equal(values[mask],data[3][mask]):raise GeoError('REVIEW_ALIGNMENT_MISMATCH','原WC像元中心独立寻址与对齐结果不一致',502)
    sampled+=int(mask.sum())
 if inside_count!=summary['inside_pixel_count'] or unknown_count!=summary['unknown_pixel_count'] or any(totals[code]!=summary[{1:'both_crop_pixel_count',2:'only_io_pixel_count',3:'only_worldcover_pixel_count',4:'neither_crop_pixel_count'}[code]] for code in totals):raise GeoError('REVIEW_COUNT_MISMATCH','独立计数与汇总不一致',502)
 with (directory/'row_counts.csv').open(encoding='utf-8-sig') as f:rows=list(csv.DictReader(f))
 if sum(int(r['inside_pixels']) for r in rows)!=inside_count or sum(int(r['unknown_pixels']) for r in rows)!=unknown_count or any(sum(int(r['class_'+str(code)]) for r in rows)!=n for code,n in totals.items()):raise GeoError('REVIEW_CSV_MISMATCH','逐行CSV与原生输出复算不一致',502)
 if file_sha(io_path)!=source_hashes['io2024'] or file_sha(wc_path)!=source_hashes['worldcover2021']:raise GeoError('REVIEW_SOURCE_CHANGED','源栅格哈希在独立复核时改变',409)
 return {'independent_output_blocks':blocks,'per_pixel_five_band_recomputation':True,'source_io_values_and_mask_unchanged':True,'independent_wc_source_center_samples':sampled,'wc_nearest_alignment_values_match':True,'row_csv_counts_match':True,'source_files_unchanged':True,'validated_at':stamp()}


def produce(directory=None,progress=lambda detail:None):
 directory=Path(directory or ROOT/'data/agriculture-atlas/xiantao-cropland-review');directory.mkdir(parents=True,exist_ok=True);started=stamp()
 (directory/'status.json').write_text(json.dumps({'status':'running','started_at':started,'version':VERSION},indent=2)+'\n')
 try:
  io_dir=ROOT/'data/agriculture-atlas/xiantao-2024-classification';wc_dir=ROOT/'data/landcover/landcover_1c6f5eb0b1e247';io_result=json.loads((io_dir/'result.json').read_text());wc_result=json.loads((wc_dir/'result.json').read_text())
  if io_result['summary']['reference_year']!=2024 or wc_result['summary']['reference_year']!=2021:raise GeoError('REVIEW_YEAR_MISMATCH','分类源年份不符，停止而非替换',422)
  result=account(io_dir/'classification.tif',wc_dir/'classification.tif',directory,progress);image=make_preview(directory)
  boundary=io_result['boundary'];(directory/'boundary.geojson').write_bytes(canonical(boundary))
  source_records={'io2024':{'product':io_result['provenance']['source_product'],'input_raster_sha256':result['source_sha256']['io2024'],'original_area_method':io_result['summary']['area_method'],'original_crops_ha':io_result['summary']['cropland_ha'],'original_crops_mu':io_result['summary']['cropland_mu'],'original_inside_pixels':io_result['summary']['inside_pixel_count'],'source_url':io_result['provenance']['source_url']},'worldcover2021':{'product':wc_result['provenance']['product'],'input_raster_sha256':result['source_sha256']['worldcover2021'],'original_area_method':wc_result['summary']['method'],'original_crops_ha':wc_result['summary']['cropland_ha'],'original_crops_mu':wc_result['summary']['cropland_mu'],'original_inside_pixels':wc_result['summary']['inside_pixel_count'],'source_tiles':wc_result['provenance']['source_tiles']}}
  result.update({'id':'xiantao-cropland-review','name':'仙桃跨产品耕作地核查参考','status':'succeeded','version':VERSION,'created_at':stamp(),'started_at':started,'boundary':boundary,'image':image,'provenance':{'source_type':'public','algorithm_version':VERSION,'operation':'cross_year_cross_model_classification_review','source_products':source_records,'sources':[{'title':'IO Annual LULC 2024','url':'https://registry.opendata.aws/io-lulc/','license':'CC BY 4.0'},{'title':'ESA WorldCover 2021 v200','url':'https://esa-worldcover.org/en/data-access','license':'CC BY 4.0'},{'title':'geoBoundaries 2017公开参考市界','url':io_result['provenance']['boundary_source']['metadata_url'],'license':io_result['provenance']['boundary_source']['license']}],'boundary_year':2017,'method':'IO2024原生UTM49N10米参照；WorldCover2021最近邻对齐，按共同有效格网分类核查','boundary_source':io_result['provenance']['boundary_source'],'alignment':'WorldCover2021 nearest neighbour to IO2024 native UTM49N10m','area_method':'common projected grid, not replacement of source original areas','limitations':LIMITATIONS},'limitations':LIMITATIONS})
  result['validation']=audit_files(directory,io_dir/'classification.tif',wc_dir/'classification.tif',result['summary'],result['source_sha256'])
  atlas_fields(result,boundary,image)
  (directory/'sources.json').write_bytes(canonical(source_records));result['artifacts']=[{'file':p.name,'bytes':p.stat().st_size,'sha256':file_sha(p)} for p in sorted(directory.iterdir()) if p.is_file() and p.name not in ('result.json','status.json') and '.partial.' not in p.name]
  (directory/'result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2,allow_nan=False)+'\n');(directory/'status.json').write_text(json.dumps({'status':'succeeded','started_at':started,'finished_at':stamp(),'result_sha256':file_sha(directory/'result.json')},indent=2)+'\n');return result
 except Exception as error:
  (directory/'status.json').write_text(json.dumps({'status':'failed','started_at':started,'finished_at':stamp(),'error':str(error),'no_substitute_data':True},ensure_ascii=False,indent=2)+'\n');raise
