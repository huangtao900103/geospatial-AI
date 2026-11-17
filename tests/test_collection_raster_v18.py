"""Fixed temporal reducers, frozen selection and local native evidence tests.

All tiny local arrays/scenes in these tests are explicitly synthetic fixtures;
they are never installed in the business workspace.
"""
import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np
import rasterio
from affine import Affine

from backend.analysis.collection_raster import make_plan, discover_collection, reduce_stack, run_collection, _hash, _validate_manifest, VERSION, FILES
from backend.core.geo_services import GeoError


class CollectionPlanTest(unittest.TestCase):
    def body(self, **extra):
        return {'algorithm_id': 'temporal_median', 'bbox': [114.2, 30.5, 114.21, 30.51], 'start': '2025-10-01', 'end': '2025-11-30', **extra}

    def test_plan_is_bounded_fixed_and_sensor_compatible(self):
        p = make_plan(self.body());self.assertTrue(p['allow_sparse_quality']);self.assertEqual(p['minimum_observations'], 2)
        self.assertEqual(make_plan(self.body(algorithm_id='temporal_trend'))['minimum_observations'], 3)
        self.assertEqual(make_plan(self.body(source_id='pc_landsat_c2_l2', index_id='savi'))['index']['reference_resolution_m'], 30)
        for extra in ({'bbox': [114,30,115,31]}, {'cloud': True}, {'scene_limit': 7}, {'scene_limit': 2.1}, {'algorithm_id': 'arbitrary_code'}, {'start': '2025-02-30'}, {'end': '2026-11-30'}, {'collection': {}}, {'source_id': 'external_url'}, {'source_id': 'pc_landsat_c2_l2', 'index_id': 'ndre'}, {'algorithm_id': 'temporal_trend', 'scene_limit': 2}, {'index_id': []}, {'requirement':'合成全省NDVI'}, {'requirement':'合成后认定作物产量'}):
            with self.subTest(extra=extra), self.assertRaises(GeoError):make_plan(self.body(**extra))

    def fixture_reader(self, days, paginate=False):
        class Reader:
            calls = []
            def fetch_bytes(reader, url, check, max_bytes):
                reader.calls.append(url)
                fs=[]
                for day,cloud,id in days:
                    fs.append({'type':'Feature','id':id,'collection':'sentinel-2-l2a','bbox':[114,30,115,31], 'properties':{'datetime':'2025-11-'+day+'T02:30:00Z','eo:cloud_cover':cloud,'platform':'sentinel-2b'}})
                return json.dumps({'features':fs,'links':[{'rel':'next','href':'https://not-authorized.example/search'}] if paginate else []}).encode()
            def discover(reader, plan, check):
                day=plan['periods']['after'][0]
                return {'after': {'id':plan['scene_id'],'datetime':day+'T02:30:00Z','cloud_cover':1,'assets':{}}}
        return Reader()

    def test_daily_dedup_and_temporally_spaced_dates_are_frozen(self):
        days=[('01',2,'S2B_DAY01'),('01',1,'S2A_BETTER_DAY01'),('03',0,'S2B_DAY03'),('07',1,'S2B_DAY07'),('15',1,'S2B_DAY15'),('30',1,'S2B_DAY30')]
        p=make_plan(self.body(scene_limit=3));m=discover_collection(p,self.fixture_reader(days))
        self.assertEqual([s['id'] for s in m['selected_scenes']], ['S2A_BETTER_DAY01','S2B_DAY07','S2B_DAY30'])
        self.assertEqual(m['eligible_unique_date_count'],5);self.assertEqual(m['candidate_scene_count'],6);self.assertEqual(_validate_manifest(p,m),m['selected_scenes'])
        bad=copy.deepcopy(m);bad['selected_scenes'][0]['id']='changed'
        with self.assertRaises(GeoError):_validate_manifest(p,bad)
        with self.assertRaises(GeoError):_validate_manifest(make_plan(self.body(index_id='evi2')),m)
        changed=copy.deepcopy(p);changed['bbox'][0]=114.201
        with self.assertRaises(GeoError):_validate_manifest(changed,m)

    def test_too_few_dates_and_untrusted_pagination_do_not_silently_truncate(self):
        with self.assertRaises(GeoError):discover_collection(make_plan(self.body(algorithm_id='temporal_trend')),self.fixture_reader([('01',1,'S2_A'),('01',2,'S2_B')]))
        with self.assertRaises(GeoError) as error:discover_collection(make_plan(self.body()),self.fixture_reader([('01',1,'S2_A'),('10',1,'S2_B')],paginate=True))
        self.assertEqual(error.exception.code,'COLLECTION_CATALOG_DENIED')


class TemporalReducerTest(unittest.TestCase):
    def test_median_and_mean_use_valid_observations_and_at_least_two_dates(self):
        stack=np.array([[[0,.2],[np.nan,.5]],[[.5,.6],[.5,np.nan]],[[2,.4],[1,np.nan]]],dtype='float32');inside=np.ones((2,2),dtype='bool')
        for algorithm in ('temporal_median','temporal_mean'):
            arrays=reduce_stack(stack,[0,5,20],inside,algorithm)
            for row in range(2):
                for col in range(2):
                    values=[float(v) for v in stack[:,row,col] if np.isfinite(v)]
                    if len(values)<2:self.assertTrue(np.isnan(arrays['value'][row,col]))
                    else:self.assertAlmostEqual(float(arrays['value'][row,col]),float(np.median(values) if algorithm=='temporal_median' else sum(values)/len(values)),places=6)
            self.assertEqual(arrays['valid_observation_count'].tolist(),[[3,3],[2,1]])
        zeros=reduce_stack(np.zeros((2,1,1)),[0,1],np.ones((1,1),bool),'temporal_median');self.assertEqual(float(zeros['value'][0,0]),0)

    def test_ols_uses_actual_irregular_days_and_exports_null_for_low_support(self):
        times=np.array([0.,5.,20.]);stack=np.array([[[0.,.1],[.5,np.nan]],[[.5,.2],[.5,.2]],[[2.,.3],[.5,.3]]])
        arrays=reduce_stack(stack,times,np.ones((2,2),bool),'temporal_trend')
        for row,col in ((0,0),(0,1),(1,0)):
            slope,intercept=np.polyfit(times,stack[:,row,col],1)
            self.assertAlmostEqual(float(arrays['value'][row,col]),slope,places=7);self.assertAlmostEqual(float(arrays['intercept_at_time_origin'][row,col]),intercept,places=7)
        self.assertTrue(np.isnan(arrays['value'][1,1]));self.assertEqual(arrays['valid_observation_count'][1,1],2)
        self.assertTrue(np.isnan(arrays['r_squared'][1,0]));self.assertAlmostEqual(float(arrays['r_squared'][0,0]),1)
        self.assertNotAlmostEqual(float(arrays['value'][0,1]),.1,places=4)

    def test_empty_observations_and_outside_pixels_are_not_zero(self):
        inside=np.array([[True,False]])
        arrays=reduce_stack(np.full((3,1,2),np.nan),[0,1,2],inside,'temporal_trend')
        self.assertTrue(np.isnan(arrays['value']).all());self.assertEqual(arrays['valid_mask'][0,0],0);self.assertTrue(np.isnan(arrays['valid_mask'][0,1]));self.assertEqual(arrays['valid_observation_count'][0,0],0)
        for times in ([0,0,2],[0,np.nan,2],[2,1,0]):
            with self.assertRaises(GeoError):reduce_stack(np.zeros((3,1,2)),times,inside,'temporal_trend')


class CollectionExportTest(unittest.TestCase):
    def test_all_selected_scenes_including_zero_valid_are_exported_and_reproducible(self):
        p=make_plan({'algorithm_id':'temporal_mean','index_id':'ndvi','bbox':[114.2,30.5,114.21,30.51],'start':'2025-11-01','end':'2025-11-30','scene_limit':3})
        scenes=[{'id':'S2_OFFLINE_%d'%i,'datetime':'2025-11-%02dT02:30:00Z'%(i*10+1),'cloud_cover':0,'assets':{}} for i in range(3)]
        m={'version':VERSION,'plan_fingerprint':p['plan_fingerprint'],'source_id':p['source_id'],'index_id':'ndvi','selected_scenes':scenes};m['selection_sha256']=_hash(m);p['collection']=m
        bands={'_mask':np.ones((2,2),bool),'metadata':{'crs':'EPSG:32650','transform':list(Affine(10,0,231000,0,-10,3377000))[:6],'cell_area_m2':100.,'input_sha256':'a'*64,'provider':'explicit offline fixture'}}
        for key,nir,scl in [('scene_00',.5,4),('scene_01',.7,4),('after',.8,9)]:bands[key]={'nir':np.full((2,2),nir,dtype='float32'),'red':np.full((2,2),.1,dtype='float32'),'scl':np.full((2,2),scl,dtype='float32')}
        with tempfile.TemporaryDirectory() as temp,patch('backend.analysis.native_raster.read_native',return_value=bands):
            folder=Path(temp)/'processing_test';r=run_collection(p,object(),folder)
            self.assertEqual(set(x.name for x in folder.iterdir()),set(FILES));self.assertEqual(r['summary']['zero_valid_scene_count'],1);self.assertEqual(len(r['series']),3);self.assertIsNone(r['series'][2]['mean_index_all_valid']);self.assertEqual(r['summary']['common_support_pixel_count'],0)
            with rasterio.open(folder/'analysis.tif') as ds:
                a=[ds.read(ds.descriptions.index('scene_%02d_index'%i)+1) for i in range(3)];expected=(a[0].astype('float64')+a[1].astype('float64'))/2
                np.testing.assert_allclose(ds.read(1),expected,atol=1e-7);self.assertTrue(np.isnan(a[2]).all());self.assertEqual(ds.crs.to_epsg(),32650)
            loaded=json.loads((folder/'result.json').read_text());self.assertEqual(loaded['summary']['valid_count'],4)
            self.assertFalse(r['provenance']['external_engine_called']);self.assertEqual(r['image']['statistics_from_preview'],False)
            # Successful reads with no adequate observation support are unknown,
            # not fabricated zero means or an opaque empty-looking preview.
            for key in ('scene_00','scene_01','after'):bands[key]['scl'][:]=9
            empty=run_collection(p,object(),Path(temp)/'processing_no_support')
            self.assertIsNone(empty['summary']['mean_index']);self.assertEqual(empty['summary']['valid_count'],0)
            self.assertEqual(empty['summary']['zero_valid_scene_count'],3);self.assertTrue(empty['summary']['quality_warning'])
            from PIL import Image
            self.assertEqual(int(np.asarray(Image.open(Path(temp)/'processing_no_support/overlay.png'))[:,:,3].sum()),0)

    def test_zero_quality_scene_is_read_without_aborting_native_collection(self):
        from backend.analysis.native_raster import read_native
        from rasterio.transform import array_bounds
        from rasterio.warp import transform_bounds
        transform=Affine(10,0,720000,0,-10,3360000);bbox=list(transform_bounds('EPSG:32649','EPSG:4326',*array_bounds(4,4,transform)))
        p=make_plan({'algorithm_id':'temporal_mean','bbox':bbox,'start':'2025-11-01','end':'2025-11-30'})
        with tempfile.TemporaryDirectory() as temp:
            assets={}
            for name,value in [('nir',5000),('red',1000),('scl',9)]:
                path=Path(temp)/(name+'.tif')
                with rasterio.open(path,'w',driver='GTiff',width=4,height=4,count=1,dtype='uint16',crs='EPSG:32649',transform=transform,nodata=0) as ds:ds.write(np.full((4,4),value,dtype='uint16'),1)
                assets[name]={'href':str(path),'scale':1 if name=='scl' else .0001,'offset':0,'nodata':0}
            class Reader:
                def signed_url(self,url,check):return url
            bands=read_native(p,{'after':{'assets':assets}},Reader(),lambda:None)
            from backend.analysis.native_raster import grids
            data,_=grids(bands);self.assertEqual(int(data['after'][1].sum()),0)
            with self.assertRaises(GeoError):read_native({**p,'allow_sparse_quality':False},{'after':{'assets':assets}},Reader(),lambda:None)

