"""Offline typed search checks; source points are navigation, never AOI masks."""
import copy
import json
import tempfile
import unittest
from pathlib import Path
from backend.agriculture.agriculture_atlas import AgricultureAtlas
from backend.core.search_services import SearchServices,coordinates,geo_bounds
from backend.core.geo_services import GeoError

class Store:
    def __init__(self):self.rows={}
    def list(self,kind):return copy.deepcopy(self.rows.get(kind,[]))

class SearchTest(unittest.TestCase):
    def setUp(self):self.store=Store();self.search=SearchServices(self.store)
    def production(self,id='p1',index='mndwi',status='succeeded'):
        t={'id':id,'status':status,'plan':{'requirement':'分析武汉所选区域水体光谱','goal':'ndvi','region':'指定选区'},'result':{'summary':{'index_id':index,'index':{'label':index.upper(),'name':'改进型归一化水体指数'}},'provenance':{'bbox':[114.2,30.5,114.21,30.51],'source_type':'public_satellite_pixels','source':'Sentinel-2 / PC','source_details':{'scenes':{'after':{'datetime':'2025-11-14T01:00:00Z'}}}}}}
        self.store.rows.setdefault('production_task',[]).append(t);return t
    def test_real_reference_city_pinyin_alias_and_no_administrative_bbox(self):
        for query,id in [('武汉','1791247'),('Wuhan','1791247'),('湖北省武汉市','1791247'),('仙桃','1790371'),('武昌区','1791351'),('湖北省','1806949')]:
            with self.subTest(query=query):
                r=self.search.search(query);hit=r['results'][0];self.assertEqual(hit['place_id'],id);self.assertEqual(hit['kind'],'place');self.assertFalse(hit['aoi_eligible']);self.assertNotIn('bbox',hit);self.assertIn('geonames.org',hit['source_url']);self.assertIn('仅用于地图定位',hit['subtitle'])
        self.assertEqual(self.search.search('武汉')['results'][0]['center'],[114.26667,30.58333]);self.assertGreater(len(self.search.places),2000)
    def test_reference_points_are_returned_by_value(self):
        a=self.search.search('武汉');a['results'][0]['center'][0]=0;self.assertEqual(self.search.search('武汉')['results'][0]['center'][0],114.26667)
    def test_coordinates_plain_fullwidth_labels_and_cardinals(self):
        for query in ['114.2,30.5','114.2，30.5','114.2 30.5','经度114.2 纬度30.5','纬度:30.5,经度:114.2','lon=114.2 lat=30.5','114.2°E,30.5°N']:
            with self.subTest(query=query):
                r=self.search.search(query);hit=r['results'][0];self.assertEqual(hit['kind'],'coordinate');self.assertEqual(hit['center'],[114.2,30.5]);self.assertNotIn('bbox',hit);self.assertFalse(hit['aoi_eligible'])
        self.assertEqual(coordinates('114.2 W,30.5 S')[0],[-114.2,-30.5])
    def test_invalid_numeric_input_is_not_a_place_query_or_old_hit(self):
        for query in ['30.5,114.2','181,30','114,86','114.2,30.5,7','nan,30','经度114 纬度not-a-number','114.']:
            with self.subTest(query=query):
                r=self.search.search(query);self.assertEqual(r['results'],[]);self.assertEqual(r['warnings'][0]['code'],'INVALID_COORDINATES')
    def test_empty_unknown_and_invalid_limit_are_clear(self):
        self.assertEqual(self.search.search('   ')['results'],[])
        r=self.search.search('未收录的某个院落123');self.assertEqual(r['results'],[]);self.assertEqual(r['warnings'][0]['code'],'NO_MATCH')
        for value in [0,21,True,'1.2','a']:
            with self.subTest(limit=value),self.assertRaises(GeoError):self.search.search('武汉',value)
        with self.assertRaises(GeoError):self.search.search('x'*201)
        with self.assertRaises(GeoError):self.search.search('bad\nquery')
    def test_missing_place_file_does_not_break_results_or_coordinates(self):
        service=SearchServices(self.store,Path('/missing/v11-place-file.json'));self.production();r=service.search('MNDWI');self.assertEqual(r['results'][0]['task_id'],'p1');self.assertEqual(r['warnings'][0]['code'],'PLACE_INDEX_UNAVAILABLE');self.assertEqual(service.search('114.2,30.5')['results'][0]['kind'],'coordinate')
    def test_successful_result_search_works_without_visible_map_layer(self):
        self.production();r=self.search.search('MNDWI');hit=r['results'][0];self.assertEqual(hit['task_id'],'p1');self.assertEqual(hit['result_ref']['type'],'production');self.assertEqual(hit['result_ref']['image_kind'],'mndwi');self.assertEqual(hit['bbox'],[114.2,30.5,114.21,30.51]);self.assertIn('2025-11-14',hit['subtitle'])
    def test_nbr_result_and_vertical_line_extent_are_searchable(self):
        self.production('nbr-run',index='nbr');hit=self.search.search('NBR')['results'][0];self.assertEqual(hit['result_ref']['image_kind'],'nbr');self.assertEqual(hit['task_id'],'nbr-run')
        self.store.rows['dataset']=[{'id':'line','name':'南北作业轨迹','source_type':'user','bbox':[114,30,114,31],'feature_count':1}]
        line=self.search.search('作业轨迹')['results'][0];self.assertEqual(line['bbox'],[114,30,114,31]);self.assertEqual(line['center'],[114,30.5])
    def test_actual_observation_date_is_searchable(self):
        self.production();r=self.search.search('2025-11-14');self.assertEqual(r['results'][0]['task_id'],'p1')
    def test_application_name_answer_title_and_original_text_index_and_dedupe(self):
        self.production();self.store.rows['assistant_question']=[{'id':'q-source','status':'answered','question':'数据来源是什么','capability_id':'source_quality','binding':{'type':'production','task_id':'p1'},'answer':{'map_effect':'none','title':'来源说明'}},{'id':'q-main','status':'answered','question':'原始水面光谱检查','binding':{'type':'production','task_id':'p1'},'application_ref':{'id':'water-app','name':'水体线索调查'},'answer':{'map_effect':'show','title':'水体调查成果A'}}]
        for query in ('水体线索调查','water-app','原始水面','调查成果A'):
            with self.subTest(query=query):
                hits=[r for r in self.search.search(query)['results'] if r['kind']=='result'];self.assertEqual(len(hits),1);self.assertEqual(hits[0]['question_id'],'q-main');self.assertEqual(hits[0]['task_id'],'p1')
        self.assertEqual(len(self.search.search('MNDWI')['results']),1)
    def test_failed_unfinished_and_unlocated_results_are_not_success_hits(self):
        for status in ('failed','running','queued','cancelled'):self.production(status,status=status)
        no_location=self.production('unlocated');del no_location['result']['provenance']['bbox'];self.assertEqual(self.search.search('MNDWI')['results'],[])
    def test_landcover_and_user_analysis_have_typed_refs(self):
        self.store.rows['landcover_task']=[{'id':'lc1','status':'succeeded','plan':{'requirement':'仙桃全市耕作地分类统计'},'result':{'summary':{},'provenance':{'year':2021,'source_type':'public_satellite_pixels','boundary_source':{'bbox':[113,30,114,31]}}}}]
        hit=next(r for r in self.search.search('耕作地')['results'] if r['kind']=='result');self.assertEqual(hit['result_ref']['image_kind'],'cropland');self.assertEqual(hit['task_type'],'landcover')
        self.store.rows['analysis']=[{'id':'an1','status':'succeeded','geojson':{'type':'FeatureCollection','features':[{'type':'Feature','geometry':{'type':'Point','coordinates':[114.2,30.5]},'properties':{}}]},'summary':{},'provenance':{'dataset_name':'我的作业地块','source_type':'user','source':'用户导入'}}]
        hit=self.search.search('作业地块')['results'][0];self.assertEqual(hit['task_type'],'analysis');self.assertEqual(hit['result_ref']['url'],'/api/analysis/an1');self.assertEqual(hit['center'],[114.2,30.5])
    def test_shared_lc_task_restores_the_question_matching_search_intent(self):
        self.store.rows['landcover_task']=[{'id':'lc1','status':'succeeded','plan':{'requirement':'仙桃全市分类统计'},'result':{'summary':{},'provenance':{'year':2021,'source_type':'public_satellite_pixels','boundary_source':{'bbox':[113,30,114,31]}}}}]
        self.store.rows['assistant_question']=[{'id':'q-composition','status':'answered','question':'土地覆盖构成','capability_id':'landcover_composition','binding':{'type':'landcover','task_id':'lc1'},'answer':{'map_effect':'show','title':'仙桃土地覆盖构成','result_ref':{'image_kind':'classes'}}},{'id':'q-water','status':'answered','question':'永久水体面积是多少','capability_id':'water_area','binding':{'type':'landcover','task_id':'lc1'},'answer':{'map_effect':'show','title':'仙桃永久水体参考','result_ref':{'image_kind':'classes'}}}]
        hit=self.search.search('永久水体')['results'][0];self.assertEqual(hit['question_id'],'q-water');self.assertEqual(hit['label'],'仙桃永久水体参考');self.assertEqual(hit['result_ref']['image_kind'],'classes')
    def test_user_dataset_and_feature_name_are_searchable_fixture_not_included(self):
        self.store.rows['dataset']=[{'id':'user1','name':'巡查点台账','source_type':'user','bbox':[114.2,30.5,114.2,30.5],'feature_count':1,'geojson':{'type':'FeatureCollection','features':[{'type':'Feature','geometry':{'type':'Point','coordinates':[114.2,30.5]},'properties':{'name':'东湖核查点'}}]}},{'id':'fixture','name':'合成数据','source_type':'synthetic','bbox':[113,30,114,31]}]
        for query in ('巡查点','东湖核查点'):
            hit=self.search.search(query)['results'][0];self.assertEqual(hit['kind'],'dataset');self.assertEqual(hit['dataset_id'],'user1');self.assertEqual(hit['center'],[114.2,30.5]);self.assertIn('用户导入',hit['subtitle'])
        self.assertEqual(self.search.search('合成数据')['results'],[])
    def test_legacy_null_answer_and_application_reference_do_not_crash(self):
        self.production();self.store.rows['assistant_question']=[{'id':'legacy','status':'answered','question':'old','binding':{'type':'production','task_id':'p1'},'application_ref':None,'answer':None}]
        self.assertEqual(self.search.search('MNDWI')['results'][0]['task_id'],'p1')

class AtlasSearchIntegrationTest(unittest.TestCase):
    """Real Atlas discovery with temporary manifests and delivery files."""
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        self.store=Store();self.store.agriculture_atlas=AgricultureAtlas(self.root);self.search=SearchServices(self.store)
    def tearDown(self):self.temp.cleanup()
    def product(self,ident='xiantao-spectral-fixture',title='仙桃植被光谱观察',**updates):
        folder=self.root/ident;folder.mkdir()
        # Search verifies the file contract and bounds; it does not decode PNGs.
        (folder/'overlay.png').write_bytes(b'local search fixture, not satellite imagery')
        value={'id':ident,'title':title,'status':'ready','schema_version':'agriculture-atlas-1.0','period_label':'实际观测2026-10-07','layers':[{'id':'raster','kind':'raster','image':{'url':'overlay.png','bounds':[[30.1,112.9],[30.6,113.9]]}}],'artifacts':['overlay.png'],'provenance':{}}
        value.update(updates);(folder/'result.json').write_text(json.dumps(value,ensure_ascii=False));return value
    def atlas_hits(self,query):return [h for h in self.search.search(query,remote=False)['results'] if h.get('task_type')=='agriculture_atlas']
    def test_title_id_period_and_source_date_return_typed_raster_result(self):
        self.product()
        for query in ('植被光谱观察','xiantao-spectral-fixture','实际观测2026-10-07','2026-10-07'):
            with self.subTest(query=query):
                hit=self.atlas_hits(query)[0];self.assertEqual(hit['kind'],'result');self.assertEqual(hit['task_id'],'xiantao-spectral-fixture');self.assertEqual(hit['result_ref'],{'type':'agriculture_atlas','task_id':'xiantao-spectral-fixture','url':'/api/agriculture-atlas/xiantao-spectral-fixture'});self.assertEqual(hit['bbox'],[112.9,30.1,113.9,30.6]);self.assertEqual(hit['center'],[113.4,30.35]);self.assertIn('实际观测2026-10-07',hit['subtitle'])
    def test_dates_are_actual_or_published_not_fetch_times_or_invalid_calendar_dates(self):
        self.product(period_label='2026秋季单景',created_at='2026-11-15T01:00:00Z',provenance={'actual_dates':['2026-10-07T02:56:20.739885Z','2026-02-30'],'fetched_at':'2026-11-15T01:00:00Z','sources':[{'published_at':'2026-03-30'}]})
        for query in ('2026-10-07','2026-03-30'):
            hit=self.atlas_hits(query)[0];self.assertIn(query,hit['subtitle'])
        self.assertEqual(self.atlas_hits('2026-11-15'),[]);self.assertEqual(self.atlas_hits('2026-02-30'),[])
    def test_nested_scene_observation_and_annual_reference_year_are_searchable(self):
        self.product(period_label='秋季双期参考',provenance={'scenes':{'before':{'scene':{'datetime':'2026-09-13T02:00:00Z'}},'after':{'scene':{'datetime':'2026-10-07T02:00:00Z'}}}})
        self.product('xiantao-annual-fixture','耕作地年度参考',period_label='历史类别产品',summary={'reference_year':2024})
        self.assertEqual(self.atlas_hits('2026-09-13')[0]['task_id'],'xiantao-spectral-fixture');self.assertEqual(self.atlas_hits('2024')[0]['task_id'],'xiantao-annual-fixture')
    def test_geojson_location_uses_delivered_features_not_metadata_bbox(self):
        geo={'type':'FeatureCollection','features':[{'type':'Feature','geometry':{'type':'Point','coordinates':[113.1,30.2]},'properties':{}},{'type':'Feature','geometry':{'type':'Point','coordinates':[113.5,30.4]},'properties':{}}]}
        self.product('xiantao-location-fixture','养殖地址候选',layers=[{'kind':'geojson','geojson':geo}],provenance={'bbox':[100,10,150,70]})
        hit=self.atlas_hits('养殖地址')[0];self.assertEqual(hit['bbox'],[113.1,30.2,113.5,30.4]);self.assertAlmostEqual(hit['center'][0],113.3);self.assertAlmostEqual(hit['center'][1],30.3)
    def test_notice_without_geometry_remains_searchable_and_has_no_navigation_fields(self):
        self.product('xiantao-notice-fixture','仙桃高标准农田计划公告',period_label='公告2026-03-30',layers=[],provenance={'bbox':[112,30,114,31],'sources':[{'published_at':'2026-03-30'}]})
        hit=self.atlas_hits('计划公告')[0]
        for key in ('center','bbox','zoom'):self.assertNotIn(key,hit)
        self.assertIn('无空间边界',hit['subtitle']);self.assertIn('公告2026-03-30',hit['subtitle']);self.assertEqual(self.atlas_hits('2026-03-30')[0]['task_id'],'xiantao-notice-fixture')
    def test_exact_reference_city_stays_first_even_when_product_title_is_city_name(self):
        self.product(title='仙桃')
        hits=self.search.search('仙桃',remote=False)['results'];self.assertEqual(hits[0]['kind'],'place');self.assertEqual(hits[0]['place_id'],'1790371');self.assertTrue(any(h.get('task_type')=='agriculture_atlas' for h in hits))
    def test_unknown_partial_failed_running_and_broken_products_do_not_appear(self):
        for status in ('partial','failed','running','unknown'):self.product('xiantao-'+status+'-fixture','坏成果共同词',status=status)
        self.product('xiantao-refresh-fixture','坏成果共同词');(self.root/'xiantao-refresh-fixture/status.json').write_text('{"status":"running"}')
        self.product('xiantao-bounds-fixture','坏成果共同词',layers=[{'kind':'raster','image':{'url':'overlay.png','bounds':[[300,112],[301,114]]}}])
        self.product('xiantao-missing-fixture','坏成果共同词');(self.root/'xiantao-missing-fixture/overlay.png').unlink()
        self.product('xiantao-json-fixture','坏成果共同词');(self.root/'xiantao-json-fixture/result.json').write_text('{broken')
        self.product('xiantao-geometry-fixture','坏成果共同词',layers=[{'kind':'geojson','geojson':{'type':'FeatureCollection','features':[]}}])
        self.assertEqual(self.atlas_hits('坏成果共同词'),[]);self.assertEqual(self.atlas_hits('xiantao-not-present'),[])
    def test_generic_explanations_and_source_names_do_not_match_every_product(self):
        self.product(limitations=['遥感专业通用说明词'],scope_label='官方参考范围专用词',provenance={'sources':[{'title':'泛化提供方名称'}]})
        for query in ('遥感专业通用说明词','官方参考范围专用词','泛化提供方名称'):
            with self.subTest(query=query):self.assertEqual(self.atlas_hits(query),[])
    def test_exact_declared_spectral_index_survives_twenty_historical_tasks(self):
        for index in ('ndvi','ndmi','mndwi'):
            self.product('xiantao-'+index+'-fixture','仙桃植被观测'+index.upper(),product_type='city_spectral_reference',summary={'index_id':index})
            for i in range(20):
                self.store.rows.setdefault('production_task',[]).append({'id':index+'-old-'+str(i),'status':'succeeded','plan':{'requirement':'原选区光谱分析'},'result':{'summary':{'index_id':index,'index':{'label':index.upper()}},'provenance':{'bbox':[114.2,30.5,114.21,30.51],'source_type':'public_satellite_pixels'}}})
            hits=self.search.search(index.upper(),remote=False)['results'];self.assertEqual(len(hits),12);self.assertEqual(hits[0]['task_type'],'agriculture_atlas');self.assertEqual(hits[0]['task_id'],'xiantao-'+index+'-fixture')
        self.product('xiantao-not-spectral-fixture','仅项目公告',layers=[],summary={'index_id':'ndvi'},product_type='official_project_notice')
        self.assertNotIn('xiantao-not-spectral-fixture',[h['task_id'] for h in self.atlas_hits('NDVI')]);self.assertEqual(self.search.search('仙桃',remote=False)['results'][0]['kind'],'place')
    def test_xiantao_city_and_year_title_equivalents_do_not_match_other_city(self):
        title='仙桃2026高标准农田项目公告';self.product('xiantao-notice-fixture',title,layers=[],period_label='公告2026-03-30')
        hit=self.atlas_hits('仙桃市2026年高标准农田')[0];self.assertEqual(hit['task_id'],'xiantao-notice-fixture');self.assertEqual(hit['label'],title)
        self.assertEqual(self.atlas_hits('武汉市2026年高标准农田'),[])

if __name__=='__main__':unittest.main()
