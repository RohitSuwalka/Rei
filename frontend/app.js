/* ═══════════════════════════════════════════════════════════════════
   REI — Client-side Application Logic
   PDF upload, chat with streaming, settings management
   ═══════════════════════════════════════════════════════════════════ */

(() => {
    'use strict';

    // ─── STATE ──────────────────────────────────────────────────────

    const state = {
        pdfs: [],                    // list of uploaded PDFs
        selectedPdfIds: new Set(),   // multi-select: checked PDF IDs
        scope: 'selected',           // 'selected' or 'all'
        chatHistory: [],             // conversation messages [{role, content}]
        isStreaming: false,           // true while receiving a response
        settings: {},                // loaded from API
    };

    // ─── DOM REFERENCES ─────────────────────────────────────────────

    const $ = (sel) => document.querySelector(sel);
    const $$ = (sel) => document.querySelectorAll(sel);
    const modelControls = {};

    const els = {
        pdfList:        $('#pdf-list'),
        emptyState:     $('#empty-state'),
        uploadZone:     $('#upload-zone'),
        fileInput:      $('#file-input'),
        chatMessages:   $('#chat-messages'),
        chatWelcome:    $('#chat-welcome'),
        chatInput:      $('#chat-input'),
        sendBtn:        $('#send-btn'),
        settingsBtn:    $('#settings-btn'),
        settingsOverlay:$('#settings-overlay'),
        settingsClose:  $('#settings-close'),
        settingsSave:   $('#settings-save'),
        settingsStatus: $('#settings-status'),
        statusDot:      $('#status-indicator'),
        statusLabel:    $('#status-label'),
        sidebarInfo:    $('#sidebar-info'),
        uploadOverlay:  $('#upload-overlay'),
        uploadStep:     $('#upload-step'),
        progressFill:   $('#progress-fill'),
        scopeSingle:    $('#scope-single'),
        scopeAll:       $('#scope-all'),
    };

    // ─── INIT ───────────────────────────────────────────────────────

    async function init() {
        await loadSettings();
        await loadPdfs();
        bindEvents();
        updateUI();
    }

    // ─── API HELPERS ────────────────────────────────────────────────

    const API = {
        async get(path) {
            const res = await fetch(`/api${path}`);
            if (!res.ok) throw new Error(await res.text());
            return res.json();
        },
        async post(path, body) {
            const res = await fetch(`/api${path}`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(body),
            });
            if (!res.ok) throw new Error(await res.text());
            return res.json();
        },
        async del(path) {
            const res = await fetch(`/api${path}`, { method: 'DELETE' });
            if (!res.ok) throw new Error(await res.text());
            return res.json();
        },
        async uploadWithProgress(files, onProgress, onUploaded = () => {}) {
            const fileList = Array.from(files);
            const isMultiple = fileList.length > 1;
            const form = new FormData();
            const fieldName = isMultiple ? 'files' : 'file';
            fileList.forEach(file => form.append(fieldName, file));
            const endpoint = isMultiple ? '/api/upload-multiple-progress' : '/api/upload-progress';
            const res = await fetch(endpoint, { method: 'POST', body: form });
            if (!res.ok) throw new Error(await res.text());

            const reader = res.body.getReader();
            const decoder = new TextDecoder();
            let buffer = '';
            let result = null;

            const processEvent = (block) => {
                const data = block
                    .split(/\r?\n/)
                    .filter(line => line.startsWith('data: '))
                    .map(line => line.slice(6))
                    .join('\n');
                if (!data || data === '[DONE]') return;

                const event = JSON.parse(data);
                if (event.type === 'progress') {
                    onProgress(event.stage, event.progress);
                } else if (event.type === 'uploaded') {
                    onUploaded(event.uploaded);
                } else if (event.type === 'result') {
                    result = event.result;
                } else if (event.type === 'error') {
                    throw new Error(event.message);
                }
            };

            while (true) {
                const { done, value } = await reader.read();
                if (done) break;
                buffer += decoder.decode(value, { stream: true });
                const blocks = buffer.split(/\r?\n\r?\n/);
                buffer = blocks.pop() || '';
                blocks.forEach(processEvent);
            }

            buffer += decoder.decode();
            if (buffer.trim()) processEvent(buffer);
            if (!result) throw new Error('Upload stream ended before processing completed.');
            return result;
        },
    };

    // ─── DATA LOADING ───────────────────────────────────────────────

    async function loadSettings() {
        try {
            state.settings = await API.get('/settings');
        } catch (e) {
            console.error('Failed to load settings:', e);
            state.settings = {};
        }
    }

    async function loadPdfs() {
        try {
            state.pdfs = await API.get('/pdfs');
        } catch (e) {
            console.error('Failed to load PDFs:', e);
            state.pdfs = [];
        }
    }

    // ─── UI RENDERING ───────────────────────────────────────────────

    function updateUI() {
        renderPdfList();
        renderSidebarInfo();
        renderStatusIndicator();
        renderScopeToggle();
        updateSendBtn();
    }

    function renderPdfList() {
        // Clear existing items (keep empty state element)
        els.pdfList.querySelectorAll('.pdf-item').forEach(el => el.remove());

        if (state.pdfs.length === 0) {
            els.emptyState.style.display = 'block';
            return;
        }

        els.emptyState.style.display = 'none';

        state.pdfs.forEach(pdf => {
            const isSelected = state.selectedPdfIds.has(pdf.pdf_id);
            const item = document.createElement('div');
            item.className = `pdf-item${isSelected ? ' active' : ''}`;
            item.dataset.pdfId = pdf.pdf_id;
            item.innerHTML = `
                <label class="pdf-checkbox-wrapper">
                    <input type="checkbox" class="pdf-checkbox" data-pdf-id="${pdf.pdf_id}" ${isSelected ? 'checked' : ''}>
                    <span class="pdf-check-mark"></span>
                </label>
                <div class="pdf-info">
                    <div class="pdf-name" title="${escHtml(pdf.filename)}">${escHtml(pdf.filename)}</div>
                    <div class="pdf-meta">
                        <span>${pdf.page_count} pg</span>
                        <span>${pdf.chunk_count} chunks</span>
                        <span>${pdf.file_size}</span>
                    </div>
                </div>
                <button class="pdf-delete" title="Delete document" aria-label="Delete ${escHtml(pdf.filename)}">×</button>
            `;

            // Checkbox toggle
            const checkbox = item.querySelector('.pdf-checkbox');
            checkbox.addEventListener('change', (e) => {
                e.stopPropagation();
                togglePdfSelection(pdf.pdf_id, checkbox.checked);
            });

            // Click on item body (not checkbox, not delete) = toggle selection
            item.querySelector('.pdf-info').addEventListener('click', (e) => {
                checkbox.checked = !checkbox.checked;
                togglePdfSelection(pdf.pdf_id, checkbox.checked);
            });

            // Delete button
            item.querySelector('.pdf-delete').addEventListener('click', (e) => {
                e.stopPropagation();
                e.preventDefault();
                deletePdf(pdf.pdf_id, pdf.filename);
            });

            els.pdfList.appendChild(item);
        });
    }

    function renderSidebarInfo() {
        const mode = state.settings.llm_mode || 'local';
        let modelName;
        if (mode === 'local') {
            modelName = state.settings.ollama_model || 'qwen3:4b';
        } else {
            const provider = state.settings.api_provider || 'openai';
            const modelKey = `${provider}_model`;
            modelName = state.settings[modelKey] || provider;
        }

        const selectedCount = state.selectedPdfIds.size;
        const scopeLabel = state.scope === 'all'
            ? 'all docs'
            : `${selectedCount} selected`;

        els.sidebarInfo.innerHTML = `
            <div class="info-row">
                <span>mode</span>
                <span class="info-value">${mode}</span>
            </div>
            <div class="info-row">
                <span>model</span>
                <span class="info-value">${escHtml(modelName)}</span>
            </div>
            <div class="info-row">
                <span>scope</span>
                <span class="info-value">${scopeLabel}</span>
            </div>
        `;
    }

    function renderStatusIndicator() {
        const mode = state.settings.llm_mode || 'local';
        els.statusDot.className = `status-dot status-${mode}`;
        els.statusLabel.textContent = mode;
    }

    function renderScopeToggle() {
        const count = state.selectedPdfIds.size;
        els.scopeSingle.textContent = count > 0 ? `selected (${count})` : 'selected';
        els.scopeSingle.classList.toggle('active', state.scope === 'selected');
        els.scopeAll.classList.toggle('active', state.scope === 'all');
    }

    function updateSendBtn() {
        const hasText = els.chatInput.value.trim().length > 0;
        const hasTarget = state.scope === 'all' || state.selectedPdfIds.size > 0;
        els.sendBtn.disabled = !hasText || !hasTarget || state.isStreaming;
    }

    // ─── PDF MANAGEMENT ─────────────────────────────────────────────

    function togglePdfSelection(pdfId, selected) {
        if (selected) {
            state.selectedPdfIds.add(pdfId);
        } else {
            state.selectedPdfIds.delete(pdfId);
        }
        // Auto-switch to 'selected' scope when user toggles checkboxes
        if (state.selectedPdfIds.size > 0) {
            state.scope = 'selected';
        }
        updateUI();
    }

    async function deletePdf(pdfId, filename) {
        if (!confirm(`Delete "${filename || pdfId}"?`)) return;

        try {
            await API.del(`/pdfs/${pdfId}`);
            state.pdfs = state.pdfs.filter(p => p.pdf_id !== pdfId);
            state.selectedPdfIds.delete(pdfId);
            if (state.selectedPdfIds.size === 0 && state.pdfs.length === 0) {
                state.chatHistory = [];
                clearChat();
            }
            updateUI();
        } catch (e) {
            console.error('Delete failed:', e);
            addSystemMessage(`delete failed: ${extractError(e)}`);
        }
    }

    async function uploadPdf(file) {
        showUploadProgress(`Preparing ${file.name}...`);

        try {
            const result = await API.uploadWithProgress([file], (stage, percent) => {
                showUploadProgress(stage);
                setProgress(percent);
            });

            if (result.success && result.pdf_info) {
                state.pdfs.push(result.pdf_info);
                // Auto-select the newly uploaded PDF
                state.selectedPdfIds.add(result.pdf_info.pdf_id);
                state.scope = 'selected';
            }

            setProgress(100);
            setTimeout(hideUploadProgress, 400);
            updateUI();

        } catch (e) {
            hideUploadProgress();
            const detail = extractError(e);
            addSystemMessage(`upload failed: ${detail}`);
        }
    }

    async function uploadFiles(files) {
        const validFiles = Array.from(files).filter(file => file && file.name && file.name.toLowerCase().endsWith('.pdf'));

        if (!validFiles.length) {
            addSystemMessage('please choose a valid PDF file.');
            return;
        }

        if (validFiles.length === 1) {
            await uploadPdf(validFiles[0]);
            return;
        }

        showUploadProgress(`Preparing ${validFiles.length} PDFs...`);

        try {
            const result = await API.uploadWithProgress(
                validFiles,
                (stage, percent) => {
                    showUploadProgress(stage);
                    setProgress(percent);
                },
                item => {
                    if (item && item.pdf_info) {
                        state.pdfs.push(item.pdf_info);
                        state.selectedPdfIds.add(item.pdf_info.pdf_id);
                        state.scope = 'selected';
                        updateUI();
                    }
                }
            );
            setProgress(100);
            setTimeout(hideUploadProgress, 400);
            updateUI();
        } catch (e) {
            hideUploadProgress();
            const detail = extractError(e);
            addSystemMessage(`upload failed: ${detail}`);
        }
    }

    // ─── CHAT ───────────────────────────────────────────────────────

    function clearChat() {
        els.chatMessages.innerHTML = '';
        els.chatMessages.appendChild(els.chatWelcome);
        els.chatWelcome.style.display = 'flex';
    }

    function addUserMessage(text) {
        els.chatWelcome.style.display = 'none';

        const msg = document.createElement('div');
        msg.className = 'chat-msg';
        msg.innerHTML = `
            <div class="msg-header">
                <span class="msg-role role-user">user</span>
                <span class="msg-time">${timeNow()}</span>
            </div>
            <div class="msg-content user-msg">${escHtml(text)}</div>
        `;
        els.chatMessages.appendChild(msg);
        scrollToBottom();
    }

    function addAssistantMessage() {
        const msg = document.createElement('div');
        msg.className = 'chat-msg';
        msg.innerHTML = `
            <div class="msg-header">
                <span class="msg-role role-rei">rei</span>
                <span class="msg-time">${timeNow()}</span>
            </div>
            <div class="msg-content assistant-msg"></div>
        `;
        els.chatMessages.appendChild(msg);
        scrollToBottom();
        return msg.querySelector('.assistant-msg');
    }

    function addTypingIndicator() {
        const indicator = document.createElement('div');
        indicator.className = 'chat-msg typing-msg';
        indicator.setAttribute('role', 'status');
        indicator.setAttribute('aria-live', 'polite');
        indicator.innerHTML = `
            <div class="msg-header">
                <span class="msg-role role-rei">rei</span>
            </div>
            <div class="typing-indicator">
                <span class="typing-spinner" aria-hidden="true"></span>
                <span>REI is processing your question...</span>
            </div>
        `;
        els.chatMessages.appendChild(indicator);
        scrollToBottom();
        return indicator;
    }

    function addSystemMessage(text) {
        els.chatWelcome.style.display = 'none';

        const msg = document.createElement('div');
        msg.className = 'chat-msg';
        msg.innerHTML = `
            <div class="msg-header">
                <span class="msg-role role-rei">sys</span>
            </div>
            <div class="msg-error">${escHtml(text)}</div>
        `;
        els.chatMessages.appendChild(msg);
        scrollToBottom();
    }

    async function sendMessage() {
        const text = els.chatInput.value.trim();
        if (!text || state.isStreaming) return;

        // Build pdf_ids based on scope
        let pdfIds;
        if (state.scope === 'all') {
            pdfIds = [];  // empty = all docs on backend
        } else {
            pdfIds = Array.from(state.selectedPdfIds);
            if (pdfIds.length === 0) {
                addSystemMessage('select at least one document, or switch scope to "all docs".');
                return;
            }
        }

        // Add user message to UI and history
        addUserMessage(text);
        state.chatHistory.push({ role: 'user', content: text });
        els.chatInput.value = '';
        autoResizeInput();
        updateSendBtn();

        // Show typing indicator
        state.isStreaming = true;
        updateSendBtn();
        const typing = addTypingIndicator();

        try {
            // SSE streaming request
            const response = await fetch('/api/chat', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    question: text,
                    pdf_ids: pdfIds,
                    history: state.chatHistory.slice(-10),
                }),
            });

            if (!response.ok) {
                throw new Error(await response.text());
            }

            let contentEl = null;
            let fullResponse = '';

            // Read SSE stream
            const reader = response.body.getReader();
            const decoder = new TextDecoder();
            let buffer = '';

            while (true) {
                const { done, value } = await reader.read();
                if (done) break;

                buffer += decoder.decode(value, { stream: true });

                // Process SSE lines
                const lines = buffer.split('\n');
                buffer = lines.pop(); // Keep incomplete line in buffer

                for (const line of lines) {
                    if (!line.startsWith('data: ')) continue;

                    const data = line.slice(6);
                    if (data === '[DONE]') continue;

                    if (!contentEl) {
                        typing.remove();
                        contentEl = addAssistantMessage();
                    }

                    // Unescape newlines
                    const token = data.replace(/\\n/g, '\n');
                    fullResponse += token;

                    // Render markdown
                    contentEl.innerHTML = renderMarkdown(fullResponse);
                    scrollToBottom();
                }
            }

            // Add to history
            state.chatHistory.push({ role: 'assistant', content: fullResponse });

        } catch (e) {
            typing.remove();
            const detail = extractError(e);
            addSystemMessage(`error: ${detail}`);
        } finally {
            typing.remove();
            state.isStreaming = false;
            updateSendBtn();
        }
    }

    // ─── SETTINGS ───────────────────────────────────────────────────

    function openSettings() {
        loadSettingsIntoForm();
        els.settingsOverlay.classList.remove('hidden');
    }

    function closeSettings() {
        els.settingsOverlay.classList.add('hidden');
    }

    function getModelControl(provider) {
        if (!modelControls[provider]) {
            modelControls[provider] = {
                select: $(`#${provider}-model`),
                customInput: null,
                customWrapper: null,
            };
        }
        return modelControls[provider];
    }

    function showCustomModelInput(provider, model = '') {
        const control = getModelControl(provider);
        if (!control.customInput) {
            const input = document.createElement('input');
            input.type = 'text';
            input.id = `${provider}-model`;
            input.className = 'settings-input';
            input.setAttribute('aria-label', `${provider} model ID`);
            input.placeholder = 'Enter model ID';

            const wrapper = document.createElement('div');
            wrapper.appendChild(input);

            const choosePreset = document.createElement('button');
            choosePreset.type = 'button';
            choosePreset.className = 'model-preset-btn';
            choosePreset.textContent = 'choose preset';
            choosePreset.addEventListener('click', () => {
                control.customWrapper.replaceWith(control.select);
                control.select.value = control.select.options[0].value;
            });
            wrapper.appendChild(choosePreset);

            control.customInput = input;
            control.customWrapper = wrapper;
        }

        control.customInput.value = model;
        control.select.replaceWith(control.customWrapper);
    }

    function setModelSelection(provider, model) {
        const control = getModelControl(provider);
        const presetExists = Array.from(control.select.options).some(
            option => option.value === model
        );
        if (presetExists) {
            if (control.customWrapper?.isConnected) {
                control.customWrapper.replaceWith(control.select);
            }
            control.select.value = model;
        } else {
            showCustomModelInput(provider, model);
        }
    }

    function getModelSelection(provider, savedModel) {
        const control = getModelControl(provider);
        if (control.customWrapper?.isConnected) {
            return control.customInput.value.trim() || savedModel || '';
        }
        return control.select.value === '__custom__'
            ? control.customInput?.value.trim() || savedModel || ''
            : control.select.value;
    }

    function loadSettingsIntoForm() {
        const s = state.settings;

        // LLM mode
        $$('.llm-mode-btn').forEach(btn => {
            btn.classList.toggle('active', btn.dataset.mode === (s.llm_mode || 'local'));
        });

        // Show/hide sections
        const isLocal = (s.llm_mode || 'local') === 'local';
        $('#local-settings').classList.toggle('hidden', !isLocal);
        $('#api-settings').classList.toggle('hidden', isLocal);

        // Ollama model
        $('#ollama-model').value = s.ollama_model || 'qwen3:4b';

        // API provider
        $$('.provider-btn').forEach(btn => {
            btn.classList.toggle('active', btn.dataset.provider === (s.api_provider || 'openai'));
        });
        showProviderConfig(s.api_provider || 'openai');

        // API keys and models (show masked or actual)
        $('#openai-key').value = s.openai_api_key || '';
        setModelSelection('openai', s.openai_model || 'gpt-4o-mini');
        $('#openrouter-key').value = s.openrouter_api_key || '';
        setModelSelection('openrouter', s.openrouter_model || 'openrouter/free');
        $('#gemini-key').value = s.gemini_api_key || '';
        setModelSelection('gemini', s.gemini_model || 'gemini-3.8-flash');
        $('#anthropic-key').value = s.anthropic_api_key || '';
        $('#anthropic-workspace-id').value = s.anthropic_workspace_id || '';
        setModelSelection('anthropic', s.anthropic_model || 'claude-sonnet-4-20250514');
        $('#huggingface-key').value = s.huggingface_api_key || '';
        setModelSelection('huggingface', s.huggingface_model || 'openai/gpt-oss-120b');

        // Embedding mode
        $$('.embed-mode-btn').forEach(btn => {
            btn.classList.toggle('active', btn.dataset.mode === (s.embedding_mode || 'local'));
        });
    }

    function showProviderConfig(provider) {
        $$('.provider-config').forEach(el => el.classList.add('hidden'));
        const target = $(`#${provider}-config`);
        if (target) target.classList.remove('hidden');
    }

    async function saveSettings() {
        const settings = {
            llm_mode: $('.llm-mode-btn.active')?.dataset.mode || 'local',
            api_provider: $('.provider-btn.active')?.dataset.provider || 'openai',
            ollama_model: $('#ollama-model').value,
            openai_api_key: $('#openai-key').value,
            openai_model: getModelSelection('openai', state.settings.openai_model),
            openrouter_api_key: $('#openrouter-key').value,
            openrouter_model: getModelSelection('openrouter', state.settings.openrouter_model),
            gemini_api_key: $('#gemini-key').value,
            gemini_model: getModelSelection('gemini', state.settings.gemini_model),
            anthropic_api_key: $('#anthropic-key').value,
            anthropic_workspace_id: $('#anthropic-workspace-id').value.trim(),
            anthropic_model: getModelSelection('anthropic', state.settings.anthropic_model),
            huggingface_api_key: $('#huggingface-key').value,
            huggingface_model: getModelSelection('huggingface', state.settings.huggingface_model),
            embedding_mode: $('.embed-mode-btn.active')?.dataset.mode || 'local',
        };

        const missingModel = [
            'openai',
            'openrouter',
            'gemini',
            'anthropic',
            'huggingface',
        ].find(provider => {
            const control = getModelControl(provider);
            return control.customWrapper?.isConnected
                && !control.customInput.value.trim();
        });
        if (missingModel) {
            const input = getModelControl(missingModel).customInput;
            input.focus();
            input.placeholder = 'Enter a model ID before saving';
            els.settingsStatus.textContent = 'model ID required';
            els.settingsStatus.style.color = 'var(--error)';
            return;
        }

        try {
            await API.post('/settings', settings);
            // Re-load settings from server to get properly saved state
            await loadSettings();
            els.settingsStatus.textContent = 'saved ✓';
            els.settingsStatus.style.color = 'var(--accent)';
            setTimeout(() => { els.settingsStatus.textContent = ''; }, 2000);
            updateUI();
        } catch (e) {
            els.settingsStatus.textContent = 'save failed';
            els.settingsStatus.style.color = 'var(--error)';
            console.error('Settings save failed:', e);
        }
    }

    // ─── EVENTS ─────────────────────────────────────────────────────

    function bindEvents() {
        // Upload
        els.uploadZone.addEventListener('click', () => els.fileInput.click());
        els.uploadZone.addEventListener('keydown', (e) => {
            if (e.key === 'Enter' || e.key === ' ') els.fileInput.click();
        });
        els.fileInput.addEventListener('change', (e) => {
            const files = Array.from(e.target.files || []);
            if (files.length) {
                uploadFiles(files);
            }
            e.target.value = '';
        });

        // Drag and drop
        els.uploadZone.addEventListener('dragover', (e) => {
            e.preventDefault();
            els.uploadZone.classList.add('drag-over');
        });
        els.uploadZone.addEventListener('dragleave', () => {
            els.uploadZone.classList.remove('drag-over');
        });
        els.uploadZone.addEventListener('drop', (e) => {
            e.preventDefault();
            els.uploadZone.classList.remove('drag-over');
            const files = Array.from(e.dataTransfer.files || []);
            if (files.length) {
                uploadFiles(files);
            }
        });

        // Chat input
        els.chatInput.addEventListener('input', () => {
            autoResizeInput();
            updateSendBtn();
        });
        els.chatInput.addEventListener('keydown', (e) => {
            if (e.key === 'Enter' && !e.shiftKey) {
                e.preventDefault();
                sendMessage();
            }
        });
        els.sendBtn.addEventListener('click', sendMessage);

        // Scope toggle
        els.scopeSingle.addEventListener('click', () => {
            state.scope = 'selected';
            updateUI();
        });
        els.scopeAll.addEventListener('click', () => {
            state.scope = 'all';
            updateUI();
        });

        // Settings
        els.settingsBtn.addEventListener('click', openSettings);
        els.settingsClose.addEventListener('click', closeSettings);
        els.settingsOverlay.addEventListener('click', (e) => {
            if (e.target === els.settingsOverlay) closeSettings();
        });
        els.settingsSave.addEventListener('click', saveSettings);

        // LLM mode toggle in settings
        $$('.llm-mode-btn').forEach(btn => {
            btn.addEventListener('click', () => {
                $$('.llm-mode-btn').forEach(b => b.classList.remove('active'));
                btn.classList.add('active');
                const isLocal = btn.dataset.mode === 'local';
                $('#local-settings').classList.toggle('hidden', !isLocal);
                $('#api-settings').classList.toggle('hidden', isLocal);
            });
        });

        // Provider toggle in settings
        $$('.provider-btn').forEach(btn => {
            btn.addEventListener('click', () => {
                $$('.provider-btn').forEach(b => b.classList.remove('active'));
                btn.classList.add('active');
                showProviderConfig(btn.dataset.provider);
            });
        });

        ['openai', 'openrouter', 'gemini', 'anthropic', 'huggingface'].forEach(provider => {
            const control = getModelControl(provider);
            control.select.addEventListener('change', () => {
                if (control.select.value === '__custom__') {
                    showCustomModelInput(provider);
                    control.customInput.focus();
                }
            });
        });

        // Embedding mode toggle
        $$('.embed-mode-btn').forEach(btn => {
            btn.addEventListener('click', () => {
                $$('.embed-mode-btn').forEach(b => b.classList.remove('active'));
                btn.classList.add('active');
                if (btn.dataset.mode === 'api') {
                    window.alert(
                        'HAHAHA!!! OpenAI embeddings can cost money—check pricing first. Chup chap local use karle 😄'
                    );
                }
            });
        });

        // Keyboard shortcut: Escape closes settings
        document.addEventListener('keydown', (e) => {
            if (e.key === 'Escape' && !els.settingsOverlay.classList.contains('hidden')) {
                closeSettings();
            }
        });
    }

    // ─── HELPERS ────────────────────────────────────────────────────

    function scrollToBottom() {
        requestAnimationFrame(() => {
            els.chatMessages.scrollTop = els.chatMessages.scrollHeight;
        });
    }

    function autoResizeInput() {
        els.chatInput.style.height = 'auto';
        els.chatInput.style.height = Math.min(els.chatInput.scrollHeight, 120) + 'px';
    }

    function timeNow() {
        return new Date().toLocaleTimeString('en-US', {
            hour: '2-digit',
            minute: '2-digit',
            hour12: false,
        });
    }

    function escHtml(str) {
        const div = document.createElement('div');
        div.textContent = str;
        return div.innerHTML;
    }

    function extractError(e) {
        try {
            const parsed = JSON.parse(e.message);
            return parsed.detail || e.message;
        } catch {
            return e.message || 'unknown error';
        }
    }

    function showUploadProgress(text) {
        els.uploadOverlay.classList.remove('hidden');
        els.uploadStep.textContent = text;
    }

    function hideUploadProgress() {
        els.uploadOverlay.classList.add('hidden');
        els.progressFill.style.width = '0%';
    }

    function setProgress(percent) {
        els.progressFill.style.width = `${percent}%`;
    }

    // ─── BASIC MARKDOWN RENDERER ────────────────────────────────────

    function renderMarkdown(text) {
        if (!text) return '';

        let html = escHtml(text);

        // Code blocks (``` ... ```)
        html = html.replace(/```(\w*)\n([\s\S]*?)```/g, (_, lang, code) => {
            return `<pre><code>${code.trim()}</code></pre>`;
        });

        // Inline code
        html = html.replace(/`([^`]+)`/g, '<code>$1</code>');

        // Bold
        html = html.replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>');

        // Italic
        html = html.replace(/\*(.+?)\*/g, '<em>$1</em>');

        // Unordered lists
        html = html.replace(/^[\s]*[-*]\s+(.+)$/gm, '<li>$1</li>');
        html = html.replace(/(<li>.*<\/li>\n?)+/g, '<ul>$&</ul>');

        // Ordered lists
        html = html.replace(/^[\s]*\d+\.\s+(.+)$/gm, '<li>$1</li>');

        // Blockquotes
        html = html.replace(/^&gt;\s+(.+)$/gm, '<blockquote>$1</blockquote>');

        // Headings
        html = html.replace(/^####\s+(.+)$/gm, '<strong>$1</strong>');
        html = html.replace(/^###\s+(.+)$/gm, '<strong>$1</strong>');
        html = html.replace(/^##\s+(.+)$/gm, '<strong>$1</strong>');
        html = html.replace(/^#\s+(.+)$/gm, '<strong>$1</strong>');

        // Paragraphs — convert double newlines
        html = html.replace(/\n\n/g, '</p><p>');
        html = html.replace(/\n/g, '<br>');
        html = `<p>${html}</p>`;

        // Clean up empty paragraphs
        html = html.replace(/<p>\s*<\/p>/g, '');

        return html;
    }

    // ─── START ──────────────────────────────────────────────────────

    document.addEventListener('DOMContentLoaded', init);

})();
