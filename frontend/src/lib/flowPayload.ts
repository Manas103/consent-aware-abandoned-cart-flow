// Pure, framework-free logic for turning the flow builder's in-memory
// state into the JSON payload Django's POST /api/flows expects
// (flows.views.create_flow). Kept separate from the React component so it
// can be unit-tested without a DOM.

export interface StepDraft {
  delayMinutes: number;
  channel: "sms";
  template: string;
}

export interface FlowDraft {
  name: string;
  triggerType: "cart_abandoned";
  steps: StepDraft[];
}

export interface FlowPayload {
  name: string;
  trigger_type: string;
  steps: Array<{
    step_index: number;
    delay_minutes: number;
    channel: string;
    template: string;
  }>;
}

export class FlowValidationError extends Error {}

export function buildFlowPayload(draft: FlowDraft): FlowPayload {
  if (!draft.name.trim()) {
    throw new FlowValidationError("flow name is required");
  }
  if (draft.steps.length === 0) {
    throw new FlowValidationError("at least one message step is required");
  }
  for (const step of draft.steps) {
    if (!step.template.trim()) {
      throw new FlowValidationError("every step needs a message template");
    }
    if (step.delayMinutes < 0) {
      throw new FlowValidationError("delay minutes cannot be negative");
    }
  }
  return {
    name: draft.name.trim(),
    trigger_type: draft.triggerType,
    steps: draft.steps.map((step, index) => ({
      step_index: index,
      delay_minutes: step.delayMinutes,
      channel: step.channel,
      template: step.template.trim(),
    })),
  };
}
