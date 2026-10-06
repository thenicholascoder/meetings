'use client';

import React from 'react';
import styles from '@/styles/Slides.module.css';

type PptxViewerInstance = {
  slideWidth: number;
  slideHeight: number;
  open: (
    input: Uint8Array,
    options?: { renderMode?: 'list' | 'slide'; lazyMedia?: boolean; lazySlides?: boolean },
  ) => Promise<void>;
  renderSlide: (index?: number) => Promise<void>;
  destroy: () => void;
};

function clipsOverflow(value: string) {
  return value === 'hidden' || value === 'clip' || value === 'auto' || value === 'scroll';
}

/** Keep the PowerPoint anchor when the line is taller than the box. */
function unsafeFlexAlignment(value: string) {
  if (value === 'center') return 'unsafe center';
  if (value === 'flex-end') return 'unsafe flex-end';
  return null;
}

/**
 * Once a text box no longer clips, the browser treats `center` as start
 * alignment so the beginning of the line cannot fall outside the box. Put the
 * original center (or bottom / right edge) back, and let the extra text hang
 * out equally instead of shifting to one side.
 */
function restoreTextAlignment(box: HTMLElement) {
  const justify = unsafeFlexAlignment(box.style.justifyContent);
  if (justify) box.style.justifyContent = justify;
  const alignItems = unsafeFlexAlignment(box.style.alignItems);
  if (alignItems) box.style.alignItems = alignItems;
  if (box.style.whiteSpace !== 'nowrap') return;

  for (const child of box.children) {
    if (!(child instanceof HTMLElement)) continue;
    const align = child.style.textAlign;
    if (align !== 'center' && align !== 'right') continue;
    if (child.scrollWidth <= child.clientWidth + 1) continue;
    child.style.width = 'max-content';
    child.style.maxWidth = 'none';
    child.style.alignSelf = align === 'center' ? 'unsafe center' : 'unsafe flex-end';
  }
}

/**
 * Shape text is absolutely positioned in a column flex box. PowerPoint boxes
 * with wrap="none", and the renderer's shrink-to-fit pass, clip that box, so
 * the rest of the line never paints. Let it paint through the slide instead.
 * The frame around the slide still clips, so the line stays in the picture.
 */
function revealOverflowText(host: HTMLElement) {
  for (const box of host.querySelectorAll<HTMLElement>('div')) {
    const style = box.style;
    if (style.position !== 'absolute' || style.display !== 'flex' || style.flexDirection !== 'column') {
      continue;
    }
    let node: HTMLElement | null = box;
    while (node) {
      const computed = getComputedStyle(node);
      if (clipsOverflow(computed.overflowX) || clipsOverflow(computed.overflowY)) {
        node.style.overflow = 'visible';
      }
      if (node === host) {
        break;
      }
      node = node.parentElement;
    }
    restoreTextAlignment(box);
  }
}

function fitSlide(host: HTMLDivElement, slideWidth: number, slideHeight: number) {
  const frame = host.parentElement;
  if (!frame || slideWidth < 1 || slideHeight < 1) {
    return;
  }
  const scale = Math.min(frame.clientWidth / slideWidth, frame.clientHeight / slideHeight);
  if (!Number.isFinite(scale) || scale <= 0) {
    return;
  }
  const width = Math.max(1, Math.floor(slideWidth * scale));
  const height = Math.max(1, Math.floor(slideHeight * scale));
  host.style.width = `${width}px`;
  host.style.height = `${height}px`;
}

/**
 * One PowerPoint slide, fitted to the stage the same way a PDF page is.
 * Speaker notes stay outside this picture, in the presenter's notes panel.
 */
export function PptxSlide({
  bytes,
  page,
  label,
  onReady,
  onError,
}: {
  bytes: Uint8Array;
  page: number;
  label: string;
  onReady: () => void;
  onError: (message: string) => void;
}) {
  const hostRef = React.useRef<HTMLDivElement>(null);
  const viewerRef = React.useRef<PptxViewerInstance | null>(null);
  const pageRef = React.useRef(page);
  const onReadyRef = React.useRef(onReady);
  const onErrorRef = React.useRef(onError);
  const [visible, setVisible] = React.useState(false);
  pageRef.current = page;
  onReadyRef.current = onReady;
  onErrorRef.current = onError;

  React.useEffect(() => {
    const host = hostRef.current;
    if (!host) {
      return;
    }
    let cancelled = false;
    let overflowObserver: MutationObserver | null = null;
    setVisible(false);
    const reveal = () => revealOverflowText(host);
    void (async () => {
      try {
        const { PptxViewer, RECOMMENDED_ZIP_LIMITS } = await import('@aiden0z/pptx-renderer');
        if (cancelled) {
          return;
        }
        const viewer = new PptxViewer(host, {
          fitMode: 'contain',
          zipLimits: RECOMMENDED_ZIP_LIMITS,
          pdfjs: {
            moduleUrl: `${window.location.origin}/pdf.min.mjs`,
            workerUrl: `${window.location.origin}/pdf.worker.min.mjs`,
          },
        }) as PptxViewerInstance;
        viewerRef.current = viewer;
        await viewer.open(bytes.slice(), {
          renderMode: 'slide',
          lazyMedia: true,
          lazySlides: true,
        });
        if (cancelled) {
          viewer.destroy();
          return;
        }
        fitSlide(host, viewer.slideWidth, viewer.slideHeight);
        await viewer.renderSlide(Math.max(0, pageRef.current - 1));
        if (cancelled) {
          viewer.destroy();
          return;
        }
        reveal();
        overflowObserver = new MutationObserver(reveal);
        overflowObserver.observe(host, {
          subtree: true,
          childList: true,
          attributes: true,
          attributeFilter: ['style'],
        });
        setVisible(true);
        onReadyRef.current();
      } catch (error) {
        if (!cancelled) {
          viewerRef.current?.destroy();
          viewerRef.current = null;
          onErrorRef.current(error instanceof Error ? error.message : 'Could not open this PowerPoint file');
        }
      }
    })();
    return () => {
      cancelled = true;
      overflowObserver?.disconnect();
      viewerRef.current?.destroy();
      viewerRef.current = null;
      host.replaceChildren();
    };
  }, [bytes]);

  React.useEffect(() => {
    if (!visible) {
      return;
    }
    const viewer = viewerRef.current;
    if (!viewer) {
      return;
    }
    void viewer.renderSlide(Math.max(0, page - 1)).catch((error: unknown) => {
      onErrorRef.current(error instanceof Error ? error.message : 'Could not draw this slide');
    });
  }, [page, visible]);

  React.useEffect(() => {
    const host = hostRef.current;
    const frame = host?.parentElement;
    if (!host || !frame || !visible) {
      return;
    }
    let resizeTimer = 0;
    const apply = () => {
      const viewer = viewerRef.current;
      if (!viewer) {
        return;
      }
      const previousWidth = host.style.width;
      const previousHeight = host.style.height;
      fitSlide(host, viewer.slideWidth, viewer.slideHeight);
      if (host.style.width === previousWidth && host.style.height === previousHeight) {
        return;
      }
      void viewer.renderSlide(Math.max(0, pageRef.current - 1));
    };
    const observer = new ResizeObserver(() => {
      window.clearTimeout(resizeTimer);
      resizeTimer = window.setTimeout(apply, 200);
    });
    observer.observe(frame);
    return () => {
      window.clearTimeout(resizeTimer);
      observer.disconnect();
    };
  }, [visible]);

  return (
    <div
      ref={hostRef}
      className={styles.pptxHost}
      aria-label={label}
      style={{ visibility: visible ? 'visible' : 'hidden' }}
    />
  );
}
