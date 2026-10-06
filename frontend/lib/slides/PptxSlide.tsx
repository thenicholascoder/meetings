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
    setVisible(false);
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
