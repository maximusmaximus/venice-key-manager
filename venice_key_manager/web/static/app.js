// =============================================================================
// Venice Key Manager - Reactive Client Application
// =============================================================================

let allKeys = [];
let allModels = [];
let allCategories = ["Default", "Agents", "Production", "Testing", "Telegram"];
let activeCategory = "all";
let globalThreshold = 0.20;
let sseSource = null;

document.addEventListener("DOMContentLoaded", () => {
  initTabs();
  initCopyButtons();
  initModals();
  initPresets();
  initPlayground();
  initBackup();
  initSettings();
  initReport();

  // Load initial data
  loadBalance();
  loadCategories();
  loadKeys();
  loadModels();
  initSSE();

  // Refresh button
  document.getElementById("btn-refresh").addEventListener("click", () => {
    loadBalance();
    loadKeys();
    loadModels();
    showToast("Data refreshed from Venice cloud", "info");
  });

  // Search and filter listeners
  document.getElementById("key-search-input").addEventListener("input", renderKeysTable);
  document.getElementById("toggle-low-balance-only").addEventListener("change", renderKeysTable);
  document.getElementById("model-search-input").addEventListener("input", renderModelsTable);

  // Close banner listener
  document.getElementById("btn-close-banner").addEventListener("click", () => {
    document.getElementById("low-balance-banner").classList.add("hidden");
  });
});

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
    const res = await fetch("/api/balance");
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
    sseSource = new EventSource("/api/sse/stats");
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
    const res = await fetch("/api/categories");
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
    const res = await fetch("/api/keys");
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
    document.getElementById("modal-create-key").classList.remove("hidden");
  });

  document.getElementById("btn-submit-create-key").addEventListener("click", handleCreateKey);
  document.getElementById("btn-submit-cycle-key").addEventListener("click", handleCycleKey);
  document.getElementById("btn-submit-edit-key").addEventListener("click", handleEditKey);
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

async function handleCreateKey() {
  const desc = document.getElementById("create-desc").value.trim();
  if (!desc) {
    showToast("Please enter a description or agent name", "error");
    return;
  }

  const category = document.getElementById("create-category").value;
  const period = document.querySelector('input[name="create-period"]:checked').value;
  const limitVal = document.getElementById("create-limit-usd").value;
  const customThreshVal = document.getElementById("create-custom-threshold").value;
  const keyType = document.getElementById("create-type").value;

  const payload = {
    description: desc,
    apiKeyType: keyType,
    limitPeriod: period,
    category: category,
    daily_usd: limitVal !== "" ? parseFloat(limitVal) : null,
    custom_threshold: customThreshVal !== "" ? parseFloat(customThreshVal) : null
  };

  try {
    const res = await fetch("/api/keys", {
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

    // Show revealed token modal
    document.getElementById("revealed-key-token").value = data.apiKey;
    document.getElementById("revealed-key-id").value = data.id;

    const curlSnippet = `curl -X POST https://api.venice.ai/api/v1/chat/completions \\\n  -H "Authorization: Bearer ${data.apiKey}" \\\n  -H "Content-Type: application/json" \\\n  -d '{"model": "deepseek-v4-flash", "messages": [{"role": "user", "content": "Hello Venice"}]}'`;
    document.getElementById("revealed-curl").innerText = curlSnippet;

    document.getElementById("modal-key-revealed").classList.remove("hidden");

    showToast(`Key "${desc}" minted successfully!`, "success");
    loadKeys();
  } catch (err) {
    showToast(err.message, "error");
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
    const res = await fetch(`/api/keys/${keyId}/cycle`, {
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
    const res = await fetch(`/api/keys/${keyId}`, {
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
    const res = await fetch(`/api/keys/${keyId}`, { method: "DELETE" });
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
    const res = await fetch("/api/models");
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
      const res = await fetch("/api/inference/test", {
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
      await fetch("/api/settings", {
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
    await fetch("/api/categories", {
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
  await fetch(`/api/categories/${encodeURIComponent(cat)}`, { method: "DELETE" });
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
      const res = await fetch("/api/backup/import", {
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
      const res = await fetch("/api/report");
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
        const res = await fetch("/api/report/send", { method: "POST" });
        if (!res.ok) {
          const errData = await res.json().catch(() => ({}));
          throw new Error(errData.detail || "Failed to dispatch report");
        }
        showToast("Report successfully dispatched to Telegram!", "success");
      } catch (err) {
        showToast(err.message, "error");
      } finally {
        btnSendTg.disabled = false;
        btnSendTg.innerText = "✈️ Send to Telegram";
      }
    });
  }
}

