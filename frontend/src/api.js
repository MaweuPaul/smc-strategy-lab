import axios from "axios";

const API_BASE = "http://localhost:8001";

export const api = axios.create({ baseURL: API_BASE });

export async function fetchSymbols() {
  const { data } = await api.get("/api/symbols");
  return data;
}

export async function runBacktest(params) {
  const { data } = await api.post("/api/backtest", params);
  return data;
}

export async function runAsianBacktest(params) {
  const { data } = await api.post("/api/asian-backtest", params);
  return data;
}

export async function runOrbBacktest(params) {
  const { data } = await api.post("/api/orb-backtest", params);
  return data;
}

export async function runOrbNyOpenBacktest(params) {
  const { data } = await api.post("/api/orb-ny-open-backtest", params);
  return data;
}

export async function runDailyObBacktest(params) {
  const { data } = await api.post("/api/daily-ob-backtest", params);
  return data;
}

export async function runDailyFvgBacktest(params) {
  const { data } = await api.post("/api/daily-fvg-backtest", params);
  return data;
}

export async function fetchCandles(symbol, timeframe, start, end) {
  const { data } = await api.get("/api/candles", {
    params: { symbol, timeframe, start, end },
  });
  return data.candles;
}
