// Node-only Alpine factory test: no browser, network, or DOM writes.
const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
let factory;
vm.runInNewContext(fs.readFileSync('app/static/js/app.js', 'utf8'), {
    document: { addEventListener: (_name, callback) => callback() },
    Alpine: { data: (_name, callback) => { factory = callback; } },
});
const app = factory();
app.report = {spec_up: {unit: 1e10}, results: [
    {slot: 'a', spec_up: [{axis: '스타포스', star: 22, target_star: 23, gain: 100, final_damage: 1, cost: 1e10}]},
    {slot: 'b', spec_up: [{axis: '잠재', gain: 1, final_damage: 2, cost: 1e10}]},
]};
assert.equal(app.specUpActions()[0].item.slot, 'b'); // not stat gain
app.specUpCosts['a:23'] = 1e9;
assert.equal(app.specUpActions()[0].item.slot, 'a'); // changed price
assert.equal(app.specUpActions()[0].damage_efficiency, 10);
app.report.results[0].spec_up.push({axis: '스타포스', star: 22, target_star: 24, gain: 120, final_damage: 1.5, cost: 2e10});
assert.equal(app.specUpActions().find(a => a.target_star === 24).cost, 2e10);
app.report.results[0].spec_up.pop();
app.report.results[0].spec_up[0].material = {name: 'test', count: 2, price: 1e8, meso: 1e8};
app.specUpMaterials.test = 100;
assert.equal(app.specUpActions()[0].item.slot, 'b'); // material override too
console.log('spec-up UI sorting and price overrides: OK');
const stage = (star, damage) => ({axis: '스타포스', star, target_star: star+1, cost: 1e10, gain: damage*10, final_damage: damage});
app.specUpCosts = {};
app.report = {spec_up: {unit: 1e10, final_damage_per_pct: .1}, results: [
    {slot: 'a', name: 'A', spec_up: [stage(22, 3), stage(23, 4)]},
    {slot: 'b', spec_up: [{axis: '잠재', gain: 20, final_damage: 2, cost: 1e10}]}
]};
let chain = app.specUpActions();
assert.equal(chain.length, 2);
assert.equal(chain[0].label, '22→24성');
assert.equal(chain[0].cost, 2e10);
assert.ok(Math.abs(chain[0].final_damage - 7.12) < 1e-9);
assert.equal(app.specUpActions(true).length, 3);
app.report.results[0].spec_up[1] = stage(23, 1);
chain = app.specUpActions();
assert.equal(chain.map(a => a.item.slot).join(','), 'a,b,a');
assert.equal(chain[2].star, 23);
app.specUpCosts['a:23'] = 1e11;
assert.equal(app.specUpActions()[0].item.slot, 'b');
console.log('sequential stars: unlock, merge, interleave, repricing OK');
