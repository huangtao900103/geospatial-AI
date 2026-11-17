#!/usr/bin/env python3
"""Dry-run by default; apply an exact reviewed plan only with a new recovery backup."""
import argparse,json,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
from backend.analysis.sample_cleanup import scan,apply,CleanupError
parser=argparse.ArgumentParser();parser.add_argument('--db',type=Path,default=Path('data/workspace.sqlite3'));parser.add_argument('--output',type=Path);parser.add_argument('--apply-plan',type=Path);parser.add_argument('--backup-dir',type=Path);args=parser.parse_args()
try:
 if args.apply_plan:
  if not args.backup_dir:parser.error('--backup-dir is required with --apply-plan')
  result=apply(args.db,json.loads(args.apply_plan.read_text()),args.backup_dir)
 else:result=scan(args.db)
 if args.output:args.output.parent.mkdir(parents=True,exist_ok=True);args.output.write_text(json.dumps(result,ensure_ascii=False,indent=2))
 print(json.dumps({k:result[k] for k in ('schema_version','delete_count','delete_counts_by_kind') if k in result},ensure_ascii=False))
 if not args.output and not args.apply_plan:print(json.dumps(result,ensure_ascii=False,indent=2))
except (CleanupError,ValueError,OSError) as error:print('Cleanup stopped: '+str(error),file=sys.stderr);sys.exit(2)
