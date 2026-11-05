// Responsive presentation changes leave chart data and URL selections intact.
const phoneLayout=matchMedia('(max-width:700px)');
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
function isBarComparison(chart){
  return chart.config.type==='bar'||chart.data.datasets.some(dataset=>dataset.type==='bar');
}
function sizeHorizontalChart(chart){
  if(!isBarComparison(chart))return;
  const host=chart.canvas.parentElement,count=Math.max(1,chart.data.labels?.length||0);
  const barDatasets=chart.data.datasets.filter(dataset=>(dataset.type||chart.config.type)==='bar');
  const grouped=!chart.options.scales?.x?.stacked&&barDatasets.length>1;
  const rowHeight=grouped?46:34;
  host.classList.add('horizontal-bar-chart');
  host.style.setProperty('--bar-chart-height',Math.max(300,count*rowHeight+(phoneLayout.matches?60:85))+'px');
}
function applyMobileChartLayout(chart){
  rememberChartLayout(chart);
  const saved=chart.$desktopLayout,phone=phoneLayout.matches;
  chart.options.plugins.legend.display=phone?false:saved.legend;
  if(matchMedia('(prefers-reduced-motion:reduce)').matches)chart.options.animation=false;
  if(isBarComparison(chart)){
    if(!chart.$horizontalBars){
      const valueAxis=saved.indexAxis==='y'?saved.scales.x:saved.scales.y;
      const categoryAxis=saved.indexAxis==='y'?saved.scales.y:saved.scales.x;
      chart.config.options.indexAxis='y';
      // Rotate the category/value configuration together, including bounds and stacks.
      chart.config.options.scales={
        x:{...valueAxis,type:'linear',axis:'x',ticks:{...valueAxis?.ticks,maxTicksLimit:6}},
        y:{...categoryAxis,type:'category',axis:'y',ticks:{...categoryAxis?.ticks,autoSkip:false,minRotation:0,maxRotation:0,font:{size:12}}}
      };
      chart.config.options.interaction={...chart.config.options.interaction,axis:'y'};
      // Mixed bar/line plots use the same orientation for their zero reference line.
      chart.data.datasets.forEach(dataset=>{dataset.indexAxis='y';});
      chart.$horizontalBars=true;
    }
    sizeHorizontalChart(chart);
  }else if(chart.config.type!=='radar'){
    const x=chart.options.scales?.x;
    if(x?.ticks){x.ticks.maxRotation=0;x.ticks.maxTicksLimit=phone?5:8;}
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
  const signature=JSON.stringify(items.map(item=>[item.text,item.hidden,item.strokeStyle,item.fillStyle,item.lineDash]));
  if(host.dataset.signature===signature)return;
  host.dataset.signature=signature;host.replaceChildren();
  items.forEach(item=>{
    const button=document.createElement('button'),swatch=document.createElement('span'),label=document.createElement('span');
    button.type='button';button.className='mobile-legend-button';button.title=item.text;button.setAttribute('aria-pressed',String(!item.hidden));
    swatch.className='mobile-legend-swatch';swatch.style.setProperty('--series-color',typeof item.strokeStyle==='string'?item.strokeStyle:typeof item.fillStyle==='string'?item.fillStyle:'var(--blue)');
    if(item.lineDash?.length)swatch.classList.add('is-dashed');
    label.textContent=mobileLegendLabel(item.text);button.append(swatch,label);
    button.addEventListener('click',()=>{
      if(radial)chart.toggleDataVisibility(item.index);else chart.setDatasetVisibility(item.datasetIndex,!chart.isDatasetVisible(item.datasetIndex));
      chart.update('none');saveDashboardState();
    });host.append(button);
  });
}
Chart.register({id:'mobilePresentation',beforeInit:applyMobileChartLayout,beforeUpdate:sizeHorizontalChart,afterUpdate:updateMobileLegend,
  afterDestroy(chart){chart.$mobileLegend?.remove();}});
function refreshMobileCharts(){Object.values(Chart.instances).forEach(chart=>{applyMobileChartLayout(chart);chart.update('none');chart.resize();});}
phoneLayout.addEventListener('change',refreshMobileCharts);refreshMobileCharts();

// Lead with the selected team's weekly detail; league comparisons follow below it.
['position','acquisition'].forEach(view=>{
  const section=document.getElementById('team-view-'+view),chart=document.getElementById(view==='position'?'positionChart':'sourceChart');
  const card=chart?.closest('.card');if(section&&card)section.prepend(card);
});
const radarStrip=document.getElementById('radars');
if(radarStrip){radarStrip.tabIndex=0;radarStrip.setAttribute('role','region');radarStrip.setAttribute('aria-label','Team position profiles — swipe or scroll to compare teams');}

function updateTableScrollHints(){
  document.querySelectorAll('.scroll,.matchup-mirror-scroll').forEach(scroll=>{
    if(!scroll.querySelector('table'))return;
    scroll.tabIndex=0;scroll.setAttribute('role','region');
    scroll.setAttribute('aria-label',(scroll.closest('.card')?.querySelector('h2')?.textContent||'Table')+' — scroll for more columns');
    let hint=scroll.previousElementSibling;
    if(!hint?.classList.contains('table-scroll-hint')){hint=document.createElement('div');hint.className='table-scroll-hint';hint.textContent='Swipe for more columns →';scroll.before(hint);}
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
