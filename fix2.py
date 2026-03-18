src=open('kubers_calling.py',encoding='utf-8').read() 
old1="    return (o[a.status_tier||'PASS']||5)-(o[b.status_tier||'PASS']||5);" 
new1="    const td=(o[a.status_tier||'PASS']||5)-(o[b.status_tier||'PASS']||5); if(td!==0) return td; return (+(b.z||0))-(+(a.z||0));" 
old2="  body.innerHTML=positions.map(p=>{" 
new2="  let tot=0; positions.forEach(p=>{tot+=(p.current_price-p.entry_price)*p.qty*(p.direction==='LONG'?1:-1);}); const tc=tot>=0?'#00c878':'#ff4d4d'; body.innerHTML='<div style=\"padding:6px 10px;border-bottom:1px solid #333;margin-bottom:6px;display:flex;align-items:center;gap:8px\"><span style=\"color:#888;font-size:10px\">OPEN P&L</span><span style=\"font-size:18px;font-weight:700;color:'+tc+'\">'+(tot>=0?'+':'')+'\u20b9'+Math.abs(tot).toFixed(0)+'</span><span style=\"color:#888;font-size:10px;margin-left:auto\">'+positions.length+' open</span></div>'+positions.map(p=>{" 
src=src.replace(old1,new1).replace(old2,new2) 
open('kubers_calling.py','w',encoding='utf-8').write(src) 
open('kubers_calling.py','w',encoding='utf-8').write(src) 
print('FIXED') 
