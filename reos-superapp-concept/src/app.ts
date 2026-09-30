type HealthState = "verified" | "recovered" | "current" | "watch";

type HealthFact = {
  label: string;
  value: string;
  detail: string;
  state: HealthState;
};

type Deal = {
  address: string;
  stage: string;
  summary: string;
  facts: HealthFact[];
};

const deals: Record<string, Deal> = {
  land: {
    address: "3216 LAND CT, CHEYENNE",
    stage: "Closing in 4 days",
    summary: "ATLAS recovered lender evidence overnight. One money-related decision needs your authority.",
    facts: [
      { label: "CONTRACT", value: "Verified", detail: "2 sources agree", state: "verified" },
      { label: "DEADLINES", value: "Resolved", detail: "WY rules · current", state: "verified" },
      { label: "COMMUNICATION", value: "Recovered", detail: "2m ago", state: "recovered" },
      { label: "ATLAS BRIEF", value: "Current", detail: "8:12 PM", state: "current" },
    ],
  },
  north: {
    address: "1650 NORTH RIDGE DR, CASPER",
    stage: "Under contract · 11 days",
    summary: "A wire-advisory signature is the only unresolved trust item. ATLAS is monitoring the inbox.",
    facts: [
      { label: "CONTRACT", value: "Verified", detail: "executed copy", state: "verified" },
      { label: "DEADLINES", value: "Resolved", detail: "12 milestones", state: "verified" },
      { label: "COMMUNICATION", value: "Watching", detail: "signature absent", state: "watch" },
      { label: "ATLAS BRIEF", value: "Current", detail: "7:58 PM", state: "current" },
    ],
  },
  meadow: {
    address: "29 MOUNTAIN MEADOW RD, ALBANY",
    stage: "Clear to close",
    summary: "All closing prerequisites are verified. ATLAS is watching for the final settlement statement.",
    facts: [
      { label: "CONTRACT", value: "Verified", detail: "no conflicts", state: "verified" },
      { label: "DEADLINES", value: "Complete", detail: "closing Monday", state: "verified" },
      { label: "COMMUNICATION", value: "Current", detail: "11m ago", state: "current" },
      { label: "ATLAS BRIEF", value: "Current", detail: "7:46 PM", state: "current" },
    ],
  },
};

const required = <T extends Element>(selector: string): T => {
  const element = document.querySelector<T>(selector);
  if (!element) throw new Error(`Missing required element: ${selector}`);
  return element;
};

const toast = required<HTMLDivElement>("#toast");
let toastTimer: ReturnType<typeof setTimeout> | undefined;

const showToast = (message: string): void => {
  window.clearTimeout(toastTimer);
  toast.textContent = message;
  toast.classList.add("visible");
  toastTimer = window.setTimeout(() => toast.classList.remove("visible"), 2600);
};

const renderTruthStrip = (facts: HealthFact[]): void => {
  const strip = required<HTMLDivElement>("#truth-strip");
  const cells = facts.map((fact) => {
    const cell = document.createElement("div");
    cell.dataset.health = fact.state;

    const label = document.createElement("span");
    label.textContent = fact.label;
    const value = document.createElement("strong");
    value.textContent = fact.value;
    const detail = document.createElement("small");
    detail.textContent = fact.detail;

    cell.append(label, value, detail);
    return cell;
  });

  strip.replaceChildren(...cells);
};

const selectDeal = (dealId: string): void => {
  const deal = deals[dealId];
  if (!deal) return;

  document.querySelectorAll<HTMLButtonElement>("[data-deal-id]").forEach((button) => {
    button.classList.toggle("active", button.dataset.dealId === dealId);
  });

  required<HTMLElement>("#focus-address").textContent = deal.address;
  required<HTMLElement>("#focus-stage").textContent = deal.stage;
  required<HTMLElement>("#focus-summary").textContent = deal.summary;
  renderTruthStrip(deal.facts);
  showToast(`${deal.address} is now the focused property event.`);
};

document.querySelectorAll<HTMLButtonElement>("[data-deal-id]").forEach((button) => {
  button.addEventListener("click", () => selectDeal(button.dataset.dealId ?? ""));
});

document.querySelectorAll<HTMLButtonElement>("[data-queue-item] .queue-check").forEach((button) => {
  button.addEventListener("click", () => {
    const item = button.closest<HTMLElement>("[data-queue-item]");
    if (!item) return;

    const isComplete = !item.classList.contains("completed");
    item.classList.toggle("completed", isComplete);
    button.setAttribute("aria-pressed", String(isComplete));
    showToast(isComplete ? "Marked complete. ATLAS will verify downstream state." : "Reopened. ATLAS is watching the obligation again.");
  });
});

const drawer = required<HTMLElement>("#review-drawer");
const drawerBackdrop = required<HTMLElement>(".drawer-backdrop");

const setDrawerOpen = (open: boolean): void => {
  drawerBackdrop.hidden = !open;
  drawer.classList.toggle("open", open);
  drawer.setAttribute("aria-hidden", String(!open));
  document.body.style.overflow = open ? "hidden" : "";

  if (open) {
    window.requestAnimationFrame(() => drawer.querySelector<HTMLButtonElement>("button")?.focus());
  }
};

document.querySelectorAll<HTMLElement>("[data-action=\"open-review\"]").forEach((button) => {
  button.addEventListener("click", () => setDrawerOpen(true));
});

document.querySelectorAll<HTMLElement>("[data-action=\"close-review\"]").forEach((button) => {
  button.addEventListener("click", () => setDrawerOpen(false));
});

document.addEventListener("keydown", (event) => {
  if (event.key === "Escape" && drawer.classList.contains("open")) setDrawerOpen(false);
});

document.querySelectorAll<HTMLButtonElement>("[data-action=\"approve-move\"]").forEach((button) => {
  button.addEventListener("click", () => {
    button.classList.add("approved");
    button.textContent = "Approved ✓";
    button.disabled = true;
    button.closest<HTMLElement>("[data-review-action]")?.classList.add("approved");
    showToast("Approved. ATLAS is executing inside the stated permit and will create a receipt.");
  });
});

const receipt = required<HTMLElement>("[data-receipt]");
const undoReceipt = required<HTMLButtonElement>("[data-action=\"undo-receipt\"]");

undoReceipt.addEventListener("click", () => {
  receipt.classList.add("reversed");
  undoReceipt.textContent = "Change reversed";
  undoReceipt.disabled = true;
  showToast("Safe change reversed. The original evidence remains preserved.");
});

const messages = required<HTMLDivElement>("#atlas-messages");
const form = required<HTMLFormElement>("#atlas-command-form");
const command = required<HTMLTextAreaElement>("#atlas-command");

const appendMessage = (text: string, role: "user" | "atlas"): void => {
  const message = document.createElement("div");
  message.className = `message ${role === "user" ? "user-message" : "atlas-message"}`;
  message.textContent = text;
  messages.append(message);
  messages.scrollTop = messages.scrollHeight;
};

const answerAtlas = (question: string): string => {
  const normalized = question.toLowerCase();

  if (normalized.includes("source") || normalized.includes("conflict")) {
    return "The lender portal is unavailable, but the executed contract and signed Gmail attachment agree on property, amount, parties, and timestamp. I used the attachment as alternate evidence; I did not replace the original source.";
  }

  if (normalized.includes("need") || normalized.includes("today")) {
    return "One decision still needs your authority: approve the buyer confirmation for 3216 Land Ct. I can safely execute the two scheduling moves after approval. Evidence: Today queue, contract milestone, and Gmail receipt.";
  }

  return "I kept the current property, evidence set, failed path, and permit boundary in context. I found no new conflict. Ask me to inspect a source, compare a fact, or prepare a bounded action.";
};

form.addEventListener("submit", (event) => {
  event.preventDefault();
  const question = command.value.trim();
  if (!question) return;

  appendMessage(question, "user");
  command.value = "";
  window.setTimeout(() => appendMessage(answerAtlas(question), "atlas"), 280);
});

command.addEventListener("keydown", (event) => {
  if (event.key === "Enter" && (event.metaKey || event.ctrlKey)) {
    event.preventDefault();
    form.requestSubmit();
  }
});

document.querySelectorAll<HTMLButtonElement>("[data-prompt]").forEach((button) => {
  button.addEventListener("click", () => {
    command.value = button.dataset.prompt ?? "";
    form.requestSubmit();
  });
});

const showView = (viewName: "today" | "signals"): void => {
  document.querySelectorAll<HTMLElement>("[data-view]").forEach((view) => {
    view.hidden = view.dataset.view !== viewName;
  });
};

document.querySelectorAll<HTMLButtonElement>("[data-nav]").forEach((button) => {
  button.addEventListener("click", () => {
    const destination = button.dataset.nav;

    if (destination !== "today" && destination !== "signals") {
      showToast(`${button.textContent?.trim() ?? "This destination"} belongs to the next production integration horizon.`);
      return;
    }

    document.querySelectorAll<HTMLButtonElement>("[data-nav]").forEach((item) => {
      const active = item === button;
      item.classList.toggle("active", active);
      if (active) item.setAttribute("aria-current", "page");
      else item.removeAttribute("aria-current");
    });

    showView(destination);
  });
});

document.querySelectorAll<HTMLSelectElement>("[data-frequency]").forEach((select) => {
  select.addEventListener("change", () => {
    const label = select.closest<HTMLElement>("[data-signal-class]")?.dataset.signalClass ?? "signal";
    showToast(`${label[0]?.toUpperCase() ?? "S"}${label.slice(1)} frequency set to ${select.value}.`);
  });
});

document.querySelector<HTMLButtonElement>(".mobile-menu")?.addEventListener("click", () => {
  showToast("Mobile navigation would open as a compact five-destination sheet.");
});

if (window.location.hash === "#signals") {
  document.querySelector<HTMLButtonElement>("[data-nav=\"signals\"]")?.click();
}

if (window.location.hash === "#review") {
  setDrawerOpen(true);
}
