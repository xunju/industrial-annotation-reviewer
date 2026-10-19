'use strict';
const SquareModel=(()=>{
  const labels=['lt','rt','rb','lb'],clone=v=>JSON.parse(JSON.stringify(v));
  const valid=p=>Array.isArray(p)&&p.length===2&&p.every(Number.isFinite);
  const group=s=>s.group_id===undefined||s.group_id===null?null:s.group_id;
  const same=(a,b)=>JSON.stringify(a)===JSON.stringify(b);
  const boxOf=s=>{if(s.shape_type!=='rectangle'||!Array.isArray(s.points)||![2,4].includes(s.points.length)||!s.points.every(valid))return null;const xs=s.points.map(p=>p[0]),ys=s.points.map(p=>p[1]),x=Math.min(...xs),y=Math.min(...ys);return {x,y,w:Math.max(...xs)-x,h:Math.max(...ys)-y};};
  function rebuild(m){
    m.shapes=m.items.map(i=>i.shape);
    const boxes=m.items.filter(i=>i.shape.label==='desk'),corners=m.items.filter(i=>labels.includes(i.shape.label));
    const used=new Set();m.tables=boxes.map(item=>{
      const peers=boxes.filter(b=>same(group(b.shape),group(item.shape)));
      const members=peers.length===1?corners.filter(c=>same(group(c.shape),group(item.shape))):[];
      members.forEach(i=>used.add(i.id));
      return makeTable('table-'+JSON.stringify(group(item.shape))+'-'+peers.indexOf(item),group(item.shape),item,members,peers.length===1?[]:['同一 group_id 对应多个 desk 框，无法确定四角所属桌面']);
    });
    const remaining=corners.filter(c=>!used.has(c.id)),orphanGroups=[];
    for(const item of remaining){const g=group(item.shape);if(!orphanGroups.some(v=>same(v,g)))orphanGroups.push(g);}
    orphanGroups.forEach((g,i)=>m.tables.push(makeTable('orphan-'+JSON.stringify(g),g,null,remaining.filter(c=>same(group(c.shape),g)),['四角没有唯一对应的 desk 框'])));
    return m;
  }
  function makeTable(id,g,boxItem,members,initial){
    const points={},pointItems={},issues=[...initial];
    for(const label of labels){const found=members.filter(i=>i.shape.label===label);pointItems[label]=found.map(i=>i.id);if(found.length!==1)issues.push(found.length?`${label} 重复标注`:`缺少 ${label} 角点`);if(found[0]&&found[0].shape.shape_type==='point'&&Array.isArray(found[0].shape.points)&&found[0].shape.points.length===1&&valid(found[0].shape.points[0]))points[label]=clone(found[0].shape.points[0]);else if(found[0])issues.push(`${label} 必须是单点标注`);}
    const box=boxItem?boxOf(boxItem.shape):null;if(!box||box.w<=0||box.h<=0)issues.push('desk 必须是有效矩形框');
    return {id,label:'desk',group_id:g,box,points,issues,boxItem:boxItem?.id||null,pointItems};
  }
  function load(doc,reviewValid=false){const source=clone(doc),q=source.square_table_review||{},items=source.shapes.map((shape,i)=>({id:'shape-'+i,shape:clone(shape)}));return rebuild({source,items,shapes:[],tables:[],completed:reviewValid&&q.completed===true,stale:!!doc.square_table_review&&!reviewValid,serial:items.length});}
  const tables=m=>m?.tables||[],selected=(m,id)=>tables(m).find(t=>t.id===id);
  function issues(t,m){if(!t)return ['请选择方桌'];const result=[...t.issues],pts=labels.map(l=>t.points[l]);if(pts.every(valid)){
    const cross=(a,b,c)=>(b[0]-a[0])*(c[1]-b[1])-(b[1]-a[1])*(c[0]-b[0]);const signs=pts.map((p,i)=>cross(p,pts[(i+1)%4],pts[(i+2)%4]));
    if(!signs.every(v=>v>0))result.push('四角应按 lt → rt → rb → lb 顺时针形成凸四边形');
    if(pts.some(p=>p[0]+p[1]<pts[0][0]+pts[0][1]-1e-4))result.push('lt 应为图像中 x+y 最小的角点');
    if(pts.some(p=>Math.abs(p[0])<1e-6&&Math.abs(p[1])<1e-6))result.push('(0, 0) 是无效角点占位值');
    if(t.box&&pts.some(p=>p[0]<t.box.x-1||p[0]>t.box.x+t.box.w+1||p[1]<t.box.y-1||p[1]>t.box.y+t.box.h+1))result.push('检测框未包含全部四角');
  }return [...new Set(result)];}
  function output(m){const doc=clone(m.source);doc.shapes=clone(m.items.map(i=>i.shape));doc.checked=!!m.completed;doc.square_table_review={...(doc.square_table_review||{}),version:1,completed:!!m.completed};return doc;}
  function fresh(m,shape){const item={id:'shape-'+m.serial++,shape};m.items.push(item);return item;}
  function setPoint(m,id,label,p){if(!labels.includes(label)||!valid(p))throw Error('无效角点');const t=selected(m,id);if(!t)throw Error('方桌不存在');const found=m.items.find(i=>i.id===t.pointItems[label]?.[0]);if(found){found.shape.points=[clone(p)];found.shape.shape_type='point';}else fresh(m,{label,points:[clone(p)],shape_type:'point',group_id:t.group_id,flags:{}});m.completed=false;return rebuild(m);}
  function setBox(m,id,box){const t=selected(m,id);if(!t)throw Error('方桌不存在');const {x,y,w,h}=box;if(![x,y,w,h].every(Number.isFinite)||w<=0||h<=0)throw Error('无效检测框');let item=m.items.find(i=>i.id===t.boxItem);if(!item){item=fresh(m,{label:'desk',points:[],shape_type:'rectangle',group_id:t.group_id,flags:{}});item.table_id=id;}
    const pts=item.shape.points;item.shape.points=pts.length===4?[[x,y],[x+w,y],[x+w,y+h],[x,y+h]]:[[x,y],[x+w,y+h]];item.shape.shape_type='rectangle';m.completed=false;return rebuild(m);}
  function add(m,box){const numeric=m.items.map(i=>i.shape.group_id).filter(v=>Number.isInteger(v)),g=numeric.length?Math.max(-1,...numeric)+1:0;fresh(m,{label:'desk',points:[[box.x,box.y],[box.x+box.w,box.y+box.h]],shape_type:'rectangle',group_id:g,flags:{}});m.completed=false;rebuild(m);return 'table-'+JSON.stringify(g)+'-0';}
  function remove(m,id){const t=selected(m,id);if(!t)return m;const ids=new Set([t.boxItem,...Object.values(t.pointItems).flat()]);m.items=m.items.filter(i=>!ids.has(i.id));m.completed=false;return rebuild(m);}
  function removePoint(m,id,label){const t=selected(m,id);if(!t)return m;const ids=new Set(t.pointItems[label]||[]);m.items=m.items.filter(i=>!ids.has(i.id));m.completed=false;return rebuild(m);}
  return {clone,load,tables,selected,issues,output,setPoint,setBox,add,remove,removePoint};
})();
if(typeof module!=='undefined')module.exports=SquareModel;
