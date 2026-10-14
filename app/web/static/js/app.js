if ("serviceWorker" in navigator) {
  window.addEventListener("load", () => {
    navigator.serviceWorker
      .register("/sw.js")
      .catch((err) => console.warn("Service Worker nicht registriert:", err));
  });
}

// htmx does not swap error responses, so failed requests would otherwise go unnoticed.
(() => {
  let hideTimer;
  const showToast = (message) => {
    const toast = document.getElementById("toast");
    if (!toast) return;
    toast.textContent = message;
    toast.hidden = false;
    clearTimeout(hideTimer);
    hideTimer = setTimeout(() => {
      toast.hidden = true;
    }, 6000);
  };

  document.addEventListener("htmx:responseError", (event) => {
    const status = event.detail.xhr.status;
    showToast(`Aktualisieren fehlgeschlagen (HTTP ${status}). Seite neu laden und noch einmal versuchen.`);
  });
  document.addEventListener("htmx:sendError", () => {
    showToast("Keine Verbindung zum Server. Prüfe deine Netzwerkverbindung und ob der Server erreichbar ist.");
  });
  document.addEventListener("htmx:timeout", () => {
    showToast("Kickbase antwortet gerade sehr langsam. Später noch einmal versuchen.");
  });
})();
