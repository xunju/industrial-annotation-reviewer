'use strict';
const $=id=>document.getElementById(id),R=RoundModel,stage=$('stage');
const directions=[[1,0],[1,1],[0,1],[-1,1],[-1,0],[-1,-1],[0,-1],[1,-1]];
let config,dataset,rows=[],current=-1,M,image,rev='',loaded=false,busy=false,dirty=false,version=0,savePromise=null,saveTimer,conflict=false;
let listRows=null,listButtons=[];
let selected=null,view={cx:0,cy:0,scale:1},transform,drag=null,addMode=false,hidden=false,undoStack=[],redoStack=[];
const file=()=>rows[current],shape=()=>M?.shapes.find(s=>s.id===selected&&s.e);
const draftKey=()=>`round-table-draft:${dataset.directory}:${file()?.id}`,lastKey=()=>`round-table-last:${dataset.directory}`;
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
const query=(row=file())=>'?'+new URLSearchParams({dataset:dataset.dataset,id:row.id});
function storeDraft(){if(!loaded||!dirty)return;try{localStorage.setItem(draftKey(),JSON.stringify({revision:rev,document:R.output(M)}));}catch{message('浏览器草稿空间不足，请及时保存。','warn');}}
function remember(){undoStack.push({model:R.clone(M),selected});if(undoStack.length>60)undoStack.shift();redoStack=[];}
function changed(){M.completed=false;dirty=true;version++;storeDraft();clearTimeout(saveTimer);saveTimer=setTimeout(save,650);render();if(!conflict)message('轮廓已修改，核对无误后按空格确认本图。');}
function save(){
  if(drag){clearTimeout(saveTimer);saveTimer=setTimeout(save,650);return Promise.resolve(false);}
  if(savePromise)return savePromise.then(ok=>ok&&dirty?save():ok);
  if(!loaded||!dirty)return Promise.resolve(true);if(conflict)return Promise.resolve(false);
  clearTimeout(saveTimer);const v=version,payload=R.output(M),row=file();$('saveState').textContent='正在保存…';
  savePromise=(async()=>{try{
    const response=await request('/api/save',{dataset:dataset.dataset,id:row.id,revision:rev,annotation:payload});rev=response.revision;M.source=R.clone(payload);
    if(v===version){dirty=false;try{localStorage.removeItem(draftKey());}catch{}}else storeDraft();row.checked=payload.round_table_review.completed;
    $('saveState').textContent=dirty?'有修改 · 待保存':'已保存到原 JSON';renderList();return true;
  }catch(e){conflict=!!e.conflict;storeDraft();$('saveState').textContent='保存失败 · 草稿保留';message(e.message,'error');return false;}finally{savePromise=null;}})();return savePromise;
}
function setBusy(value){busy=value;renderControls();}
async function openDataset(path=null,pick=false){
  if(busy||drag||!config)return;setBusy(true);
  try{
    if(!await save())return;message(pick?'目录选择窗口已打开，请在弹出的系统窗口中选择；找不到时可按 Alt + Tab 切换。':'正在读取目录…');
    const data=await request(pick?'/api/pick':'/api/open',path?{path}:{});if(data.cancelled){message('已取消选择目录。');return;}
    if(!data.images.length){message('这个目录里没有匹配的圆桌图片与 JSON。','warn');return;}
    dataset=data;rows=data.images;resetListCache();current=-1;loaded=false;dirty=false;M=null;conflict=false;
    $('welcome').hidden=true;$('workspace').hidden=false;$('search').value='';$('filter').value='all';
    $('directoryLabel').textContent=data.directory.split(/[\\/]/).filter(Boolean).pop();$('directoryLabel').title=data.directory;
    $('datasetInfo').textContent=`${rows.length} 张图片${data.skipped?' · 跳过 '+data.skipped+' 个不匹配文件':''}\n${data.directory}`;
    let last;try{last=localStorage.getItem(lastKey());}catch{}const index=rows.findIndex(r=>r.id===last);await load(index>=0?index:0);
  }catch(e){message(e.message,'error');}finally{setBusy(false);}
}
async function load(index){
  const row=rows[index],im=new Image();im.src='/api/image'+query(row);
  const [data]=await Promise.all([request('/api/annotation'+query(row)),im.decode()]);
  if(im.naturalWidth!==data.annotation.imageWidth||im.naturalHeight!==data.annotation.imageHeight)throw new Error('图片尺寸与 JSON 不一致，请先核对文件配对；尚未修改该文件。');
  current=index;M=R.load(data.annotation);image=im;rev=data.revision;loaded=true;dirty=false;version=0;conflict=false;undoStack=[];redoStack=[];drag=null;addMode=false;hidden=false;
  let restored=false;
  try{const raw=localStorage.getItem(draftKey());if(raw){const d=JSON.parse(raw);M=R.load(d.document);dirty=true;restored=true;if(d.revision!==rev){conflict=true;message('原 JSON 已变化，已阻止覆盖。请先导出草稿，在其他窗口关闭文件后处理冲突。','error');}}localStorage.setItem(lastKey(),row.id);}catch{message('浏览器草稿无法读取，请核对原文件。','warn');}
  row.checked=M.completed;selected=R.contours(M)[0]?.id||null;fit();render();
  if(!conflict)message(restored?'已恢复未保存草稿。':M.stale?'原标注已变化，请重新检查本图。':M.completed?'本图已审核，可继续修改。':selected?'在整图上核对圆桌轮廓，无误后按空格。':'本图还没有圆桌轮廓，按 B 拖动补画。');
  if(restored&&!conflict){storeDraft();saveTimer=setTimeout(save,650);}
}
async function navigate(index){if(busy||drag||index<0||index>=rows.length||index===current)return;setBusy(true);try{if(await save())await load(index);}catch(e){message(e.message,'error');}finally{setBusy(false);}}
function resetListCache(){listRows=null;listButtons=[];$('imageList').replaceChildren();}
function renderList(){
  const text=$('search').value.toLowerCase(),f=$('filter').value,list=$('imageList');let fragment=null,completed=0;
  if(listRows!==rows){
    listRows=rows;fragment=document.createDocumentFragment();
    listButtons=rows.map((r,i)=>{
      const b=document.createElement('button'),n=document.createElement('span'),s=document.createElement('small');
      b.title=r.id;n.textContent=r.name.replace(/\.[^.]+$/,'');b.append(n,s);b.onclick=()=>navigate(i);fragment.append(b);
      return {button:b,status:s,checked:null,active:null,visible:null};
    });
  }
  rows.forEach((r,i)=>{
    const entry=listButtons[i],checked=!!r.checked,active=i===current;
    const visible=(r.id.toLowerCase().includes(text)||r.name.toLowerCase().includes(text))&&(f!=='pending'||!checked)&&(f!=='done'||checked);
    if(checked)completed++;
    if(entry.checked!==checked){entry.status.textContent=checked?'已审核':'待审核';entry.status.className=checked?'done':'';entry.checked=checked;}
    if(entry.active!==active){entry.button.className=active?'active':'';entry.active=active;}
    if(entry.visible!==visible){entry.button.hidden=!visible;entry.visible=visible;}
  });
  if(fragment)list.replaceChildren(fragment);
  const progress=`${completed} / ${rows.length}`;if($('imageProgress').textContent!==progress)$('imageProgress').textContent=progress;
}
function renderControls(){
  for(const id of ['save','fit','confirm','skip','add','remove','undo','redo','smaller','larger','rotateLeft','rotateRight','circle','cx','cy','rx','ry','angle'])$(id).disabled=!loaded||busy||!!drag;
  $('chooseDirectory').disabled=$('pathDirectory').disabled=busy||!!drag||!config;$('start').disabled=busy||!config;
  $('prevImage').disabled=!loaded||busy||!!drag||current<=0;$('nextImage').disabled=!loaded||busy||!!drag||current>=rows.length-1;
  $('undo').disabled=!loaded||busy||!!drag||!undoStack.length;$('redo').disabled=!loaded||busy||!!drag||!redoStack.length;
  for(const id of ['remove','smaller','larger','rotateLeft','rotateRight','circle','cx','cy','rx','ry','angle'])$(id).disabled=!shape()||busy||!!drag;
}
function render(){
  renderControls();if(!loaded)return;renderList();$('imageName').textContent=file().name;$('imagePosition').textContent=`${current+1} / ${rows.length}`;
  $('stageTitle').textContent=addMode?'拖动补画圆桌轮廓':'整图核对圆桌轮廓';$('stageBadge').textContent=addMode?'补画模式 · Esc 取消':M.completed?'本图已审核':'整图审核';
  $('add').classList.toggle('active',addMode);$('saveState').textContent=conflict?'保存冲突 · 草稿保留':dirty?'有修改 · 自动保存中':'已保存到原 JSON';
  const contours=R.contours(M);$('contourCount').textContent=`${contours.length} 个`;$('contours').replaceChildren();
  contours.forEach((s,i)=>{const b=document.createElement('button');b.className=s.id===selected?'active':'';b.textContent=`圆桌 ${i+1}`;b.onclick=()=>{if(!busy&&!drag){selected=s.id;addMode=false;render();}};$('contours').append(b);});
  if(!contours.length){const p=document.createElement('p');p.className='help';p.textContent='暂无轮廓，按 B 补画。';$('contours').append(p);}
  const s=shape();$('geometry').hidden=!s;if(s)for(const key of ['cx','cy','rx','ry','angle']){if(document.activeElement!==$(key))$(key).value=+(key==='angle'?s.e[key]*180/Math.PI:s.e[key]).toFixed(2);}
  draw();
}
function fit(){if(!loaded)return;const r=stage.getBoundingClientRect();view={cx:image.width/2,cy:image.height/2,scale:.94*Math.min(r.width/image.width,r.height/image.height)};draw();}
function size(){const r=stage.getBoundingClientRect(),dpr=devicePixelRatio||1,w=Math.max(1,r.width),h=Math.max(1,r.height);if(stage.width!==Math.round(w*dpr)||stage.height!==Math.round(h*dpr)){stage.width=Math.round(w*dpr);stage.height=Math.round(h*dpr);}const ctx=stage.getContext('2d');ctx.setTransform(dpr,0,0,dpr,0,0);return {ctx,w,h};}
const project=p=>[(p[0]-view.cx)*view.scale+transform.w/2,(p[1]-view.cy)*view.scale+transform.h/2];
const unproject=p=>[(p[0]-transform.w/2)/view.scale+view.cx,(p[1]-transform.h/2)/view.scale+view.cy];
const pixel=e=>{const r=stage.getBoundingClientRect();return [e.clientX-r.left,e.clientY-r.top];};
const ellipsePoint=(e,x,y)=>[e.cx+x*Math.cos(e.angle)-y*Math.sin(e.angle),e.cy+x*Math.sin(e.angle)+y*Math.cos(e.angle)];
const localPoint=(e,p)=>{const x=p[0]-e.cx,y=p[1]-e.cy,c=Math.cos(e.angle),s=Math.sin(e.angle);return [x*c+y*s,-x*s+y*c];};
function handles(s){if(!s)return [];const e=s.e,h=directions.map(([dx,dy])=>{const k=dx&&dy?Math.SQRT1_2:1;return {type:'resize',dx,dy,k,p:project(ellipsePoint(e,dx*e.rx*k,dy*e.ry*k))};}),p=project(ellipsePoint(e,0,-e.ry));h.push({type:'rotate',p:[p[0]+Math.sin(e.angle)*28,p[1]-Math.cos(e.angle)*28]});return h;}
function draw(){
  const {ctx,w,h}=size();ctx.fillStyle='#e6ebe6';ctx.fillRect(0,0,w,h);transform={w,h};if(!loaded)return;
  const top=project([0,0]);ctx.imageSmoothingEnabled=true;ctx.drawImage(image,...top,image.width*view.scale,image.height*view.scale);
  if(hidden||!$('overlays').checked)return;
  for(const s of R.contours(M)){
    const e=s.e,p=project([e.cx,e.cy]);ctx.beginPath();ctx.ellipse(...p,e.rx*view.scale,e.ry*view.scale,e.angle,0,2*Math.PI);ctx.strokeStyle='#20373aaa';ctx.lineWidth=s.id===selected?3.5:2.5;ctx.stroke();ctx.strokeStyle=s.id===selected?'#54ec85':'#f9c653';ctx.lineWidth=s.id===selected?1.8:1.2;ctx.stroke();
    if(s.id===selected){
      const corners=[[p[0],p[1]-8],[p[0]+5,p[1]],[p[0],p[1]+8],[p[0]-5,p[1]]];ctx.beginPath();corners.forEach((a,i)=>i?ctx.lineTo(...a):ctx.moveTo(...a));ctx.closePath();ctx.fillStyle='#42d34b';ctx.fill();ctx.strokeStyle='#fff';ctx.lineWidth=1.5;ctx.stroke();
      if(!addMode){ctx.fillStyle='#fff';ctx.strokeStyle='#187568';ctx.lineWidth=1.3;for(const h of handles(s)){if(h.type==='rotate'){ctx.beginPath();ctx.moveTo(...project(ellipsePoint(e,0,-e.ry)));ctx.lineTo(...h.p);ctx.stroke();ctx.beginPath();ctx.arc(...h.p,5,0,2*Math.PI);ctx.fill();ctx.stroke();}else{ctx.fillRect(h.p[0]-4,h.p[1]-4,8,8);ctx.strokeRect(h.p[0]-4,h.p[1]-4,8,8);}}}
    }
  }
  if(drag?.type==='add'&&drag.preview){const e=drag.preview;ctx.beginPath();ctx.ellipse(...project([e.cx,e.cy]),e.rx*view.scale,e.ry*view.scale,0,0,2*Math.PI);ctx.setLineDash([5,4]);ctx.strokeStyle='#54ec85';ctx.lineWidth=2;ctx.stroke();ctx.setLineDash([]);}
}
function hit(p){for(const s of [...R.contours(M)].reverse()){const [x,y]=localPoint(s.e,p);if(Math.hypot(x/s.e.rx,y/s.e.ry)<=1.06)return s;}return null;}
function edit(fn){if(!loaded||busy||drag||!shape())return;remember();fn(shape().e);R.sync(shape());changed();}
function setAdd(){if(!loaded||busy||drag)return;addMode=!addMode;render();message(addMode?'按住左键拖动补画轮廓，Esc 取消。':'已退出补画模式。');}
stage.addEventListener('contextmenu',e=>e.preventDefault());
stage.addEventListener('pointerdown',e=>{
  if(!loaded||busy||drag)return;const px=pixel(e),p=unproject(px);stage.focus();e.preventDefault();
  if(e.button===2||e.button===1||e.button===0&&e.shiftKey){drag={type:'pan',pixel:px,view:{...view},pointerId:e.pointerId};}
  else if(e.button!==0)return;
  else if(addMode){if(p[0]<0||p[1]<0||p[0]>image.width||p[1]>image.height)return;drag={type:'add',start:p,pointerId:e.pointerId};}
  else if($('overlays').checked&&!hidden){
    const h=handles(shape()).find(h=>Math.hypot(h.p[0]-px[0],h.p[1]-px[1])<=10);const s=h?shape():hit(p);if(!s){selected=null;render();return;}
    selected=s.id;drag={type:h?.type||'move',handle:h,e:R.clone(s.e),start:p,before:R.clone(M),pointerId:e.pointerId,moved:false};
  }else return;
  stage.setPointerCapture(e.pointerId);render();
});
stage.addEventListener('pointermove',e=>{
  if(!drag||e.pointerId!==drag.pointerId)return;const px=pixel(e),p=unproject(px);
  if(drag.type==='pan'){view.cx=drag.view.cx-(px[0]-drag.pixel[0])/view.scale;view.cy=drag.view.cy-(px[1]-drag.pixel[1])/view.scale;draw();return;}
  if(drag.type==='add'){const x=Math.max(0,Math.min(image.width,p[0])),y=Math.max(0,Math.min(image.height,p[1]));drag.preview={cx:(x+drag.start[0])/2,cy:(y+drag.start[1])/2,rx:Math.abs(x-drag.start[0])/2,ry:Math.abs(y-drag.start[1])/2,angle:0};draw();return;}
  if(Math.hypot(p[0]-drag.start[0],p[1]-drag.start[1])<.01)return;const old=drag.e,next=R.clone(old);drag.moved=true;
  if(drag.type==='move'){next.cx+=p[0]-drag.start[0];next.cy+=p[1]-drag.start[1];}
  if(drag.type==='rotate')next.angle+=Math.atan2(p[1]-old.cy,p[0]-old.cx)-Math.atan2(drag.start[1]-old.cy,drag.start[0]-old.cx);
  if(drag.type==='resize'){
    const [x,y]=localPoint(old,p),{dx,dy,k}=drag.handle;let ox=0,oy=0;
    if(dx){const anchor=-dx*old.rx*k,tip=dx>0?Math.max(anchor+k,x):Math.min(anchor-k,x);next.rx=Math.abs(tip-anchor)/(2*k);ox=(tip+anchor)/2;}
    if(dy){const anchor=-dy*old.ry*k,tip=dy>0?Math.max(anchor+k,y):Math.min(anchor-k,y);next.ry=Math.abs(tip-anchor)/(2*k);oy=(tip+anchor)/2;}
    [next.cx,next.cy]=ellipsePoint(old,ox,oy);
  }
  shape().e=next;R.sync(shape());M.completed=false;render();
});
function endDrag(cancel=false){
  if(!drag)return;const d=drag;drag=null;try{stage.releasePointerCapture(d.pointerId);}catch{}
  if(cancel){if(d.before)M=d.before;render();return;}
  if(d.type==='add'&&d.preview?.rx>=2&&d.preview?.ry>=2){remember();selected=R.add(M,d.preview).id;addMode=false;changed();}
  else if(d.moved){undoStack.push({model:d.before,selected});if(undoStack.length>60)undoStack.shift();redoStack=[];changed();}
  else render();
}
stage.addEventListener('pointerup',()=>endDrag());stage.addEventListener('pointercancel',()=>endDrag(true));stage.addEventListener('lostpointercapture',()=>{if(drag)endDrag(true);});
function undo(redo=false){if(!loaded||busy||drag)return;const from=redo?redoStack:undoStack,to=redo?undoStack:redoStack,s=from.pop();if(!s)return;to.push({model:R.clone(M),selected});M=s.model;selected=s.selected;addMode=false;changed();}
function remove(){if(!shape()||busy||drag)return;remember();M.shapes=M.shapes.filter(s=>s.id!==selected);selected=R.contours(M)[0]?.id||null;changed();}
async function confirm(){
  if(!loaded||busy||drag||addMode)return;if(!R.contours(M).length){message('本图没有圆桌轮廓，请补画后再确认。','warn');return;}
  setBusy(true);try{M.completed=true;dirty=true;version++;storeDraft();render();if(!await save()){M.completed=false;storeDraft();render();return;}if(current<rows.length-1)await load(current+1);else message('本图已确认，已到最后一张。'+(rows.every(r=>r.checked)?'全部图片审核完成。':'队列中还有待审核图片。'));}catch(e){message(e.message,'error');}finally{setBusy(false);}
}
function exportDraft(){if(!loaded)return;const blob=new Blob([JSON.stringify(R.output(M),null,2)],{type:'application/json'}),url=URL.createObjectURL(blob),a=document.createElement('a');a.href=url;a.download=file().id.split('/').pop().replace(/\.json$/,'_草稿.json');a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);}
for(const key of ['cx','cy','rx','ry','angle'])$(key).addEventListener('change',()=>{
  const value=Number($(key).value);if($(key).value.trim()===''||!Number.isFinite(value)||Math.abs(value)>10000||(['rx','ry'].includes(key)&&value<.5)){message('请输入有效数值，半轴至少为 0.5。','warn');$(key).value='';render();return;}edit(e=>e[key]=key==='angle'?value*Math.PI/180:value);
});
function scaleContour(factor){edit(e=>{e.rx=Math.max(.5,e.rx*factor);e.ry=Math.max(.5,e.ry*factor);});}
function rotateContour(deg){edit(e=>e.angle+=deg*Math.PI/180);}
$('smaller').onclick=()=>scaleContour(.98);$('larger').onclick=()=>scaleContour(1.02);$('rotateLeft').onclick=()=>rotateContour(-1);$('rotateRight').onclick=()=>rotateContour(1);$('circle').onclick=()=>edit(e=>{e.rx=e.ry=(e.rx+e.ry)/2;});
$('add').onclick=setAdd;$('remove').onclick=remove;$('undo').onclick=()=>undo();$('redo').onclick=()=>undo(true);$('confirm').onclick=confirm;$('skip').onclick=()=>navigate(current+1);
$('prevImage').onclick=()=>navigate(current-1);$('nextImage').onclick=()=>navigate(current+1);$('save').onclick=save;$('fit').onclick=fit;$('overlays').onchange=draw;$('export').onclick=exportDraft;
$('search').oninput=renderList;$('filter').onchange=renderList;
$('chooseDirectory').onclick=$('start').onclick=()=>openDataset(null,true);$('pathDirectory').onclick=()=>{$('pathDialog').showModal();$('directoryPath').focus();};
$('openPath').onclick=()=>{const p=$('directoryPath').value.trim();if(p){$('pathDialog').close();openDataset(p);}};
$('directoryPath').onkeydown=e=>{if(e.key==='Enter'){e.preventDefault();$('openPath').click();}};
window.addEventListener('keydown',e=>{
  const key=e.key.toLowerCase(),cmd=e.metaKey||e.ctrlKey;
  if(cmd&&key==='s'){e.preventDefault();if(['cx','cy','rx','ry','angle'].includes(e.target.id))e.target.blur();save();return;}
  if(['INPUT','SELECT','TEXTAREA'].includes(e.target.tagName)||$('pathDialog').open)return;
  if(cmd&&key==='z'){e.preventDefault();undo(e.shiftKey);return;}if(cmd&&key==='y'){e.preventDefault();undo(true);return;}
  if(!loaded||busy)return;
  if(key==='escape'){if(drag)endDrag(true);addMode=false;render();return;}
  if(drag)return;
  if(key==='h'){hidden=true;draw();return;}
  if(key===' '||key==='enter'){e.preventDefault();if(!e.repeat)confirm();return;}
  if(key==='b'&&!e.repeat){setAdd();return;}if(key==='delete'||key==='backspace'){e.preventDefault();remove();return;}
  if(key==='f'){fit();return;}if(key==='['){navigate(current-1);return;}if(key===']'){navigate(current+1);return;}
  if(['arrowleft','arrowright','arrowup','arrowdown'].includes(key)){e.preventDefault();const d=e.shiftKey?10:1;edit(a=>{a.cx+=(key==='arrowright'?d:key==='arrowleft'?-d:0);a.cy+=(key==='arrowdown'?d:key==='arrowup'?-d:0);});return;}
  if(key==='-'||key==='_')scaleContour(e.shiftKey?0.9:0.98);if(key==='+'||key==='=')scaleContour(e.shiftKey?1.1:1.02);
  if(key===','||key==='<')rotateContour(e.shiftKey?-10:-1);if(key==='.'||key==='>')rotateContour(e.shiftKey?10:1);
});
window.addEventListener('keyup',e=>{if(e.key.toLowerCase()==='h'){hidden=false;draw();}});
window.addEventListener('blur',()=>{hidden=false;endDrag(true);draw();});
window.addEventListener('beforeunload',e=>{if(drag?.before&&drag.moved){dirty=true;storeDraft();}if(dirty){storeDraft();e.preventDefault();e.returnValue='';}});
new ResizeObserver(()=>{if(loaded)fit();}).observe(stage);
(async()=>{try{config=await request('/api/config');for(const p of config.presets){const b=document.createElement('button');b.textContent=p.name;b.onclick=()=>openDataset(p.path);$('presets').append(b);}renderControls();const explicitPath=new URLSearchParams(window.location.search).get('path');if(explicitPath||config.default)await openDataset(explicitPath||config.default);}catch(e){message('本地服务不可用：'+e.message,'error');}})();
