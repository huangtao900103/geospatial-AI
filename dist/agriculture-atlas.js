const esc=value=>String(value??'').replace(/[&<>"']/g,char=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[char]));
const safeURL=value=>typeof value==='string'&&(/^\/(?!\/)/.test(value)||/^https?:\/\//i.test(value))?value:null;
const safeColor=value=>typeof value==='string'&&/^#[\da-f]{3}(?:[\da-f]{3})?(?:[\da-f]{2})?$/i.test(value)?value:'#729da7';
const number=(value,precision=2,unit='')=>{
 if(value===null||value===undefined||value==='')return '未记录';
 if(typeof value==='boolean')return value?'是':'否';
 if(typeof value!=='number')return String(value);
 if(!Number.isFinite(value))return '未记录';
 return value.toLocaleString('zh-CN',{useGrouping:unit!=='年',maximumFractionDigits:Math.max(0,Math.min(6,Number.isInteger(precision)?precision:2))});
};
const precision=metric=>metric.precision??(['条','个','项','景','次','年'].includes(metric.unit)?0:2);
const spatial=layer=>layer?.kind==='raster'?!!safeURL(layer.image?.url):['geojson','dataset_geojson'].includes(layer?.kind)&&Array.isArray(layer.geojson?.features)&&layer.geojson.features.length>0;
const icon=name=>`<svg viewBox="0 0 24 24" aria-hidden="true"><path d="${{layers:'m12 3 9 5-9 5-9-5 9-5ZM3 12l9 5 9-5M3 16l9 5 9-5',arrow:'M5 12h14m-5-5 5 5-5 5',map:'m3 6 6-3 6 3 6-3v15l-6 3-6-3-6 3V6Zm6-3v15m6-12v15',download:'M12 3v12m-4-4 4 4 4-4M4 17v4h16v-4',back:'M19 12H5m5-5-5 5 5 5',document:'M6 3h8l4 4v14H6V3Zm8 0v5h4M9 12h6m-6 4h6'}[name]||'M4 4h16v16H4z'}"/></svg>`;
function status(product){
 if(product.status==='ready'&&product.quality?.status==='below_target')return {label:'低覆盖参考',kind:'reference'};
 if(product.status==='ready')return {label:product.layer_count===0?'资料已整理':'可查看',kind:product.layer_count===0?'reference':'ready'};
 return {label:({running:'处理中',processing:'处理中',queued:'等待处理',failed:'处理未完成',unavailable:'待数据接入',needs_data:'待数据接入'})[product.status]||'待核验',kind:product.status==='failed'?'failed':'pending'};
}
const metrics=product=>Array.isArray(product?.summary_metrics)?product.summary_metrics:[];
const cardMetrics=product=>{const seen=new Set();return metrics(product).filter(metric=>{const key=String(metric.label||'');if(seen.has(key))return false;seen.add(key);return true;}).slice(0,2);};
const cardMetric=metric=>{
 const scale=typeof metric.value==='number'&&Number.isFinite(metric.value)&&Math.abs(metric.value)>=10000&&['亩','公顷','平方米'].includes(metric.unit);
 return {value:scale?(metric.value/10000).toLocaleString('zh-CN',{minimumFractionDigits:2,maximumFractionDigits:2}):number(metric.value,precision(metric),metric.unit),unit:scale?'万'+metric.unit:metric.unit||'',raw:number(metric.value,precision(metric),metric.unit)+(metric.unit||'')};
};
const metricHTML=metric=>`<div class="atlas-metric"><b>${esc(number(metric.value,precision(metric),metric.unit))}<small>${esc(metric.unit||'')}</small></b><span>${esc(metric.label||'指标')}</span></div>`;
const productGroups=[['all','全部'],['remote','遥感成果'],['clues','产业线索'],['projects','项目资料']];
const productGroup=product=>product.product_type==='public_address_clues'?'clues':product.product_type==='official_project_notice'?'projects':['annual_classification','city_spectral_reference','cross_product_review','crop_reference'].includes(product.product_type)?'remote':'other';
const searchText=value=>String(value||'').normalize('NFKC').trim().toLowerCase();
// Discovery aliases do not change the product's scientific title or definition.
const productSearchText=product=>searchText([product.title,product.id,product.period_label,product.scope_label,String(product.id||'').includes('cropland')?'农田 耕地 耕作地 Crops':''].join(' '));
export const validAtlasVersion=value=>typeof value==='string'&&/^[a-f0-9]{64}$/.test(value);
export function atlasPinnedURL(value,version){const url=safeURL(value);if(!url||!validAtlasVersion(version))return null;const parsed=new URL(url,'http://localhost');if(parsed.origin!=='http://localhost'||!parsed.pathname.startsWith('/api/agriculture-atlas/'))return null;parsed.searchParams.set('product_sha256',version);return parsed.pathname+parsed.search;}
export function atlasQualityMarkup(product,{compact=false}={}){const quality=product?.quality;if(quality?.status!=='below_target')return '';return `<${compact?'span':'p'} class="atlas-quality-warning" role="note"><b>低覆盖参考</b>${compact?'':esc(quality.warning||'本轮有效覆盖未达到设定门槛，缺测区域保持未知；请结合实际日期核查。')}</${compact?'span':'p'}>`;}
export function atlasSavedSelection(key){try{const value=JSON.parse(localStorage.getItem(key));return value&&typeof value.id==='string'&&/^[a-z0-9][a-z0-9-]{2,95}$/.test(value.id)&&validAtlasVersion(value.product_sha256)?value:null;}catch{return null;}}
export function atlasWorkspaceFocus({focus,question,map,detail}){if(['history','catalog'].includes(focus?.kind))return focus.kind;if(focus?.kind==='new_question')return 'new_question';if(focus?.kind==='question'&&focus.id===question?.id)return 'question';if(focus?.kind==='atlas'&&(map||detail))return 'atlas';const ref=question?.answer?.result_ref;if(!focus&&ref?.type==='agriculture_atlas'&&map?.id===ref.task_id&&map.product_sha256===ref.product_sha256&&(!detail||detail.id===ref.task_id&&detail.product_sha256===ref.product_sha256))return 'question';return map||detail?'atlas':question?'question':'none';}
function rememberDetail(product){if(!validAtlasVersion(product?.product_sha256))return;try{localStorage.setItem('geoai-v19-atlas-detail',JSON.stringify({id:product.id,product_sha256:product.product_sha256}));localStorage.setItem('geoai-v19-workspace-focus',JSON.stringify({kind:'atlas',id:product.id}));}catch{}}

export class AgricultureAtlas{
 constructor({api,onShowProduct,onClose,onChange}={}){
  Object.assign(this,{api,onShowProduct,onClose,onChange});this.region='仙桃市';this.products=[];this.loaded=false;this.loading=false;this.detailLoading=false;this.mapping=false;this.selected=null;this.selectedId=null;this.error='';this.catalogError='';this.expanded=false;this.query='';this.category='all';this.detailGeneration=0;
 }
 notify(){this.onChange?.();}
 async restore(){const saved=atlasSavedSelection('geoai-v19-atlas-detail');if(saved)return this.open(saved.id,{expectedSha:saved.product_sha256,remember:false});return null;}
 async load(){
  if(this.loading)return this.pendingLoad;
  this.loading=true;this.catalogError='';this.notify();
  this.pendingLoad=(async()=>{
   try{const response=await this.api('/agriculture-atlas');if(!response||!Array.isArray(response.products))throw Error('成果目录返回格式不完整');this.region=String(response.region||'仙桃市');this.productionRecordCount=Number.isInteger(response.production_record_count)?response.production_record_count:null;this.products=response.products;this.loaded=true;return this.products;}
   catch(error){this.catalogError='成果目录暂未连接：'+(error?.message||'请稍后重试');return this.products;}
   finally{this.loading=false;this.pendingLoad=null;this.notify();}
  })();
  return this.pendingLoad;
 }
 async open(id,{expectedSha=null,remember=true}={}){
  if(typeof id!=='string'||!id.trim()||id.length>200)throw Error('未找到成果标识');
  const generation=++this.detailGeneration;this.selected=null;this.selectedId=id;this.expectedSha=expectedSha;this.error='';this.detailLoading=true;this.notify();
  try{const product=await this.api('/agriculture-atlas/'+encodeURIComponent(id));if(!product||product.id!==id)throw Error('成果标识不一致，未展示替代结果');if(expectedSha&&product.product_sha256!==expectedSha)throw Error('已保存成果的版本已变化，原图未自动替换；请明确打开当前版本。');if(generation!==this.detailGeneration)return null;this.selected=product;if(remember)rememberDetail(product);return product;}
  catch(error){if(generation===this.detailGeneration)this.error='读取成果未完成：'+(error?.message||'请稍后重试');return null;}
  finally{if(generation===this.detailGeneration){this.detailLoading=false;this.notify();}}
 }
 async act(action,id){
  if(action==='atlas-reload')return this.selectedId?this.open(this.selectedId,{expectedSha:this.expectedSha}):this.load();
  if(action==='atlas-current')return this.open(this.selectedId);
  if(action==='atlas-open')return this.open(id);
  if(action==='atlas-back'){this.detailGeneration++;this.detailLoading=false;this.selected=null;this.selectedId=null;this.error='';try{localStorage.removeItem('geoai-v19-atlas-detail');}catch{}this.onClose?.();this.notify();return;}
  if(action==='atlas-expand'){this.expanded=!this.expanded;this.notify();return;}
  if(action==='atlas-filter'){if(productGroups.some(([key])=>key===id))this.category=id;this.notify();return;}
  if(action==='atlas-clear'){this.query='';this.category='all';this.notify();return;}
  if(action!=='atlas-map')return false;
  if(this.mapping)return;
  const product=this.selected?.id===id?this.selected:await this.open(id);if(!product)return;
  if(product.status!=='ready'||!(Array.isArray(product.layers)?product.layers:[]).some(spatial)){this.error='该资料未提供可上图的空间图层；未生成或推测其位置。';this.notify();return;}
  if(typeof this.onShowProduct!=='function'){this.error='地图尚未就绪，成果已保留，可稍后再试。';this.notify();return;}
  this.mapping=true;this.error='';this.notify();
  try{await this.onShowProduct(product);}
  catch(error){this.error='成果已读取，上图未完成：'+(error?.message||'请重试');}
  finally{this.mapping=false;this.notify();}
 }
 matchingProducts(products=this.products){
  const terms=searchText(this.query).split(/\s+/).filter(Boolean);
  return products.filter(product=>(this.category==='all'||productGroup(product)===this.category)&&terms.every(term=>productSearchText(product).includes(term)));
 }
 render({sceneId=null,compact=false}={}){
  const selected=this.selected&&(!sceneId||(this.selected.scene_ids||[]).includes(sceneId))?this.selected:null;
  if(selected)return this.renderDetail(selected);
  if(this.selectedId&&(this.detailLoading||this.error))return `<section class="agri-atlas atlas-detail"><button class="atlas-back" data-mc-action="atlas-back">${icon('back')}返回农业成果</button>${this.detailLoading?'<p class="atlas-state" role="status">正在读取成果…</p>':`<p class="atlas-error" role="alert">${esc(this.error)}</p><button class="atlas-secondary" data-mc-action="${this.expectedSha?'atlas-current':'atlas-reload'}">${this.expectedSha?'明确查看当前版本':'重试读取'}</button>`}</section>`;
  const relevant=this.products.filter(product=>!sceneId||(product.scene_ids||[]).includes(sceneId));
  const matches=this.matchingProducts(relevant),filtered=!!this.query.trim()||this.category!=='all',shown=compact&&!this.expanded&&!filtered?matches.slice(0,4):matches;
  return `<section class="agri-atlas ${compact?'atlas-compact':''}" aria-label="农业成果">
   <header class="atlas-heading"><div><span class="atlas-eyebrow">成果目录</span><h3>农业成果</h3></div>${this.loaded?`<span class="atlas-ready-count">${relevant.length}项成果</span>`:''}</header>
   ${!compact?`<p class="atlas-intro">${sceneId?'查看本专题成果，按标注日期与范围使用。':'查看成果范围、日期与计算依据。'}</p>`:''}
   ${this.loaded&&relevant.length?`<div class="atlas-picker-controls"><label class="atlas-search"><span class="sr-only">查找农业成果</span><svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="10" cy="10" r="6"/><path d="m15 15 5 5"/></svg><input id="atlas-product-search" type="search" placeholder="搜索成果、年份或指数，如 NDVI" value="${esc(this.query)}" autocomplete="off">${this.query?'<button data-mc-action="atlas-clear" aria-label="清除成果搜索与筛选">×</button>':''}</label><div class="atlas-filters" role="group" aria-label="农业成果分类">${productGroups.map(([key,label])=>`<button data-mc-action="atlas-filter" data-id="${key}" aria-pressed="${this.category===key}">${label}<small>${key==='all'?relevant.length:relevant.filter(product=>productGroup(product)===key).length}</small></button>`).join('')}</div></div>`:''}
   ${this.catalogError?`<div class="atlas-error" role="status">${esc(this.catalogError)} <button data-mc-action="atlas-reload">重新连接</button></div>`:''}
   ${this.loading&&!this.loaded?'<p class="atlas-state" role="status">正在载入成果目录…</p>':''}
   ${!this.loaded&&!this.loading&&!this.catalogError?'<button class="atlas-secondary" data-mc-action="atlas-reload">载入农业成果</button>':''}
   ${filtered?`<p class="atlas-match-count" role="status">找到${matches.length}项成果 <button data-mc-action="atlas-clear">重置筛选</button></p>`:''}
   <div class="atlas-picker-list">${shown.map(product=>this.renderRow(product)).join('')}</div>
   ${this.loaded&&relevant.length&&!matches.length?'<p class="atlas-empty">没有匹配的成果，试试“耕作地”“NDVI”或“养殖”。</p>':''}
   ${this.loaded&&!relevant.length?`<p class="atlas-empty">${sceneId?'本专题暂无成果，可选择下方工具分析或上传业务台账。':'暂无已归档成果。'}</p>`:''}
   ${compact&&!filtered&&matches.length>4?`<button class="atlas-expand" data-mc-action="atlas-expand" aria-expanded="${this.expanded}">${this.expanded?'收起成果列表':'展开全部'+matches.length+'项成果'} ${icon('arrow')}</button>`:''}
  </section>`;
 }
 renderRow(product){
  const state=status(product),noSpatial=product.layer_count===0,title=String(product.title||'未命名成果');
  const metric=cardMetrics(product)[0],value=metric?cardMetric(metric):null;
  return `<article class="atlas-product-row"><button class="atlas-row-summary" data-mc-action="atlas-open" data-id="${esc(product.id)}" aria-label="查看${esc(product.title||'成果')}详情"><strong>${esc(title)}</strong><span class="atlas-row-period">${esc(product.period_label||'时相见来源')}${typeof product.resolution_m==='number'&&Number.isFinite(product.resolution_m)?` · ${esc(number(product.resolution_m,2))}米`:''}</span><span class="atlas-row-foot"><span class="atlas-row-status atlas-row-status-${state.kind}">${esc(state.label)}</span>${value?`<span title="${esc((metric.label||'指标')+'：'+value.raw)}">${esc(value.value+value.unit)}</span>`:''}<span class="atlas-row-detail">详情 ${icon('arrow')}</span></span></button>${!noSpatial?`<button class="atlas-row-map" data-mc-action="atlas-map" data-id="${esc(product.id)}" aria-label="将${esc(product.title||'成果')}上图" ${this.mapping||product.status!=='ready'?'disabled':''}>${icon('map')}上图</button>`:`<span class="atlas-row-document">${icon('document')}资料</span>`}</article>`;
 }
 switcherMarkup(product){
  if(this.products.length<2)return '';
  const listed=this.products.some(item=>item.id===product.id);
  return `<label class="atlas-switcher"><span>切换成果</span><select data-atlas-select aria-label="切换农业成果">${!listed?`<option value="${esc(product.id)}" selected>${esc(product.title||'当前成果')}</option>`:''}${productGroups.filter(([key])=>key!=='all').map(([key,label])=>{const products=this.products.filter(item=>productGroup(item)===key);return products.length?`<optgroup label="${label}">${products.map(item=>`<option value="${esc(item.id)}" ${item.id===product.id?'selected':''}>${esc(item.title||'未命名成果')} · ${esc(item.period_label||'日期见来源')}</option>`).join('')}</optgroup>`:''}).join('')}${this.products.filter(item=>productGroup(item)==='other').map(item=>`<option value="${esc(item.id)}" ${item.id===product.id?'selected':''}>${esc(item.title||'未命名成果')}</option>`).join('')}</select></label>`;
 }
 renderCard(product){
  const state=status(product),noSpatial=product.layer_count===0,title=String(product.title||'未命名成果');
  return `<button class="atlas-product-card" data-mc-action="atlas-open" data-id="${esc(product.id)}" aria-label="查看${esc(product.title||'成果')}">
   <span class="atlas-card-top"><span class="atlas-card-identity">${icon(noSpatial?'document':'layers')}${typeof product.resolution_m==='number'&&Number.isFinite(product.resolution_m)?`<small>${esc(number(product.resolution_m,2))}米</small>`:''}</span><span class="atlas-status atlas-status-${state.kind}">${esc(state.label)}</span></span>
   <strong title="${esc(product.title||'未命名成果')}">${esc(title)}</strong>
   <span class="atlas-card-period">${esc(product.period_label||'时相见来源')}</span>
   <span class="atlas-card-scope" title="${esc(product.scope_label||'范围见成果说明')}">${esc(product.scope_label||'范围见成果说明')}</span>
   ${cardMetrics(product).length?`<span class="atlas-card-metrics">${cardMetrics(product).map((metric,index)=>{const value=cardMetric(metric);return `<span class="${index?'atlas-card-secondary':'atlas-card-headline'}" title="${esc((metric.label||'指标')+'：'+value.raw)}"><b>${esc(value.value)}<small>${esc(value.unit)}</small></b><em>${esc(metric.label||'指标')}</em></span>`;}).join('')}</span>`:''}
   ${atlasQualityMarkup(product,{compact:true})}<span class="atlas-card-action">${noSpatial?'查看资料与来源':'查看成果与来源'} ${icon('arrow')}</span>
  </button>`;
 }
 sourceMarkup(product,{expanded=false}={}){
  const provenance=product.provenance||{},sources=Array.isArray(provenance.sources)?provenance.sources:[];
  return `<details class="atlas-provenance" ${expanded?'open':''}><summary>原始数据来源与处理依据</summary>${sources.length?`<ol>${sources.slice(0,20).map((source,index)=>{const value=typeof source==='string'?{url:source}:source||{},url=safeURL(value.url||value.href),title=value.title||value.name||value.source||'来源 '+(index+1);return `<li>${url?`<a href="${esc(url)}" target="_blank" rel="noopener noreferrer">${esc(title)}</a>`:esc(title)}${value.license?`<small>${esc(value.license)}</small>`:''}</li>`}).join('')}</ol>`:'<p>此快照没有结构化来源链接，请查看归档来源记录。</p>'}${provenance.boundary_year?`<p>参考市界：${esc(provenance.boundary_year)}年公开版本，不是现行法定调查界。</p>`:''}${provenance.method?`<details class="atlas-method"><summary>处理方法</summary><p>${esc(provenance.method)}</p></details>`:''}</details>`;
 }
 renderDetail(product,{showBack=true,mapAction='atlas-map',mapAllowed=true,downloads:downloadOverride=null,notice='',errorMessage=null,focus='normal'}={}){
  const layers=Array.isArray(product.layers)?product.layers:[],hasSpatial=layers.some(spatial),state=status(product),detailError=errorMessage??this.error;
  const downloads=(downloadOverride||(Array.isArray(product.downloads)?product.downloads:[])).filter(item=>safeURL(item.url)),sourceFocus=focus==='source',downloadFocus=focus==='download',promoteMap=showBack&&!sourceFocus&&!downloadFocus;
  const mapButton=hasSpatial&&mapAllowed?`<button class="atlas-map-primary" data-mc-action="${esc(mapAction)}" data-id="${esc(product.id)}" ${this.mapping||product.status!=='ready'?'disabled':''}>${icon('map')}${this.mapping?'正在上图…':'上图查看'}</button>`:'';
  const actions=`<div class="atlas-actions">${promoteMap?'':mapButton}${!hasSpatial?'<p class="atlas-nospatial">资料未提供空间边界，可查看内容、来源与使用范围。</p>':''}${downloads.map(item=>`<a class="atlas-download" href="${esc(validAtlasVersion(product.product_sha256)?atlasPinnedURL(item.url,product.product_sha256)||safeURL(item.url):safeURL(item.url))}" download>${icon('download')}${esc(item.label||'下载成果')}${item.type?`<small>${esc(item.type)}</small>`:''}</a>`).join('')}</div>`;
  const sourceNote=product.product_type==='public_address_clues'?'公开地址候选尚需业务核实，不是全量机构或许可名录。':product.product_type==='official_project_notice'?'公告记载的是项目计划，不代表已完成或已验收，也未提供工程边界。':product.product_type==='cross_product_review'?'不同年度、不同模型的核查参考，不能据此认定耕地增减或分类准确率。':'有效覆盖率表示可参与计算的范围，不代表识别精度或官方认定。';
  return `<section class="agri-atlas atlas-detail atlas-focus-${esc(focus)}" aria-label="${esc(product.title||'农业成果详情')}">
   ${showBack?`<button class="atlas-back" data-mc-action="atlas-back">${icon('back')}返回成果列表</button>${this.switcherMarkup(product)}`:''}
   <header class="atlas-detail-heading"><span class="atlas-status atlas-status-${state.kind}">${esc(state.label)}</span><h3>${esc(product.title||'成果详情')}</h3><p>${esc(product.period_label||'日期见来源')}${typeof product.resolution_m==='number'?' · '+esc(number(product.resolution_m,2))+'米':''}</p><p>${esc(product.scope_label||'范围见来源')}</p></header>
   ${atlasQualityMarkup(product)}${notice?`<p class="atlas-version-notice" role="status">${esc(notice)}</p>`:''}${detailError?`<p class="atlas-error" role="alert">${esc(detailError)}</p>`:''}
   ${promoteMap&&mapButton?`<div class="atlas-actions atlas-selection-map">${mapButton}</div>`:''}
   ${sourceFocus?this.sourceMarkup(product,{expanded:true})+`<p class="atlas-source-note">${esc(sourceNote)}</p>`:''}
   ${downloadFocus?'<p class="atlas-download-note">文件对应本次已保存的成果版本，可选完整成果包或单项原生文件。</p>'+actions:''}
   ${!sourceFocus&&!downloadFocus?(product.description?`<p class="atlas-description">${esc(product.description)}</p>`:'')+(metrics(product).length?`<div class="atlas-detail-metrics">${metrics(product).map(metricHTML).join('')}</div>`:'<p class="atlas-intro">资料未提供定量指标，请结合来源与使用范围查看。</p>')+actions:''}
   ${!sourceFocus&&layers.length?`<details class="atlas-layers"><summary>图层与图例 · ${layers.length}个</summary>${layers.map(layer=>`<section><h4>${esc(layer.title||'成果图层')}</h4>${Array.isArray(layer.legend)&&layer.legend.length?`<div class="atlas-legend">${layer.legend.slice(0,16).map(item=>`<span><i style="background:${safeColor(item.color)}"></i>${esc(item.label||'未命名类别')}</span>`).join('')}</div>`:''}</section>`).join('')}</details>`:''}
   ${Array.isArray(product.limitations)&&product.limitations.length?sourceFocus||downloadFocus?`<details class="atlas-provenance"><summary>完整适用范围 · ${product.limitations.length}项</summary><ul>${product.limitations.map(item=>`<li>${esc(item)}</li>`).join('')}</ul></details>`:`<section class="atlas-boundaries"><h4>使用范围</h4><ul>${product.limitations.map(item=>`<li>${esc(item)}</li>`).join('')}</ul></section>`:''}
   ${!sourceFocus?this.sourceMarkup(product):''}
  </section>`;
 }
}
