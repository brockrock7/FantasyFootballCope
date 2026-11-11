(function(root){
  const escape=value=>String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  function classifySuggestion(a,b,mode){
    if(mode==='real')return a>=.5&&b>=.5?{a,b}:null;
    return a<=-.1&&a>=-3&&b<=-.1&&b>=-3?{a:Math.abs(a)*1.12+.1,b:Math.abs(b)*1.12+.1}:null;
  }
  function mount(host,D,onChange){
    const data=D.trade_calculator,ids=Object.keys(data.teams),cache=new Map();
    let mode=null,revision=0,rows=[],state={team:ids[0],partners:[],positions:[],sort:'a',descending:true};
    const abbr=id=>D.team_abbrevs[data.teams[id]?.name]||data.teams[id]?.name||id;
    host.innerHTML='<dialog class="suggestions-gate"><form><h3>Suggested Trades</h3><label>Three-digit code<input name="code" inputmode="numeric" pattern="[0-9]{3}" maxlength="3" required autocomplete="off"></label><button type="submit">Continue</button><button type="button" class="gate-cancel">Cancel</button></form></dialog><div class="suggestions-content" hidden></div>';
    const gate=host.querySelector('dialog'),content=host.querySelector('.suggestions-content');
    function enter(){mode=null;content.hidden=true;gate.querySelector('input').value='';if(!gate.open)gate.showModal();gate.querySelector('input').focus();}
    gate.querySelector('form').addEventListener('submit',event=>{event.preventDefault();mode=gate.querySelector('input').value==='429'?'real':'decoy';gate.close();content.hidden=false;render();compute();});
    gate.querySelector('.gate-cancel').onclick=()=>{gate.close();document.querySelector('[data-trades-view="history"]').click();};
    gate.addEventListener('cancel',()=>document.querySelector('[data-trades-view="history"]').click());
    function button(label,kind,value,active){return `<button type="button" data-kind="${kind}" data-value="${escape(value)}" class="team-button${active?' active':''}" aria-pressed="${active}">${escape(label)}</button>`;}
    function render(){
      const positions=[...new Set(Object.values(data.teams).flatMap(t=>t.players.map(p=>p.position)))];
      content.innerHTML=`<h2>Suggested Trades</h2><p>Projected 1-for-1 trades, including byes, replacement players, and scheduled head-to-head effects. Values are balance points per remaining week.</p><div class="toolbar"><span>Your team</span><div class="team-buttons">${ids.map(id=>button(abbr(id),'team',id,id===state.team)).join('')}</div></div><div class="toolbar"><span>Trade partners</span><div class="team-buttons">${button('All','partners','all',!state.partners.length)}${ids.filter(id=>id!==state.team).map(id=>button(abbr(id),'partners',id,state.partners.includes(id))).join('')}</div></div><div class="toolbar"><span>Positions involved</span><div class="team-buttons">${button('All','positions','all',!state.positions.length)}${positions.map(pos=>button(pos,'positions',pos,state.positions.includes(pos))).join('')}</div></div><p class="suggestions-status" role="status"></p><div class="scroll"><table class="suggestions-table"></table></div>`;
      draw();
    }
    function draw(){
      if(!mode)return;
      const filtered=rows.filter(row=>(!state.partners.length||state.partners.includes(row.partner))&&(!state.positions.length||[row.send.position,row.receive.position].some(p=>state.positions.includes(p)))).map(row=>{const values=classifySuggestion(row.a,row.b,mode);return values?{...row,...values,total:values.a+values.b}:null;}).filter(Boolean);
      filtered.sort((a,b)=>{const x=a[state.sort],y=b[state.sort];return (state.descending?-1:1)*(typeof x==='number'?x-y:String(x).localeCompare(String(y)));});
      const columns=[['sendName','Send'],['receiveName','Receive'],['partnerName','Partner'],['a','Your gain'],['b','Partner gain'],['total','Combined']];
      content.querySelector('table').innerHTML=`<thead><tr>${columns.map(([key,label])=>`<th><button type="button" data-sort="${key}">${label}${state.sort===key?(state.descending?' ▼':' ▲'):''}</button></th>`).join('')}</tr></thead><tbody>${filtered.slice(0,150).map(row=>`<tr><td>${escape(row.sendName)}<small>${escape(row.send.position)}</small></td><td>${escape(row.receiveName)}<small>${escape(row.receive.position)}</small></td><td>${escape(row.partnerName)}</td><td>+${row.a.toFixed(2)}</td><td>+${row.b.toFixed(2)}</td><td>+${row.total.toFixed(2)}</td></tr>`).join('')}</tbody>`;
      content.querySelector('.suggestions-status').textContent=filtered.length?`${filtered.length} matching trades${filtered.length>150?' · showing the best 150':''}`:'No matching trades in the current projection set. Try other teams or position filters.';
    }
    async function compute(){
      const run=++revision,team=state.team;
      if(cache.has(team)){rows=cache.get(team);draw();return;}
      rows=[];const result=[],own=data.teams[team].players,n=data.weeks.length;
      if(!n){draw();return;}
      let count=0;
      for(const partner of ids.filter(id=>id!==team))for(const send of own)for(const receive of data.teams[partner].players){
        if(run!==revision)return;
        const evaluation=TradeCalculator.evaluateTrade(data,{teamA:team,teamB:partner,sendA:[String(send.id)],sendB:[String(receive.id)],startWeek:data.weeks[0],endWeek:data.weeks.at(-1)});
        result.push({partner,send,receive,sendName:send.name,receiveName:receive.name,partnerName:abbr(partner),a:evaluation.a.delta/n,b:evaluation.b.delta/n});
        if(++count%24===0){content.querySelector('.suggestions-status').textContent=`Evaluating roster fit… ${count} trades`;await new Promise(resolve=>setTimeout(resolve,0));}
      }
      if(run!==revision)return;cache.set(team,result);rows=result;draw();
    }
    content.addEventListener('click',event=>{const button=event.target.closest('button');if(!button)return;
      if(button.dataset.sort){const key=button.dataset.sort;state.descending=state.sort===key?!state.descending:true;state.sort=key;draw();onChange();return;}
      const {kind,value}=button.dataset;if(!kind)return;
      if(kind==='team'){state.team=value;state.partners=state.partners.filter(id=>id!==value);render();compute();}
      else {state[kind]=value==='all'?[]:state[kind].includes(value)?state[kind].filter(x=>x!==value):[...state[kind],value];render();}
      onChange();
    });
    return {enter,getState:()=>({...state}),setState(next){if(!next)return;state={...state,...next,team:ids.includes(next.team)?next.team:ids[0],partners:Array.isArray(next.partners)?next.partners.filter(id=>ids.includes(id)):[],positions:Array.isArray(next.positions)?next.positions:[]};if(mode){render();compute();}},selectTeam(name){const id=ids.find(id=>data.teams[id].name===name);if(id){state.team=id;state.partners=state.partners.filter(value=>value!==id);if(mode){render();compute();}}}};
  }
  if(typeof module!=='undefined')module.exports={classifySuggestion};
  root.SuggestedTrades={mount,classifySuggestion};
})(typeof window==='undefined'?globalThis:window);
