import * as pdfjsLib from "/static/pdf.mjs";

pdfjsLib.GlobalWorkerOptions.workerSrc = "/static/pdf.worker.mjs";

const STANDARD_FONT_URL = "/static/standard_fonts/";

function showViewerError(viewer, message) {
  const status = viewer.querySelector("[data-pdf-status]");
  const canvas = viewer.querySelector("canvas");
  const controls = viewer.querySelector("[data-pdf-controls]");

  if (status) {
    status.textContent = message;
    status.hidden = false;
  }
  if (canvas) canvas.hidden = true;
  if (controls) controls.hidden = true;
}

async function initializeViewer(viewer) {
  const pdfUrl = viewer.dataset.pdfUrl;
  const canvas = viewer.querySelector("canvas");
  const status = viewer.querySelector("[data-pdf-status]");
  const controls = viewer.querySelector("[data-pdf-controls]");
  const previous = viewer.querySelector("[data-pdf-previous]");
  const next = viewer.querySelector("[data-pdf-next]");
  const pageLabel = viewer.querySelector("[data-pdf-page]");

  if (!pdfUrl || !canvas || !status || !controls || !previous || !next || !pageLabel) {
    return;
  }

  status.textContent = "Loading PDF…";
  status.hidden = false;

  try {
    const loadingTask = pdfjsLib.getDocument({
      url: pdfUrl,
      standardFontDataUrl: STANDARD_FONT_URL,
    });
    const pdf = await loadingTask.promise;
    const context = canvas.getContext("2d", { alpha: false });
    let currentPage = 1;
    let rendering = false;

    async function renderPage(pageNumber) {
      if (rendering) return;
      rendering = true;
      previous.disabled = true;
      next.disabled = true;

      try {
        const page = await pdf.getPage(pageNumber);
        const naturalViewport = page.getViewport({ scale: 1 });
        const availableWidth = Math.max(280, viewer.clientWidth - 4);
        const viewport = page.getViewport({
          scale: availableWidth / naturalViewport.width,
        });
        const outputScale = Math.max(1, window.devicePixelRatio || 1);

        canvas.width = Math.floor(viewport.width * outputScale);
        canvas.height = Math.floor(viewport.height * outputScale);
        canvas.style.width = `${Math.floor(viewport.width)}px`;
        canvas.style.height = `${Math.floor(viewport.height)}px`;
        canvas.hidden = false;

        await page.render({
          canvasContext: context,
          viewport,
          transform:
            outputScale === 1
              ? undefined
              : [outputScale, 0, 0, outputScale, 0, 0],
        }).promise;

        currentPage = pageNumber;
        pageLabel.textContent = `Page ${currentPage} of ${pdf.numPages}`;
        status.hidden = true;
        controls.hidden = false;
      } finally {
        previous.disabled = currentPage <= 1;
        next.disabled = currentPage >= pdf.numPages;
        rendering = false;
      }
    }

    previous.addEventListener("click", () => {
      if (currentPage > 1) void renderPage(currentPage - 1);
    });
    next.addEventListener("click", () => {
      if (currentPage < pdf.numPages) void renderPage(currentPage + 1);
    });

    await renderPage(currentPage);
  } catch (error) {
    console.error("PDF preview failed", error);
    showViewerError(
      viewer,
      "PDF preview is unavailable. Use the open or download links instead.",
    );
  }
}

for (const viewer of document.querySelectorAll("[data-pdf-viewer]")) {
  void initializeViewer(viewer);
}
