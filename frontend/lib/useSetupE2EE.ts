import React from 'react';
import { ExternalE2EEKeyProvider } from 'livekit-client';
import { decodePassphrase } from './client-utils';

export function useSetupE2EE() {
  const e2eePassphrase =
    typeof window !== 'undefined' ? decodePassphrase(location.hash.substring(1)) : undefined;

  // One worker for the page. Creating it during render leaked a worker on every
  // update, and terminating it in an effect cleanup kills it again under
  // React's development remount.
  const worker = React.useMemo(() => {
    if (typeof window === 'undefined' || !e2eePassphrase) {
      return undefined;
    }
    return new Worker(new URL('livekit-client/e2ee-worker', import.meta.url));
  }, [e2eePassphrase]);

  return { worker, e2eePassphrase };
}
