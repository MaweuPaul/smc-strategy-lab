"""
One-time helper: connects to the cTrader Open API demo endpoint, authenticates
the app and the access token from ctrader_session.json, and prints every
trading account visible to that token so you can pick a ctidTraderAccountId
to put in .env as CTRADER_ACCOUNT_ID.

Read-only: only sends ApplicationAuth / GetAccountListByAccessToken. No
account auth, no orders, nothing that touches a specific account's trading
session.
"""

import json
import os

from dotenv import load_dotenv
from twisted.internet import reactor

from ctrader_open_api import Client, Protobuf, TcpProtocol, EndPoints
from ctrader_open_api.messages.OpenApiMessages_pb2 import (
    ProtoOAApplicationAuthReq,
    ProtoOAGetAccountListByAccessTokenReq,
)

load_dotenv()

CLIENT_ID = os.environ.get("CTRADER_CLIENT_ID")
CLIENT_SECRET = os.environ.get("CTRADER_CLIENT_SECRET")
SESSION_PATH = os.path.join(os.path.dirname(__file__), "ctrader_session.json")


def main():
    if not os.path.exists(SESSION_PATH):
        raise SystemExit("No ctrader_session.json found -- run ctrader_auth.py first.")
    with open(SESSION_PATH) as f:
        session = json.load(f)

    print(f"Connecting to {EndPoints.PROTOBUF_DEMO_HOST}:{EndPoints.PROTOBUF_PORT} ...")
    client = Client(EndPoints.PROTOBUF_DEMO_HOST, EndPoints.PROTOBUF_PORT, TcpProtocol)

    def on_app_auth_response(_):
        print("App authenticated. Requesting account list...")
        req = ProtoOAGetAccountListByAccessTokenReq()
        req.accessToken = session["access_token"]
        client.send(req).addCallbacks(on_account_list, on_error)

    def on_account_list(response):
        msg = Protobuf.extract(response)
        if not msg.ctidTraderAccount:
            print("No trading accounts visible to this token.")
        for acc in msg.ctidTraderAccount:
            print(f"ctidTraderAccountId={acc.ctidTraderAccountId}  "
                  f"live={acc.isLive}  login={acc.traderLogin}")
        reactor.callLater(0.3, reactor.stop)

    def on_error(failure):
        print("Error:", failure)
        reactor.stop()

    def connected(_):
        print("TCP connected. Sending application auth...")
        req = ProtoOAApplicationAuthReq()
        req.clientId = CLIENT_ID
        req.clientSecret = CLIENT_SECRET
        client.send(req).addCallbacks(on_app_auth_response, on_error)

    def disconnected(client_, reason):
        print("Disconnected:", reason)

    def watchdog():
        print("Timed out after 90s waiting for a response -- stopping.")
        reactor.stop()

    client.setConnectedCallback(connected)
    client.setDisconnectedCallback(disconnected)
    client.startService()
    reactor.callLater(90, watchdog)
    reactor.run()


if __name__ == "__main__":
    main()
