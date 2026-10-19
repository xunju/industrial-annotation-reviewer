'use strict';
const $=id=>document.getElementById(id),R=SquareModel,stage=$('stage');
const labels=['lt','rt','rb','lb'],colors={lt:'#f5af3e',rt:'#29bbec',rb:'#da80cb',lb:'#58d19c'};
const keyLabels={q:'lt','1':'lt',w:'rt','2':'rt',s:'rb','3':'rb',a:'lb','4':'lb'};
let config,dataset,rows=[],current=-1,M,image,rev='',loaded=false,busy=false,dirty=false,version=0,savePromise=null,saveTimer,conflict=false;
let listRows=null,listButtons=[],selected=null,selectedPoint=null,view={cx:0,cy:0,scale:1},transform,drag=null,addMode=false,hidden=false,pointer=null;
let undoStack=[],redoStack=[],geometryResult=null,geometryVersion=-1,geometryTimer,geometryJob=0,previewTimer,previewJob=0,previewKey='';
const file=()=>rows[current],table=()=>R.selected(M,selected),tables=()=>R.tables(M);
const draftKey=()=>`square-table-draft:${dataset.directory}:${file()?.id}`,lastKey=()=>`square-table-last:${dataset.directory}`;
const query=(row=file())=>'?'+new URLSearchParams({dataset:dataset.dataset,id:row.id});
const sameGroup=(a,b)=>JSON.stringify(a)===JSON.stringify(b);
function message(text,kind=''){for(const id of ['message','welcomeMessage']){$(id).textContent=text;$(id).className=kind;}}
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
function storeDraft(){if(!loaded||!dirty)return;try{localStorage.setItem(draftKey(),JSON.stringify({revision:rev,document:R.output(M)}));}catch{message('浏览器草稿空间不足，请及时保存或导出草稿。','warn');}}
function remember(){undoStack.push({model:R.clone(M),selected,point:selectedPoint});if(undoStack.length>60)undoStack.shift();redoStack=[];}
function keepSelection(group){if(!table()){selected=tables().find(t=>sameGroup(t.group_id,group))?.id||tables()[0]?.id||null;}if(!table())selectedPoint=null;}
function invalidateGeometry(){clearTimeout(geometryTimer);clearTimeout(previewTimer);geometryJob++;previewJob++;geometryResult=null;geometryVersion=-1;previewKey='';$('previewImage').hidden=true;$('previewStatus').hidden=false;$('previewStatus').textContent='正在核对四角…';}
function changed(){
  M.completed=false;dirty=true;version++;if(file())file().checked=false;storeDraft();clearTimeout(saveTimer);saveTimer=setTimeout(save,650);
  invalidateGeometry();geometryTimer=setTimeout(checkGeometry,300);render();if(!conflict)message('标注已修改，核对所有桌面后按 Enter 确认本图。');
}
function save(){
  if(drag){clearTimeout(saveTimer);saveTimer=setTimeout(save,650);return Promise.resolve(false);}
  if(savePromise)return savePromise.then(ok=>ok&&dirty?save():ok);
  if(!loaded||!dirty)return Promise.resolve(true);if(conflict)return Promise.resolve(false);
  clearTimeout(saveTimer);const v=version,payload=R.output(M),row=file(),draft=draftKey(),set=dataset.dataset;$('saveState').textContent='正在保存…';
  savePromise=(async()=>{try{
    const response=await request('/api/save',{dataset:set,id:row.id,revision:rev,annotation:payload});rev=response.revision;
    if(v===version){M.source=R.clone(response.annotation||payload);M.completed=!!response.review_valid&&M.source.square_table_review?.completed===true;dirty=false;try{localStorage.removeItem(draft);}catch{}}
    else storeDraft();
    row.checked=v===version?M.completed:false;$('saveState').textContent=dirty?'有修改 · 待保存':'已保存到原 JSON';renderList();renderControls();return true;
  }catch(e){conflict=!!e.conflict;storeDraft();$('saveState').textContent='保存失败 · 草稿保留';message(e.conflict?'原 JSON 已被其他程序修改，已停止覆盖并保留草稿。请先导出草稿，再核对原文件。':e.message,'error');return false;}
  finally{savePromise=null;}})();return savePromise;
}
function setBusy(value){busy=value;renderControls();}
async function openDataset(path=null,pick=false){
  if(busy||drag||!config)return;setBusy(true);
  try{
    if(!await save())return;
    message(pick?'请在系统窗口选择目录；若窗口被遮挡，可按 Alt + Tab 切换。也可使用“输入路径”。':'正在读取目录…');
    const data=await request(pick?'/api/pick':'/api/open',path?{path}:{});if(data.cancelled){message('已取消选择目录。');return;}
    if(!data.images.length){message('这个目录里没有匹配的方桌图片与 JSON。','warn');return;}
    dataset=data;rows=data.images;resetListCache();current=-1;loaded=false;dirty=false;M=null;conflict=false;invalidateGeometry();
    try{localStorage.setItem('square-table-directory',data.directory);}catch{}
    $('welcome').hidden=true;$('workspace').hidden=false;$('search').value='';$('filter').value='all';
    $('directoryLabel').textContent=data.directory.split(/[\\/]/).filter(Boolean).pop();$('directoryLabel').title=data.directory;
    $('datasetInfo').textContent=`${rows.length} 张图片${data.skipped?' · 跳过 '+data.skipped+' 个不匹配文件':''}\n${data.directory}`;
    let last;try{last=localStorage.getItem(lastKey());}catch{}const index=rows.findIndex(r=>r.id===last);await load(index>=0?index:0);
  }catch(e){message(e.message,'error');}finally{setBusy(false);}
}
async function load(index){
  clearTimeout(saveTimer);const row=rows[index],im=new Image();im.src='/api/image'+query(row);
  const [data]=await Promise.all([request('/api/annotation'+query(row)),im.decode()]);
  if(im.naturalWidth!==data.annotation.imageWidth||im.naturalHeight!==data.annotation.imageHeight)throw new Error('图片尺寸与 JSON 不一致，请先核对文件配对；尚未修改该文件。');
  current=index;M=R.load(data.annotation,data.review_valid);image=im;rev=data.revision;loaded=true;dirty=false;version=0;conflict=false;undoStack=[];redoStack=[];drag=null;addMode=false;hidden=false;pointer=null;selectedPoint=null;invalidateGeometry();
  let restored=false;
  try{
    const raw=localStorage.getItem(draftKey());if(raw){const d=JSON.parse(raw);M=R.load(d.document,false);dirty=true;restored=true;if(d.revision!==rev){conflict=true;message('原 JSON 已变化，已阻止覆盖并恢复浏览器草稿。请先导出草稿，再核对原文件。','error');}}
    localStorage.setItem(lastKey(),row.id);
  }catch{message('浏览器草稿无法读取，请核对原文件。','warn');}
  row.checked=M.completed;selected=tables()[0]?.id||null;fit();render();geometryTimer=setTimeout(checkGeometry,0);
  if(!conflict)message(restored?'已恢复未保存草稿。':M.stale?'原标注或几何规则已变化，请重新检查本图。':M.completed?'本图已审核，可继续核对或修改。':selected?'核对绿色检测框及四个角点，全部无误后按 Enter。':'本图还没有方桌标注，按 B 拖动补画 desk 框，再补齐四角。');
  if(restored&&!conflict){storeDraft();saveTimer=setTimeout(save,650);}
}
async function navigate(index){if(busy||drag||index<0||index>=rows.length||index===current)return;setBusy(true);try{if(await save())await load(index);}catch(e){message(e.message,'error');}finally{setBusy(false);}}
function resetListCache(){listRows=null;listButtons=[];$('imageList').replaceChildren();}
function renderList(){
  const text=$('search').value.toLowerCase(),filter=$('filter').value,list=$('imageList');let fragment=null,completed=0;
  if(listRows!==rows){
    listRows=rows;fragment=document.createDocumentFragment();listButtons=rows.map((r,i)=>{
      const button=document.createElement('button'),name=document.createElement('span'),status=document.createElement('small');button.title=r.id;name.textContent=r.name.replace(/\.[^.]+$/,'');button.append(name,status);button.onclick=()=>navigate(i);fragment.append(button);return {button,status,checked:null,active:null,visible:null};
    });
  }
  rows.forEach((r,i)=>{
    const entry=listButtons[i],checked=!!r.checked,active=i===current,visible=(r.id.toLowerCase().includes(text)||r.name.toLowerCase().includes(text))&&(filter!=='pending'||!checked)&&(filter!=='done'||checked);if(checked)completed++;
    if(entry.checked!==checked){entry.status.textContent=checked?'已审核':'待审核';entry.status.className=checked?'done':'';entry.checked=checked;}
    if(entry.active!==active){entry.button.className=active?'active':'';entry.active=active;}
    if(entry.visible!==visible){entry.button.hidden=!visible;entry.visible=visible;}
  });
  if(fragment)list.replaceChildren(fragment);const progress=`${completed} / ${rows.length}`;if($('imageProgress').textContent!==progress)$('imageProgress').textContent=progress;
}
function renderControls(){
  const blocked=!loaded||busy||!!drag,t=table();
  for(const id of ['save','fit','confirm','skip','add','remove','removePoint','undo','redo','boxX','boxY','boxW','boxH','pointX','pointY','preview'])$(id).disabled=blocked;
  $('chooseDirectory').disabled=$('pathDirectory').disabled=busy||!!drag||!config;$('start').disabled=busy||!config;
  $('prevImage').disabled=blocked||current<=0;$('nextImage').disabled=$('skip').disabled=blocked||current>=rows.length-1;
  $('undo').disabled=blocked||!undoStack.length;$('redo').disabled=blocked||!redoStack.length;$('remove').disabled=blocked||!t;
  $('removePoint').disabled=blocked||!t||!selectedPoint||!t.pointItems[selectedPoint]?.length;
  for(const id of ['boxX','boxY','boxW','boxH'])$(id).disabled=blocked||!t?.box;
  for(const id of ['pointX','pointY'])$(id).disabled=blocked||!t?.points[selectedPoint];
  $('preview').disabled=blocked||!t;for(const b of document.querySelectorAll('[data-label],[data-point]'))b.disabled=blocked||!t;
}
function currentGeometry(t=table()){return geometryVersion===version?geometryResult?.tables?.find(g=>g.id===t?.id):null;}
function localIssues(t=table()){return t?R.issues(t,M):[];}
function showIssues(){
  const t=table();if(!t)return;const g=currentGeometry(t),issues=g?[...new Set([...localIssues(t),...(g.issues||[])])]:localIssues(t);
  const globalIssues=geometryVersion===version?(geometryResult?.issues||[]):[];
  const text=issues.length?issues.map(s=>'• '+s).join('\n'):g?.ok?'四角与检测框通过四边形几何检查。':globalIssues.length?globalIssues.map(s=>'• '+s).join('\n'):'正在核对四边形几何规则…';
  $('issues').textContent=text;$('issues').className=g?.ok&&!issues.length&&!globalIssues.length?'valid':'';
  const stats=[];if(Number.isFinite(g?.area_ratio))stats.push(`面积比 ${(g.area_ratio*100).toFixed(1)}%`);if(Number.isFinite(g?.side_ratio))stats.push(`边长比 ${g.side_ratio.toFixed(2)}`);if(Number.isFinite(g?.homography_condition))stats.push(`条件数 ${g.homography_condition.toExponential(2)}`);$('geometryStats').textContent=stats.join(' · ');
}
function render(){
  renderControls();if(!loaded)return;renderList();$('imageName').textContent=file().name;$('imagePosition').textContent=`${current+1} / ${rows.length}`;
  $('stageTitle').textContent=addMode?'拖动补画 desk 检测框':'整图核对检测框与四角';$('stageBadge').textContent=addMode?'补框模式 · 松开后补齐四角 · Esc 取消':M.completed?'本图已审核':selectedPoint?`所选角点 ${selectedPoint} · Del 删除`:'整图审核';
  $('add').classList.toggle('active',addMode);$('saveState').textContent=conflict?'保存冲突 · 草稿保留':savePromise?'正在保存…':dirty?'有修改 · 自动保存中':'已保存到原 JSON';
  $('tableCount').textContent=`${tables().length} 个`;$('tables').replaceChildren();
  tables().forEach((t,i)=>{const button=document.createElement('button'),name=document.createElement('span'),status=document.createElement('small'),g=currentGeometry(t),issues=g?[...localIssues(t),...(g.issues||[])]:localIssues(t);button.className=(t.id===selected?'active ':'')+(issues.length?'has-issue':'');name.textContent=`方桌 ${i+1}`;status.textContent=issues.length?`${issues.length} 项待检查`:g?.ok?'几何通过':'检查中…';button.append(name,status);button.onclick=()=>selectTable(t.id);$('tables').append(button);});
  if(!tables().length){const p=document.createElement('p');p.className='help';p.textContent='暂无方桌，按 B 补框，再补齐四角。';$('tables').append(p);}
  const t=table();$('geometry').hidden=!t;if(t){
    $('selectedTitle').textContent=`方桌 ${tables().indexOf(t)+1}`;$('groupLabel').textContent=`group ${t.group_id??'未分组'}`;showIssues();
    for(const [id,key] of [['boxX','x'],['boxY','y'],['boxW','w'],['boxH','h']])if(document.activeElement!==$(id))$(id).value=t.box?+t.box[key].toFixed(2):'';
    for(const [id,i] of [['pointX',0],['pointY',1]])if(document.activeElement!==$(id))$(id).value=t.points[selectedPoint]?+t.points[selectedPoint][i].toFixed(2):'';
  }
  for(const b of document.querySelectorAll('[data-label]')){b.classList.toggle('set',!!t?.points[b.dataset.label]);b.classList.toggle('active',b.dataset.label===selectedPoint);}
  for(const b of document.querySelectorAll('[data-point]'))b.classList.toggle('active',b.dataset.point===selectedPoint);
  draw();
}
function selectTable(id){if(busy||drag)return;selected=id;selectedPoint=null;addMode=false;render();schedulePreview();}
async function checkGeometry(){
  if(!loaded||drag)return null;clearTimeout(geometryTimer);const job=++geometryJob,v=version,row=file(),set=dataset.dataset,payload=R.output(M);
  try{
    const data=await request('/api/geometry',{dataset:set,id:row.id,annotation:payload});
    if(job!==geometryJob||v!==version||row!==file())return null;geometryResult=data;geometryVersion=v;render();schedulePreview();return data;
  }catch(e){if(job===geometryJob&&v===version&&row===file()){geometryResult={ok:false,tables:[],issues:[e.message]};geometryVersion=v;render();$('previewStatus').textContent='几何检查失败，请重试。';}return null;}
}
function schedulePreview(){clearTimeout(previewTimer);previewJob++;const t=table(),g=currentGeometry(t);if(!t||!g?.ok){previewKey='';$('previewImage').hidden=true;$('previewStatus').hidden=false;$('previewStatus').textContent=t?(g?'请先修正四角或检测框问题':'正在核对四角…'):'请选择方桌';return;}previewTimer=setTimeout(updatePreview,180);}
async function updatePreview(force=false){
  if(!loaded||drag||!table())return;clearTimeout(previewTimer);const t=table(),v=version,row=file(),set=dataset.dataset,key=`${set}|${row.id}|${t.id}|${v}`;
  if(!force&&previewKey===key)return;const job=++previewJob;$('previewImage').hidden=true;$('previewStatus').hidden=false;$('previewStatus').textContent='正在生成桌面预览…';
  try{
    const data=await request('/api/preview',{dataset:set,id:row.id,table:t.id,annotation:R.output(M)});
    if(job!==previewJob||v!==version||row!==file()||selected!==t.id)return;
    if(!data.ok||!data.image)throw new Error(data.error||'请先修正四角与检测框。');
    $('previewImage').src=data.image;$('previewImage').hidden=false;$('previewStatus').hidden=true;previewKey=key;
  }catch(e){if(job===previewJob&&v===version&&row===file()&&selected===t.id){$('previewImage').hidden=true;$('previewStatus').hidden=false;$('previewStatus').textContent=e.message;}}
}
function fit(){if(!loaded)return;const r=stage.getBoundingClientRect();view={cx:image.width/2,cy:image.height/2,scale:.94*Math.min(r.width/image.width,r.height/image.height)};draw();}
function size(){const r=stage.getBoundingClientRect(),dpr=devicePixelRatio||1,w=Math.max(1,r.width),h=Math.max(1,r.height);if(stage.width!==Math.round(w*dpr)||stage.height!==Math.round(h*dpr)){stage.width=Math.round(w*dpr);stage.height=Math.round(h*dpr);}const ctx=stage.getContext('2d');ctx.setTransform(dpr,0,0,dpr,0,0);return {ctx,w,h};}
const project=p=>[(p[0]-view.cx)*view.scale+transform.w/2,(p[1]-view.cy)*view.scale+transform.h/2];
const unproject=p=>[(p[0]-transform.w/2)/view.scale+view.cx,(p[1]-transform.h/2)/view.scale+view.cy];
const pixel=e=>{const r=stage.getBoundingClientRect();return [e.clientX-r.left,e.clientY-r.top];};
const inside=p=>p[0]>=0&&p[1]>=0&&p[0]<=image.width&&p[1]<=image.height;
const clamp=p=>[Math.max(0,Math.min(image.width,p[0])),Math.max(0,Math.min(image.height,p[1]))];
function handles(t=table()){if(!t?.box)return [];const b=t.box;return [{name:'lt',p:[b.x,b.y],offset:[-12,-12]},{name:'rt',p:[b.x+b.w,b.y],offset:[12,-12]},{name:'rb',p:[b.x+b.w,b.y+b.h],offset:[12,12]},{name:'lb',p:[b.x,b.y+b.h],offset:[-12,12]}].map(h=>{const anchor=project(h.p);return {...h,anchor,p:[anchor[0]+h.offset[0],anchor[1]+h.offset[1]]};});}
function draw(){
  const {ctx,w,h}=size();ctx.fillStyle='#e6ebe6';ctx.fillRect(0,0,w,h);transform={w,h};if(!loaded)return;
  const top=project([0,0]);ctx.imageSmoothingEnabled=true;ctx.drawImage(image,...top,image.width*view.scale,image.height*view.scale);if(hidden||!$('overlays').checked)return;
  tables().forEach((t,i)=>{
    const active=t.id===selected;
    if(t.box){const p=project([t.box.x,t.box.y]),bw=t.box.w*view.scale,bh=t.box.h*view.scale;ctx.lineWidth=active?3.5:2.5;ctx.strokeStyle='#183d2dcc';ctx.strokeRect(...p,bw,bh);ctx.lineWidth=active?1.9:1.3;ctx.strokeStyle='#58ef85';ctx.strokeRect(...p,bw,bh);if(active){ctx.fillStyle='#58ef8507';ctx.fillRect(...p,bw,bh);}ctx.font='bold 11px sans-serif';const tag=`desk ${i+1}`;ctx.fillStyle='#164f3fd9';ctx.fillRect(p[0],p[1]-19,ctx.measureText(tag).width+12,18);ctx.fillStyle='#fff';ctx.fillText(tag,p[0]+6,p[1]-6);}
    const pts=labels.map(l=>t.points[l]);ctx.setLineDash([]);ctx.lineWidth=active?2:1.4;
    for(let j=0;j<4;j++){if(!pts[j]||!pts[(j+1)%4])continue;ctx.beginPath();ctx.moveTo(...project(pts[j]));ctx.lineTo(...project(pts[(j+1)%4]));ctx.strokeStyle=colors[labels[j]];ctx.stroke();}
    labels.forEach(label=>{const p=t.points[label];if(!p)return;const px=project(p),sel=active&&label===selectedPoint;ctx.beginPath();ctx.arc(...px,sel?6:active?5:4,0,Math.PI*2);ctx.fillStyle=colors[label];ctx.fill();ctx.strokeStyle=sel?'#fff':'#20373a';ctx.lineWidth=sel?2.5:1.3;ctx.stroke();ctx.font='bold 11px ui-monospace,monospace';const labelWidth=ctx.measureText(label).width;ctx.fillStyle='#20373ad4';ctx.fillRect(px[0]+8,px[1]-18,labelWidth+8,17);ctx.fillStyle=colors[label];ctx.fillText(label,px[0]+12,px[1]-6);});
    if(active&&!addMode){ctx.fillStyle='#fff';ctx.strokeStyle='#187568';ctx.lineWidth=1.4;for(const handle of handles(t)){ctx.beginPath();ctx.moveTo(...handle.anchor);ctx.lineTo(...handle.p);ctx.stroke();ctx.fillRect(handle.p[0]-4,handle.p[1]-4,8,8);ctx.strokeRect(handle.p[0]-4,handle.p[1]-4,8,8);}}
  });
  if(drag?.type==='add'&&drag.preview){const b=drag.preview,p=project([b.x,b.y]);ctx.setLineDash([5,4]);ctx.strokeStyle='#58ef85';ctx.lineWidth=2;ctx.strokeRect(...p,b.w*view.scale,b.h*view.scale);ctx.setLineDash([]);}
}
function hitPoint(px){let closest=null,distance=12;for(const t of [...tables()].reverse())for(const label of labels){const p=t.points[label];if(!p)continue;const q=project(p),d=Math.hypot(q[0]-px[0],q[1]-px[1]);if(d<distance){distance=d;closest={table:t,label};}}return closest;}
function hitTable(p){return [...tables()].reverse().find(t=>t.box&&p[0]>=t.box.x&&p[1]>=t.box.y&&p[0]<=t.box.x+t.box.w&&p[1]<=t.box.y+t.box.h)||null;}
function mutate(fn){if(!loaded||busy||drag||!table())return;const group=table().group_id;remember();fn(table());keepSelection(group);changed();}
function setPointAt(label){
  if(!loaded||busy||drag||addMode||!table())return;if(!pointer||!inside(unproject(pointer))){message('请先把鼠标指向图片中的角点位置，再按对应快捷键。','warn');return;}
  const p=clamp(unproject(pointer));selectedPoint=label;mutate(t=>R.setPoint(M,t.id,label,p));
}
function setAdd(){if(!loaded||busy||drag)return;addMode=!addMode;selectedPoint=null;render();message(addMode?(table()&&!table().box?'所选桌面缺少 desk 框。拖动补框会保留现有角点及 group；Esc 取消。':'按住左键拖动补画 desk 框，松开后用 Q / W / S / A 补齐四角；Esc 取消。'):'已退出补框模式。');}
stage.addEventListener('contextmenu',e=>e.preventDefault());
stage.addEventListener('wheel',e=>{if(!loaded||busy||drag)return;e.preventDefault();const p=pixel(e),before=unproject(p),factor=Math.exp(-e.deltaY*.0015);view.scale=Math.max(.025,Math.min(30,view.scale*factor));const after=unproject(p);view.cx+=before[0]-after[0];view.cy+=before[1]-after[1];pointer=p;draw();},{passive:false});
stage.addEventListener('pointerdown',e=>{
  if(!loaded||busy||drag)return;const px=pixel(e),p=unproject(px);pointer=px;stage.focus();e.preventDefault();
  if(e.button===2||e.button===1||e.button===0&&e.shiftKey)drag={type:'pan',pixel:px,view:{...view},pointerId:e.pointerId};
  else if(e.button!==0)return;
  else if(addMode){if(!inside(p))return;drag={type:'add',start:p,pointerId:e.pointerId};}
  else if($('overlays').checked&&!hidden){
    const point=hitPoint(px),handle=point?null:handles().find(h=>Math.hypot(h.p[0]-px[0],h.p[1]-px[1])<=9),t=point?.table||handle&&table()||hitTable(p);
    if(!t){selectedPoint=null;render();return;}selected=t.id;selectedPoint=point?.label||null;
    drag={type:point?'point':handle?'resize':'box',handle:handle?.name,offset:handle?.offset,point:point?.label,box:t.box?R.clone(t.box):null,start:p,before:R.clone(M),tableId:t.id,group:t.group_id,selectedBefore:selected,pointBefore:selectedPoint,pointerId:e.pointerId,moved:false};
    invalidateGeometry();
  }else return;
  stage.setPointerCapture(e.pointerId);render();
});
stage.addEventListener('pointermove',e=>{
  pointer=pixel(e);if(!drag||e.pointerId!==drag.pointerId)return;const px=pointer,p=unproject(px);
  if(drag.type==='pan'){view.cx=drag.view.cx-(px[0]-drag.pixel[0])/view.scale;view.cy=drag.view.cy-(px[1]-drag.pixel[1])/view.scale;draw();return;}
  if(drag.type==='add'){const q=clamp(p);drag.preview={x:Math.min(q[0],drag.start[0]),y:Math.min(q[1],drag.start[1]),w:Math.abs(q[0]-drag.start[0]),h:Math.abs(q[1]-drag.start[1])};draw();return;}
  if(Math.hypot(p[0]-drag.start[0],p[1]-drag.start[1])<.01)return;drag.moved=true;
  if(drag.type==='point')R.setPoint(M,drag.tableId,drag.point,clamp(p));
  if(drag.type==='box'){const b=drag.box;R.setBox(M,drag.tableId,{...b,x:Math.max(0,Math.min(image.width-b.w,b.x+p[0]-drag.start[0])),y:Math.max(0,Math.min(image.height-b.h,b.y+p[1]-drag.start[1]))});}
  if(drag.type==='resize'){
    const b=drag.box,q=clamp([p[0]-drag.offset[0]/view.scale,p[1]-drag.offset[1]/view.scale]),left=drag.handle.includes('l'),top=drag.handle.includes('t'),anchorX=left?b.x+b.w:b.x,anchorY=top?b.y+b.h:b.y,tipX=left?Math.min(anchorX-1,q[0]):Math.max(anchorX+1,q[0]),tipY=top?Math.min(anchorY-1,q[1]):Math.max(anchorY+1,q[1]);
    R.setBox(M,drag.tableId,{x:Math.min(anchorX,tipX),y:Math.min(anchorY,tipY),w:Math.abs(tipX-anchorX),h:Math.abs(tipY-anchorY)});
  }
  keepSelection(drag.group);M.completed=false;geometryResult=null;geometryVersion=-1;render();
});
stage.addEventListener('pointerleave',()=>{if(!drag)pointer=null;});
function endDrag(cancel=false){
  if(!drag)return;const d=drag;drag=null;try{stage.releasePointerCapture(d.pointerId);}catch{}
  if(cancel){if(d.before)M=d.before;render();geometryTimer=setTimeout(checkGeometry,0);return;}
  if(d.type==='add'&&d.preview?.w>=2&&d.preview?.h>=2){
    const t=table(),repair=!!t&&!t.box;remember();if(repair){const group=t.group_id;R.setBox(M,t.id,d.preview);keepSelection(group);}else selected=R.add(M,d.preview);
    selectedPoint=null;addMode=false;changed();message(repair?'已为现有四角补上 desk 框，保留原 group。请核对几何提示。':'检测框已补画。鼠标指向四个角，依次按 Q、W、S、A 补齐四角。');
  }
  else if(d.moved){undoStack.push({model:d.before,selected:d.selectedBefore,point:d.pointBefore});if(undoStack.length>60)undoStack.shift();redoStack=[];changed();}
  else{render();if(d.before)geometryTimer=setTimeout(checkGeometry,0);else schedulePreview();}
}
stage.addEventListener('pointerup',()=>endDrag());stage.addEventListener('pointercancel',()=>endDrag(true));stage.addEventListener('lostpointercapture',()=>{if(drag)endDrag(true);});
function undo(redo=false){if(!loaded||busy||drag)return;const from=redo?redoStack:undoStack,to=redo?undoStack:redoStack,s=from.pop();if(!s)return;to.push({model:R.clone(M),selected,point:selectedPoint});M=s.model;selected=s.selected;selectedPoint=s.point;keepSelection(null);addMode=false;changed();}
function remove(){if(!table()||busy||drag)return;remember();R.remove(M,selected);selected=tables()[0]?.id||null;selectedPoint=null;changed();}
function removePoint(){if(!table()||!selectedPoint||busy||drag||!table().pointItems[selectedPoint]?.length)return;mutate(t=>R.removePoint(M,t.id,selectedPoint));}
async function confirm(){
  if(!loaded||busy||drag||addMode)return;if(!tables().length){message('本图没有方桌标注，请补框并补齐四角后再确认。','warn');return;}if(conflict){message('原文件存在保存冲突，请先导出草稿并核对原文件。','error');return;}
  setBusy(true);try{
    const result=await checkGeometry();if(!result?.ok){const first=result?.tables?.find(t=>!t.ok);if(first){selected=first.id;selectedPoint=null;render();schedulePreview();}message('本图仍有几何或标注问题，请修正右侧提示后再确认。','warn');return;}
    M.completed=true;dirty=true;version++;geometryVersion=version;storeDraft();render();
    if(!await save()){M.completed=false;file().checked=false;storeDraft();render();return;}
    if(!M.completed){message('后端未确认审核状态，请重新核对本图；已停止切换。','error');render();return;}
    if(current<rows.length-1)await load(current+1);else{render();message('本图已确认，已到最后一张。'+(rows.every(r=>r.checked)?'全部图片审核完成。':'队列中还有待审核图片。'));}
  }catch(e){message(e.message,'error');}finally{setBusy(false);}
}
function exportDraft(){if(!loaded)return;const blob=new Blob([JSON.stringify(R.output(M),null,2)],{type:'application/json'}),url=URL.createObjectURL(blob),a=document.createElement('a');a.href=url;a.download=file().id.split(/[\\/]/).pop().replace(/\.json$/,'_草稿.json');a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);}
for(const [id,key] of [['boxX','x'],['boxY','y'],['boxW','w'],['boxH','h']])$(id).addEventListener('change',()=>{
  const value=Number($(id).value),t=table();if(!t?.box)return;
  if($(id).value.trim()===''||!Number.isFinite(value)||Math.abs(value)>1e6||(['w','h'].includes(key)&&value<1)){message('请输入有效数值，检测框宽度和高度至少为 1。','warn');$(id).blur();render();return;}
  mutate(t=>R.setBox(M,t.id,{...t.box,[key]:value}));
});
for(const [id,index] of [['pointX',0],['pointY',1]])$(id).addEventListener('change',()=>{
  const value=Number($(id).value),t=table();if(!t?.points[selectedPoint])return;
  if($(id).value.trim()===''||!Number.isFinite(value)||value<0||value>(index===0?image.width:image.height)){message('角点坐标必须位于图片范围内。','warn');$(id).blur();render();return;}
  mutate(t=>{const p=[...t.points[selectedPoint]];p[index]=value;R.setPoint(M,t.id,selectedPoint,p);});
});
for(const b of document.querySelectorAll('[data-label],[data-point]'))b.onclick=()=>{if(!loaded||busy||drag||!table())return;selectedPoint=b.dataset.label||b.dataset.point;render();message(`已选择 ${selectedPoint}；鼠标指向图片并按对应快捷键可放置，已有点也可拖动。`);};
$('add').onclick=setAdd;$('remove').onclick=remove;$('removePoint').onclick=removePoint;$('undo').onclick=()=>undo();$('redo').onclick=()=>undo(true);$('confirm').onclick=confirm;$('skip').onclick=()=>navigate(current+1);
$('prevImage').onclick=()=>navigate(current-1);$('nextImage').onclick=()=>navigate(current+1);$('save').onclick=save;$('fit').onclick=fit;$('overlays').onchange=draw;$('export').onclick=exportDraft;$('preview').onclick=()=>updatePreview(true);
$('search').oninput=renderList;$('filter').onchange=renderList;
$('chooseDirectory').onclick=$('start').onclick=()=>openDataset(null,true);$('pathDirectory').onclick=()=>{$('pathDialog').showModal();$('directoryPath').focus();};
$('openPath').onclick=()=>{const path=$('directoryPath').value.trim();if(path){$('pathDialog').close();openDataset(path);}};
$('directoryPath').onkeydown=e=>{if(e.key==='Enter'){e.preventDefault();$('openPath').click();}};
window.addEventListener('keydown',e=>{
  const key=e.key.toLowerCase(),cmd=e.metaKey||e.ctrlKey;
  if(cmd&&key==='s'){e.preventDefault();if(e.target.matches?.('#geometry input'))e.target.blur();save();return;}
  if(['INPUT','SELECT','TEXTAREA'].includes(e.target.tagName)||$('pathDialog').open)return;
  if(cmd&&key==='z'){e.preventDefault();undo(e.shiftKey);return;}if(cmd&&key==='y'){e.preventDefault();undo(true);return;}if(cmd||e.altKey||!loaded||busy)return;
  if(key==='escape'){if(drag)endDrag(true);addMode=false;render();return;}if(drag)return;
  if(key==='h'){hidden=true;draw();return;}
  if(key===' '||key==='enter'){e.preventDefault();if(!e.repeat)confirm();return;}
  if(keyLabels[key]){e.preventDefault();if(!e.repeat)setPointAt(keyLabels[key]);return;}
  if(key==='b'&&!e.repeat){setAdd();return;}if(key==='delete'||key==='backspace'){e.preventDefault();if(selectedPoint)removePoint();else remove();return;}
  if(key==='f'){fit();return;}if(key==='['){navigate(current-1);return;}if(key===']'){navigate(current+1);return;}
  if(['arrowleft','arrowright','arrowup','arrowdown'].includes(key)&&table()){
    e.preventDefault();const d=e.shiftKey?10:1,dx=key==='arrowright'?d:key==='arrowleft'?-d:0,dy=key==='arrowdown'?d:key==='arrowup'?-d:0;
    if(selectedPoint&&table().points[selectedPoint])mutate(t=>R.setPoint(M,t.id,selectedPoint,clamp([t.points[selectedPoint][0]+dx,t.points[selectedPoint][1]+dy])));
    else if(table().box)mutate(t=>R.setBox(M,t.id,{...t.box,x:t.box.x+dx,y:t.box.y+dy}));
  }
});
window.addEventListener('keyup',e=>{if(e.key.toLowerCase()==='h'){hidden=false;draw();}});
window.addEventListener('blur',()=>{hidden=false;endDrag(true);draw();});
window.addEventListener('beforeunload',e=>{if(drag?.before&&drag.moved){dirty=true;M.completed=false;storeDraft();}if(dirty){storeDraft();e.preventDefault();e.returnValue='';}});
new ResizeObserver(()=>{if(loaded)fit();}).observe(stage);
(async()=>{try{
  config=await request('/api/config');for(const preset of config.presets||[]){const b=document.createElement('button');b.textContent=preset.name;b.onclick=()=>openDataset(preset.path);$('presets').append(b);}
  if(config.reference){const ref=config.reference;$('reference').textContent=typeof ref==='string'?ref:ref.description||ref.name||'Independent quadrilateral validation';}
  if(config.geometry){const g=config.geometry;$('geometryRules').textContent=`面积比 ${(g.min_area_ratio*100).toFixed(0)}%–${(g.max_area_ratio*100).toFixed(0)}%；角点间距至少 ${g.min_corner_distance} 像素；边长比不超过 ${g.max_side_ratio}；单应矩阵条件数不超过 ${Number(g.max_homography_condition).toExponential(0)}。`;}
  const explicitPath=new URLSearchParams(window.location.search).get('path');let lastDirectory;try{lastDirectory=localStorage.getItem('square-table-directory');}catch{}renderControls();if(explicitPath||lastDirectory||config.default)await openDataset(explicitPath||lastDirectory||config.default);
}catch(e){message('本地服务不可用：'+e.message,'error');}})();
