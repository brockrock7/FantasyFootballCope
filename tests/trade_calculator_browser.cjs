// Real dashboard controls, public payload, and URL history integration.
const assert=require('node:assert/strict');
const path=require('node:path');
module.exports=async function verifyTradeCalculator(page){
  await page.setViewportSize({width:390,height:844});
  await page.locator('[data-tab="trades"]').click();
  await page.locator('[data-trades-view="calculator"]').click();
  await page.waitForSelector('#tcTeamA');
  assert.equal(await page.locator('#trades-view-history').isVisible(),false);
  const original=await page.evaluate(()=>tradeCalculator.getState());
  const inputA=page.locator('.tc-player-list[data-side="A"] input').first();
  const inputB=page.locator('.tc-player-list[data-side="B"] input').first();
  await inputA.check();
  await inputB.check();
  await page.waitForFunction(()=>dashboardState().trade.sendA.length===1&&dashboardState().trade.sendB.length===1);
  const trade=await page.evaluate(()=>tradeCalculator.getState());
  assert.deepEqual((await page.evaluate(()=>dashboardState())).trade,trade);
  const results=await page.evaluate(()=>{
    const result=tradeCalculator.evaluate();
    return {weeks:result.weeks.length,a:result.a.delta,b:result.b.delta,
      weeklyA:result.a.weeks.reduce((sum,row)=>sum+row.delta,0),
      unique: [result.a,result.b].every(side=>side.weeks.every(row=>[row.before,row.after].every(lineup=>{
        const ids=lineup.lineup.filter(slot=>slot.player).map(slot=>slot.player.id);
        return ids.length===new Set(ids).size;
      })))};
  });
  assert(results.unique);assert(Number.isFinite(results.a)&&Number.isFinite(results.b));assert.equal(results.a,results.weeklyA);
  await page.goBack();await page.waitForFunction(()=>tradeCalculator.getState().sendB.length===0);
  assert.equal((await page.evaluate(()=>tradeCalculator.getState())).sendA.length,1);
  await page.goForward();await page.waitForFunction(()=>tradeCalculator.getState().sendB.length===1);
  await page.reload({waitUntil:'networkidle'});
  await page.waitForFunction(()=>tradeCalculator.getState()?.sendB.length===1);
  assert.deepEqual(await page.evaluate(()=>tradeCalculator.getState()),trade,'trade survives reload');
  const available=await page.locator('.tc-start option').evaluateAll(options=>options.map(option=>option.value));
  if(available.length>1){
    await page.locator('.tc-start').selectOption(available[1]);
    await page.waitForFunction(week=>dashboardState().trade.startWeek===Number(week),available[1]);
    assert.equal(await page.evaluate(()=>tradeCalculator.evaluate().weeks.length),results.weeks-1);
    await page.goBack();await page.waitForFunction(week=>tradeCalculator.getState().startWeek===week,trade.startWeek);
  }
  for(const width of [320,390,430,768,1440]){
    await page.setViewportSize({width,height:900});
    const geometry=await page.evaluate(()=>{
      const rosters=[...document.querySelectorAll('.tc-roster')].map(node=>node.getBoundingClientRect());
      return {overflow:document.documentElement.scrollWidth>innerWidth,sameTop:Math.abs(rosters[0].top-rosters[1].top)<1,separate:rosters[0].right<=rosters[1].left,
        controls:[...document.querySelectorAll('#tradeCalculator select,#tradeCalculator button')].filter(node=>node.getClientRects().length).map(node=>node.getBoundingClientRect().height)};
    });
    assert(!geometry.overflow,'no trade page overflow '+width);assert(geometry.sameTop&&geometry.separate,'side-by-side rosters '+width);
    assert(geometry.controls.every(height=>height>=44),'trade touch controls '+width);
  }
  if(process.env.DASHBOARD_SCREENSHOT_DIR){
    await page.setViewportSize({width:390,height:844});await page.locator('#tradeCalculator').scrollIntoViewIfNeeded();
    await page.screenshot({path:path.join(process.env.DASHBOARD_SCREENSHOT_DIR,'trade-calculator-mobile.png'),fullPage:true});
    await page.setViewportSize({width:1440,height:1000});
    await page.screenshot({path:path.join(process.env.DASHBOARD_SCREENSHOT_DIR,'trade-calculator-desktop.png'),fullPage:true});
  }
  await page.locator('#globalTeams button').nth(4).click();
  const global=await page.evaluate(()=>({team:globalTeam,own:D.trade_calculator.teams[tradeCalculator.getState().teamA].name}));
  assert.equal(global.team,global.own,'global selection updates calculator own team');
  const current=await page.evaluate(()=>tradeCalculator.getState());
  assert.notEqual(current.teamA,current.teamB);
  assert.equal(await page.locator('#tcTeamB option[value="'+current.teamA+'"]').count(),0);
  await page.locator('.tc-reset').click();
  assert.equal((await page.evaluate(()=>tradeCalculator.getState())).sendA.length,0);
  assert.equal((await page.evaluate(()=>tradeCalculator.evaluate())).a.delta,0);
  await page.locator('[data-trades-view="history"]').click();
  const history=await page.evaluate(()=>({raw:teamTradeChart.data.datasets[0].borderDash,started:teamTradeChart.data.datasets[1].borderDash,
    outlines:[...document.querySelectorAll('.trade-sent img,.trade-sent .roster-photo')].map(node=>getComputedStyle(node).borderColor),
    circles:[...individualTradeCharts].flatMap(chart=>chart.data.datasets.filter(dataset=>!dataset.hideFromLegend).map(dataset=>({radius:dataset.pointRadius,colors:dataset.pointBackgroundColor})))}));
  const legends=await page.evaluate(()=>Object.values(Chart.instances).flatMap(chart=>(chart.legend?.legendItems||[]).map(item=>({text:item.text,style:item.pointStyle}))));
  assert(legends.every(item=>item.text!=='0 points'&&item.style==='line'),'all legend keys are segments without zero reference');
  assert.equal(await page.evaluate(()=>[...individualTradeCharts].every(chart=>chart.data.datasets.some(dataset=>dataset.fill?.above==='rgba(63,185,80,.18)'))),true,'trade green/red regions');
  assert(history.raw.length>0);assert.equal(history.started.length,0);
  assert(history.outlines.every(color=>color==='rgb(245, 166, 35)'),'sent photos orange');
  history.circles.forEach(dataset=>{assert(dataset.radius>=6);assert(Array.isArray(dataset.colors));});
  console.log('PASS: trade roster selections, exact result totals, URL history/reload/week range, global team, 320–1440px side-by-side layout, touch controls, and trade history styles');
};
