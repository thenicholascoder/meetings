const WARMUP_TIMEOUT_MS = 90000;

let warmPromise: Promise<void> | null = null;
let ready = false;

/**
 * next dev compiles /egress on the first request. That request took 33s,
 * which is longer than Egress will wait for the page, so Chrome never printed
 * START_RECORDING and Stop aborted the job. Load the route once in this
 * browser before StartEgress so the compile is finished.
 */
export function recorderPageIsWarm(): boolean {
  return ready;
}

export function warmRecorderPage(): Promise<void> {
  if (warmPromise) {
    return warmPromise;
  }
  if (typeof document === 'undefined') {
    ready = true;
    warmPromise = Promise.resolve();
    return warmPromise;
  }
  warmPromise = new Promise((resolve) => {
    const frame = document.createElement('iframe');
    frame.setAttribute('aria-hidden', 'true');
    frame.tabIndex = -1;
    frame.title = '';
    frame.style.cssText = 'position:absolute;width:0;height:0;border:0;visibility:hidden';
    let settled = false;
    const finish = () => {
      if (settled) {
        return;
      }
      settled = true;
      ready = true;
      frame.remove();
      resolve();
    };
    frame.addEventListener('load', () => {
      if (!frame.src.includes('/egress')) {
        return;
      }
      finish();
    });
    window.setTimeout(finish, WARMUP_TIMEOUT_MS);
    frame.src = '/egress';
    try {
      document.body.appendChild(frame);
    } catch (error) {
      console.error(error);
      finish();
    }
  });
  return warmPromise;
}
