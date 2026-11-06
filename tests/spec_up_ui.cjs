// Node-only Alpine factory test: no browser, network, or DOM writes.
// 시세 비용(스타포스 기대 비용·노작 시세 기본값·연마석 비용)은 화면이 계산합니다. 설정은 서버(app/spec_up.py pricing_config)에서 받습니다.
const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const { execFileSync } = require('node:child_process');

const python = ['.venv/Scripts/python.exe', '.venv/bin/python'].find(p => fs.existsSync(p)) || 'python';
const pricing = JSON.parse(execFileSync(python, ['-c',
    'import json; from app.spec_up import pricing_config; print(json.dumps(pricing_config()))'],
    { encoding: 'utf8', env: { ...process.env, PYTHONUTF8: '1' } }));

let factory;
vm.runInNewContext(fs.readFileSync('app/static/js/app.js', 'utf8'), {
    document: { addEventListener: (_name, callback) => callback() },
    Alpine: { data: (_name, callback) => { factory = callback; } },
    window: { MECULATOR_PRICING: pricing },
    localStorage: { setItem() {}, getItem() { return null; } },
});
const app = factory();

// 1. 스타포스 기대 비용 = starforce.gg/ev 기본 화면(140제 · 노작 1억 · 샤타 · 강화 최적화)의 누적 기대값
for (const [end, eok] of [[13, 0.22], [15, 1.48], [18, 6.08], [22, 59.08], [25, 948.55]]) {
    assert.ok(Math.abs(app.starforceCost(140, 1e8, 12, end) / 1e8 - eok) < 0.006, `12→${end}: ${app.starforceCost(140, 1e8, 12, end) / 1e8}`);
}
assert.equal(app.starforceCost(140, 1e8, 0, 12), 0); // 12성 미만은 계산하지 않음
const sum = [22, 23, 24].reduce((total, star) => total + app.starforceCost(200, 15e8, star, star + 1), 0);
assert.ok(Math.abs(sum - app.starforceCost(200, 15e8, 22, 25)) < 1e-3); // 단계 합 = 누적
console.log('starforce expected cost matches starforce.gg');

// 2. 노작 시세 기본값: 이름 + 부위 또는 장착 칸(무기류), 없으면 레벨별 기본값
const lookup = (name, part, slot, level) => app.defaultBasePrice({ name, part, slot, level });
assert.equal(JSON.stringify(lookup('에테르넬 시프반다나', '모자', '모자', 250)), JSON.stringify({ price: 0.3, registered: true }));
assert.equal(JSON.stringify(lookup('에테르넬 시프글러브', '장갑', '장갑', 250)), JSON.stringify({ price: 20, registered: true }));
assert.equal(JSON.stringify(lookup('데스티니 대거', '단검', '무기', 250)), JSON.stringify({ price: 100, registered: true }));
assert.equal(JSON.stringify(lookup('처음 보는 반지', '반지', '반지1', 200)), JSON.stringify({ price: 0.1, registered: false }));
console.log('clean-item default prices: OK');

// 3. 정렬은 100억당 최종뎀, 비용은 시세로 바로 계산 (노작·연마석 모두)
app.report = {spec_up: {unit: 1e10}, results: [
    {slot: 'a', name: '처음 보는 반지', part: '반지', level: 140,
     spec_up: [{axis: '스타포스', star: 21, target_star: 22, level: 140, gain: 100, final_damage: 0.5}]},
    {slot: 'b', name: 'B', spec_up: [{axis: '잠재', gain: 1, final_damage: 2, cost: 1e10}]},
]};
const star = () => app.specUpActions().find(a => a.item.slot === 'a');
assert.equal(star().cost, app.starforceCost(140, 1e7, 21, 22)); // 기본값 140제 0.1억
app.setSpecUpPrice({ item: { name: '처음 보는 반지' } }, 1);
assert.equal(star().cost, app.starforceCost(140, 1e8, 21, 22)); // 바꾼 시세로 바로
app.resetSpecUpPrice({ item: { name: '처음 보는 반지' } });
assert.equal(star().cost, app.starforceCost(140, 1e7, 21, 22));
app.setSpecUpPrice({ item: { name: '처음 보는 반지' } }, 1e6); // 노작 100조 → 파괴 비용이 커져 뒤로
assert.equal(app.specUpActions()[0].item.slot, 'b');
app.resetSpecUpPrice({ item: { name: '처음 보는 반지' } });
app.report.results[1].spec_up = [{axis: '시드링', gain: 1, final_damage: 1, material: {name: '생명의 연마석', count: 10, meso: 5e9}}];
const ring = () => app.specUpActions().find(a => a.item.slot === 'b');
assert.equal(ring().cost, 10 * pricing.materials['생명의 연마석'] * 1e8 + 5e9); // 기본 연마석 시세
app.setMaterialPrice({ material: { name: '생명의 연마석' } }, 1);
assert.equal(ring().cost, 10 * 1e8 + 5e9);
console.log('spec-up sorting and price edits: OK');

// 4. 같은 아이템 스타포스는 낮은 성부터, 연속이면 한 카드로 합치고 사이에 다른 추천이 끼면 나눔
const stage = (from, damage) => ({axis: '스타포스', star: from, target_star: from + 1, level: 200, gain: damage * 10, final_damage: damage});
app.specUpMaterials = {};
app.report = {spec_up: {unit: 1e10, final_damage_per_pct: .1}, results: [
    {slot: 'a', name: 'A', level: 200, spec_up: [stage(22, 3), stage(23, 4)]},
    {slot: 'b', name: 'B', spec_up: [{axis: '잠재', gain: 20, final_damage: 0.01, cost: 1e10}]},
]};
let chain = app.specUpActions();
assert.equal(chain.length, 2);
assert.equal(chain[0].label, '22→24성');
assert.ok(Math.abs(chain[0].cost - app.starforceCost(200, 1e7, 22, 24)) < 1e-3);
assert.ok(Math.abs(chain[0].final_damage - 7.12) < 1e-9);
assert.equal(app.specUpActions(true).length, 3);
// 잠재 효율이 22→23 과 23→24 사이면 잠재가 두 단계 사이에 끼어 카드가 나뉩니다
app.report.results[0].spec_up[1] = stage(23, 1e-9);
const efficiency22 = 3 / (app.starforceCost(200, 1e7, 22, 23) / 1e10);
app.report.results[1].spec_up[0].final_damage = efficiency22 / 2;  // 잠재 비용 100억 → 100억당 = 22→23 의 절반
chain = app.specUpActions();
assert.equal(chain.map(a => a.item.slot).join(','), 'a,b,a');
assert.equal(chain[2].star, 23);
console.log('sequential stars: unlock, merge, interleave OK');

// 5. 매물 교체: 비용 = 노작 + 스타포스 0→목표 성 + 서버 큐브·불꽃 기댓값, 아이템마다 효율이 가장 좋은 등급 하나만
app.specUpPrices = {};
app.report = {spec_up: {unit: 1e10}, results: [
    {slot: 'a', name: '처음 보는 반지', part: '반지', level: 200, spec_up: [
        {axis: '매물 교체', grade: 1, level: 200, star: 0, target_star: 17, listing_fixed: 5e9, gain: 10, final_damage: 1},
        {axis: '매물 교체', grade: 2, level: 200, star: 0, target_star: 22, listing_fixed: 9e9, gain: 30, final_damage: 3},
    ]},
]};
const listings = app.specUpActions();
assert.equal(listings.length, 1);
const expectedCost = 1e7 + app.starforceCost(200, 1e7, 0, listings[0].target_star) + listings[0].listing_fixed;  // 200제 반지 기본 노작 0.1억
assert.ok(Math.abs(listings[0].cost - expectedCost) < 1e-3);
assert.equal(app.specUpActions(true).length, 2);
console.log('listing cost and best grade per item: OK');
