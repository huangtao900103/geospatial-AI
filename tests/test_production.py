import copy
import hashlib
import json
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch
from geo_services import GeoError, PublicSources
from production_services import (make_plan, ndvi_grid, compute_product, sample_bands, SentinelReader,
                                 SAMPLE_BBOX, capabilities, asset_url, raster_available)
from server import Workspace


def settled(service, task_id):
    for _ in range(300):
        result=service.get(task_id)
        if result['status'] in ('succeeded','failed','cancelled'):return result
        time.sleep(.01)
    raise AssertionError('task did not settle')


class PlanTest(unittest.TestCase):
    def test_cropland_area_never_routes_to_reference_ndvi(self):
        requests = [
            '仙桃市要统计全省农田面积，这个平台拿遥感数据来做，怎么给出最终分析结果？',
            '用遥感统计湖北省2025年耕地总面积',
            '仙桃市统计2025年农田面积',
            '统计高标准农田有多少亩',
        ]
        for requirement in requests:
            for extra in ({}, {'bbox':[113.30,30.30,113.31,30.31], 'goal':'ndvi'}):
                with self.subTest(requirement=requirement, extra=extra), self.assertRaises(GeoError) as raised:
                    make_plan({'requirement':requirement, 'mode':'sentinel_cog', **extra})
                self.assertEqual(raised.exception.code,'CROPLAND_AREA_REQUIRES_CLASSIFICATION')
                self.assertEqual(raised.exception.status,422)

    def test_full_region_demand_cannot_be_replaced_with_small_window(self):
        for requirement in ['计算湖北全省NDVI', '分析仙桃全市植被变化', '统计全县NDVI', '全域遥感分析']:
            with self.subTest(requirement=requirement), self.assertRaises(GeoError) as raised:
                make_plan({'requirement':requirement})
            self.assertEqual(raised.exception.code,'FULL_REGION_NOT_SUPPORTED')
        self.assertTrue(make_plan({'requirement':'比较仙桃市2025年10月和11月的NDVI下降'})['executable'])

    def test_dates_months_and_direction_understand_intent(self):
        p=make_plan({'requirement':'比较仙桃市2025年10月和11月的NDVI下降'})
        self.assertEqual(p['goal'],'change');self.assertEqual(p['direction'],'decrease')
        self.assertEqual(p['periods'],{'before':['2025-10-01','2025-10-31'],'after':['2025-11-01','2025-11-30']})
        p=make_plan({'requirement':'2025年10月NDVI分析'});self.assertEqual(p['goal'],'ndvi');self.assertEqual(p['periods']['after'],['2025-10-01','2025-10-31'])
        p=make_plan({'requirement':'2025年NDVI分析'});self.assertEqual(p['periods']['after'][0],'2025-09-01')
        p=make_plan({'requirement':'对比2025-10-01到2025-10-31与2025-11-01到2025-11-30的NDVI上升'})
        self.assertEqual(p['direction'],'increase');self.assertEqual(p['periods']['before'],['2025-10-01','2025-10-31'])
        self.assertEqual(p['periods']['after'],['2025-11-01','2025-11-30'])
        self.assertEqual(make_plan({'requirement':'比较2025和2026的NDVI'})['goal'],'change')
    def test_natural_threshold_comparison_and_cloud_are_not_confused(self):
        p=make_plan({'requirement':'筛选仙桃2025年10月NDVI低于0.1的区域，云量低于20%'})
        self.assertEqual(p['goal'],'ndvi');self.assertEqual(p['threshold'],.1);self.assertEqual(p['selection']['operator'],'lt');self.assertEqual(p['cloud'],20)
        p=make_plan({'requirement':'对比2025年10月和11月NDVI，筛选下降超过0.1的区域'})
        self.assertEqual(p['goal'],'change');self.assertEqual(p['direction'],'decrease');self.assertEqual(p['threshold'],.1);self.assertEqual(p['selection']['operator'],'gt')
        p=make_plan({'requirement':'2025年10月NDVI低于0.1','threshold':.3})
        self.assertEqual(p['threshold'],.3);self.assertEqual(p['selection']['threshold_source'],'request_body')
        p=make_plan({'requirement':'2025年10月NDVI，云量低于20%'})
        self.assertEqual(p['threshold'],.2);self.assertEqual(p['cloud'],20)
    def test_relative_percentage_is_blocked_not_absolute(self):
        p=make_plan({'requirement':'筛选两期NDVI下降10%的区域'})
        self.assertFalse(p['executable']);self.assertTrue(any('相对百分比' in g for g in p['blocking_gaps']))
    def test_modes_bounds_nonfinite_periods(self):
        for fields in [{'mode':'anything'},{'bbox':[0,0,1,1]},{'bbox':[False,0,.01,.01]}, {'bbox':[float('nan'),0,.01,.01]}, {'threshold':10**1000},{'cloud':True},{'cloud':101},{'periods':{'before':['2025-10-01','2025-11-15'],'after':['2025-11-01','2025-11-30']}}]:
            with self.subTest(fields=str(fields)[:80]),self.assertRaises(GeoError):make_plan({'requirement':'NDVI分析',**fields})
    def test_unknown_and_business_goals_do_not_become_fake_ndvi(self):
        for r in ['造火箭','黄鳝食品安全监管和禁捕确权','全部业务上图，农田工程进度和冷链','武汉市植被NDVI']:
            p=make_plan({'requirement':r});self.assertFalse(p['executable']);self.assertTrue(p['blocking_gaps'])
        p=make_plan({'requirement':'仙桃市植被NDVI'});self.assertTrue(p['executable']);self.assertIn('非全市',p['delivered_scope'])
    def test_sample_bound_and_source_dates_are_honest(self):
        p=make_plan({'requirement':'2022和2023年植被变化','bbox':[113.3,30.3,113.31,30.31]});self.assertFalse(p['executable'])
        bands=sample_bands();self.assertEqual(bands['metadata']['source_dates'],['sample_epoch_1','sample_epoch_2']);self.assertIn('人工',bands['metadata']['description'])
        self.assertEqual([m['id'] for m in capabilities()['modes']],['sentinel_cog']);self.assertEqual(capabilities()['default_sampling'],'native');self.assertIsNone(capabilities()['default_bbox'])


class RasterMathTest(unittest.TestCase):
    def test_real_grid_math_quality_not_attribute_or_zero_fill(self):
        bands={'red':[[.1,.1],[0,None]],'nir':[[.3,.1],[0,.5]],'scl':[[4,4],[4,4]]}
        grid,reasons=ndvi_grid(bands)
        self.assertAlmostEqual(grid[0][0],.5);self.assertEqual(grid[0][1],0)
        self.assertIsNone(grid[1][0]);self.assertIsNone(grid[1][1]);self.assertEqual(sum(reasons.values()),2)
        with self.assertRaises(GeoError):ndvi_grid({'red':[[.1]],'nir':[[.3,.3]],'scl':[[4]]})
    def test_direction_and_georeferenced_cells_recompute(self):
        p=make_plan({'requirement':'比较两期NDVI下降','threshold':.2})
        metadata={'source_type':'artificial_band_sample','provider':'test manual','input_sha256':'abc'}
        before={'red':[[.1,.3]],'nir':[[.3,.1]],'scl':[[4,4]]}
        after={'red':[[.1,.1]],'nir':[[.1,.3]],'scl':[[4,4]]}
        b={'before':before,'after':after,'metadata':metadata}
        result=compute_product(p,b,'prod-test');f=result['geojson']['features']
        self.assertEqual([x['properties']['delta_ndvi'] for x in f],[-.5,1.0])
        self.assertEqual([x['properties']['selected'] for x in f],[True,False]);self.assertEqual(result['summary']['changed_count'],1)
        self.assertEqual(f[0]['geometry']['coordinates'][0][0],SAMPLE_BBOX[:2]);self.assertEqual(f[-1]['geometry']['coordinates'][0][2],SAMPLE_BBOX[2:])
        p['direction']='increase';r=compute_product(p,b,'test');self.assertEqual([x['properties']['selected'] for x in r['geojson']['features']],[False,True])
        p['direction']='absolute';self.assertEqual(compute_product(p,b,'test')['summary']['changed_count'],2)
    def test_low_ndvi_comparison_really_selects_lower_values(self):
        p=make_plan({'requirement':'筛选2025年10月NDVI低于0.1的区域'})
        bands={'after':{'red':[[.1,.1]],'nir':[[.1,.3]],'scl':[[4,4]]},'metadata':{'source_type':'artificial_band_sample','provider':'test','input_sha256':'abc'}}
        result=compute_product(p,bands,'low-test');features=result['geojson']['features']
        self.assertEqual([f['properties']['selected'] for f in features],[True,False]);self.assertEqual(features[0]['properties']['category'],'below_threshold')
        self.assertEqual(result['summary']['selection']['operator'],'lt');self.assertEqual(result['summary']['selected_count'],1)
    def test_two_period_smaller_variation_is_not_reversed(self):
        p=make_plan({'requirement':'筛选两期NDVI变化幅度小于0.1的区域'})
        self.assertEqual(p['selection']['operator'],'lt');self.assertEqual(p['threshold'],.1)
        bands={'before':{'red':[[.1,.1]],'nir':[[.3,.3]],'scl':[[4,4]]},'after':{'red':[[.1,.1]],'nir':[[.3,.1]],'scl':[[4,4]]},'metadata':{'source_type':'artificial_band_sample','provider':'test','input_sha256':'abc'}}
        result=compute_product(p,bands,'low-change')
        self.assertEqual([f['properties']['selected'] for f in result['geojson']['features']],[True,False])
    def test_no_valid_pixels_fails_no_publish(self):
        p=make_plan({'requirement':'NDVI分析'});band={'red':[[.1]],'nir':[[.3]],'scl':[[9]]}
        with self.assertRaisesRegex(GeoError,'共同有效'):compute_product(p,{'after':band,'metadata':{'source_type':'artificial_band_sample'}},'test')
    def test_xml_calibration_and_unsigned_provenance(self):
        reader=SentinelReader(PublicSources());xml=b'<Root><BOA_QUANTIFICATION_VALUE>10000</BOA_QUANTIFICATION_VALUE><BOA_ADD_OFFSET band_id="3">-1000</BOA_ADD_OFFSET><BOA_ADD_OFFSET band_id="7">-1000</BOA_ADD_OFFSET></Root>'
        url='https://sentinel2l2a01.blob.core.windows.net/sentinel2-l2/example/MTD_MSIL2A.xml'
        reader.signed_url=lambda u,c:u+'?sig=DO_NOT_EXPORT';reader.fetch_bytes=lambda u,c,**k:xml
        c=reader.calibration(url,lambda:None)
        self.assertEqual(c['red']['scale'],.0001);self.assertEqual(c['nir']['offset'],-.1)
        self.assertEqual(c['provenance']['xml_sha256'],hashlib.sha256(xml).hexdigest());self.assertNotIn('sig=',json.dumps(c))
        reader.fetch_bytes=lambda u,c,**k:b'<Root/>'
        with self.assertRaises(GeoError):reader.calibration(url,lambda:None)
    def test_fixed_unsigned_assets(self):
        valid='https://sentinel2l2a01.blob.core.windows.net/sentinel2-l2/example/B04.tif';self.assertEqual(asset_url(valid),valid)
        for u in ['http://evil.test/x.tif',valid+'?sig=x','https://evil.test/sentinel2-l2/B04.tif','https://u:p@sentinel2l2a01.blob.core.windows.net/sentinel2-l2/a.tif']:
            with self.assertRaises(GeoError):asset_url(u)


class LifecycleTest(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.w=Workspace(Path(self.tmp.name)/'workspace.sqlite3')
    def tearDown(self):self.w.close();self.tmp.cleanup()
    def test_sample_runs_records_actual_io_and_survives_reload(self):
        task=self.w.production.create({'requirement':'比较仙桃市2025年10月和11月NDVI下降','mode':'sample_pixels'})
        done=settled(self.w.production,task['id']);self.assertEqual(done['status'],'succeeded',done.get('error'))
        self.assertEqual(done['result']['summary']['pixel_count'],64);self.assertEqual(done['result']['summary']['valid_count'],60)
        self.assertEqual(done['snapshot'],done['plan']);self.assertEqual(done['result']['provenance']['source_type'],'artificial_band_sample')
        self.assertTrue(all(n.get('started_at') and n.get('finished_at') and n.get('input_summary') and n.get('output_summary') for n in done['nodes']))
        self.assertEqual(done['result']['report']['steps'],done['nodes'])
        with self.assertRaises(GeoError):self.w.production.cancel(task['id'])
        self.assertEqual(self.w.production.get(task['id'])['status'],'succeeded')
    def test_no_public_fallback_on_upstream_failure(self):
        class Failing:
            def discover(self,p,c):raise GeoError('NO_SUITABLE_SCENE','no scene',422)
        self.w.production.reader=Failing()
        with patch('production_services.raster_available',return_value=True),patch('production_services.sample_bands') as sample:
            task=self.w.production.create({'requirement':'2025年10月NDVI','mode':'sentinel_cog'});done=settled(self.w.production,task['id'])
        self.assertEqual(done['status'],'failed');self.assertIsNone(done['result']);sample.assert_not_called()
    def test_cancel_retry_immutable_snapshot(self):
        entered=threading.Event()
        class Hold:
            def discover(self,p,check):
                entered.set()
                while True:check();time.sleep(.005)
        self.w.production.reader=Hold()
        with patch('production_services.raster_available',return_value=True):
            task=self.w.production.create({'requirement':'2025年10月NDVI','mode':'sentinel_cog'});self.assertTrue(entered.wait(1))
            cancelled=self.w.production.cancel(task['id']);self.assertEqual(cancelled['status'],'cancelled');time.sleep(.03)
            self.assertEqual(self.w.production.get(task['id'])['status'],'cancelled')
            again=self.w.production.retry(task['id']);self.assertNotEqual(again['id'],task['id']);self.assertEqual(again['plan']['aoi'],task['plan']['aoi']);self.w.production.cancel(again['id'])
    def test_dependency_gap_is_explicit(self):
        with patch('production_services.raster_available',return_value=False):
            task=self.w.production.create({'requirement':'NDVI分析','mode':'sentinel_cog'});done=settled(self.w.production,task['id'])
        self.assertEqual(done['status'],'failed');self.assertEqual(done['error']['code'],'BLOCKING_GAPS')
    def test_recovery_marks_unfinished_failed(self):
        plan=make_plan({'requirement':'NDVI分析'})
        record={'id':'production-interrupted','name':'test','status':'running','plan':plan,'snapshot':plan,'logs':[],'nodes':[{'id':'read','status':'running'}],'result':None,'created_at':'2025-01-01'}
        self.w.save('production_task',record)
        from production_services import ProductionServices
        recovered=ProductionServices(self.w).get(record['id']);self.assertEqual(recovered['status'],'failed');self.assertEqual(recovered['error']['code'],'INTERRUPTED');self.assertEqual(recovered['nodes'][0]['status'],'failed');self.assertTrue(recovered['nodes'][0]['finished_at'])


@unittest.skipUnless(raster_available(),'rasterio optional; test with project .venv for real raster window')
class RasterWindowTest(unittest.TestCase):
    def test_actual_local_tiff_windows_scale_offset_and_same_grid(self):
        import numpy as np
        import rasterio
        from rasterio.transform import from_bounds
        original_open=rasterio.open
        with tempfile.TemporaryDirectory() as td:
            bbox=[113.3,30.3,113.31,30.31];paths={}
            for band,value in [('red',2000),('nir',6000),('scl',4)]:
                path=Path(td)/(band+'.tif');paths[band]=path
                with original_open(path,'w',driver='GTiff',height=32,width=32,count=1,dtype='uint16',crs='EPSG:4326',transform=from_bounds(*bbox,32,32),nodata=0) as ds:ds.write(np.full((32,32),value,dtype='uint16'),1)
            reader=SentinelReader(PublicSources());reader.signed_url=lambda href,check:href
            assets={band:{'href':'https://sentinel2l2a01.blob.core.windows.net/sentinel2-l2/test/'+band+'.tif','scale':.0001 if band!='scl' else 1,'offset':-.1 if band!='scl' else 0,'nodata':0} for band in paths}
            p=make_plan({'requirement':'2025年10月NDVI','mode':'sentinel_cog','bbox':bbox})
            def open_file(href,*a,**kw):return original_open(paths[href.rsplit('/',1)[-1].split('.')[0]],*a,**kw)
            with patch('rasterio.open',side_effect=open_file):bands=reader.read(p,{'after':{'id':'local-test','assets':assets}},lambda:None)
            result=compute_product(p,bands,'local-test');self.assertEqual(result['summary']['valid_count'],256);self.assertAlmostEqual(result['summary']['mean_ndvi'],2/3,places=6)
            self.assertEqual(result['provenance']['grid']['bounds'],bbox);self.assertNotIn('sig=',json.dumps(result))


if __name__=='__main__':unittest.main()
