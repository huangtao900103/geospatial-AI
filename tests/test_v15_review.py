"""Review regressions: declared capability scope, product pinning, Inspector and exports."""
import hashlib
import io
import json
import tempfile
import threading
import unittest
import zipfile
from pathlib import Path
from urllib.request import urlopen

from geo_services import GeoError
from server import Workspace, LocalServer, make_handler


class ReviewIntegrationTest(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        self.w=Workspace(self.root/'workspace.sqlite3')
    def tearDown(self):
        self.w.close();self.temp.cleanup()
    def test_product_is_explicit_and_templates_freeze_matching_year_and_area_method(self):
        context={'bbox':[113.3,30.3,113.31,30.31]}
        self.assertEqual(self.w.applications.get('cropland_delineation')['defaults']['parameters']['product'],'io_lulc_2024')
        with self.assertRaises(GeoError) as missing:self.w.applications.prepare({'application_id':'cropland_delineation','context':context})
        self.assertEqual(missing.exception.code,'APPLICATION_PRODUCT_REQUIRED')
        payload,ref=self.w.applications.prepare({'application_id':'cropland_delineation','context':context,'parameters':{'product':'io_lulc_2024'}})
        self.assertEqual(ref['parameters']['product'],'io_lulc_2024');self.assertEqual(ref['method']['source_year'],2024)
        self.assertEqual(payload['context']['cropland_product'],'io_lulc_2024');self.assertIn('2024',payload['question'])
        plan=self.w.executions.prepare(payload['question'],payload['context']);self.assertTrue(plan['executable'],plan);self.assertEqual(plan['tool']['inputs']['product'],'io_lulc_2024')
        old,ref=self.w.applications.prepare({'application_id':'cropland_delineation','context':context,'parameters':{'product':'worldcover_2021_v200'}})
        self.assertEqual(ref['method']['source_year'],2021);self.assertIn('2021',old['question'])
        conflict=self.w.executions.prepare(old['question'],{**context,'cropland_product':'io_lulc_2024'});self.assertFalse(conflict['executable']);self.assertEqual(conflict['status'],'unsupported')
        current=self.w.executions.prepare('标注今年选区内的全部农田',context);self.assertFalse(current['executable'])
        for question in ('武汉市有多少亩耕作地？','武汉市农田有多少亩？'):
            ambiguous=self.w.executions.prepare(question,{**context,'cropland_product':'io_lulc_2024'})
            self.assertEqual(ambiguous['status'],'needs_data');self.assertEqual(ambiguous['missing_fields'],['scope_confirmation'])
        law=self.w.executions.prepare('武汉市官方耕地保有量有多少亩？',{**context,'cropland_product':'io_lulc_2024'});self.assertFalse(law['executable']);self.assertEqual(law['status'],'unsupported')
        for question,selected in (('这个区域有多少亩耕作地？',{'cropland_product':'io_lulc_2024'}),('所选区域农田分布在哪里？',{})):
            missing=self.w.executions.prepare(question,selected);self.assertEqual(missing['status'],'needs_data');self.assertEqual(missing['missing_fields'],['bbox'])
    def test_card_scope_and_business_preparation_never_claim_department_systems_connected(self):
        catalog=self.w.applications.catalog();cards={a['id']:a for a in catalog['applications']}
        self.assertEqual(cards['imagery_search']['capability_kind'],'catalog_only')
        self.assertEqual(cards['land_resources']['capability_kind'],'fixed_historical_region')
        self.assertEqual(cards['cropland_delineation']['capability_kind'],'historical_product')
        self.assertEqual(set(cards['cropland_delineation']['method']['product_profiles']),{'worldcover_2021_v200','io_lulc_2024'})
        scenarios=catalog['business_scenarios'];self.assertEqual(scenarios['deployment_scope'],'local_workspace')
        self.assertEqual(len(scenarios['items']),6)
        for item in scenarios['items']:
            self.assertEqual(item['business_integration_status'],'awaiting_authorized_data');self.assertTrue(item['required_data'])
    def test_io_lineage_names_the_real_product_and_rejects_other_year(self):
        p={'product':{'id':'io_lulc_2024','year':2024},'year':2024,'selected_bbox':[113.3,30.3,113.31,30.31],
           'operation':'published_classification_extraction','classified_window_sha256':'a'*64,'selection_sha256':'b'*64,'area_method':'native_utm_projected_grid'}
        self.w.save('cropland_task',{'id':'test_io_lineage','status':'succeeded','result':{'provenance':p},'created_at':'2026-11-14'})
        ref={'task_type':'cropland','task_id':'test_io_lineage'}
        plan=self.w.data_catalog.create_plan({'question':'标注选区2024年耕作地图斑','context':ref})
        self.assertEqual(plan['used'][0]['source_id'],'io_lulc_2024');self.assertEqual(plan['execution_evidence']['source_year'],2024)
        other=self.w.data_catalog.create_plan({'question':'标注选区2021年耕作地图斑','context':ref});self.assertFalse(other['used']);self.assertIsNotNone(other['related_existing_result'])
    def test_inspector_reads_io_mask_and_marks_cloud_as_unknown(self):
        import numpy as np
        import rasterio
        from rasterio.transform import from_origin
        from rasterio.warp import transform_bounds
        folder=self.w.cropland.output_dir/'test_io_inspect';folder.mkdir(parents=True)
        transform=from_origin(500000,3400000,10,10)
        data=np.array([[5,10],[0,2]],dtype='uint8');mask=np.array([[1,1],[1,0]],dtype='uint8')
        with rasterio.open(folder/'classification.tif','w',driver='GTiff',width=2,height=2,count=2,dtype='uint8',crs='EPSG:32649',nodata=0,transform=transform) as ds:
            ds.write(data,1);ds.write(mask,2);ds.set_band_description(1,'classification');ds.set_band_description(2,'aoi_mask')
            bbox=list(transform_bounds(ds.crs,'EPSG:4326',*ds.bounds))
        w,s,e,n=bbox;boundary={'type':'FeatureCollection','features':[{'type':'Feature','properties':{},'geometry':{'type':'Polygon','coordinates':[[[w,s],[e,s],[e,n],[w,n],[w,s]]]}}]}
        self.w.save('cropland_task',{'id':'test_io_inspect','created_at':'2026-11-14','status':'succeeded','plan':{},'result':{'boundary':boundary,'summary':{'reference_year':2024,'nominal_resolution_m':10},'classes':[{'code':5,'name':'作物覆盖'},{'code':2,'name':'树木覆盖'}],'provenance':{'product':{'id':'io_lulc_2024'},'year':2024,'area_method':'native_utm_projected_grid'}}})
        from rasterio.warp import transform as project
        def inspect(row,col):
            x,y=rasterio.transform.xy(transform,row,col);lon,lat=project('EPSG:32649','EPSG:4326',[x],[y]);return self.w.inspection_services.inspect('cropland','test_io_inspect',lon[0],lat[0])
        crop=inspect(0,0);self.assertTrue(crop['valid']);self.assertEqual(crop['class_code'],5);self.assertEqual(crop['source_year'],2024)
        cloud=inspect(0,1);self.assertTrue(cloud['inside_aoi']);self.assertFalse(cloud['valid']);self.assertEqual(cloud['quality_reason'],'cloud_unknown')
        missing=inspect(1,0);self.assertFalse(missing['valid']);self.assertEqual(missing['quality_reason'],'nodata_unknown')
        outside=inspect(1,1);self.assertFalse(outside['inside_aoi']);self.assertFalse(outside['valid'])
    def test_bundle_http_has_verified_gis_and_readable_report_without_private_configuration(self):
        data=self.w.create_dataset({'name':'显式测试点','geojson':{'type':'FeatureCollection','features':[{'type':'Feature','properties':{'name':'test fixture'},'geometry':{'type':'Point','coordinates':[113.,30.]}}]}})
        result=self.w.create_analysis({'dataset_id':data['id'],'algorithm_id':'spatial_summary','parameters':{}})
        server=LocalServer(('127.0.0.1',0),make_handler(self.w));server.quiet=True
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        try:
            base='http://127.0.0.1:'+str(server.server_port)
            with urlopen(base+'/api/results/analysis/'+result['id']+'/manifest') as r:manifest=json.load(r)
            with urlopen(base+'/api/results/analysis/'+result['id']+'/bundle.zip') as r:
                raw=r.read();self.assertIn(result['id'],r.headers['Content-Disposition'])
            with zipfile.ZipFile(io.BytesIO(raw)) as z:
                self.assertIsNone(z.testzip());self.assertIn('README.txt',z.namelist());self.assertIn('inputs.json',z.namelist());self.assertIn('provenance.json',z.namelist())
                for entry in manifest['entries']:self.assertEqual(hashlib.sha256(z.read(entry['path'])).hexdigest(),entry['sha256'])
                self.assertFalse(any('tianditu.json' in name or 'sqlite' in name for name in z.namelist()))
            with urlopen(base+'/api/business-scenarios') as r:self.assertEqual(len(json.load(r)['items']),6)
        finally:server.shutdown();server.server_close();thread.join(2)


if __name__=='__main__':unittest.main()
