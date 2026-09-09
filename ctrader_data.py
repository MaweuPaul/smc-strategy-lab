"""
cTrader Open API data layer -- read-only historical candles, as a drop-in
alternative to the MetaTrader5-based fetchers elsewhere in this project.

Unlike MT5 (a local terminal), cTrader's Open API is a cloud socket service,
so this module keeps one persistent authenticated connection alive for the
life of the process (via crochet, which runs Twisted's reactor in a
background thread and lets the rest of the app call these functions as
plain synchronous, blocking calls -- no async/await needed at the call site).

Never places an order: only ApplicationAuth, AccountAuth (auth, not
trading), SymbolsList, and GetTrendbars are ever sent.
"""

import os
import json
import threading

import crochet
import pandas as pd
from dotenv import load_dotenv

crochet.setup()

from ctrader_open_api import Client, Protobuf, TcpProtocol, EndPoints
from ctrader_open_api.messages.OpenApiMessages_pb2 import (
    ProtoOAApplicationAuthReq,
    ProtoOAAccountAuthReq,
    ProtoOASymbolsListReq,
    ProtoOAGetTrendbarsReq,
)
from ctrader_open_api.messages.OpenApiModelMessages_pb2 import ProtoOATrendbarPeriod

load_dotenv()

CLIENT_ID = os.environ.get("CTRADER_CLIENT_ID")
CLIENT_SECRET = os.environ.get("CTRADER_CLIENT_SECRET")
ACCOUNT_ID = int(os.environ["CTRADER_ACCOUNT_ID"]) if os.environ.get("CTRADER_ACCOUNT_ID") else None
IS_LIVE = os.environ.get("CTRADER_IS_LIVE", "false").lower() == "true"
SESSION_PATH = os.path.join(os.path.dirname(__file__), "ctrader_session.json")

# Confirmed by querying this account's symbol list -- see ctrader_list_symbols.py.
# "DXY" has no exact equivalent on this broker; DB US Dollar Index is a
# different underlying (different basket/methodology from the real ICE DXY),
# used here only as a labeled stand-in.
SYMBOL_NAME_MAP = {
    "EURUSD": "EURUSD", "GBPUSD": "GBPUSD", "USDJPY": "USDJPY", "AUDUSD": "AUDUSD",
    "USDCHF": "USDCHF", "USDCAD": "USDCAD", "NZDUSD": "NZDUSD",
    "XAUUSD": "XAUUSD", "XAGUSD": "XAGUSD",
    "US500": "US 500", "US30": "US 30", "USTEC": "US TECH 100", "UK100": "UK 100",
    "DXY": "DB US DOLLAR INDEX",
}

TIMEFRAME_TO_PERIOD = {
    "M1": ProtoOATrendbarPeriod.M1, "M5": ProtoOATrendbarPeriod.M5,
    "M15": ProtoOATrendbarPeriod.M15, "H1": ProtoOATrendbarPeriod.H1,
    "H4": ProtoOATrendbarPeriod.H4, "D1": ProtoOATrendbarPeriod.D1,
}

MAX_TRENDBARS_PER_REQUEST = 5000  # cTrader's documented cap


class _Session:
    """Owns the one persistent connection + auth state + caches. All the
    Twisted/Deferred code lives here; everything public on this class is
    wrapped with @crochet.wait_for so callers just get a blocking function."""

    def __init__(self):
        self._lock = threading.Lock()
        self._client = None
        self._ready_deferred = None
        self._is_ready = False
        self._symbol_id_by_name = {}

    def _host(self):
        return EndPoints.PROTOBUF_LIVE_HOST if IS_LIVE else EndPoints.PROTOBUF_DEMO_HOST

    def _access_token(self):
        with open(SESSION_PATH) as f:
            return json.load(f)["access_token"]

    def _connect_and_auth(self):
        """Returns a Deferred that fires once TCP+appAuth+accountAuth+symbol
        list are all done. Only actually connects once; later calls reuse
        the same Deferred/connection."""
        from twisted.internet.defer import Deferred

        client = Client(self._host(), EndPoints.PROTOBUF_PORT, TcpProtocol)
        self._client = client
        d = Deferred()

        def on_symbols(response):
            msg = Protobuf.extract(response)
            for s in msg.symbol:
                self._symbol_id_by_name[s.symbolName] = s.symbolId
            d.callback(None)

        def on_account_auth(_):
            req = ProtoOASymbolsListReq()
            req.ctidTraderAccountId = ACCOUNT_ID
            client.send(req, responseTimeoutInSeconds=20).addCallbacks(on_symbols, d.errback)

        def on_app_auth(_):
            req = ProtoOAAccountAuthReq()
            req.ctidTraderAccountId = ACCOUNT_ID
            req.accessToken = self._access_token()
            client.send(req, responseTimeoutInSeconds=20).addCallbacks(on_account_auth, d.errback)

        def connected(_):
            req = ProtoOAApplicationAuthReq()
            req.clientId = CLIENT_ID
            req.clientSecret = CLIENT_SECRET
            client.send(req, responseTimeoutInSeconds=20).addCallbacks(on_app_auth, d.errback)

        client.setConnectedCallback(connected)
        client.setDisconnectedCallback(lambda *a: None)
        client.startService()
        return d

    def _ensure_ready(self):
        from twisted.internet.defer import succeed

        with self._lock:
            if self._is_ready:
                return succeed(None)
            if self._ready_deferred is None:
                def _mark_ready(result):
                    self._is_ready = True
                    return result
                self._ready_deferred = self._connect_and_auth()
                self._ready_deferred.addCallback(_mark_ready)
            # Chain a no-op onto the shared in-flight deferred so every
            # concurrent caller waits for the same one-time connect, without
            # any of them permanently extending the shared object itself.
            return self._ready_deferred.addCallback(lambda _: None)

    def _get_trendbars(self, symbol_id, period, from_ms, to_ms):
        d2 = self._ensure_ready()

        def fetch(_):
            from twisted.internet.defer import Deferred
            inner = Deferred()
            req = ProtoOAGetTrendbarsReq()
            req.ctidTraderAccountId = ACCOUNT_ID
            req.symbolId = symbol_id
            req.period = period
            req.fromTimestamp = from_ms
            req.toTimestamp = to_ms
            req.count = MAX_TRENDBARS_PER_REQUEST
            self._client.send(req, responseTimeoutInSeconds=30).addCallbacks(
                lambda response: inner.callback(Protobuf.extract(response)), inner.errback
            )
            return inner

        return d2.addCallback(fetch)

    @crochet.wait_for(timeout=60)
    def fetch_candles(self, symbol_name, timeframe, start, end):
        """The one function the rest of the app should call. start/end are
        pandas-parseable timestamps (UTC assumed). Returns a Deferred that
        crochet turns into a blocking call returning a plain dict list."""
        ctrader_name = SYMBOL_NAME_MAP.get(symbol_name, symbol_name)
        period = TIMEFRAME_TO_PERIOD[timeframe]
        start_ms = int(pd.Timestamp(start, tz="UTC").timestamp() * 1000)
        end_ms = int(pd.Timestamp(end, tz="UTC").timestamp() * 1000)

        d = self._ensure_ready()

        def resolve_symbol(_):
            symbol_id = self._symbol_id_by_name.get(ctrader_name)
            if symbol_id is None:
                raise ValueError(f"Symbol '{ctrader_name}' (for {symbol_name}) not found on this cTrader account")
            return self._get_trendbars(symbol_id, period, start_ms, end_ms).addCallback(_trendbars_to_rows)

        return d.addCallback(resolve_symbol)


# cTrader's trendbar low/deltaOpen/deltaHigh/deltaClose are always encoded
# as integers relative to a fixed 1e5 scale -- confirmed empirically (see
# ctrader_test_fetch.py): using the per-symbol `digits` field instead gave
# prices exactly 1000x too large for both XAUUSD and US500.
TRENDBAR_PRICE_SCALE = 100_000


def _trendbars_to_rows(tb_res):
    scale = TRENDBAR_PRICE_SCALE
    rows = []
    for bar in tb_res.trendbar:
        open_ = (bar.low + bar.deltaOpen) / scale
        high = (bar.low + bar.deltaHigh) / scale
        close = (bar.low + bar.deltaClose) / scale
        low = bar.low / scale
        rows.append({
            "time": bar.utcTimestampInMinutes * 60,
            "open": open_, "high": high, "low": low, "close": close,
        })
    rows.sort(key=lambda r: r["time"])
    return rows


_session = _Session()


def fetch_candles_ctrader(symbol_name: str, timeframe: str, start, end) -> pd.DataFrame:
    """Drop-in replacement shape for the MT5 fetchers: returns a DataFrame
    with time (unix seconds), open, high, low, close, sorted ascending."""
    rows = _session.fetch_candles(symbol_name, timeframe, start, end)
    df = pd.DataFrame(rows)
    if df.empty:
        return df
    df["time"] = pd.to_datetime(df["time"], unit="s", utc=True)
    return df[["time", "open", "high", "low", "close"]].reset_index(drop=True)
