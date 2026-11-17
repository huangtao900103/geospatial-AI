"""Offline spectral math, genuine local GeoTIFF IO and routing contracts.
These fixtures are not evidence of remote satellite acquisition.
"""
import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from importlib.util import find_spec
from backend.core.geo_services import GeoError
from backend.analysis.spectral_indices import specification,choose_index,detect_indices,available_indices,BANDS
from backend.analysis.production_services import make_plan,SentinelReader
from backend.analysis.native_raster import grids,compute_native,read_native,VERSION
from backend.analysis.raster_cache import RasterWindowCache
from backend.server import Workspace

READY=all(find_spec(x) for x in ('numpy','rasterio','PIL'))
BOX=[114.2,30.5,114.21,30.51]

def plan(id,**kw):
    return make_plan({'requirement':'计算所选区域2025年11月'+id.upper()+'大于0的范围','mode':'sentinel_cog','sampling':'native','index_id':id,'bbox':BOX,**kw})

class SpectralContractTest(unittest.TestCase):
    def test_explicit_names_conflicts_and_distinct_ndwi_definitions(self):
        self.assertEqual(detect_indices('分析MNDWI变化'),['mndwi']);self.assertEqual(detect_indices('NDWI和NDMI'),['ndwi','ndmi'])
        self.assertEqual(choose_index('改进型归一化水体指数')['id'],'mndwi');self.assertNotEqual(specification('ndwi')['required_bands'],specification('ndmi')['required_bands'])
        for question,supplied in [('MNDWI','ndvi'),('NDMI','ndbi')]:
            with self.assertRaises(GeoError) as e:choose_index(question,supplied)
            self.assertEqual(e.exception.code,'INDEX_CONFLICT')
        with self.assertRaises(GeoError):choose_index('计算NDMI和NDWI')
        for code in ('EVI','LAI','FVC','NDSI','增强植被指数'):
            with self.assertRaises(GeoError) as e:choose_index('计算'+code+'植被指数')
            self.assertEqual(e.exception.code,'INDEX_UNSUPPORTED')
    def test_index_plan_only_allows_real_native_and_keeps_negative_threshold(self):
        for id in ('ndwi','mndwi','ndmi','ndbi','nbr'):
            p=plan(id);self.assertTrue(p['executable']);self.assertEqual(p['selection']['field'],'after_'+id);self.assertEqual(p['selection']['threshold'],0);self.assertEqual(p['selection']['operator'],'gt')
            with self.assertRaises(GeoError):make_plan({'requirement':id+'分析','mode':'sample_pixels'})
            with self.assertRaises(GeoError):make_plan({'requirement':id+'分析','mode':'sentinel_cog','sampling':'representative'})
        p=plan('mndwi',threshold=-.1);self.assertEqual(p['threshold'],-.1)
        self.assertEqual(specification('mndwi')['reference_band'],'B11');self.assertEqual(specification('ndwi')['reference_band'],'B03')
    def test_selective_discovery_and_xml_offsets_for_exact_needed_bands(self):
        reader=SentinelReader(None);prefix='https://sentinel2l2a01.blob.core.windows.net/sentinel2-l2/scene/'
        xml=('<root><BOA_QUANTIFICATION_VALUE>10000</BOA_QUANTIFICATION_VALUE>'+''.join('<BOA_ADD_OFFSET band_id="'+str(i)+'">-'+str(1000+i)+'</BOA_ADD_OFFSET>' for i in range(13))+'</root>').encode()
        scene={'id':'unit-fixture','bbox':[113,29,116,32],'properties':{'eo:cloud_cover':1,'datetime':'2025-11-15T00:00:00Z'},'assets':{b:{'href':prefix+b+'.tif'} for b in ('B03','B04','B08','B11','B12','SCL')}}
        scene['assets']['product-metadata']={'href':prefix+'metadata.xml'}
        reader.fetch_json=lambda url,check:{'features':[scene]};reader.fetch_bytes=lambda *a,**kw:xml;reader.signed_url=lambda u,c:u
        for id in ('ndvi','ndwi','mndwi','ndmi','ndbi','nbr'):
            p=plan(id);r=reader.discover(p,lambda:None)['after'];expected={b['name'] for b in p['index']['required_bands']}|{'scl'}
            self.assertEqual(set(r['assets']),expected)
            for b in p['index']['required_bands']:
                a=r['assets'][b['name']];self.assertEqual(a['scale'],.0001);self.assertAlmostEqual(a['offset'],-(1000+int(b['xml_band_id']))/10000)
            self.assertEqual(set(r['calibration']['boa_add_offset']),{b['id'] for b in p['index']['required_bands']})
    def test_named_other_city_requires_bbox_and_cannot_become_full_region(self):
        with tempfile.TemporaryDirectory() as tmp:
            w=Workspace(Path(tmp)/'test.sqlite3')
            try:
                for city in ('武汉市','宜昌市','襄阳市'):
                    q='分析'+city+'所选区域2025年11月MNDWI大于0的范围'
                    self.assertEqual(w.executions.prepare(q,{'bbox':BOX})['status'],'ready');self.assertEqual(w.executions.prepare(q,{})['missing_fields'],['bbox'])
                for q in ('分析湖北省全省2025年11月MNDWI','统计武汉市全市NDWI水体面积','统计MNDWI灾损面积','所选区2025年11月MNDWI统计高标准农田面积','所选区2025年11月NDVI用于土地确权'):
                    self.assertNotEqual(w.executions.prepare(q,{'bbox':BOX})['status'],'ready')
                self.assertEqual(w.executions.prepare('分析选区2025年11月EVI植被变化',{'bbox':BOX})['status'],'unsupported')
                conflict=w.executions.prepare('分析所选区域2025年11月MNDWI',{'bbox':BOX,'period':{'start':'2025-10-01','end':'2025-10-31'}});self.assertNotEqual(conflict['status'],'ready');self.assertTrue(any('PERIOD_CONFLICT' in x for x in conflict['blocking_gaps']))
            finally:w.close()
    def test_cache_identity_separates_index_formula_and_reference(self):
        with tempfile.TemporaryDirectory() as tmp:
            w=Workspace(Path(tmp)/'test.sqlite3')
            try:
                ids=[w.executions.input_key('sentinel_native',{'requirement':'分析选区2025年11月'+id,'mode':'sentinel_cog','sampling':'native','index_id':id,'bbox':BOX}) for id in ('ndvi','ndwi','mndwi','ndmi','ndbi','nbr')]
                self.assertEqual(len(set(ids)),6)
            finally:w.close()

@unittest.skipUnless(READY,'project raster runtime required')
class SpectralMathTest(unittest.TestCase):
    def setUp(self):self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
    def tearDown(self):self.tmp.cleanup()
    def data(self,id,shape=(2,2)):
        import numpy as np
        p=plan(id);a=p['index'];resolution=a['reference_resolution_m'];values={'green':.3,'red':.1,'nir':.5,'swir1':.2,'swir2':.1}
        epoch={b['name']:np.full(shape,values[b['name']],dtype='float32') for b in a['required_bands']};epoch['scl']=np.full(shape,4,dtype='float32')
        metadata={'sampling':'native','source_type':'public_satellite_pixels','provider':'offline unit fixture','crs':'EPSG:32649','transform':[resolution,0,720000,0,-resolution,3360000],'cell_area_m2':resolution**2,'resampling':'required bands nearest aligned to original reference','input_sha256':'a'*64,'scenes':{'after':{'datetime':'2025-11-15T00:00:00Z'}}}
        return p,{'after':epoch,'metadata':metadata,'_mask':np.ones(shape,dtype=bool)}
    def calc(self,p,b,name='result'):return compute_native(p,b,'test',self.root/name,lambda:None)
    def test_six_formulas_from_calibrated_values_and_needed_band_only_masks(self):
        expected={'ndvi':2/3,'ndwi':-.25,'mndwi':.2,'ndmi':3/7,'ndbi':-3/7,'nbr':2/3}
        for id,value in expected.items():
            with self.subTest(index=id):
                p,b=self.data(id);r=self.calc(p,b,id);self.assertAlmostEqual(r['summary']['mean_index'],value,places=6);self.assertEqual(r['summary']['valid_count'],4)
                self.assertEqual(sum(h['count'] for h in r['summary']['index_histogram']),4)
                if id!='ndvi':self.assertNotIn('mean_ndvi',r['summary']);self.assertNotIn('ndvi_histogram',r['summary'])
        p,b=self.data('mndwi');b['after']['red']=__import__('numpy').full((2,2),float('nan'));self.assertEqual(self.calc(p,b,'irrelevant-red')['summary']['valid_count'],4)
    def test_unclassified_is_counted_and_warning_is_not_accuracy(self):
        p,b=self.data('mndwi');b['after']['scl'][0,0]=7;r=self.calc(p,b);self.assertEqual(r['summary']['unclassified_retained_count'],1);self.assertEqual(r['summary']['valid_count'],4);self.assertIn('SCL7',r['summary']['quality_warning']);self.assertIn('非严格无云',r['provenance']['quality']['policy_note'])
    def test_twenty_metre_grid_area_and_export_inputs_allow_independent_formula(self):
        import numpy as np,rasterio
        p,b=self.data('mndwi');r=self.calc(p,b);self.assertEqual(r['summary']['selected_count'],4);self.assertEqual(r['summary']['selected_area_ha'],.16)
        with rasterio.open(self.root/'result/analysis.tif') as d:
            self.assertEqual(d.descriptions,('after_mndwi','common_valid_mask','aoi_mask','after_green_reflectance','after_swir1_reflectance','after_scl'));self.assertEqual(abs(d.transform.a),20);self.assertEqual(d.tags(4)['source_band'],'B03')
            a,z,scl=d.read(4),d.read(5),d.read(6);valid=np.isfinite(a)&np.isfinite(z)&(a>=0)&(z>=0)&((a+z)>0)&np.isin(scl,[4,5,6,7])&(d.read(3)==1)
            np.testing.assert_array_equal(valid,d.read(2)==1);np.testing.assert_allclose(d.read(1)[valid],((a-z)/(a+z))[valid],rtol=0,atol=0)
        self.assertEqual(r['provenance']['grid']['reference_resolution_m'],20);self.assertTrue(all('NDVI' not in x['label'] for x in r['image']['legend']))
    def test_two_period_difference_keeps_after_minus_before_sign_and_input_bands(self):
        import numpy as np,rasterio
        p,b=self.data('nbr');p.update(goal='change',direction='decrease',threshold=.1);b['before']=copy.deepcopy(b['after']);b['before']['swir2'][:]=0
        r=self.calc(p,b);self.assertLess(r['summary']['mean_delta'],0);self.assertEqual(r['summary']['selected_count'],4);self.assertEqual(r['summary']['change_formula'],'after_nbr-before_nbr')
        with rasterio.open(self.root/'result/analysis.tif') as d:self.assertIn('before_swir2_reflectance',d.descriptions);self.assertIn('before_scl',d.descriptions);self.assertNotIn('delta_ndvi',d.descriptions)

@unittest.skipUnless(READY,'project raster runtime required')
class SpectralWindowTest(unittest.TestCase):
    def setUp(self):
        import numpy as np,rasterio
        from rasterio.transform import from_origin
        from rasterio.warp import transform_bounds
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name);self.paths={};self.open=rasterio.open
        for name,res,value in [('green',10,4000),('nir',10,6000),('swir1',20,3000),('scl',20,4)]:
            path=self.root/(name+'.tif');self.paths[name]=str(path);size=160//res
            with rasterio.open(path,'w',driver='GTiff',height=size,width=size,count=1,dtype='uint16',crs='EPSG:32649',transform=from_origin(720000,3360000,res,res),nodata=0) as d:d.write(np.full((size,size),value,dtype='uint16'),1)
        self.box=list(transform_bounds('EPSG:32649','EPSG:4326',720020,3359860,720140,3359980));self.cache=RasterWindowCache();self.patch=patch('backend.analysis.raster_cache.WINDOW_CACHE',self.cache);self.patch.start()
    def tearDown(self):self.patch.stop();self.tmp.cleanup()
    def read(self,id):
        p=plan(id,bbox=self.box);assets={b['name']:{'href':self.paths[b['name']],'scale':.0001,'offset':-.1,'nodata':0} for b in p['index']['required_bands']};assets['scl']={'href':self.paths['scl'],'scale':1,'offset':0,'nodata':0};scenes={'after':{'id':'offline-fixture','datetime':'2025-11-15T00:00:00Z','assets':assets}}
        class Reader:
            def signed_url(self,url,check):return url
        return p,read_native(p,scenes,Reader(),lambda:None)
    def test_actual_local_tiff_mixed_resolution_read_cache_and_input_hash_stability(self):
        p,a=self.read('mndwi');p,b=self.read('mndwi');self.assertEqual(a['metadata']['input_sha256'],b['metadata']['input_sha256']);self.assertEqual(a['metadata']['efficiency']['source_window_cache_hits'],0);self.assertEqual(b['metadata']['efficiency']['source_window_cache_hits'],3);self.assertEqual(abs(a['metadata']['transform'][0]),20);self.assertEqual(set(a['after']),{'green','swir1','scl'})
        values,valid=grids(a,'mndwi')[0]['after'];self.assertTrue(valid.any());self.assertAlmostEqual(float(values[valid][0]),.2,places=6)
        p,c=self.read('ndwi');self.assertEqual(abs(c['metadata']['transform'][0]),10);self.assertEqual(c['metadata']['efficiency']['source_window_cache_hits'],2);self.assertEqual(set(c['after']),{'green','nir','scl'})
    def test_bad_scl_preflight_does_not_read_optical_band_pixels(self):
        import numpy as np,rasterio
        with rasterio.open(self.paths['scl'],'r+') as d:d.write(np.full((8,8),9,dtype='uint16'),1)
        calls=[];original=self.open
        class Record:
            def __init__(self,path,*a,**kw):self.ds=original(path,*a,**kw);self.path=str(path)
            def __enter__(self):return self
            def __exit__(self,*args):self.ds.close()
            def __getattr__(self,k):return getattr(self.ds,k)
            def read(self,*a,**kw):calls.append(self.path);return self.ds.read(*a,**kw)
        with patch('rasterio.open',side_effect=Record),self.assertRaises(GeoError) as error:self.read('mndwi')
        self.assertEqual(error.exception.code,'INSUFFICIENT_VALID_PIXELS');self.assertEqual(calls,[self.paths['scl']])

if __name__=='__main__':unittest.main()
