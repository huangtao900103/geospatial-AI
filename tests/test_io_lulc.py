"""IO2024 contracts and real native raster processing of labelled test fixtures."""
import copy
import json
import math
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch
from backend.core.geo_services import GeoError
from backend.analysis.cropland_services import CroplandServices, make_plan, product_profile, capabilities
from backend.analysis.io_lulc_reader import (IOLulcReader, validate_item, vectorize_crops, make_preview,
                            PRODUCT, AREA_METHOD, COLLECTION, DATA_HOST)
from backend.analysis.landcover_services import available
from test_cropland import MemoryStore


def item_payload():
    return {'features':[{'id':'49R-2024','collection':COLLECTION,
          'properties':{'datetime':'2024-01-01T00:00:00Z','start_datetime':'2024-01-01T00:00:00Z','end_datetime':'2024-12-31T23:59:59.995Z'},
          'assets':{'supercell':{'href':'https://'+DATA_HOST+'/49R_2024.tif','proj:epsg':32649,
                    'classification:classes':[{'value':5,'name':'crops'},{'value':10,'name':'clouds'}]}}}],
          'links':[]}


class ContractTest(unittest.TestCase):
    def test_explicit_product_and_year_are_frozen_and_default_remains_2021(self):
        body={'bbox':[113.3,30.3,113.31,30.31]}
        self.assertEqual(make_plan(body)['reference_year'],2021)
        p=make_plan({**body,'product':'io_lulc_2024','requirement':'提取选区2024年农田图斑'})
        self.assertEqual(p['reference_year'],2024);self.assertEqual(p['class_code'],5)
        self.assertEqual(p['area_method'],AREA_METHOD);self.assertIn('投影',p['method'])
        self.assertEqual({p['id'] for p in capabilities()['products']},{'worldcover_2021_v200','io_lulc_2024'})
        profile=product_profile('io_lulc_2024');profile['year']=2099
        self.assertEqual(product_profile('io_lulc_2024')['year'],2024)
        for requirement in ['标注2021年农田','标注2026年农田','最新农田','2024年5月农田','2024年高标准农田','今年农田']:
            with self.subTest(requirement=requirement),self.assertRaises(GeoError):
                make_plan({**body,'product':'io_lulc_2024','requirement':requirement})

    def test_annual_products_reject_specific_dates_and_seasons_without_context_fallback(self):
        for product,year in [('io_lulc_2024',2024),('worldcover_2021_v200',2021)]:
            for pattern in [
                '标注{y}-11-01至{y}-11-30选区农田',
                '标注{y}/11/01至{y}/11/30选区农田',
                '标注{y}.11.01选区农田',
                '标注{y}-11选区农田',
                '标注{y}年11月份选区农田',
                '标注{y}年十一月选区农田',
                '标注{y}年第四季度选区农田',
                '标注{y}年Q4选区农田',
                '标注{y}年秋冬播农田',
                '标注{y}年夏季农田',
                '标注{y}春农田',
                '标注{y}年度春耕农田',
                '标注{y}年播种期农田',
                '标注{y}年summer农田',
                '标注2026年选区农田',
            ]:
                requirement=pattern.format(y=year)
                with self.subTest(product=product,requirement=requirement),self.assertRaises(GeoError) as e:
                    make_plan({'bbox':[113.3,30.3,113.31,30.31],'product':product,'requirement':requirement})
                self.assertEqual(e.exception.code,'YEAR_MISMATCH')
            for requirement in [f'标注{year}年度选区农田',f'将当前范围内{year}年农田全部标注出来']:
                with self.subTest(product=product,requirement=requirement):
                    p=make_plan({'bbox':[113.3,30.3,113.31,30.31],'product':product,'requirement':requirement})
                    self.assertEqual(p['reference_year'],year)

    def test_fixed_official_item_and_epoch_validated(self):
        b=validate_item(item_payload());self.assertEqual(b['item_id'],'49R-2024');self.assertEqual(b['epsg'],32649)
        changes=[('href','https://evil.test/49R_2024.tif'),('href','https://'+DATA_HOST+'/49R_2024.tif?sig=private'),
                 ('href','http://'+DATA_HOST+'/49R_2024.tif'),('proj:epsg',4326)]
        for key,value in changes:
            p=item_payload();p['features'][0]['assets']['supercell'][key]=value
            with self.subTest(key=key,value=value),self.assertRaises(GeoError):validate_item(p)
        p=item_payload();p['features'][0]['properties']['datetime']='2023-01-01T00:00:00Z'
        with self.assertRaises(GeoError):validate_item(p)
        p=item_payload();p['features'][0]['assets']['supercell']['classification:classes'][0]['value']=40
        with self.assertRaises(GeoError):validate_item(p)

    def test_no_or_multiple_tiles_and_pagination_do_not_fake_single_coverage(self):
        for payload in [{'features':[]}, {'features':item_payload()['features']*2}, {**item_payload(),'links':[{'rel':'next'}]}]:
            with self.subTest(payload=payload),self.assertRaises(GeoError):validate_item(payload)


@unittest.skipUnless(available(),'native raster runtime required')
class NativeIOTest(unittest.TestCase):
    def setUp(self):
        import numpy as np
        import rasterio
        from rasterio.transform import from_origin
        from rasterio.warp import transform
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name);self.source=self.root/'test-only-io.tif'
        x,y=transform('EPSG:4326','EPSG:32649',[113.3],[30.3]);self.transform=from_origin(math.floor(x[0]/10)*10-250,math.ceil(y[0]/10)*10+250,10,10)
        self.data=np.full((50,50),5,dtype='uint8');self.data[25,25]=10;self.data[24,24]=0;self.data[24,25]=2
        self.write_source(self.data);self.bbox=[113.2998,30.2998,113.3002,30.3002]
        self.reader=IOLulcReader(catalog_fetch=lambda bbox:item_payload(),source_path=str(self.source))
    def tearDown(self):self.temp.cleanup()
    def write_source(self,data,transform=None,crs='EPSG:32649'):
        import rasterio
        with rasterio.open(self.source,'w',driver='GTiff',width=data.shape[1],height=data.shape[0],count=1,dtype='uint8',crs=crs,transform=transform or self.transform,nodata=0) as ds:ds.write(data,1)
    def account(self,folder='out'):
        return self.reader.account(self.bbox,{},self.root/folder)

    def test_native_center_mask_matches_independent_inverse_coordinates_and_cloud_is_unknown(self):
        import numpy as np
        import rasterio
        from rasterio.warp import transform
        r=self.account();s=r['summary']
        with rasterio.open(r['native_path']) as ds:
            classes=ds.read(1);aoi=ds.read(2)==1;rows,cols=np.indices(aoi.shape)
            xx=ds.transform.c+(cols.ravel()+.5)*10;yy=ds.transform.f-(rows.ravel()+.5)*10
            lon,lat=transform(ds.crs,'EPSG:4326',xx.tolist(),yy.tolist());w,so,e,n=self.bbox
            expected=np.array([(w<=x<=e and so<=y<=n) for x,y in zip(lon,lat)]).reshape(aoi.shape)
            self.assertTrue(np.array_equal(aoi,expected));self.assertEqual(ds.descriptions,('classification','aoi_mask'))
            self.assertEqual(ds.crs.to_epsg(),32649);self.assertEqual(ds.transform.a,10)
            count=int(((classes==5)&aoi).sum());cloud=int(((classes==10)&aoi).sum());unknown=int((aoi&np.isin(classes,[0,10])).sum())
            self.assertGreater(cloud,0);self.assertGreater(unknown,cloud)
            self.assertEqual(s['cropland_pixel_count'],count);self.assertEqual(s['cloud_pixel_count'],cloud)
            self.assertEqual(s['nodata_pixel_count'],unknown);self.assertEqual(s['inside_pixel_count'],s['valid_pixel_count']+unknown)
            self.assertAlmostEqual(s['cropland_ha'],count*.01);self.assertEqual(s['area_method'],AREA_METHOD)
            self.assertLess(s['coverage_ratio'],1);self.assertEqual(s['reference_year'],2024)

    def test_single_tile_must_cover_actual_projected_aoi(self):
        import numpy as np
        self.write_source(np.full((2,2),5,dtype='uint8'))
        with self.assertRaises(GeoError) as e:self.account()
        self.assertEqual(e.exception.code,'SOURCE_COVERAGE_GAP')

    def test_grid_declarations_cannot_replace_real_tiff_crs_and_resolution(self):
        from rasterio.transform import from_origin
        for transform,crs in [(from_origin(self.transform.c,self.transform.f,20,20),'EPSG:32649'),(self.transform,'EPSG:32650')]:
            self.write_source(self.data,transform,crs)
            with self.subTest(crs=crs),self.assertRaises(GeoError) as e:self.account()
            self.assertEqual(e.exception.code,'IO_GRID_MISMATCH')

    def make_vector_source(self,values=None):
        import numpy as np
        import rasterio
        path=self.root/'vector-test.tif'
        values=np.asarray(values if values is not None else [[5,5,5,2,5],[5,1,5,2,2],[5,5,5,2,2],[2,2,2,5,2],[0,0,2,2,2]],dtype='uint8')
        with rasterio.open(path,'w',driver='GTiff',width=5,height=5,count=2,dtype='uint8',crs='EPSG:32649',transform=self.transform,nodata=0) as ds:
            ds.write(values,1);ds.write(np.ones_like(values),2);ds.set_band_description(1,'classification');ds.set_band_description(2,'aoi_mask')
        return path

    def test_four_connected_patches_holes_area_and_geojson_roundtrip(self):
        import numpy as np
        import rasterio
        from rasterio.features import rasterize
        from rasterio.warp import transform_geom
        path=self.make_vector_source();v=vectorize_crops(path,self.root/'vectors')
        self.assertEqual(v['patch_count'],3);self.assertEqual(v['pixel_count'],10);self.assertEqual(v['area_m2'],1000)
        ring=next(f for f in v['geojson']['features'] if f['properties']['pixel_count']==8)
        self.assertEqual(len(ring['geometry']['coordinates']),2)
        with rasterio.open(path) as ds:
            burnt=rasterize([(transform_geom('EPSG:4326',ds.crs,f['geometry']),1) for f in v['geojson']['features']],out_shape=(5,5),transform=ds.transform,dtype='uint8')
            self.assertTrue(np.array_equal(burnt==1,ds.read(1)==5))
        self.assertTrue(all(f['properties']['class_code']==5 and f['properties']['reference_year']==2024 and f['properties']['area_method']==AREA_METHOD for f in v['geojson']['features']))
        preview=make_preview(path,self.root/'vectors');self.assertEqual(preview['display_crs'],'EPSG:3857')

    def test_zero_crops_and_complexity_cancel_limits(self):
        import numpy as np
        path=self.make_vector_source(np.full((5,5),2,dtype='uint8'));v=vectorize_crops(path,self.root/'zero')
        self.assertEqual(v['patch_count'],0);self.assertEqual(v['area_m2'],0)
        path=self.make_vector_source()
        with self.assertRaises(GeoError) as e:vectorize_crops(path,self.root/'limit',max_patches=1)
        self.assertEqual(e.exception.code,'PATCH_LIMIT');self.assertFalse((self.root/'limit'/'cropland.geojson').exists())
        def cancelled():raise GeoError('CANCELLED','test cancellation')
        with self.assertRaises(GeoError):vectorize_crops(path,self.root/'cancel',check=cancelled)
        self.assertFalse((self.root/'cancel'/'cropland.geojson').exists())

    def test_service_dispatches_explicit_io_product_without_touching_worldcover(self):
        store=MemoryStore(self.root);service=CroplandServices(store,io_reader=self.reader)
        try:
            with patch('backend.analysis.cropland_services.account_native',side_effect=AssertionError('must not read WorldCover')):
                t=service.create({'bbox':self.bbox,'product':'io_lulc_2024'})
                for _ in range(300):
                    t=service.get(t['id'])
                    if t['status'] not in ('queued','running'):break
                    time.sleep(.01)
            self.assertEqual(t['status'],'succeeded',t);r=t['result']
            self.assertEqual(r['provenance']['year'],2024);self.assertEqual(r['provenance']['class_code'],5)
            self.assertEqual(r['provenance']['source_id'],'io_lulc_2024');self.assertEqual(r['summary']['area_method'],AREA_METHOD)
            self.assertIn('2024',r['report']['title']);self.assertFalse(r['provenance']['statistics_resampled'])
            for artifact in r['artifacts']:self.assertTrue(service.artifact(t['id'],artifact['url'].split('/')[-1]).is_file())
        finally:service.close()

if __name__=='__main__':unittest.main()
