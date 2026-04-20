/**
 * Cyberpunk Signal Monitor - Main Application
 * Full interactive dashboard with tabs, charts, modals, and real-time updates.
 */

// ==================== UTILITIES ====================
const Utils = {
    fmt(n, decimals = 2) {
        if (n == null || isNaN(n)) return '--';
        return Number(n).toLocaleString(undefined, { minimumFractionDigits: decimals, maximumFractionDigits: decimals });
    },
    fmtPrice(n) {
        if (n == null || isNaN(n)) return '--';
        const num = Number(n);
        if (num >= 1000) return num.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 });
        if (num >= 1) return num.toFixed(4);
        return num.toFixed(6);
    },
    fmtPct(n) {
        if (n == null || isNaN(n)) return '--';
        const v = Number(n);
        const cls = v > 0 ? 'pnl-positive' : v < 0 ? 'pnl-negative' : 'pnl-neutral';
        return `<span class="${cls}">${v > 0 ? '+' : ''}${v.toFixed(2)}%</span>`;
    },
    fmtTime(ts) {
        if (!ts) return '--';
        const d = new Date(ts);
        if (isNaN(d.getTime())) return '--';
        return d.toLocaleString();
    },
    /**
     * Format elapsed duration since a timestamp.
     * @param {string|Date} startTime - ISO timestamp or Date of when the signal started
     * @returns {string} Human-readable duration like "2h 15m" or "3d 1h"
     */
    fmtDuration(startTime) {
        if (!startTime) return '--';
        const start = new Date(startTime);
        if (isNaN(start.getTime())) return '--';
        const now = new Date();
        const diff = now - start;
        if (diff < 0) return '--';
        const days = Math.floor(diff / 86400000);
        const hours = Math.floor((diff % 86400000) / 3600000);
        const mins = Math.floor((diff % 3600000) / 60000);
        if (days > 0) return `${days}d ${hours}h`;
        if (hours > 0) return `${hours}h ${mins}m`;
        return `${mins}m`;
    },
    /**
     * Get CSS class for long-running signals (over 12h).
     * @param {string|Date} startTime
     * @returns {string} CSS class
     */
    durationClass(startTime) {
        if (!startTime) return 'duration-cell';
        const diff = new Date() - new Date(startTime);
        return diff > 43200000 ? 'duration-cell duration-long' : 'duration-cell';
    },
    signalClass(type) {
        if (!type) return '';
        const t = type.toUpperCase();
        if (t.includes('STRONG_LONG')) return 'signal-strong-long';
        if (t.includes('STRONG_SHORT')) return 'signal-strong-short';
        if (t.includes('LONG')) return 'signal-long';
        if (t.includes('SHORT')) return 'signal-short';
        return '';
    },
    signalLabel(type) {
        if (!type) return '--';
        return type.replace('STRONG_', '⚡ ').replace('_', ' ');
    },
    statusBadge(status) {
        const map = {
            'ACTIVE': 'status-active',
            'CLOSED_WIN': 'status-win',
            'CLOSED_LOSS': 'status-loss',
            'EXPIRED': 'status-expired',
            'CANCELLED': 'status-cancelled'
        };
        const cls = map[status] || 'status-active';
        return `<span class="status-badge ${cls}">${status || '--'}</span>`;
    },
    async api(url) {
        try {
            const res = await fetch(url);
            const data = await res.json();
            if (!data.success) throw new Error(data.error || 'API Error');
            return data;
        } catch (err) {
            console.error('API Error:', url, err);
            App.toast.show(err.message, 'error');
            return null;
        }
    }
};

// ==================== TOAST NOTIFICATIONS ====================
class Toast {
    constructor() {
        this.container = document.getElementById('toast-container');
    }

    show(message, type = 'info', duration = 4000) {
        const icons = { success: 'fa-check-circle', error: 'fa-exclamation-circle', warning: 'fa-exclamation-triangle', info: 'fa-info-circle' };
        const toast = document.createElement('div');
        toast.className = `toast ${type}`;
        toast.innerHTML = `<i class="fas ${icons[type] || icons.info}"></i><span>${message}</span>`;
        this.container.appendChild(toast);
        setTimeout(() => {
            toast.classList.add('hiding');
            setTimeout(() => toast.remove(), 300);
        }, duration);
    }
}

// ==================== MODAL ====================
class Modal {
    constructor() {
        this.overlay = document.getElementById('signal-modal');
        this.body = document.getElementById('modal-body');
    }

    show(signal) {
        const pnl = signal.pnl_percent;
        const pnlCls = pnl > 0 ? 'positive' : pnl < 0 ? 'negative' : '';
        const extInfo = signal.extended
            ? `<div class="detail-item"><div class="label">Extended By</div><div class="value">${signal.extension_hours || '--'}h</div></div>`
            : '';

        this.body.innerHTML = `
            <div class="detail-grid">
                <div class="detail-item"><div class="label">ID</div><div class="value">#${signal.id}</div></div>
                <div class="detail-item"><div class="label">Symbol</div><div class="value">${signal.symbol}</div></div>
                <div class="detail-item"><div class="label">Signal Type</div><div class="value ${Utils.signalClass(signal.signal_type)}">${Utils.signalLabel(signal.signal_type)}</div></div>
                <div class="detail-item"><div class="label">Score</div><div class="value">${signal.score || '--'}</div></div>
                <div class="detail-item"><div class="label">Entry Price</div><div class="value">${Utils.fmtPrice(signal.entry_price)}</div></div>
                <div class="detail-item"><div class="label">Stop Loss</div><div class="value">${Utils.fmtPrice(signal.stop_loss)}</div></div>
                <div class="detail-item"><div class="label">Take Profit</div><div class="value">${Utils.fmtPrice(signal.take_profit)}</div></div>
                <div class="detail-item"><div class="label">Risk:Reward</div><div class="value">${signal.rr_ratio || '--'}</div></div>
                <div class="detail-item"><div class="label">ATR</div><div class="value">${signal.atr_value ? Utils.fmtPrice(signal.atr_value) : '--'}</div></div>
                <div class="detail-item"><div class="label">TP Source</div><div class="value">${signal.tp_source || '--'}</div></div>
                <div class="detail-item"><div class="label">Trail Start</div><div class="value">${signal.trail_start ? Utils.fmtPrice(signal.trail_start) : '--'}</div></div>
                <div class="detail-item"><div class="label">Trail Stop</div><div class="value">${signal.trail_stop ? Utils.fmtPrice(signal.trail_stop) : '--'}</div></div>
                <div class="detail-item"><div class="label">Status</div><div class="value">${Utils.statusBadge(signal.status)}</div></div>
                <div class="detail-item"><div class="label">PnL</div><div class="value ${pnlCls}">${pnl != null ? (pnl > 0 ? '+' : '') + Number(pnl).toFixed(2) + '%' : '--'}</div></div>
                <div class="detail-item"><div class="label">Exit Price</div><div class="value">${signal.exit_price ? Utils.fmtPrice(signal.exit_price) : '--'}</div></div>
                <div class="detail-item"><div class="label">Hold Hours</div><div class="value">${signal.hold_hours || '--'}h</div></div>
                <div class="detail-item"><div class="label">Running For</div><div class="value">${Utils.fmtDuration(signal.confirmed_at || signal.timestamp)}</div></div>
                ${extInfo}
                <div class="detail-item"><div class="label">Entry Time</div><div class="value">${Utils.fmtTime(signal.confirmed_at || signal.timestamp)}</div></div>
                <div class="detail-item"><div class="label">Exit Time</div><div class="value">${Utils.fmtTime(signal.exit_time)}</div></div>
            </div>
        `;
        this.overlay.classList.add('show');
    }

    close() {
        this.overlay.classList.remove('show');
    }
}

// ==================== SIGNALS MODULE ====================
class SignalsModule {
    constructor() {
        this.data = [];
        this.filtered = [];
        this.page = 1;
        this.perPage = 25;
    }

    async load() {
        const result = await Utils.api('/api/signals?limit=200');
        if (!result) return;
        this.data = result.data || [];
        this.filtered = [...this.data];
        this.populateSymbolFilter();
        this.render();
    }

    populateSymbolFilter() {
        const select = document.getElementById('filter-symbol');
        const symbols = [...new Set(this.data.map(s => s.symbol))].sort();
        // Keep existing "All Symbols" option
        select.innerHTML = '<option value="">All Symbols</option>';
        symbols.forEach(sym => {
            const opt = document.createElement('option');
            opt.value = sym;
            opt.textContent = sym;
            select.appendChild(opt);
        });
    }

    applyFilters() {
        const symbol = document.getElementById('filter-symbol').value;
        const status = document.getElementById('filter-status').value;
        const type = document.getElementById('filter-type').value;

        this.filtered = this.data.filter(s => {
            if (symbol && s.symbol !== symbol) return false;
            if (status && s.status !== status) return false;
            if (type && s.signal_type !== type) return false;
            return true;
        });

        this.page = 1;
        this.render();
        App.toast.show(`Showing ${this.filtered.length} signals`, 'info');
    }

    render() {
        const tbody = document.querySelector('#all-signals-table tbody');
        const start = (this.page - 1) * this.perPage;
        const pageData = this.filtered.slice(start, start + this.perPage);

        if (pageData.length === 0) {
            tbody.innerHTML = '<tr class="loading-row"><td colspan="11">No signals found</td></tr>';
            return;
        }

        tbody.innerHTML = pageData.map(s => `
            <tr onclick="App.modal.show(${JSON.stringify(s).replace(/"/g, '&quot;')})" style="cursor:pointer">
                <td>#${s.id}</td>
                <td>${s.symbol}</td>
                <td class="${Utils.signalClass(s.signal_type)}">${Utils.signalLabel(s.signal_type)}</td>
                <td>${s.score || '--'}</td>
                <td>${Utils.fmtPrice(s.entry_price)}</td>
                <td>${Utils.fmtPrice(s.stop_loss)}</td>
                <td>${Utils.fmtPrice(s.take_profit)}</td>
                <td>${s.rr_ratio || '--'}</td>
                <td>${Utils.statusBadge(s.status)}</td>
                <td>${Utils.fmtPct(s.pnl_percent)}</td>
                <td>${Utils.fmtTime(s.timestamp)}</td>
            </tr>
        `).join('');

        this.renderPagination();
    }

    renderPagination() {
        const container = document.getElementById('signals-pagination');
        const totalPages = Math.ceil(this.filtered.length / this.perPage);
        if (totalPages <= 1) { container.innerHTML = ''; return; }

        let html = '';
        const maxButtons = 7;
        let startPage = Math.max(1, this.page - 3);
        let endPage = Math.min(totalPages, startPage + maxButtons - 1);
        if (endPage - startPage < maxButtons - 1) startPage = Math.max(1, endPage - maxButtons + 1);

        if (this.page > 1) html += `<button onclick="App.signals.goPage(${this.page - 1})">&laquo;</button>`;
        for (let i = startPage; i <= endPage; i++) {
            html += `<button class="${i === this.page ? 'active' : ''}" onclick="App.signals.goPage(${i})">${i}</button>`;
        }
        if (this.page < totalPages) html += `<button onclick="App.signals.goPage(${this.page + 1})">&raquo;</button>`;

        container.innerHTML = html;
    }

    goPage(p) {
        this.page = p;
        this.render();
    }
}

// ==================== HISTORY MODULE ====================
class HistoryModule {
    constructor() {
        this.data = [];
    }

    async load() {
        const status = document.getElementById('history-status').value;
        const result = await Utils.api(`/api/signals?status=${status}&limit=200`);
        if (!result) return;
        this.data = result.data || [];
        this.render();
        App.toast.show(`Loaded ${this.data.length} ${status} signals`, 'info');
    }

    render() {
        const tbody = document.querySelector('#history-table tbody');
        if (this.data.length === 0) {
            tbody.innerHTML = '<tr class="loading-row"><td colspan="10">No signals found</td></tr>';
            return;
        }

        tbody.innerHTML = this.data.map(s => `
            <tr onclick="App.modal.show(${JSON.stringify(s).replace(/"/g, '&quot;')})" style="cursor:pointer">
                <td>#${s.id}</td>
                <td>${s.symbol}</td>
                <td class="${Utils.signalClass(s.signal_type)}">${Utils.signalLabel(s.signal_type)}</td>
                <td>${Utils.fmtPrice(s.entry_price)}</td>
                <td>${Utils.fmtPrice(s.exit_price)}</td>
                <td>${Utils.fmtPct(s.pnl_percent)}</td>
                <td>${Utils.statusBadge(s.status)}</td>
                <td>${s.hold_hours || '--'}</td>
                <td>${Utils.fmtTime(s.confirmed_at || s.timestamp)}</td>
                <td>${Utils.fmtTime(s.exit_time)}</td>
            </tr>
        `).join('');
    }
}

// ==================== ANALYTICS MODULE ====================
class AnalyticsModule {
    constructor() {
        this.charts = {};
    }

    async load() {
        await Promise.all([
            this.loadDailyChart(),
            this.loadWinLossChart(),
            this.loadSymbolChart(),
            this.loadHoldDurationTable()
        ]);
    }

    async loadDailyChart() {
        const result = await Utils.api('/api/performance/daily?days=30');
        if (!result || !result.data) return;

        const data = result.data;
        const labels = data.map(d => d.date ? d.date.slice(5) : '');
        const totals = data.map(d => d.total_signals);
        const wins = data.map(d => d.winning_signals);
        const winrates = data.map(d => d.winrate);

        if (this.charts.daily) this.charts.daily.destroy();

        this.charts.daily = new Chart(document.getElementById('chart-daily'), {
            type: 'bar',
            data: {
                labels,
                datasets: [
                    {
                        label: 'Total',
                        data: totals,
                        backgroundColor: 'rgba(0, 255, 255, 0.3)',
                        borderColor: '#00ffff',
                        borderWidth: 1,
                        yAxisID: 'y'
                    },
                    {
                        label: 'Wins',
                        data: wins,
                        backgroundColor: 'rgba(0, 255, 128, 0.3)',
                        borderColor: '#00ff80',
                        borderWidth: 1,
                        yAxisID: 'y'
                    },
                    {
                        label: 'Win Rate %',
                        data: winrates,
                        type: 'line',
                        borderColor: '#a855f7',
                        backgroundColor: 'rgba(168, 85, 247, 0.1)',
                        tension: 0.3,
                        fill: true,
                        yAxisID: 'y1',
                        pointRadius: 3,
                        pointBackgroundColor: '#a855f7'
                    }
                ]
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                plugins: { legend: { labels: { color: '#8888aa', font: { family: 'monospace' } } } },
                scales: {
                    x: { ticks: { color: '#555570' }, grid: { color: 'rgba(30, 30, 53, 0.5)' } },
                    y: { ticks: { color: '#555570' }, grid: { color: 'rgba(30, 30, 53, 0.5)' }, title: { display: true, text: 'Signals', color: '#8888aa' } },
                    y1: { position: 'right', ticks: { color: '#555570' }, grid: { display: false }, title: { display: true, text: 'Win Rate %', color: '#8888aa' }, min: 0, max: 100 }
                }
            }
        });
    }

    async loadWinLossChart() {
        const result = await Utils.api('/api/stats/summary');
        if (!result || !result.data) return;

        const d = result.data;
        if (this.charts.winloss) this.charts.winloss.destroy();

        this.charts.winloss = new Chart(document.getElementById('chart-winloss'), {
            type: 'doughnut',
            data: {
                labels: ['Wins', 'Losses'],
                datasets: [{
                    data: [d.total_wins || 0, d.total_losses || 0],
                    backgroundColor: ['rgba(0, 255, 128, 0.6)', 'rgba(255, 0, 64, 0.6)'],
                    borderColor: ['#00ff80', '#ff0040'],
                    borderWidth: 2
                }]
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                plugins: {
                    legend: { position: 'bottom', labels: { color: '#8888aa', font: { family: 'monospace' }, padding: 20 } }
                }
            }
        });
    }

    async loadSymbolChart() {
        const result = await Utils.api('/api/stats/summary');
        if (!result || !result.data || !result.data.by_symbol) return;

        const bySymbol = result.data.by_symbol;
        // Aggregate by symbol
        const agg = {};
        bySymbol.forEach(s => {
            if (!agg[s.symbol]) agg[s.symbol] = { wins: 0, losses: 0 };
            agg[s.symbol].wins += s.winning_signals || 0;
            agg[s.symbol].losses += s.losing_signals || 0;
        });

        const symbols = Object.keys(agg);
        const wins = symbols.map(s => agg[s].wins);
        const losses = symbols.map(s => agg[s].losses);

        if (this.charts.symbol) this.charts.symbol.destroy();

        this.charts.symbol = new Chart(document.getElementById('chart-symbol'), {
            type: 'bar',
            data: {
                labels: symbols.map(s => s.replace('USDT', '')),
                datasets: [
                    { label: 'Wins', data: wins, backgroundColor: 'rgba(0, 255, 128, 0.5)', borderColor: '#00ff80', borderWidth: 1 },
                    { label: 'Losses', data: losses, backgroundColor: 'rgba(255, 0, 64, 0.5)', borderColor: '#ff0040', borderWidth: 1 }
                ]
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                indexAxis: 'y',
                plugins: { legend: { labels: { color: '#8888aa', font: { family: 'monospace' } } } },
                scales: {
                    x: { ticks: { color: '#555570' }, grid: { color: 'rgba(30, 30, 53, 0.5)' }, stacked: true },
                    y: { ticks: { color: '#555570' }, grid: { color: 'rgba(30, 30, 53, 0.5)' }, stacked: true }
                }
            }
        });
    }

    async loadHoldDurationTable() {
        const result = await Utils.api('/api/hold-duration/stats');
        if (!result || !result.data) return;

        const tbody = document.querySelector('#hold-duration-table tbody');
        if (result.data.length === 0) {
            tbody.innerHTML = '<tr class="loading-row"><td colspan="6">No hold duration data</td></tr>';
            return;
        }

        tbody.innerHTML = result.data.map(s => `
            <tr>
                <td>${s.symbol || '--'}</td>
                <td>${s.avg_hold_hours || '--'}</td>
                <td>${s.extended_count || 0}</td>
                <td>${s.expired_count || 0}</td>
                <td>${Utils.fmtPct(s.avg_expired_pnl)}</td>
                <td>${s.total || 0}</td>
            </tr>
        `).join('');
    }
}

// ==================== MAIN APP ====================
const App = {
    toast: null,
    modal: null,
    signals: new SignalsModule(),
    history: new HistoryModule(),
    analytics: new AnalyticsModule(),
    intervals: [],
    activeSignals: [],
    summaryData: null,

    async init() {
        this.toast = new Toast();
        this.modal = new Modal();

        // Setup tabs
        document.querySelectorAll('.nav-link').forEach(link => {
            link.addEventListener('click', (e) => {
                e.preventDefault();
                this.switchTab(link.dataset.tab);
            });
        });

        // Close modal on overlay click
        document.getElementById('signal-modal').addEventListener('click', (e) => {
            if (e.target === e.currentTarget) this.modal.close();
        });

        // ESC to close modal
        document.addEventListener('keydown', (e) => {
            if (e.key === 'Escape') this.modal.close();
        });

        // Initial load
        await this.loadDashboard();
        this.startClock();
        this.startAutoRefresh();
        this.startPriceWebSocket();

        this.toast.show('Dashboard initialized', 'success');
    },

    switchTab(tabName) {
        document.querySelectorAll('.nav-link').forEach(l => l.classList.remove('active'));
        document.querySelector(`[data-tab="${tabName}"]`).classList.add('active');
        document.querySelectorAll('.tab-content').forEach(t => t.classList.remove('active'));
        document.getElementById(`tab-${tabName}`).classList.add('active');

        // Lazy load
        if (tabName === 'signals' && this.signals.data.length === 0) this.signals.load();
        if (tabName === 'analytics') this.analytics.load();
    },

    async loadDashboard() {
        await Promise.all([
            this.loadSummary(),
            this.loadActiveSignals()
        ]);
    },

    async loadSummary() {
        const result = await Utils.api('/api/stats/summary');
        if (!result || !result.data) return;
        this.summaryData = result.data;
        const d = result.data;

        document.getElementById('val-total').textContent = d.total_signals || 0;
        document.getElementById('val-wins').textContent = d.total_wins || 0;
        document.getElementById('val-losses').textContent = d.total_losses || 0;
        document.getElementById('val-winrate').textContent = (d.winrate || 0).toFixed(1) + '%';
        document.getElementById('val-pnl').textContent = (d.avg_pnl || 0).toFixed(2) + '%';
    },

    async loadActiveSignals() {
        const result = await Utils.api('/api/signals/active');
        if (!result) return;
        this.activeSignals = result.data || [];

        document.getElementById('val-active').textContent = this.activeSignals.length;
        document.getElementById('active-count').textContent = this.activeSignals.length;

        const tbody = document.querySelector('#active-signals-table tbody');
        if (this.activeSignals.length === 0) {
            tbody.innerHTML = '<tr class="loading-row"><td colspan="11">No active signals</td></tr>';
        } else {
            tbody.innerHTML = this.activeSignals.map(s => {
                const sJson = JSON.stringify(s).replace(/'/g, '&#39;').replace(/"/g, '&quot;');
                const currentPrice = s.current_price ? Utils.fmtPrice(s.current_price) : '--';
                const unrealizedPnl = s.unrealized_pnl != null ? Utils.fmtPct(s.unrealized_pnl) : '--';
                
                return `
            <tr>
                <td>${s.symbol}</td>
                <td class="${Utils.signalClass(s.signal_type)}">${Utils.signalLabel(s.signal_type)}</td>
                <td>${s.score || '--'}</td>
                <td>${Utils.fmtPrice(s.entry_price)}</td>
                <td class="current-price-cell" data-symbol="${s.symbol}">${currentPrice}</td>
                <td class="unrealized-pnl-cell" data-symbol="${s.symbol}">${unrealizedPnl}</td>
                <td>${Utils.fmtPrice(s.stop_loss)}</td>
                <td>${Utils.fmtPrice(s.take_profit)}</td>
                <td>${s.rr_ratio || '--'}</td>
                <td class="${Utils.durationClass(s.confirmed_at || s.timestamp)}">${Utils.fmtDuration(s.confirmed_at || s.timestamp)}</td>
                <td><button class="action-btn" onclick='App.modal.show(JSON.parse(this.dataset.signal))' data-signal="${sJson}" aria-label="View signal details for ${s.symbol}"><i class="fas fa-eye" aria-hidden="true"></i></button></td>
            </tr>`;
            }).join('');
        }

        // Connection status
        this.setConnectionStatus(true);
    },


    setConnectionStatus(connected) {
        const dot = document.getElementById('status-dot');
        const text = document.getElementById('connection-status');
        if (connected) {
            dot.classList.add('connected');
            text.textContent = 'Connected';
            text.style.color = '#00ff80';
        } else {
            dot.classList.remove('connected');
            text.textContent = 'Disconnected';
            text.style.color = '#ff0040';
        }
    },

    startClock() {
        const update = () => {
            document.getElementById('current-time').textContent = new Date().toLocaleTimeString();
        };
        update();
        setInterval(update, 1000);
    },

    startAutoRefresh() {
        // Refresh dashboard every 30 seconds
        setInterval(() => this.loadDashboard(), 30000);
    },

    /**
     * WebSocket-based real-time price updates for active signals
     * Connects to Binance Futures WebSocket stream for live price feeds
     */
    async startPriceWebSocket() {
        // Wait for initial active signals to load
        await new Promise(resolve => setTimeout(resolve, 1000));
        
        if (!this.activeSignals || this.activeSignals.length === 0) {
            // Retry after 5 seconds if no active signals yet
            setTimeout(() => this.startPriceWebSocket(), 5000);
            return;
        }

        const symbols = [...new Set(this.activeSignals.map(s => s.symbol.toLowerCase()))];
        if (symbols.length === 0) return;

        // Create WebSocket streams for each symbol (using mini ticker for efficiency)
        const streams = symbols.map(s => `${s}@miniTicker`);
        const wsUrl = `wss://fstream.binance.com/stream?streams=${streams.join('/')}`;
        
        console.log('[WebSocket] Connecting to:', wsUrl);
        
        let ws = null;
        let reconnectAttempts = 0;
        const maxReconnectAttempts = 10;
        
        const connect = () => {
            try {
                ws = new WebSocket(wsUrl);
                
                ws.onopen = () => {
                    console.log('[WebSocket] Connected');
                    reconnectAttempts = 0;
                    this.setConnectionStatus(true);
                };
                
                ws.onmessage = (event) => {
                    try {
                        const data = JSON.parse(event.data);
                        if (!data.data) return;
                        
                        const symbol = data.data.s; // e.g., "BTCUSDT"
                        const price = parseFloat(data.data.c); // current price
                        
                        // Update all cells with matching symbol
                        const priceCells = document.querySelectorAll(`.current-price-cell[data-symbol="${symbol}"]`);
                        const pnlCells = document.querySelectorAll(`.unrealized-pnl-cell[data-symbol="${symbol}"]`);
                        
                        if (priceCells.length > 0) {
                            priceCells.forEach(cell => {
                                cell.textContent = Utils.fmtPrice(price);
                                cell.classList.add('price-updated');
                                setTimeout(() => cell.classList.remove('price-updated'), 300);
                            });
                            
                            // Calculate and update PnL for each row
                            pnlCells.forEach(cell => {
                                const row = cell.closest('tr');
                                const entryCell = row.querySelector('td:nth-child(4)'); // Entry price column
                                const signalTypeCell = row.querySelector('td:nth-child(2)'); // Signal type column
                                
                                if (entryCell && signalTypeCell) {
                                    const entryPrice = parseFloat(entryCell.textContent.replace(/,/g, ''));
                                    const signalType = signalTypeCell.textContent.toUpperCase();
                                    
                                    if (!isNaN(entryPrice)) {
                                        let pnlPct;
                                        if (signalType.includes('LONG')) {
                                            pnlPct = ((price - entryPrice) / entryPrice) * 100;
                                        } else if (signalType.includes('SHORT')) {
                                            pnlPct = ((entryPrice - price) / entryPrice) * 100;
                                        } else {
                                            pnlPct = 0;
                                        }
                                        
                                        cell.innerHTML = Utils.fmtPct(pnlPct);
                                    }
                                }
                            });
                        }
                    } catch (err) {
                        console.error('[WebSocket] Error parsing message:', err);
                    }
                };
                
                ws.onerror = (error) => {
                    console.error('[WebSocket] Error:', error);
                    this.setConnectionStatus(false);
                };
                
                ws.onclose = () => {
                    console.log('[WebSocket] Disconnected');
                    this.setConnectionStatus(false);
                    
                    // Auto-reconnect with exponential backoff
                    if (reconnectAttempts < maxReconnectAttempts) {
                        const delay = Math.min(1000 * Math.pow(2, reconnectAttempts), 30000);
                        console.log(`[WebSocket] Reconnecting in ${delay}ms (attempt ${reconnectAttempts + 1}/${maxReconnectAttempts})`);
                        reconnectAttempts++;
                        setTimeout(connect, delay);
                    } else {
                        console.error('[WebSocket] Max reconnection attempts reached');
                    }
                };
                
            } catch (err) {
                console.error('[WebSocket] Failed to create connection:', err);
                this.setConnectionStatus(false);
            }
        };
        
        connect();
    }
};

// ==================== BOOT ====================
document.addEventListener('DOMContentLoaded', () => {
    App.init();
});
