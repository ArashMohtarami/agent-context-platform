"use strict";

const tabs = [...document.querySelectorAll('[role="tab"]')];
function selectTab(tab, focus = false) {
  for (const item of tabs) {
    const selected = item === tab;
    item.setAttribute("aria-selected", String(selected));
    item.tabIndex = selected ? 0 : -1;
    document.getElementById(item.getAttribute("aria-controls")).hidden = !selected;
  }
  if (focus) tab.focus();
}
for (const tab of tabs) {
  tab.addEventListener("click", () => selectTab(tab));
  tab.addEventListener("keydown", (event) => {
    const index = tabs.indexOf(tab);
    let next;
    if (event.key === "ArrowRight") next = tabs[(index + 1) % tabs.length];
    if (event.key === "ArrowLeft") next = tabs[(index + tabs.length - 1) % tabs.length];
    if (event.key === "Home") next = tabs[0];
    if (event.key === "End") next = tabs[tabs.length - 1];
    if (next) { event.preventDefault(); selectTab(next, true); }
  });
}

for (const button of document.querySelectorAll("[data-copy]")) {
  button.addEventListener("click", async () => {
    const source = document.getElementById(button.dataset.copy);
    const status = document.getElementById("copy-status");
    try {
      if (!navigator.clipboard?.writeText) throw new Error("Clipboard unavailable");
      await navigator.clipboard.writeText(source.textContent);
      status.textContent = "Commands copied.";
    } catch {
      // HTTP on a public IP may lack the Clipboard API. Selecting the text
      // keeps the recipe usable without executing it or requesting credentials.
      const range = document.createRange();
      range.selectNodeContents(source);
      const selection = window.getSelection();
      selection.removeAllRanges();
      selection.addRange(range);
      status.textContent = "Commands selected. Use your browser's copy command.";
    }
    window.clearTimeout(button.statusTimeout);
    button.statusTimeout = window.setTimeout(() => { status.textContent = ""; }, 4500);
  });
}
