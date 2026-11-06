// Node-only Alpine factory test: no browser, network, or DOM writes.
// 스타포스 기대 비용은 Python(app/starforce_cost.py)에서 설정과 기준값을 받아 JS 계산과 대조합니다.
const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const { execFileSync } = require('node:child_process');

const python = ['.venv/Scripts/python.exe', '.venv/bin/python'].find(p => fs.existsSync(p)) || 'python';
const reference = JSON.parse(execFileSync(python, ['-c', `
import json
from app.starforce_cost import client_config, expected_cost
cases = [(140, 1e8, 12, 22), (140, 1e8, 12, 25), (140, 1e8, 21, 22), (200, 15e8, 17, 22), (200, 15e8, 22, 25),
         (250, 100e8, 22, 26), (250, 3e7, 15, 18), (160, 18e8, 12, 30), (145, 5e8, 18, 23), (200, 1e8, 10, 13)]
print(json.dumps({"config": client_config(), "cases": [[*c, expected_cost(*c)] for c in cases]}))
`], { encoding: 'utf8', env: { ...process.env, PYTHONUTF8: '1' } }));

let factory;
vm.runInNewContext(fs.readFileSync('app/static/js/app.js', 'utf8'), {
    document: { addEventListener: (_name, callback) => callback() },
    Alpine: { data: (_name, callback) => { factory = callback; } },
    window: { MECULATOR_STARFORCE: reference.config },
    localStorage: { setItem() {}, getItem() { return null; } },
});
const app = factory();

// 1. JS 기대 비용 = Python 기대 비용 (starforce.gg 기준값 140제·노작 1억 12→22성 59.08억 포함)
for (const [level, base, start, end, expected] of reference.cases) {
    const actual = app.starforceCost(level, base, start, end);
    assert.ok(Math.abs(actual - expected) <= Math.max(1, expected * 1e-12), `${level} ${base} ${start}→${end}: ${actual} != ${expected}`);
}
assert.ok(Math.abs(app.starforceCost(140, 1e8, 12, 22) / 1e8 - 59.08) < 0.01);
console.log('starforce expected cost: JS matches Python');

// 2. 정렬은 100억당 최종뎀, 노작 시세를 바꾸면 그 자리에서 다시 계산
app.report = {spec_up: {unit: 1e10}, results: [
    {slot: 'a', name: 'A', spec_up: [{axis: '스타포스', star: 21, target_star: 22, level: 140, base_price: 1e8, gain: 100, final_damage: 0.5, cost: 1e10}]},
    {slot: 'b', name: 'B', spec_up: [{axis: '잠재', gain: 1, final_damage: 2, cost: 1e10}]},
]};
assert.equal(app.specUpActions()[0].item.slot, 'b'); // not stat gain
app.setSpecUpPrice({ item: { name: 'A' } }, 1); // 노작 1억 → 140제 21→22성 약 24억
const repriced = app.specUpActions().find(a => a.item.slot === 'a');
assert.equal(repriced.cost, app.starforceCost(140, 1e8, 21, 22));
assert.equal(app.specUpActions()[0].item.slot, 'a'); // 싸진 스타포스가 1위로
app.resetSpecUpPrice({ item: { name: 'A' } });
assert.equal(app.specUpActions().find(a => a.item.slot === 'a').cost, 1e10); // 기본 시세로 돌아감
app.report.results[0].spec_up[0].material = {name: 'test', count: 2, price: 1e8, meso: 1e8};
app.specUpMaterials.test = 100;
assert.equal(app.specUpActions()[0].item.slot, 'b'); // material override too
console.log('spec-up UI sorting and price overrides: OK');

// 3. 같은 아이템 스타포스는 낮은 성부터, 연속이면 한 카드로 합치고 사이에 다른 추천이 끼면 나눔
const stage = (star, damage) => ({axis: '스타포스', star, target_star: star + 1, level: 200, base_price: 1e8, cost: 1e10, gain: damage * 10, final_damage: damage});
app.specUpMaterials = {};
app.report = {spec_up: {unit: 1e10, final_damage_per_pct: .1}, results: [
    {slot: 'a', name: 'A', spec_up: [stage(22, 3), stage(23, 4)]},
    {slot: 'b', name: 'B', spec_up: [{axis: '잠재', gain: 20, final_damage: 2, cost: 1e10}]}
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
app.setSpecUpPrice({ item: { name: 'A' } }, 1e6); // 노작 100조 → 파괴 비용이 커져 스타포스가 뒤로
assert.equal(app.specUpActions()[0].item.slot, 'b');
console.log('sequential stars: unlock, merge, interleave, repricing OK');
