"""Real indexed geometry and persisted business transitions in temporary workspaces."""
import copy
import importlib.util
import json
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from backend.server import Workspace, LocalServer, make_handler
from backend.core.geo_services import GeoError


def polygon(w=113,s=30,e=113.01,n=30.01):
    return {'type':'Polygon','coordinates':[[[w,s],[e,s],[e,n],[w,n],[w,s]]]}
def feature(code,geo=None):
    return {'type':'Feature','properties':{'code':code,'name':'业务对象'+code,'status':'在建'},'geometry':geo or polygon()}
def collection(*features):return {'type':'FeatureCollection','features':list(features)}


@unittest.skipUnless(importlib.util.find_spec('shapely'),'requires declared Shapely dependency')
class AgricultureOperationsTest(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.w=Workspace(Path(self.tmp.name)/'workspace.sqlite3');self.s=self.w.agri_operations
    def tearDown(self):self.w.close();self.tmp.cleanup()
    def registry(self,kind='farmland'):
        return self.s.create_registry({'name':'隔离业务台账','kind':kind,'source':'明确测试输入，非正式业务资料','year':2026,'actor':'测试录入'})
    def batch(self,reg,*features,batch_id='batch-1'):
        current=next(r for r in self.s.registries() if r['id']==reg['id'])
        return self.s.import_batch(reg['id'],{'geojson':collection(*features),'code_field':'code','name_field':'name','status_field':'status','batch_id':batch_id,'expected_revision':current['revision'],'actor':'测试录入'})
    def event(self,geo=None):
        return self.s.create_event({'title':'隔离洪涝核查','hazard':'flood','geometry':geo or polygon(),'source_name':'人工测试研判','source_kind':'manual','issued_at':'2026-11-17T08:00:00+08:00','actor':'测试研判'})
    def impact(self,reg,geo=None):return self.s.analyze_impact(self.event(geo)['id'],{'registry_ids':[reg['id']],'actor':'测试研判'})
    def dispatch(self,impact,ids=None):return self.s.dispatch(impact['id'],{'object_ids':ids or [o['id'] for o in impact['objects']],'assignee':'核查一组','due_date':'2026-12-01','actor':'调度员'})
    def transition(self,t,status,**extra):return self.s.update_task(t['id'],{'expected_revision':t['revision'],'actor':'经办人','status':status,'note':'实际处理说明',**extra})
    def test_empty_workspace_has_no_business_seeds_or_live_connections(self):
        self.assertEqual(self.s.registries(),[]);self.assertEqual(self.s.documents('task'),[])
        self.assertEqual(len(self.s.catalog()['modules']),9)
        self.assertTrue(all(c['status']=='not_connected' for c in self.s.catalog()['connections']))
    def test_more_than_500_objects_use_real_batch_ingest_and_pagination(self):
        reg=self.registry()
        for batch in range(3):self.batch(reg,*[feature(str(batch*250+i)) for i in range(250)],batch_id=str(batch))
        self.assertEqual(self.s.registries()[0]['object_count'],750)
        first=self.s.objects({'registry_id':[reg['id']],'limit':['200'],'offset':['0']})
        last=self.s.objects({'registry_id':[reg['id']],'limit':['200'],'offset':['600']})
        self.assertEqual(first['total'],750);self.assertEqual(len(first['items']),200);self.assertTrue(first['has_more']);self.assertEqual(len(last['items']),150);self.assertFalse(last['has_more'])
        self.assertFalse(set(o['id'] for o in first['items'])&set(o['id'] for o in last['items']))
    def test_duplicate_batch_is_idempotent_and_conflicting_batch_is_rejected(self):
        reg=self.registry();a=self.batch(reg,feature('A'));b=self.batch(reg,feature('A'));self.assertTrue(b['reused']);self.assertEqual(a['object_ids'],b['object_ids'])
        with self.assertRaises(GeoError):self.batch(reg,feature('B'))
        self.assertEqual(self.s.registries()[0]['object_count'],1)
    def test_duplicate_code_rolls_back_the_entire_batch_and_index(self):
        reg=self.registry();self.batch(reg,feature('A'))
        with self.assertRaises(GeoError):self.batch(reg,feature('B'),feature('A'),batch_id='different')
        self.assertEqual(self.s.objects({})['total'],1)
        with self.w.connection() as c:self.assertEqual(c.execute('SELECT count(*) FROM agri_object_bounds').fetchone()[0],1)
    def test_polygon_hole_and_bbox_false_positive_are_not_impact_hits(self):
        reg=self.registry();hole=polygon();hole['coordinates'].append([[113.002,30.002],[113.002,30.008],[113.008,30.008],[113.008,30.002],[113.002,30.002]])
        self.batch(reg,feature('hole',hole),feature('hit',polygon(113.004,30.004,113.005,30.005)))
        impact=self.impact(reg,polygon(113.003,30.003,113.006,30.006))
        self.assertEqual(impact['bbox_candidate_count'],2);self.assertEqual([o['code'] for o in impact['objects']],['hit'])
        self.assertNotIn('affected_area',impact)
    def test_boundary_contact_is_not_counted_as_exposed_polygon(self):
        reg=self.registry();self.batch(reg,feature('touch',polygon(113.01,30,113.02,30.01)))
        result=self.impact(reg);self.assertEqual(result['object_count'],0);self.assertEqual(result['boundary_only_contacts'],1)
    def test_gis_export_uses_the_frozen_intersection_objects(self):
        reg=self.registry();self.batch(reg,feature('A'));impact=self.impact(reg)
        o=impact['objects'][0];self.s.update_object(o['id'],{'expected_revision':1,'status':'已建','note':'后续状态更新','actor':'项目员'})
        export=self.s.impact_geojson(impact['id']);self.assertEqual(len(export['features']),1);self.assertEqual(export['features'][0]['properties']['business_status'],'在建');self.assertEqual(export['metadata']['impact_id'],impact['id'])
    def test_rtree_bbox_query_selects_only_candidates_in_requested_view(self):
        reg=self.registry();self.batch(reg,feature('inside'),feature('outside',polygon(114,31,114.1,31.1)))
        p=self.s.objects({'bbox':['112.99,29.99,113.02,30.02']});self.assertEqual(p['total'],1);self.assertEqual(p['items'][0]['code'],'inside')
    def test_stale_object_revision_cannot_overwrite_current_status(self):
        reg=self.registry();self.batch(reg,feature('A'));o=self.s.objects({})['items'][0]
        b={'expected_revision':1,'actor':'项目员','status':'已建','progress':100,'note':'验收资料按台账填写'}
        updated=self.s.update_object(o['id'],b);self.assertEqual(updated['revision'],2)
        with self.assertRaises(GeoError):self.s.update_object(o['id'],b)
        self.assertEqual(len(self.s.audit_log(o['id'])),1)
    def test_registry_change_prevents_dispatch_from_an_obsolete_impact(self):
        reg=self.registry();self.batch(reg,feature('A'));impact=self.impact(reg);self.batch(reg,feature('B'),batch_id='batch-2')
        with self.assertRaises(GeoError):self.dispatch(impact)
        self.assertEqual(self.s.documents('task'),[])
        self.assertEqual(len(self.s.document('impact',impact['id'])['objects']),1)
    def test_complete_dispatch_feedback_review_export_survives_reopen(self):
        reg=self.registry();self.batch(reg,feature('A'));impact=self.impact(reg);t=self.dispatch(impact)['tasks'][0]
        t=self.transition(t,'processing');t=self.transition(t,'submitted',finding='needs_support',evidence_urls=['https://example.org/test-evidence'])
        t=self.transition(t,'closed');export=self.s.export_event(impact['event_id'])
        self.assertEqual(export['tasks'][0]['feedback']['finding'],'needs_support');self.assertEqual(export['tasks'][0]['status'],'closed');self.assertEqual(len(self.s.audit_log(t['id'])),4)
        s2=type(self.s)(self.w);self.assertEqual(s2.document('task',t['id'])['review']['note'],'实际处理说明')
    def test_cannot_skip_feedback_or_close_without_review_step(self):
        reg=self.registry();self.batch(reg,feature('A'));t=self.dispatch(self.impact(reg))['tasks'][0]
        for status in ['submitted','closed',{},[]]:
            with self.assertRaises(GeoError):self.transition(t,status)
        t=self.transition(t,'processing')
        with self.assertRaises(GeoError):self.transition(t,'submitted')
        self.assertEqual(self.s.document('task',t['id'])['status'],'processing')
    def test_returned_feedback_and_actor_history_are_retained(self):
        reg=self.registry();self.batch(reg,feature('A'));t=self.dispatch(self.impact(reg))['tasks'][0]
        t=self.transition(t,'processing');t=self.transition(t,'submitted',finding='unknown');t=self.transition(t,'returned');t=self.transition(t,'processing');t=self.transition(t,'submitted',finding='normal');self.transition(t,'closed')
        audit=self.s.audit_log(t['id']);self.assertEqual(audit[2]['snapshot']['feedback']['finding'],'unknown');self.assertEqual(audit[-1]['snapshot']['feedback']['finding'],'normal')
    def test_duplicate_dispatch_reuses_same_task_and_cannot_change_assignee(self):
        reg=self.registry();self.batch(reg,feature('A'));impact=self.impact(reg);first=self.dispatch(impact);second=self.dispatch(impact)
        self.assertEqual(first,second);self.assertEqual(len(self.s.documents('task')),1)
        with self.assertRaises(GeoError):self.s.dispatch(impact['id'],{'object_ids':[impact['objects'][0]['id']],'assignee':'another','due_date':'2026-12-01','actor':'调度'})
    def test_out_of_scope_dispatch_cannot_create_a_task(self):
        reg=self.registry();self.batch(reg,feature('A'));impact=self.impact(reg)
        with self.assertRaises(GeoError):self.dispatch(impact,['missing'])
        self.assertEqual(self.s.documents('task'),[])
    def test_guidance_needs_review_then_creates_frozen_execution_tasks(self):
        reg=self.registry();self.batch(reg,feature('A'));g=self.s.create_guidance({'title':'管理任务','content':'实地检查排水沟渠并回传记录','registry_id':reg['id'],'actor':'农技员'})
        self.assertEqual(self.s.documents('task'),[])
        body={'expected_revision':g['revision'],'actor':'审核员','assignee':'执行组','due_date':'2026-12-01'}
        result=self.s.publish_guidance(g['id'],body);self.assertEqual(result['task_count'],1);t=self.s.documents('task')[0];self.assertEqual(t['instruction'],g['content'])
        with self.assertRaises(GeoError):self.s.publish_guidance(g['id'],body)
        self.assertEqual(len(self.s.documents('task')),1)
    def test_empty_or_changed_guidance_target_cannot_be_published(self):
        reg=self.registry();g=self.s.create_guidance({'title':'task','content':'check','registry_id':reg['id'],'actor':'author'})
        with self.assertRaises(GeoError):self.s.publish_guidance(g['id'],{'expected_revision':1,'actor':'reviewer','assignee':'team','due_date':'2026-12-01'})
        self.batch(reg,feature('A'))
        with self.assertRaises(GeoError):self.s.publish_guidance(g['id'],{'expected_revision':1,'actor':'reviewer','assignee':'team','due_date':'2026-12-01'})
    def test_invalid_geometry_and_non_polygon_registry_input_are_rejected(self):
        reg=self.registry()
        for geo in [{'type':'Point','coordinates':[113,30]}, {'type':'Polygon','coordinates':[[[113,30],[114,30],[114,31]]]}, {'type':'Point','coordinates':[float('nan'),30]}]:
            with self.assertRaises(GeoError):self.batch(reg,feature('A',geo))
        self.assertEqual(self.s.objects({})['total'],0)
    def test_version_and_unsafe_evidence_are_rejected_without_partial_update(self):
        reg=self.registry();self.batch(reg,feature('A'));t=self.dispatch(self.impact(reg))['tasks'][0];t=self.transition(t,'processing')
        with self.assertRaises(GeoError):self.transition(t,'submitted',finding='affected',evidence_urls=['file:///etc/passwd'])
        self.assertEqual(self.s.document('task',t['id'])['revision'],2)
        with self.assertRaises(GeoError):self.s.update_task(t['id'],{'actor':'someone','status':'submitted','note':'no version'})
    def test_json_route_validates_unknown_fields_and_pagination(self):
        for q in [{'limit':['201']},{'bbox':['1,2,0,3']},{'q':['a','b']},{'unknown':['x']}]:
            with self.assertRaises(GeoError):self.s.objects(q)
        with self.assertRaises(GeoError):self.s.create_registry({'name':'bad','kind':{},'source':'x','actor':'x'})


class AgricultureOperationsHTTPTest(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.w=Workspace(Path(self.tmp.name)/'workspace.sqlite3');self.server=LocalServer(('127.0.0.1',0),make_handler(self.w));self.server.quiet=True
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start();self.url='http://127.0.0.1:'+str(self.server.server_port)
    def tearDown(self):self.server.shutdown();self.server.server_close();self.thread.join();self.w.close();self.tmp.cleanup()
    def request(self,path,body=None,origin=None):
        headers={'Content-Type':'application/json'}
        if origin:headers['Origin']=origin
        req=urllib.request.Request(self.url+'/api/agri-operations'+path,data=json.dumps(body).encode() if body is not None else None,headers=headers)
        return urllib.request.urlopen(req)
    def test_http_catalog_registry_and_existing_same_origin_guard(self):
        with self.request('/catalog') as r:self.assertEqual(len(json.load(r)['modules']),9)
        b={'name':'HTTP台账','kind':'farmland','source':'isolated','actor':'test'}
        with self.request('/registries',b,self.url) as r:self.assertEqual(r.status,201)
        with self.request('/registries') as r:self.assertEqual(len(json.load(r)['registries']),1)
        with self.assertRaises(urllib.error.HTTPError) as e:self.request('/registries',b,'https://evil.example')
        self.assertEqual(e.exception.code,403)
