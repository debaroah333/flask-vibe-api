/**
 * 個人記帳本前端主應用程式 (app.js)
 * 嚴格遵循 docs/前端串接.md 規格：
 * - 僅在有正文之請求設定 Content-Type: application/json
 * - GET 請求不帶 Content-Type，維持 CORS 簡單請求，並於網址附上 _t 毫秒快取破壞參數
 * - 所有請求均使用 cache: 'no-store' 與 mode: 'cors'
 * - 失敗時只顯示後端回傳之 message；網路中斷時顯示「目前連不上記帳服務」
 */

(function () {
  'use strict';

  // ==========================================
  // 全域狀態 (State Management)
  // ==========================================
  const state = {
    apiBaseUrl: '',
    currentTab: 'home',
    bookkeepingType: 'expense', // 'expense' | 'income'
    accounts: [],
    incomeCategories: [],
    expenseCategories: [],
    records: [], // 當月記帳紀錄 (花費或入帳)
    instruments: [],
    invoices: []
  };

  // ==========================================
  // DOM 元素快取 (Element Selectors)
  // ==========================================
  const dom = {
    toast: document.getElementById('toast'),
    viewConnection: document.getElementById('view-connection'),
    viewMain: document.getElementById('view-main'),
    formConnection: document.getElementById('form-connection'),
    inputApiUrl: document.getElementById('input-api-url'),
    connectionError: document.getElementById('connection-error'),
    headerTitle: document.getElementById('header-title'),
    btnClearConn: document.getElementById('btn-clear-conn'),
    bottomNav: document.getElementById('bottom-nav'),

    // 首頁元件
    pageHome: document.getElementById('page-home'),
    summaryMonthLabel: document.getElementById('summary-month-label'),
    summaryIncome: document.getElementById('summary-income'),
    summaryExpense: document.getElementById('summary-expense'),
    accountsList: document.getElementById('accounts-list'),
    btnRefreshAccounts: document.getElementById('btn-refresh-accounts'),
    cardCreateAccount: document.getElementById('card-create-account'),
    formCreateAccount: document.getElementById('form-create-account'),
    inputAccName: document.getElementById('input-acc-name'),

    // 記帳頁元件
    pageBookkeeping: document.getElementById('page-bookkeeping'),
    tabTypeExpense: document.getElementById('tab-type-expense'),
    tabTypeIncome: document.getElementById('tab-type-income'),
    quickCreateCategoryCard: document.getElementById('quick-create-category-card'),
    categoryEmptyHint: document.getElementById('category-empty-hint'),
    formQuickCategory: document.getElementById('form-quick-category'),
    inputQuickCatName: document.getElementById('input-quick-cat-name'),
    formRecord: document.getElementById('form-record'),
    labelRecordDate: document.getElementById('label-record-date'),
    inputRecordDate: document.getElementById('input-record-date'),
    inputRecordAmount: document.getElementById('input-record-amount'),
    selectRecordCategory: document.getElementById('select-record-category'),
    btnToggleAddCat: document.getElementById('btn-toggle-add-cat'),
    inlineAddCatWrap: document.getElementById('inline-add-cat-wrap'),
    inputInlineCatName: document.getElementById('input-inline-cat-name'),
    btnInlineCatSave: document.getElementById('btn-inline-cat-save'),
    labelRecordAccount: document.getElementById('label-record-account'),
    selectRecordAccount: document.getElementById('select-record-account'),
    groupMerchant: document.getElementById('group-merchant'),
    inputRecordMerchant: document.getElementById('input-record-merchant'),
    groupSource: document.getElementById('group-source'),
    inputRecordSource: document.getElementById('input-record-source'),
    inputRecordNote: document.getElementById('input-record-note'),
    hintDescRule: document.getElementById('hint-desc-rule'),
    btnSubmitRecord: document.getElementById('btn-submit-record'),
    recordListTitle: document.getElementById('record-list-title'),
    recordCountLabel: document.getElementById('record-count-label'),
    recordsContainer: document.getElementById('records-container'),

    // 投資頁元件
    pageInvestment: document.getElementById('page-investment'),
    btnFetchQuotes: document.getElementById('btn-fetch-quotes'),
    quoteFetchMsg: document.getElementById('quote-fetch-msg'),
    instrumentsList: document.getElementById('instruments-list'),
    cardCreateInstrument: document.getElementById('card-create-instrument'),
    formCreateInstrument: document.getElementById('form-create-instrument'),
    inputInstSymbol: document.getElementById('input-inst-symbol'),
    inputInstName: document.getElementById('input-inst-name'),

    // 發票頁元件
    pageInvoices: document.getElementById('page-invoices'),
    btnRefreshInvoices: document.getElementById('btn-refresh-invoices'),
    invoicesList: document.getElementById('invoices-list')
  };

  // ==========================================
  // 輔助函式：日期與金額格式化
  // ==========================================
  function getCurrentMonthKey() {
    const now = new Date();
    const year = now.getFullYear();
    const month = String(now.getMonth() + 1).padStart(2, '0');
    return `${year}-${month}`;
  }

  function getTodayString() {
    const now = new Date();
    const year = now.getFullYear();
    const month = String(now.getMonth() + 1).padStart(2, '0');
    const day = String(now.getDate()).padStart(2, '0');
    return `${year}-${month}-${day}`;
  }

  function getMonthDateRange(monthKey) {
    const [y, m] = monthKey.split('-').map(Number);
    const startDate = `${monthKey}-01`;
    const lastDay = new Date(y, m, 0).getDate();
    const endDate = `${monthKey}-${String(lastDay).padStart(2, '0')}`;
    return { startDate, endDate };
  }

  function formatCurrency(num) {
    const val = Number(num) || 0;
    return val.toLocaleString('zh-TW', {
      minimumFractionDigits: 2,
      maximumFractionDigits: 2
    });
  }

  // ==========================================
  // 提示訊息 (Toast) 控制
  // ==========================================
  let toastTimer = null;
  function showToast(message, isError = false) {
    if (!dom.toast) return;
    dom.toast.textContent = message;
    dom.toast.style.backgroundColor = isError ? 'rgba(217, 4, 41, 0.95)' : 'rgba(33, 37, 41, 0.95)';
    dom.toast.classList.remove('hidden');

    if (toastTimer) clearTimeout(toastTimer);
    toastTimer = setTimeout(() => {
      dom.toast.classList.add('hidden');
    }, 3200);
  }

  // ==========================================
  // 後端網址清理與正規化
  // ==========================================
  function normalizeBackendUrl(rawUrl) {
    const trimmed = (rawUrl || '').trim();
    if (!/^https?:\/\//i.test(trimmed)) {
      throw new Error('網址格式錯誤，必須以 http:// 或 https:// 開頭');
    }
    const url = new URL(trimmed);
    // macOS AirPlay Receiver 監聽在 [::1]:5000，將 localhost:5000 轉為 127.0.0.1:5000 避免衝突
    if (url.hostname === 'localhost' && url.port === '5000') {
      url.hostname = '127.0.0.1';
    }
    let base = url.origin;
    if (url.pathname && url.pathname !== '/') {
      base += url.pathname.replace(/\/+$/, '');
    }
    return base;
  }

  // ==========================================
  // 核心網路通訊模組 (API Fetch Wrapper)
  // ==========================================
  async function apiFetch(endpoint, options = {}) {
    if (!state.apiBaseUrl) {
      throw new Error('尚未設定後端連線網址');
    }

    const method = (options.method || 'GET').toUpperCase();
    const isGet = method === 'GET';

    // 組合完整網址
    let fullUrl = `${state.apiBaseUrl}${endpoint}`;

    // 依據規則：健康檢查與所有 GET 請求加上 _t 毫秒時間戳記
    if (isGet) {
      const sep = fullUrl.includes('?') ? '&' : '?';
      fullUrl = `${fullUrl}${sep}_t=${Date.now()}`;
    }

    const fetchOptions = {
      method: method,
      mode: 'cors',
      cache: 'no-store'
    };

    // 依據規則：有正文之請求才設定 Content-Type: application/json，GET 不得設定以維持簡單請求
    if (!isGet && options.body) {
      fetchOptions.headers = {
        'Content-Type': 'application/json',
        ...(options.headers || {})
      };
      fetchOptions.body = typeof options.body === 'string' ? options.body : JSON.stringify(options.body);
    } else if (options.headers) {
      fetchOptions.headers = options.headers;
    }

    let response;
    try {
      response = await fetch(fullUrl, fetchOptions);
    } catch (networkErr) {
      // 連不上伺服器、網路中斷或連線被拒
      throw new Error('目前連不上記帳服務');
    }

    let resultJson = null;
    try {
      resultJson = await response.json();
    } catch (parseErr) {
      // 回應非 JSON 格式
      if (!response.ok) {
        throw new Error('目前連不上記帳服務');
      }
      throw new Error('伺服器回應格式不正確');
    }

    // 後端標準三鍵結構：code, message, data
    if (!response.ok || (resultJson && resultJson.code >= 400)) {
      const errorMsg = (resultJson && resultJson.message) ? resultJson.message : '目前連不上記帳服務';
      throw new Error(errorMsg);
    }

    return resultJson;
  }

  // ==========================================
  // 畫面切換與導覽 (Navigation & View Routing)
  // ==========================================
  function switchView(viewName) {
    if (viewName === 'connection') {
      dom.viewConnection.classList.remove('hidden');
      dom.viewMain.classList.add('hidden');
    } else {
      dom.viewConnection.classList.add('hidden');
      dom.viewMain.classList.remove('hidden');
    }
  }

  function switchTab(tabName) {
    state.currentTab = tabName;

    // 更新底部按鈕高亮
    const navButtons = dom.bottomNav.querySelectorAll('.nav-item');
    navButtons.forEach(btn => {
      btn.classList.toggle('active', btn.dataset.target === tabName);
    });

    // 隱藏所有分頁
    dom.pageHome.classList.add('hidden');
    dom.pageBookkeeping.classList.add('hidden');
    dom.pageInvestment.classList.add('hidden');
    dom.pageInvoices.classList.add('hidden');

    // 顯示目標分頁與標題
    if (tabName === 'home') {
      dom.headerTitle.textContent = '首頁';
      dom.pageHome.classList.remove('hidden');
      loadHomeData();
    } else if (tabName === 'bookkeeping') {
      dom.headerTitle.textContent = '記帳';
      dom.pageBookkeeping.classList.remove('hidden');
      loadBookkeepingData();
    } else if (tabName === 'investment') {
      dom.headerTitle.textContent = '投資';
      dom.pageInvestment.classList.remove('hidden');
      loadInvestmentData();
    } else if (tabName === 'invoices') {
      dom.headerTitle.textContent = '發票';
      dom.pageInvoices.classList.remove('hidden');
      loadInvoicesData();
    }
  }

  // ==========================================
  // 連線管理 (Connection Management)
  // ==========================================
  async function testHealthAndConnect(targetUrl, isInitialCheck = false) {
    let normalizedUrl;
    try {
      normalizedUrl = normalizeBackendUrl(targetUrl);
    } catch (err) {
      if (dom.connectionError) {
        dom.connectionError.textContent = err.message;
        dom.connectionError.classList.remove('hidden');
      }
      return false;
    }

    if (dom.connectionError) {
      dom.connectionError.classList.add('hidden');
    }

    // 進行健康檢查：GET /api/health
    try {
      const fullHealthUrl = `${normalizedUrl}/api/health?_t=${Date.now()}`;
      const res = await fetch(fullHealthUrl, {
        method: 'GET',
        mode: 'cors',
        cache: 'no-store'
      });

      if (!res.ok) {
        throw new Error('伺服器未準備就緒');
      }

      const json = await res.json();
      if (json && json.code === 200 && json.data && json.data.status === 'ok') {
        // 連線成功，儲存並進入主頁
        state.apiBaseUrl = normalizedUrl;
        localStorage.setItem('ledger.connection', JSON.stringify({ apiBaseUrl: normalizedUrl }));
        switchView('main');
        switchTab('home');
        return true;
      } else {
        const msg = (json && json.message) ? json.message : '目前連不上記帳服務';
        throw new Error(msg);
      }
    } catch (err) {
      const displayMsg = (err.message && err.message !== 'Failed to fetch') 
        ? err.message 
        : '目前連不上記帳服務';

      if (dom.connectionError) {
        dom.connectionError.textContent = displayMsg;
        dom.connectionError.classList.remove('hidden');
      }
      if (!isInitialCheck) {
        showToast(displayMsg, true);
      }
      switchView('connection');
      return false;
    }
  }

  function handleClearConnection() {
    localStorage.removeItem('ledger.connection');
    state.apiBaseUrl = '';
    switchView('connection');
    if (dom.connectionError) {
      dom.connectionError.classList.add('hidden');
    }
    showToast('已清除連線資訊');
  }

  // ==========================================
  // 1. 首頁模組 (Home Module)
  // ==========================================
  async function loadHomeData() {
    const currentMonth = getCurrentMonthKey();
    if (dom.summaryMonthLabel) {
      dom.summaryMonthLabel.textContent = `${currentMonth} 月`;
    }

    // 1. 查詢月度現金流彙總：GET /api/summary?month=YYYY-MM
    try {
      const summaryRes = await apiFetch(`/api/summary?month=${encodeURIComponent(currentMonth)}`);
      if (summaryRes && summaryRes.data) {
        dom.summaryIncome.textContent = formatCurrency(summaryRes.data.income_twd);
        dom.summaryExpense.textContent = formatCurrency(summaryRes.data.expense_twd);
      }
    } catch (err) {
      showToast(err.message, true);
    }

    // 2. 查詢帳戶清單與個別餘額
    await loadAccountsWithBalance();
  }

  async function loadAccountsWithBalance() {
    if (!dom.accountsList) return;
    dom.accountsList.innerHTML = '<p class="sub-text">載入帳戶中...</p>';

    try {
      const accRes = await apiFetch('/api/accounts');
      state.accounts = (accRes && Array.isArray(accRes.data)) ? accRes.data : [];

      if (state.accounts.length === 0) {
        dom.accountsList.innerHTML = '';
        dom.cardCreateAccount.classList.remove('hidden');
        return;
      }

      dom.cardCreateAccount.classList.add('hidden');
      dom.accountsList.innerHTML = '';

      // 對每個帳戶並行查詢動態台幣餘額：GET /api/accounts/<id>/balance
      const accountCardsPromises = state.accounts.map(async (acc) => {
        let twdBalance = 0;
        try {
          const balRes = await apiFetch(`/api/accounts/${acc.id}/balance`);
          if (balRes && balRes.data) {
            twdBalance = (balRes.data.twd_balance !== undefined) 
              ? balRes.data.twd_balance 
              : (balRes.data.balance_twd !== undefined ? balRes.data.balance_twd : balRes.data.balance);
          }
        } catch {
          twdBalance = 0;
        }

        const kindLabels = {
          cash: '現金',
          bank: '銀行',
          credit_card: '信用卡',
          broker: '證券交割'
        };
        const kindText = kindLabels[acc.kind] || acc.kind;

        const card = document.createElement('div');
        card.className = 'account-item-card';
        card.innerHTML = `
          <div class="account-info">
            <div class="account-name-row">
              <span class="account-name">${escapeHtml(acc.name)}</span>
              <span class="account-kind-badge">${escapeHtml(kindText)}</span>
            </div>
            <span class="account-currency">${escapeHtml(acc.currency)}</span>
          </div>
          <div class="account-balance-val amount-num">
            $ ${formatCurrency(twdBalance)}
          </div>
        `;
        return card;
      });

      const cardElements = await Promise.all(accountCardsPromises);
      dom.accountsList.innerHTML = '';
      cardElements.forEach(card => dom.accountsList.appendChild(card));
    } catch (err) {
      dom.accountsList.innerHTML = '<p class="sub-text">暫時無法取得帳戶資料</p>';
      showToast(err.message, true);
    }
  }

  // 首頁建立第一個現金帳戶
  async function handleCreateCashAccount(e) {
    e.preventDefault();
    const name = (dom.inputAccName.value || '').trim();
    if (!name) {
      showToast('請輸入帳戶名稱', true);
      return;
    }

    const payload = {
      name: name,
      kind: 'cash',
      currency: 'TWD',
      opening_balance: 0.0
    };

    try {
      await apiFetch('/api/accounts', {
        method: 'POST',
        body: payload
      });
      showToast('現金帳戶建立成功');
      dom.inputAccName.value = '日常現金包';
      await loadHomeData();
    } catch (err) {
      showToast(err.message, true);
    }
  }

  // ==========================================
  // 2. 記帳模組 (Bookkeeping Module)
  // ==========================================
  async function loadBookkeepingData() {
    // 預設日期設為今天
    if (!dom.inputRecordDate.value) {
      dom.inputRecordDate.value = getTodayString();
    }

    // 載入帳戶下拉
    await refreshAccountDropdown();

    // 載入分類下拉
    await refreshCategoryDropdown();

    // 載入本月歷史紀錄
    await loadMonthRecords();
  }

  async function refreshAccountDropdown() {
    try {
      const accRes = await apiFetch('/api/accounts');
      state.accounts = (accRes && Array.isArray(accRes.data)) ? accRes.data : [];

      dom.selectRecordAccount.innerHTML = '';
      if (state.accounts.length === 0) {
        dom.selectRecordAccount.innerHTML = '<option value="">(無帳戶，請至首頁建立)</option>';
      } else {
        dom.selectRecordAccount.innerHTML = '<option value="">請選擇帳戶</option>';
        state.accounts.forEach(acc => {
          const opt = document.createElement('option');
          opt.value = acc.id;
          opt.textContent = `${acc.name} (${acc.currency})`;
          dom.selectRecordAccount.appendChild(opt);
        });
        // 預設選取第一個
        dom.selectRecordAccount.selectedIndex = 1;
      }
    } catch (err) {
      dom.selectRecordAccount.innerHTML = '<option value="">載入失敗</option>';
    }
  }

  async function refreshCategoryDropdown() {
    const isExpense = state.bookkeepingType === 'expense';
    const endpoint = isExpense ? '/api/expense-categories' : '/api/income-categories';

    dom.selectRecordCategory.innerHTML = '<option value="">載入中...</option>';
    try {
      const res = await apiFetch(endpoint);
      const categories = (res && Array.isArray(res.data)) ? res.data : [];

      if (isExpense) {
        state.expenseCategories = categories;
      } else {
        state.incomeCategories = categories;
      }

      dom.selectRecordCategory.innerHTML = '';
      if (categories.length === 0) {
        dom.selectRecordCategory.innerHTML = '<option value="">(尚無分類，請先新增)</option>';
        dom.quickCreateCategoryCard.classList.remove('hidden');
        dom.categoryEmptyHint.textContent = isExpense 
          ? '目前尚無花費分類，請先新增一個分類：' 
          : '目前尚無入帳分類，請先新增一個分類：';
      } else {
        dom.quickCreateCategoryCard.classList.add('hidden');
        dom.selectRecordCategory.innerHTML = '<option value="">請選擇分類</option>';
        categories.forEach(cat => {
          const opt = document.createElement('option');
          opt.value = cat.id;
          opt.textContent = cat.name;
          dom.selectRecordCategory.appendChild(opt);
        });
        dom.selectRecordCategory.selectedIndex = 1;
      }
    } catch (err) {
      dom.selectRecordCategory.innerHTML = '<option value="">載入失敗</option>';
    }
  }

  // 切換花費 / 入帳模式
  function setBookkeepingType(type) {
    state.bookkeepingType = type;
    const isExpense = type === 'expense';

    dom.tabTypeExpense.classList.toggle('active', isExpense);
    dom.tabTypeIncome.classList.toggle('active', !isExpense);

    if (isExpense) {
      dom.labelRecordDate.textContent = '消費日期';
      dom.labelRecordAccount.textContent = '支付帳戶';
      dom.groupMerchant.classList.remove('hidden');
      dom.groupSource.classList.add('hidden');
      dom.hintDescRule.textContent = '商家名稱與備註至少需填寫一項';
      dom.btnSubmitRecord.textContent = '記錄花費';
      dom.recordListTitle.textContent = '本月花費明細';
    } else {
      dom.labelRecordDate.textContent = '入帳日期';
      dom.labelRecordAccount.textContent = '存入帳戶';
      dom.groupMerchant.classList.add('hidden');
      dom.groupSource.classList.remove('hidden');
      dom.hintDescRule.textContent = '來源描述與備註至少需填寫一項';
      dom.btnSubmitRecord.textContent = '記錄入帳';
      dom.recordListTitle.textContent = '本月入帳明細';
    }

    refreshCategoryDropdown();
    loadMonthRecords();
  }

  // 載入當月紀錄列表
  async function loadMonthRecords() {
    const isExpense = state.bookkeepingType === 'expense';
    const monthKey = getCurrentMonthKey();
    const { startDate, endDate } = getMonthDateRange(monthKey);

    const endpoint = isExpense
      ? `/api/expenses?date_from=${startDate}&date_to=${endDate}`
      : `/api/incomes?date_from=${startDate}&date_to=${endDate}`;

    dom.recordsContainer.innerHTML = '<p class="sub-text">載入明細中...</p>';
    try {
      const res = await apiFetch(endpoint);
      state.records = (res && Array.isArray(res.data)) ? res.data : [];
      // 依日期倒序排列，讓最新筆放最上方
      state.records.sort((a, b) => {
        const da = a.spent_on || a.received_on || '';
        const db = b.spent_on || b.received_on || '';
        return db.localeCompare(da) || (b.id - a.id);
      });
      renderRecordsList();
    } catch (err) {
      dom.recordsContainer.innerHTML = '<p class="sub-text">暫無明細紀錄</p>';
      dom.recordCountLabel.textContent = '0 筆';
    }
  }

  function renderRecordsList() {
    dom.recordsContainer.innerHTML = '';
    const isExpense = state.bookkeepingType === 'expense';
    dom.recordCountLabel.textContent = `${state.records.length} 筆`;

    if (state.records.length === 0) {
      dom.recordsContainer.innerHTML = '<p class="sub-text">本月尚無紀錄</p>';
      return;
    }

    // 建立分類名稱對照快取
    const catMap = {};
    const catList = isExpense ? state.expenseCategories : state.incomeCategories;
    catList.forEach(c => { catMap[c.id] = c.name; });

    state.records.forEach(item => {
      const title = isExpense 
        ? (item.merchant || item.note || '花費') 
        : (item.source || item.note || '入帳');
      const dateStr = item.spent_on || item.received_on || '';
      const catName = catMap[item.category_id] || '一般';
      const sign = isExpense ? '-' : '+';
      const typeClass = isExpense ? 'expense' : 'income';

      const card = document.createElement('div');
      card.className = 'record-row-card';
      card.innerHTML = `
        <div class="record-meta">
          <div class="record-primary-row">
            <span class="record-title">${escapeHtml(title)}</span>
            <span class="record-cat-badge">${escapeHtml(catName)}</span>
          </div>
          <span class="record-date-sub">${escapeHtml(dateStr)}</span>
        </div>
        <div class="record-amount-val amount-num ${typeClass}">
          ${sign} $ ${formatCurrency(item.amount)}
        </div>
      `;
      dom.recordsContainer.appendChild(card);
    });
  }

  // 提交新增花費 / 入帳
  async function handleRecordSubmit(e) {
    e.preventDefault();
    const isExpense = state.bookkeepingType === 'expense';

    const dateVal = (dom.inputRecordDate.value || '').trim();
    const amountVal = parseFloat(dom.inputRecordAmount.value);
    const categoryId = parseInt(dom.selectRecordCategory.value, 10);
    const accountId = parseInt(dom.selectRecordAccount.value, 10);
    const noteVal = (dom.inputRecordNote.value || '').trim() || null;

    if (!dateVal) {
      showToast('請選擇日期', true);
      return;
    }
    if (isNaN(amountVal) || amountVal <= 0) {
      showToast('金額必須大於 0', true);
      return;
    }
    if (!categoryId) {
      showToast('請選擇分類', true);
      return;
    }
    if (!accountId) {
      showToast('請選擇帳戶', true);
      return;
    }

    let payload = {};
    let endpoint = '';

    if (isExpense) {
      const merchantVal = (dom.inputRecordMerchant.value || '').trim() || null;
      if (!merchantVal && !noteVal) {
        showToast('商家名稱與備註至少需填寫一項', true);
        return;
      }
      endpoint = '/api/expenses';
      payload = {
        spent_on: dateVal,
        amount: amountVal,
        category_id: categoryId,
        account_id: accountId,
        merchant: merchantVal,
        note: noteVal,
        currency: 'TWD',
        fx_rate_to_twd: 1.0
      };
    } else {
      const sourceVal = (dom.inputRecordSource.value || '').trim() || null;
      if (!sourceVal && !noteVal) {
        showToast('來源描述與備註至少需填寫一項', true);
        return;
      }
      endpoint = '/api/incomes';
      payload = {
        received_on: dateVal,
        amount: amountVal,
        category_id: categoryId,
        account_id: accountId,
        source: sourceVal,
        note: noteVal,
        currency: 'TWD',
        fx_rate_to_twd: 1.0
      };
    }

    try {
      const res = await apiFetch(endpoint, {
        method: 'POST',
        body: payload
      });

      showToast(isExpense ? '花費記錄新增成功' : '入帳明細新增成功');

      // 依據規則：成功後把新列放在本月清單最上方
      if (res && res.data) {
        state.records.unshift(res.data);
        renderRecordsList();
      }

      // 清空部分表單輸入
      dom.inputRecordAmount.value = '';
      if (dom.inputRecordMerchant) dom.inputRecordMerchant.value = '';
      if (dom.inputRecordSource) dom.inputRecordSource.value = '';
      dom.inputRecordNote.value = '';
    } catch (err) {
      showToast(err.message, true);
    }
  }

  // 快速新增分類 (無分類時之提示卡或折疊列)
  async function handleAddCategory(name) {
    const trimmed = (name || '').trim();
    if (!trimmed) {
      showToast('分類名稱不可為空', true);
      return;
    }

    const isExpense = state.bookkeepingType === 'expense';
    const endpoint = isExpense ? '/api/expense-categories' : '/api/income-categories';

    try {
      const res = await apiFetch(endpoint, {
        method: 'POST',
        body: { name: trimmed }
      });

      showToast('分類新增成功');
      await refreshCategoryDropdown();

      // 自動選中剛新增的分類
      if (res && res.data && res.data.id) {
        dom.selectRecordCategory.value = String(res.data.id);
      }

      // 隱藏新增欄位
      dom.inlineAddCatWrap.classList.add('hidden');
      dom.inputInlineCatName.value = '';
      dom.inputQuickCatName.value = '';
    } catch (err) {
      showToast(err.message, true);
    }
  }

  // ==========================================
  // 3. 投資模組 (Investment Module)
  // ==========================================
  async function loadInvestmentData() {
    if (!dom.instrumentsList) return;
    dom.instrumentsList.innerHTML = '<p class="sub-text">載入投資標的中...</p>';

    try {
      const instRes = await apiFetch('/api/instruments');
      const allInstruments = (instRes && Array.isArray(instRes.data)) ? instRes.data : [];

      // 依據規格：僅列出市場為 tw_stock 之標的
      state.instruments = allInstruments.filter(item => item.market === 'tw_stock');

      if (state.instruments.length === 0) {
        dom.instrumentsList.innerHTML = '<p class="sub-text">目前尚無台股標的</p>';
        dom.cardCreateInstrument.classList.remove('hidden');
        return;
      }

      dom.cardCreateInstrument.classList.add('hidden');
      dom.instrumentsList.innerHTML = '';

      // 針對每一檔標的，取得持倉 GET /api/instruments/<id>/holding 與報價 GET /api/instruments/<id>/quote
      const cardsPromises = state.instruments.map(async (inst) => {
        let holdingQty = 0;
        let avgCost = 0;

        try {
          const holdRes = await apiFetch(`/api/instruments/${inst.id}/holding`);
          if (holdRes && holdRes.data) {
            holdingQty = holdRes.data.quantity || 0;
            avgCost = holdRes.data.average_buy_cost || 0;
          }
        } catch {
          holdingQty = 0;
        }

        // 最新報價：GET /api/instruments/<id>/quote；若查無則顯示「還沒有報價」
        let quoteText = '還沒有報價';
        try {
          const quoteRes = await apiFetch(`/api/instruments/${inst.id}/quote`);
          if (quoteRes && quoteRes.data && quoteRes.data.price !== undefined && quoteRes.data.price !== null) {
            quoteText = `$ ${formatCurrency(quoteRes.data.price)}`;
          }
        } catch {
          quoteText = '還沒有報價';
        }

        const card = document.createElement('div');
        card.className = 'instrument-card';
        card.innerHTML = `
          <div class="instrument-main-row">
            <div class="instrument-info">
              <span class="instrument-name">${escapeHtml(inst.name)}</span>
              <span class="instrument-symbol">${escapeHtml(inst.symbol)}</span>
            </div>
            <div class="instrument-quote-val amount-num">
              ${escapeHtml(quoteText)}
            </div>
          </div>
          <div class="instrument-holding-row">
            <span>持有數量：<strong class="amount-num">${holdingQty}</strong> 股</span>
            <span>平均成本：<strong class="amount-num">$ ${formatCurrency(avgCost)}</strong></span>
          </div>
        `;
        return card;
      });

      const cards = await Promise.all(cardsPromises);
      dom.instrumentsList.innerHTML = '';
      cards.forEach(c => dom.instrumentsList.appendChild(c));
    } catch (err) {
      dom.instrumentsList.innerHTML = '<p class="sub-text">暫時無法取得投資標的</p>';
      showToast(err.message, true);
    }
  }

  // 抓取台股最新報價
  async function handleFetchQuotes() {
    dom.btnFetchQuotes.disabled = true;
    dom.btnFetchQuotes.textContent = '抓取中...';
    dom.quoteFetchMsg.classList.add('hidden');

    try {
      // 依據規格呼叫 POST /api/tw-stock-quotes/fetch
      const res = await apiFetch('/api/tw-stock-quotes/fetch', {
        method: 'POST',
        body: {}
      });

      // missing 與 skipped 用一句人話帶過
      let summaryText = '報價已更新完畢';
      if (res && res.data) {
        const missingCount = Array.isArray(res.data.missing) ? res.data.missing.length : (Number(res.data.missing) || 0);
        const skippedCount = Array.isArray(res.data.skipped) ? res.data.skipped.length : (Number(res.data.skipped) || 0);

        if (missingCount > 0 || skippedCount > 0) {
          summaryText = `報價更新完成（其中 ${missingCount} 檔查無報價、${skippedCount} 檔略過更新）`;
        } else {
          summaryText = '已成功抓取最新台股收盤報價';
        }
      } else if (res && res.message) {
        summaryText = res.message;
      }

      dom.quoteFetchMsg.textContent = summaryText;
      dom.quoteFetchMsg.classList.remove('hidden');
      showToast(summaryText);

      // 重新讀取標的持倉與報價
      await loadInvestmentData();
    } catch (err) {
      // 若後端端點未提供（404）或連線失敗，用一句人話呈現
      const friendlyMsg = err.message.includes('404') || err.message.includes('找不到')
        ? '證交所報價更新服務目前尚未提供'
        : err.message;
      dom.quoteFetchMsg.textContent = friendlyMsg;
      dom.quoteFetchMsg.classList.remove('hidden');
      showToast(friendlyMsg, true);
    } finally {
      dom.btnFetchQuotes.disabled = false;
      dom.btnFetchQuotes.textContent = '抓取最新報價';
    }
  }

  // 建立台股標的
  async function handleCreateInstrument(e) {
    e.preventDefault();
    const symbol = (dom.inputInstSymbol.value || '').trim();
    const name = (dom.inputInstName.value || '').trim();

    if (!symbol || !name) {
      showToast('請完整填寫代號與名稱', true);
      return;
    }

    const payload = {
      market: 'tw_stock',
      symbol: symbol,
      name: name,
      currency: 'TWD'
    };

    try {
      await apiFetch('/api/instruments', {
        method: 'POST',
        body: payload
      });
      showToast('台股標的建立成功');
      dom.inputInstSymbol.value = '';
      dom.inputInstName.value = '';
      await loadInvestmentData();
    } catch (err) {
      showToast(err.message, true);
    }
  }

  // ==========================================
  // 4. 發票模組 (Invoices Module - Read Only)
  // ==========================================
  async function loadInvoicesData() {
    if (!dom.invoicesList) return;
    dom.invoicesList.innerHTML = '<p class="sub-text">載入發票中...</p>';

    try {
      const res = await apiFetch('/api/invoices');
      state.invoices = (res && Array.isArray(res.data)) ? res.data : [];

      if (state.invoices.length === 0) {
        dom.invoicesList.innerHTML = '<p class="sub-text">目前尚無已登錄之發票</p>';
        return;
      }

      dom.invoicesList.innerHTML = '';

      state.invoices.forEach(inv => {
        const badgeInfo = getPrizeStatusBadge(inv.prize_status);

        const card = document.createElement('div');
        card.className = 'invoice-item-card';
        card.innerHTML = `
          <div class="invoice-meta">
            <div class="invoice-number-row">
              <span class="invoice-number">${escapeHtml(inv.number)}</span>
              <span class="badge ${badgeInfo.className}">${escapeHtml(badgeInfo.text)}</span>
            </div>
            <span class="invoice-date">開立日：${escapeHtml(inv.issued_on || '未填')}</span>
          </div>
          <div class="invoice-right">
            <span class="invoice-amount amount-num">$ ${formatCurrency(inv.amount)}</span>
          </div>
        `;
        dom.invoicesList.appendChild(card);
      });
    } catch (err) {
      dom.invoicesList.innerHTML = '<p class="sub-text">暫時無法取得發票清單</p>';
      showToast(err.message, true);
    }
  }

  function getPrizeStatusBadge(status) {
    switch (status) {
      case 'won':
        return { text: '已中獎', className: 'badge-won' };
      case 'claimed':
        return { text: '已兌領', className: 'badge-claimed' };
      case 'lost':
        return { text: '未中獎', className: 'badge-lost' };
      case 'void':
        return { text: '已作廢', className: 'badge-void' };
      case 'unchecked':
      default:
        return { text: '未對獎', className: 'badge-neutral' };
    }
  }

  // ==========================================
  // 安全轉義 HTML 避免 XSS
  // ==========================================
  function escapeHtml(str) {
    if (str === null || str === undefined) return '';
    return String(str)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#039;');
  }

  // ==========================================
  // 事件監聽綁定 (Event Listeners Setup)
  // ==========================================
  function setupEventListeners() {
    // 1. 連線表單提交
    if (dom.formConnection) {
      dom.formConnection.addEventListener('submit', async (e) => {
        e.preventDefault();
        const urlInput = (dom.inputApiUrl.value || '').trim();
        await testHealthAndConnect(urlInput, false);
      });
    }

    // 2. 清除連線
    if (dom.btnClearConn) {
      dom.btnClearConn.addEventListener('click', handleClearConnection);
    }

    // 3. 底部導航分頁切換
    if (dom.bottomNav) {
      dom.bottomNav.addEventListener('click', (e) => {
        const btn = e.target.closest('.nav-item');
        if (!btn) return;
        const targetTab = btn.dataset.target;
        if (targetTab) switchTab(targetTab);
      });
    }

    // 4. 首頁手動重新整理帳戶
    if (dom.btnRefreshAccounts) {
      dom.btnRefreshAccounts.addEventListener('click', () => loadAccountsWithBalance());
    }

    // 5. 首頁建立現金帳戶
    if (dom.formCreateAccount) {
      dom.formCreateAccount.addEventListener('submit', handleCreateCashAccount);
    }

    // 6. 記帳切換花費 / 入帳
    if (dom.tabTypeExpense) {
      dom.tabTypeExpense.addEventListener('click', () => setBookkeepingType('expense'));
    }
    if (dom.tabTypeIncome) {
      dom.tabTypeIncome.addEventListener('click', () => setBookkeepingType('income'));
    }

    // 7. 記帳表單提交
    if (dom.formRecord) {
      dom.formRecord.addEventListener('submit', handleRecordSubmit);
    }

    // 8. 展開折疊式新增分類欄
    if (dom.btnToggleAddCat) {
      dom.btnToggleAddCat.addEventListener('click', () => {
        dom.inlineAddCatWrap.classList.toggle('hidden');
        if (!dom.inlineAddCatWrap.classList.contains('hidden')) {
          dom.inputInlineCatName.focus();
        }
      });
    }

    // 9. 確定新增折疊分類
    if (dom.btnInlineCatSave) {
      dom.btnInlineCatSave.addEventListener('click', () => {
        handleAddCategory(dom.inputInlineCatName.value);
      });
    }

    // 10. 快速新增分類 (無分類提示卡)
    if (dom.formQuickCategory) {
      dom.formQuickCategory.addEventListener('submit', (e) => {
        e.preventDefault();
        handleAddCategory(dom.inputQuickCatName.value);
      });
    }

    // 11. 投資頁抓取報價
    if (dom.btnFetchQuotes) {
      dom.btnFetchQuotes.addEventListener('click', handleFetchQuotes);
    }

    // 12. 投資頁建立台股標的
    if (dom.formCreateInstrument) {
      dom.formCreateInstrument.addEventListener('submit', handleCreateInstrument);
    }

    // 13. 發票手動重新整理
    if (dom.btnRefreshInvoices) {
      dom.btnRefreshInvoices.addEventListener('click', () => loadInvoicesData());
    }
  }

  // ==========================================
  // 應用程式初始化 (App Initialization)
  // ==========================================
  async function init() {
    setupEventListeners();

    // 檢查 localStorage 內是否有儲存的連線資訊
    const savedConn = localStorage.getItem('ledger.connection');
    if (savedConn) {
      try {
        const parsed = JSON.parse(savedConn);
        if (parsed && parsed.apiBaseUrl) {
          dom.inputApiUrl.value = parsed.apiBaseUrl;
          // 打開時先重新檢測健康度
          const ok = await testHealthAndConnect(parsed.apiBaseUrl, true);
          if (ok) return;
        }
      } catch {
        localStorage.removeItem('ledger.connection');
      }
    }

    // 尚未連線或檢驗失敗，停留在連線畫面
    switchView('connection');
  }

  // DOM 載入後啟動
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
