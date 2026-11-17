(() => {
  "use strict";

  const EXTRA_FIELDS = [
    ["id_chip", "ID do chip", "text"],
    ["dt_inicio", "Data de início", "date"],
    ["ultima_recarga_valor", "Última recarga (R$)", "number", "0.01"],
    ["ultima_recarga_data", "Data da última recarga", "date"],
    ["total_gasto", "Total gasto (R$)", "number", "0.01"],
    ["ativo", "Ativo", "select"],
    ["maturando_em", "Início da maturação", "datetime-local"],
    ["created_at", "Data de cadastro", "datetime-local"],
  ];

  function toast(message, type = "info") {
    if (typeof window.showToast === "function") window.showToast(message, type);
    else alert(message);
  }

  function inputValue(value, type) {
    if (value == null) return "";
    if (type === "date") return String(value).slice(0, 10);
    if (type === "datetime-local") return String(value).replace("Z", "").slice(0, 16);
    return value;
  }

  function ensureExtraFields() {
    const form = document.getElementById("modalForm");
    const actions = form?.querySelector(".modal-actions");
    if (!form || !actions || document.getElementById("modal_id_chip")) return;

    EXTRA_FIELDS.forEach(([name, label, type, step]) => {
      const wrapper = document.createElement("div");
      wrapper.className = "form-group";
      const labelEl = document.createElement("label");
      labelEl.textContent = label;
      let field;
      if (type === "select") {
        field = document.createElement("select");
        field.innerHTML = '<option value="true">Sim</option><option value="false">Não</option>';
      } else {
        field = document.createElement("input");
        field.type = type;
        if (step) field.step = step;
      }
      field.id = `modal_${name}`;
      field.name = name;
      wrapper.append(labelEl, field);
      form.insertBefore(wrapper, actions);
    });

    const warning = document.createElement("p");
    warning.className = "chip-muted full";
    warning.textContent = "SK e data de atualização são controlados automaticamente e não podem ser alterados.";
    form.insertBefore(warning, actions);
  }

  function fillExtraFields(chip) {
    EXTRA_FIELDS.forEach(([name, , type]) => {
      const el = document.getElementById(`modal_${name}`);
      if (el) el.value = inputValue(chip?.[name], type);
    });
  }

  function replaceSaveButton() {
    const oldButton = document.getElementById("modalSaveBtn");
    if (!oldButton || oldButton.dataset.crudComplete === "true") return;
    const button = oldButton.cloneNode(true);
    button.dataset.crudComplete = "true";
    oldButton.replaceWith(button);

    button.addEventListener("click", async () => {
      const form = document.getElementById("modalForm");
      if (!form?.reportValidity()) return;
      const original = button.innerHTML;
      button.disabled = true;
      button.innerHTML = 'Salvando <span class="spinner"></span>';
      try {
        const payload = {};
        new FormData(form).forEach((value, key) => { payload[key] = value === "" ? null : value; });
        payload.sk_chip = Number(payload.sk_chip);
        payload.qt_disparos = Number(payload.qt_disparos || 0);
        payload.qt_banimentos = Number(payload.qt_banimentos || 0);
        payload.ultima_recarga_valor = payload.ultima_recarga_valor == null ? null : Number(payload.ultima_recarga_valor);
        payload.total_gasto = payload.total_gasto == null ? null : Number(payload.total_gasto);
        payload.ativo = String(payload.ativo) === "true";

        const response = await fetch("/chips/update-completo-json", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(payload),
        });
        const result = await response.json();
        if (!response.ok || !result.success) throw new Error(result.error || "Erro ao atualizar chip");
        toast("Todas as informações do chip foram atualizadas.", "success");
        setTimeout(() => location.reload(), 450);
      } catch (error) {
        console.error("[Chips CRUD] Falha ao atualizar", error);
        toast(error.message || "Falha ao atualizar chip", "error");
      } finally {
        button.disabled = false;
        button.innerHTML = original;
      }
    });
  }

  function addDeleteButtons() {
    document.querySelectorAll("#tableBody tr").forEach(row => {
      const actions = row.querySelector(".quick-actions");
      const edit = actions?.querySelector(".edit-btn");
      if (!actions || !edit || actions.querySelector(".delete-chip-btn")) return;
      const button = document.createElement("button");
      button.type = "button";
      button.className = "action-btn delete-chip-btn";
      button.title = "Excluir chip permanentemente";
      button.dataset.sk = edit.dataset.sk;
      button.innerHTML = '<i class="fas fa-trash-alt"></i>';
      actions.appendChild(button);
    });

    document.querySelectorAll(".delete-chip-btn").forEach(button => {
      button.onclick = async () => {
        const sk = Number(button.dataset.sk);
        const confirmation = prompt(`Esta ação exclui permanentemente o chip SK ${sk} e seus registros vinculados.\n\nDigite EXCLUIR para confirmar:`);
        if (confirmation == null) return;
        if (confirmation.trim().toUpperCase() !== "EXCLUIR") {
          toast("Exclusão cancelada: confirmação incorreta.", "error");
          return;
        }
        button.disabled = true;
        try {
          const response = await fetch("/chips/delete-json", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ sk_chip: sk, confirmation }),
          });
          const result = await response.json();
          if (!response.ok || !result.success) throw new Error(result.error || "Erro ao excluir chip");
          toast("Chip excluído permanentemente.", "success");
          button.closest("tr")?.remove();
          setTimeout(() => location.reload(), 500);
        } catch (error) {
          console.error("[Chips CRUD] Falha ao excluir", error);
          toast(error.message || "Falha ao excluir chip", "error");
          button.disabled = false;
        }
      };
    });
  }

  ensureExtraFields();
  replaceSaveButton();

  if (typeof window.abrirModalEdicao === "function") {
    const originalOpen = window.abrirModalEdicao;
    window.abrirModalEdicao = function enhancedOpen(chip) {
      originalOpen(chip);
      fillExtraFields(chip);
    };
  }

  if (typeof window.renderRows === "function") {
    const originalRender = window.renderRows;
    window.renderRows = function enhancedRender(list) {
      originalRender(list);
      addDeleteButtons();
    };
  }

  addDeleteButtons();
})();
