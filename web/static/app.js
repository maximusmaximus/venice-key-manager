// Frontend controller for Venice & Telegram Key Management Dashboard

let appState = {
  stats: {},
  keys: [],
  agentBots: [],
  rateLimits: []
};

// DOM loaded
document.addEventListener("DOMContentLoaded", () => {
  initTabs();
  initModals();
  initInference();
  initForms();

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
}

function openModal(id) {
  const modal = document.getElementById(id);
  if (!modal) return;
  modal.classList.add("active");

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

// --- Data Fetching ---

async function refreshAll() {
  await Promise.all([
    loadStats(),
    loadKeys(),
    loadAgentBots(),
    loadConfig()
  ]);
}

async function loadStats() {
  try {
    const res = await fetch("/api/stats");
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
    const res = await fetch("/api/keys");
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

async function loadAgentBots() {
  const tbody = document.getElementById("tbody-tg-bots");
  try {
    const res = await fetch("/api/agent_bots");
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
    const res = await fetch("/api/rate_limits");
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
    const res = await fetch("/api/config");
    const data = await res.json();
    if (data.success) {
      if (data.authorized_chat_id) {
        document.getElementById("cfg-chat-id").value = data.authorized_chat_id;
      }
    }
  } catch (err) {}
}

// --- Key Actions ---

async function deployKey(keyId) {
  try {
    const res = await fetch("/api/deploy_key", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ key_string: keyId })
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
    const res = await fetch("/api/revoke_key", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ key_id: keyId })
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
    const res = await fetch("/api/agent_bots/test", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ agent_name: agentName })
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
    const res = await fetch("/api/agent_bots/deploy", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ agent_name: agentName })
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
        const res = await fetch("/api/create_key", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            mode: "import",
            key_string: keyStr,
            description: desc,
            key_type: kType,
            max_model_tier: tier,
            limit_usd: limit ? parseFloat(limit) : null
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
          limit_period: period
        };
        if (adminKey) payload.admin_key = adminKey;

        const res = await fetch("/api/create_key", {
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
      const res = await fetch("/api/agent_bots/register", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ agent_name: agent, bot_token: token, config_path: configPath, notes: notes })
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
      const res = await fetch("/api/agent_bots/register", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ agent_name: agent, bot_token: token })
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
      const res = await fetch("/api/config", {
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
      const res = await fetch("/api/config", {
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
        const res = await fetch("/api/config", {
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
    const res = await fetch("/api/infer", {
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
