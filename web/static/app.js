// Frontend controller for Venice & Telegram Key Management Dashboard

let appState = {
  activeNode: "local",
  fleetNodes: [],
  stats: {},
  keys: [],
  subkeys: [],
  allocations: [],
  agentBots: [],
  rateLimits: [],
  authenticated: false,
  pairingRequired: true,
  domain: ""
};

function getPairingCode() {
  const urlParams = new URLSearchParams(window.location.search);
  const codeFromUrl = urlParams.get("pairing_code");
  if (codeFromUrl) {
    sessionStorage.setItem("venice_pairing_code", codeFromUrl);
    return codeFromUrl;
  }
  return sessionStorage.getItem("venice_pairing_code") || "";
}

function getAuthHeaders(headers = {}) {
  const code = getPairingCode();
  const res = { ...headers };
  if (code) {
    res["X-Pairing-Code"] = code;
  }
  return res;
}

async function apiFetch(url, options = {}) {
  options.headers = getAuthHeaders(options.headers || {});
  const res = await fetch(url, options);
  if (res.status === 401) {
    try {
      const data = await res.clone().json();
      if (data && data.requires_pairing) {
        appState.authenticated = false;
        updatePairingUI(false);
      }
    } catch (_) {}
  }
  return res;
}

// DOM loaded
document.addEventListener("DOMContentLoaded", () => {
  initTabs();
  initModals();
  initInference();
  initForms();
  initFleet();
  initPairingAndSubkeys();

  // Initial data load
  refreshAll();

  // Periodic polling every 12 seconds
  setInterval(refreshAll, 12000);

  document.getElementById("btn-refresh").addEventListener("click", () => {
    refreshAll();
    showToast("Refreshing live data...", "info");
  });
});

// Tab Switching
function initTabs() {
  const tabBtns = document.querySelectorAll(".tab-btn");
  tabBtns.forEach(btn => {
    btn.addEventListener("click", () => {
      tabBtns.forEach(b => b.classList.remove("active"));
      document.querySelectorAll(".tab-content").forEach(c => c.classList.remove("active"));

      btn.classList.add("active");
      const targetId = btn.getAttribute("data-tab");
      document.getElementById(targetId).classList.add("active");

      if (targetId === "tab-rate-limits" && appState.rateLimits.length === 0) {
        loadRateLimits();
      }
    });
  });
}

// Modals
function initModals() {
  const btnOpenCreate = document.getElementById("btn-open-create-key");
  if (btnOpenCreate) {
    btnOpenCreate.addEventListener("click", () => {
      openModal("modal-create-key");
    });
  }

  const btnAddBot = document.getElementById("btn-add-agent-bot");
  if (btnAddBot) {
    btnAddBot.addEventListener("click", () => {
      openModal("modal-add-bot");
    });
  }

  const btnWizard = document.getElementById("btn-botfather-wizard");
  if (btnWizard) {
    btnWizard.addEventListener("click", () => {
      openModal("modal-botfather-wizard");
    });
  }

  // Modal sub-tabs (Import vs Generate)
  const tabImport = document.getElementById("tab-btn-import-key");
  const tabGenerate = document.getElementById("tab-btn-generate-key");
  const secImport = document.getElementById("section-import-key");
  const secGenerate = document.getElementById("section-generate-key");

  if (tabImport && tabGenerate) {
    tabImport.addEventListener("click", () => {
      tabImport.classList.add("active");
      tabGenerate.classList.remove("active");
      if (secImport) secImport.style.display = "block";
      if (secGenerate) secGenerate.style.display = "none";
    });

    tabGenerate.addEventListener("click", () => {
      tabGenerate.classList.add("active");
      tabImport.classList.remove("active");
      if (secGenerate) secGenerate.style.display = "block";
      if (secImport) secImport.style.display = "none";
    });
  }

  const btnCopyAllocUrl = document.getElementById("btn-copy-alloc-url");
  if (btnCopyAllocUrl) {
    btnCopyAllocUrl.addEventListener("click", () => {
      const urlBox = document.getElementById("alloc-result-url");
      if (urlBox) copyToClipboard(urlBox.value, "Cloud DNS claim link copied!");
    });
  }

  const btnCopyAllocToken = document.getElementById("btn-copy-alloc-token");
  if (btnCopyAllocToken) {
    btnCopyAllocToken.addEventListener("click", () => {
      const tokenBox = document.getElementById("alloc-result-token");
      if (tokenBox) copyToClipboard(tokenBox.value, "Claim token copied!");
    });
  }
}

function openModal(id) {
  const modal = document.getElementById(id);
  if (!modal) return;
  modal.classList.add("active");

  if (id === "modal-mint-allocation") {
    const successBox = document.getElementById("alloc-success-box");
    const btn = document.getElementById("btn-confirm-mint-alloc");
    if (successBox) successBox.style.display = "none";
    if (btn) {
      btn.style.display = "inline-flex";
      btn.disabled = false;
      btn.innerHTML = `<span>⚡</span> Mint Allocation Link`;
    }
  }

  if (id === "modal-create-key") {
    const hasAdmin = Boolean(appState.stats && appState.stats.has_admin_key);
    const banner = document.getElementById("modal-admin-required-banner");
    const groupAdmin = document.getElementById("group-modal-admin-key");
    const tabImport = document.getElementById("tab-btn-import-key");
    const tabGenerate = document.getElementById("tab-btn-generate-key");
    const secImport = document.getElementById("section-import-key");
    const secGenerate = document.getElementById("section-generate-key");

    if (banner) banner.style.display = hasAdmin ? "none" : "flex";
    if (groupAdmin) groupAdmin.style.display = hasAdmin ? "none" : "block";

    // If no admin key, default to import tab
    if (!hasAdmin && tabImport && tabGenerate) {
      tabImport.classList.add("active");
      tabGenerate.classList.remove("active");
      if (secImport) secImport.style.display = "block";
      if (secGenerate) secGenerate.style.display = "none";
    }
  }
}

function closeModal(id) {
  const modal = document.getElementById(id);
  if (modal) modal.classList.remove("active");
}

// Toast Notifications
function showToast(message, type = "success") {
  const container = document.getElementById("toast-container");
  const toast = document.createElement("div");
  toast.className = `toast ${type}`;
  toast.innerHTML = `<span>${type === 'error' ? '❌' : type === 'info' ? 'ℹ️' : '✅'}</span> <span>${message}</span>`;
  container.appendChild(toast);
  setTimeout(() => {
    toast.style.opacity = "0";
    setTimeout(() => toast.remove(), 300);
  }, 3500);
}

// --- Data Fetching & Pairing Status ---

function getNodeQueryParam() {
  return (appState.activeNode && appState.activeNode !== "local") ? `?node=${encodeURIComponent(appState.activeNode)}` : "";
}

async function checkPairingStatus() {
  try {
    const res = await apiFetch("/api/pairing_status");
    const data = await res.json();
    if (data.success) {
      appState.authenticated = Boolean(data.authenticated);
      appState.pairingRequired = Boolean(data.requires_pairing);
      appState.domain = data.domain || "";
      updatePairingUI(appState.authenticated);
    }
  } catch (err) {
    console.warn("Could not check pairing status:", err);
  }
}

function updatePairingUI(isPaired) {
  const guestGate = document.getElementById("guest-gate-card");
  const authView = document.getElementById("authenticated-view");
  const lblPair = document.getElementById("lbl-pairing-status");
  const iconPair = document.getElementById("icon-pair-status");
  const domainBadge = document.getElementById("gate-domain-badge");
  const nodeSwitcher = document.querySelector(".node-switcher-group");
  const btnSync = document.getElementById("btn-sync-code");
  const btnRefresh = document.getElementById("btn-refresh");

  if (domainBadge && appState.domain) {
    domainBadge.innerText = appState.domain.toUpperCase();
  }

  if (isPaired) {
    if (guestGate) guestGate.style.display = "none";
    if (authView) authView.style.display = "block";
    if (nodeSwitcher) nodeSwitcher.style.display = "flex";
    if (btnSync) btnSync.style.display = "inline-flex";
    if (btnRefresh) btnRefresh.style.display = "inline-flex";
    if (lblPair) lblPair.innerText = "Paired (Click to Unpair)";
    if (iconPair) iconPair.innerText = "🔓";
  } else {
    // When NOT logged in: strictly hide all administrative features
    // Only the standalone Key Validation portal is visible
    if (guestGate) guestGate.style.display = "block";
    if (authView) authView.style.display = "none";
    if (nodeSwitcher) nodeSwitcher.style.display = "none";
    if (btnSync) btnSync.style.display = "none";
    if (btnRefresh) btnRefresh.style.display = "none";
    if (lblPair) lblPair.innerText = "Log In / Pair Machine";
    if (iconPair) iconPair.innerText = "🔑";
  }
}

async function refreshAll() {
  await checkPairingStatus();
  if (appState.authenticated) {
    await Promise.all([
      loadStats(),
      loadKeys(),
      loadSubkeys(),
      loadAllocations(),
      loadAgentBots(),
      loadConfig(),
      loadVaultStatus(),
      loadServiceStatus(),
      loadFleetNodes()
    ]);
  } else {
    // Guest mode: Only key validation feature is active.
    // Zero administrative or private fleet endpoints are queried.
  }
}

async function loadStats() {
  try {
    const res = await apiFetch(`/api/stats${getNodeQueryParam()}`);
    if (res.status === 401) {
      document.getElementById("val-usd-balance").innerText = "$--";
      document.getElementById("val-diem-balance").innerText = "-- DIEM";
      document.getElementById("val-bundled-credits").innerText = "--";
      document.getElementById("val-keys-count").innerText = "--";
      document.getElementById("val-tg-bots-count").innerText = "--";
      return;
    }
    const data = await res.json();
    if (data.success) {
      appState.stats = data;
      const balances = data.balances || {};
      const usd = balances.USD !== undefined ? Number(balances.USD).toFixed(2) : "0.00";
      const diem = balances.DIEM !== undefined ? Number(balances.DIEM).toFixed(2) : "0.00";
      const bundled = balances.BUNDLED_CREDITS || 0;

      document.getElementById("val-usd-balance").innerText = `$${usd}`;
      document.getElementById("val-diem-balance").innerText = `${diem} DIEM`;
      document.getElementById("val-bundled-credits").innerText = bundled;
      document.getElementById("val-api-tier").innerText = (data.apiTier && data.apiTier.id) ? data.apiTier.id.toUpperCase() : "PAID";
      document.getElementById("val-keys-count").innerText = data.keys_count || 0;
      document.getElementById("val-tg-bots-count").innerText = data.agent_bots_count || 0;
      const primaryBotElem = document.getElementById("val-primary-bot");
      if (primaryBotElem) {
        primaryBotElem.innerText = data.agent_bots_count > 0 ? "Active" : "None";
      }

      // Admin key badge
      const adminBadge = document.getElementById("badge-admin-status");
      const adminBanner = document.getElementById("admin-alert-banner");
      if (data.has_admin_key) {
        adminBadge.innerText = "ADMIN: ACTIVE";
        adminBadge.className = "stat-badge badge-green";
        adminBanner.classList.add("hidden");
      } else {
        adminBadge.innerText = "ADMIN: UNCONFIGURED";
        adminBadge.className = "stat-badge badge-purple";
        adminBanner.classList.remove("hidden");
      }
    }
  } catch (err) {
    console.error("Failed to load stats:", err);
  }
}

async function loadKeys() {
  const tbody = document.getElementById("tbody-venice-keys");
  try {
    const res = await apiFetch(`/api/keys${getNodeQueryParam()}`);
    if (res.status === 401) {
      tbody.innerHTML = `<tr><td colspan="6" class="text-center text-muted" style="padding: 24px;">🔒 Machine management is locked. Enter the pairing code to view and manage keys.</td></tr>`;
      return;
    }
    const data = await res.json();
    if (data.success && data.keys) {
      appState.keys = data.keys;
      if (data.keys.length === 0) {
        tbody.innerHTML = `<tr><td colspan="6" class="text-center text-muted" style="padding: 24px;">No API keys stored in local vault. <button class="btn btn-sm btn-secondary" onclick="openModal('modal-create-key')" style="margin-left: 8px;">➕ Add / Import Key</button></td></tr>`;
        return;
      }
      tbody.innerHTML = data.keys.map(k => {
        const id = k.id || "N/A";
        const desc = k.description || "Venice Key";
        const type = k.apiKeyType || "INFERENCE";
        const tier = (k.maxModelTier || "xl").toUpperCase();
        let tierBadgeClass = "badge-purple";
        if (tier === "XS") tierBadgeClass = "badge-cyan";
        else if (tier === "S") tierBadgeClass = "badge-green";
        else if (tier === "M") tierBadgeClass = "badge-yellow";
        else if (tier === "L") tierBadgeClass = "badge-dim";
        else if (tier === "XL") tierBadgeClass = "badge-purple";

        const limitUsd = (k.consumptionLimits && k.consumptionLimits.usd) ? `$${k.consumptionLimits.usd}` : "Unlimited";
        const usage = (k.usage && k.usage.trailingDays && k.usage.trailingSevenDays.usd) ? `$${k.usage.trailingSevenDays.usd}` : (k.usage && k.usage.trailingSevenDays && k.usage.trailingSevenDays.usd) ? `$${k.usage.trailingSevenDays.usd}` : "$0.00";
        const created = k.createdAt ? new Date(k.createdAt).toLocaleDateString() : "Active";

        return `
          <tr>
            <td>
              <div style="font-weight: 600;">${escapeHtml(desc)}</div>
              <div class="text-muted text-mono" style="font-size: 11px;">${escapeHtml(id)}</div>
            </td>
            <td>
              <div style="display: flex; gap: 4px; flex-wrap: wrap;">
                <span class="badge ${type === 'ADMIN' ? 'badge-purple' : 'badge-cyan'}">${type}</span>
                <span class="badge ${tierBadgeClass}">TIER: ${tier}</span>
              </div>
            </td>
            <td>${limitUsd}</td>
            <td>${usage}</td>
            <td><span class="badge badge-green">${created}</span></td>
            <td>
              <div style="display: flex; gap: 6px;">
                <button class="btn btn-sm btn-secondary" onclick="deployKey('${id}')" title="Deploy to agent configuration">🚀 Deploy</button>
                <button class="btn btn-sm btn-danger" onclick="revokeKey('${id}')" title="Revoke Key">🗑️ Revoke</button>
              </div>
            </td>
          </tr>
        `;
      }).join("");
    }
  } catch (err) {
    tbody.innerHTML = `<tr><td colspan="6" class="text-center text-muted">Failed to load keys: ${err.message}</td></tr>`;
  }
}

async function loadSubkeys() {
  const tbody = document.getElementById("tbody-subkeys");
  if (!tbody) return;
  try {
    const res = await apiFetch(`/api/subkeys${getNodeQueryParam()}`);
    if (res.status === 401) {
      tbody.innerHTML = `<tr><td colspan="6" class="text-center text-muted" style="padding: 24px;">🔒 Machine management is locked. Enter the pairing code to view and mint sub-keys.</td></tr>`;
      return;
    }
    const data = await res.json();
    if (data.success && data.subkeys) {
      appState.subkeys = data.subkeys;
      if (data.subkeys.length === 0) {
        tbody.innerHTML = `<tr><td colspan="6" class="text-center text-muted" style="padding: 16px;">No agent sub-keys minted yet. <button class="btn btn-sm btn-secondary" onclick="openModal('modal-create-subkey')" style="margin-left: 8px;">🎟️ Mint Sub-Key</button></td></tr>`;
        return;
      }
      tbody.innerHTML = data.subkeys.map(sk => {
        const id = sk.id || "N/A";
        const label = sk.label || "Sub-Key";
        const tier = (sk.quality_tier || "s").toUpperCase();
        const budget = sk.budget_usd !== undefined ? `$${Number(sk.budget_usd).toFixed(2)}` : "$0.25";
        const period = sk.period || "DAY";
        const agent = sk.assigned_agent || "Unassigned";
        const node = sk.assigned_node || "local";
        const created = sk.created_at ? new Date(sk.created_at).toLocaleDateString() : "Active";

        let tierBadgeClass = "badge-green";
        if (tier === "XS") tierBadgeClass = "badge-cyan";
        else if (tier === "M") tierBadgeClass = "badge-yellow";
        else if (tier === "L") tierBadgeClass = "badge-dim";
        else if (tier === "XL") tierBadgeClass = "badge-purple";

        return `
          <tr>
            <td>
              <div style="font-weight: 600;">${escapeHtml(label)}</div>
              <div class="text-muted text-mono" style="font-size: 11px;">ID: ${escapeHtml(id)}</div>
            </td>
            <td><span class="badge ${tierBadgeClass}">TIER: ${tier}</span></td>
            <td><strong>${budget}</strong> <span class="text-dim" style="font-size: 11px;">/${period}</span></td>
            <td>
              <span class="badge ${agent !== 'Unassigned' ? 'badge-cyan' : 'badge-dim'}">${escapeHtml(agent)}</span>
              <span class="text-dim text-mono" style="font-size: 11px; margin-left: 4px;">(${escapeHtml(node)})</span>
            </td>
            <td><span class="badge badge-green">${created}</span></td>
            <td>
              <div style="display: flex; gap: 6px;">
                <button class="btn btn-sm btn-secondary" onclick="promptApplySubkey('${id}')" title="Deploy to agent">🚀 Apply</button>
              </div>
            </td>
          </tr>
        `;
      }).join("");
    }
  } catch (err) {
    tbody.innerHTML = `<tr><td colspan="6" class="text-center text-muted">Failed to load sub-keys: ${err.message}</td></tr>`;
  }
}

async function loadAgentBots() {
  const tbody = document.getElementById("tbody-tg-bots");
  try {
    const res = await apiFetch(`/api/agent_bots${getNodeQueryParam()}`);
    if (res.status === 401) {
      tbody.innerHTML = `<tr><td colspan="6" class="text-center text-muted" style="padding: 24px;">🔒 Machine management is locked. Enter the pairing code to view and manage agent bots.</td></tr>`;
      return;
    }
    const data = await res.json();
    if (data.success && data.agent_bots) {
      appState.agentBots = data.agent_bots;
      if (data.agent_bots.length === 0) {
        tbody.innerHTML = `<tr><td colspan="6" class="text-center text-muted">No agent Telegram bots registered. Use @BotFather Wizard or register a token.</td></tr>`;
        return;
      }
      tbody.innerHTML = data.agent_bots.map(b => {
        const agent = b.agent_name || "Unknown";
        const username = b.username ? `@${b.username}` : "Unlinked";
        const name = b.first_name || "Agent Bot";
        const configPath = b.config_path || "config.yaml";

        return `
          <tr>
            <td style="font-weight: 700;">${escapeHtml(agent)}</td>
            <td><a href="https://t.me/${b.username || ''}" target="_blank" class="text-cyan text-mono">${escapeHtml(username)}</a></td>
            <td>${escapeHtml(name)}</td>
            <td><span class="badge badge-green">VERIFIED</span></td>
            <td class="text-mono" style="font-size: 11px; max-width: 250px; overflow: hidden; text-overflow: ellipsis;">${escapeHtml(configPath)}</td>
            <td>
              <div style="display: flex; gap: 6px;">
                <button class="btn btn-sm btn-secondary" onclick="testAgentBot('${agent}')" title="Send test ping to chat">📡 Test</button>
                <button class="btn btn-sm btn-primary" onclick="deployAgentBot('${agent}')" title="Deploy token to config file">🚀 Deploy</button>
              </div>
            </td>
          </tr>
        `;
      }).join("");
    }
  } catch (err) {
    tbody.innerHTML = `<tr><td colspan="6" class="text-center text-muted">Failed to load agent bots: ${err.message}</td></tr>`;
  }
}

async function loadRateLimits() {
  const tbody = document.getElementById("tbody-rate-limits");
  tbody.innerHTML = `<tr><td colspan="2" class="text-center text-muted">Fetching live rate limits...</td></tr>`;
  try {
    const res = await apiFetch("/api/rate_limits");
    const data = await res.json();
    if (data.success && data.rateLimits) {
      appState.rateLimits = data.rateLimits;
      tbody.innerHTML = data.rateLimits.map(m => {
        const limitsStr = (m.rateLimits && m.rateLimits.length > 0)
          ? m.rateLimits.map(l => `${l.amount} ${l.type}`).join(" | ")
          : "Standard Tier Limit";
        return `
          <tr>
            <td class="text-mono" style="font-weight: 600;">${escapeHtml(m.apiModelId || '')}</td>
            <td><span class="badge badge-purple">${escapeHtml(limitsStr)}</span></td>
          </tr>
        `;
      }).join("");
    }
  } catch (err) {
    tbody.innerHTML = `<tr><td colspan="2" class="text-center text-muted">Error: ${err.message}</td></tr>`;
  }
}

async function loadConfig() {
  try {
    const res = await apiFetch("/api/config");
    const data = await res.json();
    if (data.success) {
      if (data.authorized_chat_id) {
        document.getElementById("cfg-chat-id").value = data.authorized_chat_id;
      }
    }
  } catch (err) {}
}

async function loadVaultStatus() {
  const elPrimary = document.getElementById("stat-primary-vault");
  const elMirror = document.getElementById("stat-backup-mirror");
  const elProfile = document.getElementById("stat-profile-mirror");
  const elLast = document.getElementById("stat-last-backup");
  const elCount = document.getElementById("val-backup-count");

  try {
    const res = await apiFetch("/api/vault/status");
    if (!res.ok) return;
    const data = await res.json();
    if (data.success) {
      if (elPrimary) {
        const kb = (data.vault_size_bytes / 1024).toFixed(1);
        elPrimary.innerHTML = `<span class="badge badge-green">ACTIVE</span> <span class="text-mono" style="font-size: 11px;">(${kb} KB)</span>`;
      }
      if (elMirror) {
        elMirror.innerHTML = data.backup_mirror_exists 
          ? `<span class="badge badge-green">SYNCED ✅</span>` 
          : `<span class="badge badge-yellow">MISSING ⚠️</span>`;
      }
      if (elProfile) {
        elProfile.innerHTML = data.user_profile_mirror_exists 
          ? `<span class="badge badge-green">SYNCED ✅</span>` 
          : `<span class="badge badge-yellow">STANDBY ⚠️</span>`;
      }
      if (elLast) {
        elLast.innerText = data.last_backup_at ? new Date(data.last_backup_at).toLocaleString() : "None";
      }
      if (elCount) {
        elCount.innerText = data.total_snapshots || 0;
      }
    }
  } catch (err) {
    console.warn("Failed to load vault backup status:", err);
  }
}

async function loadServiceStatus() {
  const elWatchdog = document.getElementById("stat-service-watchdog");
  const elAutostart = document.getElementById("stat-service-autostart");
  const elUptime = document.getElementById("stat-service-uptime");
  const elRestarts = document.getElementById("stat-service-restarts");

  try {
    const res = await apiFetch("/api/service/status");
    if (!res.ok) return;
    const data = await res.json();
    if (data.success) {
      if (elWatchdog) {
        if (data.supervisor_running) {
          elWatchdog.innerHTML = `<span class="badge badge-green">WATCHDOG ACTIVE ✅</span> <span class="text-mono" style="font-size: 11px;">(PID: ${data.supervisor_pid})</span>`;
        } else {
          elWatchdog.innerHTML = `<span class="badge badge-cyan">DIRECT PROCESS ⚡</span>`;
        }
      }
      if (elAutostart) {
        if (data.autostart_installed) {
          const methods = (data.autostart_methods || []).join(", ");
          elAutostart.innerHTML = `<span class="badge badge-green">BOOT ENABLED ✅</span> <span class="text-mono" style="font-size: 10px;">(${methods})</span>`;
        } else {
          elAutostart.innerHTML = `<span class="badge badge-yellow">NOT CONFIGURED ⚠️</span>`;
        }
      }
      if (elUptime) {
        const uptimeMin = (data.uptime_seconds / 60).toFixed(1);
        elUptime.innerText = `Port ${data.port} | Uptime: ${uptimeMin}m`;
      }
      if (elRestarts) {
        elRestarts.innerText = data.restarts_count || 0;
      }
    }
  } catch (err) {
    console.warn("Failed to load service status:", err);
  }
}

async function loadVaultBackups() {
  const tbody = document.getElementById("tbody-vault-backups");
  if (!tbody) return;
  tbody.innerHTML = `<tr><td colspan="6" class="text-center text-muted">Loading snapshots...</td></tr>`;

  try {
    const res = await apiFetch("/api/vault/backups");
    const data = await res.json();
    if (data.success && data.backups) {
      if (data.backups.length === 0) {
        tbody.innerHTML = `<tr><td colspan="6" class="text-center text-muted">No snapshots created yet.</td></tr>`;
        return;
      }
      tbody.innerHTML = data.backups.map(b => {
        const typeBadge = b.type === 'snapshot' 
          ? `<span class="badge badge-purple">SNAPSHOT</span>`
          : (b.type === 'mirror_local' ? `<span class="badge badge-cyan">LOCAL MIRROR</span>` : `<span class="badge badge-green">PROFILE MIRROR</span>`);
        const sizeKb = (b.size_bytes / 1024).toFixed(1);
        const dateStr = b.modified_at ? new Date(b.modified_at).toLocaleString() : "Unknown";
        const adminStr = b.has_admin_key ? "Admin: Yes" : "Admin: No";
        const infStr = b.has_inference_key ? "Inf: Yes" : "Inf: No";
        const subkeysCount = b.subkeys_count || 0;

        const safePath = JSON.stringify(b.path);

        return `
          <tr>
            <td>
              <div style="font-weight: 600; font-size: 13px;">${escapeHtml(b.filename)}</div>
              <div class="text-dim text-mono" style="font-size: 10px; max-width: 250px; overflow: hidden; text-overflow: ellipsis;">${escapeHtml(b.path)}</div>
            </td>
            <td>${typeBadge}</td>
            <td class="text-mono">${sizeKb} KB</td>
            <td>
              <div style="font-size: 12px;"><strong>${b.keys_count}</strong> keys / <strong>${subkeysCount}</strong> subkeys</div>
              <div class="text-dim" style="font-size: 11px;">${adminStr} | ${infStr}</div>
            </td>
            <td><span class="text-muted" style="font-size: 12px;">${dateStr}</span></td>
            <td>
              <button class="btn btn-sm btn-secondary" onclick='restoreVaultBackup(${safePath})' title="Restore vault to this snapshot">
                🔄 Restore
              </button>
            </td>
          </tr>
        `;
      }).join("");
    }
  } catch (err) {
    tbody.innerHTML = `<tr><td colspan="6" class="text-center text-muted">Error loading backups: ${err.message}</td></tr>`;
  }
}

async function restoreVaultBackup(path) {
  if (!confirm(`Are you sure you want to restore the vault from this backup?\nA safety snapshot of current state will be preserved before restoring.`)) return;
  showToast("Restoring vault snapshot...", "info");
  try {
    const res = await apiFetch("/api/vault/restore", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ path: path })
    });
    const data = await res.json();
    if (data.success) {
      showToast("Vault restored successfully!");
      closeModal("modal-vault-backups");
      refreshAll();
    } else {
      showToast(`Restore failed: ${data.error}`, "error");
    }
  } catch (err) {
    showToast(`Error: ${err.message}`, "error");
  }
}

// --- Key Actions ---

async function deployKey(keyId) {
  try {
    const res = await apiFetch("/api/deploy_key", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ key_string: keyId, target_node: appState.activeNode })
    });
    const data = await res.json();
    if (data.success) {
      showToast(`Key deployed to ${data.target}`);
    } else {
      showToast(`Deploy failed: ${data.error}`, "error");
    }
  } catch (err) {
    showToast(`Error: ${err.message}`, "error");
  }
}

async function revokeKey(keyId) {
  if (!confirm(`Are you sure you want to revoke key ${keyId}?`)) return;
  try {
    const res = await apiFetch("/api/revoke_key", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ key_id: keyId, target_node: appState.activeNode })
    });
    const data = await res.json();
    if (data.success) {
      showToast("Key revoked successfully");
      loadKeys();
    } else {
      showToast(`Revoke failed: ${data.error}`, "error");
    }
  } catch (err) {
    showToast(`Error: ${err.message}`, "error");
  }
}

// --- Agent Bot Actions ---

async function testAgentBot(agentName) {
  showToast(`Sending test ping from ${agentName}...`, "info");
  try {
    const res = await apiFetch("/api/agent_bots/test", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ agent_name: agentName, target_node: appState.activeNode })
    });
    const data = await res.json();
    if (data.success) {
      showToast(`Test ping delivered to chat! Message ID: ${data.message_id}`);
    } else {
      showToast(`Ping failed: ${data.error}`, "error");
    }
  } catch (err) {
    showToast(`Error: ${err.message}`, "error");
  }
}

async function deployAgentBot(agentName) {
  try {
    const res = await apiFetch("/api/agent_bots/deploy", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ agent_name: agentName, target_node: appState.activeNode })
    });
    const data = await res.json();
    if (data.success) {
      showToast(`Bot token deployed to ${data.target}`);
    } else {
      showToast(`Deploy failed: ${data.error}`, "error");
    }
  } catch (err) {
    showToast(`Error: ${err.message}`, "error");
  }
}

// --- Forms & Inputs ---

function initForms() {
  // Confirm Import Venice Key
  const btnConfirmImport = document.getElementById("btn-confirm-import-key");
  if (btnConfirmImport) {
    btnConfirmImport.addEventListener("click", async () => {
      const keyStr = document.getElementById("import-key-string").value.trim();
      const desc = document.getElementById("import-key-desc").value.trim() || "Imported Key";
      const kType = document.getElementById("import-key-type").value;
      const limit = document.getElementById("import-key-limit").value;
      const tierSelect = document.getElementById("import-key-tier");
      const tier = tierSelect ? tierSelect.value : "xl";

      if (!keyStr) {
        showToast("Please enter or paste a Venice API key", "error");
        document.getElementById("import-key-string").focus();
        return;
      }

      btnConfirmImport.disabled = true;
      btnConfirmImport.innerHTML = `<span>⏳</span> Verifying Key...`;

      try {
        const res = await apiFetch("/api/create_key", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            mode: "import",
            key_string: keyStr,
            description: desc,
            key_type: kType,
            max_model_tier: tier,
            limit_usd: limit ? parseFloat(limit) : null,
            target_node: appState.activeNode
          })
        });
        const data = await res.json();
        if (data.success) {
          showToast(`Key "${desc}" verified & added to vault!`);
          closeModal("modal-create-key");
          document.getElementById("import-key-string").value = "";
          document.getElementById("import-key-desc").value = "";
          document.getElementById("import-key-limit").value = "";
          loadKeys();
          loadStats();
        } else {
          showToast(`Import failed: ${data.error}`, "error");
        }
      } catch (err) {
        showToast(`Network error: ${err.message}`, "error");
      } finally {
        btnConfirmImport.disabled = false;
        btnConfirmImport.innerHTML = `<span>✅</span> Verify & Save Key`;
      }
    });
  }

  // Confirm Issue Venice Key (Remote Admin API)
  const btnConfirmCreate = document.getElementById("btn-confirm-create-key");
  if (btnConfirmCreate) {
    btnConfirmCreate.addEventListener("click", async () => {
      const desc = document.getElementById("new-key-desc").value.trim() || "Agent Key";
      const kType = document.getElementById("new-key-type").value;
      const limit = document.getElementById("new-key-limit").value;
      const period = document.getElementById("new-key-period").value;
      const tierSelect = document.getElementById("new-key-tier");
      const tier = tierSelect ? tierSelect.value : "xl";
      const adminKeyInput = document.getElementById("modal-admin-key-input");
      const adminKey = adminKeyInput ? adminKeyInput.value.trim() : "";

      btnConfirmCreate.disabled = true;
      btnConfirmCreate.innerHTML = `<span>⏳</span> Generating via Venice...`;

      try {
        const payload = {
          mode: "generate",
          description: desc,
          key_type: kType,
          max_model_tier: tier,
          limit_usd: limit ? parseFloat(limit) : null,
          limit_period: period,
          target_node: appState.activeNode
        };
        if (adminKey) payload.admin_key = adminKey;

        const res = await apiFetch("/api/create_key", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(payload)
        });
        const data = await res.json();
        if (data.success) {
          showToast("Venice key generated successfully!");
          closeModal("modal-create-key");
          document.getElementById("new-key-desc").value = "";
          document.getElementById("new-key-limit").value = "";
          if (adminKeyInput) adminKeyInput.value = "";
          loadKeys();
          loadStats();
        } else {
          showToast(`Creation failed: ${data.error}`, "error");
        }
      } catch (err) {
        showToast(`Network error: ${err.message}`, "error");
      } finally {
        btnConfirmCreate.disabled = false;
        btnConfirmCreate.innerHTML = `<span>⚡</span> Generate Key via Venice`;
      }
    });
  }

  // Confirm Register Agent Bot
  document.getElementById("btn-confirm-add-bot").addEventListener("click", async () => {
    const agent = document.getElementById("new-bot-agent").value.trim();
    const token = document.getElementById("new-bot-token").value.trim();
    const configPath = document.getElementById("new-bot-config").value.trim();
    const notes = document.getElementById("new-bot-notes").value.trim();

    if (!agent || !token) {
      showToast("Agent Name and Bot Token are required", "error");
      return;
    }

    try {
      const res = await apiFetch("/api/agent_bots/register", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ agent_name: agent, bot_token: token, config_path: configPath, notes: notes, target_node: appState.activeNode })
      });
      const data = await res.json();
      if (data.success) {
        showToast(`Registered bot @${data.bot.username} for agent ${agent}!`);
        closeModal("modal-add-bot");
        loadAgentBots();
        loadStats();
      } else {
        showToast(`Registration failed: ${data.error}`, "error");
      }
    } catch (err) {
      showToast(`Error: ${err.message}`, "error");
    }
  });

  // Wizard Token Register
  document.getElementById("btn-wizard-register").addEventListener("click", async () => {
    const token = document.getElementById("wizard-token-input").value.trim();
    const agent = document.getElementById("wizard-agent-name").value.trim() || "hermes-agent";

    if (!token) {
      showToast("Please paste the bot token from @BotFather", "error");
      return;
    }

    try {
      const res = await apiFetch("/api/agent_bots/register", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ agent_name: agent, bot_token: token, target_node: appState.activeNode })
      });
      const data = await res.json();
      if (data.success) {
        showToast(`Bot @${data.bot.username} verified and saved to vault!`);
        closeModal("modal-botfather-wizard");
        loadAgentBots();
        loadStats();
      } else {
        showToast(`Failed: ${data.error}`, "error");
      }
    } catch (err) {
      showToast(`Error: ${err.message}`, "error");
    }
  });

  // Save Venice Master Keys
  document.getElementById("btn-save-venice-keys").addEventListener("click", async () => {
    const adminKey = document.getElementById("cfg-admin-key").value.trim();
    const infKey = document.getElementById("cfg-inf-key").value.trim();
    const payload = {};
    if (adminKey) payload.admin_key = adminKey;
    if (infKey) payload.inference_key = infKey;

    try {
      const res = await apiFetch("/api/config", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload)
      });
      const data = await res.json();
      if (data.success) {
        showToast("Venice keys updated in vault!");
        document.getElementById("cfg-admin-key").value = "";
        document.getElementById("cfg-inf-key").value = "";
        refreshAll();
      }
    } catch (err) {
      showToast(`Error: ${err.message}`, "error");
    }
  });

  // Save TG Settings
  document.getElementById("btn-save-tg-settings").addEventListener("click", async () => {
    const chatId = document.getElementById("cfg-chat-id").value.trim();
    try {
      const res = await apiFetch("/api/config", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ authorized_chat_id: chatId })
      });
      const data = await res.json();
      if (data.success) {
        showToast("Telegram chat settings saved!");
      }
    } catch (err) {
      showToast(`Error: ${err.message}`, "error");
    }
  });

  // Save Admin Key directly from top alert banner
  const btnSaveBannerAdmin = document.getElementById("btn-save-banner-admin-key");
  const inputBannerAdmin = document.getElementById("banner-admin-key-input");

  if (btnSaveBannerAdmin && inputBannerAdmin) {
    const submitBannerAdminKey = async () => {
      const key = inputBannerAdmin.value.trim();
      if (!key) {
        showToast("Please enter or paste your Venice Admin Key", "error");
        inputBannerAdmin.focus();
        return;
      }

      btnSaveBannerAdmin.disabled = true;
      btnSaveBannerAdmin.innerHTML = `<span>⏳</span> Saving...`;

      try {
        const res = await apiFetch("/api/config", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ admin_key: key })
        });
        const data = await res.json();
        if (data.success) {
          showToast("Venice Admin Key saved! Remote key issuance unlocked.");
          inputBannerAdmin.value = "";
          refreshAll();
        } else {
          showToast(`Failed: ${data.error}`, "error");
        }
      } catch (err) {
        showToast(`Error: ${err.message}`, "error");
      } finally {
        btnSaveBannerAdmin.disabled = false;
        btnSaveBannerAdmin.innerHTML = `<span>💾</span> Save Admin Key`;
      }
    };

    btnSaveBannerAdmin.addEventListener("click", submitBannerAdminKey);
    inputBannerAdmin.addEventListener("keydown", (e) => {
      if (e.key === "Enter") {
        e.preventDefault();
        submitBannerAdminKey();
      }
    });
  }

  // Backup & Disaster Recovery Buttons
  const btnCreateBackup = document.getElementById("btn-create-backup");
  if (btnCreateBackup) {
    btnCreateBackup.addEventListener("click", async () => {
      btnCreateBackup.disabled = true;
      btnCreateBackup.innerHTML = `<span>⏳</span> Backing up...`;
      try {
        const res = await apiFetch("/api/vault/backup", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ label: "web_ui" })
        });
        const data = await res.json();
        if (data.success) {
          showToast(`Snapshot created! (${data.size_bytes} B)`);
          loadVaultStatus();
        } else {
          showToast(`Backup failed: ${data.error}`, "error");
        }
      } catch (err) {
        showToast(`Error: ${err.message}`, "error");
      } finally {
        btnCreateBackup.disabled = false;
        btnCreateBackup.innerHTML = `<span>💾</span> Create Instant Backup`;
      }
    });
  }

  const triggerAutoRecall = async (btn) => {
    if (btn) {
      btn.disabled = true;
      btn.innerHTML = `<span>⏳</span> Recalling...`;
    }
    showToast("Running deep auto-recall across backup stores...", "info");
    try {
      const res = await apiFetch("/api/vault/recall", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ sync_remote: true })
      });
      const data = await res.json();
      if (data.success) {
        const adminMsg = data.recovered_admin_key ? "Admin Key Recovered! " : "";
        const infMsg = data.recovered_inference_key ? "Inference Key Recovered! " : "";
        const syncMsg = data.remote_keys_synced > 0 ? `${data.remote_keys_synced} remote keys synced! ` : "";
        showToast(`Auto-recall complete! ${adminMsg}${infMsg}${syncMsg}Keys verified.`);
        refreshAll();
      } else {
        showToast(`Recall failed: ${data.error}`, "error");
      }
    } catch (err) {
      showToast(`Error: ${err.message}`, "error");
    } finally {
      if (btn) {
        btn.disabled = false;
        btn.innerHTML = `<span>🔄</span> Auto-Recall & Recover Keys`;
      }
    }
  };

  const btnAutoRecall = document.getElementById("btn-auto-recall-vault");
  if (btnAutoRecall) {
    btnAutoRecall.addEventListener("click", () => triggerAutoRecall(btnAutoRecall));
  }

  const btnModalRecall = document.getElementById("btn-modal-auto-recall");
  if (btnModalRecall) {
    btnModalRecall.addEventListener("click", () => triggerAutoRecall(btnModalRecall));
  }

  const btnViewBackups = document.getElementById("btn-view-backups");
  if (btnViewBackups) {
    btnViewBackups.addEventListener("click", () => {
      openModal("modal-vault-backups");
      loadVaultBackups();
    });
  }

  // Service Install & Restart
  const btnSvcInstall = document.getElementById("btn-service-install");
  if (btnSvcInstall) {
    btnSvcInstall.addEventListener("click", async () => {
      btnSvcInstall.disabled = true;
      btnSvcInstall.innerHTML = `<span>⏳</span> Installing...`;
      try {
        const res = await apiFetch("/api/service/install", { method: "POST" });
        const data = await res.json();
        if (data.success) {
          showToast("Auto-restart service installed for boot!");
          loadServiceStatus();
        } else {
          showToast(`Install notice: ${JSON.stringify(data.messages || data.error)}`, "warning");
        }
      } catch (err) {
        showToast(`Error: ${err.message}`, "error");
      } finally {
        btnSvcInstall.disabled = false;
        btnSvcInstall.innerHTML = `<span>🔧</span> Re-install Boot Service`;
      }
    });
  }

  const btnSvcRestart = document.getElementById("btn-service-restart");
  if (btnSvcRestart) {
    btnSvcRestart.addEventListener("click", async () => {
      if (!confirm("Recycle and restart the Venice Key Manager service now?")) return;
      btnSvcRestart.disabled = true;
      btnSvcRestart.innerHTML = `<span>⏳</span> Restarting...`;
      try {
        const res = await apiFetch("/api/service/restart", { method: "POST" });
        const data = await res.json();
        if (data.success) {
          showToast("Restart signal dispatched! Service is recycling...");
          setTimeout(refreshAll, 2500);
        } else {
          showToast(`Restart failed: ${data.error}`, "error");
        }
      } catch (err) {
        showToast(`Error: ${err.message}`, "error");
      } finally {
        btnSvcRestart.disabled = false;
        btnSvcRestart.innerHTML = `<span>🔄</span> Recycle / Restart Service`;
      }
    });
  }
}

// --- Inference Sandbox ---

function initInference() {
  const maxTokensSlider = document.getElementById("inf-max-tokens");
  const tempSlider = document.getElementById("inf-temp");
  const lblMaxTokens = document.getElementById("lbl-max-tokens");
  const lblTemp = document.getElementById("lbl-temp");

  maxTokensSlider.addEventListener("input", () => {
    lblMaxTokens.innerText = maxTokensSlider.value;
  });

  tempSlider.addEventListener("input", () => {
    lblTemp.innerText = tempSlider.value;
  });

  document.getElementById("btn-run-inference").addEventListener("click", runInference);
}

async function runInference() {
  const prompt = document.getElementById("inf-prompt").value.trim();
  const model = document.getElementById("inf-model").value;
  const maxTokens = parseInt(document.getElementById("inf-max-tokens").value);
  const temp = parseFloat(document.getElementById("inf-temp").value);

  if (!prompt) {
    showToast("Please enter a prompt", "error");
    return;
  }

  const btn = document.getElementById("btn-run-inference");
  const btnText = document.getElementById("inf-btn-text");
  const spinner = document.getElementById("inf-spinner");
  const resultBox = document.getElementById("inf-result");
  const reasoningBox = document.getElementById("inf-reasoning");
  const reasoningContent = document.getElementById("inf-reasoning-content");

  btnText.innerText = "Generating...";
  btn.disabled = true;
  resultBox.innerHTML = `<span class="text-muted">Waiting for Venice stream...</span>`;
  reasoningBox.classList.add("hidden");

  try {
    const res = await apiFetch("/api/infer", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        prompt: prompt,
        model: model,
        max_tokens: maxTokens,
        temperature: temp
      })
    });
    const data = await res.json();
    if (data.success) {
      resultBox.innerText = data.content || "(Empty content response)";
      document.getElementById("metric-latency").innerText = `Latency: ${data.latency_ms} ms`;

      const usage = data.usage || {};
      document.getElementById("metric-tokens").innerText = `Tokens: ${usage.total_tokens || 0}`;

      const cost = data.cost || {};
      const costUsd = cost.usd !== undefined ? `$${cost.usd.toFixed(5)}` : "$0.00";
      document.getElementById("metric-cost").innerText = `Cost: ${costUsd}`;

      if (data.reasoning) {
        reasoningContent.innerText = data.reasoning;
        reasoningBox.classList.remove("hidden");
      }
    } else {
      resultBox.innerHTML = `<span style="color: var(--accent-red);">Inference Error: ${escapeHtml(data.error || 'Unknown error')}</span>`;
      document.getElementById("metric-latency").innerText = `Latency: ${data.latency_ms || 0} ms`;
    }
  } catch (err) {
    resultBox.innerHTML = `<span style="color: var(--accent-red);">Network Error: ${escapeHtml(err.message)}</span>`;
  } finally {
    btnText.innerText = "⚡ Run Inference";
    btn.disabled = false;
  }
}

// Helpers
function escapeHtml(str) {
  if (!str) return "";
  return String(str)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#039;");
}

// --- Fleet Mesh & Multi-Machine Management ---

function initFleet() {
  const selNode = document.getElementById("select-active-node");
  if (selNode) {
    selNode.addEventListener("change", (e) => {
      switchActiveNode(e.target.value);
    });
  }

  const btnSyncCode = document.getElementById("btn-sync-code");
  if (btnSyncCode) {
    btnSyncCode.addEventListener("click", () => {
      syncNodeGit(appState.activeNode);
    });
  }

  const btnSyncAll = document.getElementById("btn-fleet-sync-all");
  if (btnSyncAll) {
    btnSyncAll.addEventListener("click", () => {
      syncNodeGit("all");
    });
  }

  const btnAddNode = document.getElementById("btn-add-fleet-node");
  if (btnAddNode) {
    btnAddNode.addEventListener("click", () => {
      openModal("modal-add-node");
    });
  }

  const btnConfirmAdd = document.getElementById("btn-confirm-add-node");
  if (btnConfirmAdd) {
    btnConfirmAdd.addEventListener("click", handleRegisterNode);
  }
}

async function loadFleetNodes() {
  const tbody = document.getElementById("tbody-fleet-nodes");
  const selNode = document.getElementById("select-active-node");

  try {
    const res = await apiFetch("/api/fleet/nodes?probe=1");
    const data = await res.json();
    if (data.success && data.nodes) {
      appState.fleetNodes = data.nodes;

      // Update dropdown options
      if (selNode) {
        const currentVal = appState.activeNode || "local";
        selNode.innerHTML = data.nodes.map(n => {
          const isSelected = n.name === currentVal ? "selected" : "";
          const statusIcon = n.status === "online" ? "🟢" : "⚪";
          return `<option value="${escapeHtml(n.name)}" ${isSelected}>${statusIcon} ${escapeHtml(n.label || n.name)}</option>`;
        }).join("");
        selNode.value = currentVal;
      }

      // Update fleet table
      if (tbody) {
        tbody.innerHTML = data.nodes.map(n => {
          const isLocal = n.name === "local";
          const isOnline = n.status === "online";
          const statusBadge = isOnline 
            ? `<span class="badge badge-green">ONLINE</span>` 
            : `<span class="badge badge-dim">OFFLINE / UNREACHABLE</span>`;
          const latency = n.latency_ms !== null && n.latency_ms !== undefined ? `${n.latency_ms} ms` : "--";
          const version = n.version || {};
          const commit = version.commit ? version.commit.substring(0, 7) : (isLocal ? "Local Repo" : "Unknown");
          const branch = version.branch || "main";

          return `
            <tr>
              <td>
                <div style="font-weight: 700;">${escapeHtml(n.label || n.name)} ${isLocal ? '<span class="badge badge-purple" style="margin-left: 4px;">HOST</span>' : ''}</div>
                <div class="text-dim text-mono" style="font-size: 11px;">ID: ${escapeHtml(n.name)}</div>
              </td>
              <td class="text-mono" style="font-size: 12px;">${escapeHtml(n.base_url || n.ip || 'localhost')}</td>
              <td>${statusBadge}</td>
              <td><span class="text-mono">${escapeHtml(latency)}</span></td>
              <td>
                <span class="badge badge-cyan text-mono">${escapeHtml(branch)}@${escapeHtml(commit)}</span>
              </td>
              <td>
                <div style="display: flex; gap: 6px;">
                  <button class="btn btn-sm btn-secondary" onclick="switchActiveNode('${escapeHtml(n.name)}')" title="Switch dashboard view to this machine">
                    ${appState.activeNode === n.name ? '👁️ Active' : '🔄 Connect'}
                  </button>
                  <button class="btn btn-sm btn-secondary" onclick="syncNodeGit('${escapeHtml(n.name)}')" title="Git pull latest code on this node">
                    📥 Pull Git
                  </button>
                  ${!isLocal ? `<button class="btn btn-sm btn-danger" onclick="removeFleetNode('${escapeHtml(n.name)}')" title="Remove node from mesh">🗑️</button>` : ''}
                </div>
              </td>
            </tr>
          `;
        }).join("");
      }
    }
  } catch (err) {
    if (tbody) {
      tbody.innerHTML = `<tr><td colspan="6" class="text-center text-muted">Failed to scan fleet nodes: ${err.message}</td></tr>`;
    }
  }
}

function switchActiveNode(nodeName) {
  appState.activeNode = nodeName;
  const selNode = document.getElementById("select-active-node");
  if (selNode) selNode.value = nodeName;

  const nodeObj = appState.fleetNodes.find(n => n.name === nodeName);
  const label = nodeObj ? (nodeObj.label || nodeObj.name) : nodeName;

  showToast(`Switched active node to: ${label}`, "info");
  refreshAll();
}

async function syncNodeGit(target) {
  const targetLabel = target === "all" ? "all fleet nodes" : target;
  showToast(`Triggering Git pull on ${targetLabel}...`, "info");

  const btnSyncCode = document.getElementById("btn-sync-code");
  if (btnSyncCode) {
    btnSyncCode.disabled = true;
  }

  try {
    const res = await apiFetch("/api/fleet/sync", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ node: target })
    });
    const data = await res.json();
    if (data.success) {
      if (target === "all") {
        showToast("Git sync triggered across all online nodes!");
      } else {
        const commit = data.commit_after ? data.commit_after.substring(0, 7) : "latest";
        showToast(`Sync complete on ${target}! Now on commit ${commit}`);
      }
      loadFleetNodes();
    } else {
      showToast(`Git sync failed: ${data.error || 'Check server logs'}`, "error");
    }
  } catch (err) {
    showToast(`Error syncing Git: ${err.message}`, "error");
  } finally {
    if (btnSyncCode) {
      btnSyncCode.disabled = false;
    }
  }
}

async function handleRegisterNode() {
  const nameInput = document.getElementById("new-node-name");
  const urlInput = document.getElementById("new-node-url");
  const labelInput = document.getElementById("new-node-label");

  const name = nameInput.value.trim().toLowerCase();
  const url = urlInput.value.trim();
  const label = labelInput.value.trim() || name;

  if (!name || !url) {
    showToast("Node Name and Base URL are required", "error");
    return;
  }

  try {
    const res = await apiFetch("/api/fleet/register_node", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name: name, base_url: url, label: label })
    });
    const data = await res.json();
    if (data.success) {
      showToast(`Node "${label}" paired with mesh!`);
      closeModal("modal-add-node");
      nameInput.value = "";
      urlInput.value = "";
      labelInput.value = "";
      loadFleetNodes();
    } else {
      showToast(`Failed to pair node: ${data.error}`, "error");
    }
  } catch (err) {
    showToast(`Error: ${err.message}`, "error");
  }
}

async function removeFleetNode(name) {
  if (!confirm(`Are you sure you want to remove node "${name}" from your fleet mesh?`)) return;

  try {
    const res = await apiFetch("/api/fleet/remove_node", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name: name })
    });
    const data = await res.json();
    if (data.success) {
      showToast(`Node "${name}" removed from mesh`);
      if (appState.activeNode === name) {
        switchActiveNode("local");
      }
      loadFleetNodes();
    } else {
      showToast(`Failed to remove node`, "error");
    }
  } catch (err) {
    showToast(`Error: ${err.message}`, "error");
  }
}

// --- Node Pairing & Guest Validation & Subkeys ---

function initPairingAndSubkeys() {
  const btnTogglePair = document.getElementById("btn-toggle-pair");
  if (btnTogglePair) btnTogglePair.addEventListener("click", handlePairToggle);

  const btnOpenPair = document.getElementById("btn-open-pair-modal");
  if (btnOpenPair) btnOpenPair.addEventListener("click", () => openModal("modal-pair-code"));

  const btnSubmitPair = document.getElementById("btn-submit-pair-code");
  if (btnSubmitPair) btnSubmitPair.addEventListener("click", handlePairCodeSubmit);

  const inputPair = document.getElementById("input-pair-code");
  if (inputPair) {
    inputPair.addEventListener("keydown", (e) => {
      if (e.key === "Enter") handlePairCodeSubmit();
    });
  }

  const btnGuestVal = document.getElementById("btn-guest-validate-key");
  if (btnGuestVal) btnGuestVal.addEventListener("click", handleGuestValidateKey);

  const inputGuest = document.getElementById("input-guest-key");
  if (inputGuest) {
    inputGuest.addEventListener("keydown", (e) => {
      if (e.key === "Enter") handleGuestValidateKey();
    });
  }

  const btnToggleGuestPw = document.getElementById("btn-toggle-guest-pw");
  if (btnToggleGuestPw && inputGuest) {
    btnToggleGuestPw.addEventListener("click", () => {
      const isPw = inputGuest.type === "password";
      inputGuest.type = isPw ? "text" : "password";
      btnToggleGuestPw.innerText = isPw ? "🙈" : "👁️";
    });
  }

  const btnConfirmSubkey = document.getElementById("btn-confirm-create-subkey");
  if (btnConfirmSubkey) btnConfirmSubkey.addEventListener("click", handleCreateSubkey);

  const btnConfirmAlloc = document.getElementById("btn-confirm-mint-alloc");
  if (btnConfirmAlloc) btnConfirmAlloc.addEventListener("click", handleMintAllocation);
}

function handlePairToggle() {
  if (appState.authenticated) {
    if (confirm("Do you want to unpair this browser session? Machine management will be locked.")) {
      sessionStorage.removeItem("venice_pairing_code");
      appState.authenticated = false;
      updatePairingUI(false);
      showToast("Machine session unpaired. Access locked to guest mode.", "info");
      refreshAll();
    }
  } else {
    openModal("modal-pair-code");
  }
}

async function handlePairCodeSubmit() {
  const input = document.getElementById("input-pair-code");
  const errDiv = document.getElementById("pair-code-error");
  const code = input.value.trim();
  if (!code) {
    errDiv.innerText = "Please enter a pairing code.";
    errDiv.style.display = "block";
    return;
  }

  errDiv.style.display = "none";
  const btn = document.getElementById("btn-submit-pair-code");
  btn.disabled = true;
  btn.innerHTML = `<span>⏳</span> Verifying...`;

  try {
    const res = await fetch("/api/pair", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ code: code })
    });
    const data = await res.json();
    if (data.success && data.authenticated) {
      sessionStorage.setItem("venice_pairing_code", code);
      appState.authenticated = true;
      closeModal("modal-pair-code");
      input.value = "";
      updatePairingUI(true);
      showToast("Machine paired successfully! Full access unlocked.");
      refreshAll();
    } else {
      errDiv.innerText = data.error || "Invalid pairing code. Please check and try again.";
      errDiv.style.display = "block";
    }
  } catch (err) {
    errDiv.innerText = `Network error: ${err.message}`;
    errDiv.style.display = "block";
  } finally {
    btn.disabled = false;
    btn.innerHTML = `<span>🔓</span> Authenticate & Unlock`;
  }
}

async function handleGuestValidateKey() {
  const input = document.getElementById("input-guest-key");
  const resultDiv = document.getElementById("guest-validate-result");
  const key = input.value.trim();
  if (!key) {
    showToast("Please enter a Venice API key to validate", "error");
    input.focus();
    return;
  }

  const btn = document.getElementById("btn-guest-validate-key");
  btn.disabled = true;
  btn.innerHTML = `<span>⏳</span> Testing Key...`;
  resultDiv.style.display = "block";
  resultDiv.innerHTML = `<div class="text-muted" style="padding: 10px;">Validating key and checking balances with Venice.ai...</div>`;

  try {
    const res = await fetch("/api/validate_key", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ key: key })
    });
    const data = await res.json();
    if (data.success && data.valid) {
      const balances = data.balances || {};
      const usd = balances.USD !== undefined ? Number(balances.USD).toFixed(2) : "0.00";
      const diem = balances.DIEM !== undefined ? Number(balances.DIEM).toFixed(2) : "0.00";
      const bundled = balances.BUNDLED_CREDITS || 0;
      const tier = (data.tier || "s").toUpperCase();
      const latency = data.latency_ms || "--";
      const masked = data.masked_key || (key.substring(0, 8) + "..." + key.substring(key.length - 4));

      resultDiv.innerHTML = `
        <div style="background: rgba(16, 185, 129, 0.08); border: 1px solid rgba(16, 185, 129, 0.4); border-radius: var(--radius-md); padding: 14px;">
          <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 10px; flex-wrap: wrap; gap: 8px;">
            <div style="display: flex; align-items: center; gap: 8px;">
              <span class="badge badge-green">VALID KEY</span>
              <span class="badge badge-purple">TIER: ${escapeHtml(tier)}</span>
              <span class="text-mono" style="font-size: 12px; color: var(--text-main); font-weight: 600;">${escapeHtml(masked)}</span>
            </div>
            <div class="text-dim text-mono" style="font-size: 11px;">Ping: ${latency} ms</div>
          </div>
          <div style="display: grid; grid-template-columns: repeat(auto-fit, minmax(140px, 1fr)); gap: 12px; margin-bottom: 10px;">
            <div style="background: var(--bg-card); padding: 8px 12px; border-radius: var(--radius-sm); border: 1px solid var(--border-color);">
              <div class="text-dim" style="font-size: 11px;">USD BALANCE</div>
              <div style="font-size: 16px; font-weight: 700; color: #10b981;">$${usd}</div>
            </div>
            <div style="background: var(--bg-card); padding: 8px 12px; border-radius: var(--radius-sm); border: 1px solid var(--border-color);">
              <div class="text-dim" style="font-size: 11px;">DIEM BALANCE</div>
              <div style="font-size: 16px; font-weight: 700; color: #a855f7;">${diem} DIEM</div>
            </div>
            <div style="background: var(--bg-card); padding: 8px 12px; border-radius: var(--radius-sm); border: 1px solid var(--border-color);">
              <div class="text-dim" style="font-size: 11px;">BUNDLED CREDITS</div>
              <div style="font-size: 16px; font-weight: 700; color: #06b6d4;">${bundled}</div>
            </div>
          </div>
          <p style="font-size: 12px; color: var(--text-muted); margin: 0;">
            ✅ Key verified and responsive! To assign this key to autonomous agents or mint sub-keys, unlock this node above using your pairing code.
          </p>
        </div>
      `;
    } else {
      resultDiv.innerHTML = `
        <div style="background: rgba(239, 68, 68, 0.08); border: 1px solid rgba(239, 68, 68, 0.4); border-radius: var(--radius-md); padding: 12px;">
          <div style="display: flex; align-items: center; gap: 8px; margin-bottom: 4px;">
            <span class="badge badge-danger">INVALID KEY</span>
            <span style="font-size: 12px; color: var(--accent-red); font-weight: 600;">${escapeHtml(data.error || 'Failed to authenticate key with Venice API')}</span>
          </div>
          <div class="text-dim" style="font-size: 11px;">Check that your key is active and formatted correctly.</div>
        </div>
      `;
    }
  } catch (err) {
    resultDiv.innerHTML = `
      <div style="background: rgba(239, 68, 68, 0.08); border: 1px solid rgba(239, 68, 68, 0.4); border-radius: var(--radius-md); padding: 12px;">
        <span class="badge badge-danger">ERROR</span>
        <span style="font-size: 12px; color: var(--accent-red); margin-left: 8px;">Network error: ${escapeHtml(err.message)}</span>
      </div>
    `;
  } finally {
    btn.disabled = false;
    btn.innerHTML = `<span>⚡</span> Test & Validate Key`;
  }
}

async function handleCreateSubkey() {
  const labelInput = document.getElementById("subkey-label");
  const tierInput = document.getElementById("subkey-quality-tier");
  const budgetInput = document.getElementById("subkey-budget");
  const periodInput = document.getElementById("subkey-period");
  const agentInput = document.getElementById("subkey-target-agent");
  const nodeInput = document.getElementById("subkey-target-node");

  const label = labelInput.value.trim();
  if (!label) {
    showToast("Please enter a label for the sub-key", "error");
    labelInput.focus();
    return;
  }

  const budget = parseFloat(budgetInput.value) || 0.25;
  const tier = tierInput.value || "s";
  const period = periodInput.value || "DAY";
  const targetAgent = agentInput.value || null;
  const targetNode = nodeInput.value || "local";

  const btn = document.getElementById("btn-confirm-create-subkey");
  btn.disabled = true;
  btn.innerHTML = `<span>⏳</span> Minting...`;

  try {
    const res = await apiFetch("/api/subkeys/create", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        label: label,
        budget_usd: budget,
        quality_tier: tier,
        period: period,
        target_agent: targetAgent,
        target_node: targetNode
      })
    });
    const data = await res.json();
    if (data.success) {
      showToast(`Sub-key "${label}" minted successfully! (Tier: ${tier.toUpperCase()}, Budget: $${budget})`);
      closeModal("modal-create-subkey");
      labelInput.value = "";
      budgetInput.value = "0.25";
      loadSubkeys();
      loadKeys();
    } else {
      showToast(`Failed to mint sub-key: ${data.error}`, "error");
    }
  } catch (err) {
    showToast(`Error: ${err.message}`, "error");
  } finally {
    btn.disabled = false;
    btn.innerHTML = `<span>⚡</span> Mint & Deploy Sub-Key`;
  }
}

async function promptApplySubkey(subkeyId) {
  const agent = prompt("Enter agent name to deploy sub-key to (e.g. hermes-music, a2a-node, dawagent, worker-audio):", "hermes-music");
  if (!agent) return;

  try {
    const res = await apiFetch("/api/subkeys/apply", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        subkey_id: subkeyId,
        agent_name: agent.trim(),
        target_node: appState.activeNode
      })
    });
    const data = await res.json();
    if (data.success) {
      showToast(`Sub-key deployed to agent "${agent}" on ${data.target_node}!`);
      loadSubkeys();
    } else {
      showToast(`Deploy failed: ${data.error}`, "error");
    }
  } catch (err) {
    showToast(`Error: ${err.message}`, "error");
  }
}

// --- Agent Key Allocations (venice.vmu.cash) ---

async function loadAllocations() {
  const tbody = document.getElementById("tbody-allocations");
  if (!tbody) return;

  try {
    const res = await apiFetch(`/api/allocations${getNodeQueryParam()}`);
    if (res.status === 401) {
      tbody.innerHTML = `<tr><td colspan="7" class="text-center text-muted" style="padding: 24px;">🔒 Enter pairing code to view agent key allocations.</td></tr>`;
      return;
    }
    const data = await res.json();
    if (data.success && data.allocations) {
      appState.allocations = data.allocations;
      if (data.allocations.length === 0) {
        tbody.innerHTML = `<tr><td colspan="7" class="text-center text-muted">No key allocations minted yet. Tap "Mint Claim Link" to create one.</td></tr>`;
        return;
      }
      tbody.innerHTML = data.allocations.map(alloc => {
        const id = alloc.id || "N/A";
        const label = alloc.label || "Key Allocation";
        const agent = alloc.target_agent || "agent";
        const node = alloc.target_node || "local";
        const claimUrl = alloc.claim_url || `https://venice.vmu.cash/claim/${alloc.claim_token}`;
        const token = alloc.claim_token || "";
        const allocated = alloc.allocated_keys_count || 1;
        const remaining = alloc.remaining_claims !== undefined ? alloc.remaining_claims : allocated;
        const tier = (alloc.quality_tier || "s").toUpperCase();
        const budget = alloc.budget_usd !== undefined ? `$${Number(alloc.budget_usd).toFixed(2)}` : "$0.25";
        const period = alloc.limit_period || "DAY";
        const status = alloc.status || "ACTIVE";
        const vFrom = alloc.valid_from ? new Date(alloc.valid_from).toLocaleDateString() : "Immediate";
        const vUntil = alloc.valid_until ? new Date(alloc.valid_until).toLocaleDateString() : "No Expiry";

        let statusBadge = "badge-green";
        if (status === "PENDING") statusBadge = "badge-yellow";
        else if (status === "CLAIMED") statusBadge = "badge-purple";
        else if (status === "EXPIRED") statusBadge = "badge-dim";
        else if (status === "REVOKED") statusBadge = "badge-danger";

        let tierBadgeClass = "badge-green";
        if (tier === "XS") tierBadgeClass = "badge-cyan";
        else if (tier === "M") tierBadgeClass = "badge-yellow";
        else if (tier === "L") tierBadgeClass = "badge-dim";
        else if (tier === "XL") tierBadgeClass = "badge-purple";

        return `
          <tr>
            <td>
              <div style="font-weight: 700; color: #f8fafc;">${escapeHtml(label)}</div>
              <div style="display: flex; align-items: center; gap: 6px; margin-top: 4px;">
                <span class="badge badge-purple" style="font-size: 11px;">${escapeHtml(agent)}</span>
                <span class="text-dim text-mono" style="font-size: 11px;">(${escapeHtml(node)})</span>
              </div>
            </td>
            <td>
              <div style="display: flex; align-items: center; gap: 8px;">
                <a href="${escapeHtml(claimUrl)}" target="_blank" class="text-cyan text-mono" style="font-size: 11.5px; text-decoration: underline; max-width: 250px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;" title="${escapeHtml(claimUrl)}">
                  ${escapeHtml(claimUrl)}
                </a>
                <button class="btn btn-secondary btn-sm" onclick="copyToClipboard('${escapeHtml(claimUrl)}', 'Claim Link copied!')" title="Copy Claim Link">
                  📋 Link
                </button>
              </div>
            </td>
            <td>
              <div style="font-weight: 700;">${allocated} Keys</div>
              <div class="text-dim" style="font-size: 11px;"><span class="${remaining > 0 ? 'text-green' : 'text-muted'}">${remaining} remaining</span></div>
            </td>
            <td>
              <div style="font-size: 11px;">
                <div>Opens: <span class="text-mono" style="color: #cbd5e1;">${escapeHtml(vFrom)}</span></div>
                <div>Closes: <span class="text-mono" style="color: #94a3b8;">${escapeHtml(vUntil)}</span></div>
              </div>
            </td>
            <td>
              <span class="badge ${tierBadgeClass}">TIER: ${tier}</span>
              <div class="text-dim" style="font-size: 11px; margin-top: 3px;">${budget}/${period}</div>
            </td>
            <td>
              <span class="badge ${statusBadge}">${status}</span>
            </td>
            <td>
              <div style="display: flex; gap: 6px; align-items: center;">
                <button class="btn btn-sm btn-secondary" onclick="copyToClipboard('${escapeHtml(token)}', 'Claim token copied!')" title="Copy Claim Token">
                  🔑 Token
                </button>
                ${status !== 'REVOKED' ? `<button class="btn btn-sm btn-danger" onclick="promptRevokeAllocation('${id}')" title="Revoke Allocation">✕ Revoke</button>` : ''}
              </div>
            </td>
          </tr>
        `;
      }).join("");
    }
  } catch (err) {
    tbody.innerHTML = `<tr><td colspan="7" class="text-center text-muted">Failed to load allocations: ${err.message}</td></tr>`;
  }
}

async function handleMintAllocation() {
  const labelInput = document.getElementById("alloc-label");
  const agentInput = document.getElementById("alloc-target-agent");
  const countInput = document.getElementById("alloc-keys-count");
  const tierInput = document.getElementById("alloc-tier");
  const budgetInput = document.getElementById("alloc-budget");
  const validFromInput = document.getElementById("alloc-valid-from");
  const validUntilInput = document.getElementById("alloc-valid-until");
  const periodInput = document.getElementById("alloc-period");
  const nodeInput = document.getElementById("alloc-target-node");

  const label = labelInput ? labelInput.value.trim() : "";
  const agent = agentInput ? agentInput.value.trim() : "";
  if (!label || !agent) {
    showToast("Please provide both an Allocation Label and Target Agent", "error");
    if (!label && labelInput) labelInput.focus();
    else if (agentInput) agentInput.focus();
    return;
  }

  const count = countInput ? parseInt(countInput.value) || 1 : 1;
  const tier = tierInput ? tierInput.value || "s" : "s";
  const budget = budgetInput ? parseFloat(budgetInput.value) || 0.25 : 0.25;
  const vFrom = validFromInput && validFromInput.value ? new Date(validFromInput.value).toISOString() : null;
  const vUntil = validUntilInput && validUntilInput.value ? new Date(validUntilInput.value).toISOString() : null;
  const period = periodInput ? periodInput.value || "DAY" : "DAY";
  const node = nodeInput ? nodeInput.value || "local" : "local";

  const btn = document.getElementById("btn-confirm-mint-alloc");
  if (btn) {
    btn.disabled = true;
    btn.innerHTML = `<span>⏳</span> Minting Allocation...`;
  }

  try {
    const res = await apiFetch("/api/allocations/mint", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        label: label,
        target_agent: agent,
        allocated_keys_count: count,
        valid_from: vFrom,
        valid_until: vUntil,
        quality_tier: tier,
        budget_usd: budget,
        limit_period: period,
        target_node: node
      })
    });
    const data = await res.json();
    if (data.success && data.allocation) {
      showToast(`Allocation minted for ${agent}! Share the Cloud DNS link.`);

      const successBox = document.getElementById("alloc-success-box");
      const urlBox = document.getElementById("alloc-result-url");
      const tokenBox = document.getElementById("alloc-result-token");
      const snippetBox = document.getElementById("alloc-result-mcp-snippet");

      if (urlBox) urlBox.value = data.claim_url;
      if (tokenBox) tokenBox.value = data.claim_token;
      if (snippetBox) {
        snippetBox.innerText = `venice_claim_allocated_key(claim_token_or_url="${data.claim_url}")`;
      }
      if (successBox) successBox.style.display = "block";
      if (btn) btn.style.display = "none";

      loadAllocations();
    } else {
      showToast(`Failed to mint allocation: ${data.error || 'Server error'}`, "error");
    }
  } catch (err) {
    showToast(`Error: ${err.message}`, "error");
  } finally {
    if (btn) {
      btn.disabled = false;
      btn.innerHTML = `<span>⚡</span> Mint Allocation Link`;
    }
  }
}

async function promptRevokeAllocation(id) {
  if (!confirm(`Are you sure you want to revoke key allocation "${id}"? Agents will no longer be able to claim keys with this token.`)) return;

  try {
    const res = await apiFetch("/api/allocations/revoke", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ id: id })
    });
    const data = await res.json();
    if (data.success) {
      showToast("Allocation revoked successfully.");
      loadAllocations();
    } else {
      showToast("Failed to revoke allocation.", "error");
    }
  } catch (err) {
    showToast(`Error: ${err.message}`, "error");
  }
}

function copyToClipboard(text, msg = "Copied to clipboard!") {
  if (!text) return;
  if (navigator.clipboard && navigator.clipboard.writeText) {
    navigator.clipboard.writeText(text).then(() => {
      showToast(msg, "info");
    }).catch(() => {
      prompt("Copy this value manually:", text);
    });
  } else {
    prompt("Copy this value manually:", text);
  }
}

