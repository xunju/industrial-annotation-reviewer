'use strict';
const $=id=>document.getElementById(id), R=ReviewModel;
const stage=$('stage'), mini=$('mini'), colors={wai:'#e18a2c',nei:'#08b3db'}, pointColors=['#e09a22','#098cbd','#b05ca4','#24846b'];
const keyIndex=Object.fromEntries(Object.entries(R.keyLabels).map(([key,label])=>[key,R.labels.indexOf(label)]));
const directions=[[1,0],[1,1],[0,1],[-1,1],[-1,0],[-1,-1],[0,-1],[1,-1]];
let config=null,dataset=null,rows=[],current=-1,M=null,image=null,rev='',loaded=false,busy=false,dirty=false,version=0,savePromise=null,saveTimer=null,conflict=false;
let selectedShape=null,view={cx:0,cy:0,scale:1},transform=null,pointer=null,drag=null,addMode=null,held=null,hidden=false,spaceHeld=false,spacePanned=false,shiftHeld=false;
let imageRevision='',lastDeletion=null,deletionHistory=[];
let requestedImageId=new URLSearchParams(window.location.search).get('image');
let undoStack=[],redoStack=[],imageURL=null,queueVersion=0;
let listRows=null,listButtons=[];
let datasetPartKinds=[],imagePartKinds=[];
function matchesRow(row){
  const text=$('search').value.trim().toLowerCase(),filter=$('filter').value;
  return (!text||[row.id,row.name].join(' ').toLowerCase().includes(text))
    && !(filter==='pending'&&row.checked||filter==='done'&&!row.checked);
}
function nextVisibleIndex(direction,start=current){
  for(let i=start+direction;i>=0&&i<rows.length;i+=direction)if(matchesRow(rows[i]))return i;
  return -1;
}
function navigateVisible(direction){return navigate(nextVisibleIndex(direction));}
async function changeListFilter(){
  if(busy||drag)return;setBusy(true);
  try{if(!await save())return;renderList();if(!loaded||!matchesRow(file())){
    let i=nextVisibleIndex(1);if(i<0)i=rows.findIndex(matchesRow);
    if(i>=0)await load(i);else showEmptyDataset();
  }}catch(e){message(e.message,'error');}finally{setBusy(false);}
}
const part=()=>M?.parts.find(p=>p.id===M.current), shape=()=>M?.shapes.find(s=>s.id===selectedShape);
const file=()=>rows[current], draftKey=()=>`quality-review-draft:${dataset.directory}:${file()?.id}`;
const lastKey=()=>`quality-review-last:${dataset.directory}`;
const deletionKey=()=>`quality-review-last-deletion:${dataset.directory}`;
const canDeleteImage=()=>config?.capabilities?.image_deletion===true;
function datasetKinds(data){
  // A named queue also identifies empty images and survives deleting its last target.
  for(const folder of (data.directory||'').split(/[\\/]/).reverse()){
    if(folder==='螺栓'||/^(?:LABELME_|MSCOCO_|YOLO_)?screw$/i.test(folder))return ['bolt'];
    if(folder==='垫片'||/^(?:LABELME_|MSCOCO_|YOLO_)?shim$/i.test(folder))return ['washer'];
  }
  const kinds=new Set();
  for(const row of data.images||[]){
    if(['螺栓','混合'].includes(row.kind))kinds.add('bolt');
    if(['垫片','混合'].includes(row.kind))kinds.add('washer');
  }
  return [...kinds];
}
function annotationKinds(doc){
  const kinds=new Set();
  for(const s of doc.shapes||[]){
    if(['screw',...R.labels].includes(s.label))kinds.add('bolt');
    if(['wai','nei','shim',...R.referenceLabels].includes(s.label))kinds.add('washer');
  }
  return [...kinds];
}
function editingKinds(){
  const kinds=new Set([...datasetPartKinds,...imagePartKinds]);
  // Generic, entirely unlabelled directories still need both ways to start annotating.
  return kinds.size?kinds:new Set(['bolt','washer']);
}
function canAdd(label){return ['screw','wai','nei'].includes(label)&&editingKinds().has(label==='screw'?'bolt':'washer');}
function rememberDeletion(value){if(value)deletionHistory.push(value);else deletionHistory.pop();lastDeletion=deletionHistory.at(-1)||null;try{if(deletionHistory.length)localStorage.setItem(deletionKey(),JSON.stringify(deletionHistory));else localStorage.removeItem(deletionKey());}catch{}}
function restoreDeletionRecord(){lastDeletion=null;deletionHistory=[];try{const value=JSON.parse(localStorage.getItem(deletionKey())||'null');deletionHistory=(Array.isArray(value)?value:value?[value]:[]).filter(item=>item&&/^[a-f0-9]{32}$/.test(item.deletion_id)&&typeof item.row_id==='string');lastDeletion=deletionHistory.at(-1)||null;}catch{}}
const phase=()=>M?.phase||'parts';
function message(text,kind=''){
  $('message').textContent=text;$('message').className=kind;
  let w=$('welcomeMessage');if(!w){w=document.createElement('p');w.id='welcomeMessage';$('welcome').append(w);}w.textContent=text;w.className=kind;
}
async function request(url,body){
  const options=body?{method:'POST',headers:{'Content-Type':'application/json','X-Review-Token':config.token},body:JSON.stringify(body)}:{};
  const controller=url==='/api/pick'?new AbortController():null;let timer,timedOut=false;
  if(controller){options.signal=controller.signal;timer=setTimeout(()=>{timedOut=true;controller.abort();},130000);}
  try{
    const response=await fetch(url,options),data=await response.json();
    if(!response.ok){const e=new Error(data.error||'请求失败');e.conflict=response.status===409;throw e;}return data;
  }catch(e){if(timedOut)throw new Error('目录选择等待超时，已恢复操作。请重试，或点击“输入路径”直接填写数据集路径。');throw e;}
  finally{clearTimeout(timer);}
}
function query(row=file()){return '?'+new URLSearchParams({dataset:dataset.dataset,id:row.id});}
function storeDraft(){if(!loaded||!dirty)return;try{localStorage.setItem(draftKey(),JSON.stringify({revision:rev,document:R.output(M)}));}catch(e){message('浏览器草稿空间不足，请及时保存到原 JSON。','warn');}}
function remember(){undoStack.push({model:R.clone(M),selectedShape});if(undoStack.length>60)undoStack.shift();redoStack=[];}
function changed(id=null){
  if(id)R.invalidate(M,id);else M.completed=false;
  dirty=true;version++;queueVersion++;storeDraft();clearTimeout(saveTimer);saveTimer=setTimeout(save,650);render();
  if(id&&!conflict)message('标注已修改，请在整图核对后按 Enter 完成，无需逐个确认。');
}
function progressChanged(){dirty=true;version++;storeDraft();}
function save(){
  if(drag){clearTimeout(saveTimer);saveTimer=setTimeout(save,650);return Promise.resolve(false);}
  if(savePromise)return savePromise.then(ok=>ok&&dirty?save():ok);
  if(!loaded||!dirty)return Promise.resolve(true);
  if(conflict)return Promise.resolve(false);
  clearTimeout(saveTimer);const v=version,payload=R.output(M),row=file();$('saveState').textContent='正在保存…';
  savePromise=(async()=>{
    try{
      const response=await request('/api/save',{dataset:dataset.dataset,id:row.id,revision:rev,annotation:payload});
      rev=response.revision;M.source=R.clone(payload);
      if(v===version){dirty=false;try{localStorage.removeItem(draftKey());}catch{}}else storeDraft();
      row.checked=payload.quality_review.completed;row.reviewed=M.parts.filter(p=>R.requiresIndividualReview(p)&&payload.quality_review.confirmed.includes(p.id)).length;
      $('saveState').textContent=dirty?'有修改 · 待保存':'已保存到原 JSON';renderList();return true;
    }catch(e){conflict=!!e.conflict;storeDraft();$('saveState').textContent='保存失败 · 草稿保留';message(e.message,'error');return false;}
    finally{savePromise=null;}
  })();return savePromise;
}
function setBusy(value){busy=value;renderControls();}
async function openDataset(path=null,pick=false){
  if(busy||drag||!config)return;
  setBusy(true);
  try{
    if(!await save())return;
    message(pick?'目录选择窗口已打开，请在弹出的系统窗口中选择；找不到时可按 Alt + Tab 切换。':'正在读取目录…');
    const data=await request(pick?'/api/pick':'/api/open',path?{path}:{});
    if(data.cancelled){message('已取消选择目录。');return;}
    dataset=data;rows=data.images;datasetPartKinds=datasetKinds(data);imagePartKinds=[];restoreDeletionRecord();resetListCache();current=-1;loaded=false;M=null;dirty=false;conflict=false;undoStack=[];redoStack=[];
    $('welcome').hidden=true;$('workspace').hidden=false;$('search').value='';$('filter').value='all';
    $('directoryLabel').textContent=data.directory.split(/[\\/]/).filter(Boolean).pop();$('directoryLabel').title=data.directory;
    $('datasetInfo').textContent=`${rows.length} 张图片${data.skipped?' · 跳过 '+data.skipped+' 个不匹配文件':''}\n${data.directory}`;
    if(!rows.length){showEmptyDataset();message(lastDeletion?'本目录已没有可审核图片，可点击“撤销删图”恢复。':'这个目录里没有可审核的图片与 LabelMe JSON 配对，请选择其他目录。','warn');return;}
    let last=null;try{last=localStorage.getItem(lastKey());}catch{}
    const requested=requestedImageId;let index=rows.findIndex(r=>r.id===requested);requestedImageId=null;
    if(index<0)index=rows.findIndex(r=>r.id===last&&matchesRow(r));
    if(index<0)index=rows.findIndex(matchesRow);if(index>=0)await load(index);else showEmptyDataset();
    if(requested){const cleanURL=new URL(window.location.href);cleanURL.searchParams.delete('image');window.history.replaceState(window.history.state,'',cleanURL.href);}
  }catch(e){message(e.message,'error');}finally{setBusy(false);}
}
async function load(index){
  const row=rows[index],im=new Image();im.src='/api/image'+query(row);
  const [data]=await Promise.all([request('/api/annotation'+query(row)),im.decode()]);
  if(im.naturalWidth!==data.annotation.imageWidth||im.naturalHeight!==data.annotation.imageHeight)throw new Error('图片实际尺寸与 JSON 不一致，请先核对配对文件；尚未修改该文件。');
  current=index;M=R.load(data.annotation);imagePartKinds=annotationKinds(data.annotation);image=im;rev=data.revision;imageRevision=data.image_revision||'';loaded=true;dirty=false;version=0;conflict=false;undoStack=[];redoStack=[];drag=null;pointer=null;addMode=null;held=null;
  let restored=false;
  try{
    const raw=localStorage.getItem(draftKey());
    if(raw){const d=JSON.parse(raw);if(d.revision===rev){M=R.load(d.document);dirty=true;restored=true;}else{conflict=true;M=R.load(d.document);dirty=true;message('有尚未保存的草稿，但原 JSON 已变化。当前保留草稿供导出；已阻止覆盖原文件。','error');}}
    localStorage.setItem(lastKey(),row.id);
  }catch(e){message('浏览器草稿无法读取，请核对原文件后继续。','warn');}
  row.checked=M.completed;row.reviewed=M.parts.filter(p=>R.requiresIndividualReview(p)&&M.confirmed.includes(p.id)).length;
  if(!M.parts.length)M.phase='overview';
  chooseShape();fit();queueVersion++;render();
  if(!conflict)message(restored?'已恢复未保存草稿。':M.stale?'原文件标注已变化，已重新开始本图质量审核。':M.completed?'本图质量审核已完成，可继续检查或修改。':phase()==='overview'?'螺栓、垫片均按整图检查，无需逐个确认。核对框、角点、内外轮廓、漏标和多标后按 Enter 完成本图。':'局部视图用于调整标注，按空格返回整图检查，再按 Enter 完成。');
  if(restored){storeDraft();saveTimer=setTimeout(save,650);}
}
async function navigate(index){
  if(busy||drag||index<0||index>=rows.length||index===current)return;
  setBusy(true);try{if(await save())await load(index);}catch(e){message(e.message,'error');}finally{setBusy(false);}
}
function showEmptyDataset(){
  current=-1;loaded=false;M=null;imagePartKinds=[];image=null;rev='';imageRevision='';dirty=false;conflict=false;selectedShape=null;undoStack=[];redoStack=[];pointer=null;addMode=null;held=null;

  $('imageName').textContent=rows.length?'当前筛选没有匹配图片':'本目录已没有可审核图片';$('imagePosition').textContent=`0 / ${rows.length}`;$('stageTitle').textContent=rows.length?'等待加载图片':'本目录已清空';$('stageBadge').textContent=`${rows.length} 张图片`;$('stageType').textContent='DATASET REVIEW';
  $('partList').replaceChildren();$('contours').replaceChildren();$('queueCount').textContent='0 个';$('partProgress').textContent='按需放大';
  for(const id of ['confirmedCount','remainingCount'])$(id).textContent='0';$('deferredCount').textContent='未加载';
  $('boltControls').hidden=true;$('washerControls').hidden=true;$('issues').hidden=true;$('help').textContent='可点击“撤销删图”恢复刚刚移除的样本，或选择图片、打开另一个数据目录。';$('saveState').textContent=rows.length?'等待加载图片':'当前目录已清空';
  renderControls();renderList();draw();drawMini();
}
async function removeImage(){
  if(!loaded||busy||drag)return;
  if(!canDeleteImage()){message('当前后台不支持成对移除，请重新启动随本项目提供的零件服务。','warn');return;}
  if(conflict){message('当前文件存在冲突，请先保留或导出草稿，再重新打开目录。','warn');return;}
  const row=file(),index=current,key=draftKey();let deleted=false;setBusy(true);clearTimeout(saveTimer);
  try{
    if(!await save())return;
    const result=await request('/api/delete-image',{dataset:dataset.dataset,id:row.id,revision:rev,image_revision:imageRevision});
    deleted=true;
    rememberDeletion({deletion_id:result.deletion_id,row_id:row.id,name:row.name});
    try{localStorage.removeItem(key);localStorage.removeItem(lastKey());}catch{}
    rows.splice(index,1);resetListCache();showEmptyDataset();queueVersion++;
    $('datasetInfo').textContent=`${rows.length} 张图片\n${dataset.directory}`;renderList();
    let next=nextVisibleIndex(1,index-1);if(next<0)next=nextVisibleIndex(-1,index);
    if(next>=0)await load(next);else showEmptyDataset();
    message(`已删除 ${row.name} 及对应 JSON，并移入本目录的可恢复区。可点击“撤销删图”恢复。`);
  }catch(e){if(!deleted)conflict=conflict||!!e.conflict;message(deleted?`已删除 ${row.name} 及对应 JSON；下一张读取失败：${e.message}。可从左侧重新选图或撤销删图。`:e.message,'error');}
  finally{setBusy(false);render();}
}
async function restoreImage(){
  if(!dataset||busy||drag||!lastDeletion||!canDeleteImage())return;
  const deletion=lastDeletion;let restored=false;setBusy(true);clearTimeout(saveTimer);
  try{
    if(!await save())return;
    const result=await request('/api/restore-image',{dataset:dataset.dataset,deletion_id:deletion.deletion_id});
    restored=true;rememberDeletion(null);
    if(result.row&&!rows.some(row=>row.id===result.row.id))rows.push(result.row);
    resetListCache();showEmptyDataset();queueVersion++;
    $('datasetInfo').textContent=`${rows.length} 张图片\n${dataset.directory}`;
    const data=await request('/api/open',{path:dataset.directory});
    const preservedSearch=$('search').value,preservedFilter=$('filter').value;
    dataset=data;rows=data.images;
    $('search').value=preservedSearch;$('filter').value=preservedFilter;
    resetListCache();showEmptyDataset();queueVersion++;
    $('datasetInfo').textContent=`${rows.length} 张图片\n${dataset.directory}`;renderList();
    const index=rows.findIndex(row=>row.id===(result.id||deletion.row_id));
    const target=index>=0&&matchesRow(rows[index])?index:rows.findIndex(matchesRow);
    if(target>=0)await load(target);else showEmptyDataset();
    message(`已恢复 ${deletion.name} 及对应 JSON。`);
  }catch(e){message(restored?`已恢复 ${deletion.name} 及对应 JSON；重新读取失败：${e.message}。可从左侧重新选图或打开同一目录。`:e.message,'error');}
  finally{setBusy(false);render();}
}
function resetListCache(){listRows=null;listButtons=[];$('imageList').replaceChildren();}
function renderList(){
  const text=$('search').value.toLowerCase(),f=$('filter').value,list=$('imageList');let fragment=null,completed=0;
  if(listRows!==rows){
    listRows=rows;fragment=document.createDocumentFragment();
    const names=new Map();for(const r of rows)names.set(r.name,(names.get(r.name)||0)+1);
    listButtons=rows.map((r,i)=>{
      const b=document.createElement('button'),n=document.createElement('span'),s=document.createElement('small');
      b.title=r.id;n.textContent=r.name.replace('color_','').replace(/\.[^.]+$/,'')+(names.get(r.name)>1?' · '+r.kind:'');b.append(n,s);b.onclick=()=>navigate(i);fragment.append(b);
      return {button:b,status:s,checked:null,reviewed:null,active:null,visible:null};
    });
  }
  rows.forEach((r,i)=>{
    const entry=listButtons[i],checked=!!r.checked,reviewed=r.reviewed||0,active=i===current;
    const visible=matchesRow(r);
    if(checked)completed++;
    if(entry.checked!==checked||entry.reviewed!==reviewed){entry.status.textContent=checked?'已审核':'待审核';entry.status.className=checked?'done':'';entry.checked=checked;entry.reviewed=reviewed;}
    if(entry.active!==active){entry.button.className=active?'active':'';entry.active=active;}
    if(entry.visible!==visible){entry.button.hidden=!visible;entry.visible=visible;}
  });
  if(fragment)list.replaceChildren(fragment);
  const progress=`${rows.filter(r=>r.checked).length} / ${rows.length}`;if($('imageProgress').textContent!==progress)$('imageProgress').textContent=progress;
  if(loaded){const visible=rows.filter(matchesRow);$('imagePosition').textContent=`${visible.indexOf(file())+1} / ${visible.length}（全部 ${rows.length}）`;}
}
function chooseShape(){const ms=R.members(M,part());selectedShape=(ms.find(s=>s.shape.label==='wai')||ms.find(s=>s.shape.label==='screw')||ms[0])?.id||null;}
function selectPart(id,enterCrop=false){
  if(!loaded||busy||drag)return;M.current=id;addMode=null;pointer=null;if(enterCrop)M.phase='parts';chooseShape();fit();progressChanged();storeDraft();clearTimeout(saveTimer);saveTimer=setTimeout(save,650);render();
}
function setPhase(next){if(!loaded||busy||drag)return;M.phase=next;addMode=null;pointer=null;fit();progressChanged();storeDraft();clearTimeout(saveTimer);saveTimer=setTimeout(save,650);render();}
function stepPart(delta){const i=M?.parts.findIndex(p=>p.id===M.current),p=M?.parts[i+delta];if(p)selectPart(p.id,true);}
function fit(){
  if(!loaded)return;const rect=stage.getBoundingClientRect(),p=part();pointer=null;
  if(phase()==='overview'||!p){view={cx:image.width/2,cy:image.height/2,scale:Math.min(rect.width/image.width,rect.height/image.height)*.94};}
  else{const b=R.partBounds(M,p),c=R.center(M,p),padding=Math.max(12,Math.max(b[2]-b[0],b[3]-b[1])*.4);view={cx:c[0],cy:c[1],scale:Math.min(rect.width/(b[2]-b[0]+padding*2),rect.height/(b[3]-b[1]+padding*2))*.88};}
  draw();
}
function size(canvas){const r=canvas.getBoundingClientRect(),dpr=window.devicePixelRatio||1,w=Math.max(1,r.width),h=Math.max(1,r.height);if(canvas.width!==Math.round(w*dpr)||canvas.height!==Math.round(h*dpr)){canvas.width=Math.round(w*dpr);canvas.height=Math.round(h*dpr);}const ctx=canvas.getContext('2d');ctx.setTransform(dpr,0,0,dpr,0,0);return {ctx,w,h};}
function project(p,t=transform){return [(p[0]-t.cx)*t.scale+t.w/2,(p[1]-t.cy)*t.scale+t.h/2];}
function unproject(p){return [(p[0]-transform.w/2)/view.scale+view.cx,(p[1]-transform.h/2)/view.scale+view.cy];}
function pixel(e){const r=stage.getBoundingClientRect();return [e.clientX-r.left,e.clientY-r.top];}
function validPoint(p){return p&&p.every(Number.isFinite)&&p[0]>=0&&p[1]>=0&&p[0]<=image.width&&p[1]<=image.height;}
function clampPoint(p){return [Math.max(0,Math.min(image.width,p[0])),Math.max(0,Math.min(image.height,p[1]))];}
function ellipsePoint(e,x,y){const c=Math.cos(e.angle),s=Math.sin(e.angle);return [e.cx+x*c-y*s,e.cy+x*s+y*c];}
function localPoint(e,p){const x=p[0]-e.cx,y=p[1]-e.cy,c=Math.cos(e.angle),s=Math.sin(e.angle);return [x*c+y*s,-x*s+y*c];}
function handles(s){
  if(s?.e){const e=s.e,h=directions.map(([dx,dy])=>({type:'resize',dx,dy,p:project(ellipsePoint(e,e.rx*dx,e.ry*dy))}));const p=project(ellipsePoint(e,0,-e.ry));h.push({type:'rotate',p:[p[0]+Math.sin(e.angle)*28,p[1]-Math.cos(e.angle)*28]});return h;}
  if(['screw','shim'].includes(s?.shape.label)){const b=R.bounds(s.shape.points);return [[b[0],b[1]],[b[2],b[1]],[b[2],b[3]],[b[0],b[3]]].map((p,i)=>({type:'boxResize',corner:i,p:project(p)}));}return [];
}
function diamond(ctx,p,t,small=false){const [x,y]=project(R.center(M,p),t),rx=small?3.5:6,ry=small?6:10,corners=[[x,y-ry],[x+rx,y],[x,y+ry],[x-rx,y]],facets=['#94f86a','#40c331','#1d8d2d','#60df46'];ctx.save();ctx.setLineDash([]);facets.forEach((color,i)=>{ctx.beginPath();ctx.moveTo(x,y);ctx.lineTo(...corners[i]);ctx.lineTo(...corners[(i+1)%4]);ctx.closePath();ctx.fillStyle=color;ctx.fill();});ctx.beginPath();corners.forEach((p,i)=>i?ctx.lineTo(...p):ctx.moveTo(...p));ctx.closePath();ctx.strokeStyle='#efffe8';ctx.lineWidth=1.5;ctx.stroke();ctx.restore();}
function drawPart(ctx,p,t,small=false){
  const isCurrent=p.id===M.current;
  if(p.type==='bolt'){
    const outline=R.boltOutline(M,p),ms=R.members(M,p);
    const points=R.labels.map(label=>ms.find(s=>s.shape.label===label)?.shape.points[0]||null);
    const bad=points.every(Boolean)&&!!R.pointError(points);
    ctx.save();ctx.setLineDash([]);ctx.beginPath();
    for(let i=0;i<4;i++){const a=outline[i],b=outline[(i+1)%4];if(a&&b){ctx.moveTo(...project(a,t));ctx.lineTo(...project(b,t));}}
    ctx.strokeStyle=bad?'#ffffffbb':'#20373aa6';ctx.lineWidth=small?1.6:3;ctx.stroke();
    ctx.strokeStyle=bad?'#e34c3e':'#f2fff5';ctx.lineWidth=small?.8:1.5;ctx.stroke();ctx.restore();
  }
  for(const s of R.members(M,p)){
    const label=s.shape.label,points=s.shape.points;
    if(s.e){ctx.beginPath();if(s.edited){const e=s.e,xy=project([e.cx,e.cy],t);ctx.ellipse(...xy,e.rx*t.scale,e.ry*t.scale,e.angle,0,Math.PI*2);}else{const path=s.shape.shape_type==='circle'?R.polygon(s.e):points;path.forEach((pt,i)=>{const xy=project(pt,t);i?ctx.lineTo(...xy):ctx.moveTo(...xy);});ctx.closePath();}ctx.strokeStyle=colors[label];ctx.lineWidth=small?.8:isCurrent?1.5:1;ctx.stroke();if(!small&&phase()==='overview'){ctx.fillStyle=colors[label];for(const point of points){const xy=project(point,t);ctx.beginPath();ctx.arc(...xy,1.5,0,Math.PI*2);ctx.fill();}}}
    else if(['screw','shim'].includes(label)){const b=R.bounds(points),a=project(b.slice(0,2),t),z=project(b.slice(2),t);if(isCurrent&&!small){ctx.strokeStyle='#ffffff';ctx.lineWidth=4;ctx.strokeRect(...a,z[0]-a[0],z[1]-a[1]);}ctx.strokeStyle=M.confirmed.includes(p.id)?'#24846b':label==='shim'?'#b57d23':'#2e91b3';ctx.lineWidth=small?1:1.6;ctx.strokeRect(...a,z[0]-a[0],z[1]-a[1]);}
    else if(R.referenceLabels.includes(label)&&points[0]){const a=project(points[0],t),color=label.startsWith('wai_')?colors.wai:colors.nei;ctx.beginPath();ctx.arc(...a,small?1.8:4,0,Math.PI*2);ctx.fillStyle=color;ctx.fill();ctx.strokeStyle='#fff';ctx.lineWidth=1;ctx.stroke();if(!small){ctx.font='11px ui-monospace,monospace';ctx.fillStyle=color;ctx.fillText(label,a[0]+7,a[1]-6);}}
    else if(R.labels.includes(label)&&points[0]){const i=R.labels.indexOf(label),a=project(points[0],t);ctx.beginPath();ctx.arc(...a,small?1.8:3.5,0,Math.PI*2);ctx.fillStyle=pointColors[i];ctx.fill();ctx.strokeStyle='#fff';ctx.lineWidth=small?.6:1;ctx.stroke();if(!small){ctx.font='11px ui-monospace,monospace';ctx.fillStyle=pointColors[i];ctx.fillText(['1','2','4','3'][i]+(phase()==='parts'?' · '+['左上','右上','左下','右下'][i]:''),a[0]+7,a[1]-6);}}
  }
  if(!small&&phase()==='overview'){const b=R.partBounds(M,p),a=project(b.slice(0,2),t);if(p.type==='washer'){const z=project(b.slice(2),t);ctx.strokeStyle='#698d7980';ctx.lineWidth=1;ctx.setLineDash([3,3]);ctx.strokeRect(...a,z[0]-a[0],z[1]-a[1]);ctx.setLineDash([]);}ctx.font='10px ui-monospace,monospace';ctx.fillStyle='#20373ad9';const text=String(M.parts.indexOf(p)+1);ctx.fillRect(a[0],a[1]-15,ctx.measureText(text).width+8,14);ctx.fillStyle='#fff';ctx.fillText(text,a[0]+4,a[1]-4);}
}
function drawUnassignedBoltPoints(ctx,t,small=false){
  for(const id of M.unassignedBoltPoints||[]){
    const s=M.shapes.find(s=>s.id===id),point=s?.shape.points?.[0],i=R.labels.indexOf(s?.shape.label);
    if(i<0||!point?.every(Number.isFinite))continue;
    const a=project(point,t);ctx.beginPath();ctx.arc(...a,small?1.8:4,0,Math.PI*2);ctx.fillStyle=pointColors[i];ctx.fill();ctx.strokeStyle='#fff';ctx.lineWidth=1;ctx.stroke();
    if(!small){ctx.font='11px ui-monospace,monospace';ctx.fillStyle=pointColors[i];ctx.fillText(['1','2','4','3'][i]+' · 未分组',a[0]+7,a[1]-6);}
  }
}
function draw(){
  const {ctx,w,h}=size(stage);ctx.fillStyle='#e6ebe6';ctx.fillRect(0,0,w,h);if(!loaded)return;transform={...view,w,h};
  ctx.save();ctx.beginPath();ctx.rect(0,0,w,h);ctx.clip();const top=project([0,0]);ctx.imageSmoothingEnabled=true;ctx.imageSmoothingQuality='high';ctx.drawImage(image,...top,image.width*view.scale,image.height*view.scale);
  if(!hidden&&$('overlays').checked){
    const shown=phase()==='overview'?M.parts:part()?[part()]:[];shown.forEach(p=>drawPart(ctx,p,transform));if(phase()==='overview')drawUnassignedBoltPoints(ctx,transform);
    const s=shape();if(s&&part()?.members.includes(s.id)&&!addMode){
      ctx.strokeStyle=s.e?colors[s.shape.label]:'#2e91b3';ctx.fillStyle='#fff';ctx.lineWidth=1;
      for(const h of handles(s)){if(h.type==='rotate'){const a=project(ellipsePoint(s.e,0,-s.e.ry));ctx.beginPath();ctx.moveTo(...a);ctx.lineTo(...h.p);ctx.stroke();ctx.beginPath();ctx.arc(...h.p,5,0,Math.PI*2);ctx.fill();ctx.stroke();}else{ctx.fillRect(h.p[0]-4,h.p[1]-4,8,8);ctx.strokeRect(h.p[0]-4,h.p[1]-4,8,8);}}
    }
    if(part())diamond(ctx,part(),transform);
  }
  if(drag?.type==='create'){const a=project(drag.start),b=project(drag.end);ctx.strokeStyle=drag.label==='screw'?'#187568':colors[drag.label];ctx.lineWidth=1.5;ctx.setLineDash([4,3]);if(drag.label==='screw')ctx.strokeRect(a[0],a[1],b[0]-a[0],b[1]-a[1]);else{ctx.beginPath();ctx.ellipse((a[0]+b[0])/2,(a[1]+b[1])/2,Math.abs(a[0]-b[0])/2,Math.abs(a[1]-b[1])/2,0,0,Math.PI*2);ctx.stroke();}ctx.setLineDash([]);}
  ctx.restore();drawMini();
}
function drawMini(){const {ctx,w,h}=size(mini);ctx.fillStyle='#e6ebe6';ctx.fillRect(0,0,w,h);if(!loaded)return;const scale=Math.min(w/image.width,h/image.height)*.96,t={cx:image.width/2,cy:image.height/2,scale,w,h},top=project([0,0],t);ctx.drawImage(image,...top,image.width*scale,image.height*scale);if(!hidden&&$('overlays').checked){M.parts.forEach(p=>drawPart(ctx,p,t,true));drawUnassignedBoltPoints(ctx,t,true);if(part())diamond(ctx,part(),t,true);}}
let renderedQueue=-1;
function renderQueue(){
  if(renderedQueue!==queueVersion){
    renderedQueue=queueVersion;$('partList').replaceChildren();M.parts.forEach((p,i)=>{
      const b=document.createElement('button');b.dataset.id=p.id;b.title=`${p.type==='washer'?'垫片':'螺栓'} ${i+1} · 点击放大`;
      const c=document.createElement('canvas');c.width=120;c.height=120;const ctx=c.getContext('2d'),bounds=R.partBounds(M,p),center=R.center(M,p),span=Math.max(24,bounds[2]-bounds[0],bounds[3]-bounds[1])*1.6;
      ctx.fillStyle='#e9ece7';ctx.fillRect(0,0,120,120);ctx.drawImage(image,60-center[0]/span*120,60-center[1]/span*120,image.width/span*120,image.height/span*120);
      const label=document.createElement('span');label.textContent=`${String(i+1).padStart(2,'0')} · 局部调整`;b.append(c,label);b.onclick=()=>selectPart(p.id,true);$('partList').append(b);
    });
  }for(const b of $('partList').children)b.classList.toggle('active',b.dataset.id===M.current);
}
function renderControls(){
  const disabled=!loaded||busy,overview=phase()==='overview',p=part(),kinds=editingKinds();
  for(const id of ['save','partsMode','overviewMode','fit','prevPart','nextPart','confirm','defer','complete','addOuter','addInner','addBolt','removeContour','removePart','circle','export'])$(id).disabled=disabled;
  $('chooseDirectory').disabled=busy||!config;$('start').disabled=busy||!config;$('pathDirectory').disabled=busy||!config;
  $('removeImage').disabled=disabled||conflict||!canDeleteImage();$('restoreImage').disabled=busy||!dataset||!lastDeletion||!canDeleteImage();
  $('search').disabled=busy||!!drag;$('filter').disabled=busy||!!drag;
  $('prevImage').disabled=disabled||nextVisibleIndex(-1)<0;$('nextImage').disabled=disabled||nextVisibleIndex(1)<0;
  $('confirm').hidden=overview;$('complete').hidden=!overview;$('prevPart').hidden=overview;$('nextPart').hidden=overview;$('defer').hidden=true;
  $('confirm').disabled=disabled;$('defer').disabled=true;$('complete').disabled=disabled||!overview;
  $('removePart').disabled=disabled||!p;$('removeContour').disabled=disabled||!shape();$('circle').disabled=disabled||!shape()?.e;
  $('undo').disabled=disabled||!undoStack.length;$('redo').disabled=disabled||!redoStack.length;
  $('partsMode').disabled=disabled||!M?.parts.length;
  for(const [id,label] of [['addOuter','wai'],['addInner','nei'],['addBolt','screw']]){
    $(id).hidden=!loaded||!canAdd(label);$(id).disabled=disabled||!canAdd(label);
  }
  $('boltControls').hidden=!loaded||p?.type!=='bolt'||overview;
  $('washerControls').hidden=!loaded||p?.type!=='washer';
  const category=kinds.size===2?'螺栓与垫片':kinds.has('bolt')?'螺栓':'垫片';
  $('reviewSubtitle').textContent=category+' · 整图审核';document.title=category+'标注 · 质量审核';
  $('geometryNote').textContent=kinds.size===1&&kinds.has('bolt')?'螺栓框与四个角点独立调整；放大螺栓后可用 Q/W/S/A 或 1/2/3/4 改角点。':'垫片内外轮廓仍需贴合实物边界。';
  $('removeContour').textContent=p?.type==='bolt'?'删除选中框 / 点':p?.type==='washer'?'删除选中轮廓 / 参考点':'删除选中轮廓 / 点';
  if(addMode&&!canAdd(addMode))addMode=null;if(held&&!canAdd(held))held=null;
  for(const id of ['cx','cy','rx','ry','angle'])$(id).disabled=disabled||!shape()?.e;
}
function render(){
  renderControls();if(!loaded)return;const p=part(),overview=phase()==='overview',ms=R.members(M,p),total=M.parts.length;
  file().checked=M.completed;file().reviewed=0;
  $('imageName').textContent=file().name;$('imagePosition').textContent=`${current+1} / ${rows.length}`;
  $('partsMode').classList.toggle('active',!overview);$('overviewMode').classList.toggle('active',overview);$('partProgress').textContent='按需放大';
  const boltCount=M.parts.filter(p=>p.type==='bolt').length,washerCount=total-boltCount;
  $('partsMode').title='螺栓、垫片均可按需放大调整，无需逐个确认';
  $('stageType').textContent=overview?'WHOLE IMAGE REVIEW':p?.type==='washer'?'WASHER REVIEW':'BOLT REVIEW';
  $('stageTitle').textContent=overview?'整图检查 · 核对标注、漏标与多标':p?`${p.type==='washer'?'垫片':'螺栓'} ${String(M.parts.filter(o=>o.type===p.type).indexOf(p)+1).padStart(2,'0')} / ${M.parts.filter(o=>o.type===p.type).length} · 局部调整，无需单独确认`:'暂无标注';
  $('stageBadge').textContent=addMode?`拖动补画${addMode==='screw'?'螺栓框':addMode==='wai'?'外圈':'内孔'} · Esc 退出`:overview?`${image.width} × ${image.height} · ${boltCount} 螺栓框 · ${washerCount} 垫片${M.unassignedBoltPoints?.length?' · '+M.unassignedBoltPoints.length+' 未分组角点':''}`:'◆ 当前标注 · 滚轮可继续放大';
  $('confirmedCount').textContent=boltCount;$('remainingCount').textContent=washerCount;$('deferredCount').textContent=M.completed?'已完成':'待审核';$('queueCount').textContent=`${boltCount} 螺栓框 · ${washerCount} 垫片`;
  $('complete').textContent=M.completed?'已完成 · 下一张 Enter':current===rows.length-1?'整图无漏标 / 多标 · 完成最后一张 Enter':'整图无漏标 / 多标 · 完成并下一张 Enter';
  for(const el of $('boltControls').children)el.classList.toggle('set',ms.some(s=>s.shape.label===R.labels[Number(el.dataset.point)]));
  $('contours').replaceChildren();if(p){for(const s of ms.filter(s=>s.e)){const b=document.createElement('button');b.textContent=s.shape.label==='wai'?'外圈 · 橙色':'内孔 · 蓝色';b.className=s.id===selectedShape?'active':'';b.onclick=()=>{if(busy||drag)return;selectedShape=s.id;render();};$('contours').append(b);}}
  const e=shape()?.e;for(const id of ['cx','cy','rx','ry','angle'])if(document.activeElement!==$(id))$(id).value=e?(id==='angle'?e.angle*180/Math.PI:e[id]).toFixed(2):'';
  for(const [id,label] of [['addOuter','wai'],['addInner','nei'],['addBolt','screw']])$(id).classList.toggle('active',addMode===label);
  $('help').textContent=overview?'直接核对整图中的螺栓框、角点及垫片内外轮廓，无需逐个确认；标注可直接拖动，双击或点击缩略图可放大调整。F 重新居中整图；结构与配对提示供人工核对，整图无误后按 Enter。绿色菱形仅用于定位；垫片内外轮廓仍应贴合实物边界。':p?.type==='washer'?'拖内部移动；8 个方块从对应方向拉伸；顶部圆点旋转。按住 1 / 2 拖动补圈；滚轮缩放，右键拖动 / Shift + 左键拖动 / 空格 + 拖动平移；F 居中当前零件，点按空格返回整图，再按 Enter 完成。':'鼠标指向位置，按 Q / W / S / A 或 1 / 2 / 3 / 4 改点。连线：左上 1 → 右上 2 → 右下 3 → 左下 4 → 左上。F 居中当前零件；调整后切回整图检查，按 Enter 完成本图。框与四点独立编辑。';
  if(overview&&editingKinds().size===1)$('help').textContent=editingKinds().has('bolt')?'核对整图中的螺栓框、四个角点、漏标与多标，无需逐个确认。点击“＋ 螺栓框”或按 B 补框；双击螺栓或点击缩略图放大后，用 Q/W/S/A 或 1/2/3/4 调整角点。F 居中，整图无误后按 Enter。框与角点独立调整，绿色菱形仅用于定位。':'核对整图中的垫片内外轮廓、漏标与多标，无需逐个确认。按住 1 / 2 拖动补画外圈 / 内孔；双击垫片或点击缩略图可放大调整。F 居中，整图无误后按 Enter。轮廓应贴合实物边界，绿色菱形仅用于定位。';
  const problems=[];
  problems.push(...R.overviewIssues(M));
  const conversion=M.source.washer_three_point_conversion;
  if(conversion&&!M.completed){
    problems.push(conversion.method==='three_point_circumcircle_draft'?'本图内外圈由三点生成圆形草稿，请检查贴合；有透视时可拉伸、旋转为椭圆。':'本图三点标签已换为同图已有内外轮廓，请核对后完成整图审核。');
    if(conversion.draft_geometry_warnings?.length)problems.push('部分三点圆草稿存在内孔超出外圈或轮廓越界，请重点检查并调整。');
    if(conversion.source_variant_warning)problems.push('同图不同来源的采样点有明显差异，请结合实物边界检查。');
  }
  const supported=new Set([...M.parts.flatMap(p=>p.members),...(M.unassignedBoltPoints||[])]),other=M.shapes.filter(s=>!supported.has(s.id)).length;
  if(p?.type==='washer'&&ms.some(s=>s.shape.label==='shim')&&!ms.some(s=>s.e))$('help').textContent='六点垫片：橙色 wai_1/2/3 为外圈采样点，蓝色 nei_1/2/3 为内孔采样点。鼠标拖动原点或 shim 框修正；原框与六点保留，不自动生成内外轮廓。F 居中当前零件，点按空格返回整图，再按 Enter 完成。';
  if(other)problems.push(`${other} 个其他或无法配对的标注保持原样，请用原工具核对。`);
  $('issues').hidden=!problems.length;$('issues').textContent=problems.join(' ');
  if(dirty&&!savePromise)$('saveState').textContent=conflict?'文件冲突 · 草稿保留':'有修改 · 待保存';else if(!dirty)$('saveState').textContent='已读取 / 已保存原 JSON';
  renderList();renderQueue();draw();
}
async function confirmPart(){
  if(!loaded||busy||drag||phase()!=='parts')return;
  setPhase('overview');message('螺栓、垫片均无需逐个确认。请在整图核对后按 Enter 完成。');
}
async function complete(){
  if(!loaded||busy||drag||phase()!=='overview')return;
  const error=R.completionError(M);if(error){message(error,'warn');return;}
  setBusy(true);remember();const previousCompleted=M.completed,previousDeferred=[...M.deferred];M.completed=true;M.deferred=M.deferred.filter(id=>M.parts.some(p=>p.id===id&&R.requiresIndividualReview(p)));progressChanged();storeDraft();
  try{if(await save()){const next=nextVisibleIndex(1);if(next>=0)await load(next);else{message('当前筛选的最后一张已完成质量审核，修改与进度均已保存到原 JSON。');if(!matchesRow(file()))showEmptyDataset();}}else{M.completed=previousCompleted;M.deferred=previousDeferred;storeDraft();}}catch(e){message(e.message,'error');}finally{setBusy(false);render();}
}
function syncEllipse(s){s.shape.points=R.polygon(s.e);s.shape.shape_type='polygon';s.shape.flags={...s.shape.flags,human_ellipse_edited:true};s.edited=true;}
function owner(id){return M.parts.find(p=>p.members.includes(id));}
function placePoint(index){
  if(busy||drag||phase()!=='parts'||part()?.type!=='bolt')return;
  if(!validPoint(pointer)){message('先把鼠标移动到图片内的关键点位置，再按键。','warn');return;}
  remember();const p=part(),ms=R.members(M,p),label=R.labels[index],xy=pointer.map(v=>Math.round(v*1000)/1000);let s=ms.find(s=>s.shape.label===label);
  if(s)s.shape.points=[xy];else{s=R.newShape(M,label,[xy],'point',ms.find(s=>s.shape.label==='screw')?.shape.group_id??R.nextGroup(M));p.members.push(s.id);}
  s.shape.flags={...s.shape.flags,confirmed:false,model_suggested:false};selectedShape=s.id;changed(p.id);
}
function removePart(){if(busy||drag)return;if(shape()&&!owner(shape().id)){removeContour();return;}if(!part())return;remember();const p=part(),i=M.parts.indexOf(p);M.shapes=M.shapes.filter(s=>!p.members.includes(s.id));M.parts=M.parts.filter(o=>o!==p);R.invalidate(M,p.id);M.current=M.parts[Math.min(i,M.parts.length-1)]?.id||null;chooseShape();if(!M.parts.length)M.phase='overview';changed();fit();message('已删除整个零件；可撤销。请在整图核对删除后的数量。');}
function removeContour(){if(!shape()||busy||drag)return;remember();const s=shape(),p=owner(s.id);M.shapes=M.shapes.filter(o=>o!==s);M.unassignedBoltPoints=(M.unassignedBoltPoints||[]).filter(id=>id!==s.id);if(p){p.members=p.members.filter(id=>id!==s.id);R.invalidate(M,p.id);if(!p.members.length){M.parts=M.parts.filter(o=>o!==p);M.current=M.parts[0]?.id||null;}}chooseShape();changed();render();message('已删除选中轮廓 / 点；其余标注保留，可撤销。');}
function undo(redo=false){if(!loaded||busy||drag)return;const from=redo?redoStack:undoStack,to=redo?undoStack:redoStack;if(!from.length)return;to.push({model:R.clone(M),selectedShape});const snap=from.pop();M=snap.model;selectedShape=snap.selectedShape;dirty=true;version++;queueVersion++;storeDraft();clearTimeout(saveTimer);saveTimer=setTimeout(save,650);fit();render();message(redo?'已重做。':'已撤销，可用重做恢复。');}
function startAdd(label){if(!loaded||busy||drag||!canAdd(label))return;addMode=addMode===label?null:label;if(phase()!=='overview'&&label==='screw')M.phase='overview';fit();render();message(addMode?'在图片上拖动补画，Esc 退出。':'已退出补画模式。');}
function addShape(label,start,end){
  if(!loaded||busy||!canAdd(label))return;
  const b=R.bounds([start,end]);if(b[2]-b[0]<1||b[3]-b[1]<1){message('范围太小，请重新补画。','warn');return;}
  remember();let p=null,group=null,s;
  if(label==='screw'){group=R.nextGroup(M);s=R.newShape(M,label,[b.slice(0,2),b.slice(2)],'rectangle',group);}
  else{
    const e={cx:(b[0]+b[2])/2,cy:(b[1]+b[3])/2,rx:(b[2]-b[0])/2,ry:(b[3]-b[1])/2,angle:0};
    const candidates=M.parts.filter(p=>p.type==='washer'&&!R.members(M,p).some(s=>s.shape.label===label)).map(p=>({p,c:R.center(M,p),b:R.partBounds(M,p)})).filter(o=>Math.hypot(o.c[0]-e.cx,o.c[1]-e.cy)<Math.max(o.b[2]-o.b[0],o.b[3]-o.b[1],e.rx*2,e.ry*2)*.35).sort((a,b)=>Math.hypot(a.c[0]-e.cx,a.c[1]-e.cy)-Math.hypot(b.c[0]-e.cx,b.c[1]-e.cy));
    p=candidates[0]?.p;group=p?R.members(M,p)[0].shape.group_id:null;
    if(group==null){group=R.nextGroup(M);if(p)R.members(M,p).forEach(s=>s.shape.group_id=group);}
    s=R.newShape(M,label,R.polygon(e),'polygon',group);s.e=e;s.edited=true;
  }
  if(p)p.members.push(s.id);else{p={id:'quality-part-'+(++M.serial),type:label==='screw'?'bolt':'washer',members:[s.id]};M.parts.push(p);}
  R.orderParts(M);M.current=p.id;selectedShape=s.id;changed(p.id);message(label==='screw'?'已补螺栓框。请核对四个角点后，在整图按 Enter 完成。':'已补垫片轮廓。请核对内外圈贴合、漏标和多标，再在整图按 Enter 完成。');
}
function hitShape(px){
  const xy=unproject(px),candidates=(phase()==='overview'?M.shapes:R.members(M,part())).filter(s=>{
    if(s.e){const q=localPoint(s.e,xy);return Math.hypot(q[0]/s.e.rx,q[1]/s.e.ry)<=1+8/(view.scale*Math.min(s.e.rx,s.e.ry));}
    if([...R.labels,...R.referenceLabels].includes(s.shape.label)&&s.shape.points[0]){const p=project(s.shape.points[0]);return Math.hypot(p[0]-px[0],p[1]-px[1])<11;}
    if(['screw','shim'].includes(s.shape.label)){const b=R.bounds(s.shape.points);return xy[0]>=b[0]&&xy[0]<=b[2]&&xy[1]>=b[1]&&xy[1]<=b[3];}return false;
  });
  const edge=s=>s.e?Math.abs(Math.hypot(...localPoint(s.e,xy).map((v,i)=>v/(i?s.e.ry:s.e.rx)))-1)*Math.min(s.e.rx,s.e.ry)*view.scale:0;
  const points=candidates.filter(s=>[...R.labels,...R.referenceLabels].includes(s.shape.label)).sort((a,b)=>{const aa=project(a.shape.points[0]),bb=project(b.shape.points[0]);return Math.hypot(aa[0]-px[0],aa[1]-px[1])-Math.hypot(bb[0]-px[0],bb[1]-px[1]);});if(points.length)return points[0];
  const edges=candidates.filter(s=>s.e&&edge(s)<8).sort((a,b)=>edge(a)-edge(b));
  if(edges.length)return edges[0];
  const selected=candidates.find(s=>s.id===selectedShape);if(selected)return selected;
  candidates.sort((a,b)=>{const aa=a.e?a.e.rx*a.e.ry:1e8,bb=b.e?b.e.rx*b.e.ry:1e8;return aa-bb;});return candidates[0]||null;
}
stage.addEventListener('contextmenu',e=>e.preventDefault());
stage.addEventListener('pointerdown',e=>{
  if(!loaded||busy||drag||!transform)return;e.preventDefault();stage.focus({preventScroll:true});const px=pixel(e),xy=unproject(px);pointer=xy;
  if(e.button===2||e.button===1||(e.button===0&&(e.shiftKey||shiftHeld))||e.altKey||spaceHeld){spacePanned=spacePanned||spaceHeld;drag={type:'pan',start:px,view:{...view},pointerId:e.pointerId};}
  else if(e.button===0){
    const label=held||addMode;if(label&&canAdd(label)&&validPoint(xy)){drag={type:'create',label,start:xy,end:xy,pointerId:e.pointerId};}
    else if(!hidden&&$('overlays').checked){
      const hit=hitShape(px);let s=shape(),handle=s&&handles(s).filter(h=>Math.hypot(h.p[0]-px[0],h.p[1]-px[1])<10).sort((a,b)=>Math.hypot(a.p[0]-px[0],a.p[1]-px[1])-Math.hypot(b.p[0]-px[0],b.p[1]-px[1]))[0];
      if(hit&&[...R.labels,...R.referenceLabels].includes(hit.shape.label)){
        const point=project(hit.shape.points[0]),pointDistance=Math.hypot(point[0]-px[0],point[1]-px[1]);
        if(!handle||pointDistance<=Math.hypot(handle.p[0]-px[0],handle.p[1]-px[1])){s=hit;handle=null;}
      }
      if(!handle){s=hitShape(px);if(!s){selectedShape=null;render();return;}selectedShape=s.id;const p=owner(s.id);if(p)M.current=p.id;handle=handles(s).find(h=>Math.hypot(h.p[0]-px[0],h.p[1]-px[1])<10);}
      // Whole-image click selects; subsequent drag edits the selected shape.
      const before={model:R.clone(M),selectedShape};
      const type=handle?.type||(s.e?'move':[...R.labels,...R.referenceLabels].includes(s.shape.label)?'point':'boxMove');
      drag={type,shapeId:s.id,start:xy,original:R.clone(s),handle,before,pointerId:e.pointerId,changed:false};
      if(type==='rotate')drag.startAngle=Math.atan2(xy[1]-s.e.cy,xy[0]-s.e.cx);
    }
  }
  if(drag)stage.setPointerCapture(e.pointerId);render();
});
stage.addEventListener('pointermove',e=>{
  if(!loaded||!transform)return;const px=pixel(e),xy=unproject(px);pointer=xy;
  if(!drag){stage.style.cursor=spaceHeld||shiftHeld||e.shiftKey?'grab':held||addMode?'crosshair':hitShape(px)?'move':'crosshair';return;}
  if(drag.pointerId!==e.pointerId)return;
  if(drag.type==='pan'){view.cx=drag.view.cx-(px[0]-drag.start[0])/view.scale;view.cy=drag.view.cy-(px[1]-drag.start[1])/view.scale;stage.style.cursor='grabbing';draw();return;}
  if(drag.type==='create'){drag.end=clampPoint(xy);draw();return;}
  const s=M.shapes.find(s=>s.id===drag.shapeId),o=drag.original,dx=xy[0]-drag.start[0],dy=xy[1]-drag.start[1];
  if(Math.hypot(dx,dy)*view.scale<.5&&!drag.changed)return;
  if(s.e){
    if(drag.type==='move'){s.e.cx=o.e.cx+dx;s.e.cy=o.e.cy+dy;}
    if(drag.type==='rotate')s.e.angle=o.e.angle+Math.atan2(xy[1]-o.e.cy,xy[0]-o.e.cx)-drag.startAngle;
    if(drag.type==='resize'){const [x,y]=localPoint(o.e,xy),{dx,dy}=drag.handle;let sx=0,sy=0;if(dx){const anchor=-dx*o.e.rx,tip=dx>0?Math.max(anchor+1,x):Math.min(anchor-1,x);s.e.rx=Math.abs(tip-anchor)/2;sx=(tip+anchor)/2;}if(dy){const anchor=-dy*o.e.ry,tip=dy>0?Math.max(anchor+1,y):Math.min(anchor-1,y);s.e.ry=Math.abs(tip-anchor)/2;sy=(tip+anchor)/2;}[s.e.cx,s.e.cy]=ellipsePoint(o.e,sx,sy);}
    syncEllipse(s);
  }else if(drag.type==='point'){s.shape.points=[clampPoint(xy).map(v=>Math.round(v*1000)/1000)];s.shape.flags={...s.shape.flags,model_suggested:false,confirmed:false};}
  else{
    let b=R.bounds(o.shape.points);if(drag.type==='boxMove'){const tx=Math.max(-b[0],Math.min(image.width-b[2],dx)),ty=Math.max(-b[1],Math.min(image.height-b[3],dy));b=[b[0]+tx,b[1]+ty,b[2]+tx,b[3]+ty];}
    else if(drag.type==='boxResize'){const i=drag.handle.corner,p=clampPoint(xy);if(i===0||i===3)b[0]=Math.min(b[2]-1,p[0]);else b[2]=Math.max(b[0]+1,p[0]);if(i===0||i===1)b[1]=Math.min(b[3]-1,p[1]);else b[3]=Math.max(b[1]+1,p[1]);}
    s.shape.points=[b.slice(0,2),b.slice(2)];s.shape.shape_type='rectangle';
  }drag.changed=true;draw();
});
function finishDrag(e,cancel=false){
  if(!drag||drag.pointerId!==e.pointerId)return;const d=drag;drag=null;if(stage.hasPointerCapture(e.pointerId))stage.releasePointerCapture(e.pointerId);stage.style.cursor=shiftHeld||spaceHeld?'grab':'crosshair';
  if(cancel&&d.before){M=d.before.model;selectedShape=d.before.selectedShape;render();return;}
  if(d.type==='create'&&!cancel)addShape(d.label,d.start,d.end);
  else if(d.changed){undoStack.push(d.before);redoStack=[];const p=owner(d.shapeId);changed(p?.id);}
  render();
}
stage.addEventListener('pointerup',e=>finishDrag(e));stage.addEventListener('pointercancel',e=>finishDrag(e,true));
stage.addEventListener('pointerleave',()=>{if(!drag)pointer=null;});
stage.addEventListener('dblclick',e=>{if(!loaded||!transform||busy||drag||held||addMode||spaceHeld)return;const s=hitShape(pixel(e)),p=s&&owner(s.id);if(p)selectPart(p.id,true);});
stage.addEventListener('wheel',e=>{if(!loaded||busy||drag)return;e.preventDefault();const px=pixel(e),before=unproject(px);view.scale=Math.max(.1,Math.min(120,view.scale*Math.exp(-e.deltaY*.0015)));view.cx=before[0]-(px[0]-transform.w/2)/view.scale;view.cy=before[1]-(px[1]-transform.h/2)/view.scale;pointer=before;draw();},{passive:false});
for(const id of ['cx','cy','rx','ry','angle'])$(id).onchange=()=>{const s=shape(),value=Number($(id).value);if(!s?.e||busy||drag||$(id).value===''||!Number.isFinite(value)||(['rx','ry'].includes(id)&&value<.5)){render();return;}remember();s.e[id]=id==='angle'?value*Math.PI/180:value;syncEllipse(s);changed(owner(s.id)?.id);};
$('circle').onclick=()=>{const s=shape();if(!s?.e||busy)return;remember();s.e.rx=s.e.ry=(s.e.rx+s.e.ry)/2;s.e.angle=0;syncEllipse(s);changed(owner(s.id)?.id);};
$('start').onclick=$('chooseDirectory').onclick=()=>openDataset(null,true);

$('pathDirectory').onclick=()=>{if(config&&!busy)$('pathDialog').showModal();};
$('openPath').onclick=()=>{const path=$('directoryPath').value.trim();if(path){$('pathDialog').close();openDataset(path);}};
$('pathDialog').querySelector('form').addEventListener('submit',e=>{if(e.submitter?.value==='cancel')return;e.preventDefault();$('openPath').click();});
$('save').onclick=async()=>{if(busy||drag)return;if(await save())message('修改与审核进度已保存到原 JSON。');};
$('prevImage').onclick=()=>navigateVisible(-1);$('nextImage').onclick=()=>navigateVisible(1);
let listFilterTimer=null;
$('search').oninput=()=>{renderList();clearTimeout(listFilterTimer);listFilterTimer=setTimeout(changeListFilter,250);};
$('filter').onchange=changeListFilter;
$('removeImage').onclick=removeImage;$('restoreImage').onclick=restoreImage;
$('partsMode').onclick=()=>setPhase('parts');$('overviewMode').onclick=mini.onclick=()=>setPhase('overview');$('fit').onclick=fit;
$('confirm').onclick=confirmPart;$('complete').onclick=complete;$('prevPart').onclick=()=>stepPart(-1);$('nextPart').onclick=()=>stepPart(1);
$('addOuter').onclick=()=>startAdd('wai');$('addInner').onclick=()=>startAdd('nei');$('addBolt').onclick=()=>startAdd('screw');$('removePart').onclick=removePart;$('removeContour').onclick=removeContour;
$('undo').onclick=()=>undo();$('redo').onclick=()=>undo(true);$('overlays').onchange=draw;
$('export').onclick=()=>{if(!loaded||drag)return;const url=URL.createObjectURL(new Blob([JSON.stringify(R.output(M),null,2)+'\n'],{type:'application/json'})),a=document.createElement('a');a.href=url;a.download=file().id.split('/').pop().replace('.json','.review-draft.json');a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);};
document.addEventListener('keydown',e=>{
  if(e.key==='Shift'){shiftHeld=true;stage.style.cursor=drag?.type==='pan'?'grabbing':'grab';}
  if($('pathDialog').open)return;const editing=['INPUT','SELECT','TEXTAREA'].includes(e.target.tagName);
  if((e.metaKey||e.ctrlKey)&&e.key.toLowerCase()==='s'){e.preventDefault();if(!busy&&!drag)save();return;}
  if(editing)return;
  if((e.metaKey||e.ctrlKey)&&e.key.toLowerCase()==='z'){e.preventDefault();undo(e.shiftKey);return;}
  if(e.key==='Escape'){e.preventDefault();if(drag){finishDrag({pointerId:drag.pointerId},true);}held=null;addMode=null;hidden=false;render();return;}
  if(!loaded||busy)return;const k=e.key.toLowerCase();
  if(k==='h'){hidden=true;draw();return;}
  if(e.code==='Space'){e.preventDefault();if(!e.repeat){spaceHeld=true;spacePanned=false;}return;}
  if(e.repeat)return;
  if(k==='delete'&&e.shiftKey&&!e.ctrlKey&&!e.metaKey&&!e.altKey){e.preventDefault();removeImage();return;}
  if(k in keyIndex&&part()?.type==='bolt'&&phase()==='parts'){e.preventDefault();placePoint(keyIndex[k]);}
  else if((k==='1'||k==='2')&&canAdd(k==='1'?'wai':'nei')){e.preventDefault();held=k==='1'?'wai':'nei';}
  else if(k==='b'&&canAdd('screw')){e.preventDefault();startAdd('screw');}
  else if(k==='f'&&!e.ctrlKey&&!e.metaKey&&!e.altKey&&!drag){e.preventDefault();fit();}
  else if(k==='enter'&&phase()==='overview'){e.preventDefault();complete();}
  else if(k==='['){e.preventDefault();stepPart(-1);}else if(k===']'){e.preventDefault();stepPart(1);}
  else if((k==='delete'||k==='backspace')&&!e.shiftKey&&!e.ctrlKey&&!e.metaKey&&!e.altKey){e.preventDefault();removePart();}
  else if(k.startsWith('arrow')&&shape()?.e&&!drag){const d={arrowleft:[-1,0],arrowright:[1,0],arrowup:[0,-1],arrowdown:[0,1]}[k];if(d){e.preventDefault();remember();const s=shape(),step=e.shiftKey?.25:1;s.e.cx+=d[0]*step;s.e.cy+=d[1]*step;syncEllipse(s);changed(owner(s.id)?.id);}}
});
document.addEventListener('keyup',e=>{
  if(e.key==='Shift'){shiftHeld=false;stage.style.cursor=spaceHeld?'grab':'crosshair';}
  if(e.code==='Space'){const confirm=spaceHeld&&!spacePanned;spaceHeld=false;spacePanned=false;if(confirm&& !['INPUT','SELECT','TEXTAREA'].includes(e.target.tagName)&&!$('pathDialog').open){e.preventDefault();confirmPart();}}
  if(e.key==='1'||e.key==='2')held=null;if(e.key.toLowerCase()==='h'){hidden=false;draw();}
});
window.addEventListener('blur',()=>{spaceHeld=false;spacePanned=false;shiftHeld=false;held=null;hidden=false;if(drag)finishDrag({pointerId:drag.pointerId},true);draw();});
window.addEventListener('beforeunload',e=>{if(dirty){storeDraft();e.preventDefault();e.returnValue='';}});
new ResizeObserver(()=>{if(loaded)fit();}).observe(stage);
renderControls();
(async()=>{try{config=await request('/api/config');renderControls();for(const preset of config.presets){const b=document.createElement('button');b.textContent=preset.name;b.onclick=()=>openDataset(preset.path);$('presets').append(b);}const explicitPath=new URLSearchParams(window.location.search).get('path');if(explicitPath||config.default)await openDataset(explicitPath||config.default);}catch(e){message(e.message,'error');}})();
