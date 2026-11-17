"""Independent Otsu/geometry checks; synthetic fixtures stay in temp folders."""
import hashlib
import json
import tempfile
import unittest
from pathlib import Path

import numpy as np
import rasterio
from rasterio.features import rasterize
from rasterio.transform import from_origin
from rasterio.warp import transform_bounds, transform_geom

from backend.analysis.adaptive_segmentation import otsu, run_segmentation, FILES
from backend.core.geo_services import GeoError

ROOT=Path(__file__).resolve().parents[1]
REAL_NDVI=ROOT/'data/production/production_e4981e20877640/analysis.tif'


class OtsuMathTest(unittest.TestCase):
    def test_two_modes_split_at_independent_between_class_maximum(self):
        values=np.array([-.9]*4+[-.8]*7+[-.3]*3+[.5]*5+[.7]*7+[.9]*2+[np.nan,np.inf])
        threshold,meta=otsu(values);finite=values[np.isfinite(values)]
        counts,edges=np.histogram(finite,bins=128,range=(float(finite.min()),float(finite.max())));centres=(edges[:-1]+edges[1:])/2
        scores=[]
        for split in range(1,len(counts)+1):
            left=sum(int(v) for v in counts[:split]);right=sum(int(v) for v in counts[split:]);total=left+right
            if not left or not right:scores.append(float('-inf'));continue
            lm=sum(float(x)*int(n) for x,n in zip(centres[:split],counts[:split]))/left
            rm=sum(float(x)*int(n) for x,n in zip(centres[split:],counts[split:]))/right
            scores.append((left/total)*(right/total)*(lm-rm)**2)
        expected=int(np.argmax(scores));self.assertAlmostEqual(threshold,float(edges[expected+1]),places=12)
        self.assertLess(threshold,.5);self.assertGreater(threshold,-.3);self.assertEqual(int((finite>=threshold).sum()),14)
        self.assertAlmostEqual(meta['between_class_variance'],scores[expected],places=12);self.assertEqual(sum(meta['counts']),len(finite));self.assertTrue(0<meta['separability']<=1+1e-12)

    def test_first_tie_boundary_and_constant_or_insufficient_rejection(self):
        threshold,meta=otsu(np.array([-1.]*5+[1.]*5))
        self.assertEqual(threshold,-1+2/128);self.assertEqual(meta['tie_policy'],'first maximum, lowest histogram split boundary')
        for values,code in [([1,1,1],'OTSU_NO_VARIANCE'),([np.nan,0],'OTSU_INSUFFICIENT_VALUES'),([np.nan,np.inf],'OTSU_INSUFFICIENT_VALUES')]:
            with self.subTest(values=values),self.assertRaises(GeoError) as e:otsu(values)
            self.assertEqual(e.exception.code,code)


class OtsuNativeGeometryTest(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.folder=Path(self.tmp.name);self.transform=from_origin(500000,3400000,10,10)
        self.data=np.full((7,7),-1.,dtype='float32');self.data[1:6,1:6]=1;self.data[2:5,2:5]=-1
        self.data[0,0]=1;self.data[0,6]=1;self.data[6,6]=1
        self.inside=np.ones((7,7),bool);self.inside[0,3]=False;self.good=np.ones((7,7),bool);self.good[3,0]=False
        # A high-valued invalid pixel must not leak into Otsu or the partition.
        self.data[3,0]=1;self.path=self.folder/'explicit_synthetic_source.tif'
        with rasterio.open(self.path,'w',driver='GTiff',width=7,height=7,count=3,dtype='float32',crs='EPSG:32649',transform=self.transform,nodata=np.nan) as ds:
            for i,(name,value) in enumerate([('after_ndvi',self.data),('common_valid_mask',self.good),('aoi_mask',self.inside)],1):ds.write(np.asarray(value,dtype='float32'),i);ds.set_band_description(i,name)
        bbox=list(transform_bounds('EPSG:32649','EPSG:4326',*rasterio.transform.array_bounds(7,7,self.transform)))
        self.plan={'source':{'task_id':'EXPLICIT_SYNTHETIC_UNIT_FIXTURE','index_id':'ndvi','index_label':'NDVI','bbox':bbox,'source_id':'pc_sentinel2_l2a','actual_dates':{'after':'2025-11-14T02:30:00Z'},'artifact_sha256':hashlib.sha256(self.path.read_bytes()).hexdigest()},'direction':'above','min_patch_pixels':2,'input_fingerprint':'unit-fixture-no-business-record'}

    def tearDown(self):self.tmp.cleanup()

    def test_hole_four_connectivity_small_patch_filter_and_geojson_roundtrip(self):
        result=run_segmentation(self.plan,self.path,self.folder/'test_hole');s=result['summary']
        expected_valid=self.inside&self.good;expected=expected_valid&(self.data>0);expected[0,0]=False;expected[0,6]=False;expected[6,6]=False
        self.assertEqual(s['valid_pixel_count'],47);self.assertEqual(s['native_pixel_count'],48)
        self.assertEqual((s['raw_patch_count'],s['patch_count'],s['removed_patch_count']),(4,1,3));self.assertEqual((s['raw_selected_pixel_count'],s['selected_pixel_count'],s['removed_pixel_count']),(19,16,3))
        self.assertAlmostEqual(s['selected_area_ha'],.16);self.assertEqual(s['invalid_pixel_count'],1);self.assertEqual(s['unknown_area_ha'],.01)
        self.assertEqual(len(result['geojson']['features'][0]['geometry']['coordinates']),2)
        shapes=[(transform_geom('EPSG:4326','EPSG:32649',f['geometry']),1) for f in result['geojson']['features']]
        burned=rasterize(shapes,out_shape=(7,7),transform=self.transform,fill=0,dtype='uint8').astype(bool)
        np.testing.assert_array_equal(burned,expected)
        with rasterio.open(self.folder/'test_hole/analysis.tif') as ds:
            names=ds.descriptions;selected=ds.read(names.index('selected_mask')+1);raw=ds.read(names.index('raw_selected_mask')+1)
            np.testing.assert_array_equal(selected[expected_valid],expected[expected_valid]);self.assertTrue(np.isnan(selected[~expected_valid]).all());self.assertEqual(int(np.nansum(raw)),19)
            self.assertEqual(ds.crs.to_epsg(),32649);self.assertEqual(ds.res,(10.,10.))
        self.assertEqual(set(x.name for x in (self.folder/'test_hole').iterdir()),set(FILES));self.assertFalse(result['image']['statistics_from_preview']);self.assertTrue(result['provenance']['not_external_engine_call'])

    def test_below_and_above_are_complementary_on_valid_pixels(self):
        a=run_segmentation({**self.plan,'min_patch_pixels':1},self.path,self.folder/'above')
        b=run_segmentation({**self.plan,'direction':'below','min_patch_pixels':1},self.path,self.folder/'below')
        self.assertEqual(a['summary']['threshold'],b['summary']['threshold']);self.assertEqual(a['summary']['selected_pixel_count']+b['summary']['selected_pixel_count'],47)
        self.assertEqual(a['summary']['threshold_operator'],'gte');self.assertEqual(b['summary']['threshold_operator'],'lt')
        with rasterio.open(self.folder/'above/analysis.tif') as ds, rasterio.open(self.folder/'below/analysis.tif') as other:
            av,bv=ds.read(1),other.read(1);valid=np.isfinite(av);np.testing.assert_array_equal(av[valid]+bv[valid],np.ones(int(valid.sum())))

    def test_removed_all_patches_is_a_true_zero_not_a_failure_or_unknown(self):
        r=run_segmentation({**self.plan,'min_patch_pixels':1000},self.path,self.folder/'none')
        self.assertEqual(r['summary']['selected_pixel_count'],0);self.assertEqual(r['summary']['selected_area_ha'],0);self.assertEqual(r['geojson']['features'],[])
        self.assertEqual(r['summary']['valid_pixel_count'],47)
        with rasterio.open(self.folder/'none/analysis.tif') as ds:
            selected=ds.read(1);self.assertEqual(int(np.nansum(selected)),0);self.assertEqual(int(np.isfinite(selected).sum()),47)

    @unittest.skipUnless(REAL_NDVI.is_file(),'optional existing genuine native NDVI evidence absent')
    def test_explicit_existing_public_scene_readonly_and_independent_mask_roundtrip(self):
        original=hashlib.sha256(REAL_NDVI.read_bytes()).hexdigest();source=json.loads(REAL_NDVI.with_name('result.json').read_text());p=source['provenance']
        self.assertEqual(p['source_type'],'public_satellite_pixels');self.assertEqual(p['sampling'],'native')
        plan={'source':{'task_id':'production_e4981e20877640','index_id':'ndvi','index_label':'NDVI','bbox':p['bbox'],'actual_dates':{'after':p['source_details']['scenes']['after']['datetime']},'source_id':'pc_sentinel2_l2a','artifact_sha256':original},'direction':'above','min_patch_pixels':3,'input_fingerprint':'read-only-public-native-evidence'}
        r=run_segmentation(plan,REAL_NDVI,self.folder/'genuine_public')
        with rasterio.open(REAL_NDVI) as src,rasterio.open(self.folder/'genuine_public/analysis.tif') as dst:
            values=src.read(src.descriptions.index('after_ndvi')+1);inside=src.read(src.descriptions.index('aoi_mask')+1)==1;valid=inside&(src.read(src.descriptions.index('common_valid_mask')+1)==1)&np.isfinite(values)
            self.assertEqual(r['summary']['valid_pixel_count'],int(valid.sum()));self.assertEqual(r['summary']['raw_selected_pixel_count'],int((valid&(values>=r['summary']['threshold'])).sum()))
            features=[(transform_geom('EPSG:4326',src.crs,f['geometry']),1) for f in r['geojson']['features']]
            burned=rasterize(features,out_shape=values.shape,transform=src.transform,fill=0,dtype='uint8') if features else np.zeros(values.shape,'uint8')
            np.testing.assert_array_equal(burned[valid],dst.read(1)[valid]);self.assertEqual(int(burned.sum()),r['summary']['selected_pixel_count'])
        self.assertEqual(hashlib.sha256(REAL_NDVI.read_bytes()).hexdigest(),original)

