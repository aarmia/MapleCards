document.addEventListener('alpine:init', () => {
    Alpine.data('meculatorApp', () => ({
        nickname: '',
        report: null,
        loading: false,
        errorMessage: '',
        activeRequestNickname: '',
        requestController: null,
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
            this.loading = false;
            this.activeRequestNickname = '';
            this.viewMode = 'top5';
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
            this.errorMessage = '';
            this.viewMode = 'top5';

            try {
                const res = await fetch(`/check-items/${encodeURIComponent(trimmedNickname)}`, {
                    signal: controller.signal
                });
                const data = await res.json();

                if (!res.ok) {
                    throw new Error('서버 요청에 실패했습니다. 잠시 후 다시 시도해주세요.');
                }

                if (data?.error) {
                    throw new Error(data.error);
                }

                this.report = data;
            } catch(e) {
                if (e.name === 'AbortError') {
                    return;
                }

                this.report = null;
                this.errorMessage = e.message || '진단 실패: 서버 연결을 확인하세요.';
            } finally {
                if (this.requestController === controller) {
                    this.requestController = null;
                    this.loading = false;
                    this.activeRequestNickname = '';
                }
            }
        },

        getFilteredItems() {
            if (!this.report || !this.report.results) return [];
            if (this.viewMode === 'wse') {
                const filtered = this.report.results.filter(item => item.is_wse === true);
                return [...filtered].reverse();
            }
            if (this.viewMode === 'special') {
                return this.report.results.filter(item => item.is_special === true);
            }
            const nonWseItems = this.report.results.filter(item => !item.is_wse);
            if (this.viewMode === 'all') return nonWseItems;
            return nonWseItems.slice(0, 5);
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
