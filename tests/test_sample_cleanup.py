"""Precise, recoverable SQLite sample cleanup; no live Workspace is initialized."""
import copy
import hashlib
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch
from sample_cleanup import scan,apply,classify_dataset,CleanupError,AUDITED_FIXTURE_APPS

class SchemaReadFailure:
 def __init__(self,message='unable to open database file'):self.message=message;self.closed=False
 def execute(self,*args):raise sqlite3.OperationalError(self.message)
 def close(self):self.closed=True

class SampleCleanupTest(unittest.TestCase):
 def setUp(self):
  self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name);self.db=self.root/'data/workspace.sqlite3';self.db.parent.mkdir()
  with sqlite3.connect(self.db) as db:
   db.execute('CREATE TABLE records(kind TEXT,id TEXT,data TEXT,created_at TEXT,PRIMARY KEY(kind,id))');db.execute('CREATE TABLE eo_pixel_uses(observation_id TEXT,task_id TEXT,input_sha256 TEXT,used_at TEXT)')
 def tearDown(self):self.temp.cleanup()
 def save(self,kind,id,**data):
  value={'id':id,**data}
  with sqlite3.connect(self.db) as db:db.execute('INSERT OR REPLACE INTO records VALUES(?,?,?,?)',(kind,id,json.dumps(value,ensure_ascii=False),'2026-11-14'))
  return value
 def rows(self):
  with sqlite3.connect(self.db) as db:return {(k,i):raw for k,i,raw in db.execute('SELECT kind,id,data FROM records')}
 def geo(self,properties):return {'type':'FeatureCollection','features':[{'type':'Feature','id':'f1','properties':properties,'geometry':{'type':'Point','coordinates':[113.4,30.3]}}]}
 def seed(self):
  self.save('dataset','bad_dataset',name='Ordinary renamed file',source_type='user',geojson=self.geo({'name':'point','source':'人工构造教学数据，非真实遥感反射率'}))
  self.save('analysis','bad_analysis',dataset_id='bad_dataset',provenance={'source_type':'user'})
  self.save('execution','bad_execution',status='succeeded',binding={'type':'analysis','task_id':'bad_analysis'})
  self.save('assistant_question','bad_question',status='answered',execution_ref={'id':'bad_execution'})
  self.save('assistant_shortcut','bad_shortcut',source_question_id='bad_question')
  self.save('data_plan','bad_lineage',request={'context':{'task_id':'bad_analysis'}})
  self.save('dataset','real_reference',name='公开参考水系（专题流程验收）',source_type='user',geojson=self.geo({'name':'Yangtze','source_type':'public','source':'Natural Earth','description':'真实公开参考数据，非实测，非权属边界'}))
  self.save('production_task','real_production',status='succeeded',plan={'mode':'sentinel_cog','sampling':'native'},result={'provenance':{'source_type':'public_satellite_pixels'}})
  self.save('dataset','unknown_user',name='客户实测地块',source_type='user',geojson=self.geo({'name':'客户1','source':'提供方待核实'}))
 def cold_wal_backup(self):
  cold=self.root/'cold snapshot #1'/'workspace?.sqlite3';cold.parent.mkdir()
  source=sqlite3.connect(self.db);target=sqlite3.connect(cold)
  try:
   self.assertEqual(source.execute('PRAGMA journal_mode=WAL').fetchone()[0],'wal');source.backup(target)
  finally:target.close();source.close()
  self.assertEqual(cold.read_bytes()[18:20],b'\x02\x02');self.assertFalse(Path(str(cold)+'-wal').exists());self.assertFalse(Path(str(cold)+'-shm').exists())
  return cold
 def test_only_affirmative_samples_and_recursive_descendants_removed(self):
  self.seed();before=self.rows();p=scan(self.db);deleted={(r['kind'],r['id']) for r in p['delete_records']}
  self.assertEqual(p['delete_count'],6);self.assertIn(('assistant_shortcut','bad_shortcut'),deleted);self.assertNotIn(('dataset','real_reference'),deleted);self.assertNotIn(('dataset','unknown_user'),deleted);self.assertEqual(self.rows(),before)
  result=apply(self.db,p,self.root/'recovery');self.assertEqual(result['delete_count'],6);self.assertTrue(result['retained_records_unchanged']);self.assertEqual(self.rows(),{key:raw for key,raw in before.items() if key not in deleted});self.assertEqual(scan(self.db)['delete_count'],0)
  with sqlite3.connect(self.root/'recovery/workspace.sqlite3') as db:self.assertEqual(db.execute('SELECT count(*) FROM records').fetchone()[0],len(before))
 def test_ambiguous_title_and_real_public_candidate_catalog_are_not_evidence(self):
  self.seed();self.save('data_plan','keep_candidates',candidates=[{'dataset_id':'bad_dataset'}],source_snapshot={'id':'real_source'})
  self.save('dataset','ambiguous',name='验收测试数据',source_type='user',geojson=self.geo({'name':'田1','source':'公开实测数据'}))
  removed={r['id'] for r in scan(self.db)['delete_records']};self.assertNotIn('keep_candidates',removed);self.assertNotIn('ambiguous',removed)
 def test_virtual_fixture_and_artificial_band_lineage_cannot_survive(self):
  self.save('analysis','fixture_analysis',dataset_id='xt-fixture');self.save('production_task','manual',status='succeeded',plan={'mode':'sample_pixels'});self.save('assistant_question','question_manual',status='answered',answer={'result_ref':{'url':'/api/production/tasks/manual'}})
  self.assertEqual(scan(self.db)['delete_count'],3)
 def test_explicitly_nonreal_upload_requires_both_title_and_feature_evidence(self):
  d={'name':'自动上图验收（点线面，非实际业务）','source_type':'user','geojson':self.geo({'name':'上传验收点位（非实际设施）','说明':'仅验证地图显示'})};self.assertTrue(classify_dataset(d))
  d['name']='客户数据';self.assertFalse(classify_dataset(d))
 def test_reviewed_demo_hash_matches_content_not_user_editable_name_or_sha_field(self):
  d={'name':'客户数据','sha256':'claimed','geojson':self.geo({'name':'plot'})};raw=json.dumps(d['geojson'],ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()
  with patch('sample_cleanup.AUDITED_DEMO_HASHES',{hashlib.sha256(raw).hexdigest():'audited_demo'}):self.assertTrue(classify_dataset(d))
  self.assertFalse(classify_dataset(d))
 def test_legacy_app_requires_audited_identity_and_no_real_data_binding(self):
  id=next(iter(AUDITED_FIXTURE_APPS));self.save('app',id,scope='workspace',layer_ids=[],modules=['baseline']);self.assertEqual(scan(self.db)['delete_count'],1)
  self.save('app',id,scope='workspace',layer_ids=[],modules=['baseline'],dataset_id='real');self.assertEqual(scan(self.db)['delete_count'],0)
 def test_database_drift_tampered_plan_or_active_work_rejected(self):
  self.seed();p=scan(self.db);bad=copy.deepcopy(p);bad['delete_count']=0
  with self.assertRaises(CleanupError):apply(self.db,bad,self.root/'recovery')
  self.save('dataset','new_real',geojson=self.geo({'name':'new'}))
  with self.assertRaises(CleanupError):apply(self.db,p,self.root/'recovery')
  self.save('execution','active_real',status='running');p=scan(self.db)
  with self.assertRaises(CleanupError):apply(self.db,p,self.root/'recovery')
 def test_deleted_artifacts_backed_up_and_real_artifacts_preserved(self):
  self.seed();self.save('agriculture_task','bad_agriculture',status='succeeded',plan={'dataset':{'id':'bad_dataset'}})
  bad=self.db.parent/'agriculture/bad_agriculture/result.geojson';bad.parent.mkdir(parents=True);bad.write_bytes(b'nonreal geometry output')
  real=self.db.parent/'production/real_production/analysis.tif';real.parent.mkdir(parents=True);real.write_bytes(b'real raster test bytes')
  p=scan(self.db);self.assertEqual(len(p['artifact_files']),1);result=apply(self.db,p,self.root/'recovery');self.assertFalse(bad.exists());self.assertEqual((self.root/'recovery/artifacts/agriculture/bad_agriculture/result.geojson').read_bytes(),b'nonreal geometry output');self.assertTrue(real.exists());self.assertFalse(result['artifact_removal_warnings'])
 def test_changed_artifact_or_link_refuses_cleanup(self):
  self.save('production_task','bad',status='succeeded',plan={'mode':'sample_pixels'});p=self.db.parent/'production/bad/analysis.tif';p.parent.mkdir(parents=True);p.write_bytes(b'old');plan=scan(self.db);p.write_bytes(b'new')
  with self.assertRaises(CleanupError):apply(self.db,plan,self.root/'recovery')
  p.unlink();outside=self.root/'outside';outside.write_bytes(b'private');p.symlink_to(outside)
  with self.assertRaises(CleanupError):scan(self.db)
 def test_backup_must_not_be_inside_live_data(self):
  self.seed()
  with self.assertRaises(CleanupError):apply(self.db,scan(self.db),self.db.parent/'backup')
 def test_actual_cold_wal_backup_scans_and_applies_without_rw_dry_run(self):
  self.seed();cold=self.cold_wal_backup();before=cold.read_bytes();real_connect=sqlite3.connect;opened=[]
  def connect(target,*args,**kwargs):
   opened.append((str(target),kwargs));return real_connect(target,*args,**kwargs)
  with patch('sample_cleanup.sqlite3.connect',side_effect=connect):plan=scan(cold)
  self.assertEqual(plan['record_count_before'],9);self.assertEqual(plan['delete_count'],6);self.assertEqual(cold.read_bytes(),before)
  self.assertTrue(opened);self.assertTrue(all('?mode=ro' in target and kwargs.get('uri') for target,kwargs in opened))
  result=apply(cold,plan,self.root/'cold-recovery');self.assertEqual(result['delete_count'],6);self.assertEqual(scan(cold)['record_count_before'],3)
  recovery=self.root/'cold-recovery/workspace.sqlite3';self.assertEqual(scan(recovery)['record_count_before'],9)
 def test_first_schema_error_uses_immutable_for_scan_and_backup_source(self):
  self.seed();cold=self.cold_wal_backup();real_connect=sqlite3.connect;failed=[];immutable=[]
  # Force the platform-dependent first-SELECT failure even on SQLite builds
  # that can open a sidecar-free WAL backup without immutable mode.
  def connect(target,*args,**kwargs):
   if str(target)==cold.as_uri()+'?mode=ro':
    connection=SchemaReadFailure();failed.append(connection);return connection
   if 'immutable=1' in str(target):immutable.append(str(target))
   return real_connect(target,*args,**kwargs)
  with patch('sample_cleanup.sqlite3.connect',side_effect=connect):
   plan=scan(cold);result=apply(cold,plan,self.root/'forced-recovery')
  self.assertEqual(result['delete_count'],6);self.assertEqual(len(immutable),3);self.assertTrue(all(connection.closed for connection in failed))
  self.assertEqual(scan(self.root/'forced-recovery/workspace.sqlite3')['record_count_before'],9)
 def test_nonempty_wal_never_falls_back_or_hides_committed_records(self):
  self.save('dataset','checkpointed',source_type='user');writer=sqlite3.connect(self.db)
  try:
   writer.execute('PRAGMA journal_mode=WAL');writer.execute('PRAGMA wal_checkpoint(TRUNCATE)')
   writer.execute('INSERT INTO records VALUES(?,?,?,?)',('dataset','wal_only',json.dumps({'id':'wal_only','source_type':'user'}),'2026-11-14'));writer.commit()
   self.assertGreater(Path(str(self.db)+'-wal').stat().st_size,0);real_connect=sqlite3.connect
   with patch('sample_cleanup.sqlite3.connect',wraps=real_connect) as opened:
    self.assertEqual(scan(self.db)['record_count_before'],2)
   self.assertFalse(any('immutable=1' in str(call.args[0]) for call in opened.call_args_list))
   failure=SchemaReadFailure()
   with patch('sample_cleanup.sqlite3.connect',return_value=failure) as opened:
    with self.assertRaisesRegex(sqlite3.OperationalError,'unable to open database file'):scan(self.db)
   self.assertEqual(opened.call_count,1);self.assertTrue(failure.closed)
  finally:writer.close()
 def test_unrelated_read_error_does_not_enable_immutable(self):
  failure=SchemaReadFailure('database is locked')
  with patch('sample_cleanup.sqlite3.connect',return_value=failure) as opened:
   with self.assertRaisesRegex(sqlite3.OperationalError,'database is locked'):scan(self.db)
  self.assertEqual(opened.call_count,1);self.assertTrue(failure.closed)
 def test_empty_wal_allows_read_only_immutable_fallback(self):
  self.seed();cold=self.cold_wal_backup();Path(str(cold)+'-wal').touch();real_connect=sqlite3.connect;opened=[]
  def connect(target,*args,**kwargs):
   opened.append(str(target))
   if str(target)==cold.as_uri()+'?mode=ro':return SchemaReadFailure()
   return real_connect(target,*args,**kwargs)
  with patch('sample_cleanup.sqlite3.connect',side_effect=connect):self.assertEqual(scan(cold)['record_count_before'],9)
  self.assertEqual(len(opened),2);self.assertIn('immutable=1',opened[-1]);self.assertEqual(Path(str(cold)+'-wal').stat().st_size,0)
 def test_wal_created_during_immutable_read_aborts_scan(self):
  self.seed();cold=self.cold_wal_backup();real_connect=sqlite3.connect
  def connect(target,*args,**kwargs):
   if str(target)==cold.as_uri()+'?mode=ro':return SchemaReadFailure()
   connection=real_connect(target,*args,**kwargs)
   if 'immutable=1' in str(target):Path(str(cold)+'-wal').write_bytes(b'concurrent writer')
   return connection
  with patch('sample_cleanup.sqlite3.connect',side_effect=connect):
   with self.assertRaisesRegex(CleanupError,'WAL changed'):scan(cold)
 def test_snapshot_guard_still_rejects_new_record_during_backup(self):
  self.seed();self.save('production_task','bad_artifact',status='succeeded',plan={'mode':'sample_pixels'})
  artifact=self.db.parent/'production/bad_artifact/result.json';artifact.parent.mkdir(parents=True);artifact.write_text('{}')
  plan=scan(self.db);before=self.rows()
  import shutil
  real_copy=shutil.copy2
  def concurrent_write(source,target):
   result=real_copy(source,target);self.save('dataset','concurrent_real',source_type='user');return result
  with patch('sample_cleanup.shutil.copy2',side_effect=concurrent_write):
   with self.assertRaisesRegex(CleanupError,'Database changed during backup'):apply(self.db,plan,self.root/'racing-recovery')
  after=self.rows();self.assertTrue(all(after[key]==raw for key,raw in before.items()));self.assertIn(('dataset','concurrent_real'),after);self.assertTrue(artifact.exists())

if __name__=='__main__':unittest.main()
