// Embedded by expected_wins._html, so shared links work on the static Pages site.
// Only intentional selections enter history; hover, refreshes and chart data do not.
let restoringDashboard=false, dashboardRevision=0, globalTeam='', preferredLiveTeam='';
let chartPreferences={}, clickedDistribution='';
const dashboardViewVersion=1;
function chartKey(chart){
  if(chart.canvas.id)return chart.canvas.id;
  const radar=chart.canvas.closest('.rcell');
  return radar?'radar:'+radar.querySelector('h3')?.title:'';
}
function eachDashboardChart(callback){Object.values(Chart.instances).forEach(callback);}
function captureChartPreferences(){
  const result={};
  eachDashboardChart(chart=>{
    const key=chartKey(chart);if(!key)return;
    const hidden=chart.data.datasets.flatMap((dataset,index)=>chart.isDatasetVisible(index)?[]:[dataset.label||String(index)]);
    const radial=['pie','doughnut','polarArea'].includes(chart.config.type);
    const slices=radial?(chart.data.labels||[]).flatMap((label,index)=>chart.getDataVisibility(index)?[]:[label]):[];
    if(hidden.length||slices.length)result[key]={hidden,slices};
  });
  return result;
}
function restoreChartPreferences(){
  eachDashboardChart(chart=>{
    const pref=chartPreferences[chartKey(chart)]||{},hidden=pref.hidden||[];
    chart.data.datasets.forEach((dataset,index)=>chart.setDatasetVisibility(index,!hidden.includes(dataset.label||String(index))));
    if(['pie','doughnut','polarArea'].includes(chart.config.type))
      (chart.data.labels||[]).forEach((label,index)=>{if(chart.getDataVisibility(index)===(pref.slices||[]).includes(label))chart.toggleDataVisibility(index);});
    chart.update('none');
  });
}
function selectedButtons(){
  return Object.fromEntries([...document.querySelectorAll('.team-buttons[id]')].filter(host=>!['liveWeeks','liveMatchups'].includes(host.id)).map(host=>[host.id,host.querySelector('button.active')?.title||'']));
}
function tableSorts(){
  const result={};
  document.querySelectorAll('table[id]').forEach(table=>{
    const header=[...table.querySelectorAll('th.sortable')].find(th=>/[▲▼]$/.test(th.textContent));
    if(header)result[table.id]={column:header.textContent.replace(/\s*[▲▼]$/,''),descending:header.textContent.endsWith('▼')};
  });return result;
}
function dashboardState(){
  const sub={};['league','team','luck','trades'].forEach(group=>{sub[group]=document.querySelector('.'+group+'-subtab.active')?.dataset[group+'View'];});
  return {v:dashboardViewVersion,tab:document.querySelector('.tab.active')?.dataset.tab,sub,globalTeam,
    buttons:selectedButtons(),live:{week:selectedLiveWeek,matchup:selectedLiveMatchup},sorts:tableSorts(),
    pins:{power:pinnedPowerTeam===null?null:D.teams[pinnedPowerTeam],multiverse:pinnedMultiverseIndex===null?null:D.teams[pinnedMultiverseIndex]},
    charts:captureChartPreferences(),distribution:clickedDistribution,trade:tradeCalculator.getState(),suggestions:suggestedTrades.getState(),
    details:[...document.querySelectorAll('details')].map((detail,index)=>detail.open?index:null).filter(index=>index!==null)};
}
function saveDashboardState(replace=false){
  if(restoringDashboard)return;
  if(!rosterSdPopover.classList.contains('active'))clickedDistribution='';
  const state=dashboardState(),url=new URL(location.href),encoded=JSON.stringify(state);
  chartPreferences=state.charts;
  if(url.searchParams.get('view')===encoded)return;
  url.searchParams.set('view',encoded);
  history[replace?'replaceState':'pushState'](null,'',url);
}
function readDashboardState(){
  try{const state=JSON.parse(new URL(location.href).searchParams.get('view'));return state?.v===dashboardViewVersion?state:null;}catch(_){return null;}
}
function chooseButton(hostId,title){
  const host=document.getElementById(hostId);
  const button=host&&[...host.querySelectorAll('button')].find(button=>button.title===title);
  if(button&&!button.classList.contains('active'))button.click();
}
function markGlobalTeam(team){
  globalTeam=D.teams.includes(team)?team:'';
  document.querySelectorAll('#globalTeams button').forEach(button=>{
    const active=button.title===globalTeam;button.classList.toggle('active',active);button.setAttribute('aria-pressed',String(active));
  });
}
function chooseGlobalTeam(team){
  if(!D.teams.includes(team))return;
  markGlobalTeam(team);
  teamControls.forEach(control=>control.choose(team));
  tradeCalculator.selectTeam(team);suggestedTrades.selectTeam(team);
  showMultiverseTeam(team);
  pinnedPowerTeam=D.teams.indexOf(team);highlightPowerTeam(pinnedPowerTeam);
  pinnedMultiverseIndex=D.teams.indexOf(team);highlightMultiverseLine(pinnedMultiverseIndex);
  preferredLiveTeam=team;showLive();
}
D.teams.forEach(team=>makeChoiceButton(document.getElementById('globalTeams'),teamAbbrev(team),team,false,()=>chooseGlobalTeam(team)));

// Snapshot loading and dynamic trade charts may finish after URL restoration.
const showLiveWithoutNavigation=showLive;
showLive=function(){
  if(preferredLiveTeam){
    const snap=liveCache[selectedLiveWeek],previews=Number(selectedLiveWeek)===Number(D.matchup_preview?.week)?D.matchup_preview?.matchups||[]:[];
    const matches=snap?.matchups?.length?snap.matchups:previews.map(match=>[match.home_team,match.away_team]);
    const index=matches.findIndex(match=>match.includes(preferredLiveTeam));
    if(index>=0){selectedLiveMatchup=index;preferredLiveTeam='';}
  }
  showLiveWithoutNavigation();restoreChartPreferences();
};

async function restoreDashboardState(state){
  const revision=++dashboardRevision;
  restoringDashboard=true;
  try{
    // A missing state represents the original default view when going Back.
    state=state||initialDashboardState;
    if(state.tab!=="trades"||state.sub?.trades!=="suggestions")document.querySelector(".suggestions-gate")?.close();
    chartPreferences=state.charts||{};preferredLiveTeam='';
    hideRosterDistribution();clickedDistribution='';
    markGlobalTeam(state.globalTeam);
    if(state.tab)document.querySelector(`.tab[data-tab="${CSS.escape(state.tab)}"]`)?.click();
    ['league','team','luck','trades'].forEach(group=>{
      const view=state.sub?.[group];
      if(typeof view==='string')document.querySelector(`.${group}-subtab[data-${group}-view="${CSS.escape(view)}"]`)?.click();
    });
    Object.entries(state.buttons||{}).forEach(([host,title])=>{
      if(!['globalTeams','liveWeeks','liveMatchups'].includes(host)&&typeof title==='string')chooseButton(host,title);
    });
    tradeCalculator.setState(state.trade||initialDashboardState.trade,{silent:true});
    suggestedTrades.setState(state.suggestions||initialDashboardState.suggestions);
    const week=String(state.live?.week||'');
    if([...liveWeeksHost.children].some(button=>button.textContent==='W'+week)){
      selectedLiveWeek=week;
      [...liveWeeksHost.children].forEach(button=>{const active=button.textContent==='W'+week;button.classList.toggle('active',active);button.setAttribute('aria-pressed',String(active));});
    }
    selectedLiveMatchup=Math.max(0,Number.isInteger(state.live?.matchup)?state.live.matchup:0);
    for(const [tableId,sort] of Object.entries(state.sorts||{})){
      for(let tries=0;tries<2;tries++){
        const header=[...document.getElementById(tableId)?.querySelectorAll('th.sortable')||[]].find(th=>th.textContent.replace(/\s*[▲▼]$/,'')===sort.column);
        if(!header||header.textContent.endsWith(sort.descending?'▼':'▲'))break;
        header.click();
      }
    }
    const power=D.teams.indexOf(state.pins?.power),multiverseIndex=D.teams.indexOf(state.pins?.multiverse);
    pinnedPowerTeam=power<0?null:power;highlightPowerTeam(pinnedPowerTeam);
    pinnedMultiverseIndex=multiverseIndex<0?null:multiverseIndex;highlightMultiverseLine(pinnedMultiverseIndex);
    document.querySelectorAll('details').forEach((detail,index)=>{detail.open=(state.details||[]).includes(index);});
    restoreChartPreferences();
    if(typeof state.distribution==='string'&&state.distribution){
      const trigger=[...document.querySelectorAll('.sd-trigger')].find(button=>button.getAttribute('aria-label')===state.distribution);
      if(trigger){trigger.click();clickedDistribution=state.distribution;}
    }
  }finally{restoringDashboard=false;}
  await loadLive();
  if(revision===dashboardRevision)restoreChartPreferences();
}

document.addEventListener('click',event=>{
  if(restoringDashboard)return;
  if(event.composedPath().includes(liveMatchupsHost))preferredLiveTeam='';
  const trigger=event.target.closest('.sd-trigger');
  if(trigger)clickedDistribution=trigger.getAttribute('aria-label')||'';
  if(event.target.closest('button,th.sortable'))saveDashboardState();
});
document.addEventListener('keydown',event=>{if(event.key==='Escape'&&clickedDistribution){clickedDistribution='';saveDashboardState();}});
document.querySelectorAll('details').forEach(detail=>detail.addEventListener('toggle',()=>saveDashboardState()));
new MutationObserver(()=>{
  if(clickedDistribution&&!rosterSdPopover.classList.contains('active')){clickedDistribution='';saveDashboardState();}
}).observe(rosterSdPopover,{attributes:true,attributeFilter:['class']});
Chart.register({id:'dashboardUrlState',afterEvent(chart,args){
  if(args.event.type==='click')queueMicrotask(()=>saveDashboardState());
}});
window.addEventListener('popstate',()=>restoreDashboardState(readDashboardState()));
const initialDashboardState=dashboardState();
const linkedDashboardState=readDashboardState();
if(linkedDashboardState)restoreDashboardState(linkedDashboardState);
else{showLive();saveDashboardState(true);}
