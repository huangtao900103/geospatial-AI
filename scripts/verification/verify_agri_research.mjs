import assert from 'node:assert/strict';
import {AgriResearch,researchSeriesResult,researchNDMIResult,renderResearchCharts,frozenResearchURL} from '../../frontend/src/workspaces/agri-research.js';
import {MapCopilot} from '../../frontend/src/workspaces/map-copilot.js';

// All records below live only in memory. This check never calls the production API.
const cases=[],hash='a'.repeat(64),productHash='b'.repeat(64),clone=structuredClone;
const series={id:'series-test',series_sha256:hash,summary:{index_id:'ndvi',common_support_pixel_count:3,support_scope:'质量合格观测共同范围',support_note:'全部观测共同支持为0；不插值'},sequence_summary:{missing_windows:['2026-07']},observations:[
 {date:'2026-06-01',mean_common_support:0,mean_value:.6,quality_common_mean_ndmi:0,ndmi_mean:.7,ndmi_valid_ratio:.8,valid_ratio:.9,comparison_eligible:true,product_id:'product-test',product_sha256:productHash},
 {date:'2026-09-05',mean_common_support:null,mean_value:null,quality_common_mean_ndmi:null,ndmi_mean:null,ndmi_valid_ratio:0,valid_ratio:0,comparison_eligible:false}
]};
const normalized=researchSeriesResult(series),ndmi=researchNDMIResult(series);
assert.equal(normalized.time_series[0].mean_common_support,0);
assert.equal(normalized.time_series[1].mean_common_support,null);
assert.equal(ndmi.time_series[0].mean_common_support,0);
assert.equal(ndmi.time_series[1].mean_common_support,null);
assert.equal(ndmi.summary.common_support_pixel_count,3);
assert.equal(ndmi.time_series[0].mean_value,.7);
cases.push('NDVI and NDMI share declared support and preserve zero separately from missing values');

const ui=new AgriResearch();let html=ui.renderSeriesDetail(series);
assert.match(html,/NDVI · 质量合格观测共同范围/);
assert.match(html,/NDMI · 质量合格观测共同范围/);
assert.match(html,/2026-09-05 · 缺测/);
assert.match(html,/2026-07/);
assert.match(html,/时序版本 a{64}/);
assert.match(renderResearchCharts(series),/全部观测共同支持为0；不插值/);
cases.push('Actual dates, quality comparison scope, missing windows and source versions remain visible');

ui.catalog={series:[],classifier:{status:'needs_samples',macro_f1:.99}};ui.tab='samples';
assert.doesNotMatch(ui.render(),/独立验证 Macro F1/);
assert.doesNotMatch(ui.render(),/99%/);
assert.match(ui.render(),/data-mc-action="agr-samples-import" disabled/);
ui.catalog.classifier.status='trained';assert.doesNotMatch(ui.render(),/独立验证 Macro F1/);
cases.push('Unverified crop models never display fabricated accuracy or an enabled empty sample import');

const geojson={type:'FeatureCollection',features:[{type:'Feature',geometry:{type:'Point',coordinates:[113.4,30.3]},properties:{label:'rice',crop_year:2026,field_id:'field-a',split:'train'}}]};
const calls=[];let review={id:'review-test',geojson:{type:'FeatureCollection',features:[{type:'Feature',id:'candidate-test',geometry:{type:'Polygon',coordinates:[]},properties:{area_ha:1,direction:'decrease'}}]},summary:{feature_count:1,common_valid_ratio:.5},candidate_statuses:{}};
let catalog={series:[{id:'series-test',title:'真实时序'}],classifier:{status:'needs_samples'},sample_sets:[],models:[],reviews:[]};
const workflow=new AgriResearch({api:async(path,options)=>{
 calls.push({path,options});
 if(path==='/agri-research/catalog')return clone(catalog);
 if(path==='/agri-research/samples'){catalog={...catalog,sample_sets:[{id:'samples-test',status:'needs_samples'}]};return {sample_set:{id:'samples-test',status:'needs_samples'}};}
 if(path==='/agri-research/reviews'&&options?.method==='POST')return clone(review);
 if(path==='/agri-research/reviews/review-test/status'){review={...review,candidate_statuses:{'candidate-test':clone(options.body)}};return {ok:true};}
 if(path==='/agri-research/reviews/review-test')return clone(review);
 if(path==='/agri-research/train')return {id:'model-test',status:'needs_seasonal_data'};
 throw Error('Unexpected mock path: '+path);
}});
await workflow.load();
await workflow.readSampleFile({name:'survey.geojson',size:100,text:async()=>JSON.stringify(geojson)});
Object.assign(workflow.sampleDraft,{series_id:'series-test',reference:'实地记录',license:'单位授权',verified_by:'调查单位'});
await workflow.importSamples();
const upload=calls.find(call=>call.path==='/agri-research/samples');
assert.deepEqual(Object.keys(upload.options.body).sort(),['geojson','label_source','name','series_id']);
assert.deepEqual(upload.options.body.geojson,geojson);
assert.deepEqual(upload.options.body.label_source,{method:'field_survey',reference:'实地记录',license:'单位授权',verified_by:'调查单位'});
assert.equal(workflow.selectedSampleSet,'samples-test');
assert.equal(workflow.error,'');
cases.push('Sample import sends only approved labels, declared source and series reference');

workflow.tab='samples';assert.match(workflow.render(),/data-mc-action="agr-train" disabled/);
workflow.catalog.sample_sets[0].status='ready_for_training';
assert.doesNotMatch(workflow.render(),/data-mc-action="agr-train" disabled/);
cases.push('Training is enabled only after backend sample readiness');

Object.assign(workflow.reviewDraft,{series_id:'series-test',change_id:'change-test'});
await workflow.createReview();
assert.match(workflow.error,/阈值/);
assert.equal(calls.filter(call=>call.path==='/agri-research/reviews').length,0);
workflow.reviewDraft.threshold='0.15';await workflow.createReview();
assert.deepEqual(calls.find(call=>call.path==='/agri-research/reviews').options.body,{series_id:'series-test',change_id:'change-test',threshold:.15,min_pixels:1,direction:'both'});
cases.push('Review generation requires an explicit threshold and sends the declared analysis conditions');

const originalGeometry=JSON.stringify(workflow.selectedReview.geojson);
workflow.reviewStates['candidate-test']='verified';workflow.reviewNotes['candidate-test']='内存测试记录';
await workflow.saveCandidate('candidate-test');
assert.deepEqual(calls.find(call=>call.path.endsWith('/status')).options.body,{candidate_id:'candidate-test',status:'verified',note:'内存测试记录'});
assert.equal(workflow.selectedReview.candidate_statuses['candidate-test'].status,'verified');
assert.equal(JSON.stringify(workflow.selectedReview.geojson),originalGeometry);
assert(calls.some(call=>call.path==='/agri-research/reviews/review-test'&&!call.options));
cases.push('Candidate status appends a record and reloads the detail without rewriting source geometry');

workflow.selectedReview={...clone(review),geojson:{type:'FeatureCollection',features:Array.from({length:26},(_,index)=>({...clone(review.geojson.features[0]),id:'candidate-'+index}))},summary:{truncated:true,candidate_count_before_display_limit:40,omitted_candidate_count:14}};
workflow.tab='reviews';html=workflow.render();
assert.equal((html.match(/data-mc-detail="agr-candidate-/g)||[]).length,20);
assert.match(html,/另有14个未呈现/);
assert.match(html,/20 \/ 26/);
assert.match(html,/指数下降线索/);
assert.match(html,/maxlength="500"/);
assert.match(html,/下载已显示候选 GeoJSON/);
await workflow.act('agr-review-more');
assert.equal((workflow.render().match(/data-mc-detail="agr-candidate-/g)||[]).length,26);
cases.push('Displayed candidate limits, omitted counts and incremental reading stay explicit');

workflow.selectedReview=null;workflow.reviewDraft.series_id='series-test';
workflow.seriesCache.set('series-test',{changes:[{id:'raw-duplicate',comparison_eligible:false,common_valid_ratio:.5},{id:'quality-change',comparison_eligible:true,common_valid_ratio:.5,before_date:'2026-06-01',after_date:'2026-08-04'},{id:'empty-change',comparison_eligible:true,common_valid_ratio:0}]});
html=workflow.render();assert.match(html,/value="quality-change"/);assert.doesNotMatch(html,/value="raw-duplicate"|value="empty-change"/);
cases.push('Change selection excludes duplicate raw pairs and pairs with no common support');

let mapped=false;const mapUI=new AgriResearch({api:async()=>({id:'product-test',product_sha256:'c'.repeat(64)}),onShowProduct:async()=>{mapped=true;}});
mapUI.selectedSeries=clone(series);await mapUI.showObservation('product-test');
assert.equal(mapped,false);assert.match(mapUI.error,/版本已变化/);
mapUI.api=async()=>({id:'product-test',product_sha256:productHash});await mapUI.showObservation('product-test');assert.equal(mapped,true);
await assert.rejects(()=>mapUI.showObservation('2026-09-05'),/没有有效像元/);
cases.push('Observation maps verify the declared product hash and refuse a zero-coverage observation');

let firstResolve;const raceUI=new AgriResearch({api:path=>path.endsWith('/first')?new Promise(resolve=>{firstResolve=resolve;}):Promise.resolve({id:'second',observations:[]})});
const first=raceUI.openSeries('first');await raceUI.openSeries('second');firstResolve({id:'first',observations:[]});await first;
assert.equal(raceUI.selectedSeries.id,'second');
cases.push('A slower earlier series request cannot replace the latest selected series');

const seriesURL='/api/agri-research/series/series-test/files/result.json?series_sha256='+hash;
assert.equal(frozenResearchURL(seriesURL,series),seriesURL);
for(const url of [seriesURL.replace('series-test/files','other/files'),seriesURL.replace(hash,'c'.repeat(64)),seriesURL+'&extra=1',seriesURL.replace('/files/result.json','/files/../private')])assert.equal(frozenResearchURL(url,series),null);
cases.push('Frozen time-series downloads require the exact source ID and version');

const question={id:'question-test',answer:{title:'真实时序 <script>',summary:'仅来自归档观测',research_series:clone(series),result_ref:{type:'agriculture_atlas',task_id:'product-test',product_sha256:productHash},atlas_product_snapshot:{id:'product-test',product_sha256:productHash},map_effect:'show',downloads:[{url:seriesURL,label:'时序JSON'},{url:'/api/agriculture-atlas/product-test/files/overlay.png',label:'观测地图'}]}};
const context={atlasChecks:new Map([['question-test',{matched:true}]]),atlasSnapshot:q=>q.answer.atlas_product_snapshot,mapError:'',downloadsOpen:true,sourceOpen:false,logs:()=>'',executionPanel:()=>''},before=JSON.stringify(question);
html=MapCopilot.prototype.researchAnswer.call(context,question);
assert.match(html,/真实时序 &lt;script&gt;/);assert.match(html,/data-mc-action="locate"/);assert.match(html,/series_sha256=/);assert.match(html,/product_sha256=/);
context.atlasChecks.set(question.id,{matched:false,message:'来源版本变化'});
html=MapCopilot.prototype.researchAnswer.call(context,question);
assert.doesNotMatch(html,/data-mc-action="locate"/);assert.doesNotMatch(html,/href="\/api\/agriculture-atlas/);assert.match(html,/href="\/api\/agri-research/);assert.match(html,/<td>0<\/td>/);assert.equal(JSON.stringify(question),before);
cases.push('Frozen answer rendering escapes text and preserves the snapshot while guarding each download and map version');

console.log(JSON.stringify({passed:true,count:cases.length,cases},null,2));
