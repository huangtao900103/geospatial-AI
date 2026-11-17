"""Evidence-based removal of synthetic application samples, without starting Workspace.

The scanner reads SQLite in mode=ro. Applying a reviewed plan requires a stopped
service, no active work, an unchanged database snapshot and a recoverable backup.
This module never uses dataset names such as '验收' as a reason on their own.
"""
from __future__ import annotations
import copy
import hashlib
import json
from pathlib import Path
import re
import shutil
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone

VERSION='sample-cleanup-1.0'
ACTIVE={'queued','running','planning','processing'}
VIRTUAL_IDS={'xt-fixture','xt-fixture-v1','manual-band-grid-v1'}
ARTIFACT_DIRS={'production_task':'production','agriculture_task':'agriculture','cropland_task':'cropland','landcover_task':'landcover'}
# Audited exact content: 24 fabricated demonstration parcels in a repeated grid;
# region="西部示例片区" etc, names="示例地块 NN", manually filled veg_before/after.
AUDITED_DEMO_HASHES={'404960e19813b85ecf078917500ce4ca4bc4c154a57fdeb212c9ce26dbc4f9a7':'reviewed_24_parcel_demo_grid'}
# These legacy application configs can only render the fixture in the old app
# preview; server.validate_app accepts fixture/scenario layers, not real datasets.
AUDITED_FIXTURE_APPS={'app_9f675833496540','app_1ba94f3bed4542','app_2858ff27587c48','app_c138eff135b44a'}
LEGACY_LAYERS={'farm','industry','machine','water','alert','all','xt-fixture','baseline','farmland','crop','disaster','fishing','eel','service','processing','logistics','land','input','integration'}
EXPLICIT_MARKERS=('人工构造','本机合成','非真实业务数据','算法核验选区，非实测农田','算法测试，非实测农田','非实际业务','非实际设施','非实际作业')

class CleanupError(RuntimeError):pass

def _canonical(value):return json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False).encode('utf-8')
def _digest(value):return hashlib.sha256(_canonical(value)).hexdigest()
def _file_sha(path):
 h=hashlib.sha256()
 with path.open('rb') as stream:
  for block in iter(lambda:stream.read(1024*1024),b''):h.update(block)
 return h.hexdigest()
def _now():return datetime.now(timezone.utc).isoformat(timespec='seconds')
def _wal_has_data(path):
 try:return Path(str(path)+'-wal').stat().st_size>0
 except FileNotFoundError:return False
def _read_identity(path):
 stat=path.stat();return stat.st_dev,stat.st_ino,stat.st_size,stat.st_mtime_ns,stat.st_ctime_ns

@contextmanager
def _read_only_connection(path):
 """Read cold WAL-header backups without repairing or writing their database.

 SQLite can fail on the first schema read of a sidecar-free WAL backup. Only
 that precise error permits immutable mode, and only while no WAL has data.
 Existing WAL commits must always be read by the normal read-only connection.
 """
 connection=None;immutable=False;identity=None
 try:
  try:
   connection=sqlite3.connect(path.as_uri()+'?mode=ro',uri=True)
   # connect() itself may succeed even though opening the schema cannot.
   connection.execute('SELECT count(*) FROM sqlite_master').fetchone()
  except sqlite3.OperationalError as error:
   if connection is not None:connection.close();connection=None
   if str(error).lower()!='unable to open database file' or _wal_has_data(path):raise
   identity=_read_identity(path)
   connection=sqlite3.connect(path.as_uri()+'?mode=ro&immutable=1',uri=True);immutable=True
   if _wal_has_data(path):raise CleanupError('WAL changed during immutable read; stop writers and retry')
   connection.execute('SELECT count(*) FROM sqlite_master').fetchone()
  yield connection
  if immutable and (_wal_has_data(path) or _read_identity(path)!=identity):raise CleanupError('Database changed during immutable read; stop writers and retry')
 finally:
  if connection is not None:connection.close()

def _read(db_path):
 path=Path(db_path).absolute()
 if not path.is_file():raise CleanupError('Database does not exist')
 with _read_only_connection(path) as c:
  rows=c.execute('SELECT kind,id,data FROM records ORDER BY kind,id').fetchall()
 return path,[{'kind':kind,'id':id,'data':json.loads(raw),'sha256':hashlib.sha256(raw.encode()).hexdigest()} for kind,id,raw in rows]
def _snapshot(rows):return _digest([(r['kind'],r['id'],r['sha256']) for r in rows])

def classify_dataset(value):
 """Return concrete sample evidence; unknown and publicly sourced data survive."""
 geo=value.get('geojson') or {};metadata=geo.get('metadata') or {};features=geo.get('features') or [];reasons=[]
 if value.get('source_type')=='synthetic' or metadata.get('source_type')=='synthetic' or metadata.get('mode')=='synthetic':
  reasons.append({'code':'synthetic_dataset','evidence':'Dataset or GeoJSON metadata explicitly marks synthetic'})
 if geo and _digest(geo) in AUDITED_DEMO_HASHES:
  reasons.append({'code':'audited_demo_content','evidence':AUDITED_DEMO_HASHES[_digest(geo)]+'; exact reviewed GeoJSON SHA256'})
 # Dataset title alone is insufficient: require explicit nonreal markers in the
 # data's own source/purpose/description or every feature's provenance labels.
 metadata_text=' '.join(str(metadata.get(k,'')) for k in ('source','description','purpose'))
 marked=[]
 for f in features:
  p=f.get('properties') or {}
  text=' '.join(str(p.get(k,'')) for k in ('source','purpose','说明','description'))
  marked.append(any(marker in text for marker in EXPLICIT_MARKERS))
 explicit_named_test=any(marker in str(value.get('name','')) for marker in ('非实际业务','非实测地块')) and features and all(any(marker in str((f.get('properties') or {}).get('name','')) for marker in ('非实际设施','非实际作业','非实测农田')) for f in features)
 if any(marker in metadata_text for marker in EXPLICIT_MARKERS) or marked and all(marked) or explicit_named_test:
  reasons.append({'code':'explicit_nonreal_feature_provenance','evidence':'All input features or collection provenance explicitly state artificial/nonreal/test geometry'})
 return reasons

def _base_reasons(row):
 kind,obj=row['kind'],row['data'];reasons=[]
 if kind=='dataset':return classify_dataset(obj)
 result=obj.get('result') or {};provenance=result.get('provenance') or obj.get('provenance') or {};plan=obj.get('plan') or {}
 if kind=='production_task' and (plan.get('mode')=='sample_pixels' or provenance.get('source_type')=='artificial_band_sample'):
  reasons.append({'code':'artificial_band_task','evidence':'Production mode sample_pixels / artificial_band_sample'})
 if kind=='analysis' and (provenance.get('source_type')=='synthetic' or provenance.get('mode')=='fixture-spatial-analysis'):
  reasons.append({'code':'fixture_analysis','evidence':'Analysis provenance explicitly identifies fixture/synthetic input'})
 if kind=='plan' and any(node.get('engine')=='local.fixture_catalog' for node in obj.get('nodes',[]) if isinstance(node,dict)):
  reasons.append({'code':'fixture_workflow_plan','evidence':'Plan retrieves local.fixture_catalog, not real data'})
 if kind=='run' and (provenance.get('mode')=='fixture-spatial-analysis' or provenance.get('dataset')=='xt-fixture-v1'):
  reasons.append({'code':'fixture_workflow_run','evidence':'Run result provenance is fixture-spatial-analysis / xt-fixture-v1'})
 if kind=='app' and obj.get('id') in AUDITED_FIXTURE_APPS and obj.get('scope')=='workspace' and set(obj.get('layer_ids',[])).issubset(LEGACY_LAYERS) and not any(obj.get(k) for k in ('dataset_id','dataset_ids','data_sources','source_refs')):
  reasons.append({'code':'audited_fixture_app','evidence':'Audited legacy config only supports fixture/scenario layer preview; no real data bindings'})
 return reasons

def _references(value,ids,path=''):
 if isinstance(value,dict):
  # Catalog candidates and full provider snapshots are not input lineage.
  for key,child in value.items():
   if key in ('candidates','catalog_snapshot','available_sources','source_profiles','raster_sources'):continue
   yield from _references(child,ids,path+'.'+key)
 elif isinstance(value,list):
  for i,child in enumerate(value):yield from _references(child,ids,path+'['+str(i)+']')
 elif isinstance(value,str):
  if value in ids:yield {'path':path,'reference_id':value}
  elif value.startswith('/api/'):
   pieces=value.split('/')
   for piece in pieces:
    if piece in ids:yield {'path':path,'reference_id':piece}

def _artifact_candidates(db_path,rows,removed):
 found=[];base=db_path.parent
 for row in rows:
  key=row['kind']+':'+row['id']
  if key not in removed or row['kind'] not in ARTIFACT_DIRS:continue
  if not isinstance(row['id'],str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,100}',row['id']):raise CleanupError('Unsafe task identifier; artifact scan stopped')
  directory=base/ARTIFACT_DIRS[row['kind']]/row['id']
  if directory.is_symlink():raise CleanupError('Refusing linked task directory')
  if not directory.exists():continue
  for item in sorted(directory.rglob('*')):
   if item.is_symlink():raise CleanupError('Refusing linked artifact')
   if item.is_file():found.append({'relative_path':str(item.relative_to(base)),'bytes':item.stat().st_size,'sha256':_file_sha(item),'record_key':key})
 return found

def scan(db_path):
 path,rows=_read(db_path);removed={};virtual=copy.deepcopy(VIRTUAL_IDS)
 for row in rows:
  reasons=_base_reasons(row)
  if reasons:removed[row['kind']+':'+row['id']]=reasons
 changed=True
 while changed:
  changed=False;ids={key.split(':',1)[1] for key in removed}|virtual
  for row in rows:
   key=row['kind']+':'+row['id']
   if key in removed:continue
   links=list(_references(row['data'],ids))
   if links:
    removed[key]=[{'code':'derived_from_removed_sample','evidence':links[:20]}];changed=True
 candidates=[{'kind':row['kind'],'id':row['id'],'sha256':row['sha256'],'reasons':removed[row['kind']+':'+row['id']]} for row in rows if row['kind']+':'+row['id'] in removed]
 counts={}
 for row in candidates:counts[row['kind']]=counts.get(row['kind'],0)+1
 retained=[{'kind':row['kind'],'id':row['id'],'reason':'real public raster / reference data, or no affirmative synthetic evidence'} for row in rows if row['kind'] in ('dataset','production_task','cropland_task','landcover_task','agriculture_task','analysis','app') and row['kind']+':'+row['id'] not in removed]
 plan={'schema_version':VERSION,'db_path':str(path),'captured_at':_now(),'database_fingerprint':_snapshot(rows),'record_count_before':len(rows),'delete_count':len(candidates),'delete_counts_by_kind':counts,'delete_records':candidates,'retained_data_records':retained,'active_records':[{'kind':row['kind'],'id':row['id'],'status':row['data'].get('status')} for row in rows if row['data'].get('status') in ACTIVE],'artifact_files':_artifact_candidates(path,rows,removed),'runtime_followup':['Remove virtual xt-fixture datasets/map layers and teaching import endpoints so removed records cannot reappear','Disable fixture-backed legacy workflow/run/app previews; keep genuine raster, user input and public reference routes'],'safety':'Only affirmative synthetic markers, reviewed exact content, audited fixture configs and their input/reference descendants. Unknown user inputs and real public data are retained.'}
 plan['plan_sha256']=_digest(plan)
 return plan

def apply(db_path,plan,backup_dir):
 """Apply exactly a reviewed immutable scan, preserving backup and other rows."""
 plan=copy.deepcopy(plan);expected=plan.pop('plan_sha256',None)
 if expected!=_digest(plan):raise CleanupError('Cleanup plan fingerprint mismatch')
 plan['plan_sha256']=expected;path,rows=_read(db_path)
 if str(path)!=plan.get('db_path') or _snapshot(rows)!=plan.get('database_fingerprint'):raise CleanupError('Database changed since scan; regenerate and review the plan')
 if any(row['data'].get('status') in ACTIVE for row in rows):raise CleanupError('Active records found; stop services and wait before cleanup')
 backup=Path(backup_dir).absolute()
 if backup.exists():raise CleanupError('Backup directory must be new to avoid overwriting recovery evidence')
 if backup.resolve().is_relative_to(path.parent.resolve()):raise CleanupError('Backup directory must be outside the live data tree')
 artifacts=plan.get('artifact_files',[]);root=path.parent
 for item in artifacts:
  file=root/item['relative_path']
  if '..' in Path(item['relative_path']).parts or not file.resolve().is_relative_to(root.resolve()) or file.is_symlink() or not file.is_file() or _file_sha(file)!=item['sha256']:raise CleanupError('Artifact changed or unsafe path; cleanup stopped')
 backup.mkdir(parents=True);backup.chmod(0o700)
 for item in artifacts:
  source=root/item['relative_path'];target=backup/'artifacts'/item['relative_path'];target.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(source,target)
 with _read_only_connection(path) as src, sqlite3.connect(backup/'workspace.sqlite3') as dst:src.backup(dst)
 (backup/'cleanup-plan.json').write_text(json.dumps(plan,ensure_ascii=False,indent=2),'utf-8')
 deleted={(row['kind'],row['id']) for row in plan['delete_records']};kept={(row['kind'],row['id']):row['sha256'] for row in rows if (row['kind'],row['id']) not in deleted}
 with sqlite3.connect(path) as connection:
  connection.execute('BEGIN IMMEDIATE')
  current=connection.execute('SELECT kind,id,data FROM records ORDER BY kind,id').fetchall()
  locked=[{'kind':k,'id':i,'sha256':hashlib.sha256(raw.encode()).hexdigest()} for k,i,raw in current]
  if _snapshot(locked)!=plan['database_fingerprint']:raise CleanupError('Database changed during backup; cleanup transaction rolled back')
  for kind,id in sorted(deleted):
   if connection.execute('DELETE FROM records WHERE kind=? AND id=?',(kind,id)).rowcount!=1:raise CleanupError('Expected record missing; transaction rolled back')
  tables={r[0] for r in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
  if 'eo_pixel_uses' in tables:
   for kind,id in sorted(deleted):
    if kind in ARTIFACT_DIRS:connection.execute('DELETE FROM eo_pixel_uses WHERE task_id=?',(id,))
  after=connection.execute('SELECT kind,id,data FROM records ORDER BY kind,id').fetchall()
  actual={(k,i):hashlib.sha256(raw.encode()).hexdigest() for k,i,raw in after}
  if actual!=kept:raise CleanupError('Untouched record verification failed; transaction rolled back')
  connection.commit()
 # Only exact reviewed files, after their recoverable copy and DB transaction.
 removed_files=[];artifact_warnings=[]
 for item in artifacts:
  file=root/item['relative_path']
  if file.is_symlink() or not file.is_file() or _file_sha(file)!=item['sha256']:
   artifact_warnings.append({'relative_path':item['relative_path'],'reason':'changed_after_backup_not_deleted'});continue
  file.unlink();removed_files.append(item['relative_path'])
 for relative in sorted({str(Path(name).parent) for name in removed_files},key=len,reverse=True):
  folder=root/relative
  if folder.exists() and not any(folder.iterdir()):folder.rmdir()
 result={'schema_version':VERSION,'applied_at':_now(),'delete_count':len(deleted),'delete_counts_by_kind':plan['delete_counts_by_kind'],'retained_record_count':len(kept),'retained_records_unchanged':True,'deleted_artifact_files':removed_files,'artifact_removal_warnings':artifact_warnings,'backup_dir':str(backup),'backup_database_sha256':_file_sha(backup/'workspace.sqlite3'),'plan_sha256':expected}
 (backup/'cleanup-result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),'utf-8')
 return result
