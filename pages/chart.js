// ---- 📊 迷你行情弹窗（TradingView Lightweight Charts）----
// 入口：showFundChart(code) — 弹窗模式，复用 modal-overlay 交互
// 数据源：/api/chart/{fund_code}?period=1y

let _chartFundCode = '';
let _chartPeriod = '1y';
let _tvChart = null;
let _tvRsiChart = null;

function showFundChart(code) {
    _chartFundCode = code;
    _chartPeriod = '1y';
    _openChartModal();
}

function _openChartModal() {
    // 销毁旧图表
    _tvChart = null; _tvRsiChart = null;
    // 弹窗
    const o = document.createElement('div');
    o.className = 'modal-overlay';
    o.id = 'chartModalOverlay';
    o.onclick = e => { if (e.target === o) o.remove(); };
    o.innerHTML = `<div class="modal-sheet" onclick="event.stopPropagation()" style="max-height:92vh;overflow-y:auto">
        <div class="modal-handle"></div>
        <div class="modal-title" id="chartModalTitle">📊 ${_chartFundCode}</div>
        <div style="display:flex;gap:6px;margin:12px 0" id="chartPeriodBtns">
            ${['3m','6m','1y','3y'].map(p => `<button class="action-btn ${p===_chartPeriod?'primary':'secondary'}" onclick="_chartSwitchPeriod('${p}')" style="flex:1;padding:5px 0;font-size:12px">${p}</button>`).join('')}
        </div>
        <div id="tvChartWrap" style="border-radius:8px;overflow:hidden;background:var(--bg2,#1e293b)">
            <div style="text-align:center;padding:50px 0"><div class="loading-spinner"></div><div style="color:var(--text2);margin-top:10px;font-size:12px">加载行情...</div></div>
        </div>
        <div id="tvRsiWrap" style="margin-top:6px;border-radius:8px;overflow:hidden;background:var(--bg2,#1e293b)"></div>
        <div id="chartCostInfo" style="margin-top:8px;font-size:12px"></div>
    </div>`;
    document.body.appendChild(o);
    _loadChartData();
}

function _chartSwitchPeriod(p) {
    _chartPeriod = p;
    document.querySelectorAll('#chartPeriodBtns button').forEach((btn, i) => {
        btn.className = 'action-btn ' + (['3m','6m','1y','3y'][i] === p ? 'primary' : 'secondary');
    });
    document.getElementById('tvChartWrap').innerHTML = '<div style="text-align:center;padding:50px 0"><div class="loading-spinner"></div></div>';
    document.getElementById('tvRsiWrap').innerHTML = '';
    _tvChart = null; _tvRsiChart = null;
    _loadChartData();
}

async function _loadChartData() {
    if (!API_AVAILABLE) {
        document.getElementById('tvChartWrap').innerHTML = '<div style="text-align:center;padding:30px;color:var(--text2)">后端离线</div>';
        return;
    }
    try {
        const url = `${API_BASE}/chart/${_chartFundCode}?period=${_chartPeriod}&${getProfileParam()}`;
        const r = await fetch(url, { signal: AbortSignal.timeout(15000) });
        if (!r.ok) throw new Error('HTTP ' + r.status);
        const d = await r.json();
        const title = document.getElementById('chartModalTitle');
        if (title) title.textContent = `📊 ${d.fund_name || _chartFundCode}`;
        if (!d.kline_data || d.kline_data.length === 0) {
            document.getElementById('tvChartWrap').innerHTML = '<div style="text-align:center;padding:30px;color:var(--text2)">暂无行情数据</div>';
            return;
        }
        _renderTVChart(d);
    } catch (e) {
        document.getElementById('tvChartWrap').innerHTML = `<div style="text-align:center;padding:30px;color:var(--red)">加载失败: ${e.message}</div>`;
    }
}

function _renderTVChart(data) {
    const wrap = document.getElementById('tvChartWrap');
    wrap.innerHTML = '';
    const chartH = Math.min(window.innerHeight * 0.35, 300);

    _tvChart = LightweightCharts.createChart(wrap, {
        width: wrap.clientWidth, height: chartH,
        layout: { background: { color: '#1e293b' }, textColor: '#94a3b8' },
        grid: { vertLines: { color: '#334155' }, horzLines: { color: '#334155' } },
        crosshair: { mode: LightweightCharts.CrosshairMode.Normal },
        timeScale: { borderColor: '#475569', timeVisible: false },
        rightPriceScale: { borderColor: '#475569' },
    });

    // K 线
    const candleSeries = _tvChart.addCandlestickSeries({
        upColor: '#10b981', downColor: '#ef4444',
        borderUpColor: '#10b981', borderDownColor: '#ef4444',
        wickUpColor: '#10b981', wickDownColor: '#ef4444',
    });
    const kData = data.kline_data.map(p => ({ time: p.date, open: p.open, high: p.high, low: p.low, close: p.close }));
    candleSeries.setData(kData);

    // 成交量
    if (data.volume_data && data.volume_data.length > 0) {
        const volSeries = _tvChart.addHistogramSeries({ priceFormat: { type: 'volume' }, priceScaleId: 'vol' });
        _tvChart.priceScale('vol').applyOptions({ scaleMargins: { top: 0.8, bottom: 0 } });
        volSeries.setData(data.volume_data.map((v, i) => ({
            time: v.date, value: v.volume,
            color: kData[i] && kData[i].close >= kData[i].open ? 'rgba(16,185,129,0.3)' : 'rgba(239,68,68,0.3)',
        })));
    }

    // 成本线
    if (data.cost_line && data.cost_line > 0) {
        candleSeries.createPriceLine({ price: data.cost_line, color: '#f59e0b', lineWidth: 2, lineStyle: LightweightCharts.LineStyle.Dashed, axisLabelVisible: true, title: '成本' });
        const last = kData[kData.length - 1];
        const pnl = last ? ((last.close - data.cost_line) / data.cost_line * 100).toFixed(2) : 0;
        const pnlC = pnl >= 0 ? 'var(--green)' : 'var(--red)';
        document.getElementById('chartCostInfo').innerHTML = `<div style="display:flex;gap:12px;justify-content:center;color:var(--text2)"><span>成本 ¥${data.cost_line.toFixed(4)}</span><span style="color:${pnlC}">浮盈 ${pnl >= 0 ? '+' : ''}${pnl}%</span></div>`;
    }

    // RSI
    if (data.indicators && data.indicators.rsi_14 && data.indicators.rsi_14.length > 0) {
        const rsiWrap = document.getElementById('tvRsiWrap');
        rsiWrap.innerHTML = '';
        _tvRsiChart = LightweightCharts.createChart(rsiWrap, {
            width: rsiWrap.clientWidth, height: 80,
            layout: { background: { color: '#1e293b' }, textColor: '#94a3b8' },
            grid: { vertLines: { color: '#334155' }, horzLines: { color: '#334155' } },
            timeScale: { borderColor: '#475569', timeVisible: false },
            rightPriceScale: { borderColor: '#475569' },
        });
        const rsiSeries = _tvRsiChart.addLineSeries({ color: '#818cf8', lineWidth: 1.5, priceFormat: { minMove: 0.01 } });
        rsiSeries.setData(data.indicators.rsi_14.map(r => ({ time: r.date, value: r.rsi })));
        rsiSeries.createPriceLine({ price: 70, color: '#ef4444', lineWidth: 1, lineStyle: 2, axisLabelVisible: false, title: '' });
        rsiSeries.createPriceLine({ price: 30, color: '#10b981', lineWidth: 1, lineStyle: 2, axisLabelVisible: false, title: '' });
        // 同步时间轴
        _tvChart.timeScale().subscribeVisibleLogicalRangeChange(range => { if (range && _tvRsiChart) _tvRsiChart.timeScale().setVisibleLogicalRange(range); });
        _tvRsiChart.timeScale().subscribeVisibleLogicalRangeChange(range => { if (range && _tvChart) _tvChart.timeScale().setVisibleLogicalRange(range); });
    }

    _tvChart.timeScale().fitContent();
    if (_tvRsiChart) _tvRsiChart.timeScale().fitContent();

    // resize
    new ResizeObserver(() => {
        if (_tvChart) _tvChart.applyOptions({ width: wrap.clientWidth });
        const rw = document.getElementById('tvRsiWrap');
        if (_tvRsiChart && rw) _tvRsiChart.applyOptions({ width: rw.clientWidth });
    }).observe(wrap);
}

// 保留 renderChart() 作为 navigateTo('chart') 的 fallback
async function renderChart() {
    if (!_chartFundCode) { navigateTo('stocks'); return; }
    _openChartModal();
}
