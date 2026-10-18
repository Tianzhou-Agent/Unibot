import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import "@/index.css";
import { initCanvasI18n } from "../i18n";
import { ImageRecognitionCanvas } from "./ImageRecognitionCanvas";
import en from "./locales/en";
import zh from "./locales/zh";

initCanvasI18n("imageRec", { en, zh }).then(() => {
  createRoot(document.getElementById("root")!).render(
    <StrictMode>
      <ImageRecognitionCanvas />
    </StrictMode>,
  );
});
