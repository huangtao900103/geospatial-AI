"""Native one-cell inspector checks using only temporary local GeoTIFFs."""
import copy
import hashlib
import tempfile
import unittest
from pathlib import Path
from importlib.util import find_spec
from backend.core.inspection_services import InspectionServices
from backend.core.geo_services import GeoError

class Service:
    def __init__(self,task,root):self.task=task;self.root=root;self.names=[]
    def get(self,id):return copy.deepcopy(self.task)
    def artifact(self,id,name):self.names.append(name);return self.root/name
class Store:pass

@unittest.skipUnless(all(find_spec(m) for m in ('rasterio','numpy')),'raster runtime required')
class InspectionTest(unittest.TestCase):
    def setUp(self):
        import numpy as np
        import rasterio
        from rasterio.transform import from_origin
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
        a=np.array([[.2,.4],[np.nan,.6]],dtype='float32');common=np.array([[1,0],[np.nan,1]],dtype='float32');aoi=np.array([[1,1],[0,1]],dtype='float32')
        with rasterio.open(self.root/'analysis.tif','w',driver='GTiff',height=2,width=2,count=3,dtype='float32',crs='EPSG:4326',transform=from_origin(114.2,30.52,.01,.01),nodata=float('nan')) as ds:
            for i,(name,data) in enumerate(zip(('after_ndvi','common_valid_mask','aoi_mask'),(a,common,aoi)),1):ds.write(data,i);ds.set_band_description(i,name)
        production={'id':'p1','status':'succeeded','plan':{'sampling':'native','goal':'ndvi'},'result':{'summary':{'index_id':'ndvi'},'provenance':{'grid':{'reference_resolution_m':10},'source_details':{'scenes':{'after':{'datetime':'2025-11-14T00:00:00Z'}}}}}}
        boundary={'type':'FeatureCollection','features':[{'type':'Feature','geometry':{'type':'Polygon','coordinates':[[[114.2,30.52],[114.22,30.52],[114.2,30.5],[114.2,30.52]]]},'properties':{}}]}
        with rasterio.open(self.root/'classification.tif','w',driver='GTiff',height=2,width=2,count=1,dtype='uint8',crs='EPSG:4326',transform=from_origin(114.2,30.52,.01,.01),nodata=0) as ds:ds.write(np.array([[40,80],[255,40]],dtype='uint8'),1)
        landcover={'id':'lc1','status':'succeeded','result':{'boundary':boundary,'summary':{'reference_year':2021,'nominal_resolution_m':10},'provenance':{'year':2021}}}
        self.store=Store();self.store.production=Service(production,self.root);self.store.landcover=Service(landcover,self.root);self.inspect=InspectionServices(self.store)
    def tearDown(self):self.tmp.cleanup()
    def test_one_local_valid_cell_and_source_metadata(self):
        r=self.inspect.inspect('production','p1',114.205,30.515);self.assertTrue(r['inside_aoi']);self.assertTrue(r['valid']);self.assertEqual(r['row'],0);self.assertEqual(r['column'],0);self.assertAlmostEqual(r['value'],.2,places=6);self.assertEqual(r['actual_dates'],['2025-11-14']);self.assertEqual(r['reference_resolution_m'],10);self.assertEqual(self.store.production.names,['analysis.tif'])
    def test_common_invalid_does_not_publish_finite_after_value(self):
        r=self.inspect.inspect('production','p1',114.215,30.515);self.assertTrue(r['inside_aoi']);self.assertFalse(r['valid']);self.assertIsNone(r['value']);self.assertAlmostEqual(r['bands']['after_ndvi'],.4,places=6);self.assertEqual(r['status'],'invalid')
    def test_outside_aoi_and_outside_raster_are_distinct_and_nan_is_null(self):
        r=self.inspect.inspect('production','p1',114.205,30.505);self.assertFalse(r['inside_aoi']);self.assertEqual(r['status'],'outside_aoi');self.assertIsNone(r['value']);self.assertIsNone(r['bands']['after_ndvi'])
        r=self.inspect.inspect('production','p1',110,20);self.assertEqual(r['status'],'outside_raster');self.assertIsNone(r['row']);self.assertEqual(r['bands'],{});self.assertIsNone(r['value'])
    def test_invalid_kind_id_coordinate_and_unfinished_task(self):
        for kind,id,x,y in [('dataset','p1',114,30),('production','../p1',114,30),('production','p1',float('nan'),30),('production','p1',181,30),('production','p1',True,30)]:
            with self.subTest(kind=kind,id=id,x=x),self.assertRaises(GeoError):self.inspect.inspect(kind,id,x,y)
        self.store.production.task['status']='running'
        with self.assertRaises(GeoError) as e:self.inspect.inspect('production','p1',114,30)
        self.assertEqual(e.exception.code,'INSPECTION_NOT_READY')
    def test_legacy_sample_is_explicitly_unsupported(self):
        self.store.production.task['plan']['sampling']='representative'
        with self.assertRaises(GeoError) as e:self.inspect.inspect('production','p1',114,30)
        self.assertEqual(e.exception.code,'INSPECTION_UNSUPPORTED');self.assertEqual(self.store.production.names,[])
    def test_landcover_respects_reference_polygon_not_bbox(self):
        # Lower-right cell has class40 but its centre is outside the triangle.
        r=self.inspect.inspect('landcover','lc1',114.215,30.505);self.assertFalse(r['inside_aoi']);self.assertFalse(r['valid']);self.assertIsNone(r['class_code']);self.assertEqual(r['status'],'outside_aoi')
        r=self.inspect.inspect('landcover','lc1',114.205,30.515);self.assertTrue(r['inside_aoi']);self.assertTrue(r['valid']);self.assertEqual(r['class_code'],40);self.assertEqual(r['class_label'],'耕作地（Cropland）');self.assertEqual(r['source_year'],2021)
    def test_landcover_unknown_nodata_and_missing_boundary_are_not_classes(self):
        import rasterio,numpy as np
        self.store.landcover.task['result']['boundary']['features'][0]['geometry']['coordinates']=[[[114.2,30.52],[114.22,30.52],[114.22,30.5],[114.2,30.5],[114.2,30.52]]]
        r=self.inspect.inspect('landcover','lc1',114.205,30.505);self.assertTrue(r['inside_aoi']);self.assertFalse(r['valid']);self.assertIsNone(r['class_code'])
        with rasterio.open(self.root/'classification.tif','r+') as ds:ds.write(np.zeros((2,2),dtype='uint8'),1)
        self.assertFalse(self.inspect.inspect('landcover','lc1',114.205,30.515)['valid'])
        del self.store.landcover.task['result']['boundary']
        with self.assertRaises(GeoError) as e:self.inspect.inspect('landcover','lc1',114.205,30.515)
        self.assertEqual(e.exception.code,'INSPECTION_UNSUPPORTED')
    def test_file_and_task_record_are_not_modified(self):
        path=self.root/'analysis.tif';before=hashlib.sha256(path.read_bytes()).hexdigest();task=copy.deepcopy(self.store.production.task)
        self.inspect.inspect('production','p1',114.205,30.515);self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(),before);self.assertEqual(task,self.store.production.task)
    def test_projected_cell_uses_native_crs_and_change_band(self):
        import rasterio,numpy as np
        from rasterio.transform import from_origin
        from rasterio.warp import transform
        xs,ys=transform('EPSG:4326','EPSG:32650',[114.205],[30.515]);x,y=xs[0],ys[0]
        with rasterio.open(self.root/'analysis.tif','w',driver='GTiff',height=1,width=1,count=5,dtype='float32',crs='EPSG:32650',transform=from_origin(x-10,y+10,20,20),nodata=float('nan')) as ds:
            for i,(name,value) in enumerate([('after_ndvi',.7),('before_ndvi',.8),('delta_ndvi',-.1),('common_valid_mask',1),('aoi_mask',1)],1):ds.write(np.array([[value]],dtype='float32'),i);ds.set_band_description(i,name)
        for lon,lat in [(27,0),(-153,0)]:
            outside=self.inspect.inspect('production','p1',lon,lat);self.assertEqual(outside['status'],'outside_raster');self.assertIsNone(outside['value'])
        self.store.production.task['plan']['goal']='change';r=self.inspect.inspect('production','p1',114.205,30.515);self.assertEqual(r['crs'],'EPSG:32650');self.assertEqual(r['row'],0);self.assertEqual(r['column'],0);self.assertEqual(r['value_band'],'delta_ndvi');self.assertAlmostEqual(r['value'],-.1,places=6)

if __name__=='__main__':unittest.main()
