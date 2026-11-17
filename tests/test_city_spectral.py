"""Scientific contracts and independent delivered-raster checks; no network."""
import copy,csv,json,tempfile,unittest,subprocess,sys,shutil
from datetime import date
from unittest.mock import patch
from pathlib import Path
import numpy as np
import rasterio
from rasterio.transform import from_origin
from rasterio.warp import transform_geom
from backend.agriculture.city_spectral import quality_mask,select_after,index_array,scene_contract,grid_for_dataset,stats,sha_file,validate_product,production_request,prepare_catalogs,covers_city_geometry,produce,CityReader,validate_native_asset,CATALOG_EVIDENCE_FILES,CROP_REFERENCE_FILE,archive_crop_reference
from backend.core.geo_services import GeoError

class CitySpectralTest(unittest.TestCase):
 def test_quality_rejects_each_cloud_fill_snow_and_saturation_but_keeps_water(self):
  qa=np.array([0,1,2,4,8,16,32,64,128,0,np.nan,-1,65536,.5]);rad=np.zeros(14);rad[9]=1
  np.testing.assert_array_equal(quality_mask(qa,rad),[True,False,False,False,False,False,False,True,True,False,False,False,False,False])

 def test_latest_selection_uses_city_quality_instead_of_scene_cloud_score(self):
  candidates=[{'scene_id':'older','datetime':'2026-10-07','qa_valid_ratio':.99,'scene_cloud_cover':30},{'scene_id':'latest','datetime':'2026-10-31','qa_valid_ratio':.39,'scene_cloud_cover':.01}]
  picked,reason,below=select_after(candidates);self.assertEqual(picked['scene_id'],'older');self.assertFalse(below)
  candidates[1]['qa_valid_ratio']=.71;self.assertEqual(select_after(candidates)[0]['scene_id'],'latest')
  candidates[0]['qa_valid_ratio']=.2;candidates[1]['qa_valid_ratio']=.3;picked,reason,below=select_after(candidates);self.assertTrue(below);self.assertEqual(picked['scene_id'],'latest');self.assertIn('below',reason)
  with self.assertRaises(GeoError):select_after([])

 def test_negative_reflectance_zero_denominator_and_outside_stay_unknown(self):
  bands={'nir':np.array([[.6,-.1,0,.6,.6]],dtype='float32'),'red':np.array([[.2,.1,0,.2,.2]],dtype='float32')};inside=np.array([[1,1,1,0,1]],dtype=bool);qa=np.array([[0,0,0,0,8]],dtype='uint16');rad=np.zeros_like(qa)
  value,valid=index_array('ndvi',bands,qa,rad,inside);np.testing.assert_array_equal(valid,[[1,0,0,0,0]]);self.assertAlmostEqual(value[0,0],.5,delta=1e-7);self.assertTrue(np.isnan(value[~valid]).all())

 def test_calibration_metadata_and_year_are_required(self):
  feature={'id':'LC08_L2SP_123039_20261007_02_T1','properties':{'datetime':'2026-10-07T02:56:20Z'},'assets':{}}
  for key in ('green','red','nir08','swir16','qa_pixel','qa_radsat'):
   feature['assets'][key]={'href':'https://landsateuwest.blob.core.windows.net/landsat-c2/level-2/standard/oli-tirs/unit_fixture_'+key+'.TIF','raster:bands':[{'scale':.0000275,'offset':-.2,'nodata':0,'spatial_resolution':30}]}
  contract=scene_contract(feature);self.assertEqual(contract['assets']['red']['scale'],.0000275);self.assertEqual(contract['assets']['red']['offset'],-.2)
  changed=copy.deepcopy(feature);changed['properties']['datetime']='2027-01-01T00:00:00Z'
  with self.assertRaises(GeoError):scene_contract(changed)
  changed=copy.deepcopy(feature);changed['assets']['red']['raster:bands'][0].pop('offset')
  with self.assertRaises(GeoError):scene_contract(changed)

 def test_native_polygon_window_has_holes_and_hard_limit_without_coarsening(self):
  with tempfile.TemporaryDirectory() as tmp:
   path=Path(tmp)/'native.tif';t=from_origin(684585,3379845,30,30)
   with rasterio.open(path,'w',driver='GTiff',width=6,height=4,count=1,dtype='uint16',crs='EPSG:32649',transform=t) as src:src.write(np.zeros((4,6),dtype='uint16'),1)
   native={'type':'Polygon','coordinates':[[[684585.1,3379725.1],[684764.9,3379725.1],[684764.9,3379844.9],[684585.1,3379844.9],[684585.1,3379725.1]],[[684615,3379785],[684645,3379785],[684645,3379815],[684615,3379815],[684615,3379785]]]};boundary={'type':'FeatureCollection','features':[{'type':'Feature','geometry':transform_geom('EPSG:32649','EPSG:4326',native),'properties':{}}]}
   with rasterio.open(path) as src:
    grid=grid_for_dataset(src,boundary);self.assertEqual(grid['shape'],(4,6));self.assertEqual(int(grid['inside'].sum()),23);self.assertEqual(grid['cell_area_m2'],900.)
    with self.assertRaises(GeoError) as error:grid_for_dataset(src,boundary,max_cells=5)
    self.assertEqual(error.exception.code,'CITY_GRID_LIMIT')

 def native_source(self,root,resolution=30,dtype='uint16',count=1,nodata=0,origin=599985):
  path=root/('native-'+str(resolution)+'-'+dtype+'-'+str(count)+'-'+str(nodata)+'.tif')
  with rasterio.open(path,'w',driver='GTiff',height=6,width=6,count=count,dtype=dtype,crs='EPSG:32649',transform=from_origin(origin,3400015,resolution,resolution),nodata=nodata) as dst:
   for band in range(1,count+1):dst.write(np.full((6,6),20000,dtype=dtype),band)
  return path

 def test_opened_cog_actual_resolution_must_match_30m_even_with_supplied_grid(self):
  with tempfile.TemporaryDirectory() as tmp:
   root=Path(tmp);source=self.native_source(root,resolution=20)
   class Signer:
    def signed_url(self,url,check):return str(source)
   scene={'scene_id':'unit-only','assets':{'red':{'href':'https://landsateuwest.blob.core.windows.net/landsat-c2/level-2/standard/oli-tirs/unit.TIF','asset_key':'red','nodata':0,'scale':.0000275,'offset':-.2}}};grid={'shape':(3,3),'transform':from_origin(600000,3400000,30,30),'crs':rasterio.crs.CRS.from_epsg(32649)}
   with self.assertRaises(GeoError) as error:CityReader(Signer()).read(scene,'red',grid=grid)
   self.assertEqual(error.exception.code,'CITY_ASSET_GRID_MISMATCH')

 def test_opened_cog_native_encoding_and_optical_nodata_are_checked(self):
  with tempfile.TemporaryDirectory() as tmp:
   root=Path(tmp)
   for properties in ({'dtype':'float32'},{'count':2},{'nodata':99}):
    with self.subTest(properties=properties):
     with rasterio.open(self.native_source(root,**properties)) as ds:
      with self.assertRaises(GeoError) as error:validate_native_asset(ds,'red',{'nodata':0})
      self.assertEqual(error.exception.code,'CITY_ASSET_ENCODING_MISMATCH')

 def test_30m_origin_alignment_keeps_zero_radsat_valid(self):
  with tempfile.TemporaryDirectory() as tmp:
   source=self.native_source(Path(tmp))
   with rasterio.open(source,'r+') as dst:dst.write(np.zeros((6,6),dtype='uint16'),1)
   class Signer:
    def signed_url(self,url,check):return str(source)
   scene={'scene_id':'unit-only','assets':{'qa_radsat':{'href':'https://landsateuwest.blob.core.windows.net/landsat-c2/level-2/standard/oli-tirs/unit.TIF','asset_key':'qa_radsat'}}};grid={'shape':(3,3),'transform':from_origin(600000,3400000,30,30),'crs':rasterio.crs.CRS.from_epsg(32649)}
   values,_,evidence=CityReader(Signer()).read(scene,'qa_radsat',grid=grid);self.assertTrue((values==0).all());self.assertTrue(quality_mask(np.zeros_like(values),values).all());self.assertTrue(evidence['native_grid_validated']);self.assertEqual(evidence['native_pixel_size_m'],[30.,30.])

 def fixture(self,root):
  folder=root/'xiantao-2026-spectral-ndvi';folder.mkdir();t=from_origin(684585,3379845,30,30);shape=(4,6);city=np.ones(shape,dtype=bool);red=np.full(shape,.2,dtype='float32');nir=np.full(shape,.6,dtype='float32');qa=np.zeros(shape,dtype='float32');qa[0,0]=8;rad=np.zeros_like(qa);nir[1,0]=-.1;red[2,0]=0;nir[2,0]=0
  valid=city&(qa==0)&(nir>=0)&((nir+red)>0);value=np.full(shape,np.nan,dtype='float32');np.divide(nir-red,nir+red,out=value,where=valid);crop=np.ones(shape,dtype='uint8');crop[:,:3]=5
  ref=root/'xiantao-2024-classification';ref.mkdir();reference=np.repeat(np.repeat(crop,3,axis=0),3,axis=1)
  with rasterio.open(ref/'classification.tif','w',driver='GTiff',width=18,height=12,count=1,dtype='uint8',crs='EPSG:32649',transform=from_origin(684585,3379845,10,10),nodata=0) as dst:dst.write(reference,1)
  fields=[('value',value),('valid_mask',valid),('city_mask',city),('crop2024_reference',crop==5),('after_red_reflectance',red),('after_nir_reflectance',nir),('after_qa_pixel',qa),('after_qa_radsat',rad)]
  with rasterio.open(folder/'analysis.tif','w',driver='GTiff',width=6,height=4,count=8,dtype='float32',crs='EPSG:32649',transform=t,nodata=np.nan) as dst:
   for i,(name,array) in enumerate(fields,1):dst.write(array.astype('float32'),i);dst.set_band_description(i,name)
  native={'type':'Polygon','coordinates':[[[684585.1,3379725.1],[684764.9,3379725.1],[684764.9,3379844.9],[684585.1,3379844.9],[684585.1,3379725.1]]]};boundary={'type':'FeatureCollection','features':[{'type':'Feature','geometry':transform_geom('EPSG:32649','EPSG:4326',native),'properties':{}}]};(folder/'boundary.geojson').write_text(json.dumps(boundary))
  s=stats(value,valid,city,crop);s['native_crs']='EPSG:32649';result={'id':folder.name,'summary':s,'native_sha256':sha_file(folder/'analysis.tif'),'provenance':{'grid':{'shape':list(shape)},'crop_reference':{'sha256':sha_file(ref/'classification.tif')}}}
  with (folder/'statistics.csv').open('w',newline='') as out:
   writer=csv.writer(out);writer.writerow(['valid_pixels_30m','mean_index']);writer.writerow([s['valid_pixel_count'],s['mean_index']]);writer.writerow([s['crop2024_reference']['valid_pixel_count_30m'],s['crop2024_reference']['mean_index']])
  hist,edges=np.histogram(value[valid],bins=np.linspace(-1,1,21))
  with (folder/'histogram.csv').open('w',newline='') as out:writer=csv.writer(out);writer.writerow(['lower_inclusive','upper_exclusive_except_last','pixels']);writer.writerows(zip(edges[:-1],edges[1:],hist))
  return folder,result

 def test_independent_validation_reopens_native_inputs_and_csv(self):
  with tempfile.TemporaryDirectory() as tmp:
   folder,result=self.fixture(Path(tmp));validation=validate_product(folder,result);self.assertEqual(validation['status'],'passed');self.assertEqual(validation['valid_pixels_recomputed'],21);self.assertEqual(validation['max_formula_absolute_error'],0)

 def test_independent_validation_detects_quality_mask_corruption_after_sha_refresh(self):
  with tempfile.TemporaryDirectory() as tmp:
   folder,result=self.fixture(Path(tmp))
   with rasterio.open(folder/'analysis.tif','r+') as dst:qa=dst.read(7);qa[0,1]=8;dst.write(qa,7)
   result['native_sha256']=sha_file(folder/'analysis.tif')
   with self.assertRaises(GeoError) as error:validate_product(folder,result)
   self.assertEqual(error.exception.code,'CITY_VALIDATION_FAILED')

 def test_independent_validation_detects_changed_reference_data(self):
  with tempfile.TemporaryDirectory() as tmp:
   folder,result=self.fixture(Path(tmp));(folder.parent/'xiantao-2024-classification'/'classification.tif').write_bytes(b'changed')
   with self.assertRaises(GeoError) as error:validate_product(folder,result)
   self.assertEqual(error.exception.code,'CITY_VALIDATION_FAILED')

 def catalog_feature(self,day='2026-10-07',cloud=1,geometry=None):
  ident='LC08_L2SP_123039_'+day.replace('-','')+'_02_T1';feature={'type':'Feature','id':ident,'collection':'landsat-c2-l2','geometry':geometry or {'type':'Polygon','coordinates':[[[112,29],[115,29],[115,32],[112,32],[112,29]]]},'properties':{'datetime':day+'T02:56:20Z','platform':'landsat-8','eo:cloud_cover':cloud},'assets':{}}
  for key in ('green','red','nir08','swir16','qa_pixel','qa_radsat'):feature['assets'][key]={'href':'https://landsateuwest.blob.core.windows.net/landsat-c2/level-2/standard/oli-tirs/unit_fixture_'+day+'_'+key+'.TIF','raster:bands':[{'scale':.0000275,'offset':-.2,'nodata':0,'spatial_resolution':30}]}
  return feature

 def test_request_rejects_future_long_overlapping_and_unbounded_inputs(self):
  for kwargs in ({'periods':{'before':['2026-09-01','2026-09-30'],'after':['2026-11-01','2026-11-15']}},{'max_candidates':5},{'max_catalog_items':10000},{'minimum_qa':0},{'periods':{'before':['2026-09-01','2026-10-02'],'after':['2026-10-01','2026-10-31']}}):
   with self.assertRaises(GeoError):production_request(today=date(2026,11,15),**kwargs)

 def test_discovery_is_bounded_official_filters_actual_footprint_and_freezes(self):
  from backend.analysis.landcover_services import load_boundary
  boundary,metadata=load_boundary();request=production_request(max_candidates=1);before=self.catalog_feature('2026-09-13');newer=self.catalog_feature('2026-10-31');older=self.catalog_feature();too_cloudy=self.catalog_feature('2026-10-15',99);partial=self.catalog_feature('2026-10-23',1,{'type':'Polygon','coordinates':[[[113,30],[113.1,30],[113.1,30.1],[113,30.1],[113,30]]]})
  class Catalog:
   def __init__(self):self.urls=[]
   def fetch_json(self,url,check):self.urls.append(url);return {'features':[before] if '2026-09-01' in url else [older,newer,too_cloudy,partial],'links':[{'rel':'next','href':'https://untrusted.invalid/next'}]}
  with tempfile.TemporaryDirectory() as tmp:
   reader=Catalog();root=Path(tmp);contracts,evidence=prepare_catalogs(boundary,metadata,request,root/'live',reader=reader);self.assertEqual(len(reader.urls),2);self.assertTrue(all(u.startswith('https://planetarycomputer.microsoft.com/api/stac/v1/search?') for u in reader.urls));self.assertEqual(contracts['after'][0]['scene_id'],newer['id']);self.assertEqual(evidence['screening']['after']['eligible_full_city_items'],2);self.assertTrue(evidence['screening']['after']['bounded_candidate_scope']);self.assertTrue(evidence['screening']['after']['catalog_pagination_not_followed'])
   replay,_=prepare_catalogs(boundary,metadata,request,root/'replay',mode='frozen',frozen_dir=root/'live');self.assertEqual(replay,contracts)
   manifest=root/'live'/'catalog-manifest.json';original=manifest.read_bytes();incomplete=json.loads(original);incomplete['catalogs']=[v for v in incomplete['catalogs'] if v['file']!='catalog-after.json'];manifest.write_text(json.dumps(incomplete))
   with self.assertRaises(GeoError) as error:prepare_catalogs(boundary,metadata,request,root/'missing-entry',mode='frozen',frozen_dir=root/'live')
   self.assertEqual(error.exception.code,'CITY_FROZEN_HASH_MISMATCH');manifest.write_bytes(original)
   p=root/'live'/'catalog-after.json';p.write_text(p.read_text()+' ')
   with self.assertRaises(GeoError) as error:prepare_catalogs(boundary,metadata,request,root/'changed',mode='frozen',frozen_dir=root/'live')
   self.assertEqual(error.exception.code,'CITY_FROZEN_HASH_MISMATCH')

 def test_catalog_to_actual_qa_to_four_native_products_in_temporary_directory(self):
  with tempfile.TemporaryDirectory() as tmp:
   root=Path(tmp);fixture,result=self.fixture(root);boundary=json.loads((fixture/'boundary.geojson').read_text());ref=root/'xiantao-2024-classification'/'classification.tif';(ref.parent/'result.json').write_text(json.dumps({'summary':{'cropland_ha':1.08,'reference_year':2024,'grid_resolution_m':10},'provenance':{'source_product':{'id':'io_lulc_2024'}},'native_sha256':sha_file(ref)}));request=production_request();before=self.catalog_feature('2026-09-13');older=self.catalog_feature();newer=self.catalog_feature('2026-10-31');t=from_origin(684585,3379845,30,30)
   class Catalog:
    def fetch_json(self,url,check):return {'features':[before] if '2026-09-01' in url else [newer,older]}
   class Reader:
    def __init__(self):self.calls=[]
    def read(self,scene,band,grid=None,boundary=None):
     self.calls.append((scene['scene_id'],band));grid=grid or {'shape':(4,6),'inside':np.ones((4,6),dtype=bool),'crs':rasterio.crs.CRS.from_epsg(32649),'transform':t,'cell_area_m2':900.};value=8 if band=='qa_pixel' and '20261031' in scene['scene_id'] else 0 if band.startswith('qa_') else .6 if band=='nir' else .2
     return np.full((4,6),value,dtype='uint16' if band.startswith('qa_') else 'float32'),grid,{'href':scene['assets'][band]['href'],'test_local_only':True}
   reader=Reader()
   with patch('backend.agriculture.city_spectral.load_boundary',return_value=(boundary,{'boundary_year_represented':2017})):products=produce(root/'products',root/'catalogs',request=request,crop_reference_path=ref,catalog_reader=Catalog(),city_reader=reader)
   self.assertEqual(len(products),4);self.assertTrue(all(r['validation']['status']=='passed' for r in products));self.assertTrue(all('2026-10-07' in r['summary']['actual_dates'][-1] for r in products));self.assertEqual(len(reader.calls),12);self.assertEqual(products[-1]['summary']['mean_delta'],0)
   for result in products:
    folder=root/'products'/result['id'];declared={v['file']:v for v in result['artifacts'] if isinstance(v,dict)}
    for name in CATALOG_EVIDENCE_FILES:self.assertTrue((folder/name).is_file());self.assertEqual(sha_file(folder/name),declared[name]['sha256'])
    self.assertTrue(result['provenance']['catalog_archive']['original_request_available'])
    self.assertEqual(sha_file(folder/CROP_REFERENCE_FILE),sha_file(ref))
   # A consumer can move one product away from its original sibling data and
   # recompute its archived values and historical stratum without network IO.
   portable=root/'portable'/'xiantao-2026-spectral-ndvi';shutil.copytree(root/'products'/'xiantao-2026-spectral-ndvi',portable)
   self.assertEqual(validate_product(portable)['status'],'passed')
   altered=portable/'catalog-after.json';altered.write_bytes(altered.read_bytes()+b' ')
   with self.assertRaises(GeoError) as error:validate_product(portable)
   self.assertEqual(error.exception.code,'CITY_CATALOG_EVIDENCE_MISMATCH')
   with self.assertRaises(GeoError) as error:produce(root/'products',root/'other-catalogs',request=request,crop_reference_path=ref)
   self.assertEqual(error.exception.code,'CITY_OUTPUT_EXISTS')

 def test_validate_only_cli_does_not_mutate_any_production_file(self):
  with tempfile.TemporaryDirectory() as tmp:
   root=Path(tmp);folder,result=self.fixture(root);(folder/'result.json').write_text(json.dumps(result));before={str(p):p.read_bytes() for p in root.rglob('*') if p.is_file()};run=subprocess.run([sys.executable,'scripts/production/produce_xiantao_spectral.py','--validate-only','--output',str(root),'--crop-reference',str(root/'xiantao-2024-classification'/'classification.tif')],capture_output=True,text=True)
   self.assertEqual(run.returncode,0,run.stderr);self.assertEqual(json.loads(run.stdout)[0]['validation']['status'],'passed');after={str(p):p.read_bytes() for p in root.rglob('*') if p.is_file()};self.assertEqual(before,after)
