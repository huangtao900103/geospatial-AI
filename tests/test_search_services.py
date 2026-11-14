"""Offline typed search checks; source points are navigation, never AOI masks."""
import copy
import tempfile
import unittest
from pathlib import Path
from search_services import SearchServices,coordinates,geo_bounds
from geo_services import GeoError

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

if __name__=='__main__':unittest.main()
