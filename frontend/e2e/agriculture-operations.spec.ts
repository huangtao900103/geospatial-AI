import {test,expect,type Page} from '@playwright/test'
import {readFile} from 'node:fs/promises'
const polygon=(w=113.3,s=30.3,e=113.305,n=30.305)=>({type:'Polygon',coordinates:[[[w,s],[e,s],[e,n],[w,n],[w,s]]]})
const feature=(code:string,geo=polygon())=>({type:'Feature',geometry:geo,properties:{code,name:'项目'+code,status:'在建'}})
const fc=(features:ReturnType<typeof feature>[])=>({type:'FeatureCollection',features})
async function open(page:Page){await page.goto('/#/map/operations');await expect(page.getByRole('heading',{name:'业务工作台',exact:true})).toBeVisible();await expect(page.locator('.ops-module')).toHaveCount(9);await page.getByLabel('操作人',{exact:true}).fill('隔离测试经办员')}
async function seed(page:Page,name:string){
 const response=await page.request.post('/api/agri-operations/registries',{data:{name,kind:'high_standard',source:'浏览器隔离台账，非正式业务资料',year:2026,actor:'测试建档'}});expect(response.ok()).toBeTruthy();const registry=await response.json()
 const imported=await page.request.post('/api/agri-operations/registries/'+registry.id+'/import',{data:{geojson:fc([feature('A'),feature('B',polygon(113.4,30.4,113.405,30.405))]),code_field:'code',name_field:'name',status_field:'status',batch_id:'test',expected_revision:1,actor:'测试建档'}});expect(imported.ok()).toBeTruthy();return registry
}
test.beforeEach(async({page})=>{await page.route('**/*',r=>new URL(r.request().url()).hostname==='127.0.0.1'?r.continue():r.abort())})

test('large GeoJSON is actually ingested in batches, queried and mapped',async({page})=>{
 await open(page);await page.getByRole('tab',{name:'台账',exact:true}).click();await page.getByText('新建台账',{exact:true}).click()
 await page.getByLabel('台账名称',{exact:true}).fill('批量地块建档600')
 await page.getByLabel('台账来源',{exact:true}).fill('隔离输入，非仙桃实际地块')
 await page.getByRole('button',{name:'建立台账',exact:true}).click()
 await expect(page.locator('.ops-notice')).toContainText('台账已建立')
 await page.getByText('导入业务对象',{exact:true}).click()
 await page.getByLabel('业务编码字段',{exact:true}).fill('code');await page.getByLabel('名称字段（可选）').fill('name');await page.getByLabel('状态字段（可选）').fill('status')
 const content=fc(Array.from({length:600},(_,i)=>feature(String(i))))
 await page.getByLabel('批量GeoJSON文件').setInputFiles({name:'600-fields.geojson',mimeType:'application/geo+json',buffer:Buffer.from(JSON.stringify(content))})
 await expect(page.locator('.ops-notice')).toContainText('共核验 600 个对象')
 await expect(page.locator('.ops-list-toolbar')).toContainText('共600个对象')
 await page.getByLabel('查找业务对象').fill('599');await page.getByRole('button',{name:'查询对象',exact:true}).click()
 await expect(page.locator('.ops-card')).toHaveCount(1);await expect(page.locator('.ops-card')).toContainText('项目599')
 await page.getByRole('button',{name:'定位对象',exact:true}).click();await expect(page.locator('.mw-owned-layers')).toContainText('项目599')
 await page.getByRole('button',{name:'更新档案',exact:true}).click();await page.getByLabel('业务状态',{exact:true}).fill('已建');await page.getByLabel('进度百分比').fill('100');await page.getByLabel('更新说明').fill('隔离测试更新，非工程验收认定')
 await page.getByRole('button',{name:'保存档案更新',exact:true}).click();await expect(page.locator('.ops-card')).toContainText('已建')
 await page.getByRole('button',{name:'定位对象',exact:true}).click();await expect(page.locator('.mw-owned-layers')).toContainText('业务台账')
 await page.screenshot({path:'output/playwright/agriculture-registry.png'})
})

test('event intersection, dispatch, field receipt, review and archive form a real loop',async({page})=>{
 const registry=await seed(page,'灾害核查项目台账')
 await page.addInitScript(()=>localStorage.setItem('geoai-v9-selected-area',JSON.stringify({name:'隔离核查范围',bbox:[113.299,30.299,113.31,30.31]})))
 await open(page);await page.getByRole('tab',{name:'防灾核查',exact:true}).click();await page.getByText('登记核查事件',{exact:true}).click()
 await page.getByLabel('事件名称',{exact:true}).fill('隔离强降雨范围研判');await page.getByLabel('来源单位或材料').fill('测试录入的核查材料')
 await page.getByRole('button',{name:'保存核查事件',exact:true}).click();await expect(page.locator('.ops-notice')).toContainText('已登记人工研判事件')
 await page.getByRole('checkbox',{name:/灾害核查项目台账/}).check();await page.getByRole('button',{name:'分析待核查对象',exact:true}).click()
 await expect(page.getByRole('heading',{name:'空间相交 1个对象',exact:true})).toBeVisible()
 await expect(page.locator('.ops-scroll')).toContainText('不是实际受灾对象')
 const gisPending=page.waitForEvent('download');await page.getByRole('link',{name:'下载全部待核查对象GeoJSON'}).click();const gisPath=await (await gisPending).path();expect(JSON.parse(await readFile(gisPath!,'utf8')).features).toHaveLength(1)
 await page.getByLabel('责任单位或人员',{exact:true}).fill('核查一组');await page.getByRole('button',{name:'派发核查任务',exact:true}).click()
 await page.getByRole('button',{name:'办理任务',exact:true}).click()
 await page.getByLabel('处理或审核说明').fill('已接收核查任务');await page.getByRole('button',{name:'接单处理',exact:true}).click()
 await page.getByLabel('处理或审核说明').fill('现场排水通畅，已完成核查');await page.getByLabel('现场核查或执行结果').selectOption('normal');await page.getByRole('button',{name:'提交现场回执',exact:true}).click()
 await page.getByLabel('操作人',{exact:true}).fill('隔离测试审核员');await page.getByLabel('处理或审核说明').fill('资料齐全，核查结果已复核');await page.getByRole('button',{name:'审核归档',exact:true}).click()
 await expect(page.locator('.ops-card')).toContainText('已归档');await expect(page.locator('.ops-feedback')).toContainText('现场排水通畅')
 await page.screenshot({path:'output/playwright/agriculture-archived-task.png'})
 await page.getByRole('tab',{name:'防灾核查',exact:true}).click()
 const pending=page.waitForEvent('download');await page.getByRole('button',{name:'导出核查档案',exact:true}).click();const path=await (await pending).path(),record=JSON.parse(await readFile(path!,'utf8'))
 expect(record.tasks).toHaveLength(1);expect(record.tasks[0].status).toBe('closed');expect(record.tasks[0].feedback.finding).toBe('normal');expect(record.impacts[0].objects[0].registry_id).toBe(registry.id);expect(record.audit.length).toBeGreaterThanOrEqual(5)
 await page.screenshot({path:'output/playwright/agriculture-event.png'})
})

test('a stale analysis cannot dispatch tasks after its objects have changed',async({page})=>{
 const registry=await seed(page,'版本核查台账')
 const event=await (await page.request.post('/api/agri-operations/events',{data:{title:'版本核查事件',hazard:'flood',geometry:polygon(),source_name:'隔离测试',source_kind:'manual',issued_at:'2026-11-17T12:00:00+08:00',actor:'test'}})).json()
 await open(page);await page.getByRole('tab',{name:'防灾核查',exact:true}).click();await page.getByLabel('选择核查事件').selectOption(event.id);await page.getByRole('checkbox',{name:/版本核查台账/}).check();await page.getByRole('button',{name:'分析待核查对象',exact:true}).click()
 await expect(page.getByRole('heading',{name:'空间相交 1个对象',exact:true})).toBeVisible()
 const objects=await (await page.request.get('/api/agri-operations/objects?registry_id='+registry.id)).json(),obj=objects.items[0]
 await page.request.patch('/api/agri-operations/objects/'+obj.id,{data:{expected_revision:obj.revision,actor:'另一个经办人',status:'更新状态',note:'后续更新'}})
 await page.getByLabel('责任单位或人员',{exact:true}).fill('核查组');await page.getByRole('button',{name:'派发核查任务',exact:true}).click()
 await expect(page.getByRole('alert')).toContainText('记录已更新')
})

test('production guidance is drafted, reviewed and assigned to actual objects',async({page})=>{
 const registry=await seed(page,'生产指导对象台账');await open(page);await page.getByRole('tab',{name:'生产指导',exact:true}).click()
 await page.getByLabel('指导标题').fill('排水沟渠检查安排');await page.getByLabel('指导对象台账').selectOption(registry.id);await page.getByLabel('指导内容').fill('组织现场检查排水沟渠，记录通畅情况并回传核查资料。')
 await page.getByRole('button',{name:'保存指导草稿',exact:true}).click();await expect(page.locator('.ops-card')).toContainText('待审核')
 await page.getByLabel('执行单位',{exact:true}).fill('农业服务组');await page.getByLabel('操作人',{exact:true}).fill('指导审核员')
 await page.getByRole('button',{name:'审核发布指导',exact:true}).click();await expect(page.locator('.ops-card')).toContainText('已生成2个执行任务')
 await page.getByRole('tab',{name:'任务',exact:true}).click();await page.getByLabel('查找业务任务').fill('排水沟渠检查安排');await expect(page.locator('.ops-card')).toHaveCount(2)
})

test('business refresh failure retains previous content and mobile layout fits',async({page})=>{
 await page.setViewportSize({width:390,height:844});await open(page)
 await page.getByRole('button',{name:'收起农业业务面板'}).click();await expect(page.locator('.agriculture-operations')).toBeHidden()
 await page.getByRole('button',{name:'打开导航',exact:true}).click();await page.getByRole('button',{name:'农业业务',exact:true}).click();await expect(page.locator('.agriculture-operations')).toBeVisible()
 await page.screenshot({path:'output/playwright/agriculture-mobile-clean.png'})
 await page.route('**/api/agri-operations/registries',route=>route.fulfill({status:503,json:{error:{message:'隔离测试暂时离线'}}}))
 await page.getByRole('button',{name:'刷新业务',exact:true}).click();await expect(page.getByRole('alert')).toContainText('已有内容保留');await expect(page.locator('.ops-module')).toHaveCount(9)
 expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true)
 await page.screenshot({path:'output/playwright/agriculture-mobile.png'})
})
