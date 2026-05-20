(function () {
  'use strict';

  const DATA_URL = 'data/report.json';

  const ETF_COLORS = {
    VOO:  '#1D9E75',
    QQQ:  '#378ADD',
    GLD:  '#EF9F27',
    VGIT: '#9B59B6',
    TYD:  '#E8A0BF',
    GRID: '#E24B4A',
  };

  const SIGNAL_CLASS = {
    '強力買入': 'badge-strong-buy',
    '買  入':   'badge-buy',
    '持  有':   'badge-hold',
    '賣  出':   'badge-sell',
    '強力賣出': 'badge-strong-sell',
  };

  function el(id) { return document.getElementById(id); }
  function fmt(v, d = 2) { return (v == null) ? 'N/A' : Number(v).toFixed(d); }
  function fmtPct(v, d = 1) { return (v == null) ? 'N/A' : (v * 100).toFixed(d) + '%'; }
  function sign(v) { return v >= 0 ? '+' : ''; }

  function colorClass(v, greenThresh, redThresh, invert = false) {
    if (v == null) return '';
    const isGood = invert ? v <= greenThresh : v >= greenThresh;
    const isBad  = invert ? v >= redThresh   : v <= redThresh;
    if (isGood) return 'c-grn';
    if (isBad)  return 'c-red';
    return 'c-yel';
  }

  // ── Header ──
  function renderHeader(data) {
    const d = new Date(data.generated_at);
    el('report-date').textContent = d.toLocaleDateString('zh-TW', {
      year: 'numeric', month: '2-digit', day: '2-digit'
    });
    el('last-updated').textContent = '更新 ' + d.toLocaleTimeString('zh-TW', {
      hour: '2-digit', minute: '2-digit'
    });
  }

  // ── Macro Strip ──
  function renderMacroStrip(m) {
    const us10y = el('m-us10y');
    us10y.textContent = fmt(m.us10y) + '%';
    us10y.className = 'strip-val ' + (m.us10y > 5 ? 'c-red' : m.us10y > 4 ? 'c-yel' : 'c-grn');

    const rec = el('m-recprob');
    rec.textContent = fmt(m.recession_prob, 0) + '%';
    rec.className = 'strip-val ' + (m.recession_prob > 50 ? 'c-red' : m.recession_prob > 20 ? 'c-yel' : 'c-grn');

    const hy = el('m-hyspread');
    hy.textContent = fmt(m.hy_spread) + '%';
    hy.className = 'strip-val ' + (m.hy_spread > 6.5 ? 'c-red' : m.hy_spread < 3.5 ? 'c-yel' : 'c-grn');

    const pmi = m.ism_pmi && m.ism_pmi.length ? m.ism_pmi[m.ism_pmi.length - 1] : null;
    const pmiEl = el('m-pmi');
    pmiEl.textContent = pmi != null ? fmt(pmi, 1) : 'N/A';
    pmiEl.className = 'strip-val ' + (pmi >= 50 ? 'c-grn' : 'c-red');

    const rr = el('m-realrate');
    rr.textContent = (m.real_rate >= 0 ? '+' : '') + fmt(m.real_rate) + '%';
    rr.className = 'strip-val ' + (m.real_rate > 2 ? 'c-red' : m.real_rate > 0 ? 'c-yel' : 'c-grn');

    const sahm = el('m-sahm');
    sahm.textContent = m.sahm_triggered ? '⚠️ 觸發' : fmt(m.sahm_indicator) + '%';
    sahm.className = 'strip-val ' + (m.sahm_triggered ? 'c-red' : 'c-grn');
  }

  // ── Regime Banner ──
  function renderRegime(data) {
    const r = data.regime;
    if (!r) return;

    const badge   = el('regime-badge');
    const details = el('regime-details');
    const banner  = el('regime-banner');

    const REGIME_CFG = {
      'RISK_ON':     { text: '🟢 RISK ON',     cls: 'regime-on'     },
      'TRANSITION':  { text: '🟡 TRANSITION',  cls: 'regime-trans'  },
      'RISK_OFF':    { text: '🔴 RISK OFF',    cls: 'regime-off'    },
    };
    const cfg = REGIME_CFG[r.regime] || { text: r.regime, cls: '' };
    badge.textContent = cfg.text;
    badge.className   = 'regime-badge ' + cfg.cls;
    banner.className  = 'regime-banner ' + cfg.cls;

    const parts = [];
    if (r.credit)    parts.push('信用' + r.credit);
    if (r.pmi_trend) parts.push(r.pmi_trend);
    if (r.curve)     parts.push('曲線' + r.curve);
    if (r.recession_flag) parts.push('⚠ 衰退訊號已觸發');
    details.textContent = parts.join(' · ');
  }

  // ── Tail Risk Bar ──
  function renderTailRisks(data) {
    const risks = data.tail_risks;
    const bar   = el('tail-risk-bar');
    const inner = el('tail-risk-inner');
    if (!risks || !risks.length) {
      bar.style.display = 'none';
      return;
    }
    bar.style.display = 'block';
    inner.innerHTML = risks.map(r =>
      `<div class="tail-risk-item">⚠ ${r.warning}</div>`
    ).join('');
  }

  // ── AI Card ──
  function renderAI(data) {
    el('ai-text').textContent = data.gemini_analysis || '（AI 分析暫無資料）';
  }

  // ── HRP Chart ──
  function renderHRP(hrp) {
    if (!hrp || !Object.keys(hrp).length) {
      el('hrp-section').innerHTML = '<h2 class="card-title">HRP 風險平衡配置</h2><p class="loading-msg">資料暫無</p>';
      return;
    }
    const tickers = Object.keys(hrp);
    const values  = tickers.map(t => hrp[t]);
    const colors  = tickers.map(t => ETF_COLORS[t] || '#666');

    new Chart(document.getElementById('hrpChart').getContext('2d'), {
      type: 'doughnut',
      data: {
        labels: tickers,
        datasets: [{
          data: values,
          backgroundColor: colors,
          borderColor: '#0f1117',
          borderWidth: 3,
          hoverOffset: 4,
        }]
      },
      options: {
        responsive: false,
        cutout: '62%',
        plugins: {
          legend: { display: false },
          tooltip: { callbacks: { label: ctx => ' ' + (ctx.parsed * 100).toFixed(1) + '%' } }
        }
      }
    });

    const legend = el('hrp-legend');
    legend.innerHTML = '';
    tickers.forEach((t, i) => {
      const li = document.createElement('li');
      li.innerHTML =
        `<span class="legend-dot" style="background:${colors[i]}"></span>` +
        `<span class="legend-name">${t}</span>` +
        `<span class="legend-pct">${(values[i]*100).toFixed(1)}%</span>`;
      legend.appendChild(li);
    });
  }

  // ── DCC Card ──
  function renderDCC(dcc) {
    const container = el('dcc-content');
    if (!dcc || !dcc.tickers) {
      container.innerHTML = '<p class="loading-msg">DCC-GARCH 資料暫無</p>';
      return;
    }

    const c   = dcc.corr || {};
    const c30 = dcc.corr_30d_avg || {};
    const v   = dcc.vol_annual || {};
    const hrp = dcc.hrp || {};
    const rp  = dcc.risk_parity || {};
    const t   = dcc.tickers;
    const pairs = Object.keys(c);

    function corrColor(val) {
      if (val == null) return '';
      return Math.abs(val) > 0.5 ? 'c-red' : Math.abs(val) > 0.2 ? 'c-yel' : 'c-grn';
    }
    function trendLabel(curr, avg) {
      const diff = curr - avg;
      if (Math.abs(diff) < 0.02) return '↔ 持平';
      return diff > 0 ? '↑ 上升' : '↓ 下降';
    }

    let html = '<div class="dcc-grid">';

    // Correlations
    html += '<div><p class="dcc-sub-title">動態條件相關係數</p>';
    pairs.forEach(key => {
      const curr = c[key], avg = c30[key];
      const label = key.replace('_', ' ↔ ');
      html += `<div class="dcc-row">
        <span class="dcc-label">${label}</span>
        <span>
          <span class="dcc-val ${corrColor(curr)}">${sign(curr)}${fmt(curr, 3)}</span>
          <span class="dcc-trend">${trendLabel(curr, avg)}</span>
        </span>
      </div>`;
    });
    html += '</div>';

    // Volatilities
    html += '<div><p class="dcc-sub-title">年化條件波動率</p>';
    t.forEach(ticker => {
      const vol = v[ticker];
      html += `<div class="dcc-row">
        <span class="dcc-label">${ticker}</span>
        <span class="dcc-val ${vol > 0.25 ? 'c-red' : vol > 0.15 ? 'c-yel' : 'c-grn'}">${fmtPct(vol)}</span>
      </div>`;
    });
    html += '</div>';

    // HRP vs Risk Parity
    html += '<div><p class="dcc-sub-title">最佳化配置（HRP / RP）</p>';
    t.forEach(ticker => {
      html += `<div class="dcc-row">
        <span class="dcc-label">${ticker}</span>
        <span><span class="dcc-val c-grn">${fmtPct(hrp[ticker])}</span> <span class="dcc-trend">/ ${fmtPct(rp[ticker])}</span></span>
      </div>`;
    });
    html += '</div>';

    html += '</div>';
    html += `<p class="dcc-params">DCC α=${fmt(dcc.dcc_alpha,4)} β=${fmt(dcc.dcc_beta,4)} ｜ 距離矩陣 d<sub>ij</sub>=√((1−ρ)/2)，單連結聚類</p>`;
    container.innerHTML = html;
  }

  // ── ETF Cards ──
  function renderETFCards(signals, signalLights) {
    const grid = el('etf-grid');
    if (!signals || !signals.length) {
      grid.innerHTML = '<p class="loading-msg">無 ETF 資料</p>';
      return;
    }
    grid.innerHTML = '';
    const sl = signalLights || {};

    signals.forEach(sig => {
      const chgPos    = sig.change_pct >= 0;
      const sigLight  = sl[sig.ticker] || {};
      const light     = sigLight.light     || '';
      const reason    = sigLight.reason    || '';
      const prevLight = sigLight.prev_light || '';
      const changed   = sigLight.changed   || false;

      // 信號燈樣式
      const lightClass = light.includes('🟢') ? 'sl-green'
                       : light.includes('🔴') ? 'sl-red'
                       : 'sl-yellow';

      // delta 標籤（僅在改變時顯示）
      const deltaHtml = changed
        ? `<span class="sl-delta">${prevLight} → ${light}</span>`
        : '';

      const card = document.createElement('div');
      card.className = 'etf-card';
      card.dataset.ticker = sig.ticker;

      card.innerHTML = `
        <div class="etf-header">
          <div class="etf-name-wrap">
            <div class="etf-name">${sig.name}</div>
            <div class="etf-ticker">${sig.ticker}</div>
          </div>
          <div class="etf-price-wrap">
            <div class="etf-price">$${fmt(sig.price)}</div>
            <span class="etf-chg ${chgPos ? 'chg-pos' : 'chg-neg'}">${sign(sig.change_pct)}${fmt(sig.change_pct)}%</span>
          </div>
        </div>

        ${light ? `
        <div class="signal-light-row ${lightClass}">
          <span class="sl-light">${light}</span>
          <span class="sl-reason">${reason}</span>
          ${deltaHtml}
        </div>` : ''}

        <div class="etf-metrics">
          <div class="metric">
            <span class="metric-label">Z-Score</span>
            <span class="metric-val ${sig.z_score > 2.5 || sig.z_score < -2 ? 'c-red' : sig.z_score > -1 && sig.z_score < 1.5 ? 'c-grn' : 'c-yel'}">${sign(sig.z_score)}${fmt(sig.z_score)}</span>
          </div>
          <div class="metric">
            <span class="metric-label">RSI(14)</span>
            <span class="metric-val ${sig.rsi > 70 ? 'c-red' : sig.rsi < 30 ? 'c-grn' : 'c-dim'}">${fmt(sig.rsi, 1)}</span>
          </div>
          <div class="metric">
            <span class="metric-label">Sharpe 1Y</span>
            <span class="metric-val ${sig.sharpe_1y > 1 ? 'c-grn' : sig.sharpe_1y > 0 ? 'c-yel' : 'c-red'}">${sign(sig.sharpe_1y)}${fmt(sig.sharpe_1y)}</span>
          </div>
          <div class="metric">
            <span class="metric-label">Max DD</span>
            <span class="metric-val ${sig.max_drawdown < -20 ? 'c-red' : sig.max_drawdown < -10 ? 'c-yel' : 'c-dim'}">${fmt(sig.max_drawdown, 1)}%</span>
          </div>
          <div class="metric">
            <span class="metric-label">MACD 柱</span>
            <span class="metric-val ${(sig.macd_hist || 0) > 0 ? 'c-grn' : 'c-red'}">${sig.macd_hist != null ? sign(sig.macd_hist) + fmt(sig.macd_hist, 4) : 'N/A'}</span>
          </div>
          <div class="metric">
            <span class="metric-label">Kelly 乘數</span>
            <span class="metric-val c-blu">${sig.fund_multiplier}x</span>
          </div>
        </div>
        <div class="etf-footer">
          <span class="etf-phase">${sig.cycle_phase}</span>
        </div>
      `;
      grid.appendChild(card);
    });
  }

  // ── TYD Timing ──
  function renderTYD(data) {
    const el_tyd = el('tyd-indicator');
    if (!el_tyd) return;
    const t = data.tyd_timing;
    if (!t) { el_tyd.style.display = 'none'; return; }
    const score = t.score != null ? t.score : (t.tyd_score != null ? t.tyd_score : 0);
    const label = t.label || '';
    const color = score >= 70 ? '#1D9E75' : score >= 50 ? '#EF9F27' : '#888888';
    el_tyd.innerHTML =
      `<span class="tyd-label" style="color:${color}">TYD 時機：${label}</span>` +
      `<span class="tyd-score" style="color:${color}">${score}/100</span>`;
  }

  // ── News ──
  function renderNews(signals) {
    const container = el('news-container');
    let html = '';
    let hasNews = false;
    (signals || []).forEach(sig => {
      if (!sig.news_headlines || !sig.news_headlines.length) return;
      hasNews = true;
      const color = ETF_COLORS[sig.ticker] || '#888';
      html += `<div class="news-group">
        <div class="news-group-title" style="color:${color}">${sig.ticker} — ${sig.name}</div>
        <ul>${sig.news_headlines.map(h => {
          let display, url;
          if (typeof h === 'object' && h !== null) {
            display = h.title_zh || h.title || '';
            url     = h.url || '';
          } else {
            display = String(h);
            url     = '';
          }
          const tag = url
            ? `<a href="${url}" target="_blank" rel="noopener noreferrer">${display}</a>`
            : `<span>${display}</span>`;
          return `<li>${tag}</li>`;
        }).join('')}</ul>
      </div>`;
    });
    container.innerHTML = hasNews ? html : '<p class="loading-msg">暫無新聞標題</p>';
  }

  // ── Bootstrap ──
  async function init() {
    try {
      const resp = await fetch(DATA_URL + '?t=' + Date.now());
      if (!resp.ok) throw new Error('HTTP ' + resp.status);
      const data = await resp.json();

      renderHeader(data);
      renderRegime(data);
      renderTailRisks(data);
      renderMacroStrip(data.macro);
      renderAI(data);
      renderHRP(data.hrp_weights);
      renderTYD(data);
      renderDCC(data.dcc);
      renderETFCards(data.etf_signals, data.signal_lights);
      renderNews(data.etf_signals);
    } catch (err) {
      console.error('[app]', err);
      el('etf-grid').innerHTML = `<p class="error-msg">資料載入失敗：${err.message}<br>請確認 data/report.json 已生成。</p>`;
    }
  }

  document.addEventListener('DOMContentLoaded', init);
})();
