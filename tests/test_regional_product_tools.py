"""Isolated regional archive lookup and immutable follow-up integration tests."""
import copy
import hashlib
import json
import tempfile
import threading
import time
import unittest
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request,urlopen
from unittest.mock import patch
from backend.server import Workspace,LocalServer,make_handler
from backend.agriculture.regional_product_tools import product_fingerprint,read_product,explicit_dates
from backend.core.geo_services import GeoError


class RegionalProductsTest(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name);self.w=Workspace(self.root/'workspace.sqlite3')
        self.crop=self.product('xiantao-2024-cropland','annual_classification','2024年度产品',2024,[],1500)
        self.ndvi=self.product('xiantao-2026-spectral-ndvi','city_spectral_reference','实际观测 2026-10-07',2026,['2026-10-07T02:00:00Z'],0)
        self.change=self.product('xiantao-2026-spectral-change','city_spectral_reference','实际观测 2026-09-13 → 2026-10-07',2026,['2026-09-13T02:00:00Z','2026-10-07T02:00:00Z'],-.1)
        self.notice=self.product('xiantao-high-standard-2026','official_project_notice','公告2026-03-30',2026,[],12,False)
        self.poi=self.product('xiantao-poi-supplies','public_address_clues','检索时间2026-11-15',2026,[],2)
    def tearDown(self):self.w.close();self.temp.cleanup()
    def product(self,ident,kind,period,year,dates,value,spatial=True):
        folder=self.root/'agriculture-atlas'/ident;folder.mkdir(parents=True)
        layer={'id':'view','kind':'geojson','geojson':{'type':'FeatureCollection','features':[{'type':'Feature','properties':{'test_fixture':'isolated only'},'geometry':{'type':'Point','coordinates':[113.4,30.3]}}]}}
        raw={'schema_version':'agriculture-atlas-1.0','id':ident,'title':'Isolated test '+ident,'status':'ready','product_type':kind,'scope_label':'仙桃全市2017公开参考市界（隔离测试）','period_label':period,'resolution_m':30 if dates else None,'summary_metrics':[{'label':'Crops类别参考面积' if ident==self.crop_id else '测试指标','value':value,'unit':'亩' if ident==self.crop_id else '指数','precision':1 if ident==self.crop_id else 6}],'summary':{'reference_year':year,'actual_dates':dates,'index_id':'ndvi' if dates else None},'provenance':{'source_id':'pc_landsat_c2_l2' if dates else 'io_lulc_2024','actual_dates':dates,'boundary_year':2017,'sources':[{'title':'Isolated fixture only','url':'https://example.invalid/fixture'}]},'limitations':['仅临时测试，未写入正式业务库。'],'layers':[layer] if spatial else [],'artifacts':[]}
        (folder/'result.json').write_text(json.dumps(raw,ensure_ascii=False));return raw
    crop_id='xiantao-2024-cropland'
    def plan(self,text,context=None):return self.w.executions.prepare(text,context or {})
    def ask(self,text,context=None):
        q=self.w.assistant.create({'question':text,'context':context or {}})
        for _ in range(300):
            r=self.w.assistant.get(q['id'])
            if r['status']!='processing':return r
            time.sleep(.01)
        self.fail('archive question did not settle')
    def test_city_2024_and_unspecified_year_choose_explicit_2024_archive(self):
        for text in ['仙桃全市2024年农田参考面积有多少','仙桃全市农田有多少亩']:
            plan=self.plan(text);self.assertTrue(plan['executable'],plan);self.assertEqual(plan['tool']['id'],'regional_product_read');self.assertEqual(plan['tool']['inputs']['product_id'],self.crop_id)
    def test_main_area_or_distribution_request_is_not_hidden_by_appended_source(self):
        for text in ['请查看仙桃市2024年耕作地参考面积，并展示分布和来源。',
                     '仙桃全市2024年农田参考面积有多少，并说明来源',
                     '请说明仙桃2024年耕作地参考成果来源，同时展示分布',
                     '查看仙桃2024年耕作地参考面积和来源',
                     '查看仙桃2024年耕作地参考面积与来源']:
            plan=self.plan(text)
            self.assertTrue(plan['executable'],plan)
            self.assertEqual(plan['intent'],'regional_product_read',text)
            question=self.ask(text)
            self.assertEqual(question['status'],'answered',question)
            self.assertEqual(question['capability_id'],'regional_product_read',text)
            self.assertEqual(question['answer']['map_effect'],'show',text)
            self.assertEqual(question['answer']['metrics'][0]['value'],1500)
            self.assertEqual(question['answer']['result_ref']['task_id'],self.crop_id)
            self.assertTrue(question['answer']['atlas_product_snapshot']['provenance']['sources'])
    def test_reference_only_named_area_or_distribution_keeps_reference_focus(self):
        for text,intent in [('查看仙桃2024年耕作地参考面积的数据来源','source_quality'),
                            ('说明仙桃2024年耕作地分布图的来源','source_quality'),
                            ('下载仙桃2024年耕作地参考面积成果','download_results')]:
            plan=self.plan(text)
            self.assertTrue(plan['executable'],plan)
            self.assertEqual(plan['intent'],intent,text)
            question=self.ask(text)
            self.assertEqual(question['status'],'answered',question)
            self.assertEqual(question['answer']['map_effect'],'none')
    def test_appended_source_does_not_bypass_region_year_or_legal_constraints(self):
        for text in ['请查看武汉市2024年耕作地参考面积，并展示分布和来源。',
                     '请查看仙桃市2025年耕作地参考面积，并展示分布和来源。',
                     '请查看仙桃市2024年官方耕地面积，并展示分布和来源。',
                     '请查看仙桃市张沟镇2024年耕作地参考面积，并展示分布和来源。']:
            plan=self.plan(text)
            self.assertFalse(plan['executable'],text)
            self.assertTrue(plan['blocking_gaps'],text)
    def test_explicit_2021_preserves_original_product_route(self):
        with patch('backend.workflows.execution_services.load_boundary',return_value=({}, {'sha256':'b'*64,'bbox':[113,30,114,31]})):
            plan=self.plan('仙桃全市2021年农田参考面积有多少')
        self.assertNotEqual((plan.get('tool') or {}).get('id'),'regional_product_read')
    def test_full_city_read_must_not_substitute_selected_area_or_other_region(self):
        for context in [{'bbox':[113.4,30.3,113.41,30.31]},{'dataset_id':'a'},{'region':'武汉市'}]:
            plan=self.plan('仙桃全市2024年农田面积有多少',context);self.assertFalse(plan['executable']);self.assertEqual(plan['status'],'needs_data')
        self.assertFalse(self.plan('湖北全省2024年农田面积，包括仙桃')['executable'])
    def test_named_town_village_or_subdistrict_never_receives_city_metrics(self):
        for text in ['仙桃市张沟镇2024年耕作地有多少亩','仙桃彭场镇2024年农田有多少亩','仙桃2024年某村农田有多少亩','仙桃干河街道2024年农田面积','仙桃2026年张沟镇NDVI均值']:
            plan=self.plan(text);self.assertFalse(plan['executable'],text);self.assertIn('全市',' '.join(plan['blocking_gaps']))
        for text in ['仙桃张沟镇农资门店地址线索在哪里','仙桃张沟镇农资门店地址有多少','仙桃市张沟镇农资经营部地址在哪里','仙桃彭场镇农机维修门店地址']:
            self.assertFalse(self.plan(text)['executable'],text)
        self.assertTrue(self.plan('查看已归档的仙桃全市农资候选地址，来源含张沟镇名称')['executable'])
    def test_service_locations_are_not_device_positions_or_business_metrics(self):
        for text in ['仙桃农机设备位置分布','仙桃农机北斗位置在哪里','仙桃养殖面积分布','仙桃冷链容量分布']:
            self.assertFalse(self.plan(text)['executable'],text)
    def test_year_month_or_quarter_aggregation_never_reads_single_observation(self):
        for text in ['仙桃2026年全年NDVI平均值','仙桃2026年全年NDVI变化','仙桃2026年逐月NDVI平均值','仙桃2026年第一季度NDVI平均值','仙桃2026年逐日NDMI统计','仙桃2026年整个秋季NDVI平均']:
            self.assertFalse(self.plan(text)['executable'],text)
        self.assertTrue(self.plan('查看仙桃2026年NDVI归档')['executable'])
        self.assertTrue(self.plan('仙桃2024年全年农田参考面积')['executable'])
    def test_direction_urban_development_or_riparian_subarea_never_gets_city_total(self):
        for text in ['仙桃西部2024年农田参考面积是多少','仙桃经济开发区2024年农田参考面积是多少','仙桃城区2024年耕作地面积','仙桃沿江2024年农田面积','仙桃南部2026年10月7日NDVI均值']:
            plan=self.plan(text);self.assertFalse(plan['executable'],text);self.assertIn('全市',' '.join(plan['blocking_gaps']))
        self.assertTrue(self.plan('仙桃全市2024年农田参考面积是多少')['executable'])
        self.assertTrue(self.plan('仙桃全市2026年10月7日NDVI均值')['executable'])
    def test_class_share_question_cannot_be_answered_by_effective_coverage(self):
        for text in ['仙桃2024农田占比多少','仙桃全市2024年耕作地比例是多少','仙桃2024农田百分比是多少','仙桃2024农田覆盖率是多少']:
            plan=self.plan(text);self.assertFalse(plan['executable']);self.assertIn('有效覆盖率不是',' '.join(plan['blocking_gaps']))
        self.assertTrue(self.plan('仙桃全市2024年农田有效分类覆盖比例是多少')['executable'])
        self.assertTrue(self.plan('仙桃全市2024年农田有效分类覆盖率是多少')['executable'])
        water=self.product('xiantao-2024-water','annual_classification','2024年度产品',2024,[],12)
        water['summary_metrics']=[{'label':'水域参考面积','value':12,'unit':'公顷'},{'label':'水域占参考范围比例','value':42,'unit':'%'}];(self.root/'agriculture-atlas'/water['id']/'result.json').write_text(json.dumps(water))
        self.assertTrue(self.plan('仙桃全市2024年水域占比多少')['executable'])
    def test_official_survey_or_cadastral_area_is_not_public_classification(self):
        for text in ['仙桃全市2024年官方耕地面积是多少','仙桃全市2024年耕地调查面积是多少','仙桃全市2024年土地调查面积','仙桃全市三调耕地面积','仙桃全市2024年变更调查农田面积','仙桃全市2024年地籍农田面积']:
            plan=self.plan(text);self.assertFalse(plan['executable'],text);self.assertTrue(plan['blocking_gaps']);self.assertIsNone((plan.get('tool') or {}).get('id'))
            if text in ('仙桃全市2024年官方耕地面积是多少','仙桃全市2024年耕地调查面积是多少'):self.assertIn('调查',' '.join(plan['blocking_gaps']))
        self.assertTrue(self.plan('仙桃全市2024年耕作地参考面积是多少')['executable'])
        product=self.w.agriculture_atlas.get(self.crop_id);context={'source_type':'agriculture_atlas','source_task_id':self.crop_id,'atlas_product_sha256':product['product_sha256']}
        question=self.ask('这些来源是否官方',context);self.assertEqual(question['status'],'answered',question);self.assertEqual(question['answer']['map_effect'],'none');self.assertIn('不是官方耕地调查',question['answer']['summary'])
        follow=self.ask('这些来源是否官方',{'reference_question_id':question['id']});self.assertEqual(follow['status'],'answered');self.assertIn('不是官方耕地调查',follow['answer']['summary'])
        self.assertTrue(self.plan('查看仙桃2026年官方高标准农田项目公告')['executable'])
    def test_explicit_source_and_model_constraints_are_not_ignored(self):
        for context in [{'source_id':'pc_sentinel2_l2a'},{'observation_id':'other'},{'source_type':'production','source_task_id':'old'},{'source_task_id':'old'},{'cropland_product':'worldcover_2021_v200'},{'threshold':.5}]:
            self.assertFalse(self.plan('仙桃全市2024年农田参考面积有多少',context)['executable'],context)
        self.assertTrue(self.plan('仙桃全市2024年农田参考面积有多少',{'source_id':'io_lulc_2024','cropland_product':'io_lulc_2024'})['executable'])
    def test_single_observation_date_and_month_are_exact(self):
        self.assertTrue(self.plan('仙桃全市2026年10月7日NDVI是多少')['executable'])
        self.assertTrue(self.plan('仙桃全市2026年10月NDVI是多少')['executable'])
        for text in ['仙桃全市2026年10月31日NDVI是多少','仙桃全市2026年11月NDVI是多少','仙桃全市10月7日NDVI是多少','仙桃全市2026-10-31NDVI是多少','仙桃全市2026年13月7日NDVI是多少']:
            self.assertFalse(self.plan(text)['executable'],text)
    def test_archive_never_substitutes_another_index_or_uncomputed_statistic(self):
        for text in ['仙桃全市2026年10月7日NDBI和NDVI是多少','仙桃全市2026年10月7日NDBI植被指数是多少','仙桃全市2026年10月7日NDVI最大值是多少','仙桃全市2026年10月7日NDVI小于0.2的面积','仙桃全市2026年10月7日NDVI农田面积是多少','读取武汉NDVI']:
            plan=self.plan(text,{'source_type':'agriculture_atlas','source_task_id':self.ndvi['id']} if text=='读取武汉NDVI' else {});self.assertFalse(plan['executable'],text)
    def test_current_capabilities_describes_available_regional_years(self):
        question=self.ask('当前可以提供什么基线');self.assertEqual(question['status'],'answered');self.assertIn('2024',question['answer']['summary']);self.assertIn('2026',question['answer']['summary']);self.assertIsNone(question['answer']['source_year'])
    def test_explicit_periods_must_contain_real_dates_without_swapping_epochs(self):
        question='仙桃全市2026年NDVI变化是多少'
        good={'before':['2026-09-01','2026-09-30'],'after':['2026-10-01','2026-10-31']}
        self.assertTrue(self.plan(question,{'periods':good})['executable'])
        bad=copy.deepcopy(good);bad['after']=['2026-10-30','2026-10-31'];self.assertFalse(self.plan(question,{'periods':bad})['executable'])
        self.assertFalse(self.plan('仙桃全市2024农田面积',{'period':{'start':'2024-10-01','end':'2024-10-31'}})['executable'])
    def test_existing_archive_does_not_satisfy_recompute_or_legal_inference(self):
        for text in ['重新计算仙桃全市2024年农田面积','重跑仙桃全市2026年10月7日NDVI']:
            plan=self.plan(text);self.assertFalse(plan['executable']);self.assertIn('不会把读取归档当重算',' '.join(plan['blocking_gaps']))
        for text in ['仙桃全市2026年高标准农田实际面积','仙桃全市2024年永久基本农田面积','仙桃全市2026年粮食产量','仙桃全市2026年禁捕违法有多少','仙桃今年农田面积','仙桃全市2024年至2026年农田减少多少']:
            plan=self.plan(text);self.assertFalse(plan['executable'],text)
    def test_notice_is_readable_but_never_located_or_called_completed(self):
        q=self.ask('查看仙桃2026年高标准农田项目公告');self.assertEqual(q['status'],'answered',q);answer=q['answer'];self.assertEqual(answer['map_effect'],'none');self.assertEqual(answer['atlas_product_snapshot']['layers'],[]);self.assertEqual(answer['source_year'],2026);self.assertIn('读取已有真实成果',q['message']);self.assertNotIn('新计算完成',answer['summary'])
    def test_point_candidates_cannot_answer_total_inventory(self):
        self.assertTrue(self.plan('仙桃农资门店地址有哪些')['executable']);self.assertFalse(self.plan('仙桃全市所有农资门店地址和总数')['executable'])
    def test_product_hash_excludes_own_field_and_changes_with_content(self):
        product=self.w.agriculture_atlas.get(self.crop_id);self.assertEqual(product_fingerprint(product),product['product_sha256']);self.assertEqual(product_fingerprint({**product,'product_sha256':'0'*64}),product['product_sha256']);changed={**product,'title':'changed'};self.assertNotEqual(product_fingerprint(changed),product['product_sha256'])
    def test_detail_followup_uses_explicit_product_and_displayed_version(self):
        product=self.w.agriculture_atlas.get(self.crop_id);context={'source_type':'agriculture_atlas','source_task_id':self.crop_id,'atlas_product_sha256':product['product_sha256']}
        for text in ['这些数据来自哪里','下载这次成果','这些数据准吗','数据是哪一年的']:
            q=self.ask(text,context);self.assertEqual(q['status'],'answered',q);self.assertEqual(q['answer']['result_ref']['task_id'],self.crop_id);self.assertEqual(q['answer']['product_sha256'],product['product_sha256']);self.assertEqual(q['answer']['map_effect'],'none')
        folder=self.root/'agriculture-atlas'/self.crop_id;raw=json.loads((folder/'result.json').read_text());raw['title']='updated detail version';(folder/'result.json').write_text(json.dumps(raw))
        q=self.ask('这些数据来自哪里',context);self.assertEqual(q['status'],'needs_data');self.assertIsNone(q['answer'])
        self.assertFalse(self.plan('这些数据来自哪里',{'atlas_product_sha256':product['product_sha256']})['executable'])
        self.assertFalse(self.plan('分析仙桃全市2024年农田情况',context)['executable'])
        with self.assertRaises(GeoError):self.w.assistant.create({'question':'这些数据来自哪里','context':{**context,'atlas_product_sha256':'bad'}})
    def test_unbound_source_or_download_question_cannot_default_to_old_baseline(self):
        for text in ['这些数据来自哪里','下载这次成果','这些数据来自哪一年']:
            q=self.ask(text);self.assertEqual(q['status'],'needs_data');self.assertIsNone(q['answer']);self.assertIsNone(q['binding'])
    def test_version_change_after_planning_never_reads_newest(self):
        plan=self.plan('仙桃全市2024年农田面积');folder=self.root/'agriculture-atlas'/self.crop_id;raw=json.loads((folder/'result.json').read_text());raw['summary_metrics'][0]['value']=3000;(folder/'result.json').write_text(json.dumps(raw))
        with self.assertRaises(GeoError) as error:read_product(self.w,plan['tool']['inputs'])
        self.assertEqual(error.exception.code,'REGIONAL_PRODUCT_VERSION_CHANGED')
    def test_answer_freezes_zero_and_source_followup_keeps_original_after_change(self):
        first=self.ask('仙桃全市2026年10月7日NDVI是多少');self.assertEqual(first['status'],'answered',first);a=first['answer'];self.assertEqual(a['metrics'][0]['value'],0);self.assertEqual(a['source_year'],2026);self.assertIn('读取已有真实成果',a['summary']);self.assertIn('product_sha256=',a['downloads'][0]['url'])
        folder=self.root/'agriculture-atlas'/self.ndvi['id'];raw=json.loads((folder/'result.json').read_text());raw['summary_metrics'][0]['value']=.8;(folder/'result.json').write_text(json.dumps(raw))
        for text in ['这些数据来源是什么','下载原结果']:
            follow=self.ask(text,{'reference_question_id':first['id']});self.assertEqual(follow['status'],'answered',follow);self.assertEqual(follow['answer']['metrics'][0]['value'],0);self.assertEqual(follow['answer']['product_sha256'],a['product_sha256']);self.assertEqual(follow['answer']['map_effect'],'none')
        for text,context in [('2026年10月31日这些数据来源是什么',{}),('这些数据来源是什么',{'bbox':[113,30,113.01,30.01]}),('下载原结果',{'source_id':'pc_sentinel2_l2a'}),('下载原NDMI结果',{}),('计算这些数据的准确率',{}),('这些数据的精度是多少',{})]:
            follow=self.ask(text,{'reference_question_id':first['id'],**context});self.assertEqual(follow['status'],'needs_data');self.assertIsNone(follow['answer'])
    def test_archive_shortcut_stores_question_and_constraints_not_answer(self):
        first=self.ask('仙桃全市2024年农田参考面积有多少');shortcut=self.w.assistant.save_shortcut({'source_question_id':first['id']});self.assertEqual(shortcut['capability_id'],'regional_product_read');self.assertNotIn('answer',shortcut)
        again=self.w.assistant.create({'question':shortcut['question'],'capability_id':shortcut['capability_id'],'context':shortcut['context']});self.assertNotEqual(again['id'],first['id'])
    def test_temporary_real_http_question_detail_and_versioned_download(self):
        server=LocalServer(('127.0.0.1',0),make_handler(self.w));server.quiet=True;thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start();base='http://127.0.0.1:'+str(server.server_port)
        try:
            payload=json.dumps({'question':'仙桃全市2024年农田参考面积有多少'}).encode()
            q=json.load(urlopen(Request(base+'/api/assistant/questions',data=payload,headers={'Content-Type':'application/json'}),timeout=5))
            for _ in range(100):
                detail=json.load(urlopen(base+'/api/assistant/questions/'+q['id'],timeout=5))
                if detail['status']!='processing':break
                time.sleep(.01)
            self.assertEqual(detail['status'],'answered',detail);answer=detail['answer'];self.assertEqual(answer['result_ref']['type'],'agriculture_atlas')
            with urlopen(base+answer['downloads'][-1]['url'],timeout=5) as response:self.assertEqual(response.status,200)
            folder=self.root/'agriculture-atlas'/self.crop_id;raw=json.loads((folder/'result.json').read_text());raw['title']='changed archive';(folder/'result.json').write_text(json.dumps(raw))
            with self.assertRaises(HTTPError) as error:urlopen(base+answer['downloads'][-1]['url'],timeout=5)
            self.assertEqual(error.exception.code,409)
        finally:server.shutdown();server.server_close();thread.join(2)

if __name__=='__main__':unittest.main()
