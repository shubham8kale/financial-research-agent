import "@testing-library/jest-dom/vitest";
import { afterEach } from "vitest";
import { cleanup } from "@testing-library/react";

// Unmount rendered components between tests (not automatic without globals).
afterEach(() => cleanup());

// jsdom does not implement scrollIntoView; MessageList calls it on every update.
if (!Element.prototype.scrollIntoView) {
  Element.prototype.scrollIntoView = () => {};
}
