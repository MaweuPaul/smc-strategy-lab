import test from 'node:test';
import assert from 'node:assert/strict';
import { eventIndex, exposureBlocks, settleBlock, runMonteCarlo } from './dailyObAnalysis.js';

const trade = (symbol, start, end, r, mult=1) => ({ symbol, chain_id: symbol,
  entry_time: `2025-01-0${start}T00:00:00Z`, exit_time: `2025-01-0${end}T00:00:00Z`,
  r_multiple: r, risk_mult: mult });

test('overlapping trades retain original concurrent cash sizing', () => {
  const blocks = exposureBlocks([trade('A',1,4,2),trade('B',2,3,1)]);
  assert.equal(blocks.length,1);
  assert.deepEqual(settleBlock(blocks[0],1000,1), { balance:1030, balances:[1010,1030] });
});
test('pyramid risk multiplier applies', () => {
  assert.equal(settleBlock(exposureBlocks([trade('A',1,2,2,.5)])[0],2000,1).balance,2020);
});
test('bootstrap is reproducible and preserves blocks', () => {
  const trades = [trade('A',1,4,2),trade('B',2,3,1),trade('C',5,6,-1)];
  const a = runMonteCarlo(trades,1,1000,20,4,42);
  assert.deepEqual(a,runMonteCarlo(trades,1,1000,20,4,42));
  assert.equal(a.blocks,2);
  assert.equal(a.grouped,true);
});
test('empty or nonfinite outcomes produce no result', () => {
  assert.equal(runMonteCarlo([],1,1000),null);
  assert.equal(runMonteCarlo([{r_multiple:Infinity}],1,1000),null);
});
test('events map by containment, not nearest open', () => {
  const candles=[{time:0},{time:3600}];
  assert.equal(eventIndex(candles,'1970-01-01T00:50:00Z','H1'),0);
  assert.equal(eventIndex(candles,'1970-01-01T01:00:00Z','H1'),1);
  assert.equal(eventIndex(candles,'1970-01-01T01:00:00Z','H1',true),0);
});
test('events in gaps or outside history are not snapped', () => {
  const candles=[{time:0},{time:7200}];
  assert.equal(eventIndex(candles,'1970-01-01T01:30:00Z','H1'),null);
  assert.equal(eventIndex([], '1970-01-01T00:00:00Z','H1'),null);
});
