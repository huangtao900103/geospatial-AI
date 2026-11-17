#!/usr/bin/env python3
"""Produce the complete Xiantao IO2024 native-grid reference atlas."""
import argparse,json,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
from backend.agriculture.city_classification import produce
p=argparse.ArgumentParser();p.add_argument('--output',type=Path);args=p.parse_args()
result=produce(args.output,progress=lambda detail:print(json.dumps(detail,ensure_ascii=False),flush=True))
print(json.dumps({'status':result['status'],'summary':result['summary']},ensure_ascii=False),flush=True)
