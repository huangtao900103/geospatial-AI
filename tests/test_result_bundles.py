"""Portable bundle integrity and scope tests; fixture bytes are not real EO data."""
import copy
import hashlib
import json
import tempfile
import threading
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from geo_services import GeoError
from result_bundle_services import FILES,ResultBundleServices,canonical


class Service:
    def __init__(self,root,kind,records):self.output_dir=root/kind;self.records=records
    def get(self,ident):return copy.deepcopy(self.records[ident])
    def artifact(self,ident,name):
        path=self.output_dir/ident/name
        if name not in FILES[self.records[ident]['kind']] or not path.is_file():raise GeoError('ARTIFACT_UNAVAILABLE','Missing fixture',404)
        return path


class Store:
    def __init__(self,root):
        self.db_path=root/'test.sqlite3';self.records={};self.lock=threading.RLock()
        for kind in ('cropland','production','landcover'):setattr(self,kind,Service(root,kind,self.records))
    def get(self,kind,ident):return copy.deepcopy(self.records[ident])


class BundleTest(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name);self.store=Store(self.root);self.service=ResultBundleServices(self.store)
    def tearDown(self):self.temp.cleanup()
    def seed(self,kind='cropland',ident='fixture_task'):
        task={'id':ident,'kind':kind,'name':'Explicit test fixture only','status':'succeeded','plan':{'bbox':[113.,30.,113.01,30.01],'product':{'id':'io_lulc_2024','year':2024},'class_code':5},'result':{'summary':{'reference_year':2024,'cropland_ha':1.2,'patch_count':1},'provenance':{'year':2024,'product':{'id':'io_lulc_2024'},'method':'test fixture method'},'report':{'limitations':['TEST ONLY; not independently validated']}},'created_at':'2026-11-14','finished_at':'2026-11-14'}
        if kind=='production':task['plan'].update(mode='sentinel_cog',sampling='native')
        self.store.records[ident]=task
        if kind=='analysis':
            task.update(dataset_id='fixture_dataset',algorithm_id='spatial_summary',geojson={'type':'FeatureCollection','features':[]},summary={'feature_count':0},provenance={'source_type':'user','parameters':{},'dataset_version':1,'sha256':'fixture-fingerprint'})
            del task['result'];del task['plan']
        else:
            folder=getattr(self.store,kind).output_dir/ident;folder.mkdir(parents=True)
            for name in FILES[kind]:(folder/name).write_bytes(('TEST FIXTURE ONLY '+name).encode())
        return task
    def test_bundle_contents_hashes_year_inputs_and_manifest_are_consistent_and_read_only(self):
        task=self.seed();before=copy.deepcopy(self.store.records)
        manifest=self.service.manifest('cropland',task['id']);archive=self.service.artifact('cropland',task['id'])
        self.assertEqual(manifest['source_year'],2024);self.assertEqual(manifest['inputs']['class_code'],5)
        with zipfile.ZipFile(archive) as z:
            self.assertIsNone(z.testzip());self.assertEqual(z.read('manifest.json'),canonical(manifest))
            for e in manifest['entries']:
                raw=z.read(e['path']);self.assertEqual(len(raw),e['bytes']);self.assertEqual(hashlib.sha256(raw).hexdigest(),e['sha256'])
            self.assertIn('task_record.json',z.namelist());self.assertIn('README.txt',z.namelist());self.assertIn('inputs.json',z.namelist())
        self.assertEqual(before,self.store.records);self.assertEqual(self.service.artifact('cropland',task['id']),archive)
    def test_only_fixed_artifacts_are_exported_not_neighbor_configs_or_database(self):
        self.seed();folder=self.store.cropland.output_dir/'fixture_task'
        (folder/'private-config.json').write_text('fixture secret must not enter archive');(folder/'workspace.sqlite3').write_bytes(b'test only')
        with zipfile.ZipFile(self.service.artifact('cropland','fixture_task')) as z:
            self.assertNotIn('private-config.json',z.namelist());self.assertNotIn('workspace.sqlite3',z.namelist())
    def test_invalid_scope_or_non_successful_task_is_not_downloadable(self):
        self.seed()
        for kind,ident in [('weather','fixture_task'),('cropland','../fixture_task'),('cropland','a/b'),('cropland','')]:
            with self.subTest(kind=kind,ident=ident),self.assertRaises(GeoError):self.service.artifact(kind,ident)
        self.store.records['fixture_task']['status']='cancelled'
        with self.assertRaises(GeoError) as raised:self.service.artifact('cropland','fixture_task')
        self.assertEqual(raised.exception.code,'BUNDLE_NOT_READY')
    def test_missing_or_empty_required_artifact_stops_instead_of_partial_zip(self):
        self.seed();p=self.store.cropland.output_dir/'fixture_task'/'classification.tif';p.unlink()
        with self.assertRaises(GeoError):self.service.artifact('cropland','fixture_task')
        p.write_bytes(b'')
        with self.assertRaises(GeoError) as e:self.service.artifact('cropland','fixture_task')
        self.assertEqual(e.exception.code,'BUNDLE_FILE_EMPTY');self.assertFalse(self.service.output_dir.exists())
    def test_file_and_parent_directory_symlinks_are_rejected(self):
        self.seed();p=self.store.cropland.output_dir/'fixture_task'/'classification.tif';p.unlink();outside=self.root/'outside.tif';outside.write_bytes(b'fixture private location');p.symlink_to(outside)
        with self.assertRaises(GeoError) as e:self.service.artifact('cropland','fixture_task')
        self.assertEqual(e.exception.code,'BUNDLE_PATH_REJECTED')
        p.unlink();folder=p.parent;other=self.root/'outside_task';folder.rename(other);folder.symlink_to(other,target_is_directory=True)
        with self.assertRaises(GeoError) as e:self.service.artifact('cropland','fixture_task')
        self.assertEqual(e.exception.code,'BUNDLE_PATH_REJECTED')
    def test_size_limit_fails_before_archive_generation(self):
        self.seed();small=ResultBundleServices(self.store,max_total_bytes=100)
        with self.assertRaises(GeoError) as e:small.artifact('cropland','fixture_task')
        self.assertEqual(e.exception.code,'BUNDLE_SIZE_LIMIT');self.assertFalse(small.output_dir.exists())
    def test_corrupted_cached_zip_is_rebuilt_and_changed_source_gets_new_fingerprint(self):
        self.seed();original=self.service.artifact('cropland','fixture_task');original.write_bytes(b'corrupt cached archive')
        self.assertEqual(self.service.artifact('cropland','fixture_task'),original)
        with zipfile.ZipFile(original) as z:self.assertIsNone(z.testzip())
        (self.store.cropland.output_dir/'fixture_task'/'patches.csv').write_bytes(b'changed explicit test fixture')
        self.assertNotEqual(self.service.artifact('cropland','fixture_task'),original)
    def test_mid_bundle_source_change_is_detected_and_partial_archive_removed(self):
        self.seed();original=self.service._snapshot
        def changing(*args):
            snapshot=original(*args)
            (self.store.cropland.output_dir/'fixture_task'/'classification.tif').write_bytes(b'fixture changed after snapshot')
            return snapshot
        with patch.object(self.service,'_snapshot',side_effect=changing),self.assertRaises(GeoError) as e:self.service.artifact('cropland','fixture_task')
        self.assertEqual(e.exception.code,'BUNDLE_SOURCE_CHANGED');self.assertEqual(list(self.service.output_dir.iterdir()),[])
    def test_native_production_and_vector_analysis_are_supported_but_sample_is_rejected(self):
        self.seed('production','native');self.assertTrue(self.service.artifact('production','native').is_file())
        self.store.records['native']['plan']['mode']='sample_pixels'
        with self.assertRaises(GeoError) as e:self.service.artifact('production','native')
        self.assertEqual(e.exception.code,'BUNDLE_UNSUPPORTED')
        self.seed('analysis','vector')
        with zipfile.ZipFile(self.service.artifact('analysis','vector')) as z:
            self.assertEqual(json.loads(z.read('result.geojson'))['type'],'FeatureCollection');self.assertEqual(json.loads(z.read('inputs.json'))['dataset_id'],'fixture_dataset')
    def test_readme_explains_frozen_cropland_scope_year_area_unknowns_and_shared_task(self):
        task=self.seed();task['plan'].update(requirement='提取选区2024年作物覆盖参考图斑并统计面积',method='IO2024原生UTM10米投影格网面积',area_method='native_utm_projected_grid');task['result']['provenance']['product'].update(name='Impact Observatory / Esri Annual LULC 2024');task['result']['summary'].update(cropland_ha=73.65,cropland_mu=1104.75,patch_count=1,coverage_ratio=.8,unknown_area_ha=2.0,cloud_area_ha=1.0,native_crs='EPSG:32649',method=task['plan']['method'])
        with zipfile.ZipFile(self.service.artifact('cropland','fixture_task')) as z:text=z.read('README.txt').decode('utf-8')
        for value in ['73.65 公顷','1,104.75 亩','80.00 %','2.00 公顷','其中云遮挡未知面积：1.00 公顷','2024 年','113.01','原生UTM10米投影格网面积','原始需求（共享真实子任务创建时归档）','当前提问、问题ID及引用链']:
            self.assertIn(value,text)
    def test_readme_reports_actual_spectral_dates_and_vector_dataset_version(self):
        task=self.seed('production','native');task['plan'].update(requirement='对选区进行NDMI分析',index_id='ndmi');task['result']['summary'].update(index={'label':'NDMI'},mean_index=.123456,valid_count=400,selected_area_ha=1.2);task['result']['provenance']['source_details']={'scenes':{'after':{'datetime':'2025-11-29T00:00:00Z'}}}
        with zipfile.ZipFile(self.service.artifact('production','native')) as z:text=z.read('README.txt').decode('utf-8')
        for value in ['2025-11-29T00:00:00Z','执行指数：NDMI','0.1235 无量纲','400 个','1.20 公顷']:self.assertIn(value,text)
        self.seed('analysis','vector')
        with zipfile.ZipFile(self.service.artifact('analysis','vector')) as z:text=z.read('README.txt').decode('utf-8')
        for value in ['输出要素数：0 个','输入数据版本：1','fixture_dataset','spatial_summary']:self.assertIn(value,text)


if __name__=='__main__':unittest.main()
