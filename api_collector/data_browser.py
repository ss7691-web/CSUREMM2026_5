import csv
import io
import os
import shutil
import tempfile
import threading
import zipfile
from flask import Flask, jsonify, Response, render_template_string, send_file

PAGE = """
<!doctype html><html><head><meta charset="utf-8"><title>Kalshi Data Browser</title>
<style>
  body{font-family:system-ui,sans-serif;margin:0;display:flex;height:100vh;color:#1a1a1a}
  #list{width:38%;border-right:1px solid #ddd;overflow:auto;padding:12px}
  #detail{flex:1;overflow:auto;padding:16px}
  input{width:100%;padding:8px;margin-bottom:10px;box-sizing:border-box;font-size:14px}
  table{border-collapse:collapse;width:100%;font-size:13px}
  th,td{text-align:left;padding:6px 8px;border-bottom:1px solid #eee}
  tr.m{cursor:pointer} tr.m:hover{background:#f5f7ff}
  .pill{font-size:11px;padding:2px 6px;border-radius:10px;background:#eee}
  .active{background:#d8f5d8} .settled{background:#f5d8d8}
  a.btn{display:inline-block;margin:4px 8px 12px 0;padding:8px 12px;background:#2b59ff;
        color:#fff;text-decoration:none;border-radius:6px;font-size:13px}
  h2{margin:0 0 4px} .muted{color:#888;font-size:12px}
</style></head><body>
<div id="list">
  <div id="summary" class="muted" style="margin-bottom:10px;font-size:12px;line-height:1.6"></div>
  <input id="q" placeholder="filter markets by ticker / title...">
  <table><tbody id="markets"></tbody></table>
</div>
<div id="detail"><p class="muted">Select a market on the left.</p></div>
<script>
let MARKETS=[];
async function load(){ MARKETS=await (await fetch('/api/markets')).json(); render(); }
function render(){
  const q=document.getElementById('q').value.toLowerCase();
  const rows=MARKETS.filter(m=>(m.ticker+' '+(m.title||'')).toLowerCase().includes(q));
  document.getElementById('markets').innerHTML=rows.map(m=>
    `<tr class="m" onclick='detail(${JSON.stringify(m.ticker)})'>
       <td>${m.ticker}<div class="muted">${m.title||''}</div></td>
       <td><span class="pill ${m.status||''}">${m.status||''}</span></td></tr>`).join('');
}
async function detail(t){
  const d=document.getElementById('detail'); d.innerHTML='<p class="muted">loading...</p>';
  const trades=await (await fetch('/api/trades/'+encodeURIComponent(t))).json();
  const m=MARKETS.find(x=>x.ticker===t)||{};
  const s=m.series_ticker;
  const seriesBtns=s?`<p><a class="btn" href="/download/series/${encodeURIComponent(s)}/trades">All ${s} trades (parquet)</a>
       <a class="btn" href="/download/series/${encodeURIComponent(s)}/orderbook">All ${s} orderbook (zip)</a></p>`:'';
  d.innerHTML=`<h2>${t}</h2><div class="muted">${m.title||''} — ${m.category||''} — ${m.status||''}</div>
    <p><a class="btn" href="/download/trades/${encodeURIComponent(t)}">Download trades CSV</a>
       <a class="btn" href="/download/lob/${encodeURIComponent(t)}">Download orderbook CSV</a></p>
    ${seriesBtns}
    <h3>${trades.length} trades</h3>
    <table><thead><tr><th>ts</th><th>yes</th><th>no</th><th>count</th><th>taker</th></tr></thead>
    <tbody>${trades.slice(-200).reverse().map(r=>
      `<tr><td>${r.ts}</td><td>${r.yes_price_dollars}</td><td>${r.no_price_dollars}</td>
           <td>${r.count_fp}</td><td>${r.taker_side}</td></tr>`).join('')}</tbody></table>`;
}
document.getElementById('q').addEventListener('input',render);
async function loadSummary(){
  const s=await (await fetch('/api/summary')).json();
  const total=s.reduce((a,b)=>a+Number(b.markets),0);
  document.getElementById('summary').innerHTML =
    `<b>${total}</b> markets total`+
    s.map(r=>`<div>${r.category}: <b>${r.markets}</b> (${r.active} active)</div>`).join('');
}
loadSummary(); setInterval(loadSummary, 5000);
load();
</script></body></html>
"""

def make_app(con):
    app = Flask(__name__)

    def rows(sql, params=None):
        cur = con.cursor()
        c = cur.execute(sql, params or [])
        cols = [d[0] for d in c.description]
        return [dict(zip(cols, r)) for r in c.fetchall()]

    @app.route("/")
    def index():
        return render_template_string(PAGE)

    @app.route("/api/markets")
    def api_markets():
        return jsonify(rows(
            "SELECT ticker, title, series_ticker, category, status FROM markets ORDER BY ticker"))

    @app.route("/api/trades/<ticker>")
    def api_trades(ticker):
        return jsonify(rows("SELECT * FROM trades WHERE market_ticker=? ORDER BY ts", [ticker]))

    def csv_text(data):
        if not data:
            return ""
        buf = io.StringIO()
        w = csv.DictWriter(buf, fieldnames=list(data[0].keys()))
        w.writeheader(); w.writerows(data)
        return buf.getvalue()

    def csv_resp(data, fname):
        return Response(csv_text(data), mimetype="text/csv",
                        headers={"Content-Disposition": f"attachment; filename={fname}"})

    @app.route("/download/trades/<ticker>")
    def dl_trades(ticker):
        return csv_resp(rows("SELECT * FROM trades WHERE market_ticker=? ORDER BY ts", [ticker]),
                        f"{ticker}_trades.csv")

    @app.route("/download/lob/<ticker>")
    def dl_lob(ticker):
        return csv_resp(rows("SELECT * FROM lob WHERE market_ticker=? ORDER BY seq", [ticker]),
                        f"{ticker}_lob.csv")


    def _series_exists(series):
        return bool(rows("SELECT 1 FROM markets WHERE series_ticker = ? LIMIT 1", [series]))

    def _copy_to(sql_select, path):
        con.cursor().execute(f"COPY ({sql_select}) TO '{path}' (FORMAT PARQUET)")

    def _send_and_cleanup(path, tmpdir, download_name, mimetype):

        f = open(path, "rb")
        shutil.rmtree(tmpdir, ignore_errors=True)
        return send_file(f, as_attachment=True,
                         download_name=download_name, mimetype=mimetype)

    @app.route("/download/series/<series>/trades")
    def dl_series_trades(series):
        if not _series_exists(series):
            return Response("unknown series\n", status=404)
        tmp = tempfile.mkdtemp(prefix="kalshi_dl_")
        path = os.path.join(tmp, "trades.parquet")
        _copy_to(f"""
            SELECT t.* FROM trades t
            JOIN markets m ON t.market_ticker = m.ticker
            WHERE m.series_ticker = '{series}'""", path)
        return _send_and_cleanup(path, tmp, f"{series}_trades.parquet",
                                 "application/octet-stream")


    @app.route("/download/series/<series>/orderbook")
    def dl_series_orderbook(series):
        if not _series_exists(series):
            return Response("unknown series\n", status=404)
        tmp = tempfile.mkdtemp(prefix="kalshi_dl_")
        lob_path = os.path.join(tmp, "lob.parquet")
        snap_path = os.path.join(tmp, "snapshots.parquet")
        _copy_to(f"""
            SELECT l.* FROM lob l
            JOIN markets m ON l.market_ticker = m.ticker
            WHERE m.series_ticker = '{series}'""", lob_path)
        _copy_to(f"""
            SELECT s.* FROM snapshots s
            JOIN markets m ON s.market_ticker = m.ticker
            WHERE m.series_ticker = '{series}'""", snap_path)
        zip_path = os.path.join(tmp, "orderbook.zip")
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_STORED) as z:
            z.write(lob_path, f"{series}_lob.parquet")    # streams file->zip in chunks
            z.write(snap_path, f"{series}_snapshots.parquet")
        return _send_and_cleanup(zip_path, tmp, f"{series}_orderbook.zip", "application/zip")

    @app.route("/api/summary")
    def api_summary():
        return jsonify(rows("""
            SELECT COALESCE(category, '(watchlist/uncategorized)') AS category,
                   COUNT(*) AS markets,
                   SUM(CASE WHEN status = 'active' THEN 1 ELSE 0 END) AS active
            FROM markets
            GROUP BY 1
            ORDER BY 1
        """))
    return app

def start_browser(con, port=5000):
    app = make_app(con)
    t = threading.Thread(target=lambda: app.run(port=port, use_reloader=False), daemon=True)
    t.start()
    return t