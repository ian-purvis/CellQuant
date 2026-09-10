"""Audit: execute actual AST-extracted batch UI methods with doubles; no Qt GUI."""
import ast,json,tempfile
from pathlib import Path
from types import SimpleNamespace as N
import numpy as np
from cellquant.classify import ClassificationRecipe
from cellquant.contracts import ImageVolume,LabelVolume
root=Path(__file__).resolve().parents[2]
c=next(n for n in ast.parse((root/'src/cellquant/plugin/coexpression_batch.py').read_text()).body if isinstance(n,ast.ClassDef))
c.body=[n for n in c.body if isinstance(n,ast.FunctionDef) and n.name in ['apply_recipe_to_table','recipe_from_table','open_for_review','save_curated_labels']];c.bases=[]
s={'Path':Path,'json':json,'ClassificationRecipe':ClassificationRecipe,'LabelVolume':LabelVolume}
exec(compile(ast.fix_missing_locations(ast.Module(body=[c],type_ignores=[])),'actual_ui_methods','exec'),s)
class T:
 def __init__(self,v=''):self.v=v
 def text(self):return self.v
 def setText(self,v):self.v=v
class C:
 def currentData(self):return self.v
 def findData(self,v):return v
 def setCurrentIndex(self,v):self.v=v
class Table:
 def __init__(self):self.rows=[]
 def setRowCount(self,n):self.rows=[]
 def rowCount(self):return len(self.rows)
 def item(self,r,c):return self.rows[r][c]
p=s['BatchCoexpressionPanel']();p.recipe_name=T();p.calibration_group=T();p.markers=Table();combos=[]
def add():p.markers.rows.append([T() for _ in range(6)]);combos.append(C())
p.add_marker=add;p._marker_combo=lambda r:combos[r];p._current_layout_channels=lambda:['A']
r=ClassificationRecipe(dict(name='custom',region_policy='centroid',markers=[dict(name='A',channel=0,low=10,positive_fraction=.5,calibration={'control':'negative'})],queries=[dict(name='conditional',positive=['A'],denominator_positive=['A'])]))
p.apply_recipe_to_table(r);r2=p.recipe_from_table()
print('QUERY BEFORE:',r.raw['queries'],'AFTER:',r2.raw['queries']);print('CALIBRATION AFTER:',r2.raw['markers'][0].get('calibration'));print('REGION POLICY AFTER:',r2.raw['region_policy'])
assert r.raw['queries']!=r2.raw['queries'] and 'calibration' not in r2.raw['markers'][0]
with tempfile.TemporaryDirectory() as t:
 run=Path(t);source=run/'source.tif';source.touch();(run/'provenance.json').write_text(json.dumps({'source':str(source)}))
 s['load_config']=lambda _:N(raw={'io':{},'segment':{'mode':'single_plane_2d','z_index':2}});s['read_run_labels']=lambda _:np.ones((1,4,4),np.uint32)
 v=ImageVolume(np.zeros((3,4,4,1),np.uint16),(1.,1.,1.),('A',),source,{})
 ctl=N(image_volume=None);ctl.open_path=lambda *a,**k:setattr(ctl,'image_volume',v);ctl._publish_labels=lambda labels:setattr(ctl,'labels',labels)
 p.controller=ctl;p.review_pick=N(currentData=lambda:str(run));p.review_status=T();p.status=T();p.open_for_review()
 print('REVIEW IMAGE SHAPE:',ctl.image_volume.data.shape,'LABEL SHAPE:',ctl.labels.data.shape,'TRANSFORM:',ctl.image_volume.metadata)
 assert ctl.image_volume.data.shape[0]==3 and ctl.labels.data.shape[0]==1
 ctl.image_volume=ImageVolume(v.data,(7.,7.,7.),('A',),run/'old.tif',{});ctl.open_path=lambda *a,**k:None
 pending=[];s['QtCore']=N(QTimer=N(singleShot=lambda ms,fn:pending.append(fn)));p.open_for_review();pending.pop(0)()
 print('STALE REVIEW SOURCE:',ctl.image_volume.source.name,'NEW LABEL SPACING:',ctl.labels.spacing_um)
 assert ctl.labels.spacing_um==(7.,7.,7.)
 unrelated=np.full((1,4,4),9,np.uint32);p.viewer=N(layers=[N(name='Labels',data=unrelated,metadata={'source_run':'different-run'})]);s['LABEL_LAYER_NAME']='Labels';s['_validated_label_array']=lambda x:x
 saved=[]
 s['save_reviewed_labels']=lambda dest,data:(saved.append((dest,data.copy())) or dest/'labels_reviewed.tif')
 p.runs=();p._populate_run_list=lambda:None;p.refresh_review_pick=lambda:None;p.save_curated_labels()
 assert saved[0][0]==run and np.array_equal(saved[0][1],unrelated)
 print('UNRELATED LABELS ACCEPTED FOR ORIGINAL RUN:',int(saved[0][1].max()))
print('FOUR REPRODUCTIONS CONFIRMED using actual AST methods and doubles; not real Qt interaction')
