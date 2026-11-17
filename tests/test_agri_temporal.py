"""Temporal contracts use isolated sources; no synthetic business records."""
import copy
import json
import tempfile
import unittest
from datetime import date
from pathlib import Path
from urllib.parse import parse_qs,urlsplit
from unittest.mock import patch

import numpy as np
import rasterio
from rasterio.transform import from_origin
from rasterio.warp import transform_geom

from backend.agriculture.agri_temporal import request,discover,produce,reduce_support,validate,add_quality_comparison
from backend.agriculture.city_spectral import ASSETS,sha_file
from backend.core.geo_services import GeoError


def feature(day,cloud=10,platform='landsat-8',full=True,tier='T1'):
    prefix='LC08' if platform=='landsat-8' else 'LC09'
    ident=prefix+'_L2SP_123039_'+day.replace('-','')+'_02_'+tier
    ring=[[112,29],[115,29],[115,32],[112,32],[112,29]] if full else [[113,30],[113.01,30],[113.01,30.01],[113,30.01],[113,30]]
    assets={}
    for band,name in ASSETS.items():
        src={'href':'https://landsateuwest.blob.core.windows.net/landsat-c2/level-2/standard/oli-tirs/2025/123/039/'+ident+'/'+ident+'_'+name+'.TIF'}
        if band not in ('qa_pixel','qa_radsat'):src['raster:bands']=[{'scale':.0000275,'offset':-.2,'nodata':0,'spatial_resolution':30}]
        assets[name]=src
    return {'type':'Feature','id':ident,'collection':'landsat-c2-l2','geometry':{'type':'Polygon','coordinates':[ring]},'properties':{'datetime':day+'T03:00:00Z','platform':platform,'eo:cloud_cover':cloud},'assets':assets}


class Catalog:
    def __init__(self,missing=None,tier2=None):self.calls=0;self.missing=missing;self.tier2=tier2
    def fetch_json(self,url,check):
        self.calls+=1;start=parse_qs(urlsplit(url).query)['datetime'][0][:10]
        day=start[:8]+'15'
        return {'features':[] if start[:7]==self.missing else [feature(day,tier='T2')] if start[:7]==self.tier2 else [feature(day),feature(day,5),feature(day,0,tier='T2'),feature(day,1,platform='landsat-9'),feature(day,0,full=False)],'links':[]}


class Reader:
    def __init__(self):self.calls=[];self.grid={'shape':(4,6),'transform':from_origin(684000,3379800,30,30),'crs':rasterio.crs.CRS.from_epsg(32649),'inside':np.ones((4,6),dtype=bool),'cell_area_m2':900}
    def read(self,scene,band,grid=None,boundary=None):
        self.calls.append((scene['scene_id'],band));month=int(scene['datetime'][5:7]);shape=self.grid['shape']
        if band=='qa_pixel':value=np.zeros(shape,dtype='float32');value[0,0]=8
        elif band=='qa_radsat':value=np.zeros(shape,dtype='float32');value[0,1]=1
        elif band=='red':value=np.full(shape,.15,dtype='float32')
        elif band=='nir':value=np.full(shape,.3+month*.01,dtype='float32')
        elif band=='swir1':value=np.full(shape,.2,dtype='float32')
        else:raise AssertionError(band)
        return value,grid or self.grid,{'test_only':'isolated numeric native grid'}


class TemporalTest(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name);self.reader=Reader()
        g=self.reader.grid;t=g['transform'];w=g['shape'][1]*30;h=g['shape'][0]*30
        geom={'type':'Polygon','coordinates':[[[t.c,t.f],[t.c+w,t.f],[t.c+w,t.f-h],[t.c,t.f-h],[t.c,t.f]]]}
        self.boundary={'type':'FeatureCollection','features':[{'type':'Feature','properties':{},'geometry':transform_geom(g['crs'],'EPSG:4326',geom)}]}
        self.meta={'sha256':'a'*64,'boundary_year_represented':2017}
    def tearDown(self):self.tmp.cleanup()
    def plan(self):return request('2025-05-01','2025-09-30',today=date(2026,1,1))
    def run_series(self,catalog=None):
        with patch('backend.agriculture.agri_temporal.load_boundary',return_value=(self.boundary,self.meta)):
            return produce(self.root/'series',self.plan(),archive_root=None,catalog_reader=catalog or Catalog(),city_reader=self.reader,progress=lambda _:None)
    def test_windows_are_complete_and_past_but_do_not_claim_daily_values(self):
        plan=self.plan();self.assertEqual(len(plan['windows']),5);self.assertEqual(plan['windows'][1]['end'],'2025-06-30')
        self.assertEqual(len(request('2025-05-01','2025-05-31',interval='dekad',today=date(2026,1,1))['windows']),3)
        for kwargs in [{'platform':'sentinel2'},{'max_candidates':0},{'minimum_qa':0},{'end':'2027-01-01'},{'start':'2024-01-01'}]:
            with self.assertRaises(GeoError):request(**kwargs,today=date(2026,1,1))
    def test_discovery_filters_platform_partial_footprints_and_duplicate_dates(self):
        catalog=Catalog();plan=self.plan();m=discover(plan,self.boundary,self.root/'catalogs',catalog)
        self.assertEqual(catalog.calls,5)
        for window in m['windows']:
            self.assertEqual(len(window['candidates']),1);self.assertEqual(window['candidates'][0]['platform'],'landsat-8');self.assertEqual(window['candidates'][0]['scene_cloud_cover'],5)
            self.assertTrue(window['rejected'])
        self.assertEqual(discover(plan,self.boundary,self.root/'catalogs',catalog),m);self.assertEqual(catalog.calls,5)
        path=self.root/'catalogs'/m['catalogs'][0]['file'];path.write_text('{}')
        with self.assertRaises(GeoError):discover(plan,self.boundary,self.root/'catalogs',catalog)
    def test_unknown_support_is_not_zero_and_common_support_preserves_real_zero(self):
        values=np.array([[[0.,np.nan],[.2,.3]],[[.1,np.nan],[np.nan,.4]],[[.2,np.nan],[.4,.5]]],dtype='float32')
        count,common,means=reduce_support(values,np.ones((2,2),dtype=bool))
        self.assertEqual(count.tolist(),[[3,0],[2,3]]);self.assertEqual(common.tolist(),[[True,False],[False,True]])
        self.assertAlmostEqual(means[0],.15,places=6)
    def test_real_pipeline_exports_formulas_and_counts_reopened_independently(self):
        result=self.run_series();self.assertEqual(result['status'],'ready');self.assertEqual(len(result['observations']),5)
        self.assertEqual(result['sequence_summary']['common_support_pixels'],22)
        self.assertFalse(result['provenance']['composite']);self.assertEqual(len(result['changes']),8)
        self.assertEqual(sum(not v['comparison_eligible'] for v in result['changes']),4)
        for item in result['observations']:
            self.assertEqual(item['ndvi_valid_pixel_count'],22);self.assertEqual(item['ndmi_valid_pixel_count'],22)
            with rasterio.open(self.root/'series'/item['native_file']) as ds:
                self.assertEqual(ds.descriptions[:3],('value','valid_mask','city_mask'));self.assertTrue(np.isnan(ds.read(1)[0,0]));self.assertTrue(np.isnan(ds.read(1)[0,1]))
                nir=.3+int(item['date'][5:7])*.01;self.assertAlmostEqual(float(ds.read(1)[1,1]),(nir-.15)/(nir+.15),places=6)
        self.assertEqual(validate(self.root/'series')['status'],'passed')
        self.assertLess(result['validation']['max_formula_absolute_error'],1e-7)
    def test_existing_result_is_read_only_and_changed_raster_is_rejected(self):
        result=self.run_series();before={str(p):p.read_bytes() for p in (self.root/'series').rglob('*') if p.is_file()};calls=len(self.reader.calls)
        again=self.run_series();self.assertEqual(again,result);self.assertEqual(len(self.reader.calls),calls)
        self.assertEqual(before,{str(p):p.read_bytes() for p in (self.root/'series').rglob('*') if p.is_file()})
        item=result['observations'][0];path=self.root/'series'/item['native_file']
        with rasterio.open(path,'r+') as ds:
            x=ds.read(1);x[1,1]=.99;ds.write(x,1)
        with self.assertRaises(GeoError):validate(self.root/'series')
    def test_missing_month_remains_explicit_and_no_placeholder_observation(self):
        result=self.run_series(Catalog('2025-07'))
        self.assertEqual(len(result['observations']),4);self.assertEqual(result['sequence_summary']['missing_windows'],['2025-07'])
        self.assertFalse(any(item['date'].startswith('2025-07') for item in result['observations']))
        self.assertEqual(sum(not v['comparison_eligible'] for v in result['changes']),3)
    def test_tier2_is_preserved_as_a_rejection_but_never_enters_sequence(self):
        result=self.run_series(Catalog(tier2='2025-07'))
        self.assertEqual(result['sequence_summary']['missing_windows'],['2025-07'])
        gap=next(v for v in result['window_status'] if v['label']=='2025-07')
        self.assertIn('Tier2',gap['reason']);self.assertTrue(all(v['scene_id'].endswith('_T1') for v in result['observations']))
    def test_frozen_catalog_and_existing_native_observations_need_no_remote_reads(self):
        original=self.run_series();reader=Reader()
        with patch('backend.agriculture.agri_temporal.load_boundary',return_value=(self.boundary,self.meta)):
            final=produce(self.root/'frozen',self.plan(),archive_root=None,city_reader=reader,frozen_catalog_dir=self.root/'series/catalogs',reuse_from=self.root/'series',progress=lambda _:None)
        self.assertEqual(reader.calls,[])
        self.assertEqual([v['sha256'] for v in original['observations']],[v['sha256'] for v in final['observations']])
        self.assertTrue(all('reused_series' in v['evidence'] for v in final['observations']))
        self.assertEqual(validate(self.root/'frozen')['status'],'passed')
    def test_failed_read_resumes_from_frozen_catalog_and_verified_earlier_pixels(self):
        class Failing(Reader):
            def read(self,scene,band,**kwargs):
                if scene['datetime'].startswith('2025-06') and band=='red':raise GeoError('CITY_SOURCE_READ_FAILED','isolated failure',502)
                return super().read(scene,band,**kwargs)
        self.reader=Failing()
        with self.assertRaises(GeoError):self.run_series()
        first=next((self.root/'series/observations').glob('*/analysis.tif'));saved=first.read_bytes()
        self.reader=Reader();catalog=Catalog();result=self.run_series(catalog)
        self.assertEqual(catalog.calls,0);self.assertEqual(first.read_bytes(),saved)
        self.assertFalse(any(scene.startswith('LC08_L2SP_123039_202505') for scene,band in self.reader.calls))
        self.assertEqual(result['status'],'ready')
    def test_quality_subset_is_independently_recomputed_without_filling_original_gaps(self):
        class QualityReader(Reader):
            def read(self,scene,band,**kwargs):
                values,grid,evidence=super().read(scene,band,**kwargs);month=int(scene['datetime'][5:7])
                if band=='qa_pixel' and month==5:values[:3,:]=8
                if band=='qa_pixel' and month==9:values[:,:]=8
                if band=='swir1' and month==6:values[1,1]=np.nan
                if band=='red' and month==7:values[2,2]=np.nan
                return values,grid,evidence
        self.reader=QualityReader();result=self.run_series();subset=result['quality_comparison']
        self.assertEqual(result['sequence_summary']['common_support_pixels'],0)
        self.assertTrue(all(v['common_mean'] is None for v in result['observations']))
        self.assertEqual(subset['dates'],['2025-06-15','2025-07-15','2025-08-15'])
        self.assertEqual(subset['common_support_pixels'],20)
        self.assertAlmostEqual(subset['common_support_ratio'],20/24)
        for item in result['observations']:
            month=int(item['date'][5:7]);eligible=month in (6,7,8)
            self.assertEqual(item['comparison_eligible'],eligible)
            if eligible:
                nir=np.float32(.3+month*.01);red=np.float32(.15);swir=np.float32(.2)
                self.assertAlmostEqual(item['quality_common_mean_ndvi'],float((nir-red)/(nir+red)),places=7)
                self.assertAlmostEqual(item['quality_common_mean_ndmi'],float((nir-swir)/(nir+swir)),places=7)
            else:
                self.assertIsNone(item['quality_common_mean_ndvi']);self.assertIsNone(item['quality_common_mean_ndmi'])
        additions=[v for v in result['changes'] if v['comparison_eligible']]
        self.assertEqual([(v['before_date'],v['after_date']) for v in additions],[('2025-06-15','2025-07-15'),('2025-07-15','2025-08-15')])
        unsupported=[v for v in result['changes'] if v['common_valid_pixel_count']==0]
        self.assertTrue(unsupported)
        self.assertTrue(all(v['support_status']=='no_support' and v['positive_condition_area_ha'] is None and v['negative_condition_area_ha'] is None and v['mean_delta_ndvi'] is None for v in unsupported))
        self.assertTrue(validate(self.root/'series')['quality_comparison_checked'])
        frozen={entry['file']:sha_file(self.root/'series'/entry['file']) for entry in result['artifacts'] if not entry['file'].startswith('quality-')}
        again=add_quality_comparison(self.root/'series')
        self.assertEqual(again['quality_comparison'],subset)
        self.assertEqual(frozen,{path:sha_file(self.root/'series'/path) for path in frozen})
        tampered=copy.deepcopy(again);tampered['observations'][1]['quality_common_mean_ndvi']+=.1
        with self.assertRaises(GeoError):validate(self.root/'series',tampered)
        # Re-signing a corrupted derived mask must not bypass independent pixels.
        path=self.root/'series'/subset['native_file']
        with rasterio.open(path,'r+') as ds:
            values=ds.read(1);values[3,3]=0;ds.write(values,1)
        tampered=copy.deepcopy(again);tampered['quality_comparison']['sha256']=sha_file(path)
        for entry in tampered['artifacts']:
            if entry['file']==subset['native_file']:entry['sha256']=sha_file(path)
        with self.assertRaises(GeoError):validate(self.root/'series',tampered)
    def test_no_qualified_dates_remain_unknown_without_zero_comparison_values(self):
        class CloudReader(Reader):
            def read(self,scene,band,**kwargs):
                values,grid,evidence=super().read(scene,band,**kwargs)
                if band=='qa_pixel':values[:,:]=8
                return values,grid,evidence
        self.reader=CloudReader();result=self.run_series();subset=result['quality_comparison']
        self.assertEqual(result['status'],'insufficient_dates')
        self.assertEqual(subset['status'],'insufficient_common_support');self.assertEqual(subset['dates'],[])
        self.assertEqual(subset['common_support_pixels'],0)
        self.assertTrue(all(item['quality_common_mean_ndvi'] is None and item['quality_common_mean_ndmi'] is None for item in result['observations']))
        self.assertFalse(any(item['comparison_eligible'] for item in result['changes']))
        self.assertEqual(validate(self.root/'series')['status'],'passed')


if __name__=='__main__':unittest.main()
