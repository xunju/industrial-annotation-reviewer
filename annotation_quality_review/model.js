/* Geometry and LabelMe contract. Untouched shapes retain their exact coordinates and metadata. */
'use strict';
const ReviewModel = (() => {
  const labels = ['top_left', 'top_right', 'bot_left', 'bot_right'];
  const referenceLabels = ['wai_1', 'wai_2', 'wai_3', 'nei_1', 'nei_2', 'nei_3'];
  // JSON storage order is preserved; perimeter order uses names, never screen position.
  const outlineLabels = ['top_left', 'top_right', 'bot_right', 'bot_left'];
  const keyLabels = {q:'top_left',w:'top_right',s:'bot_right',a:'bot_left',
                    '1':'top_left','2':'top_right','3':'bot_right','4':'bot_left'};
  const clone = value => JSON.parse(JSON.stringify(value));
  const signature = shapes => {
    const str = JSON.stringify(shapes); let hash = 2166136261;
    for (let i = 0; i < str.length; i++) hash = Math.imul(hash ^ str.charCodeAt(i), 16777619);
    return (hash >>> 0).toString(16);
  };
  const valid = p => Array.isArray(p) && p.length === 2 && p.every(Number.isFinite);
  function ellipse(shape) {
    const pts = shape.points;
    if (shape.shape_type === 'circle' && pts.length === 2) {
      return {cx: pts[0][0], cy: pts[0][1], rx: Math.hypot(pts[1][0]-pts[0][0], pts[1][1]-pts[0][1]), ry: Math.hypot(pts[1][0]-pts[0][0], pts[1][1]-pts[0][1]), angle: 0};
    }
    const n = pts.length, cx = pts.reduce((a,p)=>a+p[0],0)/n, cy = pts.reduce((a,p)=>a+p[1],0)/n;
    let xx=0, xy=0, yy=0;
    pts.forEach(p=>{const x=p[0]-cx,y=p[1]-cy;xx+=x*x/n;xy+=x*y/n;yy+=y*y/n;});
    const delta=Math.hypot(xx-yy,2*xy);
    return {cx, cy, rx:Math.sqrt(Math.max(.25,xx+yy+delta)), ry:Math.sqrt(Math.max(.25,xx+yy-delta)), angle:delta<.02?0:.5*Math.atan2(2*xy,xx-yy)};
  }
  function polygon(e) {
    const c=Math.cos(e.angle),s=Math.sin(e.angle);
    return Array.from({length:16},(_,i)=>{const a=i*Math.PI/8,x=e.rx*Math.cos(a),y=e.ry*Math.sin(a);return [e.cx+x*c-y*s,e.cy+x*s+y*c].map(v=>Math.round(v*1000)/1000);});
  }
  function bounds(points) {
    const good=points.filter(valid);
    return good.length ? [Math.min(...good.map(p=>p[0])),Math.min(...good.map(p=>p[1])),Math.max(...good.map(p=>p[0])),Math.max(...good.map(p=>p[1]))] : [0,0,20,20];
  }
  function ellipseBounds(e) {
    const x=Math.hypot(e.rx*Math.cos(e.angle),e.ry*Math.sin(e.angle));
    const y=Math.hypot(e.rx*Math.sin(e.angle),e.ry*Math.cos(e.angle));
    return [e.cx-x,e.cy-y,e.cx+x,e.cy+y];
  }
  function load(doc) {
    const source=clone(doc), q=source.quality_review || {}, trusted=q.shape_signature===signature(source.shapes);
    const shapes=source.shapes.map((raw,i)=>{
      const shape=clone(raw);
      return {id:'shape-'+i,shape,e:['wai','nei'].includes(shape.label)&&shape.points?.length>=2&&shape.points.every(valid)?ellipse(shape):null};
    });
    const used=new Set(), parts=[];
    const add=(type,members,id)=>{
      if(!members.length)return;
      let partId=id||type+'-'+parts.length;
      while(parts.some(p=>p.id===partId))partId+='-new';
      members.forEach(s=>used.add(s.id));parts.push({type,id:partId,members:members.map(s=>s.id)});
    };
    // Saved washer associations remain valid. Bolt associations are rebuilt from
    // actual screw boxes, so old point-only groups cannot create extra bolts.
    const savedParts=trusted&&Array.isArray(q.parts)?q.parts:[];
    for(const p of savedParts){
      const members=(p.shape_indices||[]).map(i=>shapes[i]).filter(s=>s&&!used.has(s.id)&&s.shape.label!=='screw'&&!labels.includes(s.shape.label));
      if(p.type==='washer')add(p.type,members,p.id);
    }
    const remaining=()=>shapes.filter(s=>!used.has(s.id));
    // Honor group IDs; infer only ungrouped holes, keeping their source group IDs intact.
    for(const outer of remaining().filter(s=>s.e&&s.shape.label==='wai')){
      if(used.has(outer.id))continue;
      const gid=outer.shape.group_id;
      const paired=remaining().filter(s=>s.e&&s!==outer&&s.shape.label==='nei'&&gid!=null&&s.shape.group_id===gid);
      add('washer',[outer,...paired]);
    }
    for(const hole of remaining().filter(s=>s.e)){
      const candidates=parts.filter(p=>p.type==='washer').map(p=>({p,outer:shapes.find(s=>p.members.includes(s.id)&&s.shape.label==='wai')})).filter(o=>o.outer&&hole.e.rx*hole.e.ry<o.outer.e.rx*o.outer.e.ry&&Math.hypot(hole.e.cx-o.outer.e.cx,hole.e.cy-o.outer.e.cy)<Math.max(o.outer.e.rx,o.outer.e.ry)*.65);
      candidates.sort((a,b)=>Math.hypot(hole.e.cx-a.outer.e.cx,hole.e.cy-a.outer.e.cy)-Math.hypot(hole.e.cx-b.outer.e.cx,hole.e.cy-b.outer.e.cy));
      if(candidates.length){candidates[0].p.members.push(hole.id);used.add(hole.id);}else add('washer',[hole]);
    }
    // Six reference points describe a native washer pose, not ring polygons.
    // Unique groups attach directly; repeated/null groups use only an unambiguous
    // containing box, so unrelated entities are never merged by a shared null ID.
    const referenceBoxes=remaining().filter(s=>s.shape.label==='shim');
    const referenceAssignments=new Map(referenceBoxes.map(s=>[s.id,[]]));
    for(const point of remaining().filter(s=>referenceLabels.includes(s.shape.label))){
      const peers=referenceBoxes.filter(box=>box.shape.group_id===point.shape.group_id);
      let box=peers.length===1?peers[0]:null;
      if(!box&&point.shape.points?.length===1&&valid(point.shape.points[0])){
        const [x,y]=point.shape.points[0];
        const inside=peers.filter(candidate=>{
          const [left,top,right,bottom]=bounds(candidate.shape.points||[]);
          return x>=left&&x<=right&&y>=top&&y<=bottom;
        });
        if(inside.length===1)box=inside[0];
      }
      if(box)referenceAssignments.get(box.id).push(point);
    }
    for(const box of referenceBoxes)add('washer',[box,...referenceAssignments.get(box.id)]);
    for(const point of remaining().filter(s=>referenceLabels.includes(s.shape.label))){
      if(used.has(point.id))continue;
      const gid=point.shape.group_id;
      add('washer',gid==null?[point]:remaining().filter(s=>s.shape.group_id===gid&&referenceLabels.includes(s.shape.label)));
    }
    const boltBoxes=remaining().filter(s=>s.shape.label==='screw');
    const boltAssignments=new Map(boltBoxes.map(s=>[s.id,[]]));
    const unassignedBoltPoints=[],boltGroupingIssues=[];
    const usableBox=box=>box.shape.shape_type==='rectangle'&&[2,4].includes(box.shape.points?.length)&&box.shape.points.every(valid);
    for(const point of remaining().filter(s=>labels.includes(s.shape.label))){
      const gid=point.shape.group_id;
      const peers=gid==null?[]:boltBoxes.filter(box=>box.shape.group_id===gid);
      let box=peers.length===1?peers[0]:null;
      let inside=[];
      if(!box&&point.shape.points?.length===1&&valid(point.shape.points[0])){
        const [x,y]=point.shape.points[0];
        inside=boltBoxes.filter(candidate=>{
          if(!usableBox(candidate))return false;
          const [left,top,right,bottom]=bounds(candidate.shape.points);
          return right>left&&bottom>top&&x>=left&&x<=right&&y>=top&&y<=bottom;
        });
        // Test every box before looking at group IDs or free point slots. An
        // overlapping neighbor must never inherit a duplicate corner point.
        if(inside.length===1&&(gid==null||peers.includes(inside[0])))box=inside[0];
      }
      if(box)boltAssignments.get(box.id).push(point);
      else{
        unassignedBoltPoints.push(point.id);
        boltGroupingIssues.push({shape_id:point.id,label:point.shape.label,reason:inside.length>1?'ambiguous_boxes':'no_unique_matching_box',candidate_box_ids:inside.map(s=>s.id)});
      }
    }
    for(const box of boltBoxes){
      const previous=savedParts.filter(p=>p.type==='bolt'&&(p.shape_indices||[]).map(i=>shapes[i]).filter(s=>s?.shape.label==='screw').length===1&&(p.shape_indices||[]).some(i=>shapes[i]===box));
      add('bolt',[box,...boltAssignments.get(box.id)],previous.length===1?previous[0].id:undefined);
    }
    orderParts({shapes,parts},!trusted);
    const ids=new Set(parts.map(p=>p.id));
    const model={source,shapes,parts,unassignedBoltPoints,boltGroupingIssues,confirmed:trusted?(q.confirmed||[]).filter(id=>ids.has(id)):[],deferred:trusted?(q.deferred||[]).filter(id=>ids.has(id)):[],
      completed:trusted&&q.completed===true, phase:'overview',
      current:trusted&&ids.has(q.current)?q.current:parts[0]?.id||null,
      stale:!!source.quality_review&&!trusted, serial:shapes.length+parts.length};
    return model;
  }
  function members(model,part) { return part ? part.members.map(id=>model.shapes.find(s=>s.id===id)).filter(Boolean) : []; }
  function orderParts(model,spatial=false) {
    model.parts.sort((a,b)=>{
      const type=(a.type==='bolt'?0:1)-(b.type==='bolt'?0:1);if(type||!spatial)return type;
      const aa=partBounds(model,a),bb=partBounds(model,b);return aa[1]-bb[1]||aa[0]-bb[0];
    });
  }
  function requiresIndividualReview() {return false;}
  function nextPending() {return undefined;}
  function boltOutline(model,part) {
    const ms=members(model,part);
    return outlineLabels.map(label=>ms.find(s=>s.shape.label===label)?.shape.points?.[0]||null);
  }
  function partBounds(model,part) {
    const ms=members(model,part);
    if(part.type==='washer'){
      const outer=ms.find(s=>s.shape.label==='wai');
      if(outer?.e)return ellipseBounds(outer.e);
      const box=ms.find(s=>s.shape.label==='shim');
      return bounds(box?box.shape.points:ms.flatMap(s=>s.shape.points));
    }
    const box=ms.find(s=>s.shape.label==='screw');
    return bounds(box?box.shape.points:ms.flatMap(s=>s.shape.points));
  }
  function center(model,part) {
    if(part.type==='washer'){const ms=members(model,part),e=(ms.find(s=>s.shape.label==='wai')||ms[0])?.e;if(e)return [e.cx,e.cy];}
    const b=partBounds(model,part);return [(b[0]+b[2])/2,(b[1]+b[3])/2];
  }
  function pointError(points) {
    if(points.some(p=>!valid(p)))return '四个点尚未标齐，请补齐后确认。';
    const p=outlineLabels.map(label=>points[labels.indexOf(label)]);
    const turns=p.map((a,i)=>{const b=p[(i+1)%4],c=p[(i+2)%4];return (b[0]-a[0])*(c[1]-b[1])-(b[1]-a[1])*(c[0]-b[0]);});
    return turns.every(t=>t>1e-5)||turns.every(t=>t< -1e-5)?null:'四点连线交叉或退化，请检查点名和位置。';
  }
  function partError(model,part) {
    const ms=members(model,part);
    if(part.type==='washer'){
      const outer=ms.filter(s=>s.shape.label==='wai'),inner=ms.filter(s=>s.shape.label==='nei');
      if(outer.length||inner.length){
        if(outer.length!==1||inner.length!==1)return '每个垫片需要一个外圈和一个内孔，请检查缺圈或重复轮廓。';
        if(!outer[0].e||!inner[0].e||inner[0].e.rx*inner[0].e.ry>=outer[0].e.rx*outer[0].e.ry)return '内孔范围应小于外圈，请调整后确认。';
        return null;
      }
      const boxes=ms.filter(s=>s.shape.label==='shim');
      if(boxes.length!==1)return '六点垫片需要一个 shim 框，请检查缺框或重复框。';
      const box=boxes[0].shape;
      if(box.shape_type!=='rectangle'||![2,4].includes(box.points?.length)||!box.points.every(valid))return 'shim 必须是有限坐标的两点或四点矩形框。';
      const b=bounds(box.points);
      if(b[2]<=b[0]||b[3]<=b[1])return 'shim 框退化，请修正后确认。';
      const points=[];
      for(const label of referenceLabels){
        const found=ms.filter(s=>s.shape.label===label);
        if(found.length!==1)return (found.length?'重复 ':'缺少 ')+label+' 六点垫片参考点。';
        const shape=found[0].shape;
        if(shape.shape_type!=='point'||shape.points?.length!==1||!valid(shape.points[0]))return label+' 必须是一个有限坐标点。';
        points.push(shape.points[0]);
      }
      for(let i=0;i<6;i+=3){
        const [a,b,c]=points.slice(i,i+3);
        const cross=(b[0]-a[0])*(c[1]-a[1])-(b[1]-a[1])*(c[0]-a[0]);
        if(Math.abs(cross)<=1e-5)return (i?'nei':'wai')+' 三个参考点共线或退化，请修正后确认。';
      }
      return null;
    }
    const boxes=ms.filter(s=>s.shape.label==='screw');
    if(boxes.length!==1)return '需要一个螺栓框，请先补框。';
    const box=boxes[0].shape;
    if(box.shape_type!=='rectangle'||![2,4].includes(box.points?.length)||!box.points.every(valid))return '螺栓框必须是有限坐标的两点或四点矩形框。';
    const b=bounds(box.points);
    if(b[2]<=b[0]||b[3]<=b[1])return '螺栓框退化，请核对。';
    if(labels.some(label=>ms.filter(s=>s.shape.label===label).length>1))return '同一个关键点重复标注，请删除多余点。';
    if(ms.some(s=>labels.includes(s.shape.label)&&(s.shape.shape_type!=='point'||s.shape.points?.length!==1)))return '螺栓角点必须是单点标注，请核对。';
    return pointError(labels.map(label=>ms.find(s=>s.shape.label===label)?.shape.points?.[0]));
  }
  function overviewIssues(model) {
    const issues=[],bolts=model.parts.filter(p=>p.type==='bolt');
    bolts.forEach((p,i)=>{const error=partError(model,p);if(error)issues.push('螺栓 '+(i+1)+'：'+error);});
    const unassigned=model.unassignedBoltPoints||[];
    if(unassigned.length)issues.push(unassigned.length+' 个螺栓角点无法唯一对应框，已保留为未分组点，请结合整图核对。');
    const deferred=bolts.filter(p=>model.deferred.includes(p.id));
    if(deferred.length)issues.push(deferred.length+' 个螺栓待复查，核对整图后可由人工确认完成。');
    const expected=model.source.bolt_review?.expected;
    if(Number.isFinite(expected)&&expected!==bolts.length)issues.push('原记录预计 '+expected+' 个螺栓，当前有 '+bolts.length+' 个螺栓框，请核对数量。');
    const washers=model.parts.filter(p=>p.type==='washer');
    washers.forEach((p,i)=>{const error=partError(model,p);if(error)issues.push('垫片 '+(i+1)+'：'+error);});
    const deferredWashers=washers.filter(p=>model.deferred.includes(p.id));
    if(deferredWashers.length)issues.push('旧记录中有 '+deferredWashers.length+' 个垫片待复查，请结合整图核对后由人工确认完成。');
    const assigned=new Set(model.parts.flatMap(p=>p.members));
    const unassignedWashers=model.shapes.filter(s=>['wai','nei','shim',...referenceLabels].includes(s.shape.label)&&!assigned.has(s.id));
    if(unassignedWashers.length)issues.push(unassignedWashers.length+' 个垫片标签未建立显示关联，原始标注已保留，请结合整图核对。');
    return issues;
  }
  // Associations support navigation and editing; their inferred structure and
  // legacy per-part progress never gate an explicit human overview decision.
  function completionError() {return null;}
  function invalidate(model,id) {model.confirmed=model.confirmed.filter(v=>v!==id);model.deferred=model.deferred.filter(v=>v!==id);model.completed=false;}
  function output(model) {
    const doc=clone(model.source),index=new Map(model.shapes.map((s,i)=>[s.id,i]));
    doc.shapes=model.shapes.map(s=>clone(s.shape));
    doc.quality_review={...(doc.quality_review||{}),schema:1,shape_signature:signature(doc.shapes),confirmed:[...model.confirmed],deferred:[...model.deferred],
      completed:model.completed,phase:model.phase,current:model.current,updated_at:new Date().toISOString(),
      parts:model.parts.map(p=>({id:p.id,type:p.type,shape_indices:p.members.map(id=>index.get(id)).filter(i=>i!==undefined)}))};
    doc.quality_review.part_review_mode='overview';
    if(model.shapes.some(s=>['wai','nei','shim',...referenceLabels].includes(s.shape.label)))doc.quality_review.washer_review_mode='overview';
    const bolts=model.parts.filter(p=>p.type==='bolt');
    if(bolts.length||(model.unassignedBoltPoints||[]).length||doc.bolt_review){
      doc.quality_review.bolt_review_mode='overview';
      doc.quality_review.bolt_box_count=bolts.length;
    }
    doc.checked=model.completed;
    if(Object.prototype.hasOwnProperty.call(doc.flags||{},'needs_manual_review'))doc.flags.needs_manual_review=!model.completed;
    // Sync existing shadow geometry only when its explicit group association is
    // unique. Ambiguous metadata remains intact and receives a diagnostic; it
    // never becomes evidence for grouping the editable LabelMe shapes.
    if(Array.isArray(doc.bolt_review?.objects)){
      const objects=doc.bolt_review.objects,unsynced=[];
      const geometry=shape=>({label:shape.label,points:shape.points,shape_type:shape.shape_type,group_id:shape.group_id});
      const changed=shape=>{
        // This runs only for a unique explicit group. Match by group and label,
        // because a successful save may reindex shapes after a deletion.
        const originals=model.source.shapes.filter(s=>s.group_id===shape.shape.group_id&&s.label===shape.shape.label);
        const original=originals.length===1?originals[0]:null;
        return !original||JSON.stringify(geometry(shape.shape))!==JSON.stringify(geometry(original));
      };
      objects.forEach((object,objectIndex)=>{
        if(object.state==='rejected')return;
        const gid=object.group_id;
        const candidates=gid==null?[]:bolts.filter(p=>members(model,p).some(s=>s.shape.label==='screw'&&s.shape.group_id===gid));
        let reason=gid==null?'null_group_id':objects.filter(o=>o.group_id===gid).length!==1?'duplicate_object_group_id':candidates.length!==1?'no_unique_box_group_id':null;
        const ms=!reason?members(model,candidates[0]):[],box=ms.find(s=>s.shape.label==='screw');
        if(!reason&&(box.shape.shape_type!=='rectangle'||![2,4].includes(box.shape.points?.length)||!box.shape.points.every(valid)))reason='invalid_box';
        if(!reason){const b=bounds(box.shape.points);if(b[2]<=b[0]||b[3]<=b[1])reason='invalid_box';}
        const pointShapes=labels.map(label=>ms.filter(s=>s.shape.label===label));
        if(!reason&&pointShapes.some(points=>points.length!==1||points[0].shape.shape_type!=='point'||points[0].shape.points?.length!==1||!valid(points[0].shape.points[0])))reason='no_unique_finite_four_points';
        if(reason)unsynced.push({object_index:objectIndex,object_id:object.id??null,group_id:gid??null,reason});
        else if([box,...pointShapes.flat()].some(changed)){
          object.bbox=partBounds(model,candidates[0]);
          object.points=pointShapes.map(points=>clone(points[0].shape.points[0]));
          if(Array.isArray(object.point_sources))object.point_sources=pointShapes.map((points,i)=>changed(points[0])?'human':object.point_sources[i]||'human');
        }
        // This state records the human overview decision; the diagnostic above
        // separately says whether legacy shadow geometry could be synchronized.
        if(model.completed)object.state='done';
      });
      doc.quality_review.bolt_metadata_unsynced=unsynced;
      doc.bolt_review.boxes_confirmed=!!model.completed;
    }
    return doc;
  }
  function newShape(model,label,points,type,group) {
    const s={id:'new-shape-'+(++model.serial),shape:{label,points:clone(points),group_id:group,shape_type:type,flags:{},description:'',score:null}};
    if(['wai','nei'].includes(label))s.e=ellipse(s.shape);
    model.shapes.push(s);return s;
  }
  function nextGroup(model) {return Math.max(-1,...model.shapes.map(s=>typeof s.shape.group_id==='number'?s.shape.group_id:-1))+1;}
  return {labels,referenceLabels,outlineLabels,keyLabels,clone,signature,ellipse,polygon,bounds,ellipseBounds,load,members,orderParts,requiresIndividualReview,nextPending,boltOutline,partBounds,center,pointError,partError,overviewIssues,completionError,invalidate,output,newShape,nextGroup};
})();
if(typeof module!=='undefined')module.exports=ReviewModel;
