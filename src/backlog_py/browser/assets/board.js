    let draggedTaskId = null;
    const taskDialog = document.getElementById("task-dialog");
    const taskCreateDialog = document.getElementById("task-create-dialog");
    const taskCreateForm = document.getElementById("task-create-form");
    const taskEditDialog = document.getElementById("task-edit-dialog");
    const taskEditForm = document.getElementById("task-edit-form");
    const taskArchiveDialog = document.getElementById("task-archive-dialog");
    const taskArchiveConfirm = document.getElementById("task-archive-confirm");
    const configSettingsDialog = document.getElementById("config-settings-dialog");
    const configSettingsForm = document.getElementById("config-settings-form");
    const dodDefaultsDialog = document.getElementById("dod-defaults-dialog");
    const dodDefaultsForm = document.getElementById("dod-defaults-form");
    const serviceStatusDialog = document.getElementById("service-status-dialog");
    const serviceShutdownConfirm = document.getElementById("service-shutdown-confirm");
    const documentsDialog = document.getElementById("documents-dialog");
    const decisionsDialog = document.getElementById("decisions-dialog");
    const boardElement = document.querySelector("[data-board-revision]");
    const boardRefreshIntervalMs = 5000;
    let currentBoardRevision = boardElement?.dataset.boardRevision || "";
    let boardRefreshInFlight = false;
    let boardRefreshTimer = null;
    let boardRevisionEvents = null;

    function setText(id, value) {
      const element = document.getElementById(id);
      if (element) element.textContent = value || "—";
    }

    function setHtml(id, value) {
      const element = document.getElementById(id);
      if (element) element.innerHTML = value || '<p class="markdown-empty">No content</p>';
    }

    function readonlyListItem(title, meta, onClick) {
      const item = document.createElement("li");
      const button = document.createElement("button");
      button.type = "button";
      const titleElement = document.createElement("span");
      titleElement.className = "readonly-list-title";
      titleElement.textContent = title || "Untitled";
      const metaElement = document.createElement("span");
      metaElement.className = "readonly-list-meta";
      metaElement.textContent = meta || "";
      button.append(titleElement, metaElement);
      button.addEventListener("click", onClick);
      item.appendChild(button);
      return item;
    }

    function renderEmptyReadonlyList(list, message) {
      list.replaceChildren();
      const empty = document.createElement("li");
      empty.className = "readonly-list-meta";
      empty.textContent = message;
      list.appendChild(empty);
    }

    const mermaidModuleUrl = "https://cdn.jsdelivr.net/npm/mermaid@10/dist/mermaid.esm.min.mjs";
    let mermaidModulePromise = null;

    async function renderMermaidDiagrams(root = document) {
      const diagrams = Array.from(root.querySelectorAll("[data-mermaid-diagram] .mermaid"));
      if (diagrams.length === 0) return;
      try {
        mermaidModulePromise = mermaidModulePromise || import(mermaidModuleUrl);
        const module = await mermaidModulePromise;
        const mermaid = module.default || module;
        mermaid.initialize({startOnLoad: false, securityLevel: "strict"});
        await mermaid.run({nodes: diagrams});
      } catch (error) {
        console.error(error);
        mermaidModulePromise = null;
        diagrams.forEach((diagram) => {
          diagram.closest("[data-mermaid-diagram]")?.classList.add("mermaid-render-failed");
        });
      }
    }

    function hasOpenDialog() {
      return Boolean(document.querySelector("dialog[open]"));
    }

    function handleBoardRevision(nextRevision) {
      if (nextRevision && nextRevision !== currentBoardRevision && !hasOpenDialog()) {
        window.location.reload();
      }
    }

    async function pollBoardRevision() {
      if (!currentBoardRevision || boardRefreshInFlight) return;
      boardRefreshInFlight = true;
      try {
        const response = await fetch("/api/board", {
          headers: {"Accept": "application/json"},
          cache: "no-store",
        });
        if (!response.ok) {
          console.error(await response.text());
          return;
        }
        const payload = await response.json();
        handleBoardRevision(payload.revision);
      } catch (error) {
        console.error(error);
      } finally {
        boardRefreshInFlight = false;
      }
    }

    function startBoardRevisionPolling() {
      if (boardRefreshTimer) return;
      boardRefreshTimer = window.setInterval(pollBoardRevision, boardRefreshIntervalMs);
    }

    function stopBoardRevisionPolling() {
      if (!boardRefreshTimer) return;
      window.clearInterval(boardRefreshTimer);
      boardRefreshTimer = null;
    }

    function closeBoardRevisionEvents() {
      if (!boardRevisionEvents) return;
      boardRevisionEvents.close();
      boardRevisionEvents = null;
    }

    function handleServiceShutdownEvent(payload) {
      stopBoardRevisionPolling();
      closeBoardRevisionEvents();
      if (serviceShutdownConfirm) serviceShutdownConfirm.disabled = true;
      const requestedAt = payload?.shutdownRequestedAt ? ` at ${payload.shutdownRequestedAt}` : "";
      setText("service-status-message", `Server shutdown was requested${requestedAt}.`);
    }

    function connectBoardRevisionEvents() {
      if (!("EventSource" in window)) return false;
      boardRevisionEvents = new EventSource("/api/board/events");
      boardRevisionEvents.addEventListener("revision", (event) => {
        try {
          const payload = JSON.parse(event.data || "{}");
          handleBoardRevision(payload.revision);
        } catch (error) {
          console.error(error);
        }
      });
      boardRevisionEvents.addEventListener("shutdown", (event) => {
        try {
          const payload = JSON.parse(event.data || "{}");
          handleServiceShutdownEvent(payload);
        } catch (error) {
          console.error(error);
        }
      });
      boardRevisionEvents.onerror = () => {
        if (boardRevisionEvents?.readyState === EventSource.CLOSED) {
          startBoardRevisionPolling();
        }
      };
      return true;
    }

    function renderChecklist(id, items, section) {
      const list = document.getElementById(id);
      if (!list) return;
      list.replaceChildren();
      if (!items || items.length === 0) {
        const empty = document.createElement("li");
        empty.textContent = "No items";
        list.appendChild(empty);
        return;
      }
      items.forEach((item, index) => {
        const li = document.createElement("li");
        li.className = "checklist-item";
        const label = document.createElement("label");
        const checkbox = document.createElement("input");
        checkbox.type = "checkbox";
        checkbox.checked = Boolean(item.checked);
        checkbox.setAttribute("data-checklist-section", section);
        checkbox.setAttribute("data-checklist-index", String(index + 1));
        checkbox.addEventListener("change", submitTaskChecklistState);
        const text = document.createElement("span");
        const itemId = item.itemId ? `#${item.itemId} ` : "";
        text.textContent = `${itemId}${item.text}`;
        label.appendChild(checkbox);
        label.appendChild(text);
        li.appendChild(label);
        list.appendChild(li);
      });
    }

    function checklistText(items) {
      return (items || []).map((item) => item.text || "").filter(Boolean).join("\\n");
    }

    function metadataList(value) {
      return String(value || "").split(/[\\n,]/).map((item) => item.trim()).filter(Boolean);
    }

    async function openDocumentDetail(identifier) {
      const response = await fetch(`/api/docs/${encodeURIComponent(identifier)}`);
      if (!response.ok) {
        console.error(await response.text());
        return;
      }
      const doc = await response.json();
      setText("document-detail-title", doc.title);
      setText("document-detail-path", doc.path);
      setText("document-detail-type", doc.type);
      setText("document-detail-tags", (doc.tags || []).join(", "));
      setHtml("document-detail", doc.contentHtml);
      renderMermaidDiagrams(documentsDialog || window.document);
    }

    function renderDocumentsList(documents) {
      const list = document.getElementById("documents-list");
      if (!list) return;
      if (!documents || documents.length === 0) {
        renderEmptyReadonlyList(list, "No documents");
        setText("document-detail-title", "Document detail");
        setText("document-detail-path", "");
        setText("document-detail-type", "");
        setText("document-detail-tags", "");
        setHtml("document-detail", "");
        return;
      }
      list.replaceChildren();
      documents.forEach((doc) => {
        const identifier = doc.id || doc.path;
        const metaParts = [doc.id, doc.path, doc.type].filter(Boolean);
        list.appendChild(readonlyListItem(doc.title, metaParts.join(" · "), () => openDocumentDetail(identifier)));
      });
      const first = documents[0];
      openDocumentDetail(first.id || first.path);
    }

    async function openDocuments() {
      const response = await fetch("/api/docs");
      if (!response.ok) {
        console.error(await response.text());
        return;
      }
      renderDocumentsList(await response.json());
      documentsDialog?.showModal();
    }

    async function openDecisionDetail(identifier) {
      const response = await fetch(`/api/decisions/${encodeURIComponent(identifier)}`);
      if (!response.ok) {
        console.error(await response.text());
        return;
      }
      const decision = await response.json();
      setText("decision-detail-title", `${decision.id} - ${decision.title}`);
      setText("decision-detail-status", decision.status);
      setText("decision-detail-date", decision.date);
      setText("decision-detail-path", decision.path);
      setHtml("decision-detail-context", decision.contextHtml);
      setHtml("decision-detail-decision", decision.decisionHtml);
      setHtml("decision-detail-consequences", decision.consequencesHtml);
      setHtml("decision-detail-alternatives", decision.alternativesHtml);
      renderMermaidDiagrams(decisionsDialog || document);
    }

    function renderDecisionsList(decisions) {
      const list = document.getElementById("decisions-list");
      if (!list) return;
      if (!decisions || decisions.length === 0) {
        renderEmptyReadonlyList(list, "No decisions");
        setText("decision-detail-title", "Decision detail");
        setText("decision-detail-status", "");
        setText("decision-detail-date", "");
        setText("decision-detail-path", "");
        setHtml("decision-detail-context", "");
        setHtml("decision-detail-decision", "");
        setHtml("decision-detail-consequences", "");
        setHtml("decision-detail-alternatives", "");
        return;
      }
      list.replaceChildren();
      decisions.forEach((decision) => {
        const metaParts = [decision.id, decision.status, decision.date].filter(Boolean);
        list.appendChild(readonlyListItem(decision.title, metaParts.join(" · "), () => openDecisionDetail(decision.id)));
      });
      openDecisionDetail(decisions[0].id);
    }

    async function openDecisions() {
      const response = await fetch("/api/decisions");
      if (!response.ok) {
        console.error(await response.text());
        return;
      }
      renderDecisionsList(await response.json());
      decisionsDialog?.showModal();
    }

    function selectedRange(textarea) {
      const valueLength = textarea.value.length;
      const start = Number.isInteger(textarea.selectionStart) ? textarea.selectionStart : valueLength;
      const end = Number.isInteger(textarea.selectionEnd) ? textarea.selectionEnd : start;
      return {start, end};
    }

    function replaceMarkdownSelection(textarea, start, end, replacement, selectStart, selectEnd) {
      const value = textarea.value || "";
      textarea.value = value.slice(0, start) + replacement + value.slice(end);
      textarea.focus();
      textarea.setSelectionRange(selectStart, selectEnd);
      textarea.dispatchEvent(new Event("input", {bubbles: true}));
    }

    function applyMarkdownLineFormat(textarea, range, command) {
      const value = textarea.value || "";
      const lineStart = range.start === 0 ? 0 : value.lastIndexOf("\\n", range.start - 1) + 1;
      const nextLineBreak = value.indexOf("\\n", range.end);
      const lineEnd = nextLineBreak === -1 ? value.length : nextLineBreak;
      const segment = value.slice(lineStart, lineEnd);
      const placeholder = command === "heading" ? "Heading" : "List item";
      const lines = (segment || placeholder).split("\\n");
      const replacement = lines.map((line, index) => {
        if (command === "heading") return line.startsWith("#") ? line : `## ${line}`;
        if (command === "numbered") return `${index + 1}. ${line}`;
        return `- ${line}`;
      }).join("\\n");
      replaceMarkdownSelection(textarea, lineStart, lineEnd, replacement, lineStart, lineStart + replacement.length);
    }

    function applyMarkdownFormat(textarea, command) {
      if (!textarea || !command) return;
      const range = selectedRange(textarea);
      const value = textarea.value || "";
      const selected = value.slice(range.start, range.end);
      const inlineFormats = {
        bold: {prefix: "**", suffix: "**", placeholder: "bold text"},
        italic: {prefix: "*", suffix: "*", placeholder: "italic text"},
        code: {prefix: "`", suffix: "`", placeholder: "code"},
      };
      if (inlineFormats[command]) {
        const format = inlineFormats[command];
        const content = selected || format.placeholder;
        const replacement = `${format.prefix}${content}${format.suffix}`;
        const innerStart = range.start + format.prefix.length;
        replaceMarkdownSelection(textarea, range.start, range.end, replacement, innerStart, innerStart + content.length);
        return;
      }
      if (command === "link") {
        const content = selected || "link text";
        const replacement = `[${content}](url)`;
        const innerStart = range.start + 1;
        replaceMarkdownSelection(textarea, range.start, range.end, replacement, innerStart, innerStart + content.length);
        return;
      }
      if (command === "bullet" || command === "numbered" || command === "heading") {
        applyMarkdownLineFormat(textarea, range, command);
      }
    }

    function toolbarTextarea(button) {
      const toolbar = button.closest("[data-markdown-toolbar]");
      const container = toolbar ? toolbar.parentElement : null;
      return container ? container.querySelector("[data-markdown-input]") : null;
    }

    function markdownEditor(textarea) {
      return textarea ? textarea.closest("[data-markdown-editor]") : null;
    }

    function markdownPreviewPanel(textarea) {
      const editor = markdownEditor(textarea);
      return editor ? editor.querySelector("[data-markdown-preview-for]") : null;
    }

    function markdownRichPanel(textarea) {
      const editor = markdownEditor(textarea);
      return editor ? editor.querySelector("[data-markdown-rich-for]") : null;
    }

    function safeRichHref(href) {
      const value = String(href || "").trim();
      if (!value) return "";
      const scheme = /^([a-z][a-z0-9+.-]*):/i.exec(value);
      if (scheme && !/^(https?|mailto)$/i.test(scheme[1])) return "#";
      return value;
    }

    function appendInlineMarkdown(parent, source) {
      const text = String(source || "");
      const tokenPattern = /(\\*\\*[^*]+\\*\\*|`[^`]+`|\\[[^\\]]+\\]\\([^)]+\\)|\\*[^*]+\\*)/g;
      let lastIndex = 0;
      let match;
      while ((match = tokenPattern.exec(text)) !== null) {
        if (match.index > lastIndex) parent.appendChild(document.createTextNode(text.slice(lastIndex, match.index)));
        parent.appendChild(inlineMarkdownNode(match[0]));
        lastIndex = match.index + match[0].length;
      }
      if (lastIndex < text.length) parent.appendChild(document.createTextNode(text.slice(lastIndex)));
    }

    function inlineMarkdownNode(token) {
      if (token.startsWith("**") && token.endsWith("**")) {
        const strong = document.createElement("strong");
        strong.textContent = token.slice(2, -2);
        return strong;
      }
      if (token.startsWith("`") && token.endsWith("`")) {
        const code = document.createElement("code");
        code.textContent = token.slice(1, -1);
        return code;
      }
      const linkMatch = /^\\[([^\\]]+)\\]\\(([^)]+)\\)$/.exec(token);
      if (linkMatch) {
        const link = document.createElement("a");
        link.textContent = linkMatch[1];
        link.href = safeRichHref(linkMatch[2]);
        return link;
      }
      if (token.startsWith("*") && token.endsWith("*")) {
        const emphasis = document.createElement("em");
        emphasis.textContent = token.slice(1, -1);
        return emphasis;
      }
      return document.createTextNode(token);
    }

    function appendRichParagraph(root, text) {
      const paragraph = document.createElement("p");
      appendInlineMarkdown(paragraph, text);
      if (!paragraph.childNodes.length) paragraph.appendChild(document.createElement("br"));
      root.appendChild(paragraph);
    }

    function appendRichList(root, lines, ordered) {
      const list = document.createElement(ordered ? "ol" : "ul");
      lines.forEach((line) => {
        const item = document.createElement("li");
        appendInlineMarkdown(item, line);
        list.appendChild(item);
      });
      root.appendChild(list);
    }

    function markdownToRichHtml(markdown) {
      const root = document.createElement("div");
      const lines = String(markdown || "").replace(/\\r\\n/g, "\\n").split("\\n");
      let index = 0;
      while (index < lines.length) {
        const line = lines[index];
        if (!line.trim()) {
          index += 1;
          continue;
        }
        const fence = /^```([^`]*)\\s*$/.exec(line);
        if (fence) {
          const language = String(fence[1] || "").trim();
          const codeLines = [];
          index += 1;
          while (index < lines.length && !lines[index].startsWith("```")) {
            codeLines.push(lines[index]);
            index += 1;
          }
          if (index < lines.length) index += 1;
          const pre = document.createElement("pre");
          if (language) pre.dataset.codeLanguage = language;
          const code = document.createElement("code");
          code.textContent = codeLines.join("\\n");
          pre.appendChild(code);
          root.appendChild(pre);
          continue;
        }
        const heading = /^(#{1,6})\\s+(.*)$/.exec(line);
        if (heading) {
          const element = document.createElement("h" + heading[1].length);
          appendInlineMarkdown(element, heading[2]);
          root.appendChild(element);
          index += 1;
          continue;
        }
        if (/^[-*]\\s+/.test(line)) {
          const items = [];
          while (index < lines.length && /^[-*]\\s+/.test(lines[index])) {
            items.push(lines[index].replace(/^[-*]\\s+/, ""));
            index += 1;
          }
          appendRichList(root, items, false);
          continue;
        }
        if (/^\\d+\\.\\s+/.test(line)) {
          const items = [];
          while (index < lines.length && /^\\d+\\.\\s+/.test(lines[index])) {
            items.push(lines[index].replace(/^\\d+\\.\\s+/, ""));
            index += 1;
          }
          appendRichList(root, items, true);
          continue;
        }
        const paragraphLines = [];
        while (
          index < lines.length &&
          lines[index].trim() &&
          !/^```/.test(lines[index]) &&
          !/^(#{1,6})\\s+/.test(lines[index]) &&
          !/^[-*]\\s+/.test(lines[index]) &&
          !/^\\d+\\.\\s+/.test(lines[index])
        ) {
          paragraphLines.push(lines[index]);
          index += 1;
        }
        appendRichParagraph(root, paragraphLines.join(" "));
      }
      if (!root.childNodes.length) appendRichParagraph(root, "");
      return root.innerHTML;
    }

    function inlineMarkdownFromNode(node) {
      if (node.nodeType === Node.TEXT_NODE) return node.textContent || "";
      if (node.nodeType !== Node.ELEMENT_NODE) return "";
      const element = node;
      const tag = element.tagName;
      if (tag === "BR") return "\\n";
      const content = Array.from(element.childNodes).map(inlineMarkdownFromNode).join("");
      if (tag === "STRONG" || tag === "B") return `**${content}**`;
      if (tag === "EM" || tag === "I") return `*${content}*`;
      if (tag === "CODE" && element.closest("pre")) return element.textContent || "";
      if (tag === "CODE") return "`" + (element.textContent || "") + "`";
      if (tag === "A") return `[${content}](${safeRichHref(element.getAttribute("href") || "")})`;
      return content;
    }

    function richBlockToMarkdown(node) {
      if (node.nodeType === Node.TEXT_NODE) return (node.textContent || "").trim();
      if (node.nodeType !== Node.ELEMENT_NODE) return "";
      const element = node;
      const tag = element.tagName;
      if (/^H[1-6]$/.test(tag)) {
        return "#".repeat(Number(tag.slice(1))) + " " + inlineMarkdownFromNode(element).trim();
      }
      if (tag === "UL" || tag === "OL") {
        return Array.from(element.children)
          .filter((item) => item.tagName === "LI")
          .map((item, index) => (tag === "OL" ? `${index + 1}. ` : "- ") + inlineMarkdownFromNode(item).trim())
          .join("\\n");
      }
      if (tag === "PRE") {
        const code = element.querySelector("code") || element;
        const language = element.dataset.codeLanguage || "";
        return "```" + language + "\\n" + (code.textContent || "").replace(/\\n$/, "") + "\\n```";
      }
      return inlineMarkdownFromNode(element).trim();
    }

    function richHtmlToMarkdown(root) {
      if (!root) return "";
      return Array.from(root.childNodes)
        .map(richBlockToMarkdown)
        .filter((block) => block.length > 0)
        .join("\\n\\n")
        .trim();
    }

    function syncRichEditorToTextarea(textarea) {
      const editor = markdownEditor(textarea);
      const rich = markdownRichPanel(textarea);
      if (!textarea || !editor || !rich || editor.dataset.markdownMode !== "rich") return;
      textarea.value = richHtmlToMarkdown(rich);
      textarea.dispatchEvent(new Event("input", {bubbles: true}));
    }

    function syncAllRichEditors(root) {
      root?.querySelectorAll("[data-markdown-input]").forEach((textarea) => {
        syncRichEditorToTextarea(textarea);
      });
    }

    function setMarkdownEditorMode(textarea, mode) {
      const editor = markdownEditor(textarea);
      const preview = markdownPreviewPanel(textarea);
      const rich = markdownRichPanel(textarea);
      if (!editor || !preview || !rich) return;
      editor.dataset.markdownMode = mode;
      preview.hidden = mode !== "preview";
      rich.hidden = mode !== "rich";
      editor.querySelectorAll("[data-markdown-mode]").forEach((button) => {
        button.setAttribute("aria-selected", button.dataset.markdownMode === mode ? "true" : "false");
      });
    }

    async function renderMarkdownPreview(textarea) {
      if (!textarea) return false;
      syncRichEditorToTextarea(textarea);
      const preview = markdownPreviewPanel(textarea);
      if (!preview) return false;
      const response = await fetch("/api/markdown/preview", {
        method: "POST",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify({markdown: textarea.value || ""}),
      });
      if (!response.ok) {
        console.error(await response.text());
        return false;
      }
      const payload = await response.json();
      preview.innerHTML = payload.html || '<p class="markdown-empty">No content</p>';
      renderMermaidDiagrams(preview);
      return true;
    }

    function showMarkdownPreview(textarea) {
      syncRichEditorToTextarea(textarea);
      renderMarkdownPreview(textarea).then((rendered) => {
        if (rendered) setMarkdownEditorMode(textarea, "preview");
      });
    }

    function showMarkdownEdit(textarea) {
      syncRichEditorToTextarea(textarea);
      setMarkdownEditorMode(textarea, "edit");
      textarea?.focus();
    }

    function showMarkdownRich(textarea) {
      if (!textarea) return;
      const rich = markdownRichPanel(textarea);
      if (!rich) return;
      rich.innerHTML = markdownToRichHtml(textarea.value || "");
      setMarkdownEditorMode(textarea, "rich");
      rich.focus();
    }

    function resetMarkdownEditors(root) {
      root?.querySelectorAll("[data-markdown-input]").forEach((textarea) => {
        const preview = markdownPreviewPanel(textarea);
        if (preview) preview.innerHTML = "";
        const rich = markdownRichPanel(textarea);
        if (rich) rich.innerHTML = "";
        setMarkdownEditorMode(textarea, "edit");
      });
    }

    async function openTaskDetails(taskId) {
      const response = await fetch(`/api/tasks/${encodeURIComponent(taskId)}`);
      if (!response.ok) {
        console.error(await response.text());
        return;
      }
      const task = await response.json();
      if (taskDialog) taskDialog.dataset.taskId = task.id;
      setText("task-dialog-title", `${task.id} - ${task.title}`);
      setText("task-dialog-status", task.status);
      setText("task-dialog-path", task.path);
      setText("task-dialog-created", task.createdDate);
      setText("task-dialog-updated", task.updatedDate);
      setText("task-dialog-priority", task.priority);
      setText("task-dialog-assignees", (task.assignees || []).join(", "));
      setText("task-dialog-labels", (task.labels || []).join(", "));
      setText("task-dialog-milestone", task.milestone);
      setHtml("task-dialog-description-html", task.descriptionHtml);
      setHtml("task-dialog-implementation-notes", task.implementationNotesHtml);
      setHtml("task-dialog-final-summary", task.finalSummaryHtml);
      renderMermaidDiagrams(taskDialog || document);
      renderChecklist("task-dialog-acceptance", task.acceptanceCriteria, "acceptanceCriteria");
      renderChecklist("task-dialog-dod", task.definitionOfDone, "definitionOfDone");
      if (taskDialog && taskDialog.showModal) taskDialog.showModal();
      else if (taskDialog) taskDialog.setAttribute("open", "open");
    }

    async function openTaskEdit(taskId) {
      const response = await fetch(`/api/tasks/${encodeURIComponent(taskId)}`);
      if (!response.ok) {
        console.error(await response.text());
        return;
      }
      const task = await response.json();
      if (!taskEditForm) return;
      taskEditForm.dataset.taskId = task.id;
      taskEditForm.elements.title.value = task.title || "";
      taskEditForm.elements.status.value = task.status || "";
      taskEditForm.elements.priority.value = task.priority || "";
      taskEditForm.elements.milestone.value = task.milestone || "";
      taskEditForm.elements.assignees.value = (task.assignees || []).join(", ");
      taskEditForm.elements.labels.value = (task.labels || []).join(", ");
      taskEditForm.elements.description.value = task.description || "";
      taskEditForm.elements.acceptanceCriteria.value = checklistText(task.acceptanceCriteria);
      taskEditForm.elements.implementationNotes.value = task.implementationNotes || "";
      taskEditForm.elements.finalSummary.value = task.finalSummary || "";
      resetMarkdownEditors(taskEditForm);
      setText("task-edit-title", `${task.id} - Edit task`);
      if (taskEditDialog && taskEditDialog.showModal) taskEditDialog.showModal();
      else if (taskEditDialog) taskEditDialog.setAttribute("open", "open");
    }

    async function openTaskArchive(taskId) {
      const response = await fetch(`/api/tasks/${encodeURIComponent(taskId)}`);
      if (!response.ok) {
        console.error(await response.text());
        return;
      }
      const task = await response.json();
      if (taskArchiveConfirm) taskArchiveConfirm.dataset.taskId = task.id;
      setText("task-archive-title", `${task.id} - Archive task`);
      setText("task-archive-name", task.title);
      if (taskArchiveDialog && taskArchiveDialog.showModal) taskArchiveDialog.showModal();
      else if (taskArchiveDialog) taskArchiveDialog.setAttribute("open", "open");
    }

    function openTaskCreate() {
      if (taskCreateForm) taskCreateForm.reset();
      resetMarkdownEditors(taskCreateForm);
      if (taskCreateDialog && taskCreateDialog.showModal) taskCreateDialog.showModal();
      else if (taskCreateDialog) taskCreateDialog.setAttribute("open", "open");
    }

    async function openConfigSettings() {
      const response = await fetch("/api/settings/config");
      if (!response.ok) {
        console.error(await response.text());
        return;
      }
      const payload = await response.json();
      const settings = payload.settings || {};
      if (configSettingsForm) {
        configSettingsForm.elements.projectName.value = settings.projectName || "";
        configSettingsForm.elements.defaultAssignee.value = settings.defaultAssignee || "";
        configSettingsForm.elements.defaultStatus.value = settings.defaultStatus || "";
        configSettingsForm.elements.dateFormat.value = settings.dateFormat || "";
        configSettingsForm.elements.defaultPort.value = settings.defaultPort || "";
        configSettingsForm.elements.activeBranchDays.value = settings.activeBranchDays || "";
        configSettingsForm.elements.zeroPaddedIds.value = settings.zeroPaddedIds || "";
        configSettingsForm.elements.statuses.value = (settings.statuses || []).join("\\n");
        configSettingsForm.elements.includeDatetimeInDates.checked = Boolean(settings.includeDatetimeInDates);
        configSettingsForm.elements.autoOpenBrowser.checked = Boolean(settings.autoOpenBrowser);
        configSettingsForm.elements.remoteOperations.checked = Boolean(settings.remoteOperations);
        configSettingsForm.elements.checkActiveBranches.checked = Boolean(settings.checkActiveBranches);
        configSettingsForm.elements.autoCommit.checked = Boolean(settings.autoCommit);
      }
      if (configSettingsDialog && configSettingsDialog.showModal) configSettingsDialog.showModal();
      else if (configSettingsDialog) configSettingsDialog.setAttribute("open", "open");
    }

    async function openDodDefaultsSettings() {
      const response = await fetch("/api/settings/dod-defaults");
      if (!response.ok) {
        console.error(await response.text());
        return;
      }
      const payload = await response.json();
      if (dodDefaultsForm) {
        dodDefaultsForm.elements.items.value = (payload.items || []).join("\\n");
      }
      if (dodDefaultsDialog && dodDefaultsDialog.showModal) dodDefaultsDialog.showModal();
      else if (dodDefaultsDialog) dodDefaultsDialog.setAttribute("open", "open");
    }

    async function refreshServiceStatus() {
      const response = await fetch("/api/service/status", {
        headers: {"Accept": "application/json"},
        cache: "no-store",
      });
      if (!response.ok) {
        console.error(await response.text());
        return false;
      }
      const status = await response.json();
      setText("service-status-project", status.projectName);
      setText("service-status-root", status.projectRoot);
      setText("service-status-backlog", status.backlogDir);
      setText("service-status-host", status.host);
      setText("service-status-port", String(status.port || ""));
      setText("service-status-url", status.rootUrl);
      if (serviceShutdownConfirm) serviceShutdownConfirm.disabled = Boolean(status.shutdownInProgress);
      if (status.shutdownInProgress) {
        const requestedAt = status.shutdownRequestedAt ? ` at ${status.shutdownRequestedAt}` : "";
        setText("service-status-message", `Shutdown has been requested${requestedAt}.`);
      } else {
        setText("service-status-message", status.shutdownSupported ? "Shutdown is available from this local browser session." : "");
      }
      return true;
    }

    function renderServiceRequestLog(requests) {
      const list = document.getElementById("service-request-log");
      if (!list) return;
      list.replaceChildren();
      if (!requests || requests.length === 0) {
        const empty = document.createElement("li");
        empty.textContent = "No requests recorded";
        list.appendChild(empty);
        return;
      }
      requests.slice(-10).reverse().forEach((request) => {
        const item = document.createElement("li");
        item.textContent = `${request.timestamp} ${request.method} ${request.path} ${request.status}`;
        list.appendChild(item);
      });
    }

    async function refreshServiceRequests() {
      const response = await fetch("/api/service/requests", {
        headers: {"Accept": "application/json"},
        cache: "no-store",
      });
      if (!response.ok) {
        console.error(await response.text());
        return false;
      }
      const payload = await response.json();
      renderServiceRequestLog(payload.requests || []);
      return true;
    }

    async function refreshServicePanel() {
      const loaded = await refreshServiceStatus();
      if (!loaded) return false;
      await refreshServiceRequests();
      return true;
    }

    async function openServiceStatus() {
      const loaded = await refreshServicePanel();
      if (!loaded) return;
      if (serviceStatusDialog && serviceStatusDialog.showModal) serviceStatusDialog.showModal();
      else if (serviceStatusDialog) serviceStatusDialog.setAttribute("open", "open");
    }

    async function submitServiceShutdown(event) {
      event.preventDefault();
      const response = await fetch("/api/service/shutdown", {
        method: "POST",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify({}),
      });
      if (!response.ok) {
        console.error(await response.text());
        return;
      }
      const payload = await response.json();
      if (serviceShutdownConfirm) serviceShutdownConfirm.disabled = Boolean(payload.shutdownInProgress);
      setText("service-status-message", payload.message || "Server is stopping.");
    }

    async function submitTaskCreate(event) {
      event.preventDefault();
      const form = event.currentTarget;
      syncAllRichEditors(taskCreateForm);
      const data = new FormData(form);
      const criteria = String(data.get("acceptanceCriteria") || "")
        .split("\\n")
        .map((item) => item.trim())
        .filter(Boolean);
      const response = await fetch("/api/tasks", {
        method: "POST",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify({
          title: String(data.get("title") || ""),
          status: String(data.get("status") || ""),
          description: String(data.get("description") || ""),
          acceptanceCriteria: criteria,
        }),
      });
      if (!response.ok) {
        console.error(await response.text());
        return;
      }
      window.location.reload();
    }

    async function submitTaskEdit(event) {
      event.preventDefault();
      const form = event.currentTarget;
      const taskId = form.dataset.taskId;
      if (!taskId) return;
      syncAllRichEditors(taskEditForm);
      const data = new FormData(form);
      const criteria = String(data.get("acceptanceCriteria") || "")
        .split("\\n")
        .map((item) => item.trim())
        .filter(Boolean);
      const response = await fetch(`/api/tasks/${encodeURIComponent(taskId)}/edit`, {
        method: "POST",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify({
          title: String(data.get("title") || ""),
          status: String(data.get("status") || ""),
          priority: String(data.get("priority") || ""),
          milestone: String(data.get("milestone") || ""),
          assignees: metadataList(data.get("assignees")),
          labels: metadataList(data.get("labels")),
          description: String(data.get("description") || ""),
          acceptanceCriteria: criteria,
          implementationNotes: String(data.get("implementationNotes") || ""),
          finalSummary: String(data.get("finalSummary") || ""),
        }),
      });
      if (!response.ok) {
        console.error(await response.text());
        return;
      }
      window.location.reload();
    }

    async function submitTaskChecklistState(event) {
      const checkbox = event.currentTarget;
      const taskId = taskDialog?.dataset.taskId;
      const section = checkbox?.dataset.checklistSection;
      const index = Number(checkbox?.dataset.checklistIndex);
      if (!taskId || !section || !Number.isInteger(index)) return;
      const response = await fetch(`/api/tasks/${encodeURIComponent(taskId)}/checklist`, {
        method: "POST",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify({section, index, checked: checkbox.checked}),
      });
      if (!response.ok) {
        console.error(await response.text());
        checkbox.checked = !checkbox.checked;
        return;
      }
      const payload = await response.json();
      setText("task-dialog-updated", payload.task.updatedDate);
      renderChecklist("task-dialog-acceptance", payload.task.acceptanceCriteria, "acceptanceCriteria");
      renderChecklist("task-dialog-dod", payload.task.definitionOfDone, "definitionOfDone");
    }

    async function submitTaskArchive(event) {
      event.preventDefault();
      const taskId = taskArchiveConfirm?.dataset.taskId;
      if (!taskId) return;
      const response = await fetch(`/api/tasks/${encodeURIComponent(taskId)}/archive`, {
        method: "POST",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify({}),
      });
      if (!response.ok) {
        console.error(await response.text());
        return;
      }
      window.location.reload();
    }

    async function submitConfigSettings(event) {
      event.preventDefault();
      const form = event.currentTarget;
      const data = new FormData(form);
      const statuses = String(data.get("statuses") || "")
        .split("\\n")
        .map((item) => item.trim())
        .filter(Boolean);
      const response = await fetch("/api/settings/config", {
        method: "POST",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify({
          settings: {
            projectName: String(data.get("projectName") || ""),
            defaultAssignee: String(data.get("defaultAssignee") || ""),
            defaultStatus: String(data.get("defaultStatus") || ""),
            dateFormat: String(data.get("dateFormat") || ""),
            defaultPort: Number(data.get("defaultPort") || 0),
            activeBranchDays: Number(data.get("activeBranchDays") || 0),
            zeroPaddedIds: String(data.get("zeroPaddedIds") || ""),
            statuses,
            includeDatetimeInDates: Boolean(form.elements.includeDatetimeInDates?.checked),
            autoOpenBrowser: Boolean(form.elements.autoOpenBrowser?.checked),
            remoteOperations: Boolean(form.elements.remoteOperations?.checked),
            checkActiveBranches: Boolean(form.elements.checkActiveBranches?.checked),
            autoCommit: Boolean(form.elements.autoCommit?.checked),
          },
        }),
      });
      if (!response.ok) {
        console.error(await response.text());
        return;
      }
      window.location.reload();
    }

    async function submitDodDefaultsSettings(event) {
      event.preventDefault();
      const form = event.currentTarget;
      const data = new FormData(form);
      const items = String(data.get("items") || "")
        .split("\\n")
        .map((item) => item.trim())
        .filter(Boolean);
      const response = await fetch("/api/settings/dod-defaults", {
        method: "POST",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify({items}),
      });
      if (!response.ok) {
        console.error(await response.text());
        return;
      }
      window.location.reload();
    }

    document.getElementById("task-create-open")?.addEventListener("click", openTaskCreate);
    document.getElementById("task-create-cancel")?.addEventListener("click", () => taskCreateDialog?.close());
    taskCreateForm?.addEventListener("submit", submitTaskCreate);
    document.getElementById("task-edit-cancel")?.addEventListener("click", () => taskEditDialog?.close());
    taskEditForm?.addEventListener("submit", submitTaskEdit);
    document.getElementById("task-archive-cancel")?.addEventListener("click", () => taskArchiveDialog?.close());
    taskArchiveConfirm?.addEventListener("click", submitTaskArchive);
    document.getElementById("config-settings-open")?.addEventListener("click", openConfigSettings);
    document.getElementById("config-settings-cancel")?.addEventListener("click", () => configSettingsDialog?.close());
    configSettingsForm?.addEventListener("submit", submitConfigSettings);
    document.getElementById("dod-defaults-open")?.addEventListener("click", openDodDefaultsSettings);
    document.getElementById("dod-defaults-cancel")?.addEventListener("click", () => dodDefaultsDialog?.close());
    dodDefaultsForm?.addEventListener("submit", submitDodDefaultsSettings);
    document.getElementById("documents-open")?.addEventListener("click", openDocuments);
    document.getElementById("decisions-open")?.addEventListener("click", openDecisions);
    document.getElementById("service-status-open")?.addEventListener("click", openServiceStatus);
    document.getElementById("service-status-refresh")?.addEventListener("click", refreshServicePanel);
    serviceShutdownConfirm?.addEventListener("click", submitServiceShutdown);
    document.querySelectorAll("[data-markdown-command]").forEach((button) => {
      button.addEventListener("click", () => {
        applyMarkdownFormat(toolbarTextarea(button), button.dataset.markdownCommand);
      });
    });
    document.querySelectorAll("button[data-markdown-mode]").forEach((button) => {
      button.addEventListener("click", () => {
        const target = button.dataset.markdownTarget || "";
        const textarea = document.getElementById(target);
        if (!(textarea instanceof HTMLTextAreaElement)) return;
        if (button.dataset.markdownMode === "preview") showMarkdownPreview(textarea);
        else if (button.dataset.markdownMode === "rich") showMarkdownRich(textarea);
        else showMarkdownEdit(textarea);
      });
    });
    document.querySelectorAll("[data-markdown-rich-for]").forEach((rich) => {
      rich.addEventListener("input", () => {
        const target = rich.dataset.markdownRichFor || "";
        const textarea = document.getElementById(target);
        if (textarea instanceof HTMLTextAreaElement) syncRichEditorToTextarea(textarea);
      });
    });

    document.querySelectorAll("[data-task-details]").forEach((button) => {
      button.addEventListener("click", (event) => {
        event.stopPropagation();
        openTaskDetails(button.dataset.taskDetails);
      });
    });
    document.querySelectorAll("[data-task-edit]").forEach((button) => {
      button.addEventListener("click", (event) => {
        event.stopPropagation();
        openTaskEdit(button.dataset.taskEdit);
      });
    });
    document.querySelectorAll("[data-task-archive]").forEach((button) => {
      button.addEventListener("click", (event) => {
        event.stopPropagation();
        openTaskArchive(button.dataset.taskArchive);
      });
    });

    document.querySelectorAll("[data-task-id]").forEach((task) => {
      task.addEventListener("dragstart", (event) => {
        draggedTaskId = task.dataset.taskId;
        event.dataTransfer.setData("text/plain", draggedTaskId);
      });
    });
    document.querySelectorAll("[data-status]").forEach((column) => {
      column.addEventListener("dragover", (event) => {
        event.preventDefault();
        column.classList.add("drag-over");
      });
      column.addEventListener("dragleave", () => column.classList.remove("drag-over"));
      column.addEventListener("drop", async (event) => {
        event.preventDefault();
        column.classList.remove("drag-over");
        const taskId = event.dataTransfer.getData("text/plain") || draggedTaskId;
        const status = column.dataset.status;
        if (!taskId || !status) return;
        const response = await fetch(`/api/tasks/${encodeURIComponent(taskId)}/status`, {
          method: "POST",
          headers: {"Content-Type": "application/json"},
          body: JSON.stringify({status}),
        });
        if (!response.ok) {
          console.error(await response.text());
          return;
        }
        window.location.reload();
      });
    });
    if (!connectBoardRevisionEvents()) startBoardRevisionPolling();
    document.addEventListener("visibilitychange", () => {
      if (!document.hidden) pollBoardRevision();
    });

