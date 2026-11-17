"""Portable, verifiable exports of completed results, using fixed local artifacts.

No config, database, remote URL, user filename or arbitrary filesystem path is
accepted. A bundle attests its byte contents; it is not an official review or a
claim that a land-cover classification has independently validated accuracy.
"""
import copy
import csv
import io
import hashlib
import json
import os
import re
import stat
import threading
import uuid
import zipfile
from pathlib import Path

from backend.core.geo_services import GeoError,finite,digest
from backend.agriculture.agriculture_services import FILES as AGRICULTURE_FILES
from backend.analysis.raster_processing_services import NAMES as PROCESSING_NAMES, files as processing_files

VERSION='1.0'
FILES={
    'cropland':('cropland.geojson','classification.tif','patches.csv','row_counts.csv','overlay.png','classes.png','result.json'),
    'production':('analysis.tif','overlay.png','result.json'),
    'landcover':('classification.tif','row_counts.csv','overlay.png','classes.png','result.json'),
    'analysis':(),
    'agriculture':(),
    'raster_processing':(),
}
MAX_TOTAL_BYTES=512*1024*1024
CHUNK=1024*1024


def canonical(value):
    return json.dumps(value,sort_keys=True,ensure_ascii=False,allow_nan=False,indent=2).encode('utf-8')


def bundle_link(kind,task_id):
    return {'label':'完整GIS成果与复核资料包','url':'/api/results/'+kind+'/'+task_id+'/bundle.zip','type':'zip'}


def observation_dates(provenance):
    scenes=(provenance.get('source_details') or {}).get('scenes',{})
    pairs=scenes.items() if isinstance(scenes,dict) else enumerate(scenes,1) if isinstance(scenes,list) else []
    values={str(epoch):scene.get('datetime') for epoch,scene in pairs if isinstance(scene,dict) and scene.get('datetime')}
    if not values:
        actual=provenance.get('actual_dates') or (provenance.get('source_ref') or {}).get('actual_dates') or {}
        if isinstance(actual,dict):values={str(epoch):value for epoch,value in actual.items() if isinstance(value,str) and value}
        elif isinstance(actual,list):values={str(number):value for number,value in enumerate(actual,1) if isinstance(value,str) and value}
    return values


def readable_summary(kind,task,result,inputs,provenance,year):
    """Business-readable values from the same frozen task, without new inference."""
    summary=result.get('summary') or {}
    def amount(value,unit='',places=2):
        return (format(value,','+'.'+str(places)+'f')+(' '+unit if unit else '')) if finite(value) else '未提供'
    product=provenance.get('product') or inputs.get('product') or {}
    bbox=inputs.get('bbox') or (inputs.get('aoi') or {}).get('bbox') or provenance.get('selected_bbox') or provenance.get('bbox') or provenance.get('source_bbox') or (provenance.get('boundary_source') or {}).get('bbox')
    dates=observation_dates(provenance)
    source=product.get('name') or provenance.get('source') or provenance.get('provider') or provenance.get('source_id') or (provenance.get('source_ref') or {}).get('source_id') or ((provenance.get('source_ref') or {}).get('type','')+'归档原生遥感成果' if kind=='agriculture' else '见provenance.json')
    if not isinstance(source,str):source=json.dumps(source,ensure_ascii=False)
    lines=['业务摘要：',
           '原始需求（共享真实子任务创建时归档）：'+str(inputs.get('requirement') or '未归档；下方保留实际数据版本与算法'),
           '归档区域：'+str(inputs.get('region') or ('用户地图选区' if bbox else '按所选用户数据版本')),
           '冻结选区（WGS84，西/南/东/北）：'+(json.dumps(bbox,ensure_ascii=False) if bbox else '未归档矩形范围'),
           '数据产品/来源：'+source,
           '分类产品年度：'+str(year)+' 年' if year is not None else '产品年度：不适用；按实际影像日期或用户数据版本解释',
           '实际影像观测：'+('；'.join(epoch+' = '+str(value) for epoch,value in dates.items()) if dates else '无单景观测日期；年度分类产品或用户数据以其版本说明为准')]
    if kind in ('cropland','landcover'):
        lines.extend(['耕作地/作物覆盖参考面积：'+amount(summary.get('cropland_ha'),'公顷')+'（'+amount(summary.get('cropland_mu'),'亩')+'）',
                      '连通分类图斑数：'+(amount(summary.get('patch_count'),'个',0) if kind=='cropland' else '全市分类栅格统计，未生成逐图斑边界'),
                      '有效分类覆盖：'+amount(summary.get('coverage_ratio')*100 if finite(summary.get('coverage_ratio')) else None,'%')+'（数据完整性，不是分类准确率）',
                      '未知/缺测面积：'+amount(summary.get('unknown_area_ha',summary.get('nodata_area_ha')),'公顷')])
        if finite(summary.get('cloud_area_ha')):lines.append('其中云遮挡未知面积：'+amount(summary['cloud_area_ha'],'公顷'))
        method=summary.get('method') or provenance.get('method') or inputs.get('method') or '见来源与方法记录'
        lines.append('面积方法：'+str(method))
        area_method=summary.get('area_method') or provenance.get('area_method') or inputs.get('area_method')
        if area_method:lines.append('面积方法标识：'+str(area_method))
        native_crs=summary.get('native_crs') or provenance.get('native_crs')
        if native_crs:lines.append('原生坐标参考：'+str(native_crs))
        lines.append('业务解释：年度分类产品参考图斑不等于确权宗地，不能单凭本结果认定当期农田、法定耕地或分类精度。')
    elif kind=='production':
        index=summary.get('index') or provenance.get('algorithm') or inputs.get('index') or {}
        index_label=index.get('label') or inputs.get('index_id','光谱指数').upper()
        lines.extend(['执行指数：'+str(index_label),
                      '有效像元平均值：'+amount(summary.get('mean_index',summary.get('mean_ndvi')),'无量纲',4),
                      '有效像元：'+amount(summary.get('valid_count'),'个',0),
                      '阈值命中投影格网面积：'+amount(summary.get('selected_area_ha'),'公顷'),
                      '阈值/关系：'+json.dumps(inputs.get('selection') or {'threshold':inputs.get('threshold')},ensure_ascii=False)])
        if finite(summary.get('mean_delta')):lines.append('两期指数平均变化：'+amount(summary['mean_delta'],'无量纲',4))
        lines.append('业务解释：指数与条件命中格网为光谱线索，不能直接作为农田分类面积、受灾损失或法定调查结论。')
    elif kind=='raster_processing':
        algorithm=inputs.get('algorithm_id');source_ref=provenance.get('source_ref') or inputs.get('source') or {}
        index=summary.get('index') or provenance.get('index') or {}
        lines.extend(['执行算法：'+PROCESSING_NAMES.get(algorithm,str(algorithm)),
                      '执行指数：'+str(summary.get('index_label') or index.get('label') or summary.get('index_id',inputs.get('index_id','')).upper()),
                      '有效结果像元：'+amount(summary.get('valid_pixel_count',summary.get('valid_count')),'个',0),
                      '有效结果覆盖：'+amount(summary.get('valid_ratio')*100 if finite(summary.get('valid_ratio')) else None,'%')+'（支持与数据完整性，不是模型精度）',
                      '未知/无足够支持面积：'+amount(summary.get('unknown_area_ha'),'公顷'),
                      '执行位置：本机登记的固定算法；未调用OpenGE或Google Earth Engine云端服务。'])
        if algorithm=='otsu_segmentation':
            lines.extend(['源任务及栅格SHA256：'+str(source_ref.get('task_id','未提供'))+' / '+str(source_ref.get('artifact_sha256','未提供')),
                          '自动阈值与关系：'+amount(summary.get('threshold'),'指数值',8)+' / '+str(summary.get('threshold_operator','见来源报告')),
                          '保留条件格网面积：'+amount(summary.get('selected_area_ha'),'公顷'),
                          '原始命中/筛除像元：'+amount(summary.get('raw_selected_pixel_count'),'个',0)+' / '+amount(summary.get('removed_pixel_count'),'个',0),
                          '最小图斑像元数：'+amount(summary.get('min_patch_pixels'),'个',0),
                          '保留连通条件图斑：'+amount(summary.get('patch_count'),'个',0),
                          'GeoTIFF保留筛选后/原始命中掩膜、质量与AOI掩膜、源指数；分区并非农田、水体、撂荒或灾损分类。'])
        else:
            trend=algorithm=='temporal_trend';mean=summary.get('mean_value',summary.get('mean_slope_per_day') if trend else summary.get('mean_index'))
            lines.extend(['实际独立UTC观测日期：'+amount(summary.get('unique_date_count',summary.get('scene_count')),'景',0),
                          '每像元最低有效观测支持：'+amount(summary.get('min_observations',summary.get('minimum_observations')),'次',0),
                          ('平均线性变化率：' if trend else '合成有效像元平均值：')+amount(mean,'指数/天' if trend else '指数值',8),
                          '时间原点：'+str(summary.get('time_origin','不适用或见来源记录')),
                          '逐景CSV同时区分各景全部有效像元与所有日期共同支持范围；两种区域均值不可混用。',
                          'GeoTIFF保留主值、有效观测次数、选区/有效掩膜及逐景指数，趋势另保留截距和拟合诊断；不足支持保持未知。',
                          '多景归约只代表锁定观测的像元统计，不代表逐日连续覆盖、整月实测均值、长期趋势或作物产量。'])
        lines.append('业务解释：统计、阈值及变化率均不直接认定法定耕地、作物类别、受灾损失或独立模型精度。')
    elif kind=='agriculture':
        source_ref=provenance.get('source_ref') or inputs.get('source') or {}
        lines.extend(['源任务：'+str(source_ref.get('type',''))+'/'+str(source_ref.get('task_id','未提供')),
                      '源栅格SHA256：'+str(source_ref.get('artifact_sha256','未提供')),
                      '面积方法：'+str(summary.get('area_method') or provenance.get('area_method') or '未提供')])
        if inputs.get('algorithm_id')=='fvc':
            lines.extend(['执行方法：显式NDVI端元的线性像元二分FVC估算',
                          '土壤/全植被NDVI端元：'+str(inputs.get('soil_ndvi'))+' / '+str(inputs.get('vegetation_ndvi'))+'（用户提供，未做本地校准）',
                          '有效像元平均FVC：'+amount(summary.get('mean_fvc'),'比例',4),
                          '有效像元：'+amount(summary.get('valid_pixel_count'),'个',0),
                          '低端/高端裁剪像元：'+amount(summary.get('clamped_low_count'),'个',0)+' / '+amount(summary.get('clamped_high_count'),'个',0),
                          'FVC加权植被等效面积估计：'+amount(summary.get('estimated_vegetation_area_ha'),'公顷')+'（非耕地分类面积或实测面积）',
                          '未知/缺测面积：'+amount(summary.get('unknown_area_ha'),'公顷'),
                          'GeoTIFF保留FVC、未裁剪FVC、AOI/有效掩膜及源NDVI，可复算端元和裁剪规则。'])
        else:
            dataset=inputs.get('dataset') or {}
            lines.extend(['执行方法：原生像元中心逐地块分区统计',
                          '用户地块数据及版本：'+str(dataset.get('name') or dataset.get('id','未提供'))+' / '+str(dataset.get('version','未提供')),
                          '地块数：'+amount(summary.get('zone_count'),'个',0),
                          '部分覆盖/范围外地块：'+amount(summary.get('partial_zone_count'),'个',0)+' / '+amount(summary.get('outside_zone_count'),'个',0),
                          '地块像元并集覆盖面积：'+amount(summary.get('unique_covered_area_ha'),'公顷'),
                          '地块像元并集有效面积：'+amount(summary.get('unique_valid_area_ha'),'公顷'),
                          '地块像元并集未知面积：'+amount(summary.get('unique_unknown_area_ha'),'公顷'),
                          '逐地块面积之和可重复计入重叠地块；并集面积另列，不能混用。',
                          '部分覆盖地块只统计源AOI交集，未覆盖部分不外推；覆盖率不代表分类精度。'])
            if finite(summary.get('unique_selected_area_ha')):lines.append('并集阈值命中格网面积：'+amount(summary['unique_selected_area_ha'],'公顷')+'（非作物分类面积）')
            if finite(summary.get('unique_cropland_area_ha')):lines.append('并集源产品耕作地类别参考面积：'+amount(summary['unique_cropland_area_ha'],'公顷')+'（非当期法定耕地）')
        lines.append('业务解释：农业派生结果绑定已归档源版本；不能直接作为产量、灾损、法定耕地或独立精度结论。')
    else:
        lines.extend(['输出要素数：'+amount(result.get('feature_count',summary.get('feature_count')),'个',0),
                      '输入数据集：'+str(provenance.get('dataset_name') or inputs.get('dataset_id') or task.get('dataset_id') or '未提供'),
                      '输入数据版本：'+str(provenance.get('dataset_version',inputs.get('dataset_version','未提供'))),
                      '执行算法：'+str(task.get('algorithm_id') or provenance.get('algorithm_id') or '未提供')])
        if finite(summary.get('area_sum_ha')):lines.append('输出面要素面积合计：'+amount(summary['area_sum_ha'],'公顷'))
    lines.extend(['', '归属说明：本包对应共享计算子任务，原始需求为该子任务创建时归档的需求。当前提问、问题ID及引用链可在平台的任务记录查看；本包不改写任何问题或原任务。', ''])
    return lines


class ResultBundleServices:
    def __init__(self,store,output_dir=None,max_total_bytes=MAX_TOTAL_BYTES):
        self.store=store
        self.output_dir=Path(output_dir or Path(store.db_path).parent/'result-bundles')
        self.max_total_bytes=max_total_bytes
        self.lock=threading.RLock()

    @staticmethod
    def validate(kind,task_id):
        if kind not in FILES:
            raise GeoError('BUNDLE_UNSUPPORTED','该结果类型暂不提供GIS成果包',422)
        if not isinstance(task_id,str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,100}',task_id):
            raise GeoError('INVALID_TASK_ID','成果编号无效',400)

    def _task(self,kind,task_id):
        self.validate(kind,task_id)
        task=self.store.get('analysis',task_id) if kind=='analysis' else getattr(self.store,kind).get(task_id)
        if task.get('status')!='succeeded':
            raise GeoError('BUNDLE_NOT_READY','只有真实成功并归档的成果可以打包',409)
        if kind=='production' and (task.get('plan',{}).get('sampling')!='native' or task.get('plan',{}).get('mode')!='sentinel_cog'):
            raise GeoError('BUNDLE_UNSUPPORTED','样本或旧代表网格不是可复核原生遥感成果',422)
        if kind!='analysis' and not isinstance(task.get('result'),dict):
            raise GeoError('BUNDLE_NOT_READY','该任务缺少完整归档结果',409)
        return copy.deepcopy(task)

    def _path(self,kind,task_id,name):
        service=getattr(self.store,kind)
        path=Path(service.artifact(task_id,name))
        root=Path(getattr(service,'output_dir',Path(self.store.db_path).parent/'production'))
        expected=root/task_id/name
        if path.absolute()!=expected.absolute():
            raise GeoError('BUNDLE_PATH_REJECTED','成果文件不属于该任务的固定产物目录',422)
        current=path
        while current.absolute()!=root.absolute():
            if current.is_symlink():
                raise GeoError('BUNDLE_PATH_REJECTED','成果包不接受链接到其他位置的文件',422)
            current=current.parent
        if not path.resolve().is_relative_to(root.resolve()) or not stat.S_ISREG(path.stat().st_mode):
            raise GeoError('BUNDLE_PATH_REJECTED','成果文件必须是任务目录内的普通文件',422)
        return path

    def _snapshot(self,kind,task_id):
        task=self._task(kind,task_id)
        result=task if kind=='analysis' else task['result']
        provenance=copy.deepcopy(result.get('provenance') or {})
        inputs=copy.deepcopy(task.get('plan') or {'dataset_id':task.get('dataset_id'),'algorithm_id':task.get('algorithm_id'),'parameters':provenance.get('parameters',{}),'dataset_version':provenance.get('dataset_version'),'input_sha256':provenance.get('sha256')})
        report=result.get('report') or {}
        limitations=copy.deepcopy(report.get('limitations') or inputs.get('limitations') or [])
        if kind=='cropland':limitations.append('不同产品的类别定义和面积方法不同，不能将WorldCover2021与IO2024面积差直接解释为耕地增减。')
        if kind=='analysis':limitations.append('输入业务属性与真实性由提供方负责，几何分析不构成权属或法定认定。')
        limitations.append('本包用于结果复核和移交，不代表政府主管部门审核、法定调查或独立精度验证。')
        summary=copy.deepcopy(result.get('summary') or {})
        if kind=='analysis' and summary.get('scene_id'):
            limitations.extend(v for v in summary.get('limitations',[]) if v not in limitations)
            limitations.append('source.geojson为完整输入，result.geojson为筛选后输出；按inputs.json内query和分组/汇总字段复算。provenance.sha256是规范化内容哈希，文件字节SHA256见manifest.json。')
        year=summary.get('reference_year',provenance.get('year',provenance.get('source_year')))
        title=task.get('name') or ('用户矢量分析' if kind=='analysis' else '遥感分析成果')
        readme='\n'.join([
            '时空智能工作台｜GIS成果与复核资料包',
            '任务：'+task_id+'；类型：'+kind,
            '名称：'+str(title),
            '数据/产品年度：'+str(year if year is not None else '见provenance.json中的实际观测日期'),
            '',
            *readable_summary(kind,task,result,inputs,provenance,year),
            '使用说明：',
            '1. GIS文件可在QGIS等软件中按文件声明的CRS打开；PNG仅为预览，不用于重算面积。',
            '2. inputs.json保留冻结分析条件，provenance.json保留来源、方法和输入指纹。',
            '3. task_record.json为该成功任务快照，不包含服务配置或工作空间数据库。',
            '4. manifest.json列出各文件字节数与SHA256；manifest文件自身不作递归哈希。',
            '5. 可按manifest.json核验文件；面积与分类质量应结合方法、缺测与独立样本复核。',
            '',
            '适用范围与限制：',
            *['- '+str(item) for item in limitations],
            '',
        ])
        generated={'task_record.json':canonical(task),'inputs.json':canonical(inputs),
                   'provenance.json':canonical(provenance),'README.txt':readme.encode('utf-8')}
        if kind=='analysis':
            generated.update({'result.geojson':canonical(result.get('geojson',{})),'result.json':canonical(result)})
        if kind=='analysis' and summary.get('scene_id'):
            try:dataset=self.store.get_dataset(task.get('dataset_id'))
            except Exception as error:raise GeoError('BUNDLE_SOURCE_CHANGED','原始业务台账已不可用，不能生成无法复核的场景成果包',409) from error
            if dataset.get('version')!=provenance.get('dataset_version') or dataset.get('sha256')!=provenance.get('sha256') or digest(dataset.get('geojson'))!=provenance.get('sha256'):
                raise GeoError('BUNDLE_SOURCE_CHANGED','原始业务台账版本或内容已改变，不能用新数据替换归档输入',409)
            generated['source.geojson']=canonical(dataset['geojson'])
            def csv_bytes(rows):
                stream=io.StringIO(newline='')
                def cell(value):
                    if value is None:return ''
                    if isinstance(value,str) and value.lstrip().startswith(('=','+','-','@')):return "'"+value
                    return value
                writer=csv.writer(stream);writer.writerows([[cell(v) for v in row] for row in rows]);return ('\ufeff'+stream.getvalue()).encode('utf-8')
            metrics=[['metric','value','unit'],['matched_records',summary.get('feature_count'),'records'],['input_records',summary.get('input_feature_count'),'records'],['area_sum',summary.get('area_sum_ha'),'ha'],['line_length',summary.get('line_length_m'),'m'],['entity_count',summary.get('entity_count'),'entities'],[summary.get('measure_field') or 'measure_not_requested',summary.get('measure_sum'),summary.get('measure_unit','')]]
            metrics.extend(['group:'+str(k),v,'records'] for k,v in summary.get('groups',{}).items())
            features=result.get('geojson',{}).get('features',[]);fields=sorted({key for f in features for key in f.get('properties',{})})
            generated['summary.csv']=csv_bytes(metrics)
            generated['features.csv']=csv_bytes([['GeoJSON feature ID','GeoJSON geometry type',*fields]]+[[f.get('id'),f['geometry']['type'],*[f['properties'].get(key) for key in fields]] for f in features])
        names=FILES[kind]
        if kind=='agriculture':
            algorithm=inputs.get('algorithm_id')
            if algorithm not in AGRICULTURE_FILES or summary.get('algorithm_id')!=algorithm:raise GeoError('BUNDLE_UNSUPPORTED','农业结果方法与冻结计划不一致，不能猜测归档文件',422)
            names=AGRICULTURE_FILES[algorithm]
        elif kind=='raster_processing':
            algorithm=inputs.get('algorithm_id')
            if algorithm not in PROCESSING_NAMES or summary.get('algorithm_id')!=algorithm:raise GeoError('BUNDLE_UNSUPPORTED','影像处理方法与冻结计划不一致，不能猜测归档文件',422)
            names=processing_files(algorithm)
        sources={name:self._path(kind,task_id,name) for name in names}
        entries=[];total=0
        for name in sorted([*sources,*generated]):
            if name in sources:
                path=sources[name];size=path.stat().st_size
                if size<=0:raise GeoError('BUNDLE_FILE_EMPTY','归档产物为空，不能发布完整成果包',422)
                total+=size
                if total>self.max_total_bytes:raise GeoError('BUNDLE_SIZE_LIMIT','成果包超过本机512MiB处理上限，请按范围拆分',413)
                sha=hashlib.sha256()
                read_size=0
                with path.open('rb') as handle:
                    for chunk in iter(lambda:handle.read(CHUNK),b''):
                        read_size+=len(chunk)
                        if read_size>size:raise GeoError('BUNDLE_SOURCE_CHANGED','成果文件在校验中发生变化，请重新核验',409)
                        sha.update(chunk)
                if read_size!=size:raise GeoError('BUNDLE_SOURCE_CHANGED','成果文件在校验中发生变化，请重新核验',409)
                checksum=sha.hexdigest()
            else:
                raw=generated[name];size=len(raw);total+=size;checksum=hashlib.sha256(raw).hexdigest()
                if total>self.max_total_bytes:raise GeoError('BUNDLE_SIZE_LIMIT','成果包超过本机512MiB处理上限，请按范围拆分',413)
            entries.append({'path':name,'bytes':size,'sha256':checksum,'role':'archived_artifact' if name in sources else 'reproduction_metadata'})
        manifest={'schema_version':VERSION,'task_ref':{'type':kind,'task_id':task_id},'title':title,
                  'task_status':'succeeded','source_year':year,'result_created_at':task.get('finished_at',task.get('created_at')),
                  'observation_dates':observation_dates(provenance),
                  'summary':summary,'inputs':inputs,'provenance':provenance,'limitations':limitations,
                  'entries':entries,'total_uncompressed_bytes':total,
                  'manifest_note':'各条目SHA256覆盖实际文件内容；本清单自身不递归入表。'}
        manifest['package_fingerprint']=hashlib.sha256(canonical(manifest)).hexdigest()
        return manifest,sources,generated

    def manifest(self,kind,task_id):
        with self.lock:return self._snapshot(kind,task_id)[0]

    def _valid_archive(self,path,manifest):
        try:
            expected={entry['path']:entry for entry in manifest['entries']}
            with zipfile.ZipFile(path) as archive:
                if len(archive.namelist())!=len(expected)+1 or set(archive.namelist())!=set(expected)|{'manifest.json'}:return False
                manifest_bytes=canonical(manifest)
                if archive.getinfo('manifest.json').file_size!=len(manifest_bytes) or archive.read('manifest.json')!=manifest_bytes:return False
                for name,entry in expected.items():
                    if archive.getinfo(name).file_size!=entry['bytes']:return False
                    sha=hashlib.sha256()
                    with archive.open(name) as handle:
                        for chunk in iter(lambda:handle.read(CHUNK),b''):sha.update(chunk)
                    if sha.hexdigest()!=entry['sha256']:return False
            return True
        except (OSError,ValueError,KeyError,zipfile.BadZipFile):return False

    def artifact(self,kind,task_id):
        with self.lock:
            manifest,sources,generated=self._snapshot(kind,task_id)
            self.output_dir.mkdir(parents=True,exist_ok=True,mode=0o700)
            filename=kind+'-'+task_id+'-'+manifest['package_fingerprint'][:16]+'.zip'
            target=self.output_dir/filename
            if target.is_file() and not target.is_symlink() and self._valid_archive(target,manifest):return target
            partial=self.output_dir/(filename+'.'+uuid.uuid4().hex+'.partial')
            try:
                with zipfile.ZipFile(partial,'w',zipfile.ZIP_DEFLATED,compresslevel=6) as archive:
                    os.chmod(partial,0o600)
                    for entry in manifest['entries']:
                        name=entry['path'];sha=hashlib.sha256();written=0
                        with archive.open(name,'w') as destination:
                            if name in sources:
                                path=self._path(kind,task_id,name)
                                with path.open('rb') as source:
                                    for chunk in iter(lambda:source.read(CHUNK),b''):
                                        written+=len(chunk)
                                        if written>entry['bytes']:raise GeoError('BUNDLE_SOURCE_CHANGED','成果文件在打包中发生变化，请重新核验',409)
                                        sha.update(chunk);destination.write(chunk)
                            else:
                                raw=generated[name];written=len(raw);sha.update(raw);destination.write(raw)
                        if written!=entry['bytes'] or sha.hexdigest()!=entry['sha256']:
                            raise GeoError('BUNDLE_SOURCE_CHANGED','成果文件在打包中发生变化，请重新核验',409)
                    archive.writestr('manifest.json',canonical(manifest))
                os.replace(partial,target)
                return target
            finally:
                if partial.exists():partial.unlink()
