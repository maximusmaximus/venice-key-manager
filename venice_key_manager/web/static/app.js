// =============================================================================
// Venice Key Manager - Reactive Client Application
// =============================================================================

const AUTH_TOKEN_KEY = "vkm_auth_token";
let authToken = localStorage.getItem(AUTH_TOKEN_KEY) || "";
let dashboardInitialized = false;

let allKeys = [];
let allModels = [];
let allCategories = ["Default", "Agents", "Production", "Testing", "External"];
let activeCategory = "all";
let globalThreshold = 0.20;
let sseSource = null;

// Authenticated wrapper around fetch
async function authFetch(url, options = {}) {
  options.headers = options.headers || {};
  const currentToken = authToken || localStorage.getItem(AUTH_TOKEN_KEY) || "";

  if (currentToken) {
    if (options.headers instanceof Headers) {
      options.headers.set("Authorization", `Bearer ${currentToken}`);
      options.headers.set("X-Access-Token", currentToken);
    } else {
      options.headers["Authorization"] = `Bearer ${currentToken}`;
      options.headers["X-Access-Token"] = currentToken;
    }
  }

  const res = await fetch(url, options);
  if (res.status === 401) {
    lockDashboard("Session expired or access revoked. Please enter a valid access code.");
    throw new Error("Unauthorized");
  }
  return res;
}

document.addEventListener("DOMContentLoaded", () => {
  initAuth();
});

function initAuth() {
  const form = document.getElementById("auth-gate-form");
  const input = document.getElementById("auth-key-input");
  const btnSubmit = document.getElementById("btn-submit-auth");
  const btnLock = document.getElementById("btn-lock-session");

  // 1. Check if server-side validated token
  if (window.__INITIALLY_AUTH && window.__URL_TOKEN) {
    authToken = window.__URL_TOKEN;
    localStorage.setItem(AUTH_TOKEN_KEY, authToken);
    cleanUrlToken();
    unlockDashboard();
    return;
  }

  // 2. Check if URL has ?token=
  const urlParams = new URLSearchParams(window.location.search);
  const tokenFromUrl = urlParams.get("token");
  if (tokenFromUrl) {
    verifyAndUnlock(tokenFromUrl);
    return;
  }

  // 3. Check localStorage
  const savedToken = localStorage.getItem(AUTH_TOKEN_KEY);
  if (savedToken) {
    verifyAndUnlock(savedToken);
    return;
  }

  // 4. Otherwise, show auth gate
  showAuthGate();

  if (form) {
    form.addEventListener("submit", (e) => {
      e.preventDefault();
      const val = input.value.trim();
      if (!val) {
        showAuthGate("Please enter your authorization access code.");
        return;
      }
      btnSubmit.disabled = true;
      btnSubmit.innerText = "Verifying...";
      verifyAndUnlock(val, () => {
        btnSubmit.disabled = false;
        btnSubmit.innerText = "Unlock 🔓";
      });
    });
  }

  if (btnLock) {
    btnLock.addEventListener("click", () => {
      lockDashboard();
    });
  }
}

async function verifyAndUnlock(token, onComplete) {
  try {
    const res = await fetch("/api/auth/verify", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ token: token.trim() })
    });
    if (!res.ok) {
      throw new Error("Invalid or expired key");
    }
    authToken = token.trim();
    localStorage.setItem(AUTH_TOKEN_KEY, authToken);
    cleanUrlToken();
    unlockDashboard();
    showToast("Dashboard unlocked successfully!", "success");
  } catch (err) {
    localStorage.removeItem(AUTH_TOKEN_KEY);
    authToken = "";
    showAuthGate("Invalid or expired access code. Please request a new code from admin.");
  } finally {
    if (onComplete) onComplete();
  }
}

function unlockDashboard() {
  const gate = document.getElementById("auth-gate-screen");
  const main = document.getElementById("app-main-content");
  if (gate) gate.classList.add("hidden");
  if (main) main.classList.remove("hidden");

  // Update backup download link if present
  const backupDownloadBtn = document.querySelector('a[href^="/api/backup/export"]');
  if (backupDownloadBtn && authToken) {
    backupDownloadBtn.href = `/api/backup/export?token=${encodeURIComponent(authToken)}`;
  }

  initDashboardUI();
}

function showAuthGate(errorMsg) {
  if (sseSource) {
    sseSource.close();
    sseSource = null;
  }
  const gate = document.getElementById("auth-gate-screen");
  const main = document.getElementById("app-main-content");
  if (gate) gate.classList.remove("hidden");
  if (main) main.classList.add("hidden");

  const errBanner = document.getElementById("auth-error-banner");
  const errText = document.getElementById("auth-error-text");
  if (errorMsg && errBanner && errText) {
    errBanner.classList.remove("hidden");
    errText.innerText = errorMsg;
  } else if (errBanner) {
    errBanner.classList.add("hidden");
  }
}

function lockDashboard(msg) {
  authToken = "";
  localStorage.removeItem(AUTH_TOKEN_KEY);
  fetch("/api/auth/logout", { method: "POST" }).catch(() => {});
  showAuthGate(msg || "");
  showToast(msg || "Dashboard session locked.", "info");
}

function cleanUrlToken() {
  if (window.location.search.includes("token=")) {
    const cleanUrl = window.location.pathname;
    window.history.replaceState({}, document.title, cleanUrl);
  }
}

function initDashboardUI() {
  if (!dashboardInitialized) {
    initTabs();
    initCopyButtons();
    initModals();
    initPresets();
    initPlayground();
    initBackup();
    initSettings();
    initReport();
    initAllocations();

    // Refresh button
    const btnRefresh = document.getElementById("btn-refresh");
    if (btnRefresh) {
      btnRefresh.addEventListener("click", () => {
        loadBalance();
        loadKeys();
        loadModels();
        loadProjects();
        loadExternalKeys();
        loadGatewayInfo();
        showToast("Data refreshed from Venice cloud", "info");
      });
    }

    // Search and filter listeners
    const keySearch = document.getElementById("key-search-input");
    if (keySearch) keySearch.addEventListener("input", renderKeysTable);

    const toggleLow = document.getElementById("toggle-low-balance-only");
    if (toggleLow) toggleLow.addEventListener("change", renderKeysTable);

    const modelSearch = document.getElementById("model-search-input");
    if (modelSearch) modelSearch.addEventListener("input", renderModelsTable);

    const btnCloseBanner = document.getElementById("btn-close-banner");
    if (btnCloseBanner) {
      btnCloseBanner.addEventListener("click", () => {
        document.getElementById("low-balance-banner").classList.add("hidden");
      });
    }

    dashboardInitialized = true;
  }

  // Always reload fresh data
  loadBalance();
  loadCategories();
  loadKeys();
  loadModels();
  loadProjects();
  loadExternalKeys();
  loadGatewayInfo();
  initSSE();
}

// =============================================================================
// TOAST NOTIFICATIONS
// =============================================================================
function showToast(message, type = "info") {
  const container = document.getElementById("toast-container");
  const toast = document.createElement("div");
  toast.className = `toast toast-${type}`;
  toast.innerHTML = `<span>${type === 'success' ? '✅' : type === 'error' ? '❌' : 'ℹ️'}</span> <span>${message}</span>`;
  container.appendChild(toast);
  setTimeout(() => {
    toast.style.opacity = "0";
    setTimeout(() => toast.remove(), 300);
  }, 3500);
}

// =============================================================================
// COPY TO CLIPBOARD ENGINE
// =============================================================================
function initCopyButtons() {
  document.addEventListener("click", (e) => {
    const btn = e.target.closest(".copy-btn");
    if (!btn) return;

    let textToCopy = "";
    const targetId = btn.dataset.copyTarget;
    if (targetId) {
      const targetEl = document.getElementById(targetId);
      if (targetEl) {
        textToCopy = targetEl.value !== undefined ? targetEl.value : targetEl.innerText;
      }
    } else if (btn.dataset.copyText) {
      textToCopy = btn.dataset.copyText;
    }

    if (!textToCopy) return;

    navigator.clipboard.writeText(textToCopy).then(() => {
      const origText = btn.innerHTML;
      btn.innerHTML = "✅ Copied!";
      btn.classList.add("copied");
      setTimeout(() => {
        btn.innerHTML = origText;
        btn.classList.remove("copied");
      }, 1500);
      showToast("Copied to clipboard", "success");
    }).catch(err => {
      showToast("Clipboard copy failed: " + err, "error");
    });
  });
}

// =============================================================================
// NAVIGATION TABS
// =============================================================================
function initTabs() {
  const tabs = document.querySelectorAll(".nav-tab");
  tabs.forEach(tab => {
    tab.addEventListener("click", () => {
      tabs.forEach(t => t.classList.remove("active"));
      document.querySelectorAll(".tab-pane").forEach(p => p.classList.remove("active"));
      tab.classList.add("active");
      const target = document.getElementById(tab.dataset.tab);
      if (target) target.classList.add("active");
    });
  });
}

// =============================================================================
// REAL-TIME BALANCE & STATUS
// =============================================================================
async function loadBalance() {
  try {
    const res = await authFetch("/api/balance");
    if (!res.ok) throw new Error("Failed to load balance");
    const data = await res.json();
    updateBalanceUI(data);
  } catch (err) {
    document.getElementById("system-status-dot").className = "status-dot danger";
    document.getElementById("system-status-text").innerText = "Connection Error";
  }
}

function updateBalanceUI(data) {
  const usd = data.balances?.USD ?? 0.0;
  const diem = data.balances?.DIEM ?? 0.0;
  globalThreshold = data.global_threshold ?? 0.20;

  document.getElementById("val-balance-usd").innerText = `$${usd.toFixed(4)}`;
  document.getElementById("val-balance-diem").innerText = `${diem.toFixed(4)} DIEM`;

  const banner = document.getElementById("low-balance-banner");
  const bannerText = document.getElementById("banner-text");
  const kpiBal = document.getElementById("kpi-balance");

  if (usd <= globalThreshold) {
    banner.classList.remove("hidden");
    bannerText.innerHTML = `<strong>⚠️ Low USD Balance Alert:</strong> Account balance is <strong>$${usd.toFixed(4)}</strong> (under threshold $${globalThreshold.toFixed(2)}).`;
    kpiBal.classList.add("low-balance");
  } else {
    banner.classList.add("hidden");
    kpiBal.classList.remove("low-balance");
  }

  // Next epoch countdown
  if (data.next_epoch_begins) {
    updateEpochCountdown(data.next_epoch_begins);
  }

  document.getElementById("system-status-dot").className = data.access_permitted ? "status-dot active" : "status-dot warning";
  document.getElementById("system-status-text").innerText = data.access_permitted ? "Venice Online" : "Access Limited";
}

function updateEpochCountdown(isoStr) {
  const target = new Date(isoStr).getTime();
  const now = Date.now();
  const diff = target - now;

  if (diff <= 0) {
    document.getElementById("val-epoch-countdown").innerText = "00:00:00";
    return;
  }

  const hours = Math.floor(diff / (1000 * 60 * 60));
  const minutes = Math.floor((diff % (1000 * 60 * 60)) / (1000 * 60));
  const seconds = Math.floor((diff % (1000 * 60)) / 1000);

  document.getElementById("val-epoch-countdown").innerText =
    `${String(hours).padStart(2, '0')}:${String(minutes).padStart(2, '0')}:${String(seconds).padStart(2, '0')}`;
}

// =============================================================================
// SERVER-SENT EVENTS (SSE) STREAM
// =============================================================================
function initSSE() {
  if (sseSource) sseSource.close();
  try {
    const sseUrl = "/api/sse/stats?token=" + encodeURIComponent(authToken);
    sseSource = new EventSource(sseUrl);
    sseSource.onmessage = (event) => {
      try {
        const data = jsonParseSafe(event.data);
        if (data && !data.error) {
          document.getElementById("val-balance-usd").innerText = `$${Number(data.balance_usd).toFixed(4)}`;
          document.getElementById("val-period-spend").innerText = `$${Number(data.total_current_spend).toFixed(4)}`;
          document.getElementById("val-keys-count").innerText = data.total_keys;
          document.getElementById("val-low-keys-count").innerText = `${data.low_keys_count} low balance`;

          if (data.next_epoch_begins) {
            updateEpochCountdown(data.next_epoch_begins);
          }

          const banner = document.getElementById("low-balance-banner");
          if (data.is_low_balance) {
            banner.classList.remove("hidden");
          }
        }
      } catch (e) {}
    };
    sseSource.onerror = () => {
      sseSource.close();
      setTimeout(initSSE, 10000);
    };
  } catch (e) {}
}

// =============================================================================
// CATEGORIES
// =============================================================================
async function loadCategories() {
  try {
    const res = await authFetch("/api/categories");
    const data = await res.json();
    allCategories = data.categories || [];
    renderCategoryPills();
    populateCategoryDropdowns();
  } catch (err) {}
}

function renderCategoryPills() {
  const container = document.getElementById("category-pills-container");
  container.innerHTML = "";

  const allPill = document.createElement("button");
  allPill.className = `pill ${activeCategory === "all" ? "active" : ""}`;
  allPill.innerText = "All Keys";
  allPill.dataset.cat = "all";
  allPill.onclick = () => selectCategory("all");
  container.appendChild(allPill);

  allCategories.forEach(cat => {
    const pill = document.createElement("button");
    pill.className = `pill ${activeCategory.toLowerCase() === cat.toLowerCase() ? "active" : ""}`;
    pill.innerText = cat;
    pill.dataset.cat = cat;
    pill.onclick = () => selectCategory(cat);
    container.appendChild(pill);
  });
}

function selectCategory(cat) {
  activeCategory = cat;
  renderCategoryPills();
  renderKeysTable();
}

function populateCategoryDropdowns() {
  const selects = [
    document.getElementById("create-category"),
    document.getElementById("edit-key-category")
  ];
  selects.forEach(sel => {
    if (!sel) return;
    const current = sel.value;
    sel.innerHTML = "";
    allCategories.forEach(cat => {
      const opt = document.createElement("option");
      opt.value = cat;
      opt.innerText = cat;
      sel.appendChild(opt);
    });
    if (current && allCategories.includes(current)) sel.value = current;
  });
}

// =============================================================================
// API KEYS CRUD & TABLE RENDERING
// =============================================================================
async function loadKeys() {
  try {
    const res = await authFetch("/api/keys");
    if (!res.ok) throw new Error("Failed to load keys");
    allKeys = await res.json();

    document.getElementById("val-keys-count").innerText = allKeys.length;
    document.getElementById("tab-keys-count").innerText = allKeys.length;

    const lowCount = allKeys.filter(k => k.is_low_balance).length;
    document.getElementById("val-low-keys-count").innerText = `${lowCount} low balance`;

    const totalSpend = allKeys.reduce((acc, k) => acc + parseFloat(k.currentPeriodUsage?.usd || 0), 0);
    document.getElementById("val-period-spend").innerText = `$${totalSpend.toFixed(4)}`;

    renderKeysTable();
    populatePlaygroundKeys();
  } catch (err) {
    document.getElementById("keys-tbody").innerHTML = `<tr><td colspan="9" class="text-center py-8 text-muted">Error loading keys: ${err.message}</td></tr>`;
  }
}

function renderKeysTable() {
  const tbody = document.getElementById("keys-tbody");
  const searchQuery = document.getElementById("key-search-input").value.toLowerCase().trim();
  const lowOnly = document.getElementById("toggle-low-balance-only").checked;

  let filtered = allKeys.filter(k => {
    if (activeCategory !== "all" && k.category.toLowerCase() !== activeCategory.toLowerCase()) {
      return false;
    }
    if (lowOnly && !k.is_low_balance) {
      return false;
    }
    if (searchQuery) {
      const matchDesc = (k.description || "").toLowerCase().includes(searchQuery);
      const matchId = (k.id || "").toLowerCase().includes(searchQuery);
      const matchLast6 = (k.last6Chars || "").toLowerCase().includes(searchQuery);
      const matchCat = (k.category || "").toLowerCase().includes(searchQuery);
      return matchDesc || matchId || matchLast6 || matchCat;
    }
    return true;
  });

  if (filtered.length === 0) {
    tbody.innerHTML = `<tr><td colspan="9" class="text-center py-8 text-muted">No API keys match current filters.</td></tr>`;
    return;
  }

  tbody.innerHTML = filtered.map(k => {
    const isLow = k.is_low_balance;
    const limitUSD = k.consumptionLimits?.usd !== undefined && k.consumptionLimits?.usd !== null
      ? `$${Number(k.consumptionLimits.usd).toFixed(2)}`
      : `<span class="text-dim">Unlimited</span>`;

    const spentUSD = parseFloat(k.currentPeriodUsage?.usd || 0).toFixed(4);
    const trailUSD = parseFloat(k.usage?.trailingSevenDays?.usd || 0).toFixed(4);

    const remainingCol = k.remaining_usd !== null && k.remaining_usd !== undefined
      ? `<span class="${isLow ? 'text-warning font-bold' : ''}">$${k.remaining_usd.toFixed(4)} ${isLow ? '⚠️' : ''}</span>`
      : `<span class="text-dim">--</span>`;

    const last6 = k.last6Chars || "••••••";

    return `
      <tr class="${isLow ? 'row-low-balance' : ''}">
        <td>
          <div class="font-semibold text-white">${escapeHtml(k.description || 'Unnamed Key')}</div>
          <div class="text-xs text-dim">${formatDate(k.createdAt)}</div>
        </td>
        <td>
          <span class="chip chip-category">${escapeHtml(k.category || 'Default')}</span>
        </td>
        <td>
          <span class="chip chip-type">${k.apiKeyType}</span>
        </td>
        <td>
          <div class="key-id-cell">
            <span class="mono text-xs">...${escapeHtml(last6)}</span>
            <button class="btn btn-xs btn-outline copy-btn" data-copy-text="${escapeHtml(k.id)}" title="Copy Full Key ID">📋 ID</button>
          </div>
        </td>
        <td>
          <span class="mono">${limitUSD}</span>
          <span class="text-xs text-dim">/ ${k.limitPeriod || 'EPOCH'}</span>
        </td>
        <td><span class="mono">$${spentUSD}</span></td>
        <td><span class="mono">${remainingCol}</span></td>
        <td><span class="mono text-dim">$${trailUSD}</span></td>
        <td>
          <div style="display:flex; gap:6px;">
            <button class="btn btn-xs btn-outline" onclick="openCycleModal('${k.id}')" title="Rotate / Cycle Key">🔄 Cycle</button>
            <button class="btn btn-xs btn-outline" onclick="openEditModal('${k.id}')" title="Edit Budget & Category">✏️ Edit</button>
            <button class="btn btn-xs btn-danger" onclick="revokeKey('${k.id}')" title="Revoke Key">❌</button>
          </div>
        </td>
      </tr>
    `;
  }).join("");
}

// =============================================================================
// MODALS LOGIC
// =============================================================================
function initModals() {
  document.querySelectorAll("[data-close-modal]").forEach(btn => {
    btn.addEventListener("click", () => {
      const modalId = btn.dataset.closeModal;
      document.getElementById(modalId).classList.add("hidden");
    });
  });

  document.getElementById("btn-open-create-modal").addEventListener("click", () => {
    document.getElementById("create-desc").value = "";
    document.getElementById("create-limit-usd").value = "0.50";
    document.getElementById("create-custom-threshold").value = "";
    const bCountInput = document.getElementById("create-batch-count");
    if (bCountInput) bCountInput.value = "1";
    document.getElementById("modal-create-key").classList.remove("hidden");
  });

  document.getElementById("btn-submit-create-key").addEventListener("click", handleCreateKey);
  document.getElementById("btn-submit-cycle-key").addEventListener("click", handleCycleKey);
  document.getElementById("btn-submit-edit-key").addEventListener("click", handleEditKey);

  // Batch Codes & Keys Modal Wiring
  const btnOpenBatch = document.getElementById("btn-open-batch-codes");
  if (btnOpenBatch) {
    btnOpenBatch.addEventListener("click", () => {
      document.getElementById("modal-batch-codes").classList.remove("hidden");
      loadActiveTokens();
    });
  }

  const tabCodesBtn = document.getElementById("batch-tab-codes-btn");
  const tabKeysBtn = document.getElementById("batch-tab-keys-btn");
  const tabCodesContent = document.getElementById("batch-tab-codes-content");
  const tabKeysContent = document.getElementById("batch-tab-keys-content");

  if (tabCodesBtn && tabKeysBtn) {
    tabCodesBtn.addEventListener("click", () => {
      tabCodesBtn.classList.add("active");
      tabKeysBtn.classList.remove("active");
      tabCodesContent.classList.remove("hidden");
      tabKeysContent.classList.add("hidden");
    });
    tabKeysBtn.addEventListener("click", () => {
      tabKeysBtn.classList.add("active");
      tabCodesBtn.classList.remove("active");
      tabKeysContent.classList.remove("hidden");
      tabCodesContent.classList.add("hidden");
    });
  }

  const btnSubmitBatchCodes = document.getElementById("btn-submit-batch-codes");
  if (btnSubmitBatchCodes) {
    btnSubmitBatchCodes.addEventListener("click", handleBatchCodesGenerate);
  }

  const btnSubmitBatchKeys = document.getElementById("btn-submit-batch-keys");
  if (btnSubmitBatchKeys) {
    btnSubmitBatchKeys.addEventListener("click", handleBatchKeysMint);
  }

  const btnRefreshActiveCodes = document.getElementById("btn-refresh-active-codes");
  if (btnRefreshActiveCodes) {
    btnRefreshActiveCodes.addEventListener("click", loadActiveTokens);
  }

  // Copy and Export buttons for Batch Codes
  const btnCopyCodes = document.getElementById("btn-copy-all-batch-codes");
  if (btnCopyCodes) {
    btnCopyCodes.addEventListener("click", () => {
      if (!currentBatchCodes || !currentBatchCodes.length) return;
      const text = currentBatchCodes.map(c => c.token).join("\n");
      navigator.clipboard.writeText(text);
      showToast(`Copied ${currentBatchCodes.length} codes to clipboard!`, "success");
    });
  }

  const btnCopyLinks = document.getElementById("btn-copy-all-batch-links");
  if (btnCopyLinks) {
    btnCopyLinks.addEventListener("click", () => {
      if (!currentBatchCodes || !currentBatchCodes.length) return;
      const text = currentBatchCodes.map(c => c.magic_url).join("\n");
      navigator.clipboard.writeText(text);
      showToast(`Copied ${currentBatchCodes.length} magic links to clipboard!`, "success");
    });
  }

  const btnDownloadCsv = document.getElementById("btn-download-batch-csv");
  if (btnDownloadCsv) {
    btnDownloadCsv.addEventListener("click", () => {
      if (!currentBatchCodes || !currentBatchCodes.length) return;
      let csv = "Token,Prefix,ExpiresAt,MagicUrl\n";
      currentBatchCodes.forEach(c => {
        csv += `"${c.token}","${c.prefix || ''}","${c.expires_at || ''}","${c.magic_url || ''}"\n`;
      });
      const blob = new Blob([csv], { type: "text/csv;charset=utf-8;" });
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `venice-batch-codes-${Date.now()}.csv`;
      a.click();
      URL.revokeObjectURL(url);
      showToast("Batch codes CSV downloaded!", "success");
    });
  }

  const btnCopyKeys = document.getElementById("btn-copy-all-batch-keys");
  if (btnCopyKeys) {
    btnCopyKeys.addEventListener("click", () => {
      if (!currentBatchKeys || !currentBatchKeys.length) return;
      const text = currentBatchKeys.map(k => `${k.description}: ${k.apiKey}`).join("\n");
      navigator.clipboard.writeText(text);
      showToast(`Copied ${currentBatchKeys.length} API keys to clipboard!`, "success");
    });
  }

  const btnCopyEnv = document.getElementById("btn-copy-batch-keys-env");
  if (btnCopyEnv) {
    btnCopyEnv.addEventListener("click", () => {
      if (!currentBatchKeys || !currentBatchKeys.length) return;
      const text = currentBatchKeys.map((k, i) => `export VENICE_KEY_${i + 1}="${k.apiKey}" # ${k.description}`).join("\n");
      navigator.clipboard.writeText(text);
      showToast("Copied .env format to clipboard!", "success");
    });
  }

  const btnCopyBatchBundle = document.getElementById("btn-copy-batch-keys-bundle");
  if (btnCopyBatchBundle) {
    btnCopyBatchBundle.addEventListener("click", () => {
      if (!currentBatchKeys || !currentBatchKeys.length) return;
      const tsUrl = currentBatchEndpoints?.tailscale_v1_url || "http://100.99.202.75:8660/v1";
      const cfUrl = currentBatchEndpoints?.cloudflare_v1_url || "https://worship-him-knight-jul.trycloudflare.com/v1";
      let bundle = `📦 VENICE BATCH KEYS ALLOCATION (${currentBatchKeys.length} Keys)\n`;
      bundle += `🌐 Tailscale Address (Internal): ${tsUrl}\n`;
      bundle += `☁️ Cloudflare DNS URL (External): ${cfUrl}\n\n`;
      bundle += `KEYS:\n`;
      currentBatchKeys.forEach((k, i) => {
        bundle += `${i + 1}. ${k.description}: ${k.apiKey}\n`;
      });
      navigator.clipboard.writeText(bundle);
      showToast("Copied full batch bundle with endpoints!", "success");
    });
  }

  const btnCopyKeyBundle = document.getElementById("btn-copy-revealed-key-bundle");
  if (btnCopyKeyBundle) {
    btnCopyKeyBundle.addEventListener("click", () => {
      if (!currentRevealedKeyBundle) return;
      const b = currentRevealedKeyBundle;
      const text = `🔑 VENICE API KEY: ${b.name}\n` +
        `Token: ${b.token}\n` +
        `ID: ${b.id}\n\n` +
        `🌐 CONNECTION ENDPOINTS FOR RECEIVING AGENTS:\n` +
        `• Tailscale Address (Internal Agents): ${b.tailscale_address}\n` +
        `• Cloudflare DNS URL (External Agents): ${b.cloudflare_dns_url}\n\n` +
        `cURL Fast Test Snippet:\n` +
        `curl -X POST ${b.cloudflare_dns_url}/chat/completions \\\n` +
        `  -H "Authorization: Bearer ${b.token}" \\\n` +
        `  -H "Content-Type: application/json" \\\n` +
        `  -d '{"model": "deepseek-v4-flash", "messages": [{"role": "user", "content": "Hello Venice!"}]}'`;
      navigator.clipboard.writeText(text);
      showToast("Full agent bundle copied to clipboard!", "success");
    });
  }

  const btnCopyExtBundle = document.getElementById("btn-copy-revealed-ext-bundle");
  if (btnCopyExtBundle) {
    btnCopyExtBundle.addEventListener("click", () => {
      if (!currentRevealedExtBundle) return;
      const b = currentRevealedExtBundle;
      const text = `🔑 VENICE INFERENCE KEY: ${b.name}\n` +
        `Token: ${b.token}\n` +
        `Project: ${b.project_name}\n` +
        `Allocation: $${b.daily_limit_usd} / day (Max Tier: ${b.max_tier.toUpperCase()})\n\n` +
        `🌐 CONNECTION ENDPOINTS FOR RECEIVING AGENTS:\n` +
        `• Tailscale Address (Internal Agents): ${b.tailscale_address}\n` +
        `• Cloudflare DNS URL (External Agents): ${b.cloudflare_dns_url}\n\n` +
        `cURL Snippet:\n` +
        `curl -X POST ${b.cloudflare_dns_url}/chat/completions \\\n` +
        `  -H "Authorization: Bearer ${b.token}" \\\n` +
        `  -H "Content-Type: application/json" \\\n` +
        `  -d '{"model": "${b.max_tier}", "messages": [{"role": "user", "content": "Hello!"}]}'\n\n` +
        `Python Snippet:\n` +
        `from openai import OpenAI\n` +
        `client = OpenAI(base_url="${b.cloudflare_dns_url}", api_key="${b.token}")\n` +
        `res = client.chat.completions.create(model="${b.max_tier}", messages=[{"role": "user", "content": "Hello!"}])\n` +
        `print(res.choices[0].message.content)`;
      navigator.clipboard.writeText(text);
      showToast("Full agent bundle copied to clipboard!", "success");
    });
  }
}

function initPresets() {
  const presetBtns = document.querySelectorAll(".preset-btn");
  presetBtns.forEach(btn => {
    btn.addEventListener("click", () => {
      presetBtns.forEach(b => b.classList.remove("active"));
      btn.classList.add("active");
      document.getElementById("create-limit-usd").value = btn.dataset.val;
    });
  });
}

// Global batch and revealed bundles state
let currentBatchCodes = [];
let currentBatchKeys = [];
let currentBatchEndpoints = null;
let currentRevealedKeyBundle = null;
let currentRevealedExtBundle = null;

async function handleCreateKey() {
  const desc = document.getElementById("create-desc").value.trim();
  if (!desc) {
    showToast("Please enter a description or agent name prefix", "error");
    return;
  }

  const category = document.getElementById("create-category").value;
  const period = document.querySelector('input[name="create-period"]:checked').value;
  const limitVal = document.getElementById("create-limit-usd").value;
  const customThreshVal = document.getElementById("create-custom-threshold").value;
  const keyType = document.getElementById("create-type").value;
  const batchCount = parseInt(document.getElementById("create-batch-count")?.value || "1", 10);

  // If user entered batch count > 1, execute batch creation
  if (batchCount > 1) {
    const batchPayload = {
      prefix: desc,
      count: batchCount,
      daily_usd: limitVal !== "" ? parseFloat(limitVal) : null,
      limitPeriod: period,
      category: category,
      apiKeyType: keyType,
      custom_threshold: customThreshVal !== "" ? parseFloat(customThreshVal) : null
    };

    try {
      const res = await authFetch("/api/keys/batch", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(batchPayload)
      });
      if (!res.ok) {
        const err = await res.json();
        throw new Error(err.detail || "Batch key creation failed");
      }
      const data = await res.json();
      document.getElementById("modal-create-key").classList.add("hidden");

      // Show revealed modal with first key or summary
      const first = data.keys[0];
      const tsUrl = data.endpoints?.tailscale_v1_url || "http://100.99.202.75:8660/v1";
      const cfUrl = data.endpoints?.cloudflare_v1_url || "https://worship-him-knight-jul.trycloudflare.com/v1";

      document.getElementById("revealed-key-token").value = first.apiKey;
      document.getElementById("revealed-key-id").value = `${data.count} Keys Minted (e.g. ${first.id})`;

      const tsInput = document.getElementById("revealed-key-tailscale");
      if (tsInput) tsInput.value = tsUrl;
      const cfInput = document.getElementById("revealed-key-cloudflare");
      if (cfInput) cfInput.value = cfUrl;

      currentRevealedKeyBundle = {
        name: `${desc} (Batch of ${data.count})`,
        token: first.apiKey,
        id: first.id,
        tailscale_address: tsUrl,
        cloudflare_dns_url: cfUrl,
      };

      const envSnippet = data.keys.map((k, i) => `export VENICE_KEY_${i + 1}="${k.apiKey}" # ${k.description}`).join("\n");
      document.getElementById("revealed-curl").innerText = envSnippet;
      document.getElementById("modal-key-revealed").classList.remove("hidden");

      showToast(`Batch of ${data.count} keys minted successfully!`, "success");
      loadKeys();
    } catch (err) {
      showToast(err.message, "error");
    }
    return;
  }

  const payload = {
    description: desc,
    apiKeyType: keyType,
    limitPeriod: period,
    category: category,
    daily_usd: limitVal !== "" ? parseFloat(limitVal) : null,
    custom_threshold: customThreshVal !== "" ? parseFloat(customThreshVal) : null
  };

  try {
    const res = await authFetch("/api/keys", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload)
    });
    if (!res.ok) {
      const err = await res.json();
      throw new Error(err.detail || "Key creation failed");
    }
    const data = await res.json();

    document.getElementById("modal-create-key").classList.add("hidden");

    // Populate revealed token modal
    document.getElementById("revealed-key-token").value = data.apiKey;
    document.getElementById("revealed-key-id").value = data.id;

    const tsUrl = data.endpoints?.tailscale_v1_url || "http://100.99.202.75:8660/v1";
    const cfUrl = data.endpoints?.cloudflare_v1_url || "https://worship-him-knight-jul.trycloudflare.com/v1";

    const tsInput = document.getElementById("revealed-key-tailscale");
    if (tsInput) tsInput.value = tsUrl;
    const cfInput = document.getElementById("revealed-key-cloudflare");
    if (cfInput) cfInput.value = cfUrl;

    currentRevealedKeyBundle = {
      name: desc,
      token: data.apiKey,
      id: data.id,
      tailscale_address: tsUrl,
      cloudflare_dns_url: cfUrl,
    };

    const curlSnippet = `# 1. Direct Venice API:\ncurl -X POST https://api.venice.ai/api/v1/chat/completions \\\n  -H "Authorization: Bearer ${data.apiKey}" \\\n  -H "Content-Type: application/json" \\\n  -d '{"model": "deepseek-v4-flash", "messages": [{"role": "user", "content": "Hello Venice"}]}'\n\n# 2. Local Mesh Gateway (Tailscale):\ncurl -X POST ${tsUrl}/chat/completions \\\n  -H "Authorization: Bearer ${data.apiKey}" \\\n  -H "Content-Type: application/json" \\\n  -d '{"model": "deepseek-v4-flash", "messages": [{"role": "user", "content": "Hello!"}]}'\n\n# 3. External Gateway (Cloudflare DNS):\ncurl -X POST ${cfUrl}/chat/completions \\\n  -H "Authorization: Bearer ${data.apiKey}" \\\n  -H "Content-Type: application/json" \\\n  -d '{"model": "deepseek-v4-flash", "messages": [{"role": "user", "content": "Hello!"}]}'`;
    document.getElementById("revealed-curl").innerText = curlSnippet;

    document.getElementById("modal-key-revealed").classList.remove("hidden");

    showToast(`Key "${desc}" minted successfully!`, "success");
    loadKeys();
  } catch (err) {
    showToast(err.message, "error");
  }
}

// -----------------------------------------------------------------------------
// Batch Access Codes & Batch Keys Helpers
// -----------------------------------------------------------------------------
async function handleBatchCodesGenerate() {
  const pfx = document.getElementById("batch-code-prefix").value.trim() || "vkm_code_";
  const cnt = parseInt(document.getElementById("batch-code-count").value || "5", 10);
  const ttl = parseInt(document.getElementById("batch-code-ttl").value || "168", 10);
  const notes = document.getElementById("batch-code-notes").value.trim();

  const btn = document.getElementById("btn-submit-batch-codes");
  btn.disabled = true;
  btn.innerText = "Generating Codes...";

  try {
    const res = await authFetch("/api/auth/tokens/batch", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        prefix: pfx,
        count: cnt,
        ttl_hours: ttl,
        notes: notes
      })
    });
    if (!res.ok) {
      const err = await res.json();
      throw new Error(err.detail || "Failed to generate batch codes");
    }
    const data = await res.json();
    currentBatchCodes = data.tokens || [];

    // Render generated codes
    const resultsBox = document.getElementById("batch-code-results-box");
    const outList = document.getElementById("batch-codes-output-list");
    resultsBox.classList.remove("hidden");

    outList.innerHTML = currentBatchCodes.map((item, idx) => `
      <div class="batch-item-row" style="padding:6px 0; border-bottom:1px solid rgba(255,255,255,0.06); display:flex; justify-content:space-between; align-items:center;">
        <div>
          <span class="text-accent font-bold">#${idx + 1}</span>
          <span class="mono text-white ml-2">${escapeHtml(item.token)}</span>
          <div class="text-xs text-dim">
            <a href="${escapeHtml(item.magic_url)}" target="_blank" class="text-accent">🔗 Magic Link</a>
            · Expires: ${formatDate(item.expires_at)}
          </div>
        </div>
        <button class="btn btn-xs btn-outline copy-btn" data-copy-text="${escapeHtml(item.token)}">📋 Copy</button>
      </div>
    `).join("");

    showToast(`Successfully created ${data.count} codes with prefix "${pfx}"!`, "success");
    loadActiveTokens();
  } catch (err) {
    showToast(err.message, "error");
  } finally {
    btn.disabled = false;
    btn.innerText = "🎟️ Generate Batch Codes";
  }
}

async function loadActiveTokens() {
  const tbody = document.getElementById("tbody-active-tokens");
  if (!tbody) return;

  try {
    const res = await authFetch("/api/auth/tokens");
    if (!res.ok) throw new Error("Failed to load active tokens");
    const tokens = await res.json();

    if (!tokens.length) {
      tbody.innerHTML = `<tr><td colspan="5" class="text-center text-dim py-3">No active access codes found.</td></tr>`;
      return;
    }

    tbody.innerHTML = tokens.map(t => `
      <tr>
        <td><span class="chip chip-category text-xs">${escapeHtml(t.prefix || 'custom')}</span></td>
        <td>
          <div class="mono text-xs text-white">${escapeHtml(t.token)}</div>
          <div class="text-xs text-dim"><a href="${escapeHtml(t.magic_url)}" target="_blank" class="text-accent">Open Link</a></div>
        </td>
        <td class="text-xs text-dim">${formatDate(t.expires_at)}</td>
        <td class="text-xs text-dim">${escapeHtml(t.notes || t.created_by || '--')}</td>
        <td>
          <button class="btn btn-xs btn-danger" onclick="revokeAuthToken('${escapeHtml(t.token)}')">Revoke</button>
        </td>
      </tr>
    `).join("");
  } catch (err) {
    tbody.innerHTML = `<tr><td colspan="5" class="text-center text-warning py-3">Failed to load codes: ${escapeHtml(err.message)}</td></tr>`;
  }
}

async function revokeAuthToken(tokenStr) {
  if (!confirm(`Revoke access code "${tokenStr}"? Devices using this code will be locked out.`)) return;
  try {
    const res = await authFetch(`/api/auth/tokens/${encodeURIComponent(tokenStr)}`, {
      method: "DELETE"
    });
    if (!res.ok) throw new Error("Failed to revoke token");
    showToast("Access code revoked successfully", "success");
    loadActiveTokens();
  } catch (err) {
    showToast(err.message, "error");
  }
}

async function handleBatchKeysMint() {
  const pfx = document.getElementById("batch-key-prefix").value.trim() || "agent-";
  const cnt = parseInt(document.getElementById("batch-key-count").value || "3", 10);
  const cat = document.getElementById("batch-key-category").value;
  const limitVal = document.getElementById("batch-key-limit").value;
  const period = document.getElementById("batch-key-period").value;

  const btn = document.getElementById("btn-submit-batch-keys");
  btn.disabled = true;
  btn.innerText = "Minting Keys on Venice...";

  try {
    const res = await authFetch("/api/keys/batch", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        prefix: pfx,
        count: cnt,
        category: cat,
        daily_usd: limitVal !== "" ? parseFloat(limitVal) : 0.50,
        limitPeriod: period,
        apiKeyType: "INFERENCE"
      })
    });
    if (!res.ok) {
      const err = await res.json();
      throw new Error(err.detail || "Failed to mint batch keys");
    }
    const data = await res.json();
    currentBatchKeys = data.keys || [];
    currentBatchEndpoints = data.endpoints || null;

    const resultsBox = document.getElementById("batch-key-results-box");
    const outList = document.getElementById("batch-keys-output-list");
    const epBox = document.getElementById("batch-key-endpoints-info");
    resultsBox.classList.remove("hidden");

    if (epBox && currentBatchEndpoints) {
      epBox.innerHTML = `
        <div class="mb-1 text-accent font-semibold">🌐 Connection Endpoints for Receiving Agents:</div>
        <div><strong>• Tailscale (Internal Mesh):</strong> <span class="mono text-white">${escapeHtml(currentBatchEndpoints.tailscale_v1_url || '')}</span></div>
        <div><strong>• Cloudflare DNS (External):</strong> <span class="mono text-white">${escapeHtml(currentBatchEndpoints.cloudflare_v1_url || '')}</span></div>
      `;
    }

    outList.innerHTML = currentBatchKeys.map((k, idx) => `
      <div class="batch-item-row" style="padding:6px 0; border-bottom:1px solid rgba(255,255,255,0.06); display:flex; justify-content:space-between; align-items:center;">
        <div>
          <span class="text-accent font-bold">#${idx + 1}</span>
          <span class="font-semibold text-white ml-2">${escapeHtml(k.description)}</span>
          <span class="text-xs text-dim ml-2">(ID: ${k.id})</span>
          <div class="mono text-xs text-white mt-1">${escapeHtml(k.apiKey)}</div>
        </div>
        <button class="btn btn-xs btn-outline copy-btn" data-copy-text="${escapeHtml(k.apiKey)}">📋 Copy</button>
      </div>
    `).join("");

    showToast(`Successfully minted ${data.count} Venice API keys!`, "success");
    loadKeys();
  } catch (err) {
    showToast(err.message, "error");
  } finally {
    btn.disabled = false;
    btn.innerText = "🔑 Mint Batch Keys";
  }
}

// Cycle Key Modal
function openCycleModal(keyId) {
  const key = allKeys.find(k => k.id === keyId);
  if (!key) return;

  document.getElementById("cycle-key-id").value = key.id;
  document.getElementById("cycle-new-limit").value = "";

  const summary = `
    <div style="font-size:13px;">
      <div><strong>Key:</strong> ${escapeHtml(key.description || 'Unnamed')}</div>
      <div class="text-xs text-dim">ID: ${key.id}</div>
      <div class="mt-2"><strong>Category:</strong> ${key.category} | <strong>Current Limit:</strong> $${key.consumptionLimits?.usd ?? 'Unlimited'} (${key.limitPeriod})</div>
    </div>
  `;
  document.getElementById("cycle-summary-card").innerHTML = summary;
  document.getElementById("modal-cycle-key").classList.remove("hidden");
}

async function handleCycleKey() {
  const keyId = document.getElementById("cycle-key-id").value;
  const newLimit = document.getElementById("cycle-new-limit").value;
  const revokeOld = document.getElementById("cycle-revoke-old").checked;

  const payload = {
    id: keyId,
    revoke_old: revokeOld,
    keep_limits: true,
    new_daily_usd: newLimit !== "" ? parseFloat(newLimit) : null
  };

  try {
    const res = await authFetch(`/api/keys/${keyId}/cycle`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload)
    });
    if (!res.ok) {
      const err = await res.json();
      throw new Error(err.detail || "Key cycling failed");
    }
    const data = await res.json();

    document.getElementById("modal-cycle-key").classList.add("hidden");

    // Show revealed new token modal
    const newK = data.new_key;
    document.getElementById("revealed-key-token").value = newK.apiKey;
    document.getElementById("revealed-key-id").value = newK.id;

    const curlSnippet = `curl -X POST https://api.venice.ai/api/v1/chat/completions \\\n  -H "Authorization: Bearer ${newK.apiKey}" \\\n  -H "Content-Type: application/json" \\\n  -d '{"model": "deepseek-v4-flash", "messages": [{"role": "user", "content": "Hello Venice"}]}'`;
    document.getElementById("revealed-curl").innerText = curlSnippet;

    document.getElementById("modal-key-revealed").classList.remove("hidden");

    showToast("Key rotated successfully!", "success");
    loadKeys();
  } catch (err) {
    showToast(err.message, "error");
  }
}

// Edit Key Modal
function openEditModal(keyId) {
  const key = allKeys.find(k => k.id === keyId);
  if (!key) return;

  document.getElementById("edit-key-id").value = key.id;
  document.getElementById("edit-key-desc").value = key.description || "";
  document.getElementById("edit-key-category").value = key.category || "Default";
  document.getElementById("edit-key-limit").value = key.consumptionLimits?.usd ?? "";
  document.getElementById("edit-key-threshold").value = key.custom_threshold ?? "";
  document.getElementById("edit-key-period").value = key.limitPeriod || "EPOCH";

  document.getElementById("modal-edit-key").classList.remove("hidden");
}

async function handleEditKey() {
  const keyId = document.getElementById("edit-key-id").value;
  const desc = document.getElementById("edit-key-desc").value.trim();
  const category = document.getElementById("edit-key-category").value;
  const limit = document.getElementById("edit-key-limit").value;
  const thresh = document.getElementById("edit-key-threshold").value;
  const period = document.getElementById("edit-key-period").value;

  const payload = {
    id: keyId,
    description: desc,
    category: category,
    limitPeriod: period,
    daily_usd: limit !== "" ? parseFloat(limit) : null,
    custom_threshold: thresh !== "" ? parseFloat(thresh) : null
  };

  try {
    const res = await authFetch(`/api/keys/${keyId}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload)
    });
    if (!res.ok) {
      const err = await res.json();
      throw new Error(err.detail || "Key update failed");
    }
    document.getElementById("modal-edit-key").classList.add("hidden");
    showToast("Key settings updated", "success");
    loadKeys();
  } catch (err) {
    showToast(err.message, "error");
  }
}

// Revoke Key
async function revokeKey(keyId) {
  const key = allKeys.find(k => k.id === keyId);
  const name = key ? key.description : keyId;
  if (!confirm(`Are you sure you want to permanently revoke and delete key "${name}"? This action cannot be undone.`)) {
    return;
  }

  try {
    const res = await authFetch(`/api/keys/${keyId}`, { method: "DELETE" });
    if (!res.ok) throw new Error("Revocation failed");
    showToast(`Key "${name}" revoked`, "success");
    loadKeys();
  } catch (err) {
    showToast(err.message, "error");
  }
}

// =============================================================================
// MODELS CATALOG
// =============================================================================
async function loadModels() {
  try {
    const res = await authFetch("/api/models");
    if (!res.ok) throw new Error("Failed to load models");
    allModels = await res.json();
    document.getElementById("tab-models-count").innerText = allModels.length;
    renderModelsTable();
  } catch (err) {}
}

function renderModelsTable() {
  const tbody = document.getElementById("models-tbody");
  const searchQuery = document.getElementById("model-search-input").value.toLowerCase().trim();
  const activePrivacy = document.querySelector("#model-privacy-pills .pill.active")?.dataset.privacy || "all";

  let filtered = allModels.filter(m => {
    if (activePrivacy !== "all" && m.privacy.toLowerCase() !== activePrivacy.toLowerCase()) {
      return false;
    }
    if (searchQuery) {
      return m.id.toLowerCase().includes(searchQuery) || (m.name || "").toLowerCase().includes(searchQuery);
    }
    return true;
  });

  if (filtered.length === 0) {
    tbody.innerHTML = `<tr><td colspan="7" class="text-center py-8 text-muted">No models match current filter.</td></tr>`;
    return;
  }

  tbody.innerHTML = filtered.map(m => {
    let privacyChip = `<span class="chip">${m.privacy}</span>`;
    if (m.privacy === "e2ee") privacyChip = `<span class="chip chip-e2ee">🔒 E2EE Enclave</span>`;
    else if (m.privacy === "private") privacyChip = `<span class="chip chip-zdr">🛡️ ZDR Private</span>`;

    const inPrice = m.pricing?.input?.usd !== undefined ? `$${m.pricing.input.usd.toFixed(2)}` : "--";
    const outPrice = m.pricing?.output?.usd !== undefined ? `$${m.pricing.output.usd.toFixed(2)}` : "--";

    return `
      <tr>
        <td>
          <div class="font-semibold text-white">${escapeHtml(m.name || m.id)}</div>
          <div class="mono text-xs text-dim">${escapeHtml(m.id)}</div>
        </td>
        <td>${privacyChip}</td>
        <td><span class="mono">${m.context_length ? (m.context_length / 1000).toFixed(0) + 'k' : '--'}</span></td>
        <td><span class="mono">${inPrice}</span></td>
        <td><span class="mono">${outPrice}</span></td>
        <td>
          <div style="display:flex; gap:4px; flex-wrap:wrap;">
            ${m.capabilities?.supportsVision ? '<span class="chip">Vision</span>' : ''}
            ${m.capabilities?.supportsFunctionCalling ? '<span class="chip">Tools</span>' : ''}
            ${m.capabilities?.supportsReasoning ? '<span class="chip">Thinking</span>' : ''}
          </div>
        </td>
        <td>
          <button class="btn btn-xs btn-outline" onclick="selectModelForPlayground('${m.id}')">⚡ Test</button>
        </td>
      </tr>
    `;
  }).join("");
}

// Model privacy pills listener
document.querySelectorAll("#model-privacy-pills .pill").forEach(pill => {
  pill.addEventListener("click", () => {
    document.querySelectorAll("#model-privacy-pills .pill").forEach(p => p.classList.remove("active"));
    pill.classList.add("active");
    renderModelsTable();
  });
});

function selectModelForPlayground(modelId) {
  document.querySelector('.nav-tab[data-tab="tab-playground"]').click();
  const select = document.getElementById("play-model-select");
  let opt = Array.from(select.options).find(o => o.value === modelId);
  if (!opt) {
    opt = new Option(modelId, modelId);
    select.add(opt);
  }
  select.value = modelId;
}

// =============================================================================
// INFERENCE PLAYGROUND
// =============================================================================
function initPlayground() {
  document.getElementById("btn-run-inference").addEventListener("click", async () => {
    const model = document.getElementById("play-model-select").value;
    const prompt = document.getElementById("play-prompt-input").value.trim();
    const apiKey = document.getElementById("play-key-select").value || null;

    if (!prompt) return;

    const btn = document.getElementById("btn-run-inference");
    btn.disabled = true;
    btn.innerText = "⏳ Running...";

    document.getElementById("tel-status").innerText = "Executing...";
    document.getElementById("play-output-box").innerText = "Contacting Venice inference endpoint...";

    try {
      const res = await authFetch("/api/inference/test", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ prompt, model, api_key: apiKey })
      });
      const data = await res.json();

      document.getElementById("tel-latency").innerText = `${data.latency_ms} ms`;
      document.getElementById("tel-tokens").innerText = `${data.total_tokens} (${data.prompt_tokens} in / ${data.completion_tokens} out)`;
      document.getElementById("tel-status").innerText = data.success ? "Success" : "Failed";

      if (data.success) {
        document.getElementById("play-output-box").innerText = data.output;
      } else {
        document.getElementById("play-output-box").innerText = "Error: " + data.error;
      }
    } catch (err) {
      document.getElementById("play-output-box").innerText = "Network Error: " + err.message;
      document.getElementById("tel-status").innerText = "Error";
    } finally {
      btn.disabled = false;
      btn.innerText = "🚀 Run Inference";
    }
  });
}

function populatePlaygroundKeys() {
  const select = document.getElementById("play-key-select");
  const current = select.value;
  select.innerHTML = '<option value="">Master Key (Server Configured)</option>';
  allKeys.forEach(k => {
    const opt = document.createElement("option");
    opt.value = k.id;
    opt.innerText = `${k.description || 'Key'} (...${k.last6Chars || '••••'})`;
    select.appendChild(opt);
  });
  select.value = current;
}

// =============================================================================
// SETTINGS & THRESHOLDS MODAL
// =============================================================================
function initSettings() {
  document.getElementById("btn-open-settings").addEventListener("click", () => {
    document.getElementById("settings-global-threshold").value = globalThreshold;
    renderSettingsCategoriesList();
    document.getElementById("modal-settings").classList.remove("hidden");
  });

  document.getElementById("btn-save-settings").addEventListener("click", async () => {
    const newThresh = parseFloat(document.getElementById("settings-global-threshold").value);
    if (!isNaN(newThresh)) {
      await authFetch("/api/settings", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ global_threshold: newThresh })
      });
      globalThreshold = newThresh;
      showToast("Settings saved", "success");
      loadBalance();
      loadKeys();
    }
    document.getElementById("modal-settings").classList.add("hidden");
  });

  document.getElementById("btn-save-new-category").addEventListener("click", async () => {
    const input = document.getElementById("new-category-input");
    const cat = input.value.trim();
    if (!cat) return;
    await authFetch("/api/categories", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ category: cat })
    });
    input.value = "";
    loadCategories();
    renderSettingsCategoriesList();
    showToast(`Category "${cat}" added`, "success");
  });
}

function renderSettingsCategoriesList() {
  const list = document.getElementById("settings-categories-list");
  list.innerHTML = allCategories.map(cat => `
    <div style="display:flex; justify-content:space-between; align-items:center; padding:6px 0; border-bottom:1px solid rgba(255,255,255,0.05);">
      <span>${escapeHtml(cat)}</span>
      ${cat !== "Default" ? `<button class="btn btn-xs btn-outline" onclick="deleteCategory('${cat}')">Delete</button>` : '<span class="text-xs text-dim">Core</span>'}
    </div>
  `).join("");
}

async function deleteCategory(cat) {
  await authFetch(`/api/categories/${encodeURIComponent(cat)}`, { method: "DELETE" });
  loadCategories();
  renderSettingsCategoriesList();
  showToast(`Category "${cat}" removed`, "info");
}

// =============================================================================
// BACKUP EXPORT & IMPORT
// =============================================================================
function initBackup() {
  document.getElementById("btn-backup-menu").addEventListener("click", () => {
    document.getElementById("modal-backup").classList.remove("hidden");
  });

  document.getElementById("btn-upload-backup").addEventListener("click", async () => {
    const fileInput = document.getElementById("backup-file-input");
    if (!fileInput.files.length) {
      showToast("Please select a JSON backup file to upload", "error");
      return;
    }
    const formData = new FormData();
    formData.append("file", fileInput.files[0]);

    try {
      const res = await authFetch("/api/backup/import", {
        method: "POST",
        body: formData
      });
      if (!res.ok) throw new Error("Import failed");
      showToast("Backup restored successfully!", "success");
      document.getElementById("modal-backup").classList.add("hidden");
      loadCategories();
      loadKeys();
      loadBalance();
    } catch (err) {
      showToast(err.message, "error");
    }
  });
}

// =============================================================================
// UTILITIES
// =============================================================================
function escapeHtml(str) {
  if (!str) return "";
  return String(str)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#039;");
}

function formatDate(isoStr) {
  if (!isoStr) return "--";
  try {
    const d = new Date(isoStr);
    return d.toLocaleDateString() + " " + d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
  } catch (e) {
    return isoStr;
  }
}

function jsonParseSafe(str) {
  try {
    return JSON.parse(str);
  } catch (e) {
    return null;
  }
}

// =============================================================================
// DAILY REPORT
// =============================================================================
function initReport() {
  const btnOpen = document.getElementById("btn-open-report");
  const modal = document.getElementById("modal-report");
  const pre = document.getElementById("report-content-pre");
  const btnRefresh = document.getElementById("btn-refresh-report");
  const btnCopy = document.getElementById("btn-copy-report");
  const btnSendTg = document.getElementById("btn-send-report-tg");

  let currentMarkdown = "";

  async function fetchReport() {
    pre.innerText = "Generating live Venice usage report...";
    try {
      const res = await authFetch("/api/report");
      if (!res.ok) throw new Error("Failed to load report");
      const data = await res.json();
      currentMarkdown = data.markdown;
      pre.innerText = data.markdown;
    } catch (err) {
      pre.innerText = `Error: ${err.message}`;
      showToast("Failed to generate report", "error");
    }
  }

  if (btnOpen) {
    btnOpen.addEventListener("click", () => {
      modal.classList.remove("hidden");
      fetchReport();
    });
  }

  if (btnRefresh) {
    btnRefresh.addEventListener("click", fetchReport);
  }

  if (btnCopy) {
    btnCopy.addEventListener("click", () => {
      if (!currentMarkdown) return;
      navigator.clipboard.writeText(currentMarkdown);
      showToast("Report markdown copied to clipboard!", "success");
    });
  }

  if (btnSendTg) {
    btnSendTg.addEventListener("click", async () => {
      btnSendTg.disabled = true;
      btnSendTg.innerText = "Sending...";
      try {
        const res = await authFetch("/api/report/send", { method: "POST" });
        if (!res.ok) {
          const errData = await res.json().catch(() => ({}));
          throw new Error(errData.detail || "Failed to dispatch report");
        }
        showToast("Report notification successfully dispatched!", "success");
      } catch (err) {
        showToast(err.message, "error");
      } finally {
        btnSendTg.disabled = false;
        btnSendTg.innerText = "🚀 Dispatch Notification";
      }
    });
  }
}

// =============================================================================
// EXTERNAL ALLOCATIONS, PROJECTS, & GATEWAY
// =============================================================================
let allProjects = [];
let allExternalKeys = [];
let currentGatewayInfo = null;

function initAllocations() {
  // Cloudflare Gateway URL Save
  const btnSaveCf = document.getElementById("btn-save-cf-url");
  if (btnSaveCf) {
    btnSaveCf.addEventListener("click", saveGatewayUrl);
  }

  // Snippet Copy Buttons
  const btnCopyCurl = document.getElementById("btn-copy-gw-curl");
  if (btnCopyCurl) {
    btnCopyCurl.addEventListener("click", () => {
      const code = document.getElementById("gw-snippet-pre")?.innerText;
      if (code) {
        navigator.clipboard.writeText(code);
        showToast("cURL example copied to clipboard!", "success");
      }
    });
  }

  const btnCopyPy = document.getElementById("btn-copy-gw-py");
  if (btnCopyPy) {
    btnCopyPy.addEventListener("click", () => {
      const gwUrl = currentGatewayInfo?.effective_gateway_url || "http://localhost:8660";
      const pySnippet = `from openai import OpenAI\n\nclient = OpenAI(\n    base_url="${gwUrl}/v1",\n    api_key="<EXTERNAL_KEY_OR_PAIRING_CODE>"\n)\n\nresponse = client.chat.completions.create(\n    model="xs",  # or "s", "m", "l", "xl"\n    messages=[{"role": "user", "content": "Hello Venice!"}]\n)\nprint(response.choices[0].message.content)`;
      navigator.clipboard.writeText(pySnippet);
      showToast("Python OpenAI snippet copied to clipboard!", "success");
    });
  }

  // Project Modals
  const btnOpenCreateProj = document.getElementById("btn-open-create-project");
  if (btnOpenCreateProj) {
    btnOpenCreateProj.addEventListener("click", () => {
      document.getElementById("proj-create-name").value = "";
      document.getElementById("proj-create-desc").value = "";
      document.getElementById("proj-create-daily").value = "2.00";
      document.getElementById("proj-create-weekly").value = "";
      document.getElementById("proj-create-default-sub").value = "0.25";
      document.getElementById("proj-create-tier").value = "xl";
      document.getElementById("modal-create-project").classList.remove("hidden");
    });
  }

  const btnSubmitCreateProj = document.getElementById("btn-submit-create-project");
  if (btnSubmitCreateProj) {
    btnSubmitCreateProj.addEventListener("click", handleCreateProject);
  }

  const btnSubmitEditProj = document.getElementById("btn-submit-edit-project");
  if (btnSubmitEditProj) {
    btnSubmitEditProj.addEventListener("click", handleEditProject);
  }

  // External Key Modals
  const btnOpenCreateExt = document.getElementById("btn-open-create-ext-key");
  if (btnOpenCreateExt) {
    btnOpenCreateExt.addEventListener("click", () => {
      document.getElementById("ext-key-name").value = "";
      document.getElementById("ext-key-daily-limit").value = "0.25";
      document.getElementById("ext-key-notes").value = "";
      document.getElementById("ext-key-prefix").value = "vkm_ext_";
      document.getElementById("modal-create-ext-key").classList.remove("hidden");
    });
  }

  const btnSubmitCreateExt = document.getElementById("btn-submit-create-ext-key");
  if (btnSubmitCreateExt) {
    btnSubmitCreateExt.addEventListener("click", handleCreateExternalKey);
  }

  const btnSubmitSubKey = document.getElementById("btn-submit-create-sub-key");
  if (btnSubmitSubKey) {
    btnSubmitSubKey.addEventListener("click", handleCreateSubKey);
  }

  const btnSubmitEditExt = document.getElementById("btn-submit-edit-ext-key");
  if (btnSubmitEditExt) {
    btnSubmitEditExt.addEventListener("click", handleEditExternalKey);
  }

  // Filter dropdown
  const filterProj = document.getElementById("filter-ext-project");
  if (filterProj) {
    filterProj.addEventListener("change", () => {
      loadExternalKeys(filterProj.value);
    });
  }
}

async function loadGatewayInfo() {
  try {
    const res = await authFetch("/api/gateway/info");
    if (!res.ok) return;
    currentGatewayInfo = await res.json();
    const inputCf = document.getElementById("cfg-cf-gateway-url");
    if (inputCf && currentGatewayInfo.cloudflare_gateway_url) {
      inputCf.value = currentGatewayInfo.cloudflare_gateway_url;
    }
    const pre = document.getElementById("gw-snippet-pre");
    if (pre && currentGatewayInfo.effective_gateway_url) {
      pre.innerText = `curl -X POST ${currentGatewayInfo.effective_gateway_url}/v1/chat/completions \\\n  -H "Authorization: Bearer <EXTERNAL_KEY_OR_PAIRING_CODE>" \\\n  -H "Content-Type: application/json" \\\n  -d '{"model": "xs", "messages": [{"role": "user", "content": "Hello!"}]}'`;
    }
  } catch (err) {}
}

async function saveGatewayUrl() {
  const inputCf = document.getElementById("cfg-cf-gateway-url");
  const url = inputCf ? inputCf.value.trim() : "";
  try {
    const res = await authFetch("/api/gateway/config", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ cloudflare_gateway_url: url })
    });
    if (!res.ok) throw new Error("Failed to save gateway config");
    showToast("Cloudflare Gateway endpoint updated successfully!", "success");
    loadGatewayInfo();
  } catch (err) {
    showToast(err.message, "error");
  }
}

async function loadProjects() {
  const tbody = document.getElementById("projects-tbody");
  try {
    const res = await authFetch("/api/projects");
    if (!res.ok) throw new Error("Failed to load projects");
    const data = await res.json();
    allProjects = data.projects || [];

    const tabBadge = document.getElementById("tab-projects-count");
    if (tabBadge) tabBadge.innerText = allProjects.length;

    // Update project select dropdowns
    const selCreate = document.getElementById("ext-key-project-select");
    const selFilter = document.getElementById("filter-ext-project");
    if (selCreate) {
      selCreate.innerHTML = allProjects.map(p =>
        `<option value="${escapeHtml(p.id)}">${escapeHtml(p.name)} ($${p.daily_limit_usd.toFixed(2)}/day cap)</option>`
      ).join("");
    }
    if (selFilter) {
      const cur = selFilter.value;
      selFilter.innerHTML = `<option value="">All Projects (${allProjects.length})</option>` +
        allProjects.map(p => `<option value="${escapeHtml(p.id)}" ${p.id === cur ? 'selected' : ''}>${escapeHtml(p.name)}</option>`).join("");
    }

    if (!allProjects.length) {
      if (tbody) tbody.innerHTML = `<tr><td colspan="9" class="text-center py-6 text-muted">No projects found. Create your first project allocation!</td></tr>`;
      return;
    }

    if (tbody) {
      tbody.innerHTML = allProjects.map(p => {
        const isPaused = p.status !== "active";
        const weeklyStr = p.weekly_limit_usd ? `$${p.weekly_limit_usd.toFixed(2)}` : "--";
        const defaultSubStr = `$${(p.default_sub_key_daily_usd || 0.25).toFixed(2)}`;
        const daySpent = (p.current_day_spend || 0).toFixed(4);
        const dayLimit = p.daily_limit_usd.toFixed(2);
        const percent = Math.min(100, Math.round(((p.current_day_spend || 0) / p.daily_limit_usd) * 100));

        return `
          <tr class="${isPaused ? 'row-paused' : ''}">
            <td>
              <div class="font-semibold text-white">${escapeHtml(p.name)}</div>
              <div class="text-xs text-dim">${escapeHtml(p.description || p.id)}</div>
            </td>
            <td><span class="mono font-bold">$${dayLimit}</span></td>
            <td><span class="mono text-dim">${weeklyStr}</span></td>
            <td><span class="mono text-accent">${defaultSubStr} / day</span></td>
            <td><span class="chip chip-category font-bold">${p.max_model_tier.toUpperCase()}</span></td>
            <td>
              <div class="spend-bar-cell">
                <span class="mono text-xs">$${daySpent} (${percent}%)</span>
                <div class="progress-bar-bg"><div class="progress-bar-fill" style="width: ${percent}%;"></div></div>
              </div>
            </td>
            <td><span class="chip chip-type">${p.connected_keys_count || 0} active</span></td>
            <td><span class="chip ${isPaused ? 'chip-warning' : 'chip-success'}">${p.status}</span></td>
            <td>
              <div style="display:flex; gap:6px;">
                <button class="btn btn-xs btn-outline" onclick="openEditProjectModal('${p.id}')">✏️ Edit</button>
                <button class="btn btn-xs btn-primary" onclick="openCreateExtKeyForProject('${p.id}')">➕ Key</button>
                ${p.id !== 'proj_default' ? `<button class="btn btn-xs btn-danger" onclick="deleteProject('${p.id}')">❌</button>` : ''}
              </div>
            </td>
          </tr>
        `;
      }).join("");
    }
  } catch (err) {
    if (tbody) tbody.innerHTML = `<tr><td colspan="9" class="text-center text-warning py-6">Error loading projects: ${escapeHtml(err.message)}</td></tr>`;
  }
}

async function loadExternalKeys(projectId = "") {
  const tbody = document.getElementById("ext-keys-tbody");
  try {
    const url = projectId ? `/api/external-keys?project_id=${encodeURIComponent(projectId)}` : "/api/external-keys";
    const res = await authFetch(url);
    if (!res.ok) throw new Error("Failed to load external keys");
    const data = await res.json();
    allExternalKeys = data.keys || [];

    if (!allExternalKeys.length) {
      if (tbody) tbody.innerHTML = `<tr><td colspan="9" class="text-center py-6 text-muted">No external keys found. Click 'Generate External Key' to grant inference allocation!</td></tr>`;
      return;
    }

    if (tbody) {
      tbody.innerHTML = allExternalKeys.map(k => {
        const isPaused = k.status !== "active";
        const tokenDisplay = k.token ? `...${k.token.slice(-6)}` : k.id;
        const spentVal = (k.current_period_spend || 0).toFixed(4);
        const limitVal = (k.daily_limit_usd || 0.25).toFixed(2);
        const periodLabel = k.limit_period || "DAY";

        return `
          <tr class="${isPaused ? 'row-paused' : ''}">
            <td>
              <div class="font-semibold text-white">${escapeHtml(k.name)}</div>
              <div class="text-xs text-dim">${escapeHtml(k.notes || k.id)}</div>
            </td>
            <td><span class="chip chip-category text-xs">${escapeHtml(k.project_name || k.project_id)}</span></td>
            <td><span class="chip chip-type text-xs">${k.key_type || 'EXTERNAL'}</span></td>
            <td>
              <div class="key-id-cell">
                <span class="mono text-xs">${escapeHtml(tokenDisplay)}</span>
                <button class="btn btn-xs btn-outline copy-btn" data-copy-text="${escapeHtml(k.token || k.id)}" title="Copy Full Token">📋 Copy</button>
              </div>
            </td>
            <td><span class="mono font-bold">$${limitVal}</span> <span class="text-xs text-dim">/ ${periodLabel}</span></td>
            <td><span class="mono text-accent">$${spentVal}</span></td>
            <td><span class="chip font-bold">${(k.max_model_tier || 'xl').toUpperCase()}</span></td>
            <td><span class="chip ${isPaused ? 'chip-danger' : 'chip-success'}">${k.status}</span></td>
            <td>
              <div style="display:flex; gap:6px;">
                <button class="btn btn-xs btn-outline" onclick="copyExtKeyCurl('${k.token}', '${k.max_model_tier}')" title="Copy cURL snippet">📋 cURL</button>
                <button class="btn btn-xs btn-outline" onclick="openEditExtKeyModal('${k.id}')" title="Modify Allocation">✏️ Edit</button>
                <button class="btn btn-xs btn-secondary" onclick="openCreateSubKeyModal('${k.id}', '${escapeHtml(k.name)}')" title="Delegate Sub-Key">🌱 Sub-Key</button>
                <button class="btn btn-xs btn-danger" onclick="revokeExternalKey('${k.id}')" title="Revoke Key">❌</button>
              </div>
            </td>
          </tr>
        `;
      }).join("");
    }
  } catch (err) {
    if (tbody) tbody.innerHTML = `<tr><td colspan="9" class="text-center text-warning py-6">Error loading keys: ${escapeHtml(err.message)}</td></tr>`;
  }
}

// Project Modal Handlers
async function handleCreateProject() {
  const name = document.getElementById("proj-create-name").value.trim();
  if (!name) return showToast("Project name is required", "error");

  const desc = document.getElementById("proj-create-desc").value.trim();
  const daily = parseFloat(document.getElementById("proj-create-daily").value) || 1.00;
  const weeklyInput = document.getElementById("proj-create-weekly").value;
  const weekly = weeklyInput ? parseFloat(weeklyInput) : null;
  const defaultSub = parseFloat(document.getElementById("proj-create-default-sub").value) || 0.25;
  const tier = document.getElementById("proj-create-tier").value;

  try {
    const res = await authFetch("/api/projects", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        name,
        description: desc,
        daily_limit_usd: daily,
        weekly_limit_usd: weekly,
        default_sub_key_daily_usd: defaultSub,
        max_model_tier: tier,
      })
    });
    if (!res.ok) {
      const err = await res.json();
      throw new Error(err.detail || "Failed to create project");
    }
    document.getElementById("modal-create-project").classList.add("hidden");
    showToast(`Project '${name}' created successfully!`, "success");
    loadProjects();
  } catch (err) {
    showToast(err.message, "error");
  }
}

function openEditProjectModal(projectId) {
  const p = allProjects.find(item => item.id === projectId);
  if (!p) return;
  document.getElementById("proj-edit-id").value = p.id;
  document.getElementById("proj-edit-name").value = p.name;
  document.getElementById("proj-edit-daily").value = p.daily_limit_usd;
  document.getElementById("proj-edit-default-sub").value = p.default_sub_key_daily_usd || 0.25;
  document.getElementById("proj-edit-tier").value = p.max_model_tier || "xl";
  document.getElementById("proj-edit-status").value = p.status || "active";
  document.getElementById("modal-edit-project").classList.remove("hidden");
}

async function handleEditProject() {
  const pid = document.getElementById("proj-edit-id").value;
  const name = document.getElementById("proj-edit-name").value.trim();
  const daily = parseFloat(document.getElementById("proj-edit-daily").value);
  const defaultSub = parseFloat(document.getElementById("proj-edit-default-sub").value);
  const tier = document.getElementById("proj-edit-tier").value;
  const status = document.getElementById("proj-edit-status").value;

  try {
    const res = await authFetch(`/api/projects/${pid}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        name,
        daily_limit_usd: daily,
        default_sub_key_daily_usd: defaultSub,
        max_model_tier: tier,
        status,
      })
    });
    if (!res.ok) {
      const err = await res.json();
      throw new Error(err.detail || "Failed to update project");
    }
    document.getElementById("modal-edit-project").classList.add("hidden");
    showToast("Project allocation updated successfully!", "success");
    loadProjects();
  } catch (err) {
    showToast(err.message, "error");
  }
}

async function deleteProject(projectId) {
  if (!confirm(`Delete project and revoke all associated external keys?`)) return;
  try {
    const res = await authFetch(`/api/projects/${projectId}`, { method: "DELETE" });
    if (!res.ok) {
      const err = await res.json();
      throw new Error(err.detail || "Failed to delete project");
    }
    showToast("Project deleted.", "info");
    loadProjects();
    loadExternalKeys();
  } catch (err) {
    showToast(err.message, "error");
  }
}

// External Key Handlers
function openCreateExtKeyForProject(projectId) {
  const sel = document.getElementById("ext-key-project-select");
  if (sel) sel.value = projectId;
  document.getElementById("ext-key-name").value = "";
  document.getElementById("ext-key-daily-limit").value = "0.25";
  document.getElementById("ext-key-notes").value = "";
  document.getElementById("modal-create-ext-key").classList.remove("hidden");
}

async function handleCreateExternalKey() {
  const projectId = document.getElementById("ext-key-project-select").value;
  const name = document.getElementById("ext-key-name").value.trim();
  if (!name) return showToast("Key/Agent name is required", "error");

  const daily = parseFloat(document.getElementById("ext-key-daily-limit").value) || 0.25;
  const period = document.getElementById("ext-key-period").value;
  const tier = document.getElementById("ext-key-tier").value;
  const prefix = document.getElementById("ext-key-prefix").value.trim() || "vkm_ext_";
  const notes = document.getElementById("ext-key-notes").value.trim();

  try {
    const res = await authFetch("/api/external-keys", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        project_id: projectId,
        name,
        daily_limit_usd: daily,
        limit_period: period,
        max_model_tier: tier,
        prefix,
        notes,
      })
    });
    if (!res.ok) {
      const err = await res.json();
      throw new Error(err.detail || "Failed to generate key");
    }
    const data = await res.json();
    document.getElementById("modal-create-ext-key").classList.add("hidden");
    revealExternalKey(data.key, data.endpoints);
    loadExternalKeys();
    loadProjects();
  } catch (err) {
    showToast(err.message, "error");
  }
}

function openCreateSubKeyModal(parentKeyId, parentName) {
  document.getElementById("sub-key-parent-id").value = parentKeyId;
  document.getElementById("sub-key-parent-label").innerText = `${parentName} (${parentKeyId})`;
  document.getElementById("sub-key-name").value = "";
  document.getElementById("sub-key-amount").value = "0.25";
  document.getElementById("sub-key-notes").value = "";
  document.getElementById("modal-create-sub-key").classList.remove("hidden");
}

async function handleCreateSubKey() {
  const parentId = document.getElementById("sub-key-parent-id").value;
  const name = document.getElementById("sub-key-name").value.trim();
  if (!name) return showToast("Sub-key name is required", "error");

  const amount = parseFloat(document.getElementById("sub-key-amount").value) || 0.25;
  const period = document.getElementById("sub-key-period").value;
  const tier = document.getElementById("sub-key-tier").value;
  const notes = document.getElementById("sub-key-notes").value.trim();

  try {
    const res = await authFetch(`/api/external-keys/${parentId}/subkeys`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        name,
        amount_usd: amount,
        period,
        max_model_tier: tier,
        notes,
      })
    });
    if (!res.ok) {
      const err = await res.json();
      throw new Error(err.detail || "Failed to generate sub-key");
    }
    const data = await res.json();
    document.getElementById("modal-create-sub-key").classList.add("hidden");
    revealExternalKey(data.key, data.endpoints);
    loadExternalKeys();
    loadProjects();
  } catch (err) {
    showToast(err.message, "error");
  }
}

function openEditExtKeyModal(keyId) {
  const k = allExternalKeys.find(item => item.id === keyId);
  if (!k) return;
  document.getElementById("ext-edit-key-id").value = k.id;
  document.getElementById("ext-edit-name").value = k.name;
  document.getElementById("ext-edit-daily-limit").value = k.daily_limit_usd;
  document.getElementById("ext-edit-tier").value = k.max_model_tier || "xl";
  document.getElementById("ext-edit-status").value = k.status || "active";
  document.getElementById("modal-edit-ext-key").classList.remove("hidden");
}

async function handleEditExternalKey() {
  const kid = document.getElementById("ext-edit-key-id").value;
  const name = document.getElementById("ext-edit-name").value.trim();
  const daily = parseFloat(document.getElementById("ext-edit-daily-limit").value);
  const tier = document.getElementById("ext-edit-tier").value;
  const status = document.getElementById("ext-edit-status").value;

  try {
    const res = await authFetch(`/api/external-keys/${kid}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        name,
        daily_limit_usd: daily,
        max_model_tier: tier,
        status,
      })
    });
    if (!res.ok) {
      const err = await res.json();
      throw new Error(err.detail || "Failed to update external key");
    }
    document.getElementById("modal-edit-ext-key").classList.add("hidden");
    showToast("Allocation updated!", "success");
    loadExternalKeys();
  } catch (err) {
    showToast(err.message, "error");
  }
}

async function revokeExternalKey(keyId) {
  if (!confirm(`Revoke external key? Gated inference access will be immediately blocked.`)) return;
  try {
    const res = await authFetch(`/api/external-keys/${keyId}`, { method: "DELETE" });
    if (!res.ok) throw new Error("Revocation failed");
    showToast("External key revoked", "success");
    loadExternalKeys();
  } catch (err) {
    showToast(err.message, "error");
  }
}

function revealExternalKey(keyObj, endpoints) {
  document.getElementById("revealed-ext-key-token").value = keyObj.token;

  const tsUrl = endpoints?.tailscale_v1_url || "http://100.99.202.75:8660/v1";
  const cfUrl = endpoints?.cloudflare_v1_url || (currentGatewayInfo?.effective_gateway_url ? `${currentGatewayInfo.effective_gateway_url}/v1` : "https://worship-him-knight-jul.trycloudflare.com/v1");

  const tsInput = document.getElementById("revealed-ext-key-tailscale");
  if (tsInput) tsInput.value = tsUrl;
  const cfInput = document.getElementById("revealed-ext-key-cloudflare");
  if (cfInput) cfInput.value = cfUrl;

  const modelTier = keyObj.max_model_tier || "xl";

  const curlCmd = `# 1. Internal Agent (Tailscale Address):\ncurl -X POST ${tsUrl}/chat/completions \\\n  -H "Authorization: Bearer ${keyObj.token}" \\\n  -H "Content-Type: application/json" \\\n  -d '{"model": "${modelTier}", "messages": [{"role": "user", "content": "Hello!"}]}'\n\n# 2. External Agent (Cloudflare DNS URL):\ncurl -X POST ${cfUrl}/chat/completions \\\n  -H "Authorization: Bearer ${keyObj.token}" \\\n  -H "Content-Type: application/json" \\\n  -d '{"model": "${modelTier}", "messages": [{"role": "user", "content": "Hello!"}]}'`;
  document.getElementById("revealed-ext-key-curl").innerText = curlCmd;

  const pyCode = `from openai import OpenAI\n\n# Choose the endpoint matching your agent location:\n# Internal Mesh:  ${tsUrl}\n# External Remote: ${cfUrl}\n\nclient = OpenAI(\n    base_url="${cfUrl}",  # Switch to "${tsUrl}" if on internal network\n    api_key="${keyObj.token}"\n)\n\nres = client.chat.completions.create(\n    model="${modelTier}",\n    messages=[{"role": "user", "content": "Hello!"}]\n)\nprint(res.choices[0].message.content)`;
  document.getElementById("revealed-ext-key-py").innerText = pyCode;

  currentRevealedExtBundle = {
    name: keyObj.name || "External Key",
    token: keyObj.token,
    id: keyObj.id,
    project_name: keyObj.project_name || keyObj.project_id || "Default",
    max_tier: modelTier,
    daily_limit_usd: keyObj.daily_limit_usd,
    tailscale_address: tsUrl,
    cloudflare_dns_url: cfUrl,
  };

  document.getElementById("modal-ext-key-revealed").classList.remove("hidden");
}

function copyExtKeyCurl(token, tier) {
  const tsUrl = "http://100.99.202.75:8660/v1";
  const cfUrl = currentGatewayInfo?.effective_gateway_url ? `${currentGatewayInfo.effective_gateway_url}/v1` : "https://worship-him-knight-jul.trycloudflare.com/v1";
  const curlCmd = `# 1. Internal Agent (Tailscale Address):\ncurl -X POST ${tsUrl}/chat/completions \\\n  -H "Authorization: Bearer ${token}" \\\n  -H "Content-Type: application/json" \\\n  -d '{"model": "${tier}", "messages": [{"role": "user", "content": "Hello!"}]}'\n\n# 2. External Agent (Cloudflare DNS URL):\ncurl -X POST ${cfUrl}/chat/completions \\\n  -H "Authorization: Bearer ${token}" \\\n  -H "Content-Type: application/json" \\\n  -d '{"model": "${tier}", "messages": [{"role": "user", "content": "Hello!"}]}'`;
  navigator.clipboard.writeText(curlCmd);
  showToast("cURL commands (Tailscale + Cloudflare) copied to clipboard!", "success");
}


