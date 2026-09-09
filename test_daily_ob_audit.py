"""Synthetic timing/accounting regression tests; no terminal or network calls."""
import unittest
from unittest.mock import patch
import pandas as pd
from daily_ob_backtest import (run_daily_ob, filter_obs_by_displacement, bias_at_time,
                               find_invalidation_exit, find_pyramid_trigger)
from daily_ob_support import completed_bars, simulate_daily_exit, account_trades
from smc_backtest import find_swings, find_order_blocks, find_fvgs, structure_bias_series


def candles(n, freq='h', price=110):
    return pd.DataFrame({'time': pd.date_range('2025-01-01', periods=n, freq=freq, tz='UTC'),
                         'open': [price]*n, 'high': [price+1]*n,
                         'low': [price-1]*n, 'close': [price]*n})


def ob(i=0, top=95, bottom=90):
    return {'formed_at': i, 'candle_idx': max(0, i-1), 'type': 'bullish',
            'top': top, 'bottom': bottom, 'status': 'active'}


class TimingTests(unittest.TestCase):
    def test_incomplete_bar_removed(self):
        df = candles(3)
        out = completed_bars(df, 'H1', df.time.iloc[2] + pd.Timedelta(minutes=30))
        self.assertEqual(len(out), 2)

    def test_bias_waits_for_close(self):
        h = completed_bars(candles(3, 'D'), 'D1')
        self.assertEqual(bias_at_time(h, ['bearish', 'bullish', 'neutral'], h.time.iloc[1]), 'bearish')
        self.assertEqual(bias_at_time(h, ['bearish']*3, h.time.iloc[0]), 'neutral')

    def test_displacement_delays_qualification(self):
        gaps = [{'type': 'bullish', 'formed_at': 5, 'top': 96, 'bottom': 95}]
        with patch('daily_ob_backtest.find_fvgs', return_value=gaps):
            result = filter_obs_by_displacement([ob(2)], candles(8, 'D'))
        self.assertEqual(result[0]['qualified_at'], 5)
        self.assertEqual(result[0]['formed_at'], 2)

    def run_fixture(self, obs, ltf, **kw):
        with patch('daily_ob_backtest.find_order_blocks', return_value=obs):
            return run_daily_ob('TEST', htf_data=candles(8, 'D'), ltf_data=ltf,
                                max_hold_bars=2, **kw)

    def test_touch_before_ob_confirmation_ignored(self):
        ltf = candles(100)
        ltf.loc[1, 'low'] = 94  # cannot count during breakout day
        ltf.loc[25, 'low'] = 94
        result = self.run_fixture([ob()], ltf)
        self.assertEqual(result.iloc[0].entry_time, ltf.time.iloc[26])
        self.assertGreaterEqual(result.iloc[0].touch_time, result.iloc[0].ob_available_time)

    def test_future_displacement_cannot_validate_early_touch(self):
        ltf = candles(190)
        ltf.loc[25, 'low'] = 94
        ltf.loc[75, 'low'] = 94
        gaps = [{'type': 'bullish', 'formed_at': 2, 'top': 96, 'bottom': 95}]
        with patch('daily_ob_backtest.find_fvgs', return_value=gaps):
            result = self.run_fixture([ob()], ltf, require_displacement=True)
        self.assertEqual(result.iloc[0].entry_time, ltf.time.iloc[76])

    def test_candidates_selected_by_entry_not_formation(self):
        ltf = candles(190)
        ltf.loc[100, 'low'] = 94  # older OB, later trade
        ltf.loc[50, 'low'] = 104  # younger OB, earlier trade
        result = self.run_fixture([ob(), ob(1, 105, 102)], ltf)
        self.assertEqual(list(result.entry_time), [ltf.time.iloc[51], ltf.time.iloc[101]])

    def test_next_open_fill_and_killzone(self):
        ltf = candles(70)
        ltf.loc[31, 'low'] = 94  # 07:00 signal, 08:00 entry
        ltf.loc[32, ['open', 'high', 'close']] = [112, 113, 112]
        result = self.run_fixture([ob()], ltf, killzones=[(8, 9)])
        self.assertEqual(result.iloc[0].entry, 112)
        self.assertEqual(result.iloc[0].entry_time, ltf.time.iloc[32])

    def test_no_terminal_signal_fill(self):
        ltf = candles(30)
        ltf.loc[29, 'low'] = 94
        self.assertTrue(self.run_fixture([ob()], ltf).empty)

    def test_future_tail_does_not_change_earlier_entries(self):
        ltf = candles(180)
        ltf.loc[30, 'low'] = 94
        short = self.run_fixture([ob()], ltf.iloc[:80])
        ltf.loc[100:, 'low'] = 80
        long = self.run_fixture([ob()], ltf)
        self.assertEqual(short.iloc[0].entry_time, long.iloc[0].entry_time)
        self.assertEqual(short.iloc[0].stop, long.iloc[0].stop)
        self.assertEqual(short.iloc[0].target, long.iloc[0].target)

    def test_invalidation_reference_known_at_entry_open(self):
        df = candles(8, price=100)
        df.loc[2, 'low'] = 90  # confirms at close 3, unavailable at open 3
        df.loc[5:, ['low', 'close']] = [85, 86]
        self.assertIsNone(find_invalidation_exit(df, 3, 7, 'bullish', minor_window=1))

    def test_invalidation_returns_last_required_close(self):
        df = candles(9, price=100)
        df.loc[2, 'low'] = 90
        df.loc[6:, ['low', 'close']] = [85, 86]
        self.assertEqual(find_invalidation_exit(df, 4, 8, 'bullish', confirm_bars=2), 7)

    def test_pyramid_ignores_base_exit_candle(self):
        df = candles(3, price=100)
        df.loc[2, 'high'] = 120
        self.assertIsNone(find_pyramid_trigger(df, 0, 2, 100, 10, 'bullish', 1))


class ExecutionTests(unittest.TestCase):
    def fill(self, rows, **kw):
        df = candles(len(rows), price=100)
        df[['open', 'high', 'low', 'close']] = rows
        return simulate_daily_exit(completed_bars(df, 'H1'), 0, 100, 90, 130,
                                   'bullish', 10, 10, **kw)

    def test_stop_gap_fills_open(self):
        r = self.fill([[100, 105, 95, 100], [80, 85, 75, 82]])
        self.assertEqual((r['exit_price'], r['r_multiple'], r['exit_phase']), (80, -2, 'open'))

    def test_invalidation_next_open(self):
        r = self.fill([[100, 105, 95, 100], [98, 101, 95, 96], [93, 95, 91, 94]], invalidation_idx=1)
        self.assertEqual((r['exit_idx'], r['exit_price'], r['exit_reason']), (2, 93, 'invalidation'))

    def test_breakeven_not_retroactive(self):
        r = self.fill([[100, 115, 95, 110], [108, 109, 99, 101]], breakeven_trigger_r=1)
        self.assertEqual(r['exit_idx'], 1)
        self.assertEqual(r['exit_price'], 100)

    def test_ambiguous_bar_stop_first(self):
        r = self.fill([[100, 140, 80, 120]])
        self.assertEqual(r['exit_price'], 90)
        self.assertEqual(r['exit_time'], pd.Timestamp('2025-01-01T01:00Z'))


class AccountingTests(unittest.TestCase):
    def test_overlap_does_not_spend_future_profit(self):
        trades = pd.DataFrame([
            dict(symbol='A', chain_id='a', leg_type='base', entry_time='2025-01-01', exit_time='2025-01-04', r_multiple=2, risk_mult=1),
            dict(symbol='B', chain_id='b', leg_type='base', entry_time='2025-01-02', exit_time='2025-01-03', r_multiple=1, risk_mult=1)])
        result, metrics = account_trades(trades, 1, 1000)
        self.assertEqual(list(result.risk_amount), [10, 10])
        self.assertEqual(metrics['equity_curve'], [1000, 1010, 1030])

    def test_half_risk_pyramid(self):
        trades = pd.DataFrame([dict(symbol='A', chain_id='a', leg_type='pyramid',
            entry_time='2025-01-01', exit_time='2025-01-02', r_multiple=2, risk_mult=.5)])
        result, metrics = account_trades(trades, 1, 2000)
        self.assertEqual(result.iloc[0].risk_amount, 10)
        self.assertEqual(result.iloc[0].pnl_amount, 20)
        self.assertEqual(metrics['final_equity'], 2020)


if __name__ == '__main__':
    unittest.main()
