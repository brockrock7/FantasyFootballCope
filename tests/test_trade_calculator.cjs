const assert=require('node:assert/strict');
const {eligible,projection,optimizeLineup,evaluateTrade}=require('../trade_calculator.js');
const player=(id,position,points,extra={})=>({id,name:'Player '+id,position,projections:{1:{points,source:'espn_weekly',available:true},2:{points,source:'espn_weekly',available:true}},...extra});
const data=(a,b,slots=['RB','WR'])=>({weeks:[1,2],lineup_slots:slots,teams:{a:{name:'A',players:a},b:{name:'B',players:b}}});
const state=(extra={})=>({teamA:'a',teamB:'b',sendA:[],sendB:[],startWeek:1,endWeek:2,...extra});
let checks=0;
function check(name,fn){fn();checks++;console.log('PASS '+name);}

check('a 20 PPG WR does not add points behind two 20 PPG WR starters',()=>{
  const fixture=data([player(1,'RB',15),player(2,'WR',20),player(3,'WR',20)],[player(4,'WR',20)],['RB','WR','WR']);
  const result=evaluateTrade(fixture,state({sendA:['1'],sendB:['4']}));
  assert.equal(result.a.delta,-30); // loses 15 starter points in each of two weeks
  assert.equal(result.a.after,80); // incoming WR does not create a third WR slot
  assert.equal(result.b.delta,-10); // partner exchanges its WR20 for RB15
});
check('equal projected WR depth has value in a week with a starter bye',()=>{
  const fixture=data([player(1,'WR',20,{bye_week:2}),player(2,'WR',20)],[player(3,'WR',20)],['WR','WR']);
  const result=evaluateTrade(fixture,state({sendB:['3']}));
  assert.deepEqual(result.a.weeks.map(row=>row.delta),[0,20]);
  assert.equal(result.a.delta,20);
});
check('flex is optimized jointly and each player is assigned only once',()=>{
  const players=[player(1,'RB',30),player(2,'WR',20),player(3,'TE',18)];
  const result=optimizeLineup(players,['FLEX','RB','WR'],1);
  assert.equal(result.total,68);
  assert.equal(result.lineup[0].player.id,3);
  assert.equal(new Set(result.lineup.map(row=>row.player.id)).size,3);
});
check('receiving a flex improvement only adds its marginal upgrade',()=>{
  const fixture=data([player(1,'RB',15),player(2,'WR',20),player(3,'TE',10)],[player(4,'WR',25)],['RB','WR','FLEX']);
  const result=evaluateTrade(fixture,state({sendB:['4']}));
  assert.equal(result.a.before,90);
  assert.equal(result.a.delta,30); // replaces TE10 with WR25, not 25 new points
  assert.deepEqual(result.a.weeks[0].displaced.map(row=>row.id),[3]);
});
check('league-provided eligibility overrides fallback positional assumptions',()=>{
  const candidate=player(1,'WR',25,{eligible_slots:['WR']});
  assert.equal(eligible(candidate,'FLEX'),false);
  assert.equal(optimizeLineup([candidate],['FLEX'],1).total,0);
  assert.equal(eligible(player(2,'WR',25),'FLEX'),true);
  assert.equal(eligible(player(3,'QB',25),'OP'),true);
});
check('missing, unavailable, bye, and zero projections are distinct',()=>{
  const players=[player(1,'RB',100,{bye_week:1}),player(2,'RB',90,{projections:{1:{points:90,available:false,source:'unavailable'}}}),player(3,'RB',0),player(4,'RB',null,{projections:{1:{points:null,available:false,source:'missing'}}})];
  const result=optimizeLineup(players,['RB'],1);
  assert.equal(result.total,0);
  assert.equal(result.lineup[0].player.id,3);
  assert.deepEqual(result.missing.map(row=>row.id),[4]);
  assert.equal(result.unavailable.length,2);
  assert.equal(projection(players[0],1).available,false);
});
check('IR metadata does not erase a genuine future-week projection',()=>{
  const playerOnIR=player(1,'RB',18,{on_ir:true,injury_status:'INJURY_RESERVE',projections:{1:{points:0,available:false,source:'unavailable'},2:{points:18,available:true,source:'espn_weekly'}}});
  assert.equal(optimizeLineup([playerOnIR],['RB'],1).total,0);
  assert.equal(optimizeLineup([playerOnIR],['RB'],2).total,18);
});
check('negative projections can leave a slot empty without fabricating losses',()=>{
  const result=optimizeLineup([player(1,'D/ST',-2)],['D/ST'],1);
  assert.equal(result.total,0);assert.equal(result.lineup[0].player,null);
});
check('one roster entry cannot fill duplicate slots twice',()=>{
  const duplicate=player(1,'RB',20);
  const result=optimizeLineup([duplicate,duplicate],['RB','RB','FLEX'],1);
  assert.equal(result.total,20);
  assert.equal(result.lineup.filter(row=>row.player).length,1);
});
check('multi-player deals evaluate both complete rosters and respect horizon',()=>{
  const fixture=data([player(1,'RB',30),player(2,'RB',25),player(3,'WR',8)],[player(4,'WR',30),player(5,'WR',25),player(6,'RB',8)]);
  const result=evaluateTrade(fixture,state({sendA:['2'],sendB:['5'],startWeek:2}));
  assert.equal(result.a.delta,17);assert.equal(result.b.delta,17);assert.deepEqual(result.weeks,[2]);
  const empty=evaluateTrade(fixture,state());assert.equal(empty.hasTrade,false);assert.equal(empty.a.delta,0);
  assert.equal(evaluateTrade(fixture,state({teamB:'a'})),null);
});
check('randomized exact assignment matches an exhaustive legal-lineup oracle',()=>{
  let seed=9183;
  function random(){seed=(seed*1664525+1013904223)>>>0;return seed/4294967296;}
  function brute(players,slots,index=0,used=new Set()){
    if(index===slots.length)return 0;
    let best=brute(players,slots,index+1,used);
    for(const p of players)if(!used.has(p.id)&&eligible(p,slots[index])){
      used.add(p.id);best=Math.max(best,projection(p,1).points+brute(players,slots,index+1,used));used.delete(p.id);
    }
    return best;
  }
  for(let sample=0;sample<120;sample++){
    const players=Array.from({length:6},(_,id)=>player(id,['QB','RB','WR','TE'][Math.floor(random()*4)],Math.floor(random()*40)-5));
    const slots=['RB','WR','FLEX','OP'];
    assert.equal(optimizeLineup(players,slots,1).total,brute(players,slots));
  }
});
console.log(checks+' trade calculator checks passed.');

check('vacant slots use distinct available free agents and respect their byes',()=>{
 const {withReplacements,tradeRating}=require('../trade_calculator.js');
 const fa=[player(90,'RB',12,{free_agent:true,bye_week:2}),player(91,'RB',8,{free_agent:true})];
 const before=withReplacements([],['RB','RB'],1,fa);
 assert.equal(before.total,20);assert.equal(new Set(before.lineup.map(x=>x.player.id)).size,2);
 assert.equal(withReplacements([],['RB','RB'],2,fa).total,8);
 assert.equal(withReplacements([player(1,'RB',5)],['RB'],1,fa).total,5);
 const fixture=data([], [player(2,'RB',20)],['RB']);fixture.free_agents=fa;
 assert.equal(evaluateTrade(fixture,state({sendB:['2']})).a.delta,20); // (20-12)+(20-8)
 assert.equal(tradeRating(10,12,2),'Mutually beneficial');assert.equal(tradeRating(-10,-12,2),'Mutually detrimental');
 assert.equal(tradeRating(10,-12,2),'One-sided');assert.equal(tradeRating(.5,-.5,2),'Even');
});
