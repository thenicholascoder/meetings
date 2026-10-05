import { useIsRecording } from '@livekit/components-react';
import * as React from 'react';

// The record button owns the toasts. The border turns on with that toast,
// at the click, and turns off when Stop succeeds. LiveKit's own flag arrives
// later, after Chrome has joined, so it is only a fallback after a refresh.
const listeners = new Set<() => void>();
let armed = false;
let dismissed = false;

function emit() {
  listeners.forEach((listener) => listener());
}

export function followLiveRecording() {
  armed = true;
  dismissed = false;
  emit();
}

export function dismissRecordingBorder() {
  armed = false;
  dismissed = true;
  emit();
}

export function RecordingIndicator() {
  const isRecording = useIsRecording();
  const [, setTick] = React.useState(0);

  React.useEffect(() => {
    const onChange = () => setTick((value) => value + 1);
    listeners.add(onChange);
    return () => {
      listeners.delete(onChange);
    };
  }, []);

  const showBorder = armed || (isRecording && !dismissed);

  return (
    <div
      style={{
        position: 'absolute',
        top: '0',
        left: '0',
        width: '100%',
        height: '100%',
        boxShadow: showBorder ? 'var(--lk-danger3) 0px 0px 0px 3px inset' : 'none',
        pointerEvents: 'none',
      }}
    ></div>
  );
}
