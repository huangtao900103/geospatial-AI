"""Metadata suitability is strict and never claims that pixels were read."""
import copy
from pathlib import Path
import sys
import unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from geo_services import GeoError
from algorithm_catalog import catalog,match_observation


def observation(source='pc_sentinel2_l2a'):
    land=source=='pc_landsat_c2_l2'
    keys={'green':'green','red':'red','nir08':'nir','swir16':'swir1','swir22':'swir2','blue':'blue'} if land else {'B03':'green','B04':'red','B08':'nir','B11':'swir1','B12':'swir2','B02':'blue','B05':'rededge','B8A':'nir_narrow'}
    root='https://landsateuwest.blob.core.windows.net/landsat-c2/level-2/standard/oli-tirs/2025/123/039/' if land else 'https://sentinel2l2a01.blob.core.windows.net/sentinel2-l2/50/R/LS/2025/11/14/0/'
    ext='.TIF' if land else '.tif'
    assets=[{'key':key,'common_name':common,'href':root+key+ext,'media_type':'image/tiff; application=geotiff; profile=cloud-optimized','unit':'reflectance_after_calibration','scale':0.0000275 if land else None,'offset':-0.2 if land else None,'nodata':0,'resolution':30 if land else 20 if key in ('B05','B8A','B11','B12') else 10} for key,common in keys.items()]
    for key in (('qa_pixel','qa_radsat') if land else ('SCL',)):
        assets.append({'key':key,'common_name':key.lower(),'href':root+key+ext,'media_type':'image/tiff','unit':'bit index' if land else None})
    return {'id':'observation_test','fingerprint':'test-fingerprint','source_id':source,'collection':'landsat-c2-l2' if land else 'sentinel-2-l2a','platform':'landsat-9' if land else 'sentinel-2b','datetime':'2025-11-14T02:30:00Z','assets':assets}


def ready(obs,index='ndvi'):
    with patch('algorithm_catalog.runtime_ready',return_value=True):return match_observation(obs,index)['matches'][0]


class ContractTest(unittest.TestCase):
    def test_contracts_distinguish_sources_quality_and_reference_resolution(self):
        data=catalog();self.assertEqual(len(data['contracts']),23)
        s2=next(c for c in data['contracts'] if c['id']=='pc_sentinel2_l2a:mndwi:native-v1')
        ls=next(c for c in data['contracts'] if c['id']=='pc_landsat_c2_l2:mndwi:native-v1')
        self.assertEqual(s2['required_bands'],['B03','B11']);self.assertEqual(s2['resolution']['reference_m'],20)
        self.assertEqual(ls['required_bands'],['green','swir16']);self.assertEqual(ls['resolution']['reference_m'],30)
        self.assertEqual(s2['required_quality_assets'],['SCL']);self.assertEqual(ls['required_quality_assets'],['qa_pixel','qa_radsat'])
        self.assertTrue(ls['quality_policy']['water_bit_7_retained']);self.assertFalse(ls['time']['cross_sensor_change_supported'])
    def test_valid_metadata_is_request_ready_but_does_not_claim_pixel_read(self):
        with patch('algorithm_catalog.runtime_ready',return_value=True):
            for source in ('pc_sentinel2_l2a','pc_landsat_c2_l2'):
                result=match_observation(observation(source));self.assertEqual(result['ready_count'],11 if source=='pc_landsat_c2_l2' else 12)
                self.assertTrue(result['metadata_only']);self.assertFalse(result['pixels_read'])
                self.assertEqual(result['observation_fingerprint'],'test-fingerprint')
    def test_s2_missing_asset_calibration_is_permitted_only_by_xml_policy(self):
        o=observation();o['assets'][0]['unit']='DN';item=ready(o,'ndwi');self.assertTrue(item['ready'])
        c=next(c for c in catalog()['contracts'] if c['id']==item['id'])
        self.assertEqual(c['calibration_policy']['id'],'product_xml_before_read')
        self.assertTrue(item['calibration_required_before_compute'])
    def test_landsat_calibration_missing_or_wrong_cannot_be_ready(self):
        for field,value in (('scale',None),('scale',.0001),('offset',0),('nodata',None),('resolution',10)):
            o=observation('pc_landsat_c2_l2');next(a for a in o['assets'] if a['key']=='red')[field]=value
            with self.subTest(field=field,value=value):self.assertFalse(ready(o)['ready'])
    def test_quality_and_index_specific_assets_are_required(self):
        o=observation();o['assets']=[a for a in o['assets'] if a['key']!='B11']
        self.assertTrue(ready(o,'ndvi')['ready']);self.assertFalse(ready(o,'ndmi')['ready'])
        for source,quality in (('pc_sentinel2_l2a','SCL'),('pc_landsat_c2_l2','qa_pixel'),('pc_landsat_c2_l2','qa_radsat')):
            o=observation(source);o['assets']=[a for a in o['assets'] if a['key']!=quality]
            self.assertFalse(ready(o)['ready'])
    def test_landsat_legacy_platform_and_wrong_collection_are_rejected(self):
        for field,value in (('platform','landsat-7'),('platform',None),('collection','landsat-c2l2-sr')):
            o=observation('pc_landsat_c2_l2');o[field]=value;self.assertFalse(ready(o)['ready'])
    def test_catalog_only_dem_sar_and_unimplemented_optical_never_ready(self):
        for source in ('earthsearch_sentinel2_l2a','earthsearch_cop_dem_glo30','cdse_sentinel2_l2a','pc_sentinel1_rtc'):
            o=observation();o['source_id']=source;o['stages']={'pixel_verified':True}
            item=ready(o);self.assertFalse(item['ready']);self.assertIn('SOURCE_EXECUTOR_UNSUPPORTED',[g['code'] for g in item['gaps']])
    def test_signed_arbitrary_and_invalid_locators_are_blocked_without_echo(self):
        for href in ('https://evil.invalid/B04.tif','http://sentinel2l2a01.blob.core.windows.net/sentinel2-l2/B04.tif','https://sentinel2l2a01.blob.core.windows.net/sentinel2-l2/B04.tif?sig=secret-test',None,'https://user:pass@sentinel2l2a01.blob.core.windows.net/sentinel2-l2/B04.tif'):
            o=observation();next(a for a in o['assets'] if a['key']=='B04')['href']=href
            item=ready(o);self.assertFalse(item['ready']);self.assertNotIn('secret-test',str(item))
    def test_wrong_semantics_units_or_media_do_not_match_by_band_name_alone(self):
        for field,value in (('common_name','elevation'),('unit','kelvin'),('media_type','image/png'),('auth_required',True)):
            o=observation();next(a for a in o['assets'] if a['key']=='B04')[field]=value
            self.assertFalse(ready(o)['ready'])
        for href in ('https://landsateuwest.blob.core.windows.net:443/landsat-c2/level-2/standard/oli-tirs/red.TIF','https://landsateuwest.blob.core.windows.net/landsat-c2/level-2/standard/oli-tirs/red..TIF','https://landsateuwest.blob.core.windows.net/landsat-c2/level-2/standard/oli-tirs/red TIF.TIF'):
            o=observation('pc_landsat_c2_l2');next(a for a in o['assets'] if a['key']=='red')['href']=href
            self.assertFalse(ready(o)['ready'])
    def test_missing_runtime_stages_and_invalid_time_are_not_ready(self):
        with patch('algorithm_catalog.runtime_ready',return_value=False):
            self.assertEqual(match_observation(observation())['ready_count'],0)
        for date in ('2025-11-14','2025-11-14T02:30:00','not-a-date',None):
            o=observation();o['datetime']=date;self.assertFalse(ready(o)['ready'])
        o=observation();o['stages']={'pixel_readable':False};self.assertFalse(ready(o)['ready'])
    def test_duplicate_keys_and_unsupported_index_are_explicit(self):
        o=observation();o['assets'].append(copy.deepcopy(o['assets'][0]));self.assertFalse(ready(o)['ready'])
        with self.assertRaises(GeoError):match_observation(o,'evi')
        with self.assertRaises(GeoError):match_observation(None)

if __name__=='__main__':unittest.main()
