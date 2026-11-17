import assert from 'node:assert/strict';
import {MapWorkspace} from '../../dist/map-workspace.js';
import {MapCopilot} from '../../dist/map-copilot.js';

const storage=new Map();
globalThis.localStorage={getItem:key=>storage.get(key)??null,setItem:(key,value)=>storage.set(key,value)};
const area={name:'Regression selection',bbox:[113.3,30.3,113.31,30.31]};
const clear={hidden:true},select={classList:{toggle(){}}},counter={};
const layers=new Set();
const workspace=Object.assign(Object.create(MapWorkspace.prototype),{
 context:{},selectionActive:false,map:{removeLayer:layer=>layers.delete(layer)},
 element:{querySelectorAll:selector=>selector.includes('clear-area')?[clear]:[select]},
 paintArea(){this.areaOutline={};layers.add(this.areaOutline)},
 q:()=>counter,renderOwned(){},renderResults(){},resultCount:()=>0,toast(){},
});
workspace.showSelectedArea(area);
assert.equal(clear.hidden,false);assert.equal(layers.size,1);
await workspace.updateContext({selectedAOI:null});
assert.equal(workspace.areaSelection,null);assert.equal(workspace.areaOutline,null);
assert.equal(layers.size,0);assert.equal(clear.hidden,true);

const copilot=Object.assign(Object.create(MapCopilot.prototype),{
 element:{querySelector:()=>null},tab:'chat',onAreaChange:area=>workspace.showSelectedArea(area),
});
workspace.onAreaChange=area=>copilot.setArea(area);
copilot.setArea(area);
assert.deepEqual(JSON.parse(storage.get('geoai-v9-selected-area')),area);
await workspace.perform('clear-area',{});
assert.equal(copilot.area,null);assert.equal(workspace.context.selectedAOI,null);
assert.equal(JSON.parse(storage.get('geoai-v9-selected-area')),null);
await workspace.updateContext({selectedAOI:copilot.area});
assert.equal(layers.size,0);

copilot.setArea(area);
await copilot.act('clear-area',{});
assert.equal(workspace.areaOutline,null);assert.equal(clear.hidden,true);

copilot.setArea(area);
workspace.selectionActive=true;
let resolved=false;
workspace.areaResolve=value=>{assert.equal(value,null);resolved=true};
workspace.endAreaSelection=function(){this.selectionActive=false;this.areaResolve=null};
await workspace.perform('clear-area',{});
assert(resolved);assert.equal(workspace.selectionActive,false);assert.equal(copilot.area,null);
assert.equal(layers.size,0);
assert(MapWorkspace.prototype.shell.call({}).includes('aria-label="清除框选"'));
console.log(JSON.stringify({passed:true,cases:5,checks:['null context removes the outline','toolbar clears persistent selection','refresh cannot resurrect cleared selection','query chip uses the same state','clear cancels pending selection without accepting its draft']},null,2));
