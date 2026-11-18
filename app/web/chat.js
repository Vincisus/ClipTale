<<<<<<< HEAD

const button = document.getElementById("generate");
const status = document.getElementById("status") || document.getElementById("current-step");
=======
const button = document.getElementById("generate");
<<<<<<< HEAD
const status = document.getElementById("status") || document.getElementById("current-step");
=======
const status = document.getElementById("status");
>>>>>>> 715c55182517403e7c1f76e44a36231d6bd83387
>>>>>>> a1b3d5dbe7dff46c884019b7db2e62ebc54c7d36
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
<<<<<<< HEAD
=======
<<<<<<< HEAD
>>>>>>> a1b3d5dbe7dff46c884019b7db2e62ebc54c7d36
  progressFill.style.width = (job.progress || 0) + "%";
  progressText.textContent = (job.progress || 0) + "%";
  const stepEl = document.getElementById("current-step");
  if (stepEl) stepEl.textContent = job.current_step || "In attesa";
<<<<<<< HEAD
=======
=======
  progressFill.style.width = job.progress + "%";
  progressText.textContent = job.progress + "%";
  currentStep.textContent = job.current_step;
  status.textContent = job.status === "completed" ? "Completato" : "In corso";
>>>>>>> 715c55182517403e7c1f76e44a36231d6bd83387
>>>>>>> a1b3d5dbe7dff46c884019b7db2e62ebc54c7d36
}

button.addEventListener("click", async () => {
  button.disabled = true;
<<<<<<< HEAD
  status.textContent = "Avvio...";
=======
<<<<<<< HEAD
  status.textContent = "Avvio...";
=======
  status.textContent = "Avvio…";
>>>>>>> 715c55182517403e7c1f76e44a36231d6bd83387
>>>>>>> a1b3d5dbe7dff46c884019b7db2e62ebc54c7d36
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
<<<<<<< HEAD
    if (!response.ok) throw new Error(data.detail || "Errore durante l'avvio");
=======
<<<<<<< HEAD
    if (!response.ok) throw new Error(data.detail || "Errore durante l'avvio");
=======
    if (!response.ok) throw new Error(data.detail || "Errore durante l’avvio");
>>>>>>> 715c55182517403e7c1f76e44a36231d6bd83387
>>>>>>> a1b3d5dbe7dff46c884019b7db2e62ebc54c7d36

    addLog("Job avviato: " + data.job_id);

    const interval = setInterval(async () => {
<<<<<<< HEAD
=======
<<<<<<< HEAD
>>>>>>> a1b3d5dbe7dff46c884019b7db2e62ebc54c7d36
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
<<<<<<< HEAD
=======
=======
      const statusResponse = await fetch("/api/jobs/" + data.job_id);
      const job = await statusResponse.json();

      updateProgress(job);
      log.innerHTML = "";
      job.logs.forEach(addLog);

      if (job.status === "completed") {
        clearInterval(interval);
        button.disabled = false;
        addLog("Video YouTube: " + job.result.youtube_video);
        addLog("Clip TikTok generate: " + job.result.tiktok_clips.length);
      }

      if (job.status === "failed") {
        clearInterval(interval);
        button.disabled = false;
        status.textContent = "Errore";
        addLog("Errore: " + job.error);
      }
    }, 1500);

  } catch (error) {
>>>>>>> 715c55182517403e7c1f76e44a36231d6bd83387
>>>>>>> a1b3d5dbe7dff46c884019b7db2e62ebc54c7d36
    status.textContent = "Errore";
    addLog("Errore: " + error.message);
    button.disabled = false;
  }
});
