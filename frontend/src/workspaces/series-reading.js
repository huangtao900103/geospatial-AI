// Compare the same archived pixel support, never substitute per-scene means.
const esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const finite=v=>typeof v==='number'&&Number.isFinite(v);
const fmt=(v,p=4)=>finite(v)?v.toLocaleString('zh-CN',{maximumFractionDigits:p}):'未知';
function archived(row,keys){for(const key of keys)if(Object.hasOwn(row,key))return finite(row[key])?row[key]:null;return null;}
export function comparableSeries(result){
 const raw=result.time_series||result.series||result.report?.time_series||[];
 if(!Array.isArray(raw))return {rows:[],commonCount:null};
 const count=result.summary?.common_support_pixel_count;
 const rows=raw.slice(0,8).map(row=>{
  const commonCount=archived(row,['common_support_pixel_count']);
  return {date:String(row.date||row.utc_date||row.datetime||'').slice(0,10),time:Date.parse(row.datetime||row.date||row.utc_date||''),common:count===0||commonCount===0?null:archived(row,['mean_common_support','mean_index_common_support']),own:archived(row,['mean_value','mean_index_all_valid','mean_index','mean_ndvi']),ratio:finite(row.valid_ratio)?row.valid_ratio:null,commonCount};
 });
 const counts=rows.map(r=>r.commonCount);
 return {rows,commonCount:finite(count)?count:counts.length&&counts.every(v=>v!==null&&v===counts[0])?counts[0]:null};
}
function observationPlot(rows,label){
 const valid=rows.filter(r=>finite(r.common)&&finite(r.time));if(!valid.length)return '';
 const values=valid.map(r=>r.common),times=valid.map(r=>r.time),lo=Math.min(...values),hi=Math.max(...values),pad=Math.max((hi-lo)*.15,.005),min=lo-pad,max=hi+pad,start=Math.min(...times),end=Math.max(...times);
 const x=r=>end===start?180:44+(r.time-start)/(end-start)*280,y=r=>112-(r.common-min)/(max-min)*84;
 const segments=rows.slice(1).map((r,i)=>finite(r.common)&&finite(r.time)&&finite(rows[i].common)&&finite(rows[i].time)?`<line x1="${x(rows[i]).toFixed(2)}" y1="${y(rows[i]).toFixed(2)}" x2="${x(r).toFixed(2)}" y2="${y(r).toFixed(2)}"/>`:'').join('');
 return `<svg class="series-observation-plot" viewBox="0 0 348 148" role="img" aria-label="${esc(label)}共同有效范围的实际观测均值"><title>实际观测；连接线仅用于阅读，非逐日监测</title><g class="series-grid"><path d="M44 28H324M44 112H324"/></g><g class="series-labels"><text x="2" y="32">${esc(fmt(max,3))}</text><text x="2" y="116">${esc(fmt(min,3))}</text><text x="44" y="140">${esc(valid[0].date)}</text><text x="324" y="140" text-anchor="end">${esc(valid.at(-1).date)}</text></g><g class="series-connections">${segments}</g><g class="series-observations">${valid.map(r=>`<circle cx="${x(r).toFixed(2)}" cy="${y(r).toFixed(2)}" r="3.5"><title>${esc(r.date)}：${esc(fmt(r.common))}</title></circle>`).join('')}</g></svg>`;
}
export function renderCommonSeries(result){
 const {rows,commonCount}=comparableSeries(result);if(!rows.length)return '';
 const label=result.summary?.index_label||result.summary?.index?.label||result.summary?.index_id?.toUpperCase()||'光谱指数';
 const noCommon=commonCount===0||rows.every(r=>r.common===null);
 return `<section class="series-reading"><div class="series-heading"><h3>实际观测</h3><span>${esc(label)} · 共同有效范围</span></div><p>${noCommon?'无共同可比范围或该成果未记录共同范围均值；保持未知，不以各景自身均值替代。':`以下均值比较同一批像元${commonCount!==null?'（'+fmt(commonCount,0)+'个）':''}，避免云量差异改变统计范围。`}</p>${noCommon?'':observationPlot(rows,label)}<table><thead><tr><th>实际日期</th><th>共同范围均值</th></tr></thead><tbody>${rows.map(r=>`<tr><td>${esc(r.date||'未记录')}</td><td>${esc(fmt(r.common))}</td></tr>`).join('')}</tbody></table><p class="series-footnote">${noCommon?'未绘制观测图；未知值不补零。':'图中只连接实际观测，不代表逐日连续监测。纵轴按本次数据自动缩放。'}</p><details><summary>逐景自身有效范围（辅助）</summary><p>各景有效范围可能不同，下列均值不宜直接作同一批地物的变化比较。</p><table><thead><tr><th>日期</th><th>自身均值</th><th>有效覆盖</th></tr></thead><tbody>${rows.map(r=>`<tr><td>${esc(r.date||'未记录')}</td><td>${esc(fmt(r.own))}</td><td>${r.ratio===null?'未知':esc(fmt(r.ratio*100,1))+'%'}</td></tr>`).join('')}</tbody></table></details></section>`;
}
