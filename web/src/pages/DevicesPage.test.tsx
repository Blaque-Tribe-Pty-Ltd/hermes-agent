// @vitest-environment jsdom
import { act, type ReactNode } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const apiMocks = vi.hoisted(() => ({
  getMoonPieDevices: vi.fn(),
  confirmMoonPieDevice: vi.fn(),
}));
const showToast = vi.hoisted(() => vi.fn());

vi.mock("@/lib/api", () => ({ api: apiMocks }));
vi.mock("@nous-research/ui/hooks/use-toast", () => ({
  useToast: () => ({ toast: null, showToast }),
}));
vi.mock("@nous-research/ui/ui/components/toast", () => ({ Toast: () => null }));
vi.mock("@nous-research/ui/ui/components/badge", () => ({
  Badge: ({ children }: { children?: ReactNode }) => <span>{children}</span>,
}));
vi.mock("@nous-research/ui/ui/components/button", () => ({
  Button: ({ children, onClick, disabled }: {
    children?: ReactNode; onClick?: () => void; disabled?: boolean;
  }) => <button onClick={onClick} disabled={disabled}>{children}</button>,
}));
vi.mock("@nous-research/ui/ui/components/spinner", () => ({ Spinner: () => <span>loading</span> }));
vi.mock("@nous-research/ui/ui/components/typography/h2", () => ({
  H2: ({ children }: { children?: ReactNode }) => <h2>{children}</h2>,
}));
vi.mock("@nous-research/ui/ui/components/card", () => ({
  Card: ({ children }: { children?: ReactNode }) => <div>{children}</div>,
  CardContent: ({ children }: { children?: ReactNode }) => <div>{children}</div>,
}));

let container: HTMLDivElement;
let root: Root;
(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

async function waitFor(condition: () => boolean) {
  const deadline = Date.now() + 3000;
  while (!condition()) {
    if (Date.now() > deadline) throw new Error("condition was not met");
    await act(async () => new Promise((resolve) => setTimeout(resolve, 10)));
  }
}

beforeEach(() => {
  vi.clearAllMocks();
  container = document.createElement("div");
  document.body.append(container);
  root = createRoot(container);
});

afterEach(async () => {
  await act(async () => root.unmount());
  container.remove();
});

describe("DevicesPage", () => {
  it("lists pending separately and refreshes after operator confirmation", async () => {
    const pending = {
      device_id: "moonpie-pending",
      name: "Studio Mac",
      model: "Mac14,13",
      os_version: "15.1",
      confirmed: false,
      created_at: "2026-09-30T10:00:00Z",
    };
    const confirmed = { ...pending, device_id: "moonpie-confirmed", name: "Laptop", confirmed: true };
    apiMocks.getMoonPieDevices
      .mockResolvedValueOnce([pending, confirmed])
      .mockResolvedValueOnce([{ ...pending, confirmed: true }, confirmed]);
    apiMocks.confirmMoonPieDevice.mockResolvedValue({ ok: true, device_id: pending.device_id });

    const { default: DevicesPage } = await import("./DevicesPage");
    await act(async () => root.render(<DevicesPage />));
    await waitFor(() => container.textContent?.includes("Pending devices (1)") === true);

    const confirm = Array.from(container.querySelectorAll("button")).find(
      (button) => button.textContent === "Confirm",
    );
    if (!confirm) throw new Error("confirm button was not rendered");
    await act(async () => confirm.dispatchEvent(new MouseEvent("click", { bubbles: true })));
    await waitFor(() => apiMocks.getMoonPieDevices.mock.calls.length === 2);

    expect(apiMocks.confirmMoonPieDevice).toHaveBeenCalledWith("moonpie-pending");
    expect(container.textContent).toContain("Pending devices (0)");
    expect(container.textContent).toContain("Confirmed devices (2)");
  });
});
