"""
One-time helper: full auth chain (app auth -> account auth) then fetches the
symbol list for the account, so we can map human-readable names (EURUSD,
XAUUSD, ...) to cTrader's numeric symbolId, which every trendbar (candle)
request needs.

Read-only: ApplicationAuth, AccountAuth (auth only, not trading), SymbolsList.
"""

import json
import os

from dotenv import load_dotenv
from twisted.internet import reactor

from ctrader_open_api import Client, Protobuf, TcpProtocol, EndPoints
from ctrader_open_api.messages.OpenApiMessages_pb2 import (
    ProtoOAApplicationAuthReq,
    ProtoOAAccountAuthReq,
    ProtoOASymbolsListReq,
)

load_dotenv()

CLIENT_ID = os.environ.get("CTRADER_CLIENT_ID")
CLIENT_SECRET = os.environ.get("CTRADER_CLIENT_SECRET")
ACCOUNT_ID = int(os.environ.get("CTRADER_ACCOUNT_ID"))
IS_LIVE = os.environ.get("CTRADER_IS_LIVE", "false").lower() == "true"
SESSION_PATH = os.path.join(os.path.dirname(__file__), "ctrader_session.json")


def main():
    with open(SESSION_PATH) as f:
        session = json.load(f)

    host = EndPoints.PROTOBUF_LIVE_HOST if IS_LIVE else EndPoints.PROTOBUF_DEMO_HOST
    print(f"Connecting to {host}:{EndPoints.PROTOBUF_PORT} ...")
    client = Client(host, EndPoints.PROTOBUF_PORT, TcpProtocol)

    def on_symbols(response):
        msg = Protobuf.extract(response)
        print(f"{len(msg.symbol)} symbols total. Looking for our instruments:")
        wanted = {"EURUSD", "USDJPY", "XAUUSD", "GBPUSD", "AUDUSD", "USDCHF", "NZDUSD",
                  "USDCAD", "XAGUSD", "USTEC", "US30", "US500", "UK100", "DXY", "NAS100"}
        found = set()
        for s in msg.symbol:
            if s.symbolName in wanted:
                print(f"  {s.symbolName}: symbolId={s.symbolId}")
                found.add(s.symbolName)
        print()
        missing_keywords = ["US30", "30", "500", "SPX", "NAS", "TEC", "100", "FTSE", "UK",
                             "DXY", "DOLLAR", "USDX", "INDEX", "IDX", "WALL", "DOW"]
        print("Candidate matches for the missing ones (US30/US500/USTEC/UK100/DXY):")
        for s in msg.symbol:
            name_upper = s.symbolName.upper()
            if any(k in name_upper for k in missing_keywords):
                print(f"  {s.symbolName}: symbolId={s.symbolId}")
        reactor.callLater(0.3, reactor.stop)

    def on_account_auth(_):
        print("Account authenticated. Requesting symbols list...")
        req = ProtoOASymbolsListReq()
        req.ctidTraderAccountId = ACCOUNT_ID
        client.send(req).addCallbacks(on_symbols, on_error)

    def on_app_auth(_):
        print("App authenticated. Sending account auth...")
        req = ProtoOAAccountAuthReq()
        req.ctidTraderAccountId = ACCOUNT_ID
        req.accessToken = session["access_token"]
        client.send(req).addCallbacks(on_account_auth, on_error)

    def on_error(failure):
        print("Error:", failure)
        reactor.stop()

    def connected(_):
        print("TCP connected. Sending application auth...")
        req = ProtoOAApplicationAuthReq()
        req.clientId = CLIENT_ID
        req.clientSecret = CLIENT_SECRET
        client.send(req).addCallbacks(on_app_auth, on_error)

    def disconnected(client_, reason):
        print("Disconnected:", reason)

    def watchdog():
        print("Timed out after 90s -- stopping.")
        reactor.stop()

    client.setConnectedCallback(connected)
    client.setDisconnectedCallback(disconnected)
    client.startService()
    reactor.callLater(90, watchdog)
    reactor.run()


if __name__ == "__main__":
    main()
