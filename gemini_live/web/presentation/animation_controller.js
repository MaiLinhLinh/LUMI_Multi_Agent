import { runCommonEffect } from "./effects/common_effects.js";
import { runChartEffect } from "./effects/chart_effects.js";
import { runItemEffect } from "./effects/item_effects.js";

/** Shared scene lifecycle. It is intentionally independent of every domain. */
export class AnimationController {
  constructor({ templateHost, viewport, overlay, avatar }) {
    this.templateHost = templateHost;
    this.viewport = viewport;
    this.overlay = overlay;
    this.avatar = avatar;
    this.pendingScene = null;
    this.activeTarget = null;
    this.timer = null;
    this.handlers = [runCommonEffect, runChartEffect, runItemEffect];
  }

  clear() {
    if (this.timer) window.clearTimeout(this.timer);
    this.timer = null;
    this.pendingScene = null;
    this.activeTarget?.classList.remove("lumi-highlight");
    this.activeTarget = null;
    this.overlay.replaceChildren();
  }

  queue(command) { this.pendingScene = command; }

  armAtAudioStart(audioStartAt, audioContext) {
    if (!this.pendingScene || !audioContext) return;
    const scene = this.pendingScene;
    this.pendingScene = null;
    const delay = Math.max(0, (audioStartAt - audioContext.currentTime) * 1000) + 180;
    this.timer = window.setTimeout(() => {
      this.avatar.dataset.avatarState = "speaking";
      this.play(scene);
    }, delay);
    console.info("[GEMINI_LIVE:SCENE_ARMED]", { scene, delay_ms: Math.round(delay) });
  }

  play(command) {
    const root = this.templateHost.shadowRoot;
    const target = root?.querySelector(`[data-present-id="${CSS.escape(command.target_id)}"]`);
    if (!target) {
      console.warn("[GEMINI_LIVE:UI_TARGET_MISSING]", command);
      return;
    }
    this.clear();
    this.activeTarget = target;
    const context = { target, overlay: this.overlay, rect: this.rectFor(target) };
    const handled = this.handlers.some((handler) => handler(command, context));
    if (!handled) console.warn("[GEMINI_LIVE:UI_EFFECT_UNIMPLEMENTED]", command);
  }

  rectFor(target) {
    const root = this.viewport.getBoundingClientRect();
    const box = target.getBoundingClientRect();
    return { x: box.left - root.left, y: box.top - root.top, width: box.width, height: box.height };
  }
}
