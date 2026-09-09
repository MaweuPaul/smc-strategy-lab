import unittest
from unittest.mock import patch
import pandas as pd
from pydantic import ValidationError
from fastapi.encoders import jsonable_encoder
from backend_api import DailyObBacktestRequest, daily_ob_backtest, compute_cagr


class DailyApiTests(unittest.TestCase):
    def test_rejects_invalid_settings(self):
        for extra in ({'invalidation_confirm_bars': 0}, {'min_rr': 6, 'max_rr': 3},
                      {'htf_timeframe': 'W1'}, {'risk_pct': float('nan')},
                      {'symbols': ['A', 'A']}, {'symbols': []}):
            with self.subTest(extra=extra), self.assertRaises(ValidationError):
                DailyObBacktestRequest(**{'symbols': ['A'], **extra})

    def test_h4_metadata_even_without_trades(self):
        with patch('backend_api.run_daily_ob', return_value=pd.DataFrame()):
            result = daily_ob_backtest(DailyObBacktestRequest(symbols=['A'], htf_timeframe='H4'))
        self.assertEqual(result['ltf'], 'M15')
        self.assertEqual(result['config']['htf_timeframe'], 'H4')

    def test_partial_errors_returned(self):
        with patch('backend_api.run_daily_ob', side_effect=RuntimeError('no history')):
            result = daily_ob_backtest(DailyObBacktestRequest(symbols=['A']))
        self.assertEqual(result['errors'], {'A': 'no history'})

    def test_pyramid_dollars_and_json_response(self):
        df = pd.DataFrame([dict(symbol='A', chain_id='a', leg_type='pyramid',
            entry_time=pd.Timestamp('2025-01-02T00:00Z'), exit_time=pd.Timestamp('2025-01-03T00:00Z'),
            r_multiple=2., risk_mult=.5, ob_top=100., ob_bottom=90., ob_type='bullish',
            ob_formed_time=pd.Timestamp('2025-01-01T00:00Z'))])
        df.attrs.update(evaluation_start=pd.Timestamp('2025-01-01T00:00Z'),
                        evaluation_end=pd.Timestamp('2026-01-01T00:00Z'), ltf='M15')
        with patch('backend_api.run_daily_ob', return_value=df):
            result = daily_ob_backtest(DailyObBacktestRequest(symbols=['A'], start_equity=2000, htf_timeframe='H4'))
        self.assertEqual(result['metrics']['final_equity'], 2020)
        self.assertEqual(result['trades'][0]['risk_amount'], 10)
        self.assertEqual(result['trades'][0]['pnl_amount'], 20)
        self.assertEqual(result['ltf'], 'M15')
        self.assertEqual(result['metrics']['evaluation_start'], '2025-01-01T00:00:00+00:00')
        jsonable_encoder(result)

    def test_cagr_preserves_fractional_days(self):
        _, span = compute_cagr(pd.DataFrame(), 1001, 1000,
                              pd.Timestamp('2025-01-01'), pd.Timestamp('2025-01-01T12:00'))
        self.assertAlmostEqual(span, .5/365.25)


if __name__ == '__main__':
    unittest.main()
