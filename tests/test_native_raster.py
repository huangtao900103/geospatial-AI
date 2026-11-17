"""Offline numeric/export fixtures, not evidence of a remote satellite acquisition."""
import copy
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from importlib.util import find_spec
from backend.core.geo_services import GeoError
from backend.analysis.native_raster import compute_native,grids,quality_summary,VERSION

READY=all(find_spec(m) for m in ('numpy','rasterio','PIL'))


@unittest.skipUnless(READY,'project raster runtime required')
class NativeRasterTest(unittest.TestCase):
    def setUp(self):self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
    def tearDown(self):self.tmp.cleanup()
    def fixture(self,after,before=None,mask=None):
        import numpy as np
        from affine import Affine
        from rasterio.transform import array_bounds
        from rasterio.warp import transform_bounds
        a=np.asarray(after,dtype='float32');rows,cols=a.shape
        def epoch(values):
            values=np.asarray(values,dtype='float32');return {'red':1-values,'nir':1+values,'scl':np.full(values.shape,4,dtype='float32')}
        transform=Affine(10,0,720000,0,-10,3360000);bounds=array_bounds(rows,cols,transform);bbox=list(transform_bounds('EPSG:32649','EPSG:4326',*bounds,densify_pts=21))
        meta={'sampling':'native','algorithm_version':VERSION,'source_type':'public_satellite_pixels','provider':'offline mathematical fixture, not an acquisition','bbox':bbox,'rows':rows,'cols':cols,'crs':'EPSG:32649','transform':list(transform)[:6],'cell_area_m2':100.,'resampling':'before/NIR/SCL nearest onto after-B04 reference grid','input_sha256':hashlib.sha256(a.tobytes()).hexdigest(),'scenes':{'after':{'id':'fixture-after','datetime':'2025-11-20T00:00:00Z'}}}
        bands={'after':epoch(a),'metadata':meta,'_mask':np.ones((rows,cols),dtype='bool') if mask is None else np.asarray(mask,dtype='bool')}
        if before is not None:bands['before']=epoch(before);meta['scenes']['before']={'id':'fixture-before','datetime':'2025-10-20T00:00:00Z'}
        plan={'mode':'sentinel_cog','sampling':'native','requirement':'offline numerical fixture','goal':'change' if before is not None else 'ndvi','direction':'absolute','threshold':.25,'selection':{'operator':'gte'},'aoi':{'bbox':bbox},'periods':{'before':['2025-10-01','2025-10-31'],'after':['2025-11-01','2025-11-30']},'limitations':['Offline synthetic arrays for numeric tests only.']}
        return plan,bands
    def calculate(self,plan,bands,name='result'):
        return compute_native(plan,bands,'test-native',self.root/name,lambda:None)
    def quality_fixture(self):
        import numpy as np
        p,b=self.fixture([[.25,.5,.5],[-.25,.25,0]],[[.5,.25,0],[-.5,0,.5]])
        b['before']['scl'][0,2]=9;b['after']['scl'][1,0]=3;b['after']['red'][1,2]=np.nan
        return p,b
    def test_common_mask_and_mean_are_from_same_real_input_cells(self):
        p,b=self.quality_fixture();r=self.calculate(p,b);s=r['summary']
        self.assertEqual(s['native_pixel_count'],6);self.assertEqual(s['before_valid_count'],5);self.assertEqual(s['after_valid_count'],4);self.assertEqual(s['valid_count'],3);self.assertEqual(s['invalid_count'],3);self.assertEqual(s['valid_ratio'],.5)
        self.assertAlmostEqual(s['mean_before_ndvi'],.25);self.assertAlmostEqual(s['mean_ndvi'],1/3,places=7);self.assertAlmostEqual(s['mean_delta'],1/12,places=7)
        self.assertEqual(s['mask_reasons']['before']['scl_excluded'],1);self.assertEqual(s['mask_reasons']['after']['scl_excluded'],1);self.assertEqual(s['mask_reasons']['after']['nodata'],1)
        self.assertEqual(sum(bin['count'] for bin in s['ndvi_histogram']),3)
    def test_strict_inclusive_direction_and_absolute_thresholds(self):
        p,b=self.fixture([[.25,-.25,.5,-.5,0]],[[0,0,0,0,0]])
        for direction,operator,expected in [('increase','gt',1),('increase','gte',2),('decrease','gt',1),('decrease','gte',2),('absolute','lt',1),('absolute','lte',3)]:
            with self.subTest(direction=direction,operator=operator):
                p['direction']=direction;p['selection']['operator']=operator;r=self.calculate(p,b,direction+operator);self.assertEqual(r['summary']['selected_count'],expected);self.assertAlmostEqual(r['summary']['selected_area_ha'],expected*.01)
    def test_single_period_lower_comparison_and_exact_area(self):
        p,b=self.fixture([[0,.25,.5]])
        p['selection']['operator']='lt';r=self.calculate(p,b);s=r['summary'];self.assertEqual(s['selected_count'],1);self.assertEqual(s['selected_area_ha'],.01);self.assertEqual(s['analyzed_area_ha'],.03);self.assertIsNone(s['mean_before_ndvi']);self.assertIsNone(s['mean_delta'])
    def test_no_valid_or_too_low_valid_fails_before_export(self):
        import numpy as np
        for name,valid in [('all-invalid',0),('one-of-sixteen',1)]:
            p,b=self.fixture(np.full((4,4),.5));b['after']['scl'][:]=9
            if valid:b['after']['scl'][0,0]=4
            with self.subTest(name=name),self.assertRaises(GeoError) as error:self.calculate(p,b,name)
            self.assertEqual(error.exception.code,'INSUFFICIENT_VALID_PIXELS');self.assertFalse((self.root/name/'analysis.tif').exists())
    def test_low_quality_warning_is_not_false_success_precision(self):
        import numpy as np
        p,b=self.fixture(np.full((4,4),.5));b['after']['scl'][:]=9;b['after']['scl'][0,:2]=4
        r=self.calculate(p,b);self.assertEqual(r['summary']['valid_ratio'],.125);self.assertIn('低于50%',r['summary']['quality_warning']);self.assertEqual(r['provenance']['quality']['valid_ratio'],.125)
    def test_aoi_outside_is_not_counted_as_zero_or_cloud(self):
        import numpy as np
        p,b=self.quality_fixture();b['_mask'][0,2]=False
        data,reasons=grids(b);self.assertFalse(data['after'][1][0,2]);self.assertTrue(np.isnan(data['after'][0][0,2]));self.assertEqual(reasons['before']['scl_excluded'],0)
        r=self.calculate(p,b);self.assertEqual(r['summary']['native_pixel_count'],5);self.assertEqual(r['summary']['valid_count'],3);self.assertAlmostEqual(r['summary']['analyzed_area_ha'],.05)
    def test_invalid_reflectance_and_zero_denominator_not_pseudo_zero_ndvi(self):
        import numpy as np
        p,b=self.fixture([[.5,.5,.5,.5]])
        b['after']['red'][0,0]=np.nan;b['after']['red'][0,1]=-.1;b['after']['red'][0,2]=0;b['after']['nir'][0,2]=0
        data,reasons=grids(b);self.assertEqual(int(data['after'][1].sum()),1);self.assertTrue(np.isnan(data['after'][0][0,:3]).all());self.assertEqual(reasons['after']['nodata'],1);self.assertEqual(reasons['after']['invalid_reflectance'],2)
        q=quality_summary(b);self.assertEqual(q['after']['valid_count'],1)
    def test_all_change_geotiff_bands_are_correct_and_georeferenced(self):
        import numpy as np
        import rasterio
        from affine import Affine
        p,b=self.quality_fixture();r=self.calculate(p,b);data,_=grids(b);common=data['after'][1]&data['before'][1]
        with rasterio.open(self.root/'result'/'analysis.tif') as ds:
            self.assertEqual(ds.count,5);self.assertEqual(ds.descriptions,('after_ndvi','before_ndvi','delta_ndvi','common_valid_mask','aoi_mask'));self.assertEqual(ds.crs.to_epsg(),32649);self.assertEqual(ds.transform,Affine(*b['metadata']['transform']));self.assertTrue(all(t=='float32' for t in ds.dtypes));self.assertTrue(np.isnan(ds.nodata))
            np.testing.assert_allclose(ds.read(1),data['after'][0],equal_nan=True);np.testing.assert_allclose(ds.read(2),data['before'][0],equal_nan=True)
            np.testing.assert_allclose(ds.read(3),np.where(common,data['after'][0]-data['before'][0],np.nan),equal_nan=True)
            np.testing.assert_allclose(ds.read(4),common.astype('float32'));np.testing.assert_allclose(ds.read(5),b['_mask'].astype('float32'));self.assertEqual(ds.tags()['sampling'],'native');self.assertEqual(ds.tags()['algorithm_version'],VERSION);self.assertEqual(ds.tags()['input_sha256'],b['metadata']['input_sha256'])
    def test_single_period_band_count_and_outside_mask_export(self):
        import numpy as np
        import rasterio
        p,b=self.fixture([[.5,.25],[0,-.5]],mask=[[True,False],[True,True]]);self.calculate(p,b)
        with rasterio.open(self.root/'result'/'analysis.tif') as ds:
            self.assertEqual(ds.descriptions,('after_ndvi','common_valid_mask','aoi_mask'));self.assertTrue(np.isnan(ds.read(1)[0,1]));self.assertTrue(np.isnan(ds.read(2)[0,1]));self.assertEqual(ds.read(3)[0,1],0)
    def test_png_geojson_and_statistics_are_separate_artifacts(self):
        import numpy as np
        from PIL import Image
        p,b=self.quality_fixture();r=self.calculate(p,b)
        self.assertEqual(len(r['geojson']['features']),1);self.assertEqual(r['geojson']['features'][0]['geometry']['type'],'Polygon');self.assertEqual(r['summary']['native_pixel_count'],6);self.assertEqual(r['image']['crs'],'EPSG:3857');self.assertFalse(r['image']['statistics_from_preview']);self.assertLessEqual(max(r['image']['width'],r['image']['height']),1536)
        w,s,e,n=r['image']['bounds'];self.assertLess(w,e);self.assertLess(s,n)
        with Image.open(self.root/'result'/'overlay.png') as image:
            self.assertEqual(image.mode,'RGBA');rgba=np.asarray(image);self.assertTrue(set(np.unique(rgba[:,:,3]))<={0,220});self.assertTrue((rgba[:,:,3]>0).any())
        saved=json.loads((self.root/'result'/'result.json').read_text());self.assertEqual(saved['summary'],r['summary']);self.assertEqual(saved['provenance']['algorithm_version'],VERSION);self.assertEqual(saved['provenance']['input_sha256'],b['metadata']['input_sha256']);self.assertEqual({a['type'] for a in saved['artifacts']},{'geotiff','png','json'})
    def test_cancellation_checks_prevent_publishing_success(self):
        p,b=self.fixture([[.5,.5]])
        def check():raise GeoError('CANCELLED','offline cancellation')
        with self.assertRaises(GeoError) as error:compute_native(p,b,'cancelled',self.root/'cancelled',check)
        self.assertEqual(error.exception.code,'CANCELLED');self.assertFalse((self.root/'cancelled'/'result.json').exists())


if __name__=='__main__':unittest.main()
