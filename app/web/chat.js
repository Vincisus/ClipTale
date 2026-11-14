const button = document.getElementById("generate");
const status = document.getElementById("status") || document.getElementById("current-step");
const log = document.getElementById("log");
const progressFill = document.getElementById("progress-fill");
const progressText = document.getElementById("progress-text");
const currentStep = document.getElementById("current-step");

function addLog(text) {
  const line = document.createElement("div");
  line.className = "line";
  line.textContent = text;
  log.appendChild(line);
  log.scrollTop = log.scrollHeight;
}

function updateProgress(job) {
  progressFill.style.width = (job.progress || 0) + "%";
  progressText.textContent = (job.progress || 0) + "%";
  const stepEl = document.getElementById("current-step");
  if (stepEl) stepEl.textContent = job.current_step || "In attesa";
}

button.addEventListener("click", async () => {
  button.disabled = true;
  status.textContent = "Avvioâ€¦";
  log.innerHTML = "";

  try {
    const response = await fetch("/api/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        message: "Genera il prossimo video",
        series: "tradimenti_e_vendetta",
        duration_minutes: 12,
        tiktok_clips: 4
      })
    });

    const data = await response.json();
    if (!response.ok) throw new Error(data.detail || "Errore durante lâ€™avvio");

    addLog("Job avviato: " + data.job_id);

    const interval = setInterval(async () => {
      try {
        const statusResponse = await fetch("/api/jobs/" + data.job_id).then(r => r.json());
        updateProgress(statusResponse);
        log.innerHTML = "";
        (statusResponse.logs || []).forEach(addLog);

        if (statusResponse.status === "completed") {
          clearInterval(interval);
          button.disabled = false;
          addLog("Video YouTube: " + (statusResponse.result?.youtube_video || "non disponibile"));
          addLog("Clip TikTok generate: " + (statusResponse.result?.tiktok_clips?.length || 0));
        }

        if (statusResponse.status === "failed") {
          clearInterval(interval);
          button.disabled = false;
          status.textContent = "Errore";
          addLog("Errore: " + statusResponse.error);
        }
      } catch(e) {
        addLog("Errore polling: " + e.message);
      }
    }, 1500);

  } catch(error) {
    status.textContent = "Errore";
    addLog("Errore: " + error.message);
    button.disabled = false;
  }
});
