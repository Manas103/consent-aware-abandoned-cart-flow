import { useState } from "react";
import { buildFlowPayload, FlowDraft, FlowValidationError, StepDraft } from "../lib/flowPayload";
import { createFlow } from "../lib/api";

const emptyStep = (): StepDraft => ({ delayMinutes: 60, channel: "sms", template: "" });

/** The React flow builder: define an abandoned-cart trigger and an
 * ordered list of SMS steps, then POST the resulting Flow definition to
 * the Django backend (flows.views.create_flow). This is intentionally a
 * small, single-screen form, not a drag-and-drop canvas; the shape of
 * data it produces (a trigger plus ordered, delayed message steps) is
 * what the backend's state machine and Celery workers consume. */
export function FlowBuilder({ apiBaseUrl }: { apiBaseUrl: string }) {
  const [name, setName] = useState("");
  const [steps, setSteps] = useState<StepDraft[]>([emptyStep()]);
  const [status, setStatus] = useState<string>("");

  const draft: FlowDraft = { name, triggerType: "cart_abandoned", steps };

  const addStep = () => setSteps([...steps, emptyStep()]);
  const updateStep = (i: number, patch: Partial<StepDraft>) =>
    setSteps(steps.map((s, idx) => (idx === i ? { ...s, ...patch } : s)));

  const submit = async () => {
    try {
      const payload = buildFlowPayload(draft);
      const result = await createFlow(apiBaseUrl, payload);
      setStatus(`created flow #${result.id}`);
    } catch (err) {
      if (err instanceof FlowValidationError) {
        setStatus(`invalid: ${err.message}`);
      } else {
        setStatus(`error: ${(err as Error).message}`);
      }
    }
  };

  return (
    <div className="flow-builder">
      <h1>Abandoned Cart Flow Builder</h1>
      <label>
        Flow name
        <input value={name} onChange={(e) => setName(e.target.value)} />
      </label>
      <h2>Trigger</h2>
      <p>Cart abandoned (fixed for this builder)</p>
      <h2>Steps</h2>
      {steps.map((step, i) => (
        <div key={i} className="step-row">
          <span>Step {i + 1}</span>
          <label>
            Delay (minutes)
            <input
              type="number"
              value={step.delayMinutes}
              onChange={(e) => updateStep(i, { delayMinutes: Number(e.target.value) })}
            />
          </label>
          <label>
            Message
            <textarea
              value={step.template}
              onChange={(e) => updateStep(i, { template: e.target.value })}
            />
          </label>
        </div>
      ))}
      <button type="button" onClick={addStep}>
        Add step
      </button>
      <button type="button" onClick={submit}>
        Save flow
      </button>
      {status && <p className="status">{status}</p>}
    </div>
  );
}
