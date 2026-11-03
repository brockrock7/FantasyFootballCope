// Pure pairing logic extracted from its durable dashboard source.
const fs=require('node:fs');
const path=require('node:path');
const vm=require('node:vm');
const assert=require('node:assert/strict');
const source=fs.readFileSync(path.resolve(__dirname,'../expected_wins.py'),'utf8');
const start=source.indexOf('function matchupLineupPairs('),end=source.indexOf('function renderLiveLineups(',start);
const scope={};vm.createContext(scope);vm.runInContext(source.slice(start,end),scope);
const player=(slot,name)=>({slot,name});
const home=[player('D/ST','Home DST'),player('FLEX','Home flex'),player('RB','RB1'),player('WR','Home WR'),player('QB','Home QB'),player('RB','RB2'),player('K','Home K'),player('TE','Home TE')];
const away=[player('RB','Away RB1'),player('RB','Away RB2'),player('WR','Away WR1'),player('WR','Away WR2'),player('DEF','Away DST'),player('QB','Away QB'),player('RB/WR/TE','Away flex'),player('K','Away K')];
const pairs=scope.matchupLineupPairs(home,away,['QB','RB','WR','TE','FLEX','K','D/ST']);
const names=JSON.parse(JSON.stringify(pairs.map(pair=>pair.map(item=>item?.name||null))));
assert.deepEqual(names,[['Home QB','Away QB'],['RB1','Away RB1'],['RB2','Away RB2'],['Home WR','Away WR1'],[null,'Away WR2'],['Home TE',null],['Home flex','Away flex'],['Home K','Away K'],['Home DST','Away DST']]);
assert.equal(home[0].name,'Home DST','original lineups remain unchanged');
assert.deepEqual(JSON.parse(JSON.stringify(scope.matchupLineupPairs([],[]))),[]);
assert.equal(scope.matchupLineupPairs([player('CUSTOM','custom')],[],['CUSTOM'])[0][0].name,'custom');
console.log('PASS: sorted lineup slots, repeated positions, opposing pairing, aliases, custom slots, empty lineups');
