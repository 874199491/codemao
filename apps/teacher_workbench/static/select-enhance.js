(function () {
  const instances = new WeakMap();
  function syncSelect(select) {
    if (!select || select.closest(".performance-page") || select.id === "studentClassTime") return;
    const options = Array.from(select.options).map((option) => ({
      text: option.textContent,
      value: option.value,
      selected: option.selected,
      disabled: option.disabled,
    }));
    const value = select.value;
    const previous = instances.get(select);
    if (previous) previous.destroy();
    const instance = new SlimSelect({
      select,
      settings: { showSearch: options.length > 8, allowDeselect: true },
    });
    instances.set(select, instance);
    if (value && options.some((option) => option.value === value)) instance.setSelected(value);
  }
  function syncAll(root = document) {
    root.querySelectorAll("select").forEach(syncSelect);
  }
  window.refreshStyledSelects = syncAll;
  document.addEventListener("DOMContentLoaded", () => {
    syncAll();
  });
})();
