(function(root){
  const escape=value=>String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  function classifySuggestion(a,b,mode){
    if(mode==='real')return a>=.5&&b>=.5?{a,b}:null;
    return a<=-.1&&a>=-3&&b<=-.1&&b>=-3?{a:Math.abs(a)*1.12+.1,b:Math.abs(b)*1.12+.1}:null;
  }
  function hasOnlyContributors(data,evaluation){
    // Remove each incoming player from the completed trade's receiving roster.
    // A starting assignment alone is insufficient: tied alternatives can replace it.
    return [evaluation.a,evaluation.b].every(side=>{
      const sent=new Set(side.sent.map(player=>String(player.id)));
      const roster=side.team.players.filter(player=>!sent.has(String(player.id))).concat(side.received);
      return side.received.every(player=>{
        const weeks=side.weeks.filter(row=>row.after.lineup.some(slot=>slot.player&&String(slot.player.id)===String(player.id)));
        if(!weeks.length)return false;
        const without=roster.filter(candidate=>String(candidate.id)!==String(player.id));
        const marginal=weeks.reduce((sum,row)=>sum+row.after.total-root.TradeCalculator.withReplacements(without,data.lineup_slots||[],row.week,data.free_agents).total,0);
        return marginal>1e-6;
      });
    });
  }
  const packageKey=row=>row.send.map(p=>String(p.id)).sort().join(',')+'|'+row.receive.map(p=>String(p.id)).sort().join(',');
  const average=p=>{const rows=Object.values(p.projections||{}).filter(row=>row.available!==false&&Number.isFinite(row.points));return rows.length?rows.reduce((sum,row)=>sum+row.points,0)/rows.length:0;};
  function seeds(rows){
    const result=[];
    for(const score of [r=>Math.min(r.a,r.b),r=>r.a,r=>-Math.abs(r.a+1)-Math.abs(r.b+1)]){
      const best=[...rows].sort((a,b)=>score(b)-score(a)).find(row=>!result.includes(row));if(best)result.push(best);
    }return result;
  }
  async function searchPackages(own,other,evaluate,{cancelled=()=>false,onBatch=()=>{}}={}){
    const buckets=new Map(),seen=new Set(),results=[];let count=0,lastYield=performance.now();
    async function assess(candidate,bucket){
      if(cancelled())return;
      const key=packageKey(candidate);if(seen.has(key))return;seen.add(key);
      const row={...candidate,...evaluate(candidate)};bucket.push(row);if(row.contributes!==false)results.push(row);
      count++;
      if(count%24===0||performance.now()-lastYield>=16){onBatch(count);await new Promise(resolve=>setTimeout(resolve,0));lastYield=performance.now();}
    }
    for(let a=1;a<=Math.min(4,own.length);a++)for(let b=1;b<=Math.min(4,other.length);b++){
      if(cancelled())return [];const bucket=[];buckets.set(a+':'+b,bucket);
      if(a===1&&b===1){for(const send of own)for(const receive of other){if(cancelled())return [];await assess({send:[send],receive:[receive]},bucket);}continue;}
      const candidates=new Map();
      for(const [pa,pb,direction,pool] of [[a-1,b,'send',own],[a,b-1,'receive',other]]){
        for(const parent of seeds(buckets.get(pa+':'+pb)||[])){
          const used=new Set(parent[direction].map(p=>String(p.id)));
          const next=pool.filter(p=>!used.has(String(p.id))).map(player=>({send:parent.send,receive:parent.receive,[direction]:[...parent[direction],player]}));
          next.sort((x,y)=>Math.abs(x.send.reduce((v,p)=>v+average(p),0)-x.receive.reduce((v,p)=>v+average(p),0))-Math.abs(y.send.reduce((v,p)=>v+average(p),0)-y.receive.reduce((v,p)=>v+average(p),0)));
          next.slice(0,4).forEach(row=>candidates.set(packageKey(row),row));
        }
      }
      for(const candidate of candidates.values()){if(cancelled())return [];await assess(candidate,bucket);}
    }
    onBatch(count);return results;
  }
  function mount(host,D,onChange){
    const data=D.trade_calculator,ids=Object.keys(data.teams),cache=new Map();
    let mode=null,revision=0,rows=[],computingTeam=null,progress=0,state={team:ids[0],partners:[],positions:[],sort:'a',descending:true};
    const abbr=id=>D.team_abbrevs[data.teams[id]?.name]||data.teams[id]?.name||id;
    host.innerHTML='<div class="suggestions-content"></div>';
    const content=host.querySelector('.suggestions-content');
    function enter(nextMode='decoy'){mode=nextMode==='real'?'real':'decoy';render();compute();}
    function button(label,kind,value,active){return `<button type="button" data-kind="${kind}" data-value="${escape(value)}" class="team-button${active?' active':''}" aria-pressed="${active}">${escape(label)}</button>`;}
    function render(){
      const positions=[...new Set(Object.values(data.teams).flatMap(t=>t.players.map(p=>p.position)))];
      content.innerHTML=`<h2>Suggested Trades</h2><p>Candidate trades with 1–4 players per side, including byes, replacement players, and scheduled head-to-head effects. Every included player must improve their receiving team’s projected lineup. Values are balance points per remaining week.</p><div class="toolbar"><span>Your team</span><div class="team-buttons">${ids.map(id=>button(abbr(id),'team',id,id===state.team)).join('')}</div></div><div class="toolbar"><span>Trade partners · select multiple</span><div class="team-buttons">${button('All','partners','all',!state.partners.length)}${ids.filter(id=>id!==state.team).map(id=>button(abbr(id),'partners',id,state.partners.includes(id))).join('')}</div></div><div class="toolbar"><span>Allowed positions · select multiple</span><div class="team-buttons">${button('All','positions','all',!state.positions.length)}${positions.map(pos=>button(pos,'positions',pos,state.positions.includes(pos))).join('')}</div></div><p class="suggestions-status" role="status"></p><div class="scroll"><table class="suggestions-table"></table></div>`;
      draw();
    }
    function draw(){
      if(!mode)return;
      const filtered=rows.filter(row=>(!state.partners.length||state.partners.includes(row.partner))&&(!state.positions.length||[...row.send,...row.receive].map(player=>player.position).every(p=>state.positions.includes(p)))).map(row=>{const values=classifySuggestion(row.a,row.b,mode);return values?{...row,...values,total:values.a+values.b}:null;}).filter(Boolean);
      filtered.sort((a,b)=>{const x=a[state.sort],y=b[state.sort];return (state.descending?-1:1)*(typeof x==='number'?x-y:String(x).localeCompare(String(y)));});
      const columns=[['sendName','Send'],['receiveName','Receive'],['partnerName','Partner'],['a','Your gain'],['b','Partner gain'],['total','Combined']];
      content.querySelector('table').innerHTML=`<thead><tr>${columns.map(([key,label])=>`<th><button type="button" data-sort="${key}">${label}${state.sort===key?(state.descending?' ▼':' ▲'):''}</button></th>`).join('')}</tr></thead><tbody>${filtered.slice(0,150).map(row=>`<tr><td>${row.send.map(player=>`<div class="suggestion-player">${escape(player.name)}<small>${escape(player.position)}</small></div>`).join('')}</td><td>${row.receive.map(player=>`<div class="suggestion-player">${escape(player.name)}<small>${escape(player.position)}</small></div>`).join('')}</td><td>${escape(row.partnerName)}</td><td>+${row.a.toFixed(2)}</td><td>+${row.b.toFixed(2)}</td><td>+${row.total.toFixed(2)}</td></tr>`).join('')}</tbody>`;
      content.querySelector('.suggestions-status').textContent=computingTeam===state.team?`Evaluating roster fit… ${progress} packages · ${filtered.length} matches so far`:filtered.length?`${filtered.length} matching trades${filtered.length>150?' · showing the best 150':''}`:'No matching trades in the current projection set. Try other teams or position filters.';
    }
    async function compute(){
      const team=state.team;if(computingTeam===team)return;
      const run=++revision;
      if(cache.has(team)){computingTeam=null;rows=cache.get(team);draw();return;}
      computingTeam=team;progress=0;rows=[];draw();const result=[],own=data.teams[team].players,n=data.weeks.length;
      if(!n){computingTeam=null;draw();return;}
      for(const partner of ids.filter(id=>id!==team)){
        const offset=progress;
        const found=await searchPackages(own,data.teams[partner].players,candidate=>{
          const evaluation=TradeCalculator.evaluateTrade(data,{teamA:team,teamB:partner,sendA:candidate.send.map(p=>String(p.id)),sendB:candidate.receive.map(p=>String(p.id)),startWeek:data.weeks[0],endWeek:data.weeks.at(-1)});
          const a=evaluation.a.delta/n,b=evaluation.b.delta/n;
          const contributes=!!(classifySuggestion(a,b,'real')||classifySuggestion(a,b,'decoy'))&&hasOnlyContributors(data,evaluation);
          return {partner,sendName:candidate.send.map(p=>p.name).join(', '),receiveName:candidate.receive.map(p=>p.name).join(', '),partnerName:abbr(partner),a,b,contributes};
        },{cancelled:()=>run!==revision,onBatch:count=>{progress=offset+count;content.querySelector('.suggestions-status').textContent=`Evaluating roster fit… ${progress} packages`;}});
        if(run!==revision)return;result.push(...found);rows=result;draw();
      }
      if(run!==revision)return;computingTeam=null;cache.set(team,result);rows=result;draw();
    }
    content.addEventListener('click',event=>{const button=event.target.closest('button');if(!button)return;
      if(button.dataset.sort){const key=button.dataset.sort;state.descending=state.sort===key?!state.descending:true;state.sort=key;draw();onChange();return;}
      const {kind,value}=button.dataset;if(!kind)return;
      if(kind==='team'){state.team=value;state.partners=state.partners.filter(id=>id!==value);render();compute();}
      else {state[kind]=value==='all'?[]:state[kind].includes(value)?state[kind].filter(x=>x!==value):[...state[kind],value];render();}
      onChange();
    });
    return {enter,getMode:()=>mode,getState:()=>({...state}),setState(next){if(!next)return;state={...state,...next,team:ids.includes(next.team)?next.team:ids[0],partners:Array.isArray(next.partners)?next.partners.filter(id=>ids.includes(id)):[],positions:Array.isArray(next.positions)?next.positions:[]};if(mode){render();compute();}},selectTeam(name){const id=ids.find(id=>data.teams[id].name===name);if(id){state.team=id;state.partners=state.partners.filter(value=>value!==id);if(mode){render();compute();}}}};
  }
  if(typeof module!=='undefined')module.exports={classifySuggestion,searchPackages,hasOnlyContributors};
  root.SuggestedTrades={mount,classifySuggestion,searchPackages,hasOnlyContributors};
})(typeof window==='undefined'?globalThis:window);
