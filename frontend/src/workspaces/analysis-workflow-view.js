// Present only persisted processing states and measured diagnostic values.
const esc=value=>String(value??'').replace(/[&<>"']/g,char=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[char]));
const finite=value=>typeof value==='number'&&Number.isFinite(value);
const number=(value,digits=4)=>finite(value)?value.toLocaleString('zh-CN',{maximumFractionDigits:digits}):'未知';
const labels={queued:'等待处理',planning:'校验条件',running:'处理中',dispatching:'正在提交',succeeded:'已完成',completed:'已完成',failed:'未完成',cancelled:'已取消',pending:'待执行',blocked:'前置未完成',needs_data:'待补数据',unsupported:'暂不支持',skipped:'已跳过'};
const state=value=>Object.hasOwn(labels,value)?value:'pending';
const done=value=>['succeeded','completed'].includes(value);
const strings=value=>Array.isArray(value)?value.filter(item=>typeof item==='string'):[];
const qualityFields={qa_valid_ratio:'质量合格像元覆盖率',ndvi_valid_ratio:'NDVI有效覆盖率',ndmi_valid_ratio:'NDMI有效覆盖率'};
function qualityReason(reason){
 if(typeof reason==='string')return reason;
 if(!reason||typeof reason!=='object')return '未记录排除原因';
 const field=typeof reason.field==='string'?reason.field:'',ratio=Object.hasOwn(qualityFields,field),label=ratio?qualityFields[field]:field||'质量指标';
 const value=finite(reason.value)?number(reason.value*(ratio?100:1),2)+(ratio?'%':''):'未知';
 const minimum=finite(reason.minimum)?number(reason.minimum*(ratio?100:1),2)+(ratio?'%':''):null;
 return `${label}为${value}${minimum===null?'，未记录准入门槛':'，要求至少'+minimum}`;
}
function excludedObservation(item){
 if(typeof item==='string')return item;
 if(!item||typeof item!=='object')return '未记录排除原因';
 const direct=typeof item.reason==='string'?item.reason:typeof item.message==='string'?item.message:null;
 const reason=direct||(Array.isArray(item.exclusion_reasons)?item.exclusion_reasons.map(qualityReason).join('；'):'未记录排除原因');
 return [typeof item.date==='string'?item.date:'',reason].filter(Boolean).join(' · ');
}
export function frozenTrendURL(value,reference){
 const id=reference?.id;if(typeof id!=='string'||!(/^[a-zA-Z0-9][a-zA-Z0-9._-]{0,120}$/).test(id)||id.includes('..'))return null;
 const expected='/api/agri-trends/'+encodeURIComponent(id);return value===expected?expected:null;
}
// Match only the registered new single-date spectral-to-partition request.
export function spectralChainRequest(text){
 const value=String(text||''),lower=value.toLowerCase();
 if(!/(otsu|自动阈值分区|自适应阈值分区)/i.test(value)||/什么是|是什么意思|如何使用|怎么使用|原理|公式|介绍|解释|能否实现|可以实现吗/.test(value))return false;
 return /先.+(?:再|然后)|(?:计算|生成|生产|获取|读取).+(?:并|再|然后|后).*(?:otsu|阈值分区)|连续处理|连续分析/.test(lower);
}
export function explicitlySelectedResult(text){return /所选成果|当前成果|这份结果|这个结果|所选结果/.test(String(text||''));}
function explicitIndex(text){
 return /(?<![a-z0-9])(?:ndvi|ndwi|mndwi|ndmi|ndbi|nbr|evi2?|savi|msavi2?|gndvi|ndre|bsi|osavi|lai|ndci|awei|gci|fvc|ndsi)(?![a-z0-9])/i.test(text)||/归一化|水体指数|水分指数|植被含水指数|燃烧比|增强植被指数|土壤调节植被指数|红边植被指数|裸土指数|裸土光谱指数|叶面积指数|植被覆盖度/.test(text);
}
export function spectralChainContext(text,source){
 if(!spectralChainRequest(text)||!source)return null;
 if(source.type==='production'&&!explicitlySelectedResult(text)||source.type==='observation'&&explicitlySelectedResult(text))return null;
 const context={...source.context};if(context.period)context.period={...context.period};
 if(explicitIndex(text))delete context.index_id;
 return context;
}
export function workflowReference(question){return question?.workflow_ref||question?.answer?.workflow_ref||null;}
export function renderAnalysisWorkflow(job,{expanded=false}={}){
 if(!job||!Array.isArray(job.nodes))return '';
 const nodes=job.nodes,completed=nodes.filter(node=>done(node.status)).length;
 const content=`${expanded?`<div class="mc-workflow-heading"><b>分析过程</b><span>${completed} / ${nodes.length} 步完成</span></div>`:''}<ol class="mc-workflow-nodes">${nodes.map((node,index)=>`<li class="${state(node.status)}"><span aria-hidden="true">${done(node.status)?'✓':index+1}</span><div><b>${esc(node.label||node.name||'处理步骤')}</b><small>${esc(labels[state(node.status)])}${node.reused?' · 沿用已校验结果':''}</small>${node.message?`<p>${esc(node.message)}</p>`:''}${node.evidence?`<p class="mc-workflow-validation">${Array.isArray(node.evidence.artifacts)?esc(node.evidence.artifacts.length)+'个产物文件已核验 · ':''}来源与内容版本已记录</p>`:node.validation?.message?`<p class="mc-workflow-validation">${esc(node.validation.message)}</p>`:''}</div></li>`).join('')}</ol><details class="mc-workflow-evidence" data-mc-detail="workflow-evidence"><summary>输入、来源与校验记录</summary><p>处理按已保存的条件衔接；前置结果未通过校验时，不继续下游计算。</p><pre>${esc(JSON.stringify({workflow_id:job.id,version:job.version,plan_sha256:job.plan_sha256,parent_workflow_id:job.parent_workflow_id,nodes:nodes.map(node=>({id:node.id,tool_id:node.tool_id,status:node.status,binding:node.binding,result_ref:node.result_ref||node.output_ref,request:node.request,evidence:node.evidence,input_sha256:node.input_sha256,output_sha256:node.output_sha256,validation:node.validation,reused:node.reused})),evidence:job.evidence,error:job.error},null,2))}</pre></details>`;
 return expanded?`<section class="mc-workflow" aria-label="分析过程">${content}</section>`:`<details class="mc-workflow-disclosure" data-mc-detail="workflow"><summary>分析过程 <span>${completed}/${nodes.length} 步 · ${esc(labels[state(job.status)])}</span></summary><section class="mc-workflow">${content}</section></details>`;
}
export function renderTrendDiagnostics(record){
 if(!record||typeof record!=='object')return '';
 const result=record.result||record,dates=Array.isArray(result.actual_dates)?result.actual_dates:[],period=result.period||{start:dates[0],end:dates.at(-1)},support=result.support||{},slope=result.slope_per_day,change=result.change_per_30_days,count=result.observation_count,span=result.span_days,gap=result.max_gap_days;
 const index=String(result.index_id||'').toUpperCase(),limitations=strings(result.limitations),excluded=Array.isArray(result.excluded_observations)?result.excluded_observations:[];
 return `<section class="mc-trend" aria-label="稳健趋势诊断"><div class="mc-trend-heading"><h3>所选时段趋势</h3><span>${esc(index)} · Sen 斜率</span></div><div class="mc-trend-metrics"><div><span>每日变化率</span><b>${esc(number(slope,6))}<small> 指数 / 天</small></b></div><div><span>30天折算变化</span><b>${esc(number(change,5))}<small> 指数</small></b></div></div><p>${esc(period.start||'日期未记录')} 至 ${esc(period.end||'日期未记录')} · ${esc(number(count,0))}个有效日期 · 跨度${esc(number(span,0))}天${finite(gap)?' · 最大间隔'+esc(number(gap,0))+'天':''}</p><p>共同范围${finite(support.common_support_area_ha)?' '+esc(number(support.common_support_area_ha,2))+' 公顷':finite(result.common_support_area_ha)?' '+esc(number(result.common_support_area_ha,2))+' 公顷':''}${finite(support.common_support_pixel_count)?' · '+esc(number(support.common_support_pixel_count,0))+' 像元':finite(result.common_support_pixel_count)?' · '+esc(number(result.common_support_pixel_count,0))+' 像元':''}；缺测不插值。</p><p class="mc-trend-note">30天折算按本次斜率换算，不代表未来预测。稀疏观测可能包含正常物候变化，负斜率不代表单调退化。显著性未评估；不能据此认定作物面积、产量或灾损。</p>${limitations.length||excluded.length?`<details data-mc-detail="trend-quality"><summary>质量条件与适用范围${excluded.length?' · '+excluded.length+'个日期未参与':''}</summary>${limitations.map(value=>`<p>${esc(value)}</p>`).join('')}${excluded.length?`<ul>${excluded.map(item=>`<li>${esc(excludedObservation(item))}</li>`).join('')}</ul>`:''}</details>`:''}</section>`;
}
