"""Frozen optimized contexts from 2026-11-05; detects converter drift, not API/optimizer drift.

Expected values use the restored buffs (all stat 0 / attack 355 / crit 54),
NOT assertions that the old reference screenshots match.
Run scripts.audit_efficiency separately for fresh API comparison.
"""
import unittest

from app.potential import StatContext, convert_line
from scripts.audit_efficiency import REFERENCES, options


SNAPSHOTS = [
    ("지아름", 298, [8794.485540334856, 9.530000000000001, 30155, 113966.44719939119,
                    13258.789999999999, 11193.04, 179.5, 100, 1489, 2.27, .1333506086992039, .07056663168940187],
     [10.77, 8, 6.94, 6.80, 6, 5.67, 4.00, 3.59, 3.50, 3.47, 2.10, 1.89, 1.07, .80, .49, .24, .15, .13]),
    ("강은호", 299, [9674.134790528233, 10.98, 31885, 138107, 16356, 25080, 207.9, 102, 1494,
                    3.08, .13938751500213423, .0662568306010929],
     [11.03, 8, 7.29, 6.84, 6, 5.70, 2.75, 3.68, 2.40, 3.64, 2.21, 1.99, 1.12, .84, .48, .24, .15, .13]),
    ("세나", 297, [8883.964143426296, 10.04, 31280, 120475, 19192, 16751.92, 144.9, 121, 1484,
                  2.8200000000000003, .15324514038607437, .08391434262948208],
     [12.56, 8, 7.24, 6.92, 6, 5.77, 3.55, 4.19, 3.11, 3.62, 2.19, 1.97, 1.23, .92, .61, .30, .18, .17]),
]
FIELDS = ('base_stat', 'stat_pct', 'exempt_stat', 'final_main', 'final_sub', 'attack', 'crit_damage',
          'crit_rate', 'ap_stat', 'attack_multiplier', 'sub_pct_ratio', 'sub_flat_ratio')


class EfficiencySnapshots(unittest.TestCase):
    def test_three_frozen_contexts_eighteen_options_each(self):
        for name, level, fields, expected in SNAPSHOTS:
            main, sub, _ = REFERENCES[name]
            context = StatContext(main_stat=main.lower(), main_names=(main,), sub_names=(sub,),
                                  stat_weight=4, is_archer=False, **dict(zip(FIELDS, fields)))
            for option, value in zip(options(main, sub), expected):
                with self.subTest(character=name, option=option):
                    self.assertEqual(round(convert_line(option, context, level)[0], 2), value)
