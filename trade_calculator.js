/* Exact, bye-aware lineup comparisons. Embedded in the generated static dashboard. */
(function(root){
  'use strict';
  const idOf=player=>String(player.id);
  const slotName=value=>String(value||'').toUpperCase().replace(/\s/g,'').replace('DST','D/ST');
  const finite=value=>value!==null&&value!==undefined&&value!==''&&Number.isFinite(Number(value));
  function eligible(player,slot){
    const target=slotName(slot),explicit=player.eligible_slots;
    // ESPN slot eligibility is authoritative when provided, including an empty list.
    if(Array.isArray(explicit))return explicit.some(value=>slotName(value)===target);
    const position=slotName(player.position);
    if(target===position)return true;
    const groups={FLEX:['RB','WR','TE'],'RB/WR/TE':['RB','WR','TE'],'RB/WR':['RB','WR'],'WR/TE':['WR','TE'],OP:['QB','RB','WR','TE'],SUPERFLEX:['QB','RB','WR','TE'],'QB/RB/WR/TE':['QB','RB','WR','TE'],DP:['DE','DT','DL','LB','CB','S','DB'],DL:['DE','DT'],DB:['CB','S']};
    return (groups[target]||[]).includes(position);
  }
  function projection(player,week){
    if(Number(player.bye_week)===Number(week))return {points:0,available:false,source:'bye',reason:'Bye'};
    const row=player.projections?.[week];
    if(typeof row==='number')return {points:row,available:true,source:'espn_weekly'};
    if(!row||!finite(row.points))return {points:null,available:row?.available!==false,source:row?.source||'missing',reason:row?.reason||'Projection unavailable'};
    return {...row,points:Number(row.points),available:row.available!==false};
  }
  function optimizeLineup(players,slots,week){
    const seen=new Set(),unique=players.filter(player=>{const id=idOf(player);if(seen.has(id))return false;seen.add(id);return true;});
    const available=unique.map(player=>({player,projection:projection(player,week)})).filter(row=>row.projection.available&&finite(row.projection.points));
    const n=slots.length,m=available.length+n;
    if(!n)return {total:0,lineup:[],missing:[],fallback:[],unavailable:[]};
    // Rectangular Hungarian assignment: each slot gets one unique player or an
    // empty placeholder. This jointly optimizes FLEX and repeated position slots.
    const u=new Float64Array(n+1),v=new Float64Array(m+1),p=new Int32Array(m+1),way=new Int32Array(m+1);
    const cost=(i,j)=>j>=available.length?0:eligible(available[j].player,slots[i])?-available[j].projection.points-1e-9:1e9;
    for(let i=1;i<=n;i++){
      p[0]=i;let j0=0;
      const minv=new Float64Array(m+1).fill(Infinity),used=new Uint8Array(m+1);
      do{
        used[j0]=1;const i0=p[j0];let delta=Infinity,j1=0;
        for(let j=1;j<=m;j++)if(!used[j]){
          const cur=cost(i0-1,j-1)-u[i0]-v[j];
          if(cur<minv[j]){minv[j]=cur;way[j]=j0;}
          if(minv[j]<delta){delta=minv[j];j1=j;}
        }
        for(let j=0;j<=m;j++)if(used[j]){u[p[j]]+=delta;v[j]-=delta;}else minv[j]-=delta;
        j0=j1;
      }while(p[j0]!==0);
      do{const j1=way[j0];p[j0]=p[j1];j0=j1;}while(j0!==0);
    }
    const lineup=slots.map(slot=>({slot,player:null,points:0,source:'empty'}));
    for(let j=1;j<=available.length;j++)if(p[j]){
      const row=available[j-1];lineup[p[j]-1]={slot:slots[p[j]-1],player:row.player,points:row.projection.points,source:row.projection.source};
    }
    const missing=unique.filter(player=>{const row=projection(player,week);return row.source==='missing'||(row.available&&!finite(row.points));});
    const unavailable=unique.filter(player=>{const row=projection(player,week);return !row.available&&row.source!=='missing';});
    const fallback=lineup.filter(row=>row.player&&!['espn_weekly','espn'].includes(row.source));
    return {total:lineup.reduce((sum,row)=>sum+row.points,0),lineup,missing,fallback,unavailable};
  }
  function withReplacements(players,slots,week,freeAgents=[]){
    const best=optimizeLineup(players,slots,week),vacant=best.lineup.map((row,index)=>row.player?null:index).filter(index=>index!==null);
    if(vacant.length){
      const owned=new Set(players.map(idOf)),replacements=optimizeLineup(freeAgents.filter(player=>!owned.has(idOf(player))),vacant.map(index=>slots[index]),week);
      vacant.forEach((index,i)=>{best.lineup[index]=replacements.lineup[i];});
      best.fallback.push(...replacements.fallback);
      best.total=best.lineup.reduce((sum,row)=>sum+row.points,0);
    }
    return best;
  }
  function tradeRating(a,b,count){
    const threshold=.5*Math.max(1,count),sign=value=>Math.abs(value)<=threshold?0:Math.sign(value),x=sign(a),y=sign(b);
    return x===0&&y===0?'Even':x>0&&y>0?'Mutually beneficial':x<0&&y<0?'Mutually detrimental':'One-sided';
  }
  function evaluateTrade(data,state){
    const first=data.teams?.[state.teamA],second=data.teams?.[state.teamB];
    if(!first||!second||String(state.teamA)===String(state.teamB))return null;
    const weeks=(data.weeks||[]).filter(week=>week>=Number(state.startWeek)&&week<=Number(state.endWeek));
    const selectedA=new Set((state.sendA||[]).map(String)),selectedB=new Set((state.sendB||[]).map(String));
    const sentA=first.players.filter(player=>selectedA.has(idOf(player))),sentB=second.players.filter(player=>selectedB.has(idOf(player)));
    const nextA=first.players.filter(player=>!selectedA.has(idOf(player))).concat(sentB),nextB=second.players.filter(player=>!selectedB.has(idOf(player))).concat(sentA);
    function side(team,beforePlayers,afterPlayers,sent,received){
      const incoming=new Set(received.map(idOf));
      const rows=weeks.map(week=>{
        const before=withReplacements(beforePlayers,data.lineup_slots||[],week,data.free_agents),after=withReplacements(afterPlayers,data.lineup_slots||[],week,data.free_agents);
        const afterIds=new Set(after.lineup.filter(row=>row.player).map(row=>idOf(row.player)));
        const displaced=before.lineup.filter(row=>row.player&&!row.player.free_agent&&!afterIds.has(idOf(row.player))&&!sent.some(player=>idOf(player)===idOf(row.player))).map(row=>row.player);
        return {week,before,after,delta:after.total-before.total,displaced,incomingStarts:after.lineup.filter(row=>row.player&&incoming.has(idOf(row.player))).length};
      });
      return {team,sent,received,weeks:rows,before:rows.reduce((sum,row)=>sum+row.before.total,0),after:rows.reduce((sum,row)=>sum+row.after.total,0),delta:rows.reduce((sum,row)=>sum+row.delta,0),incomingStarts:rows.reduce((sum,row)=>sum+row.incomingStarts,0),incomingAvailableWeeks:rows.reduce((sum,row)=>sum+received.filter(player=>{const proj=projection(player,row.week);return proj.available&&finite(proj.points);}).length,0)};
    }
    const a=side(first,first.players,nextA,sentA,sentB),b=side(second,second.players,nextB,sentB,sentA);
    a.lineupDelta=a.delta;b.lineupDelta=b.delta;
    weeks.forEach((week,i)=>{const meeting=data.head_to_head?.[state.teamA]?.[week]===String(state.teamB)||data.head_to_head?.[state.teamB]?.[week]===String(state.teamA);a.weeks[i].opponentEffect=meeting?-b.weeks[i].delta:0;b.weeks[i].opponentEffect=meeting?-a.weeks[i].delta:0;a.weeks[i].balance=a.weeks[i].delta+a.weeks[i].opponentEffect;b.weeks[i].balance=b.weeks[i].delta+b.weeks[i].opponentEffect;});
    a.delta=a.weeks.reduce((sum,row)=>sum+row.balance,0);b.delta=b.weeks.reduce((sum,row)=>sum+row.balance,0);
    return {weeks,hasTrade:!!(sentA.length||sentB.length),a,b};
  }
  const escape=value=>String(value??'').replace(/[&<>"']/g,char=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[char]));
  const number=value=>Number(value).toFixed(1);
  const signed=value=>(Math.abs(value)<.05?'':value>0?'+':'')+number(Math.abs(value)<.05?0:value);
  const tone=value=>Math.abs(value)<.05?'neutral':value>0?'positive':'negative';
  const sourceLabel=source=>({espn_weekly:'ESPN weekly projection',espn:'ESPN weekly projection',espn_baseline:'ESPN average baseline',recent_projection:'Recent ESPN projection baseline',historical_ppg:'Historical scoring average baseline',bye:'Bye',missing:'No projection',empty:'Empty slot'}[source]||String(source||'Estimate').replace(/_/g,' '));
  function mount(container,dashboard,options={}){
    if(typeof container==='string')container=document.querySelector(container);
    if(!container)return null;
    const data=dashboard.trade_calculator||{},teams=data.teams||{},ids=Object.keys(teams),weeks=data.weeks||[];
    if(ids.length<2||!weeks.length||!data.lineup_slots?.length){container.innerHTML='<div class="notice">Trade comparisons are unavailable until remaining-week projections and league lineup slots are loaded.</div>';return {getState:()=>null,setState:()=>{},selectTeam:()=>{},evaluate:()=>null};}
    let state={teamA:ids[0],teamB:ids[1],sendA:[],sendB:[],startWeek:weeks[0],endWeek:weeks.at(-1),focusWeek:weeks[0],lineupView:"after"};
    let result=null;
    const abbrev=id=>dashboard.team_abbrevs?.[teams[id]?.name]||teams[id]?.name||id;
    const photo=player=>{const url=player.team_logo_url&&['D/ST','DST','DEF'].includes(player.position)?player.team_logo_url:player.photo_url;return /^https?:\/\//i.test(url||'')?`<img src="${escape(url)}" alt="" loading="lazy" referrerpolicy="no-referrer">`:'<span class="tc-photo-placeholder" aria-hidden="true">'+escape(player.position)+'</span>';};
    function normalize(next){
      const a=ids.includes(String(next.teamA))?String(next.teamA):state.teamA;
      const b=ids.includes(String(next.teamB))&&String(next.teamB)!==a?String(next.teamB):ids.find(id=>id!==a);
      const valid=(values,team)=>[...new Set((Array.isArray(values)?values:[]).map(String))].filter(id=>teams[team].players.some(player=>idOf(player)===id));
      const start=weeks.includes(Number(next.startWeek))?Number(next.startWeek):weeks[0],end=weeks.includes(Number(next.endWeek))?Number(next.endWeek):weeks.at(-1);
      return {teamA:a,teamB:b,sendA:valid(next.sendA,a),sendB:valid(next.sendB,b),startWeek:Math.min(start,end),endWeek:Math.max(start,end),focusWeek:weeks.includes(Number(next.focusWeek))?Number(next.focusWeek):start,lineupView:next.lineupView==='before'?'before':'after'};
    }
    function teamOptions(selected,exclude){return ids.filter(id=>id!==exclude).map(id=>`<option value="${escape(id)}"${id===selected?' selected':''}>${escape(teams[id].name)}</option>`).join('');}
    const weekOptions=selected=>weeks.map(week=>`<option value="${week}"${Number(selected)===week?' selected':''}>Week ${week}</option>`).join('');
    function renderShell(){
      container.classList.add('trade-calculator');
      container.innerHTML=`<div class="tc-intro"><div><h2>Trade Calculator</h2><p>Build a trade. See what changes in each team’s best weekly lineup.</p></div><button type="button" class="tc-reset">Clear trade</button></div>
      <div class="tc-rosters"><section class="tc-roster tc-own" aria-label="Your roster"><label class="tc-team-label" for="tcTeamA">Your team</label><select id="tcTeamA" class="tc-team" data-side="A">${teamOptions(state.teamA)}</select><div class="tc-roster-caption"><span>Select players to send</span><strong class="tc-selection-count" data-side="A"></strong></div><div class="tc-player-list" data-side="A"></div></section><section class="tc-roster tc-other" aria-label="Trade partner roster"><label class="tc-team-label" for="tcTeamB">Trade partner</label><select id="tcTeamB" class="tc-team" data-side="B">${teamOptions(state.teamB,state.teamA)}</select><div class="tc-roster-caption"><span>Select players to receive</span><strong class="tc-selection-count" data-side="B"></strong></div><div class="tc-player-list" data-side="B"></div></section></div>
      <section class="tc-evaluation" aria-labelledby="tcImpactTitle"><div class="tc-impact-heading"><div><span class="tc-eyebrow">Lineup impact</span><h3 id="tcImpactTitle">What does this trade add?</h3></div><div class="tc-horizon"><label>From<select class="tc-start" aria-label="First week to evaluate">${weekOptions(state.startWeek)}</select></label><label>Through<select class="tc-end" aria-label="Last week to evaluate">${weekOptions(state.endWeek)}</select></label></div></div><p class="tc-live-status" role="status" aria-live="polite" aria-atomic="true"></p><div class="tc-rating" role="status"></div><div class="tc-analysis-board"><div class="tc-results"></div><div class="tc-weekly"></div><div class="tc-lineup-detail"></div></div><details class="tc-method"><summary>How the comparison works</summary><div class="tc-method-content"></div></details></section>`;
      container.querySelector('.tc-method-content').innerHTML='<p>For every selected week, each roster is assigned to the league’s actual starting slots to maximize projected points. A player can fill only one eligible slot. The difference between the best lineups before and after the trade is the gain; a stronger bench does not automatically add starter points.</p><p>Bye weeks and known unavailable weeks are excluded. Empty slots use the best available free-agent projection; occupied roster slots stay fixed. Keeper value, injury probabilities and playoff advancement are not modeled. Weeks are weighted equally. This is a projection comparison, not a guarantee.</p>'+(data.notes||[]).map(note=>'<p>'+escape(note)+'</p>').join('');
      renderRosters();renderResults();
    }
    function playerAverage(player){const values=weeks.filter(week=>week>=state.startWeek&&week<=state.endWeek).map(week=>projection(player,week)).filter(row=>row.available&&finite(row.points));return values.length?values.reduce((sum,row)=>sum+row.points,0)/values.length:null;}
    function projectionColor(player,avg){const leaders=Object.values(teams).flatMap(team=>team.players).filter(p=>p.position===player.position).map(p=>Number(p.season_ppg)||0),top=Math.max(1,...leaders);return `hsl(${Math.max(0,Math.min(1,(avg||0)/top))*120},75%,62%)`;}
    function renderRosters(){
      for(const side of ['A','B']){
        const selected=new Set(state['send'+side]),team=teams[state['team'+side]],host=container.querySelector('.tc-player-list[data-side="'+side+'"]');
        const positionOrder=[...new Set(data.lineup_slots||[]),'BE','BENCH','IR'];
        const ordered=[...team.players].sort((a,b)=>(positionOrder.indexOf(a.slot)<0?100:positionOrder.indexOf(a.slot))-(positionOrder.indexOf(b.slot)<0?100:positionOrder.indexOf(b.slot))||(playerAverage(b)??-Infinity)-(playerAverage(a)??-Infinity)||a.name.localeCompare(b.name));
        host.innerHTML=ordered.map(player=>{const id=idOf(player),avg=playerAverage(player),injury=player.injury_status&&!['ACTIVE','NORMAL','HEALTHY'].includes(String(player.injury_status).toUpperCase())?String(player.injury_status):'',meta=[player.slot||player.position,player.nfl_team,player.bye_week?'Bye '+player.bye_week:''].filter(Boolean).join(' · ');return `<label class="tc-player${selected.has(id)?' is-selected':''}" title="${escape(player.name)}"><input type="checkbox" data-side="${side}" value="${escape(id)}"${selected.has(id)?' checked':''} aria-label="${side==='A'?'Send':'Receive'} ${escape(player.name)}"><span class="tc-player-photo">${photo(player)}</span><span class="tc-player-info"><span class="tc-player-name">${escape(player.name)}</span><span class="tc-player-meta">${escape(meta)}</span>${injury?'<span class="tc-injury">'+escape(injury)+(player.on_ir?' · IR':'')+'</span>':player.on_ir?'<span class="tc-injury">IR roster slot</span>':''}</span><span class="tc-player-value" style="color:${projectionColor(player,avg)}">${avg===null?'—':number(avg)}<small>proj/game ${player.mean_delta==null?'':'<span class="mean-delta">Δ</span> '+signed(player.mean_delta)}</small></span></label>`;}).join('');
        container.querySelector('.tc-selection-count[data-side="'+side+'"]').textContent=selected.size+' selected';
      }
    }
    function exchangeMarkup(side){return '<div class="tc-exchange"><div><span class="tc-outgoing">Send</span><p>'+(side.sent.length?side.sent.map(player=>escape(player.name)).join(', '):'No players selected')+'</p></div><div><span class="tc-incoming">Receive</span><p>'+(side.received.length?side.received.map(player=>escape(player.name)).join(', '):'No players selected')+'</p></div></div>';}
    function renderResults(){
      result=evaluateTrade(data,state);if(!result)return;
      const count=result.weeks.length,max=Math.max(1,...[result.a,result.b].map(side=>Math.abs(side.delta))),has=result.hasTrade;
      container.querySelector('.tc-live-status').textContent=has?`${abbrev(state.teamA)}: ${signed(result.a.delta)} projected starter points. ${abbrev(state.teamB)}: ${signed(result.b.delta)} across ${count} weeks.`:'Select players above to compare projected starting-lineup points.';
      container.querySelector('.tc-rating').innerHTML=`<strong>${has?tradeRating(result.a.delta,result.b.delta,count):'Select a trade'}</strong><div class="tc-rating-scale"><span class="${tone(result.a.delta)}">${result.a.delta>.5*count?'↑':result.a.delta<-.5*count?'↓':'↔'} ${escape(abbrev(state.teamA))} ${signed(result.a.delta/count)}/wk</span><span class="${tone(result.b.delta)}">${result.b.delta>.5*count?'↑':result.b.delta<-.5*count?'↓':'↔'} ${escape(abbrev(state.teamB))} ${signed(result.b.delta/count)}/wk</span></div><small>Within ±0.5 starter points/week is treated as even.</small>`;
      container.querySelector('.tc-results').innerHTML=[result.a,result.b].map((side,index)=>{
        const missing=new Set(side.weeks.flatMap(row=>[...row.before.missing,...row.after.missing]).map(idOf)),fallback=side.weeks.reduce((sum,row)=>sum+row.before.fallback.length+row.after.fallback.length,0),empty=side.weeks.reduce((sum,row)=>sum+row.after.lineup.filter(slot=>!slot.player).length,0);
        return `<article class="tc-result"><h4>${escape(side.team.name)}</h4><div class="tc-impact-number ${tone(side.delta)}">${signed(side.delta)}<span>balance points</span></div><div class="tc-gain-track" aria-hidden="true"><span class="${tone(side.delta)}" style="width:${Math.max(0,Math.abs(side.delta)/max*50)}%;${side.delta<0?'right':'left'}:50%"></span></div><div class="tc-result-baseline"><span>Before <strong>${number(side.before/count)}<small> / week</small></strong></span><span>After <strong>${number(side.after/count)}<small> / week</small></strong></span></div><p class="tc-per-week">${signed(side.delta/count)} balance points/week · ${count} weeks</p><p class="tc-fit-note">Own lineup: ${signed(side.lineupDelta)} · Opponent effect: ${signed(side.delta-side.lineupDelta)}</p>${exchangeMarkup(side)}<p class="tc-fit-note">${side.received.length?side.received.map(player=>`${escape(player.name)} starts in <strong>${side.weeks.filter(row=>row.after.lineup.some(slot=>slot.player&&idOf(slot.player)===idOf(player))).length} of ${count}</strong> remaining weeks${count!==weeks.length?' in this range':''} (byes included).`).join('<br>'):'Add players to see where they fit.'}</p>${missing.size?`<p class="tc-data-warning">${missing.size} player${missing.size===1?' has':'s have'} missing weekly projections and may be omitted from lineups.</p>`:''}${fallback?'<p class="tc-estimate-note">Includes baseline estimates when ESPN weekly projections are unavailable.</p>':''}</article>`;
      }).join('');
      container.querySelector('.tc-weekly').innerHTML=`<h4>Week-by-week trade balance</h4><p class="tc-table-help">Each week includes lineup gain minus your partner’s gain when you face them. Byes and replacements are included.</p><div class="tc-week-table-wrap"><table class="tc-week-table"><thead><tr><th scope="col">Week</th><th scope="col" title="${escape(result.a.team.name)}">${escape(abbrev(state.teamA))}</th><th scope="col" title="${escape(result.b.team.name)}">${escape(abbrev(state.teamB))}</th></tr></thead><tbody>${result.weeks.map((week,index)=>`<tr data-week="${week}"><th scope="row"><button type="button" class="tc-week-choice" data-week="${week}" aria-label="Inspect week ${week}">${week}</button></th><td class="${tone(result.a.weeks[index].balance)}">${signed(result.a.weeks[index].balance)}</td><td class="${tone(result.b.weeks[index].balance)}">${signed(result.b.weeks[index].balance)}</td></tr>`).join('')}</tbody><tfoot><tr><th scope="row">Total</th><td class="${tone(result.a.delta)}">${signed(result.a.delta)}</td><td class="${tone(result.b.delta)}">${signed(result.b.delta)}</td></tr></tfoot></table></div>`;
      if(!result.weeks.includes(state.focusWeek))state.focusWeek=result.weeks[0];
      renderLineups();
    }
    function renderLineups(){
      const detail=container.querySelector('.tc-lineup-detail'),weekly=container.querySelector('.tc-weekly');
      weekly.remove();
      detail.innerHTML=`<div class="tc-lineup-heading"><div><h4>Weekly projected lineups</h4><p class="tc-table-help">Select a week in the center to compare both teams.</p></div><div class="tc-lineup-controls"><label>Lineup<select class="tc-lineup-view" aria-label="Projected lineup comparison"><option value="after"${state.lineupView==='after'?' selected':''}>After trade</option><option value="before"${state.lineupView==='before'?' selected':''}>Before trade</option></select></label><label>Week<select class="tc-focus" aria-label="Inspect projected lineup week">${result.weeks.map(week=>`<option value="${week}"${state.focusWeek===week?' selected':''}>${week}</option>`).join('')}</select></label></div></div><div class="tc-lineup-grid">${[result.a,result.b].map((side,index)=>{
        const row=side.weeks.find(row=>row.week===state.focusWeek),incoming=new Set(side.received.map(idOf)),sent=new Set(side.sent.map(idOf)),after=state.lineupView==='after',lineup=after?row.after:row.before;
        return `<section class="tc-projected-roster tc-projected-${index?'away':'home'}"><h5>${escape(side.team.name)}</h5><p class="tc-lineup-caption">Week ${state.focusWeek} · ${after?'After':'Before'} trade</p><div class="tc-lineup-table-wrap"><table class="tc-lineup-table"><thead><tr><th scope="col">Slot</th><th scope="col">Player</th><th scope="col">Proj.</th></tr></thead><tbody>${lineup.lineup.map(slot=>{const player=slot.player,name=player?(player.free_agent?'Replacement':player.name):'Empty',kind=player?(after&&incoming.has(idOf(player))?'tc-incoming':!after&&sent.has(idOf(player))?'tc-outgoing':''):'';return `<tr><th scope="row">${escape(slot.slot)}</th><td class="${kind}" title="${escape(name+' · '+sourceLabel(slot.source))}">${escape(name)}</td><td>${number(slot.points)}</td></tr>`;}).join('')}</tbody><tfoot><tr><th scope="row" colspan="2">Total</th><td>${number(lineup.total)}</td></tr></tfoot></table></div>${after&&row.displaced.length?'<p class="tc-fit-note">Moves to bench: '+row.displaced.map(player=>escape(player.name)).join(', ')+'.</p>':''}</section>`;
      }).join('')}</div><p class="tc-table-help"><span class="tc-outgoing">Orange: sent</span> · <span class="tc-incoming">Blue: received</span>. Lineups are optimized projections, not saved ESPN lineups.</p>`;
      const grid=detail.querySelector('.tc-lineup-grid');grid.insertBefore(weekly,grid.children[1]);
      weekly.querySelectorAll('tbody tr').forEach(row=>{const active=Number(row.dataset.week)===state.focusWeek;row.classList.toggle('is-active',active);row.querySelector('button').setAttribute('aria-pressed',String(active));});
    }
    function notify(){if(typeof options.onChange==='function')options.onChange(api.getState());}
    container.addEventListener('change',event=>{
      const input=event.target;
      if(input.matches('.tc-team')){
        const side=input.dataset.side;state['team'+side]=input.value;state['send'+side]=[];
        if(state.teamA===state.teamB){state.teamB=ids.find(id=>id!==state.teamA);state.sendB=[];}
        container.querySelector('#tcTeamB').innerHTML=teamOptions(state.teamB,state.teamA);renderRosters();renderResults();notify();
      }else if(input.matches('.tc-player input')){
        const key='send'+input.dataset.side,selected=new Set(state[key]);input.checked?selected.add(input.value):selected.delete(input.value);state[key]=[...selected];input.closest('.tc-player').classList.toggle('is-selected',input.checked);container.querySelector('.tc-selection-count[data-side="'+input.dataset.side+'"]').textContent=selected.size+' selected';renderResults();notify();
      }else if(input.matches('.tc-start,.tc-end')){
        state[input.matches('.tc-start')?'startWeek':'endWeek']=Number(input.value);
        if(state.startWeek>state.endWeek){if(input.matches('.tc-start'))state.endWeek=state.startWeek;else state.startWeek=state.endWeek;}
        container.querySelector('.tc-start').value=state.startWeek;container.querySelector('.tc-end').value=state.endWeek;renderRosters();renderResults();notify();
      }else if(input.matches('.tc-lineup-view')){state.lineupView=input.value==='before'?'before':'after';renderLineups();container.querySelector('.tc-lineup-view').focus({preventScroll:true});notify();
      }else if(input.matches('.tc-focus')){state.focusWeek=Number(input.value);renderLineups();container.querySelector('.tc-focus').focus({preventScroll:true});notify();}
    });
    container.addEventListener('click',event=>{const week=event.target.closest('.tc-week-choice');if(week){state.focusWeek=Number(week.dataset.week);renderLineups();container.querySelector(`.tc-week-choice[data-week="${state.focusWeek}"]`).focus({preventScroll:true});notify();return;}if(event.target.closest('.tc-reset')){state.sendA=[];state.sendB=[];renderRosters();renderResults();notify();}});
    const api={
      getState:()=>({...state,sendA:[...state.sendA],sendB:[...state.sendB]}),
      setState:(next,{silent=true}={})=>{if(!next)return;state=normalize(next);renderShell();if(!silent)notify();},
      selectTeam:team=>{const id=ids.find(id=>id===String(team)||teams[id].name===team);if(!id||id===state.teamA)return;state.teamA=id;state.sendA=[];if(state.teamB===id){state.teamB=ids.find(value=>value!==id);state.sendB=[];}renderShell();},
      evaluate:()=>result
    };
    renderShell();return api;
  }
  const api={eligible,projection,optimizeLineup,withReplacements,tradeRating,evaluateTrade,mount};
  if(typeof module!=='undefined'&&module.exports)module.exports=api;
  if(root)root.TradeCalculator=api;
})(typeof window!=='undefined'?window:globalThis);
