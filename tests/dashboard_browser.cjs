// Run with Node and Playwright available through NODE_PATH. Uses generated index.html.
const {chromium}=require('playwright');
const http=require('node:http');
const fs=require('node:fs');
const path=require('node:path');
const assert=require('node:assert/strict');
const root=path.resolve(__dirname,'..');
const server=http.createServer((request,response)=>{
  const name=new URL(request.url,'http://localhost').pathname;
  const target=path.resolve(root,'.'+(name==='/'?'/index.html':name));
  if(!target.startsWith(root+path.sep)){response.writeHead(403).end();return;}
  try{response.setHeader('Content-Type',target.endsWith('.json')?'application/json':target.endsWith('.html')?'text/html':'text/plain');response.end(fs.readFileSync(target));}
  catch(_){response.writeHead(404).end();}
});
(async()=>{
  await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
  const browser=await chromium.launch({headless:true,...(process.env.BROWSER_CHANNEL?{channel:process.env.BROWSER_CHANNEL}:{})});
  try{
    const page=await browser.newPage({viewport:{width:1440,height:1000}}),errors=[];
    page.on('pageerror',error=>{errors.push(error.message);console.error('Browser error:',error.stack||error.message);});
    page.on('requestfailed',request=>{if(request.url().includes('chart.umd'))console.error('Chart request failed:',request.failure());});
    await page.goto(`http://127.0.0.1:${server.address().port}`,{waitUntil:'networkidle'});
    await page.waitForFunction(()=>new URL(location.href).searchParams.has('view'));
    assert.deepEqual(errors,[],'dashboard loads without runtime errors');
    const state=()=>page.evaluate(()=>dashboardState());
    const first=await state(),team=await page.locator('#globalTeams button').nth(2).getAttribute('title');
    const before=await page.evaluate(()=>history.length);
    await page.locator('#globalTeams button').nth(2).click();
    await page.waitForFunction(team=>dashboardState().globalTeam===team,team);
    const global=await state();
    for(const id of ['positionTeams','sourceTeams','differentialTeams','rosterTeams','luckTeams','tradeTeams','multiverseTeams'])assert.equal(global.buttons[id],team,id);
    assert.equal(global.pins.power,team);assert.equal(global.pins.multiverse,team);
    assert.equal(await page.evaluate(team=>(D.matchup_preview?.matchups||[])[selectedLiveMatchup]&&[D.matchup_preview.matchups[selectedLiveMatchup].home_team,D.matchup_preview.matchups[selectedLiveMatchup].away_team].includes(team),team),true);
    assert.equal(await page.evaluate(()=>history.length),before+1,'one global click is one history entry');
    await page.goBack();await page.waitForFunction(team=>dashboardState().globalTeam===team,first.globalTeam);
    assert.equal((await state()).buttons.positionTeams,first.buttons.positionTeams);
    await page.goForward();await page.waitForFunction(team=>dashboardState().globalTeam===team,team);
    await page.locator('[data-tab="team"]').click();
    await page.locator('#positionTeams button').nth(3).click();
    const local=await state();assert.notEqual(local.buttons.positionTeams,team);assert.equal(local.buttons.sourceTeams,team);
    await page.goBack();await page.waitForFunction(team=>dashboardState().buttons.positionTeams===team,team);
    await page.goForward();await page.waitForFunction(team=>dashboardState().buttons.positionTeams===team,local.buttons.positionTeams);
    // Use the real Chart.js legend event path, not a programmatic visibility edit.
    await page.locator('#positionChart').scrollIntoViewIfNeeded();
    const legend=await page.evaluate(()=>{const chart=Chart.getChart('positionChart'),box=chart.legend.legendHitBoxes[0],rect=chart.canvas.getBoundingClientRect();return{x:rect.left+box.left+box.width/2,y:rect.top+box.top+box.height/2};});
    await page.mouse.click(legend.x,legend.y);
    await page.waitForFunction(()=>!!JSON.parse(new URL(location.href).searchParams.get('view')).charts.positionChart?.hidden.length);
    const hidden=(await state()).charts.positionChart;
    await page.goBack();await page.waitForFunction(()=>!dashboardState().charts.positionChart);
    await page.goForward();await page.waitForFunction(()=>!!dashboardState().charts.positionChart);
    assert.deepEqual((await state()).charts.positionChart,hidden);
    await page.locator('[data-team-view="roster"]').click();
    await page.locator('.sd-trigger').first().click();
    await page.waitForFunction(()=>!!dashboardState().distribution);
    const distribution=(await state()).distribution;
    const range=await page.evaluate(()=>({min:rosterSdChart.options.scales.x.min,max:rosterSdChart.options.scales.x.max,legend:rosterSdChart.legend.legendItems.map(item=>item.text)}));
    assert(range.min<=0&&range.max>=50);assert.equal(range.legend.length,3);
    await page.keyboard.press('Escape');
    await page.goBack();await page.waitForFunction(value=>dashboardState().distribution===value,distribution);
    await page.keyboard.press('Escape');
    await page.locator('[data-tab="luck"]').click();
    await page.locator('[data-luck-view="multiverse"]').click();
    await page.locator('#multiverseTeams button').nth(4).click();
    assert.equal(await page.evaluate(()=>multiverseFirstPie.data.datasets[0].offset.filter(value=>value>0).length),1);
    await page.locator('[data-tab="league"]').click();
    await page.locator('#standingsTable th').filter({hasText:'Last 3'}).click();
    assert.equal((await state()).sorts.standingsTable.column,'Last 3');
    await page.locator('[data-tab="live"]').click();
    assert.equal(await page.locator('.matchup-mirror').count(),1);
    assert.equal(await page.locator('.matchup-mirror tfoot .matchup-points').count(),2);
    if(await page.locator('#liveMatchups button').count()>1){
      await page.locator('#liveMatchups button').nth(1).click();
      const selected=(await state()).live.matchup;
      await page.reload({waitUntil:'networkidle'});
      assert.equal((await state()).live.matchup,selected,'shared URL restores matchup after async snapshot load');
    }
    if(await page.locator('#liveWeeks button').count()>1){
      const current=(await state()).live;
      await page.locator('#liveWeeks button').last().click();
      await page.waitForFunction(()=>liveChart.data.datasets[0].data.length>0);
      const historical=(await state()).live;
      assert.notEqual(historical.week,current.week);
      await page.reload({waitUntil:'networkidle'});
      assert.deepEqual((await state()).live,historical,'historical week reload');
      await page.goBack();await page.waitForFunction(week=>dashboardState().live.week===week,current.week);
      assert.equal((await state()).live.matchup,current.matchup);
    }
    const saved=await state();
    await page.reload({waitUntil:'networkidle'});
    assert.deepEqual(await state(),saved,'reload restores complete state');
    if(process.env.DASHBOARD_SCREENSHOT_DIR)await page.screenshot({path:path.join(process.env.DASHBOARD_SCREENSHOT_DIR,'matchup-desktop.png'),fullPage:true});
    for(const width of [320,390,430]){
      await page.setViewportSize({width,height:844});
      await page.waitForFunction(()=>document.documentElement.scrollWidth<=innerWidth);
      assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true,'no page overflow at '+width+'px');
    }
    if(process.env.DASHBOARD_SCREENSHOT_DIR)await page.screenshot({path:path.join(process.env.DASHBOARD_SCREENSHOT_DIR,'matchup-mobile.png'),fullPage:true});
    // A real touch context exercises the pinned mobile distribution sheet.
    const touchPage=await browser.newPage({viewport:{width:390,height:844},hasTouch:true,isMobile:true});
    try{
      touchPage.on('pageerror',error=>{errors.push(error.message);console.error('Touch browser error:',error.stack||error.message);});
      await touchPage.goto(`http://127.0.0.1:${server.address().port}`,{waitUntil:'networkidle'});
      await touchPage.waitForFunction(()=>new URL(location.href).searchParams.has('view'));
      await touchPage.locator('[data-tab="team"]').tap();
      await touchPage.locator('[data-team-view="roster"]').tap();
      const trigger=touchPage.locator('.sd-trigger').first();
      await trigger.tap();
      await touchPage.waitForFunction(()=>rosterSdPopover.classList.contains('active')&&rosterSdPinned);
      const geometry=await touchPage.evaluate(()=>{const rect=rosterSdPopover.getBoundingClientRect(),close=rosterSdClose.getBoundingClientRect();return{left:rect.left,right:rect.right,top:rect.top,bottom:rect.bottom,width:innerWidth,height:innerHeight,closeHeight:close.height,closeWidth:close.width,overflow:document.documentElement.scrollWidth>innerWidth,locked:getComputedStyle(document.body).overflow==='hidden',focused:document.activeElement===rosterSdClose,modal:rosterSdPopover.getAttribute('aria-modal')};});
      assert(geometry.left>=0&&geometry.right<=geometry.width+1&&geometry.top>=0&&geometry.bottom<=geometry.height+1,'sheet stays inside viewport');
      assert(Math.abs(geometry.bottom-geometry.height)<2,'mobile sheet is anchored to bottom');
      assert(geometry.closeHeight>=44&&geometry.closeWidth>=44,'Close is a touch target');
      assert(!geometry.overflow&&geometry.locked&&geometry.focused);assert.equal(geometry.modal,'true');
      await touchPage.keyboard.press('Shift+Tab');
      assert.equal(await touchPage.evaluate(()=>rosterSdPopover.contains(document.activeElement)),true,'focus stays inside sheet');
      await touchPage.keyboard.press('Tab');
      assert.equal(await touchPage.evaluate(()=>document.activeElement===rosterSdClose),true,'Tab wraps to Close');
      await touchPage.locator('.sd-popover').dispatchEvent('mouseleave');
      await touchPage.waitForTimeout(250);
      assert.equal(await touchPage.locator('.sd-popover.active').count(),1,'explicitly opened sheet stays open after mouseleave');
      await touchPage.locator('#rosterSdClose').tap();
      await touchPage.waitForFunction(()=>!rosterSdPopover.classList.contains('active')&&!dashboardState().distribution);
      assert.equal(await touchPage.evaluate(()=>document.activeElement?.classList.contains('sd-trigger')),true,'Close returns focus to player');
      await trigger.tap();await touchPage.keyboard.press('Escape');
      await touchPage.waitForFunction(()=>!rosterSdPopover.classList.contains('active')&&!document.body.classList.contains('sd-modal-open'));
      await trigger.tap();
      if(process.env.DASHBOARD_SCREENSHOT_DIR)await touchPage.screenshot({path:path.join(process.env.DASHBOARD_SCREENSHOT_DIR,'roster-sheet-mobile.png')});
      await touchPage.locator('.sd-backdrop').tap({position:{x:8,y:8}});
      await touchPage.waitForFunction(()=>!rosterSdPopover.classList.contains('active'));
    }finally{await touchPage.close();}
    await require('./mobile_layout.cjs')(page);
    await require('./trade_calculator_browser.cjs')(page);
    assert.deepEqual(errors,[]);
    console.log('PASS: global/local selection, native history, real legend clicks, sorts, roster popout, pie highlight, matchup, reload, 320/390/430px layout, and touch distribution sheet');
  }finally{await browser.close();}
})().catch(error=>{console.error(error);process.exitCode=1;}).finally(()=>server.close());
