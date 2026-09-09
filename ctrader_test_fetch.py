from ctrader_data import fetch_candles_ctrader

df = fetch_candles_ctrader("XAUUSD", "H1", "2024-01-08", "2024-01-10")
print(df.shape)
print(df.head(5))
print(df.tail(5))

df2 = fetch_candles_ctrader("US 500" if False else "US500", "D1", "2023-01-01", "2023-02-01")
print(df2.shape)
print(df2.head(5))
