/* Smooth display; exact source coordinates survive review until a contour is edited. */
'use strict';
const RoundModel = (() => {
  const labels = ['circle','round','round_table','table_circle','圆桌','圆桌轮廓'];
  const clone = v => JSON.parse(JSON.stringify(v));
  const signature = shapes => {let h=2166136261;const s=JSON.stringify(shapes);for(let i=0;i<s.length;i++)h=Math.imul(h^s.charCodeAt(i),16777619);return (h>>>0).toString(16);};
  const valid = p => Array.isArray(p)&&p.length===2&&p.every(Number.isFinite);
  // Pivoted least squares in normalized image coordinates fits ellipse polygons even
  // when their vertices are spaced by arc length rather than parameter angle.
  function solve(a,b){
    const rows=a.map((r,i)=>[...r,b[i]]),n=b.length;
    for(let i=0;i<n;i++){
      let k=i;for(let j=i+1;j<n;j++)if(Math.abs(rows[j][i])>Math.abs(rows[k][i]))k=j;
      if(Math.abs(rows[k][i])<1e-10)return null;
      [rows[i],rows[k]]=[rows[k],rows[i]];const d=rows[i][i];for(let j=i;j<=n;j++)rows[i][j]/=d;
      for(let k=0;k<n;k++)if(k!==i){const v=rows[k][i];for(let j=i;j<=n;j++)rows[k][j]-=v*rows[i][j];}
    }return rows.map(r=>r[n]);
  }
  function ellipse(shape){
    const pts=shape.points;
    if(shape.shape_type==='circle'&&pts.length===2){const r=Math.max(.5,Math.hypot(pts[1][0]-pts[0][0],pts[1][1]-pts[0][1]));return {cx:pts[0][0],cy:pts[0][1],rx:r,ry:r,angle:0};}
    const n=pts.length,cx=pts.reduce((s,p)=>s+p[0],0)/n,cy=pts.reduce((s,p)=>s+p[1],0)/n;
    let xx=0,xy=0,yy=0;pts.forEach(p=>{const x=p[0]-cx,y=p[1]-cy;xx+=x*x/n;xy+=x*y/n;yy+=y*y/n;});
    const scale=Math.sqrt(xx+yy),matrix=Array.from({length:5},()=>Array(5).fill(0)),rhs=Array(5).fill(0);
    if(n>=5&&scale>.5){
      pts.forEach(p=>{const x=(p[0]-cx)/scale,y=(p[1]-cy)/scale,r=[x*y,y*y,x,y,1];for(let i=0;i<5;i++){rhs[i]-=r[i]*x*x;for(let j=0;j<5;j++)matrix[i][j]+=r[i]*r[j];}});
      const v=solve(matrix,rhs);
      if(v){
        const [b,c,d,e,f]=v,det=c-b*b/4;
        if(det>1e-8&&c>0){
          const ox=(b*e/2-c*d)/(2*det),oy=(b*d/2-e)/(2*det),level=-(ox*ox+b*ox*oy+c*oy*oy+d*ox+e*oy+f);
          const delta=Math.hypot(1-c,b),lo=(1+c-delta)/2,hi=(1+c+delta)/2;
          if(level>0&&lo>1e-8){const fit={cx:cx+ox*scale,cy:cy+oy*scale,rx:scale*Math.sqrt(level/lo),ry:scale*Math.sqrt(level/hi),angle:delta<1e-8?0:.5*Math.atan2(-b,c-1)};
            if(Object.values(fit).every(Number.isFinite)&&fit.rx<scale*10&&fit.ry>=.5)return fit;}
        }
      }
    }
    const delta=Math.hypot(xx-yy,2*xy);return {cx,cy,rx:Math.sqrt(Math.max(.25,xx+yy+delta)),ry:Math.sqrt(Math.max(.25,xx+yy-delta)),angle:delta<.02?0:.5*Math.atan2(2*xy,xx-yy)};
  }
  function polygon(e,n=96,start=0,winding=1){const c=Math.cos(e.angle),s=Math.sin(e.angle);return Array.from({length:n},(_,i)=>{const a=start+winding*i*2*Math.PI/n,x=e.rx*Math.cos(a),y=e.ry*Math.sin(a);return [e.cx+x*c-y*s,e.cy+x*s+y*c].map(v=>Math.round(v*1000)/1000);});}
  function load(doc){
    const source=clone(doc),q=source.round_table_review||{},trusted=q.shape_signature===signature(source.shapes);
    const shapes=source.shapes.map((shape,i)=>{
      const editable=labels.includes(shape.label)&&Array.isArray(shape.points)&&shape.points.every(valid)&&(shape.shape_type==='circle'?shape.points.length===2:(!shape.shape_type||shape.shape_type==='polygon')&&shape.points.length>=3);
      if(!editable)return {id:'shape-'+i,shape,e:null};
      const e=ellipse(shape),first=shape.points[0],dx=first[0]-e.cx,dy=first[1]-e.cy,c=Math.cos(e.angle),s=Math.sin(e.angle);
      const area=shape.points.reduce((sum,p,i)=>{const next=shape.points[(i+1)%shape.points.length];return sum+p[0]*next[1]-p[1]*next[0];},0);
      return {id:'shape-'+i,shape,e,count:shape.shape_type==='circle'?96:shape.points.length,start:Math.atan2((-dx*s+dy*c)/e.ry,(dx*c+dy*s)/e.rx),winding:area<0?-1:1};
    });
    return {source,shapes,completed:trusted&&q.completed===true,stale:!!source.round_table_review&&!trusted,serial:shapes.length};
  }
  const contours=m=>m.shapes.filter(s=>s.e);
  function sync(s){s.shape.points=polygon(s.e,s.count,s.start,s.winding);s.shape.shape_type='polygon';}
  function add(m,e){const shape={label:contours(m)[0]?.shape.label||'circle',points:polygon(e),group_id:null,shape_type:'polygon',flags:{}};const item={id:'new-'+m.serial++,shape,e:clone(e),count:96,start:0,winding:1};m.shapes.push(item);return item;}
  function output(m){const doc=clone(m.source);doc.shapes=clone(m.shapes.map(s=>s.shape));doc.checked=m.completed;doc.round_table_review={version:1,completed:m.completed,shape_signature:signature(doc.shapes)};return doc;}
  return {clone,signature,ellipse,polygon,load,contours,sync,add,output};
})();
if(typeof module!=='undefined')module.exports=RoundModel;
