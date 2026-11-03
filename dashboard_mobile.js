// Responsive presentation changes leave chart data and URL selections intact.
const phoneLayout=matchMedia('(max-width:700px)');
const horizontalTeamCharts=new Set(['positionTotalsChart','sourceTotalsChart','actualOptimalChart','starterBenchPointsChart','luckBars']);
function mobileLegendLabel(text){
  let label=String(text);
  [...D.teams].sort((a,b)=>b.length-a.length).forEach(team=>{label=label.replaceAll(team,teamAbbrev(team));});
  return label.replace(' (received − sent)','');
}
function rememberChartLayout(chart){
  if(chart.$desktopLayout)return;
  const scales=chart.config.options.scales||{};
  chart.$desktopLayout={legend:chart.options.plugins.legend.display,indexAxis:chart.options.indexAxis||'x',scales};
}
function applyMobileChartLayout(chart){
  rememberChartLayout(chart);
  const saved=chart.$desktopLayout,phone=phoneLayout.matches;
  chart.options.plugins.legend.display=phone?false:saved.legend;
  if(matchMedia('(prefers-reduced-motion:reduce)').matches)chart.options.animation=false;
  if(horizontalTeamCharts.has(chart.canvas.id)){
    const horizontal=phone||saved.indexAxis==='y';
    const valueAxis=saved.indexAxis==='y'?saved.scales.x:saved.scales.y;
    const categoryAxis=saved.indexAxis==='y'?saved.scales.y:saved.scales.x;
    chart.config.options.indexAxis=horizontal?'y':'x';
    // Supply explicit scale types when moving axes; do not carry resolved scale IDs.
    const value={...valueAxis,type:'linear',axis:horizontal?'x':'y',ticks:{...valueAxis?.ticks,maxTicksLimit:5}};
    const category={...categoryAxis,type:'category',axis:horizontal?'y':'x',ticks:{...categoryAxis?.ticks,autoSkip:false,font:{size:12}}};
    chart.config.options.scales=horizontal?{x:value,y:category}:saved.scales;
    chart.canvas.parentElement.classList.toggle('mobile-team-comparison',phone);
    chart.canvas.parentElement.style.setProperty('--team-chart-height',Math.max(360,(chart.data.labels?.length||0)*38+65)+'px');
  }else if(phone&&chart.config.type!=='radar'){
    const x=chart.options.scales?.x;
    if(x?.ticks){x.ticks.maxRotation=0;x.ticks.maxTicksLimit=5;}
  }
}
function updateMobileLegend(chart){
  const phone=phoneLayout.matches,show=phone&&chart.$desktopLayout?.legend!==false;
  let host=chart.$mobileLegend;
  if(!show){if(host)host.hidden=true;return;}
  if(!host){host=document.createElement('div');host.className='mobile-chart-legend';host.setAttribute('role','group');host.setAttribute('aria-label',(chart.canvas.closest('.card')?.querySelector('h2')?.textContent||'Chart')+' legend');chart.canvas.parentElement.after(host);chart.$mobileLegend=host;}
  host.hidden=false;
  const radial=['pie','doughnut','polarArea'].includes(chart.config.type);
  const items=(chart.legend?.legendItems||[]).filter(item=>!chart.data.datasets[item.datasetIndex??0]?.hideFromLegend);
  const signature=JSON.stringify(items.map(item=>[item.text,item.hidden,item.strokeStyle,item.fillStyle]));
  if(host.dataset.signature===signature)return;
  host.dataset.signature=signature;host.replaceChildren();
  items.forEach(item=>{
    const button=document.createElement('button'),swatch=document.createElement('span'),label=document.createElement('span');
    button.type='button';button.className='mobile-legend-button';button.title=item.text;button.setAttribute('aria-pressed',String(!item.hidden));
    swatch.className='mobile-legend-swatch';swatch.style.setProperty('--series-color',typeof item.strokeStyle==='string'?item.strokeStyle:typeof item.fillStyle==='string'?item.fillStyle:'var(--blue)');
    label.textContent=mobileLegendLabel(item.text);button.append(swatch,label);
    button.addEventListener('click',()=>{
      if(radial)chart.toggleDataVisibility(item.index);else chart.setDatasetVisibility(item.datasetIndex,!chart.isDatasetVisible(item.datasetIndex));
      chart.update('none');saveDashboardState();
    });host.append(button);
  });
}
Chart.register({id:'mobilePresentation',beforeInit:applyMobileChartLayout,afterUpdate:updateMobileLegend,
  afterDestroy(chart){chart.$mobileLegend?.remove();}});
function refreshMobileCharts(){Object.values(Chart.instances).forEach(chart=>{applyMobileChartLayout(chart);chart.update('none');chart.resize();});}
phoneLayout.addEventListener('change',refreshMobileCharts);refreshMobileCharts();

function updateTableScrollHints(){
  document.querySelectorAll('.scroll,.matchup-mirror-scroll').forEach(scroll=>{
    if(!scroll.querySelector('table'))return;
    scroll.tabIndex=0;scroll.setAttribute('role','region');
    scroll.setAttribute('aria-label',(scroll.closest('.card')?.querySelector('h2')?.textContent||'Table')+' — scroll for more columns');
    let hint=scroll.previousElementSibling;
    if(!hint?.classList.contains('table-scroll-hint')){hint=document.createElement('div');hint.className='table-scroll-hint';hint.textContent='Swipe or scroll for more columns →';scroll.before(hint);}
    hint.hidden=scroll.scrollWidth<=scroll.clientWidth+1;
  });
}
let mobileLayoutFrame;
function scheduleMobileLayout(){cancelAnimationFrame(mobileLayoutFrame);mobileLayoutFrame=requestAnimationFrame(updateTableScrollHints);}
new MutationObserver(scheduleMobileLayout).observe(document.querySelector('main'),{childList:true,subtree:true});
window.addEventListener('resize',scheduleMobileLayout);
// Keep the active tab/button in its horizontal strip after native history navigation.
document.addEventListener('click',event=>{
  const button=event.target.closest('.tab,.subtab,#globalTeams button');
  if(button&&phoneLayout.matches)button.scrollIntoView({block:'nearest',inline:'nearest'});
  scheduleMobileLayout();
});
scheduleMobileLayout();
