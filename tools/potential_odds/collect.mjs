// 넥슨 공식 확률 안내(잠재능력 재설정·에디셔널)에서 레전드리 등급 표를 부위·레벨별로 모읍니다 (spec-up 추천의 큐브 확률).
// 페이지의 조회 함수(CUBEPROBABILITY.getDetailList)를 그대로 쓰고 요청 사이에 1초 쉽니다. 결과는 build.py 로 app/data/potential_odds.json 을 만듭니다.
// 준비: chrome --headless=new --remote-debugging-port=9222 --user-data-dir=<임시 폴더>
// 사용: node collect.mjs <black|addi> <출력 JSON> <레벨,레벨,...> [부위번호,...]   예) node collect.mjs black black.json 200,250
import { writeFileSync } from "node:fs";
const [, , page, out, levelsArg, partsArg] = process.argv;
const PARTS = { 6: "모자", 7: "상의", 8: "한벌옷", 9: "하의", 10: "신발", 11: "장갑", 12: "망토", 13: "벨트", 14: "어깨장식", 15: "얼굴장식", 16: "눈장식", 17: "귀고리", 18: "반지", 19: "펜던트", 20: "기계심장" };
const levels = levelsArg.split(",").map(Number);
const parts = partsArg ? partsArg.split(",").map(Number) : Object.keys(PARTS).map(Number);
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
for (let i = 0; i < 20; i++) { try { await fetch("http://127.0.0.1:9222/json/version"); break; } catch { await sleep(500); } }
const t = await (await fetch("http://127.0.0.1:9222/json/new?about:blank", { method: "PUT" })).json();
const ws = new WebSocket(t.webSocketDebuggerUrl); await new Promise((r) => ws.addEventListener("open", r, { once: true }));
let id = 0; const pend = new Map();
ws.addEventListener("message", (e) => { const m = JSON.parse(e.data); if (m.id && pend.has(m.id)) { pend.get(m.id)(m); pend.delete(m.id); } });
const send = (method, params = {}) => new Promise((res) => { const n = ++id; pend.set(n, res); ws.send(JSON.stringify({ id: n, method, params })); });
const ev = async (expression) => (await send("Runtime.evaluate", { expression, awaitPromise: true, returnByValue: true })).result?.result?.value;
await send("Page.enable");
await send("Page.navigate", { url: `https://maplestory.nexon.com/Guide/OtherProbability/cube/${page}` }); await sleep(4000);
const result = [];
for (const level of levels) for (const part of parts) {
  await ev(`document.querySelector('#CubeSearchGroupArea').innerHTML = ''`);
  await ev(`CUBEPROBABILITY.getDetailList(CubeItemID, 4, ${part}, ${level})`);
  let text = "";
  for (let i = 0; i < 30; i++) { await sleep(300); text = await ev(`document.querySelector('#CubeSearchGroupArea')?.innerText || ''`); if (text.includes("첫 번째 옵션")) break; }
  result.push({ level, part: PARTS[part], text });
  console.log(level, PARTS[part], text.length, (text.match(/장비 레벨([^\n]*)/) || [])[1]);
  await sleep(1000);
}
writeFileSync(out, JSON.stringify(result, null, 1));
await send("Page.close"); ws.close(); process.exit(0);
