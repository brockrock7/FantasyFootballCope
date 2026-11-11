const assert=require('node:assert/strict');
const {searchPackages}=require('../suggested_trades.js');
(async()=>{
 const own=Array.from({length:5},(_,i)=>({id:i,name:'A'+i,projections:{1:{points:10+i}}}));
 const other=own.map(p=>({...p,id:p.id+10}));
 const results=await searchPackages(own,other,row=>({a:row.send.length+row.receive.length,b:row.send.length-row.receive.length}));
 const sizes=new Set(),keys=new Set();
 for(const row of results){
   assert(row.send.length>=1&&row.send.length<=4&&row.receive.length>=1&&row.receive.length<=4);
   assert.equal(new Set(row.send.map(p=>p.id)).size,row.send.length);
   assert.equal(new Set(row.receive.map(p=>p.id)).size,row.receive.length);
   sizes.add(row.send.length+':'+row.receive.length);
   const key=row.send.map(p=>p.id).sort().join(',')+'|'+row.receive.map(p=>p.id).sort().join(',');assert(!keys.has(key));keys.add(key);
 }
 assert.equal(sizes.size,16,'every 1–4 package-size pairing is evaluated');
 assert.equal(results.filter(r=>r.send.length===1&&r.receive.length===1).length,25,'all singles evaluated');
 assert.deepEqual(await searchPackages(own,other,()=>{throw Error('cancelled search should not evaluate');},{cancelled:()=>true}),[]);
 console.log('PASS: all 16 package-size pairs, exhaustive singles, distinct players, deduplicated trades and cancellation; candidates:',results.length);
})().catch(error=>{console.error(error);process.exitCode=1;});
