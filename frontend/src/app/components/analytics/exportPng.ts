export function exportPng(containerEl: HTMLDivElement, filename: string): void {
  const svg = containerEl.querySelector("svg");
  if (!svg) return;

  const clone = svg.cloneNode(true) as SVGSVGElement;

  // Inline computed colors on text elements (resolves CSS variables)
  const origTexts = svg.querySelectorAll("text");
  const cloneTexts = clone.querySelectorAll("text");
  for (let i = 0; i < origTexts.length; i++) {
    const computed = getComputedStyle(origTexts[i]);
    cloneTexts[i]?.setAttribute("fill", computed.fill || computed.color);
  }

  // Inline computed stroke on line/path/rect elements
  const shapeSelector = "line, path, rect, circle, polyline, polygon";
  const origShapes = svg.querySelectorAll(shapeSelector);
  const cloneShapes = clone.querySelectorAll(shapeSelector);
  for (let i = 0; i < origShapes.length; i++) {
    const computed = getComputedStyle(origShapes[i]);
    if (computed.stroke && computed.stroke !== "none") {
      cloneShapes[i]?.setAttribute("stroke", computed.stroke);
    }
  }

  const rect = svg.getBoundingClientRect();
  const scale = 2; // retina
  const w = rect.width * scale;
  const h = rect.height * scale;

  clone.setAttribute("width", String(w));
  clone.setAttribute("height", String(h));
  clone.setAttribute("viewBox", `0 0 ${rect.width} ${rect.height}`);
  clone.setAttribute("xmlns", "http://www.w3.org/2000/svg");

  const serializer = new XMLSerializer();
  const svgString = serializer.serializeToString(clone);
  const blob = new Blob([svgString], { type: "image/svg+xml;charset=utf-8" });
  const url = URL.createObjectURL(blob);

  const img = new Image();
  img.onload = () => {
    const canvas = document.createElement("canvas");
    canvas.width = w;
    canvas.height = h;
    const ctx = canvas.getContext("2d")!;

    // Background fill matching current theme
    const bg = getComputedStyle(document.documentElement)
      .getPropertyValue("--bg-secondary")
      .trim() || "#ffffff";
    ctx.fillStyle = bg;
    ctx.fillRect(0, 0, w, h);

    ctx.drawImage(img, 0, 0, w, h);
    URL.revokeObjectURL(url);

    const a = document.createElement("a");
    a.href = canvas.toDataURL("image/png");
    a.download = filename;
    a.click();
  };
  img.src = url;
}

export function makePngFilename(widget: string, period: string): string {
  const date = new Date().toISOString().split("T")[0];
  return `fng_chart_${widget}_${period}_${date}.png`;
}
