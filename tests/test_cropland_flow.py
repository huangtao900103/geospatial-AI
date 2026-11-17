"""One-map question to real local raster and GIS lifecycle, using explicit test pixels."""
import json
import tempfile
import time
import unittest
from pathlib import Path
from backend.server import Workspace
from backend.analysis.cropland_services import CroplandServices
from backend.analysis.landcover_services import available
from backend.workflows.assistant_services import route


@unittest.skipUnless(available(), 'native raster environment required')
class CroplandFlowTest(unittest.TestCase):
    def setUp(self):
        import numpy as np
        import rasterio
        from rasterio.transform import from_origin
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        self.w=Workspace(self.root/'workspace.sqlite3');self.w.cropland.close()
        self.rx=1/12000;self.bbox=[113.,30.5-5*self.rx,113.+5*self.rx,30.5]
        self.source=self.root/'test-only-classification.tif'
        data=np.array([[40,40,40,10,40],[40,80,40,10,10],[40,40,40,10,10],[10,10,10,40,10],[0,0,10,10,10]],dtype='uint8')
        with rasterio.open(self.source,'w',driver='GTiff',width=5,height=5,count=1,dtype='uint8',crs='EPSG:4326',nodata=0,transform=from_origin(113.,30.5,self.rx,self.rx)) as ds:ds.write(data,1)
        self.w.cropland=CroplandServices(self.w,source_paths=[str(self.source)])
    def tearDown(self):self.w.close();self.temp.cleanup()
    def ask(self,text,context=None):
        q=self.w.assistant.create({'question':text,'context':context or {}})
        for _ in range(500):
            q=self.w.assistant.get(q['id'])
            if q['status']!='processing':return q
            time.sleep(.01)
        self.fail('question did not finish')
    def test_selected_question_reads_pixels_and_keeps_every_patch_and_original_followup(self):
        text='要把某一选定区域内的农田通过遥感影像全部标注出来'
        missing=self.ask(text);self.assertEqual(missing['status'],'needs_data');self.assertEqual(missing['clarification']['missing_fields'],['bbox'])
        q=self.ask(text,{'bbox':self.bbox});self.assertEqual(q['status'],'answered',q['message']);self.assertEqual(q['binding']['type'],'cropland')
        answer=q['answer'];self.assertEqual(answer['source_year'],2021);self.assertEqual(answer['metrics'][2]['value'],3);self.assertIn('不是当期',str(answer['warnings']));self.assertEqual(len(answer['downloads']),7)
        lineage=self.w.data_catalog.get_plan(answer['data_plan_ref']['id']);self.assertEqual(lineage['used'][0]['source_id'],'worldcover_2021');self.assertEqual(lineage['execution_evidence']['bbox'],self.bbox);self.assertEqual(lineage['interpreted']['region'],'地图选区')
        task=self.w.cropland.get(q['binding']['task_id']);self.assertEqual(task['result']['summary']['cropland_pixel_count'],10)
        self.assertEqual(len(task['result']['geojson']['features']),3);self.assertLess(task['result']['summary']['coverage_ratio'],1)
        for a in task['result']['artifacts']:self.assertTrue(self.w.cropland.artifact(task['id'],a['url'].rsplit('/',1)[-1]).is_file())
        repeated=self.ask(text,{'bbox':self.bbox});self.assertEqual(repeated['binding']['task_id'],task['id']);self.assertTrue(repeated['binding']['reused'])
        follow=self.ask('下载这次成果',{'reference_question_id':q['id']});self.assertEqual(follow['status'],'answered');self.assertEqual(follow['binding']['task_id'],task['id']);self.assertEqual(follow['answer']['map_effect'],'none')
        search=self.w.search_services.search('耕作地标注',remote=False);self.assertTrue(any(h.get('task_id')==task['id'] and h['result_ref']['type']=='cropland' for h in search['results']))
        inspect=self.w.inspection_services.inspect('cropland',task['id'],113.+self.rx/2,30.5-self.rx/2);self.assertTrue(inspect['valid']);self.assertEqual(inspect['class_code'],40)
        self.assertNotEqual(self.w.executions.input_key('cropland_delineation',{'bbox':self.bbox}),self.w.executions.input_key('cropland_delineation',{'bbox':[113.,30.499,113.001,30.5]}))
    def test_current_year_special_classes_and_index_shortcuts_cannot_substitute_history(self):
        for text in ['标注选区今年农田','提取选区上年度农田','提取选区上年农田','标注选区2025年农田','标注选区高标准农田','标注选区农田并给出播种面积','标注选区棉花农田','标注选区的官方农田','标注选区所有农田含果园','用NDVI标注选区全部农田']:
            with self.subTest(text=text):
                plan=self.w.executions.prepare(text,{'bbox':self.bbox,'period':{'start':'2025-11-01','end':'2025-11-30'}});self.assertFalse(plan['executable']);self.assertEqual(plan['status'],'unsupported')
        self.assertEqual(self.w.cropland.list(),[])
    def test_application_freezes_year_and_scope_and_disallows_other_imagery_context(self):
        payload,ref=self.w.applications.prepare({'application_id':'cropland_delineation','context':{'bbox':self.bbox},'parameters':{'product':'worldcover_2021_v200'}})
        self.assertEqual(ref['method']['source_year'],2021);self.assertIn('2021',payload['question']);self.assertEqual(ref['context']['bbox'],self.bbox)
        plan=self.w.executions.prepare(payload['question'],payload['context']);self.assertTrue(plan['executable']);self.assertEqual(plan['tool']['id'],'cropland_delineation')
        conflict=self.w.executions.prepare(payload['question'],{**payload['context'],'period':{'start':'2025-01-01','end':'2025-12-31'}});self.assertFalse(conflict['executable'])
    def test_large_aoi_and_monthly_requests_explain_missing_conditions_before_tool_call(self):
        plan=self.w.executions.prepare('标注选区农田',{'bbox':[113.,30.,113.1,30.1]});self.assertFalse(plan['executable']);self.assertEqual(plan['status'],'needs_data');self.assertEqual(plan['missing_fields'],['bbox'])
        plan=self.w.executions.prepare('标注选区2021年11月农田',{'bbox':self.bbox});self.assertFalse(plan['executable']);self.assertEqual(plan['status'],'unsupported');self.assertEqual(self.w.cropland.list(),[])

if __name__=='__main__':unittest.main()
