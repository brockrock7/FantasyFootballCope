// Called by dashboard_browser.cjs against the same local dashboard server.
const assert=require('node:assert/strict');
const path=require('node:path');
module.exports=async function verifyMobileLayout(page){
  const widths=[320,390,430,768],metrics=[];
  for(const width of widths){
    await page.setViewportSize({width,height:844});
    for(const tab of ['league','live','team','luck','trades','leaderboard']){
      await page.locator(`[data-tab="${tab}"]`).click();
      const subtabs=await page.locator(`#panel-${tab} .subtab`).count();
      for(let index=0;index<Math.max(1,subtabs);index++){
        if(subtabs)await page.locator(`#panel-${tab} .subtab`).nth(index).click();
        if(await page.locator('.suggestions-gate[open]').count())await page.locator('.gate-cancel').click();
        await page.waitForFunction(()=>document.documentElement.scrollWidth<=innerWidth);
        const dimensions=await page.evaluate(()=>[...document.querySelectorAll('.panel.active table')].filter(table=>table.getBoundingClientRect().width).map(table=>({id:table.id||table.className,width:Math.round(table.getBoundingClientRect().width),layout:getComputedStyle(table).tableLayout})));
        dimensions.forEach(table=>assert.equal(table.layout,'auto',table.id+' uses content widths'));
        if(width===390)metrics.push(...dimensions);
      }
    }
  }
  await page.setViewportSize({width:390,height:844});
  await page.locator('[data-tab="league"]').click();
  await page.locator('[data-league-view="standings"]').click();
  const standings=await page.locator('#standingsTable').evaluate(table=>({width:table.getBoundingClientRect().width,first:getComputedStyle(table.rows[1].cells[1]).position}));
  assert(standings.width<800,'mobile standings substantially narrower than former1100px');
  assert.equal(standings.first,'sticky');
  assert(await page.locator('#standingsTable').locator('..').getAttribute('tabindex')==='0');
  if(process.env.DASHBOARD_SCREENSHOT_DIR)await page.screenshot({path:path.join(process.env.DASHBOARD_SCREENSHOT_DIR,'standings-mobile.png'),fullPage:true});
  await page.locator('[data-tab="team"]').click();await page.locator('[data-team-view="position"]').click();
  assert.deepEqual(await page.evaluate(()=>{const c=Chart.getChart('positionTotalsChart');return{axis:c.options.indexAxis,x:c.scales.x.type,y:c.scales.y.type,horizontal:c.getDatasetMeta(0).data[0].horizontal};}),{axis:'y',x:'linear',y:'category',horizontal:true});
  const legend=page.locator('#positionTotalsChart').locator('..').locator('..').locator('.mobile-chart-legend');
  const button=legend.locator('button').first();
  assert(await button.boundingBox().then(box=>box.height>=44),'touch legend target');
  const old=await button.getAttribute('aria-pressed');await button.click();
  assert.notEqual(await legend.locator('button').first().getAttribute('aria-pressed'),old);
  await page.goBack();await page.waitForFunction(()=>!dashboardState().charts.positionTotalsChart);
  await page.goForward();await page.waitForFunction(()=>!!dashboardState().charts.positionTotalsChart);
  await page.locator('[data-tab="live"]').click();
  await page.locator('#liveWeeks button').last().click();
  await page.waitForFunction(()=>!!document.querySelector('.matchup-mirror'));
  const matchup=await page.evaluate(()=>({width:document.querySelector('.matchup-mirror').getBoundingClientRect().width,
    min:liveProjectionChart.options.scales.x.min,max:liveProjectionChart.options.scales.x.max,
    densityMax:Math.max(...liveProjectionChart.data.datasets[0].data.map(point=>point.x))}));
  assert(matchup.width<510,'matchup narrower than old510px phone floor');
  assert(matchup.max<matchup.densityMax,'chart clips extreme density tail');
  if(process.env.DASHBOARD_SCREENSHOT_DIR)await page.screenshot({path:path.join(process.env.DASHBOARD_SCREENSHOT_DIR,'matchup-mobile.png'),fullPage:true});
  await page.setViewportSize({width:1440,height:1000});
  await page.locator('[data-tab="team"]').click();await page.locator('[data-team-view="position"]').click();
  await page.waitForFunction(()=>Chart.getChart('positionTotalsChart').scales.x.type==='linear');
  assert.equal(await page.evaluate(()=>Chart.getChart('positionTotalsChart').options.indexAxis),'y','desktop bars remain horizontal');
  const bars=await page.evaluate(()=>Object.values(Chart.instances).filter(chart=>chart.config.type==='bar').map(chart=>({id:chart.canvas.id,axis:chart.options.indexAxis,x:chart.scales.x.type,y:chart.scales.y.type,series:chart.data.datasets.map((dataset,index)=>({type:dataset.type||chart.config.type,index:chart.getDatasetMeta(index).iScale.axis,value:chart.getDatasetMeta(index).vScale.axis}))})));
  for(const chart of bars){
    assert.equal(chart.axis,'y',chart.id+' is horizontal');assert.equal(chart.x,'linear');assert.equal(chart.y,'category');
    chart.series.forEach(series=>{assert.equal(series.index,'y',chart.id+' '+series.type+' categories');assert.equal(series.value,'x',chart.id+' '+series.type+' values');});
  }
  await page.locator('[data-team-view="differential"]').click();
  await page.locator('#differentialTeams button').nth(2).click();
  const diff=await page.evaluate(()=>({min:differentialChart.options.scales.x.min,max:differentialChart.options.scales.x.max,expected:Math.max(1,...differentialChart.data.datasets[0].data.map(Math.abs))}));
  assert.equal(diff.min,-diff.expected);assert.equal(diff.max,diff.expected);
  console.log('Mobile table widths at390px:',JSON.stringify(metrics));
  console.log('PASS: every tab/subtab at320/390/430/768px, intrinsic tables, compact standings/matchup, touch legends with URL history, all bars horizontal on phone and desktop, mixed reference lines and differential bounds');
};
