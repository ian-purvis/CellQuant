"""No inference: actual AST-extracted survey/queue/report functions with IO doubles."""
import ast,csv,hashlib,json,os,uuid,tempfile
from dataclasses import dataclass,asdict
from typing import Mapping
from datetime import datetime,timezone
from pathlib import Path
from types import SimpleNamespace as N
root=Path(__file__).resolve().parents[2]/'src/cellquant'
s=dict(globals());s["null_event_sink"]=lambda *a:None
def extract(file,names):
 tree=ast.parse((root/file).read_text())
 tree.body=[ast.ImportFrom(module='__future__',names=[ast.alias(name='annotations')],level=0)]+[x for x in tree.body if getattr(x,'name',None) in names]
 exec(compile(ast.fix_missing_locations(tree),str(root/file),'exec'),s)
class Config:
 def __init__(self): self.raw={'io':{'suffixes':['.tif'],'recursive':True}};self.fingerprint='configuration'
s.update(RunConfig=Config,normalize_suffixes=lambda x:x,iter_supported_files=lambda path,**kw:[path],replace_with_retry=lambda a,b:os.replace(a,b))
extract(Path('batch/__init__.py'),['BatchItem','BatchItemResult','BatchSummary','_raw_config','_input_fingerprint','_collision_name','_run_directory','_is_generated_output','build_queue','_atomic_json','_write_root_reports'])
extract(Path('survey/__init__.py'),['SurveyRecord','ChannelLayout','SurveyResult','SurveyRunResult','with_assignments','run_survey_batches'])
with tempfile.TemporaryDirectory() as d:
 d=Path(d);out=d/'out';sources=[]
 for sub in ['specimen_A','specimen_B']:
  p=d/sub/'image.tif';p.parent.mkdir();p.write_bytes(sub.encode());sources.append(str(p))
 layouts=tuple(s['ChannelLayout'](str(i),1,('DAPI',),1,(p,),0,0) for i,p in enumerate(sources))
 survey=s['SurveyResult'](1,'today',str(d),True,('.tif',),(),layouts,0)
 selected=s['with_assignments'](survey,{'0':0})
 print('EXCLUDED layout 1 remains assigned:',selected.layouts[1].segment_channel)
 assert selected.layouts[1].segment_channel==0
 q0=s['build_queue'](layouts[0].sources,out,Config());q1=s['build_queue'](layouts[1].sources,out,Config())
 print('CROSS-LAYOUT OUTPUT COLLISION:',q0[0].output_dir==q1[0].output_dir,q0[0].output_dir.name)
 assert q0[0].output_dir==q1[0].output_dir
 r0=s['BatchItemResult'](sources[0],str(q0[0].output_dir),'failed','first','first failed')
 r1=s['BatchItemResult'](sources[1],str(q1[0].output_dir),'completed','second')
 s['_write_root_reports'](out,[r0]);s['_write_root_reports'](out,[r1])
 last=json.loads((out/'batch_summary.json').read_text())
 print('AFTER TWO LAYOUT REPORT WRITES:',last['total'],'total,',last['failed'],'failed; failures.csv rows:',len(list(csv.DictReader((out/'failures.csv').open()))))
 assert last['total']==1 and last['failed']==0
print('THREE CONFIRMED: actual functions, synthetic paths only; no Cellpose or Qt execution')


