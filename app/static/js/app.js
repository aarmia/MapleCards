// 시세로 정해지는 비용(스타포스 기대 비용·시드링 연마석 비용)은 모두 화면이 계산합니다. 서버는 설정만 페이지에 넣습니다:
// window.MECULATOR_PRICING = { starforce: 확률·비용 분모·이벤트·파괴 방지·복구, base_price: 노작 시세 기본값 표, materials: 연마석 기본 시세(억) }
// (app/data/starforce_cost.toml · spec_up.toml). 스타포스 계산은 starforce.gg/ev 와 같은 방식이며 tests/spec_up_ui.cjs 가 그 값과 대조합니다.
const pricing = () => (typeof window !== 'undefined' && window.MECULATOR_PRICING) || null;
const STARFORCE_MEMO = new Map();
const sfRound = (x) => Math.floor(x + 0.5);

function sfStep(cfg, star, level, prevent) {
    let [success, , destroy] = cfg.odds[star];
    if (star <= cfg.event.destroy_max_star) destroy *= cfg.event.destroy_multiplier;
    if (prevent) destroy = 0;
    const base = sfRound((1000 + level ** 3 * (star + 1) ** 2.7 / cfg.cost_denominator[star]) / 100) * 100;
    return [success, destroy, sfRound(base * cfg.event.cost_multiplier + (prevent ? 2 * base : 0))];
}

function sfRestore(cfg, star, level, basePrice) {
    const meso = (cfg.restore.meso[level] || {})[star];
    return meso === undefined ? null : basePrice * cfg.restore.items[star] + meso * cfg.event.restore_multiplier;
}

// 성마다 star → star + 1 기대 메소. 15~17성은 파괴 방지·복구·그냥 중 최소, 18~22성은 복구가 더 싸면 복구 (starforce_cost.optimal_plan).
function sfStepCosts(cfg, level, basePrice) {
    const key = level + ':' + basePrice;
    if (STARFORCE_MEMO.has(key)) return STARFORCE_MEMO.get(key);
    const prevent = new Set(), restore = new Set();
    let cumulative = 0;
    for (let star = 12; star < 23; star++) {
        const [s, d, c] = sfStep(cfg, star, level, false);
        const options = [[(d * (basePrice + cumulative) + c) / s, 2]];
        const restoreCost = star >= 15 ? sfRestore(cfg, star, level, basePrice) : null;
        if (restoreCost !== null) options.push([(d * restoreCost + c) / s, 1]);
        if (cfg.prevention.stars.includes(star)) {
            const [ps, pd, pc] = sfStep(cfg, star, level, true);
            options.push([(pd * (basePrice + cumulative) + pc) / ps, 0]);
        }
        options.sort((a, b) => a[0] - b[0] || a[1] - b[1]);  // 같으면 파괴 방지 → 복구 → 그냥
        if (options[0][1] === 0) prevent.add(star); else if (options[0][1] === 1) restore.add(star);
        cumulative += options[0][0];
    }
    const costs = {};
    let total = 0, restored22 = null, since22 = 0;
    for (let star = 12; star < 30; star++) {
        const [s, d, c] = sfStep(cfg, star, level, prevent.has(star));
        const restoreCost = restore.has(star) ? sfRestore(cfg, star, level, basePrice) : null;
        let expected;
        if (restoreCost !== null) {
            expected = (d * restoreCost + c) / s;
            if (star === 22) { restored22 = restoreCost; since22 = 0; }
        } else if (restored22 !== null) {
            expected = (d * (restored22 + since22) + c) / s;
        } else {
            expected = (d * (basePrice + total) + c) / s;
        }
        costs[star] = expected;
        total += expected;
        since22 += expected;
    }
    if (STARFORCE_MEMO.size > 256) STARFORCE_MEMO.clear();
    STARFORCE_MEMO.set(key, costs);
    return costs;
}

// 티어 이름 → 화면 스타일. 각 티어의 점수 기준은 서버의 app/data/grades.toml 이 정합니다.
const CARD_CLASS = {
    transcendent: 'border-[3px] border-indigo-500 shadow-[0_0_15px_rgba(99,102,241,0.4)] bg-indigo-50/30',
    legendary: 'border-[3px] border-green-500 shadow-[0_0_10px_rgba(34,197,94,0.2)] bg-green-50/30',
    unique: 'border-[3px] border-yellow-400 shadow-[0_0_10px_rgba(250,204,21,0.2)] bg-yellow-50/30',
    epic: 'border-[3px] border-purple-500 shadow-[0_0_10px_rgba(168,85,247,0.2)] bg-purple-50/30',
    none: 'border border-slate-200 bg-slate-50',
};
document.addEventListener('alpine:init', () => {
    Alpine.data('meculatorApp', () => ({
        nickname: '',
        report: null,
        loading: false,
        errorMessage: '',
        errorStatus: null,
        errorCode: null,
        retryCountdown: 0,
        retryTimer: null,
        autoRetried: false,
        activeRequestNickname: '',
        requestController: null,
        requestId: '',
        statusPollTimer: null,
        loadingStage: 'character',
        queuePosition: null,
        queueEstimatedWait: null,
        viewMode: 'equip',
        // 스펙업 추천: 사용자가 바꾼 노작 시세(아이템 이름 → 억)와 그 시세로 다시 계산한 스타포스 비용(슬롯 → 메소)
        specUpExpanded: false,
        specUpPrices: {},
        specUpMaterials: {},  // 사용자가 바꾼 연마석 시세 (이름 → 억)
        // 시세 입력 중에는 카드 순서를 고정하고 비용·효율만 바로 바꿉니다. 순서가 바뀌어 카드가 움직이면 입력칸 포커스가 풀리기 때문입니다.
        specUpFrozen: null,
        specUpFreezeToken: 0,
        // 장비창 배치 [슬롯, 열, 행]. 7열 격자: 1·2열 왼쪽 블록, 3~5열 캐릭터(1~4행)와 무기류(5행), 6·7열 오른쪽 블록.
        // 슬롯 이름은 넥슨 응답의 item_equipment_slot 과 같고, 칭호는 장비창에 보이지 않습니다.
        equipLayout: [
            ['반지1', 1, 1], ['얼굴장식', 2, 1], ['모자', 6, 1], ['망토', 7, 1],
            ['반지2', 1, 2], ['눈장식', 2, 2], ['상의', 6, 2], ['장갑', 7, 2],
            ['반지3', 1, 3], ['귀고리', 2, 3], ['하의', 6, 3], ['신발', 7, 3],
            ['반지4', 1, 4], ['펜던트', 2, 4], ['어깨장식', 6, 4], ['뱃지', 7, 4],
            ['벨트', 1, 5], ['펜던트2', 2, 5], ['무기', 3, 5], ['보조무기', 4, 5], ['엠블렘', 5, 5], ['훈장', 6, 5], ['기계 심장', 7, 5],
            ['포켓 아이템', 1, 6], ['예비 특수 반지', 2, 6],
        ],
        showHelp: false,
        helpTab: 'guide',
        showDetail: false,
        showPowerNote: false,
        selectedItem: null,
        labelMap: {
            'str': 'STR', 'dex': 'DEX', 'int': 'INT', 'luk': 'LUK',
            'max_hp': '최대 HP', 'max_mp': '최대 MP',
            'attack_power': '공격력', 'magic_power': '마력',
            'armor': '방어력', 'speed': '이동속도', 'jump': '점프력',
            'boss_damage': '보스 공격력(%)', 'all_stat': '올스탯(%)',
            'ignore_monster_armor': '방어율 무시(%)', 'damage': '데미지(%)'
        },

        getTodayDate() {
            const now = new Date();
            return `${now.getFullYear()}.${String(now.getMonth() + 1).padStart(2, '0')}.${String(now.getDate()).padStart(2, '0')}`;
        },

        reset() {
            if (this.requestController) {
                this.requestController.abort();
                this.requestController = null;
            }
            this.report = null;
            this.nickname = '';
            this.errorMessage = '';
            this.errorStatus = null;
            this.errorCode = null;
            this.cancelRetry();
            this.autoRetried = false;
            this.loading = false;
            this.activeRequestNickname = '';
            this.stopStatusPolling();
            this.requestId = '';
            this.loadingStage = 'character';
            this.queuePosition = null;
            this.queueEstimatedWait = null;
            this.viewMode = 'equip';
        },

        cancelRetry() {
            if (this.retryTimer) {
                clearInterval(this.retryTimer);
                this.retryTimer = null;
            }
            this.retryCountdown = 0;
        },

        scheduleRetry(seconds) {
            // 대기열 초과(503 QUEUE_*)는 서버가 알려준 시간 뒤 한 번만 자동으로 다시 시도합니다. 폭주를 막기 위해 1회로 제한합니다.
            this.cancelRetry();
            this.retryCountdown = Math.max(1, Math.min(60, Math.round(seconds)));
            this.retryTimer = setInterval(() => {
                this.retryCountdown -= 1;
                if (this.retryCountdown > 0) return;
                this.cancelRetry();
                this.autoRetried = true;
                this.fetchReport({ auto: true });
            }, 1000);
        },

        stopStatusPolling() {
            if (this.statusPollTimer) {
                clearInterval(this.statusPollTimer);
                this.statusPollTimer = null;
            }
        },

        async pollRequestStatus() {
            if (!this.requestId || !this.loading) return;
            try {
                const response = await fetch(`/request-status/${encodeURIComponent(this.requestId)}`);
                if (!response.ok) return;
                const status = await response.json();
                if (status.stage === 'waiting') {
                    this.loadingStage = 'waiting';
                    this.queuePosition = status.queue_position;
                    this.queueEstimatedWait = status.estimated_wait_seconds ?? null;
                } else if (['character', 'equipment', 'analysis'].includes(status.stage)) {
                    this.loadingStage = status.stage;
                    this.queuePosition = null;
                    this.queueEstimatedWait = null;
                }
            } catch (_) {
                // 상태 조회 실패는 본 진단 요청에 영향을 주지 않습니다.
            }
        },

        startStatusPolling() {
            this.stopStatusPolling();
            this.pollRequestStatus();
            this.statusPollTimer = setInterval(() => this.pollRequestStatus(), 350);
        },

        async fetchReport({ auto = false } = {}) {
            const trimmedNickname = this.nickname.trim();
            if (!trimmedNickname) return;

            this.cancelRetry();
            if (!auto) this.autoRetried = false;

            if (this.loading && this.requestController && trimmedNickname === this.activeRequestNickname) {
                return;
            }

            if (this.requestController) {
                this.requestController.abort();
            }

            const controller = new AbortController();
            this.requestController = controller;
            this.activeRequestNickname = trimmedNickname;
            this.nickname = trimmedNickname;
            this.loading = true;
            this.loadingStage = 'character';
            this.queuePosition = null;
            this.requestId = globalThis.crypto?.randomUUID?.() || `${Date.now()}-${Math.random().toString(16).slice(2)}`;
            this.errorMessage = '';
            this.errorStatus = null;
            this.errorCode = null;
            this.viewMode = 'equip';
            this.startStatusPolling();

            try {
                const res = await fetch(`/check-items/${encodeURIComponent(trimmedNickname)}?request_id=${encodeURIComponent(this.requestId)}`, {
                    signal: controller.signal
                });
                const data = await this.parseJsonSafely(res);

                if (!res.ok) {
                    throw this.createServerError(this.extractErrorMessage(data), res.status, data);
                }

                if (!data || data.error) {
                    throw this.createServerError(
                        this.extractErrorMessage(data, '서버 응답을 해석할 수 없습니다.'),
                        res.status,
                        data
                    );
                }

                this.loadingStage = 'analysis';
                await new Promise(resolve => setTimeout(resolve, 250));
                this.report = data;
                this.autoRetried = false;
                this.applySavedSpecUpPrices();
            } catch(e) {
                if (e.name === 'AbortError') {
                    return;
                }

                this.report = null;
                if (e.isServerError) {
                    // 서버가 상태 코드별로 만든 메시지(404/429/503/504 등)를 그대로 보여줍니다.
                    this.errorMessage = e.message;
                    this.errorStatus = e.status;
                    this.errorCode = e.code;
                    if (!this.autoRetried && (e.code === 'QUEUE_FULL' || e.code === 'QUEUE_TIMEOUT')) {
                        this.scheduleRetry(e.retryAfter || 5);
                    }
                } else {
                    this.errorMessage = '서버에 연결할 수 없습니다. 네트워크 상태를 확인한 뒤 다시 시도해주세요.';
                    this.errorStatus = null;
                    this.errorCode = null;
                }
            } finally {
                if (this.requestController === controller) {
                    this.requestController = null;
                    this.loading = false;
                    this.activeRequestNickname = '';
                    this.stopStatusPolling();
                    this.requestId = '';
                    this.queuePosition = null;
                    this.queueEstimatedWait = null;
                }
            }
        },

        async parseJsonSafely(response) {
            try {
                return await response.json();
            } catch (_) {
                // 프록시가 HTML 오류 페이지를 돌려주는 등 JSON이 아닌 응답
                return null;
            }
        },

        extractErrorMessage(data, fallback = '서버 요청에 실패했습니다. 잠시 후 다시 시도해주세요.') {
            if (typeof data?.error === 'string' && data.error) return data.error;
            if (typeof data?.detail === 'string' && data.detail) return data.detail;
            return fallback;
        },

        createServerError(message, status, data = null) {
            const error = new Error(message);
            error.isServerError = true;
            error.status = status;
            // 서버가 내려주는 기계용 코드(넥슨 OPENAPI000xx, QUEUE_FULL 등)와 재시도 권장 시간
            error.code = typeof data?.code === 'string' ? data.code : null;
            error.retryAfter = Number.isFinite(Number(data?.retry_after)) ? Number(data.retry_after) : null;
            return error;
        },

        getErrorHint() {
            switch (this.errorCode) {
                case 'QUEUE_FULL':
                case 'QUEUE_TIMEOUT':
                    return this.retryCountdown > 0
                        ? '서버가 혼잡합니다. ' + this.retryCountdown + '초 후 자동으로 다시 시도합니다.'
                        : '서버가 혼잡합니다. 잠시 후 다시 시도해주세요.';
                case 'OPENAPI00010': return '메이플스토리 점검 중에는 넥슨 API가 캐릭터 정보를 주지 않습니다. 점검이 끝난 뒤 이용해주세요.';
                case 'OPENAPI00011': return '넥슨 Open API 점검 중입니다. 점검이 끝난 뒤 이용해주세요.';
                case 'OPENAPI00009': return '넥슨 쪽 데이터가 아직 준비되지 않았습니다. 몇 분 뒤 다시 시도해주세요.';
                case 'OPENAPI00007': return '넥슨 API 호출 한도에 도달했습니다. 잠시 후 다시 시도해주세요.';
                case 'OPENAPI00002':
                case 'OPENAPI00005':
                case 'OPENAPI00006':
                case 'NEXON_KEY_MISSING': return '서버 설정 문제입니다. 계속되면 운영자에게 알려주세요.';
                default: break;
            }
            switch (this.errorStatus) {
                case 404: return '닉네임의 철자와 띄어쓰기를 확인한 뒤 다시 시도해주세요.';
                case 429: return '동시에 많은 진단 요청이 들어와 넥슨 API 호출 한도에 도달했습니다.';
                case 503: return '서버 설정 문제로 조회할 수 없습니다. 계속되면 운영자에게 알려주세요.';
                case 504: return '넥슨 API가 느리게 응답하고 있습니다. 잠시 후 다시 시도해주세요.';
                default: return '문제가 계속되면 잠시 후 다시 시도해주세요.';
            }
        },

        // 큰 메소 값을 조·억 단위로 (예: 1조 2,670억, 95.9억)
        formatMeso(value) {
            const n = Number(value) || 0;
            const jo = Math.floor(n / 1e12), eok = (n - jo * 1e12) / 1e8;
            if (jo > 0) return jo.toLocaleString() + '조' + (eok >= 1 ? ' ' + Math.round(eok).toLocaleString() + '억' : '');
            if (eok >= 100) return Math.round(eok).toLocaleString() + '억';
            if (eok >= 0.1) return eok.toFixed(1) + '억';
            return Math.round(n / 1e4).toLocaleString() + '만';
        },

        // 스펙업 추천: 100억당 최종뎀으로 정렬. 사용자 변경 시세도 같은 기준으로 반영합니다.
        // 시세로 비용·효율을 다시 매깁니다. 스타포스는 노작 시세로 star → target_star 기대 비용, 시드링은 연마석 시세, 잠재·추옵은 서버가 준 고정 비용.
        priceAction(action, unit = this.report?.spec_up?.unit || 1e10) {
            let cost = action.cost;
            if (action.axis === '스타포스') {
                cost = this.starforceCost(action.level, Math.round(this.specUpPrice(action) * 1e8), action.star, action.target_star ?? action.star + 1);
            }
            if (action.material) cost = action.material.count * this.materialPrice(action) * 1e8 + action.material.meso;
            if (action.listing_fixed !== undefined) {  // 매물 = 직접 만드는 기댓값: (노작 + 스타포스 0→목표 성) × 부위 수 + 큐브·불꽃·주문서(서버)
                const craft = (priced, level) => {
                    const clean = Math.round(this.specUpPrice(priced) * 1e8);
                    return clean + this.starforceCost(level, clean, 0, action.target_star);
                };
                cost = action.price_items  // 서로 다른 아이템 묶음(광휘 반지 + 죽음의 맹세)은 아이템마다 노작 시세
                    ? action.price_items.reduce((sum, item) => sum + craft({ price_item: item }, item.level), 0) + action.listing_fixed
                    : craft(action, action.level) * (action.pieces || 1) + action.listing_fixed;
            }
            return { ...action, cost, efficiency: cost > 0 ? action.gain / (cost / unit) : 0,
                damage_efficiency: cost > 0 ? action.final_damage / (cost / unit) : 0 };
        },

        // 화면에 보이는 목록: 시세 입력 중이면 고정한 순서 그대로 비용만 다시 계산, 아니면 전체를 다시 정렬
        specUpVisibleActions() {
            return this.specUpFrozen ? this.specUpFrozen.map(action => this.priceAction(action)) : this.specUpActions();
        },

        freezeSpecUp(actions) {
            this.specUpFreezeToken += 1;
            if (!this.specUpFrozen) this.specUpFrozen = actions;
        },

        // 입력칸을 벗어나면 순서를 다시 정합니다. 같은 카드의 '기본 시세로' 버튼 클릭이 먼저 처리되도록 잠깐 기다립니다.
        unfreezeSpecUp() {
            const token = this.specUpFreezeToken;
            setTimeout(() => { if (this.specUpFreezeToken === token) this.specUpFrozen = null; }, 200);
        },

        // 시세 입력값 검사: 숫자와 소수점 하나만 남깁니다 (입력칸에도 바로 반영). 반영할 수 있는 숫자인지는 priceValid 로 봅니다.
        cleanPriceInput(event) {
            const el = event.target;
            let text = el.value.replace(/[^0-9.]/g, '');
            const dot = text.indexOf('.');
            if (dot >= 0) text = text.slice(0, dot + 1) + text.slice(dot + 1).replace(/\./g, '');
            if (text !== el.value) el.value = text;
            return text;
        },

        priceValid(text) {
            return text !== '' && text !== '.' && Number.isFinite(Number(text));
        },

        specUpActions(raw = false) {
            if (!this.report?.results) return [];
            const unit = this.report.spec_up?.unit || 1e10;
            const rows = [];
            for (const item of this.report.results) {
                for (const action of item.spec_up || []) rows.push(this.priceAction({ ...action, item }, unit));
            }
            if (raw) return rows;
            // 매물 교체는 아이템마다 효율이 가장 좋은 등급 하나만 남깁니다 (시세에 따라 바뀌므로 화면에서 고릅니다).
            const bestListing = {};
            for (const a of rows) {
                if (a.listing_fixed === undefined) continue;
                const key = a.item.slot + a.axis + (a.slots ? a.slots.length : '') + (a.price_item ? a.price_item.name : '');  // 매물·상위 교체(목표 아이템별)·묶음 크기를 따로
                const kept = bestListing[key];
                if (!kept || a.damage_efficiency > kept.damage_efficiency) bestListing[key] = a;
            }
            for (let i = rows.length - 1; i >= 0; i--) {
                const key = rows[i].item.slot + rows[i].axis + (rows[i].slots ? rows[i].slots.length : '') + (rows[i].price_item ? rows[i].price_item.name : '');
                if (rows[i].listing_fixed !== undefined && bestListing[key] !== rows[i]) rows.splice(i, 1);
            }
            // 같은 아이템의 스타포스는 낮은 성부터: 슬롯별 대기열의 맨 앞만 고를 수 있습니다.
            // 광휘 교체 뒤 스타포스(after_chain)는 지금 아이템과 따로 줄을 섭니다
            const queue = a => a.item.slot + '|' + (a.after_chain || '');
            const queues = {};
            for (const a of rows) if (a.axis === '스타포스') (queues[queue(a)] ||= []).push(a);
            for (const queue of Object.values(queues)) queue.sort((a, b) => a.star - b.star);
            const pending = new Set(rows), result = [], pickedChains = new Set();
            const slotsOf = a => a.slots || [a.item.slot];
            while (pending.size) {
                let action = null;
                for (const a of pending) {
                    if (a.axis === '스타포스' && queues[queue(a)][0] !== a) continue;
                    if (a.chain && [...pending].some(p => p.chain && p.chain < a.chain)) continue;  // 광휘는 정해진 순서대로
                    if (a.after_chain && !pickedChains.has(a.after_chain)) continue;  // 그 광휘 교체를 고른 뒤에만
                    if (!action || a.damage_efficiency > action.damage_efficiency) action = a;
                }
                if (!action) break;
                pending.delete(action);
                if (action.chain) pickedChains.add(action.chain);
                // 교체(매물·상위)를 추천했으면, 그 칸의 이후 추천은 더 높은 수준의 교체만 남깁니다 (지금 아이템 강화·낮은 매물 숨김)
                if (action.listing_fixed !== undefined) {
                    // 같은 아이템(예: 광휘 반지)은 한 칸에만: 다른 칸의 같은 목표 교체는 빼고 남은 후보로 추천합니다
                    const target = !action.bundle && action.price_item ? action.price_item.name : null;
                    if (target) for (const p of [...pending]) if (p.price_item && p.price_item.name === target && !p.bundle && !p.after_chain) pending.delete(p);
                    const covered = new Set(slotsOf(action));
                    for (const p of [...pending]) {
                        if (p.after_chain) continue;  // 광휘 교체 뒤 스타포스는 그 교체가 남아 있는 동안 유지
                        const higher = p.listing_fixed !== undefined && ((p.rank || 0) > (action.rank || 0)
                            || ((p.rank || 0) === (action.rank || 0) && slotsOf(p).length > slotsOf(action).length));  // 더 큰 묶음
                        if (slotsOf(p).some(slot => covered.has(slot)) && !higher) pending.delete(p);
                    }
                }
                if (action.axis === '스타포스') queues[queue(action)].shift();
                const previous = result[result.length - 1];
                if (previous && action.axis === '스타포스' && previous.axis === '스타포스'
                    && previous.item.slot === action.item.slot && previous.target_star === action.star
                    && (previous.after_chain || 0) === (action.after_chain || 0)) {
                    const perPct = previous.stat_conversion || this.report.spec_up?.final_damage_per_pct || previous.final_damage / previous.gain;
                    previous.final_damage = 100 * ((1 + previous.final_damage / 100) * (1 + action.final_damage / 100) - 1);
                    previous.gain = previous.final_damage / perPct;
                    previous.cost += action.cost;
                    previous.target_star = action.target_star;
                    previous.label = (previous.prefix || '') + previous.star + '→' + previous.target_star + '성';
                    if (previous.index_gain != null || action.index_gain != null)
                        previous.index_gain = Math.round(((previous.index_gain || 0) + (action.index_gain || 0)) * 10) / 10;
                    previous.efficiency = previous.gain / (previous.cost / unit);
                    previous.damage_efficiency = previous.final_damage / (previous.cost / unit);
                } else result.push({ ...action });
            }
            return result;
        },

        formatEfficiency(value) {
            const n = Number(value) || 0;
            return n >= 0.1 ? n.toFixed(2) : n.toFixed(3);
        },

        // 노작 시세(억): 사용자가 바꾼 값, 없으면 기본값 표(starforce_cost.toml base_price)에서 찾은 값
        // 노작 시세의 대상: 상위 교체는 바꿀 아이템(price_item), 그 외는 지금 아이템
        // 다른 아이템으로 바꾸는 추천(상위 교체와 그 뒤의 스타포스)의 바꿀 아이템 이름. 같은 아이템이면 null
        specTarget(action) {
            if (!action?.icon || action.swaps?.length > 1) return null;
            const name = action.price_item?.name;
            return name && name !== action.item?.name ? name : null;
        },

        // 카드 설명 줄: 매물 교체는 없음, 상위 교체는 세트 변화만, 나머지는 서버 설명 그대로
        specActionNote(action) {
            if (action.axis === '매물 교체') return '';
            if (action.axis === '상위 교체') return action.set_change || '';
            return action.detail || '';
        },

        // 카드 제목이 바꿀 아이템이면 설명 앞의 같은 이름을 뺍니다 ("근원의 속삭임 18→19성" → "18→19성").
        specActionLabel(action) {
            const label = action.swaps?.length > 1 ? action.spec_label : action.label;
            const target = this.specTarget(action);
            if (!target || !label?.startsWith(target)) return label;
            const rest = label.slice(target.length);
            return /^( · | (?=\d))/.test(rest) ? rest.replace(/^( · | )/, '') : label;
        },

        priceItem(action) {
            return action.price_item || action.item;
        },

        specUpPrice(action) {
            const saved = this.specUpPrices[this.priceItem(action).name];
            return saved ?? this.defaultBasePrice(this.priceItem(action)).price;
        },

        // 노작 시세 기본값: 이름에 name 이 들어 있고 (parts 가 있으면 부위나 장착 칸도 맞는) 첫 항목, 없으면 레벨별 기본값.
        // 무기류는 부위가 무기 종류(단검 등)라 장착 칸(무기·보조무기)으로 맞춥니다.
        defaultBasePrice(item) {
            const table = pricing()?.base_price;
            if (!table) return { price: 0, registered: false };
            const name = item.name || '';
            const hit = table.item.find(row => name.includes(row.name)
                && (!row.parts || row.parts.includes(item.part) || row.parts.includes(item.slot)));
            if (hit) return { price: hit.price, registered: true };
            const band = table.default.find(([floor]) => (item.level || 0) >= floor);
            return { price: band ? band[1] : 0, registered: false };
        },

        basePriceRegistered(action) {
            return this.defaultBasePrice(this.priceItem(action)).registered;
        },

        // start 성에서 end 성까지 기대 메소 (12성 미만은 0). 설정이 없으면 0 입니다.
        starforceCost(level, basePrice, start, end) {
            const cfg = pricing()?.starforce;
            if (!cfg) return 0;
            const costs = sfStepCosts(cfg, level, basePrice);
            let sum = 0;
            for (let star = Math.max(start, 12); star < Math.min(end, 30); star++) sum += costs[star];
            return sum;
        },

        specUpPriceCustom(action) {
            return this.specUpPrices[this.priceItem(action).name] !== undefined;
        },

        setSpecUpPrice(action, value) {
            const eok = Number(value);
            if (value === '' || !Number.isFinite(eok) || eok < 0) return;
            this.specUpPrices = { ...this.specUpPrices, [this.priceItem(action).name]: eok };
            try { localStorage.setItem('specup-prices', JSON.stringify(this.specUpPrices)); } catch (e) { /* 저장 불가(사생활 보호 모드 등) */ }
        },

        resetSpecUpPrice(action) {
            const prices = { ...this.specUpPrices };
            delete prices[this.priceItem(action).name];
            this.specUpPrices = prices;
            try { localStorage.setItem('specup-prices', JSON.stringify(prices)); } catch (e) { /* 무시 */ }
        },

        // 시드링 연마석 시세(억): 사용자가 바꾼 값, 없으면 기본 시세. 같은 연마석을 쓰는 모든 반지에 함께 적용됩니다.
        materialPrice(action) {
            const saved = this.specUpMaterials[action.material.name];
            return saved ?? (pricing()?.materials?.[action.material.name] || 0);
        },

        materialPriceCustom(action) {
            return this.specUpMaterials[action.material.name] !== undefined;
        },

        setMaterialPrice(action, value) {
            const eok = Number(value);
            if (value === '' || !Number.isFinite(eok) || eok < 0) return;
            this.specUpMaterials = { ...this.specUpMaterials, [action.material.name]: eok };
            try { localStorage.setItem('specup-materials', JSON.stringify(this.specUpMaterials)); } catch (e) { /* 무시 */ }
        },

        resetMaterialPrice(action) {
            const prices = { ...this.specUpMaterials };
            delete prices[action.material.name];
            this.specUpMaterials = prices;
            try { localStorage.setItem('specup-materials', JSON.stringify(prices)); } catch (e) { /* 무시 */ }
        },

        applySavedSpecUpPrices() {
            try { this.specUpMaterials = JSON.parse(localStorage.getItem('specup-materials') || '{}') || {}; } catch (e) { this.specUpMaterials = {}; }
            try { this.specUpPrices = JSON.parse(localStorage.getItem('specup-prices') || '{}') || {}; } catch (e) { this.specUpPrices = {}; }
            this.specUpExpanded = false;
            this.specUpFrozen = null;
        },

        // 모바일 장비창: 부위 그룹별로 나눕니다. 비어 있는 예비 특수 반지는 숨깁니다.
        mobileEquipGroups() {
            const groups = [
                { title: '장신구', slots: ['반지1', '반지2', '반지3', '반지4', '펜던트', '펜던트2', '얼굴장식', '눈장식', '귀고리', '벨트'] },
                { title: '방어구', slots: ['모자', '상의', '하의', '신발', '장갑', '망토', '어깨장식', '기계 심장'] },
                { title: '무기류 · 특수', slots: ['무기', '보조무기', '엠블렘', '훈장', '뱃지', '포켓 아이템', '예비 특수 반지'] },
            ];
            return groups.map(group => ({ ...group, slots: group.slots.filter(slot => slot !== '예비 특수 반지' || this.getSlotItem(slot)) }));
        },

        // 탭을 누르면 바뀐 영역이 보이도록 그 위치로 스크롤합니다(모바일에서 탭이 결과보다 위에 있기 때문).
        setViewMode(mode) {
            this.viewMode = mode;
            if (!window.matchMedia('(max-width: 767px)').matches) return;
            this.$nextTick(() => this.$refs.views?.scrollIntoView({ behavior: 'smooth', block: 'start' }));
        },

        equipCells() {
            const cells = [{ slot: 'char', style: 'grid-row: 1 / span 4; grid-column: 3 / span 3;' }];
            this.equipLayout.forEach(([slot, col, row]) => cells.push({ slot, style: `grid-row: ${row}; grid-column: ${col};` }));
            return cells;
        },

        getSlotItem(slot) {
            const results = this.report?.results || [];
            // 한벌옷은 상의 자리에 표시합니다.
            return results.find(item => item.slot === slot) || (slot === '상의' ? results.find(item => item.slot === '한벌옷') : null) || null;
        },

        // ── 상세 모달 환산 요약 ──
        axisEntries(item, category, includeHidden = false) {
            // hidden 줄(소울 옵션 첫 줄)은 점수에는 들어가지만 목록에는 보이지 않습니다.
            return (item?.scoring_basis?.recognized_options || []).filter(entry => entry.category === category && (includeHidden || !entry.hidden));
        },

        categoryColor(category) {
            return { '윗잠재': 'text-red-300', '에디셔널': 'text-blue-400', '소울': 'text-amber-400', '추가옵션': 'text-emerald-300', '익셉셔널': 'text-rose-400', '고유 옵션': 'text-violet-300' }[category] || 'text-neutral-300';
        },

        entryValueText(item, entry) {
            return '+' + entry.value + (item?.is_wse ? '% (공)' : '%');
        },

        axisTotalText(item, category) {
            const entries = this.axisEntries(item, category, true);
            if (!entries.length) return '-';
            const crit = entries.filter(entry => /크리티컬 데미지/.test(entry.option));
            const total = entries.reduce((sum, entry) => sum + entry.value, 0);
            if (item?.part === '장갑' && crit.length) {
                const rest = total - crit.reduce((sum, entry) => sum + entry.value, 0);
                return '크'.repeat(crit.length) + (rest > 0.05 ? ` + ${rest.toFixed(1)}%` : '');  // 1크 → 크, 2크 → 크크, 3크 → 크크크
            }
            return total.toFixed(1) + (item?.is_wse ? '% (공)' : '%');
        },

        addSummaryLines(item) {
            if (!item) return [];
            if (item.is_wse) return this.axisEntries(item, '추가옵션').map(entry => entry.option);
            const add = item.raw_options?.add || {};
            const main = (item.scoring_basis?.main_stat || '').toLowerCase();
            const keys = main === 'all_stat' ? ['str', 'dex', 'luk'] : main === 'hp' ? ['max_hp'] : [main];
            const lines = [];
            keys.forEach(key => { if (Number(add[key] || 0)) lines.push(`${this.labelMap[key] || key.toUpperCase()} +${add[key]}`); });
            if (Number(add.all_stat || 0)) lines.push(`올스탯 +${add.all_stat}%`);
            const attack = main === 'int' ? 'magic_power' : 'attack_power';
            if (Number(add[attack] || 0)) lines.push(`${this.labelMap[attack]} +${add[attack]}`);
            const subs = main === 'all_stat' ? [] : main === 'hp' ? ['str'] : main === 'int' ? ['luk'] : main === 'luk' ? ['dex'] : main === 'dex' ? ['str'] : ['dex'];
            const subKeys = ['섀도어', '듀얼블레이더', '카데나'].includes(this.report?.class) ? ['str', 'dex'] : subs;
            subKeys.forEach(key => { if (Number(add[key] || 0)) lines.push(`${this.labelMap[key]} +${add[key]}`); });
            return lines;
        },

        optionBlocks(item) {
            if (!item) return [];
            const raw = item.raw_options || {};
            const blocks = [
                { key: 'potential', title: 'Potential', grade: raw.potential_grade, options: (raw.potential_options || []).filter(Boolean), category: '윗잠재' },
                { key: 'additional', title: 'Additional', grade: raw.additional_grade, options: (raw.additional_options || []).filter(Boolean), category: '에디셔널' },
                { key: 'soul', title: 'Soul', grade: raw.soul_grade || null, stage: raw.soul_stage || null, options: raw.soul_options || [], category: '소울' },
            ];
            return blocks.filter(block => block.options.length || this.axisEntries(item, block.category).length);
        },

        // 원본 잠재 줄과 환산 줄을 한 줄로 합칩니다. 같은 문구가 여러 줄이면 순서대로 짝을 짓고,
        // 짝이 없는 원본 줄은 미반영 사유(배지)나 '-' 로, 원본에 없는 환산 줄(숨김 줄 제외)은 뒤에 붙입니다.
        mergedLines(item, block) {
            const entries = this.axisEntries(item, block.category).slice();
            const unrecognized = (item?.scoring_basis?.unrecognized_options || []).filter(entry => entry.category === block.category).slice();
            const take = (list, text) => {
                const index = list.findIndex(entry => entry.option === text);
                return index < 0 ? null : list.splice(index, 1)[0];
            };
            const rows = block.options.map(text => {
                const entry = take(entries, text);
                const skipped = entry ? null : take(unrecognized, text);
                return { text, entry, skipped };
            });
            entries.forEach(entry => rows.push({ text: entry.option, entry, skipped: null }));
            return rows.map((row, index) => ({
                ...row,
                key: block.key + index,
                dot: block.key === 'soul' ? '#fbbf24' : this.getOptionLineColor(row.text, item?.level, item?.is_wse, block.grade, block.key === 'additional'),
            }));
        },

        // 잠재 카드에 줄별로 표시되지 않는 미반영 옵션 (추가옵션 등 다른 분류)
        otherUnrecognized(item) {
            const inline = new Set(this.optionBlocks(item).map(block => block.category));
            return (item?.scoring_basis?.unrecognized_options || []).filter(entry => !inline.has(entry.category));
        },

        // 상세 모달에서 기본 옵션만 보여주는 부위 (뱃지: 점수·환산 없음)
        baseOnly(item) {
            return item?.part === '뱃지';
        },

        // 상세 옵션 카드: 추가옵션 → 익셉셔널 → 윗잠재 → 에디셔널 → 소울. 모두 같은 줄 모양
        // { text, sub(환산 규칙), value(환산값) | badge(미반영 사유) } 으로 맞춥니다.
        optionCards(item) {
            if (!item || this.baseOnly(item)) return [];
            const rows = Object.fromEntries((item.scoring_basis?.breakdown || []).map(row => [row.key, row]));
            const cards = [];
            const exceptional = rows.exceptional && Number(rows.exceptional.raw) > 0 ? rows.exceptional : null;
            if (rows.add) {
                // 합산된 옵션마다 오르는 수치: 방어구·장신구는 급수, 무기는 공/마 불꽃 단계(N추)와 나머지의 데미지 %p 환산
                const entries = this.axisEntries(item, '추가옵션');
                let total = `${rows.add.raw}급`;
                let lines = entries.length
                    ? entries.map(entry => ({ text: entry.option, value: `+${entry.value}` }))
                    : this.addSummaryLines(item).map(text => ({ text }));
                if (item.is_wse) {
                    const perPct = this.report?.profile?.weapon?.boss_per_pct || 0;  // 보공·데미지 1% 의 공% 가치
                    const flat = /^(공격력|마력) \+\d+$/;
                    const tier = item.scoring_basis?.add_attack_tier;
                    const chu = tier ? `${8 - tier}추` : '단계 ?';  // 불꽃 7단계 = 1추
                    const damage = entry => perPct ? Number((entry.value / perPct).toFixed(1)) : 0;
                    const rest = entries.filter(entry => !flat.test(entry.option));
                    lines = entries.map(entry => ({ text: entry.option, value: flat.test(entry.option) ? chu : `+${damage(entry)}%p` }));
                    const sum = Number(rest.reduce((s, entry) => s + entry.value / (perPct || 1), 0).toFixed(1));
                    total = [entries.some(entry => flat.test(entry.option)) ? chu : null, rest.length ? `데미지${sum}%` : null]
                        .filter(Boolean).join(' + ') || '-';
                }
                cards.push({ key: 'add', title: '추가옵션', color: this.categoryColor('추가옵션'), dot: '#34d399', total, lines, wide: !exceptional });
            }
            if (exceptional) {
                const lines = this.exceptionalLines(item).map(text => ({ text }));
                cards.push({ key: 'exceptional', title: '익셉셔널', color: this.categoryColor('익셉셔널'), dot: '#fb7185',
                             total: `${exceptional.raw}회`, lines, wide: !rows.add });
            }
            this.optionBlocks(item).forEach(block => {
                cards.push({
                    key: block.key, title: block.category, color: this.categoryColor(block.category), grade: block.grade, stage: block.stage,
                    total: this.axisTotalText(item, block.category), wide: block.key === 'soul',
                    lines: this.mergedLines(item, block).map(line => ({
                        text: line.text, sub: line.entry?.rule, dot: line.dot,
                        value: line.entry ? this.entryValueText(item, line.entry) : null,
                        badge: !line.entry && line.skipped ? line.skipped.reason : null,
                    })),
                });
            });
            return cards.map(card => ({ ...card, lines: card.lines.map((line, index) => ({ dot: card.dot, ...line, key: card.key + index })) }));
        },

        exceptionalLines(item) {
            // 익셉셔널 강화로 오른 스탯. 네 스탯이 같으면 올스탯, 공/마·HP/MP 가 같으면 묶습니다.
            const ex = item?.raw_options?.exceptional || {};
            const n = key => Number(ex[key] || 0);
            const lines = [];
            const stats = ['str', 'dex', 'int', 'luk'].map(n);
            if (stats.every(v => v === stats[0]) && stats[0] > 0) lines.push(`올스탯 +${stats[0]}`);
            else ['str', 'dex', 'int', 'luk'].forEach(key => { if (n(key)) lines.push(`${this.labelMap[key]} +${n(key)}`); });
            if (n('max_hp') && n('max_hp') === n('max_mp')) lines.push(`최대 HP/MP +${n('max_hp')}`);
            else { if (n('max_hp')) lines.push(`최대 HP +${n('max_hp')}`); if (n('max_mp')) lines.push(`최대 MP +${n('max_mp')}`); }
            if (n('attack_power') && n('attack_power') === n('magic_power')) lines.push(`공/마 +${n('attack_power')}`);
            else { if (n('attack_power')) lines.push(`공격력 +${n('attack_power')}`); if (n('magic_power')) lines.push(`마력 +${n('magic_power')}`); }
            return lines;
        },

        init() {
            // 상세 시트는 휴대폰 뒤로가기로 닫힙니다. 열 때 기록을 하나 쌓고, 뒤로가기(popstate)에 닫습니다.
            window.addEventListener('popstate', () => { this.showDetail = false; });
            this.$watch('showDetail', open => { document.body.style.overflow = open ? 'hidden' : ''; });
        },

        openItemDetail(item) {
            this.selectedItem = item;
            if (!this.showDetail) history.pushState({ meculatorDetail: true }, '');
            this.showDetail = true;
        },

        closeDetail() {
            if (!this.showDetail) return;
            if (history.state?.meculatorDetail) history.back();  // popstate 가 showDetail 을 끕니다
            else this.showDetail = false;
        },

        getSafeValue(val) {
            return Number(val) || 0;
        },

        getTotalStat(item, key) {
            if (!item || !item.raw_options) return 0;
            const b = this.getSafeValue(item.raw_options.base?.[key]);
            const a = this.getSafeValue(item.raw_options.add?.[key]);
            const e = this.getSafeValue(item.raw_options.etc?.[key]);
            const s = this.getSafeValue(item.raw_options.starforce?.[key]);
            const ex = this.getSafeValue(item.raw_options.exceptional?.[key]);
            return b + a + e + s + ex;
        },

        formatTotalStat(item, key, label) {
            const total = this.getTotalStat(item, key);
            return label.includes('%') ? total + '%' : total;
        },

        getGrades() {
            // 서버가 페이지에 심어 둔 컷오프. 진단 응답에 같은 값이 있으면 그것을 우선합니다.
            return this.report?.grades || window.MECULATOR_GRADES || {};
        },

        pickTier(bands, value, fallback) {
            const hit = (bands || []).find(([min, , exclusive]) => exclusive ? value > min : value >= min);
            return hit ? hit[1] : fallback;
        },

        getBorderColor(score) {
            const grades = this.getGrades();
            const tier = this.pickTier(grades.card_highlight, score, grades.card_highlight_default || 'none');
            return CARD_CLASS[tier] || CARD_CLASS.none;
        },

        getGradeColor(grade) {
            if (!grade) return '#475569';
            if (grade.includes('레전드리') || grade.includes('레전더리')) return '#b2e52c';
            if (grade.includes('유니크')) return '#ffb900';
            if (grade.includes('에픽')) return '#a855f7';
            if (grade.includes('레어')) return '#3b82f6';
            return '#475569';
        },

        getOptionLineColor(optText, itemLevel, isWse, fallbackGrade, isAdditional = false) {
            if (!optText) return '#475569';

            const isMatch = (keywords) => keywords.some(k =>
                Array.isArray(k) ? k.every(word => optText.includes(word)) : optText.includes(k)
            );

            if (isWse) {
                if (isMatch(['+13%', '+12%', '올스탯 +10%', '올스탯 +9%', '공격력 +32', '마력 +32', '방어율 무시 +45%', '방어율 무시 +40%', '방어율 무시 +35%', '보스 몬스터 데미지 +45%', '보스 몬스터 데미지 +40%', '보스 몬스터 데미지 +35%'])) return '#b2e52c';
                if (isMatch(['+10%', '+9%', '올스탯 +7%', '올스탯 +6%', '방어율 무시 +30%', '보스 몬스터 데미지 +30%'])) return '#ffb900';
                if (isMatch(['+7%', '+6%', '크리티컬 확률 +9%', '크리티컬 확률 +8%', '올스탯 +4%', '올스탯 +3%', '370의 HP 회복', '195의 MP 회복', '방어율 무시 +20%', '방어율 무시 +15%'])) return '#a855f7';
                if (isMatch(['+13', '+12', '+125', '+120', '+4%', '+3%', '크리티컬 확률 +5%', '크리티컬 확률 +4%', '올스탯 +6', '올스탯 +5', '250의 HP 회복', '125의 MP 회복', '7레벨 중독'])) return '#3b82f6';
            }
            else if (isAdditional) {
                if (isMatch(['+21', '+20', '+375', '+360', '공격력 +17', '공격력 +16', '마력 +17', '마력 +16', 'STR +9%', 'DEX +9%', 'INT +9%', 'LUK +9%', 'STR +8%', 'DEX +8%', 'INT +8%', 'LUK +8%', '최대 HP +12%', '최대 HP +11%', '올스탯 +7%', '올스탯 +6%', '크리티컬 데미지', ['9레벨 당', '+2'], '재사용 대기시간 -1초', '메소', '아이템 드롭률'])) return '#b2e52c';
                if (isMatch(['+19', '+18', '+315', '+300', '공격력 +15', '공격력 +14', '마력 +15', '마력 +14', '+7%', '+6%', '최대 HP +9%', '최대 HP +8%', '올스탯 +5%', ['9레벨 당', '+1'], '+20%'])) return '#ffb900';
                if (isMatch(['+15', '+14', '+195', '+180', '방어력 +150', '방어력 +120', '공격력 +12', '공격력 +11', '마력 +12', '마력 +11', '이동속도 +9', '이동속도 +8', '점프력 +9', '+5%', '+4%', '최대 HP +6%', '최대 HP +5%', '올스탯 +3%', '올스탯 +2%'])) return '#a855f7';
                if (isMatch(['+11', '+10', '+125', '+100', '방어력 +125', '방어력 +100', '공격력 +10', '마력 +10', '+3%', '+2%', '올스탯 +4', '올스탯 +3'])) return '#3b82f6';
            }
            else {
                if (isMatch(['+13%', '+12%', '올스탯 +10%', '올스탯 +9%', '크리티컬 데미지 +8%', '데미지의 20% 무시', '데미지의 40% 무시', '무적시간 +3초', '재사용 대기시간', '메소 획득량', '아이템 드롭률'])) return '#b2e52c';
                if (isMatch(['+10%', '+9%', '올스탯 +7%', '올스탯 +6%', '+34', '+32', '무적시간 +2초', '반사', '회복 스킬 효율 +30%', '샤프 아이즈', '헤이스트'])) return '#ffb900';
                if (isMatch(['+7%', '+6%', '올스탯 +4%', '올스탯 +3%', '방어력 +7%', '방어력 +6%', '100의 HP 회복', '95의 HP 회복'])) return '#a855f7';
                if (isMatch(['+13', '+12', '+125', '+120', '이동속도 +9', '이동속도 +8', '올스탯 +6', '올스탯 +5', '+4%', '+3%'])) return '#3b82f6';
            }

            return this.getGradeColor(fallbackGrade);
        },

        unrecognizedLabel(reason) {
            return { other_stat: '다른 직업 스탯', ignored: '점수 제외', unsupported: '미지원' }[reason] || reason;
        },

        unrecognizedBadgeClass(reason) {
            return reason === 'unsupported'
                ? 'bg-amber-500/20 text-amber-300 border border-amber-400/40'
                : 'bg-slate-700/60 text-slate-400 border border-slate-600';
        }
    }));
});
