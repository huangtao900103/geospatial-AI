import {renderCommonSeries} from './series-reading.js?v=20261115-polish';

const esc=value=>String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const finite=value=>typeof value==='number'&&Number.isFinite(value);
const number=(value,digits=2)=>finite(value)?value.toLocaleString('zh-CN',{maximumFractionDigits:digits}):'未记录';
const safeID=value=>typeof value==='string'&&/^[a-zA-Z0-9][a-zA-Z0-9._-]{0,120}$/.test(value);
const localURL=value=>{
 if(typeof value!=='string'||!/^\/api\/(?:agri-research|agriculture-atlas)\//.test(value)||/[\\\r\n]/.test(value)||value.includes('..')||/%(?:2e|2f|5c)/i.test(value))return null;
 try{const parsed=new URL(value,'http://localhost');return parsed.origin==='http://localhost'&&/^\/api\/(?:agri-research|agriculture-atlas)\//.test(parsed.pathname)?parsed.pathname+parsed.search:null;}catch{return null;}
};
const stateLabel=value=>({ready:'可查看',succeeded:'已完成',running:'正在处理',queued:'等待处理',insufficient_dates:'观测不足',needs_samples:'待补样本',needs_seasonal_data:'待补季节观测',needs_data:'待补数据',failed:'处理未完成',validated:'已验证',trained:'已训练',trained_baseline:'基线已训练',ready_for_training:'可训练',ready_to_train:'可训练',reviewed:'已核查',pending:'待核查',confirmed:'已确认',verified:'已核实',dismissed:'已排除'})[value]||'待核验';
const productVersion=value=>typeof value==='string'&&/^[a-f0-9]{64}$/.test(value);
const cropNames={rice:'水稻',rapeseed:'油菜',other:'其他作物与非目标地物'};
const obsDate=row=>String(row?.date||row?.actual_date||row?.datetime||'').slice(0,10);
function researchChart(result){
 const scope=result.summary?.support_scope;
 return renderCommonSeries(result).replace(' · 共同有效范围</span>',` · ${esc(scope||'共同有效范围')}</span>`);
}

export function researchSeriesResult(record){
 const source=record?.result||record||{},rows=source.time_series||source.series||source.observations||[];
 return {summary:source.summary||{index_id:'ndvi',common_support_pixel_count:source.sequence_summary?.common_support_pixels},time_series:Array.isArray(rows)?rows.map(row=>({...row,date:obsDate(row),
  mean_common_support:Object.hasOwn(row,'mean_common_support')?row.mean_common_support:Object.hasOwn(row,'mean_index_common_support')?row.mean_index_common_support:row.common_mean,
  mean_value:Object.hasOwn(row,'mean_value')?row.mean_value:Object.hasOwn(row,'mean_index_all_valid')?row.mean_index_all_valid:row.ndvi_mean,
  valid_ratio:Object.hasOwn(row,'valid_ratio')?row.valid_ratio:row.coverage_ratio
 })):[]};
}
export function researchNDMIResult(record){
 const result=researchSeriesResult(record);
 return {summary:{...result.summary,index_id:'ndmi',index_label:'NDMI'},time_series:result.time_series.map(row=>({...row,
  mean_common_support:Object.hasOwn(row,'quality_common_mean_ndmi')?row.quality_common_mean_ndmi:null,
  mean_value:finite(row.ndmi_mean)?row.ndmi_mean:null,
  valid_ratio:finite(row.ndmi_valid_ratio)?row.ndmi_valid_ratio:null
 }))};
}
export function renderResearchCharts(record,{showVersion=true}={}){
 const result=researchSeriesResult(record),rows=result.time_series,note=result.summary?.support_note||result.summary?.note,missing=record?.sequence_summary?.missing_windows||[],ndmi=rows.some(row=>Object.hasOwn(row,'quality_common_mean_ndmi'));
 return `<section class="agri-research agr-frozen-series" aria-label="已归档时序观测">${note?`<p class="agr-quality">${esc(note)}</p>`:''}${researchChart(result)}${ndmi?`<details class="agr-provenance agr-moisture" data-mc-detail="agr-moisture"><summary>水分指数 NDMI</summary><p>使用与NDVI完全相同的质量合格共同像元；缺测保留为空。</p>${researchChart(researchNDMIResult(record))}</details>`:''}${missing.length?`<p class="agr-quality">未取得合格观测的窗口：${missing.map(value=>esc(typeof value==='string'?value:value.label||value.start||'未记录')).join('、')}。缺测保留为空，不补齐或插值。</p>`:''}${showVersion&&record?.series_sha256?`<details class="agr-provenance" data-mc-detail="agr-frozen-version"><summary>时序版本</summary><small>${esc(record.series_sha256)}</small><p>曲线使用本次答案保存的观测与质量范围。</p></details>`:''}</section>`;
}
export function frozenResearchURL(value,record){
 if(!safeID(record?.id)||!productVersion(record?.series_sha256))return null;
 const url=localURL(value);if(!url)return null;const parsed=new URL(url,'http://localhost');
 return parsed.pathname.startsWith('/api/agri-research/series/'+encodeURIComponent(record.id)+'/files/')&&parsed.searchParams.get('series_sha256')===record.series_sha256&&[...parsed.searchParams].length===1?url:null;
}

export class AgriResearch{
 constructor({api,onShowProduct,onShowReview,getArea,onChange}={}){
  Object.assign(this,{api,onShowProduct,onShowReview,getArea,onChange});this.tab='series';this.catalog=null;this.selectedSeries=null;
  this.loading=false;this.busy=false;this.error='';this.requestGeneration=0;this.selectedObservation='';
  this.seriesCache=new Map();this.sampleDraft={series_id:'',name:'',method:'field_survey',reference:'',license:'',verified_by:''};this.sampleFile=null;this.selectedSampleSet='';this.sampleMessage='';
  this.reviewDraft={series_id:'',change_id:'',threshold:'',min_pixels:1,direction:'both'};this.selectedReview=null;this.reviewNotes={};this.reviewStates={};this.reviewLimit=20;
 }
 notify(){this.onChange?.();}
 async load(){
  if(this.loading)return;
  this.loading=true;this.error='';this.notify();
  try{const value=await this.api('/agri-research/catalog');if(!value||typeof value!=='object'||!Array.isArray(value.series))throw Error('研究目录格式不完整');this.catalog=value;}
  catch(error){this.error='分析目录暂未连接：'+(error.message||'请稍后重试');}
  finally{this.loading=false;this.notify();}
 }
 async openSeries(id){
  if(!safeID(id))throw Error('未找到时序成果');const generation=++this.requestGeneration;
  this.loading=true;this.error='';this.selectedSeries=null;this.selectedObservation='';this.notify();
  try{const result=await this.api('/agri-research/series/'+encodeURIComponent(id));if(generation!==this.requestGeneration)return;
   if(!result||result.id!==id)throw Error('时序成果标识不一致');this.selectedSeries=result;this.seriesCache.set(id,result);
  }catch(error){if(generation===this.requestGeneration)this.error='读取时序未完成：'+(error.message||'请重试');}
  finally{if(generation===this.requestGeneration){this.loading=false;this.notify();}}
 }
 observations(){return researchSeriesResult(this.selectedSeries).time_series;}
 async changeField(key,value){
  const fields={sample_series:'series_id',sample_name:'name',sample_method:'method',sample_reference:'reference',sample_license:'license',sample_verified_by:'verified_by'};
  if(fields[key]){this.sampleDraft[fields[key]]=value;if(key==='sample_series')this.notify();return;}
  if(key==='sample_set'){this.selectedSampleSet=value;this.notify();return;}
  if(['review_series','review_change','review_threshold','review_min_pixels','review_direction'].includes(key)){
   this.reviewDraft[key.replace('review_','')==='series'?'series_id':key.replace('review_','')==='change'?'change_id':key.replace('review_','')]=value;
   if(key==='review_change')this.notify();
   if(key==='review_series'){
    this.reviewDraft.change_id='';if(value&&!this.seriesCache.has(value)){this.loading=true;this.notify();try{const record=await this.api('/agri-research/series/'+encodeURIComponent(value));if(record.id!==value)throw Error('时序标识不一致');this.seriesCache.set(value,record);}catch(error){this.error=error.message;}finally{this.loading=false;}}
    this.notify();
   }
  }
 }
 async readSampleFile(file){
  this.error='';this.sampleFile=null;if(!file){this.notify();return;}
  try{if(file.size>1900000)throw Error('样本文件须小于1.9MB，请按调查批次拆分。');const geojson=JSON.parse(await file.text());if(geojson.type!=='FeatureCollection'||!Array.isArray(geojson.features)||!geojson.features.length)throw Error('请提供非空GeoJSON FeatureCollection样本。');if(geojson.features.some(feature=>feature?.geometry?.type!=='Point'))throw Error('本轮样本导入仅接受Point调查样本。');this.sampleFile={name:file.name,geojson};if(!this.sampleDraft.name)this.sampleDraft.name=file.name.replace(/\.(geojson|json)$/i,'');}
  catch(error){this.error='样本文件未载入：'+error.message;}
  this.notify();
 }
 async mutate(run){
  if(this.busy)return;this.busy=true;this.error='';this.notify();
  try{return await run();}catch(error){this.error=error.message||'操作未完成，请重试';}finally{this.busy=false;this.notify();}
 }
 async importSamples(){return this.mutate(async()=>{
  const draft=this.sampleDraft;if(!safeID(draft.series_id))throw Error('请选择样本对应的影像时序');if(!this.sampleFile)throw Error('请先选择实地或独立影像核实的样本文件');
  const label_source={method:draft.method,reference:draft.reference.trim(),license:draft.license.trim(),verified_by:draft.verified_by.trim()};
  if(!['field_survey','independent_photo_interpretation'].includes(label_source.method)||Object.entries(label_source).some(([key,value])=>key!=='method'&&!value))throw Error('请完整填写调查来源、使用许可和核实人员');
  const result=await this.api('/agri-research/samples',{method:'POST',body:{name:draft.name.trim()||this.sampleFile.name,series_id:draft.series_id,geojson:this.sampleFile.geojson,label_source}});
  const set=result.sample_set||result;if(!safeID(set.id))throw Error('样本返回缺少有效记录标识');this.selectedSampleSet=set.id;this.sampleMessage='样本已登记，时序特征与独立验证条件按实际数据检查。';await this.load();
 });}
 async train(){return this.mutate(async()=>{if(!safeID(this.selectedSampleSet))throw Error('请选择已满足条件的样本批次');const result=await this.api('/agri-research/train',{method:'POST',body:{sample_set_id:this.selectedSampleSet}});const model=result.model||result;this.sampleMessage=model.status==='trained_baseline'?'分类基线已训练，请查看独立空间留出验证记录。':'训练条件尚未满足，请补充样本或时序特征。';await this.load();});}
 async openReview(id){
  if(!safeID(id))throw Error('未找到核查任务');return this.mutate(async()=>{const review=await this.api('/agri-research/reviews/'+encodeURIComponent(id));if(review.id!==id)throw Error('核查任务标识不一致');this.selectedReview=review;this.reviewNotes={};this.reviewStates={};this.reviewLimit=20;});
 }
 async createReview(){return this.mutate(async()=>{
  const draft=this.reviewDraft,threshold=Number(draft.threshold),min_pixels=Number(draft.min_pixels);
  if(!safeID(draft.series_id)||!safeID(draft.change_id))throw Error('请选择时序和前后观测日期');if(String(draft.threshold).trim()===''||!Number.isFinite(threshold)||threshold<=0||threshold>2)throw Error('请填写大于0且不超过2的NDVI变化幅度阈值');if(!Number.isInteger(min_pixels)||min_pixels<1)throw Error('最小图斑须为正整数像元数');
  const review=await this.api('/agri-research/reviews',{method:'POST',body:{series_id:draft.series_id,change_id:draft.change_id,threshold,min_pixels,direction:draft.direction}});if(!safeID(review.id))throw Error('核查任务返回格式不完整');this.selectedReview=review;await this.load();
 });}
 candidateState(id){
  const review=this.selectedReview,record=review?.candidate_statuses?.[id]||(review?.candidates||[]).find(item=>String(item.id||item.candidate_id)===id)||{};
  return typeof record==='string'?{status:record}:record;
 }
 async saveCandidate(id){return this.mutate(async()=>{if(!safeID(this.selectedReview?.id))throw Error('请先打开核查任务');const reviewId=this.selectedReview.id,previous=this.candidateState(id),status=this.reviewStates[id]||previous.status||'pending',note=this.reviewNotes[id]??previous.note??'';await this.api('/agri-research/reviews/'+encodeURIComponent(reviewId)+'/status',{method:'POST',body:{candidate_id:id,status,note}});const updated=await this.api('/agri-research/reviews/'+encodeURIComponent(reviewId));if(updated.id!==reviewId)throw Error('核查更新已提交，详情暂未正确返回，请重新打开');this.selectedReview=updated;await this.load();});}
 async showObservation(id){
  if(this.busy)return;const row=this.observations().find(item=>String(item.id||item.product_id||obsDate(item))===id);
  if(row?.valid_ratio===0)throw Error('该次观测没有有效像元，不能作为地图分析结果');
  const ident=row?.product_id,expected=row?.product_sha256;
  if(!safeID(ident)||!productVersion(expected))throw Error('该观测未提供可核验的地图成果');
  this.busy=true;this.error='';this.notify();
  try{const product=await this.api('/agriculture-atlas/'+encodeURIComponent(ident));if(product.id!==ident||product.product_sha256!==expected)throw Error('观测成果版本已变化，请重新载入时序');if(typeof this.onShowProduct!=='function')throw Error('地图尚未就绪');await this.onShowProduct(product);}
  catch(error){this.error='上图未完成：'+error.message;}
  finally{this.busy=false;this.notify();}
 }
 async act(action,id){
  if(action==='agr-refresh')return this.load();
  if(action==='agr-tab'){if(['series','samples','reviews'].includes(id))this.tab=id;this.error='';this.notify();return;}
  if(action==='agr-series-open')return this.openSeries(id);
  if(action==='agr-series-back'){this.requestGeneration++;this.selectedSeries=null;this.selectedObservation='';this.loading=false;this.error='';this.notify();return;}
  if(action==='agr-observation-map')return this.showObservation(id);
  if(action==='agr-samples-import')return this.importSamples();
  if(action==='agr-train')return this.train();
  if(action==='agr-review-create')return this.createReview();
  if(action==='agr-review-open')return this.openReview(id);
  if(action==='agr-review-back'){this.selectedReview=null;this.error='';this.notify();return;}
  if(action==='agr-review-more'){this.reviewLimit+=20;this.notify();return;}
  if(action==='agr-review-save')return this.saveCandidate(id);
  if(action==='agr-review-map'){if(!this.selectedReview?.geojson?.features?.length)throw Error('本次没有可上图的候选图斑');if(typeof this.onShowReview!=='function')throw Error('地图尚未就绪');return this.mutate(()=>this.onShowReview(this.selectedReview,{viewOnly:true}));}
  return false;
 }
 render(){
  return `<section class="agri-research" aria-label="作物与时序分析"><div class="agr-tabs" role="group" aria-label="分析内容">${[['series','时序观察'],['samples','作物样本'],['reviews','变化核查']].map(([id,label])=>`<button data-mc-action="agr-tab" data-id="${id}" aria-pressed="${this.tab===id}">${label}</button>`).join('')}</div>${this.error?`<div class="agr-error" role="alert">${esc(this.error)}<button data-mc-action="agr-refresh">重新连接</button></div>`:''}${this.loading?'<p class="agr-state" role="status">正在读取数据与来源…</p>':''}${!this.catalog&&!this.loading&&!this.error?'<div class="agr-empty"><p>查看实际时序、样本与核查记录。</p><button class="agr-button" data-mc-action="agr-refresh">载入分析目录</button></div>':this.tab==='series'?this.renderSeries():this.tab==='samples'?this.renderSamples():this.renderReviews()}</section>`;
 }
 renderSeries(){
  if(this.selectedSeries)return this.renderSeriesDetail(this.selectedSeries);
  const rows=this.catalog?.series||[];
  return `<div class="agr-section-heading"><div><h3>影像时序</h3><p>按实际观测日期查看变化，云与缺测单独记录。</p></div><button data-mc-action="agr-refresh" aria-label="刷新时序目录">刷新</button></div>${rows.length?`<div class="agr-series-list">${rows.map(item=>`<button class="agr-series-row" data-mc-action="agr-series-open" data-id="${esc(item.id)}"><span><b>${esc(item.title||'影像时序')}</b><small>${esc(item.period_label||[item.start,item.end].filter(Boolean).join(' — ')||'日期见观测记录')}</small><small>${esc(item.source_label||item.source_id||'来源见详情')}${finite(item.observation_count)?' · '+esc(item.observation_count)+'次实际观测':''}</small></span><span class="agr-row-state">${stateLabel(item.status)}<i aria-hidden="true">→</i></span></button>`).join('')}</div>`:`<div class="agr-empty"><h4>暂无已归档时序</h4><p>完成影像检索和质量处理后，这里显示真实观测与可用覆盖，不预填连续曲线。</p></div>`}${this.catalog?.series_notes?`<p class="agr-note">${esc(this.catalog.series_notes)}</p>`:''}`;
 }
 renderSeriesDetail(record){
  const result=researchSeriesResult(record),rows=result.time_series,downloads=Array.isArray(record.downloads)?record.downloads:[],provenance=record.provenance||{};
  return `<button class="agr-back" data-mc-action="agr-series-back">← 返回时序列表</button><header class="agr-detail-heading"><h3>${esc(record.title||'影像时序')}</h3><p>${esc(record.period_label||'实际日期见观测记录')}</p><p>${esc(record.scope_label||'范围见来源')} ${record.resolution_m?'· '+esc(record.resolution_m)+'米':''}</p></header>${renderResearchCharts(record,{showVersion:false})}<section class="agr-observations"><h4>逐次观测与地图</h4>${rows.length?rows.map(row=>{const id=String(row.id||row.product_id||obsDate(row)),ratio=finite(row.valid_ratio)?row.valid_ratio:null,missing=ratio===0||row.quality_status==='no_valid_pixels',excluded=row.comparison_eligible===false,canMap=!missing&&safeID(row.product_id)&&productVersion(row.product_sha256);return `<article><div><b>${esc(obsDate(row)||'日期未记录')}${missing?' · 缺测':excluded?' · 低覆盖，未纳入共同范围比较':''}</b><small>${ratio===null?'有效覆盖未记录':'有效覆盖 '+number(ratio*100,1)+'%'}${row.scene_id?' · '+esc(row.scene_id):''}</small></div>${canMap?`<button class="agr-button" data-mc-action="agr-observation-map" data-id="${esc(id)}" ${this.busy?'disabled':''}>${this.busy?'正在上图…':'上图'}</button>`:`<span class="agr-muted">${missing?'无有效像元':'暂无地图产物'}</span>`}</article>`;}).join(''):'<p class="agr-note">尚无可显示的观测记录。</p>'}</section>${downloads.some(item=>localURL(item.url))?`<div class="agr-downloads">${downloads.filter(item=>localURL(item.url)).map(item=>`<a href="${esc(localURL(item.url))}" download>${esc(item.label||'下载数据')}${item.type?'<small>'+esc(item.type)+'</small>':''}</a>`).join('')}</div>`:''}<details class="agr-provenance"><summary>来源与处理说明</summary>${provenance.method?`<p>${esc(provenance.method)}</p>`:''}${Array.isArray(record.limitations)?record.limitations.map(value=>`<p>${esc(value)}</p>`).join(''):''}${Array.isArray(provenance.sources)?provenance.sources.map(source=>`<p>${esc(typeof source==='string'?source:source.title||source.name||'数据来源')}</p>`).join(''):''}${record.series_sha256||record.product_sha256?`<small>时序版本 ${esc(record.series_sha256||record.product_sha256)}</small>`:''}</details>`;
 }
 renderSamples(){
  const classifier=this.catalog?.classifier||{},sets=this.catalog?.sample_sets||[],models=this.catalog?.models||[],draft=this.sampleDraft;
  const selected=sets.find(item=>item.id===this.selectedSampleSet),counts=selected?.counts||classifier.sample_counts||{},validated=classifier.status==='validated';
  const requirements=Array.isArray(classifier.requirements)?classifier.requirements:['实地核实的作物标签、调查年份和地块位置。','水稻、油菜之外需包含其他作物及非目标地物。','训练与独立验证按地块或空间组分离，同一地块不能同时参与两者。','模型预测或年度Crops类别不能作为独立地面真值。'];
  const canTrain=!!selected&&['ready_for_training','ready_to_train'].includes(selected.status),field=(key,label,placeholder)=>`<label class="agr-field">${label}<input data-agr-field="sample_${key}" value="${esc(draft[key]||'')}" placeholder="${esc(placeholder||'')}" maxlength="1000"></label>`;
  return `<div class="agr-section-heading"><div><h3>作物识别样本</h3><p>水稻、油菜和其他地物分开标注，保留调查年份及来源。</p></div><span class="agr-status">${stateLabel(classifier.status||'needs_samples')}</span></div>
  ${this.sampleMessage?`<p class="agr-note" role="status">${esc(this.sampleMessage)}</p>`:''}
  <section class="agr-model-state"><h4>${validated?'模型验证记录':'样本与识别状态'}</h4><p>${esc(classifier.message||classifier.validation_note||'当前未形成经过独立样本验证的水稻、油菜识别结果。')}</p>${Array.isArray(classifier.issues)&&classifier.issues.length?`<ul>${classifier.issues.map(issue=>`<li>${esc(this.issueText(issue))}</li>`).join('')}</ul>`:''}${validated&&finite(classifier.macro_f1)?`<p>独立验证 Macro F1：<b>${number(classifier.macro_f1,3)}</b></p>`:''}</section>
  <section class="agr-sample-import"><h4>登记核验样本</h4><label class="agr-field">对应影像时序<select data-agr-field="sample_series" aria-label="样本对应影像时序">${this.seriesOptions(draft.series_id)}</select></label>${field('name','样本批次名称','按调查批次命名')}<label class="agr-upload">选择样本 GeoJSON<input type="file" accept=".geojson,.json,application/geo+json,application/json" data-agr-sample-file></label>${this.sampleFile?`<p class="agr-note">已载入 ${esc(this.sampleFile.name)} · ${this.sampleFile.geojson.features.length}个调查点</p>`:''}<details class="agr-provenance agr-source-fields" data-mc-detail="agr-sample-source"><summary>调查来源与许可（必填）</summary><label class="agr-field">标签核实方式<select data-agr-field="sample_method"><option value="field_survey" ${draft.method==='field_survey'?'selected':''}>实地调查</option><option value="independent_photo_interpretation" ${draft.method==='independent_photo_interpretation'?'selected':''}>独立影像判读</option></select></label>${field('reference','调查记录或来源','调查编号、资料名称或公开链接')}${field('license','使用许可','授权或开放许可名称')}${field('verified_by','核实人员或单位','实际承担标签核实的人员或单位')}</details><p class="agr-note">样本须为WGS84调查点，属性包含 label（rice / rapeseed / other）、crop_year、field_id、split（train / validation）。指数特征从对应真实时序提取。</p><button class="agr-button agr-primary" data-mc-action="agr-samples-import" ${this.busy||!this.sampleFile||!draft.series_id?'disabled':''}>${this.busy?'正在处理…':'登记并检查样本'}</button></section>
  ${sets.length?`<section class="agr-sample-batches"><h4>样本批次与训练</h4><label class="agr-field">选择样本批次<select data-agr-field="sample_set" aria-label="选择样本批次"><option value="">请选择</option>${sets.map(item=>`<option value="${esc(item.id)}" ${this.selectedSampleSet===item.id?'selected':''}>${esc(item.name||item.id)} · ${stateLabel(item.status)}</option>`).join('')}</select></label>${selected?`<div class="agr-sample-counts">${Object.entries(cropNames).map(([id,name])=>`<div><b>${finite(counts[id])?number(counts[id],0):finite(counts.train?.[id])&&finite(counts.validation?.[id])?number(counts.train[id]+counts.validation[id],0):'未登记'}</b><span>${name}</span>${counts.train?`<small>训练 ${number(counts.train[id],0)} / 验证 ${number(counts.validation?.[id],0)}</small>`:''}</div>`).join('')}</div>${Array.isArray(selected.issues)&&selected.issues.length?`<details class="agr-provenance" open><summary>待补条件 · ${selected.issues.length}项</summary><ul>${selected.issues.slice(0,20).map(issue=>`<li>${esc(this.issueText(issue))}</li>`).join('')}</ul></details>`:''}`:''}<button class="agr-button agr-primary" data-mc-action="agr-train" ${this.busy||!canTrain?'disabled':''}>${this.busy?'正在处理…':'训练并独立验证'}</button>${!canTrain?'<p class="agr-note">样本数量、空间分组和时序特征满足要求后可训练。</p>':''}</section>`:''}
  ${models.length?`<section class="agr-model-records"><h4>训练记录</h4>${models.map(model=>this.renderModel(model)).join('')}</section>`:''}
  <details class="agr-provenance"><summary>样本要求</summary><ul>${requirements.map(value=>`<li>${esc(value)}</li>`).join('')}</ul></details>${classifier.import_note?`<p class="agr-note">${esc(classifier.import_note)}</p>`:''}`;
 }
 seriesOptions(selected){return '<option value="">请选择真实影像时序</option>'+(this.catalog?.series||[]).map(item=>`<option value="${esc(item.id)}" ${item.id===selected?'selected':''}>${esc(item.title||item.id)}${item.period_label?' · '+esc(item.period_label):''}</option>`).join('');}
 issueText(issue){
  if(typeof issue==='string')return issue;const label=cropNames[issue.label]||'',split={train:'训练',validation:'验证'}[issue.split]||'',code={insufficient_class_samples:'样本不足',insufficient_spatial_groups:'独立空间组不足',missing_features:'时序特征不完整',independent_label_provenance_required:'缺少独立标签来源',incomplete_features:'时序特征缺测',missing_or_duplicate_id:'样本编号缺失或重复',duplicate_sample_id:'样本编号重复',unknown_class:'作物标签不在支持类别中',explicit_split_required:'缺少训练或验证分组',wrong_crop_year:'样本年份与时序不符',invalid_coordinates:'样本坐标无效',outside_study_area:'样本在研究范围之外',field_id_required:'缺少地块编号',feature_source_version_required:'缺少特征来源版本',feature_schema_mismatch:'时序特征字段不一致',missing_or_invalid_features:'时序特征缺测或无效'}[issue.code]||issue.message||issue.code||'待核实条件';
  return [split,label,code,finite(issue.available)?'已有'+issue.available:'',finite(issue.required)?'需要'+issue.required:''].filter(Boolean).join(' · ');
 }
 renderModel(model){
  const validation=model.validation,reported=validation?.kind==='held_out_spatial_validation'&&finite(validation.sample_count)&&validation.sample_count>0;
  return `<article class="agr-model-record"><div><b>${esc(model.name||model.id||'分类基线')}</b><span class="agr-status">${stateLabel(model.status)}</span></div>${reported?`<p>独立空间留出 ${number(validation.sample_count,0)}个样本 · Macro F1 ${number(validation.macro_f1,3)}</p><details class="agr-provenance"><summary>分类验证指标</summary><table class="agr-table"><thead><tr><th>类别</th><th>精确率</th><th>召回率</th><th>F1</th></tr></thead><tbody>${(validation.per_class||[]).map(row=>`<tr><td>${esc(row.name||cropNames[row.label]||row.label)}</td><td>${number(row.precision,3)}</td><td>${number(row.recall,3)}</td><td>${number(row.f1,3)}</td></tr>`).join('')}</tbody></table><p>指标只对应本次留出样本，不能推定全市精度或未来年份表现。</p></details>`:'<p>尚无可报告的独立空间验证指标。</p>'}</article>`;
 }
 renderReviews(){
  if(this.selectedReview)return this.renderReviewDetail(this.selectedReview);
  const rows=this.catalog?.reviews||[],draft=this.reviewDraft,series=this.seriesCache.get(draft.series_id),allChanges=series?.changes||[],qualified=allChanges.filter(item=>item.comparison_eligible===true&&finite(item.common_valid_ratio)&&item.common_valid_ratio>0),changes=qualified.length?qualified:allChanges.filter(item=>item.comparison_eligible!==false&&finite(item.common_valid_ratio)&&item.common_valid_ratio>0);
  return `<div class="agr-section-heading"><div><h3>变化核查</h3><p>同一来源、共同有效范围内的变化候选，逐项记录现场核实。</p></div></div><section class="agr-review-create"><h4>生成核查线索</h4><label class="agr-field">影像时序<select data-agr-field="review_series">${this.seriesOptions(draft.series_id)}</select></label><label class="agr-field">前后观测<select data-agr-field="review_change" ${!changes.length?'disabled':''}><option value="">${draft.series_id?'请选择前后日期':'请先选择时序'}</option>${changes.map(item=>`<option value="${esc(item.id)}" ${item.id===draft.change_id?'selected':''}>${esc(item.before_date)} → ${esc(item.after_date)}${finite(item.common_valid_ratio)?' · 共同有效'+number(item.common_valid_ratio*100,1)+'%':''}</option>`).join('')}</select></label><label class="agr-field">NDVI变化幅度阈值<input type="number" inputmode="decimal" min="0.001" max="2" step="0.01" data-agr-field="review_threshold" value="${esc(draft.threshold)}" placeholder="请按核查目的填写"></label><div class="agr-field-pair"><label class="agr-field">变化方向<select data-agr-field="review_direction">${[['both','升高与降低'],['decrease','降低'],['increase','升高']].map(([value,label])=>`<option value="${value}" ${draft.direction===value?'selected':''}>${label}</option>`).join('')}</select></label><label class="agr-field">最小连通像元<input type="number" inputmode="numeric" min="1" step="1" data-agr-field="review_min_pixels" value="${esc(draft.min_pixels)}"></label></div><button class="agr-button agr-primary" data-mc-action="agr-review-create" ${this.busy||!draft.series_id||!draft.change_id?'disabled':''}>${this.busy?'正在处理…':'生成线索'}</button><p class="agr-note">筛选的是光谱条件图斑，阈值不代表作物、灾损或违法认定。</p></section>${rows.length?`<div class="agr-review-list"><h4>已归档核查任务</h4>${rows.map(item=>`<article><div><b>${esc(item.title||item.name||'变化线索')}</b><span class="agr-status">${stateLabel(item.status)}</span></div><p>${esc(item.period_label||[item.before_date,item.after_date].filter(Boolean).join(' → ')||'日期见核查记录')}</p><p>${esc(item.note||item.reason||'等待现场核实')}</p>${finite(item.area_ha??item.summary?.area_sum_ha)?`<small>已呈现线索 ${number(item.area_ha??item.summary?.area_sum_ha,4)} 公顷</small>`:''}<button class="agr-button" data-mc-action="agr-review-open" data-id="${esc(item.id)}">查看核查</button></article>`).join('')}</div>`:'<div class="agr-empty"><h4>暂无待核查任务</h4><p>生成真实变化候选后，在此记录现场核实和处理结论。</p></div>'}<p class="agr-note">光谱变化可能来自收割、水位、生育期或观测条件。跨季差异应结合季历解释。</p>`;
 }
 renderReviewDetail(review){
  const features=Array.isArray(review.geojson?.features)?review.geojson.features:[],summary=review.summary||{},download='/api/agri-research/reviews/'+encodeURIComponent(review.id)+'/geojson';
  return `<button class="agr-back" data-mc-action="agr-review-back">← 返回核查列表</button><header class="agr-detail-heading"><h3>${esc(review.title||review.name||'变化核查')}</h3><p>${esc(review.period_label||[review.before_date||review.source?.before_date,review.after_date||review.source?.after_date].filter(Boolean).join(' → '))}</p><p>${features.length}个候选图斑${finite(summary.area_sum_ha)?' · 几何累计 '+number(summary.area_sum_ha,4)+'公顷':''}</p></header>${summary.truncated?`<p class="agr-quality" role="note">本次显示${features.length} / ${number(summary.candidate_count_before_display_limit,0)}个候选，另有${number(summary.omitted_candidate_count,0)}个未呈现。下方显示面积仅对应已呈现候选。</p>`:''}${finite(summary.common_valid_ratio)?`<p class="agr-note">前后共同有效覆盖 ${number(summary.common_valid_ratio*100,1)}%</p>`:''}<div class="agr-downloads"><button class="agr-button agr-primary" data-mc-action="agr-review-map" ${this.busy||!features.length?'disabled':''}>上图核查</button><a href="${esc(download)}" download>下载已显示候选 GeoJSON</a></div>${!features.length?'<div class="agr-empty"><p>当前阈值下没有候选图斑，未生成或推测变化范围。</p></div>':`<div class="agr-candidates">${features.slice(0,this.reviewLimit).map((feature,index)=>{const id=String(feature.id??feature.properties?.candidate_id??feature.properties?.id??''),state=this.candidateState(id),selected=this.reviewStates[id]||state.status||'pending',note=this.reviewNotes[id]??state.note??'';return `<details class="agr-candidate" data-mc-detail="agr-candidate-${esc(id)}"><summary>候选 ${index+1}<span>${stateLabel(state.status||'pending')}</span></summary><p>${esc(({increase:'指数上升线索',decrease:'指数下降线索',both:'指数变化线索'})[feature.properties?.direction||feature.properties?.change_direction]||feature.properties?.direction||feature.properties?.change_direction||'变化线索')}${finite(feature.properties?.area_ha)?' · '+number(feature.properties.area_ha,4)+'公顷':''}</p><label class="agr-field">核查状态<select data-agr-review-state="${esc(id)}">${[['pending','待核查'],['verified','已核实'],['dismissed','已排除']].map(([value,label])=>`<option value="${value}" ${selected===value?'selected':''}>${label}</option>`).join('')}</select></label><label class="agr-field">核查说明<textarea data-agr-review-note="${esc(id)}" rows="2" maxlength="500" placeholder="记录调查依据、时间和结论">${esc(note)}</textarea></label><button class="agr-button" data-mc-action="agr-review-save" data-id="${esc(id)}" ${this.busy||!id?'disabled':''}>保存核查记录</button></details>`;}).join('')}${features.length>this.reviewLimit?`<button class="agr-button" data-mc-action="agr-review-more">继续显示 · 已显示${this.reviewLimit} / ${features.length}项</button>`:''}</div>`}<p class="agr-note">记录核查结论不会改写原始候选图斑及其来源。</p>`;
 }
}
