import assert from 'node:assert/strict';
import {execFileSync} from 'node:child_process';
import fs from 'node:fs';
import {fileURLToPath} from 'node:url';
import path from 'node:path';
import {SceneWorkflow} from '../../frontend/src/workspaces/scene-workflow.js';
import {MapCopilot} from '../../frontend/src/workspaces/map-copilot.js';
import {sceneCategories,sceneFeatureColor} from '../../frontend/src/workspaces/map-workspace.js';
import {SCENE_MODULES} from '../../frontend/src/workspaces/scene-catalog.js';
const cases=[];
const scene=new SceneWorkflow({api:async()=>({bindings:[{id:'high_standard',dataset_id:'dataset_true',options:{},latest_analysis_id:'analysis_a_new'}],runs:[{id:'analysis_a_new',summary:{scene_id:'high_standard'}},{id:'analysis_z_old',summary:{scene_id:'high_standard'}}]}),onImport:()=>{},onMap:()=>{},refresh:()=>{}});
await scene.load();assert.equal(scene.results.high_standard.id,'analysis_a_new');cases.push('restore follows persisted binding, not ID sorting');
scene.runs=[{id:'analysis_a_new',name:'高标准农田',created_at:'2026-11-14T16:00:00Z',summary:{feature_count:2},provenance:{dataset_name:'隔离测试输入'}}];
const history=MapCopilot.prototype.history.call({questions:[],taskFilter:'all',taskSearch:'',sceneWorkflow:scene});
assert(history.includes('高标准农田'));assert(!history.includes('当前没有匹配任务'));cases.push('scene results suppress empty task notice');
assert(history.includes('北京时间'));cases.push('local China time displayed');
let sceneReloaded=false;
const mock={api:async p=>{assert.equal(p,'/assistant/questions');return[]},sceneWorkflow:{load:async()=>{sceneReloaded=true}},toast:()=>{},notify:()=>{},render:()=>{}};
await MapCopilot.prototype.act.call(mock,'refresh-tasks',{});assert(sceneReloaded);cases.push('task refresh includes scene results');
const appMock={api:async path=>path==='/applications'?{applications:[{id:'imagery_search'}]}:[],sceneWorkflow:{load:async()=>{throw Error('isolated unavailable')}},element:{isConnected:false}};
await MapCopilot.prototype.loadApplications.call(appMock);assert.equal(appMock.applications.length,1);assert.equal(appMock.applicationLoadError,'');assert(appMock.sceneWorkflow.loadError.includes('isolated unavailable'));cases.push('scene service failure preserves analysis tools');
const root=fileURLToPath(new URL('../..',import.meta.url));
const localPython=path.join(root,'.venv',process.platform==='win32'?'Scripts/python.exe':'bin/python');
const catalog=process.env.SCENE_TEST_URL
 ?await fetch(process.env.SCENE_TEST_URL+'/api/applications').then(r=>r.json())
 :JSON.parse(execFileSync(fs.existsSync(localPython)?localPython:'python3',['-c',
  'import json,tempfile\nfrom pathlib import Path\nfrom backend.server import Workspace\nwith tempfile.TemporaryDirectory(prefix="geoai-ui-catalog-") as directory:\n workspace=Workspace(Path(directory)/"workspace.sqlite3")\n try: print(json.dumps(workspace.applications.catalog()))\n finally: workspace.close()'],{cwd:root,encoding:'utf8'}));
const ids=new Set(catalog.applications.map(a=>a.id));const scenes=SCENE_MODULES.flatMap(m=>m.items);
assert.equal(scenes.length,23);assert.equal(scenes.filter(s=>s.id==='crop_research').length,1);assert.equal(new Set(scenes.map(s=>s.id)).size,23);for(const s of scenes)for(const id of (s.tools||[]))assert(ids.has(id),s.id+' missing tool '+id);cases.push('22 business scenes and one real temporal research entry link only to registered tools');
const categories=sceneCategories({group_by:'类别',groups:Object.fromEntries('ABCDEFGH'.split('').map((k,i)=>[k,8-i]))});assert.equal(categories.entries.length,7);assert.equal(categories.entries.at(-1).count,3);cases.push('category legend bounds to six plus correctly counted other');
const frozen={group_by:'编号',groups:{'1':1,'1.0':1},feature_groups:{a:'1.0'}};assert.equal(sceneFeatureColor({id:'a',properties:{'编号':1}},frozen),sceneCategories(frozen).entries.find(e=>e.key==='1.0').color);cases.push('frozen feature categories avoid numeric type ambiguity');
console.log(JSON.stringify({passed:true,cases},null,2));
