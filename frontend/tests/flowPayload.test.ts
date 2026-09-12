import { describe, expect, it } from "vitest";
import { buildFlowPayload, FlowDraft, FlowValidationError } from "../src/lib/flowPayload";

const validDraft: FlowDraft = {
  name: "Abandoned cart",
  triggerType: "cart_abandoned",
  steps: [
    { delayMinutes: 60, channel: "sms", template: "Still interested?" },
    { delayMinutes: 1440, channel: "sms", template: "10% off if you check out today" },
  ],
};

describe("buildFlowPayload", () => {
  it("assigns sequential step_index and trims text", () => {
    const payload = buildFlowPayload({ ...validDraft, name: "  Abandoned cart  " });
    expect(payload.name).toBe("Abandoned cart");
    expect(payload.trigger_type).toBe("cart_abandoned");
    expect(payload.steps.map((s) => s.step_index)).toEqual([0, 1]);
    expect(payload.steps[0].delay_minutes).toBe(60);
  });

  it("rejects an empty flow name", () => {
    expect(() => buildFlowPayload({ ...validDraft, name: "  " })).toThrow(FlowValidationError);
  });

  it("rejects a flow with no steps", () => {
    expect(() => buildFlowPayload({ ...validDraft, steps: [] })).toThrow(FlowValidationError);
  });

  it("rejects a step with a blank template", () => {
    const draft = { ...validDraft, steps: [{ delayMinutes: 5, channel: "sms" as const, template: "  " }] };
    expect(() => buildFlowPayload(draft)).toThrow(FlowValidationError);
  });

  it("rejects a negative delay", () => {
    const draft = { ...validDraft, steps: [{ delayMinutes: -1, channel: "sms" as const, template: "hi" }] };
    expect(() => buildFlowPayload(draft)).toThrow(FlowValidationError);
  });
});
