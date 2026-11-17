const esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const localTime=v=>{const d=new Date(v);return Number.isNaN(d.getTime())?'—':new Intl.DateTimeFormat('zh-CN',{timeZone:'Asia/Shanghai',year:'numeric',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',hour12:false}).format(d)};
const number=(v,p=2)=>typeof v==='number'&&Number.isFinite(v)?v.toLocaleString('zh-CN',{maximumFractionDigits:p}):'未知';

const hasNumber=v=>typeof v==='number'&&Number.isFinite(v);
const draftKeys=['group_by','measure_field','measure_unit','entity_field','query'];
export function sceneResultView(scene,result,draft={}){
 const s=result.summary||{},options=s.options||{},id=scene.id;
 const dirty=String(draft.dataset_id||'')!==String(result.dataset_id||'')||draftKeys.some(k=>String(draft[k]??'')!==String(options[k]??''));
 const metrics=[{id:'records',label:'匹配记录',value:s.feature_count,unit:'条',precision:0,note:'输入 '+number(s.input_feature_count,0)+' 条'}];
 if(s.polygon_feature_count)metrics.push({id:'area',label:'几何累计面积',value:s.area_sum_ha,unit:'公顷',precision:4,note:'未跨要素去重 · 约 '+number(hasNumber(s.area_sum_ha)?s.area_sum_ha*15:null,2)+' 亩'});
 if(s.line_count)metrics.push({id:'length',label:id==='machinery'?'轨迹累计长度':'线路累计长度',value:hasNumber(s.line_length_m)?(s.line_length_m>=1000?s.line_length_m/1000:s.line_length_m):null,unit:s.line_length_m>=1000?'千米':'米',precision:2,note:'未去重'+(id==='machinery'?' · 非作业面积':'')});
 if(s.entity_field)metrics.push({id:'entities',label:id==='machinery'?'唯一设备':'唯一主体 / 项目',value:s.entity_count,unit:'个',precision:0,note:'缺编号 '+number(s.entity_missing_count,0)+' 条'});
 if(s.measure_field)metrics.push({id:'measure',label:s.measure_field+'合计',value:s.measure_sum,unit:s.measure_unit,precision:4,note:'有效 '+number(s.measure_valid_count,0)+' / 缺失 '+number(s.measure_missing_count,0)+' / 非数值 '+number(s.measure_invalid_count,0)});
 const preferred=id==='machinery'?'length':['coldchain','eel','supplies','logistics'].includes(id)&&s.measure_field?'measure':s.polygon_feature_count?'area':s.line_count?'length':'records';
 const primary=metrics.find(m=>m.id===preferred)||metrics[0],secondary=metrics.filter(m=>m!==primary);
 const base='匹配 '+number(s.feature_count,0)+' / '+number(s.input_feature_count,0)+' 条记录';
 const verdict=primary.id==='records'?base+'。':base+'，'+primary.label+' '+number(primary.value,primary.precision)+' '+(primary.unit||'')+'。';
 return {dirty,primary,secondary,verdict};
}

export class SceneWorkflow{
 constructor({api,onImport,onMap,refresh}){Object.assign(this,{api,onImport,onMap,refresh});this.drafts={};this.results={};this.fields={};this.errors={};this.running=false;this.loaded=false;this.runs=[];this.datasets={};}
 async load(){const c=await this.api('/scene-workflows');this.loadError='';this.runs=c.runs||[];for(const binding of c.bindings||[]){this.drafts[binding.id]??={dataset_id:binding.dataset_id,...binding.options};const result=this.runs.find(r=>r.id===binding.latest_analysis_id);if(result)this.results[binding.id]=result;}this.loaded=true;}

 async prepare(id){const d=this.drafts[id]||{};if(d.dataset_id&&!this.fields[d.dataset_id]){const dataset=await this.api('/datasets/'+encodeURIComponent(d.dataset_id));this.datasets[d.dataset_id]=dataset;this.fields[d.dataset_id]=[...new Set(dataset.geojson.features.flatMap(f=>Object.keys(f.properties||{})))];}}
 async update(id,key,value){this.errors[id]='';const d=this.drafts[id]||={};if(key==='dataset_id'){this.drafts[id]={dataset_id:value};if(value)await this.prepare(id);}else d[key]=value;}
 async importFile(id,file){if(!file)return;if(file.size>1900000)throw Error('文件较大，请拆分到1.9MB以内；每份最多500要素、5000坐标点。');const geo=JSON.parse(await file.text());const dataset=await this.onImport(file.name.replace(/\.(geojson|json)$/i,''),geo);await this.update(id,'dataset_id',dataset.id);}
 async run(id){if(this.running)return;const d=this.drafts[id]||{};if(!d.dataset_id)throw Error('请先选择或上传本场景的业务数据');this.running=true;this.errors[id]='';this.refresh();try{const r=await this.api('/scene-workflows/'+id+'/run',{method:'POST',body:d});this.results[id]=r;this.runs.unshift(r);try{await this.onMap(r);}catch(err){throw Error('计算已归档；地图显示失败，可点击重新上图：'+err.message);}}finally{this.running=false;this.refresh();}}
 history(filter,query=''){
  if(!['all','complete'].includes(filter))return '';
  const rows=this.runs.filter(r=>(r.name+' '+r.provenance?.dataset_name).toLowerCase().includes(query.toLowerCase()));
  return rows.length?`<section class="sw-history"><h4>场景台账成果 · ${rows.length}</h4>${rows.map(r=>`<article><b>${esc(r.name)}</b><p>${esc(r.provenance?.dataset_name)} · ${r.summary?.feature_count}条 · ${esc(localTime(r.created_at))}（北京时间）</p><div class="sw-actions"><button data-mc-action="scene-history-map" data-id="${esc(r.id)}">上图查看</button><a href="/api/results/analysis/${esc(r.id)}/bundle.zip" download>下载成果</a></div></article>`).join('')}</section>`:'';
 }

 refreshDraftState(id,host){
  const result=this.results[id];if(!result)return;
  const dirty=sceneResultView({id},result,this.drafts[id]||{}).dirty;
  const note=host.querySelector('.sw-stale-note');if(note)note.hidden=!dirty;
 }
 render(scene,datasets){
  const id=scene.id,d=this.drafts[id]||{},fields=this.fields[d.dataset_id]||[],r=this.results[id],s=r?.summary||{};
  const field=(key,label,none)=>`<label class="ap-field">${label}<select aria-label="${esc(label)}" data-scene-field="${key}"><option value="">${none}</option>${fields.map(f=>`<option value="${esc(f)}" ${d[key]===f?'selected':''}>${esc(f)}</option>`).join('')}</select></label>`;
  const view=r?sceneResultView(scene,r,d):null;
  const metric=m=>`<div><b>${number(m.value,m.precision)}<small>${esc(m.unit)}</small></b><span>${esc(m.label)}</span><small>${esc(m.note)}</small></div>`;
  const result=r?`<section class="sw-result">
   <header><span class="sw-result-eyebrow">已归档结果</span><h4>${esc(scene.name)}</h4></header>
   <p class="sw-verdict">${esc(view.verdict)}</p>
   <p class="sw-stale-note" role="status" ${view.dirty?'':'hidden'}>条件已修改，下方仍是上次结果。重新运行后更新。</p>
   <div class="sw-primary-metric">${metric(view.primary)}</div>
   <div class="sw-metrics sw-secondary-metrics">${view.secondary.map(metric).join('')}</div>
   ${s.group_by?`<table class="sw-table"><thead><tr><th>${esc(s.group_by)}</th><th>记录数</th></tr></thead><tbody>${Object.entries(s.groups||{}).map(([k,v])=>`<tr><td>${esc(k)}</td><td>${v}</td></tr>`).join('')}</tbody></table>`:''}
   <div class="sw-actions"><button class="sw-map-primary" data-mc-action="scene-view-map">查看地图</button><a href="/api/results/analysis/${esc(r.id)}/bundle.zip" download>下载成果包</a><button class="sw-adjust-link" data-mc-action="scene-adjust">调整条件</button></div>
   <p class="sw-result-source">${esc(r.provenance?.dataset_name||'')} · ${esc(localTime(r.created_at))}（北京时间）</p>
   <details><summary>数据来源与统计口径</summary><p>原始时间 ${esc(r.created_at)} · 数据版本 ${esc(r.provenance?.dataset_version)}</p><p>输入 SHA256 ${esc(r.provenance?.sha256)}</p>${(s.limitations||[]).map(l=>`<p>${esc(l)}</p>`).join('')}</details>
  </section>`:'';
  return `<section class="sw-workflow">
   ${this.loadError?`<p class="sw-error" role="alert">${esc(this.loadError)} <button data-mc-action="scene-reconnect">重新连接</button></p>`:''}
   <div class="sw-input-stage"><h4>${r?'输入与条件':'选择业务数据'}</h4>
   <label class="ap-field">本场景数据<select aria-label="本场景数据" data-scene-field="dataset_id"><option value="">请选择业务数据</option>${datasets.map(x=>`<option value="${esc(x.id)}" ${x.id===d.dataset_id?'selected':''}>${esc(x.name)} · ${x.feature_count}要素</option>`).join('')}</select></label>
   <label class="sw-upload">上传本场景 GeoJSON<input type="file" accept=".json,.geojson,application/geo+json,application/json" data-scene-upload="${id}"></label>
   <p class="sw-help">WGS84经纬度；${id==='machinery'?'轨迹需为LineString，并保留设备编号；仅统计轨迹长度，不推算作业面积。':['high_standard','farmland','tenure'].includes(id)?'请提供工程或地块面，汇总几何面积。':'点、线、面均可，原始业务属性随要素保留。'}</p>
   ${d.dataset_id?`<details class="scene-stat-options" data-mc-detail="scene-stat-settings"><summary>分组、汇总与筛选（可选）</summary><div class="sw-fields">
    ${field('group_by','按字段分组','不分组')}${field('entity_field',id==='machinery'?'设备编号字段':'主体 / 项目编号字段','不统计唯一主体数')}${field('measure_field','数值汇总字段（可选）','不汇总数值')}
    <label class="ap-field">数值单位<input data-scene-field="measure_unit" value="${esc(d.measure_unit||'')}" placeholder="按台账口径填写，如：吨、万元"></label>
    <label class="ap-field">筛选记录（可选）<input data-scene-field="query" maxlength="100" value="${esc(d.query||'')}" placeholder="按属性内容筛选，如：在建"></label>
   </div></details>`:''}
   ${this.errors[id]?`<p class="sw-error" role="alert">${esc(this.errors[id])}</p>`:''}
   <button class="ap-run" data-mc-action="scene-run" ${this.running||!d.dataset_id||this.loadError?'disabled':''}>${this.running?'正在统计…':r?'重新统计':'统计并上图'}</button>
   </div>${result}
  </section>`;
 }
}
