"""Bounded archive reads, immutable source binding, and no scope substitution."""
import copy
import unittest
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

from backend.agriculture.agri_series_read import VERSION, prepare_series_read, read_series_product
from backend.core.geo_services import GeoError


def series(year=2026, region='仙桃市'):
    ident='isolated-series-'+str(year)
    observations=[];mapping={}
    for n,day in enumerate((str(year)+'-05-16',str(year)+'-08-04',str(year)+'-10-07')):
        scene='LC08-unit-'+day
        product='unit-ndvi-'+day
        sha=str(n+1)*64
        observations.append({'scene_id':scene,'date':day,'platform':'landsat-8',
            'ndvi_mean':0 if n==2 else .3+n*.05,'ndmi_mean':.15+n*.02,
            'ndvi_valid_pixel_count':100,'ndmi_valid_pixel_count':99,
            'product_id':product,'product_sha256':sha})
        mapping[scene]={'product_id':product,'product_sha256':sha}
    return {'id':ident,'status':'ready','series_sha256':'a'*64,
            'scope':{'region':region,'boundary_year':2017},
            'period':{'start':str(year)+'-05-01','end':str(year)+'-10-31'},
            'observations':observations,'changes':[],
            'provenance':{'publication':{'observations':mapping}},
            'limitations':['Representative observations; no new calculations.']}


class Research:
    def __init__(self,values):self.values={row['id']:row for row in values};self.calls=[]
    def catalog(self):return {'series':[{'id':key} for key in self.values]}
    def get(self,ident,expected=None):
        self.calls.append((ident,expected))
        if ident not in self.values:raise GeoError('SERIES_NOT_FOUND','no isolated series',404)
        value=self.values[ident]
        if expected is not None and expected!=value['series_sha256']:raise GeoError('SERIES_VERSION_CHANGED','isolated version changed',409)
        return copy.deepcopy(value)


class Atlas:
    def __init__(self,values):
        self.calls=[];self.values={}
        for value in values:
            for item in value['provenance']['publication']['observations'].values():
                self.values[item['product_id']]={'id':item['product_id'],'product_sha256':item['product_sha256'],
                    'status':'ready','summary':{'index_id':'ndvi'},'untouched_marker':['original product']}
    def require_version(self,ident,expected):
        self.calls.append((ident,expected))
        if ident not in self.values:raise GeoError('ATLAS_NOT_FOUND','isolated map missing',404)
        value=self.values[ident]
        if expected!=value['product_sha256']:raise GeoError('ATLAS_VERSION_CHANGED','isolated atlas changed',409)
        return value


def store(*values):
    values=list(values) or [series()]
    return SimpleNamespace(agri_research=Research(values),agriculture_atlas=Atlas(values))


class SeriesReadTest(unittest.TestCase):
    def setUp(self):self.store=store()
    def plan(self,question='查看仙桃全市已有观测序列',context=None):
        return prepare_series_read(self.store,question,{} if context is None else context)
    def assertBlocked(self,question,context=None):
        result=self.plan(question,context)
        self.assertIsNotNone(result,question);self.assertEqual(result['status'],'needs_data',question)
        self.assertFalse(result['executable']);self.assertTrue(result['blocking_gaps'])
        self.assertEqual(result['intent'],'agri_series_read')
        return result

    def test_prepares_existing_archive_and_freezes_latest_valid_observation(self):
        before=copy.deepcopy(self.store.agri_research.values)
        result=self.plan()
        self.assertEqual(result['status'],'ready');self.assertTrue(result['executable'])
        self.assertEqual(result['version'],VERSION);self.assertFalse(result['force'])
        self.assertEqual(result['tool']['id'],'agri_series_read')
        inputs=result['tool']['inputs']
        self.assertEqual(set(inputs),{'series_id','series_sha256','product_id','product_sha256'})
        self.assertEqual(inputs['product_id'],'unit-ndvi-2026-10-07')
        self.assertEqual(result['source_snapshot']['research_series']['observations'][-1]['ndvi_mean'],0)
        self.store.agri_research.values[inputs['series_id']]['observations'][-1]['ndvi_mean']=.8
        self.assertEqual(result['source_snapshot']['research_series']['observations'][-1]['ndvi_mean'],0)
        self.assertEqual(before[inputs['series_id']]['observations'][-1]['ndvi_mean'],0)

    def test_context_region_and_explicit_city_override_allow_a_whole_city_read(self):
        result=self.plan('查看全市长势曲线',{'region':'仙桃市','bbox':[113,30,113.1,30.1]})
        self.assertTrue(result['executable'])
        self.assertBlocked('查看仙桃长势曲线',{'region':'仙桃市','bbox':[113,30,113.1,30.1]})
        self.assertBlocked('看看时序')
        self.assertBlocked('查看仙桃市时序',{'region':'武汉市'})
        self.assertBlocked('查看仙桃市和武汉市的时序')
        self.assertBlocked('查看仙桃市沿江时序')
        self.assertBlocked('查看全省长势曲线',{'region':'仙桃市'})

    def test_water_curve_keeps_ndmi_metrics_but_uses_declared_ndvi_map(self):
        for question,context in [('查看仙桃水分曲线',{}),('查看仙桃时序',{'index_id':'ndmi'})]:
            result=self.plan(question,context);self.assertTrue(result['executable'])
            self.assertEqual(result['source_snapshot']['requested_index'],'ndmi')
            self.assertEqual(result['tool']['inputs']['product_id'],'unit-ndvi-2026-10-07')
        self.assertBlocked('查看仙桃NDVI水分曲线')
        self.assertBlocked('查看仙桃EVI时序')
        self.assertBlocked('查看仙桃长势曲线',{'index_id':'ndmi'})

    def test_exact_year_and_complete_window_can_match_without_silent_filtering(self):
        for question in ['查看仙桃2026年时序','查看仙桃2026年5—10月观测序列',
                         '查看仙桃2026年5月至10月长势曲线',
                         '查看仙桃2026-05-01至2026-10-31长势曲线',
                         '查看仙桃2026-05至2026-10观测序列']:
            result=self.plan(question);self.assertTrue(result['executable'],(question,result))
        self.assertTrue(self.plan(context={'period':{'start':'2026-05-01','end':'2026-10-31'}})['executable'])
        for question in ['查看仙桃2025年时序','查看仙桃2026年6月至10月时序',
                         '查看仙桃2026年10月时序','查看仙桃2026年10月7日时序',
                         '查看仙桃2026-10-07时序','查看仙桃5—10月时序',
                         '查看仙桃2026年五到十月时序','查看仙桃2026年全年时序',
                         '查看仙桃2026年逐月观测序列','查看仙桃今年时序']:
            self.assertBlocked(question)
        self.assertBlocked('查看仙桃2026年5—10月时序',{'period':{'start':'2026-06-01','end':'2026-10-31'}})

    def test_dynamic_catalog_matches_requested_old_year_instead_of_newest(self):
        self.store=store(series(2025),series(2026))
        result=self.plan('查看仙桃2025年观测序列')
        self.assertTrue(result['executable']);self.assertEqual(result['tool']['inputs']['series_id'],'isolated-series-2025')
        latest=self.plan();self.assertEqual(latest['tool']['inputs']['series_id'],'isolated-series-2026')

    def test_explicit_sources_and_context_bindings_are_not_replaced(self):
        for question in ['用Sentinel-2查看仙桃时序','用Landsat 9查看仙桃时序','查看仙桃天地图时序']:
            self.assertBlocked(question)
        self.assertTrue(self.plan('查看仙桃Landsat 8时序',{'source_id':'pc_landsat_c2_l2'})['executable'])
        for context in [{'source_id':'pc_sentinel2_l2a'},{'source_type':'production','source_task_id':'other'},
                        {'source_task_id':'other'},{'observation_id':'explicit-scene'},{'dataset_id':'uploaded-fields'},
                        {'source_type':'agriculture_atlas'},{'source_type':'agriculture_atlas','source_task_id':'old-map'},
                        {'periods':{}},{'threshold':.2},{'unknown_scope':'must not be ignored'}]:
            self.assertBlocked('查看仙桃全市时序',context)
        inputs=self.plan()['tool']['inputs']
        context={'source_type':'agriculture_atlas','source_task_id':inputs['product_id'],
                 'atlas_product_sha256':inputs['product_sha256']}
        self.assertTrue(self.plan(context=context)['executable'])
        context['atlas_product_sha256']='b'*64;self.assertBlocked('查看仙桃时序',context)

    def test_mixed_landsat_platforms_cannot_be_read_as_a_same_source_sequence(self):
        value=self.store.agri_research.values['isolated-series-2026']
        value['observations'][0]['platform']='landsat-9'
        result=self.assertBlocked('查看仙桃全市时序')
        self.assertIn('单一Landsat平台',result['clarification_message'])

    def test_analysis_and_unrelated_questions_return_none_for_registered_other_tools(self):
        for question in ['统计仙桃时序均值','合成仙桃时序','仙桃OLS时序趋势','重算仙桃长势曲线',
                         '重新生成仙桃观测序列','识别仙桃时序中的水稻','预测仙桃长势曲线','查仙桃农田面积']:
            self.assertIsNone(self.plan(question),question)
        self.assertIsNone(prepare_series_read(SimpleNamespace(),'查看仙桃时序',{}))
        self.assertBlocked('查看仙桃农田长势曲线')
        self.assertBlocked('查看仙桃全市时序平均值')

    def test_no_archive_no_curve_and_wrong_actual_region_are_explained(self):
        self.store.agri_research.values={};self.assertBlocked('查看仙桃时序')
        self.store=store(series(region='武汉市'));self.assertBlocked('查看仙桃时序')
        self.store=store();value=self.store.agri_research.values['isolated-series-2026']
        value['observations'][0]['ndmi_mean']=None;value['observations'][1]['ndmi_mean']=None
        self.assertBlocked('查看仙桃水分曲线')

    def test_latest_valid_map_is_selected_but_missing_publication_never_falls_back(self):
        value=self.store.agri_research.values['isolated-series-2026']
        value['observations'][-1]['ndvi_valid_pixel_count']=0
        result=self.plan();self.assertEqual(result['tool']['inputs']['product_id'],'unit-ndvi-2026-08-04')
        value['observations'][-1]['ndvi_valid_pixel_count']=100
        value['provenance']['publication']['observations'].pop(value['observations'][-1]['scene_id'])
        self.assertBlocked('查看仙桃时序')

    def test_planning_and_execution_require_original_atlas_fingerprint(self):
        result=self.plan();inputs=result['tool']['inputs']
        product=self.store.agriculture_atlas.values[inputs['product_id']]
        self.assertIs(read_series_product(self.store,inputs),product)
        self.assertEqual(set(product),{'id','product_sha256','status','summary','untouched_marker'})
        self.assertEqual(self.store.agri_research.calls[-1],(inputs['series_id'],inputs['series_sha256']))
        product['product_sha256']='b'*64
        self.assertBlocked('查看仙桃时序')
        with self.assertRaises(GeoError) as error:read_series_product(self.store,inputs)
        self.assertEqual(error.exception.code,'ATLAS_VERSION_CHANGED')

    def test_series_or_latest_observation_mapping_changes_fail_execution(self):
        inputs=self.plan()['tool']['inputs'];value=self.store.agri_research.values[inputs['series_id']]
        value['series_sha256']='b'*64
        with self.assertRaises(GeoError) as error:read_series_product(self.store,inputs)
        self.assertEqual(error.exception.code,'SERIES_VERSION_CHANGED')
        value['series_sha256']=inputs['series_sha256']
        latest=value['observations'][-1];latest['product_id']='different-map'
        value['provenance']['publication']['observations'][latest['scene_id']]['product_id']='different-map'
        with self.assertRaises(GeoError) as error:read_series_product(self.store,inputs)
        self.assertEqual(error.exception.code,'SERIES_READ_MAPPING_CHANGED')
        with self.assertRaises(GeoError):read_series_product(self.store,{**inputs,'index_id':'ndmi'})

    def test_answer_and_source_download_followups_preserve_both_frozen_version_contracts(self):
        from backend.workflows.execution_answers import build_answer
        from backend.agriculture.regional_product_tools import product_fingerprint
        research=series();research.update(period_label='2026-05-01 — 2026-10-31',
            scope_label='仙桃全市参考市界',quality_comparison={'dates':['2026-08-04','2026-10-07'],'common_support_ratio':0})
        research['downloads']=[{'label':'观测与来源记录','type':'json',
            'url':'/api/agri-research/series/'+research['id']+'/files/result.json?series_sha256='+research['series_sha256']}]
        product={'id':'unit-map','status':'ready','title':'原始最新NDVI地图',
            'product_type':'city_spectral_reference','period_label':'2026-10-07',
            'scope_label':'仙桃参考市界','layers':[{'id':'unit-map-layer'}],
            'summary':{'index_id':'ndvi','actual_dates':['2026-10-07']},
            'summary_metrics':[{'label':'NDVI','value':0,'unit':'指数'}],
            'downloads':[{'label':'地图来源','url':'/api/agriculture-atlas/unit-map/files/result.json','type':'json'}],
            'provenance':{'actual_dates':['2026-10-07'],'boundary_year':2017},'limitations':['归档地图不代表实时情况。']}
        product['product_sha256']=product_fingerprint(product)
        fingerprint=product['product_sha256']
        job={'id':'unit-execution','result':product,
            'binding':{'type':'agriculture_atlas','task_id':product['id'],'product_sha256':fingerprint},
            'intent':'agri_series_read','summary':'read frozen archive',
            'result_ref':{'type':'agriculture_atlas','task_id':product['id'],'product_sha256':fingerprint,
                          'url':'/api/agriculture-atlas/unit-map'},
            'plan_snapshot':{'source_snapshot':{'research_series':research,'requested_index':'ndmi'}}}
        answer=build_answer({'id':'unit-question','question':'查看仙桃水分曲线'},job)
        self.assertIn('地图显示最近有效观测的NDVI',answer['summary'])
        self.assertIn('水分情况见NDMI曲线',answer['summary'])
        self.assertEqual(answer['provenance']['requested_index'],'ndmi')
        self.assertEqual(answer['product_sha256'],fingerprint)
        self.assertEqual(answer['atlas_product_snapshot'],product)
        self.assertEqual(answer['research_series'],research)
        self.assertEqual(next(v for v in answer['metrics'] if v['id']=='comparison_support')['value'],0)
        atlas_download,series_download=answer['downloads']
        self.assertEqual(parse_qs(urlsplit(atlas_download['url']).query),{'product_sha256':[fingerprint]})
        self.assertEqual(parse_qs(urlsplit(series_download['url']).query),{'series_sha256':[research['series_sha256']]})
        for intent in ('source_quality','download_results'):
            followup=copy.deepcopy(job);followup['intent']=intent;followup['summary']='original frozen source'
            follow=build_answer({'id':'unit-followup','question':'来源和下载'},followup)
            self.assertEqual(follow['research_series'],research)
            self.assertEqual(follow['downloads'],answer['downloads'])
            self.assertEqual(follow['map_effect'],'none')
            self.assertEqual(follow['provenance']['series_sha256'],research['series_sha256'])
        job['plan_snapshot']['source_snapshot']['research_series']['observations'][-1]['ndmi_mean']=999
        self.assertNotEqual(answer['research_series']['observations'][-1]['ndmi_mean'],999)


if __name__=='__main__':unittest.main()
