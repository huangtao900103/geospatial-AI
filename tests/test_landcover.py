import csv
import hashlib
import json
import math
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch
from geo_services import GeoError
from landcover_services import (ellipsoid_row_pixel_area,covering_tiles,load_boundary,account_native,
                                make_preview,LandcoverServices,available,CLASSES)
from server import Workspace


class AreaTest(unittest.TestCase):
    def test_wgs84_row_area_matches_independent_equal_area_projection(self):
        if not available():self.skipTest('rasterio optional')
        from rasterio.warp import transform
        x,y=transform('EPSG:4326','EPSG:6933',[113.0,113.00008333333333],[30.0,30.00008333333333])
        expected=abs((x[1]-x[0])*(y[1]-y[0]));actual=ellipsoid_row_pixel_area(30.00008333333333,30.0,.00008333333333)
        self.assertAlmostEqual(actual,expected,places=5);self.assertLess(actual,100);self.assertGreater(actual,70)
    def test_invalid_area_parameters_do_not_become_fake_area(self):
        for triple in [(0,1,1),(91,90,1),(1,0,True),(float('nan'),0,1),(10**1000,0,1)]:
            with self.assertRaises(GeoError):ellipsoid_row_pixel_area(*triple)
    def test_covering_tiles_cross_boundaries_without_duplicates(self):
        self.assertEqual(covering_tiles([112.9,30.09,113.84,30.54]),['N30E111'])
        self.assertEqual(covering_tiles([113.9,29.9,114.1,30.1]),['N27E111','N27E114','N30E111','N30E114'])


@unittest.skipUnless(available(),'rasterio/numpy/PIL required for native-grid tests')
class NativeTest(unittest.TestCase):
    def setUp(self):
        import numpy as np
        import rasterio
        from rasterio.transform import from_origin
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name);self.res=1/12000;self.left=113.;self.top=30.5
        self.bbox=[self.left,self.top-4*self.res,self.left+4*self.res,self.top]
        self.geo={'type':'FeatureCollection','features':[{'type':'Feature','geometry':{'type':'Polygon','coordinates':[[[self.bbox[0],self.bbox[1]],[self.bbox[2],self.bbox[1]],[self.bbox[2],self.bbox[3]],[self.bbox[0],self.bbox[3]],[self.bbox[0],self.bbox[1]]]]},'properties':{'name':'test only'}}]}
        self.meta={'bbox':self.bbox,'provider':'synthetic unit-test boundary','boundary_year_represented':2017,'sha256':'test','validation':{}}
        self.path=self.root/'native.tif'
        self.values=np.array([[40,40,80,0],[40,10,80,0],[40,50,30,0],[0,0,0,0]],dtype='uint8')
        with rasterio.open(self.path,'w',driver='GTiff',width=4,height=4,count=1,dtype='uint8',crs='EPSG:4326',transform=from_origin(self.left,self.top,self.res,self.res),nodata=0) as dst:dst.write(self.values,1)
    def tearDown(self):self.tmp.cleanup()
    def account(self,geo=None):return account_native(geo or self.geo,self.meta,self.root/'result',source_urls=[str(self.path)])
    def test_full_native_classes_coverage_area_and_row_reconstruction(self):
        result=self.account();s=result['summary'];self.assertEqual(s['inside_pixel_count'],16);self.assertEqual(s['cropland_pixel_count'],4);self.assertEqual(s['valid_pixel_count'],9);self.assertEqual(s['nodata_pixel_count'],7)
        self.assertAlmostEqual(sum(c['area_ha'] for c in result['classes'])+s['nodata_area_ha'],s['total_area_ha'],places=10)
        with (self.root/'result'/'row_counts.csv').open() as row_file:rows=list(csv.DictReader(row_file))
        self.assertEqual(sum(int(r['class_40']) for r in rows),4)
        area=sum(int(r['class_40'])*ellipsoid_row_pixel_area(float(r['north']),float(r['south']),float(r['pixel_width_deg'])) for r in rows)/10000
        self.assertAlmostEqual(area,s['cropland_ha'],places=10)
        self.assertEqual(result['source_tiles'][0]['window_shape'],[4,4]);self.assertEqual(s['processed_bbox_pixel_count'],16)
    def test_overlapping_boundary_features_do_not_double_count(self):
        duplicate={'type':'FeatureCollection','features':self.geo['features']*2};r=self.account(duplicate);self.assertEqual(r['summary']['inside_pixel_count'],16);self.assertEqual(r['summary']['cropland_pixel_count'],4)
    def test_hole_removes_native_cells(self):
        import copy
        g=copy.deepcopy(self.geo);l,t,z=self.left,self.top,self.res
        g['features'][0]['geometry']['coordinates'].append([[l+z,t-z],[l+3*z,t-z],[l+3*z,t-3*z],[l+z,t-3*z],[l+z,t-z]])
        r=self.account(g);self.assertEqual(r['summary']['inside_pixel_count'],12)
    def test_preview_georeferencing_and_native_tags(self):
        import rasterio
        from PIL import Image
        r=self.account();p=make_preview(r['native_path'],self.root/'result');self.assertEqual(p['display_crs'],'EPSG:3857');self.assertEqual(len(p['bounds']),4)
        with Image.open(self.root/'result'/'overlay.png') as image:self.assertEqual(image.mode,'RGBA')
        with rasterio.open(r['native_path']) as ds:
            self.assertEqual(ds.crs.to_epsg(),4326);self.assertEqual(ds.tags()['product_year'],'2021');self.assertEqual(ds.tags()['class_40'],'Cropland')
    def test_boundary_hash_is_verified(self):
        folder=self.root/'boundary';folder.mkdir();raw=json.dumps(self.geo).encode();(folder/'xiantao_boundary.geojson').write_bytes(raw)
        (folder/'xiantao_boundary.metadata.json').write_text(json.dumps({'sha256':hashlib.sha256(raw).hexdigest()}))
        self.assertEqual(load_boundary(folder)[0],self.geo)
        (folder/'xiantao_boundary.geojson').write_text('{}')
        with self.assertRaises(GeoError):load_boundary(folder)
    def test_cancellation_stops_scan_no_fabricated_result(self):
        def cancel():raise GeoError('CANCELLED','test cancelled')
        with self.assertRaises(GeoError):account_native(self.geo,self.meta,self.root/'cancelled',check=cancel,source_urls=[str(self.path)])


class LifecycleTest(unittest.TestCase):
    def setUp(self):self.tmp=tempfile.TemporaryDirectory();self.w=Workspace(Path(self.tmp.name)/'db.sqlite')
    def tearDown(self):self.w.close();self.tmp.cleanup()
    def test_scope_product_and_requirement_are_bounded(self):
        for body in [{'region':'全省'},{'product':'worldcover_2026'},{'url':'https://evil.test/x.tif'},{'requirement':''},{'requirement':'核算2026年仙桃市面积'}]:
            with self.assertRaises(GeoError):self.w.landcover.create(body)
    def test_natural_year_and_region_are_not_overridden_by_product_field(self):
        for requirement,code in [('统计2026年仙桃全市耕地面积','YEAR_MISMATCH'),('使用2021基准比较2025年仙桃面积','YEAR_MISMATCH'),('核算湖北省全省耕地面积','REGION_UNSUPPORTED'),('核算武汉市2021年耕地面积','REGION_UNSUPPORTED'),('核算仙桃市与武汉市2021年耕地面积','REGION_UNSUPPORTED')]:
            with self.subTest(requirement=requirement),self.assertRaises(GeoError) as error:self.w.landcover.create({'product':'worldcover_2021_v200','requirement':requirement})
            self.assertEqual(error.exception.code,code);self.assertEqual(error.exception.status,422)
    def test_city_references_in_user_request_and_default_ui_are_not_other_cities(self):
        requirements=[
            '核算仙桃全市耕地分类面积，并展示全市耕地分布与各地类面积。',
            '仙桃市要统计全市农田面积，这个平台可以拿遥感数据来做，怎么样跑通流程？',
        ]
        with patch('landcover_services.threading.Thread'):
            for requirement in requirements:
                task=self.w.landcover.create({'requirement':requirement})
                self.assertEqual(task['plan']['region'],'仙桃市')
                self.assertEqual(task['plan']['requirement'],requirement)
                self.w.landcover.cancel(task['id'])
        with self.assertRaises(GeoError) as error:self.w.landcover.create({'requirement':'统计武汉全市2021年农田面积'})
        self.assertEqual(error.exception.code,'REGION_UNSUPPORTED')

    def test_cancel_is_terminal_and_retry_new_id(self):
        entered=threading.Event()
        def hold(*args,**kwargs):
            check=kwargs.get('check') or args[3]
            entered.set()
            while True:check();time.sleep(.005)
        with patch('landcover_services.account_native',side_effect=hold),patch('landcover_services.available',return_value=True):
            task=self.w.landcover.create({});self.assertTrue(entered.wait(1));self.assertEqual(self.w.landcover.cancel(task['id'])['status'],'cancelled');time.sleep(.02)
            self.assertEqual(self.w.landcover.get(task['id'])['status'],'cancelled');again=self.w.landcover.retry(task['id']);self.assertNotEqual(task['id'],again['id']);self.w.landcover.cancel(again['id'])
    def test_recovery_updates_running_node(self):
        record={'id':'landcover-interrupted','status':'running','nodes':[{'status':'running'}],'logs':[],'created_at':'2025-01-01'};self.w.save('landcover_task',record)
        recovered=LandcoverServices(self.w).get(record['id']);self.assertEqual(recovered['status'],'failed');self.assertEqual(recovered['nodes'][0]['status'],'failed')


if __name__=='__main__':unittest.main()
