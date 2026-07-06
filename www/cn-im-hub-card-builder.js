class CnImHubCardBuilder extends HTMLElement {
  constructor() {
    super();
    this._data = { title: "", content: "", rows: [] };
    this._copyFeedback = false;
    this.attachShadow({ mode: "open" });
    this._render();
    this._attachEvents();
  }

  set hass(hass) {
    this._hass = hass;
  }

  _render() {
    const s = this.shadowRoot;
    s.innerHTML = `
      <ha-card>
        <h1 class="card-title">飞书卡片构建器</h1>
        <div class="layout">
          <div class="editor">
            <div class="field">
              <label>卡片标题</label>
              <ha-textfield id="title" placeholder="Claw Assistant" value="${this._escape(this._data.title)}"></ha-textfield>
            </div>
            <div class="field">
              <label>卡片正文</label>
              <ha-textarea id="content" placeholder="Markdown 文本" value="${this._escape(this._data.content)}"></ha-textarea>
            </div>
            <div class="field">
              <label>按钮</label>
              <div id="rows" class="rows"></div>
              <mwc-button id="addRow" class="add-row" dense unelevated>
                <ha-icon icon="mdi:plus"></ha-icon> 添加行
              </mwc-button>
            </div>
          </div>
          <div class="preview-panel">
            <label>实时预览</label>
            <div id="preview" class="preview"></div>
            <label>生成的 JSON</label>
            <div class="json-area">
              <pre id="jsonOutput"></pre>
              <mwc-button id="copyBtn" dense unelevated>
                <ha-icon icon="mdi:content-copy"></ha-icon>
                <span id="copyLabel">复制 JSON</span>
              </mwc-button>
            </div>
          </div>
        </div>
      </ha-card>
      <style>
        :host {
          --card-bg: var(--card-background-color, #fff);
          --border: 1px solid var(--divider-color, #e0e0e0);
          --radius: 12px;
          --spacing: 16px;
        }
        ha-card {
          padding: 20px;
          background: var(--card-bg);
          border-radius: var(--radius);
        }
        .card-title {
          font-size: 18px;
          font-weight: 600;
          margin: 0 0 20px 0;
          color: var(--primary-text-color);
        }
        .layout {
          display: grid;
          grid-template-columns: 1fr 1fr;
          gap: 24px;
        }
        @media (max-width: 800px) {
          .layout { grid-template-columns: 1fr; }
        }
        .field {
          margin-bottom: 16px;
        }
        .field label {
          display: block;
          font-size: 13px;
          font-weight: 500;
          color: var(--secondary-text-color);
          margin-bottom: 6px;
        }
        ha-textfield, ha-textarea {
          width: 100%;
        }
        .rows {
          display: flex;
          flex-direction: column;
          gap: 8px;
          margin-bottom: 8px;
        }
        .button-row {
          display: flex;
          flex-wrap: wrap;
          gap: 8px;
          align-items: center;
          padding: 12px;
          background: var(--secondary-background-color, #f5f5f5);
          border-radius: 8px;
          position: relative;
        }
        .button-row .row-label {
          font-size: 11px;
          color: var(--secondary-text-color);
          width: 100%;
          margin-bottom: 2px;
        }
        .btn-card {
          display: flex;
          flex-direction: column;
          gap: 4px;
          padding: 8px;
          background: var(--card-bg);
          border-radius: 6px;
          border: var(--border);
          min-width: 120px;
          position: relative;
        }
        .btn-card .btn-label {
          font-size: 11px;
          font-weight: 600;
          padding: 2px 6px;
          border-radius: 3px;
          text-align: center;
          display: inline-block;
        }
        .btn-card .btn-fields {
          display: flex;
          flex-direction: column;
          gap: 2px;
        }
        .btn-card .btn-fields ha-textfield {
          --ha-textfield-min-height: 28px;
          font-size: 11px;
        }
        .btn-card .btn-fields ha-select {
          font-size: 11px;
          --ha-select-min-height: 28px;
        }
        .btn-card .btn-del {
          position: absolute;
          top: -6px;
          right: -6px;
          --mdc-icon-button-size: 20px;
          --mdc-icon-size: 14px;
          color: var(--error-color);
          background: var(--card-bg);
          border-radius: 50%;
          border: var(--border);
          cursor: pointer;
        }
        .btn-card .btn-del::before { content: "✕"; font-size: 12px; }
        .row-actions {
          display: flex;
          gap: 4px;
          align-self: stretch;
          align-items: center;
        }
        .row-del {
          color: var(--error-color);
          cursor: pointer;
          font-size: 18px;
          padding: 4px;
          opacity: 0.5;
        }
        .row-del:hover { opacity: 1; }
        .add-row {
          width: 100%;
        }
        .add-row ha-icon {
          --mdc-icon-size: 16px;
        }
        .preview-panel label {
          display: block;
          font-size: 13px;
          font-weight: 500;
          color: var(--secondary-text-color);
          margin-bottom: 6px;
        }
        .preview {
          background: var(--secondary-background-color, #f5f5f5);
          border-radius: 8px;
          padding: 16px;
          min-height: 160px;
          margin-bottom: 16px;
          font-size: 14px;
        }
        .preview-card {
          background: var(--card-bg);
          border-radius: 8px;
          overflow: hidden;
          box-shadow: 0 1px 3px rgba(0,0,0,0.12);
        }
        .preview-header {
          padding: 12px 16px;
          color: #fff;
          font-weight: 600;
          font-size: 15px;
        }
        .preview-body {
          padding: 12px 16px;
          color: var(--primary-text-color);
          white-space: pre-wrap;
          word-break: break-word;
        }
        .preview-body p { margin: 0 0 8px 0; }
        .preview-body p:last-child { margin-bottom: 0; }
        .preview-buttons {
          padding: 8px 16px 12px;
          display: flex;
          flex-wrap: wrap;
          gap: 8px;
        }
        .preview-button {
          display: inline-block;
          padding: 6px 16px;
          border-radius: 4px;
          font-size: 13px;
          font-weight: 500;
          color: #fff;
          text-align: center;
          cursor: default;
        }
        .json-area {
          position: relative;
        }
        .json-area pre {
          background: var(--secondary-background-color, #f5f5f5);
          border: var(--border);
          border-radius: 6px;
          padding: 12px;
          font-size: 11px;
          overflow-x: auto;
          max-height: 200px;
          white-space: pre-wrap;
          word-break: break-all;
          color: var(--primary-text-color);
        }
        .json-area mwc-button {
          position: absolute;
          top: 8px;
          right: 8px;
        }
        .empty-preview {
          color: var(--secondary-text-color);
          text-align: center;
          padding: 32px 16px;
          font-size: 13px;
        }
      </style>
    `;
  }

  _attachEvents() {
    const s = this.shadowRoot;
    s.getElementById("title").addEventListener("input", () => this._sync());
    s.getElementById("content").addEventListener("input", () => this._sync());
    s.getElementById("addRow").addEventListener("click", () => {
      this._data.rows.push([]);
      this._update();
    });
    s.getElementById("copyBtn").addEventListener("click", () => this._copy());

    // Wait for custom elements to be ready, then re-sync
    setTimeout(() => this._update(), 50);
  }

  _addButton(ri) {
    this._data.rows[ri].push({ label: "按钮", value: "value", color: "blue" });
    this._update();
  }

  _removeButton(ri, bi) {
    this._data.rows[ri].splice(bi, 1);
    this._update();
  }

  _removeRow(ri) {
    this._data.rows.splice(ri, 1);
    this._update();
  }

  _sync() {
    const s = this.shadowRoot;
    this._data.title = s.getElementById("title").value;
    this._data.content = s.getElementById("content").value;
    this._updateOutput();
  }

  _update() {
    const s = this.shadowRoot;
    const rowsEl = s.getElementById("rows");
    rowsEl.innerHTML = "";

    this._data.rows.forEach((row, ri) => {
      const rowDiv = document.createElement("div");
      rowDiv.className = "button-row";

      const label = document.createElement("span");
      label.className = "row-label";
      label.textContent = `第 ${ri + 1} 行`;
      rowDiv.appendChild(label);

      row.forEach((btn, bi) => {
        const card = document.createElement("div");
        card.className = "btn-card";

        const del = document.createElement("span");
        del.className = "btn-del";
        del.addEventListener("click", () => this._removeButton(ri, bi));
        card.appendChild(del);

        const fields = document.createElement("div");
        fields.className = "btn-fields";

        // Label
        const labelInput = document.createElement("ha-textfield");
        labelInput.value = btn.label;
        labelInput.placeholder = "标签";
        labelInput.addEventListener("input", () => { btn.label = labelInput.value; this._updateOutput(); });
        fields.appendChild(labelInput);

        // Value
        const valInput = document.createElement("ha-textfield");
        valInput.value = btn.value;
        valInput.placeholder = "值";
        valInput.addEventListener("input", () => { btn.value = valInput.value; this._updateOutput(); });
        fields.appendChild(valInput);

        // Color
        const colorSelect = document.createElement("ha-select");
        colorSelect.innerHTML = `
          <ha-list-item value="blue">蓝色</ha-list-item>
          <ha-list-item value="red">红色</ha-list-item>
          <ha-list-item value="grey">灰色</ha-list-item>
          <ha-list-item value="primary">强调</ha-list-item>
          <ha-list-item value="green">绿色</ha-list-item>
        `;
        colorSelect.value = btn.color;
        colorSelect.addEventListener("change", () => { btn.color = colorSelect.value; this._updateOutput(); });
        fields.appendChild(colorSelect);

        card.appendChild(fields);
        rowDiv.appendChild(card);
      });

      // Add button
      const addBtnContainer = document.createElement("div");
      addBtnContainer.style.cssText = "display:flex;flex-direction:column;gap:4px;align-self:stretch;justify-content:center;";
      const addBtn = document.createElement("mwc-button");
      addBtn.dense = true;
      addBtn.unelevated = true;
      addBtn.innerHTML = `<ha-icon icon="mdi:plus" style="--mdc-icon-size:16px"></ha-icon> 添加`;
      addBtn.addEventListener("click", () => this._addButton(ri));
      addBtnContainer.appendChild(addBtn);
      rowDiv.appendChild(addBtnContainer);

      // Row delete
      const delRow = document.createElement("span");
      delRow.className = "row-del";
      delRow.textContent = "🗑";
      delRow.addEventListener("click", () => this._removeRow(ri));
      rowDiv.appendChild(delRow);

      rowsEl.appendChild(rowDiv);
    });

    this._updateOutput();
  }

  _updateOutput() {
    const s = this.shadowRoot;
    const title = s.getElementById("title")?.value || "";
    const content = s.getElementById("content")?.value || "";

    const card = this._buildCard(title, content);
    const jsonEl = s.getElementById("jsonOutput");
    if (jsonEl) {
      jsonEl.textContent = card ? JSON.stringify(card, null, 2) : "// 填入卡片内容后自动生成";
    }

    this._renderPreview(title, content);
  }

  _buildCard(title, content) {
    const rows = this._data.rows;
    const hasContent = content.trim();
    const hasButtons = rows.some(r => r.length > 0);
    if (!hasContent && !hasButtons) return null;

    const elements = [];
    if (hasContent) {
      elements.push({ tag: "markdown", content: content });
    }

    const colorMap = { blue: "blue", red: "red", grey: "grey", primary: "primary", green: "green" };

    for (const row of rows) {
      if (row.length === 0) continue;
      const columns = row.map(btn => ({
        tag: "column",
        width: "weighted",
        weight: 1,
        elements: [{
          tag: "button",
          text: { tag: "plain_text", content: btn.label || "按钮" },
          type: colorMap[btn.color] || "blue",
          width: "fill",
          value: { action: btn.value || "" },
        }],
      }));
      elements.push({
        tag: "column_set",
        flex_mode: "bisect",
        columns: columns,
      });
    }

    return {
      schema: "2.0",
      config: { update_multi: true },
      header: {
        title: { tag: "plain_text", content: title || "Claw Assistant" },
        template: "blue",
      },
      body: { elements: elements },
    };
  }

  _renderPreview(title, content) {
    const s = this.shadowRoot;
    const preview = s.getElementById("preview");
    if (!preview) return;

    const rows = this._data.rows;
    const hasContent = content.trim();
    const hasButtons = rows.some(r => r.length > 0);

    if (!hasContent && !hasButtons) {
      preview.innerHTML = `<div class="empty-preview">在左侧填写卡片内容，实时预览将显示在此处</div>`;
      return;
    }

    const colorMap = { blue: "blue", red: "red", grey: "grey", primary: "primary", green: "green" };
    const bgMap = { blue: "#1677ff", red: "#f53d3d", grey: "#8c8c8c", primary: "#1677ff", green: "#00b42a" };
    const headerBg = "#1677ff";

    let html = `<div class="preview-card">`;
    html += `<div class="preview-header" style="background:${headerBg}">${this._escape(title || "Claw Assistant")}</div>`;
    html += `<div class="preview-body">${this._escape(content)}</div>`;

    if (hasButtons) {
      html += `<div class="preview-buttons">`;
      for (const row of rows) {
        for (const btn of row) {
          const bg = bgMap[btn.color] || bgMap.blue;
          html += `<span class="preview-button" style="background:${bg}">${this._escape(btn.label || "按钮")}</span>`;
        }
      }
      html += `</div>`;
    }

    html += `</div>`;
    preview.innerHTML = html;
  }

  _copy() {
    const s = this.shadowRoot;
    const text = s.getElementById("jsonOutput")?.textContent || "";
    if (!text || text.startsWith("//")) return;
    navigator.clipboard.writeText(text).then(() => {
      const label = s.getElementById("copyLabel");
      if (label) {
        label.textContent = "已复制 ✓";
        setTimeout(() => { label.textContent = "复制 JSON"; }, 2000);
      }
    });
  }

  _escape(str) {
    if (!str) return "";
    return str.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
  }

  getCardSize() {
    return 6;
  }
}

customElements.define("cn-im-hub-card-builder", CnImHubCardBuilder);

window.customCards = window.customCards || [];
window.customCards.push({
  type: "cn-im-hub-card-builder",
  name: "飞书卡片构建器",
  description: "可视化构建飞书交互卡片，生成 JSON 代码",
  preview: false,
});
