"""Exercise the exact browser alignment module, independent of DOM/render timing."""
import json
from pathlib import Path
import subprocess
import pytest


def run_js(function,args):
    js="""import * as alignment from './client/js/pdfalignment.js';
    let input='';for await(const chunk of process.stdin)input+=chunk;
    const {fn,args}=JSON.parse(input);console.log(JSON.stringify(alignment[fn](...args)));"""
    result=subprocess.run(['node','--input-type=module','-e',js],input=json.dumps({'fn':function,'args':args}),
        text=True,capture_output=True,check=True,cwd=Path(__file__).resolve().parents[1],timeout=10)
    return json.loads(result.stdout)


def geo(rect,rotation=0):
    return {'coordinate_system':'pymupdf_unrotated','units':'pt','rect':rect,'rotation':rotation,'page_rect':[0,0,600,800]}


def report(rotation=0):
    return {'layout_version':'reflow-1','units':[{'block_id':'original','page':3,'bbox':geo([50,100,550,200],rotation),
        'kind':'text','placements':[{'japanese_page':1,'japanese_bbox':geo([44,400,550,450])}]}],'assets':[]}


@pytest.mark.parametrize('rotation',[0,90,180,270])
def test_semantic_switch_uses_original_block_and_handles_rotated_source(rotation):
    r=report(rotation)
    p={'page':3,'x':.5,'y':150/800}
    if rotation==90:p.update(x=1-150/800,y=.5)
    if rotation==180:p.update(x=.5,y=1-150/800)
    if rotation==270:p.update(x=150/800,y=.5)
    jp=run_js('mapPosition',[r,p,'toJapanese'])
    assert jp['page']==1 and abs(jp['y']-425/800)<.00001
    back=run_js('mapPosition',[r,jp,'toOriginal'])
    assert back['page']==3 and abs(back['x']-p['x'])<.00001 and abs(back['y']-p['y'])<.00001


def test_japanese_selection_identifies_original_bbox_not_derived_bbox():
    anchor=run_js('originalSelection',[report(),1,geo([70,405,200,440])])
    assert anchor['blockId']=='original' and anchor['page']==3 and anchor['bbox']['rect']==[50,100,550,200]
    assert run_js('originalSelection',[report(),2,geo([70,405,200,440])]) is None


def test_legacy_v5_artifact_position_still_works():
    position={'page':2,'x':.5,'y':.3}
    assert run_js('mapPosition',[{'units':[]},position,'toJapanese'])==position


def test_visual_selection_maps_native_pixels_to_original_cell():
    r=report();r['units']=[{'block_id':'table','page':3,'bbox':geo([40,100,550,600]),'kind':'visual','asset_id':'V1',
       'placements':[{'japanese_page':2,'japanese_bbox':geo([44,44,554,544])}]},
       {'block_id':'cell','page':3,'bbox':geo([48,160,250,175]),'kind':'visual','asset_id':'V1',
       'placements':[{'japanese_page':2,'japanese_bbox':geo([44,44,554,544])}]}]
    r['assets']=[{'id':'V1','original_block_ids':['table','cell'],'source_page':3,'source_bbox':[40,100,550,600],
       'japanese_page':2,'japanese_bbox':geo([44,44,554,544])}]
    selected=run_js('originalSelection',[r,2,geo([52,104,254,119])])
    assert selected['blockId']=='cell' and selected['page']==3
