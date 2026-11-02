document.addEventListener('alpine:init', () => {
    Alpine.data('meculatorApp', () => ({
        nickname: '',
        report: null,
        loading: false,
        errorMessage: '',
        errorStatus: null,
        activeRequestNickname: '',
        requestController: null,
        requestId: '',
        statusPollTimer: null,
        loadingStage: 'character',
        queuePosition: null,
        viewMode: 'top5',
        showHelp: false,
        helpTab: 'guide',
        showDetail: false,
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
            this.loading = false;
            this.activeRequestNickname = '';
            this.stopStatusPolling();
            this.requestId = '';
            this.loadingStage = 'character';
            this.queuePosition = null;
            this.viewMode = 'top5';
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
                } else if (['character', 'equipment', 'analysis'].includes(status.stage)) {
                    this.loadingStage = status.stage;
                    this.queuePosition = null;
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

        async fetchReport() {
            const trimmedNickname = this.nickname.trim();
            if (!trimmedNickname) return;

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
            this.viewMode = 'top5';
            this.startStatusPolling();

            try {
                const res = await fetch(`/check-items/${encodeURIComponent(trimmedNickname)}?request_id=${encodeURIComponent(this.requestId)}`, {
                    signal: controller.signal
                });
                const data = await this.parseJsonSafely(res);

                if (!res.ok) {
                    throw this.createServerError(this.extractErrorMessage(data), res.status);
                }

                if (!data || data.error) {
                    throw this.createServerError(
                        this.extractErrorMessage(data, '서버 응답을 해석할 수 없습니다.'),
                        res.status
                    );
                }

                this.loadingStage = 'analysis';
                await new Promise(resolve => setTimeout(resolve, 250));
                this.report = data;
            } catch(e) {
                if (e.name === 'AbortError') {
                    return;
                }

                this.report = null;
                if (e.isServerError) {
                    // 서버가 상태 코드별로 만든 메시지(404/429/503/504 등)를 그대로 보여줍니다.
                    this.errorMessage = e.message;
                    this.errorStatus = e.status;
                } else {
                    this.errorMessage = '서버에 연결할 수 없습니다. 네트워크 상태를 확인한 뒤 다시 시도해주세요.';
                    this.errorStatus = null;
                }
            } finally {
                if (this.requestController === controller) {
                    this.requestController = null;
                    this.loading = false;
                    this.activeRequestNickname = '';
                    this.stopStatusPolling();
                    this.requestId = '';
                    this.queuePosition = null;
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

        createServerError(message, status) {
            const error = new Error(message);
            error.isServerError = true;
            error.status = status;
            return error;
        },

        getErrorHint() {
            switch (this.errorStatus) {
                case 404: return '닉네임의 철자와 띄어쓰기를 확인한 뒤 다시 시도해주세요.';
                case 429: return '동시에 많은 진단 요청이 들어와 넥슨 API 호출 한도에 도달했습니다.';
                case 503: return '서버 설정 문제로 조회할 수 없습니다. 계속되면 운영자에게 알려주세요.';
                case 504: return '넥슨 API가 느리게 응답하고 있습니다. 잠시 후 다시 시도해주세요.';
                default: return '문제가 계속되면 잠시 후 다시 시도해주세요.';
            }
        },

        getFilteredItems() {
            if (!this.report || !this.report.results) return [];
            const nonWseItems = this.report.results.filter(item => !item.is_wse && !item.is_special);
            if (this.viewMode === 'all') return nonWseItems;
            return nonWseItems.slice(0, 5);
        },

        getWseItems() {
            if (!this.report?.results) return [];
            return this.report.results.filter(item => item.is_wse && !item.is_special).reverse();
        },

        getSpecialItems() {
            if (!this.report?.results) return [];
            return this.report.results.filter(item => item.is_special);
        },

        openItemDetail(item) {
            this.selectedItem = item;
            this.showDetail = true;
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

        getStatColor(val, key) {
            const s = parseFloat(val);
            if (key === 'pot') {
                if (s > 118) return 'bg-ultimate';
                if (s >= 100) return 'bg-emerald-500';
                if (s >= 92) return 'bg-yellow-400';
                if (s >= 66) return 'bg-purple-500';
                if (s >= 50) return 'bg-sky-400';
                return 'bg-red-500';
            } else if (key === 'pot_additional') {
                if (s > 48) return 'bg-ultimate';
                if (s >= 32) return 'bg-emerald-500';
                if (s >= 25) return 'bg-yellow-400';
                if (s >= 19) return 'bg-purple-500';
                if (s >= 9) return 'bg-sky-400';
                return 'bg-red-500';
            } else if (key === 'star') {
                if (s >= 100) return 'bg-emerald-500';
                if (s >= 90) return 'bg-yellow-400';
                if (s >= 75) return 'bg-purple-500';
                if (s >= 50) return 'bg-sky-400';
                return 'bg-red-500';
            } else {
                if (s > 108) return 'bg-ultimate';
                if (s >= 105) return 'bg-emerald-500';
                if (s >= 90) return 'bg-yellow-400';
                if (s >= 75) return 'bg-purple-500';
                if (s >= 50) return 'bg-sky-400';
                return 'bg-red-500';
            }
        },

        getStatWidth(val, key) {
            const s = parseFloat(val);
            let max = 110.0;
            if (key === 'pot') max = 128.7;
            if (key === 'pot_additional') max = 55.0;
            return Math.min((s / max) * 100, 100);
        },

        getBorderColor(score) {
            if (score >= 365) return 'border-[3px] border-indigo-500 shadow-[0_0_15px_rgba(99,102,241,0.4)] bg-indigo-50/30';
            if (score >= 350) return 'border-[3px] border-green-500 shadow-[0_0_10px_rgba(34,197,94,0.2)] bg-green-50/30';
            if (score >= 300) return 'border-[3px] border-yellow-400 shadow-[0_0_10px_rgba(250,204,21,0.2)] bg-yellow-50/30';
            if (score >= 250) return 'border-[3px] border-purple-500 shadow-[0_0_10px_rgba(168,85,247,0.2)] bg-purple-50/30';
            return 'border border-slate-200 bg-slate-50';
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

        getGuideStyle(guideText) {
            if (!guideText) return '';
            if (guideText.includes('종결') || guideText.includes('완벽')) return 'bg-indigo-50 text-indigo-700 border-indigo-100';
            if (guideText.includes('교체') || guideText.includes('시급')) return 'bg-red-50 text-red-700 border-red-100';
            if (guideText.includes('강화') || guideText.includes('권장')) return 'bg-blue-50 text-blue-700 border-blue-100';
            return 'bg-emerald-50 text-emerald-700 border-emerald-100';
        },

        getGradeFromScore(score) {
            if (score >= 360) return 'SSS+';
            if (score >= 350) return 'SSS';
            if (score >= 300) return 'SS';
            if (score >= 250) return 'S';
            if (score >= 200) return 'A';
            if (score >= 150) return 'B';
            if (score >= 100) return 'C';
            if (score >= 50) return 'D';
            return 'F';
        },

        getScoreDisplayData(score) {
            // 350 이상: base.html의 bg-ultimate 클래스를 텍스트 그라데이션으로 활용
            if (score >= 350) return { class: 'bg-ultimate text-transparent bg-clip-text', style: '' };
            if (score >= 300) return { class: '', style: 'color: #b2e52c;' };
            if (score >= 200) return { class: '', style: 'color: #ffb900;' };
            if (score >= 100) return { class: '', style: 'color: #a855f7;' };
            return { class: '', style: 'color: #3b82f6;' };
        }
    }));
});
