import {test,expect,type Page} from '@playwright/test'
import {readFile} from 'node:fs/promises'

test.beforeEach(async({page})=>{
 // The server creates a real tiny raster in a temporary workspace. No production data is used.
 await page.route('**/*',route=>new URL(route.request().url()).hostname==='127.0.0.1'?route.continue():route.abort())
})
async function createQuestion(page:Page,question:string,context:Record<string,unknown>){
 const response=await page.request.post('/api/assistant/questions',{data:{question,context}})
 expect(response.ok()).toBeTruthy();const created=await response.json()
 await expect.poll(async()=>{const current=await (await page.request.get('/api/assistant/questions/'+created.id)).json();return current.status},{timeout:20000}).not.toBe('processing')
 return (await page.request.get('/api/assistant/questions/'+created.id)).json()
}
async function openQuestion(page:Page,question:string){
 await page.goto('/#/map/history')
 const history=page.locator('.vue-task-history');await expect(history).toBeVisible()
 await history.getByLabel('查找任务与成果').fill(question)
 await history.getByRole('button',{name:new RegExp(question)}).click()
 await expect(page.locator('.map-copilot')).toBeVisible()
}

async function typeQuestion(page:Page,text:string){
 const fold=page.locator('.mc-question-fold');if((await fold.getAttribute('open'))===null)await fold.locator(':scope > summary').click()
 const pending=page.waitForResponse(response=>response.url().endsWith('/api/assistant/questions')&&response.request().method()==='POST')
 await page.locator('#mc-question').fill(text);await page.locator('#mc-question').press('Enter')
 const response=await pending;expect(response.ok()).toBeTruthy();const submitted=response.request().postDataJSON(),created=await response.json()
 await expect.poll(async()=>{const current=await (await page.request.get('/api/assistant/questions/'+created.id)).json();return current.status},{timeout:20000}).not.toBe('processing')
 return {question:await (await page.request.get('/api/assistant/questions/'+created.id)).json(),submitted}
}

test('a question connects native index to Otsu, preserves evidence, maps and downloads actual GIS output',async({page})=>{
 const text='对所选成果计算NDVI并执行Otsu自动阈值分区'
 await openQuestion(page,'浏览器隔离原生光谱源成果')
 await page.locator('.mc-question-fold > summary').click()
 await expect(page.locator('.mc-query-source')).toContainText('当前光谱成果')
 const {question,submitted}=await typeQuestion(page,text)
 expect(submitted.context.source_type).toBe('production');expect(submitted.context.source_task_id).toBe('production_browser_spectral')
 expect(submitted.context.bbox).toBeUndefined();expect(submitted.context.period).toBeUndefined()
 expect(question.status,question.message).toBe('answered')
 expect(question.workflow_ref?.id).toBeTruthy()
 const workflow=await (await page.request.get('/api/analysis-workflows/'+question.workflow_ref.id)).json()
 expect(workflow.status).toBe('succeeded');expect(workflow.nodes).toHaveLength(2)
 for(const node of workflow.nodes){expect(node.status).toBe('succeeded');expect(node.evidence?.record_sha256).toMatch(/^[a-f0-9]{64}$/)}
 expect(workflow.result.summary.algorithm_id).toBe('otsu_segmentation')
 expect(Number.isFinite(workflow.result.summary.threshold)).toBe(true)
 expect(question.answer.result_ref.type).toBe('raster_processing')
 await expect(page.locator('.mc-answer h2')).toContainText('阈值')
 const disclosure=page.locator('.mc-workflow-disclosure');await expect(disclosure).toContainText('2/2 步')
 await disclosure.locator(':scope > summary').click()
 await expect(page.locator('.mc-workflow-nodes > li.succeeded')).toHaveCount(2)
 await expect(page.locator('.mc-workflow')).toContainText('产物文件已核验')
 await expect(page.locator('.mc-result-notice')).toContainText('不等于农田')
 await page.locator('.mc-workflow').scrollIntoViewIfNeeded()
 await page.screenshot({path:'output/playwright/analysis-workflow-process.png'})
 const downloaded=page.waitForEvent('download');await page.locator('.mc-bundle-link').click()
 const path=await (await downloaded).path();expect((await readFile(path!)).subarray(0,2).toString()).toBe('PK')
 await page.getByRole('button',{name:'查看地图',exact:true}).click()
 await page.getByRole('button',{name:'收起分析面板查看地图',exact:true}).click()
 await expect(page.locator('.mw-owned-layers')).toContainText(/Otsu|阈值/)
 const image=page.locator('.mw-native-analysis-image').last();await expect(image).toBeVisible()
 await expect.poll(async()=>{const box=await image.boundingBox();return !!box&&box.width>80&&box.height>80}).toBe(true)
 await page.screenshot({path:'output/playwright/analysis-workflow-map.png'})
})

test('a missing source requests data and cannot display a fabricated map or completed analysis',async({page})=>{
 const text='读取观测计算NDVI再执行Otsu自动阈值分区'
 const question=await createQuestion(page,text,{})
 expect(question.status,question.message).toBe('needs_data')
 expect(question.answer).toBeNull()
 await openQuestion(page,text)
 await expect(page.locator('.mc-status-card')).toContainText('请补充分析条件')
 await expect(page.locator('.mc-status-card > p')).toBeInViewport()
 await expect(page.locator('.mc-status-card > p')).toContainText('真实观测')
 await expect(page.locator('.mc-answer')).toHaveCount(0)
 await expect(page.locator('.mc-bundle-link')).toHaveCount(0)
 await expect(page.getByRole('button',{name:'查看地图',exact:true})).toHaveCount(0)
 await page.screenshot({path:'output/playwright/analysis-workflow-needs-data.png'})
})


test('releasing the visible production source prevents implicit reuse from the input box',async({page})=>{
 await openQuestion(page,'浏览器隔离原生光谱源成果')
 await page.locator('.mc-question-fold > summary').click()
 await expect(page.locator('.mc-query-source')).toContainText('当前光谱成果')
 await page.getByRole('button',{name:'解除查询来源',exact:true}).click()
 await expect(page.locator('.mc-query-source')).toHaveCount(0)
 await expect(page.locator('#mc-question')).toBeVisible()
 const {question,submitted}=await typeQuestion(page,'对当前成果计算NDVI并执行Otsu自动阈值分区')
 expect(submitted.context?.source_task_id).toBeUndefined()
 expect(question.status,question.message).toBe('needs_data')
 await expect(page.locator('.mc-status-card')).toContainText('请补充分析条件')
 await expect(page.locator('.mc-answer')).toHaveCount(0)
})
